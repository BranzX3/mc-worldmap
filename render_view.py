"""เรนเดอร์ภาพระดับสายตาแบบ offline — ตรวจ immersive ก่อน paint ลงโลกจริง

ทำไมต้องมี: เครื่องมือทุกตัวก่อนหน้านี้มองจากบนลงล่าง (render_preview.py,
report_metrics.py) แต่ immersive ตัดสินกันตอนยืนอยู่ในนั้นมองแนวนอน สิ่งที่
มองจากบนไม่บอกอะไรเลยคือ ความสูงชันที่รู้สึกได้ silhouette ของสันเขา แนวไม้
แนวหิมะ ความลึกของหมอก และสัดส่วนต้นไม้เทียบภูเขา

วิธี: ray-march บน heightmap ทีละคอลัมน์จอ (แบบ voxel-space) ใช้กฎสีและกฎ
ชนิดไม้ตัวเดียวกับ paint (surface.classify + ecology) จึงเป็นภาพที่เชื่อได้

ใช้:
    python render_view.py --at 5104 5648                 # มองไปทางทิศเหนือ
    python render_view.py --at 5104 5648 --yaw 135 --pitch -3
    python render_view.py --at 5104 5648 --panorama      # 4 ทิศในภาพเดียว
    python render_view.py --at 5104 5648 --eye 60        # ยกกล้องขึ้น 60 บล็อก
    python render_view.py --at 5104 5648 --range 4000 --size 1920x1080

หมายเหตุ: เรือนยอดถูกวาดเป็น "ผิวยกสูง" ไม่ใช่ต้นไม้ทีละต้น จึงใช้ตรวจ
silhouette/สัดส่วน/แนวไม้ได้ แต่ตรวจโครงสร้างภายในป่าไม่ได้
"""

import os
import sys

import numpy as np
from PIL import Image

import config as C
import ecology as E
import surface as S
import hydrology_patch_io as H
from pipeline_progress import use_utf8_stdout

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))

SKY_TOP = np.array([92, 132, 198], dtype=np.float32)
SKY_HORIZON = np.array([188, 208, 228], dtype=np.float32)
FOG = np.array([196, 212, 230], dtype=np.float32)
WATER_RGB = np.array([58, 104, 150], dtype=np.float32)
FOG_DISTANCE = 1400.0          # บล็อก — ระยะที่สีจมไปกับหมอกราว 63%


def load_window(cx, cz, radius, hydrology_root=None, hydrology_patch=None):
    """อ่านกรอบสี่เหลี่ยมรอบกล้อง คืน array แบบ [x, z]

    ``hydrology_patch`` คือ npz จาก `hydrology_shape.py --patch` — วางทับผลลัพธ์
    ชุดที่อยู่บนดิสก์เฉพาะในกรอบของมัน ทำให้ดูภาพของ patch ที่เพิ่งขึ้นรูปได้
    โดยไม่ต้องรัน `--global` ทั้งแผนที่ก่อน (ดู golden_patches.py)
    """
    x0, x1 = max(0, cx - radius), min(C.GRID, cx + radius)
    z0, z1 = max(0, cz - radius), min(C.GRID, cz + radius)
    hm = np.asarray(
        Image.open(os.path.join(HERE, "heightmap.png"))
    )[z0:z1, x0:x1].T.astype(np.float32)

    lc = np.load(os.path.join(HERE, "landcover.npz"))["landcover"][z0:z1, x0:x1]
    wm_path = H.water_product_path("water_mask", hydrology_root, HERE)
    water = None
    if os.path.exists(wm_path):
        water = np.asarray(np.load(wm_path, mmap_mode="r")[z0:z1, x0:x1])
    if hydrology_patch is not None:
        if water is None:
            water = np.zeros((z1 - z0, x1 - x0), dtype=bool)
        water = H.overlay_window(
            water, hydrology_patch, "water_mask", x0, x1, z0, z1
        )
    if water is not None:
        lc = S.apply_water_mask(lc, water)
    return hm, lc.T, (x0, z0)


