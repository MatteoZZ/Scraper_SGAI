# Fase 1 — Punti 5/6/7 (A–E) eseguiti in locale

**Branch:** `feature/admin-security-fase1`  
**Repo locale:** `C:\Users\meko srl\Downloads\SGAI_RAGFlow_pacchetto_collega_2026-07-24_1556\ragflow`

## Cosa è stato fatto

### A — Backend
- Credenziali **solo da env**: `SGAI_ADMIN_EMAIL`, `SGAI_ADMIN_PASSWORD`, `SGAI_ADMIN_ROLE`
- Token opzionale: `SGAI_ADMIN_API_TOKEN` (+ `SGAI_ADMIN_API_TOKEN_ROLE`)
- Endpoint nuovi: `POST /v1/admin/login`, `POST /v1/admin/logout`, `GET /v1/admin/me`
- Gate su tutte le `/v1/admin/*` (tranne login/logout)
- Ruoli: `admin-read` < `admin-export` < `admin-ops`
- Cap server-side su requeue `limit` ≤ 200

### B — Frontend
- Rimosse email/password hardcoded da `web/src/pages/admin-stats/login.tsx`
- Login via `POST /v1/admin/login`

### C — Chiamate API
- `credentials: 'include'` su login/me/logout/user-sessions/knowledge-status
- Su 401/403: sessione locale azzerata e ritorno al login

### D — Verifica bundle / secret nel frontend
Comandi (da root repo, dopo eventuale build):

```powershell
Select-String -Path "web\src\pages\admin-stats\*.tsx" -Pattern "Sgailegal|ADMIN_PASSWORD|upload89" -SimpleMatch
# dopo build:
# Get-ChildItem web\dist -Recurse -Include *.js | Select-String -Pattern "Sgailegal\.upload|ADMIN_PASSWORD"
```

Atteso: nessun match nei sorgenti admin-stats.

### E — Rotazione password (da chiedere al responsabile)
La vecchia password era nel bundle React → **va ruotata in produzione** e messa solo in env EC2, mai in Git.

Messaggio da inviare:

```text
Ho rimosso le credenziali hardcoded dalla pagina admin.
Serve rotazione della password admin storica e configurazione env:
SGAI_ADMIN_EMAIL / SGAI_ADMIN_PASSWORD / SGAI_ADMIN_ROLE
Branch: feature/admin-security-fase1
Nessun deploy finché non arriva DEPLOY AUTORIZZATO.
```

## Mappa ruoli ↔ endpoint

| Endpoint | Ruolo minimo |
|----------|--------------|
| GET/POST login, logout | pubblico |
| GET /me | admin-read |
| POST /user-sessions | admin-read |
| GET /knowledge-status | admin-read |
| POST /requeue-unstart-documents | admin-ops |

## Audit log
Su login/logout/requeue (e deny authz): log `[ADMIN_AUDIT]` con user, role, ip, parametri non sensibili, risultato.

## Non fatto qui (serve accesso / Gate)
- Metriche baseline produzione (punto 8 del piano)
- Deploy / requeue reale
- Push/PR (se vuoi lo faccio dopo tua conferma)
