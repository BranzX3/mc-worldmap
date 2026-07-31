"""สร้างแผนที่ความลึกน้ำจาก mask น้ำของ OSM

ทำไมต้องมีขั้นนี้: LiDAR ยิงทะลุน้ำไม่ได้ ค่าความสูงตรงทะเลสาบใน DEM คือ "ผิวน้ำ"
ไม่ใช่ก้นทะเลสาบ ภูมิประเทศที่สร้างจาก heightmap จึงเป็นที่ราบหินตันแบนๆ ตรงนั้น
ต้องขุดแอ่งขึ้นมาเองแล้วค่อยเติมน้ำ

ความลึกคำนวณจากระยะห่างจากฝั่ง — ยิ่งกลางยิ่งลึก ไล่แบบ exponential ให้มีชายฝั่ง
ตื้นก่อนแล้วค่อยดิ่ง เหมือนทะเลสาบธารน้ำแข็งจริง

ระยะจากฝั่งถึงกลางทะเลสาบไกลกว่าขอบ padding ของ region มาก จึงต้องคำนวณทีเดียว
ทั้งแผนที่แล้วเก็บไว้ ให้ paint_surface.py อ่านทีหลัง

ผลลัพธ์: water_depth.npy (uint8 = ความลึกเป็นบล็อก)
"""

import os

import numpy as np

import config as C
import surface as S
from pipeline_progress import use_utf8_stdout

HERE = os.path.dirname(os.path.abspath(__file__))

# กำหนดเป็นบล็อกโดยตรงเพื่อให้ bathymetry ไม่เปลี่ยนเมื่อปรับ vertical scale
MAX_DEPTH_BLOCKS = int(getattr(C, "WATER_MAX_DEPTH_BLOCKS", 30))
SHELF = float(getattr(C, "WATER_SHELF_BLOCKS", 26.0))


def chamfer_distance(mask):
    """ระยะจากขอบ mask (หน่วยบล็อก) แบบสองรอบ ไม่ต้องพึ่ง scipy

    ใช้ระยะ chamfer 3-4 หารด้วย 3 ให้ใกล้เคียงระยะยุคลิด คลาดเคลื่อนไม่กี่ %
    ซึ่งเพียงพอสำหรับกำหนดความลึก
    """
    INF = np.int32(1 << 29)
    d = np.where(mask, INF, np.int32(0))
    h, w = d.shape

    # การแพร่แนวนอนในแถวเดียวเป็น prefix-minimum ไม่ต้องวนทีละ cell
    #
    #   row[j] = min(row[j], row[j-1] + 3)  ที่ทำไล่ตามลำดับ
    #     = min ของ (row[k] + 3*(j-k)) ทุก k <= j
    #     = 3j + min ของ (row[k] - 3k) ทุก k <= j
    #
    # ก้อนหลังคือ np.minimum.accumulate ตรง ๆ ทางย้อนกลับใช้สูตรกระจกเงา
    # เดิมลูปนี้เป็น Python 10000 รอบต่อแถว x 10000 แถว x 2 pass = 200 ล้านรอบ
    # วัดจริง (สุ่ม 3% water, extrapolate O(n^2) จาก 4000^2): ~32 วิ -> ~1.4 วิ
    #
    # ค่าสูงสุดระหว่างทางคือ INF + 3w = 536,900,909 ยังอยู่ในช่วง int32
    offsets = np.arange(w, dtype=np.int32) * np.int32(3)
    scratch = np.empty(w, dtype=np.int32)

    # รอบไปข้างหน้า
    for i in range(h):
        row = d[i]
        if i > 0:
            prev = d[i - 1]
            np.minimum(row, prev + 3, out=row)
            np.minimum(row[1:], prev[:-1] + 4, out=row[1:])
            np.minimum(row[:-1], prev[1:] + 4, out=row[:-1])
        np.subtract(row, offsets, out=scratch)
        np.minimum.accumulate(scratch, out=scratch)
        np.add(scratch, offsets, out=row)
    # รอบย้อนกลับ
    for i in range(h - 1, -1, -1):
        row = d[i]
        if i < h - 1:
            nxt = d[i + 1]
            np.minimum(row, nxt + 3, out=row)
            np.minimum(row[1:], nxt[:-1] + 4, out=row[1:])
            np.minimum(row[:-1], nxt[1:] + 4, out=row[:-1])
        np.add(row, offsets, out=scratch)
        np.minimum.accumulate(scratch[::-1], out=scratch[::-1])
        np.subtract(scratch, offsets, out=row)
    # in-place หาร — `d.astype(np.float32) / 3.0` ทิ้ง float32 เต็มแผนที่
    # (400 MB) เพิ่มอีกก้อนโดยไม่จำเป็น
    distance = d.astype(np.float32)
    distance /= 3.0
    return distance


