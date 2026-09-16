"""แปลง DEM เป็นระดับบล็อก โดยไม่ให้เกิดลายขั้นบันไดตามเส้นคอนทัวร์

ปัญหา: `np.rint(elev)` ทำให้ทุกคอลัมน์ที่ความสูงจริงอยู่ในช่วงครึ่งบล็อกเดียวกัน
ตกลงบนเลขจำนวนเต็มเดียวกัน ขอบระหว่างสองระดับจึงเป็น "เส้นคอนทัวร์" ที่เรียบและ
ต่อเนื่องยาวหลายร้อยบล็อก บนไหล่เขาที่ลาดน้อยขั้นพวกนี้กว้างมากจนภูเขาดูเหมือน
แผนที่ภูมิประเทศแบบชั้น ๆ ซึ่งเป็นสัญญาณที่บอกว่า "สร้างจาก heightmap" ชัดที่สุด

วิธีแก้: เติม noise แอมพลิจูดต่ำกว่าครึ่งบล็อกก่อนปัด ขอบขั้นจะขยับไปมารอบเส้น
คอนทัวร์เดิมจนแตกเป็นขอบหยักธรรมชาติ ความสูงไม่เคยเพี้ยนเกินครึ่งบล็อกจากของจริง
จึงไม่เสียความถูกต้องของ DEM

สำคัญ: ต้องเป็น noise ที่ต่อเนื่องเชิงพื้นที่ (value noise) ไม่ใช่ white noise
ถ้าใช้ white noise จะได้ผิวหยาบเป็นเม็ดทั่วทั้งลูก ซึ่งแย่กว่าลายขั้นเดิม

และ dither เฉพาะที่ลาด — บนผาชันไม่มีลายขั้นให้แก้อยู่แล้ว การไปกวนตรงนั้นมีแต่
ทำให้หน้าผาที่คมอยู่แล้วรุ่งริ่ง

ใช้:
    python terrain_shape.py            # สร้าง terrain_y.npy + terrain_sub.npy
    python terrain_shape.py --preview  # + ภาพเทียบก่อน/หลังบนแปลงตัวอย่าง
"""

import os
import sys

import numpy as np

import config as C
import surface as S
from pipeline_progress import use_utf8_stdout

HERE = os.path.dirname(os.path.abspath(__file__))

# แอมพลิจูดสูงสุดของ dither (บล็อก) — ต้อง < 0.5 เพื่อไม่ให้ระดับเพี้ยนเกิน
# ครึ่งบล็อกจาก DEM จริง 0.42 ให้ขอบแตกชัดโดยยังไม่ทำให้พื้นที่ราบเป็นคลื่น
DITHER_AMPLITUDE = 0.49

# ความยาวคลื่นของ dither (บล็อก) — สั้นเกินได้ผิวเป็นเม็ด ยาวเกินขอบขั้นยังเรียบ
# 5 บล็อก = 20 m — sweep แล้วพบว่าคลื่นสั้นกัดลานได้ดีที่สุด (ยิ่งยาวยิ่งแค่
# เลื่อนทั้งลานไปทั้งก้อนแทนที่จะทำให้ขอบแตก)
DITHER_SCALE_BLOCKS = 5.0
DITHER_SEED = 6421

# dither ต้องเป็น band-pass บนความชัน ไม่ใช่ low-pass
#
# ลายขั้นเกิดเฉพาะตอนที่ความสูงค่อย ๆ ไต่ข้ามเส้นแบ่งบล็อก คือความชัน "น้อยแต่
# ไม่เป็นศูนย์" ที่ความชันเป็นศูนย์จริง (ผิวทะเลสาบ พื้นหุบเขาราบ ลานหิน) ไม่มี
# ขั้นให้แก้ การ dither ตรงนั้นมีแต่เปลี่ยนพื้นเรียบให้เป็นตุ่มขรุขระ ซึ่งแย่กว่า
# ลายขั้นมาก และทำผิวทะเลสาบพังทั้งแผ่น
SLOPE_FLAT = 0.02              # ต่ำกว่านี้ = ราบจริง ห้ามแตะ
SLOPE_RAMP_IN = 0.06           # ถึงตรงนี้ dither เต็มที่
SLOPE_RAMP_OUT = 1.20          # เริ่มลดเมื่อชันขึ้น
SLOPE_NO_DITHER = 2.50         # ชันกว่านี้เป็นผา ไม่มีลายขั้นให้แก้


