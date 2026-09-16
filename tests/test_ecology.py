import unittest

import numpy as np

import ecology as E
import surface as S
import vegetation as V


class StandFieldTests(unittest.TestCase):
    def test_stand_field_is_world_coordinate_invariant(self):
        """ค่าที่พิกัดโลกเดียวกันต้องเท่ากัน ไม่ว่าจะขอเป็นกรอบไหน"""
        full = E.stand_field((64, 64), 4.0, x0=1000, z0=2000)
        part = E.stand_field((16, 16), 4.0, x0=1016, z0=2016)
        np.testing.assert_allclose(full[16:32, 16:32], part, atol=1e-6)

    def test_stand_field_stays_in_unit_range(self):
        field = E.stand_field((48, 48), 4.0, x0=0, z0=0)
        self.assertGreaterEqual(float(field.min()), 0.0)
        self.assertLessEqual(float(field.max()), 1.0)

    def test_stands_are_patches_not_per_block_noise(self):
        """หมู่ไม้ต้องเป็นผืน — เพื่อนบ้านติดกันต้องแทบไม่ต่างกัน"""
        field = E.stand_field((128, 128), 4.0, x0=0, z0=0)
        step = np.abs(np.diff(field, axis=0))
        self.assertLess(float(step.mean()), 0.02)

    def test_age_field_is_world_coordinate_invariant_and_bounded(self):
        full = E.stand_age_field((64, 64), 4.0, x0=1000, z0=2000)
        part = E.stand_age_field((16, 16), 4.0, x0=1016, z0=2016)

        np.testing.assert_allclose(full[16:32, 16:32], part, atol=1e-6)
        self.assertGreaterEqual(float(full.min()), 0.0)
        self.assertLessEqual(float(full.max()), 1.0)

    def test_old_stands_shift_structure_from_understory_to_emergents(self):
        self.assertGreater(
            E.layer_age_probability("emergent", 1.0),
            E.layer_age_probability("emergent", 0.0),
        )
        self.assertLess(
            E.layer_age_probability("under", 1.0),
            E.layer_age_probability("under", 0.0),
        )

    def test_forest_edge_keeps_understory_but_suppresses_tall_layers(self):
        edge = 0.35
        self.assertLess(
            E.layer_age_probability("emergent", 0.8, edge),
            E.layer_age_probability("emergent", 0.8, 1.0),
        )
        self.assertLess(
            E.layer_age_probability("canopy", 0.8, edge),
            E.layer_age_probability("canopy", 0.8, 1.0),
        )
        self.assertGreaterEqual(
            E.layer_age_probability("under", 0.8, edge),
            E.layer_age_probability("under", 0.8, 1.0),
        )

    def test_deadwood_replaces_bushes_as_stands_age(self):
        young = E.deadwood_thresholds(0.0)
        old = E.deadwood_thresholds(1.0)

        self.assertGreater(old[0], young[0])
        self.assertGreater(old[1] - old[0], young[1] - young[0])
        self.assertAlmostEqual(old[2] - old[1], young[2] - young[1])
        self.assertGreater(young[3] - young[2], old[3] - old[2])
        self.assertTrue(all(a <= b for a, b in zip(old, old[1:])))


class SpeciesRuleTests(unittest.TestCase):
    def test_broadleaf_below_montane_and_conifer_above(self):
        low = E.tree_species(600.0, 0.5, 0.4, 0.5)
        high = E.tree_species(1200.0, 0.5, 0.4, 0.5)
        self.assertEqual(E.SPECIES[int(low)], "oak")
        self.assertEqual(E.SPECIES[int(high)], "spruce")

    def test_krummholz_takes_over_near_the_treeline(self):
        just_below = E.tree_species(S.TREELINE - 200, 0.5, 0.4, 0.5)
        near_line = E.tree_species(S.TREELINE - 60, 0.5, 0.4, 0.5)
        self.assertEqual(E.SPECIES[int(just_below)], "spruce")
        self.assertEqual(E.SPECIES[int(near_line)], "krummholz")

    def test_stand_value_selects_a_birch_patch(self):
        oak_stand = E.tree_species(600.0, 0.60, 0.4, 0.5)
        birch_stand = E.tree_species(600.0, 0.20, 0.4, 0.5)
        self.assertEqual(E.SPECIES[int(oak_stand)], "oak")
        self.assertEqual(E.SPECIES[int(birch_stand)], "birch")

    def test_azalea_needs_both_a_stand_and_moisture(self):
        dry = E.tree_species(600.0, 0.90, 0.30, 0.5)
        damp = E.tree_species(600.0, 0.90, 0.70, 0.5)
        self.assertNotEqual(E.SPECIES[int(dry)], "azalea")
        self.assertEqual(E.SPECIES[int(damp)], "azalea")

    def test_species_is_array_capable(self):
        elev = np.array([600.0, 1200.0, S.TREELINE - 10], dtype=np.float32)
        got = E.tree_species(elev, 0.5, 0.4, 0.5)
        self.assertEqual(got.shape, (3,))
        self.assertEqual(
            [E.SPECIES[int(v)] for v in got], ["oak", "spruce", "krummholz"]
        )

    def test_leaf_index_maps_into_surface_palette(self):
        for i, name in enumerate(E.SPECIES):
            idx = int(E.species_leaf_index(i))
            self.assertEqual(idx, S.IDX[E.LEAF_KEY[name]])
            self.assertLess(idx, len(S.KEYS))


