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
import water_ecology as WE
import vegetation as V
import tree_schematics as TS
from paint_world import repair_entities
from pipeline_progress import (
    content_fingerprint,
    load_progress,
    load_progress_metadata,
    save_progress,
    save_progress_metadata,
    working_set_mb,
    world_session_locked,
)
from hydrology_patch_io import (
    GLOBAL_PRODUCTS,
    LEGACY_PRODUCTS,
    load_hydrology_patch,
    overlay_window,
    water_product_path,
)

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK = 16
# chunks ต่อ region — ตัวคูณหลักของ RAM ไม่ใช่ numpy แต่เป็น chunk cache ของ amulet
#
# amulet ถือทุก chunk ที่แตะไว้ใน RAM จนกว่าจะ level.purge() ที่ปลาย region
# ที่ WORLD_HEIGHT=784 (สองเท่าวานิลลา) 1 chunk = 49 sections x 16^3 x uint32
# = ~800 KB  ->  RSIZE 32 = 1024 chunks = ~820 MB บวก object/biome/NBT ตอน save
# รวมจริงราว 1.0-1.5 GB ค้างตลอดทั้ง region
#
# 16 ให้ 256 chunks = ~205 MB แลกกับ save() ถี่ขึ้นสี่เท่า ซึ่งคุ้มมากบนเครื่อง
# ที่รันงานอื่นอยู่ด้วย  ห้ามเพิ่มกลับเป็น 32 โดยไม่วัด RAM จริงก่อน
RSIZE = 16
PAD = max(
    4,
    int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12))
    + int(getattr(C, "LAKE_LEVEL_RELAX_PASSES", 4)),
)  # lake transition radius plus shallow-level relaxation
TREE_PAD = 4            # fallback เมื่อไม่มี schematic
# ต้องสูงกว่าต้นไม้ที่สูงสุดในแพ็ก เท่ากับ CLEAR_HEADROOM ของ build_terrain.py
# ไม่งั้น --vegetation-only จะเหลือยอดใบไม้เก่าลอยค้าง
VEGETATION_HEADROOM = 64
PAINT_PIPELINE_VERSION = "2026-08-18-flow-bed-and-snow-tufts"

# หิมะที่บางกว่านี้ (หน่วย 1/8 บล็อก) ยอมให้มีช่องให้พืชโผล่ได้
SNOW_TUFT_MAX = 3
# สัดส่วนของ cell ในหย่อมที่ถูกเจาะ — 0.45 ให้ภาพ "หิมะเป็นหย่อมสลับกอหญ้า"
# ถ้าสูงกว่านี้หิมะบางจะหายไปจนดูเหมือนไม่เคยตก
SNOW_TUFT_SHARE = 0.45

# ตารางวัสดุก้นน้ำอยู่ที่ `water_ecology` — ทั้งทะเลสาบ (ตัดสินจากความลึก) และ
# ลำน้ำ (ตัดสินจากความชัน) ต้องใช้รหัสชุดเดียวกัน
LAKEBED_NAMES = WE.LAKEBED_NAMES
LAKEBED = WE.LAKEBED


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
        # วานิลลาไม่มีกก จึงใช้ sugar cane เป็นทรงแทน แต่กระจายเป็นกอเตี้ย
        # ตามตลิ่งชื้น ไม่ใช้กฎฟาร์มที่ขึ้นเป็นแนวยาวสม่ำเสมอ
        self.reed = self.bid("sugar_cane")
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
        # ชั้นวัสดุบนหน้าตั้งของผา เดิม build_terrain ถมหินล้วนและ terrain pass
        # เปลี่ยนเฉพาะบล็อกบนสุด ทำให้ด้านข้างสูงหลายบล็อกเป็นกำแพงสีเดียว
        self.cliff_face = [
            self.bid(n) for n in (
                "stone", "andesite", "calcite", "dripstone_block",
                "tuff", "mossy_cobblestone", "diorite",
                "dead_brain_coral_block",
            )
        ]

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


def lakebed_materials(depth, bed_relief, lake_mask, x0=0, z0=0,
                      elev_m=None, flow_index=None, pool_mask=None):
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
    # The broad/medium fields are appropriate for a lake basin but much wider
    # than a 1-3 block stream.  Keep that field for lake sediment, and derive a
    # second world-coordinate-anchored field for coherent riffle/bar patches in
    # the stream.  Scale 4 is still smooth across several cells, not white-noise
    # speckle, and the fixed world origin keeps tile boundaries deterministic.
    fine = S.smooth_noise(x0, z0, shape, 4.0, 7741, 2)
    stream_texture = np.clip(
        0.35 * (broad * 0.5 + 0.5)
        + 0.25 * (medium * 0.5 + 0.5)
        + 0.40 * (fine * 0.5 + 0.5),
        0.0, 0.999,
    )
    texture = np.clip(
        0.55 * (broad * 0.5 + 0.5)
        + 0.25 * (medium * 0.5 + 0.5)
        + 0.20 * (S.smooth_noise(x0, z0, shape, 7.0, 7741, 2) * 0.5 + 0.5),
        0.0, 0.999,
    )
    effective_depth = depth.astype(np.float32) + broad * 2.4 + medium * 0.8

    result = np.full(shape, LAKEBED["clay"], dtype=np.uint8)
    wet = depth > 0
    stream = wet & ~lake_mask
    # ก้นลำน้ำตัดสินจาก *ความชัน* ไม่ใช่ hash: แก่งบนที่ชันพัดตะกอนละเอียดออก
    # หมดจนเหลือหินกับกรวด ส่วนช่วงเอื่อยเก็บทรายไว้ได้  ก่อนหน้านี้ทุก cell ของ
    # ลำน้ำได้ค่าเดียวกัน (`LAKEBED["stream"]`) แล้วสุ่มบล็อกจาก palette ดิน/
    # พอดโซล/มอส ซึ่งเป็นก้นน้ำที่อ่านออกทันทีว่าไม่จริงเมื่ออยู่ใต้แก่ง
    if flow_index is None:
        result[stream] = LAKEBED["stream"]
    else:
        streambed = WE.streambed_materials(
            flow_index, depth, stream, pool_mask=pool_mask,
            texture=stream_texture,
        )
        result[stream] = streambed[stream]
        # A mapped lake mouth is a depositional transition, not an endless
        # sand strip.  On still/slack cells touching the standing-water mask,
        # raise the coarse fraction so at least a gravel bar can survive beside
        # the fine deposit.  This is deterministic and spatial (cardinal
        # contact), unlike adding random speckles to the whole stream.
        lake_touch = np.zeros(shape, dtype=bool)
        lake_touch |= lake_mask
        for _ in range(2):
            expanded = lake_touch.copy()
            expanded[1:] |= lake_touch[:-1]
            expanded[:-1] |= lake_touch[1:]
            expanded[:, 1:] |= lake_touch[:, :-1]
            expanded[:, :-1] |= lake_touch[:, 1:]
            lake_touch = expanded
        mouth = (
            stream & lake_touch & (depth >= 2)
            & (np.asarray(flow_index, dtype=np.int32) <= WE.SLACK_MAX)
        )
        result[mouth & (stream_texture < 0.60)] = LAKEBED["gravel"]
        result[mouth & (stream_texture >= 0.85)] = LAKEBED["cobble"]

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

    # ทะเลสาบเหนือแนวไม้เป็นแอ่งน้ำแข็งละลายบนหินเปล่า ไม่มีตะกอนละเอียดสะสม
    # ทราย/ดินเหนียว/โคลนจึงต้องกลายเป็นกรวดกับหินก้อน มิฉะนั้นจะเห็นก้นทราย
    # กลางลานหินที่ 2,000 ม. ด้วยเหตุผลเดียวกับชายหาดบนภูเขา
    if elev_m is not None:
        bare = np.asarray(elev_m, dtype=np.float32) >= S.TREELINE
        fine = np.isin(
            result,
            np.asarray(
                [LAKEBED[n] for n in ("sand", "clay", "mud")], dtype=np.uint8
            ),
        )
        result[bare & fine & (texture < 0.5)] = LAKEBED["gravel"]
        result[bare & fine & (texture >= 0.5)] = LAKEBED["cobble"]
    return result


