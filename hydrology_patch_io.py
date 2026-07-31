"""Small, dependency-light helpers for testing a hydrology patch in-world."""

import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# ชื่อไฟล์ของ product ชุดเดิม (make_water + make_water_levels) กับชุดใหม่
# (hydrology_shape --global) ไม่ตรงกัน ตารางนี้ต้องเป็น **ที่เดียว** ในโปรเจกต์
#
# เคยกระจายอยู่ใน paint_surface อย่างเดียว ผลคือ report_metrics / render_preview
# / render_view อ่านชุดเดิมต่อไปเงียบ ๆ ทั้งที่โลกถูกเขียนจากชุดใหม่ — ตัวเลข
# และภาพพรีวิวจึงอธิบายคนละโลกกับที่เห็นในเกม เป็นบั๊ก "แหล่งความจริงเดียว"
# รอบที่สองของไฟล์เดียวกัน (ดูตารางใน docs/PIPELINE.md)
LEGACY_PRODUCTS = {
    "terrain_y": "terrain_y.npy",
    "water_mask": "water_mask.npy",
    "depth": "water_depth.npy",
    "surface_y": "water_surface_y.npy",
    "standing_water_mask": "water_lake_mask.npy",
    "outlet_mask": "water_outlet_mask.npy",
}
GLOBAL_PRODUCTS = {
    "terrain_y": "terrain_y.npy",
    "water_mask": "water_mask.npy",
    "depth": "depth.npy",
    "surface_y": "surface_y.npy",
    "standing_water_mask": "standing_water_mask.npy",
    "waterway_mask": "waterway_mask.npy",
    "water_kind": "water_kind.npy",
    "waterfall_top_y": "waterfall_top_y.npy",
    "waterfall_lip_mask": "waterfall_lip_mask.npy",
    "waterfall_pool_mask": "waterfall_pool_mask.npy",
    "affected_chunks": "affected_chunks.npy",
}


def water_product_path(name, hydrology_root=None, here=None):
    """คืน path ของ product หนึ่งตัวตามชุดที่ใช้อยู่

    คืน None เมื่อชุดนั้นไม่มี product นี้ — เช่น `outlet_mask` เป็นแนวคิดของ
    pipeline เดิมเท่านั้น ส่วน hydrology_global ไม่มีเพราะลำธารมี directed
    profile ที่ไหลลงอยู่แล้ว ผู้เรียกต้องรับมือกับ None ไม่ใช่เดาชื่อไฟล์เอง
    """
    table = GLOBAL_PRODUCTS if hydrology_root else LEGACY_PRODUCTS
    filename = table.get(name)
    if filename is None:
        return None
    return os.path.join(hydrology_root or here or HERE, filename)


def resolve_hydrology_root(argv, flag="--hydrology-root"):
    """อ่าน --hydrology-root จาก argv แล้วตรวจว่าโฟลเดอร์นั้นเป็นของจริง"""
    if flag not in argv:
        return None
    index = argv.index(flag)
    if index + 1 >= len(argv):
        raise SystemExit(f"{flag} requires a directory")
    root = os.path.abspath(argv[index + 1])
    if not os.path.isfile(os.path.join(root, "manifest.json")):
        raise SystemExit(
            f"{root} ไม่ใช่ hydrology root (ไม่พบ manifest.json) — "
            "สร้างด้วย hydrology_shape.py --global"
        )
    return root


def load_hydrology_patch(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    patch = np.load(path, allow_pickle=False)
    required = {
        "bounds", "terrain_y", "water_mask", "standing_water_mask",
        "surface_y", "depth",
    }
    missing = sorted(required.difference(patch.files))
    if missing:
        patch.close()
        raise ValueError(
            "hydrology patch is missing: " + ", ".join(missing)
        )
    bounds = np.asarray(patch["bounds"], dtype=np.int64)
    if bounds.shape != (4,):
        patch.close()
        raise ValueError("hydrology patch bounds must be [x0, x1, z0, z1]")
    x0, x1, z0, z1 = map(int, bounds)
    expected = (z1 - z0, x1 - x0)
    for key in required - {"bounds"}:
        if patch[key].shape != expected:
            patch.close()
            raise ValueError(
                f"hydrology patch {key} has shape {patch[key].shape}; "
                f"expected {expected}"
            )
    return patch


def overlay_window(base, patch, key, x0, x1, z0, z1):
    """Overlay one [z, x] patch field on an arbitrary [z, x] window."""
    base = np.asarray(base)
    if base.shape != (z1 - z0, x1 - x0):
        raise ValueError("base shape must match the requested window")
    px0, px1, pz0, pz1 = map(int, patch["bounds"])
    ox0, ox1 = max(x0, px0), min(x1, px1)
    oz0, oz1 = max(z0, pz0), min(z1, pz1)
    if ox0 >= ox1 or oz0 >= oz1:
        return base
    result = base.copy()
    result[oz0 - z0:oz1 - z0, ox0 - x0:ox1 - x0] = patch[key][
        oz0 - pz0:oz1 - pz0, ox0 - px0:ox1 - px0
    ]
    return result

