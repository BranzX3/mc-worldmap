"""สร้างต้นไม้และพืชพื้นล่าง — ออกแบบตามสเกลที่ผู้เล่นรู้สึกสมเหตุสมผล
ไม่ใช่สเกลจริงของแผนที่ (1 บล็อก = 4 m)

ต้นไม้แต่ละต้นสุ่มจากตำแหน่งของมันเอง (hash ของ x,z) ไม่ใช่จาก RNG ที่ไหลไปเรื่อยๆ
ทำให้ต้นเดียวกันออกมาเหมือนกันเสมอ ไม่ว่าจะถูกสร้างตอนประมวลผล region ไหน
ต้นที่คร่อมขอบ region จึงต่อกันสนิท ไม่ขาดครึ่ง
"""

import numpy as np

# ระยะห่างเฉลี่ยระหว่างต้น (บล็อก) ตามความหนาแน่นของป่า
SPACING_DENSE = 5     # ป่าทึบ 900-1400 m
SPACING_NORMAL = 6
SPACING_SPARSE = 9    # ใกล้แนวไม้
JITTER = 0.42         # ขยับจากตาราง 0..0.5 — ยิ่งมากยิ่งไม่เป็นระเบียบ


def tree_rng(tx, tz, salt=0):
    """RNG ที่ขึ้นกับตำแหน่งต้นไม้เท่านั้น — deterministic ข้าม region"""
    h = (tx * 0x1f1f1f1f) ^ (tz * 0x27d4eb2d) ^ (salt * 0x9e3779b9)
    return np.random.default_rng(abs(h) % (2 ** 63))


def spruce(rng, tall):
    """ต้นสน ทรงกรวย — คืน list ของ (dx, dy, dz, kind)
    kind: 'log' หรือ 'leaf'"""
    h = int(rng.integers(6, 9 + (2 if tall else 0)))
    out = [(0, y, 0, "log") for y in range(h)]
    # ใบเป็นชั้นๆ รัศมีสลับ ให้ดูเป็นพุ่มสน
    top = h
    radii = []
    r = 0
    for i in range(h - 2):
        r = [0, 1, 1, 2, 2, 1, 1, 2, 2, 1, 1][min(i, 10)]
        radii.append(r)
    radii = radii[::-1]
    for i, r in enumerate(radii):
        y = 2 + i
        if y >= top:
            break
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx * dx + dz * dz > r * r + r:
                    continue
                if dx == 0 and dz == 0 and y < top - 1:
                    continue
                out.append((dx, y, dz, "leaf"))
    out.append((0, top, 0, "leaf"))
    out.append((0, top + 1, 0, "leaf"))
    return out


def oak(rng):
    h = int(rng.integers(4, 7))
    out = [(0, y, 0, "log") for y in range(h)]
    for y in range(h - 2, h + 2):
        r = 2 if y < h else 1
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx * dx + dz * dz > r * r + r:
                    continue
                if dx == 0 and dz == 0 and y < h:
                    continue
                out.append((dx, y, dz, "leaf"))
    return out


def birch(rng):
    h = int(rng.integers(5, 8))
    out = [(0, y, 0, "log") for y in range(h)]
    for y in range(h - 2, h + 2):
        r = 2 if y < h else 1
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx * dx + dz * dz > r * r + r:
                    continue
                if dx == 0 and dz == 0 and y < h:
                    continue
                out.append((dx, y, dz, "leaf"))
    return out


def krummholz(rng):
    """สนแคระใกล้แนวไม้ — เตี้ย แผ่กว้าง"""
    h = int(rng.integers(2, 4))
    out = [(0, y, 0, "log") for y in range(h)]
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            if dx * dx + dz * dz > 5:
                continue
            out.append((dx, h - 1, dz, "leaf"))
            if abs(dx) <= 1 and abs(dz) <= 1:
                out.append((dx, h, dz, "leaf"))
    return out


SHAPES = {"spruce": spruce, "oak": oak, "birch": birch, "krummholz": krummholz}


