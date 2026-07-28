"""biome ที่ออกแบบเองเพื่อ "สี" โดยเฉพาะ — แหล่งความจริงเดียวของโทนสีทั้งแผนที่

ทำไมไม่ใช้ biome วานิลลา: สีหญ้า/ใบไม้ของวานิลลามาจาก colormap ที่ index ด้วย
(temperature, downfall) และ temperature ถูก clamp ที่ 0 ผลคือ grove, snowy_slopes,
jagged_peaks, frozen_peaks, snowy_taiga **ให้สีหญ้าเดียวกันเป๊ะ** (128,180,151)
ทั้งหมด — ไล่ระดับความสูงทั้งเทือกเขาจึงได้สีจริงแค่ 3 เฉด ไม่พอจะไล่โทนให้สวย

biome เป็น data-driven ตั้งแต่ 1.19.4 และเราส่ง datapack อยู่แล้ว (ขยายความสูงโลก)
จึงกำหนด grass_color/foliage_color/water_color เองได้ตรง ๆ ไม่ต้องผ่าน colormap

ตรวจแล้วว่าเขียนได้จริง: amulet `get_add_biome()` รับ string อะไรก็ได้ และ
PyMCTranslate เป็น pass-through สำหรับชื่อที่ไม่รู้จัก (`if biome in map: ...;
return biome`) ชื่อ custom จึงถูกเขียนลง NBT ตรง ๆ

บริบท: โลกนี้เป็น MMORPG ที่บล็อกไม่ถูก update จากผู้เล่นหรือสภาพอากาศ และสัตว์
มาจากระบบ lifeskill ของเกม ดังนั้น temperature/spawners/has_precipitation ของ
biome ไม่มีผลกับ gameplay — เหลือแค่สีที่ต้องออกแบบ
"""

import numpy as np

NAMESPACE = "salzkammergut"

# ---------------------------------------------------------------- โทนสี
# ไล่จากหุบเขาอุ่นเขียวสด -> ป่าสนเข้ม -> ใต้แนวไม้ซีดลง -> เหนือแนวไม้เทาเขียว
# แนวคิด: พืชแอลป์ที่สูงขึ้นจะซีดและออกเทามากขึ้นเพราะใบเล็ก มีขนคลุม และมีไลเคน
# ปน ไม่ใช่เขียวสดแบบหุบเขา ค่าอ้างอิงจากวานิลลาที่วัดมา:
#   meadow (131,187,109) | taiga (134,183,131) | เขตหนาว (128,180,151)
#   pale_garden grass (119,130,114) = เทาเขียวที่สุดที่วานิลลามี
BIOMES = [
    # (คีย์, ชื่อ, สีหญ้า, สีใบไม้, สีน้ำ, สีฟ้า)
    ("valley",    "หุบเขา/ทุ่ง",      "#8fbe63", "#6aa845", "#2c6fa8", "#7ba4ff"),
    ("montane",   "ป่าภูเขา",         "#7fae60", "#5c9840", "#2c6fa8", "#7da3ff"),
    ("subalpine", "ป่าสนใต้แนวไม้",   "#86ac7a", "#65975f", "#2f74ab", "#7fa2ff"),
    ("alpine",    "เหนือแนวไม้",      "#8ea78a", "#76986f", "#3279ad", "#81a0ff"),
    ("peak",      "ยอดหิน/หิมะ",      "#96a291", "#86937f", "#3a80b2", "#859dff"),
    ("lake",      "ทะเลสาบ",          "#8fbe63", "#6aa845", "#1f6d9c", "#7ba4ff"),
    ("stream",    "ลำธาร",            "#87b862", "#66a244", "#3d8fbf", "#7ba4ff"),
]
KEYS = [b[0] for b in BIOMES]
IDX = {key: i for i, key in enumerate(KEYS)}
FULL_NAME = [f"{NAMESPACE}:{key}" for key in KEYS]


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


PREVIEW_GRASS = np.asarray([_rgb(b[2]) for b in BIOMES], dtype=np.uint8)
PREVIEW_WATER = np.asarray([_rgb(b[4]) for b in BIOMES], dtype=np.uint8)


