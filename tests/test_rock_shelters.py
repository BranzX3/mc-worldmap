"""โพรงผิว — ต้องเห็นจากนอก และต้องแตะของเดิมไม่ได้เลย

โลกนี้เป็น 2.5D จาก heightmap: `build_terrain` ถมทุกคอลัมน์ตัน ผาทุกลูกจึงเป็น
ผนังเรียบ  เทสต์นี้ตรึงกฎความปลอดภัยสามข้อที่ทำให้การเจาะโพรงไม่ไปพังอย่างอื่น
"""
import unittest

import numpy as np

import rock_shelters as RS


def cliff(nx=24, nz=24, low=60, high=90, face_at=12):
    """ผาตรง: ครึ่งหนึ่งต่ำ ครึ่งหนึ่งสูง — มี halo `RS.HALO` รอบด้าน"""
    pad = RS.HALO
    h = np.full((nx + 2 * pad, nz + 2 * pad), low, dtype=np.int32)
    h[:, face_at + pad:] = high
    return h


class ShelterSafetyTests(unittest.TestCase):
    def carve(self, height, y_lo=None, y_hi=None, x0=1000, z0=2000):
        pad = RS.HALO
        inner = height[pad:-pad, pad:-pad]
        y_lo = int(inner.min()) - 2 if y_lo is None else y_lo
        y_hi = int(inner.max()) + 2 if y_hi is None else y_hi
        ys = np.arange(y_lo, y_hi + 1, dtype=np.int32)
        return ys, RS.shelter_volume(height, ys, x0, z0)

    def test_a_flat_plain_gets_no_holes(self):
        flat = np.full((24 + 2 * RS.HALO, 24 + 2 * RS.HALO), 70, dtype=np.int32)
        ys, vol = self.carve(flat)

        self.assertFalse(vol.any(), "เจาะหลุมกลางที่ราบ")

    def test_a_gentle_slope_gets_no_holes(self):
        size = 24 + 2 * RS.HALO
        slope = np.tile(
            np.arange(size, dtype=np.int32)[:, None] // 3 + 60, (1, size)
        )
        ys, vol = self.carve(slope)

        self.assertFalse(vol.any(), "เนินลาดไม่ใช่หน้าผา")

    def test_a_real_cliff_gets_shelters(self):
        ys, vol = self.carve(cliff())

        self.assertTrue(vol.any(), "หน้าผาจริงยังไม่มีโพรงเลย")

    def test_the_surface_block_is_never_removed(self):
        height = cliff()
        inner = height[RS.HALO:-RS.HALO, RS.HALO:-RS.HALO]
        ys, vol = self.carve(height)

        for yi, y in enumerate(ys):
            cut = vol[:, yi, :]
            if not cut.any():
                continue
            self.assertTrue(
                (y <= inner[cut] - RS.ROOF).all(),
                f"เจาะใกล้ผิวเกินไปที่ y={y}",
            )

    def test_the_floor_stays_above_the_open_side(self):
        """พื้นโพรงต้องสูงกว่ายอดคอลัมน์ที่เปิดออก ไม่งั้นน้ำข้างล่างไหลเข้า"""
        height = cliff()
        inner, lowest = RS.face_exposure(height)
        ys, vol = self.carve(height)

        for yi, y in enumerate(ys):
            cut = vol[:, yi, :]
            if cut.any():
                self.assertTrue(
                    (y >= lowest[cut] + RS.FLOOR).all(),
                    f"พื้นโพรงต่ำเกินไปที่ y={y}",
                )

    def test_every_shelter_opens_to_the_outside(self):
        """โพรงที่ไม่เชื่อมกับอากาศข้างนอกคือช่องว่างที่ไม่มีใครเห็น

        เจาะจากคอลัมน์ที่ประชิดหน้าผาเข้าไป ดังนั้น cell ชั้นแรกต้องมีอากาศอยู่
        ข้าง ๆ ในระดับเดียวกัน (คอลัมน์ที่ต่ำกว่า)
        """
        height = cliff()
        inner = height[RS.HALO:-RS.HALO, RS.HALO:-RS.HALO]
        ys, vol = self.carve(height)

        opened = 0
        for yi, y in enumerate(ys):
            cut = vol[:, yi, :]
            if not cut.any():
                continue
            # อากาศที่ระดับ y = คอลัมน์ที่ยอดต่ำกว่า y และไม่ได้ถูกเจาะ
            air = (inner < y)
            neigh = np.zeros_like(air)
            neigh[1:] |= air[:-1]
            neigh[:-1] |= air[1:]
            neigh[:, 1:] |= air[:, :-1]
            neigh[:, :-1] |= air[:, 1:]
            opened += int((cut & neigh).sum())

        self.assertGreater(opened, 0, "ไม่มีโพรงไหนเปิดออกข้างนอกเลย")

    def test_every_cut_component_reaches_safe_outside_air(self):
        """ห้ามให้ผลรวมจากหลายโพรงซ่อน component ปิดตายไว้ข้างในผา"""
        height = cliff()
        inner = height[RS.HALO:-RS.HALO, RS.HALO:-RS.HALO]
        ys, vol = self.carve(height)

        for yi, y in enumerate(ys):
            cut = vol[:, yi, :]
            if not cut.any():
                continue
            # อากาศที่ต่ำกว่าพื้นโพรงตามระยะปลอดภัยจริง ไม่ใช่แค่อากาศใด ๆ
            safe_air = inner <= y - RS.FLOOR
            adjacent = np.zeros_like(cut)
            adjacent[1:] |= safe_air[:-1]
            adjacent[:-1] |= safe_air[1:]
            adjacent[:, 1:] |= safe_air[:, :-1]
            adjacent[:, :-1] |= safe_air[:, 1:]
            reachable = cut & adjacent
            while True:
                grown = reachable.copy()
                grown[1:] |= reachable[:-1]
                grown[:-1] |= reachable[1:]
                grown[:, 1:] |= reachable[:, :-1]
                grown[:, :-1] |= reachable[:, 1:]
                grown &= cut
                if np.array_equal(grown, reachable):
                    break
                reachable = grown
            self.assertTrue(
                np.array_equal(reachable, cut),
                f"มีโพรงปิดตายหรือปากต่ำไม่ปลอดภัยที่ y={y}",
            )

    def test_shelters_do_not_eat_the_whole_face(self):
        height = cliff(nx=64, nz=64, face_at=32)
        ys, vol = self.carve(height)
        inner = height[RS.HALO:-RS.HALO, RS.HALO:-RS.HALO]
        rock = (ys[None, :, None] <= inner[:, None, :])

        self.assertLess(
            float(vol.sum()) / float(rock.sum()), 0.05,
            "เจาะจนผากลายเป็นรังผึ้ง",
        )

    def test_the_same_place_always_gets_the_same_shelters(self):
        """ต้องเป็นฟังก์ชันของพิกัดโลก ไม่งั้น build ซ้ำแล้วโพรงย้ายที่"""
        height = cliff()
        ys_a, a = self.carve(height, x0=1000, z0=2000)
        ys_b, b = self.carve(height, x0=1000, z0=2000)

        np.testing.assert_array_equal(a, b)

    def test_moving_the_window_moves_the_pattern_with_it(self):
        height = cliff()
        _ys, a = self.carve(height, x0=1000, z0=2000)
        _ys, b = self.carve(height, x0=1064, z0=2000)

        self.assertFalse(np.array_equal(a, b), "ลายซ้ำเป็นคาบตามกรอบ")


if __name__ == "__main__":
    unittest.main()
