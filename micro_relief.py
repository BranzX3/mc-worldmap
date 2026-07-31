"""ภูมิสัณฐานย่อยที่ DEM ไม่มี — ตัวที่ทำให้ภูเขาเลิกดูเหมือน heightmap

DEM ที่ 4 m/px เก็บได้แค่รูปทรงใหญ่ ของที่หายไปคือรายละเอียดระดับ 10-40 m ซึ่ง
เป็นสิ่งที่ตาใช้อ่านว่า "นี่คือหินจริง" ไม่ใช่ผิวเรียบที่ถูกปัดเป็นขั้น การ dither
แก้ได้แค่ขอบขั้น (วัดแล้วลานแคบลง 20% แต่คอนทัวร์ยังตรงอยู่ดี) เพราะปัญหาไม่ได้อยู่
ที่การปัดเลข แต่อยู่ที่ *ไม่มีข้อมูลรายละเอียดให้ปัดตั้งแต่แรก*

โมดูลนี้เติมสามอย่างที่เป็นลักษณะเฉพาะของ Nördliche Kalkalpen (เทือกหินปูน):

1. ชั้นหินยื่น (bedding ledges) — หินปูนตกตะกอนเป็นชั้นแนวนอน หน้าผาจริงจึงเป็น
   ขั้นบันไดของชั้นหินหนา 10-25 m ไม่ใช่ผนังลาดเรียบ นี่คือลายเซ็นของ Dachstein
2. กองหินเชิงผา (talus) — หินที่ร่วงจากผาสะสมเป็นเนินลาดที่ตีน ทำให้รอยต่อ
   ผา-พื้นไม่ใช่มุมคม
3. หลุมยุบหินปูน (doline) — ที่ราบสูงหินปูนมีแอ่งกลมจากการละลายของหิน เป็น
   ลักษณะเด่นของที่ราบสูง Dachstein

ทุกอย่างยึดพิกัดโลกและคำนวณจาก slope/ความสูงเท่านั้น จึงต่อเนื่องข้าม tile และ
ให้ผลเดียวกันไม่ว่าจะเรียกจากกรอบไหน
"""

import numpy as np

import surface as S

# ---- ชั้นหินยื่น ----
# ความหนาชั้นหิน 6-14 บล็อก = 24-56 m
#
# หนากว่าชั้น Dachsteinkalk จริง (1-10 m = 0.25-2.5 บล็อก) อย่างตั้งใจ — ชั้นจริง
# บางเกินกว่าจะมองเห็นที่ 4 m/block ถ้าใช้ค่าจริงจะได้ผาเรียบเหมือนไม่ทำอะไร
# เลือกความหนาที่ "อ่านออกว่าเป็นชั้นหิน" แทนความถูกต้องเชิงตัวเลข
BED_THICKNESS_MIN = 6.0
BED_THICKNESS_MAX = 14.0
BED_SCALE_BLOCKS = 260.0        # ขนาดผืนที่ชุดชั้นหินเหมือนกัน
BED_LEVELS = 4                  # จำนวนชุดชั้นหินที่ต่างกัน
BED_SEED = 7717
# ใช้เฉพาะบนที่ชัน — บนที่ลาดการสแนปเป็นชั้นคือลายขั้นเทียมที่เราเพิ่งไล่ออกไป
BED_SLOPE_START = 0.80
BED_SLOPE_FULL = 1.60
# สแนปเต็มสร้างหน้าตั้งสูงเท่าความหนาชั้น: จุดตรวจ (5880, 5554) กระโดด
# 174 -> 162 -> 151 -> 140 เป็นกำแพง 11–12 บล็อกซ้ำกัน ค่า 0.25 ยังทำให้
# แนวชั้นอ่านออก แต่กระจายการเปลี่ยนระดับเป็นขั้น 3–6 บล็อก
BED_STRENGTH = 0.25

# ---- กองหินเชิงผา ----
TALUS_CLIFF_SLOPE = 1.30        # ชันกว่านี้ถือว่าเป็นหน้าผาที่ผลิตหินร่วง
TALUS_STEPS = 10                # ไหลลงได้ไกลกี่บล็อก
TALUS_DECAY = 0.80              # ความแรงที่เหลือต่อหนึ่งก้าวลง
TALUS_HEIGHT = 3.2              # ความหนาสูงสุดของกอง (บล็อก)
TALUS_SLOPE_MAX = 1.10          # กองหินเกาะได้เฉพาะที่ไม่ชันเกินมุมพัก

