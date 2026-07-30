"""ทาสีผิวดิน + ปลูกป่า + ตั้ง biome ลงบนภูมิประเทศที่ build_terrain.py สร้างไว้

ใช้:
    python paint_surface.py --patch 4200 7000 500    # แปลงทดลอง
    python paint_surface.py --patch 4200 7000 500 --terrain-only
                                                    # tune พื้น/น้ำ ไม่ปลูกพืชและต้นไม้
    python paint_surface.py --patch 4942 5610 2200 --lake-only
                                                    # ทดสอบเฉพาะก้นทะเลสาบ/น้ำ
    python paint_surface.py --patch 4200 7000 500 --vegetation-only
                                                    # tune ป่า/ทุ่ง: ล้างพืชเก่าแล้วปลูกใหม่
                                                    # ไม่แตะผิวดิน/น้ำ/หิมะ จึงเร็วกว่ามาก
    python paint_surface.py --patch ... --skip-entity-repair
                                                    # ไม่สแกน/แก้ entity regions หลังจบ

รีเซ็ตพื้นที่ที่เคยทาแล้ว: รัน build_terrain.py --patch <เดิม> ก่อน
(มันเขียนหินทับพร้อมเผื่อหัว 64 บล็อก จึงล้างต้นไม้/พืชเก่าไปในตัว)
ถ้าจะปรับแค่พืช/ต้นไม้ ใช้ --vegetation-only แทน — มันล้างเฉพาะชั้นเหนือผิวดิน
    python paint_surface.py                          # ทั้งแผนที่
    python paint_surface.py --resume                 # รันต่อจากที่ค้าง

ทำทีละ region (32x32 chunks) แล้ว save+purge เหมือน build_terrain.py เพื่อไม่ให้
amulet กอง history จน MemoryError
"""

import json
import os
import subprocess
import sys
import time

import amulet_nbt
import numpy as np
import amulet
from amulet.api.block import Block
from amulet.api.errors import ChunkDoesNotExist, ChunkLoadError
from PIL import Image

import biomes as B
import config as C
import ecology as E
import surface as S
import vegetation as V
import tree_schematics as TS
from paint_world import repair_entities
from pipeline_progress import (
    content_fingerprint,
    load_progress,
    load_progress_metadata,
    save_progress,
    save_progress_metadata,
    world_session_locked,
)

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK = 16
RSIZE = 32              # chunks ต่อ region
PAD = max(
    4,
    int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    + int(getattr(C, "LAKE_LEVEL_RELAX_PASSES", 4)),
)  # lake transition radius plus shallow-level relaxation
TREE_PAD = 4            # fallback เมื่อไม่มี schematic
# ต้องสูงกว่าต้นไม้ที่สูงสุดในแพ็ก เท่ากับ CLEAR_HEADROOM ของ build_terrain.py
# ไม่งั้น --vegetation-only จะเหลือยอดใบไม้เก่าลอยค้าง
VEGETATION_HEADROOM = 64
PAINT_PIPELINE_VERSION = "2026-07-27-supported-lakebed-v7"

LAKEBED_NAMES = (
    "stream", "sand", "gravel", "clay", "mud",
    "stone", "cobble", "calcite",
)
LAKEBED = {name: i for i, name in enumerate(LAKEBED_NAMES)}


def leaf_block(kind):
    # persistent=true จำเป็น ไม่งั้นใบไม้จะสลายตัวเมื่อเกิด block update
    return Block("minecraft", f"{kind}_leaves", {
        "persistent": amulet_nbt.StringTag("true"),
        "distance": amulet_nbt.StringTag("7"),
        "waterlogged": amulet_nbt.StringTag("false"),
    })


def log_block(kind):
    return Block("minecraft", f"{kind}_log", {"axis": amulet_nbt.StringTag("y")})


def schematic_tree_pad(schems, minimum=TREE_PAD):
    return max(
        [minimum]
        + [
            (int(t.get("width", 1)) + 1) // 2
            for variants in schems.values() for t in variants
        ]
    )


class Painter:
    def __init__(self, level):
        self.level = level
        self.ids = {}
        self.leaf_id = {k: level.block_palette.get_add_block(leaf_block(k))
                        for k in ("spruce", "oak", "birch")}
        self.log_id = {k: level.block_palette.get_add_block(log_block(k))
                       for k in ("spruce", "oak", "birch")}
        self.air = self.bid("air")
        self.dirt = self.bid("dirt")
        self.stone = self.bid("stone")
        self.snow_layer = level.block_palette.get_add_block(
            Block("minecraft", "snow", {"layers": amulet_nbt.StringTag("1")})
        )
        self.surface_ids = [self.bid(n) for n in S.BLOCK_NAMES]
        self.water = self.bid("water")
        # ก้นทะเลสาบ — ตื้นมีคลื่นซัดเหลือแต่กรวดหยาบ ลึกลงไปนิ่งจึงเป็นตะกอนละเอียด
        self.bed_shallow = [self.bid(n) for n in
                            ("gravel", "gravel", "sand", "cobblestone",
                             "coarse_dirt", "stone")]
        self.bed_deep = [self.bid(n) for n in
                         ("clay", "clay", "gravel", "sand", "dirt", "clay")]
        self.shore_cobble = self.bid("cobblestone")
        self.bed_sand = self.bid("sand")
        self.bed_gravel = self.bid("gravel")
        self.bed_clay = self.bid("clay")
        self.bed_mud = self.bid("mud")
        self.bed_stone = self.bid("stone")
        self.bed_calcite = self.bid("calcite")
        self.seagrass = self.bid("seagrass")
        self.tall_seagrass_lower = level.block_palette.get_add_block(Block(
            "minecraft", "tall_seagrass",
            {"half": amulet_nbt.StringTag("lower")},
        ))
        self.tall_seagrass_upper = level.block_palette.get_add_block(Block(
            "minecraft", "tall_seagrass",
            {"half": amulet_nbt.StringTag("upper")},
        ))
        self.lily = self.bid("lily_pad")
        self.moss_carpet = self.bid("moss_carpet")
        self.berry = {a: level.block_palette.get_add_block(Block(
            "minecraft", "sweet_berry_bush",
            {"age": amulet_nbt.StringTag(str(a))})) for a in (2, 3)}
        # ก้นลำธาร — ดินกับมอสเป็นหลัก กรวดโผล่ประปราย ให้กลมกลืนกับป่ารอบข้าง
        self.bed_stream = [self.bid(n) for n in
                           ("dirt", "coarse_dirt", "gravel", "moss_block",
                            "dirt", "podzol", "gravel", "clay")]
        # ชายหาดหินกรวดแบบทะเลสาบภูเขา ไม่ใช่หาดทรายทะเล
        self.shore_ids = [self.bid(n) for n in
                          ("gravel", "gravel", "cobblestone", "coarse_dirt",
                           "sand", "stone")]
        # หิมะหลายความหนา ทำให้แนวหิมะไล่ระดับแทนที่จะตัดเป็นเส้น
        self.snow_layers = [
            level.block_palette.get_add_block(
                Block("minecraft", "snow", {"layers": amulet_nbt.StringTag(str(i))}))
            for i in range(1, 9)
        ]
        self.snow_block = self.bid("snow_block")
        # ธารน้ำแข็ง — แกนกลางน้ำแข็งอัดแน่น ขอบบางกว่า
        self.ice_core = self.bid("blue_ice")
        self.ice_mid = self.bid("packed_ice")
        self.ice_edge = self.bid("ice")
        # slab ครึ่งบล็อกของหินแต่ละชนิด — ไล่ระดับได้ละเอียดเป็นสองเท่า
        self.slab_ids = np.zeros(len(S.KEYS), dtype=np.uint32)
        for key, slab in S.SLAB_OF.items():
            self.slab_ids[S.IDX[key]] = level.block_palette.get_add_block(
                Block("minecraft", slab, {"type": amulet_nbt.StringTag("bottom")})
            )
        self.sub_soil = [self.bid(n) for n in ("dirt", "dirt", "dirt", "coarse_dirt")]
        self.sub_alpine = [self.bid(n) for n in
                           ("coarse_dirt", "dirt", "gravel", "stone")]

        self.plant_ids = {}
        self._air_memo = {}
        self._water_memo = {}
        self._soft_memo = {}
        self._preserve_memo = {}
        self._biome_ids = {}
        # บล็อกที่ --vegetation-only ต้องไม่ลบทิ้ง แม้จะอยู่เหนือผิวดิน
        # หิมะกองอยู่เหนือผิวดินเสมอ และเป็นผลของ terrain pass ไม่ใช่ของพืช
        # ถ้าล้างไปด้วยจะหายทั้งแผนที่เพราะโหมดนี้ไม่เขียนหิมะกลับ
        self.PRESERVE_NAMES = {
            "air", "cave_air", "void_air",
            "snow", "snow_block", "powder_snow",
            "water", "ice", "packed_ice", "blue_ice", "frosted_ice",
            "seagrass", "tall_seagrass", "lily_pad",
        }
        # พืชคลุมดินที่เราโรยเอง ต้นไม้ต้องทับได้ ไม่งั้นฐานต้นจะถูกข้าม
        # แล้วลำต้นเริ่มสูงขึ้นหนึ่งช่อง เหลือหญ้าค้างอยู่ใต้ต้นที่ลอย
        self.SOFT_NAMES = {
            "air", "cave_air", "void_air", "snow",
            "short_grass", "fern", "large_fern", "tall_grass", "dead_bush",
            "brown_mushroom", "red_mushroom", "dandelion", "poppy",
            "oxeye_daisy", "cornflower", "azure_bluet", "moss_carpet",
            "sweet_berry_bush", "allium", "white_tulip", "orange_tulip",
        }
        self._schem_ids = {}
        self.schems = TS.load_all(verbose=False)
        n = sum(len(v) for v in self.schems.values())
        self.tree_pad = schematic_tree_pad(self.schems)
        print(f"schematic ต้นไม้: {n} แบบ"
              + (f" | padding {self.tree_pad} บล็อก" if n
                 else "  (ไม่มีไฟล์ใน trees/ — ใช้ทรงจากโค้ด)"))

    def is_soft(self, bid):
        """ทับได้ไหม — อากาศหรือพืชคลุมดิน"""
        v = self._soft_memo.get(bid)
        if v is None:
            try:
                v = self.level.block_palette[bid].base_name in self.SOFT_NAMES
            except Exception:
                v = False
            self._soft_memo[bid] = v
        return v

    def is_preserved(self, bid):
        """บล็อกนี้ต้องรอดจากการล้างพืชไหม (หิมะ/น้ำ/น้ำแข็ง/พืชน้ำ)"""
        v = self._preserve_memo.get(bid)
        if v is None:
            try:
                v = self.level.block_palette[bid].base_name in self.PRESERVE_NAMES
            except Exception:
                # อ่าน palette ไม่ได้ = ไม่รู้ว่าเป็นอะไร เก็บไว้ปลอดภัยกว่าลบ
                v = True
            self._preserve_memo[bid] = v
        return v

    def schem_id(self, name, props):
        key = (name, tuple(sorted(props.items())))
        v = self._schem_ids.get(key)
        if v is None:
            ns, _, base = name.partition(":")
            v = self.level.block_palette.get_add_block(Block(
                ns or "minecraft", base,
                {k: amulet_nbt.StringTag(val) for k, val in props.items()},
            ))
            self._schem_ids[key] = v
        return v

    def is_air(self, bid):
        """เช็คจาก palette จริง ไม่ใช่เทียบกับ id เดียว

        get_add_block(minecraft:air) คืน id ที่ต่างจาก universal_minecraft:air (id 0)
        ที่อยู่ในโลกจริง ถ้าเทียบตรงๆ เงื่อนไข only_air จะเป็นเท็จเสมอ
        """
        v = self._air_memo.get(bid)
        if v is None:
            try:
                v = self.level.block_palette[bid].base_name in (
                    "air", "cave_air", "void_air"
                )
            except Exception:
                v = False
            self._air_memo[bid] = v
        return v

    def is_water(self, bid):
        """Whether a palette entry is water or an underwater plant."""
        v = self._water_memo.get(bid)
        if v is None:
            try:
                v = self.level.block_palette[bid].base_name in (
                    "water", "seagrass", "tall_seagrass"
                )
            except Exception:
                v = False
            self._water_memo[bid] = v
        return v

    def biome_id(self, name):
        """id ของ biome ใน palette ของโลก — cache ไว้ ไม่ต้องถามซ้ำทุก chunk"""
        v = self._biome_ids.get(name)
        if v is None:
            v = self.level.biome_palette.get_add_biome(name)
            self._biome_ids[name] = v
        return v

    def bid(self, name):
        if name not in self.ids:
            self.ids[name] = self.level.block_palette.get_add_block(
                Block("minecraft", name)
            )
        return self.ids[name]

    def prop_block(self, name, props):
        key = (name, tuple(sorted(props.items())))
        v = self._schem_ids.get(key)
        if v is None:
            v = self.level.block_palette.get_add_block(Block(
                "minecraft", name,
                {k: amulet_nbt.StringTag(val) for k, val in props.items()},
            ))
            self._schem_ids[key] = v
        return v

    def plant(self, name):
        if name not in self.plant_ids:
            self.plant_ids[name] = self.bid(name)
        return self.plant_ids[name]