# ---------------------------------------------------------- การเลือก biome
# เส้นแบ่งต้องเป็นชุดเดียวกับที่ surface.classify() ใช้ขยับแนวไม้ ไม่งั้นขอบสี
# biome จะไม่ตรงกับขอบพืชพรรณที่วาดไว้ แล้วจะเห็นเป็นแถบสีผิดที่
def biome_index(elev_m, water, depth, treeline=None, montane=None,
                subalpine=None):
    """คืน index เข้า KEYS ต่อพิกเซล

    elev_m   ความสูงจริง (m)
    water    mask ผืนน้ำ
    depth    ความลึกน้ำ (บล็อก) — ใช้แยกทะเลสาบจากลำธาร
    treeline ความสูงแนวไม้ต่อพิกเซล (ถ้าไม่ให้ใช้ค่าคงที่ของ surface.py)
    """
    import surface as S

    elev_m = np.asarray(elev_m, dtype=np.float32)
    water = np.asarray(water, dtype=bool)
    depth = np.asarray(depth)
    if treeline is None:
        treeline = np.full(elev_m.shape, S.TREELINE, dtype=np.float32)
    if montane is None:
        montane = np.full(elev_m.shape, S.MONTANE, dtype=np.float32)
    if subalpine is None:
        subalpine = np.full(elev_m.shape, S.SUBALPINE, dtype=np.float32)

    out = np.full(elev_m.shape, IDX["valley"], dtype=np.uint8)
    out[elev_m > montane] = IDX["montane"]
    out[elev_m > subalpine] = IDX["subalpine"]
    out[elev_m > treeline] = IDX["alpine"]
    out[elev_m > treeline + 350.0] = IDX["peak"]

    # น้ำทับท้ายสุด — ทะเลสาบลึกได้สีเข้ม ลำธารตื้นได้สีใสกว่า
    out[water] = IDX["stream"]
    out[water & (depth >= 3)] = IDX["lake"]
    return out


# ------------------------------------------------------------- datapack
def _music(sound):
    return {
        "default": {"max_delay": 24000, "min_delay": 12000, "sound": sound}
    }


# ดนตรีประจำโซน — ยืมของวานิลลาที่โทนตรงกัน เป็นตัวควบคุมเสียงตัวเดียวที่
# biome ทำได้จริงบนผิวดิน (visual/fog_color กับ audio/ambient_sounds มีแค่ใน
# nether/ถ้ำ ใช้กับภูเขาไม่ได้)
MUSIC = {
    "valley": "minecraft:music.overworld.meadow",
    "montane": "minecraft:music.overworld.forest",
    "subalpine": "minecraft:music.overworld.grove",
    "alpine": "minecraft:music.overworld.snowy_slopes",
    "peak": "minecraft:music.overworld.jagged_peaks",
    "lake": "minecraft:music.overworld.meadow",
    "stream": "minecraft:music.overworld.meadow",
}

# ค่าที่ไม่มีผลกับโลกนี้ (ไม่มีสภาพอากาศ ไม่มี spawner ของวานิลลา ไม่ generate
# ใหม่) แต่ schema บังคับให้มี จึงใส่ค่ากลาง ๆ ไว้
TEMPERATURE = {
    "valley": 0.6, "montane": 0.4, "subalpine": 0.2,
    "alpine": -0.2, "peak": -0.6, "lake": 0.5, "stream": 0.5,
}


def datapack_entry(key):
    """JSON ของ biome หนึ่งตัว ตาม schema ที่อ่านจาก 26.2 จริง"""
    i = IDX[key]
    _k, _label, grass, foliage, water, sky = BIOMES[i]
    return {
        "attributes": {
            "minecraft:visual/sky_color": sky,
            "minecraft:audio/background_music": _music(MUSIC[key]),
        },
        "downfall": 0.6,
        "effects": {
            "water_color": water,
            "grass_color": grass,
            "foliage_color": foliage,
        },
        "has_precipitation": True,
        "spawn_costs": {},
        # โลกถูก generate จากสคริปต์ทั้งหมด feature/carver ของวานิลลาจะไม่ถูก
        # เรียกอยู่แล้ว และสัตว์มาจากระบบของ MMO
        "spawners": {
            "ambient": [], "axolotls": [], "creature": [], "misc": [],
            "monster": [], "underground_water_creature": [], "water_ambient": [],
            "water_creature": [],
        },
        "carvers": [],
        "features": [[] for _ in range(11)],
        "temperature": TEMPERATURE[key],
    }


def datapack_files():
    """คืน dict: พาธในdatapack -> เนื้อหา JSON"""
    return {
        f"data/{NAMESPACE}/worldgen/biome/{key}.json": datapack_entry(key)
        for key in KEYS
    }
