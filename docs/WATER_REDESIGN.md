# Water redesign — immersive-first

สถานะ: ระบบน้ำปัจจุบัน **ไม่ผ่าน visual acceptance** และห้ามถือว่าตัวเลข metric
ที่ดีขึ้นแปลว่าภาพในเกมดีขึ้น เอกสารนี้เก็บหลักฐานจาก patch `(6021, 6142)`
หลังรัน `build_terrain` และ `paint_surface` แบบเต็ม

## หลักฐานจากโลกจริง

### ลำธารและน้ำตก

- mask เป็นริบบิ้นทแยงกว้าง 3–4 บล็อกตาม buffer ของเส้น OSM
- ในกรอบเพียง 26×26 บล็อก ผิวน้ำไต่จาก Y=102 ถึง Y=142
- ที่ `(6021, 6142)` โลกจริงมี source-water ต่อเนื่อง Y=123..133 รวม 11 บล็อก
- ทั้งแผนที่มีกลุ่ม "น้ำตก" 3,346 กลุ่ม; 70.3% เป็นจุดเดี่ยว
- ม่านน้ำ 6,244 คอลัมน์ รวม 13,908 บล็อก สูงสุด 18 บล็อก

ข้อสรุป: นี่ไม่ใช่ระบบ waterfall แต่เป็นการเติมผนังน้ำเพื่อปิดขั้นของเส้นน้ำที่
ถูก drape บน DEM โดยไม่มี longitudinal river profile

### ทะเลสาบและตลิ่ง

- ทะเลสาบใหญ่สุดแบนที่ Y เดียวกันได้จริง
- แต่ 40.8% ของ dry bank ที่ติดทะเลสาบใหญ่อยู่ **ต่ำกว่าผิวน้ำ**
- dry bank ทั้งแผนที่ 16.9% อยู่ต่ำกว่าน้ำข้างเคียง
- painter เปลี่ยนวัสดุชายฝั่ง แต่ไม่เปลี่ยนรูปทรง terrain รอบฝั่ง
- ทะเลสาบใหญ่สุดมีพื้นที่ depth=31 (ชนเพดานสูตร) 26.3%

ข้อสรุป: ระดับน้ำ, bank terrain และ bathymetry ถูกคำนวณแยกกัน ภาพจึงเกิดผนัง
แนวตั้ง/ขอบน้ำลอย และก้นทะเลสาบมี plateau ที่ชน max-depth มากเกินไป

## Root causes

1. `fetch_landcover.py` รวม polygon ทะเลสาบและ directed waterway line เป็น
   landcover code `9` เดียวกัน ข้อมูลชนิดและทิศทางการไหลสูญหายถาวร
2. `make_water.py` ใช้ distance-to-shore สูตรเดียวกับน้ำทุกชนิด:
   wide river ถูกมองเป็น lake, shallow pond ถูกมองเป็น stream
3. `terrain_shape.py` เพียงกันน้ำจาก dither แต่ไม่ได้ carve channel หรือ reshape
   bank ดังนั้นน้ำถูกวางทับ DEM แทนที่จะเป็นส่วนหนึ่งของภูมิประเทศ
4. `make_water_levels.py` พยายามซ่อม graph หลัง rasterize แล้ว การ priority-flood
   แบบไร้ทิศทางจึงยกน้ำผิด branch และสร้าง outlet เชิงตัวเลข
5. `paint_surface.py` คำนวณ bed/bank/vegetation ตอนปลายเกินไป หลัง terrain ถูก
   build ไปแล้ว จึงแก้ geometry จริงไม่ได้
6. waterfall decoration ถูกสร้างจากทุก cardinal step ≥2 โดยไม่รู้ flow direction,
   rock lip, plunge pool หรือ channel width

## Pipeline ใหม่

```text
OSM water polygons ──> standing_water_mask ─┐
OSM directed ways ──> ordered centerlines ──┼─> hydrology_shape.py
base terrain_y + DEM valley context ─────────┘
                                      │
                                      ├─ final_terrain_y.npy
                                      ├─ water_mask.npy
                                      ├─ water_surface_y.npy
                                      ├─ water_depth.npy
                                      ├─ water_kind.npy
                                      ├─ flow_dir / velocity
                                      └─ waterfall_features.npz
```

`build_terrain.py` และ `paint_surface.py` ต้องเป็น consumer เท่านั้น ห้ามคำนวณ
ระดับน้ำหรือจำแนก lake/stream ซ้ำเอง

## ลำดับรื้อใหม่

1. แยก source data: waterbody polygon, river/stream/ditch และ ordered centerline
2. สร้าง river longitudinal profile ตามทิศ OSM + DEM โดย profile ต้องไหลลง
3. carve channel และ reshape bank ลง `final_terrain_y` ก่อน build
4. ทำ lake level + bank terrace พร้อมกัน แล้วค่อยสร้าง bathymetry
5. สร้าง waterfall เฉพาะ feature ที่ profile/terrain ยืนยัน พร้อม rock lip และ
   plunge pool; ห้ามเติมม่านจากทุก DEM step