def bhash(wx, wz, salt=0):
    """สุ่ม 0..1 จากพิกัดบล็อก — hash จริง ไม่ใช่สูตรเชิงเส้น

    (wx*a + wz*b) % n เป็นคาบตามแนวทแยง ทำให้ก้นน้ำ/ใต้ผิวเห็นเป็นริ้ว /////
    """
    h = (wx * 374761393 + wz * 668265263 + salt * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / float(0xFFFFFF)


def bhash_array(wx, wz, salt=0):
    """เวอร์ชัน NumPy ของ bhash() สำหรับคำนวณทั้ง region พร้อมกัน"""
    wx = np.asarray(wx, dtype=np.int64)
    wz = np.asarray(wz, dtype=np.int64)
    h = (
        wx * np.int64(374761393)
        + wz * np.int64(668265263)
        + np.int64(salt) * np.int64(1442695041)
    ) & np.int64(0xFFFFFFFF)
    h = ((h ^ (h >> np.int64(13))) * np.int64(1274126177)) & np.int64(0xFFFFFFFF)
    return ((h ^ (h >> np.int64(16))) & np.int64(0xFFFFFF)).astype(
        np.float32
    ) / np.float32(0xFFFFFF)


def lake_surface_levels(base_y, water, water_depth, level_offset=None,
                        min_core_depth=None, shelf_search=None):
    """Return (surface_y, lake_mask) with one coherent level across lake shelves.

    The DEM records the water surface, while ``water_depth`` is generated from
    distance to shore.  A depth threshold alone therefore identifies only the
    lake core and must not be used directly as the elevation mask: doing so
    leaves a one-block-low ring in the 1–2 block shallow shelf.

    Core cells establish the adjusted surface. That level is then propagated
    through adjacent water cells for ``shelf_search`` blocks. Nearby shelf
    cells join the lake level; a steep inlet/outlet is joined by a one-block
    ramp instead of leaving an abrupt cut at the lake boundary. Shallow
    streams with no nearby core retain their original DEM elevation.
    """
    base_y = np.asarray(base_y, dtype=np.int32)
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
        shelf_search,
        int(getattr(C, "LAKE_LEVEL_FLAT_SHELF_BLOCKS", 4)),
    )
    relax_passes = int(getattr(C, "LAKE_LEVEL_RELAX_PASSES", 4))

    core = water & (water_depth >= min_core_depth)
    unresolved = np.iinfo(np.int32).min
    target = np.full(base_y.shape, unresolved, dtype=np.int32)
    target[core] = base_y[core] + level_offset

    # Propagate the core level into the shallow shelf.  Taking the highest
    # adjacent core level prevents a single rounded-down DEM pixel from
    # recreating a notch at the shoreline.
    for distance in range(max(0, shelf_search)):
        neighbour = np.full(base_y.shape, unresolved, dtype=np.int32)
        neighbour[1:] = np.maximum(neighbour[1:], target[:-1])
        neighbour[:-1] = np.maximum(neighbour[:-1], target[1:])
        neighbour[:, 1:] = np.maximum(neighbour[:, 1:], target[:, :-1])
        neighbour[:, :-1] = np.maximum(neighbour[:, :-1], target[:, 1:])
        fill = water & (target == unresolved) & (neighbour != unresolved)
        if not fill.any():
            break
        # Keep a nearly-level shelf at exactly the lake surface. When the DEM
        # differs more strongly, approach it by at most one block per cell so
        # inlet/outlet channels do not start with a vertical discontinuity.
        close = np.abs(base_y - neighbour) <= 1
        ramped = np.maximum(
            neighbour - 1, np.minimum(base_y, neighbour + 1)
        )
        # Only the immediate lake shelf inherits an adjacent level wholesale.
        # Farther into a channel, use its original DEM as soon as a one-block
        # transition permits it, so the adjustment cannot end in a new step at
        # the outer search boundary.
        candidate = (
            np.where(close, neighbour, ramped)
            if distance < flat_shelf
            else ramped
        )
        target[fill] = candidate[fill]

    # Two propagation fronts can meet inside a shallow connector. Reconcile
    # only those movable shelf/channel cells; core levels remain fixed.
    for _ in range(max(0, relax_passes)):
        valid = target != unresolved
        lower = np.full(target.shape, unresolved, dtype=np.int32)
        upper = np.full(target.shape, np.iinfo(np.int32).max, dtype=np.int32)

        def constrain(dst, src):
            neighbour_valid = valid[src]
            lo = lower[dst]
            hi = upper[dst]
            values = target[src]
            lo[neighbour_valid] = np.maximum(
                lo[neighbour_valid], values[neighbour_valid] - 1
            )
            hi[neighbour_valid] = np.minimum(
                hi[neighbour_valid], values[neighbour_valid] + 1
            )

        constrain(np.s_[1:, :], np.s_[:-1, :])
        constrain(np.s_[:-1, :], np.s_[1:, :])
        constrain(np.s_[:, 1:], np.s_[:, :-1])
        constrain(np.s_[:, :-1], np.s_[:, 1:])

        movable = valid & ~core & (lower != unresolved) & (lower <= upper)
        changed = movable & ((target < lower) | (target > upper))
        if not changed.any():
            break
        target[changed] = np.maximum(
            lower[changed], np.minimum(target[changed], upper[changed])
        )

    lake = target != unresolved
    result = base_y.copy()
    result[lake] = target[lake]
    return result, lake


def pick_ground(r, table):
    """เลือกพืชพื้นล่างจากตารางถ่วงน้ำหนัก คืน None ถ้าโล่ง"""
    cum = 0.0
    for name, w in table:
        cum += w
        if r < cum:
            return name
    return None


def neighbour_relief(values):
    """Maximum cardinal-neighbour step for each [x, z] cell."""
    values = np.asarray(values, dtype=np.int32)
    relief = np.zeros(values.shape, dtype=np.int32)
    relief[1:] = np.maximum(relief[1:], np.abs(values[1:] - values[:-1]))
    relief[:-1] = np.maximum(relief[:-1], np.abs(values[:-1] - values[1:]))
    relief[:, 1:] = np.maximum(
        relief[:, 1:], np.abs(values[:, 1:] - values[:, :-1])
    )
    relief[:, :-1] = np.maximum(
        relief[:, :-1], np.abs(values[:, :-1] - values[:, 1:])
    )
    return relief


def lakebed_materials(depth, bed_relief, lake_mask, x0=0, z0=0):
    """Classify an alpine lakebed into broad, coherent sediment patches.

    The palette follows the energy gradient of a real lake: wave-washed
    gravel/cobble near shore, mixed substrate in the sublittoral zone, and
    fine clay/mud in quiet deep basins. Exposed underwater steps remain rocky.
    ``lake_mask`` distinguishes a 1-2 block lake shelf from an actual stream.
    """
    depth = np.asarray(depth, dtype=np.int32)
    bed_relief = np.asarray(bed_relief, dtype=np.int32)
    lake_mask = np.asarray(lake_mask, dtype=bool)
    if depth.shape != bed_relief.shape or depth.shape != lake_mask.shape:
        raise ValueError("depth, bed_relief, and lake_mask must have the same shape")

    shape = depth.shape
    broad = S.smooth_noise(x0, z0, shape, 84.0, 7611, 3)
    medium = S.smooth_noise(x0, z0, shape, 42.0, 7682, 2)
    texture = np.clip(
        0.74 * (broad * 0.5 + 0.5)
        + 0.26 * (medium * 0.5 + 0.5),
        0.0, 0.999,
    )
    effective_depth = depth.astype(np.float32) + broad * 2.4 + medium * 0.8

    result = np.full(shape, LAKEBED["clay"], dtype=np.uint8)
    wet = depth > 0
    stream = wet & ~lake_mask
    result[stream] = LAKEBED["stream"]

    shallow = wet & lake_mask & (effective_depth < 6.0)
    result[shallow & (texture < 0.46)] = LAKEBED["gravel"]
    result[shallow & (texture >= 0.46) & (texture < 0.59)] = LAKEBED["cobble"]
    result[shallow & (texture >= 0.59) & (texture < 0.70)] = LAKEBED["sand"]
    result[shallow & (texture >= 0.70)] = LAKEBED["clay"]

    middle = wet & lake_mask & (effective_depth >= 6.0) & (effective_depth < 13.0)
    result[middle & (texture < 0.42)] = LAKEBED["gravel"]
    result[middle & (texture >= 0.42) & (texture < 0.58)] = LAKEBED["clay"]
    result[middle & (texture >= 0.58) & (texture < 0.68)] = LAKEBED["sand"]
    result[middle & (texture >= 0.68) & (texture < 0.74)] = LAKEBED["cobble"]
    result[middle & (texture >= 0.74)] = LAKEBED["mud"]

    deep = wet & lake_mask & (effective_depth >= 13.0)
    result[deep & (texture < 0.49)] = LAKEBED["mud"]
    result[deep & (texture >= 0.49) & (texture < 0.65)] = LAKEBED["clay"]
    result[deep & (texture >= 0.65) & (texture < 0.67)] = LAKEBED["calcite"]
    result[deep & (texture >= 0.67) & (texture < 0.73)] = LAKEBED["gravel"]
    result[deep & (texture >= 0.73)] = LAKEBED["stone"]

    # Relief is a proxy for current exposure and mass movement. Fine sediment
    # cannot settle on these steps, so expose bedrock and coarse debris.
    rocky = wet & lake_mask & (depth >= 3) & (bed_relief >= 2)
    rock_texture = np.clip(
        0.70 * (medium * 0.5 + 0.5)
        + 0.30 * (broad * 0.5 + 0.5),
        0.0, 0.999,
    )
    result[rocky & (rock_texture < 0.36)] = LAKEBED["stone"]
    result[rocky & (rock_texture >= 0.36) & (rock_texture < 0.70)] = (
        LAKEBED["cobble"]
    )
    result[rocky & (rock_texture >= 0.70)] = LAKEBED["gravel"]
    return result


