# Come funzionano gli scraper

Codice preso da `ragflow/scripts` (moduli `scraper_*`).  
Questo repo contiene **solo il codice**; i PDF/HTML/ZIP scaricati restano sul PC in locale.

Esegui i comandi dalla **root** di `Scraper_SGAI`.

```powershell
pip install -r requirements-scrapers.txt
playwright install chromium   # Massimario / MEF
```

## Pattern comune

| Cartella | Portale | Output |
|----------|---------|--------|
| `scripts/scraper_adm` | ADM circolari dogane | PDF |
| `scripts/scraper_italgiure` | Italgiure (Civile sez. V + SU) | PDF |
| `scripts/scraper_eurlex` | EUR-Lex / CELLAR (fiscalità) | PDF |
| `scripts/scraper_curia` | Curia (dir. 4.10 + B-10) | PDF |
| `scripts/scraper_massimario` | Massimario DEF | HTML / PDF |
| `scripts/scraper_ebti` | EBTI Commissione | ZIP di CSV |
| `scripts/scraper_mef` | MEF giurisprudenza tributaria | PDF |
| `scripts/scraper_common` | PDF + checkpoint condivisi | — |

```powershell
python -m scripts.scraper_<nome> dry-run
python -m scripts.scraper_<nome> run
```

Ogni modulo ha `downloads_out/` e `.checkpoint.json` **propri** (resume + parallelo sicuro).  
Non sono su Git (vedi `.gitignore`). **Nessun upload SGAI** da questi moduli.

---

### ADM
Lista HTML → PDF diretti. Per l’archivio: `run --all-years`.

### Italgiure
Solr pubblico → PDF clean. Scope: Civile sezioni 5 e U (~61k). Resume se interrotto / rete instabile.

### EUR-Lex
CELLAR SPARQL (EuroVoc taxation). PDF IT preferito, poi EN.

### Curia
Stesso stack CELLAR; directory fiscali 4.10 e B-10.

### Massimario
Playwright su `def.finanze.it`, keyword + sentenze massimate, schede HTML (± PDF).

### EBTI
Dump ufficiale ZIP con CSV annuali (non PDF sentenza-per-sentenza): `run` poi `extract`.

### MEF
Modulo allineato agli altri; live via browser CDP (`--live --cdp http://127.0.0.1:9222`). Dettagli in `scripts/scraper_mef/README.md`.

---

## Parallelo

```powershell
python -m scripts.scraper_italgiure run
python -m scripts.scraper_eurlex run
python -m scripts.scraper_curia run
python -m scripts.scraper_adm run --all-years
```

Oppure: `powershell -File scripts/run_scrapers_parallel.ps1`
