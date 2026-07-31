"""slab ไล่ระดับครึ่งบล็อก — ใช้เศษความสูงที่ terrain_shape.py ปัดทิ้ง"""

import unittest

import numpy as np

import paint_surface as P
import surface as S


class SlabPaletteTests(unittest.TestCase):
    def test_only_rock_blocks_have_slabs(self):
        """วานิลลาไม่มี slab ของดิน/หญ้า/มอส/กรวด/ทราย"""
        for key in ("grass", "dirt", "moss", "podzol", "gravel", "mud",
                    "coarse", "rooted", "packed_mud", "mushroom"):
            self.assertFalse(S.HAS_SLAB[S.IDX[key]], f"{key} ไม่ควรมี slab")

    def test_rock_blocks_are_covered(self):
        for key in ("stone", "cobble", "andesite", "diorite", "tuff"):
            self.assertTrue(S.HAS_SLAB[S.IDX[key]], f"{key} ควรมี slab")

    def test_slab_names_end_with_slab(self):
        for key, name in S.SLAB_OF.items():
            self.assertIn(key, S.IDX)
            self.assertTrue(name.endswith("_slab"), name)

    def test_has_slab_matches_the_mapping(self):
        self.assertEqual(int(S.HAS_SLAB.sum()), len(set(S.SLAB_OF)))


class SlabStateTests(unittest.TestCase):
    @staticmethod
    def _fields(sub, key="stone", water=False, ice=False):
        shape = np.shape(sub)
        cls = np.full(shape, S.IDX[key], dtype=np.uint8)
        return (
            np.asarray(sub, dtype=np.float32),
            cls,
            np.full(shape, water, dtype=bool),
            np.full(shape, ice, dtype=bool),
        )

    def test_high_fraction_adds_a_slab_on_top(self):
        state = P.slab_states(*self._fields([0.4]))
        self.assertEqual(int(state[0]), 1)

    def test_low_fraction_turns_the_top_block_into_a_slab(self):
        state = P.slab_states(*self._fields([-0.4]))
        self.assertEqual(int(state[0]), -1)

    def test_small_fraction_keeps_a_full_block(self):
        for value in (-0.2, 0.0, 0.2):
            state = P.slab_states(*self._fields([value]))
            self.assertEqual(int(state[0]), 0, f"sub={value}")

    def test_soil_never_gets_a_slab(self):
        """แม้เศษจะมาก ดินก็ไม่มี slab ให้ใช้"""
        state = P.slab_states(*self._fields([0.45], key="grass"))
        self.assertEqual(int(state[0]), 0)

    def test_snow_columns_never_get_a_slab(self):
        """slab กินครึ่งช่อง หิมะในช่องถัดไปจึงลอย 0.5 บล็อกเสมอ

        วัดจากโลกจริงก่อนแก้: 23 จาก 23 คอลัมน์ที่มีทั้งสองอย่าง หิมะลอยทุกอัน
        """
        shape = (4,)
        sub = np.full(shape, 0.45, np.float32)
        cls = np.full(shape, S.IDX["stone"], np.uint8)
        dry = np.zeros(shape, bool)
        snow = np.array([0, 1, 4, 8], np.uint8)
        state = P.slab_states(sub, cls, dry, dry, snow=snow)
        self.assertEqual(int(state[0]), 1)
        self.assertTrue((state[1:] == 0).all())

    def test_snow_argument_is_optional(self):
        """ตัวเรียกเก่าที่ไม่ส่ง snow ต้องยังทำงานได้"""
        shape = (2,)
        state = P.slab_states(
            np.full(shape, 0.45, np.float32),
            np.full(shape, S.IDX["stone"], np.uint8),
            np.zeros(shape, bool), np.zeros(shape, bool),
        )
        self.assertTrue((state == 1).all())

    def test_water_and_ice_are_excluded(self):
        wet = P.slab_states(*self._fields([0.45], water=True))
        icy = P.slab_states(*self._fields([0.45], ice=True))
        self.assertEqual(int(wet[0]), 0)
        self.assertEqual(int(icy[0]), 0)

    def test_state_is_symmetric_around_zero(self):
        sub = np.linspace(-0.5, 0.5, 41)
        state = P.slab_states(*self._fields(sub))
        np.testing.assert_array_equal(state, -state[::-1])

    def test_slab_choice_follows_the_blended_surface_material(self):
        cls = np.full((5, 5), S.IDX["diorite"], dtype=np.uint8)
        cls[2, 2] = S.IDX["calcite"]
        fields = np.zeros(cls.shape, dtype=np.float32)
        blended = P.contextual_surface_blend(
            cls, fields, np.full(cls.shape, 50.0, dtype=np.float32),
            terrain_sub=np.full(cls.shape, 0.45, dtype=np.float32),
        )

        state = P.slab_states(
            np.full(cls.shape, 0.45, dtype=np.float32),
            blended, np.zeros(cls.shape, bool), np.zeros(cls.shape, bool),
        )

        self.assertEqual(int(blended[2, 2]), S.IDX["diorite"])
        self.assertEqual(int(state[2, 2]), 1)

    def test_transition_replaces_pale_moss_with_slab_compatible_coating(self):
        cls = np.full((64, 64), S.IDX["grass"], dtype=np.uint8)
        cls[:, 32:] = S.IDX["calcite"]
        damp = np.full(cls.shape, 0.75, dtype=np.float32)
        slope = np.zeros(cls.shape, dtype=np.float32)
        without_sub = P.contextual_surface_blend(cls, damp, slope)
        with_sub = P.contextual_surface_blend(
            cls, damp, slope,
            terrain_sub=np.full(cls.shape, 0.45, dtype=np.float32),
        )

        pale_transition = without_sub == S.IDX["pale_moss"]
        gravel_transition = without_sub == S.IDX["gravel"]
        self.assertTrue(pale_transition.any())
        self.assertTrue(gravel_transition.any())
        self.assertTrue(
            (with_sub[pale_transition] == S.IDX["mossy_cob"]).all()
        )
        # Loose gravel intentionally remains a full block with no fake slab.
        self.assertTrue(
            (with_sub[gravel_transition] == S.IDX["gravel"]).all()
        )


