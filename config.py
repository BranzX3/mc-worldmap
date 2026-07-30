"""ตั้งค่ากลางสำหรับทั้ง fetch_map.py และ paint_world.py"""

# จุดกึ่งกลางพื้นที่จริง (ละติจูด, ลองจิจูด)
# 47°35'21.77"N 13°39'47.90"E — Salzkammergut, ออสเตรีย
CENTER_LAT = 47.589381
CENTER_LON = 13.663306

# ขนาดพื้นที่จริง (เมตร) -> 40km x 40km
AREA_SIZE_M = 40_000

# 1 block = กี่เมตร  ->  40000/4 = 10000 x 10000 blocks
METERS_PER_BLOCK = 4
GRID = AREA_SIZE_M // METERS_PER_BLOCK

# ---- แนวตั้ง ----
# แนวตั้งถูกตั้งให้ "เมตรต่อบล็อก" เท่ากับแนวนอน = ไม่มีการบิดเบือนสัดส่วน
# ช่วงความสูงจริงในพื้นที่นี้คือ 437-2993 m (2557 m) ที่ 4 m/block ต้องใช้ 640
# บล็อก ซึ่งเกินเพดานวานิลลา (384) จึงต้องใช้ datapack ขยายความสูงของ dimension
# (ฟีเจอร์วานิลลาตั้งแต่ 1.17 ไม่ต้องมอด) — สร้างด้วย make_world_datapack.py
#
# ทำไมต้องเท่ากัน: ความชันในเกม = ความชันจริง x (m/block แนวนอน / m/block แนวตั้ง)
# ที่ 7.52 m/block เดิม อัตรานี้เป็น 0.53 ผาจริง 45 องศาจึงกลายเป็นทางลาด
# 0.53 บล็อกต่อก้าว = เดินขึ้นได้สบาย หน้าผาหินปูนทั้งเทือกหายไปหมด
# ที่ 4 m/block อัตราเป็น 1.00 ผา 45 องศา = 1 บล็อกต่อก้าว ผา 60 องศา = 1.7
Y_TERRAIN_MIN = 0
Y_TERRAIN_MAX = 640

# ---- ขอบเขต dimension (ต้องตรงกับ datapack) ----
# height ต้องเป็นพหุคูณของ 16 และ min_y + height <= 2032
# 784 = 49 sections ราว 2 เท่าของวานิลลา (24) เหลือหัว 79 บล็อกเหนือยอดเขา
WORLD_Y_MIN = -64
WORLD_HEIGHT = 784
WORLD_Y_MAX = WORLD_Y_MIN + WORLD_HEIGHT - 1        # 719 (รวมชั้นนี้)

# ชั้นบนสุดที่สคริปต์ยอมเขียน — เว้นชั้นสุดท้ายไว้กันการเขียนหลุดขอบ
Y_BUILD_CEILING = WORLD_Y_MAX - 1                   # 718

# ระดับที่เริ่มถมหิน/วาง bedrock — ต้องแยกจาก Y_TERRAIN_MIN
# ใต้ y=0 (พื้นหุบเขาต่ำสุด) เหลือ 64 บล็อกไว้เป็นก้นทะเลสาบและที่ว่างสำหรับถ้ำ
Y_FILL_BOTTOM = WORLD_Y_MIN

# ส่วนลึกเป็น seed กำหนดระดับผิวน้ำ แล้วส่งระดับนั้นผ่าน shelf น้ำตื้น
# จนถึงขอบทะเลสาบ พื้นดินริมฝั่งไม่ถูกยก และลำธารตื้นที่ไม่มี core ไม่เปลี่ยน
LAKE_LEVEL_OFFSET = 1
LAKE_MIN_DEPTH_FOR_OFFSET = 3
LAKE_SHORE_SEARCH_BLOCKS = 12
LAKE_LEVEL_FLAT_SHELF_BLOCKS = 4
LAKE_LEVEL_RELAX_PASSES = 4

