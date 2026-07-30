from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from scraper_mef.names import row_to_filename  # noqa: E402
from scraper_mef.parse import (  # noqa: E402
    PortalRow,
    metas_match,
    parse_detail_text,
    parse_table_html,
    rows_from_link_dicts,
    validate_row,
)
from scraper_mef.portal_to_filename import normalize_tipo  # noqa: E402


class TestNames(unittest.TestCase):
    def test_lombardia(self):
        meta = row_to_filename("1205", "2026", "CGT 2° Lombardia")
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["nomeFile"], "Sentenza_V70_1205_2026.pdf")

    def test_ordinanza_tipo(self):
        meta = row_to_filename("1205", "2026", "CGT 2° Lombardia", tipo="Ordinanza")
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["tipo"], "Ordinanza")
        self.assertEqual(meta["nomeFile"], "Ordinanza_V70_1205_2026.pdf")
        self.assertEqual(normalize_tipo("ordinanza"), "Ordinanza")

    def test_corte_con_trattino(self):
        emilia = row_to_filename("100", "2026", "CGT 2° Emilia-Romagna")
        self.assertTrue(emilia["ok"], emilia)
        self.assertEqual(emilia["codice"], "V92")
        self.assertEqual(emilia["nomeFile"], "Sentenza_V92_100_2026.pdf")

        trentino = row_to_filename("200", "2026", "CGT 2° Trentino-Alto Adige")
        self.assertTrue(trentino["ok"], trentino)
        self.assertEqual(trentino["codice"], "V75")
        self.assertEqual(trentino["nomeFile"], "Sentenza_V75_200_2026.pdf")

        detail = parse_detail_text(
            "Ordinanza n. 100/2026 CGT 2° Emilia-Romagna",
            page_url="/ricerca/dettaglio/x",
        )
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["codice"], "V92")

    def test_fixture_rows(self):
        html = (ROOT / "fixtures" / "sample_rows.html").read_text(encoding="utf-8")
        rows = parse_table_html(html)
        self.assertGreaterEqual(len(rows), 3)

    def test_incomplete_row(self):
        row = PortalRow(tipo="Sentenza", numero="", anno="2026", corte="CGT 2° Lombardia")
        errs = validate_row(row)
        self.assertTrue(any("numero" in e for e in errs))

    def test_link_dict_association_not_filtered_index(self):
        """Link i-esimo resta legato alla sua tr, anche se una riga tabellare è spazzatura."""
        items = [
            {
                "row_index": 0,
                "href": "/ricerca/dettaglio/aaa",
                "title": "Visualizza provvedimento n. 1/2026 CGT 2° Lombardia",
                "tipo": "Sentenza",
                "numero": "1",
                "anno": "2026",
                "corte": "CGT 2° Lombardia",
                "cells": ["Sentenza", "1", "2026", "CGT 2° Lombardia"],
            },
            {
                "row_index": 1,
                "href": "/ricerca/dettaglio/bbb",
                "title": "Visualizza provvedimento n. 99/2026 CGT 2° Puglia",
                "tipo": "Ordinanza",
                "numero": "99",
                "anno": "2026",
                "corte": "CGT 2° Puglia",
                "cells": ["Ordinanza", "99", "2026", "CGT 2° Puglia"],
            },
        ]
        rows = rows_from_link_dicts(items)
        self.assertEqual(rows[1].href, "/ricerca/dettaglio/bbb")
        self.assertEqual(rows[1].to_meta()["nomeFile"], "Ordinanza_Z31_99_2026.pdf")

    def test_detail_meta_and_mismatch(self):
        detail_html = """
        <html><body>
        <h1>Sentenza n. 1205/2026 CGT 2° Lombardia</h1>
        </body></html>
        """
        detail = parse_detail_text(detail_html, page_url="/ricerca/dettaglio/x")
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["numero"], "1205")
        self.assertEqual(detail["codice"], "V70")

        list_meta = row_to_filename("999", "2026", "CGT 2° Lombardia")
        mismatch = metas_match(list_meta, detail)
        self.assertTrue(any("numero" in e for e in mismatch))

        list_ok = row_to_filename("1205", "2026", "CGT 2° Lombardia")
        self.assertEqual(metas_match(list_ok, detail), [])


if __name__ == "__main__":
    unittest.main()
