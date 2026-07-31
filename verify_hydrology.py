"""ตรวจกฎของ product น้ำทั้งชุดบน build จริง ในคำสั่งเดียว

ทำไมต้องมี: เทสต์ทั้งหมดรันบนผังสังเคราะห์ขนาด 40-128 บล็อก **ไม่มีตัวไหนแตะ
product จริง 10000x10000 เลย** ผลคือการถดถอยที่แท้จริงลอดผ่านได้ทั้งหมด —
ครั้งหนึ่ง "ทะเลสาบลอยเหนือลำน้ำ" พุ่งจาก 78 เป็น 41,504 ขอบ โดยผ่านเทสต์
232 ตัวและผ่านการตรวจด้วยตาเปล่า เพราะคนแก้วัดแต่ตัวชี้วัดที่ตั้งใจแก้

กฎถูกกระจายอยู่หลายที่ (บางอันในเทสต์ บางอันใน report_metrics บางอันในสคริปต์
ชั่วคราวที่เขียนแล้วทิ้ง) ไฟล์นี้รวบมาไว้ที่เดียว รันจบแล้วบอก PASS/FAIL

แยกสองประเภทให้ชัด:
  INVARIANT  ผิดไม่ได้เลย ถ้า FAIL คือของเสีย ห้าม paint ลงโลก
  QUALITY    ตัวเลขคุณภาพ ไม่มีค่าถูก/ผิดตายตัว ใช้เทียบกับ build ก่อนหน้า

ใช้:
    python verify_hydrology.py hydrology_global_r
    python verify_hydrology.py hydrology_global_r --baseline hydrology_global_j
"""

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
UNRESOLVED = np.iinfo(np.int16).min
BAND = 1000

PAIRS = (
    (np.s_[1:, :], np.s_[:-1, :]),
    (np.s_[:-1, :], np.s_[1:, :]),
    (np.s_[:, 1:], np.s_[:, :-1]),
    (np.s_[:, :-1], np.s_[:, 1:]),
)


def open_products(root):
    names = ("terrain_y", "water_mask", "standing_water_mask", "waterway_mask",
             "surface_y", "depth", "waterfall_top_y")
    out = {}
    for name in names:
        path = os.path.join(root, name + ".npy")
        if not os.path.exists(path):
            raise SystemExit(f"{root}: ไม่พบ {name}.npy")
        out[name] = np.load(path, mmap_mode="r")
    out["base_y"] = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")
    return out


def bands(height, overlap=1):
    """ช่วงแถวพร้อมขอบเผื่อ — คืน (ช่วงอ่าน, ช่วงที่นับผล)"""
    for row0 in range(0, height, BAND):
        row1 = min(height, row0 + BAND)
        a0, a1 = max(0, row0 - overlap), min(height, row1 + overlap)
        yield (a0, a1), np.s_[row0 - a0:row1 - a0]


