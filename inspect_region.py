"""ตรวจว่าบล็อกที่เขียนลงไฟล์ region จริงๆ คืออะไร (อ่าน NBT ดิบ ไม่ผ่าน translation)

ใช้: python inspect_region.py [chunkX] [chunkZ]     (default 0 0)
"""

import os
import struct
import sys
import zlib

import amulet_nbt

import config as C

CANDIDATES = [
    os.path.join(C.WORLD_PATH, "dimensions", "minecraft", "overworld", "region"),
    os.path.join(C.WORLD_PATH, "region"),
]
region_dir = next((p for p in CANDIDATES if os.path.isdir(p)), None)
if region_dir is None:
    raise SystemExit("ไม่พบโฟลเดอร์ region")

cx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
cz = int(sys.argv[2]) if len(sys.argv) > 2 else 0
rx, rz = cx >> 5, cz >> 5
path = os.path.join(region_dir, f"r.{rx}.{rz}.mca")
print(f"region: {path}")
if not os.path.exists(path):
    raise SystemExit("ไม่พบไฟล์ region นี้")

with open(path, "rb") as f:
    header = f.read(4096)
    idx = (cx & 31) + (cz & 31) * 32
    entry = header[idx * 4 : idx * 4 + 4]
    offset = int.from_bytes(entry[:3], "big")
    if offset == 0:
        raise SystemExit(f"chunk ({cx},{cz}) ยังไม่มีข้อมูลในไฟล์")
    f.seek(offset * 4096)
    length, compression = struct.unpack(">IB", f.read(5))
    raw = f.read(length - 1)

data = zlib.decompress(raw) if compression == 2 else raw
tag = amulet_nbt.load(data).compound

print(f"chunk ({cx},{cz})  DataVersion={tag.get('DataVersion')}  status={tag.get('Status')}")

sections = tag.get("sections") or tag.get("Sections")
for sec in sections:
    y = int(sec["Y"])
    bs = sec.get("block_states")
    if bs is None:
        continue
    palette = [str(b["Name"]) for b in bs["palette"]]
    if len(palette) > 1 or (palette and palette[0] != "minecraft:air"):
        print(f"  section Y={y}  (blocks y={y*16}..{y*16+15})  palette={palette}")
