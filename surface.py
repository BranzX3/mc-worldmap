"""กฎการเลือกบล็อกผิวดิน + ชั้นเรือนยอดป่า + biome

classify() ทำงานบน array ขนาดใดก็ได้ ใช้ซ้ำได้ทั้ง render_preview.py (ย่อส่วน)
และ paint_surface.py (ทีละ tile ตอนเขียนโลกจริง)

หลักการ
  - ความสูงรับเป็น "เมตรจริง" ความชันคำนวณจากเมตรจริงทั้งสองแกน จึงเป็นความชัน
    จริงของภูมิประเทศ ไม่ใช่ความชันที่ถูกบีบด้วยสเกลในเกม
  - แต่ละโซนไม่ได้ใช้บล็อกเดียว แต่เป็น palette ถ่วงน้ำหนักแล้ว dither ระดับบล็อก
  - ใช้ความโค้ง (laplacian) แยกร่องเขากับสันเขา: ร่องสะสมหินร่วง/ดิน สันมีหินโผล่
  - ขอบโซนเบลอด้วยความน่าจะเป็น ไม่ใช่เส้นคม
"""

import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

LC = {
    "none": 0, "grass": 1, "farm": 2, "forest": 3, "scrub": 4,
    "wetland": 5, "scree": 6, "rock": 7, "glacier": 8, "water": 9,
}


def apply_water_mask(landcover, water_mask):
    """Return landcover adjusted to the derived, naturalized water outline.

    Pixels eroded from the source outline become wet shoreline instead of
    reverting to an unrelated OSM class. Pixels added to the outline become
    water regardless of their former land class.
    """
    landcover = np.asarray(landcover)
    water_mask = np.asarray(water_mask, dtype=bool)
    if landcover.shape != water_mask.shape:
        raise ValueError("landcover and water_mask must have the same shape")

    result = landcover.copy()
    removed = (landcover == LC["water"]) & ~water_mask
    result[removed] = LC["wetland"]
    result[water_mask] = LC["water"]
    return result


def shore_probabilities(water, water_depth, shore_width=4, search_blocks=12):
    """Return separate ``(lake_shore, stream_bank)`` probability fields."""
    water = np.asarray(water, dtype=bool)
    water_depth = np.asarray(water_depth)
    if water.shape != water_depth.shape:
        raise ValueError("water and water_depth must have the same shape")
    if shore_width < 1:
        raise ValueError("shore_width must be at least 1")

    dist = np.full(water.shape, shore_width + 1, dtype=np.int16)
    dist[water] = 0
    near = water.copy()
    for step_i in range(1, shore_width + 1):
        expanded = near.copy()
        expanded[1:, :] |= near[:-1, :]
        expanded[:-1, :] |= near[1:, :]
        expanded[:, 1:] |= near[:, :-1]
        expanded[:, :-1] |= near[:, 1:]
        newly = expanded & ~near
        dist[newly] = step_i
        near = expanded

    wmax = water_depth.astype(np.int16, copy=True)
    for _ in range(max(shore_width, int(search_blocks))):
        # `expanded` ต้องเป็นสำเนาเพื่อให้การแพร่เป็น synchronous แต่ np.maximum
        # ที่ไม่ใส่ out= ยังทิ้ง array ขนาดเท่า input เพิ่มอีกก้อนทุกบรรทัด
        expanded = wmax.copy()
        np.maximum(expanded[1:, :], wmax[:-1, :], out=expanded[1:, :])
        np.maximum(expanded[:-1, :], wmax[1:, :], out=expanded[:-1, :])
        np.maximum(expanded[:, 1:], wmax[:, :-1], out=expanded[:, 1:])
        np.maximum(expanded[:, :-1], wmax[:, 1:], out=expanded[:, :-1])
        wmax = expanded

    lake_body = np.clip(
        (wmax.astype(np.float32) - 2.0) / 6.0, 0.0, 1.0
    )
    lake_weights = np.asarray([0.92, 0.62, 0.32, 0.12], dtype=np.float32)
    bank_weights = np.asarray([0.72, 0.48, 0.25, 0.10], dtype=np.float32)
    lake_falloff = np.zeros(water.shape, dtype=np.float32)
    bank_falloff = np.zeros(water.shape, dtype=np.float32)
    for step_i in range(1, shore_width + 1):
        at_distance = dist == step_i
        lake_falloff[at_distance] = lake_weights[
            min(step_i, len(lake_weights)) - 1
        ]
        bank_falloff[at_distance] = bank_weights[
            min(step_i, len(bank_weights)) - 1
        ]

    lake_shore = lake_falloff * lake_body
    stream_bank = bank_falloff * (lake_body <= 0.0)
    return lake_shore, stream_bank

