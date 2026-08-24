"""ชุดพื้นที่อ้างอิงสำหรับตรวจงานน้ำ/ภูมิประเทศ — ตัดสินด้วยเรขาคณิตระดับบล็อก

ทำไมต้องมี: วงป้อนกลับของโปรเจกต์นี้เคยยาวเป็นวัน (รัน --global หลายชั่วโมง ->
ผู้ใช้เดินดูในเกม -> รายงานเป็นภาษาคน -> เดาสาเหตุ) และสองครั้งที่ผ่านมาการเดา
ผิด ทั้งที่ตัวเลขทุกตัวดูดีขึ้น (`dry bank 40.8% -> 0` แต่ในเกมได้สันดินรอบ
ทะเลสาบ)  เครื่องมือนี้ย่อวงให้เหลือหลักนาที และให้ทุกการแก้มีตัวเลขก่อน/หลัง
ที่วัดจากความสูงของบล็อกจริง คู่กับภาพหน้าตัดให้คนเปิดดู

ใช้:
    python golden_patches.py list
    python golden_patches.py run                       # ทุกจุด -> golden/<tag>/
    python golden_patches.py run --only lake_mouth,cliff_bedding
    python golden_patches.py run --tag before          # ตั้งชื่อรอบเอง
    python golden_patches.py compare before after      # ตัวเลขไหนแย่ลง (exit 1)
    python golden_patches.py selftest                  # ตรวจตัวเครื่องมือเอง (exit 1)
    python golden_patches.py suggest                   # หาพิกัดใหม่จากข้อมูลจริง

**เกณฑ์ผ่านไม่ได้มาจากภาพ** ภาพ 3D ที่นี่มาจาก `render_view.py` ซึ่งเป็น
renderer แทน (เรือนยอดเป็นผิวทึบ วัสดุเป็น palette พรีวิว near-field ยังพัง)
ถึงมันจะดูดีก็ไม่ได้แปลว่าในเกมดี  เกณฑ์จึงมาจากเรขาคณิตระดับบล็อกที่นิยามเป็น
สิ่งที่ผู้เล่นทำได้จริง — ปีนขึ้นได้ไหม เดินเลียบฝั่งได้ไหม ยืนแล้วเห็นฟ้าไหม

แต่ละจุดได้:
    metrics.json          **ตัวตัดสิน** — ความสูงบล็อกจริง ไม่ใช่ mask/ความน่าจะเป็น
    top_<name>.png        แผนที่มุมบน: ส้ม=พื้นถูกยก ม่วง=ถูกขุด แดง=ม่านน้ำ
    section_<name>.png    หน้าตัดลำน้ำที่จุดแย่ที่สุด — บล็อกต่อบล็อก เชื่อได้ตรง ๆ
    eye_<name>.png        เฉพาะเมื่อใส่ `--eye` — ของประกอบให้คนดู ห้ามใช้ตัดสิน

**กติกา**: ทุกตัวใน metrics.json ต้องตอบได้ว่า "ผู้เล่นเจออะไร" เป็นบล็อก ถ้า
ตอบไม่ได้ มันจะกลายเป็นตัวเลขที่ไล่ตามแล้วของจริงแย่ลง เหมือน "dry bank 40.8%
-> 0" ที่ได้สันดินรอบทะเลสาบมาแทน
"""

import datetime
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

import config as C
import hydrology_patch_io as IO
import hydrology_shape as HS
from hydrology_shape import UNRESOLVED
from pipeline_progress import use_utf8_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(HERE, "golden")
PATCH_DIR = os.path.join(HERE, "golden_patches")

# ---- ชุดพื้นที่อ้างอิง ----
#
# ทุกจุดต้องมี "เหตุผลที่อยู่ในลิสต์" ที่อ้างหลักฐานได้ ไม่ใช่พิกัดสุ่ม เพราะ
# ชุดนี้คือสิ่งที่ตัดสินว่างานผ่านหรือไม่ผ่าน  จุดที่มาจากบั๊กจริงมีค่ามากกว่า
# จุดที่สวยอยู่แล้ว — แต่ต้องมีทั้งสองอย่าง ไม่งั้นการแก้จะทำที่สวยอยู่แล้วพัง
# โดยไม่มีอะไรฟ้อง (เกิดมาแล้วกับ `bank_taper`)
#
# `size` คือกรอบ hydrology (context) ส่วนภาพครอบเฉพาะตรงกลาง
GOLDEN_PATCHES = [
    {
        "name": "prototype_stream",
        "x": 6021, "z": 6142, "size": 384,
        "why": "แพตช์ acceptance เดิมของ WATER_REDESIGN — ลำธารไหล่เขาต่อทะเลสาบ",
    },
    {
        "name": "lake_corridor",
        "x": 6066, "z": 5811, "size": 384,
        "why": "น้ำนิ่งสองฝั่งร่องลำน้ำเคยได้ระดับ 21/22 คนละค่า",
    },
    {
        "name": "player_liked",
        "x": 6165, "z": 6068, "size": 384,
        "why": "จุดที่ผู้ใช้ยืนยันว่าสวย — อยู่ในลิสต์เพื่อกันการแก้ทำพัง",
    },
    {
        "name": "floodplain_berm",
        "x": 5970, "z": 5902, "size": 384,
        "why": "ที่ราบน้ำท่วมถึง เคยได้คันดินยาวฝั่งเดียวตามแนวแม่น้ำ",
    },
    {
        "name": "lake_mouth",
        "x": 2242, "z": 3057, "size": 384,
        "why": "ปากทะเลสาบ — ม่านน้ำ 117 เสาที่ไม่มีหน้าผารองรับสักต้น",
    },
    {
        "name": "cliff_bedding",
        "x": 5880, "z": 5554, "size": 384,
        "why": "หน้าผาชั้นหิน เคยได้ขั้นซ้ำ 11-12 บล็อกจาก bedding snap",
    },
    # สามจุดนี้มาจาก `suggest` (คัดจากข้อมูลจริง ไม่ใช่จากบั๊กที่เคยเจอ) เพื่อให้
    # ชุดครอบเคสยากที่ยังไม่มีใครรายงาน — ตัวเลขในวงเล็บคือของที่ suggest วัดได้
    {
        "name": "steep_stream",
        "x": 5792, "z": 5384, "size": 384,
        "why": "ลำน้ำชันที่สุดในแผนที่ (relief 153 ในกรอบ) — ที่ที่น้ำตกควรมีจริง",
    },
    {
        "name": "hill_junction",
        "x": 3344, "z": 480, "size": 384,
        "why": "ลำน้ำหนาแน่นที่สุดบนที่ลาด (31 sample/กรอบ) — ระดับที่จุดบรรจบ",
    },
    # สองจุดนี้มาจากการสุ่มตรวจ 10 จุดนอกชุด (2026-08-13) ซึ่งพบว่าปัญหาหุบ
    # หนักกว่าที่ชุดเดิมบอกมาก — ชุดที่ใช้ตัดสินต้องมีเคสที่แย่ที่สุดอยู่ด้วย
    # ไม่งั้นจะ "ผ่าน" ทั้งที่ของจริงยังพัง
    {
        "name": "wild_canyon",
        "x": 6448, "z": 2304, "size": 384,
        "why": "สุ่มเจอ: หุบเกินธรรมชาติ 66.9% — หนักที่สุดเท่าที่วัดได้",
    },
    {
        "name": "wild_canyon_b",
        "x": 4832, "z": 3296, "size": 384,
        "why": "สุ่มเจอ: หุบเกินธรรมชาติ 56.3%",
    },
    {
        "name": "flat_river",
        "x": 6952, "z": 3816, "size": 384,
        "why": "ลำน้ำบนที่ราบ (relief 8) — เคสคันดินและก้นน้ำแบน",
    },
]


def patch_path(spec):
    return os.path.join(
        PATCH_DIR, f"hydrology_patch_x{spec['x']}_z{spec['z']}_{spec['size']}.npz"
    )


# ทุกไฟล์ที่เปลี่ยนรูปร่างของ patch ได้ ต้องอยู่ในนี้ — ขาดไปหนึ่งไฟล์แปลว่า
# `run` จะหยิบ npz เก่ามาใช้เงียบ ๆ แล้วรายงานว่า "ไม่มีอะไรเปลี่ยน"
# (channel_sections.py เคยขาด: แก้กติกาการชนกันของหน้าตัดแล้วทุก patch ยังขึ้น
# `cached` ทั้งชุด)
SHAPE_INPUTS = (
    "hydrology_shape.py", "channel_sections.py", "config.py", "surface.py",
)


def git_revision():
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=HERE, capture_output=True, text=True,
    )
    if result.returncode:
        return "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=HERE, capture_output=True, text=True,
    )
    suffix = "+dirty" if dirty.stdout.strip() else ""
    return result.stdout.strip() + suffix


