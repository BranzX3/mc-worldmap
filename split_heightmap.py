"""ตัด heightmap.png เป็นแปลงย่อยสำหรับ import เข้า Axiom ทีละแปลง

ใช้:
    python split_heightmap.py [ขนาดแปลง]      # default 2000

สำคัญ: ทุกแปลงใช้สเกลความสูงเดียวกัน (Min Y / Max Y เท่ากันหมด) เพราะค่าพิกเซล
ถูก normalize ด้วย min/max ของทั้งแผนที่ตั้งแต่ตอนสร้าง ถ้าไป normalize ใหม่ทีละแปลง
รอยต่อระหว่างแปลงจะเป็นหน้าผา — ห้ามทำเด็ดขาด

ผลลัพธ์:
    tiles/hm_x{X}_z{Z}.png   แต่ละแปลง ตั้งชื่อตามพิกัดบล็อกมุมซ้ายบน
    tiles/README.txt         ตารางพิกัดกับค่าที่ต้องกรอกใน Axiom
"""

import os
import sys

import numpy as np
from PIL import Image

import config as C

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    tile = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    src = os.path.join(HERE, "heightmap.png")
    if not os.path.exists(src):
        raise SystemExit("ไม่พบ heightmap.png — รัน make_heightmap.py ก่อน")

    out_dir = os.path.join(HERE, "tiles")
    os.makedirs(out_dir, exist_ok=True)

    print(f"อ่าน {src} ...")
    img = np.asarray(Image.open(src))
    n = img.shape[0]
    if img.shape != (n, n):
        raise SystemExit(f"heightmap ไม่เป็นสี่เหลี่ยมจัตุรัส: {img.shape}")
    if n != C.GRID:
        print(f"[เตือน] ขนาดภาพ {n} ไม่ตรงกับ GRID={C.GRID} ใน config")

    cols = (n + tile - 1) // tile
    print(f"ตัด {n}x{n} เป็น {cols}x{cols} = {cols*cols} แปลง (แปลงละ {tile} บล็อก)")

    rows_out = []
    for iz in range(cols):
        for ix in range(cols):
            x0, z0 = ix * tile, iz * tile
            x1, z1 = min(x0 + tile, n), min(z0 + tile, n)
            # แกนภาพคือ [row=z, col=x]
            patch = img[z0:z1, x0:x1]
            name = f"hm_x{x0}_z{z0}.png"
            Image.fromarray(patch, mode="I;16").save(os.path.join(out_dir, name))
            rows_out.append((name, x0, z0, x1 - 1, z1 - 1, patch.shape[1], patch.shape[0]))
        print(f"  แถว {iz+1}/{cols} เสร็จ")

    readme = os.path.join(out_dir, "README.txt")
    with open(readme, "w", encoding="utf-8") as f:
        f.write(
            f"""แปลงย่อย heightmap สำหรับ Axiom
=====================================
ทุกแปลงใช้ค่าเดียวกันนี้ ห้ามเปลี่ยนรายแปลง มิฉะนั้นรอยต่อจะเป็นหน้าผา

    Min Y = {C.Y_TERRAIN_MIN}
    Max Y = {C.Y_TERRAIN_MAX}

สเกล  แนวนอน 1 block = {C.METERS_PER_BLOCK} m   พื้นที่จริง {C.AREA_SIZE_M/1000:.0f} x {C.AREA_SIZE_M/1000:.0f} km

ไฟล์                      วางที่ X, Z      ถึง X, Z        ขนาด
"""
        )
        for name, x0, z0, x1, z1, w, h in rows_out:
            f.write(f"{name:<24} {x0:>6}, {z0:>6}   {x1:>6}, {z1:>6}   {w}x{h}\n")

    total = sum(
        os.path.getsize(os.path.join(out_dir, r[0])) for r in rows_out
    )
    print(f"\nเสร็จ — {len(rows_out)} ไฟล์ รวม {total/1024/1024:.0f} MB ใน {out_dir}")
    print(f"ตารางพิกัดอยู่ใน {readme}")
    print(f"\nทุกแปลงใช้ Min Y = {C.Y_TERRAIN_MIN}, Max Y = {C.Y_TERRAIN_MAX} เหมือนกันหมด")


if __name__ == "__main__":
    main()