def aquatic_vegetation_masks(depth, bed_kind, bed_relief, x0=0, z0=0,
                             flow_index=None):
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
    lily_roll = bhash_array(wx, wz, 28)

    density = np.zeros(shape, dtype=np.float32)
    density[habitat > 0.54] = 0.11
    density[habitat > 0.62] = 0.28
    suitable_bed = np.isin(
        bed_kind,
        np.asarray([
            LAKEBED["sand"], LAKEBED["gravel"], LAKEBED["clay"],
        ], dtype=np.uint8),
    )
    # Keep the top water block intact. Besides making saved-world validation
    # unambiguous, this preserves a real source block for the scheduled fluid
    # update when a shallow channel is first loaded.
    plantable = (
        (depth >= 2)
        & (depth <= 4)
        & (bed_relief <= 1)
        & suitable_bed
    )
    # หญ้าน้ำยึดพื้นไม่ได้ในกระแสแรง และใบบัวจะถูกพัดไปเลย — ก่อนหน้านี้กฎมี
    # แต่ความลึก ผลคือแก่งบนที่ชันมีทุ่งหญ้าทะเลอยู่ก้น
    if flow_index is not None:
        flow_index = np.asarray(flow_index, dtype=np.uint8)
        plantable &= WE.submerged_plants_allowed(flow_index)
    vegetation = plantable & (roll < density)
    tall = vegetation & (depth >= 3) & (tall_roll < 0.24)
    short = vegetation & ~tall

    # Hallstätter See has steep, open alpine shores rather than broad lily
    # marshes. Keep small clusters in sheltered one-block coves, but use an
    # independent roll so the trace population is not accidentally restricted
    # to the final 0.2% tail of the seagrass distribution.
    lily = (
        (WE.floating_plants_allowed(flow_index) if flow_index is not None
         else np.ones(shape, dtype=bool))
        & (depth == 1)
        & (bed_relief == 0)
        & np.isin(
            bed_kind,
            np.asarray([LAKEBED["clay"], LAKEBED["mud"]], dtype=np.uint8),
        )
        & (habitat > 0.68)
        & (lily_roll < np.where(habitat > 0.76, 0.045, 0.012))
    )
    return short, tall, lily


def riparian_reed_heights(water, water_depth, ground_ok, x0=0, z0=0,
                          flow_index=None):
    """Return 0–3 block reed clumps beside shallow water.

    Minecraft has no cattail/reed block, so the painter uses short sugar-cane
    clumps as a silhouette substitute. Reeds occupy dry bank columns adjacent
    to 1–2 block water, with broad world-space habitat noise keeping them out
    of exposed shore sections and coordinate hashes making tiled runs stable.
    """
    water = np.asarray(water, dtype=bool)
    water_depth = np.asarray(water_depth, dtype=np.int32)
    ground_ok = np.asarray(ground_ok, dtype=bool)
    if water.shape != water_depth.shape or water.shape != ground_ok.shape:
        raise ValueError(
            "water, water_depth, and ground_ok must have the same shape"
        )

    shallow = water & (water_depth >= 1) & (water_depth <= 2)
    # ตลิ่งข้างน้ำเชี่ยวเป็นหินเปล่า กกขึ้นได้เฉพาะข้างน้ำที่เอื่อยพอ
    if flow_index is not None:
        shallow &= WE.reeds_allowed(np.asarray(flow_index, dtype=np.uint8))
    beside_shallow = np.zeros(water.shape, dtype=bool)
    beside_shallow[1:] |= shallow[:-1]
    beside_shallow[:-1] |= shallow[1:]
    beside_shallow[:, 1:] |= shallow[:, :-1]
    beside_shallow[:, :-1] |= shallow[:, 1:]

    shape = water.shape
    habitat = (
        0.76 * (S.smooth_noise(x0, z0, shape, 46.0, 8963, 3) * 0.5 + 0.5)
        + 0.24 * (S.smooth_noise(x0, z0, shape, 17.0, 9011, 2) * 0.5 + 0.5)
    )
    wx = np.arange(x0, x0 + shape[0], dtype=np.int64)[:, None]
    wz = np.arange(z0, z0 + shape[1], dtype=np.int64)[None, :]
    roll = bhash_array(wx, wz, 38)
    height_roll = bhash_array(wx, wz, 48)

    density = np.zeros(shape, dtype=np.float32)
    density[habitat > 0.58] = 0.10
    density[habitat > 0.66] = 0.24
    occupied = ground_ok & ~water & beside_shallow & (roll < density)
    heights = np.zeros(shape, dtype=np.uint8)
    heights[occupied] = 1
    heights[occupied & (height_roll < 0.62)] = 2
    heights[occupied & (height_roll < 0.16)] = 3
    return heights


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


def cliff_face_depths(height, minimum_drop=2):
    """Return exposed vertical depth against the lowest cardinal neighbour.

    ``minimum_drop`` รับได้ทั้งค่าเดียวและ array ต่อ cell — ริมลำน้ำต้องใช้เกณฑ์
    สูงกว่าที่อื่น ดู STREAM_CLIFF_MIN_DROP
    """
    height = np.asarray(height, dtype=np.int32)
    low = height.copy()
    low[1:, :] = np.minimum(low[1:, :], height[:-1, :])
    low[:-1, :] = np.minimum(low[:-1, :], height[1:, :])
    low[:, 1:] = np.minimum(low[:, 1:], height[:, :-1])
    low[:, :-1] = np.minimum(low[:, :-1], height[:, 1:])
    depth = np.maximum(height - low, 0).astype(np.int16)
    depth[depth < np.asarray(minimum_drop, dtype=np.int16)] = 0
    return depth


# หน้าตั้งริมลำน้ำต้องสูงเท่านี้ถึงจะนับเป็น "หน้าผา" แล้วได้ palette ชั้นหิน
#
# การขุดร่องน้ำสร้างหน้าตั้งสองข้างเป็นปกติ เกณฑ์ 2 บล็อกทั่วไปจึงทำให้ตลิ่ง
# ลำธารทุกสายถูกทาเป็นชั้นหินลายทาง (dripstone/andesite/tuff/coral สลับกัน)
# ทั้งที่มันควรเป็นหิน-กรวด-ดินแบบตลิ่งลำธาร  ยิ่งก้นน้ำลึกขึ้นหน้าตั้งยิ่งสูง
# วัดแล้ว: ตลิ่งลำน้ำที่เข้าเกณฑ์หน้าผาเพิ่มจาก 102,416 เป็น 187,585 cells
#
# ขยับเกณฑ์เฉพาะรอบลำน้ำ ไม่แตะที่อื่น — ถ้าขยับทั้งแผนที่ หน้าผาภูเขาจริงจะ
# หายไป 82% ซึ่งเป็นฟีเจอร์ที่ตั้งใจใส่ไว้กันผาเป็นกำแพงสีเดียว
STREAM_CLIFF_MIN_DROP = 4
# ระยะรอบลำน้ำที่ใช้เกณฑ์นั้น — พอครอบตลิ่งที่การขุดร่องสร้างขึ้น
STREAM_CLIFF_REACH = 2


def stream_cliff_minimum(flowing_water, reach=STREAM_CLIFF_REACH,
                         normal=2, near_stream=STREAM_CLIFF_MIN_DROP):
    """เกณฑ์ความสูงหน้าผาต่อ cell — สูงขึ้นเฉพาะรอบลำน้ำ"""
    near = np.asarray(flowing_water, dtype=bool).copy()
    for _ in range(max(0, int(reach))):
        grown = near.copy()
        grown[1:] |= near[:-1]
        grown[:-1] |= near[1:]
        grown[:, 1:] |= near[:, :-1]
        grown[:, :-1] |= near[:, 1:]
        near = grown
    return np.where(near, int(near_stream), int(normal)).astype(np.int16)


_BEDROCK_KEYS = (
    "calcite", "diorite", "andesite", "stone", "tuff", "deepslate",
)
_FRACTURED_ROCK_KEYS = (
    "cobble", "cob_deep", "dripstone", "dead_brain",
)
_ROCK_DEPOSIT_KEYS = ("gravel", "clay")
_ROCK_COATING_KEYS = ("mossy_cob", "pale_moss")
_GEOLOGIC_TRANSITION_KEYS = (
    _BEDROCK_KEYS
    + _FRACTURED_ROCK_KEYS
    + _ROCK_DEPOSIT_KEYS
    + _ROCK_COATING_KEYS
)
_BEDROCK_IDS = np.asarray(
    [S.IDX[k] for k in _BEDROCK_KEYS], dtype=np.uint8
)
_GEOLOGIC_TRANSITION_IDS = np.asarray(
    [S.IDX[k] for k in _GEOLOGIC_TRANSITION_KEYS], dtype=np.uint8
)

# Only adjacent rock types in the light -> neutral -> dark sequence may spread
# into one another. Deposits, coatings, and fractured blocks never become the
# majority "bedrock" merely because they happen to touch it.
_BEDROCK_COMPATIBLE = np.zeros(
    (len(S.KEYS), len(S.KEYS)), dtype=bool
)
for _left, _right in (
    ("calcite", "diorite"),
    ("diorite", "andesite"),
    ("andesite", "stone"),
    ("stone", "tuff"),
    ("tuff", "deepslate"),
):
    _a, _b = S.IDX[_left], S.IDX[_right]
    _BEDROCK_COMPATIBLE[_a, _b] = True
    _BEDROCK_COMPATIBLE[_b, _a] = True