def shape_fingerprint(spec):
    """ลายนิ้วมือของ *ทุกอย่างที่เปลี่ยนผลของ patch* — โค้ด + input + พิกัด

    ถ้าไม่มีตัวนี้ `--tag after` จะหยิบ npz ที่ shape ไว้ **ก่อนแก้โค้ด** มาใช้
    ต่อ แล้ว `compare` ก็รายงานว่า "ไม่มีอะไรเปลี่ยน" ทั้งที่ยังไม่ได้วัดของใหม่
    เลย — เป็นบั๊กชนิดเดียวกับที่ `build_terrain --resume` เป็นอยู่ (ดู TODO)
    และร้ายกว่าตรงที่มันทำให้ *เชื่อผลผิด* ไม่ใช่แค่ข้ามงาน
    """
    import hashlib

    h = hashlib.sha256()
    h.update(f"{spec['x']},{spec['z']},{spec['size']}".encode())
    for name in SHAPE_INPUTS:
        path = os.path.join(HERE, name)
        with open(path, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    for name in ("terrain_y.npy", "water_sources.npz"):
        path = os.path.join(HERE, name)
        stat = os.stat(path)
        h.update(f"{name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
    return h.hexdigest()[:16]


def shape_patch(spec, force=False):
    """เรียก hydrology_shape --patch ผ่าน subprocess

    แยก process เพราะแต่ละ patch เปิด memmap ของ terrain/water_sources เต็ม
    แผนที่ การรันในโปรเซสเดียวกันหลายรอบทำให้ RAM โตสะสมโดยไม่จำเป็น
    """
    out = patch_path(spec)
    stamp = out + ".fingerprint"
    fingerprint = shape_fingerprint(spec)
    if os.path.exists(out) and not force:
        cached = None
        if os.path.exists(stamp):
            with open(stamp, encoding="utf-8") as f:
                cached = f.read().strip()
        if cached == fingerprint:
            return out, "cached"
    os.makedirs(PATCH_DIR, exist_ok=True)
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, "hydrology_shape.py"),
         "--patch", str(spec["x"]), str(spec["z"]), str(spec["size"])],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode:
        raise SystemExit(
            f"hydrology_shape ล้มเหลวที่ {spec['name']}:\n{result.stderr}"
        )
    produced = os.path.join(
        HERE, f"hydrology_patch_x{spec['x']}_z{spec['z']}_{spec['size']}.npz"
    )
    os.replace(produced, out)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(fingerprint)
    return out, "shaped"


# ---- ตัวเลขที่วัด "สิ่งที่ผู้เล่นเจอ" เป็นบล็อก ----
#
# ทำไมไม่ตัดสินจากภาพ: ภาพระดับสายตาที่นี่มาจาก `render_view.py` ซึ่งเป็น
# renderer แทน — เรือนยอดเป็นผิวทึบ วัสดุเป็น palette พรีวิว และ near-field ยัง
# พังอยู่ ถึงภาพจะ "ดูโอเค" ก็ไม่ได้แปลว่าในเกมโอเค  เกณฑ์ผ่าน/ไม่ผ่านจึงต้อง
# มาจาก **เรขาคณิตระดับบล็อก** ที่นิยามเป็นสิ่งที่ผู้เล่นทำได้จริง: ปีนได้ไหม
# มองเห็นฟ้าไหม เดินเลียบฝั่งได้ไหม  ภาพเหลือหน้าที่เดียวคือให้ *คน* เปิดดู
#
# ผู้เล่นในวานิลลาก้าวขึ้นได้ทีละ 1 บล็อก (กระโดดได้ 1.25) เกณฑ์ทุกตัวข้างล่าง
# อ้างเลขนี้ ไม่ใช่ค่าที่ตั้งขึ้นลอย ๆ
PLAYER_STEP = 1
# ผนังที่สูงกว่านี้ = ปีนไม่ได้ ต้องเดินอ้อม
PLAYER_WALL = 3
# ยืนกลางลำน้ำแล้วขอบสองฝั่งสูงกว่าผิวน้ำเกินนี้ = อยู่ในหุบ ไม่ใช่ริมลำธาร
CANYON_DEPTH = 8
# รัศมีที่ใช้หา "ขอบบน" ของหุบ — ไกลกว่าความกว้างลำน้ำที่กว้างที่สุดเล็กน้อย
CANYON_REACH = 6
# เกณฑ์ "หน้าตัด" ต้องตรงกับกฎที่ hydrology_shape ใช้จริง ไม่งั้น harness จะ
# รายงานการออกแบบว่าเป็นการละเมิด — อ่านจากที่นั่นที่เดียว ห้ามเขียนซ้ำ
SECTION_WIDTH_LIMIT = HS.MAX_SECTION_WIDTH
SECTION_SPREAD_LIMIT = HS.MAX_SECTION_SPREAD
# ขอบกรอบที่ไม่เอามานับ — ต้องกว้างกว่า CANYON_REACH และกว้างพอสำหรับ halo ของ
# hydrology เอง (GLOBAL_HALO = 24)
CORE_MARGIN = 24
# ขอบน้ำที่สูงกว่าผิวน้ำไม่เกินเท่านี้ = ชายฝั่งที่เหยียบได้
SHORE_MAX_RISE = 1
# สูงกว่าผิวน้ำตั้งแต่นี้ = ผนัง (ปีนไม่ขึ้น เดินเลียบไม่ได้)
WALL_MIN_RISE = 2


def _cardinal_pairs(shape):
    return (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    )


def shift_clamped(values, offset, axis):
    """เลื่อนอาร์เรย์โดย **ยืดขอบ** ไม่ใช่วนกลับมาอีกด้าน

    `np.roll` วนขอบ ผลคือ cell ริมกรอบได้ "ผนัง" มาจากอีกฟากของแผนที่ — วัดแล้ว
    ที่ cell ริมขวาซึ่งฝั่งขวาเปิดออกนอกกรอบ ได้ค่าหุบ 40 บล็อกจากผนังที่อยู่
    คอลัมน์ 0  เป็นแถบผิด 6 บล็อกรอบทุก patch และลำน้ำแตะขอบ patch บ่อยมาก
    """
    if offset == 0:
        return values.copy()
    out = np.empty_like(values)
    src = [slice(None)] * values.ndim
    dst = [slice(None)] * values.ndim
    edge = [slice(None)] * values.ndim
    fill = [slice(None)] * values.ndim
    if offset > 0:
        dst[axis], src[axis] = slice(offset, None), slice(None, -offset)
        fill[axis], edge[axis] = slice(None, offset), slice(0, 1)
    else:
        dst[axis], src[axis] = slice(None, offset), slice(-offset, None)
        fill[axis], edge[axis] = slice(offset, None), slice(-1, None)
    out[tuple(dst)] = values[tuple(src)]
    out[tuple(fill)] = values[tuple(edge)]
    return out


def canyon_depth(terrain, water, surface, reach=CANYON_REACH):
    """ยืนบนผิวน้ำแล้วขอบบนของสองฝั่งสูงกว่าหัวเท่าไร (บล็อก)

    ใช้ค่า *น้อยกว่า* ของสองฝั่ง เพราะถ้าฝั่งหนึ่งเปิดออก ผู้เล่นก็ยังเห็นวิว
    และเดินออกได้ — มันคือหุบก็ต่อเมื่อ **ปิดทั้งสองฝั่ง**  วัดแยกแกน x และ z
    แล้วเอาค่ามากสุด เพราะลำน้ำวางตัวได้ทั้งสองแนว
    """
    height = terrain.astype(np.int32)
    best = np.zeros(height.shape, dtype=np.int32)
    for axis in (0, 1):
        # ขอบบนของแต่ละฝั่ง = จุดสูงสุดในระยะ reach ของฝั่งนั้น
        # เขียนเป็น shift ตรง ๆ ไม่ใช่ filter ที่มี origin เพราะเคยพลาดเรื่อง
        # หน้าต่างเลื่อนผิดข้างมาแล้ว และตรงนี้ต้องเถียงกับภาพในเกมได้
        sides = []
        for direction in (1, -1):
            side = np.full(height.shape, np.iinfo(np.int32).min, dtype=np.int32)
            for offset in range(1, int(reach) + 1):
                side = np.maximum(
                    side, shift_clamped(height, direction * offset, axis)
                )
            sides.append(side)
        both = np.minimum(sides[0], sides[1]) - surface.astype(np.int32)
        best = np.maximum(best, both)
    return np.where(water, np.maximum(best, 0), 0)


def bank_climb(terrain, water):
    """เดินเลียบฝั่งตามแนวลำน้ำ ต้องปีนกี่บล็อกต่อก้าว

    ตลิ่งที่กระโดดขึ้นลงเกิน 1 บล็อกทุกไม่กี่ก้าว = เดินเลียบไม่ได้ ต้องปีน
    ซึ่งเป็นสิ่งที่ผู้เล่นรู้สึกทันทีและภาพนิ่งบอกไม่ได้
    """
    bank = np.zeros(water.shape, dtype=bool)
    for dst, src in _cardinal_pairs(water.shape):
        bank[dst] |= (~water)[dst] & water[src]
    height = terrain.astype(np.int32)
    climb = np.zeros(water.shape, dtype=np.int32)
    for dst, src in _cardinal_pairs(water.shape):
        step = np.abs(height[dst] - height[src])
        climb[dst] = np.maximum(
            climb[dst], np.where(bank[dst] & bank[src], step, 0)
        )
    return bank, climb


def core_mask(shape, margin=CORE_MARGIN):
    """cell ที่ถือว่าผลเชื่อถือได้ — ตัดขอบกรอบทิ้ง

    ขอบ patch มีสองปัญหาซ้อนกัน: hydrology เองก็ต้องการ halo (ดู GLOBAL_HALO)
    และตัววัดที่มองรอบตัว (canyon, ตลิ่ง) มองเลยขอบไม่ได้  ถ้าเก็บขอบไว้ ตัวเลข
    จะเด้งตามขนาดกรอบแทนที่จะตามคุณภาพงาน แล้ว compare ก็เชื่อไม่ได้
    """
    core = np.zeros(shape, dtype=bool)
    m = int(margin)
    if 2 * m >= min(shape):
        core[:] = True
        return core
    core[m:shape[0] - m, m:shape[1] - m] = True
    return core


