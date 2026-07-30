# Fonti — scraper locali

| Fonte | Modulo |
|--------|--------|
| ADM circolari dogane | `scripts.scraper_adm` |
| Italgiure Civile sez.V + SU | `scripts.scraper_italgiure` |
| EUR-Lex fiscalità | `scripts.scraper_eurlex` |
| Curia materie fiscali | `scripts.scraper_curia` |
| Massimario keyword | `scripts.scraper_massimario` |
| EBTI | `scripts.scraper_ebti` |
| MEF | `scripts.scraper_mef` |

Doc: [../SCRAPERS.md](../SCRAPERS.md)

```powershell
python -m scripts.scraper_italgiure run
python -m scripts.scraper_eurlex run
python -m scripts.scraper_curia run
python -m scripts.scraper_adm run --all-years
python -m scripts.scraper_massimario run
python -m scripts.scraper_ebti run
```