class SlabPaintTests(unittest.TestCase):
    """ตรวจว่าเขียนลง volume จริงถูกตำแหน่ง"""

    def _dense(self, sub, key="stone"):
        from tests.test_paint_surface import _Painter

        painter = _Painter()
        painter.slab_ids = np.zeros(len(S.KEYS), dtype=np.uint32)
        painter.slab_ids[S.IDX[key]] = 777
        n = 16
        shape = (n, n)
        y = np.full(shape, 60, dtype=np.int32)
        cls = np.full(shape, S.IDX[key], dtype=np.uint8)
        water = np.zeros(shape, dtype=bool)
        ice = np.zeros(shape, dtype=bool)
        slab = P.slab_states(np.full(shape, sub, np.float32), cls, water, ice)
        dense = {
            "y": y,
            "slab": slab,
            "slab_id": np.take(painter.slab_ids, cls),
            "object_y": y + (slab > 0),
            "surface_id": np.full(shape, 42, dtype=np.uint32),
            "subsoil_mask": np.zeros(shape, dtype=bool),
            "subsoil_ids": [],
            "water": water,
            "depth": np.zeros(shape, dtype=np.int32),
            "bed_y": y,
            "ice": ice,
            "ice_top": np.zeros(shape, dtype=np.uint32),
            "snow": np.zeros(shape, dtype=np.uint8),
        }
        return painter, dense, n

    def test_positive_state_puts_a_slab_above_the_surface(self):
        from tests.test_paint_surface import _Chunk

        painter, dense, n = self._dense(0.4)
        chunk = _Chunk()
        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )
        self.assertEqual(int(chunk.blocks[4, 60, 4]), 42)
        self.assertEqual(int(chunk.blocks[4, 61, 4]), 777)

    def test_negative_state_replaces_the_surface_with_a_slab(self):
        from tests.test_paint_surface import _Chunk

        painter, dense, n = self._dense(-0.4)
        chunk = _Chunk()
        P.paint_dense_chunks(
            painter, lambda _cx, _cz: chunk, dense, 0, n, 0, n
        )
        self.assertEqual(int(chunk.blocks[4, 60, 4]), 777)
        self.assertEqual(int(chunk.blocks[4, 61, 4]), 0)

    def test_objects_stand_on_top_of_a_raised_slab(self):
        _painter, dense, _n = self._dense(0.4)
        self.assertTrue((dense["object_y"] == dense["y"] + 1).all())

    def test_objects_keep_the_surface_level_for_a_sunken_slab(self):
        _painter, dense, _n = self._dense(-0.4)
        np.testing.assert_array_equal(dense["object_y"], dense["y"])


if __name__ == "__main__":
    unittest.main()