def aquatic_vegetation_masks(depth, bed_kind, bed_relief, x0=0, z0=0):
    """Return clustered shallow-lake vegetation masks.

    Vegetation is confined to the photic littoral shelf. Broad world-space
    noise creates meadows with empty gaps, while coordinate hashes thin each
    meadow naturally. Deep sediment plains intentionally remain unvegetated.
    """
    depth = np.asarray(depth, dtype=np.int32)
    bed_kind = np.asarray(bed_kind, dtype=np.uint8)
    bed_relief = np.asarray(bed_relief, dtype=np.int32)
    if depth.shape != bed_kind.shape or depth.shape != bed_relief.shape:
        raise ValueError("depth, bed_kind, and bed_relief must have the same shape")

    shape = depth.shape
    meadow = S.smooth_noise(x0, z0, shape, 52.0, 8841, 3) * 0.5 + 0.5
    meadow_detail = S.smooth_noise(x0, z0, shape, 23.0, 8912, 2) * 0.5 + 0.5
    habitat = np.clip(0.78 * meadow + 0.22 * meadow_detail, 0.0, 1.0)

    wx = np.arange(x0, x0 + shape[0], dtype=np.int64)[:, None]
    wz = np.arange(z0, z0 + shape[1], dtype=np.int64)[None, :]
    roll = bhash_array(wx, wz, 8)
    tall_roll = bhash_array(wx, wz, 18)

    density = np.zeros(shape, dtype=np.float32)
    density[habitat > 0.54] = 0.11
    density[habitat > 0.62] = 0.28
    suitable_bed = np.isin(
        bed_kind,
        np.asarray([
            LAKEBED["sand"], LAKEBED["gravel"], LAKEBED["clay"],
        ], dtype=np.uint8),
    )
    plantable = (
        (depth >= 1)
        & (depth <= 4)
        & (bed_relief <= 1)
        & suitable_bed
    )
    vegetation = plantable & (roll < density)
    tall = vegetation & (depth >= 2) & (tall_roll < 0.24)
    short = vegetation & ~tall

    # Hallstätter See has steep, open alpine shores rather than broad lily
    # marshes. Keep only a trace in the most sheltered one-block coves.
    lily = (
        (depth == 1)
        & (bed_relief == 0)
        & (habitat > 0.70)
        & (roll > 0.998)
    )
    return short, tall, lily


def slab_states(sub, cls, water, ice, snow=None, threshold=0.25):
    """ตัดสินว่าคอลัมน์ไหนควรใช้ slab เพื่อไล่ระดับครึ่งบล็อก

    `sub` คือเศษที่ terrain_shape.py ปัดทิ้ง (ผิวจริง = y + sub) เดิมค่านี้ถูกใช้
    แค่เกลี่ยหิมะ ทั้งที่มันบอกตรง ๆ ว่าผิวจริงอยู่สูงหรือต่ำกว่าหน้าบล็อกเท่าไร

    คืน state ต่อคอลัมน์
      -1  ผิวจริงต่ำกว่าครึ่งบล็อก -> บล็อกบนสุดเป็น slab (หน้าบนอยู่ y+0.5)
       0  ใช้บล็อกเต็มตามเดิม
      +1  ผิวจริงสูงกว่าครึ่งบล็อก -> วาง slab เพิ่มบน y+1

    ใช้เฉพาะบล็อกหินที่มี slab ในวานิลลา และเฉพาะบนบก — ใต้น้ำ slab จะเปิดช่อง
    ให้เห็นโพรงและทำให้ระดับก้นที่คำนวณไว้ไม่ตรง

    และต้องเลี่ยงคอลัมน์ที่มีหิมะ: slab กินครึ่งล่างของช่อง บล็อกถัดไปจึงเริ่มที่
    ขอบบนของช่องถัดไป เกิดช่องว่างครึ่งบล็อกเสมอ ทำให้หิมะลอยเหนือ slab
    (วัดจากโลกจริง: 23 จาก 23 คอลัมน์ที่มีทั้งสองอย่าง หิมะลอยทุกอัน)

    ไม่เสียอะไร — ที่มีหิมะเรามีกลไกไล่ระดับที่ละเอียดกว่าอยู่แล้ว คือความหนา
    หิมะทีละ 1/8 บล็อกจาก terrain_offset ซึ่งแก้ปัญหาเดียวกันได้ดีกว่า slab
    """
    sub = np.asarray(sub, dtype=np.float32)
    eligible = S.HAS_SLAB[np.asarray(cls, dtype=np.intp)] & ~water & ~ice
    if snow is not None:
        eligible &= np.asarray(snow) == 0
    state = np.zeros(sub.shape, dtype=np.int8)
    state[eligible & (sub > threshold)] = 1
    state[eligible & (sub < -threshold)] = -1
    return state


def prepare_dense_fields(P, surf_y, elev, cls, snow_lv, soil, wdepth,
                         shore, shore_p, decor, x0, x1, z0, z1, px0, pz0,
                         terrain_sub=None):
    """เตรียม array สำหรับผิวดิน ชั้นดิน น้ำ น้ำแข็ง และหิมะ

    array ที่คืนมามีพิกัด [x, z] เฉพาะกรอบจริง ไม่รวม padding การแยกขั้นนี้
    ทำให้ทดสอบผลลัพธ์ได้โดยไม่ต้องเปิด world และทำให้ขั้นเขียน chunk ไม่ต้อง
    คำนวณกฎซ้ำทีละบล็อก
    """
    sx = slice(x0 - px0, x1 - px0)
    sz = slice(z0 - pz0, z1 - pz0)
    shape = (x1 - x0, z1 - z0)

    base_y = surf_y[sx, sz].astype(np.int32, copy=False)
    elev_core = elev[sx, sz].astype(np.float32, copy=False)
    cls_core = cls[sx, sz]
    snow_core = snow_lv[sx, sz]
    soil_core = soil[sx, sz]
    depth_requested = wdepth[sx, sz].astype(np.int32, copy=False)
    patch_a = decor["patch_a"][sx, sz]
    patch_b = decor["patch_b"][sx, sz]
    patch_c = decor["patch_c"][sx, sz]
    damp = decor["damp"][sx, sz]
    slope = decor["slope"][sx, sz]

    wx = np.arange(x0, x1, dtype=np.int64)[:, None]
    wz = np.arange(z0, z1, dtype=np.int64)[None, :]

    water_full = cls == S.IDX["water"]
    water_mask = water_full[sx, sz]
    ice_mask = cls_core == S.IDX["ice"]
    adjusted_full, lake_full = lake_surface_levels(
        surf_y, water_full, wdepth
    )
    level_adjusted = lake_full[sx, sz]
    y = adjusted_full[sx, sz]

    surface_id = np.take(
        np.asarray(P.surface_ids, dtype=np.uint32), cls_core
    ).copy()

    # ชายฝั่งคำนวณพร้อมกันทั้ง region เพื่อลด hash/setb รายบล็อก
    shore_texture = np.clip(0.68 * patch_a + 0.32 * patch_b, 0.0, 1.0)
    beach = (
        ~water_mask
        & ~ice_mask
        & shore[sx, sz]
        & (shore_texture < shore_p[sx, sz])
    )
    cobble = beach & (bhash_array(wx, wz, 9) < 0.06)
    sand = beach & ~cobble & (patch_b > 0.60)
    gravel = beach & ~cobble & ~sand
    surface_id[cobble] = P.shore_cobble
    surface_id[sand] = P.bed_sand
    surface_id[gravel] = P.bed_gravel

    # Low, damp gaps between beach patches become coherent mud/moss wetlands.
    # This keeps the shore varied without drawing a continuous material ring.
    wet_shore = (
        ~water_mask
        & ~ice_mask
        & shore[sx, sz]
        & ~beach
        & (damp > 0.56)
        & (slope < 22.0)
        & (patch_c > 0.46)
    )
    wet_mud = wet_shore & (patch_b < 0.70)
    wet_moss = wet_shore & ~wet_mud
    surface_id[wet_mud] = P.surface_ids[S.IDX["mud"]]
    surface_id[wet_moss] = P.surface_ids[S.IDX["moss"]]

    # วัสดุใต้ผิวสามชั้น
    subsoil_keys = ("grass", "moss", "podzol", "dirt", "coarse", "rooted", "mud")
    subsoil_mask = np.isin(
        cls_core, np.asarray([S.IDX[k] for k in subsoil_keys], dtype=np.uint8)
    ) & ~beach
    subsoil_ids = []
    alpine = elev_core > S.TREELINE
    low_palette = np.asarray(P.sub_soil, dtype=np.uint32)
    high_palette = np.asarray(P.sub_alpine, dtype=np.uint32)
    for d in (1, 2, 3):
        r = bhash_array(wx, wz, 4 + d)
        li = np.minimum((r * len(low_palette)).astype(np.intp), len(low_palette) - 1)
        hi = np.minimum((r * len(high_palette)).astype(np.intp), len(high_palette) - 1)
        subsoil_ids.append(np.where(alpine, high_palette[hi], low_palette[li]))

    # ความลึกที่เขียนได้จริง หากชนขอบล่างให้เก็บตัวเลขไว้แจ้งเตือนแทนการ
    # ลดความลึกเงียบ ๆ
    fill_bottom = getattr(C, "Y_FILL_BOTTOM", C.Y_TERRAIN_MIN)
    available = np.maximum(0, y - (fill_bottom + 1))
    depth = np.where(
        water_mask, np.minimum(depth_requested, available), 0
    ).astype(np.int32)
    bed_y = y - depth
    bed_relief = neighbour_relief(bed_y)
    clipped = water_mask & (depth_requested > depth)

    # Alpine-lake scenario: coherent substrate patches follow depth and relief
    # instead of forming hard concentric material bands.
    bed_kind = lakebed_materials(
        depth, bed_relief, level_adjusted, x0=x0, z0=z0
    )
    material_ids = np.asarray([
        P.bed_clay,       # stream is replaced from bed_stream below
        P.bed_sand,
        P.bed_gravel,
        P.bed_clay,
        P.bed_mud,
        P.bed_stone,
        P.shore_cobble,
        P.bed_calcite,
    ], dtype=np.uint32)
    bed_id = material_ids[bed_kind]

    stream = water_mask & (bed_kind == LAKEBED["stream"])
    r5 = bhash_array(wx, wz, 5)
    stream_palette = np.asarray(P.bed_stream, dtype=np.uint32)
    stream_i = np.minimum(
        (r5 * len(stream_palette)).astype(np.intp), len(stream_palette) - 1
    )
    bed_id[stream] = stream_palette[stream_i[stream]]

    ice_band = np.minimum((patch_c * 3).astype(np.intp), 2)
    ice_top = np.choose(
        ice_band,
        np.asarray([P.ice_edge, P.ice_mid, P.ice_core], dtype=np.uint32),
    )

    if terrain_sub is None:
        slab = np.zeros(shape, dtype=np.int8)
    else:
        slab = slab_states(
            terrain_sub[sx, sz], cls_core, water_mask, ice_mask,
            snow=snow_core,
        )
    # ผิวที่ของอื่นต้องยืนอยู่บน — สูงขึ้นหนึ่งเมื่อมี slab วางทับ
    object_y = y + (slab > 0)

    return {
        "y": y,
        "slab": slab,
        "slab_id": np.take(P.slab_ids, cls_core),
        "object_y": object_y,
        "base_y": base_y,
        "level_adjusted": level_adjusted,
        "elev": elev_core,
        "cls": cls_core,
        "snow": snow_core,
        "soil": soil_core,
        "surface_id": surface_id,
        "subsoil_mask": subsoil_mask,
        "subsoil_ids": subsoil_ids,
        "water": water_mask,
        "depth": depth,
        "bed_y": bed_y,
        "bed_relief": bed_relief,
        "bed_kind": bed_kind,
        "bed_id": bed_id,
        "clipped": clipped,
        "ice": ice_mask,
        "ice_top": ice_top,
        "beach": beach,
        "shore_cobble_mask": cobble,
        "shore_sand_mask": sand,
        "shore_gravel_mask": gravel,
        "wet_shore": wet_shore,
        "wet_mud_mask": wet_mud,
        "wet_moss_mask": wet_moss,
        "patch_a": patch_a,
        "patch_b": patch_b,
    }


