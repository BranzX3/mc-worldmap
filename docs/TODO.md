# งานที่เหลือ — เรียงตามตัวเลขที่วัดได้

ตัวเลขทั้งหมดมาจาก `python report_metrics.py` (fullscale) เว้นที่ระบุไว้เป็นอย่างอื่น
รันใหม่ก่อนเริ่มงานเสมอ เพราะตัวเลขเปลี่ยนตามที่ input เปลี่ยน

## 1. น้ำ — รื้อใหม่ (ไม่ผ่าน visual acceptance)

| งาน | ตัวเลขปัจจุบัน |
|---|---|
| แยก lake/river/stream ตั้งแต่ OSM | **prototype ผ่าน:** `water_sources.npz` เก็บ polygon, ordered way, kind, width |
| river profile + channel carving | **patch ผ่านโครงสร้าง:** 4-connected, ไหลลง, reshape ก่อน build; ยังไม่รัน global |
| lake bank terrain | **patch ผ่านโครงสร้าง:** dry bank ต่ำกว่าน้ำ 0/208; ยังไม่รัน global |
| bathymetry | **patch ผ่านโครงสร้าง:** depth 1..26, max-depth share 3.18%; ยังไม่รัน global |
| waterfall | **patch ใหม่:** สร้างจาก directed profile เฉพาะ drop 3–4 พร้อม lip/curtain/pool |
| shoreline ecology / พืชน้ำ | รอ geometry และ flow field ชุดใหม่ก่อน |
| ~~ลำธารเป็นปล่องหิน~~ | **แก้แล้ว:** น้ำที่อยู่ในหุบ 93% -> 21% (hill_junction), ขุดลึกสุด 123 -> 33 ดู `WATER_REDESIGN.md` |
| ตลิ่งยังเป็นขั้นเกินธรรมชาติ | **เหลืออยู่:** `bank_unwalkable_excess` +6..10% (เทียบ DEM ดิบ) |
| ร่องน้ำลึกเกินเพดาน | **เหลืออยู่ ตัวใหญ่สุด:** การยุบหน้าตัดต่อยอดตัวเองจนลึก p90 27 บล็อก — `canyon_share_excess` สูงสุด 66.9% ทางแก้ที่เสนอ: ยุบเข้าหาระดับ centerline แทนค่าต่ำสุดของ run (ดู `WATER_REDESIGN.md`) |
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

## 2. ถ้ำ / ชะง่อน — 0% ทั้งแผนที่

`build_terrain.py` ถม `solid = ys <= tile` = **หินตัน 100% ไม่มีที่ว่างภายในเลย**
โลกจาก heightmap เป็น 2.5D โดยนิยาม ผาสูง 36 บล็อกทุกลูกเป็นผนังตันเรียบ

เป็นงานเดียวที่ต้องรื้อ `build_terrain` จริง ๆ **ต้องตัดสินก่อนเริ่ม:**
โพรง/เพิงหินที่ผิว (เห็นจากนอก ไม่ลึก) หรือระบบถ้ำใต้ดินจริง (ยาว เชื่อมกัน
ใช้เป็นพื้นที่เล่นของ MMO ได้) — ขนาดงานต่างกันมาก

## 3. ป่า

| งาน | สถานะ |
|---|---|
| ชนิดไม้ตามหมู่ (stand) | เสร็จ (`ecology.py`) |
| อายุ / ความหนาแน่น / โครงสร้างต่อ stand | ยังไม่มี |
| ขอบป่า (แถบ scrub, ต้นเล็กลงที่ขอบ) | ยังไม่มี |
| podzol halo ใต้เรือนยอดจริง | ยังไม่มี |
| ไม้ยืนตาย + ไม้ล้มเป็นกลุ่ม | กระจายเดี่ยวบนตาราง 13 บล็อก |
| vine / moss / glow_lichen บนลำต้น | ยังไม่มี |
| **atomic placement** | ต้นไม้เขียนได้บล็อกเดียวก็นับเป็น 1 ต้น |
| `tree_schematics.rotate()` | ไม่หมุน `axis`/`facing` (มี caveat ในไฟล์อยู่แล้ว) |

## 4. ทุ่ง

- **หิมะบล็อกพืชทั้งหมด** — `plantable` ตัด `snow > 0` ทุ่งที่มีหิมะบาง 1 layer โล่งเกลี้ยง
- **beach ถูกตัดจาก plantable** — ริมน้ำเป็นพื้นที่ตาย
- ไม่มีดอกไม้ 2 บล็อก (rose_bush / peony / lilac / sunflower)
- ไม่มี meadow planner (dry / wet / pasture)

## 5. Ecotone

`scrub` จาก OSM (4.10% ของแผนที่) **ไม่ถูกอ้างถึงที่ไหนเลยทั้ง repo** — เป็นชั้น
พุ่มระหว่างป่ากับทุ่ง/หินที่หายไปทั้งชั้น

## 6. ภูเขา — room ที่เหลือ

- **สันเขาถูกลบคม** — DEM 4 m/px เฉลี่ยสันให้มน แอลป์จริงมีสันคมแบบ arête
  `micro_relief` แก้แต่หน้าผากับตีนผา ยังไม่มีอะไรจัดการสันเขา
- **หน้าผาแบบ wall ลดแล้ว** — ลด bedding snap จาก strength 1.0 เป็น 0.25 หลังพบ
  ขั้นซ้ำ 11–12 บล็อกที่ `(5880, 5554)` และ paint วัสดุเป็นแนวชั้นบนด้านข้าง
  ที่เปิดออกจริงแล้ว ยังต้องตรวจภาพในเกมก่อนล็อกค่า
- **`ice` 37.3% ในโซนสูง** — มาจาก `landcover == glacier` ของ OSM ตรง ๆ ซึ่งอาจ
  เป็นข้อมูลเก่า และเป็น `packed_ice` ล้วน ไม่มีหินโผล่ รอยแตก หรือ moraine
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