# ---- หลุมยุบ ----
DOLINE_SCALE_BLOCKS = 70.0
DOLINE_SEED = 8123
DOLINE_THRESHOLD = 0.55         # สัดส่วนพื้นที่ที่กลายเป็นหลุม (ยิ่งสูงยิ่งน้อย)
DOLINE_DEPTH = 3.5              # ลึกสุด (บล็อก)
DOLINE_MIN_ELEV = 1450.0        # ที่ราบสูงหินปูนเหนือแนวนี้
DOLINE_MAX_SLOPE = 0.30         # เกิดบนที่ราบเท่านั้น


def _quantise(field, levels):
    """ปัดค่า 0..1 ให้เป็นขั้นบันได levels ขั้น — ได้ผืนที่ค่าคงที่ภายใน"""
    levels = max(1, int(levels))
    return np.floor(np.asarray(field, dtype=np.float32) * levels) / max(1, levels - 1)


def _noise01(shape, x0, z0, scale, seed, octaves=2):
    return S.smooth_noise(x0, z0, shape, max(2.0, scale), seed, octaves) * 0.5 + 0.5


def slope_blocks(height):
    height = np.asarray(height, dtype=np.float32)
    dz, dx = np.gradient(height, 1.0)
    return np.hypot(dx, dz)


def bedding_ledges(height, slope, x0=0, z0=0, strength=None):
    """คืน delta ที่ทำให้หน้าผากลายเป็นขั้นชั้นหิน

    สแนปความสูงเข้าหาระดับชั้นหินที่ใกล้ที่สุด ผลคือหน้าผาที่เคยลาดเรียบแตกเป็น
    ขั้นสูง ๆ คั่นด้วยชานแคบ — รูปทรงเดียวกับผาหินปูนจริง

    บนผาชันการสแนปแทบไม่ขยับตำแหน่งขอบผาในแนวราบ เพราะความสูงเปลี่ยนเร็วมาก
    ต่อหนึ่งบล็อก การเลื่อนความสูง 2 บล็อกจึงเลื่อนขอบผาแค่ราวหนึ่งบล็อก
    """
    # อ่านค่าคงที่ตอนเรียก ไม่ใช่ผูกเป็น default argument — default ถูกประเมิน
    # ครั้งเดียวตอน import การแก้ค่าโมดูลทีหลังจึงไม่มีผล (กับดักที่ทำให้การ
    # ทดสอบ strength ทั้งชุดเป็นโมฆะโดยไม่มีอะไรฟ้อง)
    if strength is None:
        strength = BED_STRENGTH
    height = np.asarray(height, dtype=np.float32)
    # ความหนาและเฟสต้อง "คงที่เป็นผืน" ไม่ใช่ไล่ต่อเนื่อง
    #
    # ถ้าไล่ต่อเนื่อง ระดับของชั้นหินจะเลื่อนทีละนิดทุกบล็อก (วัดได้ 0.08 บล็อก
    # ต่อบล็อกแนวนอน) สะสมครบหนึ่งบล็อกใน 12 ก้าว ชานหินจึงขาดตลอดและ bedding
    # แทบไม่ได้อะไรเลย (ชาน 1.43 -> 1.54 เทียบกับ 2.40 เมื่อความหนาคงที่)
    #
    # การแบ่งเป็นผืนยังตรงกับธรณีจริงมากกว่า — ชุดชั้นหินเปลี่ยนที่รอยเลื่อน
    # ไม่ใช่ค่อย ๆ หนาขึ้นทีละเซนติเมตร
    levels = BED_LEVELS
    step = _quantise(
        _noise01(height.shape, x0, z0, BED_SCALE_BLOCKS, BED_SEED), levels
    )
    thickness = BED_THICKNESS_MIN + (
        BED_THICKNESS_MAX - BED_THICKNESS_MIN
    ) * step
    phase = _quantise(
        _noise01(height.shape, x0, z0, BED_SCALE_BLOCKS * 0.5, BED_SEED + 31),
        levels,
    ) * thickness

    snapped = np.round((height + phase) / thickness) * thickness - phase
    weight = np.clip(
        (np.asarray(slope, dtype=np.float32) - BED_SLOPE_START)
        / max(1e-6, BED_SLOPE_FULL - BED_SLOPE_START),
        0.0, 1.0,
    ) * float(strength)
    return (snapped - height) * weight


