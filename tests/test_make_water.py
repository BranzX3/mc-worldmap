import unittest

import numpy as np

import make_water as W


class MakeWaterTests(unittest.TestCase):
    def test_depth_profile_keeps_shore_shallow_and_center_deep(self):
        water = np.zeros((257, 257), dtype=bool)
        water[16:-16, 16:-16] = True

        depth = W.depth_from_water_mask(
            water, max_depth_blocks=30, shelf=26.0
        )

        self.assertEqual(int(depth[16, 128]), 1)
        self.assertGreaterEqual(int(depth[128, 128]), 25)
        self.assertLessEqual(int(depth.max()), 30)
        self.assertEqual(int(depth[0, 0]), 0)

    def test_chamfer_distance_matches_closed_form(self):
        """ระยะต้องเท่ากับนิยาม chamfer 3-4 ถึง cell ที่ไม่ใช่ mask ที่ใกล้สุด

        การแพร่แนวนอนในแต่ละแถวถูกเขียนเป็น prefix-minimum
        (`np.minimum.accumulate`) แทนลูป Python ต่อ cell สูตรปิดนี้จึงเป็นตัวยืนยัน
        ว่านิยามของระยะไม่ได้เปลี่ยนไป ไม่ใช่แค่ "ผลเหมือนโค้ดเดิม"
        """
        rng = np.random.default_rng(4242)
        height, width = 13, 15
        rows = np.arange(height)[:, None, None]
        cols = np.arange(width)[None, :, None]
        for _ in range(12):
            mask = rng.random((height, width)) < 0.7
            if not (~mask).any():
                mask[0, 0] = False

            got = W.chamfer_distance(mask)

            zz, xx = np.nonzero(~mask)
            dz = np.abs(rows - zz[None, None, :])
            dx = np.abs(cols - xx[None, None, :])
            steps = 3 * np.maximum(dz, dx) + np.minimum(dz, dx)
            np.testing.assert_allclose(got, steps.min(axis=2) / 3.0, atol=1e-6)

    def test_underwater_relief_is_tile_invariant(self):
        water = np.ones((96, 112), dtype=bool)
        depth = np.full(water.shape, 18, dtype=np.uint8)

        a = W.add_underwater_relief(depth, water, tile_size=96)
        b = W.add_underwater_relief(depth, water, tile_size=23)

        np.testing.assert_array_equal(a, b)
        self.assertGreater(len(np.unique(a)), 3)

    def test_underwater_relief_preserves_shallow_shelf(self):
        depth = np.tile(np.arange(1, 8, dtype=np.uint8), (32, 1))
        water = np.ones_like(depth, dtype=bool)

        got = W.add_underwater_relief(depth, water, tile_size=11)

        np.testing.assert_array_equal(got[:, :2], depth[:, :2])

    def test_lake_edge_naturalization_preserves_narrow_stream(self):
        water = np.zeros((96, 96), dtype=bool)
        water[20:76, 20:76] = True
        water[8:20, 47:49] = True
        preliminary = W.base_depth_from_water_mask(water)

        got = W.naturalize_lake_edges(water, preliminary, tile_size=23)

        self.assertTrue(got[8:18, 47:49].all())
        self.assertTrue((got != water).any())
        self.assertTrue(got[preliminary >= 3].all())

    def test_lake_edge_naturalization_is_tile_invariant(self):
        water = np.zeros((91, 107), dtype=bool)
        water[12:80, 15:94] = True
        preliminary = W.base_depth_from_water_mask(water)

        a = W.naturalize_lake_edges(water, preliminary, tile_size=91)
        b = W.naturalize_lake_edges(water, preliminary, tile_size=19)

        np.testing.assert_array_equal(a, b)

    def test_underwater_relief_contains_sparse_cliffs(self):
        water = np.ones((256, 256), dtype=bool)
        depth = np.full(water.shape, 18, dtype=np.uint8)

        got = W.add_underwater_relief(depth, water, tile_size=64)
        steps = np.concatenate([
            np.abs(np.diff(got.astype(np.int16), axis=0)).ravel(),
            np.abs(np.diff(got.astype(np.int16), axis=1)).ravel(),
        ])

        self.assertGreater(int((steps >= 3).sum()), 0)
        self.assertLessEqual(int(got.max()), 30)


if __name__ == "__main__":
    unittest.main()