def elevation_to_height(elev_m, meta=None):
    """ความสูงจริง (m) -> ระดับบล็อกแบบต่อเนื่อง (ยังไม่ปัด)"""
    meta = meta or S.load_meta()
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]
    span = max(1e-9, hi - lo)
    return (
        C.Y_TERRAIN_MIN
        + (np.asarray(elev_m, dtype=np.float32) - lo) / span
        * (C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN)
    )


def _build_uniformiser(samples=384):
    """ตาราง CDF สำหรับดัดการกระจายของ value noise ให้สม่ำเสมอ

    value noise เป็นผลรวมของค่าสุ่มที่ถูก interpolate จึงมีการกระจายแบบระฆังคว่ำ
    (std 0.32, มีแค่ 5% ที่เกิน 0.25) การเอาไปคูณ amplitude ตรง ๆ ทำให้เกือบทุก
    เซลล์ได้แรงผลักน้อยเกินกว่าจะพลิกการปัด ลายขั้นจึงไม่หายไปไหน

    ดัดด้วย CDF ของตัวมันเองแล้วได้การกระจายสม่ำเสมอ ครึ่งหนึ่งของเซลล์จะได้แรง
    ผลักเกินครึ่งบล็อก ซึ่งพอจะย้ายขอบขั้นได้จริง

    ตารางสร้างจากตัวอย่างคงที่ตอน import จึงเป็นค่าเดียวกันทุกเครื่องทุกกรอบ
    (ห้ามวัดจากข้อมูลจริงต่อ tile เพราะแต่ละ tile จะได้ตารางคนละชุด)
    """
    reference = S.smooth_noise(
        0, 0, (samples, samples), DITHER_SCALE_BLOCKS, DITHER_SEED, 2
    ).ravel()
    reference.sort()
    quantiles = np.linspace(0.0, 1.0, 257, dtype=np.float32)
    knots = np.quantile(reference, quantiles).astype(np.float32)
    # ทำให้เพิ่มขึ้นอย่างเข้มงวด ไม่งั้น np.interp คืนค่าเพี้ยนตรงช่วงที่ค่าซ้ำ
    knots = np.maximum.accumulate(knots + np.arange(knots.size) * 1e-7)
    return knots, (quantiles * 2.0 - 1.0).astype(np.float32)


_UNIFORM_KNOTS, _UNIFORM_VALUES = _build_uniformiser()


def uniformise(noise):
    """ดัด value noise ให้กระจายสม่ำเสมอใน -1..1 โดยคงลำดับและความต่อเนื่อง"""
    return np.interp(
        np.asarray(noise, dtype=np.float32), _UNIFORM_KNOTS, _UNIFORM_VALUES
    ).astype(np.float32)


def dither_field(shape, x0=0, z0=0, scale_blocks=DITHER_SCALE_BLOCKS,
                 seed=DITHER_SEED, amplitude=DITHER_AMPLITUDE):
    """noise ต่อเนื่องกระจายสม่ำเสมอ -amplitude..amplitude ยึดพิกัดโลก

    ยึดพิกัดโลกเพื่อให้ build_terrain กับ paint_surface ที่ทำคนละกรอบได้ค่าตรง
    กันเสมอ — ถ้าไม่ตรง ผิวดินที่ paint วางจะไม่อยู่ที่เดียวกับหินที่ build ถม
    """
    noise = S.smooth_noise(x0, z0, shape, max(2.0, scale_blocks), seed, 2)
    return uniformise(noise) * amplitude