for _key in _BEDROCK_KEYS:
    _BEDROCK_COMPATIBLE[S.IDX[_key], S.IDX[_key]] = True

_BEDROCK_BRIDGE = np.full(len(S.KEYS), S.IDX["stone"], dtype=np.uint8)
_BEDROCK_COATING = np.full(
    len(S.KEYS), S.IDX["mossy_cob"], dtype=np.uint8
)
for _key in ("calcite", "diorite"):
    _BEDROCK_BRIDGE[S.IDX[_key]] = S.IDX["diorite"]
    _BEDROCK_COATING[S.IDX[_key]] = S.IDX["pale_moss"]
_BEDROCK_BRIDGE[S.IDX["andesite"]] = S.IDX["andesite"]
_BEDROCK_BRIDGE[S.IDX["stone"]] = S.IDX["stone"]
for _key in ("tuff", "deepslate"):
    _BEDROCK_BRIDGE[S.IDX[_key]] = S.IDX["tuff"]

_SLAB_TRANSITION_BRIDGE = np.arange(len(S.KEYS), dtype=np.uint8)
_SLAB_TRANSITION_BRIDGE[S.IDX["calcite"]] = S.IDX["diorite"]
_SLAB_TRANSITION_BRIDGE[S.IDX["dripstone"]] = S.IDX["tuff"]
_SLAB_TRANSITION_BRIDGE[S.IDX["pale_moss"]] = S.IDX["mossy_cob"]
_PROTECTED_TRANSITION_IDS = np.asarray(
    [S.IDX[k] for k in ("water", "ice", "blue_ice", "snow", "snow_thin")],
    dtype=np.uint8,
)


def contextual_surface_blend(
        cls, damp, slope, x0=0, z0=0, terrain_sub=None, snow=None):
    """Blend neighbours along geologically compatible transition paths."""
    cls = np.asarray(cls, dtype=np.uint8)
    damp = np.asarray(damp, dtype=np.float32)
    slope = np.asarray(slope, dtype=np.float32)
    if cls.shape != damp.shape or cls.shape != slope.shape:
        raise ValueError("cls, damp, and slope must have the same shape")
    if terrain_sub is not None:
        terrain_sub = np.asarray(terrain_sub, dtype=np.float32)
        if terrain_sub.shape != cls.shape:
            raise ValueError("terrain_sub must have the same shape as cls")
    if snow is not None:
        snow = np.asarray(snow)
        if snow.shape != cls.shape:
            raise ValueError("snow must have the same shape as cls")

    padded = np.pad(cls, 1, mode="edge")
    neighbours = np.stack((
        padded[:-2, 1:-1], padded[2:, 1:-1],
        padded[1:-1, :-2], padded[1:-1, 2:],
    ))
    bedrock = np.isin(cls, _BEDROCK_IDS)
    rock = np.isin(cls, _GEOLOGIC_TRANSITION_IDS)
    soil = np.isin(cls, S.SOIL_IDS)
    neighbour_rock = np.isin(
        neighbours, _GEOLOGIC_TRANSITION_IDS
    ).any(axis=0)
    neighbour_soil = np.isin(neighbours, S.SOIL_IDS).any(axis=0)
    protected = np.isin(cls, _PROTECTED_TRANSITION_IDS)

    broad = S.smooth_noise(x0, z0, cls.shape, 18.0, 104729, octaves=3)
    fine = S.white_noise(x0, z0, cls.shape, 104759)
    choice = np.clip(
        0.78 * (broad * 0.5 + 0.5) + 0.22 * fine, 0.0, 0.999
    )

    result = cls.copy()

    # Soil patches may merge with any soil. Bedrock patches may merge only
    # through one edge of the compatibility graph above.
    dominant_soil = cls.copy()
    dominant_soil_count = np.zeros(cls.shape, dtype=np.uint8)
    for material_id in S.SOIL_IDS:
        count = np.sum(
            neighbours == material_id, axis=0, dtype=np.uint8
        )
        better = count > dominant_soil_count
        dominant_soil[better] = material_id
        dominant_soil_count[better] = count[better]
    soil_limit = np.where(
        dominant_soil_count >= 4, 1.0,
        np.where(dominant_soil_count == 3, 0.72, 0.30),
    )
    adopt_soil = (
        soil
        & (dominant_soil != cls)
        & (dominant_soil_count >= 2)
        & (choice < soil_limit)
        & ~protected
    )
    result[adopt_soil] = dominant_soil[adopt_soil]

    dominant_bedrock = cls.copy()
    dominant_bedrock_count = np.zeros(cls.shape, dtype=np.uint8)
    for material_id in _BEDROCK_IDS:
        count = np.sum(
            neighbours == material_id, axis=0, dtype=np.uint8
        )
        better = (
            bedrock
            & _BEDROCK_COMPATIBLE[cls, material_id]
            & (count > dominant_bedrock_count)
        )
        dominant_bedrock[better] = material_id
        dominant_bedrock_count[better] = count[better]
    bedrock_limit = np.where(
        dominant_bedrock_count >= 4, 1.0,
        np.where(dominant_bedrock_count == 3, 0.72, 0.46),
    )
    adopt_bedrock = (
        bedrock
        & (dominant_bedrock != cls)
        & (dominant_bedrock_count >= 2)
        & (choice < bedrock_limit)
        & ~protected
    )
    result[adopt_bedrock] = dominant_bedrock[adopt_bedrock]

    # Anchor soil/rock contacts to the actual neighbouring bedrock. Loose
    # deposits and moss therefore inherit the local light/neutral/dark family.
    anchor = np.full(cls.shape, S.IDX["stone"], dtype=np.uint8)
    anchor_count = np.zeros(cls.shape, dtype=np.uint8)
    for material_id in _BEDROCK_IDS:
        count = np.sum(
            neighbours == material_id, axis=0, dtype=np.uint8
        )
        better = count > anchor_count
        anchor[better] = material_id
        anchor_count[better] = count[better]
    missing_anchor = bedrock & (anchor_count == 0)
    anchor[missing_anchor] = cls[missing_anchor]
    bridge = _BEDROCK_BRIDGE[anchor]
    coating = _BEDROCK_COATING[anchor]

    soil_edge = soil & neighbour_rock & ~protected
    rock_edge = rock & neighbour_soil & ~protected

    # Soil -> rock: fines, loose fragments, damp coating, then local bedrock.
    result[soil_edge & (choice < 0.22)] = S.IDX["coarse"]
    result[soil_edge & (choice >= 0.22) & (choice < 0.48)] = S.IDX["gravel"]
    result[
        soil_edge & (choice >= 0.48) & (choice < 0.68) & (damp > 0.58)
    ] = S.IDX["moss"]
    dry_middle = (
        soil_edge & (choice >= 0.48) & (choice < 0.68) & (damp <= 0.58)
    )
    result[dry_middle] = bridge[dry_middle]
    upper_edge = soil_edge & (choice >= 0.68) & (choice < 0.86)
    upper_wet = upper_edge & (damp > 0.58)
    result[upper_wet] = coating[upper_wet]
    result[upper_edge & ~upper_wet] = bridge[upper_edge & ~upper_wet]
    exposed = soil_edge & (choice >= 0.86)
    result[exposed] = bridge[exposed]

    # Rock -> soil: steep contacts retain local bedrock; gentle contacts
    # weather into deposits. Wet contacts use a family-aware coating.
    steep = rock_edge & (slope >= 36.0)
    steep_wet = steep & (damp > 0.62)
    result[steep_wet] = coating[steep_wet]
    result[steep & ~steep_wet] = bridge[steep & ~steep_wet]
    gentle = rock_edge & (slope < 36.0)
    result[gentle & (choice < 0.38)] = S.IDX["gravel"]
    result[gentle & (choice >= 0.38) & (damp <= 0.62)] = S.IDX["coarse"]
    result[gentle & (choice >= 0.38) & (damp > 0.62)] = S.IDX["moss"]

    # Some natural full blocks have no vanilla slab. At a transition that
    # genuinely needs the half-step, choose the nearest compatible slab family.
    if terrain_sub is not None:
        needs_slab = (
            (np.abs(terrain_sub) > 0.25)
            & (result != cls)
            & ~S.HAS_SLAB[result]
            & ~protected
        )
        if snow is not None:
            needs_slab &= snow == 0
        slab_bridge = _SLAB_TRANSITION_BRIDGE[result]
        can_bridge = slab_bridge != result
        use_bridge = needs_slab & can_bridge
        result[use_bridge] = slab_bridge[use_bridge]
    return result


