import unittest

import numpy as np

try:
    from shapely.geometry import LineString, MultiLineString
    import fetch_landcover as F
except ImportError:
    LineString = MultiLineString = F = None


@unittest.skipUnless(F is not None, "fetch-landcover dependencies not installed")
class WaterSourcePackingTests(unittest.TestCase):
    def test_preserves_directed_linestring_order_in_world_coordinates(self):
        line = LineString([(100.0, 300.0), (108.0, 296.0), (116.0, 288.0)])

        packed = F.pack_ordered_waterways(
            [(line, "stream", 4.0)],
            minx=100.0,
            maxy=300.0,
            resolution=4.0,
        )

        np.testing.assert_array_equal(packed["offsets"], [0, 3])
        np.testing.assert_allclose(packed["points_x"], [0.0, 2.0, 4.0])
        np.testing.assert_allclose(packed["points_z"], [0.0, 1.0, 3.0])
        self.assertEqual(int(packed["kind"][0]),
                         F.WATERWAY_KIND_CODE["stream"])
        self.assertEqual(float(packed["width_m"][0]), 4.0)

    def test_multiline_parts_remain_separate_directed_features(self):
        geometry = MultiLineString([
            [(0.0, 10.0), (4.0, 10.0)],
            [(8.0, 6.0), (8.0, 2.0)],
        ])

        packed = F.pack_ordered_waterways(
            [(geometry, "river", 12.0)],
            minx=0.0,
            maxy=10.0,
            resolution=2.0,
        )

        np.testing.assert_array_equal(packed["offsets"], [0, 2, 4])
        np.testing.assert_array_equal(
            packed["kind"],
            [F.WATERWAY_KIND_CODE["river"], F.WATERWAY_KIND_CODE["river"]],
        )


if __name__ == "__main__":
    unittest.main()
