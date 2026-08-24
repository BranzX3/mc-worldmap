# งานที่เหลือ — เรียงตามตัวเลขที่วัดได้

ตัวเลขทั้งหมดมาจาก `python report_metrics.py` (fullscale) เว้นที่ระบุไว้เป็นอย่างอื่น
รันใหม่ก่อนเริ่มงานเสมอ เพราะตัวเลขเปลี่ยนตามที่ input เปลี่ยน

## 1. น้ำ — รื้อใหม่ (ไม่ผ่าน visual acceptance)

| งาน | ตัวเลขปัจจุบัน |
|---|---|
| แยก lake/river/stream ตั้งแต่ OSM | **prototype ผ่าน:** `water_sources.npz` เก็บ polygon, ordered way, kind, width |
| river profile + channel carving | **รัน global แล้ว 2026-08-18** (`hydrology_global2`): audit 30 หน้าต่าง invariant 0/0/0, รอยต่อ tile 4.41% เทียบภายใน 4.29% |
| lake bank terrain | **patch ผ่านโครงสร้าง:** dry bank ต่ำกว่าน้ำ 0/208; ยังไม่รัน global |
| bathymetry | **รัน global แล้ว** — `bed_flat_share` ไล่จนจบแล้วและสรุปว่าใช้เป็นด่านไม่ได้ (ดู WATER_REDESIGN) ตัวที่ยังวัดได้จริงคือ `bed_flat_lake` |
| waterfall | **patch ใหม่:** สร้างจาก directed profile เฉพาะ drop 3–4 พร้อม lip/curtain/pool |
| shoreline ecology / พืชน้ำ | **เพิ่มแล้ว:** `flow_index` + `water_ecology.lake_fetch` ตัดสินวัสดุก้นน้ำ/หญ้าน้ำ/ใบบัว/กกจากแรงน้ำและ fetch; ยังต้องอ่านกลับโลกจริงรอบสุดท้าย |
| ~~ลำธารเป็นปล่องหิน~~ | **แก้แล้ว:** น้ำที่อยู่ในหุบ 93% -> 21% (hill_junction), ขุดลึกสุด 123 -> 33 ดู `WATER_REDESIGN.md` |
| ตลิ่งยังเป็นขั้นเกินธรรมชาติ | **เกือบผ่าน:** branch `codex/immersion-bank-morphology` เพิ่ม final seal cap + `smooth_walkable_banks`; golden `bank_final` ลด excess เป็น prototype -0.5%, player -0.4%, cliff 3.1%, steep 3.8%, hill 5.1% (เกณฑ์ 5% ยังเกิน 0.1 จุด). hard invariant 0 ทุก patch และ compare ไม่มี metric แย่ลง. เหลืองานแยก feature/ordinary bank ใน hill และ paint/readback รอบสุดท้าย |
| ร่องน้ำลึกเกินเพดาน | **แก้ใน golden patch ปัจจุบันแล้ว:** wild_canyon p90 27/26 -> 3/2 และ excess 0%; lake_mouth ยังเหลือ p90 13 / excess 13.3% ต้องสร้าง global ใหม่ก่อนอ้างว่าทั้งแผนที่ผ่าน |
| วัสดุก้นน้ำเป็นแถบเดียว | **ดีขึ้นจาก materialfine/mouthbar 2026-08-20:** geometry readback 3 patch ยัง 0 error, wild/steep same-neighbour 88.2%/86.8%; lake-mouth stream 26 cells เปลี่ยน sand 100% เป็น sand 88.5% + gravel 11.5%, แต่ sample เล็กและ lake same-neighbour ยัง 94% |
| global hard gate | **ผ่านใน `hydrology_global3` 2026-08-18:** ทะเลสาบลอยเหนือลำน้ำ 64 -> 0 cells, invariant อื่นยัง 0, seam 3.674% vs ภายใน 3.256%; ยังห้ามเรียกว่า visual ผ่านเพราะ terrain lift เพิ่มใน 4 golden metrics |
| invariant ที่ต้องเป็นศูนย์ | **ผ่านครบ** 11 golden patches + สุ่มนอกชุด 10 จุด: ตลิ่งลอย 0, ช่องว่าง 0, หน้าตัดไม่ราบ 0 |

