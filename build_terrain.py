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
from pipeline_progress import (
    load_progress,
    load_progress_metadata,
    save_progress,
    save_progress_metadata,
    working_set_mb,
    world_session_locked,
)
import rock_shelters as RS
from hydrology_patch_io import load_hydrology_patch

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


def surface_grid(hydrology_patch=None, hydrology_root=None):
    """คืน array [x, z] ของ y ผิวดิน (int) จาก terrain_y.npy

    ต้องอ่านไฟล์ที่ terrain_shape.py สร้างไว้ ไม่ใช่แปลง heightmap เอง — เดิม
    ไฟล์นี้กับ paint_surface.py ต่างคนต่าง np.rint() ถ้าสูตรใดสูตรหนึ่งเปลี่ยน
    (เช่นตอนเพิ่ม dither) ผิวดินที่ paint วางจะไม่ตรงกับหินที่ build ถม โดยไม่มี
    อะไรฟ้อง
    """
    path = os.path.join(
        os.path.abspath(hydrology_root) if hydrology_root else HERE,
        "terrain_y.npy",
    )
    if not os.path.exists(path):
        raise SystemExit(
            "ไม่พบ terrain_y.npy — รัน terrain_shape.py ก่อน"
        )
    y = np.load(path)
    if hydrology_patch is not None:
        x0, x1, z0, z1 = map(int, hydrology_patch["bounds"])
        if not (
            0 <= x0 < x1 <= y.shape[1] and 0 <= z0 < z1 <= y.shape[0]
        ):
            raise SystemExit("hydrology patch bounds are outside terrain_y.npy")
        y[z0:z1, x0:x1] = hydrology_patch["terrain_y"]
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


def shelters_enabled(argv):
    """Experimental shelters stay opt-in until every safety gate passes."""
    return "--shelters" in argv


