# SGAI — Download sentenze MEF

Scarica le sentenze dal portale MEF (Banca dati Giurisprudenza Tributaria),
le rinomina `Sentenza_{CODICE}_{NUMERO}_{ANNO}.pdf` e salta quelle già presenti
nella cache SGAI (`cache_nomi_base_YYYY.txt`).

> Il portale usa **Akamai**: serve un browser reale (Opera o Edge), non headless.

## Requisiti

- Windows
- Python 3.10+
- Playwright (`pip install playwright`)
- Opera oppure Microsoft Edge
- Pacchetto SGAI con `portal_to_filename.py`, `sgai_sentenze_cache.py` e
  `dati/cache_nomi_base_2025.txt` (percorso configurato in `download_mef_2025.py`)
- (Consigliato) Proton VPN installata, login + Auto-connect, per il cambio IP automatico se l’IP viene bruciato

## Setup

```powershell
cd SGAI
pip install playwright
```

## Esecuzione

### Comando consigliato (D040, semestre 1, 2025)

```powershell
cd SGAI

python download_mef_2025.py `
  --year 2025 `
  --semestre 1 `
  --materia D040 `
  --resume `
  --solo `
  --browser opera `
  --profile-dir .opera_profile_mef `
  --vpn proton `
  --page-delay 25 `
  --download-delay-min 18 `
  --download-delay-max 32
```

### Una riga (stesso comando)

```powershell
python download_mef_2025.py --year 2025 --semestre 1 --materia D040 --resume --solo --browser opera --profile-dir .opera_profile_mef --vpn proton --page-delay 25 --download-delay-min 18 --download-delay-max 32
```

### Forzare la pagina di ripartenza

```powershell
python download_mef_2025.py --year 2025 --semestre 1 --materia D040 --resume --start-pagina 355 --solo --browser opera --profile-dir .opera_profile_mef --vpn proton --page-delay 25 --download-delay-min 18 --download-delay-max 32
```

Sostituisci `355` con la pagina nel checkpoint (`mef_download_checkpoint_s1.json` → `start_pagina`).

### Flag utili

| Flag | Significato |
|------|-------------|
| `--resume` | Riprende dal checkpoint (`mef_download_checkpoint_s1.json`) |
| `--start-pagina N` | Forza la pagina di ripresa |
| `--solo` | Un solo worker su questo PC (niente lock condiviso) |
| `--browser opera\|edge` | Browser reale via CDP |
| `--profile-dir ...` | Profilo persistente (cookie Akamai) |
| `--vpn proton` | Rotazione IP automatica **solo se l’IP è bruciato** |
| `--vpn off` | Nessuna rotazione VPN |
| `--page-delay` | Pausa base tra pagine |
| `--download-delay-min/max` | Pausa tra un PDF e il successivo |

## Output

- PDF: `downloads_mef/`
- Checkpoint: `mef_download_checkpoint_s1.json`
- Log pagine: `mef_pagine_log.csv`

## Note operative

- **1 worker = 1 IP pubblico**. Due script sullo stesso IP → rischio 403.
- Su 403: prima AUTO-HEAL locale (stesso IP); il cambio Proton avviene solo se l’IP risulta bruciato da Akamai.
- Non chiudere a mano Opera/Proton mentre gira lo script.
- Interruzione: `Ctrl+C`, poi rilancia con `--resume`.
