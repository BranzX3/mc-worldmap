import os
import tempfile
import unittest

import numpy as np

import hydrology_patch_io as H


class HydrologyPatchIoTests(unittest.TestCase):
    def test_overlay_window_uses_only_intersection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "patch.npz")
            fields = {
                "bounds": np.array([10, 13, 20, 22], dtype=np.int32),
                "terrain_y": np.full((2, 3), 7, dtype=np.int16),
                "water_mask": np.ones((2, 3), dtype=bool),
                "standing_water_mask": np.zeros((2, 3), dtype=bool),
                "surface_y": np.full((2, 3), 6, dtype=np.int16),
                "depth": np.ones((2, 3), dtype=np.uint8),
            }
            np.savez_compressed(path, **fields)
            patch = H.load_hydrology_patch(path)

            got = H.overlay_window(
                np.zeros((3, 3), dtype=np.int16),
                patch,
                "terrain_y",
                9, 12, 19, 22,
            )

            np.testing.assert_array_equal(
                got,
                [[0, 0, 0], [0, 7, 7], [0, 7, 7]],
            )
            patch.close()

    def test_product_names_differ_between_the_two_pipelines(self):
        """ชื่อไฟล์ของสองชุดไม่ตรงกัน — ต้องแปลงผ่านตารางเดียวเท่านั้น

        เคยมีคนเขียน mapping นี้ไว้ใน paint_surface ที่เดียว ผลคือ
        report_metrics / render_preview / render_view อ่านชุดเดิมต่อไปเงียบ ๆ
        ทั้งที่โลกถูกเขียนจากชุดใหม่ ตัวเลขและภาพจึงอธิบายคนละโลก
        """
        root = os.path.join("any", "hydrology_global")
        cases = {
            "depth": ("water_depth.npy", "depth.npy"),
            "surface_y": ("water_surface_y.npy", "surface_y.npy"),
            "standing_water_mask": (
                "water_lake_mask.npy", "standing_water_mask.npy"
            ),
            "water_mask": ("water_mask.npy", "water_mask.npy"),
            "terrain_y": ("terrain_y.npy", "terrain_y.npy"),
        }
        for name, (legacy, modern) in cases.items():
            self.assertEqual(
                os.path.basename(H.water_product_path(name, None, "here")),
                legacy, f"{name} ของชุดเดิม",
            )
            self.assertEqual(
                os.path.basename(H.water_product_path(name, root)),
                modern, f"{name} ของชุด hydrology",
            )

    def test_outlet_mask_exists_only_in_the_legacy_pipeline(self):
        """ชุด hydrology ไม่มี outlet เพราะลำธารมี directed profile อยู่แล้ว

        ผู้เรียกต้องได้ None เพื่อบังคับให้รับมืออย่างตั้งใจ ไม่ใช่ได้ path ของ
        ไฟล์ที่ไม่มีวันมีอยู่แล้วไปพังตอนอ่าน
        """
        self.assertIsNotNone(H.water_product_path("outlet_mask", None, "here"))
        self.assertIsNone(H.water_product_path("outlet_mask", "root"))

    def test_resolve_rejects_a_directory_without_a_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                H.resolve_hydrology_root(["prog", "--hydrology-root", tmp])

            with open(os.path.join(tmp, "manifest.json"), "w") as stream:
                stream.write("{}")
            self.assertEqual(
                H.resolve_hydrology_root(["prog", "--hydrology-root", tmp]),
                os.path.abspath(tmp),
            )

        self.assertIsNone(H.resolve_hydrology_root(["prog"]))


if __name__ == "__main__":
    unittest.main()