def slope_blocks(height, spacing_blocks=1.0):
    """ความชันเป็น "บล็อกที่สูงขึ้นต่อบล็อกที่เดินไป" จากระดับบล็อกต่อเนื่อง"""
    height = np.asarray(height, dtype=np.float32)
    dz, dx = np.gradient(height, spacing_blocks)
    return np.hypot(dx, dz)


def dither_weight(slope):
    """band-pass: 0 บนพื้นราบจริง, 1 บนไหล่เขาลาดน้อย, 0 บนผาชัน"""
    slope = np.asarray(slope, dtype=np.float32)
    rise = np.clip(
        (slope - SLOPE_FLAT) / max(1e-6, SLOPE_RAMP_IN - SLOPE_FLAT), 0.0, 1.0
    )
    fall = np.clip(
        (SLOPE_NO_DITHER - slope) / max(1e-6, SLOPE_NO_DITHER - SLOPE_RAMP_OUT),
        0.0, 1.0,
    )
    return rise * fall


def despeckle(height_y):
    """ลบยอดแหลม/หลุมสูงบล็อกเดียวที่โดดออกจากเพื่อนบ้านทุกด้าน

    เกิดจากพิกเซล DEM เดี่ยว ๆ ที่ค่าเพี้ยน พอปัดเป็นบล็อกจะกลายเป็นเสาโด่หรือ
    รูลึกหนึ่งบล็อก ซึ่งในเกมเห็นชัดมากและไม่มีอยู่ในภูมิประเทศจริง
    """
    y = np.asarray(height_y, dtype=np.int32).copy()
    up = np.empty_like(y)
    down = np.empty_like(y)
    left = np.empty_like(y)
    right = np.empty_like(y)
    up[1:], up[0] = y[:-1], y[0]
    down[:-1], down[-1] = y[1:], y[-1]
    left[:, 1:], left[:, 0] = y[:, :-1], y[:, 0]
    right[:, :-1], right[:, -1] = y[:, 1:], y[:, -1]

    neighbours = np.stack([up, down, left, right])
    lo = neighbours.min(axis=0)
    hi = neighbours.max(axis=0)
    # เพื่อนบ้านทั้งสี่อยู่ระดับเดียวกันหมด แต่ตัวเองต่างออกไป = จุดโดด
    flat = lo == hi
    spike = flat & (y != lo)
    y[spike] = lo[spike]
    return y, int(spike.sum())


def quantize(elev_m, x0=0, z0=0, meta=None, amplitude=DITHER_AMPLITUDE,
             clean=True, protect=None, relief=True, relief_stats=None,
             relief_features=None):
    """คืน (terrain_y int32, sub float32) — ระดับบล็อกและเศษที่เหลือ

    sub คือส่วนที่ปัดทิ้งไป (ต่อเนื่อง - ปัด) อยู่ในช่วงราว -0.5..0.5
    paint_surface เอาไปใช้เกลี่ยความหนาหิมะให้ยอดหิมะต่อเนื่องข้ามขั้นบล็อก

    protect: mask ที่ห้าม dither เด็ดขาด — ใช้กับผืนน้ำ ผิวทะเลสาบต้องแบนสนิท
    หนึ่งระดับ การ dither แม้ครึ่งบล็อกจะทำให้ผิวน้ำเป็นตุ่มทั้งทะเลสาบ
    """
    continuous = elevation_to_height(elev_m, meta)
    if relief:
        # ภูมิสัณฐานย่อยต้องมาก่อน dither — dither มีหน้าที่กัดขอบขั้นของผลลัพธ์
        # สุดท้าย ถ้าใส่ทีหลังชั้นหินที่เพิ่งสร้างจะถูกกวนจนขอบพร่า
        import micro_relief as M

        kwargs = {} if relief_features is None else {
            "features": tuple(relief_features)
        }
        continuous, stats = M.apply(
            continuous, elev_m, x0=x0, z0=z0, protect=protect, **kwargs
        )
        if relief_stats is not None:
            relief_stats.update(stats)
    if amplitude > 0.0:
        weight = dither_weight(slope_blocks(continuous))
        if protect is not None:
            weight = np.where(np.asarray(protect, dtype=bool), 0.0, weight)
        continuous = continuous + dither_field(
            continuous.shape, x0=x0, z0=z0, amplitude=amplitude
        ) * weight
    y = np.rint(continuous).astype(np.int32)
    if clean:
        y, _ = despeckle(y)
    return y, (continuous - y).astype(np.float32)


