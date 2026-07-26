import unittest

import numpy as np

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

    @staticmethod
    def is_air(block_id):
        return block_id == 0

    @staticmethod
    def is_water(block_id):
        return block_id == _Painter.water

    @staticmethod
    def is_soft(block_id):
        return block_id == 0


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
        heightmap = np.zeros((2, 2), dtype=np.uint16)
        landcover = np.full((2, 2), S.LC["water"], dtype=np.uint8)
        depth = np.full((2, 2), 30, dtype=np.uint8)

        result = P.water_depth_preflight(heightmap, landcover, depth, row_batch=1)

        self.assertEqual(result["clipped_columns"], 4)
        self.assertEqual(result["clipped_blocks"], 28)
        self.assertEqual(result["required_fill_bottom"], -71)

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
            painter.stone,
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

        surface, _forest, _leaf, snow, _soil = S.classify(
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


if __name__ == "__main__":
    unittest.main()
