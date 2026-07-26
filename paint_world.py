"""ขั้นที่ 2: อ่าน masks.npz แล้วเขียนบล็อกลงโลก Minecraft ชั้นเดียวที่ Y = config.Y_LEVEL

ก่อนรัน:
  1. สร้างโลกใหม่ใน Minecraft แบบ Superflat -> preset "The Void" (หรือ 1 layer)
  2. ปิดเกมให้สนิท (ห้ามเปิดโลกค้างไว้ตอนรัน)
  3. ตั้ง WORLD_PATH ใน config.py ให้ตรง

เขียนทีละ chunk ด้วย numpy slice — chunk ที่ว่างทั้ง 16x16 จะถูกข้าม
"""

import glob
import os
import struct
import sys
import time
import zlib

import amulet_nbt
import numpy as np
import amulet
from amulet.api.block import Block
from amulet.api.chunk import Chunk
from amulet.api.errors import ChunkDoesNotExist, ChunkLoadError

import config as C

CHUNK = 16
SECTOR = 4096


def repair_entities():
    """ล้าง entity chunk พิการที่ amulet ทิ้งไว้

    amulet แนบ entity layer เปล่ากับทุก chunk ที่สร้างใหม่ และเพราะ PyMCTranslate
    ไม่รู้จัก DataVersion ของ MC เวอร์ชันใหม่ มันจึง serialize ออกมาเป็น tag ที่มีแค่
    {DataVersion: 0} ขาด "Position" ที่ MC บังคับ -> เกม crash ตอนโหลด chunk ด้วย
    NoSuchElementException ใน EntityStorage.loadEntities

    ฟังก์ชันนี้เขียนไฟล์ entities region ใหม่ โดยเก็บเฉพาะ chunk ที่มี "Position"
    (entity ของจริงจึงไม่หาย) ไฟล์ไหนไม่เหลือ chunk เลยก็ลบทิ้ง MC จะสร้างใหม่ให้เอง
    """
    ent_dir = os.path.join(
        C.WORLD_PATH, "dimensions", "minecraft", "overworld", "entities"
    )
    if not os.path.isdir(ent_dir):
        ent_dir = os.path.join(C.WORLD_PATH, "entities")
        if not os.path.isdir(ent_dir):
            print("ไม่พบโฟลเดอร์ entities — ข้าม")
            return

    files = sorted(glob.glob(os.path.join(ent_dir, "*.mca")))
    print(f"\nล้าง entity chunk พิการใน {len(files)} ไฟล์...")
    kept_total = dropped_total = removed_files = 0

    for n, path in enumerate(files, 1):
        with open(path, "rb") as f:
            raw_file = f.read()
        if len(raw_file) < 2 * SECTOR:
            continue

        header, timestamps = raw_file[:SECTOR], raw_file[SECTOR : 2 * SECTOR]
        keep = {}  # index -> (payload bytes รวม length+compression, timestamp)

        for i in range(1024):
            offset = int.from_bytes(header[i * 4 : i * 4 + 3], "big")
            count = header[i * 4 + 3]
            if offset == 0 or count == 0:
                continue
            start = offset * SECTOR
            if start + 5 > len(raw_file):
                continue
            (length,) = struct.unpack(">I", raw_file[start : start + 4])
            payload = raw_file[start : start + 4 + length]
            compression = raw_file[start + 4]
            body = raw_file[start + 5 : start + 4 + length]
            try:
                data = zlib.decompress(body) if compression == 2 else body
                tag = amulet_nbt.load(data).compound
            except Exception:
                dropped_total += 1
                continue
            if "Position" in tag:
                keep[i] = (payload, timestamps[i * 4 : i * 4 + 4])
                kept_total += 1
            else:
                dropped_total += 1

        if not keep:
            os.remove(path)
            removed_files += 1
        else:
            new_header = bytearray(SECTOR)
            new_times = bytearray(SECTOR)
            body_out = bytearray()
            sector = 2  # ข้าม header + timestamp
            for i, (payload, ts) in sorted(keep.items()):
                padded = payload + b"\0" * (-len(payload) % SECTOR)
                n_sectors = len(padded) // SECTOR
                new_header[i * 4 : i * 4 + 3] = sector.to_bytes(3, "big")
                new_header[i * 4 + 3] = min(n_sectors, 255)
                new_times[i * 4 : i * 4 + 4] = ts
                body_out += padded
                sector += n_sectors
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(new_header)
                f.write(new_times)
                f.write(body_out)
            os.replace(tmp, path)

        if n % 50 == 0 or n == len(files):
            sys.stdout.write(f"\r  {n}/{len(files)} ไฟล์   ")
            sys.stdout.flush()

    print(
        f"\n  เก็บ entity chunk จริง {kept_total:,} | ทิ้งขยะ {dropped_total:,} | "
        f"ลบไฟล์เปล่า {removed_files}"
    )


