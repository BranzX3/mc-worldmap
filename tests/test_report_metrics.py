import unittest

import numpy as np

import report_metrics as R


class RunLengthTests(unittest.TestCase):
    def test_runs_do_not_join_across_rows(self):
        mask = np.array([[1, 1, 0, 0], [1, 1, 0, 0]], dtype=bool)
        self.assertEqual(sorted(R.horizontal_run_lengths(mask).tolist()), [2, 2])

    def test_runs_touching_both_edges_are_counted(self):
        mask = np.array([[1, 1, 1, 1]], dtype=bool)
        self.assertEqual(R.horizontal_run_lengths(mask).tolist(), [4])

    def test_empty_mask_returns_no_runs(self):
        self.assertEqual(
            R.horizontal_run_lengths(np.zeros((3, 3), dtype=bool)).size, 0
        )


class LabelTests(unittest.TestCase):
    def test_diagonal_neighbours_are_separate_components(self):
        mask = np.array([[1, 0], [0, 1]], dtype=bool)
        labels, count = R.label_4conn(mask)
        self.assertEqual(count, 2)
        self.assertNotEqual(int(labels[0, 0]), int(labels[1, 1]))

    def test_u_shape_merges_into_one_component(self):
        mask = np.array([
            [1, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
        ], dtype=bool)
        labels, count = R.label_4conn(mask)
        self.assertEqual(count, 1)
        self.assertEqual(int(labels[0, 0]), int(labels[0, 2]))

    def test_key_splits_a_connected_run_by_level(self):
        mask = np.ones((1, 4), dtype=bool)
        key = np.array([[5, 5, 4, 4]])
        labels, count = R.label_4conn(mask, key=key)
        self.assertEqual(count, 2)
        self.assertEqual(int(labels[0, 0]), int(labels[0, 1]))
        self.assertNotEqual(int(labels[0, 1]), int(labels[0, 2]))


class StreamPoolTests(unittest.TestCase):
    """แอ่งตัน = กลุ่ม cell ระดับเดียวกันที่น้ำออกไปไหนไม่ได้"""

    @staticmethod
    def _frame(stream_y, stream_cells):
        """สร้างกรอบ 9x9 ที่มีลำธารตามที่ระบุ ล้อมด้วยพื้นดินแห้ง"""
        n = 9
        water = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        y = np.full((n, n), 100, dtype=np.int32)
        for (z, x), level in zip(stream_cells, stream_y):
            water[z, x] = True
            depth[z, x] = 1
            y[z, x] = level
        return water, depth, y

    def test_stream_draining_downhill_has_no_trapped_pool(self):
        # ต้องไหลถึงขอบกรอบ — ลำธารที่จบกลางภูมิประเทศคือแอ่งตันจริง
        cells = [(4, x) for x in range(2, 9)]
        levels = [60, 60, 59, 59, 58, 58, 57]
        water, depth, y = self._frame(levels, cells)
        result = R.stream_metrics(water, depth, y)
        self.assertEqual(result["trapped_pools"], 0)
        self.assertEqual(result["trapped_cells"], 0)

    def test_stream_ending_mid_terrain_is_trapped(self):
        """เคสจริงที่ต้องแก้ในเฟส C: DEM ทำให้ปลายลำธารไม่มีทางไป"""
        cells = [(4, x) for x in range(2, 6)]
        levels = [60, 60, 59, 59]
        water, depth, y = self._frame(levels, cells)
        result = R.stream_metrics(water, depth, y)
        self.assertEqual(result["pools"], 2)
        self.assertEqual(result["trapped_pools"], 1)
        self.assertEqual(result["trapped_cells"], 2)

    def test_isolated_flat_pool_is_reported_as_trapped(self):
        cells = [(4, 4), (4, 5)]
        water, depth, y = self._frame([60, 60], cells)
        result = R.stream_metrics(water, depth, y)
        self.assertEqual(result["pools"], 1)
        self.assertEqual(result["trapped_pools"], 1)
        self.assertEqual(result["trapped_cells"], 2)

    def test_a_flat_pool_counts_once_not_per_cell(self):
        """แอ่งแบนยาวที่ระบายได้ต้องไม่ถูกนับเป็นหลุมทีละ cell"""
        cells = [(4, x) for x in range(2, 9)]
        levels = [60] * 5 + [59, 59]
        water, depth, y = self._frame(levels, cells)
        result = R.stream_metrics(water, depth, y)
        self.assertEqual(result["pools"], 2)
        self.assertEqual(result["trapped_pools"], 0)
        self.assertEqual(result["trapped_cells"], 0)

    def test_stream_reaching_the_frame_edge_is_not_trapped(self):
        cells = [(4, x) for x in range(0, 4)]
        water, depth, y = self._frame([60] * 4, cells)
        result = R.stream_metrics(water, depth, y)
        self.assertEqual(result["trapped_pools"], 0)


class LakeFlatnessTests(unittest.TestCase):
    def test_uneven_lake_surface_is_reported(self):
        n = 12
        water = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        water[2:10, 2:10] = True
        depth[2:10, 2:10] = 6
        y = np.full((n, n), 100, dtype=np.int32)
        y[2:10, 2:6] = 40
        y[2:10, 6:10] = 41            # ครึ่งขวาสูงกว่าหนึ่งบล็อก

        result = R.lake_metrics(water, depth, y)

        self.assertEqual(result["components"], 1)
        self.assertEqual(result["not_flat_components"], 1)
        self.assertEqual(result["lakes"][0]["level_spread"], 1)
        self.assertEqual(result["not_flat_cell_share"], 1.0)

    def test_flat_lake_surface_passes(self):
        n = 12
        water = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        water[2:10, 2:10] = True
        depth[2:10, 2:10] = 6
        y = np.full((n, n), 100, dtype=np.int32)
        y[2:10, 2:10] = 40

        result = R.lake_metrics(water, depth, y)

        self.assertEqual(result["not_flat_components"], 0)
        self.assertEqual(result["lakes"][0]["level_spread"], 0)


class BiomeCoverageTests(unittest.TestCase):
    def test_negative_surface_is_flagged_as_outside_the_write_range(self):
        y = np.array([[-30, -30], [100, 100]], dtype=np.int32)
        water = np.array([[True, True], [False, False]])

        result = R.biome_coverage(y, water)

        self.assertEqual(result["columns_below_share"], 0.5)
        self.assertEqual(result["water_below_cells"], 2)
        self.assertEqual(result["water_below_share"], 1.0)


class BankTests(unittest.TestCase):
    def test_shallow_stream_bank_counts_as_untreated(self):
        n = 20
        water = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        water[10, :] = True
        depth[10, :] = 1              # ลำธารตื้น -> body == 0

        result = R.bank_metrics(water, depth)

        self.assertEqual(result["untreated_share"], 0.0)

    def test_deep_lake_bank_counts_as_treated(self):
        n = 30
        water = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        water[10:20, 10:20] = True
        depth[10:20, 10:20] = 12      # ลึกพอให้ body > 0

        result = R.bank_metrics(water, depth)

        self.assertEqual(result["untreated_cells"], 0)


if __name__ == "__main__":
    unittest.main()
