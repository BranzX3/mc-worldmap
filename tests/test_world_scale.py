"""ค่าคงที่ของสเกลโลก — ทั้งโปรเจกต์พึ่งความสอดคล้องของค่าพวกนี้

การเปลี่ยน Y_TERRAIN_* หรือ WORLD_HEIGHT โดยไม่ระวังทำให้ภูมิประเทศทะลุขอบ
โลก หรือทำให้ datapack กับสคริปต์ไม่ตรงกัน ซึ่งเป็นอาการที่จับได้ยากตอนรันจริง
"""

import unittest

import config as C
import make_world_datapack as D
import surface as S


class DimensionBoundsTests(unittest.TestCase):
    def test_config_satisfies_vanilla_dimension_rules(self):
        self.assertEqual(D.validate_config(), [])

    def test_world_height_is_a_multiple_of_a_section(self):
        self.assertEqual(C.WORLD_HEIGHT % 16, 0)
        self.assertEqual(C.WORLD_Y_MIN % 16, 0)

    def test_world_max_is_derived_not_hardcoded(self):
        self.assertEqual(C.WORLD_Y_MAX, C.WORLD_Y_MIN + C.WORLD_HEIGHT - 1)

    def test_build_ceiling_stays_inside_the_world(self):
        self.assertLess(C.Y_BUILD_CEILING, C.WORLD_Y_MAX)
        self.assertGreater(C.Y_BUILD_CEILING, C.Y_TERRAIN_MAX)

    def test_terrain_fits_with_room_for_the_tallest_tree(self):
        # ต้นไม้ที่สูงสุดในแพ็กคือ 48 บล็อก (build_terrain.CLEAR_HEADROOM = 64)
        self.assertGreaterEqual(C.Y_BUILD_CEILING - C.Y_TERRAIN_MAX, 48)

    def test_lake_beds_stay_above_bedrock(self):
        """แอ่งที่ลึกสุดใต้จุดน้ำต่ำสุดต้องไม่ทะลุพื้นโลก"""
        deepest_bed = C.Y_TERRAIN_MIN - C.WATER_MAX_DEPTH_BLOCKS
        self.assertGreater(deepest_bed, C.Y_FILL_BOTTOM)

    def test_datapack_matches_config(self):
        dim = D.dimension_type()
        self.assertEqual(dim["min_y"], C.WORLD_Y_MIN)
        self.assertEqual(dim["height"], C.WORLD_HEIGHT)
        self.assertLessEqual(dim["logical_height"], dim["height"])


class VerticalScaleTests(unittest.TestCase):
    def test_vertical_scale_matches_horizontal(self):
        """เป้าหมายของสเกลนี้คือไม่บิดเบือนสัดส่วน — ต้องอยู่ใกล้ 1.0"""
        self.assertAlmostEqual(S.vertical_distortion(), 1.0, delta=0.02)

    def test_load_meta_recomputes_scale_from_config(self):
        """meta ต้องไม่คืนค่าสเกลที่ค้างอยู่ในไฟล์"""
        meta = S.load_meta()
        blocks = C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN
        expected = (meta["elev_max_m"] - meta["elev_min_m"]) / blocks
        self.assertAlmostEqual(meta["meters_per_block_v"], expected, places=6)
        self.assertEqual(meta["y_min"], C.Y_TERRAIN_MIN)
        self.assertEqual(meta["y_max"], C.Y_TERRAIN_MAX)

    def test_water_max_depth_is_realistic_for_the_deepest_lake(self):
        """Hallstätter See ลึกจริง 125 m — ที่สเกลนี้ต้องได้ราวนั้น"""
        v = S.load_meta()["meters_per_block_v"]
        self.assertAlmostEqual(C.WATER_MAX_DEPTH_BLOCKS * v, 125.0, delta=8.0)

    def test_real_elevation_range_fits_the_terrain_band(self):
        meta = S.load_meta()
        span_m = meta["elev_max_m"] - meta["elev_min_m"]
        band_blocks = C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN
        self.assertLessEqual(span_m / C.METERS_PER_BLOCK, band_blocks + 1)


class NoHardcodedCeilingTests(unittest.TestCase):
    def test_scripts_do_not_hardcode_the_vanilla_ceiling(self):
        """318/319 เคยฝังอยู่ 7 จุด ทำให้การปลด cap ไม่มีผลกับส่วนนั้น"""
        import os
        import re

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for name in ("paint_surface.py", "build_terrain.py", "surface.py",
                     "make_water.py", "report_metrics.py"):
            path = os.path.join(here, name)
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if re.search(r"\b(318|319)\b", line):
                        offenders.append(f"{name}:{i}")
        self.assertEqual(offenders, [])


class DimensionTypeSchemaTests(unittest.TestCase):
    """dimension_type ต้องยึด schema จาก jar จริง ไม่ใช่ประกอบจากความจำ

    ครั้งแรกที่ทำ ผมเขียนเองแล้วโลกโหลดไม่ขึ้น: monster_spawn_light_level ใน
    26.2 ไม่มี "value" ซ้อน, มี has_ender_dragon_fight ที่ไม่รู้ว่ามี และฟิลด์
    อย่าง ultrawarm/natural/bed_works ย้ายเข้า attributes หมดแล้ว
    """

    def setUp(self):
        try:
            self.vanilla = D._read_json_from_jar(
                "data/minecraft/dimension_type/overworld.json"
            )
        except SystemExit as exc:
            self.skipTest(f"ไม่พบ jar ของเกม: {exc}")

    def test_only_height_fields_differ_from_vanilla(self):
        """แตะได้เฉพาะความสูง — ฟิลด์อื่นต้องเป็นของวานิลลาเป๊ะ

        การ override เป็นการแทนที่ทั้งก้อน ถ้าเผลอแก้หรือทำฟิลด์อื่นหายไป
        เกมจะปฏิเสธทั้ง registry แล้วโลกโหลดไม่ขึ้น
        """
        mine = D.dimension_type()
        allowed = {"min_y", "height", "logical_height"}
        differing = {
            k for k in set(mine) | set(self.vanilla)
            if k != "attributes" and mine.get(k) != self.vanilla.get(k)
        }
        self.assertTrue(
            differing <= allowed, f"แก้ฟิลด์ที่ไม่ควรแก้: {differing - allowed}"
        )
        self.assertEqual(mine["height"], C.WORLD_HEIGHT)
        self.assertEqual(mine["logical_height"], C.WORLD_HEIGHT)
        self.assertEqual(mine["min_y"], C.WORLD_Y_MIN)

    def test_no_vanilla_field_is_dropped(self):
        mine = D.dimension_type()
        self.assertEqual(set(self.vanilla) - set(mine), set())

    def test_clouds_are_lifted_above_the_terrain(self):
        clouds = D.dimension_type()["attributes"][
            "minecraft:visual/cloud_height"
        ]
        self.assertGreater(clouds, C.Y_TERRAIN_MAX)
        self.assertLessEqual(clouds, C.Y_BUILD_CEILING)

    def test_pack_format_comes_from_the_installed_game(self):
        fmt, _version = D.pack_format()
        pack = D.pack_mcmeta()["pack"]
        self.assertEqual(pack["pack_format"], fmt)
        # ตั้งแต่ format 81 เกมบังคับสองฟิลด์นี้ ไม่ใช่ supported_formats
        self.assertIn("min_format", pack)
        self.assertIn("max_format", pack)
        self.assertNotIn("supported_formats", pack)


if __name__ == "__main__":
    unittest.main()
