"""Config da env / CLI — nessun path assoluto del PC sviluppatore."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "downloads_out"
DEFAULT_TMP = ROOT / ".tmp_downloads"
DEFAULT_CHECKPOINT = ROOT / ".checkpoint.json"

SITE_ORIGIN = "https://www.italgiure.giustizia.it"
SOLR_URL = (
    "https://www.italgiure.giustizia.it/sncass/isapi/hc.dll/"
    "sn.solr/sn-collection/select"
)
PDF_ATTACH_BASE = (
    "https://www.italgiure.giustizia.it/xway/application/nif/clean/"
    "hc.dll?verbo=attach&db=snciv&id="
)
REFERER = "https://www.italgiure.giustizia.it/sncass/"

# Criteri collega: Civile + sez. Quinta (5) + Sezioni Unite (U)
DEFAULT_KIND = "snciv"
DEFAULT_SEZIONI = ("5", "U")
SOLR_ROWS = 50
SOLR_FIELDS = (
    "id,filename,szdec,kind,ssz,tipoprov,numcard,numdec,numdep,"
    "datdep,anno,ecli,datdec"
)
SOLR_SORT = "id asc"


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self) -> None:
        self.solr_url = os.environ.get("ITALGIURE_SCRAPER_SOLR_URL", SOLR_URL)
        self.output_dir = Path(
            os.environ.get("ITALGIURE_SCRAPER_OUTPUT", str(DEFAULT_OUTPUT))
        )
        self.tmp_dir = Path(os.environ.get("ITALGIURE_SCRAPER_TMP", str(DEFAULT_TMP)))
        self.checkpoint_path = Path(
            os.environ.get("ITALGIURE_SCRAPER_CHECKPOINT", str(DEFAULT_CHECKPOINT))
        )
        self.min_pdf_bytes = env_int("ITALGIURE_SCRAPER_MIN_PDF_BYTES", 1000)
        self.max_pdf_bytes = env_int("ITALGIURE_SCRAPER_MAX_PDF_BYTES", 80_000_000)
        self.download_delay_min = env_float("ITALGIURE_SCRAPER_DL_DELAY_MIN", 2.0)
        self.download_delay_max = env_float("ITALGIURE_SCRAPER_DL_DELAY_MAX", 5.0)
        self.page_delay_min = env_float("ITALGIURE_SCRAPER_PAGE_DELAY_MIN", 0.5)
        self.page_delay_max = env_float("ITALGIURE_SCRAPER_PAGE_DELAY_MAX", 1.5)
        self.solr_rows = env_int("ITALGIURE_SCRAPER_SOLR_ROWS", SOLR_ROWS)
        # Certificato Italgiure spesso non verificabile su Windows locali
        self.ssl_verify = env_bool("ITALGIURE_SCRAPER_SSL_VERIFY", False)
        self.user_agent = os.environ.get(
            "ITALGIURE_SCRAPER_UA",
            "Mozilla/5.0 (compatible; SGAI-italgiure-scraper/0.1; +local)",
        )
        self.kind = DEFAULT_KIND
        self.sezioni: tuple[str, ...] = DEFAULT_SEZIONI
        self.anno: str | None = None
