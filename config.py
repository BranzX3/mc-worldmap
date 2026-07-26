"""ตั้งค่ากลางสำหรับทั้ง fetch_map.py และ paint_world.py"""

# จุดกึ่งกลางพื้นที่จริง (ละติจูด, ลองจิจูด)
# 47°35'21.77"N 13°39'47.90"E — Salzkammergut, ออสเตรีย
CENTER_LAT = 47.589381
CENTER_LON = 13.663306

# ขนาดพื้นที่จริง (เมตร) -> 40km x 40km
AREA_SIZE_M = 40_000

# 1 block = กี่เมตร  ->  20000/4 = 5000 x 5000 blocks
# เลือก 4 เพราะแนวตั้งถูกล็อกที่ ~7.4 m/block โดยเพดานวานิลลา
# ค่านี้ทำให้ความเพี้ยนของสัดส่วนเหลือ 1.85 เท่า (ที่ 1 m/block จะเพี้ยน 7.4 เท่า)
METERS_PER_BLOCK = 4
GRID = AREA_SIZE_M // METERS_PER_BLOCK

# ---- แนวตั้ง (ใช้โดย make_heightmap.py) ----
# ช่วง y ที่ความสูงจริงถูกแมปลงไป (ช่วงกว้าง 340 บล็อกเท่าเดิม = ความละเอียด
# แนวตั้งและความเพี้ยนเท่าเดิม แค่ยกทั้งก้อนขึ้น 20 บล็อก)
#
# ยกขึ้นเพื่อให้มีที่ว่างใต้ผิวทะเลสาบพอขุดแอ่งลึกจริง 125 m (17 บล็อก)
# ที่ -60 ผิวทะเลสาบอยู่ y=-51 ก้นต้องไปถึง -68 ซึ่งทะลุพื้นโลก
Y_TERRAIN_MIN = -40
Y_TERRAIN_MAX = 300

# ระดับที่เริ่มถมหิน/วาง bedrock — ต้องแยกจาก Y_TERRAIN_MIN
# ถ้าใช้ค่าเดียวกัน การยกช่วงแมปขึ้นจะยกพื้นหินขึ้นตาม ที่ว่างใต้ทะเลสาบเท่าเดิม
Y_FILL_BOTTOM = -64

# ส่วนลึกเป็น seed กำหนดระดับผิวน้ำ แล้วส่งระดับนั้นผ่าน shelf น้ำตื้น
# จนถึงขอบทะเลสาบ พื้นดินริมฝั่งไม่ถูกยก และลำธารตื้นที่ไม่มี core ไม่เปลี่ยน
LAKE_LEVEL_OFFSET = 1
LAKE_MIN_DEPTH_FOR_OFFSET = 3
LAKE_SHORE_SEARCH_BLOCKS = 12
LAKE_LEVEL_FLAT_SHELF_BLOCKS = 4
LAKE_LEVEL_RELAX_PASSES = 4

# bathymetry: ความลึกนับจากผิวน้ำถึงบล็อกก้นทะเลสาบ
# ผิวน้ำ Hallstätter See อยู่ราว Y=-30 และ bedrock อยู่ Y=-64 จึงกำหนด 30
# เพื่อให้ก้นลึกสุดอยู่ราว Y=-60 และยังเหลือชั้นหินก่อนถึง bedrock
WATER_MAX_DEPTH_BLOCKS = 30
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

# ---- ฝั่ง Minecraft ----
WORLD_PATH = r"C:\Users\User\AppData\Roaming\ModrinthApp\profiles\Fabulously Optimized\saves\New World (2)"
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
