"""เรนเดอร์พรีวิวสีผิวดิน + เรือนยอด ก่อนเขียนลงโลกจริง

ใช้:
    python render_preview.py            # ทั้งแผนที่ ย่อเหลือ 2500 px
    python render_preview.py 4000       # ความละเอียดพรีวิวสูงขึ้น
    python render_preview.py 1200 5000 5000 3000   # ซูมดูรอบจุด x,z รัศมี 3000 บล็อก

ปรับกฎได้ที่ surface.py แล้วรันใหม่ ใช้เวลาไม่กี่วินาที
"""

import json
import os
import sys

import numpy as np
from PIL import Image

import config as C
import ecology as E
import surface as S

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    out_px = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
    if len(sys.argv) > 4:
        cx, cz, rad = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
        x0, x1 = max(0, cx - rad), min(C.GRID, cx + rad)
        z0, z1 = max(0, cz - rad), min(C.GRID, cz + rad)
        tag = f"_x{cx}_z{cz}_r{rad}"
    else:
        x0, x1, z0, z1 = 0, C.GRID, 0, C.GRID
        tag = ""

    meta = S.load_meta()
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]

    print(f"อ่าน heightmap ... (พื้นที่ x {x0}..{x1}, z {z0}..{z1})")
    img = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))
    # แกนภาพ [row=z, col=x]
    img = img[z0:z1, x0:x1]

    lcz = np.load(os.path.join(HERE, "landcover.npz"))
    lc = lcz["landcover"][z0:z1, x0:x1]
    wm_path = os.path.join(HERE, "water_mask.npy")
    if os.path.exists(wm_path):
        water_mask = np.load(wm_path, mmap_mode="r")[z0:z1, x0:x1]
        lc = S.apply_water_mask(lc, water_mask)

    # ย่อก่อนคำนวณ เพื่อให้ปรับกฎแล้วเห็นผลไว
    h, w = img.shape
    step = max(1, int(round(max(h, w) / out_px)))
    if step > 1:
        img = img[::step, ::step]
        lc = lc[::step, ::step]
    spacing = C.METERS_PER_BLOCK * step
    print(f"พรีวิว {img.shape[1]}x{img.shape[0]} px  ({spacing:.0f} m ต่อพิกเซล)")

    elev = lo + img.astype(np.float32) / 65535.0 * (hi - lo)
    terrain_y = (
        C.Y_TERRAIN_MIN
        + img.astype(np.float32) / 65535.0
        * (C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN)
    )
    terrain_offset = terrain_y - np.rint(terrain_y)

    print("คำนวณกฎผิวดิน ...")
    surf, forest_p, snow_extra, soil = S.classify(
        elev, lc, spacing, block_m=C.METERS_PER_BLOCK,
        x0=x0, z0=z0, terrain_offset=terrain_offset,
    )

    # ชนิดใบต้องมาจากกฎเดียวกับที่ paint ใช้ ไม่งั้นพรีวิวจะแสดงป่าคนละแบบ
    decor = S.decor_fields(
        elev, spacing, x0=x0, z0=z0, block_m=C.METERS_PER_BLOCK
    )
    species = E.canopy_species(
        elev, spacing, decor["damp"], x0=x0, z0=z0,
        block_m=C.METERS_PER_BLOCK,
    )
    leaf = E.species_leaf_index(species)

    rgb = S.to_rgb(surf, forest_p, leaf, snow_extra, elev)
    out = os.path.join(HERE, f"surface_preview{tag}.png")
    Image.fromarray(rgb).save(out)
    print(f"บันทึก {out}")

    print("\nสัดส่วนบล็อกผิว (รวมเรือนยอด):")
    top = np.where(forest_p > 0.35, leaf, surf)
    total = top.size
    counts = np.bincount(top.ravel(), minlength=len(S.KEYS))
    for i in np.argsort(counts)[::-1]:
        if counts[i] == 0:
            continue
        print(f"  {S.BLOCK_NAMES[i]:<26} {counts[i]/total:6.2%}")
    print(f"\nมีเรือนยอด {(forest_p > 0.02).mean():.2%} ของพื้นที่")

    print("\nองค์ประกอบชนิดไม้ (เฉพาะที่มีป่า):")
    canopy = forest_p > 0.02
    if canopy.any():
        picked = species[canopy]
        for i, name in enumerate(E.SPECIES):
            share = float((picked == i).mean())
            if share:
                print(f"  {name:<12} {share:6.2%}")


if __name__ == "__main__":
    main()
