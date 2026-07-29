# Riassunto operativo — SGAI / RAGFlow (collega)

**Data riassunto:** 24 luglio 2026  
**Destinatario:** collaboratore (Matteo)  
**Scopo di questo file:** avere in un solo posto obiettivi, materiali ricevuti, cosa fare prima di iniziare, fasi di lavoro, divieti e consegne finali.

---

## 1. Obiettivo generale (in una frase)

Completare in modo **sicuro e controllato** il ciclo sentenze MEF → SGAI/RAGFlow: acquisire solo ciò che manca, caricare a lotti, vedere l’avanzamento in admin, e riprendere parsing/embedding senza saturare la produzione.

### Cosa deve esistere a fine lavoro


| Componente                | Risultato atteso                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------ |
| **Scraper MEF**           | Integrato con SGAI, idempotente, con cache/duplicati, dry run, checkpoint, limiti di concorrenza |
| **Admin live**            | Pagina con avanzamento quasi in tempo reale + endpoint admin protetti lato server                |
| **Embedding controllato** | Requeue a piccoli lotti, con metriche e stop automatico se il carico è anomalo                   |




### Cosa c’è già vs cosa manca

**Già nel progetto (indicativo):** normalizzazione nomi, cache/duplicati, inventari admin, `/admin-stats`, pipeline RAGFlow di parsing/chunk/embedding.

**Manca ancora:** scraper Playwright/Selenium completo e integrato; admin davvero “live”; protezione solida degli endpoint admin prima di azioni operative; ripresa embedding a lotti autorizzati.

---



## 2. Materiali ricevuti dal collega


| Materiale                  | Percorso                                                                                                             |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Pacchetto operativo        | `C:\Users\meko srl\Downloads\SGAI_RAGFlow_pacchetto_collega_2026-07-24_1556\`                                        |
| Contenuto utile            | `...\pacchetto_collega\` (`ACCESSO_E_REGOLE.md`, `SCHEDA_DA_COMPILARE.md`, cache, script, mappa)                     |
| Piano di lavoro tutelativo | `C:\Users\meko srl\Downloads\PIANO_SCRAPER_ADMIN_EMBEDDING.md`                                                       |
| Messaggio onboarding       | in sostanza: leggi regole → compila scheda → manda `.pub` → `LETTO E ACCETTATO` → niente deploy senza autorizzazione |




### File da leggere obbligatoriamente

1. `pacchetto_collega\ACCESSO_E_REGOLE.md`
2. `pacchetto_collega\SCHEDA_DA_COMPILARE.md`
3. `PIANO_SCRAPER_ADMIN_EMBEDDING.md`
4. (dopo clone del repo) `docs/SGAI_INFRASTRUCTURE.md` e i file indicati nel piano



### Ambiente di produzione (riferimento)

- Host: `sgailegal.com` / IP `13.49.16.179`
- Repo Git: `https://github.com/gpittonMeko/ragflow.git`
- Path server: `/home/ubuntu/workspace/ragflow`
- Wake EC2 (se spenta): Lambda `wake-up` con body  
`{"force_start": true, "target_instance": "SGAI-Production"}`
- **Non** riceverai `LLM_14.pem`: accesso personale con la tua chiave `.pub`, revocabile

---

## 3. Cosa fare PRIMA di iniziare (Gate 0) — checklist

Finché il Gate 0 non è chiuso: **niente SSH produzione, niente deploy, niente requeue, niente upload pilota**.

### A. Accettazione regole

Rispondere per iscritto:

```text
LETTO E ACCETTATO
```



### B. Compilare e inviare `SCHEDA_DA_COMPILARE.md`

Includere almeno:

- nome e cognome, azienda/ruolo, email, telefono
- username GitHub
- sistema operativo, IP pubblico da cui lavorerai
- data/ora inizio e **scadenza** accesso richieste
- **chiave pubblica SSH** (solo `.pub`, una riga `ssh-ed25519` o `ssh-rsa`)
- scopo preciso, file/cartelle previsti, servizi consultati
- dichiarazione obbligatoria della scheda (sezione B)



### C. Generare la chiave SSH personale (PowerShell)

```powershell
ssh-keygen -t ed25519 -a 100 -f "$HOME\.ssh\sgai-collega" -C "nome.cognome-SGAI"
Get-Content "$HOME\.ssh\sgai-collega.pub"
```

- Inviare **solo** `sgai-collega.pub`
- **Mai** inviare `sgai-collega` (chiave privata)
- Non caricare private key su Git, chat, drive, email



### D. Proposta tecnica (richiesta dal piano — punti 1–12)

Oltre alla scheda, comunicare:

