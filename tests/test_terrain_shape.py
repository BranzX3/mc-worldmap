import unittest

import numpy as np

import terrain_shape as T


class DitherFieldTests(unittest.TestCase):
    def test_dither_is_world_coordinate_invariant(self):
        """build กับ paint ทำคนละกรอบ ต้องได้ค่าเดียวกันที่พิกัดเดียวกัน"""
        full = T.dither_field((96, 96), x0=4000, z0=7000)
        part = T.dither_field((32, 32), x0=4032, z0=7032)
        np.testing.assert_allclose(full[32:64, 32:64], part, atol=1e-5)

    def test_dither_never_exceeds_half_a_block(self):
        """เกินครึ่งบล็อกเมื่อไหร่ ระดับจะเพี้ยนจาก DEM เกินหนึ่งบล็อก"""
        field = T.dither_field((256, 256), x0=0, z0=0)
        self.assertLess(float(np.abs(field).max()), 0.5)

    def test_uniformised_dither_actually_moves_the_rounding(self):
        """value noise ดิบมีแค่ ~5% ที่แรงพอ ดัดแล้วต้องได้ราวครึ่งหนึ่ง"""
        field = T.dither_field((256, 256), x0=0, z0=0)
        strong = float((np.abs(field) > 0.25).mean())
        self.assertGreater(strong, 0.30)

    def test_uniformise_preserves_order(self):
        raw = np.linspace(-0.9, 0.9, 50, dtype=np.float32)
        mapped = T.uniformise(raw)
        self.assertTrue((np.diff(mapped) >= 0).all())


class DitherWeightTests(unittest.TestCase):
    def test_flat_ground_is_never_dithered(self):
        """ผิวทะเลสาบและพื้นราบต้องไม่ถูกกวนจนเป็นตุ่ม"""
        self.assertEqual(float(T.dither_weight(np.float32(0.0))), 0.0)
        self.assertEqual(float(T.dither_weight(np.float32(0.01))), 0.0)

    def test_gentle_slope_gets_full_dither(self):
        self.assertAlmostEqual(float(T.dither_weight(np.float32(0.3))), 1.0)

    def test_cliffs_are_left_alone(self):
        self.assertEqual(float(T.dither_weight(np.float32(3.0))), 0.0)


class QuantizeTests(unittest.TestCase):
    @staticmethod
    def _ramp(shape=(64, 64), slope=0.1):
        """ไหล่เขาลาดคงที่ — กรณีที่เกิดลายขั้นชัดที่สุด"""
        return np.arange(shape[1], dtype=np.float32)[None, :] * slope + np.zeros(
            shape, dtype=np.float32
        )

    def test_height_never_moves_more_than_one_block(self):
        elev = self._ramp()
        meta = {"elev_min_m": 0.0, "elev_max_m": 1000.0}
        plain = np.rint(T.elevation_to_height(elev, meta)).astype(np.int32)
        y, _sub = T.quantize(elev, meta=meta)
        self.assertLessEqual(int(np.abs(y - plain).max()), 1)

    def test_protected_cells_keep_the_undithered_level(self):
        elev = self._ramp()
        meta = {"elev_min_m": 0.0, "elev_max_m": 1000.0}
        protect = np.zeros(elev.shape, dtype=bool)
        protect[:, 20:40] = True
        plain = np.rint(T.elevation_to_height(elev, meta)).astype(np.int32)
        y, _sub = T.quantize(elev, meta=meta, protect=protect, clean=False)
        np.testing.assert_array_equal(y[:, 20:40], plain[:, 20:40])

    def test_sub_is_the_discarded_fraction(self):
        elev = self._ramp()
        meta = {"elev_min_m": 0.0, "elev_max_m": 1000.0}
        y, sub = T.quantize(elev, meta=meta, clean=False)
        self.assertLess(float(np.abs(sub).max()), 0.5001)
        # y + sub ต้องกลับไปเป็นความสูงต่อเนื่องที่ใช้ปัด
        self.assertTrue(np.isfinite(sub).all())

    def test_dither_narrows_terraces_on_a_gentle_slope(self):
        elev = self._ramp(shape=(128, 128), slope=0.06)
        meta = {"elev_min_m": 0.0, "elev_max_m": 1000.0}
        continuous = T.elevation_to_height(elev, meta)
        plain = np.rint(continuous).astype(np.int32)
        y, _ = T.quantize(elev, meta=meta)
        self.assertLess(
            T.terrace_width(y, continuous),
            T.terrace_width(plain, continuous),
        )

    def test_arete_feature_is_forwarded_only_when_requested(self):
        n = 65
        x = np.arange(n, dtype=np.float32)
        profile = 2310.0 - np.abs(x - n // 2) * 6.0
        elev = np.repeat(profile[None, :], n, axis=0)
        meta = {"elev_min_m": 0.0, "elev_max_m": 3000.0}

        plain, _ = T.quantize(
            elev, meta=meta, amplitude=0, clean=False,
            relief_features=(),
        )
        shaped, _ = T.quantize(
            elev, meta=meta, amplitude=0, clean=False,
            relief_features=("arete",),
        )

        center = n // 2
        self.assertGreater(int(shaped[0, center]), int(plain[0, center]))
        np.testing.assert_array_equal(shaped[:, :8], plain[:, :8])


class DespeckleTests(unittest.TestCase):
    def test_isolated_spike_is_removed(self):
        y = np.full((5, 5), 10, dtype=np.int32)
        y[2, 2] = 13
        cleaned, count = T.despeckle(y)
        self.assertEqual(count, 1)
        self.assertEqual(int(cleaned[2, 2]), 10)

    def test_isolated_pit_is_filled(self):
        y = np.full((5, 5), 10, dtype=np.int32)
        y[2, 2] = 7
        cleaned, count = T.despeckle(y)
        self.assertEqual(int(cleaned[2, 2]), 10)
        self.assertEqual(count, 1)

    def test_a_real_slope_is_not_flattened(self):
        y = np.arange(5, dtype=np.int32)[None, :] * np.ones((5, 1), np.int32)
        cleaned, count = T.despeckle(y)
        self.assertEqual(count, 0)
        np.testing.assert_array_equal(cleaned, y)

    def test_a_ridge_line_survives(self):
        """สันเขาไม่ใช่จุดโดด — เพื่อนบ้านไม่ได้ระดับเดียวกันหมด"""
        y = np.full((5, 5), 10, dtype=np.int32)
        y[2, :] = 12
        cleaned, count = T.despeckle(y)
        self.assertEqual(count, 0)
        np.testing.assert_array_equal(cleaned, y)


if __name__ == "__main__":
    unittest.main()