def paint_dense_chunks(P, get_chunk, dense, x0, x1, z0, z1,
                       lake_only=False):
    """เขียน dense layers ด้วย NumPy หนึ่งครั้งต่อ chunk

    คืนจำนวน snow layer/snow block ที่เขียนจริง ส่วน water/clipping นับจาก dense fields
    ได้โดยตรง
    """
    snow_written = 0
    fill_bottom = getattr(C, "Y_FILL_BOTTOM", C.Y_TERRAIN_MIN)

    for cx in range(x0 >> 4, ((x1 - 1) >> 4) + 1):
        wx0, wx1 = max(x0, cx * CHUNK), min(x1, (cx + 1) * CHUNK)
        dx0, dx1 = wx0 - x0, wx1 - x0
        lx0, lx1 = wx0 & 15, ((wx1 - 1) & 15) + 1

        for cz in range(z0 >> 4, ((z1 - 1) >> 4) + 1):
            wz0, wz1 = max(z0, cz * CHUNK), min(z1, (cz + 1) * CHUNK)
            dz0, dz1 = wz0 - z0, wz1 - z0
            lz0, lz1 = wz0 & 15, ((wz1 - 1) & 15) + 1

            ch = get_chunk(cx, cz)
            if ch is None:
                continue

            ss = np.s_[dx0:dx1, dz0:dz1]
            y = dense["y"][ss]
            bed_y = dense["bed_y"][ss]
            ice = dense["ice"][ss]
            snow = dense["snow"][ss]
            water = dense["water"][ss]
            depth = dense["depth"][ss]
            active_water = water & (depth > 0)

            y_lo = max(
                fill_bottom,
                int(min(y.min() - 3, bed_y.min(), (y - np.where(ice, 2, 0)).min())),
            )
            # A repaint can make a formerly deep basin shallower. Include the
            # full foundation range so the old water cavity below the new bed
            # is backfilled; otherwise gravity blocks such as sand/gravel fall
            # through it on the next Minecraft block update.
            if active_water.any():
                y_lo = fill_bottom
            snow_height = (snow.astype(np.int32) + 7) // 8
            lift = dense["slab"][ss] > 0
            y_hi = min(
                C.Y_BUILD_CEILING,
                int((y + snow_height + lift).max()) + 1,
            )

            volume = np.asarray(
                ch.blocks[lx0:lx1, y_lo:y_hi + 1, lz0:lz1]
            ).copy()
            gx, gz = np.indices(y.shape)
            top = y - y_lo

            if not lake_only:
                volume[gx, top, gz] = dense["surface_id"][ss]
                # ไล่ระดับครึ่งบล็อกด้วย slab (เฉพาะหิน ดูที่ slab_states)
                slab = dense["slab"][ss]
                slab_id = dense["slab_id"][ss]
                low = slab < 0
                if low.any():
                    volume[gx[low], top[low], gz[low]] = slab_id[low]
                high = (slab > 0) & (top + 1 < volume.shape[1])
                if high.any():
                    volume[gx[high], (top + 1)[high], gz[high]] = slab_id[high]

            subsoil = dense["subsoil_mask"][ss]
            if not lake_only:
                for d, ids in enumerate(dense["subsoil_ids"], 1):
                    m = subsoil & (top - d >= 0)
                    volume[gx[m], (top - d)[m], gz[m]] = ids[ss][m]

            if active_water.any():
                bed = bed_y - y_lo
                ys = np.arange(
                    y_lo, y_hi + 1, dtype=np.int32
                )[None, :, None]
                foundation = (
                    active_water[:, None, :]
                    & (ys > fill_bottom)
                    & (ys < bed_y[:, None, :])
                )
                fx, fy, fz = np.nonzero(foundation)
                if fx.size:
                    current = volume[fx, fy, fz]
                    writable = np.fromiter(
                        (
                            P.is_air(int(v)) or P.is_water(int(v))
                            for v in current
                        ),
                        dtype=bool,
                        count=current.size,
                    )
                    if writable.any():
                        volume[
                            fx[writable], fy[writable], fz[writable]
                        ] = P.stone
                volume[gx[active_water], bed[active_water], gz[active_water]] = (
                    dense["bed_id"][ss][active_water]
                )
                fill = (
                    active_water[:, None, :]
                    & (ys > bed_y[:, None, :])
                    & (ys <= y[:, None, :])
                )
                volume[fill] = P.water

            if ice.any() and not lake_only:
                volume[gx[ice], top[ice], gz[ice]] = dense["ice_top"][ss][ice]
                for d in (1, 2):
                    m = ice & (top - d >= 0)
                    volume[gx[m], (top - d)[m], gz[m]] = P.ice_mid

            snow_units = (
                np.zeros(snow.shape, dtype=np.int16)
                if lake_only
                else np.where(water, 0, snow).astype(np.int16)
            )
            # slab ที่วางบน y+1 ดันผิวขึ้นหนึ่ง หิมะต้องเริ่มเหนือมัน
            top = top + lift.astype(top.dtype)
            full_blocks = snow_units // 8
            remainder = snow_units % 8

            for d in range(1, int(full_blocks.max(initial=0)) + 1):
                full_mask = (full_blocks >= d) & (top + d < volume.shape[1])
                if not full_mask.any():
                    continue
                sxv, szv = np.nonzero(full_mask)
                syv = top[full_mask] + d
                current = volume[sxv, syv, szv]
                writable = np.fromiter(
                    (
                        P.is_soft(int(v)) or int(v) == int(P.snow_block)
                        for v in current
                    ),
                    dtype=bool,
                    count=current.size,
                )
                if writable.any():
                    volume[sxv[writable], syv[writable], szv[writable]] = P.snow_block
                    snow_written += int(writable.sum())

            layer_mask = (
                (remainder > 0)
                & (top + full_blocks + 1 < volume.shape[1])
            )
            if layer_mask.any():
                sxv, szv = np.nonzero(layer_mask)
                syv = top[layer_mask] + full_blocks[layer_mask] + 1
                current = volume[sxv, syv, szv]
                writable = np.fromiter(
                    (P.is_soft(int(v)) for v in current),
                    dtype=bool,
                    count=current.size,
                )
                if writable.any():
                    layers = remainder[layer_mask][writable].astype(np.intp) - 1
                    volume[sxv[writable], syv[writable], szv[writable]] = np.take(
                        np.asarray(P.snow_layers, dtype=np.uint32), layers
                    )
                    snow_written += int(writable.sum())

            ch.blocks[lx0:lx1, y_lo:y_hi + 1, lz0:lz1] = volume

    return snow_written


def verify_terrain_level(P, get_chunk, dense, x0, x1, z0, z1, step=17,
                         probe=48):
    """เช็คว่าภูมิประเทศในโลกตรงกับ heightmap ปัจจุบันไหม

    --vegetation-only ไม่ได้เขียนผิวดิน มันเชื่อว่า `dense["y"]` คือผิวจริง
    ถ้าโลกถูก build ไว้ด้วย config รุ่นก่อน (เช่นตอน Y_TERRAIN_MIN ยังเป็น -60)
    ผิวจริงจะอยู่คนละระดับ แล้วพืชทั้งแปลงจะถูกวางลอยกลางอากาศ

    ตรวจสองทาง:
      solid_at_y  ผิวที่คาดต้องมีบล็อกอยู่จริง — จับกรณีโลกต่ำกว่า heightmap
      stone_above 16 บล็อกเหนือผิวต้องไม่ใช่หิน — จับกรณีโลกสูงกว่า

    คืน dict พร้อม offset ที่วัดได้ (median ของผิวจริง - ผิวที่คาด) เพื่อให้
    ข้อความ error บอกได้ว่าโลกเพี้ยนไปกี่บล็อก
    """
    checked = missing = buried = 0
    offsets = []
    air_like = ("air", "cave_air", "void_air")

    for qx in range(0, x1 - x0, step):
        for qz in range(0, z1 - z0, step):
            if dense["water"][qx, qz]:
                continue
            wx, wz = x0 + qx, z0 + qz
            ch = get_chunk(wx >> 4, wz >> 4)
            if ch is None:
                continue
            lx, lz = wx & 15, wz & 15
            y = int(dense["y"][qx, qz])
            checked += 1

            def name_at(wy):
                if (wy < getattr(C, "Y_FILL_BOTTOM", C.Y_TERRAIN_MIN)
                        or wy > C.Y_BUILD_CEILING):
                    return None
                try:
                    return P.level.block_palette[
                        int(ch.blocks[lx, wy, lz])
                    ].base_name
                except Exception:
                    return None

            if name_at(y) in air_like:
                missing += 1
            if name_at(y + 16) == "stone":
                buried += 1

            # ประเมิน offset จากผิวจริง — มองข้ามพืช/หิมะ/ต้นไม้ที่กองอยู่บน
            # ผิว เพราะขั้นนี้รันก่อนการล้าง ผิวจริงคือบล็อกแข็งตัวบนสุด
            top = None
            for wy in range(min(C.Y_BUILD_CEILING, y + probe),
                            y - probe - 1, -1):
                nm = name_at(wy)
                if nm is None or nm in air_like:
                    continue
                if nm in P.SOFT_NAMES or "leaves" in nm or nm.endswith("_log"):
                    continue
                top = wy
                break
            if top is not None:
                offsets.append(top - y)

    return {
        "checked": checked,
        "missing_surface": missing,
        "stone_above_surface": buried,
        "median_offset": (
            int(np.median(offsets)) if offsets else 0
        ),
    }


def clear_vegetation_chunks(P, get_chunk, dense, x0, x1, z0, z1,
                            headroom=VEGETATION_HEADROOM):
    """ล้างทุกอย่างเหนือผิวดินเพื่อปลูกใหม่ โดยไม่แตะ terrain

    ใช้กฎเชิงตำแหน่งไม่ใช่บัญชีชื่อบล็อก: หลัง terrain pass เสร็จ ชั้นที่อยู่
    *เหนือ* ผิวดินมีได้แค่อากาศ หิมะ และของที่ vegetation pass วางเอง (ต้นไม้
    พืช ก้อนหิน ท่อนไม้) การล้างตามตำแหน่งจึงครอบ decor ที่ใช้บล็อกเดียวกับ
    ภูมิประเทศ (stone/cobblestone/gravel) ได้ด้วย ซึ่งบัญชีชื่อทำไม่ได้

    คอลัมน์น้ำถูกข้ามทั้งคอลัมน์ เพราะโหมดนี้ไม่ปลูกพืชน้ำกลับ
    คืนจำนวนบล็อกที่ล้าง
    """
    cleared = 0

    for cx in range(x0 >> 4, ((x1 - 1) >> 4) + 1):
        wx0, wx1 = max(x0, cx * CHUNK), min(x1, (cx + 1) * CHUNK)
        dx0, dx1 = wx0 - x0, wx1 - x0
        lx0, lx1 = wx0 & 15, ((wx1 - 1) & 15) + 1

        for cz in range(z0 >> 4, ((z1 - 1) >> 4) + 1):
            wz0, wz1 = max(z0, cz * CHUNK), min(z1, (cz + 1) * CHUNK)
            dz0, dz1 = wz0 - z0, wz1 - z0
            lz0, lz1 = wz0 & 15, ((wz1 - 1) & 15) + 1

            ch = get_chunk(cx, cz)
            if ch is None:
                continue

            ss = np.s_[dx0:dx1, dz0:dz1]
            y = dense["y"][ss]
            water = dense["water"][ss]
            if water.all():
                continue

            y_lo = int(y.min()) + 1
            y_hi = min(C.Y_BUILD_CEILING, int(y.max()) + int(headroom))
            if y_hi < y_lo:
                continue

            volume = np.asarray(
                ch.blocks[lx0:lx1, y_lo:y_hi + 1, lz0:lz1]
            ).copy()
            ys = np.arange(y_lo, y_hi + 1, dtype=np.int32)[None, :, None]
            target = (ys > y[:, None, :]) & ~water[:, None, :]
            if not target.any():
                continue

            # ตัดสินต่อค่า palette ที่พบจริง ไม่ใช่ต่อบล็อก — หนึ่ง chunk มัก
            # มีไม่ถึงสิบชนิดในชั้นนี้ จึงถูกกว่าการวน is_preserved() ทีละบล็อก
            values = np.unique(volume[target])
            drop = np.asarray(
                [v for v in values if not P.is_preserved(int(v))],
                dtype=volume.dtype,
            )
            if not drop.size:
                continue
            mask = target & np.isin(volume, drop)
            if not mask.any():
                continue
            volume[mask] = P.air
            cleared += int(mask.sum())
            ch.blocks[lx0:lx1, y_lo:y_hi + 1, lz0:lz1] = volume

    return cleared


