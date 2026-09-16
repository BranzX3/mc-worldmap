import unittest

import numpy as np

from atomic_writes import write_air_atomic


class _Chunk:
    def __init__(self):
        self.blocks = np.zeros((16, 96, 16), dtype=np.uint32)


class AtomicAirWriteTests(unittest.TestCase):
    def setUp(self):
        self.chunk = _Chunk()

    def write(self, items, get_chunk=None):
        return write_air_atomic(
            items, 0, 16, 0, 16, 0, 95,
            get_chunk or (lambda _cx, _cz: self.chunk),
            lambda block_id: block_id == 0,
        )

    def test_writes_complete_object_after_preflight(self):
        wrote = self.write(((2, 60, 3, 101), (2, 61, 3, 102)))

        self.assertEqual(wrote, 2)
        self.assertEqual(int(self.chunk.blocks[2, 60, 3]), 101)
        self.assertEqual(int(self.chunk.blocks[2, 61, 3]), 102)

    def test_collision_leaves_every_owned_cell_unchanged(self):
        self.chunk.blocks[2, 61, 3] = 77

        wrote = self.write(((2, 60, 3, 101), (2, 61, 3, 102)))

        self.assertEqual(wrote, 0)
        self.assertEqual(int(self.chunk.blocks[2, 60, 3]), 0)
        self.assertEqual(int(self.chunk.blocks[2, 61, 3]), 77)

    def test_missing_chunk_leaves_previously_checked_chunk_unchanged(self):
        def get_chunk(cx, _cz):
            return self.chunk if cx == 0 else None

        wrote = write_air_atomic(
            ((2, 60, 3, 101), (18, 60, 3, 102)),
            0, 32, 0, 16, 0, 95, get_chunk,
            lambda block_id: block_id == 0,
        )

        self.assertEqual(wrote, 0)
        self.assertEqual(int(self.chunk.blocks[2, 60, 3]), 0)

    def test_out_of_height_rejects_whole_object(self):
        wrote = self.write(((2, 95, 3, 101), (2, 96, 3, 102)))

        self.assertEqual(wrote, 0)
        self.assertEqual(int(self.chunk.blocks[2, 95, 3]), 0)

    def test_neighbour_region_cells_are_owned_elsewhere(self):
        wrote = self.write(((-1, 60, 3, 101), (0, 60, 3, 102)))

        self.assertEqual(wrote, 1)
        self.assertEqual(int(self.chunk.blocks[0, 60, 3]), 102)


if __name__ == "__main__":
    unittest.main()