def contour_straightness_counts(height_y):
    """คืน (จำนวนขอบที่เป็นเส้นตรงยาว, จำนวนขอบทั้งหมด) — สะสมข้าม tile ได้"""
    y = np.asarray(height_y, dtype=np.int32)
    step_x = np.zeros(y.shape, dtype=bool)
    step_z = np.zeros(y.shape, dtype=bool)
    step_x[:, :-1] = y[:, :-1] != y[:, 1:]
    step_z[:-1, :] = y[:-1, :] != y[1:, :]
    total = int((step_x | step_z).sum())
    if not total:
        return 0, 0
    run = 5
    half = run // 2
    straight_z = np.ones(y.shape, dtype=bool)
    straight_x = np.ones(y.shape, dtype=bool)
    for k in range(run):
        shift = k - half
        straight_z[half:y.shape[0] - half] &= np.roll(
            step_x, -shift, axis=0
        )[half:y.shape[0] - half]
        straight_x[:, half:y.shape[1] - half] &= np.roll(
            step_z, -shift, axis=1
        )[:, half:y.shape[1] - half]
    return int((straight_x | straight_z).sum()), total


def terrace_width_counts(height_y, continuous, max_slope=0.35, axis=1):
    """คืน (คู่ที่ระดับเท่ากัน, จำนวนขอบ) บนที่ลาด — สะสมข้าม tile ได้"""
    y = np.asarray(height_y, dtype=np.int32)
    gentle = slope_blocks(np.asarray(continuous, dtype=np.float32)) <= max_slope
    if not gentle.any():
        return 0, 0
    same = np.zeros(y.shape, dtype=bool)
    if axis == 1:
        same[:, :-1] = (y[:, :-1] == y[:, 1:]) & gentle[:, :-1]
    else:
        same[:-1, :] = (y[:-1, :] == y[1:, :]) & gentle[:-1, :]
    return int(same.sum()), int((gentle & ~same).sum())


