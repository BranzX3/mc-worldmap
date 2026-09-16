import unittest

import numpy as np

from water_v2.blocks import iter_column_blocks
from water_v2.engine import compile_scene
from water_v2.fluids import river_pools
from water_v2.model import DRY


class RiverPoolTests(unittest.TestCase):
    def test_long_flat_channel_is_one_source_pool_without_lake_excavation(self):
        terrain = np.full((24,40),100,dtype=np.int16)
        route = np.column_stack((np.arange(3,37),np.full(34,12))).astype(np.int32)
        product,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),[route],radii=[1])
        self.assertTrue(report['ready_for_world_write'],report)
        self.assertEqual(report['lake_count'],0)
        self.assertEqual(report['river_pool_count'],1)
        np.testing.assert_array_equal(product['river_pool_mask'],product['water_mask'])
        self.assertLessEqual(int(product['depth'].max()),4)
        self.assertLessEqual(int((terrain-product['terrain_y']).max()),4)
        water = [properties for _,name,properties in iter_column_blocks(product,20,12)
                 if name == 'minecraft:water']
        self.assertGreater(len(water),1)
        self.assertTrue(all(properties == {'level':'0'} for properties in water),
                        'an explicit pool is a full source volume, not a source surface over falling water')

    def test_wide_short_steps_receive_supply_across_their_full_inlet(self):
        base = np.full((24,40),120,dtype=np.int16)
        wet = np.zeros(base.shape,dtype=bool)
        wet[5:19,2:38] = True
        surface = np.full(base.shape,DRY,dtype=np.int16)
        profile = np.tile(110-np.arange(40)//3,(24,1))
        surface[wet] = profile[wet]
        pools = river_pools(surface,wet,np.zeros(base.shape,dtype=bool),base)
        self.assertFalse(pools.any(),'lateral width alone does not make a long still reach')

    def test_long_pool_preserves_its_level_and_drops_into_a_flowing_outlet(self):
        base = np.full((16,40),120,dtype=np.int16)
        wet = np.zeros(base.shape,dtype=bool)
        wet[8,2:36] = True
        surface = np.full(base.shape,DRY,dtype=np.int16)
        surface[8,2:25] = 110
        surface[8,25:36] = 109-np.arange(11)
        before = surface.copy()
        pools = river_pools(surface,wet,np.zeros(base.shape,dtype=bool),base)
        self.assertTrue(pools[8,2:25].all())
        self.assertFalse(pools[8,25:36].any())
        np.testing.assert_array_equal(surface,before)

    def test_uncontained_plateau_cannot_be_promoted_to_a_pool(self):
        base = np.full((16,40),100,dtype=np.int16)
        wet = np.zeros(base.shape,dtype=bool)
        wet[8,2:36] = True
        surface = np.full(base.shape,DRY,dtype=np.int16)
        surface[wet] = 99
        base[7,18] = 95
        pools = river_pools(surface,wet,np.zeros(base.shape,dtype=bool),base)
        self.assertFalse(pools.any())


if __name__ == '__main__':
    unittest.main()