def colour_tier(elev, lc, origin, step):
    """คำนวณสีพื้น/สีเรือนยอด/ความสูงเรือนยอด บนความละเอียดที่กำหนด

    ใช้ classify() + ecology ตัวเดียวกับ paint จึงเป็นสีที่โลกจริงจะได้
    """
    ox, oz = origin
    ce = elev[::step, ::step]
    clc = lc[::step, ::step]
    spacing = C.METERS_PER_BLOCK * step
    surf, forest_p, snow_lv, _soil = S.classify(
        ce, clc, spacing, block_m=C.METERS_PER_BLOCK, x0=ox, z0=oz,
    )
    decor = S.decor_fields(
        ce, spacing, x0=ox, z0=oz, block_m=C.METERS_PER_BLOCK
    )
    species = E.canopy_species(
        ce, spacing, decor["damp"], x0=ox, z0=oz,
        block_m=C.METERS_PER_BLOCK,
    )

    ground = S.PREVIEW_RGB[surf].astype(np.float32)
    snow_w = np.clip(snow_lv.astype(np.float32) / 8.0, 0, 1)[..., None] * 0.9
    ground = ground * (1 - snow_w) + np.array(
        S.BLOCKS["snow_thin"][1], np.float32
    ) * snow_w
    canopy = S.PREVIEW_RGB[E.species_leaf_index(species)].astype(np.float32)

    shade = hillshade(ce, spacing)[..., None]
    ground *= shade
    canopy *= shade * 0.94

    water = clc == S.LC["water"]
    ground[water] = WATER_RGB * 0.96

    has_canopy = (forest_p > 0.06) & ~water
    canopy_h = np.where(
        has_canopy, E.canopy_height(species, forest_p, ce), 0.0
    ).astype(np.float32)
    return {
        "origin": (ox, oz),
        "step": step,
        "ground": np.clip(ground, 0, 255),
        "canopy": np.clip(canopy, 0, 255),
        "canopy_h": canopy_h,
        "has_canopy": has_canopy,
    }


def build_scene(cx, cz, radius, step=3, near_radius=340, hydrology_root=None,
                hydrology_patch=None):
    """เตรียมความสูงและสีของกรอบที่มองเห็น

    ความสูงใช้ความละเอียดเต็มทั้งกรอบเพราะ silhouette ไวต่อรายละเอียด

    สีทำสองชั้น: ระยะใกล้ใช้ความละเอียดเต็ม (1 บล็อก) ระยะไกลใช้กริดหยาบ
    ที่ระยะ 6 บล็อก หนึ่งเซลล์กว้าง 190 พิกเซลบนจอ ถ้าใช้กริดหยาบ 3 บล็อก
    ทั้งภาพ ระยะใกล้จะเป็นแผ่นสีเรียบ 571 พิกเซล และการ dither ระดับบล็อก
    ของ palette ซึ่งเป็นหัวใจของลุค Minecraft จะมองไม่เห็นเลย
    """
    meta = S.load_meta()
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]
    hm, lc, (ox, oz) = load_window(
        cx, cz, radius, hydrology_root=hydrology_root,
        hydrology_patch=hydrology_patch,
    )

    elev = lo + hm / 65535.0 * (hi - lo)
    # ระดับผิวดินต้องมาจาก terrain_y.npy ตัวเดียวกับที่ build/paint ใช้ ไม่งั้น
    # ภาพจะไม่มีชั้นหิน กองหินเชิงผา และหลุมยุบที่ terrain_shape.py เติมไว้
    # เมื่อมี hydrology_root ต้องเป็น terrain_y ของชุดนั้น ซึ่ง reshape ตลิ่งและ
    # ก้นน้ำทับไปแล้ว
    ty_path = H.water_product_path("terrain_y", hydrology_root, HERE)
    if not os.path.exists(ty_path):
        raise SystemExit("ไม่พบ terrain_y.npy — รัน terrain_shape.py ก่อน")
    surface_y = np.asarray(np.load(ty_path, mmap_mode="r")[
        oz:oz + hm.shape[1], ox:ox + hm.shape[0]
    ]).astype(np.int32)
    if hydrology_patch is not None:
        surface_y = H.overlay_window(
            surface_y, hydrology_patch, "terrain_y",
            ox, ox + hm.shape[0], oz, oz + hm.shape[1],
        ).astype(np.int32)
    surface_y = surface_y.T

    far = colour_tier(elev, lc, (ox, oz), step)

    # ชั้นใกล้: หน้าต่างเล็กรอบกล้องที่ความละเอียด 1 บล็อก
    nr = min(near_radius, radius)
    a0 = max(0, cx - ox - nr)
    a1 = min(elev.shape[0], cx - ox + nr)
    b0 = max(0, cz - oz - nr)
    b1 = min(elev.shape[1], cz - oz + nr)
    near = colour_tier(
        elev[a0:a1, b0:b1], lc[a0:a1, b0:b1], (ox + a0, oz + b0), 1
    )

    return {
        "surface_y": surface_y,
        "origin": (ox, oz),
        "near": near,
        "far": far,
        "near_limit": float(nr - 4),
        "elev": elev,
    }


