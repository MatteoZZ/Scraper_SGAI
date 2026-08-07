# scraper_eurlex — tema fiscalità

Fonte: **CELLAR SPARQL** EuroVoc `taxation` (`http://eurovoc.europa.eu/1439`).  
PDF IT/EN unici tipicamente ~**6k** (non un tetto arbitrario): molti works EuroVoc non hanno PDF in ITA/ENG.  
Download: preferisce `pdfx`/`pdfa` (il tipo `pdf` a volte è JPEG); fallback content-negotiation sulla work URI.

```powershell
python -m scripts.scraper_eurlex dry-run
python -m scripts.scraper_eurlex run

# 2 worker paralleli (stesso downloads_out, checkpoint .w0of2 / .w1of2)
# Terminale A:
python -m scripts.scraper_eurlex run --workers 2 --worker-id 0
# Terminale B:
python -m scripts.scraper_eurlex run --workers 2 --worker-id 1
```

`--caselaw-only` limita al CELEX settore 6 (giurisprudenza).  
Nessun upload SGAI. Output: `scripts/scraper_eurlex/downloads_out`

Resume: checkpoint + cursore catalogo (dopo DNS/500 SPARQL non riparte da zero).  
Multi-worker: **non** mischiare un `run` singolo con i worker `w0of2` — o tutti single, o tutti con lo stesso `--workers N`.
