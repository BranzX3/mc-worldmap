# Backlog และประวัติงาน implementation

**ลำดับหัวข้อด้านล่างเป็น backlog เดิม ไม่ใช่ลำดับเริ่มงานปัจจุบัน** เลือกงานจาก
[IMMERSION_ROADMAP.md](IMMERSION_ROADMAP.md) ตามเป้าหมาย
[PROJECT_DIRECTION.md](PROJECT_DIRECTION.md); สถานะรับรองดู
[IMMERSION_REVIEW.md](IMMERSION_REVIEW.md)

คำว่า “แก้แล้ว/เสร็จ/เพิ่มแล้ว” ในบันทึกย้อนหลังหมายถึงขั้น implementation
เว้นแต่มี world acceptance ของ snapshot นั้นกำกับ ตัวเลขมีทั้ง report_metrics,
golden และ world sample หลายรอบ ตรวจ source/date/fingerprint ก่อนเปรียบเทียบ
และสร้าง baseline ใหม่เมื่อไม่ตรง code/input ที่กำลังแก้

## 1. น้ำ — greenfield ปัจจุบัน

อัปเดต 2026-09-14: เจ้าของโปรเจกต์ให้เขียนระบบน้ำใหม่ โค้ดอยู่ใน `water_v2/`
อ่าน DEM/OSM แล้วสร้าง topology/router, shared level solver และ product
ที่ระบุ geometry/material/fluid/plant ให้ block sink วางตามคำตอบโดยตรง
ไม่ใช้การซ่อมฝั่งหรือ painter น้ำเดิมเป็นฐานของ rewrite

งานหลักคือ W1/W2: compiler ของ `bounded_scene` ยังมี physical violations
จากข้อมูลจริงให้แก้ก่อนสร้าง writer อิสระและตรวจโลกใน W3
ยังไม่มีการเขียนระบบใหม่นี้ลงเซฟ และยังไม่รับรอง visual/post-tick
ดู [สัญญาระบบใหม่](WATER_V2.md), [ผลตรวจปัจจุบัน](reviews/water_v2_20260914.md)
และ [คิว W1 → W2 → W3](IMMERSION_ROADMAP.md)

### ประวัติระบบน้ำเดิม

รายการและตัวเลขต่อไปนี้บันทึก implementation หลายรอบของระบบเก่า
ไม่เป็นคิวพัฒนาหรือ acceptance ของ rewrite เซฟวันที่ 13 ยังเป็น candidate เก่า

อัปเดต 2026-09-13: แก้ bank seal/ร่องขุด, ความลึกและ flow ของน้ำกว้าง,
และขั้นน้ำตกที่เกิดหลังปัดพิกัดแล้ว 409 tests ผ่าน; 11 patches hard errors = 0
bank/canyon excess ผ่านทุกจุดและ water mask คงเดิม
เซฟสำเนา build/paint/readback ผ่าน 4 จุด น้ำ 10,626 คอลัมน์ hard errors = 0
วิธีเปิดผลเก่าอยู่ใน [PAINT_TEST](PAINT_TEST.md); visual review ของรอบนั้นยังไม่รับรอง
ผลและข้อควรตรวจ 11 soft flags อยู่ใน [candidate](reviews/paint_ready_20260913_v3.md)

อัปเดต 2026-09-12: patch/global ใช้การจำแนกน้ำนิ่งและ pin profiles ร่วมกันแล้ว
395 tests ผ่าน, golden 11 จุด hard invariants = 0 และจำนวนช่องน้ำคงเดิม
lake_mouth canyon excess 12.43% → 0.04%; ยังเหลือ bank hill_junction 5.42%
และ compare regressions 14 metrics ดู [รายงานรอบนั้น](reviews/standing_20260912.md)
งานที่ค้างในรอบนั้นคือขั้นตลิ่งที่เดินไม่ได้ ก่อนตรวจก้นน้ำ/แอ่งและ world gate

อัปเดต 2026-09-10: แก้การเรียง source profile ย้อนทิศและลด memory ของ slope
ด้วย row batches; 392 tests ผ่าน, golden 11 patches hard invariants = 0
hill_junction canyon excess 5.71% → 0% แต่ bank 5.42% ยังไม่ผ่าน;
lake_mouth canyon 12.43% และ compare regressions 3 metrics ยังต้องแก้
ดู [รายงานรอบนั้น](reviews/direction_20260910.md); ตารางต่อไปนี้เป็นประวัติเดิม

