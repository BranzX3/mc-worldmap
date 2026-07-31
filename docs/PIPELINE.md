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
