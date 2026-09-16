"""กฎนิเวศที่ preview กับ paint ต้องใช้ร่วมกัน — แหล่งความจริงเดียว

ทำไมต้องมีไฟล์นี้: เดิมการเลือกชนิดไม้ถูกตัดสินสองที่ด้วยกฎคนละชุด
  surface.classify()  คืน `leaf` จากความสูง + hash ของผิวดิน  -> preview ใช้
  paint_surface.py    เลือก kind จากความสูง + RNG ต่อต้น       -> โลกจริงใช้
พรีวิวจึงแสดงองค์ประกอบป่าคนละแบบกับที่ paint เขียนจริง และการจูนกฎบนพรีวิว
ไม่มีผลผูกพันกับผลลัพธ์ ทุกอย่างที่ทั้งสองฝั่งต้องเห็นตรงกันต้องย้ายมาที่นี่

ชนิดไม้เลือกจาก "หมู่ไม้" (stand) ไม่ใช่สุ่มอิสระต่อต้น — ป่าจริงในเขตแอลป์
ขึ้นเป็นผืนของชนิดเดียวกัน การสุ่มต่อต้นให้ป่าผสมเนื้อเดียวกันทั้งแผนที่
ซึ่งเป็นลักษณะที่ทำให้ป่าดูสังเคราะห์ทันที
"""

import numpy as np

import surface as S

# ชนิดไม้ที่ใช้จริง — ต้องตรงกับคีย์ใน tree_schematics.KINDS ที่มีไฟล์
SPECIES = ("spruce", "oak", "birch", "azalea", "krummholz")
SPEC = {name: i for i, name in enumerate(SPECIES)}

# สีใบสำหรับพรีวิว — map เข้าคีย์ของ surface.BLOCKS
LEAF_KEY = {
    "spruce": "spruce_leaf",
    "oak": "oak_leaf",
    "birch": "birch_leaf",
    "azalea": "azalea_leaf",
    "krummholz": "spruce_leaf",
}
LEAF_IDX = np.asarray(
    [S.IDX[LEAF_KEY[name]] for name in SPECIES], dtype=np.uint8
)

# ขนาดหมู่ไม้ (บล็อก) — 90 บล็อก = 360 m ราว 13 ha ใกล้เคียงผืนป่าภูเขาจริง
STAND_SCALE_BLOCKS = 90.0
STAND_SEED = 3301

# อายุเชิงโครงสร้างของหมู่ไม้ ไม่ใช่อายุปีจริง: 0 = ระยะฟื้นตัว/พุ่มหนา,
# 1 = mature/old-growth ที่มีไม้เด่นและ deadwood มากกว่า ใช้ scale ใหญ่กว่า
# species stand เล็กน้อยเพื่อไม่ให้เส้นแบ่งชนิดไม้กับอายุซ้อนกันเป็น polygon เดียว
STAND_AGE_SCALE_BLOCKS = 132.0
STAND_AGE_SEED = 4817

# แนวไม้แคระ — ต่ำกว่า TREELINE ลงมา เพราะไม้จะเตี้ยลงก่อนถึงแนวไม้จริง
KRUMMHOLZ_BAND = 120.0


# ความสูงเรือนยอดเป็นบล็อก ที่สเกล 4 m/block — อ้างจากความสูงจริงของชนิดไม้
# ในเขต Nördliche Kalkalpen (สนนอร์เวย์โตเต็มที่ 40-50 m, บีช/โอ๊ก 30-40 m,
# เบิร์ช 25-30 m, อะซาเลียเป็นไม้พุ่ม, สนแคระใกล้แนวไม้สูงไม่เกิน 3 m)
SPECIES_HEIGHT_BLOCKS = {
    "spruce": 11,
    "oak": 9,
    "birch": 8,
    "azalea": 3,
    "krummholz": 2,
}
HEIGHT_BY_SPECIES = np.asarray(
    [SPECIES_HEIGHT_BLOCKS[name] for name in SPECIES], dtype=np.float32
)