# สี RGB ดึงจาก texture จริงของเกม 26.2 ด้วย block_colors.py (ยกเว้นที่มาร์กว่า tint
# ซึ่งถูก biome ย้อมสีตอนรันไทม์ texture จริงเป็นเกือบขาวดำ ใช้ค่าประมาณแทน)
BLOCKS = {
    "water":       ("water",              ( 60,110,200)),   # tint
    "ice":         ("packed_ice",         (142,180,251)),
    "blue_ice":    ("blue_ice",           (116,168,253)),
    "snow":        ("snow_block",         (249,254,254)),
    "snow_thin":   ("snow",               (249,254,254)),
    # ---- ไล่สีหิน เรียงจากสว่างไปมืด (ค่า luma จริง) ----
    "calcite":     ("calcite",            (224,224,221)),   # 224
    "diorite":     ("diorite",            (189,188,189)),   # 189
    "clay":        ("clay",               (161,167,179)),   # 166  อุดช่องว่าง
    # smooth_stone เลิกใช้: texture เรียบสนิทไม่มีลาย ทำให้โดดออกจากหิน
    # ธรรมชาติรอบข้างที่มีลายทุกตัว (เก็บค่าสีไว้เป็นเอกสารของ ramp)
    "smooth":      ("smooth_stone",       (159,159,159)),   # 159  ไม่ใช้แล้ว
    "andesite":    ("andesite",           (136,136,137)),   # 136
    "gravel":      ("gravel",             (132,128,126)),   # 128
    "cobble":      ("cobblestone",        (128,127,128)),   # 127
    "stone":       ("stone",              (126,126,126)),   # 126
    "dead_brain":  ("dead_brain_coral_block", (124,118,114)), # 119
    "mossy_cob":   ("mossy_cobblestone",  (110,118, 95)),   # 115
    "dripstone":   ("dripstone_block",    (134,108, 92)),   # 112
    "pale_moss":   ("pale_moss_block",    (107,112,105)),   # 111
    "tuff":        ("tuff",               (108,109,103)),   # 108
    "deepslate":   ("deepslate",          ( 80, 80, 83)),   #  80
    "cob_deep":    ("cobbled_deepslate",  ( 77, 77, 81)),   #  78
    "basalt":      ("smooth_basalt",      ( 73, 72, 78)),   #  73
    # ---- ดิน/พืช ----
    # โทนน้ำตาลอ่อนอุ่น — ดึงสีจริงจาก texture ในเกม 26.2 เติมช่องว่างระหว่าง
    # gravel (129) กับ rooted_dirt (113) ซึ่งเดิมไม่มีอะไรอยู่เลย ทำให้พื้นดิน
    # กระโดดจากเทาไปน้ำตาลเข้มทันที
    "mushroom":    ("brown_mushroom_block", (149,112, 81)),   # 120
    "packed_mud":  ("packed_mud",         (142,107, 80)),     # 114
    "rooted":      ("rooted_dirt",        (144,104, 76)),
    "dirt":        ("dirt",               (134, 96, 67)),
    "coarse":      ("coarse_dirt",        (119, 86, 59)),
    "podzol":      ("podzol",             ( 91, 62, 31)),   # tint-free แต่ texture หลายหน้า
    "moss":        ("moss_block",         ( 89,110, 45)),
    "mud":         ("mud",                ( 60, 58, 61)),
    "grass":       ("grass_block",        (110,152, 74)),   # tint
    "spruce_leaf": ("spruce_leaves",      ( 58, 86, 54)),   # tint
    "oak_leaf":    ("oak_leaves",         ( 72,104, 50)),   # tint
    "birch_leaf":  ("birch_leaves",       ( 98,120, 62)),   # tint
    "azalea_leaf": ("flowering_azalea_leaves", ( 92,116, 58)),  # tint
}
KEYS = list(BLOCKS)
IDX = {k: i for i, k in enumerate(KEYS)}
SOIL_KEYS = ("grass", "moss", "podzol", "dirt", "coarse", "rooted")
SOIL_IDS = np.asarray([IDX[k] for k in SOIL_KEYS], dtype=np.uint8)

# ---- slab สำหรับไล่ระดับครึ่งบล็อก ----------------------------------------
# วานิลลามี slab เฉพาะหิน — ดิน หญ้า มอส กรวด ทราย ไม่มีเลย การไล่ระดับครึ่ง
# บล็อกจึงทำได้เฉพาะบนภูมิประเทศหิน ซึ่งบังเอิญเป็นที่ที่ขั้นบันไดเห็นชัดสุด
# (หน้าผา ลานหิน scree) พอดี
#
# deepslate ใช้ cobbled_deepslate_slab เพราะ deepslate ล้วนไม่มี slab และ
# cobbled เป็นตัวที่หน้าตาเป็นหินธรรมชาติที่สุดในตระกูล
SLAB_OF = {
    "stone": "stone_slab",
    "cobble": "cobblestone_slab",
    "andesite": "andesite_slab",
    "diorite": "diorite_slab",
    "tuff": "tuff_slab",
    "mossy_cob": "mossy_cobblestone_slab",
    "deepslate": "cobbled_deepslate_slab",
    "cob_deep": "cobbled_deepslate_slab",
}
HAS_SLAB = np.zeros(len(KEYS), dtype=bool)
for _k in SLAB_OF:
    HAS_SLAB[IDX[_k]] = True
PREVIEW_RGB = np.array([BLOCKS[k][1] for k in KEYS], dtype=np.uint8)
BLOCK_NAMES = [BLOCKS[k][0] for k in KEYS]

