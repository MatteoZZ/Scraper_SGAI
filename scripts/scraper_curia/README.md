# scraper_curia — materie fiscali (directory 4.10 + B-10)

Giurisprudenza UE fiscale via **CELLAR**:
- directory post-2010 **`4.10`** Tax provisions  
- directory pre-2010 **`B-10`**  
- PDF **e** PDF/A (`pdfa1a`/`pdfa2a`) — IT poi EN  

~**700+** PDF IT (non i soli 5 del filtro EuroVoc+`pdf` stretto).

```powershell
python -m scripts.scraper_curia dry-run
python -m scripts.scraper_curia run
```

Se avevi già scaricato i 5 vecchi, riparte dal checkpoint e continua con gli altri.

Output: `scripts/scraper_curia/downloads_out`
