# การตัดสินใจที่ผ่านการวัดมาแล้ว — อย่ารื้อโดยไม่อ่าน

ทุกข้อในนี้มีตัวเลขกำกับ ถ้าจะเปลี่ยนให้วัดใหม่ก่อน

## สเกล 1:1 และการปลด cap ความสูง

`METERS_PER_BLOCK = 4` แนวนอน และแนวตั้งก็ 4 m/block เท่ากัน (`Y_TERRAIN_MIN=0`,
`Y_TERRAIN_MAX=640`) → **ไม่มีการบิดเบือนสัดส่วน (วัดได้ 0.999x)**

ต้องใช้ datapack ขยาย dimension เป็น 784 บล็อก (49 sections ≈ 2x วานิลลา)

**ทำไมต้อง 1:1** — ความชันในเกม = ความชันจริง × (m/block แนวนอน ÷ แนวตั้ง)
ที่สเกลเดิม 7.52 m/block อัตรานี้เป็น 0.53 ทำให้**ผาจริง 45° กลายเป็นทางลาด
0.53 บล็อกต่อก้าว = เดินขึ้นสบาย** หน้าผาหินปูนทั้งเทือกหายไปหมด

**ข้อควรระวัง:** ยิ่งละเอียดแนวนอน (เช่น 2 m/block) แผนที่ยิ่ง **ลาดกว่าเดิม**
ไม่ใช่ชันขึ้น — เป็นเรื่องที่เข้าใจผิดกันง่าย

ผลปัจจุบัน (ความละเอียดเต็ม):
```
ราบ 49.20% | ก้าวขึ้นได้ 47.06% | ต้องกระโดด 1.78% | ปีนไม่ได้ 1.89%
ขั้นสูงสุด 36 บล็อก
```

## ค่าที่ต้องอ่านจากเกม ห้ามเขียนจากความจำ

เกมเวอร์ชัน 26.2 (`DataVersion 4903`, `data_major 107`) มี schema ต่างจากที่จำกันมา
**เขียน dimension_type เองแล้วโลกโหลดไม่ขึ้นมาแล้วหนึ่งครั้ง**

| ผมเคยเดา | ของจริง |
|---|---|
| `pack_format: 57` + `supported_formats` | `107` + **`min_format`/`max_format`** (บังคับตั้งแต่ format 81) |
| `monster_spawn_light_level: {type, value:{...}}` | `{type, max_inclusive, min_inclusive}` ไม่มี `value` ซ้อน |
| — | **บังคับมี `has_ender_dragon_fight`** |
| `ultrawarm`/`natural`/`bed_works`/`effects` | ย้ายเข้า `attributes` หมดแล้ว |

`make_world_datapack.py` จึง **อ่าน `dimension_type/overworld.json` ของวานิลลาจาก
jar แล้วแก้เฉพาะ 3 ฟิลด์** และมี test เทียบกับ jar ทุกครั้งที่รัน

พาธ jar: `%APPDATA%/ModrinthApp/meta/versions/26.2/26.2.jar` (หาอัตโนมัติ)

## เมฆและหมอก

`minecraft:visual/cloud_height` วานิลลา = 192.33 สำหรับโลกสูง 384 —
**ภูเขาเราสูงถึง y=640 ถ้าไม่ยก เมฆจะลอยตัดกลางเทือกเขา** ตั้ง `CLOUD_HEIGHT=680`

**หมอกคุมได้ที่ dimension_type ไม่ใช่ biome** (`minecraft:visual/fog_color`)
มีแค่ 8/66 biome ที่ override fog และไม่มีตัวไหนเป็น overworld ผิวดินเลย

## biome — ทำเองเพราะวานิลลาให้สีไม่พอ

สีหญ้าวานิลลามาจาก colormap ที่ index ด้วย (temperature, downfall) และ
temperature ถูก clamp ที่ 0 → **grove, snowy_slopes, jagged_peaks, frozen_peaks,
snowy_taiga ให้สีหญ้าเดียวกันเป๊ะ (128,180,151)** ไล่ระดับทั้งเทือกได้แค่ 3 เฉด

จึงสร้าง 7 custom biome ใน `biomes.py` (namespace `salzkammergut:`) กำหนด
`grass_color`/`foliage_color`/`water_color` เอง

**ยืนยันแล้วว่าเขียนได้:** `get_add_biome()` รับ string อะไรก็ได้ และ
PyMCTranslate เป็น pass-through สำหรับชื่อที่ไม่รู้จัก

