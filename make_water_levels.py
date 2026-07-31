"""สร้างระดับผิวน้ำชุดเดียวทั้งแผนที่จาก terrain_y และ bathymetry

รันหลัง terrain_shape.py เพราะระดับน้ำต้องอ้างอิงผิวดินจริงที่ build_terrain
ใช้ ไม่ใช่คำนวณจาก heightmap ซ้ำ ผลลัพธ์เก็บเฉพาะคอลัมน์น้ำ; คอลัมน์บกใช้
sentinel ของ int16 เพื่อจับการใช้ผิดประเภทได้ทันที
"""

import os
import heapq

import numpy as np
from scipy import ndimage

import config as C
from pipeline_progress import use_utf8_stdout


HERE = os.path.dirname(os.path.abspath(__file__))
UNRESOLVED = np.iinfo(np.int16).min


def label_cardinal(mask):
    """Label 4-connected components, returning int32 labels and count."""
    structure = np.asarray(
        [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8
    )
    return ndimage.label(np.asarray(mask, dtype=bool), structure=structure)


def expand_cardinal(mask, steps):
    expanded = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(0, int(steps))):
        nxt = expanded.copy()
        nxt[1:] |= expanded[:-1]
        nxt[:-1] |= expanded[1:]
        nxt[:, 1:] |= expanded[:, :-1]
        nxt[:, :-1] |= expanded[:, 1:]
        expanded = nxt
    return expanded


def component_mode_levels(base_y, labels, count, level_offset=0,
                          row_batch=512):
    """Return the most common terrain Y for every labelled component."""
    base_y = np.asarray(base_y, dtype=np.int32)
    labels = np.asarray(labels, dtype=np.int32)
    if base_y.shape != labels.shape:
        raise ValueError("base_y and labels must have the same shape")
    if count == 0:
        return np.zeros(1, dtype=np.int16)

    active = labels > 0
    y_min = int(base_y[active].min())
    y_max = int(base_y[active].max())
    span = y_max - y_min + 1
    counts = np.zeros((count + 1, span), dtype=np.uint32)
    for z0 in range(0, base_y.shape[0], row_batch):
        z1 = min(base_y.shape[0], z0 + row_batch)
        lab = labels[z0:z1].ravel()
        wet = lab > 0
        if not wet.any():
            continue
        yy = base_y[z0:z1].ravel()[wet] - y_min
        np.add.at(counts, (lab[wet], yy), 1)

    levels = np.argmax(counts, axis=1).astype(np.int32) + y_min
    levels[0] = 0
    levels[1:] += int(level_offset)
    if levels.min() <= UNRESOLVED or levels.max() > np.iinfo(np.int16).max:
        raise ValueError("component levels do not fit in int16")
    return levels.astype(np.int16)


def global_water_surface_levels(base_y, water, water_depth,
                                level_offset=None, min_core_depth=None,
                                shelf_search=None):
    """Flatten lake cores globally and rejoin their shallow shelves.

    Shallow streams farther than ``shelf_search`` from a lake core retain the
    terrain profile. The returned lake mask includes the propagated shelf.
    """
    base_y = np.asarray(base_y, dtype=np.int16)
    water = np.asarray(water, dtype=bool)
    water_depth = np.asarray(water_depth)
    if base_y.shape != water.shape or water.shape != water_depth.shape:
        raise ValueError("base_y, water, and water_depth must have the same shape")
    if level_offset is None:
        level_offset = int(getattr(C, "LAKE_LEVEL_OFFSET", 0))
    if min_core_depth is None:
        min_core_depth = int(getattr(C, "LAKE_MIN_DEPTH_FOR_OFFSET", 3))
    if shelf_search is None:
        shelf_search = int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    flat_shelf = min(
        int(shelf_search),
        int(getattr(C, "LAKE_LEVEL_FLAT_SHELF_BLOCKS", 4)),
    )

    core = water & (water_depth >= int(min_core_depth))
    labels, count = label_cardinal(core)
    levels = component_mode_levels(
        base_y, labels, count, level_offset=level_offset
    )

    target = np.full(base_y.shape, UNRESOLVED, dtype=np.int16)
    target[core] = levels[labels[core]]
    neighbour = np.empty_like(target)
    for distance in range(max(0, int(shelf_search))):
        neighbour.fill(UNRESOLVED)
        # out= ทุกบรรทัด — np.maximum ปกติสร้าง array เต็มแผนที่ทิ้งทุกครั้ง
        # (int16 x 100M cell = 200 MB ต่อบรรทัด x 4 บรรทัด x shelf_search รอบ)
        np.maximum(neighbour[1:], target[:-1], out=neighbour[1:])
        np.maximum(neighbour[:-1], target[1:], out=neighbour[:-1])
        np.maximum(neighbour[:, 1:], target[:, :-1], out=neighbour[:, 1:])
        np.maximum(neighbour[:, :-1], target[:, 1:], out=neighbour[:, :-1])
        fill = target == UNRESOLVED
        fill &= water
        fill &= neighbour != UNRESOLVED
        if not fill.any():
            break
        # คำนวณเฉพาะ cell ที่จะเขียนจริง ไม่ใช่ทั้งแผนที่แล้วค่อยเลือก
        #
        # `fill` คือ shelf บาง ๆ รอบ core (หลักหมื่นถึงแสน cell) แต่เดิมกาง
        # base32/neighbour32/close/ramped/candidate เป็น int32 เต็มแผนที่ =
        # ~1.7 GB ต่อรอบ เพื่อใช้แค่ candidate[fill] ทุก operation เป็น
        # elementwise ล้วน การ index ก่อนจึงให้ผลเท่ากันทุกประการ
        near = neighbour[fill].astype(np.int32)
        ground = base_y[fill].astype(np.int32)
        ramped = np.maximum(near - 1, np.minimum(ground, near + 1))
        candidate = (
            np.where(np.abs(ground - near) <= 1, near, ramped)
            if distance < flat_shelf
            else ramped
        )
        target[fill] = candidate.astype(np.int16)

    lake = target != UNRESOLVED
    result = np.full(base_y.shape, UNRESOLVED, dtype=np.int16)
    result[water] = base_y[water]
    result[lake] = target[lake]
    return result, lake


