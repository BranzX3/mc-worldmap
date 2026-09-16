from pathlib import Path
import tempfile
import unittest
from unittest import mock

import baseline_snapshot as B
import paint_trial as P


class PaintTrialTests(unittest.TestCase):
    def test_write_box_requires_audited_context_around_all_edges(self):
        self.assertEqual(P.trial_bounds(200, 200, 256, (8, 392, 8, 392)), (72, 328, 72, 328))
        with self.assertRaises(ValueError):
            P.trial_bounds(200, 200, 384, (8, 392, 8, 392))
        with self.assertRaises(ValueError):
            P.trial_bounds(200, 200, 255, (8, 392, 8, 392))

    def test_changed_artifact_is_rejected_even_at_the_same_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "patch.npz"
            path.write_bytes(b"original")
            evidence = {"artifact_sha256": {"patch.npz": B.digest(path)}}
            P.verify_artifact(root, evidence, path)
            path.write_bytes(b"modified")
            with self.assertRaises(ValueError):
                P.verify_artifact(root, evidence, path)

    def test_backup_preserves_bytes_and_never_overwrites_an_existing_copy(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(P, "world_session_locked", return_value=False):
            root = Path(directory)
            world = root / "world"
            world.mkdir()
            (world / "level.dat").write_bytes(b"level")
            (world / "region").mkdir()
            (world / "region/r.0.0.mca").write_bytes(b"chunks")
            copy = root / "backup/save"
            records = P.backup_save(world, copy)
            self.assertEqual(records["level.dat"], B.digest(copy / "level.dat"))
            self.assertEqual((copy / "region/r.0.0.mca").read_bytes(), b"chunks")
            self.assertEqual((world / "region/r.0.0.mca").read_bytes(), b"chunks")
            with self.assertRaises(FileExistsError):
                P.backup_save(world, copy)

    def test_backup_rejects_nested_paths_and_a_locked_world(self):
        with tempfile.TemporaryDirectory() as directory:
            world = Path(directory) / "world"
            with self.assertRaises(ValueError):
                P.backup_save(world, world / "backup")
            with mock.patch.object(P, "world_session_locked", return_value=True):
                with self.assertRaises(ValueError):
                    P.backup_save(world, Path(directory) / "backup")


if __name__ == "__main__":
    unittest.main()