**กับดัก:** `stony_peaks` ชื่อเหมือนจะตรงกับยอดหินปูน แต่ `temperature = 1.00`
คือ biome ร้อน

### การเขียน biome

`ch.biomes[:, :, :] = id` **ผิด** — slice ที่ไม่ระบุขอบถูก clamp ที่ y 0..255
(`Biomes3D` ตั้ง `default_section_counts=(0,16)` section สูง 4 บล็อก) ทำให้
**28% ของแผนที่ (ภูเขา) ไม่มี biome เลย**

ต้อง `chunk.biomes[:, y0:y1, :]` โดย y เป็นหน่วย cell (4 บล็อก) และ
**ต้องส่ง array ที่ shape ตรงเป๊ะ — amulet ไม่ broadcast**

## palette น้ำหนักไม่เป็นจริงมาตั้งแต่ต้น (แก้แล้ว)

`r` ที่ `pick()` ใช้เป็นระฆังคว่ำ (std 0.141) ส่วน `pick()` แบ่งช่วง [0,1) ตาม
น้ำหนักสะสม → **หยิบแต่ตัวกลางของรายการ บล็อกหัวและท้ายแทบไม่เคยปรากฏ**

```
rock_mid ก่อนแก้:  stone ตั้ง .26 ได้ .044 | dripstone ตั้ง .06 ได้ .000
```

แก้ด้วย `surface.uniformise()` (ดัด histogram ด้วยตาราง CDF คงที่)
มี test ตรวจทุก palette ทุกบล็อกว่าได้สัดส่วนตามที่ประกาศ ±0.03

**หลักการเดียวกันใช้กับ dither ใน `terrain_shape` ด้วย** — value noise ดิบมีแค่
5% ที่แรงพอจะพลิกการปัด ต้องดัดก่อน

## ภูมิสัณฐานย่อย (`micro_relief.py`)

DEM ที่ 4 m/px ไม่มีรายละเอียดระดับ 10-40 m ที่ตาใช้อ่านว่าเป็นหินจริง
เติมสามอย่าง: ชั้นหินยื่น 9.78% / กองหินเชิงผา 5.80% / หลุมยุบหินปูน 0.78%

ผล: ขอบขั้นที่เป็นเส้นตรงยาว **0.255 → 0.174** (dither อย่างเดียวได้แค่ 0.245)

### สองบั๊กที่ทำให้ bedding แทบไม่ทำงาน (แก้แล้ว)

1. **ความหนาชั้นแปรต่อเนื่อง** → ระดับชั้นเลื่อน 0.08 บล็อกต่อบล็อกแนวนอน
   สะสมครบ 1 บล็อกใน 12 ก้าว ชานหินขาดตลอด
   แก้เป็น **ผืนที่ค่าคงที่ภายใน** (`_quantise`, `BED_LEVELS=4`)
2. **`def bedding_ledges(..., strength=BED_STRENGTH)`** — default argument ถูก
   ประเมินครั้งเดียวตอน import การแก้ค่าโมดูลทีหลังไม่มีผล **การจูนทั้งชุด
   เป็นโมฆะโดยไม่มีอะไรฟ้อง** (ผลออกมาเท่ากันเป๊ะทุกค่า = สัญญาณเดียวที่มี)

```
ชานหิน:  ไม่มี bedding 1.43 | snap เต็ม (st 1.0) 1.61-1.95 แต่เกิดผนัง 11-12 บล็อก
ค่าปัจจุบัน (6-14, st 0.25) เก็บแนวชั้นแบบอ่อนและไม่ snap เป็นกำแพงเต็มความหนาชั้น
```

ความหนา 6-14 บล็อก = 24-56 m **หนากว่าชั้น Dachsteinkalk จริง (1-10 m) อย่าง
ตั้งใจ** เพราะชั้นจริงบางเกินกว่าจะเห็นที่ 4 m/block

ตรวจ fullscale หลังลด strength: ขอบขั้นเส้นตรงยาว 0.234, พื้นที่ bedding ที่ขยับ
เกินครึ่งบล็อก 4.85%, และระดับที่ขยับจาก DEM เกิน 1 บล็อกเหลือ 0.20%

