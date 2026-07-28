"""ปั้นภูมิประเทศจริงลงโลก Minecraft จาก heightmap.png

อ่าน heightmap.png -> คำนวณ y ผิวของทุกคอลัมน์ -> เขียนหินตันจาก Y_TERRAIN_MIN ขึ้นไปถึงผิว
ปล่อยเป็นหินล้วนไว้ ให้ไปทาสีผิวต่อใน Axiom ด้วย Tool Masks (Y / Angle / Surface)

ใช้:
    python build_terrain.py --limit 20     # ทดลองมุมเล็ก 20x20 chunks (320x320 บล็อก)
    python build_terrain.py                # เต็มแผนที่

ก่อนรัน: ปิดโลกในเกม (อยู่หน้า main menu ได้)
"""

import os
import sys
import time

import amulet_nbt
import numpy as np
import amulet
from amulet.api.block import Block
from amulet.api.chunk import Chunk
from amulet.api.errors import ChunkDoesNotExist, ChunkLoadError
from PIL import Image

import config as C
from paint_world import repair_entities
from pipeline_progress import load_progress, save_progress, world_session_locked

Image.MAX_IMAGE_PIXELS = None
CHUNK = 16
HERE = os.path.dirname(os.path.abspath(__file__))
# เผื่อหัวเหนือผิวดินสูงสุดของ chunk ตอนเขียนอากาศทับ
# ต้องสูงกว่าต้นไม้ที่สูงที่สุดในแพ็ก (48 บล็อก) ไม่งั้นใบไม้ส่วนบนจะค้างลอย
# เวลารันซ้ำเพื่อรีเซ็ตพื้นที่ที่เคยทาไปแล้ว
CLEAR_HEADROOM = 64

# หินฐานที่เติมให้เต็มใต้ผิว — section ที่เป็นบล็อกเดียวล้วน MC ไม่เขียน data array
# ขนาดไฟล์เลยแทบไม่โต ต่อให้ตันตั้งแต่ก้นถึงยอด
BLOCK_TERRAIN = ("minecraft", "stone")
BLOCK_BEDROCK = ("minecraft", "bedrock")  # ชั้นล่างสุด 1 บล็อก, None = ไม่ต้อง


def surface_grid():
    """คืน array [x, z] ของ y ผิวดิน (int) จาก terrain_y.npy

    ต้องอ่านไฟล์ที่ terrain_shape.py สร้างไว้ ไม่ใช่แปลง heightmap เอง — เดิม
    ไฟล์นี้กับ paint_surface.py ต่างคนต่าง np.rint() ถ้าสูตรใดสูตรหนึ่งเปลี่ยน
    (เช่นตอนเพิ่ม dither) ผิวดินที่ paint วางจะไม่ตรงกับหินที่ build ถม โดยไม่มี
    อะไรฟ้อง
    """
    path = os.path.join(HERE, "terrain_y.npy")
    if not os.path.exists(path):
        raise SystemExit(
            "ไม่พบ terrain_y.npy — รัน terrain_shape.py ก่อน"
        )
    y = np.load(path)
    if y.shape[0] != C.GRID:
        print(f"[เตือน] terrain_y {y.shape[0]} ไม่ตรงกับ GRID={C.GRID}")
    # แกนไฟล์ [row=z, col=x] -> ต้อง transpose ให้เป็น [x, z] แบบ Minecraft
    return y.astype(np.int32).T


