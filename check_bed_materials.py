"""สุ่มอ่านบล็อกก้นน้ำ **จากโลกจริง** แยกตามชนิดน้ำและตามแรงน้ำ

ใช้ตรวจว่าวัสดุก้นน้ำตรงกับชนิดของน้ำหรือไม่ — เคยพบว่าแม่น้ำกว้างถูกจำแนก
เป็นทะเลสาบ แล้วได้ palette ก้นทะเลสาบซึ่งให้ cobblestone เกือบครึ่ง ทั้งที่
ก้นแม่น้ำควรเป็นกรวด/ดิน/มอส

ตั้งแต่มี `flow_index` ตัวนี้ยังเป็นด่านที่ตอบว่า "กฎที่เขียนไว้ในโค้ดไปถึงบล็อก
จริงหรือเปล่า" — ก้นแก่งต้องเป็นหิน/กรวด ไม่ใช่ดินกับพอดโซล และต้องไม่มีพืชน้ำ
ในกระแสแรง  อ่านจากไฟล์ region ที่เขียนแล้วเท่านั้น ไม่เชื่อ array ในหน่วยความจำ

ใช้:
    python check_bed_materials.py --at 7296 4480 --radius 120
    python check_bed_materials.py --at 5792 5384 --radius 120 --root hydrology_global2
"""

import os
import sys
from collections import Counter

import numpy as np

import config as C
import water_ecology as WE
from pipeline_progress import use_utf8_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
UNRESOLVED = np.iinfo(np.int16).min


def sample_bed(root, cx, cz, radius, limit=700, seed=3):
    import amulet

    def load(name):
        return np.load(os.path.join(root, name), mmap_mode="r")

    window = (slice(cz - radius, cz + radius), slice(cx - radius, cx + radius))
    way = np.asarray(load("waterway_mask.npy")[window])
    still = np.asarray(load("standing_water_mask.npy")[window])
    surface = np.asarray(load("surface_y.npy")[window]).astype(np.int32)
    depth = np.asarray(load("depth.npy")[window]).astype(np.int32)
    flow_path = os.path.join(root, "flow_index.npy")
    flow = (
        np.asarray(load("flow_index.npy")[window]).astype(np.int32)
        if os.path.exists(flow_path) else None
    )

    counts = {"ลำธาร/แม่น้ำ": Counter(), "น้ำนิ่ง (ทะเลสาบ)": Counter()}
    if flow is not None:
        for name in WE.FLOW_NAMES:
            counts[f"ลำน้ำ: {name}"] = Counter()
        counts["พืชในน้ำแรง"] = Counter()
    zz, xx = np.nonzero(way | still)
    if not zz.size:
        return counts
    rng = np.random.default_rng(seed)
    picks = rng.choice(zz.size, size=min(limit, zz.size), replace=False)

    level = amulet.load_level(C.WORLD_PATH)
    try:
        for i in picks:
            lz, lx = int(zz[i]), int(xx[i])
            gz, gx = cz - radius + lz, cx - radius + lx
            bed = surface[lz, lx] - depth[lz, lx]
            chunk = level.get_chunk(gx >> 4, gz >> 4, C.DIMENSION)
            block = np.asarray(chunk.blocks[gx & 15, bed, gz & 15]).ravel()[0]
            name = str(level.block_palette[int(block)].base_name)
            key = "ลำธาร/แม่น้ำ" if way[lz, lx] else "น้ำนิ่ง (ทะเลสาบ)"
            counts[key][name] += 1
            if flow is not None and way[lz, lx]:
                klass = WE.FLOW_NAMES[
                    int(WE.flow_class(flow[lz, lx: lx + 1])[0])
                ]
                counts[f"ลำน้ำ: {klass}"][name] += 1
                # พืชน้ำอยู่เหนือก้นขึ้นมา — ตรวจทั้งคอลัมน์น้ำ
                if klass in ("brisk", "fast"):
                    for y in range(bed + 1, int(surface[lz, lx]) + 1):
                        b = np.asarray(
                            chunk.blocks[gx & 15, y, gz & 15]
                        ).ravel()[0]
                        plant = str(level.block_palette[int(b)].base_name)
                        if plant in ("seagrass", "tall_seagrass", "lily_pad"):
                            counts["พืชในน้ำแรง"][plant] += 1
    finally:
        level.close()
    return counts


def main():
    use_utf8_stdout()
    argv = sys.argv
    if "--at" not in argv:
        raise SystemExit("ต้องระบุ --at <x> <z>")
    i = argv.index("--at")
    cx, cz = int(argv[i + 1]), int(argv[i + 2])
    radius = (
        int(argv[argv.index("--radius") + 1]) if "--radius" in argv else 120
    )
    flag = next((f for f in ("--hydrology-root", "--root") if f in argv), None)
    root = (
        os.path.abspath(argv[argv.index(flag) + 1]) if flag
        else os.path.join(HERE, "hydrology_global")
    )
    if not os.path.isdir(root):
        raise SystemExit(f"ไม่พบชุด product: {root}")

    counts = sample_bed(root, cx, cz, radius)
    print(f"=== ก้นน้ำรอบ ({cx}, {cz}) รัศมี {radius} ===")
    for kind, counter in counts.items():
        total = sum(counter.values())
        if not total:
            print(f"{kind}: ไม่มีตัวอย่าง")
            continue
        parts = " | ".join(
            f"{name} {value * 100 // total}%"
            for name, value in counter.most_common(6)
        )
        cobble = counter.get("cobblestone", 0)
        print(f"{kind} ({total} จุด): {parts}")
        print(f"    cobblestone {cobble * 100 / total:.0f}%")


if __name__ == "__main__":
    main()
