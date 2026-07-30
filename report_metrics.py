"""วัดคุณภาพของ input ทั้งหมดก่อนจะ paint — regression guard ของงานภูมิทัศน์

ตรวจสิ่งที่ unit test จับไม่ได้ เพราะเป็นคุณสมบัติของ *ข้อมูลจริงทั้งแผนที่*
ไม่ใช่ของฟังก์ชันเดี่ยว ๆ เช่น "ผิวทะเลสาบแบนจริงไหม" หรือ "ลำธารมีหลุมตันกี่จุด"

ใช้:
    python report_metrics.py                    # ทั้งแผนที่ (ต้องมี water_mask/water_depth)
    python report_metrics.py --patch 4200 7000 800
                                                # เฉพาะแปลง + สถิติพืชพรรณ
    python report_metrics.py --json out.json    # เก็บค่าไว้เทียบรอบถัดไป

ไม่เปิดโลก Minecraft จึงรันได้ตอนเล่นเกมอยู่
"""

import json
import os
import sys

import numpy as np
from PIL import Image

import config as C
import surface as S

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))

# ช่วง y ที่ amulet เขียน biome ให้เมื่อใช้ slice ไม่ระบุขอบ (`ch.biomes[:, :, :]`)
# Biomes3D ตั้ง default_section_counts=(0, 16) กับ section สูง 4 บล็อก
BIOME_DEFAULT_Y_MIN = 0
BIOME_DEFAULT_Y_MAX = 16 * 4 * 4 - 1        # 255


def expand_cardinal(mask, steps):
    m = np.asarray(mask, dtype=bool).copy()
    for _ in range(int(steps)):
        nxt = m.copy()
        nxt[1:] |= m[:-1]
        nxt[:-1] |= m[1:]
        nxt[:, 1:] |= m[:, :-1]
        nxt[:, :-1] |= m[:, 1:]
        m = nxt
    return m


def label_4conn(mask, key=None):
    """คืน (labels, count) — union-find สองรอบ ไม่พึ่ง scipy

    scipy ไม่ได้อยู่ใน requirements.txt (มาติดพ่วง osmnx) และ make_water.py
    ตั้งใจไม่พึ่งมัน จึงทำเองให้ report ใช้ได้ในทุกสภาพแวดล้อมเดียวกัน

    key ถ้าให้มา จะเชื่อม cell ที่ค่า key เท่ากันเท่านั้น ใช้แบ่ง "แอ่ง" ของ
    ลำธารที่ระดับผิวน้ำเดียวกันออกจากกัน
    """
    mask = np.asarray(mask, dtype=bool)
    labels = np.zeros(mask.shape, dtype=np.int32)
    parent = [0]
    if key is not None:
        key = np.asarray(key)

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:       # path compression
            parent[a], a = root, parent[a]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    height, width = mask.shape
    for z in range(height):
        row = mask[z]
        if not row.any():
            continue
        up = labels[z - 1] if z else None
        out = labels[z]
        krow = key[z] if key is not None else None
        kup = key[z - 1] if (key is not None and z) else None
        left = 0
        for x in np.nonzero(row)[0]:
            north = int(up[x]) if up is not None else 0
            if north and kup is not None and kup[x] != krow[x]:
                north = 0
            west = left if x and row[x - 1] else 0
            if west and krow is not None and krow[x - 1] != krow[x]:
                west = 0
            if north and west:
                union(north, west)
                out[x] = min(north, west)
            elif north or west:
                out[x] = north or west
            else:
                parent.append(len(parent))
                out[x] = len(parent) - 1
            left = int(out[x])

    # ยุบ label ที่ union กันแล้วให้เป็นเลขต่อเนื่อง 1..n
    remap = np.zeros(len(parent), dtype=np.int32)
    nxt = 0
    for i in range(1, len(parent)):
        if find(i) == i:
            nxt += 1
            remap[i] = nxt
    for i in range(1, len(parent)):
        remap[i] = remap[find(i)]
    return remap[labels], nxt