def shore_profile(terrain, water, surface):
    """ขอบน้ำแต่ละจุดเป็น "ชายฝั่ง" หรือ "ผนัง"

    ลำธารที่อ่านว่าเป็นธรรมชาติต้องมีลำดับ ก้นน้ำ -> ชายฝั่งราบ -> ตลิ่งที่ลาด
    ถ้าน้ำชนผนังหินตั้งทันที มันคือรอยผ่า ไม่ใช่ร่องที่น้ำทำเอง — วัดจากผังจริง
    ทั้งแผนที่ได้ 55% ของขอบน้ำเป็นผนังตั้ง และไม่มี metric ตัวไหนเคยจับได้

    คืน (จำนวนขอบที่เป็นชายฝั่ง, จำนวนขอบที่เป็นผนัง)
    """
    dry = ~water
    shore = wall = 0
    for dst, src in _cardinal_pairs(water.shape):
        touch = np.zeros(water.shape, dtype=bool)
        touch[dst] = dry[dst] & water[src]
        rise = np.zeros(water.shape, dtype=np.int32)
        rise[dst] = terrain[dst] - surface[src]
        shore += int((touch & (rise <= SHORE_MAX_RISE)).sum())
        wall += int((touch & (rise >= WALL_MIN_RISE)).sum())
    return shore, wall


def wall_slope_ratio(terrain, base, water, reach=CANYON_REACH):
    """ผนังริมน้ำชันกว่าไหล่เขาที่มันตัดผ่านกี่เท่า

    นี่คือตัวเลขที่ตรงกับคำว่า "ไม่กลมกลืนกับภูเขา" ที่สุด: หุบจริงกว้างตาม
    ความลึก ส่วนของเราแถบดัดกว้างคงที่ ผนังจึงชันขึ้นเรื่อย ๆ ตามความลึก
    วัดที่ wild_canyon ได้ 3.6 บล็อก/บล็อก เทียบไหล่เขา 0.7 = 5 เท่า
    """
    delta = base.astype(np.int32) - terrain.astype(np.int32)
    touched = np.abs(delta) > 1
    if not touched.any():
        return 0.0
    from scipy import ndimage
    width = ndimage.distance_transform_edt(touched)
    gz, gx = np.gradient(base.astype(np.float32))
    natural = np.hypot(gz, gx)
    # วัดเฉพาะฝั่งแห้ง — การขุดก้นน้ำเป็นดีไซน์ของหน้าตัด ไม่ใช่ผนังหุบ
    # (เวอร์ชันแรกวัดที่ cell น้ำ จึงรายงานความลึกก้นแม่น้ำว่าเป็นผนัง 10 เท่า)
    sel = (~water) & (delta >= 3)
    if not sel.any():
        return 0.0
    wall = delta[sel] / np.maximum(width[sel], 1.0)
    ground = np.maximum(np.median(natural[~water]) if (~water).any() else 1.0, 0.3)
    return float(np.median(wall) / ground)


def bed_relief_share(depth, way):
    """ก้นน้ำมีรูปทรงไหม — สัดส่วน cell ที่ลึกเท่าเพื่อนบ้านทุกทิศ (= รางก้นแบน)

    ก้นน้ำที่ดีมีร่องลึก (thalweg) ส่ายไปมาและตื้นขึ้นที่ขอบ ถ้าทุก cell ลึก
    เท่ากันหมด มองจากผิวน้ำจะเป็นรางสี่เหลี่ยม ซึ่งเป็นอาการที่ยังไม่เคยวัด

    **ห้ามเรียกตัวนี้กับลำน้ำกว้าง 1 cell** — ที่นั่นมันวัดไม่ได้ ไม่ใช่วัดแล้ว
    ได้ค่าแย่  ดู `patch_metrics` ซึ่งกรองให้แล้ว
    """
    if not way.any():
        return 0.0
    flat = np.ones(way.shape, dtype=bool)
    for dst, src in _cardinal_pairs(way.shape):
        same = np.ones(way.shape, dtype=bool)
        same[dst] = ~(way[dst] & way[src]) | (depth[dst] == depth[src])
        flat &= same
    return float((flat & way).sum() / max(1, int(way.sum())))


