import unittest

import numpy as np

import render_view as R


class CameraSiteDiagnosticsTests(unittest.TestCase):
    def test_reports_local_cliff_rise_without_confusing_world_origin(self):
        terrain = np.full((30, 24), 80, dtype=np.int16)
        terrain[12, 9] = 70
        terrain[17, 9] = 96

        got = R.camera_site_diagnostics(
            terrain, cx=112, cz=209, origin=(100, 200), radius=6
        )

        self.assertEqual(got["center_y"], 70)
        self.assertEqual(got["rise"], 26)
        self.assertEqual(got["drop"], 0)
        self.assertEqual(got["relief"], 26)

    def test_rejects_camera_outside_loaded_window(self):
        with self.assertRaises(ValueError):
            R.camera_site_diagnostics(
                np.zeros((8, 8), dtype=np.int16),
                cx=99, cz=204, origin=(100, 200), radius=3,
            )


if __name__ == "__main__":
    unittest.main()
