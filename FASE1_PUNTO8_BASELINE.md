# Fase 1 — Punto 8: metriche baseline (sola lettura)

**Obiettivo:** fotografia “prima” senza modificare dati, senza requeue, senza SQL di scrittura.

## Stato

| Metrica | Come ottenerla | Stato ora |
|---------|----------------|-----------|
| Documenti totali + stati | `GET /v1/admin/knowledge-status` (dopo auth deployata) | **Pending accesso/deploy** |
| `chunk_num > 0` | campo `docsWithChunks` (aggiunto nel branch) | **Pending accesso/deploy** |
| CPU, RAM, disco, container | SSH comandi read-only | **Pending accesso SSH** |
| Velocità doc/min | 2 campioni a distanza ≥10–15 min | **Pending** |

Finché non hai SSH + env admin + (idealmente) deploy del branch, **non puoi riempire i numeri reali**.  
Usa lo script sotto appena l’accesso c’è.

## A) Documenti (API, sola lettura)

Dopo login admin (o Bearer token):

```powershell
# 1) login (salva cookie nella sessione curl/Invoke-WebRequest)
$base = "https://sgailegal.com"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$login = Invoke-RestMethod -Uri "$base/v1/admin/login" -Method Post -ContentType "application/json" `
  -WebSession $session -Body (@{ username = $env:SGAI_ADMIN_EMAIL; password = $env:SGAI_ADMIN_PASSWORD } | ConvertTo-Json)
$login

# 2) status
$st = Invoke-RestMethod -Uri "$base/v1/admin/knowledge-status?dataset=SENTENZE%20BANCA%20DATI%20MEF" `
  -WebSession $session
$st.data | ConvertTo-Json -Depth 5
```

Annota:

```text
capturedAt:
total:
unstart:
running:
cancel:
done:
fail:
docsWithChunks:   # chunk_num > 0
chunkSum:
progress:
```

## B) Host / container (SSH, sola lettura)

```bash
hostname
date
uptime
df -h
free -h
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
cd /home/ubuntu/workspace/ragflow && git status --short --branch && git log -5 --oneline
```

## C) Velocità documenti/minuto

1. Campione T0: valore `done` (e/o `docsWithChunks`)  
2. Aspetta **15 minuti** senza fare requeue/upload  
3. Campione T1  
4. Calcolo:

```text
doc_per_min ≈ (done_T1 - done_T0) / 15
```

Se non si muove: scrivi `~0 doc/min` (baseline valida).

## Template da inviare (Gate 1)

```text
Metriche baseline:
Data/ora:
Dataset: SENTENZE BANCA DATI MEF
total=
unstart= running= cancel= done= fail=
docsWithChunks= (chunk_num > 0)
chunkSum=
CPU/load (uptime):
RAM (free -h):
Disco (df -h rilevante):
Container (docker ps):
Velocità≈ ___ doc/min (finestra 15 min)
Note: nessuna modifica dati; nessuno requeue.
Problemi rilevati:
```

## Script helper

Vedi: `raccogli_baseline_fase1.ps1` (stessa cartella).  
Richiede: chiave SSH già autorizzata e/o credenziali admin già configurate sul server (non in Git).
