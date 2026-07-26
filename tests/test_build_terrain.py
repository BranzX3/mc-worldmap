import unittest

import build_terrain as B


class BuildTerrainTests(unittest.TestCase):
    def test_aligned_patch_uses_exclusive_chunk_end(self):
        self.assertEqual(
            B.patch_chunk_bounds(4864, 5888, 512, 10000),
            (288, 320, 352, 384),
        )

    def test_unaligned_patch_covers_requested_blocks(self):
        cx0, cx1, cz0, cz1 = B.patch_chunk_bounds(100, 200, 33, 10000)
        self.assertLessEqual(cx0 * 16, 84)
        self.assertGreaterEqual(cx1 * 16, 117)
        self.assertLessEqual(cz0 * 16, 184)
        self.assertGreaterEqual(cz1 * 16, 217)


if __name__ == "__main__":
    unittest.main()