| งานระบบเดิม | ผลที่บันทึกในรอบนั้น |
|---|---|
| แยก lake/river/stream ตั้งแต่ OSM | **prototype ผ่าน:** `water_sources.npz` เก็บ polygon, ordered way, kind, width |
| river profile + channel carving | **รัน global แล้ว 2026-08-18** (`hydrology_global2`): audit 30 หน้าต่าง invariant 0/0/0, รอยต่อ tile 4.41% เทียบภายใน 4.29% |
| lake bank terrain | **patch ผ่านโครงสร้าง:** dry bank ต่ำกว่าน้ำ 0/208; ยังไม่รัน global |
| bathymetry | **รัน global แล้ว** — `bed_flat_share` ไล่จนจบแล้วและสรุปว่าใช้เป็นด่านไม่ได้ (ดู WATER_REDESIGN) ตัวที่ยังวัดได้จริงคือ `bed_flat_lake` |
| waterfall | **patch ใหม่:** สร้างจาก directed profile เฉพาะ drop 3–4 พร้อม lip/curtain/pool |
| shoreline ecology / พืชน้ำ | **เพิ่มแล้ว:** `flow_index` + `water_ecology.lake_fetch` ตัดสินวัสดุก้นน้ำ/หญ้าน้ำ/ใบบัว/กกจากแรงน้ำและ fetch; tall seagrass/reed/พืชสองบล็อกตรวจอากาศทั้ง object ก่อนเขียนแล้ว; ยังต้องอ่านกลับโลกจริงรอบสุดท้าย |
| ~~ลำธารเป็นปล่องหิน~~ | **แก้แล้ว:** น้ำที่อยู่ในหุบ 93% -> 21% (hill_junction), ขุดลึกสุด 123 -> 33 ดู `WATER_REDESIGN.md` |
| ตลิ่งยังเป็นขั้นเกินธรรมชาติ | **เกือบผ่าน:** branch `codex/immersion-bank-morphology` เพิ่ม final seal cap + `smooth_walkable_banks`; golden `bank_final` เดิมลด excess เป็น prototype -0.5%, player -0.4%, cliff 3.1%, steep 3.8%, hill 5.1% (เกณฑ์ 5% ยังเกิน 0.1 จุด). เพิ่ม seal-notch restore สูงสุด DEM+1 เพื่อแก้ฝั่งต่ำที่ติดฝั่งถูกยก; patch-array smoke ลด steep/bedding residual โดยไม่เพิ่ม wall edges แต่ต้อง rerun golden เมื่อ scipy กลับมา. hard invariant 0 ทุก patch และ paint/readback ยังรอ |
| ร่องน้ำลึกเกินเพดาน | **แก้ใน golden patch ปัจจุบันแล้ว:** wild_canyon p90 27/26 -> 3/2 และ excess 0%; lake_mouth ยังเหลือ p90 13 / excess 13.3% ต้องสร้าง global ใหม่ก่อนอ้างว่าทั้งแผนที่ผ่าน |
| วัสดุก้นน้ำเป็นแถบเดียว | **ดีขึ้นจาก materialfine/mouthbar 2026-08-20:** geometry readback 3 patch ยัง 0 error, wild/steep same-neighbour 88.2%/86.8%; lake-mouth stream 26 cells เปลี่ยน sand 100% เป็น sand 88.5% + gravel 11.5%, แต่ sample เล็กและ lake same-neighbour ยัง 94% |
| global hard gate | **ผ่านใน `hydrology_global3` 2026-08-18:** ทะเลสาบลอยเหนือลำน้ำ 64 -> 0 cells, invariant อื่นยัง 0, seam 3.674% vs ภายใน 3.256%; ยังห้ามเรียกว่า visual ผ่านเพราะ terrain lift เพิ่มใน 4 golden metrics |
| invariant ที่ต้องเป็นศูนย์ | **ผ่านครบ** 11 golden patches + สุ่มนอกชุด 10 จุด: ตลิ่งลอย 0, ช่องว่าง 0, หน้าตัดไม่ราบ 0 |

