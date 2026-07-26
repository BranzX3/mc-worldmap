import os
import tempfile
import unittest

from pipeline_progress import (
    content_fingerprint,
    load_progress,
    load_progress_metadata,
    save_progress,
    save_progress_metadata,
    world_session_locked,
)
from paint_surface import select_pending_tiles


class PipelineProgressTests(unittest.TestCase):
    def test_progress_is_unique_sorted_and_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "progress.txt")
            save_progress(path, ["2,0", "1,0", "2,0", "", "0,0"])

            self.assertEqual(load_progress(path), {"0,0", "1,0", "2,0"})
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(
                    handle.read().splitlines(), ["0,0", "1,0", "2,0"]
                )
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_resume_selects_only_unfinished_tiles(self):
        tiles = [
            (0, 512, 0, 512),
            (0, 512, 512, 1024),
            (512, 1024, 0, 512),
        ]

        pending = select_pending_tiles(tiles, {"0,0"}, max_tiles=1)

        self.assertEqual(pending, [(0, 512, 512, 1024)])

    def test_fingerprint_changes_with_file_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "input.bin")
            with open(path, "wb") as handle:
                handle.write(b"first")
            before = content_fingerprint([path], version="v1")
            with open(path, "wb") as handle:
                handle.write(b"second")
            after = content_fingerprint([path], version="v1")

            self.assertNotEqual(before, after)

    def test_progress_metadata_is_atomic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "progress.meta.json")
            expected = {"schema": 1, "signature": "abc"}

            save_progress_metadata(path, expected)

            self.assertEqual(load_progress_metadata(path), expected)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_unlocked_world_session_is_available(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "session.lock"), "wb").close()

            self.assertFalse(world_session_locked(folder))


if __name__ == "__main__":
    unittest.main()
