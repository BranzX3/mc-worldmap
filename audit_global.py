"""ตรวจ product ของ --global ทั้งแผนที่ด้วยเกณฑ์เดียวกับ golden patches

ทำไมต้องแยกจาก golden_patches: ตัวนั้นวัดผลของ `--patch` ซึ่งเป็นเส้นทางคนละเส้น
กับที่เขียนลงโลกจริง  ตัวนี้อ่าน product ที่ `--global` สร้างไว้ตรง ๆ แล้วสุ่ม
หน้าต่างทั่วแผนที่ — รวมบริเวณรอยต่อ tile ซึ่งเป็นที่ที่ --global เพี้ยนได้เอง

ใช้:
    python audit_global.py hydrology_global_w [--windows 40] [--seed 1]
"""
import os
import sys

import numpy as np

import golden_patches as G

HERE = os.path.dirname(os.path.abspath(__file__))
TILE = 512


def audit(root, windows=40, size=384, seed=1):
    # `section_id` กับ `centerline_y` เป็นของบังคับ ไม่ใช่ของแถม — ถ้าไม่มี
    # `patch_metrics` จะบอกว่าวัด unflat_cross_runs ไม่ได้แล้ว raise  product
    # ที่สร้างก่อน 2026-08-18 ไม่มีสองตัวนี้ ต้องรัน --global ใหม่
    names = ("terrain_y", "surface_y", "water_mask", "waterway_mask",
             "standing_water_mask", "depth", "waterfall_top_y",
             "centerline_y", "section_id")
    missing = [n for n in names if not os.path.exists(os.path.join(root, f"{n}.npy"))]
    if missing:
        raise SystemExit(
            f"{root} ขาด product: {', '.join(missing)}\n"
            "ชุดนี้สร้างจากโค้ดรุ่นเก่า — รัน `python hydrology_shape.py --global` ใหม่"
        )
    data = {
        n: np.load(os.path.join(root, f"{n}.npy"), mmap_mode="r") for n in names
    }
    base = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")
    water_small = np.asarray(data["water_mask"][::16, ::16], dtype=bool)
    spots = np.argwhere(water_small)
    rng = np.random.default_rng(seed)
    spots = spots[rng.choice(len(spots), min(len(spots), windows * 4),
                             replace=False)]

    picked, totals = [], {"unflat": 0, "gap": 0, "float": 0}
    canyon, bank = [], []
    for z, x in spots:
        cx, cz = int(x) * 16, int(z) * 16
        # จงใจให้กรอบคร่อมรอยต่อ tile ครึ่งหนึ่ง เพื่อให้ seam อยู่ในสายตา
        if len(picked) % 2 == 0:
            cx = (cx // TILE) * TILE
        if cx < size or cx > 10000 - size or cz < size or cz > 10000 - size:
            continue
        if any(abs(cx - a) < size and abs(cz - b) < size for a, b in picked):
            continue
        picked.append((cx, cz))
        x0, x1 = cx - size // 2, cx + size // 2
        z0, z1 = cz - size // 2, cz + size // 2
        patch = {n: np.asarray(data[n][z0:z1, x0:x1]) for n in names}
        patch["bounds"] = np.asarray([x0, x1, z0, z1])
        m = G.patch_metrics(patch, np.asarray(base[z0:z1, x0:x1]))
        totals["unflat"] += m["unflat_cross_runs"]
        totals["gap"] += m["uncovered_drop"]
        totals["float"] += m["dry_bank_below_water"]
        canyon.append(m["canyon_share_excess"])
        bank.append(m["bank_unwalkable_excess"])
        if m["unflat_cross_runs"] or m["uncovered_drop"] or m["dry_bank_below_water"]:
            print(f"  ({cx},{cz}) unflat {m['unflat_cross_runs']:>4} | "
                  f"ช่องว่าง {m['uncovered_drop']:>4} | ตลิ่งลอย {m['dry_bank_below_water']:>4}")
        if len(picked) >= windows:
            break

    print(f"\nตรวจ {len(picked)} หน้าต่าง จาก {root}")
    print(f"  หน้าตัดไม่ราบรวม {totals['unflat']:,} | ช่องว่างรวม {totals['gap']:,} "
          f"| ตลิ่งลอยรวม {totals['float']:,}")
    print(f"  หุบเกินธรรมชาติ p50 {np.median(canyon) * 100:.1f}% สูงสุด {max(canyon) * 100:.1f}%")
    print(f"  ตลิ่งเกินธรรมชาติ p50 {np.median(bank) * 100:.1f}% สูงสุด {max(bank) * 100:.1f}%")
    return sum(totals.values())


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "hydrology_global"
    win = int(sys.argv[sys.argv.index("--windows") + 1]) if "--windows" in sys.argv else 40
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 1
    raise SystemExit(1 if audit(root, windows=win, seed=seed) else 0)