def patch_metrics(patch, base_terrain):
    """ทุกตัวต้องนิยามได้ว่า "ผู้เล่นเจออะไร" ไม่งั้นห้ามเพิ่มเข้ามา"""
    water = np.asarray(patch["water_mask"], dtype=bool)
    surface = np.asarray(patch["surface_y"], dtype=np.int32)
    terrain = np.asarray(patch["terrain_y"], dtype=np.int32)
    way = np.asarray(patch["waterway_mask"], dtype=bool)
    base = np.asarray(base_terrain, dtype=np.int32)
    top = np.asarray(patch["waterfall_top_y"], dtype=np.int32)
    covered = np.asarray(patch["waterfall_top_y"]) != np.iinfo(np.int16).min
    delta = terrain - base
    core = core_mask(water.shape)

    dry = ~water
    # ตลิ่งแห้งที่ต่ำกว่าผิวน้ำข้าง ๆ = ขอบน้ำลอย มองจากด้านข้างเห็นเป็นผนังน้ำ
    dry_below = np.zeros(water.shape, dtype=bool)
    # ผนังตั้งริมน้ำ = "กำแพงตลิ่ง" ที่ผู้เล่นบ่นถึง
    wall = np.zeros(water.shape, dtype=bool)
    uncovered = 0
    for dst, src in _cardinal_pairs(water.shape):
        dry_below[dst] |= dry[dst] & water[src] & (terrain[dst] < surface[src])
        wall[dst] |= dry[dst] & water[src] & (terrain[dst] - surface[src] >= 3)
        drop = surface[src] - surface[dst]
        falling = water[src] & water[dst] & (drop >= 3)
        uncovered += int((core[dst] & falling & ~(
            covered[dst] & (top[dst] - surface[dst] >= drop)
        )).sum())

    # หน้าตัดที่ผิวน้ำไม่เท่ากัน = ขั้น 1 บล็อกวิ่งขนานลำน้ำ เดินเลียบฝั่งเห็นชัด
    # ---- ขั้นที่ตาเห็นว่าผิด = ขั้นที่วิ่ง *ขนาน* ลำน้ำ ----
    #
    # นิยามที่ 1 (run ตามแกนที่ไม่ราบ) ใช้กับสถาปัตยกรรมหน้าตัดไม่ได้: ลำน้ำที่
    # ไหลลงย่อมมีขั้นตามแนวไหลเป็นเรื่องปกติ และ run ตามแกนก็คร่อมหลายหน้าตัด
    #
    # นิยามที่ 2 (คู่ cell ติดกันที่ระยะถึงฝั่งเท่ากัน) เป็นบั๊กตัวเดียวกันในรูป
    # ใหม่ ไม่ใช่การแก้: cell สองตัวที่ติดกัน *ตามแนวไหล* ก็ห่างจากฝั่งเท่ากัน
    # เสมอ ทุกขั้นที่น้ำไหลลงตามปกติจึงถูกนับ  วัดแล้วพบว่า 52-100% ของที่นับได้
    # เป็นคู่ตามแนวไหล และผังสังเคราะห์ที่หน้าตัดราบทุกแถวแต่ไหลลง 1 บล็อกทุก
    # 3 แถว ให้ค่า 12 ทั้งที่ไม่มีอะไรผิดเลย (เทสต์เดิมจับไม่ได้เพราะผิวน้ำราบ
    # สนิททั้งผัง — ไม่มีเคสน้ำไหลลง)
    #
    # นิยามที่ 3 (เทียบกับ centerline ที่ใกล้ที่สุดด้วย EDT) ถูกเรื่องหลักการ
    # แต่ *จับ cell เข้าหน้าตัดผิดตัว* บนเส้นทแยง: cell ที่อยู่ข้าง centerline
    # ของตัวเองในแนวทแยง กลับประชิด centerline ของสถานีถัดไปมากกว่า วัดที่
    # hill_junction ได้ 82 cell ที่ 'ผิด' ทั้งที่ทุกตัวถือระดับของหน้าตัดที่
    # ประชิดตัวเองจริง (0 ตัวที่ไม่ตรงกับ centerline ข้างเคียงเลย)
    #
    # นิยามนี้ไม่เดา: `section_id` มาจาก `stamp_sections` โดยตรง = cell นี้เป็น
    # ของหน้าตัดไหน  กติกาที่ตรวจคือ **cell ที่อยู่หน้าตัดเดียวกันต้องมีผิวน้ำ
    # เท่ากัน** ซึ่งตรงกับ "ยืนกลางน้ำแล้วสองฝั่งไม่เท่ากัน" พอดี และภูมิคุ้มกัน
    # การไหลลงโดยสิ้นเชิง (คนละหน้าตัดไม่เคยถูกเทียบกัน)
    #
    # ไม่ใช่การถามซ้ำสิ่งที่เพิ่งเขียน: หลัง stamp ยังมีอีกหลายด่านที่แก้ผิวน้ำ
    # (build_step_weirs, mouth clamp, limit_masked_steps, seal, ผสมทะเลสาบ)
    # ด่านพวกนั้นคือสิ่งที่ตัวเลขนี้จับ
    fields = getattr(patch, "files", patch)
    if "section_id" not in fields:
        # ห้ามคืน 0 เพราะ 0 อ่านเป็น "ผ่าน" ทั้งที่ยังไม่ได้วัดอะไรเลย
        # (ผลิตภัณฑ์ระดับโลกใน hydrology_global/ ยังไม่มี section_id.npy —
        # `audit_global.py` จะชนตรงนี้จนกว่าจะเพิ่มเข้าไปในชุดที่เขียนลงดิสก์)
        raise ValueError(
            "patch ไม่มี section_id — unflat_cross_runs วัดไม่ได้ "
            "(ต้องเป็นผังจาก stamp_sections หรือประกาศ section_id เอง)"
        )
    section_id = np.asarray(patch["section_id"], dtype=np.int64)
    owned = way & core & (section_id > 0)
    unflat = 0
    if owned.any():
        ids = section_id[owned]
        levels = surface[owned]
        # ระดับอ้างอิงของหน้าตัด = ค่าที่พบมากที่สุดในหน้าตัดนั้น (ไม่ใช่ค่าแรก
        # ที่เจอ) — cell ส่วนน้อยที่หลุดออกจากพวกคือตัวที่ผู้เล่นเห็นเป็นขั้น
        order = np.lexsort((levels, ids))
        ids, levels = ids[order], levels[order]
        start = np.flatnonzero(
            np.concatenate(([True], (ids[1:] != ids[:-1]) | (levels[1:] != levels[:-1])))
        )
        run_len = np.diff(np.concatenate((start, [ids.size])))
        run_id = ids[start]
        # ต่อหนึ่ง section: run ที่ยาวที่สุดคือระดับอ้างอิง ที่เหลือคือคนหลุด
        head = np.flatnonzero(
            np.concatenate(([True], run_id[1:] != run_id[:-1]))
        )
        bounds = np.concatenate((head, [run_id.size]))
        for a, b in zip(bounds[:-1], bounds[1:]):
            sizes = run_len[a:b]
            unflat += int(sizes.sum() - sizes.max())

    # ความยาวแอ่ง — แอ่งสั้นทั้งสาย = น้ำลดทีละบล็อกแทบทุกก้าว (อาการที่ 2)
    pools = []
    wet_core = way & core
    for row in range(way.shape[0]):
        idx = np.flatnonzero(wet_core[row])
        if idx.size < 2:
            continue
        values = surface[row, idx]
        cut = np.flatnonzero((np.diff(idx) != 1) | (np.diff(values) != 0))
        sizes = np.diff(np.concatenate(([-1], cut, [idx.size - 1])))
        pools.extend(int(s) for s in sizes if s > 0)
    pools = np.asarray(pools) if pools else np.zeros(0, dtype=int)

    # ---- สิ่งที่ผู้เล่นเจอ ----
    # ยืนอยู่ในน้ำแล้วสองฝั่งสูงกว่าหัวเท่าไร = อยู่ในหุบหรือริมลำธาร
    canyon = canyon_depth(terrain, water, surface)
    wet_canyon = canyon[water & core]
    # เดินเลียบฝั่งแล้วต้องปีนเกินก้าวปกติกี่จุด
    bank, climb = bank_climb(terrain, water)
    bank_steps = climb[bank & core]

    # ---- ตัวควบคุม: ผังเดิมล้วน ๆ ให้ค่าเท่าไร ----
    #
    # จำเป็น ไม่ใช่ของแถม  ภูมิประเทศแอลป์ชันอยู่แล้ว ถ้าไม่มีตัวเทียบก็แยกไม่ออก
    # ว่า "หุบลึก 37 บล็อก" คือเราขุดเองหรือธรรมชาติเป็นแบบนั้น — วัดแล้วพบว่า
    # DEM ดิบให้หุบแค่ 0-2 บล็อก (เราขุดเองทั้งหมด) แต่ตลิ่งที่ปีนยากมี 5-12%
    # อยู่แล้วตามธรรมชาติ  สองเรื่องนี้ต้องไล่คนละแบบ และถ้าไม่มีตัวควบคุมก็จะ
    # ไล่ตัวเลขลงไปจนต่ำกว่าธรรมชาติ ซึ่งแปลว่าไถภูเขาทิ้ง
    dem_canyon = canyon_depth(base, water, base)[water & core]
    dem_bank, dem_climb = bank_climb(base, water)
    dem_steps = dem_climb[dem_bank & core]
    # เสาน้ำ: ความสูงของน้ำในคอลัมน์เดียว — สูงผิดปกติ = เสาน้ำยืนโดด
    #
    # ต้องอ่านจาก `depth` ไม่ใช่ `surface - terrain` เพราะ shape_waterway_patch
    # ตั้ง `terrain[way] = surface[way]` ไว้แล้ว (terrain ของ cell ที่เป็นน้ำคือ
    # *ผิวน้ำ* ไม่ใช่ก้นน้ำ) สูตรผิดให้ค่า 1 ทุก cell ทั้งแผนที่ ซึ่ง "ดูปกติ"
    # จนไม่มีใครสงสัย
    # วัดเฉพาะลำน้ำ ไม่รวมทะเลสาบ — ทะเลสาบลึก 20 บล็อกเป็นเรื่องปกติ แต่ลำธาร
    # ลึก 20 บล็อกคือเสาน้ำที่ผู้เล่นตกลงไปแล้วปีนขึ้นไม่ได้
    depth = np.asarray(patch["depth"], dtype=np.int32)
    column = np.where(way & core, np.maximum(depth, 1), 0)

    # ความกว้างของหน้าตัด = จำนวน cell ที่ใช้ section_id เดียวกัน
    wide_bed = water & core
    narrow_share = 0.0
    if owned.any():
        ids, inverse, counts = np.unique(
            section_id[owned], return_inverse=True, return_counts=True
        )
        width = np.zeros(water.shape, dtype=np.int32)
        width[owned] = counts[inverse]
        narrow = owned & (width == 1)
        narrow_share = float(narrow.sum() / max(1, int(owned.sum())))
        wide_bed = (water & core) & ~narrow

    delta = np.where(core, delta, 0)
    depth = np.asarray(patch["depth"], dtype=np.int32)
    lake = (
        np.asarray(patch["standing_water_mask"], dtype=bool)
        if "standing_water_mask" in getattr(patch, "files", patch)
        else np.zeros(water.shape, dtype=bool)
    )
    shore, wall_edges = shore_profile(terrain, water, surface)
    edges = max(1, shore + wall_edges)
    lifted = delta > 0
    metrics = {
        "water_cells": int((water & core).sum()),
        "waterway_cells": int((way & core).sum()),
        # --- ต้องเป็นศูนย์ ---
        "dry_bank_below_water": int((dry_below & core).sum()),
        "uncovered_drop": int(uncovered),
        "unflat_cross_runs": int(unflat),
        # --- ผู้เล่นเจออะไร (บล็อก) ---
        # ยืนในลำน้ำแล้วมองไม่เห็นอะไรนอกจากผนังสองฝั่ง
        "canyon_depth_p50": float(np.median(wet_canyon)) if wet_canyon.size else 0.0,
        "canyon_depth_p90": (
            float(np.percentile(wet_canyon, 90)) if wet_canyon.size else 0.0
        ),
        "canyon_share": (
            float((wet_canyon >= CANYON_DEPTH).mean()) if wet_canyon.size else 0.0
        ),
        # เดินเลียบฝั่งไม่ได้ ต้องปีน
        "bank_unwalkable_share": (
            float((bank_steps > PLAYER_STEP).mean()) if bank_steps.size else 0.0
        ),
        "bank_wall_cells": int((wall & core).sum()),
        # --- หน้าตัดอ่านเป็นธรรมชาติไหม ---
        "shore_share": float(shore / edges),
        "bare_wall_share": float(wall_edges / edges),
        "wall_slope_ratio": wall_slope_ratio(terrain, base, water),
        # ต้องวัด **ผืนน้ำทั้งหมด** ไม่ใช่เฉพาะลำน้ำ — ทะเลสาบคือ 61% ของน้ำทั้ง
        # แผนที่ ตัวเลขเดิมวัดแค่ `way` ที่ lake_mouth จึงรายงาน 83% จาก cell
        # เพียง 130 ตัว ขณะที่ก้นทะเลสาบ 7,517 cell จริง ๆ แบน 27%
        # ---- ก้นน้ำเป็นรางสี่เหลี่ยมไหม ----
        #
        # วัดเฉพาะหน้าตัดที่ **กว้างตั้งแต่ 2 cell ขึ้นไป** เพราะที่กว้าง 1 cell
        # ตัวเลขนี้วัดอะไรไม่ได้เลยโดยเรขาคณิต: cell เดียวไม่มีเพื่อนบ้านด้านข้าง
        # ให้ต่าง เหลือแต่เพื่อนบ้านตามแนวไหล ซึ่งในแอ่งจริง ๆ ก็ต้องลึกเท่ากัน
        # อยู่แล้ว  สร้างก้นน้ำ "ตามตำรา" (ลำดับแอ่ง-แก่งระยะ 5-7 เท่าของความกว้าง)
        # มาวัดได้ 0.34 ที่กว้าง 1 cell และ 0.00 ที่กว้าง 3 cell — ตัวเลขเดียวกัน
        # จึงหมายถึงคนละเรื่องตามความกว้าง และเป้า <=0.25 เป็นไปไม่ได้สำหรับ
        # แผนที่ที่ลำน้ำส่วนใหญ่กว้าง 1 cell (ซึ่งถูกต้องแล้ว: 1 cell = 4 m
        # กว้างกว่าลำธารแอลป์จริงที่ 1-3 m ด้วยซ้ำ)
        #
        # ที่กว้าง 1 cell ให้ดู `narrow_share` แทน แล้วตัดสินจากความกว้างของ
        # ลำน้ำว่าตรงกับของจริงไหม ไม่ใช่จากรูปทรงก้นที่ไม่มีที่ให้อยู่
        "bed_flat_share": bed_relief_share(depth, wide_bed),
        "narrow_share": narrow_share,
        # ผังสังเคราะห์ในเทสต์ไม่มี standing_water_mask — ถือว่าไม่มีทะเลสาบ
        "bed_flat_lake": bed_relief_share(depth, lake & core),
        "bank_climb_max": int(bank_steps.max()) if bank_steps.size else 0,
        # --- เทียบกับผังเดิม: ส่วนที่ "เราทำเอง" คือส่วนที่ต้องไล่ ---
        "canyon_share_dem": (
            float((dem_canyon >= CANYON_DEPTH).mean()) if dem_canyon.size else 0.0
        ),
        "canyon_share_excess": (
            float((wet_canyon >= CANYON_DEPTH).mean()
                  - (dem_canyon >= CANYON_DEPTH).mean())
            if wet_canyon.size and dem_canyon.size else 0.0
        ),
        "bank_unwalkable_share_dem": (
            float((dem_steps > PLAYER_STEP).mean()) if dem_steps.size else 0.0
        ),
        "bank_unwalkable_excess": (
            float((bank_steps > PLAYER_STEP).mean()
                  - (dem_steps > PLAYER_STEP).mean())
            if bank_steps.size and dem_steps.size else 0.0
        ),
        # เสาน้ำที่สูงเกินความลึกปกติของลำน้ำ
        "water_column_max": int(column.max()) if column.size else 0,
        # --- ภูมิประเทศถูกดัดไปเท่าไรจาก DEM ---
        "terrain_lift_max": int(delta.max()) if delta.size else 0,
        "terrain_cut_max": int(-delta.min()) if delta.size else 0,
        "terrain_lifted_cells": int(lifted.sum()),
        "terrain_changed_cells": int((delta != 0).sum()),
        # --- อยากให้มาก (แอ่งยาว = ลำธารเป็นขั้นบันได ไม่ใช่ลาดทีละบล็อก) ---
        "pool_median": float(np.median(pools)) if pools.size else 0.0,
        "tiny_pool_share": (
            float((pools <= 2).mean()) if pools.size else 0.0
        ),
    }
    audit_metrics(metrics, patch, core)
    return metrics


