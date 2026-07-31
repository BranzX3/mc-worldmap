"""วัดคุณภาพของ input ทั้งหมดก่อนจะ paint — regression guard ของงานภูมิทัศน์

ตรวจสิ่งที่ unit test จับไม่ได้ เพราะเป็นคุณสมบัติของ *ข้อมูลจริงทั้งแผนที่*
ไม่ใช่ของฟังก์ชันเดี่ยว ๆ เช่น "ผิวทะเลสาบแบนจริงไหม" หรือ "ลำธารมีหลุมตันกี่จุด"

ใช้:
    python report_metrics.py --hydrology-root hydrology_global
                                                # ชุดที่เขียนลงโลกจริง
    python report_metrics.py                    # ชุดเดิม (make_water + make_water_levels)
    python report_metrics.py --patch 4200 7000 800
                                                # เฉพาะแปลง + สถิติพืชพรรณ
    python report_metrics.py --json out.json    # เก็บค่าไว้เทียบรอบถัดไป

**ต้องระบุ --hydrology-root ให้ตรงกับที่ build_terrain/paint_surface ใช้** ไม่งั้น
รายงานจะวัด product คนละชุดกับที่อยู่ในโลก ซึ่งเคยเกิดมาแล้วและไม่มีอะไรฟ้อง
บรรทัด "product ที่วัด" ตอนต้นรายงานมีไว้ให้เห็นทันทีว่ากำลังวัดชุดไหน

ไม่เปิดโลก Minecraft จึงรันได้ตอนเล่นเกมอยู่
"""

import json
import os
import sys

import numpy as np
from PIL import Image

import config as C
import surface as S
import hydrology_patch_io as H
from pipeline_progress import use_utf8_stdout

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
    # เขียนทับในที่ทีละแถบ — `remap[labels]` สร้าง int32 เต็มแผนที่ก้อนใหม่
    # (400 MB) ขณะที่ `labels` ก้อนเดิมยังมีชีวิต = 800 MB พร้อมกัน
    for z0 in range(0, labels.shape[0], 512):
        z1 = min(labels.shape[0], z0 + 512)
        labels[z0:z1] = remap[labels[z0:z1]]
    return labels, nxt


def label_8conn(mask):
    """กลุ่มที่เชื่อมกันแบบ 8 ทิศ — น้ำตกที่เฉียงตามลำน้ำยังนับเป็นแนวเดียวกัน

    ใช้ union-find ตัวเดียวกับ label_4conn ผ่านการรวมสองรอบ เพื่อไม่ต้องพึ่ง
    scipy ตามเจตนาเดิมของไฟล์นี้
    """
    mask = np.asarray(mask, dtype=bool)
    labels, count = label_4conn(mask)
    if count <= 1:
        return labels, count
    # เชื่อมเพิ่มตามแนวทแยงด้วยการรวมกลุ่มที่แตะกันมุมต่อมุม
    parent = list(range(count + 1))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for dst, src in (
        (np.s_[1:, 1:], np.s_[:-1, :-1]),
        (np.s_[1:, :-1], np.s_[:-1, 1:]),
    ):
        both = mask[dst] & mask[src]
        if not both.any():
            continue
        for a, b in zip(labels[dst][both], labels[src][both]):
            ra, rb = find(int(a)), find(int(b))
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

    remap = np.zeros(count + 1, dtype=np.int32)
    nxt = 0
    for i in range(1, count + 1):
        if find(i) == i:
            nxt += 1
            remap[i] = nxt
    for i in range(1, count + 1):
        remap[i] = remap[find(i)]
    for z0 in range(0, labels.shape[0], 512):
        z1 = min(labels.shape[0], z0 + 512)
        labels[z0:z1] = remap[labels[z0:z1]]
    return labels, nxt


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


