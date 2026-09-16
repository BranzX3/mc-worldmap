import unittest

import numpy as np

from water_v2.topology import build_network, route_network


def network_for(lines, bounds=(0, 32, 0, 32), widths=None, kinds=None):
    offsets = np.r_[0, np.cumsum([len(line) for line in lines])].astype(np.int32)
    points = np.asarray([point for line in lines for point in line], dtype=np.float64).reshape(-1, 2)
    return build_network(points[:, 0], points[:, 1], offsets,
                         np.asarray(kinds if kinds is not None else [3] * len(lines), dtype=np.uint8),
                         np.asarray(widths if widths is not None else [4.] * len(lines)),
                         bounds=bounds)


class SourceNetworkTests(unittest.TestCase):
    def test_shared_internal_source_vertex_forms_one_junction(self):
        network = network_for([[(2, 10), (10, 10), (20, 10)], [(10, 2), (10, 10)]])
        junction = np.flatnonzero(np.all(network.nodes == (10, 10), axis=1))
        self.assertEqual(len(junction), 1)
        self.assertEqual(int(np.count_nonzero(network.edges == junction[0])), 3)
        self.assertEqual(len(network.nodes), 4)

    def test_crossing_without_a_shared_source_vertex_is_not_welded(self):
        network = network_for([[(2, 10), (20, 10)], [(10, 2), (10, 20)]])
        self.assertEqual(len(network.edges), 2)
        self.assertEqual(len(network.nodes), 4)
        self.assertEqual(len(set(network.edges[0]) & set(network.edges[1])), 0)
        self.assertFalse(np.any(np.all(network.nodes == (10, 10), axis=1)))

    def test_line_reordering_and_reversal_preserve_ids_and_attributes(self):
        lines = [[(-8.3, 3.7), (14.4, 8.2), (40.7, 15.9)], [(14.4, 8.2), (12.2, 24.8)]]
        forward = network_for(lines, widths=[12., 4.], kinds=[1, 3])
        backward = network_for([line[::-1] for line in lines[::-1]], widths=[4., 12.], kinds=[3, 1])
        for key in ('nodes', 'edges', 'kinds', 'widths'):
            np.testing.assert_array_equal(getattr(forward, key), getattr(backward, key))

    def test_out_of_bounds_segments_are_clipped_not_projected_to_the_border(self):
        network = network_for([[(-4, 2), (14, 8)], [(-4, -3), (14, -3)]], bounds=(0, 11, 0, 11))
        np.testing.assert_allclose(network.nodes, [[0, 10 / 3], [10, 20 / 3]])
        self.assertEqual(len(network.edges), 1)

    def test_repeated_undirected_segment_keeps_the_largest_width(self):
        network = network_for([[(2, 3), (8, 3)], [(8, 3), (2, 3)]], widths=[4., 12.], kinds=[3, 1])
        self.assertEqual(len(network.edges), 1)
        self.assertEqual(network.widths[0], 12.)
        self.assertEqual(network.kinds[0], 1)

    def test_empty_sources_return_typed_empty_arrays(self):
        network = network_for([])
        self.assertEqual(network.nodes.shape, (0, 2))
        self.assertEqual(network.edges.shape, (0, 2))
        self.assertEqual(network.nodes.dtype, np.float64)
        self.assertEqual(network.edges.dtype, np.int32)
        self.assertEqual(route_network(network, np.zeros((32, 32))), [])


