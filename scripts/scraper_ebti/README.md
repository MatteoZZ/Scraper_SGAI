# scraper_ebti — Binding Tariff Information (~1M)

Dump ufficiale Commissione (**Download All BTIs**) = **ZIP di CSV annuali**, non PDF.

```powershell
python -m scripts.scraper_ebti run
python -m scripts.scraper_ebti list
python -m scripts.scraper_ebti extract
python -m scripts.scraper_ebti extract --year 2026
```

- Zip: `downloads_out/EBTI_extractFull.zip` (~374 MB)  
- Dentro: `EBTI_2004.csv` … `EBTI_2026.csv`  
- Estratti: `downloads_out/extracted/`  

Nessun upload SGAI.
