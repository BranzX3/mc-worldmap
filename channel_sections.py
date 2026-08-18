"""ขึ้นรูปลำน้ำจาก "หน้าตัด" ที่ประกาศไว้ แทนการขุดแล้วไล่ซ่อม

ทำไมต้องมีไฟล์นี้ (อ่านก่อนแก้อะไร):

`shape_waterway_patch` เดิมตัดสินระดับน้ำ **ทีละ cell บน raster** แล้วค่อยไล่
ซ่อมความขัดแย้งด้วยกฎซ้อนกันหลายชั้น (envelope, ยุบหน้าตัด, ตอกปากน้ำ, เพดาน
การขุด, การยกเว้นหน้าผา) ผลคือกฎหนึ่งไปโผล่เป็นอาการของอีกกฎหนึ่งเสมอ วัดได้
จากตัวฟังก์ชันเอง: 407 บรรทัด, แตะ `surface` 30 ครั้ง, มีคอมเมนต์อธิบายบั๊กเก่า
15 จุด และการไล่หาสาเหตุครั้งล่าสุดเดาผิดติดกัน 4 ครั้ง

อาการที่วัดได้ทั้งหมดสืบกลับไปที่โครงสร้างนี้ข้อเดียว:

    ร่องลึกกว่า DEM p90 17 บล็อก (สูงสุด 69) | ขอบน้ำเป็นผนังตั้ง 55%
    ผนังชันกว่าไหล่เขา 3.5 เท่า | ก้นน้ำแบนเท่ากันทุกทิศ 51%

ไฟล์นี้กลับด้านการทำงาน: **หน้าตัดคือสิ่งที่ประกาศ ไม่ใช่สิ่งที่เหลือจากการซ่อม**

    ก้นน้ำ (ลึกสุดกลางร่อง) -> ตื้นขึ้นที่ขอบ -> ชายฝั่งราบ -> ตลิ่งลาดจนกลืน

คุณสมบัติที่ได้ **โดยโครงสร้าง** ไม่ใช่โดยการไล่วัดแล้วแก้:

* ผิวน้ำในหน้าตัดเดียวกันเท่ากันเสมอ (แจกค่าเดียวทั้งหน้าตัด)
* มีชายฝั่งเสมอ เว้นที่ภูมิประเทศเดิมเป็นผาจริง
* ตลิ่งลาดด้วยความชันของไหล่เขาตรงนั้น จึงกลืนกับภูเขาโดยนิยาม
* ความลึกร่องมาจาก profile 1D ซึ่งวัดแล้วอยู่ต่ำกว่า DEM แค่ p50 1 / p90 2

ทิศทางเดียวที่ระบบนี้ "ซ่อม" คือกันน้ำรั่ว (ยกพื้นที่ต่ำกว่าผิวน้ำในแถบชายฝั่ง)
ซึ่งเป็นการยกที่มีขอบเขตชัดเจนและวัดได้
"""

import numpy as np
from scipy import ndimage

# ช่วงหมายเลขสถานีต่อหนึ่งเส้น — เส้นยาวสุดในแผนที่นี้ ~10,000 sample (0.5 บล็อก
# ต่อ sample) เผื่อไว้ 2^17 แล้วยังอยู่ในช่วง int32 เมื่อคูณกับจำนวนเส้น (~6,000)
SECTION_ID_STRIDE = 1 << 17

# ---- พารามิเตอร์ของหน้าตัด ----
# ทุกตัวคือ "สิ่งที่ผู้เล่นเห็น" ไม่ใช่ค่าปรับจูนลอย ๆ

