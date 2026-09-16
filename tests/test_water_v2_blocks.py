"""The new water sink writes explicit decisions, including non-source states."""

import unittest

import numpy as np

from water_v2.blocks import UNRESOLVED, compare_column_blocks, iter_column_blocks


def column_product(*, standing=True, level=0, depth=3, plant=0, curtain=UNRESOLVED):
    return {
        "terrain_y": np.array([[20]], dtype=np.int16),
        "water_mask": np.array([[True]], dtype=bool),
        "standing_water_mask": np.array([[standing]], dtype=bool),
        "surface_y": np.array([[20 + depth]], dtype=np.int16),
        "depth": np.array([[depth]], dtype=np.uint8),
        "water_level": np.array([[level]], dtype=np.uint8),
        "waterfall_top_y": np.array([[curtain]], dtype=np.int16),
        "bed_material": np.array([[1]], dtype=np.uint8),
        "aquatic_plant": np.array([[plant]], dtype=np.uint8),
        "bank_mask": np.array([[False]], dtype=bool),
        "bank_material": np.array([[4]], dtype=np.uint8),
    }


def emitted(product):
    return {y: (name, props) for y, name, props in iter_column_blocks(product, 0, 0)}


class WaterBlockSinkTests(unittest.TestCase):
    def test_source_pool_preserves_bed_and_source_water(self):
        product = column_product()
        before = {name: field.copy() for name, field in product.items()}
        self.assertEqual(emitted(product), {
            20: ("minecraft:gravel", {}),
            21: ("minecraft:water", {"level": "0"}),
            22: ("minecraft:water", {"level": "0"}),
            23: ("minecraft:water", {"level": "0"}),
        })
        for name in before:
            np.testing.assert_array_equal(product[name], before[name])

    def test_flowing_stream_preserves_explicit_surface_level(self):
        for level in range(1, 8):
            with self.subTest(level=level):
                blocks = emitted(column_product(standing=False, level=level))
                self.assertEqual(blocks[21], ("minecraft:water", {"level": "8"}))
                self.assertEqual(blocks[22], ("minecraft:water", {"level": "8"}))
                self.assertEqual(blocks[23], ("minecraft:water", {"level": str(level)}))

    def test_falling_column_and_curtain_never_become_sources(self):
        blocks = emitted(column_product(standing=False, level=8, curtain=27))
        self.assertEqual(list(blocks), list(range(20, 28)))
        self.assertTrue(all(blocks[y] == ("minecraft:water", {"level": "8"})
                            for y in range(21, 28)))

    def test_requested_plants_leave_surface_water_intact(self):
        short = emitted(column_product(plant=1))
        self.assertEqual(short[21], ("minecraft:seagrass", {}))
        self.assertEqual(short[23], ("minecraft:water", {"level": "0"}))
        tall = emitted(column_product(plant=2))
        self.assertEqual(tall[21], ("minecraft:tall_seagrass", {"half": "lower"}))
        self.assertEqual(tall[22], ("minecraft:tall_seagrass", {"half": "upper"}))
        self.assertEqual(tall[23], ("minecraft:water", {"level": "0"}))
        lily = emitted(column_product(plant=3))
        self.assertEqual(lily[24], ("minecraft:lily_pad", {}))
        self.assertEqual(lily[23], ("minecraft:water", {"level": "0"}))

    def test_no_plants_or_decoration_are_invented(self):
        blocks = emitted(column_product(depth=5))
        self.assertEqual(len(blocks), 6)
        self.assertEqual({name for name, _ in blocks.values()},
                         {"minecraft:gravel", "minecraft:water"})

    def test_bad_plant_requests_fail_before_any_block_is_yielded(self):
        cases = [
            column_product(depth=1, plant=1),
            column_product(depth=2, plant=2),
            column_product(standing=False, level=3, plant=1),
            column_product(standing=False, level=3, plant=3),
            column_product(plant=3, curtain=25),
            column_product(plant=4),
        ]
        for product in cases:
            with self.subTest(plant=int(product["aquatic_plant"][0, 0])):
                with self.assertRaises(ValueError):
                    next(iter_column_blocks(product, 0, 0))

    def test_dry_bank_writes_only_its_explicit_top(self):
        product = column_product()
        product["water_mask"][:] = False
        product["standing_water_mask"][:] = False
        product["depth"][:] = 0
        product["surface_y"][:] = UNRESOLVED
        self.assertEqual(emitted(product), {})
        product["bank_mask"][:] = True
        self.assertEqual(emitted(product), {20: ("minecraft:mud", {})})

    def test_geometry_and_fluid_state_inconsistencies_are_rejected(self):
        cases = []
        for field, value in (("depth", 2), ("water_level", 9),
                             ("water_level", 3), ("waterfall_top_y", 22),
                             ("bed_material", 6), ("bank_mask", True)):
            product = column_product()
            product[field][:] = value
            cases.append(product)
        missing = column_product()
        del missing["standing_water_mask"]
        cases.append(missing)
        wrong_dtype = column_product()
        wrong_dtype["depth"] = np.array([[-1]], dtype=np.int8)
        cases.append(wrong_dtype)
        for product in cases:
            with self.subTest(fields=list(product)):
                with self.assertRaises(ValueError):
                    next(iter_column_blocks(product, 0, 0))

    def test_local_coordinates_follow_z_x_array_order(self):
        product = {
            name: np.tile(field, (2, 3))
            for name, field in column_product().items()
        }
        product["bed_material"][1, 2] = 2
        self.assertEqual(next(iter_column_blocks(product, 2, 1)),
                         (20, "minecraft:sand", {}))
        with self.assertRaises(IndexError):
            next(iter_column_blocks(product, 1, 2))


class WaterBlockReadbackTests(unittest.TestCase):
    def test_readback_accepts_the_planned_flowing_and_falling_states(self):
        expected = list(iter_column_blocks(
            column_product(standing=False, level=4, curtain=25), 0, 0,
        ))
        observed = {y: (name, props.copy()) for y, name, props in expected}
        self.assertEqual(compare_column_blocks(expected, observed), [])

    def test_forcing_planned_flow_to_source_is_a_concrete_error(self):
        expected = list(iter_column_blocks(column_product(standing=False, level=4), 0, 0))
        observed = {y: (name, props.copy()) for y, name, props in expected}
        observed[23] = ("minecraft:water", {"level": "0"})
        self.assertEqual(compare_column_blocks(expected, observed), [{
            "y": 23,
            "reason": "properties",
            "expected": {"name": "minecraft:water", "properties": {"level": "4"}},
            "observed": {"name": "minecraft:water", "properties": {"level": "0"}},
        }])

    def test_missing_wrong_and_unexpected_blocks_are_reported(self):
        expected = [(20, "stone", {}), (21, "water", {"level": "8"})]
        observed = {20: ("gravel", {}), 22: ("water", {"level": "8"})}
        mismatches = compare_column_blocks(expected, observed)
        self.assertEqual([(item["y"], item["reason"]) for item in mismatches],
                         [(20, "block"), (21, "missing"), (22, "unexpected")])

    def test_readback_rejects_ambiguous_duplicate_expectations(self):
        with self.assertRaisesRegex(ValueError, "duplicate Y"):
            compare_column_blocks([(20, "stone", {}), (20, "gravel", {})], {})


if __name__ == "__main__":
    unittest.main()
