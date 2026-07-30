"""Config da env / CLI — nessun path assoluto del PC sviluppatore."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "downloads_out"
DEFAULT_TMP = ROOT / ".tmp_downloads"
DEFAULT_CHECKPOINT = ROOT / ".checkpoint.json"
LIST_URL = "https://www.adm.gov.it/portale/circolari-dogane"
SITE_ORIGIN = "https://www.adm.gov.it"

# Archivio circolari dogane (pagine anno) — da archivio3 + liste correnti
ARCHIVE_LIST_URLS = [
    "https://www.adm.gov.it/portale/circolari-dogane",
    "https://www.adm.gov.it/portale/-/circolari-2",
    "https://www.adm.gov.it/portale/anni-2022-20233",
    "https://www.adm.gov.it/portale/anno-20213",
    "https://www.adm.gov.it/portale/anno-20203",
    "https://www.adm.gov.it/portale/anno-2019",
    "https://www.adm.gov.it/portale/18398",  # 2018
    "https://www.adm.gov.it/portale/anno-2017",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2016",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2015",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2014",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2013",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2012",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2011",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2010",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2009",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2008",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2007",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2006",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2005",
    "https://www.adm.gov.it/portale/dogane/operatore/atti-amministrativi-generali/circolari/circolari-2004",
    "https://www.adm.gov.it/portale/-/circolari-anni-1997-2000",
]


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


class Config:
    def __init__(self) -> None:
        self.list_url = os.environ.get("ADM_SCRAPER_LIST_URL", LIST_URL)
        self.output_dir = Path(os.environ.get("ADM_SCRAPER_OUTPUT", str(DEFAULT_OUTPUT)))
        self.tmp_dir = Path(os.environ.get("ADM_SCRAPER_TMP", str(DEFAULT_TMP)))
        self.checkpoint_path = Path(
            os.environ.get("ADM_SCRAPER_CHECKPOINT", str(DEFAULT_CHECKPOINT))
        )
        self.min_pdf_bytes = env_int("ADM_SCRAPER_MIN_PDF_BYTES", 1000)
        self.max_pdf_bytes = env_int("ADM_SCRAPER_MAX_PDF_BYTES", 80_000_000)
        self.download_delay_min = env_float("ADM_SCRAPER_DL_DELAY_MIN", 2.0)
        self.download_delay_max = env_float("ADM_SCRAPER_DL_DELAY_MAX", 5.0)
        self.user_agent = os.environ.get(
            "ADM_SCRAPER_UA",
            "Mozilla/5.0 (compatible; SGAI-adm-scraper/0.1; +local)",
        )