def hillshade(elev_m, spacing_m, azimuth=315.0, altitude=48.0):
    dz, dx = np.gradient(elev_m.astype(np.float32), spacing_m)
    slope = np.arctan(np.hypot(dx, dz))
    aspect = np.arctan2(-dx, dz)
    az, alt = np.radians(azimuth), np.radians(altitude)
    sh = (
        np.sin(alt) * np.cos(slope)
        + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    )
    return np.clip(0.55 + 0.60 * sh, 0.42, 1.28)


def _fill_spans(image, columns, tops, limits, colours):
    """ระบายแนวตั้ง [top, limit) ของแต่ละคอลัมน์ — vectorised

    การวนทีละคอลัมน์ใน Python จะได้ 600 step x 1200 คอลัมน์ = 720k รอบต่อภาพ
    จึงขยาย index ทีเดียวด้วย repeat + arange แทน
    """
    lengths = limits - tops
    keep = lengths > 0
    if not keep.any():
        return
    columns, tops, lengths = columns[keep], tops[keep], lengths[keep]
    colours = colours[keep]
    total = int(lengths.sum())
    col_idx = np.repeat(columns, lengths)
    starts = np.repeat(tops, lengths)
    group_start = np.repeat(np.cumsum(lengths) - lengths, lengths)
    offset = np.arange(total) - group_start
    row_idx = starts + offset

    # แต่ละแนวคือ "ผนังข้าง" ของคอลัมน์ภูมิประเทศ ไม่ใช่หน้าบน ถ้าระบายสีเดียว
    # ทั้งแนว หน้าผาจะกลายเป็นแผ่นสีเรียบและอ่านไม่ออกว่าเป็นบล็อก ไล่เงาลงล่าง
    # เลียนแบบที่ MC ให้หน้าบนสว่างและผนังข้างมืดกว่า
    frac = offset / np.maximum(1, np.repeat(lengths, lengths))
    shade = (1.0 - 0.36 * frac).astype(np.float32)[:, None]
    image[row_idx, col_idx] = np.repeat(colours, lengths, axis=0) * shade


