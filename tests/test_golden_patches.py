import os
import unittest

import numpy as np

import golden_patches as G
from hydrology_shape import UNRESOLVED


def make_patch(size=12):
    """แพตช์สังเคราะห์ที่ 'สะอาด': ลำน้ำตรง ผิวน้ำราบ ตลิ่งสูงกว่าน้ำเล็กน้อย

    `terrain_y` ของ cell ที่เป็นน้ำเท่ากับ *ผิวน้ำ* เหมือนที่
    `shape_waterway_patch` ทำจริง ส่วนความลึกอยู่ใน `depth`
    """
    water = np.zeros((size, size), dtype=bool)
    water[:, 5:7] = True
    surface = np.full((size, size), 100, dtype=np.int16)
    terrain = np.full((size, size), 102, dtype=np.int16)
    terrain[water] = 100
    depth = np.zeros((size, size), dtype=np.uint8)
    depth[water] = 2
    return {
        "water_mask": water,
        "waterway_mask": water.copy(),
        "surface_y": surface,
        "terrain_y": terrain,
        "depth": depth,
        "waterfall_top_y": np.full((size, size), UNRESOLVED, dtype=np.int16),
        "bounds": np.asarray([0, size, 0, size]),
    }


class GoldenPatchMetricTests(unittest.TestCase):
    def test_a_clean_patch_violates_nothing(self):
        patch = make_patch()
        base = np.full(patch["terrain_y"].shape, 102, dtype=np.int16)

        metrics = G.patch_metrics(patch, base)

        for key in G.MUST_BE_ZERO:
            self.assertEqual(metrics[key], 0, f"{key} ควรเป็นศูนย์บนผังที่สะอาด")
        self.assertEqual(metrics["water_cells"], int(patch["water_mask"].sum()))

    def test_a_dry_bank_below_the_water_is_counted(self):
        """ขอบน้ำลอย — มองจากด้านข้างเห็นเป็นผนังน้ำไม่มีอะไรกั้น"""
        patch = make_patch()
        patch["terrain_y"][4, 4] = 97          # ต่ำกว่าผิวน้ำที่ 100
        base = np.full(patch["terrain_y"].shape, 102, dtype=np.int16)

        self.assertGreater(
            G.patch_metrics(patch, base)["dry_bank_below_water"], 0
        )

    def test_a_big_drop_without_a_curtain_is_counted(self):
        """ผิวน้ำตก 4 บล็อกโดยไม่มีม่าน = น้ำขาดเป็นช่องกลางสาย"""
        patch = make_patch()
        patch["surface_y"][6:, :] -= 4
        base = np.full(patch["terrain_y"].shape, 102, dtype=np.int16)

        self.assertGreater(G.patch_metrics(patch, base)["uncovered_drop"], 0)

    def test_a_curtain_covers_its_own_drop(self):
        """ขั้นเดียวกันแต่มีม่านสูงพอ ต้องไม่ถูกนับ"""
        patch = make_patch()
        patch["surface_y"][6:, :] -= 4
        top = patch["waterfall_top_y"]
        top[6, 5:7] = patch["surface_y"][6, 5:7] + 4
        base = np.full(patch["terrain_y"].shape, 102, dtype=np.int16)

        self.assertEqual(G.patch_metrics(patch, base)["uncovered_drop"], 0)

    def test_an_uneven_cross_section_is_counted(self):
        """ผิวน้ำสองฝั่งของหน้าตัดเดียวกันไม่เท่ากัน = ขั้นวิ่งขนานลำน้ำ"""
        patch = make_patch()
        patch["surface_y"][:, 6] += 1
        base = np.full(patch["terrain_y"].shape, 102, dtype=np.int16)

        self.assertGreater(
            G.patch_metrics(patch, base)["unflat_cross_runs"], 0
        )

    def test_terrain_lift_and_cut_are_reported_separately(self):
        """สันดิน (ยก) กับร่องลึก (ขุด) เป็นคนละ artifact ห้ามหักลบกัน"""
        patch = make_patch()
        base = patch["terrain_y"].astype(np.int16).copy()
        patch["terrain_y"][0, 0] += 5
        patch["terrain_y"][1, 0] -= 9

        metrics = G.patch_metrics(patch, base)

        self.assertEqual(metrics["terrain_lift_max"], 5)
        self.assertEqual(metrics["terrain_cut_max"], 9)

    def test_a_slot_canyon_is_measured_from_both_rims(self):
        """ยืนในลำน้ำแล้วผนังสองฝั่งสูงกว่าหัวเท่าไร — ตัวเลขที่แทนภาพในเกม

        ต้องใช้ฝั่งที่ *เตี้ยกว่า* ของสองฝั่ง ถ้าใช้ฝั่งสูงอย่างเดียว ลำธารที่
        ไหลเลียบตีนผา (เปิดโล่งข้างหนึ่ง) จะถูกนับเป็นหุบทั้งที่เดินออกได้และ
        เห็นวิวเต็มตา
        """
        patch = make_patch(size=20)
        base = patch["terrain_y"].astype(np.int16).copy()
        patch["terrain_y"][:, :5] = 140            # ผนังฝั่งซ้าย 40 บล็อก
        patch["terrain_y"][:, 7:] = 140            # ผนังฝั่งขวา 40 บล็อก

        deep = G.patch_metrics(patch, base)
        self.assertGreaterEqual(deep["canyon_depth_p50"], 30)
        self.assertGreater(deep["canyon_share"], 0.9)

        patch["terrain_y"][:, 7:] = 102            # เปิดฝั่งขวาออก
        open_side = G.patch_metrics(patch, base)
        # เหลือแค่ความสูงของตลิ่งฝั่งที่เปิด (2 บล็อก) ไม่ใช่ผนัง 40
        self.assertLess(open_side["canyon_depth_p50"], G.CANYON_DEPTH)
        self.assertEqual(open_side["canyon_share"], 0.0)

    def test_a_bank_you_cannot_walk_along_is_counted(self):
        """เดินเลียบฝั่งแล้วต้องปีนเกิน 1 บล็อก = ตลิ่งเป็นบันไดหิน"""
        patch = make_patch(size=20)
        base = patch["terrain_y"].astype(np.int16).copy()
        patch["terrain_y"][10:, 4] += 5            # ตลิ่งฝั่งซ้ายกระโดดขึ้น 5

        metrics = G.patch_metrics(patch, base)

        self.assertGreater(metrics["bank_unwalkable_share"], 0.0)
        self.assertGreaterEqual(metrics["bank_climb_max"], 5)

    def test_water_column_comes_from_depth_not_from_terrain(self):
        """`terrain_y` ของ cell ที่เป็นน้ำคือผิวน้ำ ไม่ใช่ก้นน้ำ

        สูตร `surface - terrain` จึงให้ 1 ทุก cell ทั้งแผนที่ ซึ่งดูปกติจนไม่มี
        ใครสงสัย — เคยเขียนผิดแบบนี้มาแล้วในไฟล์นี้เอง
        """
        patch = make_patch()
        patch["depth"][patch["waterway_mask"]] = 9
        base = patch["terrain_y"].astype(np.int16).copy()

        self.assertEqual(
            G.patch_metrics(patch, base)["water_column_max"], 9
        )

    def test_a_deep_lake_is_not_reported_as_a_water_column(self):
        """ทะเลสาบลึกเป็นเรื่องปกติ ลำธารลึกเท่ากันไม่ปกติ — ต้องแยกกัน"""
        patch = make_patch()
        patch["waterway_mask"] = np.zeros_like(patch["water_mask"])
        patch["depth"][patch["water_mask"]] = 25
        base = patch["terrain_y"].astype(np.int16).copy()

        self.assertEqual(
            G.patch_metrics(patch, base)["water_column_max"], 0
        )

    def test_the_rim_search_does_not_wrap_around_the_patch(self):
        """ผนังที่อยู่คนละฟากของกรอบ ห้ามถูกนับเป็นผนังของ cell ริมขอบ

        `np.roll` วนขอบ ผลคือ cell ริมขวาซึ่งฝั่งขวาเปิดออกนอกกรอบ ได้ค่าหุบ
        40 บล็อกจากผนังที่คอลัมน์ 0 — แถบผิดกว้าง CANYON_REACH รอบทุก patch
        """
        terrain = np.full((9, 9), 100, dtype=np.int32)
        terrain[:, 7] = 140                    # ผนังจริงฝั่งซ้ายของน้ำ
        terrain[:, 0] = 140                    # ผนังคนละฟาก ไม่ควรเกี่ยวกัน
        water = np.zeros((9, 9), dtype=bool)
        water[:, 8] = True
        surface = np.full((9, 9), 100, dtype=np.int32)

        got = G.canyon_depth(terrain, water, surface)

        self.assertEqual(int(got[4, 8]), 0, "ผนังพันขอบมาจากอีกฟากของกรอบ")

    def test_metrics_ignore_the_patch_border(self):
        """ขอบกรอบไม่ถูกนับ — ไม่งั้นตัวเลขเด้งตามขนาดกรอบ ไม่ใช่ตามคุณภาพงาน"""
        size = 2 * G.CORE_MARGIN + 10
        patch = make_patch(size=size)
        base = patch["terrain_y"].astype(np.int16).copy()
        patch["terrain_y"][0, 0] -= 40         # ความเสียหายที่มุมนอกสุด

        metrics = G.patch_metrics(patch, base)

        self.assertEqual(metrics["terrain_cut_max"], 0)
        core = G.core_mask((size, size))
        self.assertFalse(core[0, 0])
        self.assertTrue(core[size // 2, size // 2])

    def test_shape_cache_is_invalidated_when_the_shaper_changes(self):
        """ลายนิ้วมือต้องเปลี่ยนเมื่อโค้ดที่สร้าง patch เปลี่ยน

        ถ้าไม่เปลี่ยน `--tag after` จะหยิบ npz ที่ shape ไว้ก่อนแก้โค้ดมาใช้ต่อ
        แล้ว compare ก็รายงานว่า "ไม่มีอะไรแย่ลง" ทั้งที่ยังไม่ได้วัดของใหม่เลย
        """
        spec = G.GOLDEN_PATCHES[0]
        before = G.shape_fingerprint(spec)
        path = os.path.join(G.HERE, "hydrology_shape.py")
        with open(path, "rb") as f:
            original = f.read()
        try:
            with open(path, "ab") as f:
                f.write(b"\n# fingerprint probe\n")
            after = G.shape_fingerprint(spec)
        finally:
            with open(path, "wb") as f:
                f.write(original)

        self.assertNotEqual(before, after)
        self.assertEqual(G.shape_fingerprint(spec), before)

    def test_every_must_be_zero_metric_is_scored_as_a_regression(self):
        """ตัวเลขที่ต้องเป็นศูนย์ ต้องถูกนับเป็น 'แย่ลง' ตอน compare ด้วย

        ไม่งั้นค่าที่พุ่งขึ้นจะผ่าน compare ไปเงียบ ๆ ซึ่งเป็นวิธีที่ regression
        เล็ดลอดมาแล้วในโปรเจกต์นี้
        """
        self.assertTrue(set(G.MUST_BE_ZERO).issubset(G.WORSE_WHEN_UP))

    def test_camera_stands_on_dry_land_and_faces_the_water(self):
        patch = make_patch(size=40)
        spec = {"name": "t", "x": 20, "z": 20, "size": 40}

        cx, cz, yaw, pitch = G.choose_camera(patch, spec)

        water = patch["water_mask"]
        self.assertFalse(
            bool(water[np.clip(cz, 0, 39), np.clip(cx, 0, 39)]),
            "กล้องยืนอยู่ในน้ำ",
        )
        # เดินตามทิศที่กล้องหันไป ต้องเจอน้ำ ไม่งั้นภาพที่ได้ก็ตรวจอะไรไม่ได้
        dirx, dirz = np.sin(np.radians(yaw)), np.cos(np.radians(yaw))
        seen = any(
            water[
                int(np.clip(round(cz + dirz * k), 0, 39)),
                int(np.clip(round(cx + dirx * k), 0, 39)),
            ]
            for k in range(1, 120)
        )
        self.assertTrue(seen, "กล้องไม่ได้หันไปทางน้ำ")
        self.assertLessEqual(pitch, 2.0)


if __name__ == "__main__":
    unittest.main()
