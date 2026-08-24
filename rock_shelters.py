"""โพรงหินที่ผิว — เพิงผา ชะโงก และซอกหลบฝนบนหน้าผา

ทำไมต้องมี: โลกที่สร้างจาก heightmap เป็น 2.5D โดยนิยาม — `build_terrain` ถม
`solid = ys <= tile` ทุกคอลัมน์ ผาสูง 36 บล็อกทุกลูกจึงเป็น **ผนังตันเรียบ**
ไม่มีที่ว่างภายในเลยสักลูกบาศก์ ผู้เล่นเดินเลียบผาแล้วไม่มีอะไรให้เข้าไป

ไฟล์นี้ทำเฉพาะ **โพรงที่ผิว** ไม่ใช่ระบบถ้ำใต้ดิน: เจาะเข้าไปในหน้าผาที่เปิด
ออกด้านข้างอยู่แล้ว ลึกไม่เกินไม่กี่บล็อก มองเห็นจากข้างนอก เดินเข้าไปหลบได้
ไม่เชื่อมกันเป็นเครือข่าย

**กฎความปลอดภัยสามข้อ ที่ทำให้มันแตะของเดิมไม่ได้เลย**

1. เจาะได้เฉพาะที่มี *หน้าผาจริง* อยู่แล้ว — คอลัมน์ข้าง ๆ ต้องต่ำกว่าอย่างน้อย
   `MIN_FACE` บล็อก  ไม่ใช่เจาะกลางเนินแล้วได้หลุมโผล่บนสันเขา
2. เว้นเพดานใต้ผิวอย่างน้อย `ROOF` บล็อก — ผิวดินที่ painter ทาไว้ไม่ถูกแตะ และ
   ไม่มีรูทะลุขึ้นข้างบน
3. พื้นโพรงต้องสูงกว่ายอดคอลัมน์ที่เปิดออกอย่างน้อย `FLOOR` บล็อก — น้ำที่อยู่
   ข้างล่าง (ลำธาร ทะเลสาบ) จึงไหลเข้าไม่ได้แม้ระดับน้ำจะอยู่ที่ยอดคอลัมน์นั้นพอดี

ทั้งสามข้อวัดได้จาก heightmap อย่างเดียว ไม่ต้องรู้จักน้ำหรือวัสดุ จึงไม่มีทาง
ขัดกับระบบอื่นที่ตัดสินทีหลัง
"""

import numpy as np

# หน้าผาต้องสูงอย่างน้อยเท่านี้ถึงจะมีโพรงได้ (บล็อก)
MIN_FACE = 6
# เว้นเพดานใต้ผิวกี่บล็อก
ROOF = 3
# พื้นโพรงอยู่เหนือยอดคอลัมน์ที่เปิดออกกี่บล็อก
FLOOR = 2
# เจาะลึกเข้าไปในผาได้ไม่เกินกี่บล็อก
MAX_DEPTH = 3
# ความสูงของโพรงหนึ่งอัน
MAX_HEIGHT = 3
# สัดส่วนของหน้าผาที่มีโพรง — สูงกว่านี้ผาจะกลายเป็นรังผึ้ง
SHELTER_SHARE = 0.18
# ต้องมี halo เท่านี้รอบกรอบที่จะเขียน (ใช้หาว่าข้าง ๆ ต่ำลงไปแค่ไหน)
HALO = 3


def _hash01(x, z, salt):
    """สุ่ม 0..1 จากพิกัดโลก — ต้องเป็นฟังก์ชันของพิกัด ไม่ใช่ RNG ตามลำดับ
    การเขียน ไม่งั้นโพรงจะขยับตำแหน่งเมื่อ build ใหม่ทีละ region
    """
    h = (
        x.astype(np.int64) * 374761393
        + z.astype(np.int64) * 668265263
        + int(salt) * 1442695041
    ) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / float(0xFFFFFF)


def face_exposure(height, reach=HALO):
    """ยอดคอลัมน์ที่ต่ำที่สุดในรัศมี ``reach`` — "ข้าง ๆ ต่ำลงไปแค่ไหน"

    ต้องดูไกลกว่าเพื่อนบ้านที่ติดกันหนึ่งช่อง: DEM 4 m/px ถูกเฉลี่ยจนผาจริงลาด
    ลงภายในสองสามคอลัมน์แทนที่จะดิ่งในช่องเดียว  วัดจากโลกที่ build แล้วด้วย
    รัศมี 1 ได้โพรงแค่ 0.1% ของคอลัมน์ ทั้งที่แพตช์นั้นคือหน้าผาชั้นหิน
    ผาที่ลง 6 บล็อกใน 3 คอลัมน์ = 24 m ใน 12 m = 63 องศา ซึ่งเป็นผาเต็มตัว

    ``height`` ต้องมี halo อย่างน้อย ``reach`` บล็อกรอบด้าน
    """
    h = np.asarray(height, dtype=np.int32)
    reach = int(reach)
    inner = h[reach:-reach, reach:-reach]
    nx, nz = inner.shape
    lowest = np.full(inner.shape, np.iinfo(np.int32).max, dtype=np.int32)
    for dx in range(-reach, reach + 1):
        for dz in range(-reach, reach + 1):
            if dx == 0 and dz == 0:
                continue
            if abs(dx) + abs(dz) > reach:      # แนวทแยงไกลไม่นับ
                continue
            window = h[reach + dx:reach + dx + nx, reach + dz:reach + dz + nz]
            lowest = np.minimum(lowest, window)
    return inner, lowest