def surface_y(heightmap=None, patch=None, hydrology_root=None):
    """ระดับผิวดินจริงจาก terrain_y.npy

    ห้ามคำนวณ rint() เองที่นี่ — terrain_shape.py ใส่ dither, ชั้นหิน, กองหิน
    เชิงผา และหลุมยุบเข้าไปแล้ว การแปลง heightmap ตรง ๆ จะได้ภูมิประเทศคนละชุด
    กับที่เขียนลงโลกจริง แล้วตัวเลขทุกตัวในรายงานนี้จะวัดของที่ไม่มีอยู่

    เมื่อระบุ hydrology_root ต้องอ่าน terrain_y ของชุดนั้น เพราะ hydrology
    reshape ตลิ่งและก้นน้ำทับลงไปแล้ว — ตัวที่ build_terrain เขียนลงโลกจริง
    """
    path = H.water_product_path("terrain_y", hydrology_root, HERE)
    if not os.path.exists(path):
        raise SystemExit(f"ไม่พบ {path} — รัน terrain_shape.py ก่อน")
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
def stream_metrics(water, depth, y, outlet_mask=None):
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
    # เพื่อนบ้านด้วย slicing ไม่ใช่ np.roll — roll คัดลอกทั้ง array ทุกครั้ง
    # (int32 เต็มแผนที่ = 400 MB) และซ้อนกันสองชั้นต่อทิศ x 4 ทิศ ส่วนการ wrap
    # ที่ขอบไม่เคยมีผลอยู่แล้วเพราะขอบถูกตั้งเป็น escape=True ทันทีหลังลูปนี้
    escape = np.zeros(stream.shape, dtype=bool)
    for dst, src in (
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:, :-1], np.s_[:, 1:]),
        (np.s_[:, 1:], np.s_[:, :-1]),
    ):
        escape[dst] |= water[src] & (y[src] < y[dst])
    # ขอบกรอบคือทางออกจริง ไม่งั้น --patch จะรายงานลำธารที่ไหลออกนอกกรอบเป็นตัน
    escape[0], escape[-1], escape[:, 0], escape[:, -1] = True, True, True, True
    if outlet_mask is not None:
        outlet_mask = np.asarray(outlet_mask, dtype=bool)
        if outlet_mask.shape != stream.shape:
            raise ValueError("outlet_mask and water must have the same shape")
        escape |= outlet_mask & stream

    pools, pool_count = label_4conn(stream, key=y)
    trapped_pools = trapped_cells = 0
    pool_sizes = np.zeros(0, dtype=np.int64)
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
        pool_sizes = (ends - starts).astype(np.int64)
        trapped_cells = int(pool_sizes[~pool_escapes].sum())

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
        # ขนาดแอ่ง = ความยาวช่วงที่ผิวน้ำอยู่ระดับเดียวกัน
        #
        # นี่คือตัวเลขที่ตรงกับอาการ "น้ำไหลอยู่ ๆ ก็ลดลง 1 บล็อก" ที่สุด
        # ลำธารจริงเป็นขั้นบันได: แอ่งราบยาวสลับจุดตก ถ้าแอ่งส่วนใหญ่มีไม่กี่
        # cell แปลว่าผิวน้ำลดทีละบล็อกแทบทุกก้าว ซึ่งไม่มีในธรรมชาติและสะดุดตา
        # มากในเกม เพราะน้ำเป็นบล็อกเต็มจึงเห็นขั้นชัดทุกขั้น
        "pool_size_median": (
            int(np.median(pool_sizes)) if pool_sizes.size else 0
        ),
        "pool_size_p90": (
            int(np.percentile(pool_sizes, 90)) if pool_sizes.size else 0
        ),
        "tiny_pool_share": (
            round(float((pool_sizes <= 2).mean()), 4) if pool_sizes.size else 0.0
        ),
        "width_median": int(np.median(runs)) if runs.size else 0,
        "width_mean": round(float(runs.mean()), 2) if runs.size else 0.0,
        "width_1_share": round(float((runs == 1).mean()), 4) if runs.size else 0.0,
        "step_flat_share": round(float((steps == 0).mean()), 4) if steps.size else 0.0,
        "step_1_share": round(float((steps == 1).mean()), 4) if steps.size else 0.0,
        "step_2plus": int((steps >= 2).sum()) if steps.size else 0,
        "step_max": int(steps.max()) if steps.size else 0,
    }


