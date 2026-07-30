# scraper_italgiure — Cassazione Civile (Quinta + Sezioni Unite)

Scraper **locale** su Italgiure SN-Cassazione:

https://www.italgiure.giustizia.it/sncass/

**Criteri fissi (lista collega):**

- `kind = snciv` (Civile)
- sezioni: **Quinta** (`szdec:"5"`) + **Sezioni Unite** (`szdec:"U"`)
- PDF via endpoint clean attach (`.clean.pdf`)
- **Nessun upload SGAI** (lo fa il collega via endpoint)

Volume Solr: ~**61.800** documenti. Il comando `run` senza opzioni li scarica **tutti**.

## Flusso

1. Query Solr pubblica (`sn-collection/select`)
2. Costruisce URL PDF: `xway/.../hc.dll?verbo=attach&db=snciv&id=./...clean.pdf`
3. Scarica bytes, valida (`%PDF-`, `%%EOF`, `pypdf`), salva con nome canonico
4. Checkpoint per resume / skip (puoi interrompere e riprendere)
5. Se riceve HTML/captcha → stop `blocked`

Naming esempio:

`ITALGIURE_Civile_5_2021_21853_O_snciv2021521853O.pdf`

## Comandi

Dalla root repo `ragflow`:

```powershell
# Conta corpus
python -m scripts.scraper_italgiure dry-run

# Scarica TUTTO (Quinta + Sezioni Unite) — riprende da checkpoint se interrotto
python -m scripts.scraper_italgiure run
```

Output default: `scripts/scraper_italgiure/downloads_out`

SSL: di default `verify=False` (certificato Italgiure spesso fallisce su Windows).

## Test

```powershell
cd scripts
python -m unittest discover -s scraper_italgiure/tests -v

# prova singola (solo per debug)
python -m scripts.scraper_italgiure run --max 1
```

## Fuori scope

- Upload / endpoint SGAI
- Altre sezioni civili / penale
- Risoluzione captcha interattiva
- Multi-worker