def horizontal_run_lengths(mask):
    """ความยาวช่วง True ต่อเนื่องในแต่ละแถว — vectorised

    ปิดหัวท้ายแต่ละแถวด้วย False ก่อน จึงนับช่วงที่ชนขอบภาพได้ถูก และไม่มี
    ช่วงใดเชื่อมข้ามแถว
    """
    mask = np.asarray(mask, dtype=bool)
    if not mask.size:
        return np.zeros(0, dtype=np.int64)
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=bool)
    padded[:, 1:-1] = mask
    flat = padded.ravel()
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    if not edges.size:
        return np.zeros(0, dtype=np.int64)
    return edges[1::2] - edges[0::2]


def surface_y(heightmap=None, patch=None):
    """ระดับผิวดินจริงจาก terrain_y.npy

    ห้ามคำนวณ rint() เองที่นี่ — terrain_shape.py ใส่ dither, ชั้นหิน, กองหิน
    เชิงผา และหลุมยุบเข้าไปแล้ว การแปลง heightmap ตรง ๆ จะได้ภูมิประเทศคนละชุด
    กับที่เขียนลงโลกจริง แล้วตัวเลขทุกตัวในรายงานนี้จะวัดของที่ไม่มีอยู่
    """
    path = os.path.join(HERE, "terrain_y.npy")
    if not os.path.exists(path):
        raise SystemExit("ไม่พบ terrain_y.npy — รัน terrain_shape.py ก่อน")
    y = np.load(path, mmap_mode="r")
    if patch:
        x0, x1, z0, z1 = patch
        y = y[z0:z1, x0:x1]
    return np.asarray(y, dtype=np.int32)