class TerrainRouteTests(unittest.TestCase):
    def assert_cardinal(self, path):
        self.assertEqual(path.dtype, np.int32)
        self.assertTrue(np.all(np.abs(np.diff(path, axis=0)).sum(axis=1) == 1))

    def test_route_uses_the_nearby_valley_and_preserves_endpoints(self):
        terrain = np.full((24, 28), 40., dtype=np.float32)
        terrain[11:15, 3:25] = 20.
        terrain[12, 7:21] = 29.  # Mapped straight line lies on a displaced ridge.
        network = network_for([[(3, 12), (24, 12)]], bounds=(0, 28, 0, 24))
        path, = route_network(network, terrain, corridor=3)
        np.testing.assert_array_equal(path[[0, -1]], [[3, 12], [24, 12]])
        self.assert_cardinal(path)
        self.assertEqual(float(terrain[path[:, 1], path[:, 0]].max()), 20.)
        self.assertTrue(np.all(np.abs(path[:, 1] - 12) <= 3))

    def test_diagonal_route_is_cardinal_and_deterministic(self):
        network = network_for([[(2, 3), (24, 21)]])
        terrain = np.full((32, 32), 20, dtype=np.int16)
        first, = route_network(network, terrain, corridor=1)
        second, = route_network(network, terrain, corridor=1)
        self.assert_cardinal(first)
        np.testing.assert_array_equal(first, second)
        np.testing.assert_array_equal(first[[0, -1]], [[2, 3], [24, 21]])

    def test_nonzero_origin_and_uphill_source_order_preserve_world_endpoints(self):
        network = network_for([[(103, 205), (119, 216)]], bounds=(100, 124, 200, 224))
        terrain = np.tile(np.arange(24, dtype=np.int16), (24, 1))
        path, = route_network(network, terrain, origin=(100, 200))
        np.testing.assert_array_equal(path[[0, -1]], [[103, 205], [119, 216]])
        self.assert_cardinal(path)

    def test_endpoint_outside_terrain_is_rejected_not_clamped(self):
        network = network_for([[(1, 5), (30, 5)]])
        with self.assertRaisesRegex(ValueError, 'outside'):
            route_network(network, np.zeros((20, 20)))

    def test_subcell_edge_remains_one_anchored_cell(self):
        network = network_for([[(8.1, 8.1), (8.3, 8.2)]])
        path, = route_network(network, np.zeros((32, 32)))
        np.testing.assert_array_equal(path, [[8, 8]])

    def test_shared_waypoint_routes_do_not_double_back_along_the_same_leg(self):
        terrain = np.full((24, 24), 40, dtype=np.int16)
        terrain[10, 2:21] = 20
        network = network_for([[(3, 10), (10, 9), (17, 10)]], bounds=(0, 24, 0, 24))
        paths = route_network(network, terrain, corridor=3)
        first, second = [set(map(tuple, path)) for path in paths]
        self.assertEqual(len(first & second), 1)
        shared = first & second
        self.assertEqual(tuple(paths[0][-1]),tuple(paths[1][0]))
        self.assertLessEqual(float(np.linalg.norm(paths[0][-1]-[10,9])),3)
        for path, edge in zip(paths, network.edges):
            self.assert_cardinal(path)
            for point,node in zip(path[[0,-1]],edge):
                if int(np.count_nonzero(network.edges==node)) != 2:
                    np.testing.assert_array_equal(point,np.rint(network.nodes[node]))
        # A lateral contact would create the same shortcut as an overlap.
        for a in first - shared:
            for b in second - shared:
                self.assertNotEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)

    def test_chain_reservations_stay_in_the_source_polyline_corridor(self):
        terrain = np.full((24, 24), 40, dtype=np.int16)
        terrain[10, 2:21] = 20
        network = network_for([[(3, 10), (10, 9), (17, 10)]], bounds=(0, 24, 0, 24))
        for path in route_network(network, terrain, corridor=2):
            distance = np.full(len(path),np.inf)
            for edge in network.edges:
                a,b = np.rint(network.nodes[edge])
                delta = b-a
                progress = np.clip(((path-a)@delta)/(delta@delta),0.,1.)
                distance = np.minimum(distance,np.linalg.norm(path-(a+progress[:,None]*delta),axis=1))
            self.assertLessEqual(float(distance.max()),2.+1e-9)

    def test_network_reservations_are_invariant_to_source_order_and_direction(self):
        terrain = np.full((24, 24), 40, dtype=np.int16)
        terrain[10, 2:21] = 20
        lines = [[(3, 10), (10, 9)], [(10, 9), (17, 10)]]
        forward = network_for(lines, bounds=(0, 24, 0, 24))
        backward = network_for([line[::-1] for line in lines[::-1]], bounds=(0, 24, 0, 24))
        for a, b in zip(route_network(forward, terrain), route_network(backward, terrain)):
            np.testing.assert_array_equal(a, b)

    def test_true_crossing_remains_visible_when_corridors_cannot_separate_it(self):
        network = network_for([[(0, 16), (31, 16)], [(16, 0), (16, 31)]])
        paths = route_network(network, np.full((32, 32), 100, dtype=np.int16), corridor=1)
        first, second = [set(map(tuple, path)) for path in paths]
        self.assertIn((16, 16), first & second)
        self.assertFalse(set(network.edges[0]) & set(network.edges[1]))
        for path, edge in zip(paths, network.edges):
            np.testing.assert_array_equal(path[[0, -1]], network.nodes[edge])

    def test_source_junction_keeps_all_three_incident_paths_and_its_anchor(self):
        network = network_for([[(2, 12), (12, 12)], [(12, 2), (12, 12)], [(12, 12), (24, 12)]])
        paths = route_network(network, np.full((32, 32), 100, dtype=np.int16))
        self.assertEqual(len(paths), 3)
        for path, edge in zip(paths, network.edges):
            self.assert_cardinal(path)
            np.testing.assert_array_equal(path[[0, -1]], network.nodes[edge])
            self.assertIn((12, 12), set(map(tuple, path)))

    def test_nearby_shape_points_can_separate_before_the_true_junction(self):
        from water_v2.engine import compile_scene
        network = network_for([[(3,8),(10,10),(22,10)],[(3,13),(11,11),(22,10)],
                               [(22,10),(29,10)]])
        terrain = np.tile((120-np.arange(32)//3).astype(np.int16),(32,1))
        paths = route_network(network,terrain,corridor=3)
        product,report = compile_scene(terrain,np.zeros(terrain.shape,dtype=bool),paths,
                                      route_nodes=network.edges)
        self.assertNotIn('ambiguous_route_crossing',report['violations'])
        self.assertNotIn('unplanned_water_contact',report['violations'])
        junction = int(np.flatnonzero(np.all(network.nodes == (22,10),axis=1))[0])
        for path,edge in zip(paths,network.edges):
            if junction in edge:
                self.assertIn((22,10),set(map(tuple,path)))

    def test_closed_source_chain_retains_both_arcs_and_consistent_node_identity(self):
        network = network_for([[(4,4),(4,24),(24,24),(24,4),(4,4)]])
        paths = route_network(network,np.full((32,32),100,dtype=np.int16),corridor=2)
        node_cells = {}
        self.assertEqual(len(paths),len(network.edges))
        for path,edge in zip(paths,network.edges):
            self.assert_cardinal(path)
            for node,point in zip(edge,path[[0,-1]]):
                if int(node) in node_cells:
                    np.testing.assert_array_equal(point,node_cells[int(node)])
                node_cells[int(node)] = point
                self.assertLessEqual(float(np.linalg.norm(point-network.nodes[node])),2.)
        self.assertEqual(len(node_cells),4)


if __name__ == '__main__':
    unittest.main()
