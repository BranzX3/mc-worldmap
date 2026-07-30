import unittest

import numpy as np

import biomes as B
import config as C
import surface as S


class PaletteTests(unittest.TestCase):
    def test_every_biome_defines_all_three_tints(self):
        """สีหญ้า/ใบไม้/น้ำต้องกำหนดครบ ไม่งั้นตกกลับไปใช้ colormap ของวานิลลา"""
        for key in B.KEYS:
            effects = B.datapack_entry(key)["effects"]
            for field in ("grass_color", "foliage_color", "water_color"):
                self.assertIn(field, effects, f"{key} ขาด {field}")
                self.assertRegex(effects[field], r"^#[0-9a-f]{6}$")

    def test_names_are_namespaced(self):
        for name in B.FULL_NAME:
            self.assertTrue(name.startswith(f"{B.NAMESPACE}:"))

    def test_foliage_is_darker_than_grass(self):
        """ใบไม้ต้องเข้มกว่าหญ้าเหมือนวานิลลา ไม่งั้นเรือนยอดจะดูลอย"""
        for i, key in enumerate(B.KEYS):
            grass = sum(B._rgb(B.BIOMES[i][2]))
            foliage = sum(B._rgb(B.BIOMES[i][3]))
            self.assertLess(foliage, grass, f"{key}: ใบไม้ไม่เข้มกว่าหญ้า")

    def test_grass_desaturates_with_altitude(self):
        """ไล่โทนจากหุบเขาเขียวสด -> เหนือแนวไม้เทาเขียว"""
        def saturation(hex_colour):
            r, g, b = B._rgb(hex_colour)
            return (max(r, g, b) - min(r, g, b)) / max(1, max(r, g, b))

        ladder = ["valley", "montane", "subalpine", "alpine", "peak"]
        values = [saturation(B.BIOMES[B.IDX[k]][2]) for k in ladder]
        for lower, higher in zip(values, values[1:]):
            self.assertLess(higher, lower)

    def test_lake_and_stream_have_different_water(self):
        lake = B.BIOMES[B.IDX["lake"]][4]
        stream = B.BIOMES[B.IDX["stream"]][4]
        self.assertNotEqual(lake, stream)


class SelectionTests(unittest.TestCase):
    @staticmethod
    def _dry(shape):
        return np.zeros(shape, dtype=bool), np.zeros(shape, dtype=np.uint8)

    def test_altitude_ladder(self):
        elev = np.array([[600.0, 1100.0, 1600.0, 1900.0, 2400.0]],
                        dtype=np.float32)
        water, depth = self._dry(elev.shape)
        got = B.biome_index(elev, water, depth)
        self.assertEqual(
            [B.KEYS[int(v)] for v in got[0]],
            ["valley", "montane", "subalpine", "alpine", "peak"],
        )

    def test_deep_water_is_lake_shallow_is_stream(self):
        elev = np.full((1, 2), 600.0, dtype=np.float32)
        water = np.ones((1, 2), dtype=bool)
        depth = np.array([[1, 8]], dtype=np.uint8)
        got = B.biome_index(elev, water, depth)
        self.assertEqual(B.KEYS[int(got[0, 0])], "stream")
        self.assertEqual(B.KEYS[int(got[0, 1])], "lake")

    def test_water_wins_over_altitude(self):
        """ทะเลสาบบนที่สูงต้องยังเป็นทะเลสาบ ไม่ใช่ alpine"""
        elev = np.full((1, 1), 1900.0, dtype=np.float32)
        water = np.ones((1, 1), dtype=bool)
        depth = np.full((1, 1), 6, dtype=np.uint8)
        got = B.biome_index(elev, water, depth)
        self.assertEqual(B.KEYS[int(got[0, 0])], "lake")

    def test_treeline_shift_moves_the_boundary(self):
        """เส้น biome ต้องขยับตามแนวไม้ที่ classify() ขยับ ไม่ใช่ค่าคงที่"""
        elev = np.full((1, 2), S.TREELINE + 50.0, dtype=np.float32)
        water, depth = self._dry(elev.shape)
        shifted = np.array([[S.TREELINE - 100.0, S.TREELINE + 200.0]],
                           dtype=np.float32)
        got = B.biome_index(elev, water, depth, treeline=shifted)
        self.assertEqual(B.KEYS[int(got[0, 0])], "alpine")
        self.assertEqual(B.KEYS[int(got[0, 1])], "subalpine")