1. Username GitHub
2. Chiave pubblica `.pub`
3. Data di scadenza accesso
4. Descrizione tecnica dello scraper proposto
5. Libreria e dipendenze (es. Playwright)
6. URL/pagine del portale MEF
7. Frequenza massima richieste/download
8. Autenticazione al portale (se necessaria)
9. Dati salvati in locale
10. Metodo di upload in RAGFlow
11. Branch e file da creare/modificare
12. Test, metriche, rischi, rollback

Confermare anche che l’acquisizione dal portale è autorizzata e **non** aggirerà CAPTCHA/blocchi/limiti.

### E. Prima di ogni modifica successiva

Inviare e attendere conferma:

```text
Ticket/obiettivo:
Branch:
Commit di partenza:
File interessati:
Test previsti:
Rischi conosciuti:
Procedura di rollback:
Orario previsto di inizio/fine:
```

---



## 4. Fasi di lavoro (dal piano tutelativo)

Ogni fase ha un **gate**: non si passa oltre senza review/autorizzazione.


| Fase  | Nome                       | Cosa fare                                                                                                                                  | Autorizzazione chiave                                 |
| ----- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| **0** | Accesso e proposta         | Scheda, `.pub`, piano tecnico, limiti traffico                                                                                             | Gate 0 chiuso dal responsabile                        |
| **1** | Baseline + sicurezza admin | Clone, branch, leggere codice, proteggere `/v1/admin/`*, togliere credenziali dal frontend, audit log, metriche baseline **senza** requeue | PR sicurezza approvata                                |
| **2** | Scraper locale             | Modulo tipo `scripts/scraper_mef/`, cache, dry run, test con fixture, **nessuna** produzione                                               | Gate 2: review dry run/test                           |
| **3** | Upload pilota              | 1 → 5 → 20 → 50 documenti, osservazione ≥15 min tra aumenti                                                                                | `PILOTA AUTORIZZATO` (+ nuove approvazioni per 20/50) |
| **4** | Admin quasi real-time      | Polling 15–30 s, summary leggeri, azioni operative solo se protette                                                                        | Deploy solo dopo prova di carico                      |
| **5** | Sicurezza worker           | Verificare topologia executor, limiti conservativi, PR compose se serve                                                                    | Review configurazione                                 |
| **6** | Ripresa embedding          | Dry run 10 → requeue max 50 → 100 → max 200/ciclo                                                                                          | `REQUEUE AUTORIZZATO` ogni ciclo                      |
| **7** | Deploy e chiusura          | PR → push → deploy autorizzato → smoke → consegna + revoca accessi                                                                         | `DEPLOY AUTORIZZATO`                                  |




### Flusso sviluppo/deploy obbligatorio

```text
locale (branch) → test → PR → review → merge/push
→ DEPLOY AUTORIZZATO
→ sul server: working tree pulito → pull FF → build → smoke
```

Ordine: **commit → push → pull sul server → build → verifica**.  
Mai allineare il server con `reset --hard` o copie manuali nei container.

---



## 5. Regole d’oro e divieti



### Frasi che sbloccano operazioni sensibili


| Operazione    | Serve esplicitamente  |
| ------------- | --------------------- |
| Deploy        | `DEPLOY AUTORIZZATO`  |
| Requeue       | `REQUEUE AUTORIZZATO` |
| Upload pilota | `PILOTA AUTORIZZATO`  |


Il silenzio **non** vale come sì.

### Vietato (senza autorizzazione scritta)

- `git reset --hard`, `git clean -fd`, force push, riscrittura history  
- `rm -rf`, cancellazioni/rinomine massive PDF, prune Docker con volumi  
- modifiche dirette a MySQL/Redis/ES/MinIO, `.env`, IAM, DNS, SG, Lambda  
- riavvii massivi / stop istanza  
- script “ultra veloci”, centinaia di worker, requeue di migliaia di doc in una botta  
- password/token nel frontend o in Git  
- aggirare CAPTCHA/WAF/limiti del portale MEF  
- scaricare dati di produzione fuori dall’ambiente senza tracciamento/autorizzazione



### Fermarsi e avvisare subito se

- working tree server sporco / modifiche non committate  
- duplicati o nomi file sbagliati  
- CPU/RAM/disco anomali, container in restart loop, 5xx ripetuti  
- crescita `fail`, embedding fermo con coda che cresce  
- segreti esposti o perdita dati

---



## 6. Scraper — regole operative essenziali

Per ogni sentenza:

1. Leggere tipo / numero / anno / corte (celle tabella o `title`)
2. Nome canonico: `Sentenza_{CODICE}_{NUMERO}_{ANNO}.pdf`
3. Cache locale → eventuale lookup remoto sola lettura
4. Skip solo se già completata **con embedding** (regola cache)
5. Download in temp → verifica PDF (tipo, size, firma) → SHA-256
6. Rinomina senza sovrascrivere → upload a concorrenza bassa → checkpoint

Proprietà obbligatorie: **idempotenza**, **resume**, **dry run**, backoff su 429/5xx, stop pulito, log senza segreti.

