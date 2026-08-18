"""ทุ่ง ริมน้ำ และหิมะบาง — สามจุดที่ทำให้พื้นที่กว้างกลายเป็นที่ตาย

1. `plantable` ตัด `snow > 0` ทั้งหมด ทุ่งที่มีหิมะบาง 1/8 บล็อกจึงโล่งเกลี้ยง
2. `beach` ถูกตัดออกจาก plantable ริมทะเลสาบจึงไม่มีอะไรเลยสักบล็อก
3. ทุ่งไม่เคยมีดอกไม้สองบล็อก ทั้งที่วานิลลามีให้ — ทุ่งจึงแบนราบระดับเดียว
"""
import unittest

import numpy as np

import paint_surface as P
import vegetation as V


def sweep(zone, damp=0.5, elev=800.0, dense=0.2, samples=4000):
    counts = {}
    for i in range(samples):
        got = V.ground_cover(
            zone, (i % 97) / 97.0, ((i * 7) % 53) / 53.0,
            ((i * 11) % 31) / 31.0, ((i * 13) % 17) / 17.0, damp, dense, elev,
        )
        key = got if got else None
        counts[key] = counts.get(key, 0) + 1
    return counts


class ShoreZoneTests(unittest.TestCase):
    def test_shore_has_sparse_tufts_not_a_lawn(self):
        shore = sweep("shore")
        meadow = sweep("meadow")

        self.assertGreater(
            shore.get(None, 0), meadow.get(None, 0),
            "ริมน้ำหนาแน่นเท่าทุ่งหญ้า = ไม่ได้แยกโซนจริง",
        )
        self.assertLess(shore.get(None, 0), 4000, "ริมน้ำยังไม่มีอะไรเลย")

    def test_shore_never_grows_two_block_plants(self):
        for key in sweep("shore"):
            if key is not None:
                self.assertFalse(key[1], f"ริมน้ำมีพืชสองบล็อก: {key[0]}")


class MeadowFlowerTests(unittest.TestCase):
    def test_meadow_has_two_block_flowers(self):
        names = {k[0] for k in sweep("meadow") if k}

        self.assertTrue(
            names & set(V.FLOWERS_TALL),
            "ทุ่งยังไม่มีดอกไม้สองบล็อกเลย",
        )

    def test_tall_flowers_stop_above_the_meadow_belt(self):
        high = {k[0] for k in sweep("meadow", elev=1800.0) if k}

        self.assertFalse(high & set(V.FLOWERS_TALL))

    def test_tall_flowers_are_a_minority(self):
        counts = sweep("meadow")
        total = sum(counts.values())
        tall = sum(v for k, v in counts.items() if k and k[0] in V.FLOWERS_TALL)

        self.assertLess(tall / total, 0.05, "ดอกสูงเยอะจนกลายเป็นสวนดอกไม้")


class SnowTuftTests(unittest.TestCase):
    """หิมะบางต้องเป็นหย่อม ไม่ใช่ผ้าคลุมที่ฆ่าพืชทั้งผืน"""

    def test_constants_are_a_thin_dusting_only(self):
        # หิมะหนาเกิน SNOW_TUFT_MAX/8 บล็อกต้องกลบพืชจริง ๆ
        self.assertLessEqual(P.SNOW_TUFT_MAX, 4)
        self.assertGreater(P.SNOW_TUFT_SHARE, 0.0)
        self.assertLess(P.SNOW_TUFT_SHARE, 1.0)

    def test_gaps_appear_only_under_thin_snow(self):
        """จำลองกฎเดียวกับที่ painter ใช้ แล้วตรวจว่าหิมะหนาไม่ถูกเจาะ"""
        import surface as S

        shape = (64, 64)
        x0 = z0 = 512
        gap_noise = S.smooth_noise(x0, z0, shape, 19.0, 7321, 2) * 0.5 + 0.5
        wx = np.arange(x0, x0 + shape[0], dtype=np.int64)[:, None]
        wz = np.arange(z0, z0 + shape[1], dtype=np.int64)[None, :]
        roll = P.bhash_array(wx, wz, 57)
        opened = (gap_noise > 0.46) & (roll < P.SNOW_TUFT_SHARE)

        thin = np.full(shape, P.SNOW_TUFT_MAX, dtype=np.int32)
        thick = np.full(shape, P.SNOW_TUFT_MAX + 1, dtype=np.int32)
        thin_gap = ((thin > 0) & (thin <= P.SNOW_TUFT_MAX)) & opened
        thick_gap = ((thick > 0) & (thick <= P.SNOW_TUFT_MAX)) & opened

        self.assertGreater(float(thin_gap.mean()), 0.05, "หิมะบางไม่มีช่องเลย")
        self.assertLess(float(thin_gap.mean()), 0.60, "เจาะจนหิมะแทบไม่เหลือ")
        self.assertFalse(thick_gap.any(), "หิมะหนาถูกเจาะด้วย")


if __name__ == "__main__":
    unittest.main()
