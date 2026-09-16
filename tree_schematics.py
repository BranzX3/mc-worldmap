"""โหลดต้นไม้จากไฟล์ schematic แทนการสร้างทรงด้วยโค้ด

รองรับสามรูปแบบ
  .schem      Sponge schematic v2/v3 (WorldEdit / FAWE) — แนะนำ พกพาง่ายสุด
  .litematic  Litematica
  .nbt        structure block ของวานิลลา (บันทึกจากในเกมได้เลย)

วางไฟล์ไว้ในโฟลเดอร์ trees/ โดยตั้งชื่อขึ้นต้นด้วยชนิดไม้:
    trees/spruce_tall_01.nbt
    trees/spruce_old_02.schem
    trees/oak_wide_01.nbt
    trees/birch_01.nbt
    trees/krummholz_01.nbt

ต้นไม้จะถูกจัดกึ่งกลางตามแกน x/z อัตโนมัติ และวางให้ชั้นล่างสุดอยู่เหนือผิวดิน 1 บล็อก
สุ่มหมุน 4 ทิศเพื่อเพิ่มความหลากหลาย

หมายเหตุ: การหมุนแก้ property ทิศทางที่พบบ่อย (`axis`, `facing`, `rotation`)
ให้สอดคล้องกับพิกัดด้วย จึงรองรับกิ่งไม้แนวนอนและบล็อกประดับ directional ได้ด้วย
"""

import glob
import gzip
import os

import amulet_nbt

HERE = os.path.dirname(os.path.abspath(__file__))
TREE_DIR = os.path.join(HERE, "trees")
KINDS = ("spruce", "oak", "birch", "krummholz")