# --------------------------------------------------------------- น้ำตก
def waterfall_metrics(water, y, minimum_drop=2, top_n=8, declared_top=None):
    """วัดม่านน้ำที่ painter จะเขียนจริง

    `declared_top` คือ `waterfall_top_y` จาก hydrology — **ต้องส่งมาเสมอเมื่อมี**
    เพราะเป็นตัวเดียวที่ paint_surface ใช้เติมม่านน้ำ การอนุมานเอาเองจากทุก DEM
    step ที่ต่างกัน >= minimum_drop รายงานเกินความจริงเกือบสามเท่า (วัดได้
    6,677 คอลัมน์ เทียบกับ 1,350 ที่ประกาศไว้จริง) แล้วเราจะไล่แก้ของที่ไม่มีอยู่

    เมื่อไม่มี (pipeline เดิมไม่มี waterfall product) จึงค่อยอนุมานจาก DEM step
    """
    water = np.asarray(water, dtype=bool)
    y = np.asarray(y, dtype=np.int32)
    if water.shape != y.shape:
        raise ValueError("water and y must have the same shape")

    if declared_top is not None:
        declared_top = np.asarray(declared_top)
        if declared_top.shape != y.shape:
            raise ValueError("declared_top and y must have the same shape")
        has_curtain = declared_top != np.iinfo(np.int16).min
        curtain_depth = np.where(
            has_curtain, declared_top.astype(np.int32) - y, 0
        )
        np.maximum(curtain_depth, 0, out=curtain_depth)
        edges = int(has_curtain.sum())
        max_drop = int(curtain_depth.max()) if curtain_depth.size else 0
    else:
        curtain_top = y.copy()
        edges = 0
        max_drop = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            drop = y[src] - y[dst]
            falling = water[dst] & water[src] & (drop >= int(minimum_drop))
            edges += int(falling.sum())
            if falling.any():
                max_drop = max(max_drop, int(drop[falling].max()))
                target = curtain_top[dst]
                target[falling] = np.maximum(target[falling], y[src][falling])
        curtain_depth = np.maximum(curtain_top - y, 0)
    zz, xx = np.nonzero(curtain_depth > 0)
    if zz.size:
        order = np.argsort(curtain_depth[zz, xx], kind="stable")[::-1]
        largest = [
            {
                "x": int(xx[i]),
                "z": int(zz[i]),
                "drop": int(curtain_depth[zz[i], xx[i]]),
                "bottom_y": int(y[zz[i], xx[i]]),
                "top_y": int(y[zz[i], xx[i]] + curtain_depth[zz[i], xx[i]]),
            }
            for i in order[:max(0, int(top_n))]
        ]
    else:
        largest = []

    # น้ำตกจริงขวางลำน้ำเป็นแนว ไม่ใช่จุดเดี่ยวกลางสาย — WATER_REDESIGN ตั้ง gate
    # ไว้ว่าต้องเป็น cluster ที่มี lip/curtain/plunge pool  สัดส่วนจุดเดี่ยวจึงเป็น
    # ตัวชี้ว่ากำลังเติมม่านน้ำจาก DEM step แทนที่จะเป็น feature จริง
    clusters, cluster_count = label_8conn(curtain_depth > 0)
    singleton_share = 0.0
    largest_cluster = 0
    if cluster_count:
        sizes = np.bincount(clusters.ravel())[1:]
        singleton_share = round(float((sizes == 1).mean()), 4)
        largest_cluster = int(sizes.max())
    return {
        "edges": edges,
        "columns": int((curtain_depth > 0).sum()),
        "blocks": int(curtain_depth.sum()),
        "max_drop": max_drop,
        "clusters": int(cluster_count),
        "singleton_share": singleton_share,
        "largest_cluster": largest_cluster,
        "largest": largest,
    }


# ----------------------------------------------------------------- ตลิ่ง
def bank_metrics(water, depth, shore_width=4, tile_size=2048):
    """สัดส่วนตลิ่งที่ไม่ได้รับการแต่งเลย

    ใช้ probability ชุดเดียวกับ painter เพื่อให้ metric วัดสิ่งที่จะถูกเขียนจริง
    ห้ามเขียนสูตรซ้ำที่นี่ — ต้องเรียก `S.shore_probabilities` ตัวเดียวกับ paint

    ทำทีละ tile + pad เท่ารัศมีของกฎ: บนแผนที่เต็ม shore_probabilities กาง
    float32 สองชุด (400 MB ต่อชุด) บวก int16 อีกสองชุดต่อรอบ dilate รวม ~1.5 GB
    ทั้งที่ค่าของ cell ใด ๆ ขึ้นกับ input ในรัศมี `reach` เท่านั้น การแบ่ง tile
    จึงให้ผลเท่ากันทุกประการ (เหมือน terrain_shape.py)
    """
    water = np.asarray(water, dtype=bool)
    depth = np.asarray(depth)
    reach = max(shore_width, int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12)))
    bank = expand_cardinal(water, shore_width) & ~water
    total = int(bank.sum())
    untreated = 0
    height, width = water.shape
    for z0 in range(0, height, tile_size):
        z1 = min(height, z0 + tile_size)
        pz0, pz1 = max(0, z0 - reach), min(height, z1 + reach)
        for x0 in range(0, width, tile_size):
            x1 = min(width, x0 + tile_size)
            core = bank[z0:z1, x0:x1]
            if not core.any():
                continue
            px0, px1 = max(0, x0 - reach), min(width, x1 + reach)
            lake_p, bank_p = S.shore_probabilities(
                water[pz0:pz1, px0:px1], depth[pz0:pz1, px0:px1],
                shore_width=shore_width, search_blocks=reach,
            )
            inner = np.s_[z0 - pz0:z1 - pz0, x0 - px0:x1 - px0]
            treated = np.maximum(lake_p[inner], bank_p[inner]) > 0.0
            untreated += int((core & ~treated).sum())
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