def patch_chunk_bounds(center_x, center_z, size, grid_blocks):
    """Return exclusive chunk bounds covering exactly the requested block box."""
    bx0, bz0 = center_x - size // 2, center_z - size // 2
    bx1, bz1 = bx0 + size, bz0 + size
    n_chunks = (grid_blocks + CHUNK - 1) // CHUNK
    return (
        max(0, bx0 // CHUNK),
        min(n_chunks, (bx1 + CHUNK - 1) // CHUNK),
        max(0, bz0 // CHUNK),
        min(n_chunks, (bz1 + CHUNK - 1) // CHUNK),
    )


def main():
    if world_session_locked(C.WORLD_PATH):
        raise SystemExit(
            "world is currently open or locked; exit to the Minecraft main "
            "menu before running build_terrain.py"
        )

    surf = surface_grid()
    n = surf.shape[0]
    print(f"heightmap {n}x{n}  ผิวดิน y {surf.min()} ถึง {surf.max()}")

    print(f"เปิดโลก: {C.WORLD_PATH}")
    level = amulet.load_level(C.WORLD_PATH)

    air = level.block_palette.get_add_block(Block("minecraft", "air"))
    stone = level.block_palette.get_add_block(Block(*BLOCK_TERRAIN))
    bedrock = (
        level.block_palette.get_add_block(Block(*BLOCK_BEDROCK))
        if BLOCK_BEDROCK
        else None
    )

    y_base = getattr(C, 'Y_FILL_BOTTOM', C.Y_TERRAIN_MIN)
    n_chunks = n // CHUNK

    patch = None
    if "--patch" in sys.argv:
        i = sys.argv.index("--patch")
        pcx, pcz, psz = (int(sys.argv[i + 1]), int(sys.argv[i + 2]),
                         int(sys.argv[i + 3]))
        patch = patch_chunk_bounds(pcx, pcz, psz, n)
        print(
            f"[PATCH] chunk x {patch[0]}..{patch[1]} (exclusive)  "
            f"z {patch[2]}..{patch[3]} (exclusive)"
        )

    if "--limit" in sys.argv:
        n_chunks = min(n_chunks, int(sys.argv[sys.argv.index("--limit") + 1]))
        print(f"[TEST] เขียนแค่ {n_chunks}x{n_chunks} chunks = {n_chunks*CHUNK} บล็อก")

    # ทำทีละ region (32x32 chunks) แล้ว save+purge
    #
    # amulet เก็บทุก chunk ที่แก้ไว้ใน RAM เพื่อทำ undo history จนกว่าจะ save()
    # ถ้ากองทั้ง 390,625 chunks ไว้แล้วค่อย save ทีเดียวจะ MemoryError กลางทาง
    # และเสียงานทั้งหมดเพราะยังไม่เคยเขียนลงดิสก์เลย — วนทีละ region แทน
    # ผลพลอยได้: พังกลางทางเสียแค่ region เดียว และ resume ต่อได้
    RSIZE = 32
    n_regions = (n_chunks + RSIZE - 1) // RSIZE
    total_regions = n_regions * n_regions

    done_file = os.path.join(HERE, "build_progress.txt")
    done = set()
    if "--resume" in sys.argv and os.path.exists(done_file):
        done = load_progress(done_file)
        print(f"resume: ข้าม {len(done)} region ที่ทำไปแล้ว")
    elif os.path.exists(done_file) and "--patch" not in sys.argv:
        os.remove(done_file)

    written = 0
    t0 = time.time()

    if patch:
        regions = [(rx, rz)
                   for rx in range(patch[0] // RSIZE, (patch[1] - 1) // RSIZE + 1)
                   for rz in range(patch[2] // RSIZE, (patch[3] - 1) // RSIZE + 1)]
        total_regions = len(regions)
    else:
        regions = [(a, b) for a in range(n_regions) for b in range(n_regions)]

    for ri, (rx, rz) in enumerate(regions, 1):
        key = f"{rx},{rz}"
        if key in done:
            continue

        cx_lo = max(rx * RSIZE, patch[0]) if patch else rx * RSIZE
        cx_hi = min((rx + 1) * RSIZE, patch[1]) if patch else min((rx + 1) * RSIZE, n_chunks)
        cz_lo = max(rz * RSIZE, patch[2]) if patch else rz * RSIZE
        cz_hi = min((rz + 1) * RSIZE, patch[3]) if patch else min((rz + 1) * RSIZE, n_chunks)

        for cx in range(cx_lo, cx_hi):
            xs = slice(cx * CHUNK, (cx + 1) * CHUNK)
            for cz in range(cz_lo, cz_hi):
                tile = surf[xs, cz * CHUNK : (cz + 1) * CHUNK]  # (16, 16)
                top = int(tile.max())

                try:
                    chunk = level.get_chunk(cx, cz, C.DIMENSION)
                except (ChunkDoesNotExist, ChunkLoadError):
                    chunk = Chunk(cx, cz)

                # solid[x, y, z] = True เมื่อ y ต่ำกว่าหรือเท่ากับผิวของคอลัมน์นั้น
                ceil = min(C.Y_BUILD_CEILING, top + CLEAR_HEADROOM)
                ys = np.arange(y_base, ceil + 1, dtype=np.int32)
                solid = ys[None, :, None] <= tile[:, None, :]
                col = np.where(solid, stone, air).astype(np.uint32)
                if bedrock is not None:
                    col[:, 0, :] = bedrock

                chunk.blocks[:, y_base : ceil + 1, :] = col

                # amulet ตั้ง isLightOn=1 ทั้งที่ไม่ได้เขียนข้อมูลแสงเลย MC เลยไม่
                # ยอม relight แล้วอ่านค่าแสงที่ไม่มีเป็น 0 = มืดสนิท บังคับเป็น 0
                chunk.misc["isLightOn"] = amulet_nbt.ByteTag(0)
                chunk.misc["block_light"] = {}
                chunk.misc["sky_light"] = {}
                # heightmap เดิมค้างเป็น -64 (ค่าตอน chunk ยังว่าง) ถ้าปล่อยไว้
                # skylight จะผิดต่อให้ relight แล้ว — ล้างให้ MC สร้างใหม่เอง
                chunk.misc.pop("height_mapC", None)

                chunk.changed = True
                level.put_chunk(chunk, C.DIMENSION)
                written += 1

        level.save()
        level.purge()  # ล้าง history + chunk ที่ cache ไว้ ไม่งั้น RAM บวม

        if patch is None:
            done.add(key)
            save_progress(done_file, done)

        pct = ri / total_regions
        el = time.time() - t0
        rss = ""
        try:
            import ctypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32)] + [
                    (nm, ctypes.c_size_t)
                    for nm in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                    )
                ]

            c = PMC()
            c.cb = ctypes.sizeof(c)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb
            )
            rss = f" RAM {c.WorkingSetSize/1024/1024:,.0f}MB"
        except Exception:
            pass

        sys.stdout.write(
            f"\rregion {ri}/{total_regions} ({pct:5.1%})  {written:,} chunks  "
            f"ETA {(el/pct - el)/60:5.1f} นาที{rss}   "
        )
        sys.stdout.flush()

    print(f"\nเขียนเสร็จ {written:,} chunks ใน {(time.time()-t0)/60:.1f} นาที")
    level.close()

    if "--repair-entities" in sys.argv:
        repair_entities()
    else:
        print("ข้าม entity repair (ใช้ --repair-entities เมื่อต้องการรันโดยตั้งใจ)")

    mid = (n_chunks * CHUNK) // 2
    print(f"\nรวม {(time.time()-t0)/60:.1f} นาที")
    print(f"เทเลพอร์ตไปดู:  /tp @s {mid} {int(surf[mid, mid]) + 3} {mid}")


if __name__ == "__main__":
    main()
