from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from scraper_adm.names import parse_list_title  # noqa: E402
from scraper_adm.parse import parse_list_html  # noqa: E402


class TestAdmParseNames(unittest.TestCase):
    def test_circolare_title(self):
        meta = parse_list_title(
            "[468276 del 21/07/2026] – Circolare n. 19/2026 – Adempimento spontaneo"
        )
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["protocollo"], "468276")
        self.assertEqual(meta["numero"], "19")
        self.assertEqual(meta["anno"], "2026")
        self.assertEqual(meta["nomeFile"], "ADM_Circolare_19_2026_468276.pdf")

    def test_doc_without_numero(self):
        meta = parse_list_title("[111111 del 01/01/2026] – Nota operativa senza numero")
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["nomeFile"], "ADM_Doc_111111_20260101.pdf")

    def test_fixture_list(self):
        html = (ROOT / "fixtures" / "sample_list.html").read_text(encoding="utf-8")
        items = parse_list_html(html)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].href.startswith("https://www.adm.gov.it/"))
        self.assertIn(".pdf", items[0].href.lower())


if __name__ == "__main__":
    unittest.main()
