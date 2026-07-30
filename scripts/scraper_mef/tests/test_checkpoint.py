from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from scraper_mef.checkpoint import Checkpoint  # noqa: E402


class TestCheckpoint(unittest.TestCase):
    def test_atomic_resume_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cp.json"
            cp = Checkpoint(path)
            cp.set_position(page=12, row_index=3)
            cp.mark_processed("Sentenza_V70_1205_2026", page=12, row_index=4)
            cp2 = Checkpoint(path)
            self.assertEqual(cp2.data["last_page"], 12)
            self.assertEqual(cp2.data["last_row_index"], 4)
            self.assertIn("Sentenza_V70_1205_2026", cp2.data["processed"])
            self.assertTrue(cp2.is_done("Sentenza_V70_1205_2026"))

    def test_failed_and_blocked_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cp.json"
            cp = Checkpoint(path)
            cp.mark_failed("Sentenza_V70_1_2026", page=1, row_index=2)
            cp.set_status("blocked", block_reason="HTTP 429")
            cp2 = Checkpoint(path)
            self.assertIn("Sentenza_V70_1_2026", cp2.data["failed"])
            self.assertEqual(cp2.data["status"], "blocked")
            self.assertEqual(cp2.data["block_reason"], "HTTP 429")

    def test_sync_page_resets_row_index_on_page_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cp.json"
            cp = Checkpoint(path)
            cp.set_position(page=1, row_index=7)
            cp.sync_page(2)
            self.assertEqual(cp.data["last_page"], 2)
            self.assertEqual(cp.data["last_row_index"], 0)
            # stessa pagina: non azzera
            cp.set_position(row_index=3)
            cp.sync_page(2)
            self.assertEqual(cp.data["last_row_index"], 3)


if __name__ == "__main__":
    unittest.main()
