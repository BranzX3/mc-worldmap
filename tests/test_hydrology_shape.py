import contextlib
import io
import os
import shutil
import tempfile
import unittest

import numpy as np
from scipy import ndimage

import channel_sections as CS
import hydrology_shape as H
import hydrology_patch_io as IO
import config as C
from hydrology_shape import UNRESOLVED


class HydrologyShapeTests(unittest.TestCase):
    def test_densify_preserves_direction_and_endpoints(self):
        x, z = H.densify_polyline([0, 4, 4], [0, 0, 3], spacing=1)

        self.assertEqual((float(x[0]), float(z[0])), (0.0, 0.0))
        self.assertEqual((float(x[-1]), float(z[-1])), (4.0, 3.0))
        self.assertTrue(np.all(np.hypot(np.diff(x), np.diff(z)) <= 1.01))

    def test_isotonic_profile_removes_uphill_pool(self):
        got = H.isotonic_nonincreasing([10, 9, 11, 8])

        self.assertTrue((np.diff(got) <= 0).all())
        np.testing.assert_allclose(got, [10, 10, 10, 8])

    def test_regularized_stage_caps_single_cell_drop(self):
        got = H.regularize_stage(
            [20, 20, 20, 12, 12, 12], median_window=1,
            max_drop_per_sample=1,
        )

        self.assertTrue((np.diff(got) <= 0).all())
        self.assertGreaterEqual(int(np.diff(got).min()), -1)
        np.testing.assert_array_equal(got, [15, 14, 13, 12, 12, 12])

    def test_cardinalize_samples_bridges_diagonal_steps(self):
        x, z, values = H.cardinalize_samples(
            np.array([0, 1, 2]),
            np.array([0, 1, 1]),
            np.array([10, 9, 9]),
        )

        self.assertEqual((x[0], z[0]), (0, 0))
        self.assertEqual((x[-1], z[-1]), (2, 1))
        self.assertTrue(np.all(np.abs(np.diff(x)) + np.abs(np.diff(z)) == 1))
        self.assertTrue(np.all(np.diff(values) <= 0))

    def test_limit_masked_steps_closes_non_waterfall_gaps(self):
        mask = np.array([[1, 1, 1, 1]], dtype=bool)
        got = H.limit_masked_steps(
            np.array([[20, 18, 17, 10]], dtype=np.int16), mask
        )

        np.testing.assert_array_equal(got, [[13, 12, 11, 10]])
        self.assertLessEqual(int(np.abs(np.diff(got)).max()), 1)

    def test_cross_section_flattening_settles_instead_of_oscillating(self):
        """การยุบหน้าตัดต้องนิ่ง ไม่ใช่สลับค่าไปมาจนผลขึ้นกับจำนวนรอบ

        run ที่แตะปากทะเลสาบใช้ระดับทะเลสาบ (ยกขึ้นได้) ส่วน run อื่นใช้ค่า
        ต่ำสุด (กดลง) cell ที่อยู่ทั้งสอง run จึงถูกยกและกดสลับกันทุกรอบ ผลที่
        คืนออกมาขึ้นกับว่า ``passes`` เป็นเลขคู่หรือคี่ — ทางเดียวกับที่เคยทำให้
        --global เพี้ยนจาก --patch มาแล้ว  แก้ด้วยการให้ pin แผ่ไปทั้ง run
        """
        mask = np.zeros((4, 4), dtype=bool)
        mask[1, 1] = mask[1, 2] = mask[2, 2] = True
        surface = np.zeros((4, 4), dtype=np.int16)
        surface[1, 1] = 10
        surface[1, 2] = 9
        surface[2, 2] = 9
        locked = np.full((4, 4), UNRESOLVED, dtype=np.int16)
        locked[1, 1] = 10

        got = H.flatten_cross_sections(surface, mask, passes=4, locked=locked)

        # ทุก run ตามแกนต้องราบจริง ไม่ใช่ถูกยกและกดสลับกันจนหมดจำนวนรอบ
        for arr, flags, where in ((got, mask, "แถว"), (got.T, mask.T, "คอลัมน์")):
            for i in range(arr.shape[0]):
                idx = np.flatnonzero(flags[i])
                if idx.size >= 2 and (np.diff(idx) == 1).all():
                    values = arr[i, idx]
                    self.assertEqual(
                        len(set(values.tolist())), 1,
                        f"{where} {i} ยังไม่ราบ: {values.tolist()}",
                    )
        # นิ่งแล้วจริง — รันซ้ำต้องไม่เปลี่ยนอะไร
        np.testing.assert_array_equal(
            H.flatten_cross_sections(got, mask, passes=4, locked=locked), got
        )
        # ปากน้ำต้องไม่ถูกกดหลุดจากระดับที่ล็อกไว้
        self.assertEqual(int(got[1, 1]), 10)
        # และ locked ของผู้เรียกต้องไม่ถูกแก้ระหว่างทาง
        self.assertEqual(int(locked[1, 2]), UNRESOLVED)

    def test_flattening_refuses_to_collapse_a_stream_that_runs_along_the_axis(self):
        """run ที่ระดับต่างกันมากไม่ใช่หน้าตัด — ยุบมันคือการขุดปล่อง

        ความยาว run แยกสองอย่างนี้ไม่ได้: ลำน้ำที่วิ่งเกือบขนานแกนก็ยาวไม่เกิน
        `MAX_SECTION_WIDTH` ได้เหมือนกัน  วัดที่ hill_junction แล้วฟังก์ชันนี้
        ยุบ run ที่ต่างกัน 72 บล็อกให้เหลือค่าต่ำสุด 3,578 cell ในรอบเดียว
        แล้ว terrain ก็ถูกขุดตามลงไป = ลำธารกลายเป็นปล่องกว้าง 1 บล็อก
        """
        mask = np.zeros((3, 12), dtype=bool)
        mask[1, 1:11] = True
        surface = np.zeros((3, 12), dtype=np.int16)
        # ลำน้ำไหลลงตามแกน x ทีละบล็อก — ของจริงที่ห้ามแตะ
        surface[1, 1:11] = np.arange(40, 30, -1, dtype=np.int16)

        got = H.flatten_cross_sections(surface, mask)

        np.testing.assert_array_equal(got, surface)

    def test_lake_depth_has_shallow_shelf_and_varied_basin(self):
        z, x = np.mgrid[:128, :128]
        distance = np.minimum.reduce([x + 1, z + 1, 128 - x, 128 - z])
        got = H.lake_depth_from_distance(distance, x0=400, z0=800)

        self.assertLessEqual(int(got[0, 0]), 2)
        self.assertGreater(int(got[64, 64]), int(got[0, 0]))
        center = got[32:96, 32:96]
        self.assertGreater(len(np.unique(center)), 3)
        self.assertLess(float((center == center.max()).mean()), 0.35)

    def test_bank_lift_seals_edge_without_building_a_ridge(self):
        """ตลิ่งต้องกันน้ำรั่วได้ โดยไม่สร้างสันและไม่มีขอบคม

        สูตรเดิม ``minimum = near_stage + (d - 1)`` สูงขึ้นตามระยะแล้วตัดจบที่
        bank_width ถ้าพื้นเดิมลาดลงออกจากน้ำ ทั้งแถบจะถูกยกเป็นสันเหนือผิวน้ำ
        แล้วเกิดหน้าผาที่ขอบแถบ — วัดบนผังนี้ได้ขั้น 9 บล็อก
        """
        size, stage, blend = 96, 250, 8.0
        z, x = np.mgrid[:size, :size]
        # พื้นลาดลงออกจากกลาง = ทะเลสาบไหล่เขา ซึ่งเป็นเคสที่สร้างสันเทียม
        terrain = (
            260
            - np.maximum(0, np.abs(x - 48) - 8) * 2
            - np.maximum(0, np.abs(z - 48) - 8) * 2
        ).astype(np.int32)
        body = np.zeros((size, size), dtype=bool)
        body[40:57, 40:57] = True
        terrain[body] = stage
        surface = np.full((size, size), UNRESOLVED, dtype=np.int16)
        surface[body] = stage

        distance, indices = ndimage.distance_transform_edt(
            ~body, return_indices=True
        )
        near_stage = surface[tuple(indices)].astype(np.int32)
        shaped = H.taper_bank_lift(
            terrain, near_stage, distance, blend_blocks=blend, where=~body
        )

        # 1) กันน้ำรั่ว: บล็อกแห้งที่ติดน้ำแบบ cardinal ต้องไม่ต่ำกว่าผิวน้ำ
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            touching = (~body)[dst] & body[src]
            self.assertTrue(
                (shaped[dst][touching] >= stage).all(),
                "ขอบน้ำมีบล็อกต่ำกว่าผิวน้ำ น้ำจะไหลออก",
            )

        # 2) ห้ามยกพื้นให้สูงกว่าผิวน้ำ — สันเทียมเกิดจากตรงนี้
        lifted = shaped > terrain
        self.assertTrue(lifted.any(), "ผังทดสอบไม่ได้ยกอะไรเลย")
        self.assertTrue(
            (shaped[lifted] <= stage).all(),
            "ยกพื้นเหนือผิวน้ำ = สันเทียมรอบทะเลสาบ",
        )

        # 3) การขึ้นรูปต้องจางหมดก่อนถึง blend และไม่ทิ้งขอบคม
        self.assertFalse(lifted[distance >= blend].any())
        dry = ~body
        steps = [
            np.abs(np.diff(shaped, axis=1))[dry[:, :-1] & dry[:, 1:]],
            np.abs(np.diff(shaped, axis=0))[dry[:-1] & dry[1:]],
        ]
        self.assertLessEqual(
            int(max(part.max() for part in steps)), 4,
            "ยังมีขั้นคมบนพื้นแห้งรอบน้ำ",
        )

    def test_stage_keeps_real_cliffs_but_flattens_ordinary_reaches(self):
        """ผิวน้ำต้องเรียบตลอดสาย ยกเว้นตรงหน้าผาที่ภูมิประเทศยืนยัน

        ระบบเดิมบังคับ drop เท่ากันทุกที่ หน้าผา 20 บล็อกจึงถูกไถเป็นขั้นเล็ก
        กระจายสิบกว่าจุด ผลคือไม่มีน้ำตกจริงเลย ส่วนที่ตาเห็นว่าเป็นน้ำตกคือ
        เศษการปัดเศษ ซึ่งโผล่เป็นเสาน้ำโดดกลางลำธาร (70.2% ของกลุ่มเป็นจุดเดี่ยว)
        """
        gentle = H.STREAM_MAX_STEP * H.DENSIFY_SPACING
        cliff = H.MAX_WATERFALL_DROP * H.DENSIFY_SPACING
        raw = np.concatenate([
            np.linspace(100, 90, 40),      # ลำน้ำลาดเรื่อย ๆ
            np.linspace(70, 66, 40),       # หลังหน้าผา 20 บล็อก
        ])

        flattened = H.regularize_stage(raw, max_drop_per_sample=2.0)
        shaped = H.regularize_stage(
            raw, max_drop_per_sample=2.0,
            gentle_drop_per_sample=gentle, cliff_drop_per_sample=cliff,
        )

        flat_drops = -np.diff(flattened.astype(np.int32))
        shaped_drops = -np.diff(shaped.astype(np.int32))

        # เดิม: หน้าผาถูกไถจนไม่เหลือน้ำตก
        self.assertLess(int(flat_drops.max()), 3)
        # ใหม่: น้ำตกเดียวตรงหน้าผา ที่เหลือเรียบ
        self.assertGreaterEqual(int(shaped_drops.max()), 15)
        self.assertEqual(int((shaped_drops >= 3).sum()), 1)
        ordinary = shaped_drops[shaped_drops < 3]
        self.assertLessEqual(int(ordinary.max()), H.STREAM_MAX_STEP)

    def test_river_inside_a_lake_polygon_is_not_treated_as_still_water(self):
        """แม่น้ำที่ OSM เก็บเป็น polygon เดียวกับทะเลสาบต้องไม่ถูกจับเป็นน้ำนิ่ง

        root cause ข้อ 2 ของ WATER_REDESIGN — วัดจากโลกจริงได้ว่า "แม่น้ำ" ที่
        ลึกแค่ 1.8 บล็อกถูกจัดเป็นทะเลสาบ ผิวจึงถูกบังคับให้แบนทั้งที่มันไหล และ
        ได้ palette ก้นทะเลสาบ (cobblestone 47%)

        ต้องตัดสินที่ระดับ cell เพราะ 4-connected labeling รวมทะเลสาบกับแม่น้ำที่
        แตะกันเป็นก้อนเดียว — ก้อนใหญ่สุดกิน 78% ของผืนน้ำทั้งแผนที่
        """
        size = 60
        body = np.zeros((size, size), dtype=bool)
        body[10:50, 28:33] = True          # แม่น้ำกว้าง 5 ไหลลงแนวตั้ง
        body[40:56, 10:50] = True          # ทะเลสาบกว้างที่ปลายน้ำ
        profiles = [{
            "x": np.full(40, 30, dtype=np.int32),
            "z": np.arange(10, 50, dtype=np.int32),
            "radius": 2.0,
        }]

        corridor = H.waterway_corridor(profiles, body.shape)
        still = body & ~corridor

        # ตัวแม่น้ำต้องถูกตัดออกจากน้ำนิ่ง
        river_core = body[10:38, 28:33]
        self.assertFalse(
            (still[10:38, 28:33]).any(),
            "ร่องแม่น้ำยังถูกนับเป็นน้ำนิ่ง",
        )
        self.assertTrue(river_core.all(), "ผังทดสอบผิด")

        # ทะเลสาบส่วนที่อยู่ห่างเส้นทางน้ำต้องยังเป็นน้ำนิ่ง
        self.assertTrue(
            still[44:56, 12:24].all(),
            "ทะเลสาบถูกตัดทิ้งไปด้วย ทั้งที่อยู่ห่างเส้นทางน้ำ",
        )
        self.assertGreater(int(still.sum()), 0)
        self.assertLess(int(still.sum()), int(body.sum()))

    def test_stream_width_grows_with_accumulated_flow(self):
        """ลำน้ำต้องกว้างขึ้นตามสายที่ไหลมารวม ไม่ใช่คงที่ตามชนิดของ OSM

        89% ของเส้นถูกแท็กเป็น `stream` ซึ่ง OSM ใช้ครอบทั้งร่องน้ำบนสันเขาและ
        ลำธารที่รับน้ำจากสิบสาขา ถ้าอิงชนิดอย่างเดียวทุกสายจะกว้าง 1 บล็อกเท่ากัน
        หมดตลอดความยาว — ผิด acceptance gate ที่ว่าห้ามเป็น buffer ribbon คงที่
        """
        headwater = H.stream_radius(4.0, 22)
        midstream = H.stream_radius(4.0, 600)
        trunk = H.stream_radius(4.0, 16000)

        self.assertLess(headwater, midstream)
        self.assertLess(midstream, trunk)
        self.assertGreaterEqual(headwater, H.MIN_STREAM_RADIUS)
        self.assertLessEqual(trunk, H.MAX_STREAM_RADIUS)

        # แม่น้ำที่ OSM ระบุความกว้างไว้ ต้องไม่แคบลงกว่าที่ระบุ
        self.assertGreaterEqual(
            H.stream_radius(12.0, 22), 12.0 / (2.0 * C.METERS_PER_BLOCK)
        )

    def test_upstream_accumulation_follows_the_network(self):
        """ปลายน้ำต้องสะสมความยาวของทุกสาขาที่ไหลเข้ามา"""
        # สองสาขายาว 10 บล็อก ไหลมารวมเป็นลำน้ำสายเดียว
        sources = {
            "points_x": np.asarray(
                [0, 10,  0, 10,  10, 20], dtype=np.float32
            ),
            "points_z": np.asarray(
                [0, 10,  20, 10,  10, 10], dtype=np.float32
            ),
            "offsets": np.asarray([0, 2, 4, 6], dtype=np.int32),
            "kind": np.asarray([3, 3, 3], dtype=np.uint8),
        }

        got = H.accumulate_upstream_length(sources)

        self.assertAlmostEqual(float(got[0]), float(np.hypot(10, 10)), places=3)
        self.assertAlmostEqual(float(got[1]), float(np.hypot(10, 10)), places=3)
        # สายล่างยาว 10 บวกสองสาขาที่ไหลเข้า
        self.assertAlmostEqual(
            float(got[2]), 10.0 + float(got[0]) + float(got[1]), places=3
        )

    def test_bank_cut_slopes_the_edge_down_to_the_water(self):
        """แถวที่ติดน้ำต้องถูกลาดลงมาเสมอผิวน้ำ ไม่ใช่ทิ้งเป็นผนังตั้ง

        `regularize_stage` กดผิวน้ำต่ำกว่า DEM ลำธารจึงจมลงไปในพื้นราวหนึ่ง
        บล็อก ถ้าไม่ลาดตลิ่งลงมา จะได้ร่องผนังตั้งฉากซึ่งตาอ่านว่าเป็นขอบคม
        รอบลำธาร (วัดจากโลกจริงได้ 38.2% ของบล็อกแห้งที่ติดน้ำสูงกว่าผิวน้ำ)

        เคยพังเงียบ ๆ มาแล้ว: `margin=1` ทำให้เพดานสูงกว่าพื้นจริงหนึ่งระดับ
        เสมอ ฟังก์ชันจึงไม่เคยกดอะไรเลยทั้งที่ถูกเรียกอยู่
        """
        size = 21
        way = np.zeros((size, size), dtype=bool)
        way[:, 10] = True
        stage_y = 99
        terrain = np.full((size, size), stage_y + 1, dtype=np.int32)
        terrain[way] = stage_y
        surface = np.full((size, size), UNRESOLVED, dtype=np.int16)
        surface[way] = stage_y

        distance, indices = ndimage.distance_transform_edt(
            ~way, return_indices=True
        )
        near_stage = surface[tuple(indices)].astype(np.int32)
        shaped = H.taper_bank_cut(
            terrain, near_stage, distance, blend_blocks=8.0, where=~way
        )

        touching = np.zeros_like(way)
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            touching[dst] |= (~way)[dst] & way[src]

        # ผังทดสอบต้องเริ่มจากผนังตั้งจริง ๆ ไม่งั้นเทสต์ไม่ได้วัดอะไร
        self.assertTrue((terrain[touching] > near_stage[touching]).all())

        self.assertFalse(
            (shaped[touching] > near_stage[touching]).any(),
            "ยังเหลือผนังตั้งรอบลำธาร",
        )
        self.assertFalse(
            (shaped[touching] < near_stage[touching]).any(),
            "กดต่ำกว่าผิวน้ำ = น้ำรั่วออกข้าง",
        )
        # ห่างออกไปต้องไม่ถูกแตะ ภูมิประเทศเดิมยังอยู่
        far = distance >= 8.0
        np.testing.assert_array_equal(shaped[far], terrain[far])

    def test_pools_are_lengthened_without_losing_the_descent(self):
        """แอ่งต้องยาวขึ้นเป็น step-pool แต่น้ำต้องลดระดับรวมเท่าเดิม

        ลำธารบนภูเขาจริงเป็นแอ่งราบยาวคั่นด้วยจุดตกสั้น ๆ ไม่ใช่ลาดลงทีละบล็อก
        สม่ำเสมอ ถ้ารวบขั้นแล้วน้ำลดรวมน้อยลง แปลว่าน้ำถูกยกค้างไว้ ซึ่งจะทำให้
        ผิวน้ำลอยเหนือพื้นแล้วล้นออกข้าง
        """
        raw = np.repeat(np.linspace(100, 80, 20), 8)   # ลง 20 บล็อกใน 160 sample
        common = dict(
            gentle_drop_per_sample=H.STREAM_MAX_STEP * H.DENSIFY_SPACING,
            cliff_drop_per_sample=H.WATERFALL_TERRAIN_DROP * H.DENSIFY_SPACING,
        )

        loose = H.regularize_stage(raw, **common).astype(np.int32)
        pooled = H.regularize_stage(
            raw, pool_samples=H.MIN_POOL_BLOCKS / H.DENSIFY_SPACING, **common
        ).astype(np.int32)

        loose_steps = np.flatnonzero(np.diff(loose) != 0)
        pooled_steps = np.flatnonzero(np.diff(pooled) != 0)
        self.assertLess(pooled_steps.size, loose_steps.size)

        spacing = np.diff(pooled_steps) * H.DENSIFY_SPACING
        self.assertGreaterEqual(float(spacing.mean()), H.MIN_POOL_BLOCKS - 1)

        # ต้องยังไหลลงจนสุด — ยอมคลาดเคลื่อนได้หนึ่งบล็อกจากการปัดเศษ
        self.assertGreaterEqual(
            int(pooled[0] - pooled[-1]), int(loose[0] - loose[-1]) - 1
        )
        self.assertTrue((np.diff(pooled) <= 0).all(), "ผิวน้ำไหลย้อนขึ้น")

    def test_every_big_drop_has_a_curtain_to_cover_it(self):
        """ทุกจุดที่ผิวน้ำตกแรงต้องมีม่านน้ำรองรับ ไม่งั้นน้ำขาดเป็นช่อง

        เคยพังมาแล้วเพราะสองเงื่อนไขใช้เกณฑ์คนละค่า: envelope ปลดล็อกที่
        terrain ตก >= 4 แต่ม่านน้ำสร้างที่ >= 3 + dilation ผลคือมีจุดที่ผิวน้ำ
        ตกได้ถึง 12 บล็อกโดยไม่มีม่าน ขณะที่ม่านสูงสุดในแผนที่มีแค่ 4
        """
        size = 40
        terrain = np.zeros((size, size), dtype=np.int16)
        terrain[:] = (200 - np.arange(size, dtype=np.int16))[:, None]
        terrain[20:, :] -= 15                     # หน้าผา 15 บล็อก
        body = np.zeros((size, size), dtype=bool)
        body[0:3, 0:3] = True                     # ทะเลสาบจิ๋วให้ผ่าน guard
        terrain[body] = 200
        points_z = np.arange(2, size - 2, dtype=np.float32)
        points_x = np.full(points_z.shape, 20.0, dtype=np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([3], dtype=np.uint8),
            "width_m": np.asarray([8.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                result = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                )
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        surface = result["surface_y"].astype(np.int32)
        water = result["water_mask"]
        top = result["waterfall_top_y"].astype(np.int32)
        covered = result["waterfall_top_y"] != UNRESOLVED

        uncovered_gap = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            drop = surface[src] - surface[dst]
            falling = water[src] & water[dst] & (drop >= 3)
            # ที่ตกแรงต้องมีม่านสูงพอปิดช่อง
            short = falling & ~(
                covered[dst] & (top[dst] - surface[dst] >= drop)
            )
            uncovered_gap += int(short.sum())

        self.assertTrue(water.any(), "ผังทดสอบไม่มีน้ำเลย")
        self.assertEqual(
            uncovered_gap, 0,
            "มีจุดที่ผิวน้ำตก >=3 แต่ไม่มีม่านน้ำสูงพอปิด = น้ำขาดเป็นช่อง",
        )

    def test_flattening_does_not_reopen_a_gap_the_envelope_already_closed(self):
        """การยุบหน้าตัดกด cell ลง แล้วต้องไม่ทิ้งขั้นใหม่ที่ไม่มีม่านปิด

        envelope (`limit_masked_steps`) คุมขั้นตามแนวลำน้ำ ส่วนการยุบหน้าตัดกด
        cell ลงหาค่าต่ำสุดของหน้าตัด เพื่อนบ้านตามแนวลำน้ำที่ยังสูงอยู่จึงกลาย
        เป็นขั้นใหม่ที่ envelope ไม่เคยเห็น และไม่ผ่าน `supported` จึงไม่มีม่าน
        มาปิด = ช่องว่างกลางสายน้ำ  ต้องวนสองขั้นตอนจนลู่เข้า

        วัดบนผังนี้: ทำอย่างละครั้ง (SURFACE_FIXPOINT_PASSES = 1) เหลือช่องว่าง
        1 จุด, วนจนลู่เข้าเหลือ 0
        """
        size = 64
        terrain = np.zeros((size, size), dtype=np.int16)
        terrain[:] = (200 - np.arange(size, dtype=np.int16))[:, None]
        terrain[size // 2:, :] -= 15               # หน้าผา 15 บล็อก
        body = np.zeros((size, size), dtype=bool)
        body[0:3, 0:3] = True                      # ทะเลสาบจิ๋วให้ผ่าน guard
        terrain[body] = 200
        points_z = np.arange(2, size - 2, dtype=np.float32)
        # ลำน้ำต้องคดเคี้ยว ไม่งั้นไม่มีหน้าตัดให้ยุบ
        points_x = (
            size / 2 + 0.3 + 3.0 * np.sin(points_z / 7.0)
        ).astype(np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([1], dtype=np.uint8),
            "width_m": np.asarray([16.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                result = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                )
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        surface = result["surface_y"].astype(np.int32)
        water = result["water_mask"]
        top = result["waterfall_top_y"].astype(np.int32)
        covered = result["waterfall_top_y"] != UNRESOLVED

        uncovered = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            drop = surface[src] - surface[dst]
            falling = water[src] & water[dst] & (drop >= 3)
            uncovered += int((falling & ~(
                covered[dst] & (top[dst] - surface[dst] >= drop)
            )).sum())

        self.assertTrue(water.any(), "ผังทดสอบไม่มีน้ำเลย")
        self.assertEqual(
            uncovered, 0,
            "การยุบหน้าตัดเปิดช่องว่างที่ envelope ปิดไปแล้ว",
        )

    def test_every_stream_cell_has_a_shore_on_at_least_one_side(self):
        """ทุก cell น้ำต้องมีขอบที่เหยียบได้อย่างน้อยหนึ่งฝั่ง

        invariant ของสถาปัตยกรรมหน้าตัด: ชายฝั่งเป็น *ส่วนหนึ่งของหน้าตัดที่
        ประกาศไว้* ไม่ใช่ผลพลอยได้ของการขุด  ผู้ใช้ชี้จากภาพในเกมว่าลำธารเป็น
        "รอยผ่า" เพราะน้ำชนผนังหินทั้งสองข้าง — วัดทั้งแผนที่ได้ 55% ของขอบน้ำ

        เทสต์เดิมตรงนี้วัด "ตลิ่งลอกขั้นของน้ำ" ซึ่งผูกกับกลไก terrace ของ
        สถาปัตยกรรมเก่าที่ถูกถอดออกไปแล้ว
        """
        size = 40
        terrain = np.zeros((size, size), dtype=np.int16)
        terrain[:] = (240 - np.arange(size, dtype=np.int16))[:, None]
        body = np.zeros((size, size), dtype=bool)
        body[0:3, 0:3] = True
        terrain[body] = 240
        points_z = np.arange(2, size - 2, dtype=np.float32)
        points_x = np.full(points_z.shape, 20.0, dtype=np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([3], dtype=np.uint8),
            "width_m": np.asarray([8.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                result = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                )
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        shaped = result["terrain_y"].astype(np.int32)
        surface = result["surface_y"].astype(np.int32)
        way = result["waterway_mask"]
        self.assertTrue(way.any(), "ผังทดสอบไม่มีลำน้ำ")

        core = np.zeros(way.shape, dtype=bool)
        core[6:-6, 6:-6] = True
        # นับที่ *ขอบน้ำ* ไม่ใช่ทุก cell — cell กลางลำน้ำไม่มีเพื่อนบ้านแห้งเป็น
        # เรื่องปกติ (เขียนผิดรอบแรกแล้วได้ 67% ทั้งที่ขอบน้ำเป็นชายฝั่ง 100%)
        shore = wall = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]), (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]), (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            touch = np.zeros(way.shape, dtype=bool)
            touch[src] = (~way)[dst] & way[src] & core[src]
            rise = np.zeros(way.shape, dtype=np.int32)
            rise[src] = np.where(touch[src], shaped[dst] - surface[src], 0)
            shore += int((touch & (rise <= 1)).sum())
            wall += int((touch & (rise >= 2)).sum())

        self.assertGreater(shore + wall, 0, "ไม่มีขอบน้ำให้วัด")
        share = shore / (shore + wall)
        self.assertGreaterEqual(
            share, 0.9,
            f"ขอบน้ำเป็นชายฝั่งแค่ {share:.0%} — ที่เหลือคือผนังชนน้ำ",
        )

    def test_shaping_is_deterministic_and_cross_sections_are_flat(self):
        """สองสิ่งที่สถาปัตยกรรมใหม่รับประกัน *โดยโครงสร้าง*

        1. ผลเหมือนเดิมทุกครั้ง — ไม่มีลูปที่จบก่อนลู่เข้าให้ผลขึ้นกับจำนวนรอบ
        2. ผิวน้ำในหน้าตัดเดียวกันเท่ากัน เพราะแจกค่าเดียวทั้งหน้าตัด

        เทสต์เดิมตรวจว่า `flatten_cross_sections` รันซ้ำแล้วไม่เปลี่ยน ซึ่งเป็น
        กฎของสถาปัตยกรรมเก่าที่ถูกถอดออกไปแล้ว
        """
        size = 44
        terrain = np.full((size, size), 200, dtype=np.int16)
        terrain -= (np.arange(size, dtype=np.int16) // 3)[:, None]
        terrain[size - 8:, :] = 188
        body = np.zeros((size, size), dtype=bool)
        body[size - 8:, :] = True
        points_z = np.arange(2, size - 6, dtype=np.float32)
        points_x = (size / 2 + 2.0 * np.sin(points_z / 5.0)).astype(np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([1], dtype=np.uint8),
            "width_m": np.asarray([12.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                first = H.shape_hydrology_patch(terrain, sources, 0, size, 0, size)
                second = H.shape_hydrology_patch(terrain, sources, 0, size, 0, size)
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        for key in ("surface_y", "terrain_y", "depth"):
            np.testing.assert_array_equal(
                first[key], second[key], err_msg=f"{key} ไม่ deterministic"
            )

        surface = first["surface_y"].astype(np.int32)
        way = first["waterway_mask"]
        self.assertTrue(way.any(), "ผังทดสอบไม่มีลำน้ำ")
        unflat = 0
        for arr, mask in ((surface, way), (surface.T, way.T)):
            for i in range(arr.shape[0]):
                idx = np.flatnonzero(mask[i])
                if idx.size < 2:
                    continue
                breaks = np.flatnonzero(np.diff(idx) != 1)
                starts = np.concatenate(([0], breaks + 1))
                ends = np.concatenate((breaks, [idx.size - 1]))
                for a, b in zip(starts, ends):
                    span = idx[a:b + 1]
                    if 2 <= span.size <= 3:
                        run = arr[i, span]
                        unflat += int(int(run.max()) - int(run.min()) > 1)
        self.assertEqual(unflat, 0, "หน้าตัดสั้น ๆ ยังมีขั้นเกิน 1 บล็อก")

    def test_stream_descends_into_a_lake_far_below_it(self):
        """ลำธารที่ถึงทะเลสาบซึ่งอยู่ต่ำกว่ามากต้องไหลลงไปหา ไม่ใช่ค้างแล้วเสกม่าน

        บั๊กเดิม: การ pin ปากน้ำทำเฉพาะเมื่อต่างกันไม่เกิน max_surface_step ลำธาร
        ที่ค้างสูงกว่าทะเลสาบ 12 บล็อกจึงไม่ถูกดึงลงเลย แล้ว `at_lake` ก็เสกม่าน
        น้ำสูง 12 บล็อกมาปิดช่อง วัดจากผังจริงแล้วว่าม่านพวกนั้น 100% ไม่มีหน้าผา
        รองรับ — เป็นเสาน้ำยืนโดด ไม่ใช่น้ำตก
        """
        size = 48
        # ที่ราบสูงลาดเบา ๆ แล้วมีทะเลสาบแบนอยู่ต่ำกว่า 14 บล็อกที่ปลายผัง
        terrain = np.full((size, size), 200, dtype=np.int16)
        terrain -= (np.arange(size, dtype=np.int16) // 8)[:, None]
        terrain[size - 10:, :] = 186
        body = np.zeros((size, size), dtype=bool)
        body[size - 10:, :] = True
        points_z = np.arange(2, size - 8, dtype=np.float32)
        points_x = np.full(points_z.shape, 24.0, dtype=np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([3], dtype=np.uint8),
            "width_m": np.asarray([8.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                result = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                )
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        surface = result["surface_y"].astype(np.int32)
        way = result["waterway_mask"]
        lake = result["standing_water_mask"]
        self.assertTrue(way.any() and lake.any(), "ผังทดสอบไม่ครบ")

        lake_level = int(np.bincount(surface[lake] - surface[lake].min())
                         .argmax()) + int(surface[lake].min())
        contact = np.zeros(way.shape, dtype=bool)
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            contact[dst] |= way[dst] & lake[src]
        self.assertTrue(contact.any(), "ลำธารไม่ได้แตะทะเลสาบ")

        # cell ที่ติดทะเลสาบต้องอยู่ระดับเดียวกับทะเลสาบ ไม่ค้างเหนือมัน
        self.assertLessEqual(
            int((surface[contact] - lake_level).max()),
            H.UNSUPPORTED_MAX_STEP,
            "ผิวลำธารที่ปากน้ำยังค้างสูงกว่าระดับทะเลสาบ",
        )
        # ทะเลสาบห้ามลอยเหนือลำน้ำที่ติดกัน ไม่งั้นวานิลลาจะไหลรวมแล้วยกระดับ
        # เคยพังมาแล้วเพราะ flatten_cross_sections ยุบด้วยค่าต่ำสุดจึงกดเซลล์
        # ปากน้ำที่ pin ไว้ลงมา (ขอบที่ทะเลสาบสูงกว่าลำน้ำพุ่งจาก 78 เป็น 41,504)
        above = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            above += int((
                lake[src] & way[dst] & (surface[src] > surface[dst])
            ).sum())
        self.assertEqual(above, 0, "ทะเลสาบลอยอยู่เหนือลำน้ำที่ติดกัน")

        # และไม่มีม่านน้ำถูกเสกขึ้นมาปิดช่องที่ไม่ควรมี
        curtain = result["waterfall_top_y"] != UNRESOLVED
        self.assertEqual(
            int((curtain & contact).sum()), 0,
            "ยังมีม่านน้ำที่ปากทะเลสาบทั้งที่ไม่มีหน้าผารองรับ",
        )

    def test_flat_stream_is_not_incised_below_the_lake_it_enters(self):
        """ปากน้ำบนที่ราบต้องไม่เป็นร่องต่ำกว่าทะเลสาบหนึ่งบล็อก

        ``_incise_ceiling`` ตั้งใจลดลำน้ำบนที่ราบเพื่อให้มีตลิ่งธรรมชาติ แต่ถ้า
        profile นั้นแตะทะเลสาบ ระดับทะเลสาบคือ boundary condition ที่สำคัญกว่า
        ของจริงใน global2 เคยมีทะเลสาบสูงกว่าลำน้ำติดกัน 64 cell จากอาการนี้
        (57 cell อยู่บน profile เดียวที่ถูกกดจาก 18 เหลือ 17)
        """
        size = 48
        terrain = np.full((size, size), 18, dtype=np.int16)
        body = np.zeros((size, size), dtype=bool)
        body[size - 10:, :] = True
        points_z = np.arange(2, size - 6, dtype=np.float32)
        points_x = np.full(points_z.shape, 24.0, dtype=np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x,
            "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([3], dtype=np.uint8),
            "width_m": np.asarray([8.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                result = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                )
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        surface = result["surface_y"].astype(np.int32)
        way = result["waterway_mask"]
        lake = result["standing_water_mask"]
        above = 0
        for dst, src in (
            (np.s_[1:, :], np.s_[:-1, :]),
            (np.s_[:-1, :], np.s_[1:, :]),
            (np.s_[:, 1:], np.s_[:, :-1]),
            (np.s_[:, :-1], np.s_[:, 1:]),
        ):
            above += int((
                lake[src] & way[dst] & (surface[src] > surface[dst])
            ).sum())
        self.assertEqual(
            above, 0,
            "การ incise สร้างร่องลำน้ำต่ำกว่าทะเลสาบที่ปากน้ำ",
        )

    def test_stage_steps_move_to_the_narrow_part_of_the_channel(self):
        """ผิวน้ำต้องลดระดับตรงที่ลำน้ำแคบ ไม่ใช่กลางผืนกว้าง

        บนผืนน้ำกว้าง วานิลลาจะแผ่น้ำจากฝั่งสูงลงฝั่งต่ำ แล้ว flowing block ที่มี
        source ข้าง ๆ ครบสองตัวกลายเป็น source ใหม่ (กฎ infinite water) ระดับน้ำ
        จึงถูกยกขึ้นถาวรเป็นบริเวณกว้าง  วัดจากโลกจริง: จุดที่ผู้เล่นบอกว่าสวย
        (6165, 6068) มีอัตราแผ่น้ำ 52% แต่ศูนย์อยู่บนผืนกว้าง ส่วนจุดที่ล้น
        (2273, 3155) มีแค่ 12% แต่ 161 จาก 217 อยู่บนผืนกว้าง
        """
        n = 240
        span = H.MIN_POOL_BLOCKS / H.DENSIFY_SPACING
        raw = np.linspace(100.0, 96.0, n)
        common = dict(
            max_drop_per_sample=H.MAX_WATERFALL_DROP / 2.0,
            gentle_drop_per_sample=H.STREAM_MAX_STEP * H.DENSIFY_SPACING,
            cliff_drop_per_sample=H.cliff_drop_per_sample(),
            pool_samples=span,
        )

        def steps(stage):
            return np.flatnonzero(np.diff(np.asarray(stage, dtype=np.int32)))

        loose = steps(H.regularize_stage(raw, **common))
        self.assertTrue(loose.size >= 2, "ผังทดสอบไม่มีขั้นให้ย้าย")

        # คอคอดกว้าง 1 บล็อกวางไว้ถัดจากขั้นธรรมชาติหนึ่งอัน
        target = int(loose[1])
        neck = target + 6
        width = np.full(n, 9, dtype=np.int32)
        width[neck:neck + 4] = 1

        narrow = H.local_narrow_points(width, span)
        self.assertTrue(narrow[neck:neck + 4].all(), "คอคอดไม่ถูกมองว่าแคบ")
        self.assertFalse(
            narrow[neck - 8:neck].any(), "ช่วงกว้างข้างคอคอดยังลดระดับได้"
        )
        # ส่วนที่ไกลจนไม่มีอะไรแคบกว่าให้เลือก ยังลดได้ตามเดิม — ไม่งั้นแม่น้ำที่
        # กว้างเท่ากันทั้งสายจะยืดแอ่งไปไม่รู้จบ
        self.assertTrue(narrow[:20].any(), "ช่วงที่ไม่มีคอคอดกลับลดระดับไม่ได้")

        aimed = steps(H.regularize_stage(raw, pool_width=width, **common))
        self.assertNotIn(
            target, aimed.tolist(), "ขั้นยังอยู่กลางผืนกว้างที่เดิม"
        )
        self.assertTrue(
            (np.abs(aimed - neck) <= 2).any(),
            f"ขั้นไม่ได้ย้ายไปคอคอดที่ {neck}: {aimed.tolist()}",
        )
        # ต้องยังไหลลงครบ ไม่ใช่กลบขั้นทิ้ง
        full = H.regularize_stage(raw, pool_width=width, **common)
        base = H.regularize_stage(raw, **common)
        self.assertGreaterEqual(
            int(full[0] - full[-1]), int(base[0] - base[-1]) - 1
        )

    def test_pools_hold_water_only_where_real_banks_confine_it(self):
        """ยืดแอ่งเหนือพื้นได้เฉพาะที่ตลิ่งจริงขังน้ำไว้ ห้ามสร้างเขื่อนบนที่เปิด

        เพดานคงที่ max_above=2 เคยให้ผิวน้ำลอยเหนือพื้น 30.2% ของ sample
        (วัดจาก 400 เส้นจริง) ทั้งที่มีแค่ 26.6% ของลำน้ำที่ตลิ่งขังน้ำได้จริง
        ส่วนที่เหลือกลายเป็นคันดินเทียมสองฝั่ง — ผู้เล่นเห็นเป็น "น้ำสูงกว่าเนิน"
        และ "สองฝั่งแม่น้ำสูงไม่เท่ากัน" ที่ (2282, 3166) กับ (5970, 5902)
        """
        size = 120
        floor = (100 - np.arange(size) // 6).astype(np.int16)
        sample_z = np.arange(10.0, size - 10.0, dtype=np.float64)
        sample_x = np.full(sample_z.shape, 60.0, dtype=np.float64)
        common = dict(
            max_drop_per_sample=H.MAX_WATERFALL_DROP / 2.0,
            gentle_drop_per_sample=H.STREAM_MAX_STEP * H.DENSIFY_SPACING,
            cliff_drop_per_sample=H.cliff_drop_per_sample(),
            pool_samples=H.MIN_POOL_BLOCKS / H.DENSIFY_SPACING,
        )

        # ผังเปิด: ทั้งแนวขวางแบนเท่าพื้นลำน้ำ — ตลิ่งขังน้ำไม่ได้เลย
        open_terrain = np.repeat(floor[:, None], size, axis=1)
        gz = sample_z.astype(np.int64)
        gx = sample_x.astype(np.int64)
        raw = open_terrain[gz, gx].astype(np.float64)
        confine = H.bank_confinement(open_terrain, sample_x, sample_z, 1.0)
        stage = H.regularize_stage(
            raw, pool_confine=confine, **common
        ).astype(np.int32)
        self.assertLessEqual(
            int((stage - raw).max()), 0,
            "ผิวน้ำลอยเหนือพื้นบนที่ราบเปิดที่ไม่มีตลิ่งขังน้ำ",
        )

        # ผังร่องเขา: ผนังสองข้างสูงกว่าพื้น 6 บล็อก — แอ่งต้องยังยืดได้
        # ร่องต้องแคบกว่าระยะวัดตลิ่ง (radius+1.5 = 2.5) ไม่งั้นจุดวัดยังอยู่ในร่อง
        gorge_terrain = open_terrain + 6
        gorge_terrain[:, 59:62] -= 6          # ร่องกว้าง 3 คอลัมน์ที่ระดับพื้น
        raw_g = gorge_terrain[gz, gx].astype(np.float64)
        confine_g = H.bank_confinement(gorge_terrain, sample_x, sample_z, 1.0)
        stage_g = H.regularize_stage(
            raw_g, pool_confine=confine_g, **common
        ).astype(np.int32)
        perch = stage_g - raw_g
        self.assertGreaterEqual(
            int(perch.max()), 1, "ร่องเขาที่ขังน้ำได้จริงกลับไม่มีแอ่งเลย"
        )
        self.assertLessEqual(int(perch.max()), 2, "แอ่งลึกเกินเพดาน")
        # และแอ่งในร่องเขาต้องยาวกว่าบนที่เปิด
        def spacing(s):
            steps = np.flatnonzero(np.diff(s) != 0)
            return (
                float(np.diff(steps).mean()) * H.DENSIFY_SPACING
                if steps.size >= 2 else 0.0
            )
        self.assertGreater(spacing(stage_g), spacing(stage))

    def test_offmap_samples_are_dropped_not_clamped_to_the_border(self):
        """sample นอกแผนที่ต้องถูกตัดทิ้ง ไม่ใช่บีบมากองบน cell แถวขอบ

        เดิมใช้ np.clip ทั้ง reach ที่ไล่ระดับลงนอกกรอบจึงถูกยัดลงบน cell เดียวกัน
        แล้ว regularize_stage ลากผิวน้ำตามลงไป ผลคือร่องน้ำลึกชนเพดาน clip ตลอด
        แนวขอบแผนที่ — วัดได้ 3,864 cell ที่ -12 เป๊ะ ก้อนใหญ่สุดอยู่ที่ขอบทั้งหมด
        """
        size = 32
        terrain = np.full((size, size), 100, dtype=np.int16)
        upstream = np.asarray([50.0])
        # เส้นวิ่งจากในแผนที่ ออกไปนอกกรอบทางซ้าย แล้ววกกลับเข้ามา
        points_x = np.asarray(
            [20.0, 10.0, -30.0, -60.0, -30.0, 8.0, 18.0], dtype=np.float32
        )
        points_z = np.asarray(
            [4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0], dtype=np.float32
        )
        sources = {
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_x)], dtype=np.int32),
            "kind": np.asarray([3], dtype=np.uint8),
            "width_m": np.asarray([8.0], dtype=np.float32),
        }

        entries = H.line_profile_entries(sources, 0, terrain, upstream)
        self.assertGreaterEqual(
            len(entries), 2,
            "เส้นที่ออกนอกกรอบแล้ววกกลับต้องถูกแยกเป็นคนละ profile",
        )
        for entry in entries:
            self.assertTrue(
                (entry["x"] >= 0).all() and (entry["x"] < size).all()
                and (entry["z"] >= 0).all() and (entry["z"] < size).all(),
                "profile ยังมี sample นอกแผนที่",
            )
            # พื้นราบทั้งผัง ผิวน้ำจึงต้องไม่ถูกลากลงไปไหน
            self.assertGreaterEqual(
                int(entry["stage"].min()), 100 - H.UNSUPPORTED_MAX_STEP,
                "ผิวน้ำถูกลากลงต่ำกว่าพื้นราบ — sample นอกกรอบยังกองที่ขอบ",
            )

    def test_wide_water_far_from_a_centerline_keeps_its_own_level(self):
        """ผืนน้ำกว้างที่ไม่มี centerline ใกล้ ๆ ต้องอยู่ที่ระดับพื้นของตัวเอง

        เดิม `flowing_body` รับ stage จาก centerline ที่ใกล้ที่สุดโดยไม่จำกัดระยะ
        วัดที่ (2273, 3155) แล้ว centerline ใกล้สุดอยู่ห่าง 136 บล็อกและอยู่คนละ
        ระดับ ผลคือผืนน้ำถูกกดลงจนชนเพดาน clip (-12) แล้วเกิดผนังน้ำ 12 บล็อก
        เทียบกับทะเลสาบข้าง ๆ ทั้งแผนที่มี 7,459 cell ติดพื้นเพดานแบบนี้
        """
        size = 64
        terrain = np.full((size, size), 120, dtype=np.int16)
        # ลำธารสายเล็กที่ไหลลงไปต่ำมากอยู่คนละมุมของผัง
        terrain[:, :12] = (90 - np.arange(size, dtype=np.int16) // 2)[:, None]
        # ผืนน้ำกว้างที่ไม่มีเส้น centerline พาดผ่านเลย อยู่บนที่ราบ y=120
        body = np.zeros((size, size), dtype=bool)
        body[40:56, 40:60] = True
        points_z = np.arange(2, size - 2, dtype=np.float32)
        points_x = np.full(points_z.shape, 5.0, dtype=np.float32)
        sources = {
            "waterbody_mask": body,
            "waterway_kind": np.zeros((size, size), dtype=np.uint8),
            "points_x": points_x, "points_z": points_z,
            "offsets": np.asarray([0, len(points_z)], dtype=np.int32),
            "kind": np.asarray([3], dtype=np.uint8),
            "width_m": np.asarray([8.0], dtype=np.float32),
        }

        original_here = H.HERE
        source_dir = tempfile.mkdtemp()
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            with contextlib.redirect_stdout(io.StringIO()):
                result = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                )
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)

        wet = result["water_mask"] & body
        self.assertTrue(wet.any(), "ผืนน้ำกว้างหายไปจากผลลัพธ์")
        surface = result["surface_y"].astype(np.int32)
        sunk = int((120 - surface[wet]).max())
        self.assertLessEqual(
            sunk, H.UNSUPPORTED_MAX_STEP,
            f"ผืนน้ำกว้างถูกกดลงจากพื้นเดิม {sunk} บล็อก "
            "— รับ stage จาก centerline ที่อยู่ไกล",
        )

    def test_global_tiling_matches_single_window_patch(self):
        """--global ต้องให้ผลเท่ากับ --patch ที่ครอบพื้นที่เดียวกันทั้งผืน

        เคยไม่เท่ากันมาแล้วและไม่มีอะไรฟ้อง: global ส่ง
        `max_drop_per_sample=6.0` กับ `max_surface_step=12` ทับค่า default ของ
        patch (2.0 / 4) ผลคือ acceptance ที่วัดจาก patch ผ่านทั้งที่ผิวน้ำจริง
        ทั้งแผนที่หยาบกว่าสามเท่า ผู้เล่นเห็นเป็นน้ำกระโดดกลางสาย

        ผังทดสอบมีลำธารยาวตัดผ่านรอยต่อ tile หลายจุด บวกหน้าผา 60 บล็อกเพื่อ
        บังคับให้ทั้ง stage regularisation และ Lipschitz envelope ได้ทำงานจริง
        """
        size, tile = 128, 32
        terrain = (300 - np.arange(size, dtype=np.int16))[:, None]
        terrain = np.repeat(terrain, size, axis=1).astype(np.int16)
        terrain[64:, :] -= 60                     # หน้าผากลางสาย
        terrain[10:26, 96:112] = 250              # ทะเลสาบแบนสนิท
        body = np.zeros((size, size), dtype=bool)
        body[10:26, 96:112] = True
        # สองสายที่อยู่คนละฝั่งของรอยต่อ tile — จำเป็นสำหรับการตรวจ `section_id`
        # ถ้ามีสายเดียว ทุก tile จะแจกหมายเลข 1 ให้มันเหมือนกันหมด การแจก
        # หมายเลขตาม tile (บั๊ก) กับตามเส้น (ถูก) จึงให้ผลเท่ากันโดยบังเอิญ
        run_z = np.arange(2, size - 2, dtype=np.float32)
        points_z = np.concatenate([run_z, run_z])
        points_x = np.concatenate([
            np.full(run_z.shape, 40.0, dtype=np.float32),
            np.full(run_z.shape, 12.0, dtype=np.float32),
        ])

        source_dir = tempfile.mkdtemp()
        out_dir = tempfile.mkdtemp()
        original_here = H.HERE
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            np.savez(
                os.path.join(source_dir, "water_sources.npz"),
                waterbody_mask=body,
                waterway_kind=np.zeros((size, size), dtype=np.uint8),
                points_x=points_x, points_z=points_z,
                offsets=np.asarray(
                    [0, len(run_z), len(points_z)], dtype=np.int32
                ),
                kind=np.asarray([3, 3], dtype=np.uint8),
                width_m=np.asarray([8.0, 8.0], dtype=np.float32),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = H.shape_hydrology_global(out_dir, tile_size=tile)
                standing = np.load(
                    os.path.join(out_dir, "standing_surface_y.npy")
                )
                sources = np.load(
                    os.path.join(source_dir, "water_sources.npz")
                )
                reference = H.shape_hydrology_patch(
                    terrain, sources, 0, size, 0, size,
                    standing_surface=standing,
                )
                sources.close()

            # manifest ต้องแยก "เพดานของลำน้ำปกติ" ออกจาก "ที่วัดได้จริง"
            # เดิมมีช่องเดียวเขียน MAX_WATERFALL_DROP ไว้เสมอ ทั้งที่ริมหน้าผา
            # หลุดเพดานได้ตามออกแบบ — ตัวเลขจึงไม่เคยตรงกับผลลัพธ์
            self.assertEqual(
                manifest["max_surface_step"], H.UNSUPPORTED_MAX_STEP
            )
            self.assertEqual(
                manifest["waterfall_curtains"], H.ENABLE_WATERFALL_CURTAINS
            )
            self.assertGreaterEqual(
                manifest["max_surface_step_observed"],
                manifest["max_surface_step"],
            )

            # mask กับระดับผิวน้ำต้องตรงกันเป๊ะ — paint_surface ตรวจข้อนี้ตอน
            # รันจริงและจะหยุดกลางทาง เคยพังมาแล้วเพราะ build_step_weirs เปลี่ยน
            # cell น้ำเป็นบกโดยไม่ล้าง surface_y ทิ้ง กว่าจะรู้ก็เสีย 20 นาที
            wm = np.load(os.path.join(out_dir, "water_mask.npy"))
            sy = np.load(os.path.join(out_dir, "surface_y.npy"))
            dp = np.load(os.path.join(out_dir, "depth.npy"))
            self.assertEqual(
                int((wm & (sy == UNRESOLVED)).sum()), 0,
                "มี cell น้ำที่ไม่มีระดับผิวน้ำ",
            )
            self.assertEqual(
                int(((~wm) & (sy != UNRESOLVED)).sum()), 0,
                "มีระดับผิวน้ำค้างอยู่บน cell ที่ไม่ใช่น้ำ",
            )
            self.assertEqual(
                int(((~wm) & (dp > 0)).sum()), 0,
                "มีความลึกค้างอยู่บน cell ที่ไม่ใช่น้ำ",
            )

            # กฎที่ต้องจริงเสมอบนผลลัพธ์ที่ประกอบจาก tile: ทุกขั้นที่เกินเพดาน
            # ต้องมีม่านน้ำสูงพอปิด ไม่งั้นเป็นช่องว่างกลางสายน้ำ
            surface = np.load(
                os.path.join(out_dir, "surface_y.npy")
            ).astype(np.int32)
            way = np.load(os.path.join(out_dir, "waterway_mask.npy"))
            top = np.load(
                os.path.join(out_dir, "waterfall_top_y.npy")
            ).astype(np.int32)
            cap = manifest["max_surface_step"]
            uncovered = 0
            for dst, src in (
                (np.s_[1:, :], np.s_[:-1, :]),
                (np.s_[:-1, :], np.s_[1:, :]),
                (np.s_[:, 1:], np.s_[:, :-1]),
                (np.s_[:, :-1], np.s_[:, 1:]),
            ):
                over = (
                    way[dst] & way[src]
                    & (surface[src] - surface[dst] > cap)
                    & (top[dst] < surface[src])
                )
                uncovered += int(over.sum())
            self.assertEqual(
                uncovered, 0,
                "มีขั้นที่เกินเพดานแต่ไม่มีม่านน้ำปิด = น้ำขาดเป็นช่อง",
            )

            # ชื่อ product ที่ hydrology_patch_io ประกาศต้องมีไฟล์อยู่จริง
            # ไม่งั้นผู้อ่านทุกตัว (report_metrics / render_*) จะพังหรืออ่านผิด
            for product in IO.GLOBAL_PRODUCTS.values():
                self.assertTrue(
                    os.path.exists(os.path.join(out_dir, product)),
                    f"hydrology_shape ไม่ได้เขียน {product} "
                    "แต่ hydrology_patch_io ประกาศไว้",
                )
            for name in ("waterway_mask", "surface_y", "terrain_y",
                         "section_id"):
                produced = np.load(os.path.join(out_dir, f"{name}.npy"))
                self.assertTrue(
                    produced.any(), f"{name} ว่างเปล่า — ผังทดสอบไม่ได้ทำงาน"
                )
                np.testing.assert_array_equal(
                    produced, reference[name],
                    err_msg=f"global ให้ {name} ต่างจาก patch",
                )

            # `section_id` อยู่ในรายการเทียบข้างบนด้วย ซึ่งเป็นตัวจับว่า tile
            # แจกหมายเลขหน้าตัดตามลำดับใน subset ของตัวเอง (ผิด) หรือตามหมายเลข
            # ประจำเส้นที่คงที่ทั้งแผนที่ (ถูก) — แบบแรกทำให้ตัววัดเห็นหน้าตัด
            # เดียวเป็นสองอันคนละฝั่งรอยต่อ
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_section_ids_come_from_the_line_not_from_the_tile(self):
        """สองสายที่อยู่คนละ tile ต้องได้หมายเลขหน้าตัดคนละชุด

        `stamp_sections` เห็น profile แค่ subset ที่ tile นั้นเลือกมา ถ้าแจก
        หมายเลขตามลำดับใน subset สายแรกของทุก tile จะได้เลข 1 เหมือนกันหมด แล้ว
        ตัววัดที่จัดกลุ่มด้วย `section_id` จะรวม cell ของคนละสายเป็นหน้าตัดเดียว
        กัน  halo ต้องแคบพอที่แต่ละ tile จะเห็นไม่ครบทุกสาย ไม่งั้นเทสต์นี้ผ่าน
        โดยบังเอิญ
        """
        size, tile, halo = 96, 32, 4
        terrain = (300 - np.arange(size, dtype=np.int16))[:, None]
        terrain = np.repeat(terrain, size, axis=1).astype(np.int16)
        run_z = np.arange(2, size - 2, dtype=np.float32)
        points_z = np.concatenate([run_z, run_z])
        points_x = np.concatenate([
            np.full(run_z.shape, 8.0, dtype=np.float32),
            np.full(run_z.shape, 72.0, dtype=np.float32),
        ])

        source_dir = tempfile.mkdtemp()
        out_dir = tempfile.mkdtemp()
        original_here = H.HERE
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            np.savez(
                os.path.join(source_dir, "water_sources.npz"),
                waterbody_mask=np.zeros((size, size), dtype=bool),
                waterway_kind=np.zeros((size, size), dtype=np.uint8),
                points_x=points_x, points_z=points_z,
                offsets=np.asarray(
                    [0, len(run_z), len(points_z)], dtype=np.int32
                ),
                kind=np.asarray([3, 3], dtype=np.uint8),
                width_m=np.asarray([8.0, 8.0], dtype=np.float32),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                H.shape_hydrology_global(out_dir, tile_size=tile, halo=halo)
            section = np.load(os.path.join(out_dir, "section_id.npy"))
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)

        line = section // CS.SECTION_ID_STRIDE
        west = set(np.unique(line[:, :size // 2][line[:, :size // 2] > 0]))
        east = set(np.unique(line[:, size // 2:][line[:, size // 2:] > 0]))

        self.assertTrue(west and east, "ผังทดสอบไม่มีน้ำสักสาย")
        self.assertFalse(
            west & east, "สองสายใช้หมายเลขเส้นร่วมกัน = แจกตาม tile ไม่ใช่ตามเส้น"
        )

    def test_one_section_id_never_covers_two_places(self):
        """หน้าตัดหนึ่งอันคือแถบสั้น ๆ ขวางลำน้ำ ห้ามกระจายไปคนละมุมแผนที่

        `plan_from_profile` ตัด sample ที่อยู่นอกกรอบทิ้ง ถ้านับสถานีจากดัชนีใน
        อาร์เรย์ที่ตัดแล้ว สถานีที่ 1 ของแต่ละ tile จะเป็นคนละที่ แล้ว id เดียว
        กันจะครอบ cell ที่อยู่ห่างกันหลายร้อยบล็อก — วัดจาก product จริงเจอ
        หน้าตัดที่มีผิวน้ำสองระดับห่างกัน 51 บล็อกเพราะเหตุนี้
        """
        size, tile, halo = 96, 32, 8
        terrain = (300 - np.arange(size, dtype=np.int16))[:, None]
        terrain = np.repeat(terrain, size, axis=1).astype(np.int16)
        run_z = np.arange(2, size - 2, dtype=np.float32)

        source_dir = tempfile.mkdtemp()
        out_dir = tempfile.mkdtemp()
        original_here = H.HERE
        try:
            H.HERE = source_dir
            np.save(os.path.join(source_dir, "terrain_y.npy"), terrain)
            np.savez(
                os.path.join(source_dir, "water_sources.npz"),
                waterbody_mask=np.zeros((size, size), dtype=bool),
                waterway_kind=np.zeros((size, size), dtype=np.uint8),
                points_x=np.full(run_z.shape, 48.0, dtype=np.float32),
                points_z=run_z,
                offsets=np.asarray([0, len(run_z)], dtype=np.int32),
                kind=np.asarray([3], dtype=np.uint8),
                width_m=np.asarray([8.0], dtype=np.float32),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                H.shape_hydrology_global(out_dir, tile_size=tile, halo=halo)
            section = np.load(os.path.join(out_dir, "section_id.npy"))
        finally:
            H.HERE = original_here
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)

        worst = 0
        for sid in np.unique(section[section > 0]):
            zz, xx = np.nonzero(section == sid)
            worst = max(worst, int(zz.max() - zz.min()),
                        int(xx.max() - xx.min()))

        self.assertLessEqual(
            worst, 2 * CS.MAX_BANK_BLOCKS,
            f"มีหน้าตัดที่กว้าง {worst} บล็อก = id ชนกันข้าม tile",
        )

    def test_seam_report_separates_tile_edges_from_interior(self):
        """seam_step_report ต้องแยกขอบ tile ออกจากภายในได้ถูกต้อง"""
        surface = np.zeros((8, 8), dtype=np.int16)
        way = np.ones((8, 8), dtype=bool)
        surface[4:] = 5                      # ขั้น 5 บล็อกพาดตรงรอยต่อ tile 4

        report = H.seam_step_report(surface, way, tile_size=4)

        self.assertGreater(report["seam_edges"], 0)
        self.assertGreater(report["interior_edges"], 0)
        self.assertEqual(report["interior_step2plus_share"], 0.0)
        self.assertGreater(report["seam_step2plus_share"], 0.0)

    def test_standing_surface_does_not_depend_on_row_batch(self):
        """shelf ต้องแพร่แบบ synchronous (Jacobi) ไม่ว่าจะแบ่งแถบอย่างไร

        `_component_standing_surface` ทำงานทีละแถบเพื่อไม่ให้ต้องกาง array
        เต็มแผนที่ ถ้าแถบถัดไปเผลออ่านค่าที่แถบก่อนหน้าเพิ่งเขียน การแพร่จะวิ่ง
        ลงใต้เร็วกว่าขึ้นเหนือ ผลจึงขึ้นกับ row_batch — เป็นบั๊กที่เงียบสนิท
        เพราะทุกค่ายังดู "สมเหตุสมผล" อยู่

        ผังทดสอบ: ทะเลสาบแบนกว้างด้านบน (รอด erosion 3 ชั้นจึงได้ระดับ) ต่อกับ
        หางแคบที่ลาดลงและยาวเกิน 12 รอบการแพร่ จึงมีทั้งส่วนที่ถูกเติมและส่วนที่
        ต้องเหลือ UNRESOLVED — ระยะแพร่ที่เกินมาแม้แถวเดียวจะเห็นทันที
        """
        body = np.zeros((48, 20), dtype=bool)
        body[0:15, 2:18] = True
        body[15:48, 9:12] = True
        terrain = np.full((48, 20), 100, dtype=np.int16)
        terrain[15:] = (100 - np.arange(33, dtype=np.int16))[:, None]

        results = []
        for row_batch in (1, 2, 3, 5, 48):
            out_dir = tempfile.mkdtemp()
            try:
                surface, count, _levels = H._component_standing_surface(
                    terrain, body, out_dir, row_batch=row_batch
                )
                results.append((np.asarray(surface).copy(), count))
                surface._mmap.close()
                del surface
            finally:
                shutil.rmtree(out_dir, ignore_errors=True)

        reference, reference_count = results[0]
        # กันเทสต์กลายเป็นของว่าง: ต้องมีทั้งส่วนที่เติมแล้วและส่วนที่ยังไม่เติม
        resolved = int((reference != UNRESOLVED).sum())
        self.assertGreater(resolved, 0)
        self.assertLess(resolved, int(body.sum()))

        for got, count in results[1:]:
            np.testing.assert_array_equal(got, reference)
            self.assertEqual(count, reference_count)

    def test_water_split_by_a_channel_keeps_one_level_on_both_sides(self):
        """น้ำนิ่งสองฝั่งของร่องลำน้ำต้องได้ระดับเดียวกัน

        ร่องลำน้ำ (corridor) ถูกตัดออกจากผืนน้ำนิ่งก่อนจัดกลุ่ม ผืนเดียวของ OSM
        จึงกลายเป็นสอง component แล้วได้ระดับของใครของมัน  ที่ (6066, 5811) ได้
        21 กับ 22 เดินเลียบลำน้ำเห็นสูงต่างกันหนึ่งบล็อกตลอดแนว และเมื่อ tick
        น้ำฝั่งสูงไหลขวางลำน้ำ  ต้องจัดกลุ่มบนผืนเต็ม (`connected`) แต่เขียน
        เฉพาะส่วนที่เหลือ (`body`)
        """
        full = np.zeros((24, 24), dtype=bool)
        full[4:20, 4:20] = True
        corridor = np.zeros((24, 24), dtype=bool)
        corridor[:, 11:13] = True            # ร่องลำน้ำผ่ากลางผืนน้ำ
        body = full & ~corridor
        # ฝั่งซ้ายต่ำกว่าฝั่งขวาหนึ่งบล็อก — พอให้สอง component ได้คนละระดับ
        terrain = np.full((24, 24), 40, dtype=np.int16)
        terrain[:, :12] = 39

        out_dir = tempfile.mkdtemp()
        try:
            surface, _count, _levels = H._component_standing_surface(
                terrain, body, out_dir, connected=full
            )
            got = np.asarray(surface).copy()
            surface._mmap.close()
            del surface
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        left = got[4:20, 4:11]
        right = got[4:20, 13:20]
        self.assertTrue((left != UNRESOLVED).any(), "ฝั่งซ้ายไม่ได้ระดับเลย")
        self.assertTrue((right != UNRESOLVED).any(), "ฝั่งขวาไม่ได้ระดับเลย")
        self.assertEqual(
            sorted(set(got[got != UNRESOLVED].tolist())),
            sorted(set(left[left != UNRESOLVED].tolist())),
            "สองฝั่งของร่องลำน้ำได้ระดับผิวน้ำคนละค่า",
        )
        # และต้องไม่เขียนทับร่องลำน้ำ ซึ่งมีเจ้าของเป็น shape_waterway_patch
        self.assertTrue(
            (got[:, 11:13] == UNRESOLVED).all(),
            "เขียนผิวน้ำนิ่งทับร่องลำน้ำ",
        )


if __name__ == "__main__":
    unittest.main()
