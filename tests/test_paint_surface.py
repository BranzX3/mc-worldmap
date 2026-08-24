import unittest

import numpy as np

import config as C
import paint_surface as P
import surface as S


class _Blocks:
    def __init__(self):
        self.data = np.zeros((16, 384, 16), dtype=np.uint32)

    @staticmethod
    def _y(value):
        if isinstance(value, slice):
            return slice(
                None if value.start is None else value.start + 64,
                None if value.stop is None else value.stop + 64,
                value.step,
            )
        return value + 64

    def __getitem__(self, key):
        return self.data[key[0], self._y(key[1]), key[2]]

    def __setitem__(self, key, value):
        self.data[key[0], self._y(key[1]), key[2]] = value


class _Chunk:
    def __init__(self):
        self.blocks = _Blocks()


class _Painter:
    surface_ids = list(range(100, 100 + len(S.KEYS)))
    shore_cobble = 201
    bed_sand = 202
    bed_gravel = 203
    bed_clay = 204
    bed_mud = 205
    bed_stone = 206
    bed_calcite = 207
    sub_soil = [210, 211, 212, 213]
    sub_alpine = [220, 221, 222, 223]
    bed_stream = [230, 231, 232, 233]
    ice_edge = 240
    ice_mid = 241
    ice_core = 242
    water = 250
    snow_layers = list(range(260, 268))
    snow_block = 268
    stone = 269
    air = 0
    # slab ต่อบล็อกผิว — 0 = ไม่มี slab สำหรับบล็อกนั้น
    slab_ids = np.where(S.HAS_SLAB, 700 + np.arange(len(S.KEYS)), 0).astype(np.uint32)
    cliff_face = [800, 801, 802, 803, 804, 805, 806, 807]
    reed = 808

    @staticmethod
    def is_air(block_id):
        return block_id == 0

    @staticmethod
    def is_water(block_id):
        return block_id == _Painter.water

    @staticmethod
    def is_soft(block_id):
        return block_id == 0

    @staticmethod
    def is_preserved(block_id):
        return (
            block_id == 0
            or block_id == _Painter.water
            or block_id == _Painter.snow_block
            or block_id in _Painter.snow_layers
            or block_id in (
                _Painter.ice_edge, _Painter.ice_mid, _Painter.ice_core
            )
        )


class _NamedPalette:
    """palette จำลองที่คืนชื่อบล็อกได้ — verify_terrain_level อ่านชื่อจริง"""

    def __init__(self, names):
        self.names = names

    def __getitem__(self, block_id):
        name = self.names.get(int(block_id), "air")
        return type("_B", (), {"base_name": name})()


class _VerifyPainter(_Painter):
    STONE = 500
    GRASS = 501

    @staticmethod
    def fill_stone_to(chunk, world_y):
        """ถมหินตันจนถึง world_y (รวม y นั้นด้วย) — _Blocks เลื่อน index +64"""
        chunk.blocks.data[:, : world_y + 64 + 1, :] = _VerifyPainter.STONE

    def __init__(self):
        self.level = type("_L", (), {
            "block_palette": _NamedPalette({
                0: "air", _VerifyPainter.STONE: "stone",
                _VerifyPainter.GRASS: "grass_block",
            })
        })()
        self.SOFT_NAMES = {"air", "short_grass", "fern", "snow"}