class DatapackTests(unittest.TestCase):
    def test_one_file_per_biome(self):
        files = B.datapack_files()
        self.assertEqual(len(files), len(B.KEYS))
        for path in files:
            self.assertTrue(path.startswith(f"data/{B.NAMESPACE}/worldgen/biome/"))

    def test_schema_has_the_fields_the_game_requires(self):
        """อ้างอิงจาก biome json จริงของ 26.2 — ขาดฟิลด์แล้วเกมโหลดไม่ผ่าน"""
        entry = B.datapack_entry("valley")
        for field in ("attributes", "downfall", "effects", "has_precipitation",
                      "spawn_costs", "spawners", "carvers", "features",
                      "temperature"):
            self.assertIn(field, entry)

    def test_no_vanilla_spawners_because_the_mmo_owns_animals(self):
        for key in B.KEYS:
            for category, entries in B.datapack_entry(key)["spawners"].items():
                self.assertEqual(entries, [], f"{key}/{category} ไม่ควรมี spawner")


class _StrictBiomes:
    """เลียนแบบ BoundedPartial3DArray ที่ *ไม่* broadcast

    amulet บังคับว่า array ที่ส่งให้ต้อง shape ตรงกับ slice เป๊ะ การส่ง
    (4, 1, 4) ให้ slice (4, 196, 4) จะ ValueError — ของจริงพังตรงนี้มาแล้ว
    """

    def __init__(self):
        self.written = None

    def convert_to_3d(self):
        pass

    def __setitem__(self, key, value):
        _sx, sy, _sz = key
        depth = sy.stop - sy.start
        value = np.asarray(value)
        if value.shape != (4, depth, 4):
            raise ValueError(
                f"shape ไม่ตรง: slice ต้องการ {(4, depth, 4)} ได้ {value.shape}"
            )
        self.written = value


class _FakeChunk:
    def __init__(self):
        self.biomes = _StrictBiomes()


class BiomeWriterTests(unittest.TestCase):
    def test_writer_sends_a_full_shape_array(self):
        import paint_surface as P

        chunk = _FakeChunk()
        idx = np.zeros((16, 16), dtype=np.uint8)
        ids = np.arange(len(B.KEYS), dtype=np.uint32)
        P.write_chunk_biomes(chunk, idx, 0, 0, ids)
        depth = (C.WORLD_Y_MAX + 1) // 4 - C.WORLD_Y_MIN // 4
        self.assertEqual(chunk.biomes.written.shape, (4, depth, 4))

    def test_each_4x4_cell_uses_its_own_majority(self):
        import paint_surface as P

        chunk = _FakeChunk()
        idx = np.full((16, 16), B.IDX["valley"], dtype=np.uint8)
        idx[8:, :] = B.IDX["lake"]          # ครึ่งล่างเป็นทะเลสาบ
        ids = np.arange(len(B.KEYS), dtype=np.uint32)
        P.write_chunk_biomes(chunk, idx, 0, 0, ids)
        cells = chunk.biomes.written[:, 0, :]
        self.assertEqual(int(cells[0, 0]), B.IDX["valley"])
        self.assertEqual(int(cells[3, 0]), B.IDX["lake"])


class BiomeWriteRangeTests(unittest.TestCase):
    def test_write_range_covers_the_whole_world(self):
        """หน่วยแกน y ของ Biomes3D คือ cell 4 บล็อก"""
        y0 = C.WORLD_Y_MIN // 4
        y1 = (C.WORLD_Y_MAX + 1) // 4
        self.assertEqual(y0 * 4, C.WORLD_Y_MIN)
        self.assertEqual(y1 * 4, C.WORLD_Y_MAX + 1)
        self.assertGreater(y1 * 4, C.Y_TERRAIN_MAX)
        # ช่วง default ของ amulet คือ 0..255 ซึ่งไม่พอ
        self.assertGreater(y1 * 4 - 1, 255)
        self.assertLess(y0 * 4, 0)


if __name__ == "__main__":
    unittest.main()
