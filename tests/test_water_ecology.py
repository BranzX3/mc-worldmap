import unittest

import numpy as np

import water_ecology as WE


def field(values):
    return np.asarray(values, dtype=np.int32)[:, None].repeat(3, axis=1)


class FlowClassTests(unittest.TestCase):
    def test_boundaries_land_where_the_names_say(self):
        flow = field([0, 5, 6, 20, 21, 60, 61, 255])
        got = WE.flow_class(flow)[:, 0].tolist()

        self.assertEqual(
            got,
            [WE.STILL, WE.STILL, WE.SLACK, WE.SLACK,
             WE.BRISK, WE.BRISK, WE.FAST, WE.FAST],
        )


class LakeFetchTests(unittest.TestCase):
    def test_fetch_counts_open_water_along_each_cardinal_axis(self):
        lake = np.zeros((7, 9), dtype=bool)
        lake[2:5, 1:8] = True
        fetch = WE.lake_fetch(lake)

        self.assertEqual(int(fetch[3, 1]), 7)
        self.assertEqual(int(fetch[3, 4]), 7)
        self.assertEqual(int(fetch[2, 1]), 7)
        self.assertEqual(int(fetch[0, 0]), 0)

    def test_fetch_does_not_cross_a_land_break(self):
        lake = np.zeros((3, 9), dtype=bool)
        lake[1, :3] = True
        lake[1, 5:] = True
        fetch = WE.lake_fetch(lake)

        self.assertEqual(int(fetch[1, 1]), 3)
        self.assertEqual(int(fetch[1, 6]), 4)


class StreambedTests(unittest.TestCase):
    """ตะกอนที่ละเอียดกว่าที่แรงน้ำจะพัดไปได้เท่านั้นที่อยู่ได้"""

    def bed(self, flow, depth=2, texture=0.5, pool=False):
        shape = (4, 4)
        return WE.streambed_materials(
            np.full(shape, flow, dtype=np.int32),
            np.full(shape, depth, dtype=np.int32),
            np.ones(shape, dtype=bool),
            pool_mask=np.full(shape, pool, dtype=bool),
            texture=np.full(shape, texture, dtype=np.float32),
        )

    def test_fast_water_never_leaves_fine_sediment(self):
        fine = {WE.LAKEBED[n] for n in ("sand", "clay", "mud")}
        for texture in (0.0, 0.3, 0.5, 0.7, 0.95):
            got = set(np.unique(self.bed(255, texture=texture)).tolist())
            self.assertFalse(
                got & fine, f"น้ำแรงยังเหลือตะกอนละเอียดที่ texture={texture}"
            )

    def test_still_water_can_hold_fine_sediment(self):
        got = set()
        for texture in (0.1, 0.5, 0.75, 0.9):
            got |= set(np.unique(self.bed(0, depth=3, texture=texture)).tolist())

        self.assertTrue(got & {WE.LAKEBED[n] for n in ("sand", "clay", "mud")})

    def test_the_bed_coarsens_as_the_water_speeds_up(self):
        """ลำดับต้องเป็นทางเดียว: เร็วขึ้น = หยาบขึ้น ไม่ใช่สลับไปมา"""
        coarseness = {
            WE.LAKEBED["mud"]: 0, WE.LAKEBED["clay"]: 1,
            WE.LAKEBED["sand"]: 2, WE.LAKEBED["gravel"]: 3,
            WE.LAKEBED["cobble"]: 4, WE.LAKEBED["stone"]: 5,
        }
        for texture in (0.2, 0.5, 0.8):
            scores = [
                coarseness[int(self.bed(flow, texture=texture)[0, 0])]
                for flow in (0, 15, 40, 220)
            ]
            self.assertEqual(
                scores, sorted(scores),
                f"ที่ texture={texture} ก้นน้ำหยาบไม่เป็นลำดับ: {scores}",
            )

    def test_a_plunge_pool_is_scoured_even_though_its_surface_is_calm(self):
        calm = self.bed(0, depth=4, texture=0.9)[0, 0]
        pool = self.bed(0, depth=4, texture=0.9, pool=True)[0, 0]

        self.assertEqual(int(calm), WE.LAKEBED["mud"])
        self.assertIn(int(pool), (WE.LAKEBED["gravel"], WE.LAKEBED["cobble"]))

    def test_dry_cells_are_left_alone(self):
        shape = (3, 3)
        water = np.zeros(shape, dtype=bool)
        water[1, 1] = True
        out = WE.streambed_materials(
            np.full(shape, 200, dtype=np.int32),
            np.full(shape, 2, dtype=np.int32),
            water,
        )

        self.assertEqual(int((out != WE.LAKEBED["stream"]).sum()), 1)


class PlantGateTests(unittest.TestCase):
    def test_plants_drop_out_as_the_current_rises(self):
        flow = field([0, 15, 40, 200])
        floating = WE.floating_plants_allowed(flow)[:, 0].tolist()
        submerged = WE.submerged_plants_allowed(flow)[:, 0].tolist()

        self.assertEqual(floating, [True, False, False, False])
        self.assertEqual(submerged, [True, True, False, False])

    def test_spray_zone_is_only_the_loud_water(self):
        flow = field([0, 40, 200])

        self.assertEqual(
            WE.spray_zone(flow)[:, 0].tolist(), [False, False, True]
        )


class HistogramTests(unittest.TestCase):
    def test_histogram_reports_shares_that_add_up(self):
        flow = np.asarray([[0, 30], [100, 200]], dtype=np.int32)
        water = np.ones((2, 2), dtype=bool)

        report = WE.histogram(flow, water)

        self.assertEqual(report["cells"], 4)
        self.assertAlmostEqual(sum(report["share"].values()), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
