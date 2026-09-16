import unittest
import numpy as np

import hydrology_shape as H


class ProfileDirectionTests(unittest.TestCase):
    def profile(self, terrain, reverse=False):
        x = np.asarray([4, terrain.shape[1] - 5], dtype=np.float32)
        if reverse:
            x = x[::-1]
        sources = {
            "points_x": x, "points_z": np.full(2, 12, dtype=np.float32),
            "offsets": np.asarray([0, 2]), "kind": np.asarray([3]),
            "width_m": np.asarray([4.0]),
        }
        return H.line_profile_entries(
            sources, 0, terrain, np.asarray([50.0]),
            waterbody=np.zeros(terrain.shape, dtype=bool),
            slope_field=H.hillside_rise_per_block(terrain, minimum=0.0),
        )[0]

    def test_uphill_source_order_does_not_excavate_a_flat_canyon(self):
        terrain = np.tile((77 + np.arange(64) // 2).astype(np.int16), (24, 1))
        uphill = self.profile(terrain)
        downhill = self.profile(terrain, reverse=True)
        self.assertGreater(int(uphill["x"][0]), int(uphill["x"][-1]))
        np.testing.assert_array_equal(uphill["x"], downhill["x"])
        np.testing.assert_array_equal(uphill["stage"], downhill["stage"])
        raw = terrain[uphill["z"], uphill["x"]].astype(np.int32)
        self.assertLessEqual(int((raw - uphill["stage"]).max()), 3)
        self.assertTrue(np.all(np.diff(uphill["stage"].astype(np.int32)) <= 0))

    def test_single_endpoint_outlier_does_not_reverse_a_downhill_reach(self):
        terrain = np.tile((110 - np.arange(64) // 2).astype(np.int16), (24, 1))
        terrain[:, 59] = 130
        result = self.profile(terrain)
        self.assertFalse(result["source_reversed"])
        self.assertLess(int(result["x"][0]), int(result["x"][-1]))

    def test_flat_reach_retains_source_direction(self):
        terrain = np.full((24, 64), 77, dtype=np.int16)
        forward, backward = self.profile(terrain), self.profile(terrain, reverse=True)
        self.assertFalse(forward["source_reversed"])
        self.assertFalse(backward["source_reversed"])
        self.assertLess(int(forward["x"][0]), int(forward["x"][-1]))
        self.assertGreater(int(backward["x"][0]), int(backward["x"][-1]))

    def test_pool_rounding_cannot_turn_a_gentle_dem_edge_into_a_waterfall(self):
        terrain = np.tile((240 - 2 * np.arange(64)).astype(np.int16), (24, 1))
        result = self.profile(terrain)
        drops = -np.diff(result["stage"].astype(np.int32))
        self.assertLessEqual(int(drops.max()), H.UNSUPPORTED_MAX_STEP)
        raw = terrain[result["z"], result["x"]].astype(np.int32)
        self.assertLessEqual(int((raw - result["stage"]).max()), 3)

    def test_lake_pinning_retains_the_dem_edge_limit(self):
        profile = {"x": np.arange(8), "z": np.zeros(8, dtype=int),
                   "stage": np.asarray([30, 28, 26, 24, 22, 20, 18, 16]),
                   "edge_drop": np.full(7, 2)}
        standing = np.full((1, 8), H.UNRESOLVED, dtype=np.int16)
        standing[0, 0] = 34
        pinned = H.pin_profile_to_standing_water(profile, standing, search_radius=0)
        self.assertEqual(int(pinned["stage"][0]), 34)
        self.assertLessEqual(int((-np.diff(pinned["stage"].astype(int))).max()), 2)

    def test_real_dem_cliff_remains_a_waterfall(self):
        terrain = np.full((24, 64), 100, dtype=np.int16)
        terrain[:, 32:] = 90
        result = self.profile(terrain)
        raw = terrain[result["z"], result["x"]].astype(np.int32)
        cliff = np.flatnonzero(-np.diff(raw) >= H.WATERFALL_TERRAIN_DROP)
        self.assertEqual(len(cliff), 1)
        self.assertGreater(int(-np.diff(result["stage"].astype(int))[cliff[0]]), 2)


if __name__ == "__main__":
    unittest.main()