ขั้นตอนระบบเดิมใช้ `golden_patches.py run` และ `compare` ตาม PIPELINE
งานใหม่ใช้ product/physical report ของ `water_v2` เป็นหลัก; ทำ dev ก่อน
แล้วตรวจ build/test ที่เกี่ยวข้องตอนท้ายรอบตามกติกาปัจจุบัน

หลักฐานและการทดลองระบบเดิมอยู่ใน `docs/WATER_REDESIGN.md` ห้ามใช้ค่า
"ตลิ่งไม่ได้แต่ง 0%" เป็นเกณฑ์ผ่าน เพราะ metric เดิมวัด probability ของวัสดุ
ไม่ใช่รูปทรง bank ที่เห็นในเกม

แพตช์ acceptance ของการทดลองเก่ารอบหนึ่งอยู่ที่ `(6021, 6142)` ขนาด 256 บล็อก โดยใช้
hydrology context 384 บล็อก เขียนลงโลกแล้วและผ่านการอ่านกลับ 480 คอลัมน์น้ำ:
ไม่มีบล็อกขาด/เกิน, ไม่มีน้ำเก่าค้างในตัวอย่าง 500 จุด และ vertical run สูงสุด
7 บล็อกเกิดเฉพาะ waterfall feature (ของเดิมสูง 9–11 บล็อกตามแนวลำธาร)

## 2. ถ้ำ / ชะง่อน — unit safety ผ่าน แต่ยังไม่ผ่าน world gate

`build_terrain.py` ถม `solid = ys <= tile` = **หินตัน 100% ไม่มีที่ว่างภายในเลย**
โลกจาก heightmap เป็น 2.5D โดยนิยาม ผาสูง 36 บล็อกทุกลูกเป็นผนังตันเรียบ

มี prototype `rock_shelters.py` ใน working tree แล้ว แก้ root cause ที่ seed อาจเห็น
คอลัมน์ต่ำไกล 3 บล็อกแต่ไม่มีอากาศติดปากโพรงจริง และเพิ่ม test ตรวจว่าทุก cut
component เชื่อมถึงอากาศภายนอกที่สูงพ้นน้ำ ปัจจุบันผ่าน 10/10 test แล้ว แต่
`build_terrain` ยังเปิด shelters แบบ opt-in ด้วย `--shelters` หลังผ่าน geometry
safety tests; ห้ามเปิด default จนกว่าจะอ่านบล็อกจริงของพื้น เพดาน ปาก และการเชื่อมต่อ
จาก world save ได้ครบ

**ขอบเขตรอบแรกกำหนดแล้วใน PROJECT_DIRECTION:** โพรง/เพิงหินระดับผิวที่เห็น
และเข้าได้จากด้านนอกตามความต้องการของ route; ระบบถ้ำใต้ดินยาว/เชื่อมกันพักไว้
แยก milestone ไม่ขยาย `build_terrain` ไปทำระบบถ้ำก่อน slice ผ่าน

## 3. ป่า