def render(scene, cx, cz, yaw_deg, pitch_deg, fov_deg, width, height,
           max_range, eye_offset=2.0, fog_distance=FOG_DISTANCE):
    ox, oz = scene["origin"]
    sy = scene["surface_y"]
    nx, nz = sy.shape

    def sample_y(ix, iz):
        return sy[np.clip(ix, 0, nx - 1), np.clip(iz, 0, nz - 1)]

    # ---- ท้องฟ้าเป็นพื้นหลัง ----
    rows = np.arange(height, dtype=np.float32)[:, None]
    # เครื่องหมายของ pitch: ลบ = ก้มลง (แบบเดียวกับกล้องทุกตัวและกับที่ docstring
    # ของไฟล์นี้เขียนไว้)  เดิมเป็นบวก = ก้มลง ซึ่งกลับด้าน — ที่ -2 องศามันต่างกัน
    # ไม่กี่พิกเซลเลยไม่มีใครเห็น แต่พอ golden_patches สั่งก้ม 20 องศาเพื่อมองลง
    # ไปที่ลำน้ำ ภาพที่ได้กลับเป็นท้องฟ้ากับยอดเขา
    horizon = height * 0.5 + np.tan(np.radians(pitch_deg)) * (
        (width * 0.5) / np.tan(np.radians(fov_deg * 0.5))
    )
    t = np.clip(rows / max(1.0, horizon), 0.0, 1.0)
    image = np.repeat(
        (SKY_TOP * (1 - t) + SKY_HORIZON * t)[:, None, :], width, axis=1
    ).astype(np.float32)

    focal = (width * 0.5) / np.tan(np.radians(fov_deg * 0.5))
    cols = np.arange(width)
    angles = np.radians(yaw_deg) + np.arctan((cols - width * 0.5) / focal)
    dirx, dirz = np.sin(angles), np.cos(angles)

    eye_x = int(round(cx)) - ox
    eye_z = int(round(cz)) - oz
    eye_y = float(sample_y(np.array(eye_x), np.array(eye_z))) + eye_offset

    ymin = np.full(width, height, dtype=np.int64)

    # เรือนยอดถูกวาดเป็นผิวทึบ ถ้ากล้องยืนอยู่ในป่า ตัวอย่างที่ใกล้สุดจะสูงกว่า
    # ตาและโปรเจกต์หลุดจอ แล้วถมทั้งภาพเป็นสีใบไม้ ของจริงคนที่ยืนใต้เรือนยอด
    # มองทะลุระหว่างลำต้นออกไปได้ จึงไล่ไล่ระดับเรือนยอดเข้ามาแทนการตัดทันที
    CANOPY_FADE_START = 24.0
    CANOPY_FADE_SPAN = 40.0

    distance = 6.0
    while distance < max_range:
        # ก้าวโตขึ้นตามระยะ — คงความละเอียดใกล้ตาโดยไม่จ่ายค่าไกลตา
        dt = max(0.7, distance * 0.0075)
        wx = eye_x + dirx * distance
        wz = eye_z + dirz * distance
        ix = wx.astype(np.int64)
        iz = wz.astype(np.int64)
        inside = (ix >= 0) & (ix < nx) & (iz >= 0) & (iz < nz)

        ground_y = sample_y(ix, iz).astype(np.float32)

        # ระยะเท่ากันทุกคอลัมน์ในแต่ละก้าว จึงเลือกชั้นความละเอียดได้ทีเดียว
        tier = scene["near"] if distance < scene["near_limit"] else scene["far"]
        tox, toz = tier["origin"]
        tstep = tier["step"]
        gx = np.clip(
            (ix + ox - tox) // tstep, 0, tier["ground"].shape[0] - 1
        )
        gz = np.clip(
            (iz + oz - toz) // tstep, 0, tier["ground"].shape[1] - 1
        )

        fade = np.clip(
            (distance - CANOPY_FADE_START) / CANOPY_FADE_SPAN, 0.0, 1.0
        )
        wood = tier["has_canopy"][gx, gz].astype(np.float32) * fade
        top = ground_y + tier["canopy_h"][gx, gz] * wood
        colour = (
            tier["ground"][gx, gz] * (1.0 - wood[:, None])
            + tier["canopy"][gx, gz] * wood[:, None]
        )

        # หมอกตามระยะ — ตัวที่ให้ความลึกของภาพและบอกสเกลของภูเขา
        haze = 1.0 - np.exp(-distance / fog_distance)
        colour = colour * (1.0 - haze) + FOG * haze

        screen = np.rint(horizon - focal * (top - eye_y) / distance)
        screen = np.clip(screen, 0, height).astype(np.int64)

        paint = inside & (screen < ymin)
        if paint.any():
            idx = np.flatnonzero(paint)
            _fill_spans(image, idx, screen[idx], ymin[idx], colour[idx])
            ymin[idx] = screen[idx]

        if not inside.any():
            break
        distance += dt

    return np.clip(image, 0, 255).astype(np.uint8)