def audit_metrics(metrics, patch, core):
    """ตรวจว่าตัวเลขที่เพิ่งคำนวณ **เป็นไปได้** ก่อนปล่อยออกไป

    บั๊กในไฟล์นี้ทุกตัวที่รอดสายตามาได้ มีลักษณะเดียวกันหมด: มันให้ค่าที่
    *ดูสมเหตุสมผล* (`water_column = 1` ทุก cell, `cached` ทุกจุด) จึงไม่มีอะไร
    ขัดแย้งให้สังเกต  ด่านนี้จึงไม่ได้ตรวจว่า "ค่าถูกไหม" — ตรวจว่า **ค่ามันขัด
    กับข้อเท็จจริงที่รู้อยู่แล้วหรือเปล่า** ซึ่งเป็นสิ่งเดียวที่ตรวจได้โดยไม่ต้อง
    รู้คำตอบล่วงหน้า

    ราคาถูกมากเมื่อเทียบกับการที่ตัวเลขผิดหลุดไปเป็นฐานของการตัดสินใจทั้งรอบ
    """
    water = np.asarray(patch["water_mask"], dtype=bool)
    way = np.asarray(patch["waterway_mask"], dtype=bool)
    depth = np.asarray(patch["depth"], dtype=np.int32)
    cells = int(core.sum())

    problems = []
    for key, value in metrics.items():
        if not np.isfinite(value):
            problems.append(f"{key} ไม่ใช่ตัวเลข ({value})")
        if value < 0 and not key.endswith("_excess"):
            problems.append(f"{key} ติดลบ ({value})")
    for key in ("water_cells", "waterway_cells", "dry_bank_below_water",
                "bank_wall_cells", "terrain_lifted_cells",
                "terrain_changed_cells"):
        if metrics[key] > cells:
            problems.append(f"{key} = {metrics[key]} เกินจำนวน cell ที่นับ ({cells})")
    for key in ("canyon_share_excess", "bank_unwalkable_excess"):
        if not -1.0 <= metrics[key] <= 1.0:
            problems.append(f"{key} = {metrics[key]} ไม่ใช่ผลต่างของสัดส่วน")
    for key in ("canyon_share", "bank_unwalkable_share", "tiny_pool_share",
                "canyon_share_dem", "bank_unwalkable_share_dem"):
        if not 0.0 <= metrics[key] <= 1.0:
            problems.append(f"{key} = {metrics[key]} ไม่ใช่สัดส่วน")
    if metrics["canyon_depth_p90"] + 1e-9 < metrics["canyon_depth_p50"]:
        problems.append("p90 ของหุบต่ำกว่า p50")

    # ตรวจไขว้กับข้อมูลอีกชุดที่คำนวณคนละทาง — จับสูตรผิดที่ให้ค่าดูปกติได้
    if metrics["waterway_cells"] and int((way & core).sum()):
        expected = int(np.maximum(depth[way & core], 1).max())
        if metrics["water_column_max"] != expected:
            problems.append(
                f"water_column_max = {metrics['water_column_max']} "
                f"แต่ depth บอกว่า {expected}"
            )
    if metrics["waterway_cells"] > metrics["water_cells"]:
        problems.append("ลำน้ำมากกว่าผืนน้ำทั้งหมด")
    if not water.any() and metrics["canyon_depth_p50"]:
        problems.append("ไม่มีน้ำเลยแต่รายงานความลึกหุบ")

    if problems:
        raise AssertionError(
            "ตัวเลขขัดกับข้อเท็จจริงของ patch:\n  - " + "\n  - ".join(problems)
        )


# ตัวเลขที่ "มากขึ้น = แย่ลง" — ใช้ตอน compare
WORSE_WHEN_UP = {
    "dry_bank_below_water", "uncovered_drop", "unflat_cross_runs",
    "bank_wall_cells", "bank_unwalkable_share", "bank_climb_max",
    "canyon_depth_p50", "canyon_depth_p90", "canyon_share",
    "water_column_max", "terrain_lift_max", "terrain_cut_max",
    "terrain_lifted_cells", "tiny_pool_share",
    "canyon_share_excess", "bank_unwalkable_excess",
    "bare_wall_share", "wall_slope_ratio", "bed_flat_share",
    "bed_flat_lake",
}
# ตัวเลขที่เป็น "บริบท" ไม่ใช่คะแนน — เปลี่ยนไปเฉย ๆ ไม่ใช่ดีหรือแย่
NEUTRAL = {
    "water_cells", "waterway_cells", "terrain_changed_cells",
    # บริบท ไม่ใช่คะแนน: ลำน้ำแคบไม่ใช่ข้อบกพร่อง แต่บอกว่า bed_flat_share
    # วัดจาก cell ส่วนไหนของผืนน้ำ
    "narrow_share",
    "canyon_share_dem", "bank_unwalkable_share_dem",
}
# ขยับน้อยกว่านี้ถือว่าเป็นเสียงรบกวน ไม่ใช่การถอยหลัง
SHARE_NOISE = 0.01      # 1 percentage point
COUNT_NOISE = 0.05      # 5% ของค่าเดิม
MUST_BE_ZERO = ("dry_bank_below_water", "uncovered_drop", "unflat_cross_runs")


# ---- ภาพมุมบน ----

def hillshade(height):
    dz, dx = np.gradient(height.astype(np.float32))
    shade = 0.62 + 0.3 * (dx * 0.7 + dz * 0.7) / 3.0
    return np.clip(shade, 0.25, 1.25)


def top_down_image(patch, base_terrain, scale=3):
    """มุมบนที่บอก *สาเหตุ* ไม่ใช่แค่ว่ามีน้ำตรงไหน

    ชั้นสี: เงาภูมิประเทศที่ขึ้นรูปแล้ว + น้ำ + พื้นที่ที่ถูกยก (ส้ม) / ถูกขุด
    (ม่วง) + ม่านน้ำ (แดง)  การยก/ขุดคือสิ่งที่ทำให้เกิด "สันดิน" กับ "ถนนตัด
    ผ่านภูเขา" ซึ่งมองจากภาพน้ำอย่างเดียวไม่เห็น
    """
    terrain = np.asarray(patch["terrain_y"], dtype=np.int32)
    water = np.asarray(patch["water_mask"], dtype=bool)
    delta = terrain - np.asarray(base_terrain, dtype=np.int32)
    curtain = np.asarray(patch["waterfall_top_y"]) != np.iinfo(np.int16).min

    shade = hillshade(terrain)[..., None]
    rgb = np.full(terrain.shape + (3,), 148.0, dtype=np.float32) * shade
    lift = np.clip(delta, 0, 6)[..., None] / 6.0
    cut = np.clip(-delta, 0, 6)[..., None] / 6.0
    rgb = rgb * (1 - lift) + np.array([236, 150, 44], np.float32) * lift
    rgb = rgb * (1 - cut) + np.array([132, 92, 196], np.float32) * cut
    rgb[water] = np.array([56, 118, 172], np.float32)
    rgb[curtain] = np.array([214, 62, 62], np.float32)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    return img.resize(
        (img.width * scale, img.height * scale), Image.NEAREST
    )


# ---- หน้าตัดลำน้ำ: หลักฐานที่ไม่ต้องเชื่อ renderer ----

def worst_cell(patch, key="canyon"):
    """จุดที่ artifact แรงที่สุดในกรอบ — ที่ที่ต้องเอาไปดู ไม่ใช่จุดที่น้ำเยอะสุด"""
    water = np.asarray(patch["water_mask"], dtype=bool)
    if not water.any():
        return None
    terrain = np.asarray(patch["terrain_y"], dtype=np.int32)
    surface = np.asarray(patch["surface_y"], dtype=np.int32)
    if key == "canyon":
        score = canyon_depth(terrain, water, surface)
    else:
        raise ValueError(key)
    return tuple(int(v) for v in np.unravel_index(int(np.argmax(score)), score.shape))


