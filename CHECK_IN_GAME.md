# ตรวจในเกม — 3 จุดที่ควรดูก่อน (2026-08-13)

patch npz ขึ้นรูปไว้แล้วใน `golden_patches/` ไม่ต้องรัน `hydrology_shape` ซ้ำ
รันสองคำสั่งต่อจุด (build ก่อน paint เสมอ) แล้วบินไปดู

## 1. hill_junction (3344, 480) — จุดที่เคยเป็นปล่องหินหนักสุด

```bash
python build_terrain.py --patch 3344 480 256 --hydrology-patch golden_patches/hydrology_patch_x3344_z480_384.npz
```
```bash
python paint_surface.py --patch 3344 480 256 --hydrology-patch golden_patches/hydrology_patch_x3344_z480_384.npz
```

เดิม: 93% ของน้ำอยู่ในหุบ ผนังสองฝั่งสูงกว่าหัว p50 44 บล็อก
ตอนนี้วัดได้: 21% / p50 2 บล็อก
**ดูว่า**: เดินเลียบลำธารได้ไหม ลำธารดูเป็นลำธารหรือเป็นร่องที่ถูกขุด

## 2. wild_canyon (6448, 2304) — จุดที่ยังแย่ที่สุดตามตัวเลข

```bash
python build_terrain.py --patch 6448 2304 256 --hydrology-patch golden_patches/hydrology_patch_x6448_z2304_384.npz
```
```bash
python paint_surface.py --patch 6448 2304 256 --hydrology-patch golden_patches/hydrology_patch_x6448_z2304_384.npz
```

ตัวเลขบอกว่าผิวน้ำยังต่ำกว่า DEM p50 12 / p90 27 บล็อก (หุบเกินธรรมชาติ 66.9%)
**ดูว่า**: มันน่าเกลียดจริงตามตัวเลขไหม หรือในเกมอ่านเป็นหุบลำธารที่ยอมรับได้
คำตอบตรงนี้ตัดสินว่าจะลงแรงแก้ "ยุบหน้าตัดเข้าหา centerline" ต่อหรือไม่

## 3. player_liked (6165, 6068) — จุดที่เคยบอกว่าสวย

```bash
python build_terrain.py --patch 6165 6068 256 --hydrology-patch golden_patches/hydrology_patch_x6165_z6068_384.npz
```
```bash
python paint_surface.py --patch 6165 6068 256 --hydrology-patch golden_patches/hydrology_patch_x6165_z6068_384.npz
```

**ดูว่า**: ยังสวยเหมือนเดิมไหม — ถ้าแย่ลง แปลว่าการแก้รอบนี้ทำของดีพัง

## สิ่งที่อยากได้กลับมา

ไม่ต้องอธิบายเป็นศัพท์เทคนิค บอกแค่ "ตรงไหนดูผิดธรรมชาติ" ก็พอ ผมจะแปลงเป็น
ตัวเลขที่วัดได้เอง — สามอย่างที่ตัวเลขยังบอกไม่ได้และต้องใช้ตา:
1. ลำธารดูเป็น "ร่องที่ถูกขุด" ไหม (ตัวเลขบอกความลึก แต่ไม่บอกว่าตาอ่านยังไง)
2. ตลิ่งเดินได้จริงไหม
3. น้ำตกดูเป็นน้ำตก หรือเป็นเสาน้ำโดด

## หมายเหตุ

global รันเสร็จแล้ว 3 รอบ (17-50 นาที ไม่ใช่ 7 ชม.) — **ใช้ `hydrology_global_y`**
(halo 128) ชุดเดียวเท่านั้น สองชุดแรกมีขั้นที่รอยต่อ tile:

| ชุด | halo | รอยต่อ tile | invariant ที่ผิด |
|---|---|---|---|
| `hydrology_global_w` | 24 | 32.7% | 34 / 7 / 3 |
| `hydrology_global_x` | 64 | 18.0% | 9 / 0 / 1 |
| **`hydrology_global_y`** | **128** | **9.1%** | **1 / 0 / 0** |

```bash
python build_terrain.py --hydrology-root hydrology_global_y
python paint_surface.py --hydrology-root hydrology_global_y
```

**อย่าเพิ่งเขียนทับโลกทั้งใบก่อนตรวจ 3 จุดข้างบน** — สองชุดแรกลบทิ้งได้ (1.4 GB/ชุด)
