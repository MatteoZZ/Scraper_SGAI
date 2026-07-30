# scraper_mef (Fase 2 — locale)

Modulo scraper sentenze MEF **senza upload/requeue in produzione**.

**Nessun pilota, upload o deploy è autorizzato in questa versione.**

## Cosa è implementato

- Parse righe tabella + (live) associazione `Visualizza` → stessa `<tr>` via `href`
- Live: metadati dalla **pagina dettaglio** confrontati con la lista prima del salvataggio
- Skip A/B/C con validazione PDF locale (size, `%PDF-`, `%%EOF`, struttura `pypdf` trailer/catalogo)
- Checkpoint `processed` salta solo se PDF locale ancora valido o cache server; altrimenti invalida e riscarica
- Naming canonico `{Tipo}_{Codice}_{Numero}_{Anno}.pdf` (tipo reale, non sempre `Sentenza`)
- `--max` = tetto sui **tentativi** (successi + falliti), `>= 1` (`--max 0` = errore)
- Checkpoint/resume: `last_page`, `last_row_index` (reset su cambio pagina), `processed`, `failed`, `status`
- Stop su HTTP 403/429 / segnali WAF (status `blocked` + checkpoint)
- Upload **disabilitato** (stub)

## Cosa NON è implementato (dichiarato)

| Funzione | Stato |
|----------|--------|
| Paginazione automatica su tutte le pagine MEF | **NON implementata** |
| Ricerca/navigazione autonoma senza tab CDP | **NON implementata** |
| Concorrenza multi-worker / lease | **NON implementata** (semaforo locale 1–2 stub) |
| Upload SGAI / parsing / embedding | **NON implementato** (stub disabilitato) |
| Admin live / heartbeat remoto | **NON in questa PR** |
| `page_delay` tra pagine | stub presente, inutilizzato senza paginazione |

## Config (niente path assoluti del PC)

| Env / flag | Default |
|------------|---------|
| `MEF_SCRAPER_OUTPUT` / `--output-dir` | `scripts/scraper_mef/downloads_out` |
| `MEF_SCRAPER_SERVER_CACHES` / `--server-cache` | solo `data/cache_nomi_base_local.txt` |
| `MEF_SCRAPER_CHECKPOINT` | `.checkpoint.json` |
| `MEF_SCRAPER_UPLOAD` | `0` |
| `MEF_SCRAPER_MIN_PDF_BYTES` | `1000` |

## Comandi

Dalla root della repo `ragflow`:

```powershell
python -m scripts.scraper_mef dry-run --output-dir .\scripts\scraper_mef\downloads_out
python -m scripts.scraper_mef probe Sentenza_V70_100_2025.pdf --output-dir ...
python -m scripts.scraper_mef run --max 1 --no-resume
python -m scripts.scraper_mef run --live --max 1 --cdp http://127.0.0.1:9222 --output-dir ...
```

`--live` richiede browser con remote debugging e pagina risultati MEF già aperta.
Su CAPTCHA / 403 / 429 ripetuti: stop, checkpoint `blocked`, nessun bypass VPN.

## Test

```powershell
cd scripts
python -m unittest discover -s scraper_mef/tests -v
```

Coprono: parse/tipo, skip locale corrotto, `--max` su tentativi falliti, resume checkpoint, allineamento lista/dettaglio, PDF/HTML invalidi, 403/429, righe incomplete, duplicati.
