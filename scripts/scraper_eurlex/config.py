from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "downloads_out"
DEFAULT_CHECKPOINT = ROOT / ".checkpoint.json"
SPARQL_URL = "https://publications.europa.eu/webapi/rdf/sparql"
# EuroVoc "taxation" / fiscalità
EUROVOC_TAXATION = "http://eurovoc.europa.eu/1439"
LANG_PREF = ("ITA", "ENG")


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else float(raw)


class Config:
    def __init__(self) -> None:
        self.sparql_url = os.environ.get("EURLEX_SCRAPER_SPARQL", SPARQL_URL)
        self.eurovoc = os.environ.get("EURLEX_SCRAPER_EUROVOC", EUROVOC_TAXATION)
        self.output_dir = Path(os.environ.get("EURLEX_SCRAPER_OUTPUT", str(DEFAULT_OUTPUT)))
        self.checkpoint_path = Path(
            os.environ.get("EURLEX_SCRAPER_CHECKPOINT", str(DEFAULT_CHECKPOINT))
        )
        self.min_pdf_bytes = env_int("EURLEX_SCRAPER_MIN_PDF_BYTES", 1000)
        # alcuni CELLAR/taxation sono raccolte enormi (anche >1 GB)
        self.max_pdf_bytes = env_int("EURLEX_SCRAPER_MAX_PDF_BYTES", 2_000_000_000)
        self.download_delay_min = env_float("EURLEX_SCRAPER_DL_DELAY_MIN", 1.0)
        self.download_delay_max = env_float("EURLEX_SCRAPER_DL_DELAY_MAX", 2.5)
        self.page_size = env_int("EURLEX_SCRAPER_PAGE_SIZE", 50)
        self.user_agent = os.environ.get(
            "EURLEX_SCRAPER_UA",
            "Mozilla/5.0 (compatible; SGAI-eurlex-scraper/0.1; +local)",
        )
        self.caselaw_only = False
        self.name_prefix = "EURLEX"
        # eurovoc | curia_directory
        self.mode = "eurovoc"
        # lingue SPARQL in ordine di preferenza (Curia: solo ITA, evita doppio giro ENG inutile)
        self.languages: tuple[str, ...] = LANG_PREF
        # multi-worker: stesso downloads_out, checkpoint .w{i}of{n}.json
        self.workers = 1
        self.worker_id = 0