def base_depth_from_water_mask(water, max_depth_blocks=MAX_DEPTH_BLOCKS,
                               shelf=SHELF):
    """Distance-only depth before adding bathymetric relief."""
    water = np.asarray(water, dtype=bool)
    dist = chamfer_distance(water)
    dist[~water] = 0.0
    depth = max_depth_blocks * (1.0 - np.exp(-dist / float(shelf)))
    depth = np.where(water, np.maximum(1.0, np.rint(depth)), 0.0)
    return depth.astype(np.uint8)


def depth_from_water_mask(water, max_depth_blocks=MAX_DEPTH_BLOCKS, shelf=SHELF):
    """สร้างความลึกจากระยะถึงฝั่ง โดยคง shelf ตื้นและลาดลงอย่างต่อเนื่อง"""
    depth = base_depth_from_water_mask(water, max_depth_blocks, shelf)
    return add_underwater_relief(
        depth, water, max_depth_blocks=max_depth_blocks
    )


def _expand_cardinal(mask, steps):
    expanded = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(0, int(steps))):
        nxt = expanded.copy()
        nxt[1:] |= expanded[:-1]
        nxt[:-1] |= expanded[1:]
        nxt[:, 1:] |= expanded[:, :-1]
        nxt[:, :-1] |= expanded[:, 1:]
        expanded = nxt
    return expanded


def _neighbour_count8(mask):
    mask = np.asarray(mask, dtype=bool)
    count = np.zeros(mask.shape, dtype=np.uint8)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if not (dx or dz):
                continue
            z_src = slice(max(0, -dz), mask.shape[0] - max(0, dz))
            x_src = slice(max(0, -dx), mask.shape[1] - max(0, dx))
            z_dst = slice(max(0, dz), mask.shape[0] - max(0, -dz))
            x_dst = slice(max(0, dx), mask.shape[1] - max(0, -dx))
            count[z_dst, x_dst] += mask[z_src, x_src]
    return count


def naturalize_lake_edges(water, preliminary_depth, tile_size=512, seed=9321):
    """Perturb broad lake edges by at most one block while preserving streams."""
    water = np.asarray(water, dtype=bool)
    preliminary_depth = np.asarray(preliminary_depth)
    if water.shape != preliminary_depth.shape:
        raise ValueError("water and preliminary_depth must have the same shape")

    neighbours = _neighbour_count8(water)
    core = water & (preliminary_depth >= 3)
    lake_zone = _expand_cardinal(core, C.LAKE_SHORE_SEARCH_BLOCKS)
    near_lake = _expand_cardinal(core, C.LAKE_SHORE_SEARCH_BLOCKS + 1)

    # Never erode thin streams (few wet neighbours) or the deep lake core.
    removable = water & lake_zone & ~core & (neighbours >= 5)
    addable = ~water & near_lake & (neighbours >= 3)

    result = water.copy()
    height, width = water.shape
    for z0 in range(0, height, tile_size):
        z1 = min(height, z0 + tile_size)
        for x0 in range(0, width, tile_size):
            x1 = min(width, x0 + tile_size)
            shape_xz = (x1 - x0, z1 - z0)
            broad = S.smooth_noise(x0, z0, shape_xz, 18.0, seed, 2).T
            detail = S.smooth_noise(x0, z0, shape_xz, 6.0, seed + 71, 2).T
            score = 0.78 * broad + 0.22 * detail

            remove = removable[z0:z1, x0:x1] & (score < -0.26)
            add = addable[z0:z1, x0:x1] & (score > 0.34)
            tile = result[z0:z1, x0:x1]
            tile[remove] = False
            tile[add] = True

    result[core] = True
    return result


