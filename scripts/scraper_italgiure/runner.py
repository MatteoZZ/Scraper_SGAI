"""Orchestrazione dry-run / run Italgiure."""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from .checkpoint import Checkpoint
from .client import (
    ItalgiureBlockedError,
    ItalgiureClient,
    ItalgiureHttpError,
    build_solr_query,
    is_transient,
)
from .config import Config
from .download import ingest_pdf_bytes, validate_local_pdf
from .names import meta_from_solr_doc


def _setup_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("scraper_italgiure")


def _make_client(cfg: Config) -> ItalgiureClient:
    return ItalgiureClient(
        user_agent=cfg.user_agent,
        solr_url=cfg.solr_url,
        ssl_verify=cfg.ssl_verify,
    )


def _query(cfg: Config) -> str:
    return build_solr_query(kind=cfg.kind, sezioni=cfg.sezioni, anno=cfg.anno)


def dry_run(
    cfg: Config,
    *,
    fixture: Path | None = None,
    sample: int = 20,
) -> int:
    _setup_log()
    client = _make_client(cfg)
    query = _query(cfg)

    if fixture:
        num_found, metas = client.list_metas_from_fixture(fixture)
        source = str(fixture)
    else:
        num_found, metas = client.list_metas(
            query, rows=cfg.solr_rows, limit=max(0, sample)
        )
        source = query

    results = []
    ok = 0
    bad = 0
    for meta in metas:
        if not meta.get("ok"):
            bad += 1
            results.append(
                {
                    "action": "skip_invalid",
                    "errors": [meta.get("error")],
                    "id": meta.get("id"),
                }
            )
            continue
        ok += 1
        local = cfg.output_dir / meta["nomeFile"]
        local_errs = (
            validate_local_pdf(
                local, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
            )
            if local.exists()
            else ["non esiste"]
        )
        results.append(
            {
                "action": "would_download" if local_errs else "skip_local",
                "nomeFile": meta["nomeFile"],
                "id": meta["id"],
                "szdec": meta.get("szdec"),
                "sezione": meta.get("sezione"),
                "anno": meta.get("anno"),
                "numdec": meta.get("numdec"),
                "tipoprov": meta.get("tipoprov"),
                "datdep": meta.get("datdep"),
                "url": meta.get("url"),
            }
        )

    print(
        json.dumps(
            {
                "fonte": "ITALGIURE",
                "source": source,
                "filters": {
                    "kind": cfg.kind,
                    "sezioni": list(cfg.sezioni),
                    "anno": cfg.anno,
                },
                "metrics": {
                    "numFound": num_found,
                    "sample": len(metas),
                    "valid": ok,
                    "invalid": bad,
                },
                "items": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_download(
    cfg: Config,
    *,
    max_attempts: int | None = None,
    fixture: Path | None = None,
    resume: bool = True,
) -> int:
    log = _setup_log()
    if max_attempts is not None:
        max_attempts = int(max_attempts)
        if max_attempts < 1:
            log.error("--max se usato deve essere >= 1 (ricevuto %s)", max_attempts)
            return 2
    budget_label = "ALL" if max_attempts is None else str(max_attempts)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Checkpoint(cfg.checkpoint_path)
    client = _make_client(cfg)
    query = _query(cfg)
    checkpoint.data["query"] = str(fixture) if fixture else query
    if not resume:
        checkpoint.data["processed"] = []
        checkpoint.data["failed"] = []
        checkpoint.data["status"] = "idle"
        checkpoint.save()

    if fixture:
        num_found, metas = client.list_metas_from_fixture(fixture)
        docs_iter = iter(metas)
        log.info("Fixture Italgiure: %s documenti da %s", num_found, fixture)
    else:
        try:
            num_found = client.count(query)
        except ItalgiureHttpError as exc:
            if is_transient(exc):
                log.error(
                    "Rete/DNS non raggiunge Italgiure: %s — controlla internet e rilancia",
                    exc,
                )
                checkpoint.set_status("network_pause")
                print(
                    json.dumps(
                        {
                            "fonte": "ITALGIURE",
                            "action": "network_pause",
                            "error": str(exc)[:300],
                            "hint": "Controlla DNS/internet e rilancia: python -m scripts.scraper_italgiure run",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 4
            raise
        if num_found <= 0:
            log.error("Nessun documento Solr per query: %s", query)
            return 2
        log.info(
            "Solr Italgiure: numFound=%s budget=%s query=%s (paginazione rows=%s)",
            num_found,
            budget_label,
            query,
            cfg.solr_rows,
        )
        docs_iter = (
            meta_from_solr_doc(d) for d in client.iter_docs(query, rows=cfg.solr_rows)
        )

    checkpoint.set_status("running")
    results: list[dict[str, Any]] = []
    attempts = 0
    downloaded = 0
    errors = 0
    skipped_local = 0
    skipped_checkpoint = 0
    blocked = 0
    scanned = 0
    # In run completo non accumulare ogni skip in RAM/JSON finale
    keep_all_items = max_attempts is not None and max_attempts <= 100

    try:
        for meta in docs_iter:
            if max_attempts is not None and attempts >= max_attempts:
                break
            scanned += 1
            if not meta.get("ok"):
                item = {
                    "action": "skip_invalid",
                    "errors": [meta.get("error")],
                    "id": meta.get("id"),
                }
                if keep_all_items:
                    results.append(item)
                continue

            nome_base = meta["nomeBase"]
            dest = cfg.output_dir / meta["nomeFile"]

            if resume and checkpoint.is_done(nome_base):
                local_errs = validate_local_pdf(
                    dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
                )
                if not local_errs:
                    skipped_checkpoint += 1
                    if keep_all_items:
                        results.append({"action": "skip_checkpoint", "nomeBase": nome_base})
                    continue
                checkpoint.invalidate_done(nome_base)

            if dest.exists():
                local_errs = validate_local_pdf(
                    dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
                )
                if not local_errs:
                    skipped_local += 1
                    checkpoint.mark_processed(nome_base)
                    if keep_all_items:
                        results.append(
                            {"action": "skip_local", "nomeFile": meta["nomeFile"]}
                        )
                    continue

            attempts += 1
            item_out: dict[str, Any] = {
                "action": "download",
                "attempt": attempts,
                "nomeFile": meta["nomeFile"],
                "id": meta["id"],
                "url": meta["url"],
            }
            try:
                log.info(
                    "DOWNLOAD attempt=%s/%s %s",
                    attempts,
                    budget_label,
                    meta["nomeFile"],
                )
                data = client.download_pdf(meta["url"])
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
                item_out.update(
                    {
                        "action": "downloaded",
                        "sha256": outcome["sha256"],
                        "size": outcome["size"],
                        "path": outcome["path"],
                    }
                )
                if keep_all_items or len(results) < 50:
                    results.append(item_out)
                delay = random.uniform(cfg.download_delay_min, cfg.download_delay_max)
                time.sleep(delay)
            except ItalgiureBlockedError as exc:
                errors += 1
                blocked += 1
                item_out.update(
                    {"action": "blocked", "error": str(exc), "http_status": exc.status}
                )
                results.append(item_out)
                checkpoint.mark_failed(nome_base)
                checkpoint.set_status("blocked")
                log.error("BLOCCATO: %s", exc)
                break
            except (ItalgiureHttpError, Exception) as exc:
                errors += 1
                item_out.update({"action": "download_error", "error": str(exc)[:300]})
                results.append(item_out)
                log.error("errore: %s", exc)
                # WinError 32 / lock file / rete: non marcare failed (al resume ritenta)
                locked = getattr(exc, "winerror", None) in (32, 5) or isinstance(
                    exc, PermissionError
                )
                if is_transient(exc) or locked:
                    time.sleep(5.0 if is_transient(exc) else 1.0)
                else:
                    try:
                        checkpoint.mark_failed(nome_base)
                    except OSError as ck_exc:
                        log.error("checkpoint non salvato dopo errore: %s", ck_exc)

            if not fixture and scanned % cfg.solr_rows == 0:
                if max_attempts is None or attempts < max_attempts:
                    time.sleep(random.uniform(cfg.page_delay_min, cfg.page_delay_max))
    except Exception as exc:
        if is_transient(exc):
            log.error(
                "Rete/DNS durante paginazione Solr: %s — rilancia, riparte dal checkpoint",
                exc,
            )
            checkpoint.set_status("network_pause")
            print(
                json.dumps(
                    {
                        "fonte": "ITALGIURE",
                        "action": "network_pause",
                        "error": str(exc)[:300],
                        "hint": "Controlla internet e rilancia: python -m scripts.scraper_italgiure run",
                        "metrics": {
                            "numFound": num_found,
                            "scanned": scanned,
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

    print(
        json.dumps(
            {
                "fonte": "ITALGIURE",
                "filters": {
                    "kind": cfg.kind,
                    "sezioni": list(cfg.sezioni),
                    "anno": cfg.anno,
                    "query": query if not fixture else str(fixture),
                },
                "limits": {
                    "max_attempts": max_attempts,
                    "budget": budget_label,
                    "upload": "DISABLED_NOT_IN_SCOPE",
                },
                "output_dir": str(cfg.output_dir),
                "checkpoint": {
                    "path": str(checkpoint.path),
                    "status": checkpoint.data.get("status"),
                    "last_document": checkpoint.data.get("last_document"),
                },
                "metrics": {
                    "numFound": num_found,
                    "scanned": scanned,
                    "attempts": attempts,
                    "downloaded": downloaded,
                    "errors": errors,
                    "blocked": blocked,
                    "skipped_local": skipped_local,
                    "skipped_checkpoint": skipped_checkpoint,
                },
                "items": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if blocked:
        return 3
    return 0 if errors == 0 else 1
