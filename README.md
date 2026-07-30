# Scraper_SGAI

Una cartella per portale sotto `scripts/`, con tutto il necessario per scaricare i documenti.
**Niente sentenze già scaricate** in questo repo (le tieni in locale e le carichi altrove).

Documentazione: [SCRAPERS.md](SCRAPERS.md)

## Struttura

```
scripts/
  scraper_common/       # utilità condivise (obbligatoria)
  scraper_adm/          # ADM circolari dogane
  scraper_italgiure/    # Italgiure Cassazione Civile V + SU
  scraper_eurlex/       # EUR-Lex fiscalità
  scraper_curia/        # Curia materie fiscali
  scraper_massimario/   # Massimario DEF
  scraper_ebti/         # EBTI (ZIP CSV)
  scraper_mef/          # MEF (modulo locale / CDP)
  SOURCES.md
  run_scrapers_parallel.ps1
```

## Setup

```powershell
cd path\to\Scraper_SGAI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-scrapers.txt
playwright install chromium   # solo Massimario / MEF
```

## Esempio

```powershell
python -m scripts.scraper_adm run --all-years
python -m scripts.scraper_italgiure run
python -m scripts.scraper_eurlex run
```

Output locale (non versionato): `scripts/scraper_<nome>/downloads_out/`