6. ค่อยใส่วัสดุก้นน้ำ พืชน้ำ กก และ shoreline ecology จาก flow/velocity/exposure

## Acceptance gates

- ไม่มี dry bank ต่ำกว่าผิวน้ำข้างเคียง
- river profile ไม่มี local sink และไม่มีการยกผิวน้ำเหนือ bank
- step ≥2 เกิดได้เฉพาะ waterfall feature
- stream width ไม่เป็น buffer ribbon คงที่ตลอดสาย
- waterfall ต้องเป็น cluster ที่มี lip, curtain, plunge pool และ downstream reach
- lake max-depth cap ไม่กลายเป็น plateau กว้าง
- test patch ต้องผ่านทั้ง top-down, eye-level และการอ่านบล็อกจริงจาก world
- metric ทุกตัวต้องวัด artifact ที่เห็นจริง ไม่ใช่เพียง mask/probability

## Prototype acceptance — 2026-07-28

พื้นที่เดิม `(6021, 6142)` ใช้ output context 384 บล็อก แต่ build/paint เฉพาะ
256 บล็อก:

- source hydrology แยกเป็น waterbody 1,897,420 cells, waterway 918,867 cells
  และ ordered lines 4,882 เส้น; 98.75% ของเส้นไหลลงหรือราบตาม DEM ที่ปลายทาง
- footprint ลำธารใน patch ลดจาก buffer ribbon 867 cells เหลือ 480 cells ใน
  acceptance area และยังเป็น 4-connected
- dry bank ต่ำกว่าผิวน้ำ 0 จุด
- waterfall curtain มีเฉพาะ strong directed drop: 16 columns / 59 blocks ใน
  context; drop=2 ถือเป็น rapid และไม่สร้างม่าน
- actual world read-back ตรวจ 480 คอลัมน์น้ำ: missing 0, extra 0
- สุ่มตำแหน่ง footprint เก่าที่ถูกตัด 500 จุด: stale water 0
- vertical water run สูงสุด 7 บล็อกและตรงกับ declared waterfall; ของเก่าที่
  `(6021, 6142)` เป็น source-water pillar 11 บล็อก
- ลำธารและม่านน้ำยังเขียนเป็น source water `level=0` ทั้งหมด แต่ฝัง scheduled
  `fluid_ticks` ลง chunk ให้ Minecraft update เองตอนโหลด; acceptance patch มี
  source 494 บล็อกและ tick ตรงกัน 494/494 หลัง save/reload

คำสั่งที่ใช้:

```bash
python hydrology_shape.py --patch 6021 6142 384
python build_terrain.py --patch 6021 6142 256 \
  --hydrology-patch hydrology_patch_x6021_z6142_384.npz
python paint_surface.py --patch 6021 6142 256 \
  --hydrology-patch hydrology_patch_x6021_z6142_384.npz
```

สถานะนี้ยังเป็น **patch acceptance** ไม่ใช่ global completion ขั้นต่อไปหลัง
ผู้ใช้ตรวจในเกมคือปรับ visual จาก feedback แล้วจึงสร้าง full-map products และ
เพิ่ม flow/velocity ให้ shoreline ecology

## Feedback จากโลกจริง — 2026-07-29

ผู้ใช้ตรวจพื้นที่ที่ paint ด้วย `hydrology_global/` แล้วรายงานสองอาการ:

1. บล็อกรอบสายน้ำและแอ่งน้ำ **ถูกยกสูงขึ้น** ไม่ blend กับ terrain รอบข้าง
2. น้ำที่ไหลลงทะเลสาบ **บางจุดลดลง 1 บล็อกทันที** ทำให้ไม่ smooth

### สาเหตุที่ยืนยันแล้ว และแก้ไปแล้ว

`shape_hydrology_global` ส่ง `max_drop_per_sample=6.0` และ
`max_surface_step=12` ทับ default ของ patch (2.0 / 4) — acceptance ข้างบนจึง
ผ่านทั้งที่ของจริงหยาบกว่าสามเท่า ดู DECISIONS.md หัวข้อ "global ต้องใช้
พารามิเตอร์ชุดเดียวกับ patch" ตอนนี้มี parity test บังคับไว้แล้ว

### อาการ 1 — แก้แล้ว: ตลิ่งต้องเฟดกลับ ไม่ใช่ตัดจบ

เดิมมีสามชั้นยกพื้นรอบน้ำทับกัน และไม่มีชั้นไหน taper ออก