def prepare_dense_fields(P, surf_y, elev, cls, snow_lv, soil, wdepth,
                         shore, shore_p, decor, x0, x1, z0, z1, px0, pz0,
                         terrain_sub=None, water_surface_y=None,
                         global_lake_mask=None, waterfall_top_y=None,
                         waterfall_lip_mask=None,
                         waterfall_pool_mask=None,
                         flowing_water_mask=None,
                         flow_index=None, landcover=None):
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
    blended_cls = contextual_surface_blend(
        cls, decor["damp"], decor["slope"], x0=px0, z0=pz0,
        terrain_sub=terrain_sub, snow=snow_lv,
    )
    cls_core = blended_cls[sx, sz]
    snow_core = snow_lv[sx, sz].copy()
    # ---- หิมะบางต้องมีที่ให้พืชโผล่ ----
    #
    # `plantable` ตัด `snow > 0` ทั้งหมด ผลคือทุ่งที่มีหิมะบางแค่ 1/8 บล็อกโล่ง
    # เกลี้ยงทั้งผืน — ในภาพจริงหิมะบางเป็นหย่อม มีกอหญ้า/พุ่มโผล่พ้นขึ้นมา
    # (ลมกวาดสันและโคนกอ) ตรงนี้เจาะ "ช่องหิมะ" เป็นหย่อมตามภูมิประเทศ แล้ว
    # ทั้งตัวเขียนหิมะและตัวปลูกพืชก็เห็นตรงกันเอง เพราะอ่าน mask เดียวกัน
    thin = (snow_core > 0) & (snow_core <= SNOW_TUFT_MAX)
    if thin.any():
        gap_noise = S.smooth_noise(x0, z0, shape, 19.0, 7321, 2) * 0.5 + 0.5
        wx_gap = np.arange(x0, x1, dtype=np.int64)[:, None]
        wz_gap = np.arange(z0, z1, dtype=np.int64)[None, :]
        gap_roll = bhash_array(wx_gap, wz_gap, 57)
        # หย่อมกว้าง ๆ จาก noise แล้วเจาะทีละบล็อกด้วย hash — ได้ขอบที่ไม่เรียบ
        snow_core = np.where(
            thin & (gap_noise > 0.46) & (gap_roll < SNOW_TUFT_SHARE),
            0, snow_core,
        ).astype(snow_core.dtype)
    soil_core = np.isin(cls_core, S.SOIL_IDS)
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
    if water_surface_y is None:
        adjusted_full, lake_full = lake_surface_levels(
            surf_y, water_full, wdepth
        )
    else:
        water_surface_y = np.asarray(water_surface_y, dtype=np.int32)
        if water_surface_y.shape != surf_y.shape:
            raise ValueError(
                "water_surface_y and surf_y must have the same shape"
            )
        adjusted_full = np.asarray(surf_y, dtype=np.int32).copy()
        adjusted_full[water_full] = water_surface_y[water_full]
        if global_lake_mask is None:
            _unused, lake_full = lake_surface_levels(
                surf_y, water_full, wdepth
            )
        else:
            lake_full = np.asarray(global_lake_mask, dtype=bool)
            if lake_full.shape != surf_y.shape:
                raise ValueError(
                    "global_lake_mask and surf_y must have the same shape"
                )
    level_adjusted = lake_full[sx, sz]
    y = adjusted_full[sx, sz]
    # ริมลำน้ำใช้เกณฑ์หน้าผาสูงกว่าที่อื่น ไม่งั้นตลิ่งที่เกิดจากการขุดร่องจะถูก
    # ทาเป็นชั้นหินลายทางทั้งสาย (ดู STREAM_CLIFF_MIN_DROP)
    stream_full = (
        np.zeros(surf_y.shape, dtype=bool)
        if flowing_water_mask is None
        else np.asarray(flowing_water_mask, dtype=bool)
    )
    cliff_minimum = stream_cliff_minimum(stream_full)
    cliff_face_depth = cliff_face_depths(
        adjusted_full, minimum_drop=cliff_minimum
    )[sx, sz]
    cliff_face_depth[water_mask | ice_mask] = 0

    surface_id = np.take(
        np.asarray(P.surface_ids, dtype=np.uint32), cls_core
    ).copy()
    material_cls = cls_core.copy()

    # ชายฝั่งคำนวณพร้อมกันทั้ง region เพื่อลด hash/setb รายบล็อก
    shore_texture = np.clip(0.68 * patch_a + 0.32 * patch_b, 0.0, 1.0)
    beach = (
        ~water_mask
        & ~ice_mask
        & shore[sx, sz]
        & (shore_texture < shore_p[sx, sz])
    )
    cobble = beach & (bhash_array(wx, wz, 9) < 0.06)
    # ทรายชายหาดคือตะกอนละเอียดที่สะสมบนพื้นดิน ไม่ใช่สิ่งที่โผล่กลางลานหิน
    #
    # เดิมเลือกทราย/กรวดจาก noise ล้วน ๆ โดยไม่ดูว่าพื้นเดิมเป็นอะไรและอยู่สูง
    # แค่ไหน ลำธารที่ไหลผ่านหน้าผาหินปูนที่ 2,000 ม. จึงได้หาดทรายเหมือนทะเลสาบ
    # ในหุบเขา — เห็นเป็นแถบทรายกลางภูเขาหินซึ่งผิดทั้งธรณีวิทยาและสายตา
    #
    # เงื่อนไขสองชั้น: ต้องอยู่บนพื้นที่เป็นดินจริง และต่ำกว่าแนวไม้ ที่สูงกว่า
    # นั้นเป็นเขตกัดกร่อนเชิงกล มีแต่กรวดกับหินก้อน
    sand_ground = np.isin(material_cls, S.SOIL_IDS) & (
        elev_core < S.TREELINE
    )
    sand = beach & ~cobble & (patch_b > 0.60) & sand_ground
    gravel = beach & ~cobble & ~sand
    surface_id[cobble] = P.shore_cobble
    surface_id[sand] = P.bed_sand
    surface_id[gravel] = P.bed_gravel
    material_cls[cobble] = S.IDX["cobble"]
    # sand/gravel have no natural slab; gravel is the no-slab representative
    material_cls[sand | gravel] = S.IDX["gravel"]

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
    material_cls[wet_mud] = S.IDX["mud"]
    material_cls[wet_moss] = S.IDX["moss"]

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
    flow_core = (
        None if flow_index is None
        else np.asarray(flow_index, dtype=np.uint8)[sx, sz]
    )
    # แอ่งใต้น้ำตกต้องรู้ก่อนเลือกวัสดุ — มันถูกครูดตลอดเวลาแม้ผิวน้ำจะนิ่ง
    waterfall_pool = (
        np.zeros(shape, dtype=bool)
        if waterfall_pool_mask is None
        else np.asarray(waterfall_pool_mask, dtype=bool)[sx, sz]
    )
    bed_kind = lakebed_materials(
        depth, bed_relief, level_adjusted, x0=x0, z0=z0, elev_m=elev_core,
        flow_index=flow_core, pool_mask=waterfall_pool,
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

    if waterfall_top_y is None:
        waterfall_top = np.full(
            shape, np.iinfo(np.int16).min, dtype=np.int16
        )
    else:
        waterfall_top_y = np.asarray(waterfall_top_y, dtype=np.int16)
        if waterfall_top_y.shape != surf_y.shape:
            raise ValueError(
                "waterfall_top_y and surf_y must have the same shape"
            )
        waterfall_top = waterfall_top_y[sx, sz]
    waterfall_foot = water_mask & (waterfall_top > y)
    waterfall_lip = (
        np.zeros(shape, dtype=bool)
        if waterfall_lip_mask is None
        else np.asarray(waterfall_lip_mask, dtype=bool)[sx, sz]
    )
    rock_feature = water_mask & (
        waterfall_foot | waterfall_lip | waterfall_pool
    )
    bed_id[rock_feature] = P.bed_stone
    flowing_water = (
        np.zeros(shape, dtype=bool)
        if flowing_water_mask is None
        else np.asarray(flowing_water_mask, dtype=bool)[sx, sz] & water_mask
    )

    ice_band = np.minimum((patch_c * 3).astype(np.intp), 2)
    ice_top = np.choose(
        ice_band,
        np.asarray([P.ice_edge, P.ice_mid, P.ice_core], dtype=np.uint32),
    )

    if terrain_sub is None:
        slab = np.zeros(shape, dtype=np.int8)
    else:
        slab = slab_states(
            terrain_sub[sx, sz], material_cls, water_mask, ice_mask,
            snow=snow_core,
        )
    # ผิวที่ของอื่นต้องยืนอยู่บน — สูงขึ้นหนึ่งเมื่อมี slab วางทับ
    object_y = y + (slab > 0)

    return {
        "y": y,
        "slab": slab,
        "slab_id": np.take(P.slab_ids, material_cls),
        "object_y": object_y,
        "base_y": base_y,
        "level_adjusted": level_adjusted,
        "elev": elev_core,
        "cls": material_cls,
        "snow": snow_core,
        "soil": soil_core,
        "surface_id": surface_id,
        "cliff_face_depth": cliff_face_depth,
        "damp": damp,
        "slope": slope,
        "subsoil_mask": subsoil_mask,
        "subsoil_ids": subsoil_ids,
        "water": water_mask,
        "depth": depth,
        "bed_y": bed_y,
        "bed_relief": bed_relief,
        "bed_kind": bed_kind,
        "bed_id": bed_id,
        # แถบพุ่มเตี้ยจาก OSM — 4.1% ของแผนที่ที่เดิมไม่มีโค้ดไหนอ้างถึงเลย
        "scrub": (
            np.zeros(shape, dtype=bool) if landcover is None
            else np.asarray(landcover)[sx, sz] == S.LC["scrub"]
        ),
        # ความชันของลำน้ำต่อพัน — ตัวตัดสินว่าอะไรอยู่ในน้ำได้บ้าง
        "flow": (
            np.zeros(shape, dtype=np.uint8) if flow_core is None
            else flow_core
        ),
        "waterfall_top_y": waterfall_top,
        "waterfall_lip": waterfall_lip,
        "waterfall_pool": waterfall_pool,
        "flowing_water": flowing_water,
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
            waterfall_top = dense.get("waterfall_top_y")
            waterfall_top = (
                waterfall_top[ss]
                if waterfall_top is not None
                else np.full(
                    y.shape, np.iinfo(np.int16).min, dtype=np.int16
                )
            )

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
                max(
                    int((y + snow_height + lift).max()) + 1,
                    int(
                        waterfall_top.max(
                            initial=np.iinfo(np.int16).min
                        )
                    ) + 1,
                ),
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

                # ทาสีด้านข้างที่เปิดออกจริงเป็นแนวชั้นกว้าง 2–4 บล็อก
                # ใช้ hash ระดับกลุ่มจึงไม่เป็นเม็ดเกลือพริกไทย และไม่แตะยอดผิว
                # ซึ่ง surface classifier เลือกวัสดุให้แล้ว
                face_field = dense.get("cliff_face_depth")
                if face_field is not None:
                    face_depth = face_field[ss]
                    face_palette = np.asarray(P.cliff_face, dtype=np.uint32)
                    face_shape = face_depth.shape
                    # สนามธรณีต่อเนื่องหลายสเกล: macro เปลี่ยนชุดหินช้า ๆ,
                    # warp ทำให้ชั้นไม่เป็นเส้นระดับไม้บรรทัด และ joint ใช้
                    # zero-contour แคบ ๆ เป็นรอยแตกต่อเนื่องลงตามหน้าผา
                    macro = S.smooth_noise(
                        wx0, wz0, face_shape, 52.0, 9101, octaves=3
                    )
                    warp = np.rint(
                        S.smooth_noise(
                            wx0, wz0, face_shape, 24.0, 9157, octaves=3
                        ) * 3.0
                    ).astype(np.int32)
                    joint = np.abs(S.smooth_noise(
                        wx0, wz0, face_shape, 18.0, 9209, octaves=3
                    ))
                    grain = S.white_noise(wx0, wz0, face_shape, 9257)
                    damp_face = dense.get("damp")
                    damp_core = (
                        damp_face[ss] if damp_face is not None
                        else np.zeros(face_shape, dtype=np.float32)
                    )
                    elev_core = dense["elev"][ss]
                    for d in range(1, int(face_depth.max(initial=0)) + 1):
                        m = (face_depth >= d) & (top - d >= 0)
                        if not m.any():
                            continue
                        world_y = y - d
                        phase = np.mod(world_y + warp, 13)
                        pi = np.zeros(face_shape, dtype=np.intp)
                        high_face = elev_core >= S.SUBALPINE
                        low_mid_face = ~high_face
                        pi[high_face] = 6

                        # ชั้นรองสีใกล้เคียงกว้าง 1–2 บล็อก เป็นโครงหลัก
                        pi[
                            high_face
                            & ((phase == 4) | (phase == 5))
                            & (macro > -0.25)
                        ] = 2
                        pi[
                            high_face
                            & ((phase == 0) | (phase == 1))
                            & (macro < 0.45)
                        ] = 1
                        pi[high_face & (joint < 0.045)] = 0
                        pi[
                            low_mid_face
                            & ((phase == 0) | (phase == 1))
                            & (macro > -0.45)
                        ] = 1
                        # เลนส์หินปูนสว่างและชั้นผุสีเข้มเกิดเฉพาะบาง geological
                        # domain จึงไม่พาดเป็นลายทางสม่ำเสมอทั้งภูเขา
                        pi[
                            low_mid_face & (phase == 5) & (macro > 0.18)
                        ] = 2
                        pi[
                            low_mid_face & (phase == 9) & (macro < -0.20)
                        ] = 4
                        # joint เป็นเส้นแคบต่อเนื่อง ไม่ใช่เม็ดสุ่มรายบล็อก
                        pi[
                            low_mid_face
                            & (joint < 0.045)
                            & (macro < 0.35)
                        ] = 3
                        mineral_halo = (
                            low_mid_face
                            & (joint >= 0.045)
                            & (joint < 0.11)
                            & (macro < 0.45)
                        )
                        pi[mineral_halo] = 7
                        # คราบชื้น/มอสอยู่ตาม joint เฉพาะระดับต่ำกว่าแนว alpine
                        wet = (
                            (damp_core > 0.68)
                            & (elev_core < S.MONTANE)
                            & (joint >= 0.045)
                            & (joint < 0.11)
                        )
                        pi[wet] = 5
                        # เม็ด weathering ละเอียดมีน้อยและเลือกสีใกล้เคียง
                        pi[(grain < 0.035) & low_mid_face & (pi == 0)] = 1
                        pi[(grain < 0.035) & high_face & (pi == 6)] = 1
                        face_id = face_palette[pi]
                        # ต่อวัสดุผิวบนลงมาที่หน้าตั้งก่อนเข้าแนวชั้นหลัก
                        # โดยใช้ cls หลัง transition แล้ว จึงเข้าคู่กับ slab ด้วย
                        top_cls = dense["cls"][ss]
                        top_rock = np.isin(
                            top_cls, _GEOLOGIC_TRANSITION_IDS
                        )
                        if d == 1:
                            face_id[top_rock] = dense["surface_id"][ss][top_rock]
                        elif d == 2:
                            carry = top_rock & (macro > 0.05)
                            face_id[carry] = dense["surface_id"][ss][carry]
                        volume[gx[m], (top - d)[m], gz[m]] = face_id[m]

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
                curtain = (
                    active_water[:, None, :]
                    & (ys > y[:, None, :])
                    & (ys <= waterfall_top[:, None, :])
                )
                volume[curtain] = P.water

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


def schedule_source_water_ticks(
    level, surface_y, flowing_water, waterfall_top_y,
    x0, x1, z0, z1, px0, pz0, dimension=None,
):
    """Queue vanilla fluid updates for source-water streams and waterfalls."""
    surface_y = np.asarray(surface_y, dtype=np.int32)
    flowing_water = np.asarray(flowing_water, dtype=bool)
    waterfall_top_y = np.asarray(waterfall_top_y, dtype=np.int32)
    if not (
        surface_y.shape == flowing_water.shape == waterfall_top_y.shape
    ):
        raise ValueError("fluid tick fields must have matching shapes")
    sx = slice(x0 - px0, x1 - px0)
    sz = slice(z0 - pz0, z1 - pz0)
    curtain_top = waterfall_top_y[sx, sz]
    # A waterfall curtain may be anchored on the standing-water side of a
    # lake/river boundary. Queue that column too even when its base cell is not
    # part of the explicit flowing mask.
    stage = surface_y[sx, sz]
    wet = flowing_water[sx, sz] | (curtain_top >= stage)
    dimension = C.DIMENSION if dimension is None else dimension
    touched = {}
    queued = 0
    qx, qz = np.where(wet)
    for local_x, local_z in zip(qx, qz):
        wx, wz = x0 + int(local_x), z0 + int(local_z)
        bottom = int(stage[local_x, local_z])
        top = max(bottom, int(curtain_top[local_x, local_z]))
        chunk_key = (wx >> 4, wz >> 4)
        chunk = touched.get(chunk_key)
        if chunk is None:
            chunk = level.get_chunk(chunk_key[0], chunk_key[1], dimension)
            touched[chunk_key] = chunk
        ticks = chunk.misc.setdefault("fluid_ticks", {})
        for wy in range(bottom, top + 1):
            # Spread the work over four ticks so a loaded region does not
            # process every source in the same server tick.
            delay = 1 + ((wx * 31 + wy * 17 + wz * 13) & 3)
            ticks[(wx, wy, wz)] = ("minecraft:water", delay, 0)
            queued += 1
    for chunk in touched.values():
        chunk.changed = True
        level.put_chunk(chunk, dimension)
    return queued


def process_region(level, P, surf_y, elev, lc, wdepth, x0, x1, z0, z1,
                   stats, px0, pz0, paint_vegetation=True,
                   terrain_offset=None, lake_only=False,
                   vegetation_only=False, terrain_sub=None,
                   water_surface_y=None, global_lake_mask=None,
                   waterfall_top_y=None, waterfall_lip_mask=None,
                   waterfall_pool_mask=None, flowing_water_mask=None,
                   flow_index=None,
                   active_chunks=None):
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
    # `global_lake_mask` คือ standing_water_mask ของ hydrology ซึ่งเป็นตัวจำแนก
    # นิ่ง/ไหลที่ถูกต้อง — เกณฑ์ depth>=3 ที่ biome_index ใช้เป็น fallback ให้สี
    # ทะเลสาบกับแม่น้ำสลับกัน (ดู biome_index)
    biome_idx = B.biome_index(
        elev, lc == S.LC["water"], wdepth, standing=global_lake_mask,
    )
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
        if active_chunks is not None and key not in active_chunks:
            return None
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
        water_surface_y=water_surface_y,
        global_lake_mask=global_lake_mask,
        waterfall_top_y=waterfall_top_y,
        waterfall_lip_mask=waterfall_lip_mask,
        waterfall_pool_mask=waterfall_pool_mask,
        flowing_water_mask=flowing_water_mask,
        flow_index=flow_index,
        landcover=lc,
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
        x0=x0, z0=z0, flow_index=dense["flow"],
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

    # กกริมน้ำอยู่บนฝั่ง ไม่ใช่ในคอลัมน์น้ำ จึงวางหลังเติมน้ำและก่อนพืชพื้นล่าง
    # เพื่อให้กอหนึ่งกอนับเป็นหน่วยเดียวแม้สูงหลายบล็อก
    reed_ground = (
        (dense["soil"] | dense["wet_shore"])
        & (dense["snow"] == 0)
        & ~dense["ice"]
        & ~dense["beach"]
    )
    reed_heights = riparian_reed_heights(
        dense["water"], dense["depth"], reed_ground, x0=x0, z0=z0,
        flow_index=dense["flow"],
    )
    for qx, qz in zip(*np.nonzero(reed_heights)):
        wx, wz = x0 + int(qx), z0 + int(qz)
        base = int(dense["object_y"][qx, qz]) + 1
        wrote = 0
        for dy in range(int(reed_heights[qx, qz])):
            wrote += setb(wx, base + dy, wz, P.reed, only_air=True)
        if wrote:
            stats["reeds"] = stats.get("reeds", 0) + 1

    # ---- พืชพื้นล่าง: วนเฉพาะคอลัมน์ที่ปลูกได้ -------------------------------
    # เดิมวนครบทุกคอลัมน์ แม้เป็นน้ำ/หิน/หิมะ การรวม mud ตรงนี้ยังแก้บั๊กเดิม
    # ที่ทำให้ branch wetland เข้าไม่ถึงเพราะ soil mask ไม่รวม mud
    mud = dense["cls"] == S.IDX["mud"]
    # ริมน้ำ (`beach`) เคยถูกตัดออกทั้งหมด ทำให้แถบกรวดริมทะเลสาบเป็นพื้นที่ตาย
    # ไม่มีอะไรเลยสักบล็อก  ให้ปลูกได้แต่ไปอยู่โซน `shore` ซึ่งบางและไม่มีพืช
    # สองบล็อก (คลื่นกับน้ำแข็งกวาดทุกฤดู)
    plantable = (
        (dense["soil"] | mud | dense["wet_shore"] | dense["beach"])
        & (dense["snow"] == 0)
        & ~dense["water"]
        & ~dense["ice"]
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
        elif dense["beach"][qx, qz]:
            zone = "shore"
        elif dense["scrub"][qx, qz]:
            # ต้องมาก่อน forest/meadow — พุ่มบนที่ลาดมี forest_p สูงพอจะถูก
            # นับเป็นป่า แล้วชั้นพุ่มก็หายไปเหมือนเดิม
            zone = "scrub"
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
                        px0, pz0, samples_per_kind=8, lake_only=False,
                        water_surface_y=None, active_chunks=None):
    """Read a small spatial sample back after purge and verify disk state."""
    water = lc == S.LC["water"]
    if water_surface_y is None:
        expected, _ = lake_surface_levels(surf_y, water, wdepth)
    else:
        expected = np.asarray(surf_y, dtype=np.int32).copy()
        expected[water] = np.asarray(water_surface_y, dtype=np.int32)[water]
    sx = slice(x0 - px0, x1 - px0)
    sz = slice(z0 - pz0, z1 - pz0)
    water_core = water[sx, sz]
    expected_core = expected[sx, sz]
    active_core = np.ones(water_core.shape, dtype=bool)
    if active_chunks is not None:
        active_core[:] = False
        for cx, cz in active_chunks:
            bx0 = max(x0, cx * CHUNK) - x0
            bx1 = min(x1, (cx + 1) * CHUNK) - x0
            bz0 = max(z0, cz * CHUNK) - z0
            bz1 = min(z1, (cz + 1) * CHUNK) - z0
            if bx0 < bx1 and bz0 < bz1:
                active_core[bx0:bx1, bz0:bz1] = True

    points = []
    sample_kinds = [(water_core & active_core, "water")]
    if not lake_only:
        sample_kinds.append(((~water_core) & active_core, "land"))
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


def paint_fingerprint(hydrology_root=None):
    """Fingerprint every input that can change a fullscale tile result.

    ต้องแฮช product ของ **ชุดที่ใช้จริง** เดิมแฮชไฟล์ชุดเดิมตายตัว ทำให้เมื่อรัน
    ด้วย --hydrology-root แล้วไปแก้ hydrology_global ใหม่ signature ไม่เปลี่ยน
    --resume จึงข้าม region ต่อไปเงียบ ๆ ทั้งที่ input คนละชุดแล้ว — guard ที่มี
    ไว้กันเรื่องนี้โดยเฉพาะกลับไม่ครอบ input ที่ paint ใช้จริง
    """
    # ต้องครบทุกโมดูลที่ตัดสินว่าบล็อกไหนถูกเขียน — ขาดไปหนึ่งไฟล์แปลว่าแก้กฎ
    # แล้ว --resume ข้าม region เก่าเงียบ ๆ (บั๊กชนิดเดียวกับที่ `SHAPE_INPUTS`
    # เคยขาด channel_sections.py)
    paths = [
        os.path.join(HERE, name) for name in (
            "paint_surface.py",
            "surface.py",
            "water_ecology.py",
            "ecology.py",
            "biomes.py",
            "vegetation.py",
            "tree_schematics.py",
            "config.py",
            "heightmap.png",
            "landcover.npz",
            "terrain_shape.py",
            # terrain_sub เป็นของ terrain_shape จึงอยู่ที่รากเสมอ
            "terrain_sub.npy",
        )
    ]
    products = GLOBAL_PRODUCTS if hydrology_root else LEGACY_PRODUCTS
    paths.extend(
        os.path.join(hydrology_root or HERE, filename)
        for filename in sorted(set(products.values()))
    )
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

    hydro_patch = None
    hydro_root = None
    if "--hydrology-root" in sys.argv:
        i = sys.argv.index("--hydrology-root")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--hydrology-root requires a directory")
        hydro_root = os.path.abspath(sys.argv[i + 1])
        missing = [
            name for name in sorted(set(GLOBAL_PRODUCTS.values()))
            if not os.path.isfile(os.path.join(hydro_root, name))
        ]
        if missing:
            raise SystemExit(
                "hydrology root is incomplete: " + ", ".join(missing)
            )
        print(f"[HYDROLOGY GLOBAL] {hydro_root}")
    if "--hydrology-patch" in sys.argv:
        i = sys.argv.index("--hydrology-patch")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--hydrology-patch requires an .npz path")
        if "--patch" not in sys.argv:
            raise SystemExit("--hydrology-patch is restricted to --patch runs")
        try:
            hydro_patch = load_hydrology_patch(sys.argv[i + 1])
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        patch_water_check = validate_water_inputs(
            hydro_patch["water_mask"], hydro_patch["depth"]
        )
        if any(patch_water_check[key] for key in (
            "water_without_depth", "nonwater_with_depth", "too_deep"
        )):
            raise SystemExit(
                f"invalid hydrology patch water inputs: {patch_water_check}"
            )
        patch_wet = hydro_patch["water_mask"].astype(bool)
        patch_levels = hydro_patch["surface_y"]
        if (
            (patch_wet & (patch_levels == np.iinfo(np.int16).min)).any()
            or (~patch_wet & (patch_levels != np.iinfo(np.int16).min)).any()
        ):
            raise SystemExit(
                "hydrology patch surface_y does not match water_mask"
            )
        print(f"[HYDROLOGY] overlay {os.path.abspath(sys.argv[i + 1])}")
    if hydro_patch is not None and hydro_root is not None:
        raise SystemExit("use only one hydrology input")
    water_only = "--water-only" in sys.argv
    if water_only and hydro_root is None:
        raise SystemExit("--water-only requires --hydrology-root")

    early_meta = os.path.join(
        HERE,
        (
            "paint_progress.water.meta.json" if water_only
            else "paint_progress.vegetation.meta.json"
            if "--vegetation-only" in sys.argv
            else "paint_progress.meta.json"
        ),
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
        reset_command = [
            sys.executable, os.path.join(HERE, "build_terrain.py"),
            "--patch", *patch_args,
        ]
        if hydro_patch is not None:
            reset_command.extend([
                "--hydrology-patch",
                sys.argv[sys.argv.index("--hydrology-patch") + 1],
            ])
        subprocess.run(
            reset_command,
            check=True,
        )

    meta = S.load_meta()
    lo, hi = meta["elev_min_m"], meta["elev_max_m"]

    print("อ่าน heightmap + landcover ...")
    hm = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))  # [z, x]
    lc_all = np.load(os.path.join(HERE, "landcover.npz"))["landcover"]  # [z, x]
    # ชื่อไฟล์ของแต่ละชุดอยู่ใน hydrology_patch_io ที่เดียว — เคยเขียนไว้ที่นี่
    # แล้ว report_metrics/render_* อ่านชุดเดิมต่อไปเงียบ ๆ จนตัวเลขกับภาพพรีวิว
    # อธิบายคนละโลกกับที่เห็นในเกม
    wm_path = water_product_path("water_mask", hydro_root, HERE)
    # mmap เหมือน ty_all/wy_all ข้างล่าง — ทั้งสองตัวถูกใช้ผ่านสไลซ์ต่อ region
    # เท่านั้น การ np.load() เต็มแผนที่จ่าย 100 MB ต่อไฟล์ไปเปล่า ๆ ตลอดทั้งรัน
    # (ผู้บริโภคทุกรายทำ .copy() ก่อนเขียน: overlay_window, apply_water_mask)
    wm_all = (
        np.load(wm_path, mmap_mode="r") if os.path.exists(wm_path) else None
    )
    if wm_all is not None and wm_all.shape != lc_all.shape:
        raise SystemExit(
            "water_mask.npy has a different shape from landcover.npz; "
            "run make_water.py again"
        )
    wd_path = water_product_path("depth", hydro_root, HERE)
    wd_all = (
        np.load(wd_path, mmap_mode="r") if os.path.exists(wd_path) else None
    )
    if (wm_all is None) != (wd_all is None):
        raise SystemExit(
            "water_mask.npy and water_depth.npy must be regenerated together; "
            "run make_water.py"
        )
    if wd_all is None:
        raise SystemExit(
            "water_mask.npy and water_depth.npy are required; run make_water.py"
        )
    ty_path = water_product_path("terrain_y", hydro_root, HERE)
    # terrain_sub เป็นของ terrain_shape ไม่ใช่ของ hydrology จึงอยู่ที่รากเสมอ
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
    wy_path = water_product_path("surface_y", hydro_root, HERE)
    wl_path = water_product_path("standing_water_mask", hydro_root, HERE)
    if not (os.path.exists(wy_path) and os.path.exists(wl_path)):
        raise SystemExit(
            "ไม่พบ water_surface_y.npy / water_lake_mask.npy — "
            "รัน make_water_levels.py หลัง terrain_shape.py"
        )
    wy_all = np.load(wy_path, mmap_mode="r")
    wl_all = np.load(wl_path, mmap_mode="r")
    # In global hydrology, flowing water is every wet cell that was not
    # classified as a level standing basin. This includes sloped
    # natural=water polygons as well as explicit waterway lines.
    global_flowing = hydro_root is not None
    global_waterfall_top = (
        np.load(os.path.join(hydro_root, "waterfall_top_y.npy"), mmap_mode="r")
        if hydro_root else None
    )
    global_waterfall_lip = (
        np.load(
            os.path.join(hydro_root, "waterfall_lip_mask.npy"), mmap_mode="r"
        )
        if hydro_root else None
    )
    global_waterfall_pool = (
        np.load(
            os.path.join(hydro_root, "waterfall_pool_mask.npy"), mmap_mode="r"
        )
        if hydro_root else None
    )
    # ความชันของลำน้ำ — ชุดที่สร้างก่อน 2026-08-18 ไม่มีไฟล์นี้ ปล่อยเป็น None
    # แล้วก้นลำน้ำจะกลับไปใช้ palette เดิม (ไม่พัง แต่ไม่ได้ของใหม่)
    flow_path = os.path.join(hydro_root, "flow_index.npy") if hydro_root else None
    global_flow_index = (
        np.load(flow_path, mmap_mode="r")
        if flow_path and os.path.exists(flow_path) else None
    )
    affected_chunks = (
        np.load(os.path.join(hydro_root, "affected_chunks.npy"), mmap_mode="r")
        if hydro_root and "--water-only" in sys.argv else None
    )
    if wy_all.shape != lc_all.shape or wl_all.shape != lc_all.shape:
        raise SystemExit(
            "water level products มีขนาดไม่ตรงกับ landcover.npz; "
            "รัน make_water_levels.py ใหม่"
        )
    water_level_check = False
    unresolved_water_y = np.iinfo(np.int16).min
    for row0 in range(0, wm_all.shape[0], 512):
        row1 = min(wm_all.shape[0], row0 + 512)
        wet = np.asarray(wm_all[row0:row1], dtype=bool)
        levels = np.asarray(wy_all[row0:row1])
        if (
            (wet & (levels == unresolved_water_y)).any()
            or (~wet & (levels != unresolved_water_y)).any()
        ):
            water_level_check = True
            break
    if water_level_check:
        raise SystemExit(
            "water_surface_y.npy ไม่ตรงกับ water_mask.npy; "
            "รัน make_water_levels.py ใหม่"
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
    # คิว fluid tick ให้วานิลลาประมวลผลน้ำตอนโหลด chunk — **เปิดโดยค่าเริ่มต้น**
    #
    # เจตนาคือให้น้ำไหลตาม logic ของเกม ไม่ใช่ก้อน source นิ่ง ๆ ซึ่งได้ภาพ
    # สวยกว่ามาก  แต่มันบังคับว่าน้ำที่เราวางต้อง **อยู่ในสมดุลของกฎน้ำวานิลลา
    # อยู่แล้ว** ไม่งั้นเกมจะจัดระเบียบให้เองแล้วผลลัพธ์ต่างจากที่คำนวณไว้:
    #   1. cell น้ำที่ติดกันแต่ผิวต่างกัน 1 บล็อก -> ฝั่งสูงแผ่เข้าไปในอากาศเหนือ
    #      ฝั่งต่ำ เกิดลิ้นน้ำไหลยาวได้ถึง 7 บล็อก และยาวไม่เท่ากันสองฝั่ง
    #   2. บนผืนน้ำกว้าง flowing ที่มี source ข้าง ๆ >=2 ตัวกลายเป็น source ใหม่
    #      (กฎ infinite water) ระดับน้ำถูกยกขึ้นถาวรเป็นบริเวณกว้าง
    # วัดจากโลกจริงแล้วเจอทั้งสองอย่าง: water[level=3] ที่ (2283,84,3170) และ
    # source ที่ y=22 บนคอลัมน์ที่ทุก build บอกว่า surface=21
    #
    # และมันย้อนกลับไม่ได้ด้วยการ paint ทับ เพราะ paint เขียนแค่ช่วง y_lo..y_hi
    # ที่คำนวณจากผิวน้ำ *ใหม่* บล็อกที่ปนเปื้อนอยู่สูงกว่านั้นไม่เคยถูกแตะ
    # ต้องลบ chunk แล้วสร้างใหม่เท่านั้น
    #
    # `--no-fluid-ticks` ใช้ตอนวินิจฉัย: paint แล้วเปิดดูโดยที่โลกยังเป็นสิ่งที่
    # เราคำนวณไว้เป๊ะ ๆ เพื่อแยกว่าอะไรคือบั๊กของเรา อะไรคือผลของกฎน้ำวานิลลา
    fluid_ticks = "--no-fluid-ticks" not in sys.argv
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
            check_water = wm_all[qz0:qz1, qx0:qx1]
            check_depth = wd_all[qz0:qz1, qx0:qx1]
            if hydro_patch is not None:
                check_water = overlay_window(
                    check_water, hydro_patch, "water_mask",
                    qx0, qx1, qz0, qz1,
                )
                check_depth = overlay_window(
                    check_depth, hydro_patch, "depth",
                    qx0, qx1, qz0, qz1,
                )
            water_check = water_depth_preflight(
                hm[qz0:qz1, qx0:qx1],
                lc_all[qz0:qz1, qx0:qx1],
                check_depth,
                water_mask=check_water,
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
    suffix = (
        ".water" if water_only
        else ".vegetation" if vegetation_only
        else ""
    )
    done_file = os.path.join(HERE, f"paint_progress{suffix}.txt")
    meta_file = os.path.join(HERE, f"paint_progress{suffix}.meta.json")
    done = set()
    if patch is None:
        signature = paint_fingerprint(hydro_root)
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
    if affected_chunks is not None:
        tiles = [
            tile for tile in tiles
            if np.asarray(affected_chunks[
                tile[2] // CHUNK:(tile[3] + CHUNK - 1) // CHUNK,
                tile[0] // CHUNK:(tile[1] + CHUNK - 1) // CHUNK,
            ]).any()
        ]
        print(
            f"[WATER ONLY] {int(np.asarray(affected_chunks).sum()):,} chunks "
            f"in {len(tiles):,} paint regions"
        )
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
        terrain_slice = ty_all[az0:az1, ax0:ax1]
        water_slice = wm_all[az0:az1, ax0:ax1]
        depth_slice = wd_all[az0:az1, ax0:ax1]
        level_slice = wy_all[az0:az1, ax0:ax1]
        lake_slice = wl_all[az0:az1, ax0:ax1]
        waterfall_top_slice = (
            global_waterfall_top[az0:az1, ax0:ax1]
            if global_waterfall_top is not None
            else np.full(
                level_slice.shape, np.iinfo(np.int16).min, dtype=np.int16
            )
        )
        waterfall_lip_slice = (
            global_waterfall_lip[az0:az1, ax0:ax1]
            if global_waterfall_lip is not None
            else np.zeros(level_slice.shape, dtype=bool)
        )
        waterfall_pool_slice = (
            global_waterfall_pool[az0:az1, ax0:ax1]
            if global_waterfall_pool is not None
            else np.zeros(level_slice.shape, dtype=bool)
        )
        flowing_water_slice = (
            np.asarray(water_slice, dtype=bool)
            & ~np.asarray(lake_slice, dtype=bool)
            if global_flowing
            else np.zeros(level_slice.shape, dtype=bool)
        )
        flow_index_slice = (
            global_flow_index[az0:az1, ax0:ax1]
            if global_flow_index is not None
            else np.zeros(level_slice.shape, dtype=np.uint8)
        )
        if hydro_patch is not None:
            terrain_slice = overlay_window(
                terrain_slice, hydro_patch, "terrain_y",
                ax0, ax1, az0, az1,
            )
            water_slice = overlay_window(
                water_slice, hydro_patch, "water_mask",
                ax0, ax1, az0, az1,
            )
            depth_slice = overlay_window(
                depth_slice, hydro_patch, "depth",
                ax0, ax1, az0, az1,
            )
            level_slice = overlay_window(
                level_slice, hydro_patch, "surface_y",
                ax0, ax1, az0, az1,
            )
            lake_slice = overlay_window(
                lake_slice, hydro_patch, "standing_water_mask",
                ax0, ax1, az0, az1,
            )
            if "waterfall_top_y" in hydro_patch.files:
                waterfall_top_slice = overlay_window(
                    waterfall_top_slice, hydro_patch, "waterfall_top_y",
                    ax0, ax1, az0, az1,
                )
            if "waterfall_lip_mask" in hydro_patch.files:
                waterfall_lip_slice = overlay_window(
                    waterfall_lip_slice, hydro_patch,
                    "waterfall_lip_mask", ax0, ax1, az0, az1,
                )
            if "waterfall_pool_mask" in hydro_patch.files:
                waterfall_pool_slice = overlay_window(
                    waterfall_pool_slice, hydro_patch,
                    "waterfall_pool_mask", ax0, ax1, az0, az1,
                )
            if "flow_index" in hydro_patch.files:
                flow_index_slice = overlay_window(
                    flow_index_slice, hydro_patch, "flow_index",
                    ax0, ax1, az0, az1,
                )
            if "waterway_mask" in hydro_patch.files:
                flowing_water_slice = overlay_window(
                    flowing_water_slice, hydro_patch, "waterway_mask",
                    ax0, ax1, az0, az1,
                )
            # Patch standing/flowing classification is authoritative after all
            # overlays; do not leave sloped waterbody cells unscheduled.
            flowing_water_slice = (
                np.asarray(water_slice, dtype=bool)
                & ~np.asarray(lake_slice, dtype=bool)
            )
        surf_y = terrain_slice.T.astype(np.int32)
        terrain_offset = (
            tsub_all[az0:az1, ax0:ax1].T.astype(np.float32) / 127.0
        )
        lc_source = lc_all[az0:az1, ax0:ax1]
        if wm_all is not None:
            lc_source = S.apply_water_mask(
                lc_source, water_slice
            )
        lc = lc_source.T
        wdepth = depth_slice.T
        water_surface_y = level_slice.T
        global_lake_mask = lake_slice.T
        active_chunk_set = None
        if affected_chunks is not None:
            active_chunk_set = {
                (cx, cz)
                for cz in range(z0 // CHUNK, (z1 + CHUNK - 1) // CHUNK)
                for cx in range(x0 // CHUNK, (x1 + CHUNK - 1) // CHUNK)
                if bool(affected_chunks[cz, cx])
            }

        total_chunks += process_region(
            level, P, surf_y, elev, lc, wdepth, x0, x1, z0, z1, stats, ax0, az0,
            paint_vegetation=not terrain_only,
            terrain_offset=terrain_offset,
            lake_only=lake_only,
            vegetation_only=vegetation_only,
            terrain_sub=terrain_offset,
            water_surface_y=water_surface_y,
            global_lake_mask=global_lake_mask,
            waterfall_top_y=waterfall_top_slice.T,
            waterfall_lip_mask=waterfall_lip_slice.T,
            waterfall_pool_mask=waterfall_pool_slice.T,
            flowing_water_mask=flowing_water_slice.T,
            flow_index=flow_index_slice.T,
            active_chunks=active_chunk_set,
        )
        # คิว fluid tick เป็นตัวเลือก ไม่ใช่ค่าเริ่มต้น — ดู --fluid-ticks
        if fluid_ticks and (hydro_patch is not None or hydro_root is not None):
            stats["fluid_ticks"] = stats.get("fluid_ticks", 0) + (
                schedule_source_water_ticks(
                    level,
                    water_surface_y,
                    flowing_water_slice.T,
                    waterfall_top_slice.T,
                    x0, x1, z0, z1, ax0, az0,
                )
            )

        level.save()
        level.purge()
        if "--skip-save-validation" not in sys.argv:
            checked = validate_saved_tile(
                level, surf_y, lc, wdepth, x0, x1, z0, z1, ax0, az0,
                lake_only=lake_only,
                water_surface_y=water_surface_y,
                active_chunks=active_chunk_set,
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
        # paint fullscale ใช้เวลาหลายชั่วโมง ตัวเลข RAM จริงจึงสำคัญกว่าที่
        # build_terrain — chunk cache ของ amulet คือตัวกินหลัก ไม่ใช่ numpy
        used = working_set_mb()
        sys.stdout.write(
            f"\r{i}/{len(tiles)} ({pct:5.1%})  chunks {total_chunks:,}  "
            f"ต้นไม้ {stats['trees']:,}  พืช {stats['plants']:,}  "
            f"ETA {(el/pct - el)/60:5.1f} นาที"
            + (f"  RAM {used:,.0f}MB" if used else "") + "   "
        )
        sys.stdout.flush()

    print(f"\nเสร็จ — {total_chunks:,} chunks, ต้นไม้ {stats['trees']:,}, "
          f"พืช {stats['plants']:,}, ตกแต่ง {stats.get('decor',0):,}, "
          f"ใต้น้ำ {stats.get('underwater_decor',0):,}, "
          f"fluid ticks {stats.get('fluid_ticks',0):,}, "
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