ด้านข้างหน้าผาห้ามใช้ block hash สุ่มตรง ๆ เพราะกลายเป็นลายพราง ใช้ geology field
ต่อเนื่องหลายสเกลแทน: macro domain 52 บล็อก, warp ชั้นหิน 24 บล็อก, joint จาก
zero-contour ที่สเกล 18 บล็อก และคราบมอสเฉพาะ joint ชื้นต่ำกว่าแนว montane

การเลือกวัสดุผิวห้ามจบที่แต่ละคอลัมน์แยกกัน: `contextual_surface_blend()` อ่าน
เพื่อนบ้าน 4 ทิศ ให้ majority ภายใน material family รวมเป็นหย่อม และใช้ transition
palette เฉพาะดิน↔หิน (coarse/gravel/moss/cobble/mossy_cob/andesite) วัสดุหลัง
blend เป็นแหล่งเดียวที่ตัดสินทั้ง full block, cliff face ชั้นบน และ slab

## slab ไล่ระดับครึ่งบล็อก

ใช้ `terrain_sub` ตัดสิน: `sub < -0.25` → บล็อกบนสุดเป็น slab, `> +0.25` → วาง
slab บน y+1 ได้ความละเอียดแนวตั้งสองเท่า (43,306 slab ในแปลง 400x400)

**วานิลลามี slab เฉพาะหิน** — ดิน หญ้า มอส กรวด ทราย ไม่มีเลย
`mud_brick_slab` มี แต่ลายอิฐ (regularity 0.78 เทียบกับหินธรรมชาติ 0.23-0.52)
ทางเดียวที่จะได้ slab ดินคือ retexture ผ่าน resource pack

**slab ห้ามอยู่คอลัมน์เดียวกับหิมะ** — slab กินครึ่งช่อง หิมะในช่องถัดไปจึงลอย
0.5 บล็อก (วัดก่อนแก้: 23/23 คอลัมน์ลอยทุกอัน) ที่มีหิมะใช้ความหนาหิมะทีละ
1/8 บล็อกแทน ซึ่งละเอียดกว่า

ของอื่นปลอดภัยเพราะเงื่อนไขกันเอง: พืช/ต้นไม้/decor ต้องการ `soil` ส่วน slab มี
เฉพาะหิน

## Rock transition graph

วัสดุหินไม่ได้เป็น family เดียวกันทั้งหมดอีกต่อไป แต่แยกตามหน้าที่:

- bedrock: `calcite, diorite, andesite, stone, tuff, deepslate`
- fractured/mineral: `cobble, cob_deep, dripstone`
- loose deposit: `gravel, clay`
- coating: `mossy_cob, pale_moss`

majority blend ทำได้เฉพาะ bedrock ที่ติดกันในกราฟ
`calcite ↔ diorite ↔ andesite ↔ stone ↔ tuff ↔ deepslate` จึงไม่เกิดการกระโดด
จาก calcite ไป deepslate ในหนึ่งบล็อก ส่วนดิน↔หินยึด bedrock รอบข้างเป็น anchor:
แนวแห้งไล่ `coarse → gravel → local bedrock` และแนวชื้นไล่
`moss → pale_moss/mossy_cob → local bedrock`

`smooth_stone` และ `smooth_basalt` ไม่เป็น transition material; clay เป็นตะกอน
ไม่ใช่ bedrock. เมื่อ transition อยู่ตรงครึ่งระดับและวัสดุปลายทางไม่มี slab ให้ใช้
คู่ใกล้เคียง `calcite → diorite`, `dripstone → tuff`,
`pale_moss → mossy_cob`; gravel ตั้งใจคงเป็น full block เพราะเป็นวัสดุร่วน

หน้าผาสูงใช้ `calcite/diorite/andesite/stone` เป็นชุดหลัก ส่วน tuff,
dripstone และคราบมอสจำกัดไว้กับผาระดับต่ำ/กลางตามความชื้นและแนว joint

`dead_brain_coral_block` ใช้เป็น texture proxy ของหินปูนผุ ไม่ใช่ bedrock:
อยู่ในกลุ่ม fractured/weathered, ไม่ร่วม majority adoption และไม่มี slab.
บนหน้าผาระดับต่ำ/กลางจะเป็น halo รอบแกน dripstone ที่
`0.045 <= abs(joint noise) < 0.11`; บริเวณชื้นใช้ mossy cobblestone แทน halo.
ที่ตีนผามีสัดส่วน 8% เพื่อเชื่อมโทน
`packed_mud/brown_mushroom → dripstone → dead brain coral → gravel/stone`.
การวัดโลกจริงรอบ `(5880, 5554)` ขนาด 81×81 หลัง paint พบ 370 บล็อกบน
หน้าตั้ง (~4%): 122 บล็อกสัมผัส dripstone และ 369 บล็อกสัมผัสหิน neutral
จึงทำหน้าที่เป็น gradient ไม่ใช่ชั้นหินหลัก

