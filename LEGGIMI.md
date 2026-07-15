# PACCHETTO COLLEGA — Sentenze MEF / SGAI

**Data:** 13 luglio 2026  
**Scopo:** sapere cosa abbiamo già sul server, evitare download inutili, e rinominare correttamente i PDF anche se il portale scarica file con nomi casuali.

---

## 1. Il problema in due righe

- Sul **server SGAI** abbiamo **655.240 file** (snapshot 13/07/2026), di cui **428.096 sentenze uniche** (il resto sono duplicati).
- Il **portale MEF** è cambiato: il PDF scaricato può avere un nome **casuale/gibberish**, ma nel HTML il link ha ancora un **title leggibile** con corte, numero e anno.
- Noi salviamo i file con questo schema fisso:

```
Sentenza_{CODICE}_{NUMERO}_{ANNO}.pdf
```

Esempio: `Sentenza_V10_13747_2021.pdf` = CGT 1° Napoli, n. 13747/2021.

---

## 2. Mappatura prefissi (V / U / Z)

| Prefisso | Significato | Esempi |
|----------|-------------|--------|
| **V** | 1° grado (serie V) | V10 = Napoli, V08 = Benevento, V14 = Bari |
| **U** | 1° grado (serie U) | U01 = Alessandria, U24 = Milano, U91 = Roma |
| **Z** | 2° grado regionale | Z29 = Campania, Z18 = Lazio, Z31 = Puglia |

**Nota:** V e U sono **entrambi 1° grado** — non sono tipi diversi, sono solo due serie di codici MEF. Alcune sezioni di 2° grado usano prefisso V (es. V92 Emilia-Romagna, V70 Lombardia).

La tabella completa è in **`codici_corte.json`** (102 corti).

---

## 3. Portale nuovo (2026) — struttura tabella HTML

Ogni riga della tabella risultati ha queste colonne (prime 4 fondamentali):

| Indice `td` | Campo | Esempio |
|-------------|-------|---------|
| **0** | tipo | `Sentenza` |
| **1** | numero | `1205` |
| **2** | anno | `2026` |
| **3** | corte | `CGT 2° Lombardia` |

Il link dettaglio ha `title` tipo:
```
Visualizza provvedimento n. 1205/2026 CGT 2° Lombardia
```

**Il PDF scaricato ha nome gibberish** — ignorarlo. Usare sempre `numero + anno + corte` dalla tabella.

### Mappatura precisa — esempi reali dal portale

| Corte portale                 | Codice  |         Nome file SGAI        |
|-------------------------------|---------|-------------------------------|
| CGT 2° Lombardia n. 1205/2026 | **V70** | `Sentenza_V70_1205_2026.pdf`  |
| CGT 2° Puglia n. 1489/2026    | **Z31** | `Sentenza_Z31_1489_2026.pdf`  |
| CGT 2° Lazio n. 2686/2026     | **Z18** | `Sentenza_Z18_2686_2026.pdf`  |
| CGT 2° Sicilia n. 3338/2026   | **Z46** | `Sentenza_Z46_3338_2026.pdf`  |
| CGT 1° Bologna n. 386/2026    | **U55** | `Sentenza_U55_386_2026.pdf`   |
| CGT 1° Napoli n. 13747/2021   | **V10** | `Sentenza_V10_13747_2021.pdf` |

Verifica mappatura:
```bash
python portal_to_filename.py --test-esempi
```

### Conversione da tabella o title

```bash
# Da celle tabella
python portal_to_filename.py --corte "CGT 2° Lombardia" --numero 1205 --anno 2026

# Da title link
python portal_to_filename.py --title "Visualizza provvedimento n. 1205/2026 CGT 2° Lombardia"
```

### Integrazione scraper (Playwright)

