import unittest

import numpy as np
from scipy import ndimage

import hydrology_shape as H


class HillsideBatchTests(unittest.TestCase):
    def test_batches_preserve_original_formula_at_seams_and_map_edges(self):
        rng = np.random.default_rng(912)
        terrain = rng.integers(0, 320, size=(43, 35), dtype=np.int16)
        terrain.setflags(write=False)
        gz, gx = np.gradient(terrain.astype(np.float32))
        original = np.hypot(gz, gx)
        for smooth in (0, 1, 2, 3, 4, 7):
            expected = original
            if smooth > 1:
                expected = ndimage.uniform_filter(expected, size=smooth, mode="nearest")
            expected = np.clip(expected, 0.0, 300.0)
            for batch in (1, 7, 256):
                with self.subTest(smooth=smooth, batch=batch):
                    got = H.hillside_rise_per_block(
                        terrain, minimum=0.0, maximum=300.0,
                        smooth=smooth, row_batch=batch,
                    )
                    np.testing.assert_array_equal(got, expected)

    def test_small_maps_keep_gradient_edges_and_slope_limits(self):
        for shape in ((2, 2), (2, 9), (9, 2)):
            terrain = np.arange(np.prod(shape), dtype=np.int16).reshape(shape)
            gz, gx = np.gradient(terrain.astype(np.float32))
            expected = np.clip(ndimage.uniform_filter(
                np.hypot(gz, gx), size=3, mode="nearest",
            ), 1.0, 6.0)
            np.testing.assert_array_equal(
                H.hillside_rise_per_block(terrain, row_batch=1), expected,
            )


if __name__ == "__main__":
    unittest.main()