## global ต้องใช้พารามิเตอร์ชุดเดียวกับ patch

บั๊กที่เสียเวลาที่สุดในงานน้ำ: `shape_hydrology_global` ส่งค่าทับ default ของ
patch สองตัว

| | patch | global (เดิม) |
|---|---|---|
| `max_surface_step` | `MAX_WATERFALL_DROP` = 4 | **12** |
| `max_drop_per_sample` | `MAX_WATERFALL_DROP / 2` = 2.0 | **6.0** |

ผลคือ acceptance ทั้งหมดใน `WATER_REDESIGN.md` ซึ่งวัดจาก `--patch` ผ่านหมด
ทั้งที่ผิวน้ำจริงทั้งแผนที่หยาบกว่าสามเท่า ผู้เล่นเห็นเป็นน้ำกระโดดกลางสาย —
ไม่มี metric ตัวไหนจับได้เลยเพราะไม่เคยมีใครเทียบสอง path กันตรง ๆ

ตอนนี้ทั้งสองใช้ค่า default เดียวกัน และ
`tests/test_hydrology_shape.test_global_tiling_matches_single_window_patch`
บังคับให้ผลของ `--global` เท่ากับ `--patch` ทุก cell (ผ่าน mutation test:
ใส่ 6.0 กลับไปแล้วเทสต์จับได้ 15 cells)

**ห้ามใส่ค่าคงที่ทับที่จุดเรียกใน global** ถ้าจะเปลี่ยนพฤติกรรม ให้แก้ที่
`MAX_WATERFALL_DROP` ซึ่งเป็นแหล่งเดียว

### halo 8 พอแล้ว — วัดมาแล้ว อย่าเดาเพิ่ม

`limit_masked_steps` เป็น Dijkstra บนกราฟน้ำทั้งก้อน ระยะแพร่ไม่ถูกจำกัดด้วย
ค่าคงที่ใด ๆ จึงดูเหมือน halo ของ tile ต้องกว้างตาม **แต่วัดจริงแล้วไม่ใช่**:
บนผังที่มีลำธารตัดรอยต่อ tile หลายจุดพร้อมหน้าผา 60 บล็อก halo 32 / 8 / 2 ให้
ผลเหมือนกันทุก cell

เหตุผลคือ `regularize_stage` บีบ profile ไว้ที่ `MAX_WATERFALL_DROP / 2` ต่อ
sample ตั้งแต่ตอนสร้าง line profile ซึ่งเป็น global อยู่แล้ว stage ที่เข้าสู่
tile จึงเรียบพอที่ envelope แทบไม่ต้องแพร่ข้ามขอบ

`seam_step_report` ใน manifest เทียบสัดส่วนขั้น >=2 ที่ขอบ tile กับภายใน tile
ถ้าวันหนึ่งมีกฎที่แพร่ไกลกว่านี้ ตัวเลขนี้จะฟ้องเอง แล้วค่อยเพิ่มด้วย `--halo`

## RAM

`terrain_shape.py` เคยใช้เกิน 8 GB จน **MemoryError จริง** — array float32 เต็ม
แผนที่หนึ่งชุด = 400 MB และมันถือหลายชุดพร้อมกัน (`despeckle` ทำ `np.stack` ของ
4 สำเนา = 1.6 GB ก้อนเดียว)

แก้เป็น **tile 1024 + pad 20** ทุกกฎเป็น local (talus ไหลลง 10 บล็อกเป็นตัวไกลสุด)
การแบ่ง tile จึงให้ผลเท่ากับทำทีเดียว

`report_metrics.label_4conn` กับ `make_water.chamfer_distance` ยังจอง int32 เต็ม
แผนที่ (400 MB) แต่ไม่ถือสองก้อนพร้อมกันแล้ว — `label_4conn` เขียน remap ทับในที่
ทีละแถบแทน `remap[labels]` (ซึ่งเคยพีคที่ 800 MB) และ `chamfer_distance` หารด้วย 3
แบบ in-place