def terminal_stream_outlets(levels, water, lake_mask, ground_y,
                            stream_mask=None):
    """Choose one explicit spill point for each terminal flat stream pool."""
    levels = np.asarray(levels, dtype=np.int16)
    water = np.asarray(water, dtype=bool)
    stream = (
        water & ~np.asarray(lake_mask, dtype=bool)
        if stream_mask is None else np.asarray(stream_mask, dtype=bool)
    )
    ground_y = np.asarray(ground_y, dtype=np.int32)

    lower_wet = np.zeros(stream.shape, dtype=bool)
    dry_lower = np.zeros(stream.shape, dtype=bool)
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        lower_wet[dst] |= water[src] & (levels[src] < levels[dst])
        dry_lower[dst] |= ~water[src] & (ground_y[src] < levels[dst])

    sink = stream & ~lower_wet
    candidate = sink & dry_lower
    candidate[0] |= sink[0]
    candidate[-1] |= sink[-1]
    candidate[:, 0] |= sink[:, 0]
    candidate[:, -1] |= sink[:, -1]

    pools, count = label_cardinal(sink)
    outlets = np.zeros(stream.shape, dtype=bool)
    if count == 0:
        return outlets
    has_candidate = np.bincount(
        pools[candidate], minlength=count + 1
    ).astype(bool)
    has_candidate[0] = False
    score = np.where(candidate, 0, 1).astype(np.uint8)
    positions = ndimage.minimum_position(
        score, labels=pools, index=np.flatnonzero(has_candidate)
    )
    for z, x in positions:
        outlets[int(z), int(x)] = True
    return outlets


def condition_stream_levels(levels, water, lake_mask, ground_y=None,
                            max_raise=2, stream_mask=None):
    """Fill internal stream pits toward one deterministic outlet per network.

    The OSM raster contains many finite waterway components whose line stops
    before reaching another mapped water body (often a culvert or omitted
    continuation). Each connected shallow network therefore receives one
    provisional outlet at its lowest original cell. A capped priority flood
    raises small internal DEM pits but never lowers terrain, changes the water
    footprint, or raises a channel by more than ``max_raise`` blocks. Terminal
    pools beside lower dry terrain become explicit OSM spill/culvert outlets.
    """
    levels = np.asarray(levels, dtype=np.int16)
    water = np.asarray(water, dtype=bool)
    lake_mask = np.asarray(lake_mask, dtype=bool)
    if levels.shape != water.shape or water.shape != lake_mask.shape:
        raise ValueError("levels, water, and lake_mask must have the same shape")
    if ground_y is None:
        ground_y = levels
    ground_y = np.asarray(ground_y, dtype=np.int32)
    if ground_y.shape != water.shape:
        raise ValueError("ground_y and water must have the same shape")

    stream = (
        water & ~lake_mask
        if stream_mask is None else np.asarray(stream_mask, dtype=bool)
    )
    if stream.shape != water.shape or (stream & ~water).any():
        raise ValueError("stream_mask must be a subset of water")
    labels, count = label_cardinal(stream)
    seeds = np.zeros(stream.shape, dtype=bool)
    if count == 0:
        return levels.copy(), seeds

    positions = ndimage.minimum_position(
        levels, labels=labels, index=np.arange(1, count + 1)
    )
    heap = []
    for z, x in positions:
        z, x = int(z), int(x)
        seeds[z, x] = True
        heapq.heappush(heap, (int(levels[z, x]), z, x))

    result = levels.copy()
    visited = np.zeros(stream.shape, dtype=bool)
    visited[seeds] = True
    height, width = stream.shape
    while heap:
        level, z, x = heapq.heappop(heap)
        for nz, nx in ((z - 1, x), (z + 1, x), (z, x - 1), (z, x + 1)):
            if (
                nz < 0 or nz >= height or nx < 0 or nx >= width
                or visited[nz, nx] or not stream[nz, nx]
            ):
                continue
            visited[nz, nx] = True
            original = int(levels[nz, nx])
            filled = min(max(level, original), original + int(max_raise))
            result[nz, nx] = filled
            heapq.heappush(heap, (filled, nz, nx))

    if not visited[stream].all():
        raise RuntimeError("stream conditioning left unreachable cells")
    outlets = terminal_stream_outlets(
        result, water, lake_mask, ground_y, stream_mask=stream
    )
    return result, outlets


