import unittest

import numpy as np

import channel_sections as CS


def straight_profile(length=20, x=10, top=100, drop=0.0, radius=1.0, ident=1):
    z = np.arange(2, 2 + length, dtype=np.int32)
    stage = np.rint(top - drop * np.arange(length)).astype(np.int32)
    return {
        "x": np.full(length, x, dtype=np.int32),
        "z": z,
        "stage": stage,
        "radius": radius,
        "kind": 3,
        "ident": ident,
    }


class SectionShapeTests(unittest.TestCase):
    def test_bed_is_deepest_in_the_middle_and_shallow_at_the_edge(self):
        """ก้นน้ำต้องเป็นร่อง ไม่ใช่รางสี่เหลี่ยม — วัดได้ว่าของเดิมแบน 51%"""
        middle = CS.bed_depth_across(0, half_width=3.0, max_depth=4)
        edge = CS.bed_depth_across(3, half_width=3.0, max_depth=4)

        self.assertGreater(int(middle), int(edge))
        self.assertGreaterEqual(int(edge), 1)

    def test_bank_follows_the_local_hillside_slope(self):
        """ตลิ่งต้องไต่ด้วยความชันของเนินที่มันตัดผ่าน ไม่งั้นรอยต่อหักมุม"""
        gentle = CS.bank_height_at(5, shore_end=2, slope=0.5)
        steep = CS.bank_height_at(5, shore_end=2, slope=2.0)

        self.assertAlmostEqual(float(gentle), CS.SHORE_RISE + 3 * 0.5, places=5)
        self.assertAlmostEqual(float(steep), CS.SHORE_RISE + 3 * 2.0, places=5)

    def test_shore_band_stays_flat_next_to_the_water(self):
        """ในแถบชายฝั่ง ความสูงต้องคงที่ ไม่ไต่ขึ้นทันที"""
        at_edge = CS.bank_height_at(2, shore_end=2, slope=2.0)
        self.assertEqual(float(at_edge), float(CS.SHORE_RISE))

    def test_nearest_section_wins_so_bends_do_not_alternate(self):
        offsets = list(CS.section_offsets(2))
        steps = [step for step, _sign in offsets]
        self.assertEqual(steps, sorted(steps))
        self.assertEqual(offsets[0], (0, 1))

    def test_stamping_a_straight_stream_gives_water_shore_and_bank(self):
        """หน้าตัดที่ประทับต้องได้ลำดับ น้ำ -> ชายฝั่ง -> ตลิ่ง เสมอ"""
        # พื้นเดิมสูงกว่าผิวน้ำไม่มาก = เคสที่ต้องมีชายฝั่งทั้งสองฝั่งแน่นอน
        terrain = np.full((24, 24), 103, dtype=np.int32)
        plan = CS.plan_from_profile(
            straight_profile(top=100), terrain, np.full((24, 24), 1.0)
        )
        out = CS.stamp_sections([plan], terrain)

        row = 10
        water = out["water"][row]
        self.assertTrue(water.any(), "ไม่มีน้ำเลย")
        wet = np.flatnonzero(water)
        # ผิวน้ำในหน้าตัดเดียวกันต้องเท่ากัน
        self.assertEqual(len(set(out["surface"][row, wet].tolist())), 1)
        # ก้นน้ำต้องอยู่ใต้ผิวน้ำ
        self.assertTrue((out["terrain"][row, wet] < out["surface"][row, wet]).all())
        # ต้องมีชายฝั่งที่สูงกว่าผิวน้ำไม่เกิน SHORE_RISE ทั้งสองฝั่ง
        level = int(out["surface"][row, wet[0]])
        for side in (wet.min() - 1, wet.max() + 1):
            self.assertLessEqual(
                int(out["terrain"][row, side]) - level, CS.SHORE_RISE,
                "ไม่มีชายฝั่ง — น้ำชนผนังทันที",
            )

    def test_stamping_never_digs_below_the_bed_it_declared(self):
        """ความลึกที่ประกาศคือความลึกที่ได้ ไม่มีกลไกไหนขุดต่อ"""
        terrain = np.full((24, 24), 100, dtype=np.int32)
        plan = CS.plan_from_profile(
            straight_profile(top=100, radius=2.0), terrain,
            np.full((24, 24), 1.0),
        )
        out = CS.stamp_sections([plan], terrain)

        wet = out["water"]
        cut = out["surface"][wet] - out["terrain"][wet]
        self.assertLessEqual(
            int(cut.max()), CS.MAX_BED_DEPTH + CS.POOL_EXTRA_DEPTH
        )

    def test_a_real_cliff_beside_the_water_is_not_bulldozed(self):
        """ลำน้ำที่ไหลเลียบตีนผา ต้องไม่ไถผาลงมาหาระดับน้ำ

        เคยเกิดจริง: cell ถูกกด 48 บล็อกจนตัวเลข "ผนัง/ไหล่เขา" พุ่งเป็น 48 เท่า
        หน้าผาเป็นของภูมิประเทศ ไม่ใช่ของลำน้ำ
        """
        terrain = np.full((24, 24), 100, dtype=np.int32)
        terrain[:, 13:] = 150                      # ผาสูง 50 บล็อกชิดลำน้ำ
        plan = CS.plan_from_profile(
            straight_profile(top=100, x=10), terrain, np.full((24, 24), 1.0)
        )

        out = CS.stamp_sections([plan], terrain)
        cut = terrain - out["terrain"]

        self.assertLessEqual(
            int(cut[:, 13:].max()), CS.BANK_MAX_CUT,
            "ไถหน้าผาลงมาหาระดับน้ำ",
        )

    def test_no_step_is_left_at_the_edge_of_the_shaped_band(self):
        """ขอบแถบที่เราดัด ต้องกลืนกับพื้นเดิม ไม่ใช่จบด้วยผนัง

        เพดานการกดแบบตัดจบทิ้งผนังไว้ตรงรอยต่อ — วัดที่ hill_junction ได้ขั้น
        >=3 ที่ขอบแถบ 3,259 จุด ซึ่งคือรอยที่ตาอ่านว่า "ถูกเจาะ"
        """
        # ผังต้องสมจริง: พื้นลาดลง *ตามทิศน้ำ* และผิวน้ำเกาะพื้น (ต่ำกว่า 1 บล็อก)
        # เหมือน profile จริงที่วัดได้ (p50 ต่ำกว่า DEM 1 บล็อก)
        # ฟิกซ์เจอร์เดิมตั้งผิวน้ำคงที่ขวางเนิน = น้ำอยู่ใต้ดิน 7 บล็อกที่ต้นสาย
        terrain = np.zeros((30, 30), dtype=np.int32)
        terrain[:] = (140 - np.arange(30, dtype=np.int32))[:, None]
        profile = straight_profile(top=139, x=15, length=26, drop=1.0)
        plan = CS.plan_from_profile(
            profile, terrain, np.full((30, 30), 1.0),
        )

        out = CS.stamp_sections([plan], terrain)
        shaped = out["terrain"]
        touched = np.abs(shaped - terrain) > 1

        # ตัดหัวท้ายของเส้นออกจากการนับ: ที่ต้นสาย/ปลายสายลำน้ำต้องเริ่มจากที่ไหน
        # สักที่อยู่แล้ว การเฟด (END_FADE_SAMPLES) ลดขั้นตรงนั้นให้เหลือ 1 จุด
        # แต่กำจัดไม่ได้ทั้งหมดเพราะ profile จบลงกลางเนินจริง ๆ
        interior = np.zeros(shaped.shape, dtype=bool)
        interior[8:-8, :] = True

        steps = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]), (np.s_[:, 1:], np.s_[:, :-1]),
        ):
            edge = touched[dst] & ~touched[src] & interior[dst]
            steps += int((edge & (np.abs(shaped[dst] - shaped[src]) >= 3)).sum())
        self.assertEqual(
            steps, 0, "ยังมีผนังตรงรอยต่อกับพื้นเดิมกลางสาย",
        )

    def test_lower_stream_wins_where_two_sections_meet(self):
        """จุดบรรจบ: น้ำต้องอยู่ที่ระดับของสายที่ต่ำกว่า ไม่ใช่ลอยอยู่เหนือกัน"""
        terrain = np.full((24, 24), 120, dtype=np.int32)
        slope = np.full((24, 24), 1.0)
        high = CS.plan_from_profile(straight_profile(top=100, x=10), terrain, slope)
        low = CS.plan_from_profile(straight_profile(top=95, x=10), terrain, slope)

        out = CS.stamp_sections([high, low], terrain)
        wet = out["water"]

        self.assertTrue(wet.any())
        self.assertEqual(int(out["surface"][wet].max()), 95)

    def test_protected_cells_are_never_touched(self):
        """ทะเลสาบที่ขึ้นรูปแล้วห้ามถูกลำน้ำเขียนทับ"""
        terrain = np.full((24, 24), 120, dtype=np.int32)
        protect = np.zeros((24, 24), dtype=bool)
        protect[:, 10] = True
        plan = CS.plan_from_profile(
            straight_profile(x=10), terrain, np.full((24, 24), 1.0)
        )

        out = CS.stamp_sections([plan], terrain, protect=protect)

        self.assertFalse(out["water"][:, 10].any(), "เขียนทับพื้นที่สงวน")
        np.testing.assert_array_equal(out["terrain"][:, 10], terrain[:, 10])

    def test_bank_is_not_raised_above_the_original_hillside(self):
        """ห้ามยกพื้นเป็นสันรอบลำน้ำ — บั๊กเก่าที่เคยได้สันดินรอบทะเลสาบ"""
        terrain = np.full((24, 24), 100, dtype=np.int32)
        terrain[:, 14:] = 130                      # เนินสูงอยู่ข้างหนึ่ง
        plan = CS.plan_from_profile(
            straight_profile(top=99, x=10), terrain, np.full((24, 24), 1.0)
        )

        out = CS.stamp_sections([plan], terrain)
        dry = ~out["water"]

        raised = out["terrain"][dry] > terrain[dry]
        # ยกได้เฉพาะเพื่อกันน้ำรั่ว คือไม่เกินระดับผิวน้ำ + SHORE_RISE
        self.assertTrue(
            (out["terrain"][dry][raised] <= 99 + CS.SHORE_RISE).all(),
            "ยกพื้นสูงเกินความจำเป็น",
        )


