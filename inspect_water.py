"""ดูรูปทรงลำน้ำจาก product ของ hydrology ตรง ๆ — ไม่ต้อง build ไม่ต้อง paint

ทำไมต้องมี: `render_preview.py` ระบายสีตามวัสดุผิว และ `render_view.py` มองระดับ
สายตา ทั้งคู่ไม่บอกสามอย่างที่ตัดสินว่าแม่น้ำสวยหรือไม่
  1. ผิวน้ำเป็นขั้นบันไดถี่แค่ไหน และขั้นเรียงตามแกนหรือแนวเฉียง
  2. หน้าตัดลำน้ำ — ตลิ่งซ้าย/ขวาเท่ากันไหม ก้นน้ำมีรูปทรงไหม
  3. แถบที่ terrain ถูกดัดรอบน้ำกว้างแค่ไหน (ที่ผู้เล่นเรียกว่า "เหมือนถนน")

เคยแก้กันโดยให้ผู้เล่นบินเข้าไปดูในเกมทุกรอบ ซึ่งต้อง regenerate + ลบ chunk +
paint ก่อนทุกครั้ง รอบละสิบกว่านาที ตัวนี้อ่าน .npy ตรง ๆ ใช้เวลาไม่กี่วินาที

ใช้:
    python inspect_water.py --at 5933 5905 --hydrology-root hydrology_global_i
    python inspect_water.py --at 5933 5905 --radius 90 --out ดูแม่น้ำ.png
"""

import os
import sys

import numpy as np

import hydrology_patch_io as IO

HERE = os.path.dirname(os.path.abspath(__file__))
UNRESOLVED = np.iinfo(np.int16).min

# สีวนต่อระดับน้ำหนึ่งบล็อก — จงใจให้ต่างกันจัด ๆ เพื่อให้ "ขั้น" มองเห็นทันที
# ถ้าผิวน้ำราบยาว จะเห็นเป็นแถบสีเดียวกว้าง ๆ ถ้าเป็นบันไดจะเห็นเป็นริ้ว
LEVEL_CYCLE = np.asarray([
    (32, 90, 200), (60, 150, 225), (25, 190, 190), (120, 210, 120),
    (235, 210, 70), (240, 140, 50), (215, 70, 70), (170, 80, 190),
], dtype=np.uint8)


def load_window(root, cx, cz, radius):
    names = ("terrain_y", "water_mask", "standing_water_mask", "waterway_mask",
             "surface_y", "depth")
    out = {}
    for name in names:
        path = IO.water_product_path(name, root, HERE)
        if path is None or not os.path.exists(path):
            raise SystemExit(f"ไม่พบ product '{name}' ใน {root}")
        arr = np.load(path, mmap_mode="r")
        z0, z1 = max(0, cz - radius), min(arr.shape[0], cz + radius)
        x0, x1 = max(0, cx - radius), min(arr.shape[1], cx + radius)
        out[name] = np.asarray(arr[z0:z1, x0:x1])
        out["_bounds"] = (x0, x1, z0, z1)
    base = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")
    x0, x1, z0, z1 = out["_bounds"]
    out["base_y"] = np.asarray(base[z0:z1, x0:x1])
    return out


def shade(terrain):
    """hillshade ง่าย ๆ ให้เห็นรูปทรงพื้นเป็นฉากหลัง"""
    terrain = np.asarray(terrain, dtype=np.float32)
    gz, gx = np.gradient(terrain)
    lit = np.clip(0.60 + 0.22 * (gx + gz), 0.25, 1.0)
    grey = (lit * 150).astype(np.uint8)
    return np.dstack([grey, grey, grey])


def level_image(data):
    """ระบายสีวนตามระดับผิวน้ำ ทับบน hillshade ของพื้น"""
    water = data["water_mask"].astype(bool)
    surface = data["surface_y"].astype(np.int32)
    img = shade(data["terrain_y"])
    if water.any():
        level = surface[water] - int(surface[water].min())
        img[water] = LEVEL_CYCLE[level % len(LEVEL_CYCLE)]
    return img


def bank_image(data):
    """แถบที่ terrain ถูกดัด: แดง = ยก, ฟ้า = ไถ, เข้ม = ขยับเยอะ"""
    changed = (data["terrain_y"].astype(np.int32)
               - data["base_y"].astype(np.int32))
    water = data["water_mask"].astype(bool)
    img = shade(data["base_y"])
    strength = np.clip(np.abs(changed) / 6.0, 0.0, 1.0)
    up = (changed > 0) & ~water
    down = (changed < 0) & ~water
    img[up] = (np.stack([
        np.full(up.sum(), 255), 190 - 170 * strength[up],
        190 - 170 * strength[up],
    ], axis=1)).astype(np.uint8)
    img[down] = (np.stack([
        120 - 100 * strength[down], 190 - 120 * strength[down],
        np.full(down.sum(), 255),
    ], axis=1)).astype(np.uint8)
    img[water] = (40, 70, 130)
    return img


