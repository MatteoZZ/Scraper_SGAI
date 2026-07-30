# Fase 1 — Punto 4: endpoint admin già presenti

**Data:** 2026-07-27  
**Fonte codice:** `https://github.com/gpittonMeko/ragflow.git`  
**Branch analizzato:** `origin/feature/sgai-wake-chat-ui-applications`  
**File:** `api/apps/admin_app.py`  
**Prefisso URL:** `/v1/admin` (blueprint Flask `admin`, auto-registrazione app)

**Nota:** su `main` questi endpoint **non** ci sono. Servono il branch feature sopra (o equivalenti).  
Nel pacchetto collega (`LEGGIMI.md`) compaiono anche API tipo `sentenze-manifest` / `sentenze-check`: **non risultano** in questo branch Git.

**Auth oggi:** nessun controllo server-side reale su questi handler.  
La pagina `web/src/pages/admin-stats/` usa solo login frontend (`localStorage`) → **non protegge** le API se chiamate direttamente.

---

## Riepilogo

| # | Metodo | Path completo | Classe (proposta Fase 1) | Modifica dati? | Usato dalla UI admin-stats |
|---|--------|---------------|--------------------------|----------------|----------------------------|
| 1 | `POST` | `/v1/admin/user-sessions` | sola lettura | No | Sì |
| 2 | `GET` | `/v1/admin/knowledge-status` | sola lettura | No | Sì |
| 3 | `POST` | `/v1/admin/requeue-unstart-documents` | azione operativa | Sì (accoda task) | No (non chiamato dalla UI attuale) |

---

## 1. `POST /v1/admin/user-sessions`

| Campo | Dettaglio |
|-------|-----------|
| Funzione | `get_user_sessions` |
| Scopo | Elenco sessioni/conversazioni utente + statistiche aggregate |
| Auth server | Assente |
| Side effect | Nessuno (sola lettura DB conversazioni) |

**Body JSON (opzionale):**

```json
{
  "startDate": "2025-11-01",
  "endDate": "2025-11-04"
}
```

**Risposta (sintesi):**

```json
{
  "sessions": [
    {
      "id": "...",
      "sessionId": "...",
      "userId": "...",
      "email": null,
      "plan": "free|premium|beta",
      "loginTime": "YYYY-MM-DD HH:MM:SS",
      "ipAddress": "...",
      "userAgent": "...",
      "browser": "...",
      "os": "...",
      "deviceType": "...",
      "messagesCount": 0,
      "conversation": [{ "type": "question|answer", "text": "...", "timestamp": 0 }],
      "duration": 0,
      "tokens": 0
    }
  ],
  "stats": {
    "totalUsers": 0,
    "freeUsers": 0,
    "premiumUsers": 0,
    "betaTesters": 0,
    "todayLogins": 0,
    "uniqueCountries": 0
  }
}
```

**Note:** espone dati sensibili (IP, conversazioni). In Fase 1 va protetto almeno come `admin-read`.

---

## 2. `GET /v1/admin/knowledge-status`

| Campo | Dettaglio |
|-------|-----------|
| Funzione | `get_knowledge_status` |
| Scopo | Avanzamento parsing/embedding documenti di un knowledge base |
| Auth server | Assente |
| Side effect | Nessuno (aggregate SQL di sola lettura) |

**Query string:**

| Parametro | Default | Descrizione |
|-----------|---------|-------------|
| `dataset` | `SENTENZE BANCA DATI MEF` | Nome knowledge base |

Esempio UI:

`/v1/admin/knowledge-status?dataset=SENTENZE%20BANCA%20DATI%20MEF`

**Risposta (sintesi):**

```json
{
  "dataset": "SENTENZE BANCA DATI MEF",
  "found": true,
  "total": 0,
  "chunkSum": 0,
  "statusCounts": {
    "unstart": 0,
    "running": 0,
    "cancel": 0,
    "done": 0,
    "fail": 0
  },
  "progress": 0.0,
  "remaining": 0,
  "lastStartedAt": null
}
```

Se il dataset non esiste: `found: false` e contatori a zero.

**Utilità Fase 1 punto 8:** questo endpoint è la base naturale per le metriche documenti (totali + stati).  
**Limite:** non espone direttamente il conteggio `chunk_num > 0` come campo separato; espone `chunkSum` (somma chunk) e stati `run`. Per `chunk_num > 0` serve estensione read-only o query/report dedicato.

---

## 3. `POST /v1/admin/requeue-unstart-documents`

| Campo | Dettaglio |
|-------|-----------|
| Funzione | `requeue_unstart_documents` |
| Scopo | Rimette in coda documenti con stato `unstart` |
| Auth server | Assente |
| Side effect | **Sì** — crea/accoda task di parsing (non è dry-run di default) |

**Body JSON:**

```json
{
  "dataset": "SENTENZE BANCA DATI MEF",
  "limit": 1000,
  "dry_run": false
}
```

| Campo | Default | Note |
|-------|---------|------|
| `dataset` | `SENTENZE BANCA DATI MEF` | KB target |
| `limit` | `1000` | Max documenti per richiesta |
| `dry_run` | `false` | Se `true`, conta e non accoda |

**Comportamento (da codice):**

- seleziona solo `run == unstart` e documenti `VALID`
- salta se nel frattempo risultano `DONE` / `RUNNING`
- con `dry_run: true` restituisce solo `total_found`
- con `dry_run: false` accoda i task

**Classificazione Fase 1:** azione operativa (`admin-ops`).  
**Divieto Fase 1:** non eseguire requeue reale (`dry_run: false`) senza `REQUEUE AUTORIZZATO`.

---

## Frontend correlato (non sono API, ma rilevante)

| File | Ruolo |
|------|--------|
| `web/src/pages/admin-stats/login.tsx` | Login UI con credenziali **hardcoded** nel bundle (da rimuovere — punto 5) |
| `web/src/pages/admin-stats/index.tsx` | Chiama `user-sessions` e `knowledge-status`; gate solo `localStorage` |

---

## Endpoint citati nel pacchetto ma NON trovati in questo branch

Da `LEGGIMI.md` / documentazione collega (snapshot storico / server):

| Path (documentazione pacchetto) | Stato in branch analizzato |
|---------------------------------|----------------------------|
| `GET /v1/admin/sentenze-manifest` | Non trovato |
| `GET /v1/admin/sentenze-check` | Non trovato |
| `GET /v1/admin/sentenze-codici` | Non trovato |
| `GET /v1/admin/sentenze-export` | Non trovato |

Da chiedere al responsabile: se vivono solo su server, altro branch, o vanno ancora portati in Git.

---

## Problemi rilevati (anteprima per il report Gate 1)

1. Tutti e 3 gli endpoint admin sono chiamabili senza auth server-side.  
2. `requeue-unstart-documents` è particolarmente critico (write) e senza protezione.  
3. Auth UI solo client-side + secret nel frontend.  
4. Possibile divergenza documentazione pacchetto vs codice Git su API sentenze.

---

## Test di sola lettura consigliati (dopo auth / con autorizzazione)

```text
# status documenti (read)
GET /v1/admin/knowledge-status?dataset=SENTENZE%20BANCA%20DATI%20MEF

# dry run requeue (read-like; non accoda) — solo se autorizzati a toccare l'endpoint
POST /v1/admin/requeue-unstart-documents
{"dataset":"SENTENZE BANCA DATI MEF","limit":10,"dry_run":true}
```

In Fase 1: **non** usare `"dry_run": false`.