# ชายฝั่งราบที่ต้องมีก่อนขึ้นตลิ่ง — 1 บล็อกพอให้เดินเลียบและอ่านเป็นริมน้ำ
# กว้างกว่านี้เริ่มดูเป็นทางเดินที่ถูกถาง
SHORE_BLOCKS = 1
# ชายฝั่งอยู่สูงกว่าผิวน้ำกี่บล็อก — 1 คือขอบน้ำแบบลำธารภูเขา
SHORE_RISE = 1
# ตลิ่งลาดได้ไกลสุดกี่บล็อกก่อนปล่อยให้เป็นภูมิประเทศเดิม
# กว้างกว่านี้จะเริ่มไถไหล่เขาเป็นรางกว้าง (เคยวัดว่าถ้าจะลาดให้เท่าไหล่เขาจริง
# ต้องกว้างถึง 44 บล็อก ซึ่งคือการทำลายภูเขา จึงยอมให้ผนังชันกว่าธรรมชาติได้บ้าง
# แล้วไปคุมที่ "อย่าขุดลึก" แทน)
MAX_BANK_BLOCKS = 10
# ความชันตลิ่งขั้นต่ำ/สูงสุด (บล็อกต่อบล็อก) — ใช้ความชันไหล่เขาจริงเป็นหลัก
MIN_BANK_SLOPE = 0.5
MAX_BANK_SLOPE = 3.0
# ก้นน้ำลึกสุดกี่บล็อกใต้ผิวน้ำ (ที่ 4 m/บล็อก ลึกกว่านี้มองไม่เห็นก้นแล้ว)
MAX_BED_DEPTH = 4
# ยกพื้นกันน้ำรั่วได้มากสุดกี่บล็อก
#
# ถ้าไม่จำกัด cell ที่อยู่ริมหน้าผา (ต่ำกว่าผิวน้ำหลายสิบบล็อก) จะถูกยกขึ้นมาเป็น
# เสาดินเดี่ยว ๆ — วัดที่ hill_junction ได้ขั้นตลิ่งสูงสุด 122 บล็อก
# ถ้ายกไม่ไหวแปลว่าน้ำไม่ควรอยู่ตรงนั้นตั้งแต่แรก ปล่อยให้ metric ตลิ่งลอยฟ้อง
SEAL_MAX_RAISE = 3
# กดพื้นลงได้มากสุดกี่บล็อกตอนขึ้นรูปตลิ่ง
#
# ถ้าไม่จำกัด ลำน้ำที่ไหลเลียบตีนผาจะไถผาลงมาหาระดับน้ำทั้งแถบ — วัดที่
# hill_junction ได้ cell ที่ถูกกด 48 บล็อกจนตัวเลข "ผนัง/ไหล่เขา" พุ่งเป็น 48
# หน้าผาต้องยังเป็นหน้าผา น้ำไหลอยู่ตีนผาได้โดยไม่ต้องรื้อมัน
BANK_MAX_CUT = 6
# แถบชายฝั่งถูกกดได้ลึกกว่านั้น เพราะมันคือ *ส่วนหนึ่งของลำน้ำ* ไม่ใช่ไหล่เขา
#
# ถ้าใช้เพดานเดียวกับตลิ่ง ลำน้ำที่ไหลผ่านที่สูงจะไม่มีชายฝั่งเลย (วัดในเทสต์:
# ขอบน้ำสูงกว่าผิวน้ำ 14 บล็อกทั้งที่ควรเป็น 1) แต่ถ้าปล่อยไม่จำกัด หน้าผาริมน้ำ
# จะถูกไถทิ้ง  4 คือจุดที่ยังเจาะชั้นดินได้แต่ไม่กินเข้าไปในผา
SHORE_MAX_CUT = 4
# แอ่งลึกกว่าเพดานปกติได้อีกเท่านี้ — เป็นความตั้งใจ ไม่ใช่การรั่วของเพดาน
POOL_EXTRA_DEPTH = 1
# ปลายทั้งสองข้างของแต่ละเส้นต้องค่อย ๆ จางเข้าหาพื้นเดิมกี่ sample
#
# ถ้าไม่จาง ลำน้ำจะ "โผล่" ขึ้นมาเป็นหลุมลึกทันทีที่ cell แรก — ต้นสายของลำธาร
# (และปลายเส้นที่ถูกตัดด้วยขอบ patch) จึงกลายเป็นบ่อสี่เหลี่ยมกลางเนิน
END_FADE_SAMPLES = 4


def bed_depth_across(offset, half_width, max_depth):
    """ความลึกของก้นน้ำที่ระยะ ``offset`` จากกลางร่อง

    ก้นน้ำจริงลึกสุดกลางร่องแล้วตื้นขึ้นหาขอบ ถ้าลึกเท่ากันหมดจะได้รางสี่เหลี่ยม
    ซึ่งวัดจากผังจริงได้ 51% ของ cell (ที่ปากทะเลสาบ 90%)
    """
    offset = np.asarray(offset, dtype=np.float32)
    half = np.maximum(np.asarray(half_width, dtype=np.float32), 0.5)
    ratio = np.clip(1.0 - (offset / (half + 0.5)) ** 2, 0.0, 1.0)
    depth = np.maximum(1.0, np.asarray(max_depth, dtype=np.float32) * ratio)
    return np.rint(depth).astype(np.int32)