def add_underwater_relief(depth, water, max_depth_blocks=MAX_DEPTH_BLOCKS,
                          tile_size=512, seed=8128):
    """เพิ่มแอ่งย่อยและสันใต้น้ำ โดยไม่เปลี่ยน shelf ลึก 1–2 บล็อก

    คำนวณเป็น tile แต่ noise ยึดพิกัดโลก จึงได้ผลเหมือนกันไม่ว่าจะเปลี่ยน
    tile_size เท่าใด และไม่เกิดรอยต่อในงาน fullscale
    """
    depth = np.asarray(depth, dtype=np.uint8)
    water = np.asarray(water, dtype=bool)
    if depth.shape != water.shape:
        raise ValueError("depth and water must have the same shape")

    out = depth.copy()
    height, width = depth.shape
    for z0 in range(0, height, tile_size):
        z1 = min(height, z0 + tile_size)
        for x0 in range(0, width, tile_size):
            x1 = min(width, x0 + tile_size)
            shape_xz = (x1 - x0, z1 - z0)
            broad = S.smooth_noise(x0, z0, shape_xz, 180.0, seed, 3).T
            medium = S.smooth_noise(x0, z0, shape_xz, 58.0, seed + 101, 2).T
            ridge_noise = S.smooth_noise(
                x0, z0, shape_xz, 92.0, seed + 211, 2
            ).T
            cliff_noise = S.smooth_noise(
                x0, z0, shape_xz, 138.0, seed + 307, 2
            ).T

            base = depth[z0:z1, x0:x1].astype(np.float32)
            wet = water[z0:z1, x0:x1]
            influence = np.clip((base - 2.0) / 12.0, 0.0, 1.0)

            # broad/medium noise makes basins and shelves; a narrow band around
            # zero of another field becomes a low underwater ridge.
            relief = (2.8 * broad + 1.6 * medium) * influence
            ridge = np.clip(1.0 - np.abs(ridge_noise) / 0.16, 0.0, 1.0)
            relief -= 3.0 * ridge * influence
            # Discrete offsets across long noise contours form sparse 3–4
            # block underwater escarpments.  Keep them away from the shelf.
            cliff_influence = np.clip((base - 7.0) / 8.0, 0.0, 1.0)
            relief += np.where(cliff_noise > 0.24, 3.0, 0.0) * cliff_influence
            relief -= np.where(cliff_noise < -0.38, 2.0, 0.0) * cliff_influence

            changed = np.rint(base + relief)
            changed = np.clip(changed, 1, max_depth_blocks)
            changed[base <= 2] = base[base <= 2]
            tile = out[z0:z1, x0:x1]
            tile[wet] = changed[wet].astype(np.uint8)
    return out


def main():
    use_utf8_stdout()
    meta = S.load_meta()
    v_scale = meta["meters_per_block_v"]
    print(
        f"ความลึกสูงสุด {MAX_DEPTH_BLOCKS} บล็อก "
        f"(ประมาณ {MAX_DEPTH_BLOCKS * v_scale:.0f} m ที่ vertical scale ปัจจุบัน "
        f"= {v_scale:.2f} m/block, เพี้ยน {S.vertical_distortion(meta):.2f}x)"
    )

    lc = np.load(os.path.join(HERE, "landcover.npz"))["landcover"]
    source_water = lc == S.LC["water"]
    print(f"พื้นที่น้ำ OSM {source_water.mean():.2%}  ({source_water.sum():,} บล็อก)")

    print("ปรับขอบทะเลสาบและคำนวณระยะจากฝั่ง ...")
    preliminary = base_depth_from_water_mask(source_water)
    water = naturalize_lake_edges(source_water, preliminary)
    depth = depth_from_water_mask(water)

    np.save(os.path.join(HERE, "water_mask.npy"), water)
    np.save(os.path.join(HERE, "water_depth.npy"), depth)
    removed = int((source_water & ~water).sum())
    added = int((~source_water & water).sum())
    print(f"ขอบธรรมชาติ: ตัด {removed:,} | เติม {added:,} บล็อก")

    d = depth[water]
    print(f"\nความลึกที่ได้ (บล็อก): ต่ำสุด {d.min()}  กลาง {int(np.median(d))}  "
          f"สูงสุด {d.max()}")
    print(f"  ตื้น 1-2 บล็อก (ลำธาร/ริมฝั่ง): {(d <= 2).mean():.1%}")
    print(f"  ลึกเกิน 10 บล็อก (กลางทะเลสาบ): {(d > 10).mean():.1%}")
    print(f"\nบันทึก water_mask.npy + water_depth.npy")

    # ภาพตรวจ
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        # ย่อก่อนแล้วค่อยลงสี — เดิมสร้างภาพเต็ม 10000x10000x3 (300 MB) บวก
        # int32 เต็มแผนที่อีกสองก้อน (400 MB ต่อก้อน) แล้วโยนทิ้งเกือบหมดตอน
        # resize เหลือ 1600px  การเลือก index แบบเดียวกับ NEAREST ของ PIL
        # ให้ภาพเดิมทุกพิกเซลด้วยหน่วยความจำ ~8 MB
        size = 1600
        rows = ((np.arange(size) + 0.5) * depth.shape[0] / size).astype(np.intp)
        cols = ((np.arange(size) + 0.5) * depth.shape[1] / size).astype(np.intp)
        picked = np.ix_(rows, cols)
        small_depth = depth[picked].astype(np.int32)
        small_water = water[picked]
        img = np.zeros((size, size, 3), np.uint8)
        img[..., 2] = np.where(small_water, 90 + small_depth * 8, 0).clip(0, 255)
        img[..., 1] = np.where(small_water, 60 + small_depth * 3, 0).clip(0, 255)
        Image.fromarray(img).save(os.path.join(HERE, "water_depth.png"))
        print("บันทึก water_depth.png (ยิ่งสว่าง = ยิ่งลึก)")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
