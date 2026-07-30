import json
import unittest
from pathlib import Path

from scraper_italgiure.client import build_solr_query
from scraper_italgiure.names import (
    build_pdf_url,
    meta_from_solr_doc,
    normalize_filename_field,
    tipo_code,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_solr_docs.json"


class TestNames(unittest.TestCase):
    def test_normalize_clean_pdf(self):
        self.assertEqual(
            normalize_filename_field("./20210730/snciv@s50@a2021@n21853@tO.pdf"),
            "./20210730/snciv@s50@a2021@n21853@tO.clean.pdf",
        )

    def test_build_pdf_url(self):
        url = build_pdf_url("./20210730/snciv@s50@a2021@n21853@tO.pdf")
        self.assertIn("verbo=attach", url)
        self.assertIn("db=snciv", url)
        self.assertTrue(url.endswith(".clean.pdf"))

    def test_tipo_code(self):
        self.assertEqual(tipo_code("Ordinanza"), "O")
        self.assertEqual(tipo_code("Sentenza"), "S")
        self.assertEqual(tipo_code("", "snciv2021x123S"), "S")

    def test_meta_quinta(self):
        docs = json.loads(FIXTURE.read_text(encoding="utf-8"))["response"]["docs"]
        meta = meta_from_solr_doc(docs[0])
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["szdec"], "5")
        self.assertEqual(meta["sezione"], "Quinta")
        self.assertEqual(meta["anno"], "2021")
        self.assertEqual(meta["numdec"], "21853")
        self.assertTrue(meta["nomeFile"].startswith("ITALGIURE_Civile_5_2021_21853_O_"))
        self.assertIn("snciv2021521853O", meta["nomeFile"])

    def test_meta_unite(self):
        docs = json.loads(FIXTURE.read_text(encoding="utf-8"))["response"]["docs"]
        meta = meta_from_solr_doc(docs[1])
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["szdec"], "U")
        self.assertEqual(meta["sezione"], "SezioniUnite")
        self.assertTrue(meta["nomeFile"].startswith("ITALGIURE_Civile_U_2021_21960_O_"))

    def test_solr_query_default(self):
        q = build_solr_query()
        self.assertIn('kind:"snciv"', q)
        self.assertIn('szdec:"5"', q)
        self.assertIn('szdec:"U"', q)

    def test_solr_query_anno(self):
        q = build_solr_query(sezioni=("5",), anno="2025")
        self.assertIn('szdec:"5"', q)
        self.assertNotIn('szdec:"U"', q)
        self.assertIn('anno:"2025"', q)


if __name__ == "__main__":
    unittest.main()
