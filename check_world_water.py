"""Verify a painted hydrology patch by reading every water column from the world.

This is the final acceptance layer after ``build_terrain`` and ``paint_surface``.
It deliberately reads region files instead of trusting the arrays that were
supposed to be written.

Use:
    python check_world_water.py --at 6448 2304 --size 256 \
        --hydrology-patch golden_patches/hydrology_patch_x6448_z2304_384.npz
"""

import sys
from collections import Counter

import numpy as np
from scipy import ndimage

import config as C
import hydrology_patch_io as H
from pipeline_progress import use_utf8_stdout


UNRESOLVED = np.iinfo(np.int16).min
# These blocks occupy a water cell intentionally.  Counting them as missing
# water produced false failures in the first full readback.
FLUID_OCCUPANTS = {"water", "seagrass", "tall_seagrass"}


def material_spatial_metrics(materials, mask):
    """Summarize dominance and connected patches in a 2-D bed-material grid."""
    materials = np.asarray(materials, dtype=object)
    mask = np.asarray(mask, dtype=bool)
    if materials.shape != mask.shape:
        raise ValueError("materials and mask must have the same shape")
    total_cells = int(mask.sum())
    if not total_cells:
        return {
            "cells": 0, "counts": {}, "same_neighbour_share": 0.0,
            "largest_components": {},
        }

    counts = Counter(str(value) for value in materials[mask])
    same = neighbours = 0
    for left, right, valid in (
        (materials[:, :-1], materials[:, 1:], mask[:, :-1] & mask[:, 1:]),
        (materials[:-1, :], materials[1:, :], mask[:-1, :] & mask[1:, :]),
    ):
        same += int(((left == right) & valid).sum())
        neighbours += int(valid.sum())

    largest = {}
    structure = ndimage.generate_binary_structure(2, 1)
    for name in counts:
        labels, component_count = ndimage.label(
            mask & (materials == name), structure=structure
        )
        sizes = np.bincount(labels.ravel())[1:]
        largest[name] = {
            "cells": int(sizes.max(initial=0)),
            "components": int(component_count),
        }
    return {
        "cells": total_cells,
        "counts": dict(counts),
        "same_neighbour_share": float(same / neighbours) if neighbours else 0.0,
        "largest_components": largest,
    }


def _block_name(level, chunk, wx, y, wz):
    block_id = int(np.asarray(chunk.blocks[wx & 15, y, wz & 15]).ravel()[0])
    return level.block_palette[block_id]