def _propagate_downhill(source, height, steps=None, decay=None):
    """ไล่ค่าจากที่สูงลงที่ต่ำ — ใช้ส่งอิทธิพลของผาลงไปที่ตีนผา

    แต่ละรอบ เซลล์รับค่าจากเพื่อนบ้านที่ *สูงกว่า* ตัวเอง คูณด้วย decay จึงได้
    สนามที่แรงตรงใต้ผาและจางลงเมื่อไกลออกไป โดยไม่ต้องทำ flow routing เต็มรูป
    """
    if steps is None:
        steps = TALUS_STEPS
    if decay is None:
        decay = TALUS_DECAY
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


def talus_apron(height, slope):
    """คืน delta ที่ถมกองหินร่วงไว้เชิงผา"""
    height = np.asarray(height, dtype=np.float32)
    slope = np.asarray(slope, dtype=np.float32)
    cliff = np.clip((slope - TALUS_CLIFF_SLOPE) / 0.8, 0.0, 1.0)
    influence = _propagate_downhill(cliff, height)
    # หินเกาะได้เฉพาะที่ไม่ชันเกินมุมพักของกองหิน และตัวมันเองต้องไม่ใช่หน้าผา
    catch = np.clip((TALUS_SLOPE_MAX - slope) / TALUS_SLOPE_MAX, 0.0, 1.0)
    return influence * catch * TALUS_HEIGHT


def karst_dolines(height, slope, elev_m, x0=0, z0=0):
    """คืน delta (ค่าลบ) ของแอ่งหลุมยุบบนที่ราบสูงหินปูน"""
    shape = np.asarray(height).shape
    blob = _noise01(shape, x0, z0, DOLINE_SCALE_BLOCKS, DOLINE_SEED, 2)
    # เอาเฉพาะยอดของ noise แล้วดัดให้เป็นแอ่งก้นมน
    depth = np.clip((blob - DOLINE_THRESHOLD) / (1.0 - DOLINE_THRESHOLD), 0.0, 1.0)
    depth = depth * depth * (3.0 - 2.0 * depth)

    high = np.clip((np.asarray(elev_m, dtype=np.float32) - DOLINE_MIN_ELEV) / 200.0,
                   0.0, 1.0)
    flat = np.clip(
        (DOLINE_MAX_SLOPE - np.asarray(slope, dtype=np.float32))
        / DOLINE_MAX_SLOPE, 0.0, 1.0,
    )
    return -depth * high * flat * DOLINE_DEPTH


def apply(height, elev_m, x0=0, z0=0, protect=None, features=("bedding",
                                                              "talus",
                                                              "doline")):
    """ใส่ภูมิสัณฐานย่อยลงบนความสูงต่อเนื่อง คืน (height ใหม่, สถิติ)

    protect: mask ที่ห้ามแตะ (ผืนน้ำ) — การขยับพื้นใต้น้ำทำให้ระดับน้ำที่คำนวณ
    ไว้ไม่ตรงกับก้น และผิวทะเลสาบที่เพิ่งทำให้แบนจะเสียไป
    """
    height = np.asarray(height, dtype=np.float32).copy()
    stats = {}
    guard = None if protect is None else np.asarray(protect, dtype=bool)

    def add(delta, name):
        nonlocal height
        if guard is not None:
            delta = np.where(guard, 0.0, delta)
        stats[name] = {
            "area": float((np.abs(delta) > 0.25).mean()),
            "max": float(np.abs(delta).max()),
            "mean_abs": float(np.abs(delta).mean()),
        }
        height = height + delta

    slope = slope_blocks(height)
    if "bedding" in features:
        add(bedding_ledges(height, slope, x0=x0, z0=z0), "bedding")
        slope = slope_blocks(height)
    if "talus" in features:
        add(talus_apron(height, slope), "talus")
        slope = slope_blocks(height)
    if "doline" in features:
        add(karst_dolines(height, slope, elev_m, x0=x0, z0=z0), "doline")
    return height, stats