# bathymetry: ความลึกนับจากผิวน้ำถึงบล็อกก้นทะเลสาบ
# ที่ 4 m/block ความลึกในเกมเท่ากับความลึกจริงหารสี่ — Hallstätter See ลึกจริง
# 125 m = 31 บล็อก ตัวเลขนี้จึงเป็นค่าจริง ไม่ใช่ค่าที่จูนให้พอดีเพดานอีกแล้ว
# (ที่สเกลเดิม 30 บล็อก x 7.52 = 226 m ซึ่งลึกเกินจริงเกือบสองเท่า)
WATER_MAX_DEPTH_BLOCKS = 31
WATER_SHELF_BLOCKS = 26.0

# ไฟล์ DEM ที่คุณดาวน์โหลดมาเอง (GeoTIFF, CRS อะไรก็ได้ สคริปต์ reproject ให้)
DEM_FILE = "dem.tif"

# ความกว้างขั้นต่ำของแม่น้ำที่เป็นเส้น (waterway=river/stream) หน่วยเมตร
# OSM เก็บแม่น้ำเล็กเป็น LineString ไม่มีความกว้าง ต้อง buffer เอง
RIVER_WIDTH_M = {
    "river": 12,
    "canal": 8,
    "stream": 4,
    "ditch": 2,
    "drain": 2,
}
DEFAULT_RIVER_WIDTH_M = 4

# ---- บรรยากาศระดับ dimension (ใช้โดย make_world_datapack.py) ----
# ค่าพวกนี้อยู่บน dimension_type ไม่ใช่ biome จึงเป็นค่าเดียวทั้งโลก
#
# เมฆ: วานิลลาตั้ง 192.33 สำหรับโลกสูง 384 (ราว 60% ของความสูงโลก) ภูมิประเทศ
# ของเราสูงถึง y=640 ถ้าไม่ยก เมฆจะลอยตัดกลางเทือกเขา
# ลองลดลงมาต่ำกว่ายอดเขา (เช่น 430) ถ้าอยากได้ภาพยอดเขาโผล่พ้นทะเลเมฆแบบแอลป์
# แต่เมฆจะทะลุเนื้อภูเขาในบางมุม
CLOUD_HEIGHT = 680.0

# หมอกและสีฟ้าระดับ dimension — None = ใช้ค่าวานิลลา
# วานิลลา overworld: fog #c0d8ff, sky #78a7ff
FOG_COLOR = None
SKY_COLOR = None

# ---- ฝั่ง Minecraft ----
WORLD_PATH = r"C:\Users\User\AppData\Roaming\ModrinthApp\profiles\Fabulously Optimized\saves\New World"
DIMENSION = "minecraft:overworld"
Y_LEVEL = 60          # วางชั้นเดียวที่ความสูงนี้

# บล็อกที่ใช้ (ใช้ concrete แทน water/leaves เพราะสีคมและไม่มี physics)
BLOCK_RIVER = ("minecraft", "blue_concrete")
BLOCK_FOREST = ("minecraft", "green_concrete")
BLOCK_BASE = ("minecraft", "light_gray_concrete")
                       # None = ไม่วางอะไรเลยตรงที่ไม่ใช่แม่น้ำ/ป่า (จะเป็นรูโหว่ void)
                       # ใส่บล็อก = เติมเต็มทั้งผืน 10000x10000 เดินได้ต่อเนื่อง

# ไฟล์ mask ที่ขั้นตอนที่ 1 สร้างไว้ (อิงโฟลเดอร์สคริปต์ ไม่ใช่ cwd)
import os as _os

_HERE = _os.path.dirname(_os.path.abspath(__file__))
MASK_FILE = _os.path.join(_HERE, "masks.npz")
PREVIEW_FILE = _os.path.join(_HERE, "preview.png")

# ลำดับความสำคัญเมื่อทับกัน: แม่น้ำทับป่า
RIVER_OVER_FOREST = True