def flow_index(stage, x, z, drop_target=2, max_run=192.0):
    """ความชันของลำน้ำที่แต่ละสถานี หน่วยเป็น **ต่อพัน** (0..255 = 0..25.5%)

    ทำไมเป็นความชัน ไม่ใช่ "ความเร็ว": สเกลโลกนี้คือ 4 m ต่อบล็อกทั้งสองแกน
    การใส่สูตร Manning ตรง ๆ ให้ตัวเลขที่ดูเป็นฟิสิกส์แต่แปลว่า "ลำธารลึก 4
    เมตรไหล 6 m/s" ทุกสาย  ความชันคือสิ่งที่วัดได้จริงจาก profile และเป็นตัว
    ที่ตัดสินสิ่งที่ผู้เล่นเห็น (ตะกอนถูกพัดหรือสะสม) โดยตรง

    **หน้าต่างต้องยืดตามความชัน ไม่ใช่คงที่**: ผิวน้ำเป็นจำนวนเต็มบล็อก ถ้าวัด
    บนหน้าต่างคงที่ ±16 บล็อก ความชันที่เป็นไปได้จะมีแค่ 0, 31, 62, ... ต่อพัน
    ช่วง 6-20 (ลำน้ำบนที่ราบเชิงเขา) จึงเป็นค่าที่ *เกิดขึ้นไม่ได้เลย* วัดจาก
    ผังจริงได้ยืนยัน: ทุกจุดตกอยู่ใน "นิ่ง" หรือ "เชี่ยวขึ้นไป" ไม่มีตัวกลาง

    ตรงนี้จึงถามกลับด้าน: **ต้องเดินไปไกลแค่ไหนน้ำถึงจะลง ``drop_target``
    บล็อก** ระยะนั้นคือส่วนกลับของความชัน  ``max_run`` กันไม่ให้ช่วงที่ราบยาว
    ไปดูดค่าจากน้ำตกที่อยู่ไกลออกไป
    """
    stage = np.asarray(stage, dtype=np.float32)
    x = np.asarray(x, dtype=np.float32)
    z = np.asarray(z, dtype=np.float32)
    n = stage.size
    if n == 0:
        return np.zeros(0, dtype=np.uint8)
    step = np.concatenate(([0.0], np.hypot(np.diff(x), np.diff(z))))
    along = np.cumsum(step)
    # `regularize_stage` ทำให้ stage ไม่เพิ่มตามลำดับ array — ใช้หาตำแหน่งได้
    # ด้วย searchsorted บนค่าที่กลับเครื่องหมายแล้ว (ซึ่งไม่ลด)
    rising = -stage
    hi = np.searchsorted(rising, rising + drop_target, side="left")
    lo = np.searchsorted(rising, rising - drop_target, side="right") - 1
    hi = np.clip(hi, 0, n - 1)
    lo = np.clip(lo, 0, n - 1)
    # ตัดหน้าต่างที่ยาวเกิน — ที่ราบยาว ๆ ต้องได้ความชันต่ำ ไม่ใช่ไปยืมค่าจาก
    # น้ำตกที่อยู่ปลายสาย
    far_hi = along[hi] - along > max_run
    far_lo = along - along[lo] > max_run
    hi_pos = np.where(far_hi, np.searchsorted(along, along + max_run) - 1, hi)
    lo_pos = np.where(far_lo, np.searchsorted(along, along - max_run), lo)
    hi_pos = np.clip(hi_pos, 0, n - 1)
    lo_pos = np.clip(lo_pos, 0, n - 1)
    run = np.maximum(along[hi_pos] - along[lo_pos], 1.0)
    drop = np.maximum(stage[lo_pos] - stage[hi_pos], 0.0)
    return np.rint(np.clip(drop / run, 0.0, 0.255) * 1000).astype(np.uint8)


def bank_height_at(offset, shore_end, slope):
    """ความสูงของตลิ่งเหนือผิวน้ำที่ระยะ ``offset``

    ในแถบชายฝั่งคงที่ที่ ``SHORE_RISE`` แล้วค่อยไต่ขึ้นตามความชันไหล่เขาจริง
    ตรงนี้คือหัวใจของคำว่า "กลมกลืน": ถ้าไต่ด้วยความชันเดียวกับเนินที่มันตัดผ่าน
    รอยต่อจะไม่หักมุม
    """
    offset = np.asarray(offset, dtype=np.float32)
    shore_end = np.asarray(shore_end, dtype=np.float32)
    beyond = np.maximum(0.0, offset - shore_end)
    return SHORE_RISE + beyond * np.asarray(slope, dtype=np.float32)