def main():
    data = np.load(C.MASK_FILE)
    river, forest = data["river"], data["forest"]
    assert river.shape == (C.GRID, C.GRID), f"mask shape {river.shape} ไม่ตรงกับ GRID={C.GRID}"

    # แปลง mask -> id array เดียว (0 = ว่าง, 1 = แม่น้ำ, 2 = ป่า)
    # แกน mask คือ [row=north->south, col=west->east]
    # แกน Minecraft คือ x = west->east, z = north->south  => ต้อง transpose
    kind = np.zeros((C.GRID, C.GRID), dtype=np.uint8)
    kind[forest] = 2
    kind[river] = 1
    kind = kind.T  # ตอนนี้ index เป็น [x, z]

    print(f"เปิดโลก: {C.WORLD_PATH}")
    level = amulet.load_level(C.WORLD_PATH)

    palette = [None] * 3
    palette[1] = level.block_palette.get_add_block(Block(*C.BLOCK_RIVER))
    palette[2] = level.block_palette.get_add_block(Block(*C.BLOCK_FOREST))
    base_id = (
        level.block_palette.get_add_block(Block(*C.BLOCK_BASE))
        if C.BLOCK_BASE
        else level.block_palette.get_add_block(Block("minecraft", "air"))
    )

    y = C.Y_LEVEL
    n_chunks = C.GRID // CHUNK

    # --limit N = ทดลองเขียนแค่มุม N x N chunks ก่อน (ไว้เช็คว่าถูกต้องก่อนรันเต็ม)
    if "--limit" in sys.argv:
        n_chunks = min(n_chunks, int(sys.argv[sys.argv.index("--limit") + 1]))
        print(f"[TEST] เขียนแค่ {n_chunks}x{n_chunks} chunks แรก")

    written = skipped = 0
    t0 = time.time()

    for cx in range(n_chunks):
        xs = slice(cx * CHUNK, (cx + 1) * CHUNK)
        for cz in range(n_chunks):
            zs = slice(cz * CHUNK, (cz + 1) * CHUNK)
            tile = kind[xs, zs]  # (16, 16)

            if not tile.any() and C.BLOCK_BASE is None:
                skipped += 1
                continue

            try:
                chunk = level.get_chunk(cx, cz, C.DIMENSION)
            except (ChunkDoesNotExist, ChunkLoadError):
                chunk = Chunk(cx, cz)

            layer = np.full((CHUNK, 1, CHUNK), base_id, dtype=np.uint32)
            for k in (1, 2):
                m = tile == k
                if m.any():
                    layer[:, 0, :][m] = palette[k]

            chunk.blocks[:, y : y + 1, :] = layer
            chunk.changed = True
            level.put_chunk(chunk, C.DIMENSION)
            written += 1

        done = (cx + 1) * n_chunks
        pct = done / (n_chunks * n_chunks)
        elapsed = time.time() - t0
        eta = elapsed / pct - elapsed if pct else 0
        sys.stdout.write(
            f"\r{pct:6.1%}  เขียน {written:,} ข้าม {skipped:,}  ETA {eta/60:.1f} นาที   "
        )
        sys.stdout.flush()

    print("\nกำลังบันทึก... (ขั้นนี้ใช้เวลานาน ห้ามปิด)")
    level.save()
    level.close()
    print(f"เขียนบล็อกเสร็จ — {written:,} chunks ใน {(time.time()-t0)/60:.1f} นาที")

    repair_entities()

    print(f"\nเสร็จทั้งหมด — {(time.time()-t0)/60:.1f} นาที")
    print(f"เทเลพอร์ตไปดู:  /tp @s {C.GRID//2} {C.Y_LEVEL + 2} {C.GRID//2}")


if __name__ == "__main__":
    # python paint_world.py --repair-only  = ซ่อม entity อย่างเดียว ไม่เขียนบล็อกใหม่
    if "--repair-only" in sys.argv:
        repair_entities()
    else:
        main()
