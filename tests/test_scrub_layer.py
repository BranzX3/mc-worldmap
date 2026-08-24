"""ชั้นพุ่มเตี้ย (`natural=scrub|heath`) — 4.1% ของแผนที่ที่เคยไม่มีโค้ดอ้างถึง

เดิม landcover รหัส 4 ไม่ถูกอ้างใน `surface.classify()` เลย พื้นที่พุ่มจึงตกไปใช้
palette ตามระดับความสูง = ทุ่งหญ้าเรียบแบบเดียวกับที่ราบ และ `vegetation.ground_cover`
ก็ไม่มีโซนของมัน  เทสต์นี้ตรึงว่าทั้งพื้นและพืชแยกออกจากทุ่งหญ้าจริง
"""
import unittest

import numpy as np

import surface as S
import vegetation as V


def sweep(zone, damp=0.5, elev=1200.0, dense=0.3, samples=4000):
    """สุ่มพารามิเตอร์แบบกระจายทั่วช่วง แล้วนับว่าได้พืชอะไรบ้าง"""
    counts = {}
    for i in range(samples):
        r = (i % 97) / 97.0
        got = V.ground_cover(
            zone, r, ((i * 7) % 53) / 53.0, ((i * 11) % 31) / 31.0,
            ((i * 13) % 17) / 17.0, damp, dense, elev,
        )
        name = got[0] if got else None
        counts[name] = counts.get(name, 0) + 1
    return counts


class ScrubGroundTests(unittest.TestCase):
    def test_scrub_has_its_own_palette(self):
        self.assertIn("scrub", S.PALETTE)
        weights = dict(S.PALETTE["scrub"])
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)

    def test_scrub_ground_is_not_a_lawn(self):
        """พื้นพุ่มต้องมีดินหยาบ/หินโผล่มากกว่าหญ้า ไม่งั้นก็คือทุ่งหญ้าเดิม"""
        weights = dict(S.PALETTE["scrub"])
        bare = sum(
            weights.get(k, 0.0)
            for k in ("coarse", "gravel", "stone", "dirt", "podzol")
        )

        self.assertGreater(bare, weights.get("grass", 0.0))

    def test_classify_uses_the_scrub_code(self):
        shape = (64, 64)
        elev = np.full(shape, 1100.0, dtype=np.float32)
        landcover = np.full(shape, S.LC["grass"], dtype=np.uint8)
        landcover[:, 32:] = S.LC["scrub"]

        cls, _forest_p, _snow, _soil = S.classify(
            elev, landcover, 4.0, block_m=4.0, x0=500, z0=700
        )

        grass_side = set(np.unique(cls[:, :32]).tolist())
        scrub_side = set(np.unique(cls[:, 32:]).tolist())
        self.assertNotEqual(grass_side, scrub_side, "พุ่มยังหน้าตาเหมือนทุ่งหญ้า")

    def test_forest_edge_has_a_two_block_ecotone(self):
        shape = (64, 64)
        landcover = np.full(shape, S.LC["grass"], dtype=np.uint8)
        landcover[:, 30:34] = S.LC["forest"]
        mask = S.forest_ecotone_mask(landcover)

        self.assertTrue(mask[:, 28:30].any())
        self.assertTrue(mask[:, 34:36].any())
        self.assertFalse(mask[:, 30:34].any())
        self.assertFalse(mask[:, :27].any())

    def test_glacier_windows_only_appear_on_exposed_terrain(self):
        shape = (8, 8)
        glacier = np.full(shape, S.LC["glacier"], dtype=np.uint8)
        slope = np.full(shape, 20.0, dtype=np.float32)
        curvature = np.zeros(shape, dtype=np.float32)
        roll = np.zeros(shape, dtype=np.float32)
        self.assertFalse(
            S.glacier_rock_window_mask(glacier, slope, curvature, roll).any()
        )
        slope[:, 4:] = 45.0
        mask = S.glacier_rock_window_mask(glacier, slope, curvature, roll)
        self.assertTrue(mask[:, 4:].all())
        self.assertFalse(mask[:, :4].any())


class ScrubVegetationTests(unittest.TestCase):
    def test_scrub_grows_bushes_that_meadow_does_not(self):
        scrub = sweep("scrub")
        meadow = sweep("meadow")
        bushes = {"azalea", "flowering_azalea", "sweet_berry_bush"}

        self.assertTrue(bushes & set(scrub), "โซนพุ่มไม่มีพุ่มสักต้น")
        self.assertFalse(
            {"azalea", "flowering_azalea"} & set(meadow),
            "ทุ่งหญ้ามีพุ่มอัลเพนโรสด้วย = โซนไม่ได้แยกกันจริง",
        )

    def test_dry_scrub_shows_bare_ground_and_dead_bushes(self):
        dry = sweep("scrub", damp=0.2)

        self.assertIn("dead_bush", dry)
        self.assertGreater(dry.get(None, 0), 0, "แห้งแล้วยังปกคลุมเต็มผืน")

    def test_damp_scrub_turns_to_ferns_not_dead_bushes(self):
        damp = sweep("scrub", damp=0.8)

        self.assertNotIn("dead_bush", damp)
        self.assertTrue({"fern", "large_fern"} & set(damp))

    def test_scrub_is_denser_than_open_alpine_but_not_solid(self):
        scrub_empty = sweep("scrub").get(None, 0)
        alpine_empty = sweep("alpine").get(None, 0)

        self.assertLess(scrub_empty, alpine_empty, "พุ่มโล่งกว่าเหนือแนวไม้")
        self.assertGreater(scrub_empty, 0, "พุ่มปกคลุมเต็มผืนโดยไม่มีที่โล่ง")


if __name__ == "__main__":
    unittest.main()
