"""สแกนไฟล์ entities region ทั้งไฟล์ ดูว่ามี chunk อยู่กี่ตัวและ tag หน้าตาเป็นยังไง

ใช้: python scan_entities.py [regionX] [regionZ]
"""

import os
import struct
import sys
import zlib

import amulet_nbt

import config as C

rx = int(sys.argv[1]) if len(sys.argv) > 1 else 10
rz = int(sys.argv[2]) if len(sys.argv) > 2 else 8

path = os.path.join(
    C.WORLD_PATH, "dimensions", "minecraft", "overworld", "entities", f"r.{rx}.{rz}.mca"
)
print(f"{path}\nsize = {os.path.getsize(path):,} bytes")

with open(path, "rb") as f:
    header = f.read(4096)
    present = [
        i for i in range(1024) if int.from_bytes(header[i * 4 : i * 4 + 3], "big") != 0
    ]
    print(f"chunk ที่มีข้อมูล: {len(present)} / 1024")

    for i in present[:3]:
        cx, cz = rx * 32 + (i % 32), rz * 32 + (i // 32)
        offset = int.from_bytes(header[i * 4 : i * 4 + 3], "big")
        f.seek(offset * 4096)
        length, compression = struct.unpack(">IB", f.read(5))
        raw = f.read(length - 1)
        data = zlib.decompress(raw) if compression == 2 else raw
        tag = amulet_nbt.load(data).compound
        print(f"\n  chunk ({cx},{cz})  keys = {sorted(tag.keys())}")
        for k in ("DataVersion", "Position", "Entities"):
            if k in tag:
                v = tag[k]
                print(f"    {k} = {len(v) if k == 'Entities' else v}")
            else:
                print(f"    {k} = *** ขาดหาย ***")