def write_chunk_biomes(chunk, biome_idx, bx, bz, biome_ids):
    """เขียน biome ต่อ cell 4x4x4 ครอบความสูงทั้งโลก

    แก้สองอย่างจากของเดิมที่เขียน `ch.biomes[:, :, :] = bid`:

    1. ช่วง y — slice ที่ไม่ระบุขอบถูก clamp ที่ y 0..255 เพราะ Biomes3D ตั้ง
       default_section_counts=(0, 16) กับ section สูง 4 บล็อก ภูมิประเทศของเรา
       สูงถึง y=640 จึงมี 28% ของแผนที่ที่ไม่เคยได้ biome เลย
    2. ความละเอียด — เดิมใช้ biome ที่พบมากที่สุดค่าเดียวทั้ง chunk ทำให้ขอบ
       ระหว่างโซนเป็นสี่เหลี่ยม 16x16 ทั้งที่ Minecraft เก็บ biome ละเอียด
       ระดับ 4 บล็อกอยู่แล้ว
    """
    chunk.biomes.convert_to_3d()
    cells = np.empty((4, 4), dtype=np.uint32)
    for cx in range(4):
        for cz in range(4):
            block = biome_idx[
                bx + cx * 4:bx + cx * 4 + 4, bz + cz * 4:bz + cz * 4 + 4
            ]
            if block.size:
                vals, counts = np.unique(block, return_counts=True)
                cells[cx, cz] = biome_ids[int(vals[np.argmax(counts)])]
            else:
                cells[cx, cz] = biome_ids[0]

    # หน่วยแกน y ของ Biomes3D คือ cell (4 บล็อก) ต้องระบุขอบเองทุกครั้ง
    y0 = C.WORLD_Y_MIN // 4
    y1 = (C.WORLD_Y_MAX + 1) // 4
    # BoundedPartial3DArray ไม่ broadcast — ต้องส่ง array ที่ shape ตรงเป๊ะ
    chunk.biomes[:, y0:y1, :] = np.repeat(cells[:, None, :], y1 - y0, axis=1)
    return (y1 - y0) * 16


class SoftBlockBuffer:
    """buffer งานต้นไม้/ของตกแต่งเป็น section 16³ ก่อนเขียนกลับ Amulet"""

    def __init__(self, painter, get_chunk):
        self.painter = painter
        self.get_chunk = get_chunk
        self.sections = {}

    def set(self, wx, wy, wz, block_id):
        cx, cz = wx >> 4, wz >> 4
        section_y = wy // 16
        key = (cx, cz, section_y)
        item = self.sections.get(key)
        if item is None:
            chunk = self.get_chunk(cx, cz)
            if chunk is None:
                return False
            y0 = section_y * 16
            data = np.asarray(chunk.blocks[:, y0:y0 + 16, :]).copy()
            item = (chunk, data)
            self.sections[key] = item
        _, data = item
        lx, ly, lz = wx & 15, wy - section_y * 16, wz & 15
        if not self.painter.is_soft(int(data[lx, ly, lz])):
            return False
        data[lx, ly, lz] = block_id
        return True

    def flush(self):
        for (_cx, _cz, section_y), (chunk, data) in self.sections.items():
            y0 = section_y * 16
            chunk.blocks[:, y0:y0 + 16, :] = data
        count = len(self.sections)
        self.sections.clear()
        return count


def lake_shore_probability(water, water_depth, shore_width=4):
    """คืนโอกาสเป็นชายฝั่ง โดยค้นหา body น้ำลึกกว่าความกว้าง shore

    สูตรความลึกมี shelf กว้าง ทำให้น้ำใน 4 บล็อกแรกจากฝั่งยังลึกเพียง 1–2
    บล็อก การค้นแค่ shore_width จึงแยกทะเลสาบจาก stream ไม่ได้
    """
    search = max(
        shore_width, int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    )
    lake, _bank = S.shore_probabilities(
        water, water_depth, shore_width=shore_width, search_blocks=search
    )
    return lake


def bank_probability(water, water_depth, shore_width=4):
    """Return treatment probability for banks of shallow streams."""
    search = max(
        shore_width, int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    )
    _lake, bank = S.shore_probabilities(
        water, water_depth, shore_width=shore_width, search_blocks=search
    )
    return bank


def water_depth_preflight(heightmap, landcover, water_depth, row_batch=512,
                          water_mask=None):
    """ตรวจพื้นที่แนวตั้งสำหรับความลึกน้ำโดยไม่สร้าง array เต็มแผนที่เพิ่ม"""
    fill_bottom = getattr(C, "Y_FILL_BOTTOM", C.Y_TERRAIN_MIN)
    water_code = S.LC["water"]
    if water_mask is not None:
        water_mask = np.asarray(water_mask, dtype=bool)
        if water_mask.shape != heightmap.shape:
            raise ValueError("water_mask and heightmap must have the same shape")
    columns = clipped_columns = clipped_blocks = 0
    max_requested = max_available = 0
    required_fill_bottom = C.Y_BUILD_CEILING

    for z0 in range(0, heightmap.shape[0], row_batch):
        z1 = min(z0 + row_batch, heightmap.shape[0])
        water = (
            water_mask[z0:z1]
            if water_mask is not None
            else landcover[z0:z1] == water_code
        )
        if not water.any():
            continue
        raw = heightmap[z0:z1].astype(np.float32)
        y = np.rint(
            C.Y_TERRAIN_MIN
            + raw / 65535.0 * (C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN)
        ).astype(np.int32)
        requested = water_depth[z0:z1].astype(np.int32, copy=False)
        # Keep this check conservative.  Lake leveling can only raise the
        # surface, so using the unadjusted DEM cannot hide a bottom collision.
        available = np.maximum(0, y - (fill_bottom + 1))
        deficit = requested - available
        clipped = water & (deficit > 0)

        columns += int(water.sum())
        clipped_columns += int(clipped.sum())
        clipped_blocks += int(deficit[clipped].sum())
        max_requested = max(max_requested, int(requested[water].max()))
        max_available = max(max_available, int(available[water].max()))
        required_fill_bottom = min(
            required_fill_bottom, int((y[water] - requested[water] - 1).min())
        )

    return {
        "columns": columns,
        "clipped_columns": clipped_columns,
        "clipped_blocks": clipped_blocks,
        "max_requested": max_requested,
        "max_available": max_available,
        "required_fill_bottom": required_fill_bottom,
        "configured_fill_bottom": fill_bottom,
    }


def validate_water_inputs(water_mask, water_depth, max_depth=None,
                          row_batch=512):
    """Check the derived water products before any world is opened."""
    water_mask = np.asarray(water_mask)
    water_depth = np.asarray(water_depth)
    if water_mask.shape != water_depth.shape:
        raise ValueError("water_mask and water_depth must have the same shape")
    if max_depth is None:
        max_depth = int(getattr(C, "WATER_MAX_DEPTH_BLOCKS", 30))

    water_without_depth = nonwater_with_depth = too_deep = 0
    observed_max = 0
    for z0 in range(0, water_mask.shape[0], row_batch):
        z1 = min(z0 + row_batch, water_mask.shape[0])
        wet = water_mask[z0:z1].astype(bool, copy=False)
        depth = water_depth[z0:z1]
        water_without_depth += int((wet & (depth == 0)).sum())
        nonwater_with_depth += int((~wet & (depth != 0)).sum())
        too_deep += int((depth > max_depth).sum())
        if depth.size:
            observed_max = max(observed_max, int(depth.max()))
    return {
        "water_without_depth": water_without_depth,
        "nonwater_with_depth": nonwater_with_depth,
        "too_deep": too_deep,
        "max_depth": observed_max,
        "configured_max_depth": max_depth,
    }