def section_offsets(reach):
    """ลำดับระยะจากกลางร่องออกไปสองข้าง — ใกล้ก่อนเสมอ

    ต้องไล่จากใกล้ไปไกลเพื่อให้ "หน้าตัดที่ใกล้กว่าเป็นเจ้าของ cell" ตรงจุดที่
    สองหน้าตัดทับกัน (ทางโค้งและจุดบรรจบ) ถ้าไล่มั่วจะได้ระดับกระโดดสลับกัน
    """
    for step in range(int(reach) + 1):
        if step == 0:
            yield 0, 1
        else:
            yield step, 1
            yield step, -1


class SectionPlan:
    """หน้าตัดทั้งหมดของ patch หนึ่ง เก็บเป็นอาร์เรย์ขนานกัน (ต่อ sample)

    แยกเป็นคลาสเพราะการขึ้นรูปต้องอ่านค่าพวกนี้หลายรอบ และการเก็บเป็น dict of
    arrays ทำให้เทสต์ยิงเข้าทีละส่วนได้โดยไม่ต้องมี raster จริง
    """

    __slots__ = ("x", "z", "stage", "half_width", "bed", "slope", "kind",
                 "reach", "ident", "flow_x", "flow_z", "flow")

    def __init__(self, x, z, stage, half_width, bed, slope, kind, reach,
                 ident=0, flow_x=None, flow_z=None, flow=None):
        self.x = np.asarray(x, dtype=np.int32)
        self.z = np.asarray(z, dtype=np.int32)
        self.stage = np.asarray(stage, dtype=np.int32)
        self.half_width = np.asarray(half_width, dtype=np.float32)
        self.bed = np.asarray(bed, dtype=np.int32)
        self.slope = np.asarray(slope, dtype=np.float32)
        self.kind = np.asarray(kind, dtype=np.uint8)
        self.reach = np.asarray(reach, dtype=np.int32)
        # หมายเลขของ *เส้น* ที่ profile นี้มาจาก — ต้องคงที่ข้าม tile ไม่งั้น
        # `section_id` ของสองฝั่งรอยต่อจะเป็นคนละหน้าตัดทั้งที่เป็นเส้นเดียวกัน
        self.ident = int(ident)
        # ทิศทางการไหล (หน่วย) กับดัชนีแรงน้ำต่อสถานี — ผู้บริโภคปลายทาง
        # (วัสดุก้นน้ำ พืชน้ำ กก) ต้องใช้ ไม่ใช่เดาจากความลึกอย่างเดียว
        zeros = np.zeros(self.x.shape, dtype=np.float32)
        self.flow_x = zeros if flow_x is None else np.asarray(flow_x, np.float32)
        self.flow_z = zeros if flow_z is None else np.asarray(flow_z, np.float32)
        self.flow = (
            np.zeros(self.x.shape, dtype=np.uint8) if flow is None
            else np.asarray(flow, dtype=np.uint8)
        )

    def __len__(self):
        return int(self.x.size)


def thalweg_depth(x, z, base_depth):
    """ความลึกก้นน้ำที่ส่ายไปมาตามความยาวลำน้ำ

    ความลึกคงที่ทั้งสายทำให้ก้นเป็นรางสี่เหลี่ยม (วัดได้ 61% ที่แม่น้ำบนที่ราบ)
    ใช้ฟังก์ชันของ *พิกัดโลก* จึงไม่มีรอยต่อระหว่าง tile และไม่ต้องพึ่ง RNG
    """
    x = np.asarray(x, dtype=np.float32)
    z = np.asarray(z, dtype=np.float32)
    wobble = (
        np.sin(x / 7.0 + z / 11.0) + 0.6 * np.sin(x / 3.0 - z / 5.0)
    )
    return np.clip(
        np.rint(np.asarray(base_depth, dtype=np.float32) + wobble), 1, None
    ).astype(np.int32)


def pool_bonus(stage, min_pool=6):
    """แอ่ง (ช่วงที่ผิวน้ำระดับเดียวกันยาว ๆ) ต้องลึกกว่าแก่ง

    ลำธารจริงลึกที่แอ่งและตื้นที่แก่ง ถ้าความลึกเท่ากันตลอดสายก้นจะเป็นรางยาว
    ใช้ความยาวช่วงระดับเดียวกันเป็นตัวบอกว่าตรงไหนเป็นแอ่ง — ข้อมูลนี้มีอยู่แล้ว
    ใน profile ไม่ต้องคำนวณอะไรใหม่
    """
    stage = np.asarray(stage, dtype=np.int32)
    bonus = np.zeros(stage.shape, dtype=np.int32)
    if stage.size == 0:
        return bonus
    cut = np.flatnonzero(np.diff(stage) != 0)
    starts = np.concatenate(([0], cut + 1))
    ends = np.concatenate((cut + 1, [stage.size]))
    for a, b in zip(starts, ends):
        if b - a >= min_pool:
            mid0 = a + (b - a) // 4
            mid1 = b - (b - a) // 4
            bonus[mid0:mid1] = 1
    return bonus


