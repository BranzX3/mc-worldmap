import tempfile
from pathlib import Path
import unittest
from unittest import mock

import baseline_snapshot as B


class BaselineSnapshotTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        for name in B.REQUIRED_INPUTS:
            (root / name).write_bytes(b"input")
        (root / "untracked.py").write_text("value = 1", encoding="utf-8")
        (root / ".claude").mkdir()
        (root / ".claude" / "settings.json").write_text("private", encoding="utf-8")
        return root

    def test_capture_keeps_untracked_code_and_never_overwrites_a_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            with mock.patch.object(B, "git_output", return_value=b"fixture"):
                target = B.capture(root, "test")
                self.assertEqual(B.verify(root, "test", current=True), [])
                self.assertTrue((target / "workspace" / "untracked.py").is_file())
                self.assertFalse((target / "workspace" / ".claude").exists())
                with self.assertRaises(FileExistsError):
                    B.capture(root, "test")

    def test_content_changes_and_new_code_are_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            with mock.patch.object(B, "git_output", return_value=b"fixture"):
                target = B.capture(root, "test")
            (root / "terrain_y.npy").write_bytes(b"other")
            (root / "new.py").write_text("", encoding="utf-8")
            problems = B.verify(root, "test", current=True)
            self.assertIn("current workspace changed: terrain_y.npy", problems)
            self.assertIn("current file added/removed: new.py", problems)
            (target / "workspace" / "untracked.py").write_text("corrupt", encoding="utf-8")
            self.assertIn("snapshot changed: untracked.py", B.verify(root, "test"))

    def test_package_sources_are_captured_recursively_and_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            package = root / "water_v2"
            nested = package / "routing"
            nested.mkdir(parents=True)
            cache = package / "__pycache__"
            cache.mkdir()
            sources = {
                "__init__.py": "",
                "engine.py": "version = 1",
                "routing/__init__.py": "",
                "routing/network.py": "version = 1",
            }
            for name, content in sources.items():
                (package / name).write_text(content, encoding="utf-8")
            (cache / "engine.cpython-311.pyc").write_bytes(b"cache")
            (package / "trial.npz").write_bytes(b"generated")
            with mock.patch.object(B, "git_output", return_value=b"fixture"):
                target = B.capture(root, "test")
            captured = target / "workspace" / "water_v2"
            for name, content in sources.items():
                self.assertEqual((captured / name).read_text(encoding="utf-8"), content)
                self.assertEqual(B.inventory(root)["water_v2/" + name], "code")
            self.assertFalse((captured / "__pycache__").exists())
            self.assertFalse((captured / "trial.npz").exists())
            self.assertEqual(B.verify(root, "test", current=True), [])

            (nested / "network.py").write_text("version = 2", encoding="utf-8")
            (package / "engine.py").unlink()
            (nested / "outlets.py").write_text("version = 1", encoding="utf-8")
            problems = B.verify(root, "test", current=True)
            self.assertIn("current workspace changed: water_v2/routing/network.py", problems)
            self.assertIn("current file added/removed: water_v2/engine.py", problems)
            self.assertIn("current file added/removed: water_v2/routing/outlets.py", problems)
            # Changes to live package code cannot invalidate the frozen copy.
            self.assertEqual(B.verify(root, "test"), [])
            (captured / "routing" / "network.py").write_text("corrupt", encoding="utf-8")
            self.assertIn("snapshot changed: water_v2/routing/network.py", B.verify(root, "test"))

    def test_older_snapshot_stays_frozen_after_package_is_introduced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            with mock.patch.object(B, "git_output", return_value=b"fixture"):
                B.capture(root, "before_package")
            package = root / "water_v2"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "engine.py").write_text("version = 1", encoding="utf-8")
            self.assertEqual(B.verify(root, "before_package"), [])
            problems = B.verify(root, "before_package", current=True)
            self.assertIn("current file added/removed: water_v2/__init__.py", problems)
            self.assertIn("current file added/removed: water_v2/engine.py", problems)

    def test_tag_cannot_escape_archive(self):
        with self.assertRaises(ValueError):
            B.snapshot_path(Path("."), "../outside")


if __name__ == "__main__":
    unittest.main()