def load_inputs(patch=None, hydrology_root=None):
    """โหลด input — สไลซ์ *ตอนโหลด* ไม่ใช่โหลดเต็มแผนที่แล้วค่อยตัดทีหลัง

    เดิม `--patch` จ่าย RAM เท่า fullscale (~800 MB) เพราะ `np.load()` ทั้งไฟล์
    ครบทุกตัวก่อน แล้วค่อย `a[z0:z1, x0:x1]` ตอนท้าย mmap ทำให้แปลงเล็กอ่าน
    เฉพาะหน้าที่แตะจริง และ fullscale ไม่ต้อง resident ทั้งก้อน

    `heightmap.png` กับ `landcover.npz` ใช้เฉพาะสถิติพืชพรรณ ซึ่งมีเฉพาะโหมด
    `--patch` fullscale จึงคืน None สองตัวนี้แทนการจ่าย ~300 MB ทิ้ง (เดิม
    fullscale decode PNG 200 MB มาเพื่ออ่าน `.shape` อย่างเดียว)

    patch ที่ส่งเข้ามาต้อง clamp กับขอบแผนที่มาแล้ว — PIL `crop()` เติมขอบด้วย
    ศูนย์แทนที่จะตัดให้เหมือน numpy ถ้าไม่ clamp รูปทรงจะไม่ตรงกันเงียบ ๆ

    hydrology_root ต้องชี้ไปที่ product ชุดเดียวกับที่ build/paint ใช้เขียนโลก
    ไม่งั้นรายงานทั้งฉบับจะอธิบายคนละโลกกับที่เห็นในเกม
    """
    def product(name, required=True):
        path = H.water_product_path(name, hydrology_root, HERE)
        if path is None or not os.path.exists(path):
            if not required:
                return None
            command = (
                "hydrology_shape.py --global" if hydrology_root
                else "make_water.py แล้ว make_water_levels.py"
            )
            raise SystemExit(f"ต้องมี {path or name} ก่อน — รัน {command}")
        return np.load(path, mmap_mode="r")

    def window(array):
        if array is None:
            return None
        if patch is None:
            return array
        x0, x1, z0, z1 = patch
        return np.asarray(array[z0:z1, x0:x1])

    wm = window(product("water_mask"))
    wd = window(product("depth"))
    wy = window(product("surface_y"))
    # outlet_mask เป็นแนวคิดของ pipeline เดิมเท่านั้น ชุด hydrology ไม่มีเพราะ
    # ลำธารมี directed profile ที่ไหลลงอยู่แล้ว
    wo = window(product("outlet_mask", required=not hydrology_root))
    # ม่านน้ำที่ paint จะเขียนจริง — ต้องวัดตัวนี้ ไม่ใช่อนุมานจาก DEM step
    wf = window(product("waterfall_top_y", required=False))

    hm = lc = None
    if patch is not None:
        x0, x1, z0, z1 = patch
        with Image.open(os.path.join(HERE, "heightmap.png")) as image:
            hm = np.asarray(image.crop((x0, z0, x1, z1)))
        lc = window(np.load(os.path.join(HERE, "landcover.npz"))["landcover"])
        if hm.shape != wm.shape:
            raise SystemExit(
                f"heightmap crop {hm.shape} ไม่ตรงกับ water_mask {wm.shape}"
            )
    return hm, lc, wm, wd, wy, wo, wf