def outer_rim_field(terrain, reach=MAX_BANK_BLOCKS):
    """พื้นดินที่ *ขอบนอก* ของแถบตลิ่ง — ตัวตั้งของคำถาม "ต้องลาดไกลแค่ไหน"

    เดิม `plan_from_profile` ใช้ ``terrain`` ที่ cell กลางร่องเป็น rim ซึ่งเป็นจุด
    ที่ต่ำที่สุดของหน้าตัดอยู่แล้ว (วัดที่ wild_canyon: สูงกว่าผิวน้ำ p50 1 บล็อก
    ขณะที่พื้นดินจริงห่างออกไป 8 บล็อกสูงกว่าผิวน้ำ p50 8 / p90 17) ผลคือ ``need``
    เกือบศูนย์เสมอ ``reach`` ยุบเหลือ ~2 และแถบตลิ่งไม่เคยถูกสร้างจริง — ทั้ง
    `MAX_BANK_BLOCKS`, `BANK_MAX_CUT` และการเฟดขอบแถบจึงไม่เคยมีผล เพราะแถบสั้น
    เกินกว่าจะไปชนเพดานพวกนั้น วัดได้ว่าที่ระยะ >=4 บล็อกจากน้ำ พื้นดินไม่ถูกแตะ
    เลยสักบล็อก (carve min 0 max 0) ลำน้ำจึงเป็นร่องเปียกที่ถูกแปะลงบนพื้นดิบ

    ใช้ค่าสูงสุดในรัศมี ``reach`` เพราะสิ่งที่ต้องกลืนคือ *ยอด* ของผนัง ไม่ใช่
    ค่าเฉลี่ย — ถ้าใช้ค่าเฉลี่ยแถบจะยังสั้นกว่าผนังที่มันต้องไปบรรจบ
    """
    size = int(max(1, reach)) * 2 + 1
    return ndimage.maximum_filter(
        np.asarray(terrain, dtype=np.int32), size=size, mode="nearest"
    )


def plan_from_profile(profile, terrain, slope_field, max_bed=MAX_BED_DEPTH,
                      outer_rim=None):
    """แปลง profile หนึ่งเส้น (1D) เป็นหน้าตัดต่อ sample

    ``profile`` คือ dict จาก `hydrology_shape.line_profile_entries` ซึ่งให้
    ตำแหน่งกับระดับผิวน้ำมาแล้ว หน้าที่ตรงนี้คือเติม *รูปทรง* ให้มัน
    """
    gx = np.asarray(profile["x"], dtype=np.int32)
    gz = np.asarray(profile["z"], dtype=np.int32)
    stage = np.asarray(profile["stage"], dtype=np.int32)
    height, width = terrain.shape
    inside = (gx >= 0) & (gx < width) & (gz >= 0) & (gz < height)
    gx, gz, stage = gx[inside], gz[inside], stage[inside]
    # ต้องมีอย่างน้อยสอง sample ถึงจะหาทิศทางน้ำได้ — เส้นที่โผล่เข้ามาใน patch
    # แค่ cell เดียวไม่มีข้อมูลพอจะวางหน้าตัด (และ np.gradient ก็ระเบิด)
    if gx.size < 2:
        return None

    half = np.full(gx.shape, float(profile["radius"]), dtype=np.float32)
    # ความลึกโตตามความกว้าง — ลำธาร 1 บล็อกลึก 1, แม่น้ำกว้างลึกได้ถึงเพดาน
    bed = np.clip(np.rint(half * 1.5), 1, int(max_bed)).astype(np.int32)
    bed = np.minimum(thalweg_depth(gx, gz, bed), int(max_bed) + 1)
    bed = np.minimum(
        bed + pool_bonus(stage), int(max_bed) + POOL_EXTRA_DEPTH
    )
    local = np.clip(
        slope_field[gz, gx].astype(np.float32), MIN_BANK_SLOPE, MAX_BANK_SLOPE
    )
    # ตลิ่งต้องไต่จากผิวน้ำขึ้นไปถึงพื้นเดิม ระยะที่ต้องใช้ = ส่วนต่าง / ความชัน
    #
    # "พื้นเดิม" ต้องวัดที่ขอบนอกของแถบ ไม่ใช่ที่กลางร่อง — ดู `outer_rim_field`
    if outer_rim is None:
        outer_rim = outer_rim_field(terrain)
    rim = np.asarray(outer_rim)[gz, gx].astype(np.float32)
    need = np.maximum(0.0, rim - stage.astype(np.float32) - SHORE_RISE) / local
    reach = np.clip(
        np.rint(np.ceil(half) + SHORE_BLOCKS + need), 1, MAX_BANK_BLOCKS
    ).astype(np.int32)
    # จางเข้าหาพื้นเดิมที่ปลายทั้งสองข้าง
    index = np.arange(gx.size)
    edge = np.minimum(index, gx.size - 1 - index).astype(np.float32)
    fade = np.clip(edge / max(1.0, float(END_FADE_SAMPLES)), 0.0, 1.0)
    bed = np.maximum(1, np.rint(bed * fade).astype(np.int32))
    half = np.maximum(0.5, half * np.maximum(fade, 0.5))
    reach = np.maximum(1, np.rint(reach * np.maximum(fade, 0.3))).astype(np.int32)

    kind = np.full(gx.shape, int(profile["kind"]), dtype=np.uint8)
    # ทิศการไหล = แทนเจนต์ของเส้น ซึ่งเรียงจากต้นน้ำไปท้ายน้ำอยู่แล้ว
    # (`regularize_stage` บังคับให้ stage ไล่ลงตามลำดับ array)
    tx = np.gradient(gx.astype(np.float64))
    tz = np.gradient(gz.astype(np.float64))
    norm = np.hypot(tx, tz)
    norm[norm == 0] = 1.0
    flow = flow_index(stage, gx, gz)
    # ไม่มี default: profile สองเส้นที่ ident เท่ากันจะถูกนับเป็นหน้าตัดเดียวกัน
    # ซึ่งเป็นความผิดที่เงียบสนิท ผู้เรียกต้องเป็นคนแจกหมายเลข
    if "ident" not in profile:
        raise ValueError(
            "profile ต้องมี 'ident' (หมายเลขประจำเส้นที่คงที่ทั้งแผนที่) "
            "ไม่งั้น section_id ของคนละเส้นจะชนกัน"
        )
    return SectionPlan(gx, gz, stage, half, bed, local, kind, reach,
                       ident=int(profile["ident"]),
                       flow_x=tx / norm, flow_z=tz / norm, flow=flow)