ก่อนแตะโค้ดน้ำ/ภูมิประเทศ ให้รัน `python golden_patches.py run --tag before`
แล้วเทียบด้วย `compare` หลังแก้ — ดู `docs/PIPELINE.md` หัวข้อ golden patches

หลักฐานและ pipeline ใหม่อยู่ใน `docs/WATER_REDESIGN.md` ห้ามใช้ค่า
"ตลิ่งไม่ได้แต่ง 0%" เป็นเกณฑ์ผ่าน เพราะ metric เดิมวัด probability ของวัสดุ
ไม่ใช่รูปทรง bank ที่เห็นในเกม

แพตช์ acceptance ปัจจุบันอยู่ที่ `(6021, 6142)` ขนาด 256 บล็อก โดยใช้
hydrology context 384 บล็อก เขียนลงโลกแล้วและผ่านการอ่านกลับ 480 คอลัมน์น้ำ:
ไม่มีบล็อกขาด/เกิน, ไม่มีน้ำเก่าค้างในตัวอย่าง 500 จุด และ vertical run สูงสุด
7 บล็อกเกิดเฉพาะ waterfall feature (ของเดิมสูง 9–11 บล็อกตามแนวลำธาร)

## 2. ถ้ำ / ชะง่อน — unit safety ผ่าน แต่ยังไม่ผ่าน world gate

`build_terrain.py` ถม `solid = ys <= tile` = **หินตัน 100% ไม่มีที่ว่างภายในเลย**
โลกจาก heightmap เป็น 2.5D โดยนิยาม ผาสูง 36 บล็อกทุกลูกเป็นผนังตันเรียบ

มี prototype `rock_shelters.py` ใน working tree แล้ว แก้ root cause ที่ seed อาจเห็น
คอลัมน์ต่ำไกล 3 บล็อกแต่ไม่มีอากาศติดปากโพรงจริง และเพิ่ม test ตรวจว่าทุก cut
component เชื่อมถึงอากาศภายนอกที่สูงพ้นน้ำ ปัจจุบันผ่าน 10/10 test แล้ว แต่
`build_terrain` ยังปิดเป็น default และเปิดทดลองได้เฉพาะ `--shelters` จนกว่าจะ
paint หน้าผาตัวแทนแล้วอ่านบล็อกจริงกลับครบทั้งพื้น เพดาน ปาก และการเชื่อมต่อ

เป็นงานเดียวที่ต้องรื้อ `build_terrain` จริง ๆ **ต้องตัดสินก่อนเริ่ม:**
โพรง/เพิงหินที่ผิว (เห็นจากนอก ไม่ลึก) หรือระบบถ้ำใต้ดินจริง (ยาว เชื่อมกัน
ใช้เป็นพื้นที่เล่นของ MMO ได้) — ขนาดงานต่างกันมาก

## 3. ป่า

| งาน | สถานะ |
|---|---|
| ชนิดไม้ตามหมู่ (stand) | เสร็จ (`ecology.py`) |
| อายุ / ความหนาแน่น / โครงสร้างต่อ stand | ยังไม่มี |
| ขอบป่า (แถบ scrub, ต้นเล็กลงที่ขอบ) | **เริ่มแล้ว:** `surface.forest_ecotone_mask` สร้างแถบ scrub 2 บล็อกแบบ dither และมี regression test; ยังขาด canopy/understory/deadwood ที่สัมพันธ์กัน |
| podzol halo ใต้เรือนยอดจริง | ยังไม่มี |
| ไม้ยืนตาย + ไม้ล้มเป็นกลุ่ม | กระจายเดี่ยวบนตาราง 13 บล็อก |
| vine / moss / glow_lichen บนลำต้น | ยังไม่มี |
| **atomic placement** | ต้นไม้เขียนได้บล็อกเดียวก็นับเป็น 1 ต้น |
| `tree_schematics.rotate()` | ไม่หมุน `axis`/`facing` (มี caveat ในไฟล์อยู่แล้ว) |

## 4. ทุ่ง

**แก้แล้ว 2026-08-18** สามข้อ:
- หิมะบาง (<= `SNOW_TUFT_MAX`/8 บล็อก) ถูกเจาะเป็นหย่อมด้วย noise+hash ตัวเขียน
  หิมะกับตัวปลูกพืชอ่าน mask เดียวกันจึงไม่ตีกัน  หิมะหนายังกลบเหมือนเดิม
