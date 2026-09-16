"""Behavior checks for the independent bounded-scene water compiler."""
import builtins
import unittest
from unittest import mock

import numpy as np

from water_v2.engine import compile_scene
from water_v2.model import DRY, Settings, validate
from water_v2.topology import build_network, route_network


def horizontal(x0, x1, z):
    return np.column_stack((np.arange(x0, x1), np.full(x1 - x0, z))).astype(np.int32)


def vertical(x, z0, z1):
    return np.column_stack((np.full(z1 - z0, x), np.arange(z0, z1))).astype(np.int32)


class WaterSceneTests(unittest.TestCase):
    def assert_product(self, product):
        self.assertEqual(validate(product), product['water_mask'].shape)
        wet = product['water_mask'].ravel()
        receiver = product['receiver'].ravel()
        stage = product['surface_y'].ravel()
        self.assertTrue(np.all(receiver[~wet] == -1))
        linked = np.flatnonzero(receiver >= 0)
        self.assertTrue(np.all(receiver[linked] < wet.size))
        self.assertTrue(np.all(wet[receiver[linked]]))
        self.assertTrue(np.all(stage[receiver[linked]] <= stage[linked]),
                        'a receiver cannot require water to climb uphill')
        self.assertTrue(np.all(receiver[linked] != linked))
        # Following a section receiver must eventually reach an explicit sink.
        for start in linked:
            visited = set()
            at = int(start)
            while at >= 0:
                self.assertNotIn(at, visited, 'receiver cycle')
                visited.add(at)
                at = int(receiver[at])
        self.assertFalse(product['flow_index'][~product['water_mask']].any())
        self.assertTrue(np.all(product['water_level'][product['standing_water_mask']] == 0),
                        'standing columns must retain source surfaces')

    def simple_stream(self):
        terrain = np.tile((100 - np.arange(40) // 3).astype(np.int16), (24, 1))
        body = np.zeros(terrain.shape, dtype=bool)
        route = horizontal(3, 37, 12)
        return terrain, body, route

    def junction_scene(self):
        z, x = np.indices((32, 40))
        terrain = (120 - x // 3 + np.maximum(0, 12 - z) // 3).astype(np.int16)
        routes = [vertical(12, 3, 13), horizontal(3, 13, 12), horizontal(12, 36, 12)]
        return terrain, np.zeros(terrain.shape, dtype=bool), routes

    def test_descending_narrow_stream_is_writeable_and_supplied(self):
        terrain, body, route = self.simple_stream()
        product, report = compile_scene(terrain, body, [route])
        self.assertTrue(report['ready_for_world_write'], report)
        self.assertEqual(report['violations'], {})
        self.assert_product(product)
        self.assertTrue(product['water_mask'][route[:, 1], route[:, 0]].all())
        stages = product['surface_y'][route[:, 1], route[:, 0]].astype(int)
        self.assertTrue(np.all(np.diff(stages) <= 0))
        self.assertGreater(int(stages[0] - stages[-1]), 5)
        self.assertGreater(report['source_cells'], 0)
        self.assertGreater(report['flowing_cells'], 0)
        self.assertFalse(product['standing_water_mask'].any())
        self.assertTrue(np.any(product['flow_index'][product['water_mask']] > 0))

    def test_broad_flat_lake_has_one_level_shelf_and_deeper_interior(self):
        terrain = np.full((56, 56), 100, dtype=np.int16)
        body = np.zeros(terrain.shape, dtype=bool)
        body[8:48, 8:48] = True
        product, report = compile_scene(terrain, body, [])
        self.assertTrue(report['ready_for_world_write'], report)
        self.assert_product(product)
        np.testing.assert_array_equal(product['standing_water_mask'], body)
        self.assertEqual(np.unique(product['surface_y'][body]).tolist(), [100])
        self.assertLessEqual(int(product['depth'][8, 20]), 2)
        self.assertGreater(int(product['depth'][28, 28]), int(product['depth'][8, 20]))
        self.assertEqual(report['lake_count'], 1)

    def test_broad_smooth_sloped_polygon_is_flowing_water_not_one_lake(self):
        # Every local three-cell range is only one block, but the complete
        # mapped water body descends 16 blocks. Local smoothness is not a lake.
        terrain = np.tile((100 - np.arange(48) // 2).astype(np.int16), (32, 1))
        body = np.zeros(terrain.shape, dtype=bool)
        body[10:22, 6:42] = True
        product, report = compile_scene(terrain, body, [], settings=Settings(max_river_depth=3))
        self.assertFalse(product['standing_water_mask'].any(),
                         'a long smooth slope cannot be flattened to one lake stage')
        np.testing.assert_array_equal(product['water_mask'], body)
        self.assertGreater(int(np.ptp(product['surface_y'][body])), 10)
        self.assertTrue(report['ready_for_world_write'], report)
        self.assert_product(product)
        self.assertGreater(report['flowing_cells'], 0)

    def test_shared_junction_and_level_mouths_join_at_one_stage(self):
        terrain, body, routes = self.junction_scene()
        product, report = compile_scene(terrain, body, routes, radii=[1, 1, 1])
        self.assertTrue(report['ready_for_world_write'], report)
        self.assert_product(product)
        junction = int(product['surface_y'][12, 12])
        self.assertEqual(int(product['surface_y'][11, 12]), junction)
        self.assertGreaterEqual(int(product['surface_y'][12, 11]), junction)
        self.assertLessEqual(int(product['surface_y'][12, 11])-junction,Settings().ordinary_drop)
        self.assertEqual(int(product['surface_y'][12, 13]), junction)
        self.assertGreater(int(product['section_id'][12, 12]), 0)
        self.assertNotIn('ambiguous_route_crossing', report['violations'])

    def test_route_order_and_direction_do_not_change_the_compiled_scene(self):
        terrain, body, routes = self.junction_scene()
        radii = [0, 1, 1]
        first, first_report = compile_scene(terrain, body, routes, radii=radii)
        reordered, second_report = compile_scene(
            terrain, body, [path[::-1].copy() for path in routes[::-1]], radii=radii[::-1])
        self.assertEqual(first.keys(), reordered.keys())
        for name in first:
            np.testing.assert_array_equal(first[name], reordered[name], err_msg=name)
        self.assertEqual(first_report, second_report)

    def test_compiler_does_not_change_dry_banks_or_mutate_its_inputs(self):
        terrain, body, route = self.simple_stream()
        before_terrain, before_body, before_route = terrain.copy(), body.copy(), route.copy()
        product, report = compile_scene(terrain, body, [route], radii=[1])
        dry = ~product['water_mask']
        self.assertTrue(product['bank_mask'].any())
        np.testing.assert_array_equal(product['terrain_y'][dry], terrain[dry])
        np.testing.assert_array_equal(terrain, before_terrain)
        np.testing.assert_array_equal(body, before_body)
        np.testing.assert_array_equal(route, before_route)
        self.assertEqual(report['dry_terrain_modified'], 0)

    def test_infeasible_ridge_route_is_reported_without_raising_the_banks(self):
        terrain = np.full((24, 40), 80, dtype=np.int16)
        route = horizontal(3, 37, 12)
        terrain[route[:, 1], route[:, 0]] = 100
        product, report = compile_scene(terrain, np.zeros(terrain.shape, dtype=bool), [route])
        self.assertFalse(report['ready_for_world_write'])
        self.assertGreater(report['violations'].get('excessive_river_excavation', 0), 0)
        self.assertTrue(report['examples']['excessive_river_excavation'])
        dry = ~product['water_mask']
        np.testing.assert_array_equal(product['terrain_y'][dry], terrain[dry])
        self.assertEqual(report['dry_terrain_modified'], 0)
        self.assert_product(product)

    def test_crossing_route_interiors_are_reported_as_ambiguous(self):
        terrain = np.full((32, 32), 100, dtype=np.int16)
        routes = [horizontal(3, 29, 16), vertical(16, 3, 29)]
        _, report = compile_scene(terrain, np.zeros(terrain.shape, dtype=bool), routes)
        self.assertFalse(report['ready_for_world_write'])
        self.assertGreater(report['violations'].get('ambiguous_route_crossing', 0), 0)
        self.assertIn({'x': 16, 'z': 16}, report['examples']['ambiguous_route_crossing'])

    def test_distinct_source_nodes_rounding_to_one_cell_cannot_become_a_junction(self):
        terrain = np.tile((100-np.arange(20)).astype(np.int16),(20,1))
        network = build_network([4,8.1,8.4,12],[8,8.1,8.4,8],[0,2,4],[3,3],[4,4],
                                bounds=(0,20,0,20))
        self.assertEqual(len(network.nodes),4)
        routes = route_network(network,terrain)
        _,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),routes,
                                 route_nodes=network.edges)
        self.assertFalse(report['ready_for_world_write'])
        self.assertIn('ambiguous_route_crossing',report['violations'])

    def test_unrelated_parallel_routes_cannot_silently_become_one_channel(self):
        terrain = np.tile((100-np.arange(20)).astype(np.int16),(20,1))
        routes = [horizontal(4,13,8),horizontal(4,13,9)]
        _,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),routes)
        self.assertFalse(report['ready_for_world_write'])
        self.assertIn('unplanned_water_contact',report['violations'])

    def test_connected_subcell_edge_preserves_its_declared_junction(self):
        terrain = np.tile((100-np.arange(20)).astype(np.int16),(20,1))
        network = build_network([4,8.1,8.4,12],[8,8.1,8.4,8],[0,4],[3],[4],bounds=(0,20,0,20))
        routes = route_network(network,terrain)
        self.assertIn(1,[len(r) for r in routes])
        _,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),routes,
                                 route_nodes=network.edges)
        self.assertNotIn('ambiguous_route_crossing',report['violations'])
        self.assertNotIn('unplanned_water_contact',report['violations'])

    def test_meander_cannot_shortcut_between_its_own_nonconsecutive_stations(self):
        terrain = np.full((20,20),120,dtype=np.int16)
        terrain[9,4:13] = 100-np.arange(9)
        terrain[8,4:13] = 83+np.arange(9)
        route = np.concatenate((horizontal(4,13,9),horizontal(4,13,8)[::-1]))
        _,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),[route],
                                 route_nodes=np.asarray([[0,1]]))
        self.assertFalse(report['ready_for_world_write'])
        self.assertIn('unplanned_water_contact',report['violations'])

    def test_section_assignment_and_receivers_cannot_cross_a_dry_gap(self):
        terrain = np.tile((100-np.arange(24)).astype(np.int16),(24,1))
        routes = [horizontal(0,24,10),horizontal(0,24,15)]
        product,_ = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),routes,
                                  radii=[3,0],settings=Settings(max_river_depth=3))
        upper = set(product['section_id'][7:14].ravel())-{0}
        lower = set(product['section_id'][15].ravel())-{0}
        self.assertFalse(upper & lower)
        self.assertTrue(np.all(product['receiver'][7:14][product['receiver'][7:14]>=0]//24 < 14))

    def test_lake_with_lower_outlet_keeps_its_level_and_outlet_gradient(self):
        terrain = np.tile((100-np.maximum(0,np.arange(64)-30)//3).astype(np.int16),(40,1))
        body = np.zeros(terrain.shape,dtype=bool)
        body[8:32,8:30] = True
        product,report = compile_scene(terrain,body,[horizontal(29,59,20)])
        self.assertTrue(report['ready_for_world_write'],report)
        self.assertTrue(np.all(product['surface_y'][body]==100))
        self.assertLess(int(product['surface_y'][20,58]),95)
        self.assertTrue(np.all(product['receiver'][body] >= 0),
                        'a lake with an outlet must drain into its downstream reach')
        at = int(np.flatnonzero(body)[0])
        receiver = product['receiver'].ravel()
        while receiver[at] >= 0:
            at = int(receiver[at])
        self.assertFalse(body.ravel()[at])
        self.assertEqual(int(product['surface_y'].ravel()[at]),
                         int(product['surface_y'][product['water_mask']].min()))
        self.assert_product(product)

    def test_upstream_boundary_plateau_is_an_inlet_not_a_sink(self):
        terrain, body, _ = self.simple_stream()
        route = horizontal(0, 40, 12)
        product, report = compile_scene(terrain, body, [route])
        self.assertTrue(report['ready_for_world_write'], report)
        self.assertGreaterEqual(int(product['receiver'][12, 0]), 0)
        self.assertEqual(int(product['receiver'][12, 39]), -1)
        self.assert_product(product)

    def test_broad_elevation_band_with_lower_shore_is_not_a_lake(self):
        terrain = np.full((32, 48), 100, dtype=np.int16)
        body = np.zeros(terrain.shape, dtype=bool)
        body[6:26, 6:42] = True
        terrain[26:, :] = 97
        product, report = compile_scene(terrain, body, [])
        self.assertEqual(report['lake_count'],0,
                         'the mapped area cannot retain a standing level at its core height')
        self.assertFalse((product['standing_water_mask'] & ~product['river_pool_mask']).any())

    def test_river_depth_can_shallow_to_respect_excavation_at_a_shared_section(self):
        terrain = np.full((24,40),100,dtype=np.int16)
        route = horizontal(3,37,12)
        terrain[11,3:37] = 102
        product,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),[route],radii=[2])
        self.assertTrue(report['ready_for_world_write'],report)
        self.assertGreaterEqual(int(product['depth'][11,20]),1)
        self.assertLess(int(product['depth'][11,20]),int(product['depth'][12,20]))
        self.assertLessEqual(int((terrain-product['terrain_y']).max()),4)
        self.assertEqual(product['surface_y'][11,20],product['surface_y'][12,20])

    def test_steep_confluence_retains_inlet_steps_instead_of_flattening_them(self):
        terrain = np.tile((140-2*np.arange(32)).astype(np.int16)[:,None],(1,40))
        routes = [vertical(20,4,17),horizontal(4,21,16),vertical(20,16,29)]
        product,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),routes)
        self.assertTrue(report['ready_for_world_write'],report)
        self.assertGreater(product['surface_y'][15,20],product['surface_y'][16,20])
        self.assertNotEqual(product['section_id'][15,20],product['section_id'][16,20])
        self.assertLessEqual(int((terrain-product['terrain_y']).max()),4)

    def test_large_integer_terrain_is_rejected_before_narrowing(self):
        terrain = np.full((8, 8), 2**32+100, dtype=np.int64)
        with self.assertRaisesRegex(ValueError, 'dimension bounds'):
            compile_scene(terrain, np.zeros(terrain.shape, dtype=bool), [])

    def test_explicit_inputs_need_no_legacy_imports_or_water_files(self):
        terrain, body, route = self.simple_stream()
        original_import = builtins.__import__
        forbidden = {'hydrology_shape', 'channel_sections', 'make_water',
                     'make_water_levels', 'paint_surface', 'surface'}

        def independent_import(name, *args, **kwargs):
            if name.split('.')[0] in forbidden:
                raise AssertionError('legacy module imported: ' + name)
            return original_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=independent_import), \
                mock.patch.object(np, 'load', side_effect=AssertionError('hidden product read')):
            product, report = compile_scene(terrain, body, [route])
        self.assertTrue(report['ready_for_world_write'], report)
        self.assert_product(product)

    def test_empty_dry_scene_has_no_hidden_water_or_receivers(self):
        terrain = np.arange(80, dtype=np.int16).reshape(8, 10)
        with mock.patch.object(np, 'load', side_effect=AssertionError('hidden product read')):
            product, report = compile_scene(terrain, np.zeros(terrain.shape, dtype=bool), [])
        self.assertTrue(report['ready_for_world_write'])
        self.assertEqual(report['water_cells'], 0)
        self.assert_product(product)
        np.testing.assert_array_equal(product['terrain_y'], terrain)
        self.assertFalse(product['water_mask'].any())
        self.assertTrue((product['surface_y'] == DRY).all())
        self.assertTrue((product['receiver'] == -1).all())

    def test_nonzero_origin_preserves_physical_fields_and_receiver_indices(self):
        terrain, body, route = self.simple_stream()
        local, local_report = compile_scene(terrain, body, [route])
        shifted, report = compile_scene(terrain, body, [route + (1000, 2000)], origin=(1000, 2000))
        np.testing.assert_array_equal(shifted['bounds'], [1000, 1040, 2000, 2024])
        for key in ('terrain_y', 'surface_y', 'depth', 'water_mask', 'receiver', 'water_level'):
            np.testing.assert_array_equal(local[key], shifted[key], err_msg=key)
        self.assertEqual(report['violations'], local_report['violations'])
        self.assert_product(shifted)


if __name__ == '__main__':
    unittest.main()