def main():
    use_utf8_stdout()
    hydrology_root = H.resolve_hydrology_root(sys.argv)
    patch = None
    if "--patch" in sys.argv:
        i = sys.argv.index("--patch")
        cx, cz, size = (int(v) for v in sys.argv[i + 1:i + 4])
        half = size // 2
        # clamp ทั้งสองด้าน — load_inputs ใช้ PIL crop ซึ่งเติมขอบด้วยศูนย์
        # แทนที่จะตัดให้สั้นลงเหมือน numpy slicing
        patch = (
            max(0, cx - half), min(C.GRID, cx + half),
            max(0, cz - half), min(C.GRID, cz + half),
        )

    hm, lc, wm, wd, wy, wo, wf = load_inputs(
        patch, hydrology_root=hydrology_root
    )
    y = surface_y(patch=patch, hydrology_root=hydrology_root)
    water = wm.astype(bool)
    y[water] = wy[water]
    scope = (
        f"patch x {patch[0]}..{patch[1]} z {patch[2]}..{patch[3]}"
        if patch else f"fullscale {wm.shape[0]}x{wm.shape[1]}"
    )
    # ต้องบอกเสมอว่าวัด product ชุดไหน — ตัวเลขจากคนละชุดเทียบกันไม่ได้
    products = (
        f"hydrology_global ({os.path.basename(hydrology_root)})"
        if hydrology_root else "legacy (make_water + make_water_levels)"
    )

    report = {
        "scope": scope,
        "products": products,
        "water_cells": int(water.sum()),
        "water_share": round(float(water.mean()), 5),
        "lake": lake_metrics(water, wd, y),
        "stream": stream_metrics(water, wd, y, outlet_mask=wo),
        "waterfall": waterfall_metrics(water, y, declared_top=wf),
        "bank": bank_metrics(water, wd),
        "biome": biome_coverage(y, water),
    }

    if patch:
        meta = S.load_meta()
        lo, hi = meta["elev_min_m"], meta["elev_max_m"]
        elev = lo + hm.T.astype(np.float32) / 65535.0 * (hi - lo)
        # เศษความสูงมาจาก terrain_sub.npy เท่านั้น การแปลงจาก heightmap เองที่นี่
        # ให้คนละคำตอบกับที่ paint ใช้ แล้วสถิติพืชพรรณจะวัดผิวดินที่ไม่มีอยู่
        # (terrain_sub เป็นของ terrain_shape ไม่ใช่ของ hydrology จึงอยู่ที่ราก)
        tsub_path = os.path.join(HERE, "terrain_sub.npy")
        if not os.path.exists(tsub_path):
            raise SystemExit("ไม่พบ terrain_sub.npy — รัน terrain_shape.py ก่อน")
        x0, x1, z0, z1 = patch
        terrain_offset = np.asarray(
            np.load(tsub_path, mmap_mode="r")[z0:z1, x0:x1], dtype=np.float32
        ).T / 127.0
        report["vegetation"] = vegetation_metrics(
            elev, S.apply_water_mask(lc, wm).T,
            terrain_offset=terrain_offset,
            x0=patch[0], z0=patch[2],
        )

    print(f"=== {scope} ===")
    print(f"product ที่วัด: {products}")
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
        print(f"   ขนาดแอ่ง: median {stream['pool_size_median']} cells "
              f"| p90 {stream['pool_size_p90']} | แอ่งจิ๋ว <=2 cells "
              f"{stream['tiny_pool_share']:.1%}"
              "   <-- สูง = น้ำลดทีละบล็อกแทบทุกก้าว")

    waterfall = report["waterfall"]
    source = "จาก waterfall_top_y" if wf is not None else "อนุมานจาก DEM step"
    print(f"\n-- น้ำตก ({source}): ม่านน้ำ {waterfall['columns']:,} คอลัมน์ / "
          f"{waterfall['blocks']:,} บล็อก | สูงสุด {waterfall['max_drop']} บล็อก")
    print(f"   กลุ่ม {waterfall['clusters']:,} | จุดเดี่ยว "
          f"{waterfall['singleton_share']:.1%} | กลุ่มใหญ่สุด "
          f"{waterfall['largest_cluster']} cells"
          "   <-- จุดเดี่ยวสูง = เติมม่านจาก DEM step ไม่ใช่ feature จริง")
    if waterfall["largest"]:
        print("   จุดตรวจสูงสุด: " + " | ".join(
            f"({item['x']},{item['z']}) {item['drop']} บล็อก "
            f"Y {item['top_y']}→{item['bottom_y']}"
            for item in waterfall["largest"][:5]
        ))

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
