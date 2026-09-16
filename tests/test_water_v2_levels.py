import unittest

import numpy as np

from water_v2.levels import solve_levels


class SectionLevelTests(unittest.TestCase):
    def test_downstream_floor_can_raise_an_upstream_soft_preference(self):
        got, failures = solve_levels([99,99],[96,100],[101,101],[(0,1,0)])
        np.testing.assert_array_equal(got,[100,100])
        self.assertEqual(failures.size,0)

    def test_floor_propagates_through_both_tributaries_without_raising_banks(self):
        edges = [(0,2,0),(1,2,0),(2,3,0),(3,2,2)]
        got, failures = solve_levels([99,98,98,98],[96,96,96,100],[101]*4,edges)
        np.testing.assert_array_equal(got,[100]*4)
        self.assertEqual(failures.size,0)

    def test_junction_and_lake_boundary_are_solved_together(self):
        # Two tributaries enter a shared junction, followed by a lake. There
        # are no separately solved profiles that could disagree at the merge.
        constraints = [(0,2,0),(1,2,0),(2,3,0),(2,0,2),(2,1,2),(3,2,2)]
        got, failures = solve_levels([108,107,105,100],[100,100,100,100],
                                      [108,107,105,100],constraints)
        np.testing.assert_array_equal(got,[104,104,102,100])
        self.assertEqual(failures.size,0)

    def test_infeasible_floor_is_reported_instead_of_raising_a_bank_ceiling(self):
        got, failures = solve_levels([110,100],[109,100],[110,100],[(0,1,0),(1,0,2)])
        np.testing.assert_array_equal(got,[102,100])
        np.testing.assert_array_equal(failures,[0])

    def test_equalities_and_constraint_order_do_not_change_result(self):
        edges = [(0,1,0),(1,0,0),(1,2,0),(2,1,0)]
        first,_ = solve_levels([103,102,101],[90]*3,[103,102,101],edges)
        second,_ = solve_levels([103,102,101],[90]*3,[103,102,101],edges[::-1])
        np.testing.assert_array_equal(first,[101]*3)
        np.testing.assert_array_equal(first,second)

    def test_negative_drop_constraint_is_rejected(self):
        with self.assertRaises(ValueError):
            solve_levels([10,9],[0,0],[10,9],[(0,1,-1)])