| งาน | สถานะ |
|---|---|
| ชนิดไม้ตามหมู่ (stand) | เสร็จ (`ecology.py`) |
| อายุ / ความหนาแน่น / โครงสร้างต่อ stand | **เพิ่ม structural age แล้ว:** `stand_age_field` ต่อเนื่องข้าม region; หมู่แก่เพิ่ม emergent/ลด understory ส่วนหมู่อ่อนทำกลับกัน. ยังต้อง paint/readback วัดสัดส่วนจริง |
| ขอบป่า (แถบ scrub, ต้นเล็กลงที่ขอบ) | **ทำในโค้ดแล้ว:** `forest_ecotone_mask` สร้าง scrub 2 บล็อกและกันน้ำ; `forest_interior_factor` ทำ ramp 4 บล็อกด้านใน ลด emergent/canopy, คง understory และทำต้นริมป่าให้เล็กลง. ตัวอย่างจริง 9 พื้นที่มี structured edge 13.89% ของป่า; ยังรอ world gate |
| podzol halo ใต้เรือนยอดจริง | **ทำในโค้ดแล้ว:** หลังต้นวางสำเร็จจึงค่อยสร้าง halo จางตามรัศมี; สนเน้น podzol, ร่องชื้นเป็น moss, โคนเป็น rooted dirt และไม่ทับน้ำ/น้ำแข็ง/หิมะ. ยังรอ world-readback |
| ไม้ยืนตาย + ไม้ล้มเป็นกลุ่ม | **ผูกกับ structural age แล้ว:** หมู่แก่เพิ่ม fallen log/stump, หมู่อ่อนเพิ่ม bush; boulder คงสัดส่วนธรณี และทุก object วางแบบ atomic. ยังต้อง world-readback |
| vine / moss / glow_lichen บนลำต้น | **เพิ่ม vine/glow lichen แล้ว:** เฉพาะหมู่ชื้น โดยหมู่แก่มีมากกว่า, block-state ยึดหน้าลำต้นถูกทิศ และวาง atomic แยกจากตัวต้น; moss บนลำต้นยังใช้ glow lichen เป็นตัวแทนเพราะ vanilla ไม่มี moss face. ยังต้อง world-readback |
| **atomic placement** | **เพิ่มแล้วสำหรับต้นไม้และ geophilic decor ในส่วนที่ region เป็นเจ้าของ:** ถ้าเขียน fragment ไม่ครบ/ชนบล็อกจะ rollback; ขอบ region ยังแบ่ง ownership ตามเดิม จึงต้อง world-readback ตรวจวัตถุคร่อมขอบ |
| `tree_schematics.rotate()` | หมุน `axis`/`facing`/`rotation` แล้ว; เหลือทดสอบ schematic จริงหลายชนิด |

## 4. ทุ่ง

**แก้แล้ว 2026-08-18** สามข้อ:
- หิมะบาง (<= `SNOW_TUFT_MAX`/8 บล็อก) ถูกเจาะเป็นหย่อมด้วย noise+hash ตัวเขียน
  หิมะกับตัวปลูกพืชอ่าน mask เดียวกันจึงไม่ตีกัน  หิมะหนายังกลบเหมือนเดิม
- `beach` ปลูกได้แล้วผ่านโซน `shore` (กอหญ้าบางตามซอกกรวด ไม่มีพืชสองบล็อก)
- ดอกไม้สองบล็อก (rose_bush/peony/lilac/sunflower) เป็นหย่อมย่อยในหย่อมดอกไม้
  ต่ำกว่า 1,500 m ไม่เกิน 5% ของพื้นที่ปลูก

เพิ่มใน branch นี้: `vegetation.meadow_zone` แยก `meadow_wet` / `pasture` /
`meadow` และ `meadow_disturbance` ผูกหย่อมการกินแทะ/เหยียบย่ำกับความแห้ง,
ความชัน, ความสูงและ world-space noise เดียวกับ coarse dirt. ตัวอย่างจริง 9 พื้นที่
มี pasture 12.90% ของทุ่งที่เข้าเกณฑ์ และรอยดิน 6.78% ของ pasture; ยังขาด
การอ่านกลับจากโลกจริง

## 5. Ecotone

**แก้แล้ว 2026-08-18**: `PALETTE["scrub"]` (ดินหยาบ/พอดโซล/กรวดเป็นหลัก) +
โซน `scrub` ใน `vegetation.ground_cover` (azalea เป็นทรงพุ่ม, พุ่มเบอร์รี่เหนือ
900 m, เฟิร์นตามร่องชื้น, พุ่มแห้ง/ดินโล่งบนที่แห้ง) และ painter เลือกโซนจาก
landcover ก่อนดู forest_p

ที่ทำเพิ่มใน branch นี้: ขอบ land รอบ forest สองบล็อกถูก dither เป็น scrub ด้วย
`surface.forest_ecotone_mask` (ชั้นแรกหนา ชั้นสองบาง) และกันน้ำออกจาก ecotone;
ด้านในใช้ `forest_interior_factor` 4 บล็อกกดต้น emergent/canopy ให้เหลือต้นเล็ก
กับ understory ก่อนถึง scrub. กฎพร้อมแล้วแต่ยังต้องอ่านกลับ/ดู silhouette จากโลกจริง

## 6. ภูเขา — room ที่เหลือ