def canopy_height(species_idx, forest_p, elev_m):
    """ความสูงเรือนยอดเป็นบล็อก — บางลงเมื่อป่าโปร่งและเมื่อใกล้แนวไม้

    ใช้ทั้งใน render_view.py (วาด silhouette) และเป็นเกณฑ์คัด schematic
    ป่าที่ความหนาแน่นต่ำคือขอบป่า/ที่โล่ง ต้นจึงเล็กกว่าใจกลางผืน
    """
    base = HEIGHT_BY_SPECIES[np.asarray(species_idx, dtype=np.intp)]
    density = np.clip(np.asarray(forest_p, dtype=np.float32) / 0.45, 0.35, 1.0)
    altitude = np.clip(
        (S.TREELINE - np.asarray(elev_m, dtype=np.float32)) / 350.0, 0.30, 1.0
    )
    return base * density * altitude


def stand_field(shape, spacing_m, x0=0, z0=0, block_m=4.0,
                scale_blocks=STAND_SCALE_BLOCKS, seed=STAND_SEED):
    """noise ความถี่ต่ำ 0..1 ที่กำหนด "ชนิดเด่นของหมู่ไม้นี้"

    ยึดพิกัดโลกเหมือน surface.classify() จึงต่อเนื่องข้าม region และให้ค่า
    เดียวกันไม่ว่าจะเรียกจากพรีวิวที่ย่อส่วนหรือจาก paint ที่ทำทีละ tile
    """
    step = max(1.0, spacing_m / block_m)
    noise = S.smooth_noise(
        x0 / step, z0 / step, shape,
        max(2.0, scale_blocks / step), seed, 2,
    )
    return np.clip(noise * 0.5 + 0.5, 0.0, 1.0)


def stand_age_field(shape, spacing_m, x0=0, z0=0, block_m=4.0,
                    scale_blocks=STAND_AGE_SCALE_BLOCKS,
                    seed=STAND_AGE_SEED):
    """Return a world-stable 0..1 structural-age field for forest stands."""
    step = max(1.0, spacing_m / block_m)
    broad = S.smooth_noise(
        x0 / step, z0 / step, shape,
        max(2.0, scale_blocks / step), seed, 2,
    )
    detail = S.smooth_noise(
        x0 / step, z0 / step, shape,
        max(2.0, scale_blocks * 0.42 / step), seed + 1, 2,
    )
    # broad controls coherent stands; detail softens their boundaries without
    # turning age into per-tree salt-and-pepper noise.
    return np.clip(0.72 * (broad * 0.5 + 0.5)
                   + 0.28 * (detail * 0.5 + 0.5), 0.0, 1.0)


def layer_age_probability(layer, age, interior=1.0):
    """Tree-layer probability from stand age and distance inside the forest.

    Emergent and canopy trees fade toward the polygon edge while understory is
    retained, producing canopy -> sapling/shrub -> scrub instead of a vertical
    wall of mature trees at the OSM boundary.
    """
    age = np.clip(np.asarray(age, dtype=np.float32), 0.0, 1.0)
    interior = np.clip(np.asarray(interior, dtype=np.float32), 0.0, 1.0)
    if layer == "emergent":
        value = (0.15 + 0.85 * age) * interior ** 2
    elif layer == "canopy":
        value = (0.65 + 0.35 * age) * (0.30 + 0.70 * interior)
    elif layer == "under":
        value = np.clip((0.95 - 0.45 * age)
                        * (1.12 - 0.12 * interior), 0.0, 1.0)
    else:
        raise ValueError(f"unknown forest layer: {layer}")
    return float(value) if value.ndim == 0 else value


def deadwood_thresholds(age):
    """Cumulative placement thresholds for forest-floor structure.

    Young stands favour living bushes and contain little deadwood. Mature
    stands gain fallen logs and stumps while boulders remain geology-driven.
    """
    age = float(np.clip(age, 0.0, 1.0))
    fallen = 0.06 + 0.18 * age
    stump = fallen + 0.04 + 0.08 * age
    boulder = stump + 0.08
    bush = boulder + 0.18 * (1.0 - age)
    return fallen, stump, boulder, bush