def measure(root):
    """คืน dict ของตัวเลขดิบทั้งหมด — ไม่ตัดสินผ่าน/ไม่ผ่านที่นี่"""
    p = open_products(root)
    height = p["terrain_y"].shape[0]
    m = {k: 0 for k in (
        "leak", "leak_max", "orphan_level", "orphan_depth", "missing_level",
        "lake_above_way", "lake_above_max", "perched", "levee", "levee2",
        "spill", "spill_wide", "step_h", "step_v", "water", "way", "body",
        "bed_varied", "wide_way", "wide_shallow", "section_bad", "section_all",
    )}
    for (a0, a1), core in bands(height):
        terr = np.asarray(p["terrain_y"][a0:a1]).astype(np.int32)
        base = np.asarray(p["base_y"][a0:a1]).astype(np.int32)
        surf = np.asarray(p["surface_y"][a0:a1]).astype(np.int32)
        top = np.asarray(p["waterfall_top_y"][a0:a1]).astype(np.int32)
        water = np.asarray(p["water_mask"][a0:a1])
        body = np.asarray(p["standing_water_mask"][a0:a1])
        way = np.asarray(p["waterway_mask"][a0:a1])
        depth = np.asarray(p["depth"][a0:a1]).astype(np.int32)
        wet_top = np.where(
            water, np.maximum(surf, np.where(top != UNRESOLVED, top, -32768)),
            -32768,
        )

        m["water"] += int(water[core].sum())
        m["way"] += int(way[core].sum())
        m["body"] += int(body[core].sum())
        # --- ความสอดคล้องของ product ---
        m["missing_level"] += int((water & (surf == UNRESOLVED))[core].sum())
        m["orphan_level"] += int((~water & (surf != UNRESOLVED))[core].sum())
        m["orphan_depth"] += int((~water & (depth > 0))[core].sum())
        # --- น้ำลอยเหนือพื้นเดิม ---
        m["perched"] += int((water & (surf > base))[core].sum())
        lift = terr - base
        m["levee"] += int((~water & (lift >= 1))[core].sum())
        m["levee2"] += int((~water & (lift >= 2))[core].sum())
        # --- ก้นน้ำมีรูปทรงไหม ---
        bed = surf - depth
        varied = np.zeros(water.shape, dtype=bool)
        # --- เพื่อนบ้าน ---
        leak = np.zeros(water.shape, dtype=np.int32)
        lake_above = np.zeros(water.shape, dtype=np.int32)
        spill = np.zeros(water.shape, dtype=bool)
        for dst, src in PAIRS:
            dry = water[src] & ~water[dst]
            np.maximum(leak[dst], np.where(dry, wet_top[src] - terr[dst], 0),
                       out=leak[dst])
            both = water[src] & water[dst]
            higher = both & (wet_top[src] > wet_top[dst])
            spill[dst] |= higher
            la = body[src] & way[dst] & (surf[src] > surf[dst])
            np.maximum(lake_above[dst], np.where(la, surf[src] - surf[dst], 0),
                       out=lake_above[dst])
            varied[dst] |= both & (bed[dst] != bed[src])
        m["leak"] += int((leak[core] > 0).sum())
        m["leak_max"] = max(m["leak_max"], int(leak[core].max(initial=0)))
        m["lake_above_way"] += int((lake_above[core] > 0).sum())
        m["lake_above_max"] = max(
            m["lake_above_max"], int(lake_above[core].max(initial=0))
        )
        m["spill"] += int(spill[core].sum())
        m["bed_varied"] += int((varied & water)[core].sum())
        # ผืนน้ำกว้าง = มีเพื่อนบ้านเป็นน้ำครบสี่ทิศ
        wet_n = np.zeros(water.shape, dtype=np.int32)
        for dst, src in PAIRS:
            wet_n[dst] += water[src]
        wide = water & (wet_n >= 4)
        m["spill_wide"] += int((spill & wide)[core].sum())
        m["wide_way"] += int((wide & way)[core].sum())
        m["wide_shallow"] += int((wide & way & (depth <= 1))[core].sum())
        # ทิศของเส้นขั้น
        sh = way[1:] & way[:-1] & (surf[1:] != surf[:-1])
        sv = way[:, 1:] & way[:, :-1] & (surf[:, 1:] != surf[:, :-1])
        m["step_h"] += int(sh[core].sum())
        m["step_v"] += int(sv[core].sum())
        # หน้าตัดตามแถว
        bad, tot = run_evenness(way[core], surf[core])
        m["section_bad"] += bad
        m["section_all"] += tot

    # หน้าตัดตามคอลัมน์ — ต้องอ่านเป็นแถบคอลัมน์แทน
    width = p["terrain_y"].shape[1]
    for col0 in range(0, width, BAND):
        col1 = min(width, col0 + BAND)
        way = np.asarray(p["waterway_mask"][:, col0:col1]).T
        surf = np.asarray(p["surface_y"][:, col0:col1]).astype(np.int32).T
        bad, tot = run_evenness(way, surf)
        m["section_bad"] += bad
        m["section_all"] += tot

    manifest_path = os.path.join(root, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as stream:
            m["manifest"] = json.load(stream)
    return m


def run_evenness(mask, surface, max_width=16):
    """จำนวน run สั้น (= หน้าตัดขวาง) ที่ผิวน้ำไม่เท่ากันทั้งเส้น"""
    bad = total = 0
    for i in range(mask.shape[0]):
        row = mask[i]
        if not row.any():
            continue
        idx = np.flatnonzero(row)
        breaks = np.flatnonzero(np.diff(idx) != 1)
        starts = np.concatenate(([0], breaks + 1))
        ends = np.concatenate((breaks, [idx.size - 1]))
        for a, b in zip(starts, ends):
            span = idx[a:b + 1]
            if span.size < 3 or span.size > max_width:
                continue
            total += 1
            values = surface[i, span]
            if values.min() != values.max():
                bad += 1
    return bad, total


def share(part, whole):
    return part / whole if whole else 0.0


def build_report(m):
    """คืน list ของ (ชนิด, ชื่อ, ค่า, ข้อความ, ผ่านไหม, ยิ่งน้อยยิ่งดีไหม)"""
    seams = m.get("manifest", {})
    rows = [
        ("INVARIANT", "น้ำไม่รั่วออกจากตลิ่ง", m["leak"],
         f"{m['leak']:,} cells (ลึกสุด {m['leak_max']})", m["leak"] == 0, True),
        ("INVARIANT", "cell น้ำต้องมีระดับผิวน้ำ", m["missing_level"],
         f"{m['missing_level']:,} cells", m["missing_level"] == 0, True),
        ("INVARIANT", "ระดับผิวน้ำต้องไม่ค้างบนบก", m["orphan_level"],
         f"{m['orphan_level']:,} cells", m["orphan_level"] == 0, True),
        ("INVARIANT", "ความลึกต้องไม่ค้างบนบก", m["orphan_depth"],
         f"{m['orphan_depth']:,} cells", m["orphan_depth"] == 0, True),
        ("INVARIANT", "ทะเลสาบไม่ลอยเหนือลำน้ำ", m["lake_above_way"],
         f"{m['lake_above_way']:,} cells (สูงสุด {m['lake_above_max']})",
         m["lake_above_way"] == 0, True),
        ("QUALITY", "น้ำลอยเหนือ DEM เดิม", m["perched"],
         f"{m['perched']:,} ({share(m['perched'], m['water']):.2%} ของน้ำ)",
         None, True),
        ("QUALITY", "คันดินรอบน้ำ (ยก>=1)", m["levee"],
         f"{m['levee']:,} cells (ยก>=2: {m['levee2']:,})", None, True),
        ("QUALITY", "จุดที่น้ำจะแผ่ทับ", m["spill"],
         f"{m['spill']:,} (บนผืนกว้าง {m['spill_wide']:,})", None, True),
        ("QUALITY", "หน้าตัดที่ผิวน้ำไม่เท่ากัน", m["section_bad"],
         f"{m['section_bad']:,}/{m['section_all']:,} "
         f"({share(m['section_bad'], m['section_all']):.1%})", None, True),
        ("QUALITY", "ลำน้ำกว้างที่ตื้น <=1 บล็อก", m["wide_shallow"],
         f"{m['wide_shallow']:,}/{m['wide_way']:,} "
         f"({share(m['wide_shallow'], m['wide_way']):.1%})", None, True),
        ("QUALITY", "ก้นน้ำที่มีรูปทรง (ต่างจากเพื่อนบ้าน)", m["bed_varied"],
         f"{share(m['bed_varied'], m['water']):.1%} ของน้ำ", None, False),
    ]
    ratio = share(m["step_h"], m["step_v"]) if m["step_v"] else 0.0
    balance = abs(ratio - 1.0)
    rows.append((
        "QUALITY", "ทิศเส้นขั้น (ยิ่งใกล้ 1.00 ยิ่งดี)", round(balance, 3),
        f"นอน:ตั้ง = {m['step_h']:,}:{m['step_v']:,} ({ratio:.2f})", None, True,
    ))
    if seams:
        edge = seams.get("seam_step2plus_share", 0.0)
        inner = seams.get("interior_step2plus_share", 0.0)
        ok = edge <= max(0.002, inner * 1.5)
        rows.append((
            "INVARIANT", "รอยต่อ tile ไม่แย่กว่าภายใน", round(edge, 5),
            f"ขอบ {edge:.3%} vs ภายใน {inner:.3%}", ok, True,
        ))
    return rows


def main():
    argv = sys.argv
    if len(argv) < 2:
        raise SystemExit(__doc__)
    root = os.path.abspath(argv[1])
    baseline = None
    if "--baseline" in argv:
        baseline = os.path.abspath(argv[argv.index("--baseline") + 1])

    print(f"ตรวจ {os.path.basename(root)} ...")
    rows = build_report(measure(root))
    base_rows = None
    if baseline:
        print(f"เทียบกับ {os.path.basename(baseline)} ...")
        base_rows = {r[1]: r[2] for r in build_report(measure(baseline))}

    print()
    failed = 0
    for kind, name, value, text, ok, lower_better in rows:
        if ok is None:
            tag = "     "
        elif ok:
            tag = "PASS "
        else:
            tag = "FAIL "
            failed += 1
        delta = ""
        if base_rows is not None and name in base_rows:
            before = base_rows[name]
            if before != value:
                better = (value < before) if lower_better else (value > before)
                mark = "ดีขึ้น" if better else "แย่ลง"
                delta = f"   [{before:,} -> {value:,} {mark}]"
        print(f"[{tag}] {kind:<9} {name:<38} {text}{delta}")

    print()
    if failed:
        print(f"ไม่ผ่าน {failed} กฎ — ห้าม paint ลงโลกจนกว่าจะแก้")
    else:
        print("ผ่านกฎบังคับทั้งหมด")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
