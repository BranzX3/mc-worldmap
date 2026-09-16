import json
import os
import tempfile
import unittest
from unittest import mock
import zipfile

import preflight_environment as P


class PreflightTests(unittest.TestCase):
    def test_dependency_status_has_expected_keys(self):
        got = P.dependency_status()
        self.assertEqual(set(got), set(P.REQUIRED_MODULES))
        self.assertTrue(got["numpy"])

    def test_installed_but_broken_native_module_is_unavailable(self):
        with mock.patch.object(P, "REQUIRED_MODULES", {"broken": "broken-pkg"}):
            with mock.patch.object(P.importlib, "import_module",
                                   side_effect=OSError("DLL load failed")):
                result = P.dependency_report()["broken"]
        self.assertFalse(result["available"])
        self.assertIn("DLL load failed", result["error"])

    def test_readiness_requires_every_gate(self):
        ready = {
            "world_exists": True, "world_session_locked": False,
            "missing_packages": [], "python": {"supported": True},
            "client_jar": {"available": True},
            "world_access": {"accessible": True},
        }
        self.assertTrue(P.is_ready(ready))
        for key, value in (
            ("world_exists", False), ("world_session_locked", True),
            ("missing_packages", ["scipy"]), ("python", {"supported": False}),
            ("client_jar", {"available": False}),
            ("world_access", {"accessible": False}),
        ):
            with self.subTest(gate=key):
                self.assertFalse(P.is_ready({**ready, key: value}))

    def test_world_probe_preserves_existing_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            os.mkdir(os.path.join(folder, "region"))
            paths = [os.path.join(folder, "level.dat"),
                     os.path.join(folder, "region", "r.0.0.mca")]
            for path in paths:
                with open(path, "wb") as handle:
                    handle.write(b"unchanged")
            result = P.world_access_status(folder)
            self.assertTrue(result["accessible"])
            self.assertEqual(result["checked_files"], paths)
            for path in paths:
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), b"unchanged")

    def test_world_permission_denial_is_not_reported_as_missing(self):
        with mock.patch.object(P.os, "scandir", side_effect=PermissionError("denied")):
            result = P.world_access_status("save")
        self.assertFalse(result["accessible"])
        self.assertIn("PermissionError", result["error"])

    def test_world_probe_supports_dimension_storage_without_legacy_region(self):
        with tempfile.TemporaryDirectory() as folder:
            region = os.path.join(folder, "dimensions", "minecraft", "overworld", "region")
            os.makedirs(region)
            with open(os.path.join(folder, "level.dat"), "wb") as handle:
                handle.write(b"metadata")
            with open(os.path.join(region, "r.0.0.mca"), "wb") as handle:
                handle.write(b"region")
            with mock.patch.object(P.C, "DIMENSION", "minecraft:overworld"):
                result = P.world_access_status(folder)
            self.assertTrue(result["accessible"])
            self.assertEqual(result["region_directory"], region)
            self.assertEqual(len(result["checked_files"]), 2)

    def test_denied_dimension_path_is_not_hidden_by_legacy_fallback(self):
        with mock.patch.object(P.os, "scandir", side_effect=PermissionError("denied")) as scan:
            result = P.world_access_status("save")
        self.assertFalse(result["accessible"])
        self.assertEqual(scan.call_count, 1)

    def test_directory_without_a_save_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            os.mkdir(os.path.join(folder, "region"))
            self.assertFalse(P.world_access_status(folder)["accessible"])

    def test_jar_must_contain_schema_used_by_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "client.jar")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("version.json", json.dumps({
                    "id": "fixture", "pack_version": {"data_major": 107},
                }))
            with mock.patch("make_world_datapack.find_client_jar", return_value=path):
                self.assertFalse(P.client_jar_status()["available"])
                with zipfile.ZipFile(path, "a") as archive:
                    archive.writestr("data/minecraft/dimension_type/overworld.json",
                                     json.dumps({"min_y": -64, "height": 384,
                                                 "logical_height": 384}))
                self.assertTrue(P.client_jar_status()["available"])

    def test_full_report_keeps_dependency_failure_reason(self):
        deps = {"scipy": {"package": "scipy", "available": False,
                          "version": None, "error": "DLL failure"}}
        with mock.patch.object(P, "dependency_report", return_value=deps) as probe:
            with mock.patch.object(P, "client_jar_status", return_value={"available": True}):
                with mock.patch.object(P, "world_access_status", return_value={"accessible": True}):
                    with mock.patch.object(P, "world_session_locked", return_value=False):
                        with tempfile.TemporaryDirectory() as folder:
                            report = P.preflight(folder, full=True)
        probe.assert_called_once_with(full=True)
        self.assertEqual(report["missing_packages"], ["scipy"])
        self.assertEqual(report["dependency_details"]["scipy"]["error"], "DLL failure")
        self.assertFalse(P.is_ready(report))


if __name__ == "__main__":
    unittest.main()