def tree_species(elev_m, stand, damp, roll):
    """เลือกชนิดไม้ คืน index เข้า SPECIES — รับได้ทั้ง scalar และ array

    elev_m ความสูงจริง (เมตร)
    stand  ค่าหมู่ไม้ 0..1 จาก stand_field() — ตัวกำหนดชนิดเด่นของผืน
    damp   ความชื้น 0..1 จาก surface.decor_fields()
    roll   สุ่ม 0..1 ต่อต้น — ใช้เฉพาะผสมชนิดรอง ไม่ใช่ตัวเลือกหลัก

    เขตพืชพรรณตาม Nördliche Kalkalpen: ต่ำกว่า 900 m เป็นไม้ผลัดใบ
    900-1450 m สนเป็นหลัก เหนือ 1450 m สนล้วน และใกล้แนวไม้เป็นสนแคระ
    """
    elev_m = np.asarray(elev_m, dtype=np.float32)
    stand = np.asarray(stand, dtype=np.float32)
    damp = np.asarray(damp, dtype=np.float32)
    roll = np.asarray(roll, dtype=np.float32)

    out = np.full(np.broadcast(elev_m, stand, damp, roll).shape,
                  SPEC["oak"], dtype=np.uint8)

    broadleaf = elev_m <= S.MONTANE
    conifer = (elev_m > S.MONTANE) & (elev_m <= S.TREELINE - KRUMMHOLZ_BAND)
    dwarf = elev_m > S.TREELINE - KRUMMHOLZ_BAND

    # ---- เขตไม้ผลัดใบ: โอ๊กเป็นหลัก มีผืนเบิร์ชและพุ่มอะซาเลียริมที่ชื้น ----
    out = np.where(broadleaf & (stand < 0.34), SPEC["birch"], out)
    out = np.where(
        broadleaf & (stand > 0.80) & (damp > 0.55), SPEC["azalea"], out
    )
    # ชนิดรองแทรกในผืน — ป่าจริงไม่ได้บริสุทธิ์ 100%
    out = np.where(broadleaf & (roll < 0.15), SPEC["birch"], out)
    out = np.where(
        broadleaf & (roll > 0.93) & (damp > 0.60), SPEC["azalea"], out
    )

    # ---- เขตสน: สนล้วน ยกเว้นผืนเบิร์ชในช่วงล่างของเขต ----
    out = np.where(conifer, SPEC["spruce"], out)
    sub_montane = conifer & (elev_m < S.SUBALPINE)
    out = np.where(sub_montane & (stand > 0.78), SPEC["birch"], out)
    out = np.where(sub_montane & (roll < 0.12), SPEC["birch"], out)

    # ---- ใกล้แนวไม้: สนแคระล้วน ลมแรงเกินกว่าชนิดอื่นจะรอด ----
    out = np.where(dwarf, SPEC["krummholz"], out)
    return out


def species_leaf_index(species_idx):
    """แปลง index ชนิดไม้ -> index สีใบใน surface.KEYS (สำหรับพรีวิว)"""
    return LEAF_IDX[np.asarray(species_idx, dtype=np.intp)]


def canopy_species(elev_m, spacing_m, damp, x0=0, z0=0, block_m=4.0,
                   seed=STAND_SEED):
    """ชนิดไม้เด่นต่อพิกเซล สำหรับพรีวิว — ไม่มี roll ต่อต้น

    พรีวิวหนึ่งพิกเซลแทนพื้นที่หลายบล็อก ชนิดที่ควรแสดงคือชนิดเด่นของหมู่
    จึงส่ง roll = 0.5 ซึ่งอยู่นอกช่วงของทุกกฎชนิดรอง
    """
    stand = stand_field(
        elev_m.shape, spacing_m, x0=x0, z0=z0, block_m=block_m, seed=seed
    )
    return tree_species(elev_m, stand, damp, 0.5)
