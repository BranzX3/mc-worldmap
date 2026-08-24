import unittest

import numpy as np

import check_world_water as W


class MaterialSpatialMetricTests(unittest.TestCase):
    def test_reports_connected_dominance_instead_of_histogram_only(self):
        materials = np.asarray([
            ["stone", "stone", "gravel"],
            ["stone", "gravel", "gravel"],
        ], dtype=object)
        mask = np.ones(materials.shape, dtype=bool)

        got = W.material_spatial_metrics(materials, mask)

        self.assertEqual(got["counts"], {"stone": 3, "gravel": 3})
        self.assertEqual(got["largest_components"]["stone"]["cells"], 3)
        self.assertEqual(got["largest_components"]["gravel"]["cells"], 3)
        # Seven horizontal/vertical neighbour pairs, four equal.
        self.assertAlmostEqual(got["same_neighbour_share"], 4 / 7)

    def test_mask_excludes_dry_cells_from_material_metrics(self):
        materials = np.asarray([
            ["stone", "dirt"],
            ["stone", "dirt"],
        ], dtype=object)
        mask = np.asarray([[True, False], [True, False]])

        got = W.material_spatial_metrics(materials, mask)

        self.assertEqual(got["counts"], {"stone": 2})
        self.assertEqual(got["largest_components"]["stone"]["cells"], 2)
        self.assertEqual(got["same_neighbour_share"], 1.0)

    def test_empty_mask_has_a_stable_zero_report(self):
        got = W.material_spatial_metrics(
            np.full((2, 2), "", dtype=object), np.zeros((2, 2), dtype=bool)
        )

        self.assertEqual(got["cells"], 0)
        self.assertEqual(got["counts"], {})
        self.assertEqual(got["same_neighbour_share"], 0.0)


class FluidOccupantTests(unittest.TestCase):
    def test_submerged_plants_count_as_intentional_fluid_occupants(self):
        self.assertIn("water", W.FLUID_OCCUPANTS)
        self.assertIn("seagrass", W.FLUID_OCCUPANTS)
        self.assertIn("tall_seagrass", W.FLUID_OCCUPANTS)
        self.assertNotIn("lily_pad", W.FLUID_OCCUPANTS)


if __name__ == "__main__":
    unittest.main()