def stamp_sections(plans, terrain, protect=None):
    """ประทับหน้าตัดทั้งหมดลงกริด คืน dict ของ product

    กติกาการชนกัน: **หน้าตัดที่ใกล้กว่าชนะ** และถ้าใกล้เท่ากัน ระดับน้ำที่ต่ำกว่า
    ชนะ — ข้อหลังสำคัญที่จุดบรรจบ เพราะน้ำสองสายที่มาเจอกันต้องอยู่ที่ระดับของ
    สายที่ต่ำกว่า ไม่งั้นสายหนึ่งจะลอยอยู่เหนืออีกสาย

    ``protect`` คือ cell ที่ห้ามแตะ (ทะเลสาบที่ขึ้นรูปไปแล้ว)
    """
    height, width = terrain.shape
    shaped = terrain.astype(np.int32).copy()
    surface = np.full(terrain.shape, np.iinfo(np.int32).max, dtype=np.int32)
    depth = np.zeros(terrain.shape, dtype=np.int32)
    water = np.zeros(terrain.shape, dtype=bool)
    kind = np.zeros(terrain.shape, dtype=np.uint8)
    owner_offset = np.full(terrain.shape, 1 << 30, dtype=np.int32)
    centerline = np.zeros(terrain.shape, dtype=bool)
    # cell นี้เป็นของหน้าตัดไหน (สาย, สถานี) — 0 = ไม่ใช่ของใคร
    #
    # จำเป็นสำหรับ harness: ถ้าไม่บอกว่าใครเป็นเจ้าของ ตัววัดต้องเดาเอาเองด้วย
    # EDT ซึ่งบนเส้นทแยงจะจับ cell เข้าหน้าตัดข้าง ๆ แทนของตัวเอง แล้วรายงาน
    # หน้าตัดที่ราบสนิทว่าไม่ราบ (วัดที่ hill_junction ได้ 82 cell ที่ 'ผิด'
    # ทั้งที่ทุกตัวถือระดับของหน้าตัดที่ประชิดตัวเองจริง ๆ)
    section = np.zeros(terrain.shape, dtype=np.int32)
    # ทิศการไหลเก็บเป็น int8 (คูณ 127) — พอสำหรับ 8 ทิศบวกอะไรที่ละเอียดกว่า
    # และเล็กกว่า float32 สี่เท่าเมื่อเขียนเป็น product ระดับโลก
    flow_x = np.zeros(terrain.shape, dtype=np.int8)
    flow_z = np.zeros(terrain.shape, dtype=np.int8)
    flow = np.zeros(terrain.shape, dtype=np.uint8)
    protect = (
        np.zeros(terrain.shape, dtype=bool) if protect is None
        else np.asarray(protect, dtype=bool)
    )

    for plan in plans:
        if plan is None or len(plan) == 0:
            continue
        # id ต้องมาจาก **เส้น** ไม่ใช่ลำดับใน `plans` เพราะ --global เรียกทีละ
        # tile ด้วย subset ของ profile ชุดเดียวกัน ถ้าใช้ลำดับ cell สองฝั่งรอย
        # ต่อ tile จะได้ id คนละตัวทั้งที่เป็นหน้าตัดเดียวกัน
        plan_base = plan.ident * SECTION_ID_STRIDE
        tx = np.gradient(plan.x.astype(np.float64))
        tz = np.gradient(plan.z.astype(np.float64))
        norm = np.hypot(tx, tz)
        norm[norm == 0] = 1.0
        nx, nz = -tz / norm, tx / norm
        reach_max = int(plan.reach.max())

        for step, sign in section_offsets(reach_max):
            active = plan.reach >= step
            if not active.any():
                continue
            px = np.rint(plan.x[active] + sign * nx[active] * step)
            pz = np.rint(plan.z[active] + sign * nz[active] * step)
            px = px.astype(np.int64)
            pz = pz.astype(np.int64)
            ok = (px >= 0) & (px < width) & (pz >= 0) & (pz < height)
            if not ok.any():
                continue
            px, pz = px[ok], pz[ok]
            stage = plan.stage[active][ok]
            half = plan.half_width[active][ok]
            bed_max = plan.bed[active][ok]
            slope = plan.slope[active][ok]
            line_kind = plan.kind[active][ok]
            reach_line = plan.reach[active][ok].astype(np.float32)
            station = (np.flatnonzero(active)[ok] + 1).astype(np.int32)
            fx_line = plan.flow_x[active][ok]
            fz_line = plan.flow_z[active][ok]
            flow_line = plan.flow[active][ok]

            # profile ถูก sample ทุก 0.5 บล็อก (`DENSIFY_SPACING`) หลายสถานีจึง
            # ตกลง cell เดียวกันเสมอ ไม่ใช่กรณีพิเศษ  การเขียนแบบ fancy-index
            # ปล่อยให้ "คนเขียนทีหลังชนะ" ซึ่งไม่มีอะไรรับประกันใน numpy —
            # บังคับกติกาที่ประกาศไว้ (ระดับต่ำกว่าชนะ) ตรงนี้แทนการพึ่งลำดับ
            #
            # เคยลองเปลี่ยนเป็น "สถานีที่เล็งตรงที่สุดชนะ" เพื่อไล่หน้าตัดที่ไม่
            # ราบ วัดแล้วพบว่า **ไม่ได้อะไรเลย** (unflat 0 เท่ากันทั้งสองแบบ
            # เพราะ cell ถือระดับของสถานีที่เป็นเจ้าของมันอยู่แล้ว) แต่ผิวน้ำที่
            # ค้างสูงทำให้การซีลตลิ่งยกดินเพิ่ม: ผนังริมน้ำ hill_junction
            # 492 -> 574 และ steep_stream 445 -> 520  จึงคงกติกาเดิมไว้
            flat = pz * width + px
            order = np.lexsort((stage, flat))
            keep = np.ones(order.size, dtype=bool)
            keep[1:] = flat[order][1:] != flat[order][:-1]
            uniq = order[keep]
            px, pz = px[uniq], pz[uniq]
            stage, half = stage[uniq], half[uniq]
            bed_max, slope = bed_max[uniq], slope[uniq]
            line_kind, reach_line = line_kind[uniq], reach_line[uniq]
            station = station[uniq]
            fx_line, fz_line = fx_line[uniq], fz_line[uniq]
            flow_line = flow_line[uniq]

            free = ~protect[pz, px]
            better = (owner_offset[pz, px] > step) | (
                (owner_offset[pz, px] == step) & (stage < surface[pz, px])
            )
            take = free & better
            if not take.any():
                continue
            px, pz = px[take], pz[take]
            stage, half = stage[take], half[take]
            bed_max, slope = bed_max[take], slope[take]
            line_kind = line_kind[take]
            reach_here = reach_line[take]
            station = station[take]
            fx_line, fz_line = fx_line[take], fz_line[take]
            flow_line = flow_line[take]
            owner_offset[pz, px] = step

            wet = step <= np.maximum(half, 0.5)
            if wet.any():
                wx, wz = px[wet], pz[wet]
                wstage = stage[wet]
                d = bed_depth_across(step, half[wet], bed_max[wet])
                water[wz, wx] = True
                surface[wz, wx] = wstage
                section[wz, wx] = plan_base + station[wet]
                flow_x[wz, wx] = np.rint(fx_line[wet] * 127).astype(np.int8)
                flow_z[wz, wx] = np.rint(fz_line[wet] * 127).astype(np.int8)
                flow[wz, wx] = flow_line[wet]
                depth[wz, wx] = d
                shaped[wz, wx] = wstage - d
                kind[wz, wx] = line_kind[wet]
                if step == 0:
                    centerline[wz, wx] = True
            dryside = ~wet
            if dryside.any():
                dx, dz = px[dryside], pz[dryside]
                top = stage[dryside] + np.rint(
                    bank_height_at(
                        step, np.maximum(half[dryside], 0.5) + SHORE_BLOCKS,
                        slope[dryside],
                    )
                ).astype(np.int32)
                # กดเฉพาะที่สูงเกินรูปทรงตลิ่ง และยกเฉพาะที่ต่ำกว่าผิวน้ำ
                # (กันน้ำรั่ว) — สองทิศทางนี้คือทั้งหมดที่ระบบนี้แก้ภูมิประเทศ
                current = shaped[dz, dx]
                # ชายฝั่ง (ติดน้ำ) ยอมให้กดลึกกว่าตลิ่ง — มันคือส่วนของลำน้ำเอง
                shore_end = np.maximum(half[dryside], 0.5) + SHORE_BLOCKS
                in_shore = step <= shore_end
                # เพดานการกดต้อง **เฟดเป็นศูนย์ที่ขอบแถบ** ไม่ใช่ตัดจบ
                #
                # ตัดจบทำให้เกิดผนังตรงรอยต่อกับพื้นเดิม — วัดที่ hill_junction
                # ได้ขั้น >=3 ที่ขอบแถบ 3,259 จุด และ 2,250 cell ถูกกดชนเพดาน 6
                # พอดี ซึ่งคือรอยที่ตาอ่านว่า "ของถูกเจาะ" ไม่ใช่ภูมิประเทศ
                span = np.maximum(1.0, reach_here[dryside] - shore_end)
                fade = np.clip(
                    (reach_here[dryside] - step) / span, 0.0, 1.0
                )
                # ต้องลดลงเรื่อย ๆ ออกไปข้างนอก ไม่ใช่กระโดดขึ้น: ถ้าตลิ่งถูกกด
                # ลึกกว่าชายฝั่ง จะได้แอ่งคั่นกลางแทนที่จะเป็นทางลาดต่อเนื่อง
                budget = np.minimum(
                    BANK_MAX_CUT,
                    np.where(in_shore, SHORE_MAX_CUT, SHORE_MAX_CUT * fade),
                )
                top = np.maximum(top, current - budget.astype(np.int32))
                # ยกกันน้ำรั่วได้ **เฉพาะในแถบชายฝั่ง** เท่านั้น
                #
                # เดิมยกได้ทั้งแถบตลิ่ง ผลคือ cell ที่อยู่ห่างน้ำ 7-8 บล็อกและต่ำ
                # กว่าผิวน้ำถูกยกขึ้นมาเป็น "คันดิน" ยาวขนานลำน้ำ แล้วขอบนอกของ
                # คันนั้นก็กลายเป็นผนัง 3 บล็อกกับพื้นเดิม (บั๊กเดียวกับสันดินรอบ
                # ทะเลสาบที่เคยแก้ไปแล้วในสถาปัตยกรรมเก่า)
                #
                # ถ้าน้ำจะรั่วออกนอกแถบชายฝั่งจริง แปลว่า profile วางน้ำผิดที่
                # ตั้งแต่แรก — ปล่อยให้ metric `dry_bank_below_water` ฟ้อง
                seal = np.where(
                    in_shore,
                    np.minimum(
                        stage[dryside] + SHORE_RISE, current + SEAL_MAX_RAISE
                    ),
                    current,
                )
                shaped[dz, dx] = np.where(
                    current > top, top, np.maximum(current, seal)
                )

    surface = np.where(water, surface, np.iinfo(np.int16).min).astype(np.int32)
    return {
        "terrain": shaped,
        "surface": surface,
        "depth": depth.astype(np.uint8),
        "water": water,
        "kind": kind,
        "centerline": centerline,
        "section": section,
        "flow_x": flow_x,
        "flow_z": flow_z,
        "flow": flow,
    }