```python
# Per ogni <tr> nella tabella risultati:
cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
tipo, numero, anno, corte = cells[0], cells[1], cells[2], cells[3]

meta = parse_portal_row(numero, anno, corte, tipo)
# meta["codice"]    -> "V70"
# meta["nomeFile"]  -> "Sentenza_V70_1205_2026.pdf"

if cache.should_skip(meta["codice"], meta["numero"], meta["anno"]):
    continue  # gia sul server con embedding

# scarica PDF (nome gibberish), poi rinomina:
#   -> meta["nomeFile"]
```

Oppure dal `title` del link dettaglio:
```python
title = link.get_attribute("title")
meta = parse_portal_title(title)
```

---

## 4. Dal portale al nome file (legacy + 2026)

### Cosa leggere dal portale

Sul link download cerca l'attributo **`title`**, tipicamente:

```
Scarica il pdf della sentenza n. 13747/2021 CGT 1° Napoli
```

### Conversione automatica

```bash
python portal_to_filename.py --title "Scarica il pdf della sentenza n. 13747/2021 CGT 1° Napoli"
```

Risposta:
```json
{
  "codice": "V10",
  "nomeBase": "Sentenza_V10_13747_2021",
  "nomeFile": "Sentenza_V10_13747_2021.pdf"
}
```

Oppure, se hai già estratto i campi:
```bash
python portal_to_filename.py --corte "CGT 1° Napoli" --numero 13747 --anno 2021
```

### Integrazione nello scraper (Playwright / Selenium)

```python
# Prima di cliccare download:
title = link.get_attribute("title")
meta = parse_portal_title(title)   # vedi portal_to_filename.py
nome_corretto = meta["nomeFile"]   # Sentenza_V10_13747_2021.pdf

# 1) Controlla cache: ce l'abbiamo gia?
if cache.should_skip(meta["codice"], meta["numero"], meta["anno"]):
    continue   # SALTA download

# 2) Scarica (nome browser = gibberish, va bene)
# 3) Rinomina il file scaricato -> nome_corretto
```

**Regola d'oro:** non fidarti del nome del file scaricato. Usa sempre il **title del link** o i metadati pagina.

---

## 5. Cache veloce

### File da usare (gia pronti)

| File | Dimensione | Contenuto |
|------|-----------|-----------|
| `cache_nomi_base.txt` | ~10 MB | 428.096 nomi unici, uno per riga |
| `cache_manifest.jsonl` | ~57 MB | dettaglio: duplicati, stato, embedding |

### Setup una tantum

```bash
# Opzione A — da file gia forniti (offline, consigliata)
python sgai_sentenze_cache.py sync --from-csv dati/listone_sentenze.csv --cache-dir ./mia_cache

# Opzione B — dal server live (serve EC2 accesa)
python sgai_sentenze_cache.py sync --cache-dir ./mia_cache
```

### Controllo prima di ogni download

```bash
# Esiste sul server?
python sgai_sentenze_cache.py has V10 13747 2021
# exit 0 = SI, exit 1 = NO

# Dettaglio completo (duplicati, embedding, skip?)
python sgai_sentenze_cache.py check V10 13747 2021
```

### Nel codice Python

```python
from sgai_sentenze_cache import SentenzeCache

cache = SentenzeCache(cache_dir="mia_cache")
cache.sync()  # una volta all'avvio

if cache.should_skip("V10", "13747", "2021"):
    continue  # gia presente con embedding, NON scaricare

if cache.has("V10", "13747", "2021"):
    # esiste ma forse senza embedding: decidere se riscaricare
    pass
```

### Cosa significa "nome base"

Tutte queste varianti sono la **stessa sentenza**:
- `Sentenza_V10_13747_2021.pdf`
- `Sentenza_V10_13747_2021(1).pdf`
- `Sentenza_V10_13747_2021 - 2024-12-08T180311.512.pdf`
- `abc123random.pdf` (gibberish) → se rinominato con metadati portale

**Nome base** = `Sentenza_V10_13747_2021` (senza `.pdf`, senza `(1)`, senza timestamp).