def contour_straightness(height_y):
    """สัดส่วนขอบขั้นที่วิ่งเป็นเส้นตรงยาว — ค่าต่ำ = ขอบหยักแบบธรรมชาติ

    ขอบขั้นที่มีขอบแนวเดียวกันต่ออีกสี่ช่องคือเส้นคอนทัวร์ ซึ่งเป็นสิ่งที่ตาจับ
    ได้ว่ามาจาก heightmap ขอบที่หยักสั้น ๆ อ่านเป็นชั้นหินธรรมชาติ
    """
    y = np.asarray(height_y, dtype=np.int32)
    step_x = np.zeros(y.shape, dtype=bool)
    step_z = np.zeros(y.shape, dtype=bool)
    step_x[:, :-1] = y[:, :-1] != y[:, 1:]
    step_z[:-1, :] = y[:-1, :] != y[1:, :]
    total = int((step_x | step_z).sum())
    if not total:
        return 0.0
    run = 5
    straight_z = np.ones(y.shape, dtype=bool)
    straight_x = np.ones(y.shape, dtype=bool)
    for k in range(run):
        shift = k - run // 2
        straight_z[run // 2:y.shape[0] - run // 2] &= np.roll(
            step_x, -shift, axis=0
        )[run // 2:y.shape[0] - run // 2]
        straight_x[:, run // 2:y.shape[1] - run // 2] &= np.roll(
            step_z, -shift, axis=1
        )[:, run // 2:y.shape[1] - run // 2]
    return float((straight_x | straight_z).sum()) / total


def terrace_width(height_y, continuous=None, max_slope=0.35, axis=1):
    """ความกว้างเฉลี่ยของ "ลาน" ที่ระดับเดียวกัน เฉพาะบริเวณที่ลาด

    นี่คือสิ่งที่ตาเห็นเป็นลายขั้น: บนไหล่เขาลาดน้อย การปัดตรง ๆ ทำให้เกิดลาน
    ราบกว้างหลายสิบบล็อกคั่นด้วยขั้น 1 บล็อก ยิ่งลานแคบและกว้างไม่เท่ากัน ยิ่ง
    ดูเป็นภูมิประเทศจริง

    จำกัดเฉพาะที่ลาด (ไม่เกิน max_slope) เพราะบนผาชันไม่มีลานอยู่แล้ว
    """
    y = np.asarray(height_y, dtype=np.int32)
    if continuous is None:
        continuous = y.astype(np.float32)
    gentle = slope_blocks(np.asarray(continuous, dtype=np.float32)) <= max_slope
    if not gentle.any():
        return 0.0
    same = np.zeros(y.shape, dtype=bool)
    if axis == 1:
        same[:, :-1] = (y[:, :-1] == y[:, 1:]) & gentle[:, :-1]
    else:
        same[:-1, :] = (y[:-1, :] == y[1:, :]) & gentle[:-1, :]
    # ความกว้างเฉลี่ย = (จำนวนคู่ที่ระดับเท่ากัน / จำนวนขอบ) + 1
    edges = int((gentle & ~same).sum())
    return float(same.sum()) / max(1, edges) + 1.0


def _load_elevation(meta):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    hm = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]
    return lo + hm.astype(np.float32) / 65535.0 * (hi - lo)


# reach ที่ไกลสุดของกฎทุกตัว: talus ไหลลง 10 บล็อก + slope/despeckle อีก 2
# ใช้ 20 เผื่อไว้ — ทุกกฎเป็น local ทั้งหมด การแบ่ง tile จึงให้ผลเท่ากับทำทีเดียว
TILE_PAD = 20
TILE_SIZE = 1024


def main():
    use_utf8_stdout()
    meta = S.load_meta()
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]
    print("อ่าน heightmap ...")
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    hm = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))
    n = hm.shape[0]
    print(f"  {n}x{n} | {lo:.0f}-{hi:.0f} m | "
          f"{meta['meters_per_block_v']:.2f} m/block")

    water_path = os.path.join(HERE, "water_mask.npy")
    water_all = np.load(water_path, mmap_mode="r") if os.path.exists(water_path) else None
    if water_all is None:
        print("  [เตือน] ไม่พบ water_mask.npy — ผิวทะเลสาบอาจถูก dither จนเป็นตุ่ม")
    else:
        print(f"  ป้องกันผืนน้ำจาก dither {float(np.asarray(water_all[::16, ::16]).mean()):.2%} ของพื้นที่")

    out_y = np.zeros((n, n), dtype=np.int16)
    out_sub = np.zeros((n, n), dtype=np.int8)
    relief_features = None
    if "--arete" in sys.argv:
        relief_features = ("arete", "bedding", "talus", "doline")
        print("  [ทดลอง] เปิด arête ridge sharpening")
    relief_area = {}
    relief_max = {}
    straight_hit = straight_tot = 0
    same_hit = edge_tot = 0
    moved = np.zeros(3, dtype=np.int64)
    spikes = 0

    # แบ่ง tile เพื่อไม่ให้ต้องถือ array เต็มแผนที่หลายชุดพร้อมกัน — เดิมใช้ RAM
    # เกิน 8 GB จน MemoryError บนเครื่องที่มีงานอื่นรันอยู่
    tiles = [(a, min(a + TILE_SIZE, n)) for a in range(0, n, TILE_SIZE)]
    total = len(tiles) ** 2
    done = 0
    for z0, z1 in tiles:
        for x0, x1 in tiles:
            pz0, pz1 = max(0, z0 - TILE_PAD), min(n, z1 + TILE_PAD)
            px0, px1 = max(0, x0 - TILE_PAD), min(n, x1 + TILE_PAD)
            sub_img = hm[pz0:pz1, px0:px1].astype(np.float32)
            elev = lo + sub_img / 65535.0 * (hi - lo)
            protect = (
                np.asarray(water_all[pz0:pz1, px0:px1], dtype=bool)
                if water_all is not None else None
            )
            stats = {}
            y, frac = quantize(
                elev, x0=pz0, z0=px0, meta=meta, protect=protect,
                relief_stats=stats,
                relief_features=relief_features,
            )
            plain = np.rint(elevation_to_height(elev, meta)).astype(np.int32)
            _clean, tile_spikes = despeckle(plain)

            cz, cx = slice(z0 - pz0, z1 - pz0), slice(x0 - px0, x1 - px0)
            out_y[z0:z1, x0:x1] = y[cz, cx].astype(np.int16)
            out_sub[z0:z1, x0:x1] = np.clip(
                np.rint(frac[cz, cx] * 127.0), -127, 127
            ).astype(np.int8)

            core = np.abs(y[cz, cx] - plain[cz, cx])
            moved += np.array([
                int((core == 0).sum()), int((core == 1).sum()),
                int((core > 1).sum())
            ], dtype=np.int64)
            spikes += tile_spikes

            a, b = contour_straightness_counts(y[cz, cx])
            straight_hit += a
            straight_tot += b
            cont_core = elevation_to_height(elev, meta)[cz, cx]
            a, b = terrace_width_counts(y[cz, cx], cont_core)
            same_hit += a
            edge_tot += b
            for key, item in stats.items():
                relief_area[key] = relief_area.get(key, 0) + item["area"]
                relief_max[key] = max(relief_max.get(key, 0.0), item["max"])

            done += 1
            sys.stdout.write("\r  tile %d/%d   " % (done, total))
            sys.stdout.flush()
    print()

    if relief_area:
        print("\nภูมิสัณฐานย่อยที่เติม (พื้นที่ = ส่วนที่ขยับเกินครึ่งบล็อก):")
        label = {"bedding": "ชั้นหินยื่นบนหน้าผา",
                 "talus": "กองหินเชิงผา",
                 "doline": "หลุมยุบหินปูน",
                 "arete": "สันเขาคม arête"}
        for key in relief_area:
            print(f"  {label.get(key, key):<22} พื้นที่ {relief_area[key]/total:6.2%} | "
                  f"ลึก/หนาสุด {relief_max[key]:5.1f} บล็อก")

    after = straight_hit / max(1, straight_tot)
    width = same_hit / max(1, edge_tot) + 1.0
    tot_cells = moved.sum()
    print(f"\nขอบขั้นที่เป็นเส้นตรงยาว (ต่ำ = ดี): {after:.3f}")
    print(f"ความกว้างลานบนไหล่เขาลาด: {width:.1f} บล็อก")
    print(f"ระดับที่ขยับจาก DEM: ไม่ขยับ {moved[0]/tot_cells:.1%} | "
          f"1 บล็อก {moved[1]/tot_cells:.1%} | เกิน 1 บล็อก {moved[2]/tot_cells:.2%}")
    print(f"จุดโดดที่ลบทิ้ง: {spikes:,}")

    np.save(os.path.join(HERE, "terrain_y.npy"), out_y)
    np.save(os.path.join(HERE, "terrain_sub.npy"), out_sub)
    print(
        "\nบันทึก terrain_y.npy (int16) + terrain_sub.npy (int8) "
        "— build_terrain.py กับ paint_surface.py ต้องอ่านสองไฟล์นี้"
    )


if __name__ == "__main__":
    main()