- **สันเขาถูกลบคม** — DEM 4 m/px เฉลี่ยสันให้มน แอลป์จริงมีสันคมแบบ arête
  เพิ่ม `micro_relief.ridge_aretes` แบบ opt-in แล้ว: แตะเฉพาะสันนูนเหนือ 1,750 m
  ที่มีไหล่ชันสองด้าน, ยกไม่เกิน 2 บล็อก, ไม่ยกหุบ/น้ำ และไม่ wrap ขอบ tile.
  patch 1,024² รอบยอดสูงสุดแตะ 2.20% ของพื้นที่ (median 0.94, max 2 บล็อก);
  ทดลองผ่าน `python terrain_shape.py --arete`; ยังไม่เปิด default จนกว่าจะผ่าน
  golden silhouette + world readback
- **หน้าผาแบบ wall ลดแล้ว** — ลด bedding snap จาก strength 1.0 เป็น 0.25 หลังพบ
  ขั้นซ้ำ 11–12 บล็อกที่ `(5880, 5554)` และ paint วัสดุเป็นแนวชั้นบนด้านข้าง
  ที่เปิดออกจริงแล้ว ยังต้องตรวจภาพในเกมก่อนล็อกค่า
- **`ice` 37.3% ในโซนสูง** — ปรับแล้ว: `surface.glacier_rock_window_mask` เปิด
  หน้าต่างหินบน glacier ที่ชัน/นูนแบบ deterministic และโหมดทดลอง
  `paint_surface.py --glacier-detail` เพิ่มรอยแยกน้ำแข็งตามแนว stress กับแนว
  lateral moraine ที่ไม่ข้ามน้ำ; บนข้อมูลจริง 260,035 ช่อง glacier ได้ crevasse
  15,084 ช่อง (5.80%) และ moraine 11,302 ช่องโดยไม่ทับน้ำ/น้ำแข็ง;
  unit/integration ผ่านแล้ว แต่ paint/readback ยังรอสิทธิ์เข้าถึง save จึงยังไม่
  เปิดเป็นค่าเริ่มต้น
- `granite_slab` (149,103,85 / regularity 0.26) เป็น slab น้ำตาลตัวเดียวที่ลาย
  ธรรมชาติ — ใช้ได้ถ้าอยากได้ครึ่งขั้นบนที่ลาดชันที่ดินบาง

## 7. เก็บกวาด

- `build_terrain.py` **เพิ่ม fingerprint แล้ว** — `--resume` ตรวจ code, terrain,
  hydrology input และโหมด shelters ก่อนข้าม region (เทียบกับ `paint_fingerprint()`)
- session lock **fail-closed แล้ว** — ถ้า ACL ทำให้ตรวจ/เปิด `session.lock` ไม่ได้
  จะหยุด build แทนการตีความว่าโลกว่าง
- dead code: `vegetation.tree_positions`, `vegetation.SHAPES`, `GROUND_FOREST/MEADOW/ALPINE`
- `surface.biome_of` / `surface.BIOMES` ตายแล้วหลังย้ายไป `biomes.py`
- ค่าคงที่โซน (TREELINE/MONTANE/palette) ฝังใน `surface.py` ไม่ได้อยู่ใน config
- `render_view.py` ตรวจซ้ำแล้ว: ภาพครึ่งจอที่พิกัด acceptance เกิดจากกล้องอยู่ใน
  ร่องที่ local relief 47 บล็อก ไม่ใช่ LOD index เสีย; เพิ่ม camera-site warning
  และให้ `golden_patches.choose_camera` หักคะแนนจุดที่ผาสูงประชิด. prototype
  เลือกจุดใหม่ที่ rise 6 / relief 11 โดยไม่ขึ้น warning; ยังต้องเทียบ world จริง
  ก่อนยกระดับ renderer image เป็นเกณฑ์ผ่าน
- `report_metrics.py` ยังรายงาน "คอลัมน์สูงกว่า y=255" จากการอนุมาน ควรอ่าน biome
  กลับจากโลกจริงแทน

## ข้อตกลง lake-only

foundation backfill เขียนเฉพาะอากาศ/น้ำเก่าและไม่ทับ solid block ที่มีอยู่
จึงอุดโพรงน้ำเก่าหลังเปลี่ยนความลึกได้ โดยไม่ทำลายสิ่งปลูกสร้างหรือชั้นหินเดิม