def cross_section(data, rows=5):
    """หน้าตัดขวางกลางกรอบ — พิมพ์เป็นตัวเลขเพราะอ่านง่ายกว่ารูปเล็ก ๆ"""
    water = data["water_mask"].astype(bool)
    terr = data["terrain_y"].astype(np.int32)
    base = data["base_y"].astype(np.int32)
    surf = data["surface_y"].astype(np.int32)
    depth = data["depth"].astype(np.int32)
    height = water.shape[0]
    picked = [z for z in np.linspace(height * 0.2, height * 0.8, rows).astype(int)
              if water[z].any()]
    print("\nหน้าตัดขวาง (ซ้าย->ขวา, ตัวเลข = ระดับ):")
    for z in picked:
        wet = np.flatnonzero(water[z])
        a, b = wet.min(), wet.max()
        lo, hi = max(0, a - 4), min(water.shape[1], b + 5)
        line = []
        for x in range(lo, hi):
            if water[z, x]:
                line.append(f"~{surf[z, x]}/{surf[z, x] - depth[z, x]}")
            else:
                mark = "+" if terr[z, x] > base[z, x] else (
                    "-" if terr[z, x] < base[z, x] else " ")

                line.append(f"{mark}{terr[z, x]}")
        left = terr[z, max(0, a - 3):a]
        right = terr[z, b + 1:b + 4]
        gap = (abs(int(left.max()) - int(right.max()))
               if left.size and right.size else 0)
        print(f"  z={z:4d} กว้าง {b - a + 1:2d} | ตลิ่งต่างกัน {gap} | "
              + " ".join(line))
    print("  (~ผิวน้ำ/ก้นน้ำ, + พื้นถูกยก, - พื้นถูกไถ)")


def main():
    argv = sys.argv
    if "--at" not in argv:
        raise SystemExit(__doc__)
    i = argv.index("--at")
    cx, cz = int(argv[i + 1]), int(argv[i + 2])
    radius = int(argv[argv.index("--radius") + 1]) if "--radius" in argv else 90
    root = IO.resolve_hydrology_root(argv)
    data = load_window(root, cx, cz, radius)

    water = data["water_mask"].astype(bool)
    if not water.any():
        raise SystemExit(f"ไม่มีน้ำในกรอบรอบ ({cx}, {cz})")
    surf = data["surface_y"].astype(np.int32)
    pairs = steps = 0
    horizontal = vertical = 0
    for axis, (dst, src) in enumerate((
        (np.s_[1:, :], np.s_[:-1, :]), (np.s_[:, 1:], np.s_[:, :-1]),
    )):
        both = water[dst] & water[src]
        differ = both & (surf[dst] != surf[src])
        pairs += int(both.sum())
        steps += int(differ.sum())
        if axis == 0:
            horizontal = int(differ.sum())
        else:
            vertical = int(differ.sum())
    print(f"รอบ ({cx}, {cz}) รัศมี {radius} | น้ำ {int(water.sum()):,} cells")
    print(f"  ผิวน้ำ {surf[water].min()}..{surf[water].max()} | "
          f"ลดหนึ่งขั้นทุก {pairs / max(1, steps):.1f} บล็อก")
    print(f"  เส้นขั้น แนวนอน {horizontal} : แนวตั้ง {vertical} "
          f"(ต่างกันมาก = ขั้นเรียงตามแกน ไม่ใช่ตั้งฉากกับทิศไหล)")
    cross_section(data)

    try:
        from PIL import Image
    except ImportError:
        return
    left = level_image(data)
    right = bank_image(data)
    gap = np.full((left.shape[0], 6, 3), 255, np.uint8)
    combined = np.concatenate([left, gap, right], axis=1)
    scale = max(1, 900 // max(1, combined.shape[1]))
    if scale > 1:
        combined = combined.repeat(scale, 0).repeat(scale, 1)
    out = (argv[argv.index("--out") + 1] if "--out" in argv
           else os.path.join(HERE, f"water_x{cx}_z{cz}_r{radius}.png"))
    Image.fromarray(combined).save(out)
    print(f"\nบันทึก {out}  (ซ้าย: สีวนตามระดับน้ำ | ขวา: แดง=ยก ฟ้า=ไถ)")


if __name__ == "__main__":
    main()
