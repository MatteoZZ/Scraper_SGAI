from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from scripts.scraper_common.checkpoint import Checkpoint
from scripts.scraper_common.pdfutil import (
    ingest_pdf_bytes,
    quick_local_pdf_ok,
    validate_local_pdf,
)

from .client import (
    EurlexBlockedError,
    EurlexClient,
    EurlexHttpError,
    _is_transient,
    check_dns,
)
from .config import Config


def _setup_log(cfg: Config | None = None) -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    log = logging.getLogger("scraper_eurlex")
    # File dedicato (non _run.err.log: quello è spesso lo stderr redirect di Start-Process)
    if cfg is not None:
        log_path = cfg.checkpoint_path.parent / "_live.log"
        already = any(
            isinstance(h, logging.FileHandler)
            and str(getattr(h, "baseFilename", "")).endswith("_live.log")
            for h in log.handlers
        )
        if not already:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            log.addHandler(fh)
            log.info("log file: %s", log_path)
    return log


def dry_run(cfg: Config, *, sample: int = 20) -> int:
    _setup_log(cfg)
    client = EurlexClient(
        sparql_url=cfg.sparql_url,
        user_agent=cfg.user_agent,
        name_prefix=cfg.name_prefix,
        mode=cfg.mode,
        languages=getattr(cfg, "languages", None),
    )
    num = client.count_works(eurovoc=cfg.eurovoc, caselaw_only=cfg.caselaw_only)
    items = list(
        client.iter_pdf_items(
            eurovoc=cfg.eurovoc,
            page_size=min(cfg.page_size, max(sample, 1)),
            caselaw_only=cfg.caselaw_only,
            limit=sample,
        )
    )
    print(
        json.dumps(
            {
                "fonte": cfg.name_prefix,
                "mode": cfg.mode,
                "eurovoc": cfg.eurovoc,
                "caselaw_only": cfg.caselaw_only,
                "metrics": {"numFound": num, "sample": len(items)},
                "items": [
                    {
                        "action": "would_download",
                        "nomeFile": m["nomeFile"],
                        "celex": m["celex"],
                        "lang": m["lang"],
                        "url": m["url"],
                    }
                    for m in items
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_download(cfg: Config, *, max_attempts: int | None = None, resume: bool = True) -> int:
    log = _setup_log(cfg)
    if max_attempts is not None and max_attempts < 1:
        log.error("--max se usato deve essere >= 1")
        return 2
    from scripts.scraper_common.shard import shard_owns

    budget = "ALL" if max_attempts is None else str(max_attempts)
    workers = max(1, int(getattr(cfg, "workers", 1) or 1))
    worker_id = int(getattr(cfg, "worker_id", 0) or 0)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Checkpoint(cfg.checkpoint_path, fonte=cfg.name_prefix)
    checkpoint.data["worker"] = {"id": worker_id, "workers": workers}
    client = EurlexClient(
        sparql_url=cfg.sparql_url,
        user_agent=cfg.user_agent,
        name_prefix=cfg.name_prefix,
        mode=cfg.mode,
        languages=getattr(cfg, "languages", None),
    )
    try:
        check_dns("publications.europa.eu")
    except EurlexHttpError as exc:
        log.error("%s", exc)
        checkpoint.set_status("network_pause")
        print(
            json.dumps(
                {
                    "fonte": cfg.name_prefix,
                    "action": "network_pause",
                    "error": str(exc)[:400],
                    "hint": (
                        "In PowerShell: nslookup publications.europa.eu\n"
                        "Poi: ipconfig /flushdns  |  oppure disattiva VPN / usa DNS 1.1.1.1\n"
                        "Quando risolve: python -m scripts.scraper_eurlex run"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 4
    # count è pesante: in resume con tanti file già ok, salta il COUNT
    already = len(checkpoint.data.get("processed") or []) if resume else 0
    if resume and already >= 200:
        num = -1
        log.info(
            "skip COUNT SPARQL (resume: %s già in checkpoint) — passo al catalogo",
            already,
        )
    else:
        try:
            num = client.count_works(eurovoc=cfg.eurovoc, caselaw_only=cfg.caselaw_only)
        except Exception as exc:
            log.warning("count SPARQL non disponibile (%s) — procedo comunque", exc)
            num = -1
    checkpoint.data["query"] = f"{cfg.mode}:{cfg.eurovoc}"
    if not resume:
        checkpoint.data["processed"] = []
        checkpoint.data["failed"] = []
        checkpoint._processed.clear()
        checkpoint._failed.clear()
    checkpoint.set_status("running")
    log.info(
        "%s: numFound=%s budget=%s mode=%s lingue=%s worker=%s/%s "
        "checkpoint=%s — catalogo keyset; DOWNLOAD solo shard di questo worker",
        cfg.name_prefix,
        num if num >= 0 else "?",
        budget,
        cfg.mode,
        ",".join(client.languages),
        worker_id,
        workers,
        cfg.checkpoint_path.name,
    )

    attempts = downloaded = errors = blocked = skipped_local = skipped_checkpoint = 0
    skipped_shard = 0
    results: list[dict[str, Any]] = []
    # Snapshot failed prima del clear: servono per non saltare ITA incompleta
    failed_before = list(checkpoint.data.get("failed") or []) if resume else []
    # Non marcare failed i DNS/rete: al resume ritentano
    if resume:
        checkpoint.clear_failed()

    catalog_cursor = checkpoint.data.get("catalog_cursor") if resume else None
    # cursore vecchio (solo offset) non è compatibile col keyset → riparti
    if isinstance(catalog_cursor, dict) and "after_work" not in catalog_cursor:
        log.info("ignoro catalog_cursor legacy (offset): uso keyset da capo")
        catalog_cursor = None
        checkpoint.set_catalog_cursor(None)
    # Se ITA è già piena (~3.5k) salta dritto a ENG: evita 10+ min di solo skip.
    # Non farlo se ci sono failed ITA da ritentare (altrimenti restano persi).
    ita_failed_pending = any(str(x).endswith("_ITA") for x in failed_before)
    if resume and not catalog_cursor and already >= 2500 and not ita_failed_pending:
        ita_done = sum(
            1 for x in (checkpoint.data.get("processed") or []) if str(x).endswith("_ITA")
        )
        if ita_done >= 3400:
            catalog_cursor = {
                "scope": "eurovoc",
                "lang": "ENG",
                "after_work": "",
            }
            log.info(
                "fast-forward: ITA già ~%s in checkpoint → parto da ENG (keyset)",
                ita_done,
            )
        elif ita_done >= 2500:
            log.info(
                "ITA in checkpoint ~%s (<3400): scorri da capo per ritentare eventuali buchi",
                ita_done,
            )
    last_cursor: dict[str, Any] | None = None

    def _on_cursor(cur: dict[str, Any]) -> None:
        nonlocal last_cursor
        last_cursor = cur

    try:
        meta_iter = client.iter_pdf_items(
            eurovoc=cfg.eurovoc,
            page_size=cfg.page_size,
            caselaw_only=cfg.caselaw_only,
            start_cursor=catalog_cursor if isinstance(catalog_cursor, dict) else None,
            on_cursor=_on_cursor,
        )
        for meta in meta_iter:
            if max_attempts is not None and attempts >= max_attempts:
                break
            nome_base = meta["nomeBase"]
            if workers > 1 and not shard_owns(
                nome_base, worker_id=worker_id, workers=workers
            ):
                skipped_shard += 1
                if skipped_shard % 1000 == 0:
                    log.info(
                        "worker %s/%s: skip shard %s (di altri worker)",
                        worker_id,
                        workers,
                        skipped_shard,
                    )
                continue
            dest = cfg.output_dir / meta["nomeFile"]
            if resume and checkpoint.is_done(nome_base):
                # check leggero: full pypdf su 6k+ PDF (anche da GB) sembrava "fermo"
                if quick_local_pdf_ok(
                    dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
                ):
                    skipped_checkpoint += 1
                    if skipped_checkpoint % 500 == 0:
                        log.info(
                            "resume: skip checkpoint %s (già ok)",
                            skipped_checkpoint,
                        )
                    continue
                checkpoint.invalidate_done(nome_base)
            if dest.exists() and quick_local_pdf_ok(
                dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
            ):
                skipped_local += 1
                checkpoint.mark_processed(nome_base)
                continue

            attempts += 1
            try:
                log.info(
                    "DOWNLOAD %s/%s %s (type=%s)",
                    attempts,
                    budget,
                    meta["nomeFile"],
                    meta.get("manif_type") or "?",
                )
                data = client.download_pdf(
                    meta["url"],
                    work=meta.get("work"),
                    lang=meta.get("lang"),
                )
                outcome = ingest_pdf_bytes(
                    data,
                    dest,
                    min_bytes=cfg.min_pdf_bytes,
                    max_bytes=cfg.max_pdf_bytes,
                    overwrite=dest.exists(),
                )
                if not outcome.get("ok"):
                    raise RuntimeError("; ".join(outcome.get("errors") or ["pdf invalido"]))
                downloaded += 1
                checkpoint.mark_processed(nome_base)
                time.sleep(random.uniform(cfg.download_delay_min, cfg.download_delay_max))
            except EurlexBlockedError as exc:
                errors += 1
                blocked += 1
                results.append({"action": "blocked", "error": str(exc)})
                checkpoint.set_status("blocked")
                log.error("BLOCCATO: %s", exc)
                break
            except (EurlexHttpError, Exception) as exc:
                errors += 1
                log.error("errore: %s", exc)
                transient = _is_transient(exc)
                locked = getattr(exc, "winerror", None) in (32, 5) or isinstance(
                    exc, PermissionError
                )
                if transient or locked:
                    time.sleep(5.0)
                else:
                    try:
                        checkpoint.mark_failed(nome_base)
                    except OSError:
                        pass
        # catalogo finito senza errori di rete
        if last_cursor is not None and blocked == 0:
            checkpoint.set_catalog_cursor(None)
    except KeyboardInterrupt:
        if last_cursor is not None:
            checkpoint.set_catalog_cursor(last_cursor)
        checkpoint.set_status("network_pause")
        mod = (
            "scripts.scraper_curia"
            if cfg.name_prefix == "CURIA"
            else "scripts.scraper_eurlex"
        )
        log.warning(
            "Interrotto (Ctrl+C). Checkpoint salvato (%s già ok). Rilancia: python -m %s run",
            skipped_checkpoint + skipped_local + downloaded,
            mod,
        )
        print(
            json.dumps(
                {
                    "fonte": cfg.name_prefix,
                    "action": "interrupted",
                    "hint": f"python -m {mod} run",
                    "metrics": {
                        "numFound": num,
                        "downloaded": downloaded,
                        "skipped_checkpoint": skipped_checkpoint,
                        "skipped_local": skipped_local,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 130
    except Exception as exc:
        mod = (
            "scripts.scraper_curia"
            if cfg.name_prefix == "CURIA"
            else "scripts.scraper_eurlex"
        )
        if _is_transient(exc):
            # salva cursore catalogo: al keepalive non ri-scorre i 6k già visti
            if last_cursor is not None:
                checkpoint.set_catalog_cursor(last_cursor)
            log.error(
                "Rete/SPARQL lenta o assente: %s — rilancia, riparte dal checkpoint"
                "%s",
                exc,
                f" (catalogo {last_cursor})" if last_cursor else "",
            )
            checkpoint.set_status("network_pause")
            print(
                json.dumps(
                    {
                        "fonte": cfg.name_prefix,
                        "action": "network_pause",
                        "error": str(exc)[:300],
                        "catalog_cursor": last_cursor,
                        "hint": f"Controlla internet e rilancia: python -m {mod} run",
                        "metrics": {
                            "numFound": num,
                            "attempts": attempts,
                            "downloaded": downloaded,
                            "errors": errors,
                            "skipped_checkpoint": skipped_checkpoint,
                            "skipped_local": skipped_local,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 4
        raise

    if checkpoint.data.get("status") == "running":
        checkpoint.set_status("completed" if blocked == 0 else "blocked")
    total_ok = len(checkpoint.data.get("processed") or [])
    log.info(
        "fine run: processed=%s attempts=%s downloaded=%s errors=%s "
        "skip_ckpt=%s skip_local=%s (non è un tetto a 6000: ITA+ENG PDF unici ~6k; "
        "i ~11k EuroVoc includono works senza PDF IT/EN)",
        total_ok,
        attempts,
        downloaded,
        errors,
        skipped_checkpoint,
        skipped_local,
    )
    print(
        json.dumps(
            {
                "fonte": cfg.name_prefix,
                "limits": {"max_attempts": max_attempts, "budget": budget, "upload": "DISABLED"},
                "output_dir": str(cfg.output_dir),
                "worker": {"id": worker_id, "workers": workers},
                "checkpoint": str(cfg.checkpoint_path),
                "metrics": {
                    "numFound": num,
                    "processed_total": total_ok,
                    "attempts": attempts,
                    "downloaded": downloaded,
                    "errors": errors,
                    "blocked": blocked,
                    "skipped_local": skipped_local,
                    "skipped_checkpoint": skipped_checkpoint,
                    "skipped_shard": skipped_shard,
                },
                "items": results[-20:],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if blocked:
        return 3
    return 0 if errors == 0 else 1