### การแพร่แนวนอนต่อแถวคือ prefix-minimum ไม่ใช่ลูป

`make_water.chamfer_distance` เคยวน Python ทีละ cell เพื่อทำ
`row[j] = min(row[j], row[j-1] + 3)` = 10000 x 10000 x 2 pass = **200 ล้านรอบ**

สูตรเดียวกันเขียนปิดได้: `3j + min ของ (row[k] - 3k) ทุก k <= j` ซึ่งคือ
`np.minimum.accumulate` ตรง ๆ วัดจริงที่ 4000² ได้ 5.18 วิ -> 0.22 วิ (23x)
extrapolate O(n²) ไปที่ 10000² คือ ~32 วิ -> ~1.4 วิ

`tests/test_make_water.py` ยืนยันด้วยสูตรปิดของระยะ chamfer 3-4 (ไม่ใช่แค่ "เท่าโค้ดเดิม")

### metric ที่วัดได้จากรัศมีจำกัด ให้แบ่ง tile

`report_metrics.bank_metrics` เคยเรียก `S.shore_probabilities` ทั้งแผนที่ =
float32 สองชุด (400 MB ต่อชุด) บวก int16 อีกสองชุดต่อรอบ dilate รวม ~1.5 GB
ทั้งที่ค่าของ cell ใด ๆ ขึ้นกับ input ในรัศมี `max(shore_width,
LAKE_SHORE_SEARCH_BLOCKS)` = 12 เท่านั้น จึงแบ่ง tile 2048 + pad 12 แบบเดียวกับ
`terrain_shape.py`

**ห้ามเขียนสูตรซ้ำใน metric** — ต้องเรียก `S.shore_probabilities` ตัวเดียวกับที่
paint ใช้ ไม่งั้นจะซ้ำรอยบั๊กที่เกิดมาแล้ว 4 ครั้งตาม PIPELINE.md

### `untreated_cells` ของ bank_metrics เป็น 0 เสมอโดยโครงสร้าง

ทุก bank cell อยู่ที่ `dist` 1..`shore_width` จึงมี falloff > 0 เสมอ และ
`lake_shore` กับ `stream_bank` ตัวใดตัวหนึ่งเป็นบวกเสมอ (`stream_bank` เป็นบวกได้
เฉพาะที่ `lake_body == 0` ซึ่งเป็นที่ที่ `lake_shore` เป็นศูนย์พอดี) ตัวเลข 83.6%
ใน `baseline_metrics.txt` เป็นของก่อนแก้ `surface.py` — สอดคล้องกับที่ TODO.md
เตือนไว้แล้วว่าห้ามใช้ "ตลิ่งไม่ได้แต่ง 0%" เป็นเกณฑ์ผ่าน

ผลข้างเคียง: เทสต์ tile-invariance ของ `bank_metrics` จับ pad ที่แคบเกินไม่ได้
(เทียบศูนย์กับศูนย์) ตัวที่คุ้ม pad จริงคือ
`test_shore_probabilities_only_read_nearby_input` ใน `tests/test_paint_surface.py`

### ตัวกิน RAM ที่ใหญ่ที่สุดไม่ใช่ numpy แต่เป็น amulet

`build_terrain` กับ `paint_surface` ถือทุก chunk ที่แตะไว้ใน RAM จนกว่าจะ
`level.purge()` ที่ปลาย region ต้นทุนต่อ chunk ถูกคูณด้วย `WORLD_HEIGHT = 784`
ซึ่งสูงเป็นสองเท่าของวานิลลา:

| | |
|---|---|
| 1 chunk = 49 sections x 16³ x uint32 | ~800 KB |
| RSIZE 32 = 1024 chunks | ~820 MB (จริงราว 1.0–1.5 GB รวม object/biome/NBT) |
| RSIZE 16 = 256 chunks | ~205 MB |

จึงตั้ง **`RSIZE = 16` ทั้งสองไฟล์** แลกกับ `save()` ถี่ขึ้นสี่เท่า ห้ามเพิ่มกลับ
เป็น 32 โดยไม่วัด RAM จริงก่อน (`build_terrain` พิมพ์ working set ในแถบ progress
อยู่แล้ว)

