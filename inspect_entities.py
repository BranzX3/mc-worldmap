"""ดู entity chunk ดิบ ว่าขาด field อะไรเทียบกับที่ MC ต้องการ

ใช้: python inspect_entities.py <chunkX> <chunkZ>
"""

import os
import struct
import sys
import zlib

import amulet_nbt

import config as C

cx = int(sys.argv[1]) if len(sys.argv) > 1 else 343
cz = int(sys.argv[2]) if len(sys.argv) > 2 else 278

base = os.path.join(C.WORLD_PATH, "dimensions", "minecraft", "overworld")
for sub in ("entities", "region"):
    path = os.path.join(base, sub, f"r.{cx >> 5}.{cz >> 5}.mca")
    print(f"\n=== {sub}: {os.path.basename(path)} ===")
    if not os.path.exists(path):
        print("  ไม่มีไฟล์")
        continue
    with open(path, "rb") as f:
        header = f.read(4096)
        idx = (cx & 31) + (cz & 31) * 32
        offset = int.from_bytes(header[idx * 4 : idx * 4 + 3], "big")
        if offset == 0:
            print("  chunk นี้ไม่มีข้อมูล")
            continue
        f.seek(offset * 4096)
        length, compression = struct.unpack(">IB", f.read(5))
        raw = f.read(length - 1)
    data = zlib.decompress(raw) if compression == 2 else raw
    tag = amulet_nbt.load(data).compound
    print(f"  keys: {sorted(tag.keys())}")
    for k in ("DataVersion", "Position", "Entities", "Status"):
        if k in tag:
            v = tag[k]
            print(f"  {k}: {v if k != 'Entities' else f'{len(v)} entities'}")
