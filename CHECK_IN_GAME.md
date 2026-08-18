# จุดที่ต้องไปตรวจในเกม — 2026-08-13

patch ขึ้นรูปไว้แล้วใน `golden_patches/` ครบทุกจุด **ไม่ต้องรัน hydrology_shape ซ้ำ**
แต่ละจุดใช้สองคำสั่ง (build ก่อน paint เสมอ) กรอบ hydrology 384 แต่เขียนจริง 256

รูปแบบคำสั่ง:

```bash
python build_terrain.py --patch <X> <Z> 256 --hydrology-patch golden_patches/hydrology_patch_x<X>_z<Z>_384.npz
python paint_surface.py --patch <X> <Z> 256 --hydrology-patch golden_patches/hydrology_patch_x<X>_z<Z>_384.npz
```

---

## ลำดับ 1 — ตัวตัดสินว่าจะทำงานก้อนใหญ่ต่อหรือไม่

### 1. wild_canyon (6448, 2304) — แย่ที่สุดตามตัวเลข

```bash
python build_terrain.py --patch 6448 2304 256 --hydrology-patch golden_patches/hydrology_patch_x6448_z2304_384.npz
python paint_surface.py --patch 6448 2304 256 --hydrology-patch golden_patches/hydrology_patch_x6448_z2304_384.npz
```

ตัวเลข: หุบเกินธรรมชาติ **66.9%** — ยืนในลำน้ำแล้วผนังสองฝั่งสูงกว่าหัว p50 11 / p90 27 บล็อก
**ดูว่า**: มันน่าเกลียดจริงตามตัวเลขไหม หรือในเกมอ่านเป็นหุบลำธารที่ยอมรับได้
ถ้าตอบว่า "โอเค" ผมจะไม่รื้อ `flatten_cross_sections` ซึ่งเป็นงานก้อนใหญ่

### 2. wild_canyon_b (4832, 3296) — ยืนยันว่าข้อ 1 ไม่ใช่จุดเดียว

```bash
python build_terrain.py --patch 4832 3296 256 --hydrology-patch golden_patches/hydrology_patch_x4832_z3296_384.npz
python paint_surface.py --patch 4832 3296 256 --hydrology-patch golden_patches/hydrology_patch_x4832_z3296_384.npz
```

ตัวเลข: หุบเกิน 56.3% (p50 10 / p90 26)

---

## ลำดับ 2 — ยืนยันว่าที่แก้ไปได้ผลจริง และไม่ทำของดีพัง

### 3. hill_junction (3344, 480) — เคยเป็นปล่องหินหนักที่สุด

```bash
python build_terrain.py --patch 3344 480 256 --hydrology-patch golden_patches/hydrology_patch_x3344_z480_384.npz
python paint_surface.py --patch 3344 480 256 --hydrology-patch golden_patches/hydrology_patch_x3344_z480_384.npz
```

เดิม 93% ของน้ำอยู่ในหุบ ผนัง p50 44 บล็อก -> ตอนนี้ 18.4% / p50 2
**ดูว่า**: ลำธารดูเป็นลำธาร หรือยังเป็นร่องที่ถูกขุด

### 4. player_liked (6165, 6068) — จุดที่เคยบอกว่าสวย

```bash
python build_terrain.py --patch 6165 6068 256 --hydrology-patch golden_patches/hydrology_patch_x6165_z6068_384.npz
python paint_surface.py --patch 6165 6068 256 --hydrology-patch golden_patches/hydrology_patch_x6165_z6068_384.npz
```

**ดูว่า**: ยังสวยเหมือนเดิมไหม ถ้าแย่ลงแปลว่าการแก้รอบนี้ทำของดีพัง

---

## ลำดับ 3 — ตรวจอาการเฉพาะทาง

### 5. steep_stream (5792, 5384) — ตลิ่งแย่ที่สุด + ที่ที่น้ำตกควรมีจริง

```bash
python build_terrain.py --patch 5792 5384 256 --hydrology-patch golden_patches/hydrology_patch_x5792_z5384_384.npz
python paint_surface.py --patch 5792 5384 256 --hydrology-patch golden_patches/hydrology_patch_x5792_z5384_384.npz
```

ตลิ่งเกินธรรมชาติ 10.0% (สูงสุดในชุด) และเป็นลำน้ำชันที่สุดในแผนที่
**ดูว่า**: เดินเลียบลำธารได้ไหม / น้ำตกดูเป็นน้ำตก หรือเป็นเสาน้ำโดด

### 6. lake_mouth (2242, 3057) — ปากทะเลสาบ

```bash
python build_terrain.py --patch 2242 3057 256 --hydrology-patch golden_patches/hydrology_patch_x2242_z3057_384.npz
python paint_surface.py --patch 2242 3057 256 --hydrology-patch golden_patches/hydrology_patch_x2242_z3057_384.npz
```

เดิมมีม่านน้ำ 117 เสาที่ไม่มีหน้าผารองรับสักต้น
**ดูว่า**: ตรงที่ลำธารไหลลงทะเลสาบ น้ำต่อเนื่องไหม มีเสาน้ำลอยไหม

---

## จุดที่ตัวเลขบอกว่าสะอาดแล้ว (ดูผ่าน ๆ พอ ถ้ามีเวลา)

| จุด | พิกัด | หุบเกิน | ตลิ่งเกิน |
|---|---|---|---|
| flat_river | (6952, 3816) | 1.3% | 0.5% |
| lake_corridor | (6066, 5811) | 1.7% | 0.4% |
| floodplain_berm | (5970, 5902) | 1.8% | 1.3% |
| cliff_bedding | (5880, 5554) | 2.3% | 6.6% |
| prototype_stream | (6021, 6142) | 20.8% | 9.0% |

`prototype_stream` คือแพตช์ acceptance เดิมของเอกสาร — ถ้าอยากเทียบกับความทรงจำเก่า ดูจุดนี้

---

## สามคำถามที่ตัวเลขตอบไม่ได้ ต้องใช้ตา

1. ลำธารดูเป็น **"ร่องที่ถูกขุด"** ไหม (ตัวเลขบอกความลึกได้ แต่ไม่บอกว่าตาอ่านออกมายังไง)
2. ตลิ่ง **เดินเลียบได้จริง** ไหม
3. น้ำตกดูเป็นน้ำตก หรือเป็น **เสาน้ำโดด**

ไม่ต้องตอบเป็นศัพท์เทคนิค บอกว่า "ตรงไหนดูผิดธรรมชาติ" ก็พอ

---

## ถ้าอยากดูทั้งแผนที่แทนการดูทีละจุด

**ล้าสมัยตั้งแต่ 2026-08-18**: `hydrology_global_y` ถูกย้ายไป
`archive/hydrology_runs/` แล้ว และสร้างก่อนที่ shaper จะมี `section_id` กับ
`outer_rim_field` — ตัวเลขและรูปทรงในนั้นไม่ตรงกับโค้ดปัจจุบัน ต้องรัน
`hydrology_shape.py --global` ใหม่ก่อนใช้

```bash
python build_terrain.py --hydrology-root hydrology_global_y
python paint_surface.py --hydrology-root hydrology_global_y
```

**เขียนทับทั้งใบใช้เวลานานและย้อนยาก — แนะนำให้ตรวจ 6 จุดข้างบนก่อน**
