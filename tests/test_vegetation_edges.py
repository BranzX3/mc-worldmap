import unittest

import paint_surface as P
import vegetation as V


class VegetationEdgeTests(unittest.TestCase):
    def test_tree_padding_covers_wide_schematic(self):
        schems = {"spruce": [{"width": 43}], "oak": [{"width": 9}]}
        self.assertGreaterEqual(P.schematic_tree_pad(schems), 22)

    def test_adjacent_regions_see_same_boundary_tree_candidates(self):
        pad = 22
        left = {
            (x, z, layer)
            for x, z, layer, _ in V.tree_slots(-pad, 512 + pad, 0, 512)
            if 512 - pad <= x < 512 + pad
        }
        right = {
            (x, z, layer)
            for x, z, layer, _ in V.tree_slots(512 - pad, 1024 + pad, 0, 512)
            if 512 - pad <= x < 512 + pad
        }

        self.assertTrue(left)
        self.assertSetEqual(left, right)


if __name__ == "__main__":
    unittest.main()
