# Fase 1 — Punto 7: audit log azioni di scrittura

**Branch:** `feature/admin-security-fase1`  
**File:** `api/apps/admin_app.py`  
**Prefisso log:** `[ADMIN_AUDIT]`

## Cosa viene registrato

Per ogni evento di audit:

| Campo | Contenuto |
|-------|-----------|
| `user` | email admin o `api-token` |
| `role` | `admin-read` / `admin-export` / `admin-ops` |
| `auth` | `session` o `bearer` |
| `ip` | IP client (`X-Forwarded-For` se presente) |
| `ts` | timestamp UTC ISO |
| + campi azione | parametri non sensibili + `result` |

**Non** vengono loggati: password, token, contenuto PDF, conversazioni.

## Azioni coperte

| Azione | Quando | Campi extra tipici |
|--------|--------|--------------------|
| `login` | ok / fail | `result` |
| `logout` | logout | `result` |
| `authz_denied` | ruolo insufficiente | `required_role`, `path`, `result` |
| `requeue_unstart` | dry_run o reale | `dataset`, `limit`, `dry_run`, `total_found`, `queued`, `errors_count`, `result` |

Le letture (`user-sessions`, `knowledge-status`) **non** generano audit di scrittura (solo le write / auth rilevanti).

## Esempio riga log

```text
[ADMIN_AUDIT] {'action': 'requeue_unstart', 'user': 'admin@example.com', 'role': 'admin-ops', 'auth': 'session', 'ip': '128.116.178.91', 'ts': '2026-07-27T12:00:00Z', 'dataset': 'SENTENZE BANCA DATI MEF', 'limit': 50, 'dry_run': True, 'total_found': 50, 'queued': 0, 'result': 'ok'}
```

## Come verificarlo (dopo deploy, sola lettura)

```bash
# sul server / nei log container
docker logs ragflow-server 2>&1 | grep ADMIN_AUDIT | tail -n 50
```

## Divieto Fase 1

Anche con audit pronto: **niente requeue reale** (`dry_run: false`) senza `REQUEUE AUTORIZZATO`.
