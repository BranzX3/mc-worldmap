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
    "smooth":      ("smooth_stone",       (159,159,159)),   # 159  อุดช่องว่าง
    "andesite":    ("andesite",           (136,136,137)),   # 136
    "gravel":      ("gravel",             (132,128,126)),   # 128
    "cobble":      ("cobblestone",        (128,127,128)),   # 127
    "stone":       ("stone",              (126,126,126)),   # 126
    "mossy_cob":   ("mossy_cobblestone",  (110,118, 95)),   # 115
    "dripstone":   ("dripstone_block",    (134,108, 92)),   # 112
    "pale_moss":   ("pale_moss_block",    (107,112,105)),   # 111
    "tuff":        ("tuff",               (108,109,103)),   # 108
    "deepslate":   ("deepslate",          ( 80, 80, 83)),   #  80
    "cob_deep":    ("cobbled_deepslate",  ( 77, 77, 81)),   #  78
    "basalt":      ("smooth_basalt",      ( 73, 72, 78)),   #  73
    # ---- ดิน/พืช ----
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
PREVIEW_RGB = np.array([BLOCKS[k][1] for k in KEYS], dtype=np.uint8)
BLOCK_NAMES = [BLOCKS[k][0] for k in KEYS]

# ---- palette ถ่วงน้ำหนักต่อโซน (บล็อก, น้ำหนัก) -----------------------------
PALETTE = {
    "meadow":    [("grass", .74), ("moss", .18), ("dirt", .05), ("coarse", .03)],
    "montane":   [("grass", .55), ("moss", .30), ("podzol", .10), ("coarse", .05)],
    # พื้นป่าสน — podzol เด่นแต่ไม่ล้วน มีเข็มสนทับถม ดินโผล่ รากไม้ มอสตามที่ชื้น
    "floor_conifer": [("podzol", .40), ("coarse", .20), ("moss", .16),
                      ("dirt", .10), ("rooted", .08), ("grass", .06)],
    # พื้นป่าชื้น (ร่องเขา/ด้านเหนือ) — มอสคลุมเป็นหลัก เขียวนุ่ม ไม่ใช่น้ำตาลแห้ง
    "floor_damp": [("moss", .46), ("podzol", .18), ("grass", .16),
                   ("dirt", .10), ("mud", .05), ("rooted", .05)],
    # พื้นป่าผลัดใบที่ต่ำ — สว่างกว่า หญ้าขึ้นได้ ใบไม้ผุเป็นดินร่วน
    "floor_broad":   [("grass", .34), ("dirt", .22), ("podzol", .18),
                      ("moss", .14), ("rooted", .07), ("coarse", .05)],
    "alpine":    [("grass", .48), ("moss", .24), ("pale_moss", .10), ("gravel", .10), ("coarse", .05), ("stone", .03)],
    # เหนือแนวไม้ที่ไม่ใช่ผา — หินโล่งมีไลเคนเกาะ
    "barren":    [("gravel", .26), ("tuff", .22), ("stone", .16), ("pale_moss", .14), ("andesite", .12), ("coarse", .10)],
    "scree":     [("gravel", .36), ("cobble", .22), ("tuff", .18), ("stone", .14), ("andesite", .10)],
    # ผาต่ำใต้ 900 m — อยู่ในร่มป่า ชื้น มอสเกาะ สีเข้ม
    "rock_low":  [("stone", .24), ("mossy_cob", .18), ("cob_deep", .16), ("andesite", .14), ("deepslate", .14), ("tuff", .14)],
    # ผากลาง 900-1450 m
    "rock_mid":  [("stone", .26), ("andesite", .22), ("cobble", .16), ("tuff", .12), ("smooth", .10), ("gravel", .08), ("dripstone", .06)],
    # ผาหินปูนสูง — Dachstein เป็นหินปูนสีอ่อน ผุกร่อนแบบ karst
    # ไล่ 224 -> 189 -> 166 -> 159 -> 136 -> 126 ใช้ clay/smooth_stone อุดช่องว่าง
    "rock_high": [("calcite", .24), ("diorite", .20), ("clay", .08),
                  ("smooth", .13), ("andesite", .13), ("gravel", .07),
                  ("stone", .11), ("pale_moss", .04)],
    "wetland":   [("mud", .58), ("moss", .24), ("dirt", .18)],
    "farm":      [("rooted", .48), ("grass", .30), ("coarse", .22)],
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
    with open(os.path.join(HERE, "heightmap_meta.json"), encoding="utf-8") as f:
        return json.load(f)


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
    """คืน (surface, canopy, leaf, snow_extra)

    surface    uint8 index -> KEYS  บล็อกผิวบนสุด
    canopy     uint8 ความหนาเรือนยอด (0 = ไม่มีป่า)
    leaf       uint8 index -> KEYS  ชนิดใบ
    snow_extra uint8  ความหนาหิมะหน่วย 1/8 บล็อก (ปกติ 0..16;
                สูงสุด 20 เมื่อชดเชยเศษความสูงของ terrain)
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
    r = np.clip(0.28 * grain + 0.72 * (n_clump * 0.5 + 0.5), 0.0, 0.999)

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
    soil_ids = np.array([IDX[k] for k in
                         ("grass", "moss", "podzol", "dirt", "coarse", "rooted")])
    soil = np.isin(surf, soil_ids)
    forest_p *= soil
    # Thin snow can coexist with sparse conifers.  One full block of
    # accumulation must stay clear so trunks are not buried or rejected.
    forest_p *= np.clip((8.0 - snow_lv.astype(np.float32)) / 6.0, 0.0, 1.0)

    in_forest = forest_p > 0.02
    leaf = np.zeros(shape, dtype=np.uint8)
    leaf[in_forest] = IDX["oak_leaf"]
    leaf[in_forest & (elev_m > MONTANE)] = IDX["spruce_leaf"]
    mix = in_forest & (elev_m > MONTANE) & (elev_m < SUBALPINE) & (r > 0.86)
    leaf[mix] = IDX["birch_leaf"]
    low = in_forest & (elev_m < MONTANE) & (r > 0.72)
    leaf[low] = IDX["birch_leaf"]
    leaf[in_forest & (elev_m < VALLEY + 200) & (r < 0.06)] = IDX["azalea_leaf"]

    return surf, forest_p, leaf, snow_lv, soil


def biome_of(elev_m):
    """คืน array ของ index เข้า BIOMES"""
    # BIOMES เรียงจากสูงไปต่ำ — ไล่ย้อนจากล่างขึ้นบน ตัวสูงกว่าจึงทับได้ถูก
    out = np.full(elev_m.shape, len(BIOMES) - 1, dtype=np.uint8)
    for i in range(len(BIOMES) - 1, -1, -1):
        out[elev_m >= BIOMES[i][0]] = np.uint8(i)
    return out


def to_rgb(surf, forest_p, leaf, snow_lv=None, elev_m=None):
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