if __name__ == "__main__":
    unittest.main()


class SectionOwnershipTests(unittest.TestCase):
    """ใครเป็นเจ้าของ cell — เรื่องนี้ตัดสินทั้งความราบของหน้าตัดและตัววัด"""

    def test_when_two_stations_hit_one_cell_the_lower_stage_wins(self):
        """profile ถูก sample ทุก 0.5 บล็อก สองสถานีตกช่องเดียวกันเป็นเรื่องปกติ

        กติกาที่ประกาศคือระดับต่ำกว่าชนะ ไม่ใช่ "คนเขียนทีหลังชนะ" ซึ่ง numpy
        ไม่รับประกัน  ผังนี้ไล่ระดับ *ขึ้น* ตามลำดับ array เพื่อให้สองกติกาให้
        คำตอบต่างกัน: ถ้าพึ่งลำดับจะได้ 105 ถ้าบังคับกติกาจะได้ 100
        """
        terrain = np.full((24, 24), 130, dtype=np.int32)
        profile = {
            "x": np.full(8, 10, dtype=np.int32),
            "z": np.repeat(np.arange(4, 8, dtype=np.int32), 2),
            "stage": np.array([100, 105] * 4, dtype=np.int32),
            "radius": 1.0,
            "kind": 3,
            "ident": 1,
        }
        plan = CS.plan_from_profile(profile, terrain, np.full((24, 24), 1.0))

        out = CS.stamp_sections([plan], terrain)
        wet = out["water"]

        self.assertTrue(wet.any())
        self.assertEqual(int(out["surface"][wet].max()), 100)

    def test_every_water_cell_names_its_section(self):
        """cell น้ำที่ไม่มีเจ้าของ = ตัววัดต้องเดา ซึ่งเป็นที่มาของตัวเลขผิด"""
        terrain = np.full((24, 24), 120, dtype=np.int32)
        plan = CS.plan_from_profile(
            straight_profile(x=10), terrain, np.full((24, 24), 1.0)
        )

        out = CS.stamp_sections([plan], terrain)

        self.assertTrue((out["section"][out["water"]] > 0).all())
        self.assertFalse(out["section"][~out["water"]].any())

    def test_sections_of_different_lines_do_not_share_ids(self):
        """สองสายที่วางคู่กันต้องไม่ถูกนับเป็นหน้าตัดเดียวกัน"""
        terrain = np.full((24, 24), 120, dtype=np.int32)
        slope = np.full((24, 24), 1.0)
        a = CS.plan_from_profile(
            straight_profile(x=6, top=100, ident=1), terrain, slope)
        b = CS.plan_from_profile(
            straight_profile(x=16, top=100, ident=2), terrain, slope)

        out = CS.stamp_sections([a, b], terrain)
        left = set(np.unique(out["section"][:, :11][out["water"][:, :11]]).tolist())
        right = set(np.unique(out["section"][:, 11:][out["water"][:, 11:]]).tolist())

        self.assertTrue(left and right)
        self.assertFalse(left & right, "id ของสองสายชนกัน")