def verify_patch_world(patch_path, cx, cz, size, world_path=None):
    """Read all active-water columns in ``size`` and compare them with a patch."""
    import amulet

    cx, cz, size = int(cx), int(cz), int(size)
    if size <= 0 or size % 2:
        raise ValueError("size must be a positive even number")
    half = size // 2
    x0, x1, z0, z1 = cx - half, cx + half, cz - half, cz + half
    patch = H.load_hydrology_patch(patch_path)
    try:
        px0, px1, pz0, pz1 = map(int, patch["bounds"])
        if not (px0 <= x0 < x1 <= px1 and pz0 <= z0 < z1 <= pz1):
            raise ValueError(
                f"requested box {(x0, x1, z0, z1)} is outside patch "
                f"{(px0, px1, pz0, pz1)}"
            )
        sx = slice(x0 - px0, x1 - px0)
        sz = slice(z0 - pz0, z1 - pz0)
        water = np.asarray(patch["water_mask"][sz, sx], dtype=bool)
        stream = np.asarray(patch["waterway_mask"][sz, sx], dtype=bool)
        standing = np.asarray(
            patch["standing_water_mask"][sz, sx], dtype=bool
        )
        surface = np.asarray(patch["surface_y"][sz, sx], dtype=np.int32)
        depth = np.asarray(patch["depth"][sz, sx], dtype=np.int32)
        top = surface.copy()
        if "waterfall_top_y" in patch.files:
            waterfall_top = np.asarray(
                patch["waterfall_top_y"][sz, sx], dtype=np.int32
            )
            top = np.maximum(top, waterfall_top)
    finally:
        patch.close()

    active = water & (depth > 0)
    materials = np.full(active.shape, "", dtype=object)
    stats = {
        "columns": int(active.sum()),
        "wrong_columns": 0,
        "missing_occupants": 0,
        "fluid_at_bed": 0,
        "water_above_top": 0,
        "non_source_water": 0,
        "plant_blocks": 0,
        "max_expected_run": 0,
        "max_actual_run": 0,
    }

    level = amulet.load_level(world_path or C.WORLD_PATH)
    chunks = {}
    try:
        for lz, lx in np.argwhere(active):
            wx, wz = x0 + int(lx), z0 + int(lz)
            key = (wx >> 4, wz >> 4)
            chunk = chunks.get(key)
            if chunk is None:
                chunk = level.get_chunk(key[0], key[1], C.DIMENSION)
                chunks[key] = chunk

            bed = int(surface[lz, lx] - depth[lz, lx])
            high = int(top[lz, lx])
            expected_run = high - bed
            stats["max_expected_run"] = max(
                stats["max_expected_run"], expected_run
            )

            bed_block = _block_name(level, chunk, wx, bed, wz)
            materials[lz, lx] = str(bed_block.base_name)
            if bed_block.base_name in FLUID_OCCUPANTS:
                stats["fluid_at_bed"] += 1

            actual_run = 0
            missing = 0
            for y in range(bed + 1, high + 1):
                block = _block_name(level, chunk, wx, y, wz)
                name = str(block.base_name)
                if name not in FLUID_OCCUPANTS:
                    missing += 1
                    continue
                actual_run += 1
                if name != "water":
                    stats["plant_blocks"] += 1
                    continue
                level_property = getattr(block, "properties", {}).get("level")
                if level_property is not None:
                    value = str(level_property).strip('"')
                    if value not in ("0", "0b"):
                        stats["non_source_water"] += 1

            above = _block_name(level, chunk, wx, high + 1, wz)
            if above.base_name == "water":
                stats["water_above_top"] += 1
            if missing:
                stats["wrong_columns"] += 1
                stats["missing_occupants"] += missing
            stats["max_actual_run"] = max(stats["max_actual_run"], actual_run)
    finally:
        level.close()

    stats["stream_materials"] = material_spatial_metrics(
        materials, active & stream
    )
    stats["lake_materials"] = material_spatial_metrics(
        materials, active & standing
    )
    stats["hard_errors"] = int(
        stats["wrong_columns"]
        + stats["fluid_at_bed"]
        + stats["water_above_top"]
        + stats["non_source_water"]
    )
    return stats


def _print_materials(label, report):
    cells = report["cells"]
    if not cells:
        print(f"{label}: ไม่มีตัวอย่าง")
        return
    counts = Counter(report["counts"])
    shares = " | ".join(
        f"{name} {count / cells * 100:.1f}%"
        for name, count in counts.most_common()
    )
    print(
        f"{label} ({cells:,} cells): {shares}\n"
        f"    เพื่อนบ้านวัสดุเดียวกัน "
        f"{report['same_neighbour_share'] * 100:.1f}%"
    )


def main():
    use_utf8_stdout()
    argv = sys.argv
    for flag in ("--at", "--size", "--hydrology-patch"):
        if flag not in argv:
            raise SystemExit(f"ต้องระบุ {flag}")
    at = argv.index("--at")
    cx, cz = int(argv[at + 1]), int(argv[at + 2])
    size = int(argv[argv.index("--size") + 1])
    patch_path = argv[argv.index("--hydrology-patch") + 1]
    report = verify_patch_world(patch_path, cx, cz, size)

    print(f"=== world water readback ({cx}, {cz}) size {size} ===")
    print(
        f"columns {report['columns']:,} | wrong {report['wrong_columns']:,} | "
        f"missing {report['missing_occupants']:,} | bed fluid "
        f"{report['fluid_at_bed']:,} | above top {report['water_above_top']:,} | "
        f"non-source {report['non_source_water']:,} | plants "
        f"{report['plant_blocks']:,} | run "
        f"{report['max_actual_run']}/{report['max_expected_run']}"
    )
    _print_materials("ลำน้ำ", report["stream_materials"])
    _print_materials("ทะเลสาบ", report["lake_materials"])
    if report["hard_errors"]:
        raise SystemExit(
            f"ไม่ผ่าน world readback: {report['hard_errors']:,} hard errors"
        )
    print("ผ่าน world readback")


if __name__ == "__main__":
    main()
