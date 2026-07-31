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

    def test_explicit_terminal_outlet_drains_flat_stream(self):
        cells = [(4, x) for x in range(2, 6)]
        water, depth, y = self._frame([60] * 4, cells)
        outlet = np.zeros(water.shape, dtype=bool)
        outlet[4, 2] = True

        result = R.stream_metrics(
            water, depth, y, outlet_mask=outlet
        )

        self.assertEqual(result["trapped_pools"], 0)
        self.assertEqual(result["trapped_cells"], 0)


class WaterfallMetricTests(unittest.TestCase):
    def test_counts_edges_but_merges_them_into_one_downstream_curtain(self):
        water = np.ones((3, 3), dtype=bool)
        y = np.asarray([
            [14, 14, 14],
            [14, 10, 14],
            [14, 14, 14],
        ])

        result = R.waterfall_metrics(water, y)

        self.assertEqual(result["edges"], 4)
        self.assertEqual(result["columns"], 1)
        self.assertEqual(result["blocks"], 4)
        self.assertEqual(result["max_drop"], 4)

    def test_ignores_one_block_step_and_dry_neighbour(self):
        water = np.asarray([[True, True, False]])
        y = np.asarray([[12, 11, 20]])

        result = R.waterfall_metrics(water, y)

        self.assertEqual(result["edges"], 0)
        self.assertEqual(result["blocks"], 0)


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


class WaterfallTests(unittest.TestCase):
    def test_declared_curtain_wins_over_inferring_from_dem_steps(self):
        """เมื่อมี waterfall_top_y ต้องวัดตัวนั้น ไม่ใช่อนุมานจากทุก DEM step

        `paint_surface` เติมม่านน้ำจาก waterfall_top_y เท่านั้น การอนุมานเอง
        จากทุกคู่ cell ที่ผิวน้ำต่างกัน >= 2 รายงานเกินความจริงเกือบสามเท่า
        (วัดจากข้อมูลจริง: 6,677 คอลัมน์ เทียบกับ 1,350 ที่ประกาศไว้)
        """
        water = np.ones((3, 6), dtype=bool)
        y = np.zeros((3, 6), dtype=np.int32)
        y[:, :3] = 20          # ขั้น 20 บล็อกกลางภาพ — DEM step ที่ไม่ใช่น้ำตก
        declared = np.full((3, 6), np.iinfo(np.int16).min, dtype=np.int16)
        declared[1, 3] = 5     # ประกาศม่านน้ำจริงไว้จุดเดียว สูง 5

        inferred = R.waterfall_metrics(water, y)
        got = R.waterfall_metrics(water, y, declared_top=declared)

        self.assertGreater(inferred["columns"], got["columns"])
        self.assertEqual(got["columns"], 1)
        self.assertEqual(got["blocks"], 5)
        self.assertEqual(got["max_drop"], 5)

    def test_singleton_share_flags_scattered_curtains(self):
        """น้ำตกที่กระจายเป็นจุดเดี่ยวต้องถูกชี้ออกมา ไม่ใช่ซ่อนในผลรวม"""
        y = np.zeros((9, 9), dtype=np.int32)
        water = np.ones((9, 9), dtype=bool)
        scattered = np.full((9, 9), np.iinfo(np.int16).min, dtype=np.int16)
        for z, x in ((1, 1), (1, 5), (5, 1), (7, 7)):
            scattered[z, x] = 3
        clustered = np.full((9, 9), np.iinfo(np.int16).min, dtype=np.int16)
        clustered[4, 3:7] = 3          # แนวเดียวขวางลำน้ำ

        bad = R.waterfall_metrics(water, y, declared_top=scattered)
        good = R.waterfall_metrics(water, y, declared_top=clustered)

        self.assertEqual(bad["singleton_share"], 1.0)
        self.assertEqual(bad["largest_cluster"], 1)
        self.assertEqual(good["singleton_share"], 0.0)
        self.assertEqual(good["largest_cluster"], 4)


class PoolSizeTests(unittest.TestCase):
    def _stream(self, levels):
        water = np.zeros((6, 40), dtype=bool)
        water[2, :] = True
        depth = np.zeros((6, 40), dtype=np.uint8)
        depth[2, :] = 1
        y = np.zeros((6, 40), dtype=np.int32)
        y[2, :] = levels
        return R.stream_metrics(water, depth, y)

    def test_pool_size_separates_staircase_from_real_pools(self):
        """ขนาดแอ่งต้องแยก "ลดทีละบล็อก" ออกจาก "แอ่งราบสลับจุดตก" ได้

        เป็นตัวเลขที่ตรงกับอาการ "น้ำไหลอยู่ ๆ ก็ลดลง 1 บล็อก" ที่สุด — ถ้าแอ่ง
        ส่วนใหญ่มีไม่กี่ cell แปลว่าผิวน้ำลดแทบทุกก้าว ซึ่งไม่มีในธรรมชาติ
        """
        staircase = 100 - np.arange(40)          # ลดทีละบล็อกตลอดสาย
        pooled = 100 - np.arange(40) // 8        # ราบ 8 บล็อกแล้วค่อยตก

        bad = self._stream(staircase)
        good = self._stream(pooled)

        self.assertEqual(bad["pool_size_median"], 1)
        self.assertEqual(bad["tiny_pool_share"], 1.0)

        self.assertEqual(good["pool_size_median"], 8)
        self.assertEqual(good["tiny_pool_share"], 0.0)
        self.assertLess(good["pools"], bad["pools"])

    def test_pool_metrics_are_absent_without_any_stream(self):
        water = np.zeros((6, 40), dtype=bool)
        depth = np.zeros((6, 40), dtype=np.uint8)
        y = np.zeros((6, 40), dtype=np.int32)

        self.assertEqual(R.stream_metrics(water, depth, y), {"cells": 0})


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

    def test_bank_metrics_is_tile_invariant(self):
        """ผลต้องไม่ขึ้นกับ tile_size

        เป็น guard ของการประกอบ tile (index ของหน้าต่างใน, การนับซ้ำ/ตกหล่น)
        ไม่ใช่ของความกว้าง pad — สูตรปัจจุบันทำให้ `untreated_cells` เป็น 0 เสมอ
        โดยโครงสร้าง (ทุก bank cell อยู่ที่ dist 1..shore_width จึงมี falloff > 0
        และ lake_shore/stream_bank ตัวใดตัวหนึ่งเป็นบวกเสมอ) ตรงกับที่ docs/TODO.md
        เตือนว่าห้ามใช้ "ตลิ่งไม่ได้แต่ง 0%" เป็นเกณฑ์ผ่าน

        pad ถูกคุ้มโดย test_shore_probabilities_only_read_nearby_input
        ใน tests/test_paint_surface.py
        """
        rng = np.random.default_rng(515)
        water = np.zeros((70, 90), dtype=bool)
        water[8:26, 12:40] = True                 # ทะเลสาบลึก
        water[45, 5:85] = True                    # ลำธารตื้นพาดขวางหลายรอยต่อ
        depth = np.zeros(water.shape, dtype=np.uint8)
        depth[8:26, 12:40] = rng.integers(4, 14, size=(18, 28))
        depth[45, 5:85] = 1

        reference = R.bank_metrics(water, depth, tile_size=4096)
        self.assertGreater(reference["bank_cells"], 0)
        for tile_size in (1, 5, 16, 33):
            self.assertEqual(
                R.bank_metrics(water, depth, tile_size=tile_size), reference
            )


if __name__ == "__main__":
    unittest.main()