# ---- palette ถ่วงน้ำหนักต่อโซน (บล็อก, น้ำหนัก) -----------------------------
PALETTE = {
    "meadow":    [("grass", .74), ("moss", .18), ("dirt", .05), ("coarse", .03)],
    "montane":   [("grass", .55), ("moss", .30), ("podzol", .10), ("coarse", .05)],
    # พื้นป่าสน — podzol เด่นแต่ไม่ล้วน มีเข็มสนทับถม ดินโผล่ รากไม้ มอสตามที่ชื้น
    "floor_conifer": [("podzol", .34), ("coarse", .17), ("moss", .14),
                      ("packed_mud", .10), ("dirt", .09), ("mushroom", .07),
                      ("rooted", .05), ("grass", .04)],
    # พื้นป่าชื้น (ร่องเขา/ด้านเหนือ) — มอสคลุมเป็นหลัก เขียวนุ่ม ไม่ใช่น้ำตาลแห้ง
    "floor_damp": [("moss", .46), ("podzol", .18), ("grass", .16),
                   ("dirt", .10), ("mud", .05), ("rooted", .05)],
    # พื้นป่าผลัดใบที่ต่ำ — สว่างกว่า หญ้าขึ้นได้ ใบไม้ผุเป็นดินร่วน
    # ใบไม้ผุปีที่แล้วให้โทนน้ำตาลอ่อนอุ่น จึงมี mushroom/packed_mud แทรก
    "floor_broad":   [("grass", .28), ("dirt", .18), ("podzol", .14),
                      ("moss", .12), ("mushroom", .10), ("packed_mud", .08),
                      ("rooted", .06), ("coarse", .04)],
    "alpine":    [("grass", .48), ("moss", .24), ("pale_moss", .10), ("gravel", .10), ("coarse", .05), ("stone", .03)],
    # พุ่มเตี้ย (`natural=scrub|heath` 4.1% ของแผนที่) — ชั้นที่หายไปทั้งชั้น
    #
    # ในแอลป์นี่คือแถบ Latschenkiefer/อัลเพนโรส/จูนิเปอร์เหนือแนวป่าและตาม
    # ที่ลาดหินร่วน  พื้นไม่ใช่ทุ่งหญ้าเรียบ: ดินบางสลับหินโผล่ ใบไม้ผุใต้พุ่ม
    # ถ้าปล่อยให้ตกไปใช้ palette ตามระดับความสูง (ซึ่งเป็นสิ่งที่เกิดขึ้นมา
    # ตลอด) มันจะกลายเป็นทุ่งหญ้าเรียบแบบเดียวกับที่ราบ
    "scrub":     [("coarse", .28), ("grass", .22), ("podzol", .16),
                  ("gravel", .12), ("moss", .10), ("dirt", .07),
                  ("stone", .05)],
    # เหนือแนวไม้ที่ไม่ใช่ผา — หินโล่งมีไลเคนเกาะ
    "barren":    [("gravel", .26), ("tuff", .22), ("stone", .16), ("pale_moss", .14), ("andesite", .12), ("coarse", .10)],
    "scree":     [("gravel", .36), ("cobble", .22), ("tuff", .18), ("stone", .14), ("andesite", .10)],
    # ผาต่ำใต้ 900 m — อยู่ในร่มป่า ชื้น มอสเกาะ สีเข้ม
    "rock_low":  [("stone", .34), ("andesite", .24), ("tuff", .18),
                  ("mossy_cob", .12), ("dripstone", .07), ("pale_moss", .05)],
    # ผากลาง 900-1450 m
    "rock_mid":  [("stone", .33), ("andesite", .28), ("tuff", .15),
                  ("gravel", .09), ("cobble", .08), ("dripstone", .07)],
    # ผาหินปูนสูง — Dachstein เป็นหินปูนสีอ่อน ผุกร่อนแบบ karst
    # ไล่ calcite -> diorite -> andesite -> stone; clay/smooth ไม่ใช่ bedrock bridge
    "rock_high": [("calcite", .30), ("diorite", .29), ("andesite", .18),
                  ("stone", .13), ("gravel", .06),
                  ("pale_moss", .04)],
    # ตีนผา — เศษหินที่ร่วงลงมาปนกับดินที่ถูกกัดเซาะ โทนน้ำตาลอมเทา
    # dripstone_block เป็นหินสีน้ำตาลอุ่นตัวเดียวในเกม จึงเป็นแกนของโทนนี้
    "cliff_foot": [("gravel", .21), ("dripstone", .14), ("dead_brain", .08),
                   ("packed_mud", .13), ("coarse", .13), ("tuff", .15),
                   ("cobble", .09), ("mushroom", .07)],
    "wetland":   [("mud", .42), ("moss", .20), ("packed_mud", .18),
                  ("dirt", .12), ("mushroom", .08)],
    "farm":      [("rooted", .34), ("grass", .28), ("packed_mud", .20),
                  ("coarse", .18)],
}

# ---- ระดับความสูงจริง (เมตร) — เขตพืชพรรณ Nördliche Kalkalpen --------------
VALLEY = 650.0
MONTANE = 900.0
SUBALPINE = 1450.0
TREELINE = 1750.0
SNOW_PATCHY = 1950.0
SNOWLINE = 2350.0
MAX_SNOW_UNITS = 16       # eighth-block units: 8 = one block, 16 = two blocks

SLOPE_SCREE = 26.0
SLOPE_STEEP = 33.0
SLOPE_CLIFF = 46.0

BIOMES = [
    (2050, "minecraft:jagged_peaks"),
    (1750, "minecraft:snowy_slopes"),
    (1450, "minecraft:grove"),
    (900, "minecraft:old_growth_spruce_taiga"),
    (0, "minecraft:meadow"),
]


