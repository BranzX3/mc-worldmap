import unittest

import numpy as np

import micro_relief as M


def ramp(shape, slope):
    """ทางลาดชันคงที่ ใช้ทดสอบว่ากฎทำงานตามความชันจริง"""
    return (np.arange(shape[1], dtype=np.float32)[None, :] * slope
            + np.zeros(shape, dtype=np.float32))


class BeddingTests(unittest.TestCase):
    def test_gentle_slopes_are_untouched(self):
        """บนที่ลาดการสแนปเป็นชั้นคือลายขั้นเทียม ต้องไม่เกิด"""
        h = ramp((48, 48), 0.2)
        delta = M.bedding_ledges(h, M.slope_blocks(h))
        self.assertLess(float(np.abs(delta).max()), 1e-5)

    def test_cliffs_get_stepped(self):
        h = ramp((48, 48), 2.0)
        delta = M.bedding_ledges(h, M.slope_blocks(h))
        self.assertGreater(float(np.abs(delta).max()), 0.5)

    def test_stepping_never_exceeds_half_a_bed(self):
        """สแนปเข้าชั้นที่ใกล้ที่สุด จึงขยับได้ไม่เกินครึ่งความหนาชั้น"""
        h = ramp((64, 64), 3.0)
        delta = M.bedding_ledges(h, M.slope_blocks(h))
        self.assertLessEqual(
            float(np.abs(delta).max()), M.BED_THICKNESS_MAX / 2 + 0.01
        )

    def test_result_is_world_coordinate_invariant(self):
        h = ramp((64, 64), 2.0)
        s = M.slope_blocks(h)
        a = M.bedding_ledges(h, s, x0=1000, z0=2000)
        b = M.bedding_ledges(h, s, x0=1000, z0=2000)
        np.testing.assert_allclose(a, b)


class TalusTests(unittest.TestCase):
    @staticmethod
    def _cliff_over_apron():
        """ผาชันครึ่งบน ต่อด้วยพื้นลาดน้อยครึ่งล่าง

        ต้องลดระดับแบบเข้มงวดตลอดแนว — ถ้ามีคู่ที่ระดับเท่ากันตรงรอยต่อ การไหล
        ลงจะหยุดตรงนั้น (ตั้งใจ: กองหินไม่ไหลข้ามพื้นราบ)
        """
        h = np.zeros((8, 64), dtype=np.float32)
        h[:, :32] = np.arange(32, dtype=np.float32) * 0.1          # ตีนผา ลาดน้อย
        h[:, 32:] = 3.1 + (np.arange(32, dtype=np.float32) + 1) * 2.0  # หน้าผา
        return h

    def test_talus_accumulates_below_a_cliff(self):
        h = self._cliff_over_apron()
        delta = M.talus_apron(h, M.slope_blocks(h))
        below = float(delta[:, 24:32].mean())
        far = float(delta[:, :8].mean())
        self.assertGreater(below, 0.2)
        self.assertGreater(below, far)

    def test_talus_only_adds_material(self):
        h = self._cliff_over_apron()
        delta = M.talus_apron(h, M.slope_blocks(h))
        self.assertGreaterEqual(float(delta.min()), 0.0)

    def test_flat_terrain_grows_no_talus(self):
        h = np.zeros((16, 16), dtype=np.float32)
        delta = M.talus_apron(h, M.slope_blocks(h))
        self.assertLess(float(np.abs(delta).max()), 1e-5)


class DolineTests(unittest.TestCase):
    def test_dolines_only_dig_downward(self):
        h = np.zeros((64, 64), dtype=np.float32)
        elev = np.full((64, 64), 1800.0, dtype=np.float32)
        delta = M.karst_dolines(h, M.slope_blocks(h), elev)
        self.assertLessEqual(float(delta.max()), 0.0)

    def test_no_dolines_below_the_limestone_plateau(self):
        h = np.zeros((64, 64), dtype=np.float32)
        elev = np.full((64, 64), 800.0, dtype=np.float32)
        delta = M.karst_dolines(h, M.slope_blocks(h), elev)
        self.assertLess(float(np.abs(delta).max()), 1e-5)

    def test_no_dolines_on_steep_ground(self):
        h = ramp((64, 64), 1.5)
        elev = np.full((64, 64), 1900.0, dtype=np.float32)
        delta = M.karst_dolines(h, M.slope_blocks(h), elev)
        self.assertLess(float(np.abs(delta).max()), 1e-5)


