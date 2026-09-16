import unittest
import numpy as np
import hydrology_shape as H


class WideWaterTests(unittest.TestCase):
    def test_sloped_wide_polygon_has_shallows_a_bed_and_downstream_flow(self):
        shape = (64, 64)
        terrain = np.repeat((100 - np.arange(64) // 4)[:, None], 64, axis=1).astype(np.int16)
        body = np.zeros(shape, dtype=bool)
        body[:, 20:44] = True
        sources = {"waterbody_mask": body, "waterway_kind": np.zeros(shape, dtype=np.uint8)}
        out = H.shape_waterway_sections(
            terrain, sources, 0, 64, 0, 64, line_profiles=[],
            standing_mask=np.zeros(shape, dtype=bool),
        )
        np.testing.assert_array_equal(out["waterway_mask"], body)
        np.testing.assert_array_equal(out["surface_y"][body], terrain[body])
        self.assertLessEqual(int(out["depth"][8:56, 20].max()), 2)
        self.assertGreater(float(out["depth"][8:56, 28:36].mean()), 3)
        self.assertLessEqual(int(out["depth"].max()), H.WATERWAY_MAX_DEPTH + 1)
        self.assertTrue((out["flow_z"][8:56, 28:36] > 0).all())
        self.assertTrue((out["flow_index"][8:56, 28:36] > 0).all())

    def test_flat_water_and_dry_banks_do_not_create_flow(self):
        water = np.zeros((32, 32), dtype=bool)
        water[4:28, 4:28] = True
        surface = np.full(water.shape, H.UNRESOLVED, dtype=np.int16)
        surface[water] = 100
        fx, fz, fi = H.raster_water_flow(surface, water)
        self.assertFalse(fx.any() or fz.any() or fi.any())

    def test_downstream_search_cannot_cross_a_dry_barrier(self):
        water = np.ones((32, 32), dtype=bool)
        water[15] = False
        surface = np.full(water.shape, 100, dtype=np.int16)
        surface[16:] = 50
        fx, fz, fi = H.raster_water_flow(surface, water)
        self.assertFalse(fx.any() or fz.any() or fi.any())

    def test_flow_is_identical_when_cropped_with_its_required_halo(self):
        surface = (100 - np.arange(48)[:, None] // 3 - np.arange(48)[None, :] // 4).astype(np.int16)
        water = np.ones(surface.shape, dtype=bool)
        full = H.raster_water_flow(surface, water)
        crop = H.raster_water_flow(surface[8:40, 8:40], water[8:40, 8:40])
        for a, b in zip(full, crop):
            np.testing.assert_array_equal(a[16:32, 16:32], b[8:24, 8:24])

    def test_diagonal_water_contact_does_not_flow_through_dry_corners(self):
        water = np.zeros((24, 24), dtype=bool)
        water[8, 8] = water[9, 9] = True
        surface = np.full(water.shape, 100, dtype=np.int16)
        surface[9, 9] = 90
        fx, fz, fi = H.raster_water_flow(surface, water)
        self.assertFalse(fx.any() or fz.any() or fi.any())


if __name__ == "__main__":
    unittest.main()