- `beach` ปลูกได้แล้วผ่านโซน `shore` (กอหญ้าบางตามซอกกรวด ไม่มีพืชสองบล็อก)
- ดอกไม้สองบล็อก (rose_bush/peony/lilac/sunflower) เป็นหย่อมย่อยในหย่อมดอกไม้
  ต่ำกว่า 1,500 m ไม่เกิน 5% ของพื้นที่ปลูก

เพิ่มใน branch นี้: `vegetation.meadow_zone` แยก `meadow_wet` / `pasture` /
`meadow` จากความชื้น ความชัน และระดับสูง และ painter ใช้กฎเดียวกัน; ยังขาดการ
ผูก disturbance/การกินแทะกับ stand และการอ่านกลับจากโลกจริง

## 5. Ecotone

**แก้แล้ว 2026-08-18**: `PALETTE["scrub"]` (ดินหยาบ/พอดโซล/กรวดเป็นหลัก) +
โซน `scrub` ใน `vegetation.ground_cover` (azalea เป็นทรงพุ่ม, พุ่มเบอร์รี่เหนือ
900 m, เฟิร์นตามร่องชื้น, พุ่มแห้ง/ดินโล่งบนที่แห้ง) และ painter เลือกโซนจาก
landcover ก่อนดู forest_p

ที่ทำเพิ่มใน branch นี้: ขอบ land รอบ forest สองบล็อกถูก dither เป็น scrub ด้วย
`surface.forest_ecotone_mask` (ชั้นแรกหนา ชั้นสองบาง) และกันน้ำออกจาก ecotone;
ที่ยังขาดคือ canopy/ต้นเล็ก/deadwood ที่ทำให้ transition อ่านได้ในเกมจริง

## 6. ภูเขา — room ที่เหลือ

- **สันเขาถูกลบคม** — DEM 4 m/px เฉลี่ยสันให้มน แอลป์จริงมีสันคมแบบ arête
  `micro_relief` แก้แต่หน้าผากับตีนผา ยังไม่มีอะไรจัดการสันเขา
- **หน้าผาแบบ wall ลดแล้ว** — ลด bedding snap จาก strength 1.0 เป็น 0.25 หลังพบ
  ขั้นซ้ำ 11–12 บล็อกที่ `(5880, 5554)` และ paint วัสดุเป็นแนวชั้นบนด้านข้าง
  ที่เปิดออกจริงแล้ว ยังต้องตรวจภาพในเกมก่อนล็อกค่า
- **`ice` 37.3% ในโซนสูง** — ปรับแล้ว: `surface.glacier_rock_window_mask` เปิด
  หน้าต่างหินบน glacier ที่ชัน/นูนแบบ deterministic; crevasse/moraine เชิง
  geometry และ world readback ยังต้องตรวจ
- `granite_slab` (149,103,85 / regularity 0.26) เป็น slab น้ำตาลตัวเดียวที่ลาย
  ธรรมชาติ — ใช้ได้ถ้าอยากได้ครึ่งขั้นบนที่ลาดชันที่ดินบาง

## 7. เก็บกวาด

- `build_terrain.py` **ไม่มี fingerprint** → `--resume` ข้าม region ได้แม้ input เปลี่ยน
  (`paint_surface` มีแล้ว ดู `paint_fingerprint()`)
- dead code: `vegetation.tree_positions`, `vegetation.SHAPES`, `GROUND_FOREST/MEADOW/ALPINE`
- `surface.biome_of` / `surface.BIOMES` ตายแล้วหลังย้ายไป `biomes.py`
- ค่าคงที่โซน (TREELINE/MONTANE/palette) ฝังใน `surface.py` ไม่ได้อยู่ใน config
- `render_view.py` near-field LOD พังครึ่งภาพเมื่อมีผาใกล้กล้อง
- `report_metrics.py` ยังรายงาน "คอลัมน์สูงกว่า y=255" จากการอนุมาน ควรอ่าน biome
  กลับจากโลกจริงแทน

## ข้อตกลง lake-only

foundation backfill เขียนเฉพาะอากาศ/น้ำเก่าและไม่ทับ solid block ที่มีอยู่
จึงอุดโพรงน้ำเก่าหลังเปลี่ยนความลึกได้ โดยไม่ทำลายสิ่งปลูกสร้างหรือชั้นหินเดิม