class ApplyTests(unittest.TestCase):
    def test_protected_cells_never_move(self):
        """ผืนน้ำต้องไม่ขยับ ไม่งั้นระดับน้ำกับก้นที่คำนวณไว้จะไม่ตรงกัน"""
        h = ramp((48, 48), 1.8)
        elev = np.full(h.shape, 1900.0, dtype=np.float32)
        protect = np.zeros(h.shape, dtype=bool)
        protect[:, 10:20] = True
        out, _stats = M.apply(h, elev, protect=protect)
        np.testing.assert_allclose(out[:, 10:20], h[:, 10:20], atol=1e-5)

    def test_stats_report_each_feature(self):
        h = ramp((48, 48), 1.8)
        elev = np.full(h.shape, 1900.0, dtype=np.float32)
        _out, stats = M.apply(h, elev)
        self.assertEqual(set(stats), {"bedding", "talus", "doline"})
        for item in stats.values():
            self.assertIn("area", item)
            self.assertIn("max", item)

    def test_features_can_be_disabled(self):
        h = ramp((48, 48), 1.8)
        elev = np.full(h.shape, 1900.0, dtype=np.float32)
        out, stats = M.apply(h, elev, features=())
        np.testing.assert_allclose(out, h)
        self.assertEqual(stats, {})




class LateBindingTests(unittest.TestCase):
    """ค่าคงที่ต้องอ่านตอนเรียก ไม่ใช่ผูกเป็น default argument

    Python ประเมิน default ครั้งเดียวตอนนิยามฟังก์ชัน การเขียน
    `def f(..., strength=BED_STRENGTH)` ทำให้การแก้ค่าโมดูลทีหลังไม่มีผลเลย
    บั๊กนี้ทำให้การจูน strength ทั้งชุดเป็นโมฆะโดยไม่มีอะไรฟ้อง — ผลออกมา
    เท่ากันเป๊ะทุกค่า ซึ่งเป็นสัญญาณเดียวที่มี
    """

    def test_bed_strength_is_read_at_call_time(self):
        h = ramp((32, 32), 2.0)
        s = M.slope_blocks(h)
        original = M.BED_STRENGTH
        try:
            M.BED_STRENGTH = 1.0
            full = M.bedding_ledges(h, s)
            M.BED_STRENGTH = 0.25
            weak = M.bedding_ledges(h, s)
        finally:
            M.BED_STRENGTH = original
        self.assertGreater(
            float(np.abs(full).max()), float(np.abs(weak).max()) * 2
        )

    def test_talus_steps_is_read_at_call_time(self):
        h = np.zeros((4, 40), dtype=np.float32)
        h[:, :20] = np.arange(20, dtype=np.float32) * 0.1
        h[:, 20:] = 2.1 + (np.arange(20, dtype=np.float32) + 1) * 2.0
        original = M.TALUS_STEPS
        try:
            M.TALUS_STEPS = 1
            near = M.talus_apron(h, M.slope_blocks(h))
            M.TALUS_STEPS = 14
            far = M.talus_apron(h, M.slope_blocks(h))
        finally:
            M.TALUS_STEPS = original
        self.assertGreater(float(far.sum()), float(near.sum()))


class BedGeometryTests(unittest.TestCase):
    def test_bed_geometry_is_piecewise_constant(self):
        """ความหนาชั้นต้องคงที่เป็นผืน ไม่ไล่ต่อเนื่อง

        ถ้าไล่ต่อเนื่อง ระดับชั้นหินจะเลื่อน 0.08 บล็อกต่อบล็อกแนวนอน สะสมครบ
        หนึ่งบล็อกใน 12 ก้าว ชานหินจึงขาดตลอดและ bedding แทบไม่ได้อะไรเลย
        """
        field = M._noise01((64, 64), 0, 0, M.BED_SCALE_BLOCKS, M.BED_SEED)
        step = M._quantise(field, M.BED_LEVELS)
        self.assertLessEqual(len(np.unique(step)), M.BED_LEVELS)
        changes = float((np.diff(step, axis=1) != 0).mean())
        self.assertLess(changes, 0.02, "ค่าเปลี่ยนถี่เกินกว่าจะเป็นผืน")

    def test_quantise_covers_the_full_range(self):
        field = np.linspace(0.0, 0.999, 200, dtype=np.float32)
        step = M._quantise(field, 4)
        self.assertAlmostEqual(float(step.min()), 0.0)
        self.assertAlmostEqual(float(step.max()), 1.0)


if __name__ == "__main__":
    unittest.main()