def shelter_volume(height, ys, x0, z0, salt=4177):
    """คืน mask สามมิติ [x, y, z] ของบล็อกที่ต้องกลายเป็นอากาศ

    ``height`` คือความสูงผิวดินพร้อม halo ``HALO`` บล็อก
    ``ys`` คือช่วง y ที่กำลังเขียน (เรียงจากน้อยไปมาก)
    """
    h = np.asarray(height, dtype=np.int32)
    inner, lowest = face_exposure(h)
    ys = np.asarray(ys, dtype=np.int32)
    nx, nz = inner.shape
    out = np.zeros((nx, ys.size, nz), dtype=bool)

    face = inner - lowest
    wx = np.arange(x0, x0 + nx, dtype=np.int64)[:, None]
    wz = np.arange(z0, z0 + nz, dtype=np.int64)[None, :]
    roll = _hash01(wx, wz, salt)
    seed = (face >= MIN_FACE) & (roll < SHELTER_SHARE)
    if not seed.any():
        return out

    # ความสูงและความลึกของโพรงแต่ละอัน — สุ่มจากพิกัดเช่นกัน
    height_roll = _hash01(wx, wz, salt + 1)
    depth_roll = _hash01(wx, wz, salt + 2)
    hollow_h = 1 + np.rint(height_roll * (MAX_HEIGHT - 1)).astype(np.int32)
    hollow_d = 1 + np.rint(depth_roll * (MAX_DEPTH - 1)).astype(np.int32)

    # พื้นโพรงวางเหนือยอดที่เปิดออก แล้วจำกัดด้วยเพดานใต้ผิว
    floor_y = lowest + FLOOR
    ceil_y = inner - ROOF
    top_y = np.minimum(floor_y + hollow_h - 1, ceil_y)
    # ``face_exposure`` may find a low column up to HALO blocks away.  That is
    # enough to recognise a smoothed DEM cliff, but not enough to prove that a
    # hollow at this column opens to air: the columns in between may still be
    # solid at ``floor_y``.  Require cardinally adjacent natural air at the
    # floor before treating a seed as a mouth.  Every deeper cell is stamped
    # from this mouth, so this condition makes the whole shelter reachable.
    open_side = np.zeros((nx, nz), dtype=bool)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        neighbour = h[
            HALO + dx:HALO + dx + nx,
            HALO + dz:HALO + dz + nz,
        ]
        open_side |= neighbour < floor_y
    usable = seed & (top_y >= floor_y) & open_side
    if not usable.any():
        return out

    # เจาะเข้าไปในผาตามความลึก: cell ที่อยู่ห่างจากหน้าผาไม่เกิน hollow_d
    # ใช้การแผ่จาก seed ไปยังคอลัมน์ที่ *สูงกว่า* เท่านั้น เพื่อไม่ให้ทะลุออก
    # อีกด้านของสัน
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        for step in range(0, MAX_DEPTH):
            src = np.zeros((nx, nz), dtype=bool)
            sx = slice(max(0, -dx * step), nx - max(0, dx * step))
            sz = slice(max(0, -dz * step), nz - max(0, dz * step))
            tx = slice(max(0, dx * step), nx - max(0, -dx * step))
            tz = slice(max(0, dz * step), nz - max(0, -dz * step))
            src[tx, tz] = (
                usable[sx, sz]
                & (step < hollow_d[sx, sz])
                & (inner[tx, tz] >= inner[sx, sz])
            )
            if not src.any():
                continue
            # ช่วงความสูงต้องเป็นของ **คอลัมน์ปากโพรง** ไม่ใช่ของคอลัมน์ปลายทาง
            # ถ้าใช้ของปลายทาง cell ที่อยู่ลึกเข้าไปจะถูกเจาะคนละระดับกับปาก
            # แล้วกลายเป็นช่องว่างปิดตายที่ไม่มีใครเห็น (วัดจากโลกจริงได้ 35 จาก
            # 84 คอลัมน์ที่มีโพรงเป็นแบบนั้น)
            seed_floor = np.zeros((nx, nz), dtype=np.int32)
            seed_top = np.full((nx, nz), -1, dtype=np.int32)
            seed_floor[tx, tz] = floor_y[sx, sz]
            seed_top[tx, tz] = top_y[sx, sz]
            lo = np.where(src, seed_floor, 0)
            hi = np.where(src, seed_top, -1)
            band = (
                (ys[None, :, None] >= lo[:, None, :])
                & (ys[None, :, None] <= hi[:, None, :])
            )
            out |= band & src[:, None, :]
    return out
