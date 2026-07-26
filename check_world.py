"""เช็คว่าโลกเป็น superflat/void หรือ terrain ปกติ + นับ chunk ที่ generate ไว้แล้ว"""

import glob
import os

import amulet_nbt

import config as C

lvl = amulet_nbt.load(os.path.join(C.WORLD_PATH, "level.dat")).compound["Data"]
print("ชื่อโลก:", lvl.get("LevelName"))
print("Version:", lvl.get("Version", {}).get("Name"))

wg = lvl.get("WorldGenSettings", {})
dims = wg.get("dimensions", {})
ow = dims.get("minecraft:overworld", {})
gen = ow.get("generator", {})
print("generator type:", gen.get("type"))
settings = gen.get("settings")
if isinstance(settings, amulet_nbt.StringTag):
    print("settings:", settings)
elif settings is not None:
    layers = settings.get("layers")
    if layers is not None:
        print("flat layers:", [(str(l.get("block")), int(l.get("height", 1))) for l in layers])

region_dir = os.path.join(C.WORLD_PATH, "dimensions", "minecraft", "overworld", "region")
files = glob.glob(os.path.join(region_dir, "*.mca"))
total = sum(os.path.getsize(f) for f in files)
print(f"\nregion files: {len(files)}  รวม {total/1024/1024:.1f} MB")
