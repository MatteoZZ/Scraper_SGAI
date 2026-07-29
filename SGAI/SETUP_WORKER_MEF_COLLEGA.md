# Setup pratico worker MEF — guida per il collega

Portale: `bancadatigiurisprudenza.giustiziatributaria.gov.it`  
Script: `download_mef_2025.py`  
Obiettivo: scaricare sentenze 2025 con naming SGAI, saltando i nomi già in `cache_nomi_base_2025.txt`.

> **Attenzione:** 10–12 worker sullo **stesso IP senza proxy** = 403 continui.  
> La scala funziona **solo** con identità + proxy diversi. Senza proxy resta a **1 worker** (`--solo`).

---

## 1. Cosa serve prima di partire

| Voce | Requisito |
|------|-----------|
| Proxy | **Residential o Mobile**, uno per worker |
| Rotazione proxy | Lenta: ogni **10–20 minuti** (o via `--vpn-rotate-cmd` / provider sticky) |
| Browser | Edge reale via CDP, **un profilo per worker** |
| Rete | Ideale: 1 worker = 1 VM/PC + 1 proxy |

Lo script gestisce ora:
- `--proxy` / env `MEF_PROXY` (passato a Edge come `--proxy-server`)
- pausa **12–20 s random tra ogni PDF** (`--download-delay-min/max`)
- rotazione sessione ogni **25 download** → AUTO-HEAL (profilo/UA)
- cooldown globale **10 min** su 403 a raffica (`mef_akamai_cooldown.json`)
- AUTO-HEAL (cooldown, kill Edge, VPN cmd, profili `*_healN`, UA)

---

## 2. Piano a fasi

### Fase A — senza proxy (PC unico)
```powershell
cd "C:\Users\meko srl\.cursor\Matteo_folder"
python download_mef_2025.py --year 2025 --semestre 1 --materia D040 --resume `
  --profile-dir .edge_profile_mef_fresh --cdp-port 9230 `
  --solo --page-delay 15
```
Max **1** worker sullo stesso IP.

### Fase B — con proxy (consigliato)
1. Copia `proxies.example.txt` → `proxies.txt` (1 proxy per riga).
2. Opzionale: adatta i job in `avvia_workers_proxy.ps1`.
3. Avvia:
```powershell
.\avvia_workers_proxy.ps1
```
Inizia con **4** worker. Se 403/429 < ~5% per un’ora → sali. Se 403 a raffica → **stop 10–30 min**, poi metà worker.

### Fase C — 10–12 worker
Solo dopo Fase B stabile. Ogni nodo: profilo + porta CDP + proxy propri. **Non** condividere i file `mef_*lock` / cooldown tra VM remote (oppure usa `--solo` su ogni nodo).

---

## 3. Velocità per worker

| Parametro | Default script | Flag |
|-----------|----------------|------|
| Tra un PDF e il successivo | 12–20 s random | `--download-delay-min 12 --download-delay-max 20` |
| Tra click pagina `>` | ~15 s | `--page-delay 15` |
| Download / minuto | ~3–5 | non forzare sotto i 12 s |
| Rotazione identità | ogni 25 PDF nuovi | `--session-rotate-every 25` (0=off) |

Target realistico totale: **25–35/min** con tanti proxy, non 40/min sostenibili a lungo.

---

## 4. Proxy

```powershell
python download_mef_2025.py ... --proxy "http://USER:PASS@HOST:PORT"
# oppure
$env:MEF_PROXY = "http://USER:PASS@HOST:PORT"
```

Regole: residential/mobile; 1 sticky session = 1 worker; geoloc IT/EU; testa a mano una ricerca prima dello script.

---

## 5. Identità per worker

| Risorsa | Esempio |
|---------|---------|
| Profilo | `.edge_profile_mef_w01` … |
| CDP | `9230`, `9231`, … |
| Proxy | riga N di `proxies.txt` |

Non clonare lo stesso profilo su tutti. Lo script fa scroll leggero + pause tra PDF; AUTO-HEAL ruota UA/profilo.

---

## 6. Split lavoro

- `--semestre 1|2` + `--worker a|b` → metà materie
- `--materia D040` → una materia (es. Accertamento da sola)
- checkpoint: `mef_download_checkpoint_s1.json`, `_s1b`, …

Per D040: **un worker dedicato** su proxy pulito.

---

## 7. Cooldown 403 / 429

| Evento | Azione automatica |
|--------|-------------------|
| Singolo 403 | retry + AUTO-HEAL (~2 min) |
| ≥3 403 di fila | cooldown globale **10 min** (file condiviso) + heal lungo |
| Ogni 25 PDF | rotazione profilo/UA (se AUTO-HEAL on) |
| Dopo stop manuale | riparti con metà worker e pause più alte |

Disattiva heal solo per debug: `--no-auto-heal` (poi serve intervento manuale).

---

## 8. Checklist

1. [ ] `proxies.txt` compilato (residential)
2. [ ] Porte CDP libere; un Edge per profilo
3. [ ] Lock orfani rimossi solo se nessuno gira
4. [ ] Worker sfalsati (≥45 s) — lo fa `avvia_workers_proxy.ps1`
5. [ ] Monitor: `python monitor_mef.py`
6. [ ] 403 a raffica → stop globale, non aggiungere worker

Comando singolo con proxy:
```powershell
python download_mef_2025.py --year 2025 --semestre 1 --worker a `
  --profile-dir .edge_profile_mef_w01 --cdp-port 9230 `
  --proxy "http://USER:PASS@HOST:PORT" `
  --page-delay 15 --resume
```

---

## 9. Cosa NON fare

- 10 Edge sullo stesso ADSL senza proxy
- Copiare lo stesso profilo Edge
- Rotazione proxy a ogni PDF
- Pause sotto i 12 s “per finire prima”
- Due worker stessa materia/stesse pagine senza split

---

## 10. Messaggio corto

> Proxy residential, 4 worker max all’inizio, 12–20 s tra PDF, un profilo+proxy a testa. Script: `--proxy` + `avvia_workers_proxy.ps1`. Se 403 a raffica: stop 10–30 min, poi metà velocità. Senza proxy: 1 solo worker.

---

*Allineato a `download_mef_2025.py` (proxy, delay download, session rotate, cooldown globale).*