def main():
    if world_session_locked(C.WORLD_PATH):
        raise SystemExit(
            "world is currently open or locked; exit to the Minecraft main "
            "menu before running build_terrain.py"
        )

    # โพรงผิวยังมี safety invariant ที่ไม่ผ่าน ห้ามปนเข้า build ปกติจนกว่าจะ
    # พิสูจน์เรื่องทางเปิดและระดับพื้นครบ เปิดทดลองโดยตั้งใจด้วย --shelters
    shelters = shelters_enabled(sys.argv)
    hydro_patch = None
    hydro_root = None
    if "--hydrology-root" in sys.argv:
        i = sys.argv.index("--hydrology-root")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--hydrology-root requires a directory")
        hydro_root = os.path.abspath(sys.argv[i + 1])
        if not os.path.isfile(os.path.join(hydro_root, "terrain_y.npy")):
            raise SystemExit("hydrology root has no terrain_y.npy")
        print(f"[HYDROLOGY GLOBAL] {hydro_root}")
    if "--hydrology-patch" in sys.argv:
        i = sys.argv.index("--hydrology-patch")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--hydrology-patch requires an .npz path")
        try:
            hydro_patch = load_hydrology_patch(sys.argv[i + 1])
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        if "--patch" not in sys.argv:
            raise SystemExit("--hydrology-patch is restricted to --patch runs")
        print(f"[HYDROLOGY] overlay {os.path.abspath(sys.argv[i + 1])}")

    if hydro_patch is not None and hydro_root is not None:
        raise SystemExit("use only one hydrology input")
    water_only = "--water-only" in sys.argv
    if water_only and hydro_root is None:
        raise SystemExit("--water-only requires --hydrology-root")
    affected_chunks = (
        np.load(os.path.join(hydro_root, "affected_chunks.npy"), mmap_mode="r")
        if water_only else None
    )

    surf = surface_grid(
        hydrology_patch=hydro_patch, hydrology_root=hydro_root
    )
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
    # ที่ WORLD_HEIGHT=784 หนึ่ง chunk = 49 sections x 16^3 x uint32 = ~800 KB
    # RSIZE 32 = 1024 chunks = ~820 MB ค้างจนถึง purge() ปลาย region
    # 16 = 256 chunks = ~205 MB แลกกับ save() ถี่ขึ้นสี่เท่า
    #
    # key ของ build_progress.txt คือ index ของ region ไม่ใช่พิกัดบล็อก การเปลี่ยน
    # ค่านี้จึงทำให้ checkpoint เดิมหมายถึงคนละพื้นที่ — ดูการตรวจ region_chunks
    # ข้างล่าง ห้ามเปลี่ยนโดยไม่ผ่านการตรวจนั้น
    RSIZE = 16
    n_regions = (n_chunks + RSIZE - 1) // RSIZE
    total_regions = n_regions * n_regions

    done_file = os.path.join(HERE, "build_progress.txt")
    meta_file = os.path.join(HERE, "build_progress.meta.json")
    done = set()
    if "--resume" in sys.argv and os.path.exists(done_file):
        recorded = (load_progress_metadata(meta_file) or {}).get("region_chunks")
        if recorded != RSIZE:
            raise SystemExit(
                "--resume refused: build_progress.txt ถูกเขียนด้วย region ขนาด "
                f"{recorded or '(ไม่ทราบ — ไม่มี metadata)'} chunks "
                f"แต่ตอนนี้ RSIZE={RSIZE}\n"
                "key ของ checkpoint คือ index ของ region ไม่ใช่พิกัดบล็อก "
                "การ resume ข้ามขนาดจะข้ามพื้นที่ผิดโดยไม่มีอะไรฟ้อง\n"
                f"เลือกอย่างใดอย่างหนึ่ง: ลบ {os.path.basename(done_file)} "
                "แล้ว build ใหม่ทั้งหมด "
                f"หรือตั้ง RSIZE กลับเป็น {recorded} เพื่อใช้ checkpoint เดิมต่อ"
            )
        done = load_progress(done_file)
        print(f"resume: ข้าม {len(done)} region ที่ทำไปแล้ว")
    elif os.path.exists(done_file) and "--patch" not in sys.argv:
        os.remove(done_file)
    if "--patch" not in sys.argv:
        save_progress_metadata(
            meta_file, {"schema": 1, "region_chunks": RSIZE}
        )

    written = 0
    t0 = time.time()

    if patch:
        regions = [(rx, rz)
                   for rx in range(patch[0] // RSIZE, (patch[1] - 1) // RSIZE + 1)
                   for rz in range(patch[2] // RSIZE, (patch[3] - 1) // RSIZE + 1)]
        total_regions = len(regions)
    else:
        regions = [(a, b) for a in range(n_regions) for b in range(n_regions)]
    if affected_chunks is not None:
        regions = [
            (rx, rz) for rx, rz in regions
            if np.asarray(affected_chunks[
                rz * RSIZE:min((rz + 1) * RSIZE, affected_chunks.shape[0]),
                rx * RSIZE:min((rx + 1) * RSIZE, affected_chunks.shape[1]),
            ]).any()
        ]
        total_regions = len(regions)
        print(
            f"[WATER ONLY] {int(np.asarray(affected_chunks).sum()):,} chunks "
            f"in {total_regions:,} regions"
        )

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
                if (
                    affected_chunks is not None
                    and not bool(affected_chunks[cz, cx])
                ):
                    continue
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
                # ---- โพรงผิว: เพิงผาและซอกหินบนหน้าผาที่เปิดออกอยู่แล้ว ----
                #
                # โลกจาก heightmap เป็น 2.5D โดยนิยาม ทุกคอลัมน์ตันตั้งแต่พื้น
                # ถึงผิว ผาสูง 36 บล็อกทุกลูกจึงเป็นผนังเรียบไม่มีที่ว่างเลย
                # `rock_shelters` เจาะเฉพาะหน้าผาจริง เว้นเพดานใต้ผิว และวางพื้น
                # โพรงสูงกว่ายอดคอลัมน์ที่เปิดออก — สามข้อนี้ทำให้มันแตะผิวดินที่
                # painter ทาไว้ไม่ได้ และน้ำข้างล่างไหลเข้าไม่ได้
                if shelters:
                    halo = RS.HALO
                    hx0, hx1 = cx * CHUNK - halo, (cx + 1) * CHUNK + halo
                    hz0, hz1 = cz * CHUNK - halo, (cz + 1) * CHUNK + halo
                    if (
                        hx0 >= 0 and hz0 >= 0
                        and hx1 <= surf.shape[0] and hz1 <= surf.shape[1]
                    ):
                        hollow = RS.shelter_volume(
                            surf[hx0:hx1, hz0:hz1], ys,
                            cx * CHUNK, cz * CHUNK,
                        )
                        solid &= ~hollow
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
        used = working_set_mb()
        rss = f" RAM {used:,.0f}MB" if used else ""

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

    # ต้องชี้ไปที่สิ่งที่เพิ่งเขียนจริง ไม่ใช่กลางแผนที่เสมอ — เดิมรัน --patch
    # ตรงไหนก็พิมพ์พิกัดกลางแผนที่ ซึ่งมักเป็น void ทำให้ดูเหมือนงานหายไปทั้งก้อน
    if patch:
        tx = min(surf.shape[0] - 1, max(0, (patch[0] + patch[1]) * CHUNK // 2))
        tz = min(surf.shape[1] - 1, max(0, (patch[2] + patch[3]) * CHUNK // 2))
    else:
        tx = tz = (n_chunks * CHUNK) // 2
    print(f"\nรวม {(time.time()-t0)/60:.1f} นาที")
    print(f"เทเลพอร์ตไปดู:  /tp @s {tx} {int(surf[tx, tz]) + 4} {tz}")


if __name__ == "__main__":
    main()
