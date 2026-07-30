# scraper_eurlex — tema fiscalità

Fonte: **CELLAR SPARQL** EuroVoc `taxation` (`http://eurovoc.europa.eu/1439`) ≈ **11k** works.  
PDF via `publications.europa.eu/resource/cellar/...` (IT poi EN).

```powershell
python -m scripts.scraper_eurlex dry-run
python -m scripts.scraper_eurlex run
```

`--caselaw-only` limita al CELEX settore 6 (giurisprudenza).  
Nessun upload SGAI. Output: `scripts/scraper_eurlex/downloads_out`
