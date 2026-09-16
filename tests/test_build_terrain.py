import unittest

import build_terrain as B


class BuildTerrainTests(unittest.TestCase):
    def test_surface_shelters_are_opt_in_until_world_readback(self):
        self.assertFalse(B.shelters_enabled(["build_terrain.py"]))
        self.assertTrue(B.shelters_enabled(["build_terrain.py", "--shelters"]))
        self.assertFalse(B.shelters_enabled(["build_terrain.py", "--no-shelters"]))

    def test_build_fingerprint_changes_with_shelter_mode(self):
        enabled = B.build_fingerprint(shelters=True)
        disabled = B.build_fingerprint(shelters=False)
        self.assertNotEqual(enabled, disabled)

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
