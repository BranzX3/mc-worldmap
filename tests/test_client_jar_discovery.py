import os
import tempfile
import unittest
from unittest import mock

import make_world_datapack as D


class ClientJarDiscoveryTests(unittest.TestCase):
    def test_modrinth_loader_version_directory_is_discovered(self):
        with tempfile.TemporaryDirectory() as folder:
            name = "26.2-0.19.3"
            root = os.path.join(folder, "ModrinthApp", "meta", "versions", name)
            os.makedirs(root)
            path = os.path.join(root, name + ".jar")
            with open(path, "wb") as handle:
                handle.truncate(5_000_001)
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                with mock.patch.object(D.C, "CLIENT_JAR", None, create=True):
                    self.assertEqual(D.find_client_jar(), path)

    def test_access_denied_is_distinct_from_game_not_installed(self):
        with mock.patch.object(D.C, "CLIENT_JAR", None, create=True):
            with mock.patch.object(D.os, "listdir", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    D.find_client_jar()

    def test_invalid_override_never_selects_a_different_version(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = os.path.join(folder, "missing.jar")
            with mock.patch.object(D.C, "CLIENT_JAR", missing, create=True):
                with self.assertRaises(FileNotFoundError):
                    D.find_client_jar()


if __name__ == "__main__":
    unittest.main()