class PreviewPaintAgreementTests(unittest.TestCase):
    """พรีวิวกับ paint ต้องตัดสินชนิดไม้ตรงกัน — เหตุผลทั้งหมดของ ecology.py

    เดิม surface.classify() มีกฎ leaf ของตัวเอง ส่วน paint เลือกจากความสูง
    + RNG ต่อต้น ทำให้พรีวิวแสดงองค์ประกอบป่าคนละแบบกับโลกที่เขียนจริง
    """

    def test_canopy_species_matches_the_per_tree_call(self):
        shape = (32, 32)
        elev = np.linspace(500, 1800, shape[0] * shape[1]).reshape(shape)
        elev = elev.astype(np.float32)
        damp = np.full(shape, 0.5, dtype=np.float32)

        preview = E.canopy_species(elev, 4.0, damp, x0=800, z0=1600)
        stand = E.stand_field(shape, 4.0, x0=800, z0=1600)

        for ix in range(0, shape[0], 7):
            for iz in range(0, shape[1], 5):
                per_tree = E.tree_species(
                    float(elev[ix, iz]), float(stand[ix, iz]),
                    float(damp[ix, iz]), 0.5,
                )
                self.assertEqual(
                    int(per_tree), int(preview[ix, iz]),
                    f"ไม่ตรงกันที่ ({ix}, {iz}) elev={elev[ix, iz]:.0f}",
                )

    def test_classify_no_longer_returns_its_own_leaf_rule(self):
        """กันไม่ให้ใครเผลอเพิ่มกฎชนิดไม้กลับเข้า classify()"""
        elev = np.full((16, 16), 700.0, dtype=np.float32)
        lc = np.full((16, 16), S.LC["forest"], dtype=np.uint8)
        result = S.classify(elev, lc, 4.0, block_m=4.0)
        self.assertEqual(len(result), 4)

    def test_meadow_zone_uses_continuous_humidity_and_slope(self):
        self.assertEqual(V.meadow_zone(0.80, 8.0, 700.0), "meadow_wet")
        self.assertEqual(V.meadow_zone(0.20, 8.0, 700.0), "pasture")
        self.assertEqual(V.meadow_zone(0.20, 30.0, 700.0), "meadow")

    def test_meadow_disturbance_requires_accessible_suitable_ground(self):
        suitable = V.meadow_disturbance(0.20, 8.0, 700.0, 0.8)
        wet = V.meadow_disturbance(0.80, 8.0, 700.0, 0.8)
        steep = V.meadow_disturbance(0.20, 35.0, 700.0, 0.8)
        alpine = V.meadow_disturbance(0.20, 8.0, 2600.0, 0.8)

        self.assertGreater(suitable, 0.42)
        self.assertLess(wet, 0.42)
        self.assertLess(steep, 0.42)
        self.assertLess(alpine, 0.42)

    def test_low_disturbance_keeps_dry_flat_meadow_natural(self):
        self.assertEqual(
            V.meadow_zone(0.20, 8.0, 700.0, disturbance=0.1),
            "meadow",
        )

    def test_canopy_floor_links_material_to_tree_and_moisture(self):
        self.assertEqual(
            V.canopy_floor_block(1.5, 4, 0.3, True, 0.4), "podzol"
        )
        self.assertEqual(
            V.canopy_floor_block(1.5, 4, 0.8, True, 0.1), "moss"
        )
        self.assertEqual(
            V.canopy_floor_block(0.0, 4, 0.3, False, 0.02), "rooted"
        )
        self.assertIsNone(
            V.canopy_floor_block(4.0, 4, 0.3, True, 0.9)
        )

    def test_minority_admixture_only_comes_from_the_per_tree_roll(self):
        """roll เปลี่ยนได้แค่ชนิดรอง ชนิดเด่นของผืนต้องมาจาก stand"""
        dominant = [
            E.SPECIES[int(E.tree_species(600.0, 0.60, 0.4, r))]
            for r in np.linspace(0.2, 0.9, 12)
        ]
        self.assertEqual(set(dominant), {"oak"})


class TrunkEpiphyteTests(unittest.TestCase):
    def test_dry_stands_have_no_trunk_epiphytes(self):
        got = V.trunk_epiphytes(
            [(0, y, 0) for y in range(1, 9)],
            damp=0.4, age=1.0, rng=np.random.default_rng(3),
        )
        self.assertEqual(got, [])

    def test_damp_old_stand_produces_valid_attached_faces(self):
        trunks = [(0, y, 0) for y in range(1, 9)]
        got = V.trunk_epiphytes(
            trunks, damp=1.0, age=1.0, rng=np.random.default_rng(3)
        )

        self.assertTrue(got)
        self.assertEqual(
            got,
            V.trunk_epiphytes(
                trunks, damp=1.0, age=1.0, rng=np.random.default_rng(3)
            ),
        )
        self.assertEqual(len({item[:3] for item in got}), len(got))
        for _x, _y, _z, name, props in got:
            self.assertIn(name, {"vine", "glow_lichen"})
            attached = sum(
                props[face] == "true"
                for face in ("north", "south", "east", "west")
            )
            self.assertEqual(attached, 1)
            expected_face = {
                (1, 0): "west", (-1, 0): "east",
                (0, 1): "north", (0, -1): "south",
            }[(_x, _z)]
            self.assertEqual(props[expected_face], "true")
            self.assertEqual(props["up"], "false")
            if name == "glow_lichen":
                self.assertEqual(props["down"], "false")
                self.assertEqual(props["waterlogged"], "false")


if __name__ == "__main__":
    unittest.main()
