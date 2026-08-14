# Pipeline — ลำดับการรันและกฎที่ห้ามละเมิด

โปรเจกต์นี้สร้างแผนที่ Salzkammergut (ออสเตรีย) 40x40 km ลงโลก Minecraft
สำหรับเซิร์ฟเวอร์ MMORPG

## บริบทที่เปลี่ยนการตัดสินใจหลายอย่าง

โลกนี้เป็น **MMORPG ที่บล็อกจะไม่ถูก update จากผู้เล่นหรือสภาพอากาศ** และสัตว์/
ระบบนิเวศจะถูกออกแบบมากับระบบ lifeskill ของเกม ผลคือ

| เรื่อง | ไม่ต้องกังวล เพราะ |
|---|---|
| `temperature` ของ biome | ไม่มีสภาพอากาศ หิมะที่ทาคือสถานะสุดท้าย |
| spawner ของ biome | MMO จัดการสัตว์เอง — custom biome จึงตั้ง spawners ว่างทุกหมวด |
| ทราย/กรวดร่วงเมื่อ block update | ไม่มี block update |

## ลำดับการรัน (ห้ามสลับ)

```bash
python make_water.py            # ~1 นาที   -> water_mask.npy, water_depth.npy
python terrain_shape.py         # ~3 นาที   -> terrain_y.npy, terrain_sub.npy
python make_water_levels.py     # ระดับน้ำ global + ทางออกลำธาร
python make_world_datapack.py            # เขียน datapack ลงโลก
python make_world_datapack.py --enable   # ใส่ file/tall_overworld ลง level.dat
python make_world_datapack.py --verify   # ต้องผ่านก่อนทำอะไรต่อ
python build_terrain.py [--patch x z size]
python paint_surface.py  [--patch x z size]
```

`terrain_shape.py` ต้องรัน **หลัง** `make_water.py` เพราะใช้ `water_mask` กัน
ไม่ให้ผิวทะเลสาบถูก dither จนเป็นตุ่ม

`make_water_levels.py` ต้องรัน **หลัง** `terrain_shape.py` เพราะใช้
`terrain_y.npy` ซึ่งเป็นผิวจริงชุดเดียวกับที่ `build_terrain.py` เขียนลงโลก

## กฎเหล็ก: แหล่งความจริงเดียว

บั๊กที่เกิดซ้ำที่สุดในโปรเจกต์นี้คือ **สองไฟล์คำนวณสิ่งเดียวกันคนละสูตร** เกิดมา
แล้ว 4 ครั้ง ทุกครั้งไม่มีอะไรฟ้อง เห็นก็ต่อเมื่อเปิดดูในเกม

| ค่า | แหล่งเดียวที่ถูกต้อง | เคยพังที่ |
|---|---|---|
| ระดับผิวดิน | `terrain_y.npy` | `build_terrain` กับ `paint_surface` ต่างคน `np.rint()` เอง |
| เศษความสูง | `terrain_sub.npy` | เดียวกัน |
| ชนิดไม้ | `ecology.tree_species()` | `surface.classify()` คืน `leaf` ส่วน paint เลือกเอง -> พรีวิวโกหก |
| สเกลแนวตั้ง | `surface.load_meta()` (คำนวณสดจาก config) | `heightmap_meta.json` เก็บค่าเก่าค้างไว้ |
| ภูมิประเทศที่ metric วัด | `terrain_y.npy` | `report_metrics.py` คำนวณ `rint()` เอง = วัดของที่ไม่มีอยู่ |
| ชื่อไฟล์ product ของน้ำ | `hydrology_patch_io.water_product_path()` | mapping อยู่ใน `paint_surface` ที่เดียว `report_metrics`/`render_*` จึงอ่านชุดเดิมต่อ ทั้งที่โลกเขียนจาก `hydrology_global/` |
| เศษความสูงที่ preview/metric ใช้ | `terrain_sub.npy` | `render_preview` กับ `report_metrics` แปลงจาก `heightmap.png` เอง |