def process_region(level, P, surf_y, elev, lc, wdepth, x0, x1, z0, z1,
                   stats, px0, pz0, paint_vegetation=True,
                   terrain_offset=None, lake_only=False,
                   vegetation_only=False, terrain_sub=None):
    """เขียนผิว+พืชในกรอบบล็อก [x0,x1) x [z0,z1)

    surf_y / elev / lc ครอบกรอบที่ pad แล้ว โดยมีมุมซ้ายบนอยู่ที่บล็อก (px0, pz0)
    ทุก array index แบบ [x, z] (transpose จากภาพซึ่งเป็น [z, x] มาแล้ว)
    """
    cls, forest_p, snow_lv, soil = S.classify(
        elev, lc, C.METERS_PER_BLOCK, block_m=C.METERS_PER_BLOCK, x0=px0,
        z0=pz0, terrain_offset=terrain_offset,
    )
    # biome เลือกจากสีที่ออกแบบไว้ใน biomes.py ไม่ใช่ตารางความสูงของ surface.py
    # และต้องรู้จักน้ำ เพราะทะเลสาบ/ลำธารมีสีน้ำคนละเฉด
    biome_idx = B.biome_index(elev, lc == S.LC["water"], wdepth)
    biome_ids = np.asarray(
        [P.biome_id(name) for name in B.FULL_NAME], dtype=np.uint32
    )
    D = S.decor_fields(elev, C.METERS_PER_BLOCK, x0=px0, z0=pz0,
                       block_m=C.METERS_PER_BLOCK)
    # หมู่ไม้ — ตัวกำหนดชนิดไม้เด่นของแต่ละผืน ใช้กฎเดียวกับพรีวิว
    stand = E.stand_field(
        elev.shape, C.METERS_PER_BLOCK, x0=px0, z0=pz0,
        block_m=C.METERS_PER_BLOCK,
    )

    # ชายฝั่ง — ไล่ระดับตามระยะ ไม่ใช่วงแหวนกรวดคมๆ รอบน้ำ
    #
    # และต้องแยกทะเลสาบออกจากลำธาร: ทะเลสาบมีคลื่นซัดจนเกิดหาดกรวดจริง
    # แต่ลำธารเล็กในป่าตลิ่งเป็นดิน/หญ้า/มอส มีกรวดโผล่แค่ประปราย
    # ถ้าใส่หาดกรวดรอบลำธารทุกเส้นจะเห็นเป็นเส้นสีทาบทั่วแผนที่ ดูปลอมทันที
    isw = lc == S.LC["water"]
    # แรงของหาด: ทะเลสาบลึกได้หาดชัด ลำธารแทบไม่มี
    lake_shore_p, stream_bank_p = S.shore_probabilities(
        isw, wdepth,
        search_blocks=int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12)),
    )
    shore_p = np.maximum(lake_shore_p, stream_bank_p)
    shore = shore_p > 0.0

    chunks = {}

    def get_chunk(cx, cz):
        key = (cx, cz)
        ch = chunks.get(key)
        if ch is None:
            try:
                ch = level.get_chunk(cx, cz, C.DIMENSION)
            except (ChunkDoesNotExist, ChunkLoadError):
                return None
            chunks[key] = ch
        return ch

    soft_buffer = SoftBlockBuffer(P, get_chunk)

    def setb(wx, wy, wz, bid, only_air=False, soft=False):
        """คืน True เมื่อเขียนจริง — ใช้สำหรับนับสถิติให้ตรงความจริง

        only_air = เขียนเฉพาะที่ว่างจริงๆ
        soft     = เขียนทับพืชคลุมดินได้ด้วย (ใช้กับต้นไม้)
        """
        if not (x0 <= wx < x1 and z0 <= wz < z1):
            return False
        if (wy < getattr(C, 'Y_FILL_BOTTOM', C.Y_TERRAIN_MIN)
                or wy > C.Y_BUILD_CEILING):
            return False
        if soft:
            return soft_buffer.set(wx, wy, wz, bid)
        ch = get_chunk(wx >> 4, wz >> 4)
        if ch is None:
            return False
        lx, lz = wx & 15, wz & 15
        if only_air and not P.is_air(int(ch.blocks[lx, wy, lz])):
            return False
        ch.blocks[lx, wy, lz] = bid
        return True

    # ---- ผิวดิน + ใต้ผิว + แอ่งน้ำ + น้ำแข็ง + หิมะ ----------------------------
    # ส่วนที่มีเกือบทุกคอลัมน์เขียนเป็น volume ต่อ chunk แทน setb() ทีละบล็อก
    dense = prepare_dense_fields(
        P, surf_y, elev, cls, snow_lv, soil, wdepth, shore, shore_p, D,
        x0, x1, z0, z1, px0, pz0,
        terrain_sub=terrain_sub if terrain_sub is not None else terrain_offset,
    )
    object_surface = surf_y.copy()
    object_surface[
        x0 - px0:x1 - px0, z0 - pz0:z1 - pz0
    ] = dense["object_y"]
    if vegetation_only:
        # ต้องเช็คก่อนเขียนอะไรทั้งนั้น — โหมดนี้เชื่อผิวดินที่มีอยู่แล้ว
        # ถ้าผิวจริงไม่ตรง heightmap พืชทั้งแปลงจะลอยกลางอากาศ
        check = verify_terrain_level(P, get_chunk, dense, x0, x1, z0, z1)
        bad = check["missing_surface"] + check["stone_above_surface"]
        if check["checked"] and bad > max(1, check["checked"] // 20):
            raise SystemExit(
                "--vegetation-only refused: ภูมิประเทศในโลกไม่ตรงกับ "
                f"heightmap ปัจจุบัน ({bad}/{check['checked']} คอลัมน์ที่สุ่ม"
                f"ตรวจ; ผิวจริงต่างจากที่คาด {check['median_offset']:+d} บล็อก)"
                "\nโหมดนี้ไม่เขียนผิวดิน จึงต้องมี terrain ที่ตรงกันอยู่ก่อน — "
                "รัน build_terrain.py --patch <พิกัดเดิม> แล้ว paint_surface.py "
                "ปกติหนึ่งรอบก่อน"
            )
        # ล้างของเก่าก่อนปลูกใหม่ แทนการรัน build_terrain.py ทั้ง patch
        stats["cleared"] = stats.get("cleared", 0) + clear_vegetation_chunks(
            P, get_chunk, dense, x0, x1, z0, z1
        )
    else:
        stats["snow"] += paint_dense_chunks(
            P, get_chunk, dense, x0, x1, z0, z1, lake_only=lake_only
        )
        stats["water"] = stats.get("water", 0) + int(dense["depth"].sum())
    if dense["clipped"].any() and not vegetation_only:
        stats["water_clipped_columns"] = (
            stats.get("water_clipped_columns", 0) + int(dense["clipped"].sum())
        )
        requested = wdepth[
            x0 - px0:x1 - px0, z0 - pz0:z1 - pz0
        ].astype(np.int32, copy=False)
        stats["water_clipped_blocks"] = (
            stats.get("water_clipped_blocks", 0)
            + int((requested - dense["depth"])[dense["clipped"]].sum())
        )

    # ---- กองหินใต้น้ำ: เว้นระยะด้วย world-grid และยึดกับก้นแต่ละคอลัมน์ ----
    USTEP = 19
    underwater_x = (
        range((x0 - 3) // USTEP, (x1 + 3) // USTEP + 1)
        if not vegetation_only else ()
    )
    for gx in underwater_x:
        for gz in range((z0 - 3) // USTEP, (z1 + 3) // USTEP + 1):
            rng = V.tree_rng(gx, gz, 4041)
            wx = int((gx + 0.5 + (rng.random() - 0.5) * 0.72) * USTEP)
            wz = int((gz + 0.5 + (rng.random() - 0.5) * 0.72) * USTEP)
            qx, qz = wx - x0, wz - z0
            width, height = x1 - x0, z1 - z0
            if not (-2 <= qx < width + 2 and -2 <= qz < height + 2):
                continue
            cq_x = min(max(qx, 0), width - 1)
            cq_z = min(max(qz, 0), height - 1)
            dep = int(dense["depth"][cq_x, cq_z])
            if not dense["water"][cq_x, cq_z] or not (4 <= dep <= 22):
                continue
            # Cluster boulders on exposed shelves/slopes; quiet deep sediment
            # plains stay visually calm.
            rock_kind = int(dense["bed_kind"][cq_x, cq_z])
            rocky_substrate = rock_kind in (
                LAKEBED["stone"], LAKEBED["cobble"], LAKEBED["gravel"]
            )
            chance = (
                0.05
                + (0.10 if rocky_substrate else 0.0)
                + 0.08 * min(
                    1.0, float(dense["bed_relief"][cq_x, cq_z]) / 2.0
                )
            )
            if rng.random() > chance:
                continue

            radius = 1 + int(rng.random() < 0.24)
            wrote = 0
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if dx * dx + dz * dz > radius * radius + (radius - 1):
                        continue
                    ix, iz = qx + dx, qz + dz
                    if not (0 <= ix < x1 - x0 and 0 <= iz < z1 - z0):
                        continue
                    if not dense["water"][ix, iz]:
                        continue
                    mound = 1 + int(radius == 2 and abs(dx) + abs(dz) <= 1)
                    for dy in range(mound):
                        by = int(dense["bed_y"][ix, iz]) + 1 + dy
                        if by >= int(dense["y"][ix, iz]):
                            continue
                        bid = (
                            P.shore_cobble
                            if bhash(wx + dx, wz + dz, 4042 + dy) > 0.32
                            else P.bed_gravel
                        )
                        wrote += setb(wx + dx, by, wz + dz, bid)
            if wrote:
                stats["underwater_decor"] = stats.get("underwater_decor", 0) + 1

    # ---- พืชน้ำ: sparse writes หลังเติมน้ำเสร็จ -------------------------------
    seagrass, tall_seagrass, lily = aquatic_vegetation_masks(
        dense["depth"], dense["bed_kind"], dense["bed_relief"],
        x0=x0, z0=z0,
    )
    seagrass &= dense["water"]
    tall_seagrass &= dense["water"]
    lily &= dense["water"]
    if vegetation_only:
        # โหมดนี้ไม่ล้างคอลัมน์น้ำ พืชน้ำเดิมจึงยังอยู่ ถ้าเขียนซ้ำจะได้ของซ้อน
        seagrass = tall_seagrass = lily = np.zeros_like(seagrass)
    for qx, qz in zip(*np.nonzero(seagrass)):
        if setb(
            x0 + int(qx), int(dense["bed_y"][qx, qz]) + 1,
            z0 + int(qz), P.seagrass,
        ):
            stats["aqua"] = stats.get("aqua", 0) + 1
    for qx, qz in zip(*np.nonzero(tall_seagrass)):
        wx, wz = x0 + int(qx), z0 + int(qz)
        by = int(dense["bed_y"][qx, qz]) + 1
        if (
            by + 1 <= int(dense["y"][qx, qz])
            and setb(wx, by, wz, P.tall_seagrass_lower)
            and setb(wx, by + 1, wz, P.tall_seagrass_upper)
        ):
            stats["aqua"] = stats.get("aqua", 0) + 1
    for qx, qz in zip(*np.nonzero(lily)):
        if setb(
            x0 + int(qx), int(dense["y"][qx, qz]) + 1,
            z0 + int(qz), P.lily, only_air=True,
        ):
            stats["aqua"] = stats.get("aqua", 0) + 1

    # ---- พืชพื้นล่าง: วนเฉพาะคอลัมน์ที่ปลูกได้ -------------------------------
    # เดิมวนครบทุกคอลัมน์ แม้เป็นน้ำ/หิน/หิมะ การรวม mud ตรงนี้ยังแก้บั๊กเดิม
    # ที่ทำให้ branch wetland เข้าไม่ถึงเพราะ soil mask ไม่รวม mud
    mud = dense["cls"] == S.IDX["mud"]
    plantable = (
        (dense["soil"] | mud | dense["wet_shore"])
        & (dense["snow"] == 0)
        & ~dense["water"]
        & ~dense["ice"]
        & ~dense["beach"]
    )
    if not paint_vegetation or lake_only:
        plantable[:] = False
    off_x, off_z = x0 - px0, z0 - pz0
    for qx, qz in zip(*np.nonzero(plantable)):
        wx, wz = x0 + int(qx), z0 + int(qz)
        ix, iz = int(qx) + off_x, int(qz) + off_z
        y = int(dense["object_y"][qx, qz])
        k = int(dense["cls"][qx, qz])
        name = S.BLOCK_NAMES[k]
        e = float(dense["elev"][qx, qz])
        fp = float(forest_p[ix, iz])
        if name == "mud" or dense["wet_shore"][qx, qz]:
            zone = "wetland"
        elif fp > 0.28:
            zone = "forest"
        elif e > S.TREELINE:
            zone = "alpine"
        elif name in ("grass_block", "moss_block"):
            zone = "meadow"
        else:
            continue

        rv = bhash(wx, wz, 6)
        got = V.ground_cover(
            zone, rv,
            float(D["patch_a"][ix, iz]), float(D["patch_b"][ix, iz]),
            float(D["patch_c"][ix, iz]), float(D["damp"][ix, iz]),
            fp, e,
        )
        if not got:
            continue
        pn, two = got
        if pn == "sweet_berry_bush":
            a = 3 if bhash(wx, wz, 11) < 0.3 else 2
            if setb(wx, y + 1, wz, P.berry[a], only_air=True):
                stats["plants"] += 1
            continue
        if two:
            # พืชสองบล็อกต้องมีที่ว่างสองชั้นและตั้ง half ให้ถูก
            if setb(wx, y + 1, wz, P.prop_block(pn, {"half": "lower"}),
                    only_air=True):
                if setb(wx, y + 2, wz, P.prop_block(pn, {"half": "upper"}),
                        only_air=True):
                    stats["plants"] += 1
                else:
                    # ไม่มีที่ให้ครึ่งบน ใช้พืชบล็อกเดียวแทน
                    setb(wx, y + 1, wz,
                         P.plant("fern" if "fern" in pn else "short_grass"))
                    stats["plants"] += 1
        elif setb(wx, y + 1, wz, P.plant(pn), only_air=True):
            stats["plants"] += 1

    # ---- ต้นไม้ ----------------------------------------------------------------
    tree_slots = (
        V.tree_slots(
            x0 - P.tree_pad, x1 + P.tree_pad,
            z0 - P.tree_pad, z1 + P.tree_pad,
        )
        if paint_vegetation and not lake_only else ()
    )
    for tx, tz, layer, rng in tree_slots:
        ix, iz = tx - px0, tz - pz0
        if not (0 <= ix < elev.shape[0] and 0 <= iz < elev.shape[1]):
            continue
        fp = float(forest_p[ix, iz])
        if fp <= 0.02 or rng.random() > fp:
            continue
        if (
            cls[ix, iz] in (S.IDX["water"], S.IDX["ice"])
            or shore[ix, iz]
        ):
            continue

        e = float(elev[ix, iz])
        size = V.SIZE_OF[layer]

        # ไม้เด่นที่โผล่พ้นเรือนยอดขึ้นเฉพาะในป่าทึบที่ดินดี
        if layer == "emergent" and fp < 0.60:
            continue

        snowy = e > S.SNOW_PATCHY - 250 or snow_lv[ix, iz] > 0

        # ชนิดไม้มาจาก ecology.py ตัวเดียวกับที่พรีวิวใช้ — ห้ามตัดสินใหม่ที่นี่
        kind = E.SPECIES[int(E.tree_species(
            e, float(stand[ix, iz]), float(D["damp"][ix, iz]), rng.random()
        ))]
        if kind == "krummholz":
            size = "small"                      # สนแคระใกล้แนวไม้
        elif e > S.SUBALPINE and size == "big":
            size = "normal"                     # ใกล้แนวไม้ ไม้เด่นเตี้ยลง

        variants = P.schems.get(kind) or []
        wrote = 0

        if variants:
            t = V.pick_variant(variants, size, snowy, rng)
            sch = TS.rotate(t["blocks"], int(rng.integers(4)))

            # ฐานต้องอิงจุดต่ำสุดของผิวดินใต้โคนต้น ไม่ใช่คอลัมน์กลางต้นเดียว
            # ไม่งั้นบนพื้นลาดด้านที่ต่ำกว่าจะลอย (ยอมให้ฝั่งสูงจมดินแทน
            # ซึ่งมองแทบไม่ออก ต่างจากต้นไม้ลอยที่เห็นชัด)
            low = None
            for dx, dy, dz, nm, pr in sch:
                if dy > 2 or "leaves" in nm:
                    continue
                jx, jz = ix + dx, iz + dz
                if 0 <= jx < object_surface.shape[0] and 0 <= jz < object_surface.shape[1]:
                    v = int(object_surface[jx, jz])
                    low = v if low is None else min(low, v)
            base = (low if low is not None else int(object_surface[ix, iz])) + 1

            for dx, dy, dz, nm, pr in sch:
                wrote += setb(tx + dx, base + dy, tz + dz,
                              P.schem_id(nm, pr), soft=True)
        else:
            if kind == "krummholz":
                # ไม่มี schematic สนแคระในแพ็ก จึงใช้ทรงจากโค้ดเสมอ
                blocks, wood = V.krummholz(rng), "spruce"
            elif kind == "spruce":
                blocks, wood = V.spruce(rng, size != "small"), "spruce"
            elif kind == "birch":
                blocks, wood = V.birch(rng), "birch"
            else:
                blocks, wood = V.oak(rng), "oak"
            base = int(object_surface[ix, iz]) + 1
            lid, gid = P.leaf_id[wood], P.log_id[wood]
            for dx, dy, dz, what in blocks:
                wrote += setb(tx + dx, base + dy, tz + dz,
                              gid if what == "log" else lid, soft=True)
        if wrote:
            stats["trees"] += 1
            stats["tree_blocks"] += wrote
            stats[layer] = stats.get(layer, 0) + 1

    # ---- ของตกแต่งแบบ Geophilic: ไม้ล้ม ตอไม้ ก้อนหิน --------------------------
    # วางบนตารางหยาบ สุ่มจากพิกัดล้วน จึงต่อเนื่องข้าม region
    DSTEP = 13
    decor_x = (
        range((x0 - 6) // DSTEP, (x1 + 6) // DSTEP + 1)
        if paint_vegetation and not lake_only else ()
    )
    for gx in decor_x:
        for gz in range((z0 - 6) // DSTEP, (z1 + 6) // DSTEP + 1):
            dr = V.tree_rng(gx, gz, 777)
            wx = int((gx + 0.5 + (dr.random() - 0.5) * 0.9) * DSTEP)
            wz = int((gz + 0.5 + (dr.random() - 0.5) * 0.9) * DSTEP)
            ix, iz = wx - px0, wz - pz0
            if not (0 <= ix < elev.shape[0] and 0 <= iz < elev.shape[1]):
                continue
            if not soil[ix, iz]:
                continue
            e = float(elev[ix, iz])
            fp = float(forest_p[ix, iz])
            slope = float(D["slope"][ix, iz])
            damp = float(D["damp"][ix, iz])
            in_shore = bool(shore[ix, iz])
            y = int(surf_y[ix, iz]) + 1
            roll = dr.random()

            blocks = None
            if in_shore:
                if roll < 0.16:
                    blocks = V.boulder(dr, damp > 0.55)
            elif fp > 0.35 and slope < 30:
                wood = "spruce" if e > S.MONTANE else "oak"
                if roll < 0.16:
                    blocks = V.fallen_log(dr, wood)
                    # เห็ด/มอสงอกบนท่อนไม้ผุ — จุดเด่นของป่าแบบ Geophilic
                    extra = []
                    for bx, by, bz, nm, pr in blocks:
                        rr = dr.random()
                        if damp > 0.5 and rr < 0.30:
                            extra.append((bx, by + 1, bz,
                                          "brown_mushroom" if rr < 0.18
                                          else "moss_carpet", {}))
                    blocks = blocks + extra
                elif roll < 0.26:
                    blocks = V.stump(dr, wood)
                elif roll < 0.34:
                    blocks = V.boulder(dr, damp > 0.5)
                elif roll < 0.50:
                    blocks = V.bush(dr, "spruce" if e > S.SUBALPINE else "oak")
            elif e > S.TREELINE or slope > 26:
                if roll < 0.22:
                    blocks = V.boulder(dr, False)
            elif roll < 0.10:
                blocks = V.boulder(dr, damp > 0.55)

            if not blocks:
                continue
            n = 0
            for dx, dy, dz, nm, pr in blocks:
                jx, jz = ix + dx, iz + dz
                if not (0 <= jx < object_surface.shape[0] and 0 <= jz < object_surface.shape[1]):
                    continue
                # ให้ของตกแต่งวางตามผิวดินของแต่ละคอลัมน์ ไม่ลอยข้ามเนิน
                by = int(object_surface[jx, jz]) + 1
                n += setb(wx + dx, by + dy, wz + dz,
                          P.prop_block(nm, pr) if pr else P.plant(nm), soft=True)
            if n:
                stats["decor"] = stats.get("decor", 0) + 1

    stats["buffered_sections"] = (
        stats.get("buffered_sections", 0) + soft_buffer.flush()
    )

    # ---- biome + ธงแสง ---------------------------------------------------------
    for (cx, cz), ch in chunks.items():
        bx, bz = cx * CHUNK - px0, cz * CHUNK - pz0
        if not (lake_only or vegetation_only):
            write_chunk_biomes(ch, biome_idx, bx, bz, biome_ids)
        ch.misc["isLightOn"] = amulet_nbt.ByteTag(0)
        ch.misc["block_light"] = {}
        ch.misc["sky_light"] = {}
        ch.misc.pop("height_mapC", None)
        ch.changed = True
        level.put_chunk(ch, C.DIMENSION)

    return len(chunks)


def validate_saved_tile(level, surf_y, lc, wdepth, x0, x1, z0, z1,
                        px0, pz0, samples_per_kind=8, lake_only=False):
    """Read a small spatial sample back after purge and verify disk state."""
    water = lc == S.LC["water"]
    expected, _ = lake_surface_levels(surf_y, water, wdepth)
    sx = slice(x0 - px0, x1 - px0)
    sz = slice(z0 - pz0, z1 - pz0)
    water_core = water[sx, sz]
    expected_core = expected[sx, sz]

    points = []
    sample_kinds = [(water_core, "water")]
    if not lake_only:
        sample_kinds.append((~water_core, "land"))
    for mask, kind in sample_kinds:
        coords = np.argwhere(mask)
        if not len(coords):
            continue
        picks = np.linspace(
            0, len(coords) - 1, min(samples_per_kind, len(coords)), dtype=int
        )
        points.extend((coords[i], kind) for i in picks)

    errors = []
    air_names = {"air", "cave_air", "void_air"}
    for (qx, qz), kind in points:
        wx, wz = x0 + int(qx), z0 + int(qz)
        wy = int(expected_core[qx, qz])
        try:
            chunk = level.get_chunk(wx >> 4, wz >> 4, C.DIMENSION)
            name = level.block_palette[
                int(chunk.blocks[wx & 15, wy, wz & 15])
            ].base_name
        except Exception as exc:
            errors.append((wx, wy, wz, type(exc).__name__))
            continue
        valid = name == "water" if kind == "water" else (
            name not in air_names and name != "water"
        )
        if not valid:
            errors.append((wx, wy, wz, name))

    level.purge()
    if errors:
        raise RuntimeError(
            f"save validation failed at {len(errors)}/{len(points)} samples: "
            f"{errors[:5]}"
        )
    return len(points)


def select_pending_tiles(tiles, done, max_tiles=None):
    pending = [tile for tile in tiles if f"{tile[0]},{tile[2]}" not in done]
    if max_tiles is not None:
        pending = pending[:max(0, int(max_tiles))]
    return pending


def paint_fingerprint():
    """Fingerprint every input that can change a fullscale tile result."""
    paths = [
        os.path.join(HERE, name) for name in (
            "paint_surface.py",
            "surface.py",
            "vegetation.py",
            "tree_schematics.py",
            "config.py",
            "heightmap.png",
            "landcover.npz",
            "water_mask.npy",
            "water_depth.npy",
            "terrain_shape.py",
            "terrain_y.npy",
            "terrain_sub.npy",
        )
    ]
    tree_dir = os.path.join(HERE, "trees")
    if os.path.isdir(tree_dir):
        paths.extend(
            os.path.join(root, name)
            for root, _dirs, files in os.walk(tree_dir)
            for name in files
        )
    missing = [path for path in paths if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError(
            "paint fingerprint input missing: " + ", ".join(missing)
        )
    return content_fingerprint(paths, version=PAINT_PIPELINE_VERSION)


def main():
    if world_session_locked(C.WORLD_PATH):
        raise SystemExit(
            "world is currently open or locked; exit to the Minecraft main "
            "menu before running paint_surface.py"
        )

    early_meta = os.path.join(
        HERE,
        "paint_progress.vegetation.meta.json"
        if "--vegetation-only" in sys.argv
        else "paint_progress.meta.json",
    )
    if (
        "--resume" in sys.argv
        and "--patch" not in sys.argv
        and not os.path.exists(early_meta)
    ):
        raise SystemExit(
            "--resume refused: checkpoint metadata is missing; "
            "run without --resume to repaint every tile"
        )
    if "--reset-terrain" in sys.argv:
        if "--patch" not in sys.argv:
            raise SystemExit("--reset-terrain ใช้ได้เฉพาะเมื่อระบุ --patch")
        i = sys.argv.index("--patch")
        patch_args = sys.argv[i + 1:i + 4]
        if len(patch_args) != 3:
            raise SystemExit("--patch ต้องการ center_x center_z size")
        print("[RESET] สร้าง terrain สะอาดใน patch ก่อน paint ...")
        subprocess.run(
            [
                sys.executable, os.path.join(HERE, "build_terrain.py"),
                "--patch", *patch_args,
            ],
            check=True,
        )

    meta = S.load_meta()
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]

    print("อ่าน heightmap + landcover ...")
    hm = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))  # [z, x]
    lc_all = np.load(os.path.join(HERE, "landcover.npz"))["landcover"]  # [z, x]
    wm_path = os.path.join(HERE, "water_mask.npy")
    wm_all = np.load(wm_path) if os.path.exists(wm_path) else None
    if wm_all is not None and wm_all.shape != lc_all.shape:
        raise SystemExit(
            "water_mask.npy has a different shape from landcover.npz; "
            "run make_water.py again"
        )
    wd_path = os.path.join(HERE, "water_depth.npy")
    wd_all = np.load(wd_path) if os.path.exists(wd_path) else None
    if (wm_all is None) != (wd_all is None):
        raise SystemExit(
            "water_mask.npy and water_depth.npy must be regenerated together; "
            "run make_water.py"
        )
    if wd_all is None:
        raise SystemExit(
            "water_mask.npy and water_depth.npy are required; run make_water.py"
        )
    ty_path = os.path.join(HERE, "terrain_y.npy")
    tsub_path = os.path.join(HERE, "terrain_sub.npy")
    if not (os.path.exists(ty_path) and os.path.exists(tsub_path)):
        raise SystemExit(
            "ไม่พบ terrain_y.npy / terrain_sub.npy — รัน terrain_shape.py ก่อน"
        )
    ty_all = np.load(ty_path, mmap_mode="r")
    tsub_all = np.load(tsub_path, mmap_mode="r")
    if ty_all.shape != lc_all.shape:
        raise SystemExit(
            "terrain_y.npy มีขนาดไม่ตรงกับ landcover.npz; "
            "รัน terrain_shape.py ใหม่"
        )

    water_inputs = validate_water_inputs(wm_all, wd_all)
    if any(water_inputs[key] for key in (
        "water_without_depth", "nonwater_with_depth", "too_deep"
    )):
        raise SystemExit(
            "invalid water inputs: "
            f"{water_inputs}; run make_water.py again"
        )
    n = hm.shape[0]

    patch = None
    terrain_only = "--terrain-only" in sys.argv
    lake_only = "--lake-only" in sys.argv
    vegetation_only = "--vegetation-only" in sys.argv
    if lake_only and "--patch" not in sys.argv:
        raise SystemExit("--lake-only requires --patch to keep the test scoped")
    exclusive = [
        name for name, on in (
            ("--terrain-only", terrain_only),
            ("--lake-only", lake_only),
            ("--vegetation-only", vegetation_only),
        ) if on
    ]
    if len(exclusive) > 1:
        raise SystemExit(
            "use only one of " + ", ".join(exclusive)
        )
    if "--patch" in sys.argv:
        i = sys.argv.index("--patch")
        pcx, pcz, psz = (int(sys.argv[i + 1]), int(sys.argv[i + 2]), int(sys.argv[i + 3]))
        half = psz // 2
        patch = (max(0, pcx - half), min(n, pcx + half),
                 max(0, pcz - half), min(n, pcz + half))
        print(f"[PATCH] x {patch[0]}..{patch[1]}  z {patch[2]}..{patch[3]}")

    if wd_all is not None:
        if patch:
            qx0, qx1, qz0, qz1 = patch
            water_check = water_depth_preflight(
                hm[qz0:qz1, qx0:qx1],
                lc_all[qz0:qz1, qx0:qx1],
                wd_all[qz0:qz1, qx0:qx1],
                water_mask=(
                    wm_all[qz0:qz1, qx0:qx1]
                    if wm_all is not None else None
                ),
            )
        else:
            water_check = water_depth_preflight(
                hm, lc_all, wd_all, water_mask=wm_all
            )
        print(
            "water preflight: "
            f"ลึกสุด {water_check['max_requested']} บล็อก | "
            f"คอลัมน์ที่ชน y ต่ำสุด {water_check['clipped_columns']:,}"
        )
        if water_check["clipped_columns"]:
            print(
                "[เตือน] ความลึกน้ำถูกตัดรวม "
                f"{water_check['clipped_blocks']:,} บล็อก; "
                f"ต้องการ Y_FILL_BOTTOM <= {water_check['required_fill_bottom']} "
                f"(ปัจจุบัน {water_check['configured_fill_bottom']})"
            )

    # checkpoint แยกไฟล์ต่อโหมด — ถ้าใช้ไฟล์เดียวกัน การรัน --vegetation-only
    # ทั้งแผนที่จะมาร์ค tile ว่าเสร็จ แล้ว --resume ของ paint เต็มจะข้าม tile
    # ที่ยังไม่มีผิวดิน/น้ำ
    suffix = ".vegetation" if vegetation_only else ""
    done_file = os.path.join(HERE, f"paint_progress{suffix}.txt")
    meta_file = os.path.join(HERE, f"paint_progress{suffix}.meta.json")
    done = set()
    if patch is None:
        signature = paint_fingerprint()
        if "--resume" in sys.argv:
            metadata = load_progress_metadata(meta_file)
            if not os.path.exists(done_file) or metadata is None:
                raise SystemExit(
                    "--resume refused: checkpoint metadata is missing; "
                    "run without --resume to repaint every tile"
                )
            if metadata.get("signature") != signature:
                raise SystemExit(
                    "--resume refused: paint code or inputs changed since the "
                    "checkpoint; run without --resume to repaint every tile"
                )
            done = load_progress(done_file)
            print(f"resume: ข้าม {len(done)} region")

    print(f"เปิดโลก: {C.WORLD_PATH}")
    if terrain_only:
        print("[MODE] terrain-only: ข้ามพืช ต้นไม้ และของตกแต่ง")
    if lake_only:
        print("[MODE] lake-only: paint lakebed, water, rocks, and aquatic plants only")
    if vegetation_only:
        print(
            "[MODE] vegetation-only: ล้างชั้นเหนือผิวดินแล้วปลูกใหม่ "
            "(ไม่แตะผิวดิน/น้ำ/หิมะ/biome)"
        )
    level = amulet.load_level(C.WORLD_PATH)
    P = Painter(level)
    work_pad = max(PAD, P.tree_pad if not (terrain_only or lake_only) else PAD)
    if patch is None and "--resume" not in sys.argv:
        if os.path.exists(done_file):
            os.remove(done_file)
        save_progress_metadata(
            meta_file,
            {
                "schema": 1,
                "signature": signature,
                "pipeline_version": PAINT_PIPELINE_VERSION,
                "mode": "vegetation-only" if vegetation_only else "full",
                "region_blocks": RSIZE * CHUNK,
            },
        )

    if patch:
        tiles = [patch]
    else:
        rb = RSIZE * CHUNK
        tiles = [(x, min(x + rb, n), z, min(z + rb, n))
                 for x in range(0, n, rb) for z in range(0, n, rb)]
    max_tiles = None
    if "--max-tiles" in sys.argv:
        max_tiles = int(sys.argv[sys.argv.index("--max-tiles") + 1])
    tiles = select_pending_tiles(tiles, done, max_tiles)
    if max_tiles is not None:
        print(f"[LIMIT] ประมวลผล pending tiles สูงสุด {max_tiles}")

    stats = {"trees": 0, "tree_blocks": 0, "plants": 0, "snow": 0, "decor": 0}
    t0 = time.time()
    total_chunks = 0

    for i, (x0, x1, z0, z1) in enumerate(tiles, 1):
        key = f"{x0},{z0}"

        ax0, ax1 = max(0, x0 - work_pad), min(n, x1 + work_pad)
        az0, az1 = max(0, z0 - work_pad), min(n, z1 + work_pad)
        # ภาพเป็น [z, x] แต่โค้ดที่เหลือใช้ [x, z] ทั้งหมด จึง transpose ตรงนี้ครั้งเดียว
        sub = hm[az0:az1, ax0:ax1].T.astype(np.float32)
        elev = lo + sub / 65535.0 * (hi - lo)
        # ระดับผิวดินต้องมาจาก terrain_y.npy ตัวเดียวกับที่ build_terrain ใช้
        # ไม่ใช่คำนวณซ้ำจาก heightmap — dither ใน terrain_shape.py ทำให้สองสูตร
        # ให้คนละคำตอบ แล้วผิวดินจะไม่ตรงกับหินที่ถมไว้
        surf_y = ty_all[az0:az1, ax0:ax1].T.astype(np.int32)
        terrain_offset = (
            tsub_all[az0:az1, ax0:ax1].T.astype(np.float32) / 127.0
        )
        lc_source = lc_all[az0:az1, ax0:ax1]
        if wm_all is not None:
            lc_source = S.apply_water_mask(
                lc_source, wm_all[az0:az1, ax0:ax1]
            )
        lc = lc_source.T
        wdepth = (wd_all[az0:az1, ax0:ax1].T if wd_all is not None
                  else np.zeros_like(lc))

        total_chunks += process_region(
            level, P, surf_y, elev, lc, wdepth, x0, x1, z0, z1, stats, ax0, az0,
            paint_vegetation=not terrain_only,
            terrain_offset=terrain_offset,
            lake_only=lake_only,
            vegetation_only=vegetation_only,
            terrain_sub=terrain_offset,
        )

        level.save()
        level.purge()
        if "--skip-save-validation" not in sys.argv:
            checked = validate_saved_tile(
                level, surf_y, lc, wdepth, x0, x1, z0, z1, ax0, az0,
                lake_only=lake_only,
            )
            stats["validated_samples"] = (
                stats.get("validated_samples", 0) + checked
            )
        if patch is None:
            # checkpoint ต้องเกิดหลัง save สำเร็จ มิฉะนั้น --resume อาจข้าม
            # region ที่ถูกบันทึกลงดิสก์ไม่ครบ
            done.add(key)
            save_progress(done_file, done)

        pct = i / len(tiles)
        el = time.time() - t0
        sys.stdout.write(
            f"\r{i}/{len(tiles)} ({pct:5.1%})  chunks {total_chunks:,}  "
            f"ต้นไม้ {stats['trees']:,}  พืช {stats['plants']:,}  "
            f"ETA {(el/pct - el)/60:5.1f} นาที   "
        )
        sys.stdout.flush()

    print(f"\nเสร็จ — {total_chunks:,} chunks, ต้นไม้ {stats['trees']:,}, "
          f"พืช {stats['plants']:,}, ตกแต่ง {stats.get('decor',0):,}, "
          f"ใต้น้ำ {stats.get('underwater_decor',0):,}, "
          f"หิมะ {stats['snow']:,} "
          f"ใน {(time.time()-t0)/60:.1f} นาที")
    if stats.get("cleared"):
        print(f"ล้างพืช/ของตกแต่งเก่า {stats['cleared']:,} บล็อก")
    if stats.get("validated_samples"):
        print(f"save validation ผ่าน {stats['validated_samples']:,} samples")
    if stats.get("water_clipped_columns"):
        print(
            "[เตือน] ระหว่าง paint มีน้ำชนขอบล่าง "
            f"{stats['water_clipped_columns']:,} คอลัมน์ "
            f"และเสียความลึกรวม {stats.get('water_clipped_blocks', 0):,} บล็อก"
        )
    level.close()
    if "--repair-entities" in sys.argv:
        repair_entities()
    else:
        print("ข้าม entity repair (ใช้ --repair-entities เมื่อต้องการรันโดยตั้งใจ)")


if __name__ == "__main__":
    main()