def tree_positions(x0, x1, z0, z1, spacing_map, seed=7):
    """คืน list ของ (x, z) ตำแหน่งต้นไม้ในกรอบที่ให้ โดยใช้ตารางแบบ jitter

    spacing_map เป็นฟังก์ชัน (x, z) -> ระยะห่าง หรือ array ที่ index ได้
    """
    out = []
    # ใช้ระยะห่างต่ำสุดเป็นขนาดตาราง แล้วค่อยคัดทิ้งตามระยะจริงของแต่ละจุด
    step = SPACING_DENSE
    gx0, gx1 = x0 // step, x1 // step + 1
    gz0, gz1 = z0 // step, z1 // step + 1
    for gx in range(gx0, gx1):
        for gz in range(gz0, gz1):
            r = tree_rng(gx, gz, seed)
            jx = (r.random() - 0.5) * 2 * JITTER
            jz = (r.random() - 0.5) * 2 * JITTER
            x = int((gx + 0.5 + jx) * step)
            z = int((gz + 0.5 + jz) * step)
            if x0 <= x < x1 and z0 <= z < z1:
                out.append((x, z))
    return out


# พืชพื้นล่าง — (บล็อก, น้ำหนัก) ตามโซน
GROUND_FOREST = [
    ("short_grass", .34), ("fern", .30), ("brown_mushroom", .05),
    ("red_mushroom", .04), (None, .27),
]
GROUND_MEADOW = [
    ("short_grass", .44), ("dandelion", .06), ("oxeye_daisy", .05),
    ("cornflower", .04), ("poppy", .04), (None, .37),
]
GROUND_ALPINE = [
    ("short_grass", .26), ("fern", .06), ("oxeye_daisy", .04),
    ("azure_bluet", .03), (None, .61),
]


# ---------------------------------------------------------------- การวางต้นไม้
# ป่าจริงมีสามชั้น: ไม้เด่นที่โผล่พ้นเรือนยอด, ชั้นเรือนยอดหลัก, ไม้ชั้นล่าง
# ระยะห่างของแต่ละชั้นยึดจากความกว้างทรงพุ่มจริงในแพ็ก
LAYERS = [
    # (ชื่อ, ระยะตาราง, โอกาสฐาน, รัศมีที่กันชั้นล่างไม่ให้ขึ้นใกล้)
    ("emergent", 26, 0.55, 11),
    ("canopy",    9, 0.90,  5),
    ("under",     5, 0.85,  0),
]
SIZE_OF = {"emergent": "big", "canopy": "normal", "under": "small"}


def _cell_candidate(step, gx, gz, salt):
    """ตำแหน่งผู้สมัครของช่องตาราง + ค่าสุ่มประจำช่อง (ยึดพิกัดโลกล้วน)"""
    r = tree_rng(gx, gz, salt)
    jx = (r.random() - 0.5) * 2 * JITTER
    jz = (r.random() - 0.5) * 2 * JITTER
    x = int((gx + 0.5 + jx) * step)
    z = int((gz + 0.5 + jz) * step)
    return x, z, r


def _blocked_by(step, radius, base_chance, salt, x, z):
    """ช่องตารางชั้นบนรอบๆ จุดนี้ มีต้นไม้จองพื้นที่อยู่ไหม

    ตัดสินจาก hash อย่างเดียว ไม่ดูภูมิประเทศ เพื่อให้ผลเหมือนกันทุก region
    แม้จุดนั้นจะอยู่นอกกรอบที่กำลังประมวลผล
    """
    gx0, gz0 = x // step, z // step
    for dgx in (-1, 0, 1):
        for dgz in (-1, 0, 1):
            gx, gz = gx0 + dgx, gz0 + dgz
            cx, cz, r = _cell_candidate(step, gx, gz, salt)
            if r.random() > base_chance:
                continue
            if (cx - x) ** 2 + (cz - z) ** 2 < radius * radius:
                return True
    return False