| จุด | ทำอะไร (เดิม) |
|---|---|
| `shape_hydrology_patch` (dry edge lift) | ยกทุกบล็อกแห้งที่ติดน้ำให้ถึงระดับผิวน้ำ ไม่มีเพดาน |
| `shape_standing_water_patch` (lake bank) | ยกเป็นขั้นถึง `near_stage+3` ในระยะ `bank_width` แล้ว**ตัดจบทันที** |
| `shape_waterway_patch` (stream bank) | clip terrain ทั้งบนและล่างเข้าช่วงแคบรอบระดับน้ำ |

ตัวการคือ `minimum = near_stage + (d - 1)` ซึ่ง **สูงขึ้นตามระยะ** แล้วตัดจบที่
`bank_width` บนทะเลสาบไหล่เขาที่พื้นข้างลาดลง ทั้งแถบจึงถูกยกเป็นสันเหนือผิวน้ำ
แล้วเกิดหน้าผาที่ขอบแถบ

ทั้งหมดเกิดจากการไล่แก้ metric "dry bank ต่ำกว่าผิวน้ำ 40.8%" ซึ่งแก้ตัวเลขได้
(patch วัดได้ 0 จุด) แต่วิธีแก้คือยกพื้นเป็นสัน จึงย้ายปัญหาไปเป็น artifact ที่
ตาเห็นแทน — ตรงกับที่เอกสารนี้เตือนไว้เองว่าห้ามเชื่อ metric แทนภาพ

ตอนนี้ใช้ `bank_taper_weight` ตัวเดียวคุมทั้งการยก (`taper_bank_lift`) และการกด
(`taper_bank_cut`): ยกเต็มที่ระยะ `BANK_SEAL_BLOCKS` เท่าที่กันน้ำรั่ว แล้วลด
น้ำหนักเชิงเส้นจนเป็นศูนย์พอดีที่ `BANK_BLEND_BLOCKS` พื้นที่สูงกว่าผิวน้ำอยู่แล้ว
ไม่ถูกแตะเลย หน้าผาธรรมชาติจึงยังอยู่ครบ

วัดบนทะเลสาบไหล่เขาผังเดียวกัน:

| | ยกสูงสุด | ขั้นบนพื้นแห้งสูงสุด | รูรั่ว |
|---|---|---|---|
| เดิม | 7 | **9** | 0 |
| ใหม่ | 2 | **3** | 0 |

`tests/test_hydrology_shape.test_bank_lift_seals_edge_without_building_a_ridge`
คุมทั้งสามเงื่อนไข (กันรั่ว / ห้ามยกเหนือผิวน้ำ / ห้ามมีขอบคม) และผ่าน mutation
test แล้ว

### อาการ 2 — ยังไม่ได้แก้: ไม่มี pool quantization

`regularize_stage` บังคับแค่ว่าห้ามไหลขึ้น แล้วปล่อยให้ stage ไต่ตาม DEM ทีละ
บล็อก ลำธารจริงเป็นขั้นบันได: ช่วงราบยาว (pool) สลับกับจุดตก ไม่ใช่ลาดทีละ 1
ต่อเนื่อง — baseline เดิมก็วัดได้ว่า "ขั้น 1 บล็อก 12.4%" ซึ่งมากพอจะสะดุดตา
ตลอดสาย

**ยังไม่ลงมือแก้เพราะยังไม่มีตัวเลขว่าขั้นกระจายอย่างไรจริง** การออกแบบ
quantisation โดยเดาเสี่ยงซ้ำรอยเรื่อง halo (เดาว่าเป็นสาเหตุ วัดแล้วไม่ใช่)
ทางที่แย่ที่สุดคือรวมขั้นถี่ ๆ ให้เป็นขั้นใหญ่ขึ้น เพราะจะกลายเป็นน้ำตกเทียม
แทนที่จะเป็นแอ่ง

`report_metrics.stream_metrics` จึงเพิ่มตัววัดตรงอาการก่อน:

| ตัวเลข | ความหมาย |
|---|---|
| `pool_size_median` | ความยาวแอ่ง (ช่วงที่ผิวน้ำระดับเดียวกัน) ตรงกลาง |
| `pool_size_p90` | แอ่งยาวสุดในกลุ่มบน |
| `tiny_pool_share` | สัดส่วนแอ่งที่มี <=2 cells — **สูง = น้ำลดทีละบล็อกแทบทุกก้าว** |

แยกสองกรณีได้ชัด (ยืนยันด้วยเทสต์): ลำธารที่ลดทีละบล็อกได้ median 1 /
tiny 100% ส่วนลำธารที่มีแอ่งยาว 8 บล็อกได้ median 8 / tiny 0%

ต้องรัน `hydrology_shape.py --global` ใหม่แล้ววัดด้วยตัวเลขชุดนี้ก่อน จึงจะรู้ว่า
ควร quantise แรงแค่ไหนและตรงไหน
