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
python make_world_datapack.py            # เขียน datapack ลงโลก
python make_world_datapack.py --enable   # ใส่ file/tall_overworld ลง level.dat
python make_world_datapack.py --verify   # ต้องผ่านก่อนทำอะไรต่อ
python build_terrain.py [--patch x z size]
python paint_surface.py  [--patch x z size]
```

`terrain_shape.py` ต้องรัน **หลัง** `make_water.py` เพราะใช้ `water_mask` กัน
ไม่ให้ผิวทะเลสาบถูก dither จนเป็นตุ่ม

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

**ก่อนเพิ่มการคำนวณอะไรก็ตาม ให้เช็คว่ามีที่อื่นคำนวณค่าเดียวกันอยู่แล้วหรือไม่**

## ไฟล์ที่สร้างขึ้นระหว่างทาง

| ไฟล์ | ขนาด | สร้างโดย |
|---|---|---|
| `heightmap.png` | 200 MB | `make_heightmap.py` (ต้องมี `dem.tif` ซึ่ง**ไม่อยู่ในเครื่องแล้ว**) |
| `landcover.npz` | — | `fetch_landcover.py` |
| `water_mask.npy` / `water_depth.npy` | 100 MB | `make_water.py` |
| `terrain_y.npy` (int16) | 191 MB | `terrain_shape.py` |
| `terrain_sub.npy` (int8) | 95 MB | `terrain_shape.py` |

`heightmap.png` เก็บความสูงแบบ **normalize (0..65535 ต่อช่วง elev จริง)** จึงไม่
ขึ้นกับสเกลแนวตั้ง เปลี่ยน `Y_TERRAIN_*` ใน config ได้โดยไม่ต้องสร้าง DEM ใหม่ —
ซึ่งสำคัญมากเพราะ `dem.tif` หายไปแล้ว

## เครื่องมือตรวจ

```bash
python report_metrics.py                 # ตัวเลขคุณภาพทั้งแผนที่ ไม่เปิดโลก
python report_metrics.py --patch x z size
python render_preview.py 1200 <x> <z> <r>   # top-down
python render_view.py --at <x> <z> --panorama  # ระดับสายตา (near-field ยังพัง)
python -m unittest discover -s tests
```
