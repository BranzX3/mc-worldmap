import unittest

import numpy as np

from water_v2.basins import standing_basins
from water_v2.model import Settings


class BasinEvidenceTests(unittest.TestCase):
    def test_wide_contour_strips_keep_river_gradient_at_two_terrain_scales(self):
        for run in (12,48):
            with self.subTest(quantized_step_length=run):
                terrain = np.tile((120-np.arange(288)//run).astype(np.int32),(64,1))
                body = np.zeros(terrain.shape,dtype=bool)
                body[16:48,4:284] = True
                lakes,levels = standing_basins(terrain,body,Settings())
                self.assertEqual(levels,[])
                self.assertFalse(lakes.any())

    def test_broad_lake_with_narrow_outlet_is_one_basin(self):
        terrain = np.full((40,80),110,dtype=np.int32)
        body = np.zeros(terrain.shape,dtype=bool)
        body[8:32,5:35] = True
        terrain[body] = 100
        body[20,35:70] = True
        terrain[20,35:70] = 100-np.arange(35)//3
        lakes,levels = standing_basins(terrain,body,Settings())
        self.assertEqual(levels,[100])
        self.assertTrue(np.all(lakes[8:32,5:35] == 1))
        self.assertEqual(int(lakes[20,69]),0)

    def test_two_lakes_with_narrow_descending_connection_keep_distinct_levels(self):
        terrain = np.full((36,80),110,dtype=np.int32)
        body = np.zeros(terrain.shape,dtype=bool)
        body[8:24,5:30] = True
        body[8:24,48:73] = True
        terrain[8:24,5:30] = 100
        terrain[8:24,48:73] = 95
        body[16,30:48] = True
        terrain[16,30:48] = 100-np.arange(18)//4
        lakes,levels = standing_basins(terrain,body,Settings())
        self.assertEqual(sorted(levels),[95,100])
        self.assertNotEqual(lakes[16,12],lakes[16,60])

    def test_a_lower_exposed_bank_cannot_be_ignored_by_lake_classification(self):
        terrain = np.full((32,32),100,dtype=np.int32)
        body = np.zeros(terrain.shape,dtype=bool)
        body[6:26,6:26] = True
        terrain[26,:] = 97
        lakes,levels = standing_basins(terrain,body,Settings())
        self.assertEqual(levels,[])
        self.assertFalse(lakes.any())

    def test_open_scene_cut_does_not_close_a_wide_river(self):
        terrain = np.full((48,96),100,dtype=np.int32)
        body = np.zeros(terrain.shape,dtype=bool)
        body[10:38,:] = True
        lakes,levels = standing_basins(terrain,body,Settings())
        self.assertEqual(levels,[])


if __name__ == '__main__':
    unittest.main()