**ก่อนเพิ่มการคำนวณอะไรก็ตาม ให้เช็คว่ามีที่อื่นคำนวณค่าเดียวกันอยู่แล้วหรือไม่**

## ไฟล์ที่สร้างขึ้นระหว่างทาง

| ไฟล์ | ขนาด | สร้างโดย |
|---|---|---|
| `heightmap.png` | 200 MB | `make_heightmap.py` (ต้องมี `dem.tif` ซึ่ง**ไม่อยู่ในเครื่องแล้ว**) |
| `landcover.npz` | — | `fetch_landcover.py` |
| `water_mask.npy` / `water_depth.npy` | 100 MB | `make_water.py` |
| `terrain_y.npy` (int16) | 191 MB | `terrain_shape.py` |
| `terrain_sub.npy` (int8) | 95 MB | `terrain_shape.py` |
| `water_surface_y.npy` (int16) | 191 MB | `make_water_levels.py` |
| `water_lake_mask.npy` / `water_outlet_mask.npy` | 95 MB ต่อไฟล์ | `make_water_levels.py` |

`heightmap.png` เก็บความสูงแบบ **normalize (0..65535 ต่อช่วง elev จริง)** จึงไม่
ขึ้นกับสเกลแนวตั้ง เปลี่ยน `Y_TERRAIN_*` ใน config ได้โดยไม่ต้องสร้าง DEM ใหม่ —
ซึ่งสำคัญมากเพราะ `dem.tif` หายไปแล้ว

## เครื่องมือตรวจ

**ทุกตัวต้องส่ง `--hydrology-root` ให้ตรงกับที่ `build_terrain`/`paint_surface` ใช้**
ไม่งั้นจะวัดและวาด product คนละชุดกับที่อยู่ในโลก ซึ่งเคยเกิดมาแล้วโดยไม่มีอะไรฟ้อง
`report_metrics` พิมพ์บรรทัด "product ที่วัด" ไว้ตอนต้นรายงานเพื่อให้เห็นทันที

```bash
HR="--hydrology-root hydrology_global"
python report_metrics.py $HR              # ตัวเลขคุณภาพทั้งแผนที่ ไม่เปิดโลก
python report_metrics.py --patch x z size $HR
python render_preview.py 1200 <x> <z> <r> $HR   # top-down
python render_view.py --at <x> <z> --panorama $HR  # ระดับสายตา (near-field ยังพัง)
python -m unittest discover -s tests
```

ถ้าไม่ใส่ `--hydrology-root` ทุกตัวจะอ่าน product ชุดเดิมจาก `make_water.py` +
`make_water_levels.py` ซึ่งยังใช้ได้อยู่แต่ **ไม่ใช่ชุดที่เขียนลงโลกแล้ว**

### golden patches — ตัวที่ควรรันก่อน commit งานน้ำ/ภูมิประเทศ

```bash
python golden_patches.py selftest             # ตรวจตัวเครื่องมือก่อนเชื่อผลมัน
python golden_patches.py run --tag before     # ก่อนแก้
# ...แก้โค้ด...
python golden_patches.py run --tag after --force
python golden_patches.py compare before after # exit 1 ถ้ามีตัวไหนแย่ลง
```

9 พื้นที่อ้างอิง (แต่ละจุดมาจากบั๊กจริงหรือจากการคัดด้วย `suggest`) รันครบ ~5 นาที

**เกณฑ์ผ่านมาจากตัวเลข ไม่ใช่จากภาพ** — ภาพ 3D ที่นี่มาจาก `render_view.py`
ซึ่งเป็น renderer แทน (เรือนยอดเป็นผิวทึบ, palette พรีวิว, near-field ยังพัง)
"ภาพดูโอเค" จึงไม่ได้แปลว่าในเกมโอเค  ตัวเลขทุกตัวใน `metrics.json` จึงนิยาม
เป็นสิ่งที่ผู้เล่นทำได้จริงเป็นบล็อก:

