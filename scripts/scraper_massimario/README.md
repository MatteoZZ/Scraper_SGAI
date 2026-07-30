# scraper_massimario — DEF / CERDEF (keyword + massimate)

Portale: `def.finanze.it` — ricerca avanzata con checkbox **Ricerca sentenze massimate**.

```powershell
pip install playwright
playwright install chromium

# reset checkpoint keyword (la prima run aveva salvato solo il form vuoto)
Remove-Item scripts\scraper_massimario\.checkpoint.json -ErrorAction SilentlyContinue

python -m scripts.scraper_massimario run --no-resume --max-keywords 1 --max-docs 5 --headed
python -m scripts.scraper_massimario run
```

Output: `scripts/scraper_massimario/downloads_out`  
(PDF se disponibile, altrimenti scheda HTML con la massima)
