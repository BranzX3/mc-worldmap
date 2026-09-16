import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

import hydrology_shape as H


def sources_for(terrain, body, points=()):
    return {
        "waterbody_mask": body,
        "waterway_kind": np.zeros(terrain.shape, dtype=np.uint8),
        "points_x": np.asarray([p[0] for p in points], dtype=np.float32),
        "points_z": np.asarray([p[1] for p in points], dtype=np.float32),
        "offsets": np.asarray([0, len(points)] if points else [0]),
        "kind": np.asarray([3] if points else [], dtype=np.uint8),
        "width_m": np.asarray([4.0] if points else [], dtype=np.float32),
    }


class StandingContextTests(unittest.TestCase):
    def test_sloped_polygon_keeps_water_without_flattening_it_into_a_lake(self):
        terrain = np.full((96, 96), 80, dtype=np.int16)
        terrain[42:] += ((np.arange(54) // 3)[:, None]).astype(np.int16)
        body = np.zeros(terrain.shape, dtype=bool)
        body[8:42, 8:42] = True
        body[42:86, 22:27] = True
        sources = sources_for(terrain, body, ((24, 10), (24, 84)))
        with contextlib.redirect_stdout(io.StringIO()):
            result = H.shape_hydrology_patch(terrain, sources, 0, 96, 0, 96)
        self.assertTrue(result["standing_water_mask"][16:30, 10:17].all())
        self.assertTrue((result["surface_y"][16:30, 10:17] == 80).all())
        self.assertFalse(result["standing_water_mask"][70:82, 22:27].any())
        wet = result["water_mask"][70:82, 22:27]
        self.assertGreater(wet.mean(), .8, "the sloped water polygon disappeared")
        stage = result["surface_y"][70:82, 22:27][wet]
        raw = terrain[70:82, 22:27][wet]
        self.assertLessEqual(int((raw - stage).max()), 3)

    def test_cropped_patch_uses_complete_lake_level_not_its_local_mode(self):
        terrain = np.full((80, 80), 41, dtype=np.int16)
        terrain[:, :24] = 40
        body = np.zeros(terrain.shape, dtype=bool)
        body[8:72, 8:72] = True
        sources = sources_for(terrain, body)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(H, "HERE", directory):
            np.save(Path(directory) / "terrain_y.npy", terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                full = H.shape_hydrology_patch(terrain, sources, 0, 80, 0, 80)
                cropped = H.shape_hydrology_patch(terrain[24:56, 32:64], sources,
                                                   32, 64, 24, 56)
        self.assertTrue(cropped["water_mask"].all())
        self.assertTrue((cropped["surface_y"] == 40).all())
        np.testing.assert_array_equal(cropped["standing_water_mask"],
                                      full["standing_water_mask"][24:56, 32:64])
        np.testing.assert_array_equal(cropped["surface_y"], full["surface_y"][24:56, 32:64])

    def test_default_patch_and_global_tiles_classify_the_same_lake(self):
        terrain = np.full((64, 64), 41, dtype=np.int16)
        terrain[:, :24] = 40
        body = np.zeros(terrain.shape, dtype=bool)
        body[6:58, 6:58] = True
        sources = sources_for(terrain, body, ((30, 8), (30, 56)))
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(H, "HERE", directory):
            np.save(Path(directory) / "terrain_y.npy", terrain)
            np.savez(Path(directory) / "water_sources.npz", **sources)
            with contextlib.redirect_stdout(io.StringIO()):
                patch = H.shape_hydrology_patch(terrain, sources, 0, 64, 0, 64)
                # Full halo isolates classification consistency from the separate
                # finite geometry-halo approximation. Components cross all tiles.
                H.shape_hydrology_global(str(Path(directory) / "global"), tile_size=32, halo=64)
            for key in ("standing_water_mask", "water_mask", "surface_y", "section_id"):
                actual = np.load(Path(directory) / "global" / (key + ".npy"))
                np.testing.assert_array_equal(patch[key], actual, err_msg=key)


if __name__ == "__main__":
    unittest.main()