def cross_sections(patch, count=5, half_width=26, spacing=10):
    """หน้าตัดขวางลำน้ำรอบจุดที่แย่ที่สุด — คืน list ของ (แกน, ดัชนี, โปรไฟล์)

    ทำไมต้องมีทั้งที่มีภาพ 3D อยู่แล้ว: ภาพ 3D มาจาก renderer แทนที่ใช้ palette
    พรีวิวและเรือนยอดปลอม จะเถียงว่า "ในเกมจริงเป็นแบบนี้ไหม" ไม่ได้  ส่วนหน้าตัด
    คือความสูงของบล็อกจริงต่อบล็อก อ่านตรงจาก product เดียวกับที่ build เขียนลงโลก
    ถ้าหน้าตัดบอกว่าน้ำอยู่ก้นร่องลึก 40 บล็อก ในเกมก็ลึก 40 บล็อก ไม่มีทางเป็นอย่างอื่น
    """
    water = np.asarray(patch["water_mask"], dtype=bool)
    terrain = np.asarray(patch["terrain_y"], dtype=np.int32)
    surface = np.asarray(patch["surface_y"], dtype=np.int32)
    depth = np.asarray(patch["depth"], dtype=np.int32)
    # ก้นน้ำจริง — `terrain` ของ cell ที่เป็นน้ำถูกตั้งเป็นผิวน้ำไว้แล้ว
    bed = np.where(water, surface - np.maximum(depth, 1), terrain)
    spot = worst_cell(patch)
    if spot is None:
        return []
    z, x = spot

    # หน้าตัดคือแกนที่ผืนน้ำ *แคบกว่า* — อีกแกนคือแนวที่ลำน้ำวิ่งไป
    def run_length(axis):
        line = water[z] if axis == 1 else water[:, x]
        at = x if axis == 1 else z
        length = 1
        for direction in (1, -1):
            i = at + direction
            while 0 <= i < line.size and line[i]:
                length += 1
                i += direction
        return length

    axis = 1 if run_length(1) <= run_length(0) else 0
    sections = []
    for k in range(count):
        offset = (k - count // 2) * spacing
        if axis == 1:
            row = z + offset
            if not (0 <= row < water.shape[0]):
                continue
            lo, hi = max(0, x - half_width), min(water.shape[1], x + half_width)
            sections.append((
                f"z={row}", bed[row, lo:hi], surface[row, lo:hi],
                water[row, lo:hi],
            ))
        else:
            col = x + offset
            if not (0 <= col < water.shape[1]):
                continue
            lo, hi = max(0, z - half_width), min(water.shape[0], z + half_width)
            sections.append((
                f"x={col}", bed[lo:hi, col], surface[lo:hi, col],
                water[lo:hi, col],
            ))
    return sections


def cross_section_image(patch, scale=4, pad=6):
    """วาดหน้าตัดเป็นบล็อกจริง — หิน = เทา, น้ำ = ฟ้า, หนึ่งพิกเซลบล็อก = scale"""
    sections = cross_sections(patch)
    if not sections:
        return None
    lows, highs = [], []
    for _name, bed, surface, water in sections:
        lows.append(int(bed.min()))
        highs.append(int(max(
            bed.max(), surface[water].max() if water.any() else 0
        )))
    low, high = min(lows) - pad, max(highs) + pad
    rows = high - low + 1
    width = max(len(s[1]) for s in sections)

    tiles = []
    for name, bed, surface, water in sections:
        canvas = np.full((rows, width, 3), 26, dtype=np.uint8)
        for i in range(len(bed)):
            ground = int(np.clip(bed[i] - low, 0, rows - 1))
            canvas[rows - 1 - ground:, i] = (108, 100, 92)
            if water[i]:
                topy = int(np.clip(surface[i] - low, 0, rows - 1))
                canvas[rows - 1 - topy:rows - 1 - ground, i] = (56, 118, 172)
        img = Image.fromarray(canvas).resize(
            (width * scale, rows * scale), Image.NEAREST
        )
        tiles.append(label(img, f"หน้าตัด {name}"))
    sheet = Image.new(
        "RGB",
        (max(t.width for t in tiles), sum(t.height + 2 for t in tiles)),
        (18, 18, 20),
    )
    y = 0
    for tile in tiles:
        sheet.paste(tile, (0, y))
        y += tile.height + 2
    return sheet


# ---- ภาพระดับสายตา (ประกอบ ไม่ใช่เกณฑ์ผ่าน) ----

def choose_camera(patch, spec):
    """เลือกจุดยืนและทิศมองอัตโนมัติ — ต้องเห็นน้ำ และห้ามยืนจมอยู่ในหลุม

    ยืนบนฝั่งที่สูงกว่าน้ำ *พอประมาณ* แล้วมองกลับมา เพราะ artifact ที่ตามหา
    (สันดิน ผนังตลิ่ง เสาน้ำ) อ่านได้จากด้านข้างเท่านั้น

    "สูงที่สุด" ใช้ไม่ได้ — ครั้งแรกที่ลองได้กล้องไปยืนบนสันเขาแล้วเห็นแต่ท้องฟ้า
    เล็งที่ ~12 บล็อกเหนือผิวน้ำ และคำนวณ pitch ให้ก้มลงหาน้ำจริง ๆ
    """
    water = np.asarray(patch["water_mask"], dtype=bool)
    terrain = np.asarray(patch["terrain_y"], dtype=np.int32)
    surface = np.asarray(patch["surface_y"], dtype=np.int32)
    x0, _x1, z0, _z1 = map(int, patch["bounds"])
    if not water.any():
        return spec["x"], spec["z"], 0.0, -6.0
    # เป้าคือ *ย่านที่น้ำหนาแน่นที่สุด* ไม่ใช่ centroid ของน้ำทั้งหมด
    #
    # ในกรอบที่มีลำธารกระจายหลายสาย centroid ตกลงบนสันเขาที่ไม่มีน้ำเลย แล้ว
    # กล้องก็ไปยืนหันหน้าเข้าเนิน (เกิดมาแล้วกับ hill_junction)
    from scipy import ndimage
    span = max(3, min(33, (min(water.shape) // 2) | 1))
    density = ndimage.uniform_filter(water.astype(np.float32), size=span)
    # เป้าต้องเป็น cell ที่เป็นน้ำจริง ไม่ใช่จุดกึ่งกลางของย่าน — ย่านที่หนาแน่น
    # ที่สุดของลำน้ำคดเคี้ยวมีจุดกึ่งกลางอยู่บนบกได้
    density[~water] = -1.0
    tz_i, tx_i = np.unravel_index(int(np.argmax(density)), density.shape)
    tx, tz = float(tx_i) + x0, float(tz_i) + z0
    window = np.s_[max(0, tz_i - 24):tz_i + 24, max(0, tx_i - 24):tx_i + 24]
    local, local_wet = surface[window], water[window]
    target_y = float(np.median(
        local[local_wet] if local_wet.any() else surface[water]
    ))

    def sees_target(cx, cz, eye_y):
        """ภูมิประเทศระหว่างกล้องกับน้ำต้องไม่บังน้ำ

        จำเป็นจริง ๆ: ครั้งแรกได้ภาพที่กล้องหันหน้าเข้าเนินแล้วเห็นแต่หิน ซึ่ง
        "ดูเหมือนภาพ" แต่ตรวจอะไรไม่ได้เลย
        """
        steps = int(max(abs(cx - tx), abs(cz - tz)))
        if steps < 2:
            return False
        for k in range(1, steps):
            f = k / steps
            ix = int(round(cx + (tx - cx) * f)) - x0
            iz = int(round(cz + (tz - cz) * f)) - z0
            if not (0 <= ix < water.shape[1] and 0 <= iz < water.shape[0]):
                return False
            line = eye_y + (target_y - eye_y) * f
            if float(terrain[iz, ix]) > line + 1.5:
                return False
        return True

    # ระยะยืนต้องย่อตามขนาดกรอบ ไม่งั้นบนกรอบเล็กไม่มีผู้สมัครสักตัวที่อยู่ในกรอบ
    extent = min(water.shape)
    reaches = sorted({
        max(8, min(int(extent * fraction), 170))
        for fraction in (0.12, 0.22, 0.32, 0.45)
    })
    best = fallback = None
    for reach in reaches:
        for angle in np.arange(0, 360, 30.0):
            rad = np.radians(angle)
            cx = int(round(tx + np.sin(rad) * reach))
            cz = int(round(tz + np.cos(rad) * reach))
            ix, iz = cx - x0, cz - z0
            if not (0 <= ix < water.shape[1] and 0 <= iz < water.shape[0]):
                continue
            if water[iz, ix]:
                continue
            above = float(terrain[iz, ix]) - target_y
            score = -abs(above - 12.0)          # อยากได้สูงกว่าน้ำราว 12 บล็อก
            if fallback is None or score > fallback[0]:
                fallback = (score, cx, cz, above, reach)
            if not sees_target(cx, cz, float(terrain[iz, ix]) + 6.0):
                continue
            if best is None or score > best[0]:
                best = (score, cx, cz, above, reach)
    best = best or fallback
    if best is None:
        return spec["x"], spec["z"], 0.0, -6.0
    _score, cx, cz, above, reach = best
    yaw = float(np.degrees(np.arctan2(tx - cx, tz - cz)))
    eye = 6.0
    pitch = float(np.clip(
        np.degrees(np.arctan2(-(above + eye), reach)), -30.0, 2.0
    ))
    return cx, cz, yaw, pitch


def eye_level_image(patch, spec, size=(960, 540)):
    import render_view as V

    cx, cz, yaw, pitch = choose_camera(patch, spec)
    scene = V.build_scene(
        cx, cz, radius=900, step=3, hydrology_patch=patch,
    )
    frame = V.render(
        scene, cx, cz, yaw, pitch, 70.0, size[0], size[1], 880.0,
        eye_offset=6.0,
    )
    return Image.fromarray(frame), (cx, cz, yaw)


# ---- รอบการรัน ----

def label(img, text):
    out = Image.new("RGB", (img.width, img.height + 18), (24, 24, 28))
    out.paste(img, (0, 18))
    ImageDraw.Draw(out).text((4, 4), text, fill=(236, 236, 236))
    return out


SHEET_ROW_HEIGHT = 420


def build_sheet(out_dir, names):
    """ภาพเดียวที่ดูจบใน 2 นาที — ประกอบจาก **ไฟล์ที่มีอยู่จริงในโฟลเดอร์**

    ต้องอ่านจากดิสก์ ไม่ใช่จากสิ่งที่เพิ่งเรนเดอร์ในรอบนี้ ไม่งั้น `--only`
    จะเขียนทับ sheet ให้เหลือจุดเดียว (บั๊กเดียวกับที่ metrics.json เคยเป็น)
    แล้วคนที่เปิดดูจะเข้าใจว่าชุดทดสอบมีอยู่แค่นั้น
    """
    rows = []
    for name in names:
        tiles = []
        for kind, caption in (("top", "แผนที่: ส้ม=ยก ม่วง=ขุด"),
                              ("section", "หน้าตัดที่จุดแย่ที่สุด")):
            path = os.path.join(out_dir, f"{kind}_{name}.png")
            if not os.path.exists(path):
                continue
            img = Image.open(path)
            scale = SHEET_ROW_HEIGHT / img.height
            img = img.resize(
                (max(1, int(img.width * scale)), SHEET_ROW_HEIGHT),
                Image.LANCZOS if scale < 1 else Image.NEAREST,
            )
            tiles.append(label(img, f"{name} — {caption}"))
        if tiles:
            rows.append(tiles)
    if not rows:
        return
    width = max(sum(t.width for t in row) for row in rows)
    height = sum(max(t.height for t in row) for row in rows)
    sheet = Image.new("RGB", (width, height), (18, 18, 20))
    y = 0
    for row in rows:
        x = 0
        for tile in row:
            sheet.paste(tile, (x, y))
            x += tile.width
        y += max(t.height for t in row)
    sheet.save(os.path.join(out_dir, "sheet.png"))


def run(names=None, tag="latest", force=False, with_eye=False):
    specs = [s for s in GOLDEN_PATCHES if not names or s["name"] in names]
    if not specs:
        raise SystemExit("ไม่มีจุดที่ตรงกับ --only")
    out_dir = os.path.join(GOLDEN_DIR, tag)
    os.makedirs(out_dir, exist_ok=True)
    terrain = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")

    report, fingerprints = {}, {}
    for spec in specs:
        path, how = shape_patch(spec, force=force)
        fingerprints[spec["name"]] = shape_fingerprint(spec)
        patch = IO.load_hydrology_patch(path)
        try:
            x0, x1, z0, z1 = map(int, patch["bounds"])
            base = np.asarray(terrain[z0:z1, x0:x1])
            metrics = patch_metrics(patch, base)
            report[spec["name"]] = metrics

            top_down_image(patch, base).save(
                os.path.join(out_dir, f"top_{spec['name']}.png")
            )
            section = cross_section_image(patch)
            if section is not None:
                section.save(
                    os.path.join(out_dir, f"section_{spec['name']}.png")
                )

            if with_eye:
                eye, (cx, cz, yaw) = eye_level_image(patch, spec)
                eye.save(os.path.join(out_dir, f"eye_{spec['name']}.png"))
        finally:
            patch.close()

        broken = [k for k in MUST_BE_ZERO if metrics[k]]
        flag = "  <-- ละเมิด: " + ", ".join(broken) if broken else ""
        print(
            f"{spec['name']:<18} {how:<7} "
            f"หุบ p50/p90 {metrics['canyon_depth_p50']:>4.0f}/"
            f"{metrics['canyon_depth_p90']:<4.0f} "
            f"({metrics['canyon_share'] * 100:>4.0f}% ของน้ำอยู่ในหุบ) | "
            f"ตลิ่งปีน {metrics['bank_unwalkable_share'] * 100:>4.0f}% "
            f"(สูงสุด {metrics['bank_climb_max']:>3}) | "
            f"เสาน้ำ {metrics['water_column_max']:>3} | "
            f"ช่องว่าง {metrics['uncovered_drop']:>4,}{flag}"
        )

    # รวมกับของเดิม ไม่ใช่เขียนทับ — `--only` ไม่ควรลบผลของจุดอื่นทิ้ง ไม่งั้น
    # `compare` จะเทียบได้แค่จุดที่เพิ่งรัน แล้วรายงานว่า "ไม่มีอะไรแย่ลง"
    metrics_path = os.path.join(out_dir, "metrics.json")
    merged = {}
    if os.path.exists(metrics_path):
        with open(metrics_path, encoding="utf-8") as f:
            merged = json.load(f)
    merged.update(report)
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    # provenance แยกไฟล์ ไม่ปนกับตัวเลข — `compare` ต้องบอกได้ว่าสองรอบมาจาก
    # โค้ดคนละชุดจริงหรือเปล่า ไม่ใช่ให้เดาจากชื่อ tag
    provenance_path = os.path.join(out_dir, "run.json")
    provenance = {}
    if os.path.exists(provenance_path):
        with open(provenance_path, encoding="utf-8") as f:
            provenance = json.load(f)
    provenance.update({
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "git": git_revision(),
        "fingerprints": {
            **provenance.get("fingerprints", {}), **fingerprints,
        },
    })
    with open(provenance_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2, ensure_ascii=False)
    for line in flag_degenerate(report):
        print(f"  !! น่าสงสัย: {line} — ตัวเลขที่ไม่ต่างกันเลยทุกจุดมักแปลว่าไม่ได้วัดข้อมูล")
    build_sheet(out_dir, [s["name"] for s in GOLDEN_PATCHES])
    print(
        f"\nบันทึกที่ {out_dir}\n"
        "เกณฑ์ผ่าน/ไม่ผ่านมาจากตัวเลขข้างบน ซึ่งวัดจากความสูงของบล็อกจริง\n"
        "ภาพเป็นของประกอบให้คนเปิดดู — หน้าตัด (section_*.png) เชื่อได้ตรง ๆ\n"
        "ส่วนภาพ 3D (--eye) มาจาก renderer แทน ห้ามใช้ตัดสิน"
    )


def flag_degenerate(report):
    """เตือนเมื่อตัวเลขตัวหนึ่ง "เท่ากันหมด" ทุกจุด — เกือบทุกครั้งคือบั๊ก ไม่ใช่ข้อเท็จจริง

    9 พื้นที่นี้ถูกเลือกมาให้ *ต่างกัน* (หุบ/ที่ราบ/ปากทะเลสาบ/หน้าผา) ถ้าตัวเลข
    ตัวไหนออกมาเท่ากันเป๊ะทุกจุด แปลว่ามันไม่ได้วัดข้อมูล  `water_column_max = 1`
    ทั้ง 9 จุดคือบั๊กที่หลุดมาได้เพราะไม่มีใครมองมุมนี้ — ด่านนี้จะดักได้เอง
    """
    if len(report) < 3:
        return []
    keys = sorted(set().union(*(set(m) for m in report.values())))
    flat = []
    for key in keys:
        values = [m[key] for m in report.values() if key in m]
        if len(values) >= 3 and len(set(values)) == 1 and values[0] not in (0, 0.0):
            flat.append(f"{key} = {values[0]} เท่ากันทั้ง {len(values)} จุด")
    return flat


def selftest():
    """ตรวจตัวเครื่องมือเอง ก่อนจะเชื่อผลที่มันวัดออกมา

    สามด่าน ทุกด่านตอบคำถามเดียวกัน: "ถ้าโค้ดผิด จะมีอะไรขัดแย้งให้เห็นไหม"
      1. เทียบกับสูตรที่เขียนคนละแบบ (naive loop) — จับ vectorise ผิด/หน้าต่างเลื่อน
      2. ผังที่รู้คำตอบอยู่แล้ว — จับ metric ที่เปลี่ยนความหมายไปเงียบ ๆ
      3. ยัดค่าที่เป็นไปไม่ได้เข้าไป — จับด่าน audit ที่ตายไปเอง
    """
    failures = []
    rng = np.random.default_rng(7)

    # 1. canyon_depth: เทียบกับสูตร naive ที่วนทีละ cell (ช้าแต่ตรงไปตรงมา)
    for trial in range(6):
        h = rng.integers(90, 130, size=(14, 14)).astype(np.int32)
        w = rng.random((14, 14)) < 0.25
        s = np.full((14, 14), 100, dtype=np.int32)
        got = canyon_depth(h, w, s, reach=3)
        want = np.zeros_like(got)
        for z in range(14):
            for x in range(14):
                if not w[z, x]:
                    continue
                best = 0
                for axis in (0, 1):
                    sides = []
                    for direction in (1, -1):
                        peak = -10 ** 9
                        for step in range(1, 4):
                            iz = z + (direction * step if axis == 0 else 0)
                            ix = x + (direction * step if axis == 1 else 0)
                            iz = min(max(iz, 0), 13)
                            ix = min(max(ix, 0), 13)
                            peak = max(peak, int(h[iz, ix]))
                        sides.append(peak)
                    best = max(best, min(sides) - int(s[z, x]))
                want[z, x] = max(best, 0)
        if not np.array_equal(got, want):
            failures.append(f"canyon_depth ไม่ตรงกับสูตร naive (trial {trial})")
            break

    # 2. ผังที่รู้คำตอบ: ลำน้ำตรง ตลิ่งสูงกว่าน้ำ 2 บล็อก ต้องไม่ละเมิดอะไรเลย
    size = 2 * CORE_MARGIN + 12
    water = np.zeros((size, size), dtype=bool)
    water[:, size // 2:size // 2 + 2] = True
    surface = np.full((size, size), 100, dtype=np.int16)
    terrain = np.full((size, size), 102, dtype=np.int16)
    terrain[water] = 100
    depth = np.zeros((size, size), dtype=np.uint8)
    depth[water] = 2
    # หน้าตัดหนึ่งแถวต่อ section เหมือน fixture ใน test_golden_patches —
    # metric ตั้งใจ reject ผังที่ไม่มี section_id เพื่อไม่ให้ "วัดไม่ได้" กลาย
    # เป็นศูนย์ฟรี ดังนั้น selftest เองก็ต้องประกาศ ownership ให้ครบ
    section = np.zeros((size, size), dtype=np.int32)
    section[water] = (
        (np.arange(size, dtype=np.int32)[:, None] + 1)
        .repeat(size, axis=1)[water]
    )
    clean = {
        "water_mask": water, "waterway_mask": water.copy(),
        "surface_y": surface, "terrain_y": terrain, "depth": depth,
        "section_id": section,
        "waterfall_top_y": np.full((size, size), UNRESOLVED, dtype=np.int16),
        "bounds": np.asarray([0, size, 0, size]),
    }
    metrics = patch_metrics(clean, terrain.copy())
    for key in MUST_BE_ZERO:
        if metrics[key]:
            failures.append(f"ผังสะอาดแต่ {key} = {metrics[key]}")
    if metrics["water_column_max"] != 2:
        failures.append(
            f"water_column_max = {metrics['water_column_max']} ทั้งที่ depth = 2"
        )

    # 3. ด่าน audit ต้องยังทำงาน — ยัดค่าที่เป็นไปไม่ได้เข้าไป
    try:
        audit_metrics(
            {**metrics, "water_column_max": 999}, clean, core_mask((size, size))
        )
        failures.append("audit_metrics ปล่อยค่าที่เป็นไปไม่ได้ผ่าน")
    except AssertionError:
        pass

    for line in failures:
        print(f"  !! {line}")
    print("selftest ผ่าน" if not failures else f"selftest ล้ม {len(failures)} ข้อ")
    return len(failures)


def compare(before, after):
    def load(tag):
        with open(
            os.path.join(GOLDEN_DIR, tag, "metrics.json"), encoding="utf-8"
        ) as f:
            return json.load(f)

    def load_provenance(tag):
        path = os.path.join(GOLDEN_DIR, tag, "run.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    a, b = load(before), load(after)
    regressions = 0

    pa, pb = load_provenance(before), load_provenance(after)
    if pa and pb:
        print(
            f"  {before}: git {pa.get('git', '?')} ({pa.get('generated', '?')})\n"
            f"  {after}:  git {pb.get('git', '?')} ({pb.get('generated', '?')})"
        )
        same = [
            name for name in sorted(set(a) & set(b))
            if pa.get("fingerprints", {}).get(name)
            and pa["fingerprints"].get(name) == pb.get("fingerprints", {}).get(name)
        ]
        if same:
            print(
                "  หมายเหตุ: input+โค้ดของจุดพวกนี้ไม่ต่างกันเลย "
                f"({', '.join(same)}) — ผลที่เท่ากันจึงไม่ได้แปลว่าแก้แล้วไม่พัง"
            )

    # จุดหรือตัวเลขที่มีข้างเดียว **ต้องดัง** ไม่ใช่ข้ามเงียบ ๆ  รอบที่ patch หนึ่ง
    # ล้มแล้วไม่ถูกเขียนลง metrics.json คือรอบที่ "ไม่มีอะไรแย่ลง" แบบหลอก ๆ
    missing_after = sorted(set(a) - set(b))
    missing_before = sorted(set(b) - set(a))
    if missing_after:
        print(f"  !! หายไปจาก {after}: {', '.join(missing_after)}")
        regressions += len(missing_after)
    if missing_before:
        print(f"     จุดใหม่ใน {after} (ไม่มีของเทียบ): {', '.join(missing_before)}")

    for name in sorted(set(a) & set(b)):
        rows = []
        only_before = sorted(set(a[name]) - set(b[name]))
        if only_before:
            rows.append(f"    !! ตัวเลขหายไป: {', '.join(only_before)}")
            regressions += len(only_before)
        for key in sorted(set(b[name]) - set(a[name])):
            rows.append(f"       ตัวเลขใหม่ {key} = {b[name][key]}")
        for key in sorted(set(a[name]) & set(b[name])):
            old, new = a[name][key], b[name][key]
            if old == new:
                continue
            worse = (new > old) if key in WORSE_WHEN_UP else (new < old)
            # ธรณีประตูของเสียง: ตัวเลขที่ขยับระดับทศนิยมที่สามไม่ใช่ regression
            #
            # ไม่มีตัวนี้ compare จะรายงาน "ถอยหลัง 22 ตัว" จากการขยับ 0.1-0.3
            # percentage point ซึ่งกลบของจริงจนใช้เป็นด่านไม่ได้  ตัวที่ต้องเป็น
            # ศูนย์ไม่มีธรณีประตู — ขยับ 1 ก็คือถอยหลัง
            if worse and key not in MUST_BE_ZERO:
                if isinstance(old, float):
                    worse = abs(new - old) >= SHARE_NOISE
                else:
                    worse = abs(new - old) >= max(2, abs(old) * COUNT_NOISE)
            if key in NEUTRAL:
                worse = False
            regressions += bool(worse)
            rows.append(
                f"    {'!!' if worse else '  '} {key:<26} {old} -> {new}"
            )
        if rows:
            print(f"  {name}")
            print("\n".join(rows))
    print(
        f"\n{'พบการถอยหลัง ' + str(regressions) + ' ตัว' if regressions else 'ไม่มีตัวไหนแย่ลง'}"
        f" — แต่ยังต้องเปิด section_*.png ของสองรอบดูด้วย"
    )
    return regressions


def suggest(count=3):
    """หาพิกัดที่ควรเพิ่มเข้าชุด จากข้อมูลจริง ไม่ใช่จากการเดา

    เกณฑ์แต่ละแบบเลือก "เคสยาก" คนละชนิด: ลำน้ำชัน (น้ำตกจริง), ลำน้ำกว้างบน
    ที่ราบ (คันดิน/ก้นแบน), จุดบรรจบหลายสาย (ระดับที่ปากสาขา)
    """
    sources = np.load(os.path.join(HERE, "water_sources.npz"), mmap_mode="r")
    terrain = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")
    way = np.asarray(sources["waterway_kind"])[::8, ::8] > 0
    body = np.asarray(sources["waterbody_mask"])[::8, ::8]
    tile = np.asarray(terrain[::8, ::8], dtype=np.float32)

    from scipy import ndimage
    box = 12                                     # ~384 บล็อกที่ระยะย่อ 8
    kernel = np.ones((box, box), dtype=np.float32)
    way_n = ndimage.convolve(way.astype(np.float32), kernel, mode="constant")
    body_n = ndimage.convolve(body.astype(np.float32), kernel, mode="constant")
    relief = (
        ndimage.maximum_filter(tile, size=box)
        - ndimage.minimum_filter(tile, size=box)
    )

    criteria = {
        "ลำน้ำชัน (น้ำตกจริง)": (way_n > 12) * relief,
        "ลำน้ำกว้างบนที่ราบ": (relief < 12) * body_n * (way_n > 4),
        "ลำน้ำหนาแน่น (จุดบรรจบ)": way_n * (body_n < 4),
    }
    for title, score in criteria.items():
        flat = np.argsort(score, axis=None)[::-1]
        print(f"\n{title}")
        taken = []
        for index in flat:
            z, x = np.unravel_index(index, score.shape)
            world = (int(x) * 8, int(z) * 8)
            if any(abs(world[0] - p[0]) < 600 and abs(world[1] - p[1]) < 600
                   for p in taken):
                continue
            taken.append(world)
            print(
                f"    x={world[0]:<5} z={world[1]:<5} "
                f"score={float(score[z, x]):.0f} relief={float(relief[z, x]):.0f} "
                f"way={float(way_n[z, x]):.0f} body={float(body_n[z, x]):.0f}"
            )
            if len(taken) >= count:
                break


def main():
    use_utf8_stdout()
    argv = sys.argv[1:]
    command = argv[0] if argv else "list"

    def flag(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    if command == "list":
        for spec in GOLDEN_PATCHES:
            print(
                f"{spec['name']:<18} ({spec['x']}, {spec['z']}) "
                f"size {spec['size']}\n    {spec['why']}"
            )
    elif command == "run":
        only = flag("--only")
        run(
            names=set(only.split(",")) if only else None,
            tag=flag("--tag", "latest"),
            force="--force" in argv,
            with_eye="--eye" in argv,
        )
    elif command == "compare":
        if len(argv) < 3:
            raise SystemExit("ใช้: compare <before> <after>")
        raise SystemExit(1 if compare(argv[1], argv[2]) else 0)
    elif command == "selftest":
        raise SystemExit(1 if selftest() else 0)
    elif command == "suggest":
        suggest(count=int(flag("--count", 3)))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