def _read_nbt(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return amulet_nbt.load(raw).compound


def _load_structure_nbt(path):
    """รูปแบบ structure block ของวานิลลา"""
    tag = _read_nbt(path)
    palette = []
    for e in tag["palette"]:
        name = str(e["Name"])
        props = {}
        if "Properties" in e:
            props = {str(k): str(v) for k, v in e["Properties"].items()}
        palette.append((name, props))
    out = []
    for b in tag["blocks"]:
        pos = [int(v) for v in b["pos"]]
        name, props = palette[int(b["state"])]
        if name.endswith(":air") or name == "minecraft:structure_void":
            continue
        out.append((pos[0], pos[1], pos[2], name, props))
    return out


def _decode_varints(data):
    out, val, shift = [], 0, 0
    for byte in data:
        val |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
        else:
            out.append(val)
            val, shift = 0, 0
    return out


def _parse_palette_name(key):
    """'minecraft:spruce_log[axis=y]' -> (ชื่อ, dict ของ property)"""
    if "[" not in key:
        return key, {}
    name, rest = key.split("[", 1)
    props = {}
    for part in rest.rstrip("]").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            props[k.strip()] = v.strip()
    return name, props


def _load_schem(path):
    """Sponge schematic v2 หรือ v3"""
    tag = _read_nbt(path)
    if "Schematic" in tag:                    # v3
        tag = tag["Schematic"]
        blocks = tag["Blocks"]
        pal_tag, data = blocks["Palette"], bytes(blocks["Data"])
    else:                                     # v2
        pal_tag, data = tag["Palette"], bytes(tag["BlockData"])

    w, h, l = int(tag["Width"]), int(tag["Height"]), int(tag["Length"])
    idx_to_block = {}
    for key, v in pal_tag.items():
        idx_to_block[int(v)] = _parse_palette_name(str(key))

    ids = _decode_varints(data)
    out = []
    for i, state in enumerate(ids):
        if i >= w * h * l:
            break
        name, props = idx_to_block.get(state, ("minecraft:air", {}))
        if name.endswith(":air") or name == "minecraft:structure_void":
            continue
        y = i // (w * l)
        rem = i % (w * l)
        z = rem // w
        x = rem % w
        out.append((x, y, z, name, props))
    return out


def _litematic_bits(longs, bits, count):
    """อ่าน bit array แบบที่ Litematica ใช้ — entry คร่อมขอบ long ได้

    ต่างจาก chunk format ของ Minecraft ยุคใหม่ที่ไม่ให้คร่อม ถ้าถอดผิดแบบ
    บล็อกจะเพี้ยนทั้งก้อน
    """
    mask = (1 << bits) - 1
    u = [int(v) & 0xFFFFFFFFFFFFFFFF for v in longs]
    out = []
    for i in range(count):
        start = i * bits
        a = start >> 6
        b = ((i + 1) * bits - 1) >> 6
        off = start & 0x3F
        if a >= len(u):
            out.append(0)
            continue
        if a == b:
            out.append((u[a] >> off) & mask)
        else:
            hi = u[b] if b < len(u) else 0
            out.append(((u[a] >> off) | (hi << (64 - off))) & mask)
    return out


def _load_litematic(path):
    tag = _read_nbt(path)
    regions = tag["Regions"]
    out = []
    for _, reg in regions.items():
        size = reg["Size"]
        sx, sy, sz = (abs(int(size["x"])), abs(int(size["y"])), abs(int(size["z"])))
        palette = []
        for e in reg["BlockStatePalette"]:
            name = str(e["Name"])
            props = ({str(k): str(v) for k, v in e["Properties"].items()}
                     if "Properties" in e else {})
            palette.append((name, props))
        bits = max(2, (len(palette) - 1).bit_length())
        idx = _litematic_bits(reg["BlockStates"], bits, sx * sy * sz)
        for i, state in enumerate(idx):
            if state >= len(palette):
                continue
            name, props = palette[state]
            if name.endswith(":air") or name == "minecraft:structure_void":
                continue
            y = i // (sx * sz)
            rem = i % (sx * sz)
            z = rem // sx
            x = rem % sx
            out.append((x, y, z, name, props))
    return out


def _clean(blocks):
    """แปลงชื่อบล็อกเก่าเป็นชื่อปัจจุบัน และคัดบล็อกที่ไม่ต้องการออก"""
    out = []
    for x, y, z, n, p in blocks:
        # ต้องแปลงชื่อก่อนคัดออก ไม่งั้นชื่อเก่าอย่าง minecraft:grass จะรอดด่าน
        # แล้วค่อยกลายเป็น short_grass ทีหลัง
        n = RENAME.get(n, n)
        if n in DROP or n in GROUND or n.endswith(":air"):
            continue
        out.append((x, y, z, n, p))
    return out


def _normalise(blocks):
    """จัดกึ่งกลางแกน x/z และให้ชั้นล่างสุดเป็น y=0"""
    blocks = _clean(blocks)
    if not blocks:
        return blocks
    ys = [b[1] for b in blocks]
    y0 = min(ys)

    # จัดกึ่งกลางจาก "โคนต้น" ไม่ใช่กรอบรวมทั้งทรงพุ่ม
    # ทรงพุ่มของไม้ผลัดใบมักเบี้ยว ถ้ายึดกรอบรวมลำต้นจะเยื้องออกไปหลายบล็อก
    # ต้นไม้จะไม่ยืนตรงจุดที่เราตั้งใจปลูก
    trunk = [(x, z) for x, y, z, n, p in blocks
             if y <= y0 + 1 and "leaves" not in n]
    if trunk:
        xs = [t[0] for t in trunk]
        zs = [t[1] for t in trunk]
    else:
        xs = [b[0] for b in blocks]
        zs = [b[2] for b in blocks]
    cx = (min(xs) + max(xs)) // 2
    cz = (min(zs) + max(zs)) // 2
    return [(x - cx, y - y0, z - cz, n, p) for x, y, z, n, p in blocks]


def rotate(blocks, turns):
    """หมุนรอบแกน y ทีละ 90 องศา"""
    turns %= 4
    if turns == 0:
        return blocks
    facing_order = ("north", "east", "south", "west")
    out = []
    for x, y, z, n, p in blocks:
        # อย่าแก้ dict ของ schematic ต้นฉบับ เพราะบล็อกเดียวกันอาจถูกใช้ซ้ำ
        p = dict(p)
        for _ in range(turns):
            x, z = -z, x
            if p.get("axis") in ("x", "z"):
                p["axis"] = "z" if p["axis"] == "x" else "x"
            if p.get("facing") in facing_order:
                p["facing"] = facing_order[(facing_order.index(p["facing"]) + 1) % 4]
            if "rotation" in p:
                try:
                    p["rotation"] = str((int(p["rotation"]) + 4) % 16)
                except (TypeError, ValueError):
                    # บาง custom block ใช้ค่าที่ไม่ใช่เลข — ปล่อยไว้ตามเดิม
                    pass
        out.append((x, y, z, n, p))
    return out


# โฟลเดอร์ในแพ็ก -> ชนิดไม้ที่เราใช้ (เฉพาะที่เข้ากับภูมิภาคแอลป์)
# ตัดพวก Jungle/Palm/Mangrove/Crimson ออกเพราะไม่ขึ้นในเขตนี้
# เรียงจากเจาะจงมากไปน้อย — ต้องเช็ค paleoak/darkoak ก่อน ไม่งั้น "oaktrees"
# จะไป match กับ "paleoaktrees" และ "darkoaktrees" ด้วย
FOLDER_KIND = [
    ("paleoaktrees", None),       # ตัดทิ้ง: ไม่ใช่ไม้เขตแอลป์ และมี creaking_heart ที่สปอว์นมอน
    ("darkoaktrees", "oak"),      # ใช้เป็นป่าแก่ในหุบเขา
    ("sprucetrees", "spruce"),
    ("oaktrees", "oak"),
    ("birchtrees", "birch"),
    ("azaleatrees", "azalea"),    # ริมน้ำ/หุบเขาชื้น
    ("mushrooms", "mushroom"),
]

# ชื่อบล็อกที่ถูกเปลี่ยนในเวอร์ชันใหม่ — schematic เก่ายังใช้ชื่อเดิมอยู่
# ถ้าไม่แปลงจะได้บล็อกที่ไม่มีอยู่จริงในเกม 26.2
RENAME = {
    "minecraft:grass": "minecraft:short_grass",
    "minecraft:grass_path": "minecraft:dirt_path",
    "minecraft:scaffolding": "minecraft:air",
}

# บล็อกพื้นดิน/พืชคลุมดินที่คนทำแพ็กปูไว้รอบโคนต้นตอนสร้าง
# ต้องตัดออก เพราะเราวางต้นไม้ที่ "ผิวดิน+1" บล็อกพวกนี้จะลอยหนึ่งชั้นและไปเกาะ
# บนใบไม้ของต้นข้างเคียงบนพื้นลาด — เราปูพื้นกับโรยพืชเองอยู่แล้ว
#
# หมายเหตุ: ตัด snow_block (ฐานหิมะที่ dy=0) แต่เก็บ snow ซึ่งเป็นชั้นบางบนกิ่ง
GROUND = {
    "minecraft:grass_block", "minecraft:dirt", "minecraft:coarse_dirt",
    "minecraft:podzol", "minecraft:rooted_dirt", "minecraft:moss_block",
    "minecraft:snow_block", "minecraft:mycelium", "minecraft:farmland",
    "minecraft:dirt_path", "minecraft:mud", "minecraft:sand", "minecraft:gravel",
    "minecraft:short_grass", "minecraft:fern", "minecraft:tall_grass",
    "minecraft:large_fern", "minecraft:dead_bush", "minecraft:moss_carpet",
    "minecraft:pale_moss_carpet", "minecraft:stone", "minecraft:packed_mud",
}

# บล็อกที่ไม่อยากได้ติดมากับต้นไม้ (ของประดับ/มอนสเตอร์)
DROP = {
    "minecraft:creaking_heart", "minecraft:white_stained_glass_pane",
    "minecraft:red_glazed_terracotta", "minecraft:chiseled_quartz_block",
    "minecraft:end_rod", "minecraft:diorite_wall",
}
KINDS = ("spruce", "oak", "birch", "azalea", "krummholz", "mushroom")


def _classify_name(path):
    """อ่านชนิด/ขนาด/หิมะ จากพาธ — แพ็กนี้ตั้งชื่อแบบ Big_/Small_/Snowy_"""
    parts = [p.lower() for p in os.path.normpath(path).split(os.sep)]
    kind = None
    matched = False
    for p in parts:
        key = p.replace("lunas_", "").replace(" ", "")
        for folder, k in FOLDER_KIND:
            if folder in key:
                kind, matched = k, True
                break
        if matched:
            break
    base = os.path.basename(path).lower()
    snowy = "snow" in base or any("snow" in p for p in parts[:-1])
    if "small" in base:
        size = "small"
    elif "big" in base or "large" in base:
        size = "big"
    else:
        size = "normal"
    return kind, size, snowy


def load_all(verbose=True):
    """คืน dict: ชนิด -> list ของ variant

    แต่ละ variant เป็น dict {blocks, height, size, snowy, name}
    เลือก .schem ก่อนเสมอ ถ้าต้นเดียวกันมี .litematic ด้วยจะข้าม (เนื้อหาเหมือนกัน)
    """
    result = {k: [] for k in KINDS}
    if not os.path.isdir(TREE_DIR):
        return result

    files = []
    for root, _, names in os.walk(TREE_DIR):
        for nm in names:
            if nm.lower().endswith((".schem", ".schematic", ".nbt", ".litematic")):
                files.append(os.path.join(root, nm))

    # กันไฟล์ซ้ำ: ต้นชื่อเดียวกันที่มีทั้ง .schem และ .litematic ให้เอา .schem
    by_stem = {}
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0].lower()
        kind, size, snowy = _classify_name(f)
        key = (kind, stem, snowy)
        prio = 0 if f.lower().endswith((".schem", ".schematic")) else 1
        if key not in by_stem or prio < by_stem[key][0]:
            by_stem[key] = (prio, f, kind, size, snowy)

    skipped = 0
    for _, (_, path, kind, size, snowy) in sorted(by_stem.items(), key=lambda kv: str(kv[0])):
        if kind is None:
            skipped += 1
            continue
        try:
            low = path.lower()
            if low.endswith(".nbt"):
                blocks = _load_structure_nbt(path)
            elif low.endswith(".litematic"):
                blocks = _load_litematic(path)
            else:
                blocks = _load_schem(path)
            blocks = _normalise(blocks)
        except Exception as exc:
            print(f"  โหลดไม่ได้ {os.path.basename(path)}: {type(exc).__name__}: {exc}")
            continue
        if not blocks:
            continue
        result[kind].append({
            "blocks": blocks,
            "height": max(b[1] for b in blocks) + 1,
            "width": max(max(abs(b[0]), abs(b[2])) for b in blocks) * 2 + 1,
            "size": size,
            "snowy": snowy,
            "name": os.path.basename(path),
        })

    if verbose:
        print(f"อ่าน {sum(len(v) for v in result.values())} ต้น "
              f"(ข้ามชนิดที่ไม่ใช้ {skipped} ไฟล์)\n")
        for k, v in result.items():
            if not v:
                continue
            hs = [t["height"] for t in v]
            sn = sum(1 for t in v if t["snowy"])
            print(f"  {k:<10} {len(v):>3} แบบ   สูง {min(hs)}-{max(hs)} บล็อก"
                  + (f"   มีแบบมีหิมะ {sn}" if sn else ""))
    return result


if __name__ == "__main__":
    print(f"อ่านจาก {TREE_DIR}\n")
    got = load_all()
    tot = sum(len(v) for v in got.values())
    if not tot:
        print("\nยังไม่มีไฟล์ — จะใช้ทรงที่สร้างด้วยโค้ดแทน")
    else:
        # ตรวจว่าบล็อกที่อ่านได้สมเหตุสมผล ไม่ใช่ขยะจากการถอดรหัสผิด
        import collections
        for k, v in got.items():
            if not v:
                continue
            c = collections.Counter()
            for t in v[:3]:
                for b in t["blocks"]:
                    c[b[3]] += 1
            top = ", ".join(f"{n.split(':')[-1]}={q}" for n, q in c.most_common(4))
            print(f"\n{k}: {top}")
