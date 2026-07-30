from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from scraper_mef.cache import NameCache, SkipIndex  # noqa: E402
from scraper_mef.download import make_minimal_pdf  # noqa: E402


class TestCache(unittest.TestCase):
    def test_embedded_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cache.txt"
            p.write_text("Sentenza_V10_13747_2021|embedded\n", encoding="utf-8")
            cache = NameCache(p)
            self.assertTrue(cache.should_skip("Sentenza_V10_13747_2021"))
            self.assertFalse(cache.should_skip("Sentenza_V70_1205_2026"))

    def test_three_layers_valid_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "Sentenza_V70_100_2025.pdf").write_bytes(make_minimal_pdf(1200))
            server = Path(tmp) / "server.txt"
            server.write_text("Sentenza_U91_6086_2025\n", encoding="utf-8")
            idx = SkipIndex(
                output_dir=out,
                server_cache_files=[server],
                min_pdf_bytes=1000,
                max_pdf_bytes=80_000_000,
            )
            self.assertEqual(idx.decide("Sentenza_V70_100_2025").action, "skip_local")
            self.assertEqual(idx.decide("Sentenza_U91_6086_2025").action, "skip_server")
            self.assertEqual(idx.decide("Sentenza_V70_1205_2026").action, "would_download")

    def test_corrupt_local_does_not_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "Sentenza_V70_100_2025.pdf").write_bytes(b"")  # vuoto
            idx = SkipIndex(output_dir=out, min_pdf_bytes=1000, max_pdf_bytes=80_000_000)
            d = idx.decide("Sentenza_V70_100_2025")
            self.assertEqual(d.action, "would_download")
            self.assertEqual(d.layer, "A_locale_corrupt")


if __name__ == "__main__":
    unittest.main()
