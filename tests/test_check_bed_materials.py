import os
import tempfile
import unittest

import numpy as np

import check_bed_materials as B
import hydrology_patch_io as H


class BedMaterialInputTests(unittest.TestCase):
    def test_patch_overlays_every_field_used_for_world_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            shape = (8, 8)
            products = {
                "waterway_mask.npy": np.zeros(shape, dtype=bool),
                "standing_water_mask.npy": np.ones(shape, dtype=bool),
                "surface_y.npy": np.full(shape, 40, dtype=np.int16),
                "depth.npy": np.full(shape, 2, dtype=np.uint8),
                "flow_index.npy": np.zeros(shape, dtype=np.uint8),
            }
            for name, value in products.items():
                np.save(os.path.join(tmp, name), value)

            patch_path = os.path.join(tmp, "patch.npz")
            patch_shape = (4, 4)
            np.savez_compressed(
                patch_path,
                bounds=np.asarray([2, 6, 2, 6]),
                terrain_y=np.full(patch_shape, 35, dtype=np.int16),
                water_mask=np.ones(patch_shape, dtype=bool),
                waterway_mask=np.ones(patch_shape, dtype=bool),
                standing_water_mask=np.zeros(patch_shape, dtype=bool),
                surface_y=np.full(patch_shape, 55, dtype=np.int16),
                depth=np.full(patch_shape, 5, dtype=np.uint8),
                flow_index=np.full(patch_shape, 90, dtype=np.uint8),
            )
            patch = H.load_hydrology_patch(patch_path)
            try:
                fields = B.hydrology_window(tmp, 4, 4, 4, patch=patch)
            finally:
                patch.close()

            inner = np.s_[2:6, 2:6]
            self.assertTrue(fields["waterway_mask"][inner].all())
            self.assertFalse(fields["standing_water_mask"][inner].any())
            self.assertTrue((fields["surface_y"][inner] == 55).all())
            self.assertTrue((fields["depth"][inner] == 5).all())
            self.assertTrue((fields["flow_index"][inner] == 90).all())
            self.assertEqual(int(fields["surface_y"][0, 0]), 40)


if __name__ == "__main__":
    unittest.main()