---

## 6. Contenuto pacchetto

```
pacchetto_collega/
  LEGGIMI.md                  ← questo file
  codici_corte.json           ← mappatura completa prefissi
  portal_to_filename.py       ← portale -> nome file SGAI

../sgai_sentenze_cache.py     ← (copia inclusa) cache lookup worker
../sgai_sentenze_controllo.py ← (copia inclusa) rigenera listone completo

Oppure usa direttamente i file in questa cartella:
  sgai_sentenze_cache.py
  sgai_sentenze_controllo.py

dati/  (copiare da exports/listone_collega/)
  cache_nomi_base.txt         ← lookup veloce
  cache_manifest.jsonl        ← dettaglio duplicati
  listone_sentenze.csv        ← tutti i file con stato
  duplicati_riepilogo.csv     ← gruppi duplicati
```

### Numeri attuali (13/07/2026)

| Metrica | Valore |
|---------|--------|
| File totali sul server | 655.240 |
| Sentenze uniche (nome base) | 428.096 |
| Gruppi con duplicati | 94.766 |
| Parsate (done) | 352.769 |
| Con embedding | 353.210 |

---

## 7. Flusso completo consigliato

```
AVVIO WORKER
  |
  v
Carica cache (cache_nomi_base.txt + manifest.jsonl)
  |
  v
Per ogni risultato sul portale MEF:
  |
  +-- Leggi celle tabella: numero, anno, corte (td 1,2,3)
  |     oppure title link "Visualizza provvedimento n. ..."
  |
  +-- cache.should_skip(codice, numero, anno)?
  |     SI  -> salta
  |     NO  -> continua
  |
  +-- Scarica PDF (nome gibberish OK)
  |
  +-- Rinomina -> Sentenza_{CODICE}_{NUMERO}_{ANNO}.pdf
  |
  +-- Carica su SGAI / RAGFlow
  |
  v
Fine giornata: opzionale cache.sync() per aggiornare
```

---

## 8. API server

Base URL: `https://sgailegal.com`

| Endpoint | Uso |
|----------|-----|
| `GET /v1/admin/sentenze-manifest?format=keys` | Scarica lista nomi base (~10 MB) |
| `GET /v1/admin/sentenze-check?codice=V10&numero=13747&anno=2021` | Lookup singola |
| `GET /v1/admin/sentenze-codici` | Mappatura codici |
| `GET /v1/admin/sentenze-export` | CSV completo (242 MB, lento) |

Wake EC2 se spenta:
```
POST https://91k2hfw1n3.execute-api.eu-north-1.amazonaws.com/wake-up
Body: {"force_start": true, "target_instance": "SGAI-Production"}
```

---

## 9. Domande frequenti

**Q: Il PDF scaricato si chiama `f7a3b2c1.pdf`, come lo gestisco?**  
A: Leggi il `title` del link, calcola il nome con `portal_to_filename.py`, rinomina dopo il download.

**Q: V10 e U01 sono la stessa cosa?**  
A: No, sono corti diverse (Napoli vs Alessandria). Stesso grado (1°), codice diverso.

**Q: Come capisco se saltare il download?**  
A: `should_skip` = true solo se esiste già una copia **parsata con embedding**. Se esiste ma è `cancel`/`unstart`, potrebbe servire riscaricare.

**Q: Ogni quanto aggiornare la cache?**  
A: Una volta al giorno o prima di una sessione lunga di scraping.

---

## 10. Test rapido

```bash
# Mappatura portale
python portal_to_filename.py --corte "CGT 1° Napoli" --numero 13747 --anno 2021

# Cache (dopo sync)
python sgai_sentenze_cache.py check V10 13747 2021
```

Se `check` risponde `"skip": true` → quella sentenza c'è già, non scaricare.

---

*Per problemi: contattare il team SGAI. Snapshot generato il 13/07/2026.*