def load_meta():
    """ข้อเท็จจริงของ DEM + ค่าที่คำนวณจาก config สด ๆ ทุกครั้ง

    heightmap.png เก็บความสูงแบบ normalize (0..65535 ต่อช่วง elev จริง) จึงไม่
    ขึ้นกับสเกลแนวตั้ง เปลี่ยน Y_TERRAIN_* ใน config ได้โดยไม่ต้องสร้าง DEM ใหม่
    แต่ค่าอย่าง meters_per_block_v ที่เคยถูกเขียนค้างไว้ในไฟล์จะกลายเป็นค่าเก่า
    ทันที จึงคำนวณทับตรงนี้เสมอ เพื่อให้ไฟล์ meta ไม่มีทางโกหกเรื่องสเกล
    """
    import config as C

    with open(os.path.join(HERE, "heightmap_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)

    blocks = C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN
    meta["y_min"] = C.Y_TERRAIN_MIN
    meta["y_max"] = C.Y_TERRAIN_MAX
    meta["meters_per_block_h"] = float(C.METERS_PER_BLOCK)
    meta["meters_per_block_v"] = (
        (meta["elev_max_m"] - meta["elev_min_m"]) / blocks if blocks else 0.0
    )
    return meta


def vertical_distortion(meta=None):
    """สัดส่วนความเพี้ยน แนวตั้ง/แนวนอน — 1.0 = สมจริงเป๊ะ"""
    meta = meta or load_meta()
    return meta["meters_per_block_v"] / meta["meters_per_block_h"]


def _hash01(i, j, seed):
    """สุ่ม 0..1 จากพิกัดจำนวนเต็ม — ค่าเดิมเสมอสำหรับพิกัดเดิม"""
    h = (i.astype(np.int64) * np.int64(374761393)
         + j.astype(np.int64) * np.int64(668265263)
         + np.int64(seed) * np.int64(1442695041))
    h = (h ^ (h >> np.int64(13))) * np.int64(1274126177)
    h = h ^ (h >> np.int64(16))
    return (h & np.int64(0xFFFFFF)).astype(np.float32) / float(0xFFFFFF)


def value_noise(x0, z0, shape, scale, seed):
    """value noise ที่ยึดกับพิกัดโลกจริง คืนค่า 0..1

    ต้องยึดพิกัดโลก ไม่ใช่ index ใน array มิฉะนั้นการประมวลผลทีละ region จะได้
    ลายเดียวกันซ้ำทุก region เห็นเป็นตารางทั่วแผนที่
    """
    nx, nz = shape
    X = (np.arange(nx, dtype=np.float64) + x0) / scale
    Z = (np.arange(nz, dtype=np.float64) + z0) / scale
    xi = np.floor(X).astype(np.int64)
    zi = np.floor(Z).astype(np.int64)
    xf = (X - xi).astype(np.float32)
    zf = (Z - zi).astype(np.float32)
    # smoothstep ให้ค่าต่อเนื่องลื่น ไม่เห็นขอบ lattice
    xf = xf * xf * (3.0 - 2.0 * xf)
    zf = zf * zf * (3.0 - 2.0 * zf)

    I0, J0 = np.meshgrid(xi, zi, indexing="ij")
    a = _hash01(I0, J0, seed)
    b = _hash01(I0 + 1, J0, seed)
    c = _hash01(I0, J0 + 1, seed)
    d = _hash01(I0 + 1, J0 + 1, seed)

    u = xf[:, None]
    v = zf[None, :]
    return (a * (1 - u) * (1 - v) + b * u * (1 - v)
            + c * (1 - u) * v + d * u * v)


def smooth_noise(x0, z0, shape, scale, seed, octaves=3):
    """noise หลายชั้น ยึดพิกัดโลก — คืนค่าราว -1..1"""
    total = np.zeros(shape, dtype=np.float32)
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        s = max(2.0, scale / (2 ** o))
        total += (value_noise(x0, z0, shape, s, seed + o * 7919) * 2.0 - 1.0) * amp
        norm += amp
        amp *= 0.5
    return total / max(norm, 1e-6)


def white_noise(x0, z0, shape, seed):
    """สุ่มรายบล็อก 0..1 ยึดพิกัดโลก — ตัวที่ทำให้เกิด texture ละเอียด"""
    I, J = np.meshgrid(
        np.arange(shape[0], dtype=np.int64) + int(x0),
        np.arange(shape[1], dtype=np.int64) + int(z0),
        indexing="ij",
    )
    return _hash01(I, J, seed)


def _build_uniformiser(samples=512, seed=4242):
    """ตาราง CDF สำหรับดัดค่าที่ใช้เลือก palette ให้กระจายสม่ำเสมอ

    r ที่ classify() สร้าง (0.28*white + 0.72*smooth) มีการกระจายแบบระฆังคว่ำ
    std แค่ 0.141 และช่วง 5-95 percentile คือ 0.267-0.733 ผลคือ pick() ซึ่งแบ่ง
    ช่วง [0,1) ตามน้ำหนักสะสม จะหยิบแต่ตัวกลาง ๆ ของรายการ

    วัดจริงกับ rock_mid: stone ตั้ง 0.26 ได้ 0.044 | dripstone ตั้ง 0.06 ได้ 0.000
    บล็อกหัวและท้ายของทุก palette จึงแทบไม่เคยปรากฏบนแผนที่

    ตารางสร้างจากตัวอย่างคงที่ตอน import จึงเป็นค่าเดียวกันทุกเครื่องทุกกรอบ
    """
    grain = white_noise(0, 0, (samples, samples), seed)
    clump = smooth_noise(0, 0, (samples, samples), 20.0, seed + 1, 2)
    reference = np.clip(0.28 * grain + 0.72 * (clump * 0.5 + 0.5), 0.0, 0.999)
    quantiles = np.linspace(0.0, 1.0, 257, dtype=np.float32)
    knots = np.quantile(reference.ravel(), quantiles).astype(np.float32)
    knots = np.maximum.accumulate(knots + np.arange(knots.size) * 1e-7)
    return knots, quantiles.astype(np.float32)


_R_KNOTS, _R_VALUES = _build_uniformiser()


def uniformise(values):
    """ดัดค่า 0..1 ที่กระจายแบบระฆังคว่ำให้สม่ำเสมอ โดยคงลำดับ

    ทำให้น้ำหนักใน PALETTE เป็นสัดส่วนที่ได้จริง ไม่ใช่แค่ตัวเลขที่ประกาศไว้
    """
    return np.clip(
        np.interp(np.asarray(values, dtype=np.float32), _R_KNOTS, _R_VALUES),
        0.0, 0.999,
    ).astype(np.float32)


def pick(r, choices):
    """เลือกบล็อกจาก palette ถ่วงน้ำหนักด้วยค่า r (0..1)"""
    out = np.zeros(r.shape, dtype=np.uint8)
    total = sum(w for _, w in choices)
    cum = 0.0
    for name, w in choices:
        hi = cum + w / total
        out[(r >= cum) & (r < hi)] = IDX[name]
        cum = hi
    out[r >= cum] = IDX[choices[-1][0]]
    return out


def propagate_downhill(source, height, steps=10, decay=0.80):
    """ไล่ค่าจากที่สูงลงที่ต่ำ — ใช้ส่งอิทธิพลของหน้าผาลงไปถึงตีนผา

    แต่ละรอบ เซลล์รับค่าจากเพื่อนบ้านที่ *สูงกว่า* ตัวเอง คูณด้วย decay จึงได้
    สนามที่แรงตรงใต้ผาและจางลงเมื่อไกลออกไป โดยไม่ต้องทำ flow routing เต็มรูป

    หยุดเองที่พื้นราบ (ไม่มีเพื่อนบ้านสูงกว่า) ซึ่งถูกต้องตามฟิสิกส์ — หินร่วง
    ไม่ไหลข้ามลานราบ
    """
    source = np.asarray(source, dtype=np.float32)
    height = np.asarray(height, dtype=np.float32)
    out = source.copy()
    for _ in range(int(steps)):
        best = out.copy()
        for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted = np.roll(np.roll(out, dz, axis=0), dx, axis=1)
            higher = np.roll(np.roll(height, dz, axis=0), dx, axis=1) > height
            best = np.maximum(best, np.where(higher, shifted * decay, 0.0))
        if np.allclose(best, out):
            break
        out = best
    return out


# ---- ตีนผา ----------------------------------------------------------------
# หน้าผาปล่อยหินร่วงลงมาสะสมที่ตีน ผลคือแถบดินปนเศษหินสีน้ำตาลอมเทา ไม่ใช่หิน
# ล้วนและไม่ใช่หญ้า ถ้าไม่มีแถบนี้ สีจะเด้งจากผาเทาเป็นหญ้าเขียวในไม่กี่บล็อก
# ซึ่งเป็นรอยต่อที่ตาจับได้ทันทีว่าไม่ธรรมชาติ
CLIFF_SLOPE_START = 50.0        # องศา — เริ่มนับว่าเป็นผาที่ปล่อยหินร่วง
CLIFF_SLOPE_FULL = 65.0
# ค่าที่เหลือ sweep แล้วเทียบกับพื้นที่กองหินที่ micro_relief ถมจริง (2.88%)
# ชุดนี้ให้ครอบคลุม 2.28% และทับกับกองหินจริง 92% — นิยามเชิงเรขาคณิตกับการ
# ทับถมเห็นตรงกันเอง ซึ่งเป็นหลักฐานว่าทั้งคู่จับลักษณะเดียวกันของภูมิประเทศ
CLIFF_FOOT_REACH_BLOCKS = 10.0  # หินไหลลงมาได้ไกลแค่ไหน
CLIFF_FOOT_DECAY = 0.86         # ความแรงที่เหลือต่อหนึ่งก้าวลง
CLIFF_FOOT_CATCH_FULL = 30.0    # ชันไม่เกินนี้ หินเกาะได้เต็มที่
CLIFF_FOOT_CATCH_NONE = 45.0    # ชันเกินนี้ หินไม่เกาะเลย
CLIFF_FOOT_THRESHOLD = 0.25


def cliff_foot_field(elev_m, slope, spacing_m, block_m=4.0):
    """ความแรงของ "ตีนผา" 0..1 ต่อพิกเซล

    นิยามจากรูปทรงสุดท้ายล้วน (มีผาอยู่เหนือขึ้นไปในระยะที่หินไหลถึง และตัวเอง
    ลาดพอให้หินเกาะ) จึงไม่ต้องพึ่งไฟล์ mask จาก micro_relief และจับตีนผา
    ธรรมชาติที่มีอยู่ใน DEM เองด้วย

    ปรับจำนวนก้าวตามความละเอียดของ array ที่ส่งเข้ามา เพื่อให้พรีวิวที่ย่อส่วน
    ได้ระยะเท่ากับโลกจริง
    """
    slope = np.asarray(slope, dtype=np.float32)
    pixels_per_block = max(1.0, spacing_m / block_m)
    steps = max(1, int(round(CLIFF_FOOT_REACH_BLOCKS / pixels_per_block)))

    cliff = np.clip(
        (slope - CLIFF_SLOPE_START) / (CLIFF_SLOPE_FULL - CLIFF_SLOPE_START),
        0.0, 1.0,
    )
    influence = propagate_downhill(
        cliff, elev_m, steps=steps, decay=CLIFF_FOOT_DECAY
    )
    # catch เป็นประตู ไม่ใช่ตัวคูณเชิงเส้น — ถ้าคูณเชิงเส้นทั้งช่วง พื้นลาดปกติ
    # จะโดนหารครึ่งจนไม่มีทางถึง threshold (รอบแรกได้ครอบคลุมแค่ 0.03%)
    catch = np.clip(
        (CLIFF_FOOT_CATCH_NONE - slope)
        / (CLIFF_FOOT_CATCH_NONE - CLIFF_FOOT_CATCH_FULL),
        0.0, 1.0,
    )
    return influence * catch


def terrain_stats(elev_m, spacing_m):
    """ความชัน (องศา), northness (-1..1), ความโค้ง (บวก=เว้า/ร่อง ลบ=นูน/สัน)"""
    e = elev_m.astype(np.float32)
    dz_dy, dz_dx = np.gradient(e, spacing_m)
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    northness = -dz_dy / (np.hypot(dz_dx, dz_dy) + 1e-6)

    # laplacian หาความโค้ง — normalize ด้วยค่าเบี่ยงเบนเพื่อให้ threshold ใช้ได้ทุกภูมิประเทศ
    lap = (
        np.roll(e, 1, 0) + np.roll(e, -1, 0) + np.roll(e, 1, 1) + np.roll(e, -1, 1) - 4 * e
    ) / (spacing_m ** 2)
    s = np.std(lap) + 1e-6
    curv = np.clip(lap / (3 * s), -1.0, 1.0)
    return slope, northness, curv


def snow_units_from_amount(snow_amount, blocked=None, max_units=MAX_SNOW_UNITS,
                           terrain_offset=None):
    """Quantize a continuous snow field into smooth eighth-block thickness.

    A value of 1..7 paints that many snow layers, 8 paints one snow block,
    and 9..16 continues with a second layer/block above it.  The cardinal
    neighbour limiter removes one-column spikes and guarantees that snow
    thickness changes by at most one layer (1/8 block) per horizontal block.

    terrain_offset is the fractional high-resolution terrain height relative
    to the rounded Minecraft surface Y (normally -0.5..0.5).  It is applied
    after smoothing the accumulation depth.  This is the same principle as
    WorldPainter's smooth snow: columns on the low side of a rounded terrain
    step get more snow layers and columns on the high side get fewer, keeping
    the visible snow top continuous.  Up to four extra eighths are therefore
    possible above max_units; max_units remains the nominal accumulation cap.
    """
    amount = np.asarray(snow_amount, dtype=np.float32)
    units = np.zeros(amount.shape, dtype=np.int16)
    active = amount > 0.03
    units[active] = np.clip(
        np.ceil((amount[active] - 0.03) * 8.0), 1, max_units
    ).astype(np.int16)

    if blocked is not None:
        blocked = np.asarray(blocked, dtype=bool)
        if blocked.shape != units.shape:
            raise ValueError("blocked and snow_amount must have the same shape")
        units[blocked] = 0

    # With a maximum thickness of 16, sixteen passes are sufficient for a
    # low point to influence every column that could violate the 1-layer rule.
    for _ in range(max_units):
        prev = units.copy()
        units[1:] = np.minimum(units[1:], prev[:-1] + 1)
        units[:-1] = np.minimum(units[:-1], prev[1:] + 1)
        units[:, 1:] = np.minimum(units[:, 1:], prev[:, :-1] + 1)
        units[:, :-1] = np.minimum(units[:, :-1], prev[:, 1:] + 1)
        if np.array_equal(units, prev):
            break

    if terrain_offset is not None:
        offset = np.asarray(terrain_offset, dtype=np.float32)
        if offset.shape != units.shape:
            raise ValueError(
                "terrain_offset and snow_amount must have the same shape"
            )
        # np.rint uses ties-to-even, while Java/WorldPainter uses Math.round.
        # floor(x + 0.5) reproduces the latter for both positive and negative
        # offsets.  A rounded heightmap can differ from its source by at most
        # half a block, i.e. four snow layers.
        phase = np.floor(offset * 8.0 + 0.5).astype(np.int16)
        covered = units > 0
        units[covered] = np.clip(
            units[covered] + phase[covered], 1, max_units + 4
        )

    return units.astype(np.uint8)


def classify(elev_m, landcover, spacing_m, seed=1234, block_m=4.0, x0=0, z0=0,
             terrain_offset=None):
    """คืน (surface, forest_p, snow_extra, soil)

    surface    uint8 index -> KEYS  บล็อกผิวบนสุด
    forest_p   float32 ความหนาแน่นป่า 0..1 (0 = ไม่มีป่า)
    snow_extra uint8  ความหนาหิมะหน่วย 1/8 บล็อก (ปกติ 0..16;
                สูงสุด 20 เมื่อชดเชยเศษความสูงของ terrain)
    soil       bool   ผิวสุดท้ายเป็นดินที่ปลูกได้ไหม

    ชนิดไม้/สีใบดูที่ ecology.tree_species()
    """
    shape = elev_m.shape
    slope, north, curv = terrain_stats(elev_m, spacing_m)

    # smooth_noise วัด scale เป็นพิกเซลของ array ที่ส่งเข้ามา แต่พรีวิวมักย่อส่วน
    # จึงต้องหารด้วยจำนวนบล็อกต่อพิกเซล ไม่งั้นลายที่จูนบนพรีวิวจะไม่ตรงกับโลกจริง
    step = max(1.0, spacing_m / block_m)   # บล็อกต่อพิกเซล

    # x0/z0 คือพิกัดโลกของมุม array — ทำให้ noise ต่อเนื่องข้าม region
    def nz(scale_blocks, s, oct=3):
        return smooth_noise(x0 / step, z0 / step, shape,
                            max(2.0, scale_blocks / step), s, oct)

    n_big = nz(440, seed)          # ขยับเส้นโซน (~1.8 กม.)
    n_mid = nz(104, seed + 1)      # ปื้นระดับกลาง (~400 ม.)
    grain = white_noise(x0 / step, z0 / step, shape, seed + 2)

    # ตัวเลือกบล็อก: ให้ noise หย่อมเล็ก (~20 บล็อก = 80 ม.) เป็นตัวนำ
    # แล้วโรยเม็ดสุ่มบางๆ — ถ้าใช้ white noise ล้วนจะได้ผิวแบบเกลือพริกไทย
    n_clump = nz(20, seed + 3, 2)
    # ดัดให้กระจายสม่ำเสมอ ไม่งั้นน้ำหนักใน PALETTE ไม่เป็นจริง — บล็อกหัวและ
    # ท้ายของทุกรายการจะแทบไม่ปรากฏ (ดู _build_uniformiser)
    r = uniformise(
        np.clip(0.28 * grain + 0.72 * (n_clump * 0.5 + 0.5), 0.0, 0.999)
    )

    # เส้นโซนขยับตามภูมิประเทศ: ด้านเหนือหนาวกว่า แนวไม้ต่ำลง
    shift = n_big * 120.0 - north * 85.0
    treeline = TREELINE + shift
    snow_patchy = SNOW_PATCHY + shift
    snowline = SNOWLINE + shift * 0.6

    # ความโค้งปรับความชันที่ใช้ตัดสิน: สันเขานูนหินโผล่ง่ายกว่า ร่องเขาสะสมดิน
    slope_eff = slope - curv * 7.0

    # ---- ฐานตามระดับความสูง ---------------------------------------------------
    surf = pick(r, PALETTE["meadow"])
    m = elev_m > MONTANE
    surf[m] = pick(r, PALETTE["montane"])[m]
    m = elev_m > treeline
    surf[m] = pick(r, PALETTE["alpine"])[m]
    m = elev_m > (treeline + 250)
    surf[m] = pick(r, PALETTE["barren"])[m]

    # ---- landcover จาก OSM ----------------------------------------------------
    m = landcover == LC["farm"]
    surf[m] = pick(r, PALETTE["farm"])[m]
    m = landcover == LC["wetland"]
    surf[m] = pick(r, PALETTE["wetland"])[m]
    # พื้นป่าไม่ควรตัดขอบกับทุ่งหญ้าแบบหักดิบ (น้ำตาลชนเขียว)
    # ใช้ความหนาแน่นป่าเป็นความน่าจะเป็นผสมสองชุด ได้แถบเปลี่ยนผ่านแบบ dither
    p_slope0 = np.clip((46.0 - slope) / 14.0, 0.0, 1.0)
    fp0 = (
        (landcover == LC["forest"]).astype(np.float32)
        * np.clip((treeline - elev_m) / 220.0, 0.0, 1.0)
        * p_slope0
        * np.clip(0.75 + 0.45 * (n_mid * 0.5 + 0.5), 0.0, 1.0)
    )
    # ตัวสุ่มแยกจาก r ที่ใช้เลือกบล็อก ไม่งั้นขอบป่าจะไปสัมพันธ์กับลายพื้นผิว
    rb = np.clip(0.35 * white_noise(x0 / step, z0 / step, shape, seed + 909)
                 + 0.65 * (nz(16, seed + 910, 2) * 0.5 + 0.5), 0.0, 0.999)
    in_floor = rb < fp0
    # ความชื้น: ร่องเขา + ด้านเหนือ + หย่อม noise — ที่ชื้นพื้นเป็นมอสเขียว
    damp0 = np.clip(0.45 + 0.30 * north + 0.35 * curv + 0.20 * n_big, 0, 1)
    conifer = in_floor & (elev_m > MONTANE)
    broad = in_floor & ~conifer
    dampf = in_floor & (damp0 > 0.62)
    surf[broad] = pick(r, PALETTE["floor_broad"])[broad]
    surf[conifer] = pick(r, PALETTE["floor_conifer"])[conifer]
    surf[dampf] = pick(r, PALETTE["floor_damp"])[dampf]
    # พุ่มเตี้ยต้องมาก่อน scree/หิน เพราะที่ลาดพุ่มมักซ้อนกับที่ลาดหินร่วง
    m = landcover == LC["scrub"]
    surf[m] = pick(r, PALETTE["scrub"])[m]
    m = landcover == LC["scree"]
    surf[m] = pick(r, PALETTE["scree"])[m]

    # ---- ความชัน + ความโค้ง ---------------------------------------------------
    # ร่องเขาชันสะสมหินร่วง
    gully = (slope > SLOPE_SCREE) & (curv > 0.25) & (elev_m > MONTANE)
    surf[gully] = pick(r, PALETTE["scree"])[gully]

    steep = slope_eff > (SLOPE_STEEP + n_mid * 5)
    cliff = slope_eff > (SLOPE_CLIFF + n_mid * 4)
    rock = cliff | (landcover == LC["rock"])

    m = steep & ~rock
    surf[m] = pick(r, PALETTE["rock_mid"])[m]

    m = rock & (elev_m >= SUBALPINE)
    surf[m] = pick(r, PALETTE["rock_high"])[m]
    m = rock & (elev_m >= MONTANE) & (elev_m < SUBALPINE)
    surf[m] = pick(r, PALETTE["rock_mid"])[m]
    m = rock & (elev_m < MONTANE)
    surf[m] = pick(r, PALETTE["rock_low"])[m]

    # ---- ตีนผา -----------------------------------------------------------
    # ต้องมาหลังกฎหินทั้งหมด เพราะตัวหน้าผาเองต้องเป็นหิน ไม่ใช่ตีนผา
    # และต้องมาก่อนหิมะ/น้ำ ซึ่งทับได้ทุกอย่าง
    #
    # กันเฉพาะหน้าผาจริง (rock) ไม่กัน steep — เซลล์ตีนผามีความชัน median 31
    # องศา p75 35 องศา ซึ่งคือมุมพักของกองหินจริง (30-38) การหัก steep(>33)
    # ออกจะตัดกองหินที่เป็นตัวจริงทิ้งไปเกือบครึ่ง (วัดได้ 2.28% -> 1.41%)
    foot = cliff_foot_field(elev_m, slope, spacing_m, block_m)
    m = (foot > CLIFF_FOOT_THRESHOLD) & ~rock
    surf[m] = pick(r, PALETTE["cliff_foot"])[m]

    # ---- หิมะ -----------------------------------------------------------------
    # แถบเปลี่ยนผ่านแบบน่าจะเป็น ไม่ใช่เส้นคม
    # หิมะขับด้วยสนามต่อเนื่องตัวเดียว แล้วแปลงเป็นความหนาหน่วย 1/8 บล็อก
    # โดยไม่เปลี่ยนบล็อกฐานธรรมชาติเป็น snow_block ที่ระดับ terrain เดิม
    # จึงไม่มีรอยตกหนึ่งบล็อกตรงรอยต่อ layer 8 -> หิมะหนาอีกต่อไป
    slope_pen = 1.0 - np.clip((slope - 40.0) / 35.0, 0.0, 0.85)
    n_snow = nz(34, seed + 4001, 2)
    patchy_snow = (
        (elev_m - (snow_patchy - 140.0)) / 240.0
        * (0.72 + 0.28 * np.clip(north, -1, 1))
        * slope_pen
        + 0.14 * n_snow
    )
    settling = np.clip((72.0 - slope) / 32.0, 0.15, 1.0)
    permanent_snow = (
        1.0 + (elev_m - snowline) / 320.0
    ) * (0.65 + 0.35 * settling)
    snow_amt = np.maximum(patchy_snow, permanent_snow)

    surf[landcover == LC["glacier"]] = IDX["ice"]
    surf[landcover == LC["water"]] = IDX["water"]
    snow_lv = snow_units_from_amount(
        snow_amt,
        (landcover == LC["water"]) | (landcover == LC["glacier"]),
        terrain_offset=terrain_offset,
    )

    # ---- ความหนาแน่นของป่า -----------------------------------------------------
    # คืนเป็นค่าต่อเนื่อง 0..1 ไม่ใช่ mask สุ่มรายบล็อก เพราะตัวปลูกต้นไม้จะเอาไป
    # ใช้เป็นความน่าจะเป็นอีกที ถ้า threshold ตรงนี้ด้วยจะถูกกรองซ้ำสองชั้น
    # ป่าบางลงเมื่อเข้าใกล้แนวไม้ และเมื่อลาดชันขึ้น
    p_alt = np.clip((treeline - elev_m) / 220.0, 0.0, 1.0)
    p_slope = np.clip((46.0 - slope) / 14.0, 0.0, 1.0)
    forest_p = (
        (landcover == LC["forest"]).astype(np.float32)
        * p_alt
        * p_slope
        * (surf != IDX["water"])
        * np.clip(0.75 + 0.45 * (n_mid * 0.5 + 0.5), 0.0, 1.0)
    )
    # ที่โล่งกลางป่า — ป่าจริงไม่ได้ทึบสม่ำเสมอ มีช่องว่างจากไม้ล้ม/ดินตื้น/หิมะถล่ม
    glade = np.clip((nz(60, seed + 5001) - 0.18) * 2.4, 0.0, 1.0)
    forest_p *= (1.0 - 0.85 * glade)

    # ตัดป่าออกตรงที่ผิวสุดท้ายไม่ใช่ดิน — กฎความชัน/หิมะทำงานทีหลัง landcover
    # ถ้าไม่เช็คตรงนี้ ต้นไม้กับหญ้าจะไปงอกบนหินและหิมะ
    soil = np.isin(surf, SOIL_IDS)
    forest_p *= soil
    # Thin snow can coexist with sparse conifers.  One full block of
    # accumulation must stay clear so trunks are not buried or rejected.
    forest_p *= np.clip((8.0 - snow_lv.astype(np.float32)) / 6.0, 0.0, 1.0)

    # ชนิดใบไม้ไม่ได้ตัดสินที่นี่แล้ว — ย้ายไป ecology.tree_species() ซึ่ง paint
    # ใช้ตัวเดียวกัน เดิมที่นี่มีกฎของตัวเองทำให้พรีวิวแสดงองค์ประกอบป่าคนละ
    # แบบกับโลกจริง (surface.py ห้าม import ecology เพราะ ecology import ตัวนี้)
    return surf, forest_p, snow_lv, soil


def biome_of(elev_m):
    """คืน array ของ index เข้า BIOMES"""
    # BIOMES เรียงจากสูงไปต่ำ — ไล่ย้อนจากล่างขึ้นบน ตัวสูงกว่าจึงทับได้ถูก
    out = np.full(elev_m.shape, len(BIOMES) - 1, dtype=np.uint8)
    for i in range(len(BIOMES) - 1, -1, -1):
        out[elev_m >= BIOMES[i][0]] = np.uint8(i)
    return out


def to_rgb(surf, forest_p, leaf, snow_lv=None, elev_m=None):
    # leaf มาจาก ecology.species_leaf_index() — ตัวเรียกต้องคำนวณเอง
    """ผสมสีใบไม้ทับสีพื้นตามความหนาแน่นป่า ทำให้ขอบป่าดูจางลงจริง"""
    rgb = PREVIEW_RGB[surf].astype(np.float32)
    lf = PREVIEW_RGB[leaf].astype(np.float32)
    w = np.clip(forest_p * 1.15, 0.0, 1.0)[..., None]
    rgb = rgb * (1 - w) + lf * w
    if snow_lv is not None:
        w = np.clip(snow_lv.astype(np.float32) / 8.0, 0, 1)[..., None] * 0.9
        rgb = rgb * (1 - w) + np.array(BLOCKS["snow_thin"][1], np.float32) * w
    if elev_m is not None:
        dy, dx = np.gradient(elev_m.astype(np.float32))
        slope = np.arctan(np.hypot(dx, dy))
        aspect = np.arctan2(-dx, dy)
        az, alt = np.radians(315.0), np.radians(50.0)
        sh = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
        rgb *= np.clip(0.58 + 0.62 * sh, 0.38, 1.32)[..., None]
    return np.clip(rgb, 0, 255).astype(np.uint8)


def decor_fields(elev_m, spacing_m, x0=0, z0=0, block_m=4.0, seed=5150):
    """สนามข้อมูลสำหรับการตกแต่ง — คืน dict ของ array ขนาดเท่า elev_m

    แยกจาก classify() เพื่อให้ปรับกฎตกแต่งได้โดยไม่ไปแตะกฎเลือกบล็อกผิว

    damp     ความชื้น 0..1 — ด้านเหนือ + ร่องเขา ชื้นกว่าสันเขาที่หันใต้
    patch_a/b  noise คนละความถี่ ใช้แยก "หย่อม" ของพืชแต่ละชนิดไม่ให้ปนกันมั่ว
    clearing  ที่โล่งกลางป่า 0..1 (ค่าสูง = โล่ง)
    open_sky  ความโล่งเหนือหัว ใช้ตัดสินว่าที่นั้นแดดถึงไหม
    """
    shape = elev_m.shape
    step = max(1.0, spacing_m / block_m)
    slope, north, curv = terrain_stats(elev_m, spacing_m)

    def nz(scale_blocks, s, oct=3):
        return smooth_noise(x0 / step, z0 / step, shape,
                            max(2.0, scale_blocks / step), s, oct)

    damp = np.clip(0.45 + 0.30 * north + 0.35 * curv + 0.20 * nz(70, seed), 0, 1)
    # ที่โล่งกลางป่า — หย่อมใหญ่ ๆ ที่ต้นไม้ไม่ขึ้น ทำให้ป่าไม่ทึบเท่ากันหมด
    clearing = np.clip((nz(60, seed + 1) - 0.18) * 2.4, 0, 1)
    return {
        "slope": slope,
        "north": north,
        "curv": curv,
        "damp": damp,
        "clearing": clearing,
        "patch_a": nz(14, seed + 2, 2) * 0.5 + 0.5,
        "patch_b": nz(9, seed + 3, 2) * 0.5 + 0.5,
        "patch_c": nz(24, seed + 4, 2) * 0.5 + 0.5,
    }