def parse_args(argv):
    def flag(name, default=None, count=1):
        if name not in argv:
            return default
        i = argv.index(name)
        vals = argv[i + 1:i + 1 + count]
        return vals if count > 1 else vals[0]

    at = flag("--at", None, 2)
    if not at:
        raise SystemExit("ต้องระบุ --at <x> <z>")
    size = flag("--size", "1600x900")
    w, _, h = size.partition("x")
    return {
        "cx": int(at[0]), "cz": int(at[1]),
        "yaw": float(flag("--yaw", 0.0)),
        "pitch": float(flag("--pitch", -2.0)),
        "fov": float(flag("--fov", 70.0)),
        "width": int(w), "height": int(h),
        "range": float(flag("--range", 2600.0)),
        "eye": float(flag("--eye", 2.0)),
        "fog": float(flag("--fog", FOG_DISTANCE)),
        "step": int(flag("--step", 3)),
        "panorama": "--panorama" in argv,
        # ต้องตรงกับที่ paint_surface ใช้ ไม่งั้นภาพจะวาดน้ำและผิวดินจาก
        # product ชุดที่ไม่ได้อยู่ในโลก
        "hydrology_root": H.resolve_hydrology_root(argv),
        # npz จาก --patch วางทับเฉพาะกรอบของมัน ใช้ดู patch ที่เพิ่งขึ้นรูป
        "hydrology_patch": flag("--hydrology-patch", None),
        "out": flag("--out", None),
    }


def main():
    use_utf8_stdout()
    a = parse_args(sys.argv)
    radius = int(a["range"]) + 8
    patch = (
        None if a["hydrology_patch"] is None
        else H.load_hydrology_patch(a["hydrology_patch"])
    )
    print(f"เตรียมกรอบรอบ ({a['cx']}, {a['cz']}) รัศมี {radius} บล็อก ...")
    scene = build_scene(
        a["cx"], a["cz"], radius, step=a["step"],
        hydrology_root=a["hydrology_root"], hydrology_patch=patch,
    )

    ox, oz = scene["origin"]
    ground = int(scene["surface_y"][a["cx"] - ox, a["cz"] - oz])
    elev_m = float(scene["elev"][a["cx"] - ox, a["cz"] - oz])
    print(
        f"กล้องอยู่ y={ground + int(a['eye'])} (ผิวดิน y={ground}, "
        f"ความสูงจริง {elev_m:.0f} m)"
    )

    yaws = ([a["yaw"] + d for d in (0, 90, 180, 270)]
            if a["panorama"] else [a["yaw"]])
    frames = []
    for yaw in yaws:
        print(f"  เรนเดอร์ yaw {yaw % 360:.0f} deg ...")
        frames.append(render(
            scene, a["cx"], a["cz"], yaw, a["pitch"], a["fov"],
            a["width"], a["height"], a["range"],
            eye_offset=a["eye"], fog_distance=a["fog"],
        ))

    if len(frames) == 1:
        img = frames[0]
        tag = f"_x{a['cx']}_z{a['cz']}_yaw{int(a['yaw']) % 360}"
    else:
        img = np.concatenate(frames, axis=0)
        tag = f"_x{a['cx']}_z{a['cz']}_pano"
    out = a["out"] or os.path.join(HERE, f"view{tag}.png")
    Image.fromarray(img).save(out)
    print(f"บันทึก {out}")


if __name__ == "__main__":
    main()