# --------------------------------------------------------------- ทะเลสาบ
def lake_metrics(water, depth, y, tile_blocks=512, top_n=6):
    """ความแบนของผิวทะเลสาบ + ทะเลสาบที่คร่อม tile

    ตัวชี้วัดหลักคือ level_spread: จำนวนบล็อกที่ผิวน้ำของทะเลสาบ *เดียวกัน*
    ต่างกันใน DEM ค่าที่ไม่ใช่ 0 หมายถึงจะเห็นขั้นน้ำกลางทะเลสาบ
    """
    core = water & (depth >= int(getattr(C, "LAKE_MIN_DEPTH_FOR_OFFSET", 3)))
    labels, count = label_4conn(core)
    if not count:
        return {"components": 0, "lakes": [], "core_cells": 0}

    # จัดกลุ่มด้วยการเรียงครั้งเดียว — การวน `labels == lid` ต่อ component
    # เป็น O(components x cells) ซึ่งบนแผนที่ 10000² กับ 4000+ ทะเลสาบไม่จบ
    zz, xx = np.nonzero(core)
    lid = labels[zz, xx]
    order = np.argsort(lid, kind="stable")
    lid, zz, xx = lid[order], zz[order], xx[order]
    levels = y[zz, xx]
    starts = np.searchsorted(lid, np.arange(1, count + 1), side="left")
    ends = np.searchsorted(lid, np.arange(1, count + 1), side="right")
    sizes = ends - starts

    z_min = np.minimum.reduceat(zz, starts)
    z_max = np.maximum.reduceat(zz, starts)
    x_min = np.minimum.reduceat(xx, starts)
    x_max = np.maximum.reduceat(xx, starts)
    lv_min = np.minimum.reduceat(levels, starts)
    lv_max = np.maximum.reduceat(levels, starts)

    spread = lv_max - lv_min
    span = np.maximum(z_max - z_min + 1, x_max - x_min + 1)
    crosses = (
        (z_min // tile_blocks != z_max // tile_blocks)
        | (x_min // tile_blocks != x_max // tile_blocks)
    )
    not_flat = int((spread > 0).sum())
    not_flat_cells = int(sizes[spread > 0].sum())
    spanning = int((span > tile_blocks).sum())
    crossing = int(crosses.sum())

    lakes = []
    for i in np.argsort(sizes)[::-1][:top_n]:
        a, b = starts[i], ends[i]
        vals, counts = np.unique(levels[a:b], return_counts=True)
        share = {
            int(v): round(float(c) / (b - a), 3)
            for v, c in sorted(zip(vals, counts), key=lambda t: -t[1])
        }
        tiles = len(set(zip(zz[a:b] // tile_blocks, xx[a:b] // tile_blocks)))
        lakes.append({
            "id": int(i + 1),
            "cells": int(sizes[i]),
            "area_km2": round(int(sizes[i]) * C.METERS_PER_BLOCK ** 2 / 1e6, 2),
            "bbox": [int(z_max[i] - z_min[i] + 1), int(x_max[i] - x_min[i] + 1)],
            "level_spread": int(spread[i]),
            "level_share": share,
            "tiles": tiles,
        })
    return {
        "components": int(count),
        "core_cells": int(core.sum()),
        "not_flat_components": not_flat,
        "not_flat_cells": int(not_flat_cells),
        "not_flat_cell_share": round(float(not_flat_cells) / max(1, core.sum()), 4),
        "spanning_a_tile": spanning,
        "crossing_a_tile_boundary": crossing,
        "lakes": lakes,
    }


# ---------------------------------------------------------------- ลำธาร
def stream_metrics(water, depth, y):
    """ความต่อเนื่องของลำธาร — หลุมตันคือจุดที่น้ำไม่มีทางออก"""
    core = water & (depth >= int(getattr(C, "LAKE_MIN_DEPTH_FOR_OFFSET", 3)))
    search = int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    stream = water & ~expand_cardinal(core, search)
    total = int(stream.sum())
    if not total:
        return {"cells": 0}

    # หน่วยที่ถูกต้องคือ "แอ่ง" ไม่ใช่ cell — ลำธารในโลกจริงเป็นชั้นบันได
    # 87% ของคู่เพื่อนบ้านอยู่ระดับเดียวกัน ถ้าวัดต่อ cell ทุก cell กลางแอ่ง
    # แบนจะถูกนับเป็นหลุม (วัดได้ 73% ซึ่งไม่มีความหมาย)
    #
    # แอ่ง = กลุ่ม cell ลำธารที่ติดกันและอยู่ระดับ Y เดียวกัน
    # แอ่งตัน = ไม่มี cell ใดในกลุ่มที่ติดน้ำระดับต่ำกว่า และไม่แตะขอบกรอบ
    # นี่คือจุดที่น้ำไม่มีทางออกจริง = ลำธารขาด
    escape = np.zeros(stream.shape, dtype=bool)
    for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        shifted_y = np.roll(np.roll(y, -dz, 0), -dx, 1)
        shifted_wet = np.roll(np.roll(water, -dz, 0), -dx, 1)
        escape |= shifted_wet & (shifted_y < y)
    # ขอบกรอบคือทางออกจริง ไม่งั้น --patch จะรายงานลำธารที่ไหลออกนอกกรอบเป็นตัน
    escape[0], escape[-1], escape[:, 0], escape[:, -1] = True, True, True, True

    pools, pool_count = label_4conn(stream, key=y)
    trapped_pools = trapped_cells = 0
    if pool_count:
        pz, px = np.nonzero(stream)
        pid = pools[pz, px]
        order = np.argsort(pid, kind="stable")
        pid = pid[order]
        has_escape = escape[pz, px][order]
        starts = np.searchsorted(pid, np.arange(1, pool_count + 1), side="left")
        ends = np.searchsorted(pid, np.arange(1, pool_count + 1), side="right")
        pool_escapes = np.logical_or.reduceat(has_escape, starts)
        trapped_pools = int((~pool_escapes).sum())
        trapped_cells = int((ends - starts)[~pool_escapes].sum())

    steps = []
    for dz, dx in ((0, 1), (1, 0)):
        both = (
            stream[:stream.shape[0] - dz, :stream.shape[1] - dx]
            & stream[dz:, dx:]
        )
        steps.append(np.abs(
            y[:y.shape[0] - dz, :y.shape[1] - dx] - y[dz:, dx:]
        )[both])
    steps = np.concatenate(steps) if steps else np.zeros(0, dtype=np.int32)

    runs = horizontal_run_lengths(stream[::5])

    return {
        "cells": total,
        "share_of_water": round(total / max(1, int(water.sum())), 4),
        "pools": int(pool_count),
        "trapped_pools": trapped_pools,
        "trapped_cells": trapped_cells,
        "trapped_cell_share": round(trapped_cells / total, 4),
        "width_median": int(np.median(runs)) if runs.size else 0,
        "width_mean": round(float(runs.mean()), 2) if runs.size else 0.0,
        "width_1_share": round(float((runs == 1).mean()), 4) if runs.size else 0.0,
        "step_flat_share": round(float((steps == 0).mean()), 4) if steps.size else 0.0,
        "step_1_share": round(float((steps == 1).mean()), 4) if steps.size else 0.0,
        "step_2plus": int((steps >= 2).sum()) if steps.size else 0,
        "step_max": int(steps.max()) if steps.size else 0,
    }


# ----------------------------------------------------------------- ตลิ่ง
def bank_metrics(water, depth, shore_width=4):
    """สัดส่วนตลิ่งที่ไม่ได้รับการแต่งเลย

    ใช้ probability ชุดเดียวกับ painter เพื่อให้ metric วัดสิ่งที่จะถูกเขียนจริง
    """
    reach = max(shore_width, int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12)))
    lake_p, bank_p = S.shore_probabilities(
        water, depth, shore_width=shore_width, search_blocks=reach
    )
    bank = expand_cardinal(water, shore_width) & ~water
    total = int(bank.sum())
    untreated = int((bank & (np.maximum(lake_p, bank_p) <= 0.0)).sum())
    return {
        "bank_cells": total,
        "untreated_cells": untreated,
        "untreated_share": round(untreated / max(1, total), 4),
    }


# ------------------------------------------------------------------ biome
def biome_coverage(y, water):
    """คอลัมน์ที่หลุดช่วง y ที่ `ch.biomes[:, :, :]` เขียนถึง"""
    below = int((y < BIOME_DEFAULT_Y_MIN).sum())
    above = int((y > BIOME_DEFAULT_Y_MAX).sum())
    wet = y[water]
    return {
        "default_write_range": [BIOME_DEFAULT_Y_MIN, BIOME_DEFAULT_Y_MAX],
        "columns_below_share": round(below / y.size, 4),
        "columns_above_share": round(above / y.size, 4),
        "water_below_cells": int((wet < BIOME_DEFAULT_Y_MIN).sum()) if wet.size else 0,
        "water_below_share": (
            round(float((wet < BIOME_DEFAULT_Y_MIN).mean()), 4) if wet.size else 0.0
        ),
        "water_y_min": int(wet.min()) if wet.size else 0,
        "water_y_max": int(wet.max()) if wet.size else 0,
    }


# ------------------------------------------------------------- พืชพรรณ
def vegetation_metrics(elev, lc, terrain_offset=None, x0=0, z0=0):
    """ความหนาแน่นป่า/พืช บนแปลงเดียว — ใช้กฎเดียวกับ paint_surface

    เป็นสถิติของ *ศักยภาพ* (forest_p และจุดปลูกที่ผ่านเงื่อนไข) ไม่ใช่จำนวนต้น
    ที่เขียนลงโลกจริง เพราะยังไม่ได้รวมการชนกันของ schematic
    """
    import vegetation as V

    cls, forest_p, snow_lv, soil = S.classify(
        elev, lc, C.METERS_PER_BLOCK, block_m=C.METERS_PER_BLOCK,
        x0=x0, z0=z0, terrain_offset=terrain_offset,
    )
    height, width = elev.shape
    hectares = height * width * C.METERS_PER_BLOCK ** 2 / 10_000.0

    planted = {layer: 0 for layer, *_ in V.LAYERS}
    for tx, tz, layer, rng in V.tree_slots(x0, x0 + height, z0, z0 + width):
        ix, iz = tx - x0, tz - z0
        fp = float(forest_p[ix, iz])
        if fp <= 0.02 or rng.random() > fp:
            continue
        if cls[ix, iz] in (S.IDX["water"], S.IDX["ice"]):
            continue
        if layer == "emergent" and fp < 0.60:
            continue
        planted[layer] += 1

    total = sum(planted.values())
    return {
        "hectares": round(hectares, 1),
        "forest_p_mean": round(float(forest_p.mean()), 4),
        "forest_cover_share": round(float((forest_p > 0.02).mean()), 4),
        "soil_share": round(float(soil.mean()), 4),
        "snow_covered_share": round(float((snow_lv > 0).mean()), 4),
        "snow_over_soil_share": round(float((soil & (snow_lv > 0)).mean()), 4),
        "trees": total,
        "trees_per_hectare": round(total / max(1e-9, hectares), 2),
        "trees_by_layer": planted,
    }


def load_inputs(patch=None):
    hm = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))
    lc = np.load(os.path.join(HERE, "landcover.npz"))["landcover"]
    for name in ("water_mask.npy", "water_depth.npy"):
        if not os.path.exists(os.path.join(HERE, name)):
            raise SystemExit(f"ต้องมี {name} ก่อน — รัน make_water.py")
    wm = np.load(os.path.join(HERE, "water_mask.npy"))
    wd = np.load(os.path.join(HERE, "water_depth.npy"))
    if patch:
        x0, x1, z0, z1 = patch
        hm, lc, wm, wd = (a[z0:z1, x0:x1] for a in (hm, lc, wm, wd))
    return hm, lc, wm, wd


def main():
    patch = None
    if "--patch" in sys.argv:
        i = sys.argv.index("--patch")
        cx, cz, size = (int(v) for v in sys.argv[i + 1:i + 4])
        half = size // 2
        patch = (max(0, cx - half), cx + half, max(0, cz - half), cz + half)

    hm, lc, wm, wd = load_inputs(patch)
    y = surface_y(patch=patch)
    water = wm.astype(bool)
    scope = (
        f"patch x {patch[0]}..{patch[1]} z {patch[2]}..{patch[3]}"
        if patch else f"fullscale {hm.shape[0]}x{hm.shape[1]}"
    )

    report = {
        "scope": scope,
        "water_cells": int(water.sum()),
        "water_share": round(float(water.mean()), 5),
        "lake": lake_metrics(water, wd, y),
        "stream": stream_metrics(water, wd, y),
        "bank": bank_metrics(water, wd),
        "biome": biome_coverage(y, water),
    }

    if patch:
        meta = S.load_meta()
        lo, hi = meta["elev_min_m"], meta["elev_max_m"]
        sub = hm.T.astype(np.float32)
        elev = lo + sub / 65535.0 * (hi - lo)
        terrain_y = (
            C.Y_TERRAIN_MIN
            + sub / 65535.0 * (C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN)
        )
        report["vegetation"] = vegetation_metrics(
            elev, S.apply_water_mask(lc, wm).T,
            terrain_offset=terrain_y - np.rint(terrain_y),
            x0=patch[0], z0=patch[2],
        )

    print(f"=== {scope} ===")
    print(f"น้ำ {report['water_cells']:,} cells ({report['water_share']:.3%})\n")

    lake = report["lake"]
    print(f"-- ทะเลสาบ: {lake['components']:,} component, "
          f"core {lake['core_cells']:,} cells")
    if lake["components"]:
        print(f"   ผิวไม่แบน: {lake['not_flat_components']:,} component "
              f"= {lake['not_flat_cell_share']:.1%} ของพื้น core   <-- ต้องเป็น 0")
        print(f"   คร่อม tile 512: {lake['crossing_a_tile_boundary']:,} | "
              f"กว้างเกิน tile: {lake['spanning_a_tile']:,}")
        for item in lake["lakes"]:
            share = " ".join(f"Y={k}:{v:.0%}" for k, v in item["level_share"].items())
            print(f"   #{item['id']:<5} {item['cells']:>9,} cells "
                  f"({item['area_km2']:>5.2f} km²) bbox {item['bbox'][0]}x{item['bbox'][1]} "
                  f"spread {item['level_spread']} tiles {item['tiles']:>2} | {share}")

    stream = report["stream"]
    print(f"\n-- ลำธาร: {stream.get('cells', 0):,} cells "
          f"({stream.get('share_of_water', 0):.1%} ของน้ำ)")
    if stream.get("cells"):
        print(f"   แอ่ง {stream['pools']:,} | แอ่งตัน (น้ำไม่มีทางออก) "
              f"{stream['trapped_pools']:,} = {stream['trapped_cells']:,} cells "
              f"({stream['trapped_cell_share']:.1%})   <-- ต้องเป็น 0")
        print(f"   ความกว้าง: median {stream['width_median']} "
              f"mean {stream['width_mean']} | กว้าง 1 บล็อก {stream['width_1_share']:.1%}")
        print(f"   ขั้น Y: แบน {stream['step_flat_share']:.1%} | "
              f"1 บล็อก {stream['step_1_share']:.1%} | "
              f">=2 บล็อก {stream['step_2plus']:,} edges (สูงสุด {stream['step_max']})")

    bank = report["bank"]
    print(f"\n-- ตลิ่ง: {bank['bank_cells']:,} cells | "
          f"ไม่ได้แต่งเลย {bank['untreated_cells']:,} = "
          f"{bank['untreated_share']:.1%}   <-- ควรลดลง")

    biome = report["biome"]
    lo_y, hi_y = biome["default_write_range"]
    print(f"\n-- biome: ch.biomes[:, :, :] เขียนได้แค่ y {lo_y}..{hi_y}")
    print(f"   คอลัมน์ต่ำกว่าช่วง: {biome['columns_below_share']:.1%} | "
          f"สูงกว่าช่วง: {biome['columns_above_share']:.2%}")
    print(f"   น้ำที่หลุดช่วง: {biome['water_below_cells']:,} = "
          f"{biome['water_below_share']:.1%} (ผิวน้ำ Y "
          f"{biome['water_y_min']}..{biome['water_y_max']})   <-- ต้องเป็น 0")

    if "vegetation" in report:
        veg = report["vegetation"]
        print(f"\n-- พืชพรรณ ({veg['hectares']:,.0f} ha)")
        print(f"   forest_p เฉลี่ย {veg['forest_p_mean']:.3f} | "
              f"พื้นที่ที่มีป่า {veg['forest_cover_share']:.1%} | "
              f"ดินปลูกได้ {veg['soil_share']:.1%}")
        print(f"   หิมะคลุม {veg['snow_covered_share']:.1%} "
              f"(บนดินปลูกได้ {veg['snow_over_soil_share']:.1%} "
              f"— ส่วนนี้ปัจจุบันไม่มีพืชเลย)")
        print(f"   ต้นไม้ {veg['trees']:,} = "
              f"{veg['trees_per_hectare']} ต้น/ha | {veg['trees_by_layer']}")

    if "--json" in sys.argv:
        path = sys.argv[sys.argv.index("--json") + 1]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nบันทึก {path}")


if __name__ == "__main__":
    main()