### Due tipi di “già presente” (utile anche per gli script locali)


| Segnale tipico                                     | Significato                                     |
| -------------------------------------------------- | ----------------------------------------------- |
| Cache SGAI / server (`gia_sgai` / `skipped_cache`) | Nome già noto sul server (o in cache/skip file) |
| Locale (`gia_locali` / `skipped_local`)            | File già in cartella download / sessione locale |


Il pacchetto fornisce snapshot cache in `pacchetto_collega\dati\` (storico): sincronizzare senza cancellare/sovrascrivere dati esistenti.

---



## 7. Due binari di lavoro (non confonderli)


| Binario                             | Dove                                                                           | Quando                                         |
| ----------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------- |
| **A. Download MEF sul PC**          | Script locali (`download_mef_*.py`, `downloads_mef`, eventuali copie da `D:\`) | Può continuare in locale, senza accesso server |
| **B. Integrazione SGAI produzione** | Repo `ragflow`, admin, upload, requeue, deploy                                 | Solo dopo Gate 0 e fasi autorizzate            |


Il pacchetto collega **non** sostituisce il lavoro locale di download; fornisce regole, cache snapshot e percorso verso l’integrazione produzione.

---



## 8. Metriche e criteri di stop (da tenere a mente)

Monitorare: documenti per stato (`unstart` / `running` / `cancel` / `done` / `fail`), `chunk_num > 0`, CPU, RAM, disco, velocità doc/min, errori.

**Stop immediato** (nuovi upload/requeue) se, tra le altre: CPU executor >80% sostenuta, disco >85%, restart loop, 5xx, `fail` in crescita rapida, embedding fermo ~30 min con coda crescente, timeout DB/Redis/ES/MinIO, duplicati/nomi errati.

Fermare non significa cancellare code o volumi.

---



## 9. Consegna a fine attività

Inviare al responsabile:

1. Link a tutte le PR
2. Commit distribuiti
3. Elenco file modificati
4. Architettura scraper + config senza segreti
5. Manuali avvio/pausa/ripresa e dashboard
6. Procedura dry run e requeue
7. Test e metriche prima/dopo
8. Incidenti/anomalie
9. Elenco dati scaricati e dove sono
10. Conferma eliminazione credenziali/dati temporanei
11. Data in cui l’accesso può essere revocato

Testo di chiusura tipico:

```text
Confermo di avere eliminato dal mio computer chiavi temporanee, file .env,
token, dump e dati di produzione non espressamente autorizzati.
```

---



## 10. Messaggio minimo da mandare OGGI (bozza)

```text
LETTO E ACCETTATO

Ho letto ACCESSO_E_REGOLE.md e PIANO_SCRAPER_ADMIN_EMBEDDING.md.
In allegato/nel messaggio:
- SCHEDA_DA_COMPILARE.md compilata
- chiave pubblica SSH (.pub)
- username GitHub: <...>
- periodo accesso richiesto: dal <...> al <...>
- proposta tecnica sintetica (scraper, limiti, branch, test, rollback)

Non eseguirò deploy, requeue o upload pilota senza le autorizzazioni scritte
DEPLOY AUTORIZZATO / REQUEUE AUTORIZZATO / PILOTA AUTORIZZATO.
```

---



## 11. Prossimi passi concreti (ordine consigliato)

1. Compilare scheda + generare `.pub` + rispondere `LETTO E ACCETTATO`
2. Mandare proposta tecnica (12 punti) e attendere accesso GitHub + SSH
3. Clonare `ragflow`, creare branch, fare **Fase 1** (sicurezza admin)
4. Sviluppare **Fase 2** scraper in locale con test/dry run
5. Solo con autorizzazioni: pilota upload → admin live → worker → requeue embedding
6. Chiusura con PR, report e pulizia credenziali

---



## 12. Riferimenti rapidi ai documenti sorgente

- Regole accesso: `...\pacchetto_collega\ACCESSO_E_REGOLE.md`  
- Scheda: `...\pacchetto_collega\SCHEDA_DA_COMPILARE.md`  
- Messaggio tipo: `...\pacchetto_collega\MESSAGGIO_DA_INVIARE.md`  
- Cache/nomi: `...\pacchetto_collega\LEGGIMI.md`  
- Piano completo fasi 0–7: `C:\Users\meko srl\Downloads\PIANO_SCRAPER_ADMIN_EMBEDDING.md`  
- Questo riassunto: `C:\Users\meko srl\.cursor\Matteo_folder\SGAI\RIASSUNTO_OPERATIVO_SGAI_RAGFLOW.md`

---

*Documento di lavoro personale: sintetizza i materiali del collega; in caso di conflitto prevalgono i file ufficiali del responsabile (*`ACCESSO_E_REGOLE.md` *e* `PIANO_SCRAPER_ADMIN_EMBEDDING.md`*).*