`build_progress.txt` ใช้ **index ของ region** เป็น key ไม่ใช่พิกัดบล็อก การเปลี่ยน
`RSIZE` จึงทำให้ checkpoint เดิมหมายถึงคนละพื้นที่ — `build_progress.meta.json`
เก็บ `region_chunks` ไว้ และ `--resume` จะปฏิเสธเมื่อไม่ตรง
(`paint_progress.txt` ใช้พิกัดบล็อกจึงไม่มีปัญหานี้ และมี fingerprint คุมอยู่แล้ว)

### mmap คือค่าเริ่มต้นของ input เต็มแผนที่

`paint_surface` ใช้ input ทุกตัวผ่านสไลซ์ต่อ region เท่านั้น จึงต้อง
`np.load(..., mmap_mode="r")` ให้หมด เดิม `water_mask.npy` กับ `water_depth.npy`
ตกหล่นอยู่สองตัว = จ่าย 100 MB ต่อไฟล์ทิ้งตลอดทั้งรัน ปลอดภัยเพราะผู้บริโภคทุกราย
ทำ `.copy()` ก่อนเขียน (`overlay_window`, `surface.apply_water_mask`)

ที่ยังเหลือ: `heightmap.png` (200 MB, peak ~400 ตอน PIL decode) ยัง mmap ไม่ได้
เพราะเป็น PNG ต้อง dump เป็น `.npy` ก่อน

### คำนวณเฉพาะ cell ที่จะเขียน ไม่ใช่ทั้งแผนที่แล้วค่อยเลือก

`make_water_levels.global_water_surface_levels` เคยกาง `base32` / `neighbour32` /
`close` / `ramped` / `candidate` เป็น int32 เต็มแผนที่ = **~1.7 GB ต่อรอบ x 12 รอบ**
เพื่อใช้แค่ `candidate[fill]` ทั้งที่ `fill` คือ shelf บาง ๆ รอบ core

ทุก operation ในสูตรนั้นเป็น elementwise ล้วน การ index ด้วย `fill` **ก่อน** คำนวณ
จึงให้ผลเท่ากันทุกประการ (ยืนยันด้วยการเทียบกับสูตรเดิมบนแผนที่สุ่ม 400 ชุด)
และเหลือขนาดเท่าจำนวน cell ที่เขียนจริง

เช็คลิสต์เดียวกันนี้ใช้ได้ทุกที่ในโปรเจกต์: **ถ้าบรรทัดถัดไปเป็น `x[mask]`
ให้ย้าย mask ขึ้นไปก่อน**

### การแพร่แบบแบ่งแถบต้องเป็น Jacobi ไม่ใช่ Gauss-Seidel

`hydrology_shape._component_standing_surface` แพร่ระดับผิวน้ำจาก core ออกสู่ shelf
12 รอบ เดิมทำทั้งแผนที่ต่อรอบ ซึ่งหมายถึงอ่าน `surface` (memmap โหมด `w+`, 200 MB)
ทั้งผืน 5 ครั้งต่อรอบ = ~12 GB traffic ผ่าน page cache ของ mapping ที่ dirty อยู่
**นี่คือสาเหตุที่ทั้งเครื่องหน่วง ไม่ใช่แค่ process** — และมันไม่โผล่ในช่อง RAM
ของ process ด้วย

ตอนนี้ทำทีละแถบ (~25 MB) แต่มีกับดัก: ทุก cell ในรอบเดียวกัน **ต้องเห็น `surface`
ชุดก่อนรอบนี้เท่านั้น** ถ้าปล่อยให้แถบถัดไปอ่านค่าที่แถบก่อนหน้าเพิ่งเขียน การแพร่
จะวิ่งลงใต้เร็วกว่าขึ้นเหนือ ผลลัพธ์จึงขึ้นกับ `row_batch` โดยไม่มีอะไรฟ้อง —
ค่าทุกตัวยังดูสมเหตุสมผลหมด จึงเก็บ `above` เป็นสำเนาแถวสุดท้าย *ก่อนแก้* ของแถบ
ก่อนหน้าไว้ ส่วนแถวใต้อ่านจาก `surface` ตรง ๆ ได้เพราะยังไม่ถูกแตะในรอบนี้

`tests/test_hydrology_shape.py` มีเทสต์ยืนยันว่าผลไม่ขึ้นกับ `row_batch` (ผ่าน
mutation test แล้ว: ใส่บั๊ก Gauss-Seidel กลับไปแล้วเทสต์ fail จริง) **ถ้าเพิ่มการ
แพร่แบบแบ่งแถบที่อื่นอีก ให้เขียนเทสต์แบบเดียวกันเสมอ**
