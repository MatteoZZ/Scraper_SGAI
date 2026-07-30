# scraper_adm — circolari dogane ADM

Scraper **locale** della lista:

https://www.adm.gov.it/portale/circolari-dogane

**Nessun upload SGAI** in questo modulo (lo fa il collega via endpoint).

## Flusso

1. Legge la lista HTML  
2. Ogni voce punta già a un URL `.pdf` (`/portale/documents/...`)  
3. Scarica i bytes PDF, valida (`%PDF-`, `%%EOF`, `pypdf`), salva con nome canonico  
4. Checkpoint per resume / skip

Naming esempio: `ADM_Circolare_19_2026_468276.pdf`

## Comandi

Dalla root repo `ragflow`:

```powershell
python -m scripts.scraper_adm dry-run
python -m scripts.scraper_adm dry-run --all-years
python -m scripts.scraper_adm run --max 1 --no-resume
python -m scripts.scraper_adm run --all-years --max 300
```

`--all-years` scansiona le pagine archivio dogane (1997→oggi) oltre alla lista corrente.

Output default: `scripts/scraper_adm/downloads_out`

## Test

```powershell
cd scripts
python -m unittest discover -s scraper_adm/tests -v
```

## Fuori scope

- Upload / endpoint SGAI  
- Altre sezioni ADM / archivio storico per anno  
- Multi-worker  