def validate_water_levels(levels, water, outlets=None, lake_mask=None):
    levels = np.asarray(levels)
    water = np.asarray(water, dtype=bool)
    if levels.shape != water.shape:
        raise ValueError("levels and water must have the same shape")
    result = {
        "water_without_level": int((water & (levels == UNRESOLVED)).sum()),
        "land_with_level": int((~water & (levels != UNRESOLVED)).sum()),
    }
    if outlets is not None:
        outlets = np.asarray(outlets, dtype=bool)
        if outlets.shape != water.shape:
            raise ValueError("outlets and water must have the same shape")
        invalid = ~water
        if lake_mask is not None:
            lake_mask = np.asarray(lake_mask, dtype=bool)
            if lake_mask.shape != water.shape:
                raise ValueError("lake_mask and water must have the same shape")
            invalid |= lake_mask
        result["invalid_outlets"] = int((outlets & invalid).sum())
    return result


def main():
    use_utf8_stdout()
    paths = {
        name: os.path.join(HERE, name)
        for name in ("terrain_y.npy", "water_mask.npy", "water_depth.npy")
    }
    missing = [name for name, path in paths.items() if not os.path.exists(path)]
    if missing:
        raise SystemExit(
            "ไม่พบ " + ", ".join(missing)
            + " — รัน make_water.py แล้ว terrain_shape.py ก่อน"
        )

    terrain_y = np.load(paths["terrain_y.npy"], mmap_mode="r")
    water = np.load(paths["water_mask.npy"], mmap_mode="r")
    depth = np.load(paths["water_depth.npy"], mmap_mode="r")
    if terrain_y.shape != water.shape or water.shape != depth.shape:
        raise SystemExit("terrain_y / water_mask / water_depth มีขนาดไม่ตรงกัน")

    print(f"คำนวณระดับผิวน้ำ global {terrain_y.shape[1]}x{terrain_y.shape[0]} ...")
    levels, lake = global_water_surface_levels(terrain_y, water, depth)
    original_levels = levels.copy()
    core = np.asarray(water, dtype=bool) & (
        np.asarray(depth) >= int(getattr(C, "LAKE_MIN_DEPTH_FOR_OFFSET", 3))
    )
    stream = np.asarray(water, dtype=bool) & ~expand_cardinal(
        core, int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    )
    levels, outlets = condition_stream_levels(
        levels, water, lake, ground_y=terrain_y, stream_mask=stream
    )
    check = validate_water_levels(levels, water, outlets, lake)
    if any(check.values()):
        raise SystemExit(f"ระดับน้ำไม่สมบูรณ์: {check}")

    np.save(os.path.join(HERE, "water_surface_y.npy"), levels)
    np.save(os.path.join(HERE, "water_lake_mask.npy"), lake)
    np.save(os.path.join(HERE, "water_outlet_mask.npy"), outlets)
    print(
        f"บันทึก water_surface_y.npy + water_lake_mask.npy + "
        f"water_outlet_mask.npy | lake core {int(core.sum()):,} | "
        f"shelf {int((lake & ~core).sum()):,} | "
        f"stream outlets {int(outlets.sum()):,} | "
        f"raised {int((levels > original_levels).sum()):,} cells "
        f"(max {int((levels.astype(np.int32) - original_levels).max())} blocks)"
    )


if __name__ == "__main__":
    main()