| ตัวเลข | ผู้เล่นเจออะไร |
|---|---|
| `canyon_depth_p50/p90`, `canyon_share` | ยืนในลำน้ำแล้วผนัง**สองฝั่ง**สูงกว่าหัวเท่าไร (ฝั่งเดียวไม่นับ เพราะเดินออกได้) |
| `bank_unwalkable_share`, `bank_climb_max` | เดินเลียบฝั่งแล้วต้องปีนเกิน 1 บล็อก (ก้าวของผู้เล่น) กี่จุด |
| `water_column_max` | ความลึกของ**ลำน้ำ** (ทะเลสาบไม่นับ) — ลึกมาก = ตกลงไปแล้วปีนไม่ขึ้น |
| `uncovered_drop` | ขั้นผิวน้ำ >=3 ที่ไม่มีม่านปิด = น้ำขาดเป็นช่อง |
| `dry_bank_below_water` | ตลิ่งแห้งต่ำกว่าผิวน้ำ = ขอบน้ำลอย |

### ตรวจ product ของ --global

```bash
python audit_global.py hydrology_global_w --windows 40
```

อ่าน product ที่ `--global` สร้างไว้ตรง ๆ (คนละเส้นทางกับ `--patch`) แล้วสุ่ม
หน้าต่างทั่วแผนที่ โดย **ครึ่งหนึ่งจงใจให้คร่อมรอยต่อ tile** เพราะนั่นคือที่ที่
`--global` เพี้ยนได้เอง  คืน exit 1 ถ้ามี invariant ตัวไหนไม่เป็นศูนย์

`section_*.png` (หน้าตัดลำน้ำที่จุดแย่ที่สุด) คือหลักฐานที่เชื่อได้ตรง ๆ เพราะ
เป็นความสูงบล็อกต่อบล็อกจาก product เดียวกับที่ `build_terrain` เขียนลงโลก
ส่วน `--eye` เป็นของประกอบ ห้ามใช้ตัดสิน — และตัวตัดสินสุดท้ายยังเป็นการเดินดู
ในเกมเสมอ

กติกาที่ทำให้ผลเชื่อได้ (ทุกข้อมีเทสต์คุม):

- **ตัดขอบกรอบทิ้ง** `CORE_MARGIN = 24` — ขอบ patch ทั้งไม่มี halo พอและตัววัด
  ที่มองรอบตัวก็มองเลยขอบไม่ได้ ถ้านับด้วย ตัวเลขจะเด้งตามขนาดกรอบ
- **cache มีลายนิ้วมือ** ของ `hydrology_shape.py` / `config.py` / `surface.py`
  + mtime ของ input แก้โค้ดแล้วรันใหม่จะ shape ใหม่เองโดยไม่ต้องใส่ `--force`
  (ถ้าไม่มีตัวนี้ `compare` จะบอกว่า "ไม่มีอะไรแย่ลง" ทั้งที่วัดของเก่าอยู่)
- **`run.json`** เก็บ git rev + ลายนิ้วมือของแต่ละจุด `compare` จะเตือนเองถ้า
  สองรอบมาจาก input ชุดเดียวกันเป๊ะ (ผลเท่ากันจึงไม่ได้แปลว่าแก้แล้วไม่พัง)
- **`--only` ไม่ลบผลของจุดอื่น** ทั้ง `metrics.json` และ `sheet.png` ประกอบจาก
  ของที่มีอยู่จริงในโฟลเดอร์
- `compare` **ดัง**เมื่อมีจุดหรือตัวเลขที่มีข้างเดียว (patch ล้ม/metric ถูกลบ)
  ไม่ข้ามเงียบ ๆ  `run` คืน exit 0 เสมอ ตัวที่เป็นประตูคือ `compare`
