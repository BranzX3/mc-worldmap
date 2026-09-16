import unittest

import tree_schematics as TS


class TreeRotationTests(unittest.TestCase):
    def test_rotation_updates_coordinates_and_directional_properties(self):
        props = {"axis": "x", "facing": "north", "rotation": "15"}
        blocks = [(1, 2, -3, "minecraft:spruce_log", props)]

        rotated = TS.rotate(blocks, 1)

        self.assertEqual(rotated[0][:3], (3, 2, 1))
        self.assertEqual(rotated[0][4], {
            "axis": "z", "facing": "east", "rotation": "3",
        })
        self.assertEqual(props, {"axis": "x", "facing": "north", "rotation": "15"})

    def test_four_turns_preserves_properties_and_zero_returns_original(self):
        block = (2, 0, 5, "minecraft:oak_log", {"axis": "z"})
        blocks = [block]
        self.assertEqual(TS.rotate(blocks, 4), blocks)
        self.assertIs(TS.rotate(blocks, 0), blocks)


if __name__ == "__main__":
    unittest.main()