class PaintSurfaceTests(unittest.TestCase):
    def test_lake_core_level_propagates_across_shallow_shelf(self):
        base_y = np.full((15, 15), 10, dtype=np.int32)
        water = np.zeros((15, 15), dtype=bool)
        water[4:11, 4:11] = True
        base_y[water] = 9
        depth = np.zeros((15, 15), dtype=np.uint8)
        depth[water] = 1
        depth[5:10, 5:10] = 2
        depth[6:9, 6:9] = 3

        leveled, lake = P.lake_surface_levels(
            base_y, water, depth, level_offset=1,
            min_core_depth=3, shelf_search=4,
        )

        self.assertTrue(lake[water].all())
        self.assertTrue((leveled[water] == 10).all())
        self.assertTrue((leveled[~water] == 10).all())

    def test_shallow_stream_keeps_dem_profile(self):
        base_y = np.arange(15, dtype=np.int32)[:, None] + np.zeros(
            (15, 5), dtype=np.int32
        )
        water = np.zeros_like(base_y, dtype=bool)
        water[:, 2] = True
        depth = np.zeros_like(base_y, dtype=np.uint8)
        depth[water] = 2

        leveled, lake = P.lake_surface_levels(base_y, water, depth)

        self.assertFalse(lake.any())
        np.testing.assert_array_equal(leveled, base_y)

    def test_lake_level_ramps_into_steep_inlet(self):
        base_y = np.full((9, 9), 10, dtype=np.int32)
        water = np.zeros((9, 9), dtype=bool)
        depth = np.zeros((9, 9), dtype=np.uint8)
        water[3:6, 3:6] = True
        depth[3:6, 3:6] = 3
        base_y[3:6, 3:6] = 9
        water[2, 4] = True
        base_y[2, 4] = 13
        depth[2, 4] = 1

        leveled, lake = P.lake_surface_levels(
            base_y, water, depth, shelf_search=4
        )

        self.assertTrue(lake[2, 4])
        self.assertEqual(int(leveled[2, 4]), 11)
        self.assertEqual(
            abs(int(leveled[2, 4]) - int(leveled[3, 4])), 1
        )

    def test_lake_level_ramp_rejoins_stream_profile(self):
        base_y = np.full((13, 5), 10, dtype=np.int32)
        water = np.zeros_like(base_y, dtype=bool)
        depth = np.zeros_like(base_y, dtype=np.uint8)
        water[8:11, 1:4] = True
        depth[8:11, 1:4] = 3
        base_y[8:11, 1:4] = 9
        water[2:8, 2] = True
        base_y[2:8, 2] = np.array([15, 14, 13, 12, 11, 10])
        depth[2:8, 2] = 1

        leveled, lake = P.lake_surface_levels(
            base_y, water, depth, shelf_search=8
        )

        channel = leveled[2:9, 2]
        self.assertTrue((np.abs(np.diff(channel)) <= 1).all())
        self.assertTrue(lake[2:8, 2].all())

    def test_shallow_transition_fronts_reconcile_without_a_step(self):
        base_y = np.full((5, 11), 12, dtype=np.int32)
        water = np.zeros_like(base_y, dtype=bool)
        depth = np.zeros_like(base_y, dtype=np.uint8)
        water[2, 1:10] = True
        depth[2, 1] = 3
        depth[2, 9] = 3
        depth[2, 2:9] = 1
        base_y[2, 1] = 9
        base_y[2, 9] = 12

        leveled, lake = P.lake_surface_levels(
            base_y, water, depth, shelf_search=8
        )

        self.assertTrue(lake[2, 1:10].all())
        self.assertTrue((np.abs(np.diff(leveled[2, 1:10])) <= 1).all())

    def test_lake_level_result_is_invariant_inside_padded_tile(self):
        base_y = np.full((80, 80), 10, dtype=np.int32)
        water = np.zeros((80, 80), dtype=bool)
        depth = np.zeros((80, 80), dtype=np.uint8)
        water[12:68, 10:70] = True
        depth[12:68, 10:70] = 1
        depth[16:64, 14:66] = 2
        depth[20:60, 18:62] = 3
        base_y[water] = 9
        base_y[12:20, 39:41] = np.arange(17, 9, -1)[:, None]

        full_y, full_lake = P.lake_surface_levels(base_y, water, depth)
        pad = P.PAD
        x0, x1, z0, z1 = 28, 52, 24, 56
        tile_y, tile_lake = P.lake_surface_levels(
            base_y[x0 - pad:x1 + pad, z0 - pad:z1 + pad],
            water[x0 - pad:x1 + pad, z0 - pad:z1 + pad],
            depth[x0 - pad:x1 + pad, z0 - pad:z1 + pad],
        )

        np.testing.assert_array_equal(
            tile_y[pad:-pad, pad:-pad], full_y[x0:x1, z0:z1]
        )
        np.testing.assert_array_equal(
            tile_lake[pad:-pad, pad:-pad], full_lake[x0:x1, z0:z1]
        )

    def test_neighbour_relief_detects_underwater_step(self):
        bed = np.full((5, 5), -20, dtype=np.int32)
        bed[2:, :] = -23

        relief = P.neighbour_relief(bed)

        self.assertTrue((relief[1:3, :] == 3).all())
        self.assertTrue((relief[[0, 3, 4], :] == 0).all())

    def test_alpine_lakebed_fines_with_depth_and_keeps_slopes_rocky(self):
        n = 192
        depth = np.zeros((n, n), dtype=np.int32)
        depth[:64] = 4
        depth[64:128] = 9
        depth[128:] = 22
        lake = depth > 0
        relief = np.zeros_like(depth)
        relief[80:112] = 3

        got = P.lakebed_materials(depth, relief, lake, x0=300, z0=700)

        shallow = got[:64]
        deep = got[128:]
        rocky = got[80:112]
        coarse_codes = {
            P.LAKEBED["gravel"], P.LAKEBED["cobble"], P.LAKEBED["stone"],
        }
        fine_codes = {P.LAKEBED["mud"], P.LAKEBED["clay"]}
        self.assertGreater(
            np.isin(shallow, list(coarse_codes)).mean(),
            np.isin(shallow, list(fine_codes)).mean(),
        )
        self.assertGreater(np.isin(deep, list(fine_codes)).mean(), 0.70)
        self.assertTrue(np.isin(rocky, list(coarse_codes)).all())

    def test_lakebed_materials_are_world_coordinate_invariant(self):
        depth = np.full((96, 112), 18, dtype=np.int32)
        relief = np.zeros_like(depth)
        lake = np.ones_like(depth, dtype=bool)

        full = P.lakebed_materials(depth, relief, lake, x0=500, z0=900)
        tile = P.lakebed_materials(
            depth[17:83, 23:91],
            relief[17:83, 23:91],
            lake[17:83, 23:91],
            x0=517,
            z0=923,
        )

        np.testing.assert_array_equal(tile, full[17:83, 23:91])

    def test_a_torrent_bed_is_rock_not_soil(self):
        """ก้นแก่งต้องเป็นหิน/กรวด ไม่ใช่ดินกับพอดโซลจาก palette สุ่ม

        นี่คืออาการที่ตาอ่านออกทันทีว่าไม่จริง: น้ำเชี่ยว 10% พัดตะกอนละเอียด
        ออกหมด แต่ระบบเดิมให้ก้นลำน้ำทุกสายเป็นชุดเดียวกันโดยไม่ดูความชันเลย
        """
        shape = (64, 64)
        depth = np.full(shape, 2, dtype=np.int32)
        relief = np.zeros(shape, dtype=np.int32)
        lake = np.zeros(shape, dtype=bool)          # ลำน้ำล้วน ไม่ใช่ทะเลสาบ
        torrent = np.full(shape, 200, dtype=np.uint8)
        calm = np.zeros(shape, dtype=np.uint8)

        fast = P.lakebed_materials(
            depth, relief, lake, x0=100, z0=200, flow_index=torrent)
        slow = P.lakebed_materials(
            depth, relief, lake, x0=100, z0=200, flow_index=calm)

        fine = [P.LAKEBED[n] for n in ("sand", "clay", "mud")]
        rock = [P.LAKEBED[n] for n in ("cobble", "stone")]
        self.assertFalse(np.isin(fast, fine).any(), "แก่งยังมีตะกอนละเอียด")
        self.assertTrue(np.isin(fast, rock).all())
        self.assertTrue(np.isin(slow, fine).any(), "น้ำนิ่งควรมีตะกอนละเอียด")

    def test_narrow_stream_texture_forms_coherent_material_patches(self):
        """ลำน้ำแคบต้องมี riffle/bar เปลี่ยนวัสดุ ไม่เป็นสีเดียวทั้ง reach"""
        shape = (128, 128)
        depth = np.full(shape, 2, dtype=np.int32)
        relief = np.zeros(shape, dtype=np.int32)
        lake = np.zeros(shape, dtype=bool)
        brisk = np.full(shape, 30, dtype=np.uint8)
        got = P.lakebed_materials(
            depth, relief, lake, x0=100, z0=200, flow_index=brisk,
        )
        self.assertGreaterEqual(np.unique(got).size, 2)
        same = 0
        edges = 0
        for a, b in ((got[1:, :], got[:-1, :]), (got[:, 1:], got[:, :-1])):
            same += int((a == b).sum())
            edges += int(a.size)
        self.assertLess(
            same / edges, 0.90,
            "วัสดุลำน้ำแคบยังเป็นแถบเดียวเกินไป",
        )

    def test_lake_mouth_has_a_coarse_bar_in_the_transition(self):
        """ปากทะเลสาบต้องมีกรวดปน ไม่ใช่ทรายยาวเป็นสีเดียว"""
        shape = (128, 128)
        depth = np.zeros(shape, dtype=np.int32)
        lake = np.zeros(shape, dtype=bool)
        lake[60:, :] = True
        depth[59, :] = 3              # still stream immediately upstream
        depth[60:, :] = 3
        got = P.lakebed_materials(
            depth, np.zeros(shape, dtype=np.int32), lake,
            x0=100, z0=200, flow_index=np.zeros(shape, dtype=np.uint8),
        )
        mouth = got[59, :]
        self.assertIn(P.LAKEBED["sand"], mouth.tolist())
        self.assertIn(P.LAKEBED["gravel"], mouth.tolist())

    def test_without_a_flow_field_the_old_stream_palette_still_applies(self):
        """ชุด product เก่าไม่มี flow_index — ต้องไม่พัง แค่ไม่ได้ของใหม่"""
        shape = (16, 16)
        got = P.lakebed_materials(
            np.full(shape, 2, dtype=np.int32),
            np.zeros(shape, dtype=np.int32),
            np.zeros(shape, dtype=bool),
            x0=0, z0=0,
        )

        self.assertTrue((got == P.LAKEBED["stream"]).all())

    def test_plants_do_not_grow_in_a_torrent(self):
        n = 128
        depth = np.full((n, n), 3, dtype=np.int32)
        bed_kind = np.full((n, n), P.LAKEBED["gravel"], dtype=np.uint8)
        relief = np.zeros((n, n), dtype=np.int32)
        torrent = np.full((n, n), 200, dtype=np.uint8)

        short, tall, lily = P.aquatic_vegetation_masks(
            depth, bed_kind, relief, x0=1200, z0=3400, flow_index=torrent
        )

        self.assertFalse((short | tall | lily).any())

    def test_reeds_stay_away_from_fast_water(self):
        # ใช้ผังเดียวกับเทสต์กกเดิม — เล็กกว่านี้แล้ว noise ของถิ่นที่อยู่ไม่ผ่าน
        # เกณฑ์ความหนาแน่นเลย ทำให้ "ไม่มีกก" ด้วยเหตุผลที่ไม่เกี่ยวกับกระแสน้ำ
        shape = (256, 256)
        water = np.zeros(shape, dtype=bool)
        water[:, 80:176] = True
        depth = np.zeros(shape, dtype=np.uint8)
        depth[:, 80] = 1
        depth[:, 81:175] = 8
        depth[:, 175] = 2
        ground = ~water
        torrent = np.full(shape, 200, dtype=np.uint8)

        calm_reeds = P.riparian_reed_heights(
            water, depth, ground, x0=600, z0=900,
            flow_index=np.zeros(shape, dtype=np.uint8),
        )
        fast_reeds = P.riparian_reed_heights(
            water, depth, ground, x0=600, z0=900, flow_index=torrent,
        )

        self.assertTrue(calm_reeds.any(), "ผังทดสอบไม่มีกกเลยตั้งแต่ต้น")
        self.assertFalse(fast_reeds.any())

    def test_aquatic_vegetation_clusters_on_shallows_and_avoids_deep_water(self):
        n = 256
        depth = np.full((n, n), 3, dtype=np.int32)
        depth[192:] = 12
        bed_kind = np.full(
            (n, n), P.LAKEBED["gravel"], dtype=np.uint8
        )
        relief = np.zeros((n, n), dtype=np.int32)

        short, tall, lily = P.aquatic_vegetation_masks(
            depth, bed_kind, relief, x0=1200, z0=3400
        )
        plants = short | tall

        self.assertTrue(plants[:192].any())
        self.assertFalse(plants[192:].any())
        self.assertFalse(lily.any())
        # A meadow mask should create both occupied and empty 32-block cells,
        # not a uniform salt-and-pepper density across the whole shelf.
        cell_density = np.asarray([
            plants[x:x + 32, z:z + 32].mean()
            for x in range(0, 192, 32)
            for z in range(0, n, 32)
        ])
        self.assertGreater(float(cell_density.max()), 0.08)
        self.assertLess(float(cell_density.min()), 0.02)

    def test_aquatic_vegetation_is_world_coordinate_invariant(self):
        depth = np.full((100, 120), 3, dtype=np.int32)
        bed_kind = np.full(
            depth.shape, P.LAKEBED["clay"], dtype=np.uint8
        )
        relief = np.zeros_like(depth)

        full = P.aquatic_vegetation_masks(
            depth, bed_kind, relief, x0=500, z0=800
        )
        tile = P.aquatic_vegetation_masks(
            depth[13:87, 19:101],
            bed_kind[13:87, 19:101],
            relief[13:87, 19:101],
            x0=513,
            z0=819,
        )

        for full_mask, tile_mask in zip(full, tile):
            np.testing.assert_array_equal(
                tile_mask, full_mask[13:87, 19:101]
            )

    def test_lilies_are_sparse_but_present_in_sheltered_mud(self):
        shape = (512, 512)
        depth = np.ones(shape, dtype=np.int32)
        bed_kind = np.full(shape, P.LAKEBED["mud"], dtype=np.uint8)
        relief = np.zeros(shape, dtype=np.int32)

        _short, _tall, lily = P.aquatic_vegetation_masks(
            depth, bed_kind, relief, x0=2200, z0=4100
        )

        self.assertTrue(lily.any())
        self.assertLess(float(lily.mean()), 0.02)

    def test_riparian_reeds_only_grow_beside_shallow_water(self):
        shape = (256, 256)
        water = np.zeros(shape, dtype=bool)
        water[:, 80:176] = True
        depth = np.zeros(shape, dtype=np.uint8)
        depth[:, 80] = 1
        depth[:, 81:175] = 8
        depth[:, 175] = 2
        ground = ~water

        reeds = P.riparian_reed_heights(
            water, depth, ground, x0=600, z0=900
        )

        self.assertTrue(reeds.any())
        self.assertFalse(reeds[water].any())
        self.assertFalse(reeds[:, :78].any())
        self.assertFalse(reeds[:, 178:].any())
        self.assertLessEqual(int(reeds.max()), 3)

    def test_riparian_reeds_are_world_coordinate_invariant(self):
        water = np.zeros((100, 120), dtype=bool)
        water[20:80, 45:75] = True
        depth = water.astype(np.uint8)
        ground = ~water

        full = P.riparian_reed_heights(
            water, depth, ground, x0=500, z0=800
        )
        tile = P.riparian_reed_heights(
            water[13:87, 19:101],
            depth[13:87, 19:101],
            ground[13:87, 19:101],
            x0=513,
            z0=819,
        )

        np.testing.assert_array_equal(tile, full[13:87, 19:101])

    def test_lake_shore_search_reaches_across_shallow_shelf(self):
        water = np.zeros((64, 64), dtype=bool)
        water[12:52, 12:52] = True
        depth = np.zeros((64, 64), dtype=np.int16)
        for inset, value in ((0, 1), (2, 2), (4, 3), (6, 8)):
            depth[12 + inset:52 - inset, 12 + inset:52 - inset] = value

        probability = P.lake_shore_probability(water, depth)
        outside_shore = (probability > 0) & ~water

        self.assertTrue(outside_shore.any())
        self.assertTrue(outside_shore[11, 32])

    def test_fine_sediment_disappears_above_the_treeline(self):
        """ทะเลสาบเหนือแนวไม้ต้องไม่มีทราย/ดินเหนียว/โคลนที่ก้น

        แอ่งน้ำเหนือแนวไม้เป็นน้ำแข็งละลายบนหินเปล่า ไม่มีตะกอนละเอียดสะสม
        เดิมเลือกวัสดุจาก depth กับ noise ล้วน ๆ ทะเลสาบที่ 2,000 ม. จึงได้ก้น
        ทรายเหมือนทะเลสาบในหุบเขา — เห็นเป็นหาดทรายกลางลานหิน
        """
        depth = np.full((40, 40), 8, dtype=np.int32)
        relief = np.zeros((40, 40), dtype=np.int32)
        lake = np.ones((40, 40), dtype=bool)
        fine = np.asarray(
            [P.LAKEBED[n] for n in ("sand", "clay", "mud")], dtype=np.uint8
        )

        valley = P.lakebed_materials(
            depth, relief, lake, x0=500, z0=900,
            elev_m=np.full((40, 40), 800.0, dtype=np.float32),
        )
        alpine = P.lakebed_materials(
            depth, relief, lake, x0=500, z0=900,
            elev_m=np.full((40, 40), S.TREELINE + 250, dtype=np.float32),
        )

        self.assertTrue(np.isin(valley, fine).any(), "หุบเขาควรมีตะกอนละเอียด")
        self.assertFalse(
            np.isin(alpine, fine).any(),
            "เหนือแนวไม้ยังมีทราย/ดินเหนียว/โคลนที่ก้นน้ำ",
        )
        # ไม่ระบุความสูงต้องได้พฤติกรรมเดิม เพื่อไม่ให้ผู้เรียกเก่าเปลี่ยนผล
        legacy = P.lakebed_materials(depth, relief, lake, x0=500, z0=900)
        np.testing.assert_array_equal(legacy, valley)

    def test_shore_probabilities_only_read_nearby_input(self):
        """ค่าที่ cell ใด ๆ ต้องขึ้นกับ input ในรัศมี reach เท่านั้น

        `report_metrics.bank_metrics` อาศัยคุณสมบัตินี้ในการแบ่ง tile + pad
        แทนการกาง float32 เต็มแผนที่สองชุด ถ้ามีใครเพิ่มกฎที่มองไกลกว่า
        `max(shore_width, LAKE_SHORE_SEARCH_BLOCKS)` การแบ่ง tile จะให้ตัวเลขคนละชุด
        กับการคำนวณทีเดียวทั้งแผนที่ โดยไม่มีอะไรฟ้อง
        """
        shore_width = 4
        reach = max(shore_width, int(getattr(C, "LAKE_SHORE_SEARCH_BLOCKS", 12)))
        # ทะเลสาบลึกวางไว้ *นอก* หน้าต่างแต่ใกล้พอให้ความลึกแพร่เข้ามา — ถ้า
        # หน้าต่างไม่มี pad มันจะมองไม่เห็นทะเลสาบเลย แล้วตลิ่งช่วงบนของลำธาร
        # จะถูกจัดเป็น stream_bank แทน lake_shore
        #
        # ลำธารตื้นพาดผ่านทั้งหน้าต่างจึงมีทั้งสองสาขาอยู่ในภาพเดียว: ช่วงที่ยัง
        # อยู่ในรัศมีทะเลสาบได้ lake_shore ส่วนช่วงล่างได้ stream_bank
        # (สองค่านี้ตัดกันเสมอ — stream_bank เป็นบวกได้เฉพาะที่ lake_body == 0)
        water = np.zeros((80, 80), dtype=bool)
        depth = np.zeros((80, 80), dtype=np.uint8)
        water[14:26, 30:50] = True
        depth[14:26, 30:50] = 12
        water[26:60, 40] = True
        depth[26:60, 40] = 1

        full = S.shore_probabilities(
            water, depth, shore_width=shore_width, search_blocks=reach
        )

        z0, z1, x0, x1 = 30, 54, 20, 60
        pz0, pz1 = z0 - reach, z1 + reach
        px0, px1 = x0 - reach, x1 + reach
        windowed = S.shore_probabilities(
            water[pz0:pz1, px0:px1], depth[pz0:pz1, px0:px1],
            shore_width=shore_width, search_blocks=reach,
        )
        inner = np.s_[z0 - pz0:z1 - pz0, x0 - px0:x1 - px0]

        for got, want in zip(windowed, full):
            self.assertTrue(want[z0:z1, x0:x1].any())
            np.testing.assert_array_equal(got[inner], want[z0:z1, x0:x1])

    def test_shallow_stream_has_bank_probability(self):
        water = np.zeros((24, 24), dtype=bool)
        water[12, :] = True
        depth = water.astype(np.uint8)

        lake, bank = S.shore_probabilities(water, depth)

        self.assertFalse(lake.any())
        self.assertTrue(bank[11, 12] > 0.0)
        self.assertTrue(bank[10, 12] > 0.0)
        self.assertFalse(bank[12, 12])

    def test_cliff_face_depth_uses_lowest_cardinal_neighbour(self):
        height = np.full((5, 5), 20, dtype=np.int32)
        height[2, 2] = 28
        height[2, 1] = 25
        height[1, 2] = 18

        depth = P.cliff_face_depths(height)

        self.assertEqual(int(depth[2, 2]), 10)
        self.assertEqual(int(depth[2, 1]), 5)
        self.assertEqual(int(depth[0, 0]), 0)

    def test_cliff_face_depth_ignores_single_block_step(self):
        height = np.array([[10, 11], [10, 11]], dtype=np.int32)

        self.assertFalse(P.cliff_face_depths(height).any())

    def test_stream_banks_need_a_taller_face_to_count_as_a_cliff(self):
        """ตลิ่งที่เกิดจากการขุดร่องน้ำต้องไม่ถูกทาเป็นชั้นหินลายทาง

        การขุดร่องสร้างหน้าตั้ง 2-3 บล็อกสองข้างเป็นปกติ เกณฑ์หน้าผาทั่วไป
        (2 บล็อก) จึงทำให้ตลิ่งลำธารทุกสายเข้าเกณฑ์ palette ชั้นหิน แต่หน้าผา
        ภูเขาจริงต้องไม่หายไปด้วย จึงขยับเกณฑ์เฉพาะรอบลำน้ำ
        """
        height = np.full((9, 9), 30, dtype=np.int32)
        height[4, :] = 27                      # ร่องน้ำลึก 3 บล็อก
        height[:, 8] = 24                      # หน้าผาภูเขาลึก 6 บล็อก
        stream = np.zeros((9, 9), dtype=bool)
        stream[4, :] = True

        minimum = P.stream_cliff_minimum(stream)
        depth = P.cliff_face_depths(height, minimum_drop=minimum)

        self.assertEqual(int(minimum[3, 2]), P.STREAM_CLIFF_MIN_DROP)
        self.assertEqual(int(minimum[0, 2]), 2)
        self.assertEqual(
            int(depth[3, 2]), 0, "ตลิ่งลำธารยังถูกนับเป็นหน้าผา"
        )
        self.assertGreater(
            int(depth[0, 7]), 0, "หน้าผาภูเขาจริงหายไปด้วย"
        )

    def test_contextual_blend_uses_neighbour_material_majority(self):
        cls = np.full((7, 7), S.IDX["stone"], dtype=np.uint8)
        cls[3, 3] = S.IDX["andesite"]
        damp = np.zeros(cls.shape, dtype=np.float32)
        slope = np.full(cls.shape, 50.0, dtype=np.float32)

        got = P.contextual_surface_blend(cls, damp, slope)

        self.assertEqual(int(got[3, 3]), S.IDX["stone"])

    def test_contextual_blend_rejects_incompatible_bedrock_jump(self):
        cls = np.full((7, 7), S.IDX["deepslate"], dtype=np.uint8)
        cls[3, 3] = S.IDX["calcite"]
        fields = np.zeros(cls.shape, dtype=np.float32)

        got = P.contextual_surface_blend(cls, fields, fields)

        self.assertEqual(int(got[3, 3]), S.IDX["calcite"])

    def test_contextual_blend_accepts_adjacent_bedrock_step(self):
        cls = np.full((7, 7), S.IDX["diorite"], dtype=np.uint8)
        cls[3, 3] = S.IDX["calcite"]
        fields = np.zeros(cls.shape, dtype=np.float32)

        got = P.contextual_surface_blend(cls, fields, fields)

        self.assertEqual(int(got[3, 3]), S.IDX["diorite"])

    def test_contextual_blend_creates_soil_rock_transition_family(self):
        cls = np.full((7, 7), S.IDX["grass"], dtype=np.uint8)
        cls[:, 4:] = S.IDX["stone"]
        damp = np.full(cls.shape, 0.7, dtype=np.float32)
        slope = np.full(cls.shape, 20.0, dtype=np.float32)

        got = P.contextual_surface_blend(cls, damp, slope)
        transition = got[:, 3:5]
        allowed = {
            S.IDX[k] for k in (
                "coarse", "gravel", "moss", "mossy_cob", "stone",
            )
        }

        self.assertTrue(set(map(int, np.unique(transition))) <= allowed)

    def test_light_bedrock_edge_uses_light_transition_materials(self):
        cls = np.full((7, 7), S.IDX["grass"], dtype=np.uint8)
        cls[:, 4:] = S.IDX["calcite"]
        damp = np.full(cls.shape, 0.75, dtype=np.float32)
        slope = np.full(cls.shape, 20.0, dtype=np.float32)

        got = P.contextual_surface_blend(cls, damp, slope)
        allowed = {
            S.IDX[k] for k in (
                "coarse", "gravel", "moss", "pale_moss", "diorite",
            )
        }

        self.assertTrue(set(map(int, np.unique(got[:, 3:5]))) <= allowed)

    def test_artificial_smooth_and_basalt_are_not_geologic_family(self):
        self.assertNotIn(S.IDX["smooth"], P._GEOLOGIC_TRANSITION_IDS)
        self.assertNotIn(S.IDX["basalt"], P._GEOLOGIC_TRANSITION_IDS)

    def test_dead_brain_coral_is_weathering_not_bedrock(self):
        self.assertIn(S.IDX["dead_brain"], P._GEOLOGIC_TRANSITION_IDS)
        self.assertNotIn(S.IDX["dead_brain"], P._BEDROCK_IDS)
        self.assertFalse(S.HAS_SLAB[S.IDX["dead_brain"]])

    def test_contextual_blend_preserves_water(self):
        cls = np.full((5, 5), S.IDX["stone"], dtype=np.uint8)
        cls[2, 2] = S.IDX["water"]
        fields = np.zeros(cls.shape, dtype=np.float32)

        got = P.contextual_surface_blend(cls, fields, fields)

        self.assertEqual(int(got[2, 2]), S.IDX["water"])

    def test_deep_lake_does_not_also_get_stream_bank(self):
        water = np.zeros((40, 40), dtype=bool)
        water[10:30, 10:30] = True
        depth = np.zeros((40, 40), dtype=np.uint8)
        depth[10:30, 10:30] = 10

        lake, bank = S.shore_probabilities(water, depth)

        self.assertTrue(lake[9, 20] > 0.0)
        self.assertFalse(bank[9, 20])

    def test_bhash_array_matches_scalar_hash(self):
        xs = np.arange(17)[:, None]
        zs = np.arange(13)[None, :]
        for salt in (0, 5, 99):
            got = P.bhash_array(xs, zs, salt)
            for x, z in ((0, 0), (1, 2), (16, 12), (7, 9)):
                self.assertAlmostEqual(
                    float(got[x, z]), P.bhash(x, z, salt), places=7
                )

    def test_water_preflight_reports_vertical_clipping(self):
        # สร้างการชนขอบจาก config ไม่ใช่จากค่าคงที่ — ที่สเกลใหม่ (4 m/block,
        # พื้นหุบเขาอยู่ y=0) มีที่ว่างใต้ผิวน้ำ 63 บล็อก ความลึกคงที่ 30 จึงไม่
        # ชนอะไรอีกแล้ว ต้องคำนวณความลึกที่เกินพื้นที่จริงเสมอ
        available = C.Y_TERRAIN_MIN - (C.Y_FILL_BOTTOM + 1)
        overshoot = 7
        heightmap = np.zeros((2, 2), dtype=np.uint16)
        landcover = np.full((2, 2), S.LC["water"], dtype=np.uint8)
        depth = np.full((2, 2), available + overshoot, dtype=np.uint16)

        result = P.water_depth_preflight(heightmap, landcover, depth, row_batch=1)

        self.assertEqual(result["clipped_columns"], 4)
        self.assertEqual(result["clipped_blocks"], 4 * overshoot)
        self.assertEqual(
            result["required_fill_bottom"], C.Y_FILL_BOTTOM - overshoot
        )

    def test_water_preflight_uses_derived_mask(self):
        heightmap = np.full((2, 2), 65535, dtype=np.uint16)
        landcover = np.full((2, 2), S.LC["grass"], dtype=np.uint8)
        depth = np.zeros((2, 2), dtype=np.uint8)
        depth[0, 1] = 7
        water_mask = np.zeros((2, 2), dtype=bool)
        water_mask[0, 1] = True

        result = P.water_depth_preflight(
            heightmap, landcover, depth, water_mask=water_mask
        )

        self.assertEqual(result["columns"], 1)
        self.assertEqual(result["max_requested"], 7)

    def test_water_input_validation_rejects_mask_depth_mismatch_and_overflow(self):
        mask = np.array([[True, False], [True, False]])
        depth = np.array([[0, 2], [31, 0]], dtype=np.uint8)

        result = P.validate_water_inputs(mask, depth, max_depth=30, row_batch=1)

        self.assertEqual(result["water_without_depth"], 1)
        self.assertEqual(result["nonwater_with_depth"], 1)
        self.assertEqual(result["too_deep"], 1)
        self.assertEqual(result["max_depth"], 31)

    def test_source_water_ticks_cover_stream_surface_and_curtain(self):
        class TickChunk:
            def __init__(self):
                self.misc = {}
                self.changed = False

        class TickLevel:
            def __init__(self):
                self.chunk = TickChunk()
                self.put = []

            def get_chunk(self, _cx, _cz, _dimension):
                return self.chunk

            def put_chunk(self, chunk, dimension):
                self.put.append((chunk, dimension))

        level = TickLevel()
        surface = np.full((2, 2), 60, dtype=np.int16)
        flowing = np.array([[True, False], [False, True]])
        top = np.full((2, 2), np.iinfo(np.int16).min, dtype=np.int16)
        top[0, 0] = 63

        queued = P.schedule_source_water_ticks(
            level, surface, flowing, top,
            10, 12, 20, 22, 10, 20, dimension="test",
        )

        self.assertEqual(queued, 5)
        self.assertTrue(level.chunk.changed)
        self.assertEqual(len(level.put), 1)
        ticks = level.chunk.misc["fluid_ticks"]
        self.assertEqual(
            set(ticks),
            {
                (10, 60, 20), (10, 61, 20), (10, 62, 20),
                (10, 63, 20), (11, 60, 21),
            },
        )
        self.assertTrue(all(
            fluid == "minecraft:water" and 1 <= delay <= 4 and priority == 0
            for fluid, delay, priority in ticks.values()
        ))

    def test_derived_water_mask_maps_eroded_edge_to_wetland(self):
        landcover = np.array([
            [S.LC["water"], S.LC["grass"]],
            [S.LC["forest"], S.LC["water"]],
        ], dtype=np.uint8)
        water_mask = np.array([
            [False, True],
            [False, True],
        ])

        got = S.apply_water_mask(landcover, water_mask)

        self.assertEqual(int(got[0, 0]), S.LC["wetland"])
        self.assertEqual(int(got[0, 1]), S.LC["water"])
        self.assertEqual(int(got[1, 0]), S.LC["forest"])

    def test_dense_chunk_paints_water_ice_soil_and_snow(self):
        painter = _Painter()
        n = 16
        y = np.full((n, n), 60, dtype=np.int32)
        elev = np.full((n, n), 800.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["grass"], dtype=np.uint8)
        classes[:4] = S.IDX["water"]
        classes[4:8] = S.IDX["ice"]
        snow = np.zeros((n, n), dtype=np.uint8)
        snow[8:12] = 4
        soil = classes == S.IDX["grass"]
        depth = np.zeros((n, n), dtype=np.uint8)
        depth[:4] = 5
        shore = np.zeros((n, n), dtype=bool)
        shore[12:] = True
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.5, dtype=np.float32),
            "patch_c": np.full((n, n), 0.5, dtype=np.float32),
            "damp": np.full((n, n), 0.4, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }

        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
        )
        chunk = _Chunk()
        written_snow = P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(written_snow, 4 * n)
        self.assertEqual(
            int(chunk.blocks[0, int(dense["bed_y"][0, 0]), 0]),
            int(dense["bed_id"][0, 0]),
        )
        self.assertEqual(
            int(chunk.blocks[0, int(dense["y"][0, 0]), 0]), painter.water
        )
        self.assertEqual(int(dense["y"][0, 0]), 61)
        self.assertEqual(int(dense["y"][13, 0]), 60)
        self.assertEqual(int(chunk.blocks[5, 59, 0]), painter.ice_mid)
        self.assertEqual(int(chunk.blocks[9, 61, 0]), painter.snow_layers[3])
        self.assertIn(int(chunk.blocks[13, 59, 0]), painter.sub_soil)

    def test_dense_chunk_paints_only_declared_waterfall_curtain(self):
        painter = _Painter()
        n = 16
        y = np.full((n, n), 60, dtype=np.int32)
        elev = np.full((n, n), 800.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["grass"], dtype=np.uint8)
        classes[0, 0] = S.IDX["water"]
        snow = np.zeros((n, n), dtype=np.uint8)
        soil = classes == S.IDX["grass"]
        depth = np.zeros((n, n), dtype=np.uint8)
        depth[0, 0] = 1
        shore = np.zeros((n, n), dtype=bool)
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.5, dtype=np.float32),
            "patch_c": np.full((n, n), 0.5, dtype=np.float32),
            "damp": np.full((n, n), 0.4, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }
        water_surface = np.full((n, n), 60, dtype=np.int16)
        waterfall_top = np.full(
            (n, n), np.iinfo(np.int16).min, dtype=np.int16
        )
        waterfall_top[0, 0] = 64
        waterfall_lip = np.zeros((n, n), dtype=bool)
        waterfall_pool = np.zeros((n, n), dtype=bool)
        waterfall_pool[0, 0] = True
        flowing_water = np.zeros((n, n), dtype=bool)
        flowing_water[0, 0] = True

        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
            water_surface_y=water_surface,
            global_lake_mask=np.zeros((n, n), dtype=bool),
            waterfall_top_y=waterfall_top,
            waterfall_lip_mask=waterfall_lip,
            waterfall_pool_mask=waterfall_pool,
            flowing_water_mask=flowing_water,
        )
        chunk = _Chunk()
        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        for block_y in range(60, 65):
            self.assertEqual(
                int(chunk.blocks[0, block_y, 0]), painter.water
            )
        self.assertNotEqual(int(chunk.blocks[0, 65, 0]), painter.water)
        self.assertEqual(int(dense["bed_id"][0, 0]), painter.bed_stone)

    def test_lake_only_dense_paint_leaves_land_columns_untouched(self):
        painter = _Painter()
        n = 16
        y = np.full((n, n), 60, dtype=np.int32)
        elev = np.full((n, n), 800.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["grass"], dtype=np.uint8)
        classes[:4] = S.IDX["water"]
        snow = np.full((n, n), 4, dtype=np.uint8)
        soil = classes == S.IDX["grass"]
        depth = np.zeros((n, n), dtype=np.uint8)
        depth[:4] = 5
        shore = np.zeros((n, n), dtype=bool)
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.5, dtype=np.float32),
            "patch_c": np.full((n, n), 0.5, dtype=np.float32),
            "damp": np.full((n, n), 0.4, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }
        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
        )
        chunk = _Chunk()
        chunk.blocks.data[:] = 77

        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n,
            lake_only=True,
        )

        self.assertEqual(int(chunk.blocks[8, 60, 8]), 77)
        self.assertEqual(int(chunk.blocks[0, 61, 8]), painter.water)
        self.assertEqual(
            int(chunk.blocks[0, int(dense["bed_y"][0, 8]), 8]),
            int(dense["bed_id"][0, 8]),
        )
        self.assertEqual(
            int(chunk.blocks[0, int(dense["bed_y"][0, 8]) - 1, 8]),
            77,
        )

    def test_repaint_backfills_old_water_cavity_below_new_lakebed(self):
        painter = _Painter()
        n = 16
        y = np.full((n, n), -30, dtype=np.int32)
        elev = np.full((n, n), 500.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["water"], dtype=np.uint8)
        snow = np.zeros((n, n), dtype=np.uint8)
        soil = np.zeros((n, n), dtype=bool)
        depth = np.full((n, n), 6, dtype=np.uint8)
        shore = np.zeros((n, n), dtype=bool)
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.5, dtype=np.float32),
            "patch_c": np.full((n, n), 0.5, dtype=np.float32),
            "damp": np.full((n, n), 0.4, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }
        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
        )
        chunk = _Chunk()
        # Simulate a previous 20-block-deep water cavity.
        chunk.blocks[:, -50:-29, :] = painter.water
        old_bedrock = 999
        chunk.blocks[:, -60, :] = old_bedrock

        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n,
            lake_only=True,
        )

        bed_y = int(dense["bed_y"][0, 0])
        self.assertEqual(int(chunk.blocks[0, bed_y - 1, 0]), painter.stone)
        self.assertEqual(int(chunk.blocks[0, -50, 0]), painter.stone)
        self.assertEqual(int(chunk.blocks[0, -60, 0]), old_bedrock)
        self.assertEqual(int(chunk.blocks[0, bed_y + 1, 0]), painter.water)

    def test_snow_thickness_continues_across_full_block_boundary(self):
        painter = _Painter()
        n = 16
        y = np.full((n, n), 60, dtype=np.int32)
        elev = np.full((n, n), 2400.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["stone"], dtype=np.uint8)
        snow = np.zeros((n, n), dtype=np.uint8)
        snow[0] = 7
        snow[1] = 8
        snow[2] = 9
        snow[3] = 15
        snow[4] = 16
        soil = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        shore = np.zeros((n, n), dtype=bool)
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.5, dtype=np.float32),
            "patch_c": np.full((n, n), 0.5, dtype=np.float32),
            "damp": np.full((n, n), 0.4, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }
        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
        )
        chunk = _Chunk()
        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(int(chunk.blocks[0, 61, 0]), painter.snow_layers[6])
        self.assertEqual(int(chunk.blocks[1, 61, 0]), painter.snow_block)
        self.assertEqual(int(chunk.blocks[2, 61, 0]), painter.snow_block)
        self.assertEqual(int(chunk.blocks[2, 62, 0]), painter.snow_layers[0])
        self.assertEqual(int(chunk.blocks[3, 61, 0]), painter.snow_block)
        self.assertEqual(int(chunk.blocks[3, 62, 0]), painter.snow_layers[6])
        self.assertEqual(int(chunk.blocks[4, 61, 0]), painter.snow_block)
        self.assertEqual(int(chunk.blocks[4, 62, 0]), painter.snow_block)

    def test_snow_block_and_layer_hide_one_block_terrain_step(self):
        painter = _Painter()
        n = 16
        y = np.full((n, n), 61, dtype=np.int32)
        y[0] = 60
        elev = np.full((n, n), 2400.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["stone"], dtype=np.uint8)
        snow = np.full((n, n), 4, dtype=np.uint8)
        snow[0] = 12
        soil = np.zeros((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        shore = np.zeros((n, n), dtype=bool)
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.5, dtype=np.float32),
            "patch_c": np.full((n, n), 0.5, dtype=np.float32),
            "damp": np.full((n, n), 0.4, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }
        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
        )
        chunk = _Chunk()

        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(int(chunk.blocks[0, 61, 0]), painter.snow_block)
        self.assertEqual(int(chunk.blocks[0, 62, 0]), painter.snow_layers[3])
        self.assertEqual(int(chunk.blocks[1, 62, 0]), painter.snow_layers[3])

    def test_snow_quantization_has_no_cardinal_step_over_one_layer(self):
        amount = np.zeros((49, 49), dtype=np.float32)
        amount[24, 24] = 3.0
        amount[8:41, 8:41] = np.maximum(amount[8:41, 8:41], 2.0)
        units = S.snow_units_from_amount(amount)

        self.assertEqual(int(units.max()), S.MAX_SNOW_UNITS)
        self.assertLessEqual(int(np.abs(np.diff(units.astype(int), axis=0)).max()), 1)
        self.assertLessEqual(int(np.abs(np.diff(units.astype(int), axis=1)).max()), 1)

    def test_snow_smoothing_is_invariant_with_sixteen_block_padding(self):
        rng = np.random.default_rng(771)
        amount = rng.random((96, 96), dtype=np.float32) * 2.4 - 0.2
        full = S.snow_units_from_amount(amount)
        padded = S.snow_units_from_amount(amount[8:88, 8:88])

        np.testing.assert_array_equal(full[24:72, 24:72], padded[16:64, 16:64])

    def test_snow_uses_heightmap_fraction_to_hide_rounded_terrain_step(self):
        amount = np.ones((2, 1), dtype=np.float32)
        terrain_y = np.array([[60.49], [60.51]], dtype=np.float32)
        surface_y = np.rint(terrain_y).astype(np.int32)

        units = S.snow_units_from_amount(
            amount, terrain_offset=terrain_y - surface_y
        )
        snow_top_eighths = (surface_y + 1) * 8 + units.astype(np.int32)

        self.assertEqual(units[:, 0].tolist(), [12, 4])
        self.assertEqual(
            int(snow_top_eighths[0, 0]), int(snow_top_eighths[1, 0])
        )

    def test_snow_heightmap_smoothing_keeps_nominal_two_block_cap_smooth(self):
        amount = np.full((2, 1), 3.0, dtype=np.float32)
        terrain_y = np.array([[60.49], [60.51]], dtype=np.float32)
        surface_y = np.rint(terrain_y).astype(np.int32)

        units = S.snow_units_from_amount(
            amount, terrain_offset=terrain_y - surface_y
        )
        snow_top_eighths = (surface_y + 1) * 8 + units.astype(np.int32)

        self.assertEqual(units[:, 0].tolist(), [20, 12])
        self.assertEqual(
            int(snow_top_eighths[0, 0]), int(snow_top_eighths[1, 0])
        )

    def test_snow_heightmap_smoothing_does_not_unblock_water(self):
        amount = np.ones((2, 2), dtype=np.float32)
        blocked = np.array([[False, True], [True, False]])
        offset = np.full((2, 2), 0.49, dtype=np.float32)

        units = S.snow_units_from_amount(
            amount, blocked=blocked, terrain_offset=offset
        )

        self.assertTrue((units[blocked] == 0).all())
        self.assertTrue((units[~blocked] > 0).all())

    def test_deep_snow_keeps_natural_substrate(self):
        elev = np.full((48, 48), 3000.0, dtype=np.float32)
        landcover = np.full((48, 48), S.LC["rock"], dtype=np.uint8)

        surface, _forest, snow, _soil = S.classify(
            elev, landcover, 4.0
        )

        self.assertGreaterEqual(int(snow.min()), 8)
        self.assertFalse(np.isin(surface, [S.IDX["snow"], S.IDX["snow_thin"]]).any())

    def test_damp_non_beach_shore_becomes_wetland_patch(self):
        painter = _Painter()
        n = 12
        y = np.full((n, n), 60, dtype=np.int32)
        elev = np.full((n, n), 700.0, dtype=np.float32)
        classes = np.full((n, n), S.IDX["grass"], dtype=np.uint8)
        snow = np.zeros((n, n), dtype=np.uint8)
        soil = np.ones((n, n), dtype=bool)
        depth = np.zeros((n, n), dtype=np.uint8)
        shore = np.ones((n, n), dtype=bool)
        shore_probability = np.zeros((n, n), dtype=np.float32)
        decor = {
            "patch_a": np.full((n, n), 0.5, dtype=np.float32),
            "patch_b": np.full((n, n), 0.2, dtype=np.float32),
            "patch_c": np.full((n, n), 0.8, dtype=np.float32),
            "damp": np.full((n, n), 0.8, dtype=np.float32),
            "slope": np.full((n, n), 5.0, dtype=np.float32),
        }

        dense = P.prepare_dense_fields(
            painter, y, elev, classes, snow, soil, depth,
            shore, shore_probability, decor,
            0, n, 0, n, 0, 0,
        )

        self.assertTrue(dense["wet_shore"].all())
        self.assertTrue(dense["wet_mud_mask"].all())
        self.assertFalse(dense["beach"].any())
        self.assertTrue(
            (dense["surface_id"] == painter.surface_ids[S.IDX["mud"]]).all()
        )

    def test_soft_buffer_preserves_first_non_soft_write(self):
        painter = _Painter()
        chunk = _Chunk()
        buffer = P.SoftBlockBuffer(painter, lambda _cx, _cz: chunk)

        self.assertTrue(buffer.set(2, 65, 3, 900))
        self.assertFalse(buffer.set(2, 65, 3, 901))
        self.assertEqual(int(chunk.blocks[2, 65, 3]), 0)
        self.assertEqual(buffer.flush(), 1)
        self.assertEqual(int(chunk.blocks[2, 65, 3]), 900)

    def _vegetation_clear_fixture(self, water_column=False):
        """dense fields ที่ผิวดินอยู่ y=60 ทั้งแปลง — ใช้ทดสอบการล้างพืช"""
        n = 16
        shape = (n, n)
        y = np.full(shape, 60, dtype=np.int32)
        water = np.zeros(shape, dtype=bool)
        if water_column:
            water[:] = True
        return n, {"y": y, "water": water}

    def test_vegetation_clear_removes_plants_above_surface(self):
        painter = _Painter()
        n, dense = self._vegetation_clear_fixture()
        chunk = _Chunk()
        # ต้นไม้: ลำต้น y 61..70 และใบที่ y 71
        chunk.blocks[4, 61:71, 4] = 900
        chunk.blocks[4, 71, 4] = 901
        # ก้อนหิน decor ที่ใช้บล็อกเดียวกับภูมิประเทศ
        chunk.blocks[8, 61, 8] = painter.stone
        # ผิวดินและใต้ผิวต้องไม่ถูกแตะ
        chunk.blocks[4, 60, 4] = painter.surface_ids[0]
        chunk.blocks[4, 59, 4] = painter.stone

        cleared = P.clear_vegetation_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(cleared, 12)
        self.assertEqual(int(chunk.blocks[4, 61, 4]), 0)
        self.assertEqual(int(chunk.blocks[4, 71, 4]), 0)
        self.assertEqual(int(chunk.blocks[8, 61, 8]), 0)
        self.assertEqual(int(chunk.blocks[4, 60, 4]), painter.surface_ids[0])
        self.assertEqual(int(chunk.blocks[4, 59, 4]), painter.stone)

    def test_vegetation_clear_keeps_snow_above_surface(self):
        painter = _Painter()
        n, dense = self._vegetation_clear_fixture()
        chunk = _Chunk()
        chunk.blocks[2, 61, 2] = painter.snow_block
        chunk.blocks[2, 62, 2] = painter.snow_layers[3]
        chunk.blocks[3, 61, 3] = 900          # พืช ต้องหาย

        cleared = P.clear_vegetation_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(cleared, 1)
        self.assertEqual(int(chunk.blocks[2, 61, 2]), painter.snow_block)
        self.assertEqual(int(chunk.blocks[2, 62, 2]), painter.snow_layers[3])
        self.assertEqual(int(chunk.blocks[3, 61, 3]), 0)

    def test_vegetation_clear_skips_water_columns(self):
        painter = _Painter()
        n, dense = self._vegetation_clear_fixture(water_column=True)
        chunk = _Chunk()
        chunk.blocks[5, 61, 5] = 900          # lily pad เก่าเหนือผิวน้ำ

        cleared = P.clear_vegetation_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(cleared, 0)
        self.assertEqual(int(chunk.blocks[5, 61, 5]), 900)

    def test_terrain_verification_accepts_a_matching_world(self):
        painter = _VerifyPainter()
        n, dense = self._vegetation_clear_fixture()
        chunk = _Chunk()
        _VerifyPainter.fill_stone_to(chunk, 60)

        check = P.verify_terrain_level(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertGreater(check["checked"], 0)
        self.assertEqual(check["missing_surface"], 0)
        self.assertEqual(check["stone_above_surface"], 0)
        self.assertEqual(check["median_offset"], 0)

    def test_terrain_verification_detects_a_world_built_too_low(self):
        """เคสจริง: โลกถูก build ตอน Y_TERRAIN_MIN ยังเป็น -60 (ต่ำกว่า 20)"""
        painter = _VerifyPainter()
        n, dense = self._vegetation_clear_fixture()
        chunk = _Chunk()
        _VerifyPainter.fill_stone_to(chunk, 40)

        check = P.verify_terrain_level(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(check["missing_surface"], check["checked"])
        self.assertEqual(check["median_offset"], -20)

    def test_terrain_verification_detects_a_world_built_too_high(self):
        painter = _VerifyPainter()
        n, dense = self._vegetation_clear_fixture()
        chunk = _Chunk()
        _VerifyPainter.fill_stone_to(chunk, 90)

        check = P.verify_terrain_level(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(check["missing_surface"], 0)
        self.assertEqual(check["stone_above_surface"], check["checked"])
        self.assertEqual(check["median_offset"], 30)

    def test_terrain_verification_ignores_water_columns(self):
        painter = _VerifyPainter()
        n, dense = self._vegetation_clear_fixture(water_column=True)
        chunk = _Chunk()

        check = P.verify_terrain_level(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(check["checked"], 0)

    def test_vegetation_clear_reaches_the_tallest_tree(self):
        painter = _Painter()
        n, dense = self._vegetation_clear_fixture()
        chunk = _Chunk()
        top = 60 + P.VEGETATION_HEADROOM
        chunk.blocks[1, top, 1] = 901

        cleared = P.clear_vegetation_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )

        self.assertEqual(cleared, 1)
        self.assertEqual(int(chunk.blocks[1, top, 1]), 0)


if __name__ == "__main__":
    unittest.main()
