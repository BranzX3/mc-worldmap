import unittest

import numpy as np

import make_water_levels as W


class WaterLevelTests(unittest.TestCase):
    def test_component_mode_flattens_core_and_propagates_shelf(self):
        y = np.full((15, 15), 20, dtype=np.int16)
        water = np.zeros(y.shape, dtype=bool)
        water[3:12, 3:12] = True
        depth = np.zeros(y.shape, dtype=np.uint8)
        depth[3:12, 3:12] = 1
        depth[5:10, 5:10] = 4
        y[5:10, 5:10] = 30
        y[7, 7] = 29
        y[8, 8] = 31

        levels, lake = W.global_water_surface_levels(
            y, water, depth, level_offset=1, shelf_search=4
        )

        self.assertTrue((levels[depth >= 4] == 31).all())
        self.assertTrue(lake[water].all())
        self.assertTrue((levels[~water] == W.UNRESOLVED).all())

    def test_shallow_stream_far_from_lake_keeps_terrain_profile(self):
        y = np.full((20, 20), 50, dtype=np.int16)
        water = np.zeros(y.shape, dtype=bool)
        water[2:7, 2:7] = True
        water[15, 10:18] = True
        depth = water.astype(np.uint8)
        depth[3:6, 3:6] = 4
        y[15, 10:18] = np.arange(48, 40, -1, dtype=np.int16)

        levels, lake = W.global_water_surface_levels(
            y, water, depth, level_offset=1, shelf_search=3
        )

        np.testing.assert_array_equal(levels[15, 10:18], y[15, 10:18])
        self.assertFalse(lake[15, 10:18].any())

    def test_separate_lakes_choose_separate_modes(self):
        y = np.zeros((12, 20), dtype=np.int16)
        water = np.zeros(y.shape, dtype=bool)
        water[2:10, 2:8] = True
        water[2:10, 12:18] = True
        depth = np.where(water, 4, 0).astype(np.uint8)
        y[water] = 20
        y[:, 12:18] = 40

        levels, _lake = W.global_water_surface_levels(
            y, water, depth, level_offset=0, shelf_search=0
        )

        self.assertTrue((levels[2:10, 2:8] == 20).all())
        self.assertTrue((levels[2:10, 12:18] == 40).all())

    def test_validation_rejects_missing_and_land_levels(self):
        water = np.asarray([[True, False], [False, True]])
        levels = np.asarray(
            [[W.UNRESOLVED, 12], [W.UNRESOLVED, 9]], dtype=np.int16
        )

        result = W.validate_water_levels(levels, water)

        self.assertEqual(result["water_without_level"], 1)
        self.assertEqual(result["land_with_level"], 1)

    def test_stream_priority_flood_raises_internal_pit_toward_outlet(self):
        levels = np.full((7, 11), W.UNRESOLVED, dtype=np.int16)
        water = np.zeros(levels.shape, dtype=bool)
        water[3, 1:10] = True
        levels[3, 1:10] = [10, 13, 12, 11, 14, 15, 14, 16, 17]
        lake = np.zeros(levels.shape, dtype=bool)
        ground = np.full(levels.shape, 100, dtype=np.int16)
        ground[water] = levels[water]
        ground[2, 1] = 9

        got, outlets = W.condition_stream_levels(
            levels, water, lake, ground_y=ground
        )

        self.assertEqual(int(outlets.sum()), 1)
        self.assertTrue(outlets[3, 1])
        self.assertEqual(list(map(int, got[3, 1:10])),
                         [10, 13, 13, 13, 14, 15, 15, 16, 17])

    def test_flat_stream_has_one_explicit_outlet(self):
        levels = np.full((5, 8), W.UNRESOLVED, dtype=np.int16)
        water = np.zeros(levels.shape, dtype=bool)
        water[2, 2:6] = True
        levels[water] = 20

        got, outlets = W.condition_stream_levels(
            levels, water, np.zeros_like(water)
        )

        np.testing.assert_array_equal(got, levels)
        self.assertEqual(int(outlets.sum()), 1)


if __name__ == "__main__":
    unittest.main()