def tree_slots(x0, x1, z0, z1):
    """สร้างรายการจุดปลูกทั้งสามชั้นในกรอบที่ให้

    คืน (x, z, layer, rng) — ตัวเรียกไปตัดสินต่อว่าจุดนั้นปลูกได้จริงไหม
    ตามความหนาแน่นป่า/ความชัน/ความสูง
    """
    out = []
    for li, (layer, step, chance, _r) in enumerate(LAYERS):
        salt = 101 + li * 37
        for gx in range((x0 // step) - 1, (x1 // step) + 2):
            for gz in range((z0 // step) - 1, (z1 // step) + 2):
                x, z, r = _cell_candidate(step, gx, gz, salt)
                if not (x0 <= x < x1 and z0 <= z < z1):
                    continue
                if r.random() > chance:
                    continue
                # ชั้นล่างต้องไม่ไปเบียดใต้ทรงพุ่มของชั้นบน
                blocked = False
                for lj in range(li):
                    lname, lstep, lchance, lrad = LAYERS[lj]
                    if lrad and _blocked_by(lstep, lrad, lchance,
                                            101 + lj * 37, x, z):
                        blocked = True
                        break
                if blocked:
                    continue
                out.append((x, z, layer, r))
    return out


def pick_variant(variants, size, snowy, rng):
    """เลือกต้นจากรายการ โดยพยายามให้ตรงขนาดและสถานะหิมะที่ขอ"""
    if not variants:
        return None
    pool = [t for t in variants if t["size"] == size and t["snowy"] == snowy]
    if not pool:
        pool = [t for t in variants if t["snowy"] == snowy]
    if not pool:
        pool = [t for t in variants if t["size"] == size]
    if not pool:
        pool = variants
    return pool[int(rng.integers(len(pool)))]


# ============================================================ พืชคลุมดินแบบหย่อม
# ปัญหาของการสุ่มอิสระทีละบล็อก: ได้เกลือพริกไทย ทุกชนิดปนกันทั่วเท่าๆ กัน
# ของจริงพืชขึ้นเป็นหย่อม แต่ละหย่อมมักเป็นชนิดเดียวกัน
#
# วิธีแก้: ใช้ noise ความถี่ต่ำเลือก "ชนิดเด่นของหย่อมนี้" แล้วใช้ noise อีกตัว
# คุมความหนาแน่นภายในหย่อม สุ่มรายบล็อกเป็นแค่ตัวเขย่าขอบให้ไม่คม

# (บล็อก, สองบล็อกไหม)
TALL = {"tall_grass": True, "large_fern": True}

# หย่อมดอกไม้ — แต่ละหย่อมเป็นดอกชนิดเดียว เหมือน flower forest ของวานิลลา
FLOWERS_LOW = ["oxeye_daisy", "cornflower", "poppy", "dandelion",
               "azure_bluet", "allium", "white_tulip", "orange_tulip"]
FLOWERS_ALPINE = ["oxeye_daisy", "azure_bluet", "dandelion", "cornflower"]


def ground_cover(zone, r, patch_a, patch_b, patch_c, damp, dense, elev):
    """เลือกพืชหนึ่งบล็อกสำหรับตำแหน่งนี้ คืน (ชื่อบล็อก, สองบล็อกไหม) หรือ None

    zone     'forest' | 'meadow' | 'alpine' | 'wetland'
    r        สุ่มรายบล็อก 0..1
    patch_*  noise ความถี่ต่างกัน 0..1 ใช้แบ่งหย่อม
    damp     ความชื้น 0..1
    dense    ความทึบเรือนยอด 0..1 (0 = โล่ง)
    """
    if zone == "wetland":
        if r < 0.55:
            return ("short_grass", False)
        if r < 0.62:
            return ("tall_grass", True)
        return None

    if zone == "alpine":
        # เหนือแนวไม้ — โล่ง ลมแรง พืชเตี้ยและห่าง
        cover = 0.16 + 0.30 * patch_c
        if r > cover:
            return None
        if patch_a > 0.72 and r < cover * 0.30:
            return (FLOWERS_ALPINE[int(patch_b * len(FLOWERS_ALPINE)) %
                                   len(FLOWERS_ALPINE)], False)
        if damp > 0.62 and r < cover * 0.45:
            return ("fern", False)
        return ("short_grass", False)

    if zone == "forest":
        # ใต้เรือนยอดทึบ แสงน้อย: เฟิร์นกับเห็ดชนะหญ้า
        cover = 0.30 + 0.45 * patch_c
        if r > cover:
            return None
        shade = dense
        # เห็ดเฉพาะที่ชื้นและร่มจริงๆ ไม่ใช่โรยทั่วป่า
        if shade > 0.55 and damp > 0.66 and r < cover * 0.06:
            return ("red_mushroom" if patch_b > 0.5 else "brown_mushroom", False)
        # ผืนมอสในป่าชื้น — carpet เกาะพื้นดูนุ่มกว่าปล่อยพื้นโล่ง
        if damp > 0.62 and patch_b > 0.62 and r < cover * 0.30:
            return ("moss_carpet", False)
        # พุ่มเบอร์รี่ในป่าสน (โซน taiga จริง) เป็นหย่อมเล็กๆ
        if elev > 900 and patch_a > 0.78 and r < cover * 0.10:
            return ("sweet_berry_bush", False)
        if damp > 0.55 and patch_a > 0.55:
            if r < cover * 0.22:
                return ("large_fern", True)
            return ("fern", False)
        if shade < 0.35 and patch_a < 0.40 and r < cover * 0.30:
            return ("tall_grass", True)
        return ("short_grass", False)

    # meadow
    cover = 0.38 + 0.42 * patch_c
    if r > cover:
        return None
    if patch_a > 0.66:
        # หย่อมดอกไม้ — ชนิดเดียวทั้งหย่อม
        if r < cover * 0.32:
            i = int(patch_b * len(FLOWERS_LOW)) % len(FLOWERS_LOW)
            return (FLOWERS_LOW[i], False)
    if patch_a < 0.30 and r < cover * 0.34:
        return ("tall_grass", True)
    if damp > 0.68 and r < cover * 0.18:
        return ("fern", False)
    return ("short_grass", False)


# ============================================================ ของตกแต่งแบบ Geophilic
def fallen_log(rng, wood):
    """ต้นไม้ล้ม — ท่อนนอนยาว 3-6 บล็อก axis ตามแนวที่ล้ม"""
    n = int(rng.integers(3, 7))
    axis = "x" if rng.random() < 0.5 else "z"
    out = []
    for i in range(n):
        dx, dz = (i, 0) if axis == "x" else (0, i)
        out.append((dx, 0, dz, f"{wood}_log", {"axis": axis}))
    return out


def stump(rng, wood):
    """ตอไม้ — ท่อนตั้งสูง 1-2 บล็อก"""
    h = int(rng.integers(1, 3))
    return [(0, y, 0, f"{wood}_log", {"axis": "y"}) for y in range(h)]


def bush(rng, wood):
    """พุ่มไม้เตี้ย — ใบ 3x3 มุมแหว่ง สูง 1-2 บล็อก มีท่อนกลางถ้าใหญ่"""
    out = []
    tall = rng.random() < 0.4
    for dx in range(-1, 2):
        for dz in range(-1, 2):
            if abs(dx) + abs(dz) == 2 and rng.random() < 0.5:
                continue
            out.append((dx, 0, dz, f"{wood}_leaves",
                        {"persistent": "true", "distance": "7"}))
    out.append((0, 1, 0, f"{wood}_leaves",
                {"persistent": "true", "distance": "7"}))
    if tall:
        out.append((0, 0, 0, f"{wood}_log", {"axis": "y"}))
    return out


def boulder(rng, mossy):
    """ก้อนหิน — ก้อนกลมๆ รัศมี 1-2"""
    rad = 1 if rng.random() < 0.65 else 2
    pal = (["mossy_cobblestone", "cobblestone", "stone", "moss_block"] if mossy
           else ["stone", "cobblestone", "andesite", "gravel"])
    out = []
    for dx in range(-rad, rad + 1):
        for dy in range(0, rad + 1):
            for dz in range(-rad, rad + 1):
                if dx * dx + dy * dy + dz * dz > rad * rad + rad:
                    continue
                out.append((dx, dy, dz, pal[int(rng.integers(len(pal)))], {}))
    return out
