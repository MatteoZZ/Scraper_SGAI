"""Orchestrazione dry-run / run ADM."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from .checkpoint import Checkpoint
from .client import AdmBlockedError, AdmClient, AdmHttpError
from .config import ARCHIVE_LIST_URLS, Config
from .download import ingest_pdf_bytes, validate_local_pdf
import logging

log = logging.getLogger("scraper_adm")


def _setup_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("scraper_adm")


def _load_items(cfg: Config, *, fixture: Path | None, all_years: bool):
    client = AdmClient(user_agent=cfg.user_agent)
    if fixture:
        return client.list_items_from_fixture(fixture), str(fixture)
    if all_years:
        items = client.list_items_many(ARCHIVE_LIST_URLS)
        return items, f"all-years:{len(ARCHIVE_LIST_URLS)}_pages"
    return client.list_items(cfg.list_url), cfg.list_url


def dry_run(
    cfg: Config, *, fixture: Path | None = None, all_years: bool = False
) -> int:
    _setup_log()
    items, source = _load_items(cfg, fixture=fixture, all_years=all_years)

    results = []
    ok = 0
    bad = 0
    for item in items:
        meta = item.to_meta()
        if not meta.get("ok"):
            bad += 1
            results.append({"action": "skip_invalid", "errors": [meta.get("error")], "text": item.text[:200]})
            continue
        ok += 1
        local = cfg.output_dir / meta["nomeFile"]
        local_errs = (
            validate_local_pdf(local, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes)
            if local.exists()
            else ["non esiste"]
        )
        results.append(
            {
                "action": "would_download" if local_errs else "skip_local",
                "nomeFile": meta["nomeFile"],
                "protocollo": meta.get("protocollo"),
                "numero": meta.get("numero"),
                "anno": meta.get("anno"),
                "data": meta.get("data"),
                "url": meta.get("url"),
                "title": meta.get("title"),
            }
        )

    print(
        json.dumps(
            {
                "fonte": "ADM",
                "source": source,
                "metrics": {"discovered": len(items), "valid": ok, "invalid": bad},
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
    max_attempts: int,
    fixture: Path | None = None,
    resume: bool = True,
    all_years: bool = False,
) -> int:
    log = _setup_log()
    max_attempts = int(max_attempts)
    if max_attempts < 1:
        log.error("--max deve essere >= 1 (ricevuto %s)", max_attempts)
        return 2

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Checkpoint(cfg.checkpoint_path)
    items, source = _load_items(cfg, fixture=fixture, all_years=all_years)
    checkpoint.data["list_url"] = source
    if not resume:
        checkpoint.data["processed"] = []
        checkpoint.data["failed"] = []
        checkpoint.data["status"] = "idle"
        checkpoint.save()

    client = AdmClient(user_agent=cfg.user_agent)
    if not items:
        log.error("Nessun PDF trovato nella lista ADM")
        return 2
    log.info("Lista ADM: %s documenti da %s", len(items), source)

    checkpoint.set_status("running")
    results: list[dict[str, Any]] = []
    attempts = 0
    downloaded = 0
    errors = 0
    skipped_local = 0
    skipped_checkpoint = 0
    blocked = 0

    for item in items:
        if attempts >= max_attempts:
            break
        meta = item.to_meta()
        if not meta.get("ok"):
            results.append({"action": "skip_invalid", "errors": [meta.get("error")], "text": item.text[:200]})
            continue

        nome_base = meta["nomeBase"]
        dest = cfg.output_dir / meta["nomeFile"]

        if resume and checkpoint.is_done(nome_base):
            local_errs = validate_local_pdf(
                dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
            )
            if not local_errs:
                skipped_checkpoint += 1
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
                results.append({"action": "skip_local", "nomeFile": meta["nomeFile"]})
                continue

        attempts += 1
        item_out: dict[str, Any] = {
            "action": "download",
            "attempt": attempts,
            "nomeFile": meta["nomeFile"],
            "url": meta["url"],
        }
        try:
            log.info("DOWNLOAD attempt=%s/%s %s", attempts, max_attempts, meta["nomeFile"])
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
            results.append(item_out)
            if attempts < max_attempts:
                delay = random.uniform(cfg.download_delay_min, cfg.download_delay_max)
                time.sleep(delay)
        except AdmBlockedError as exc:
            errors += 1
            blocked += 1
            item_out.update({"action": "blocked", "error": str(exc), "http_status": exc.status})
            results.append(item_out)
            checkpoint.mark_failed(nome_base)
            checkpoint.set_status("blocked")
            log.error("BLOCCATO: %s", exc)
            break
        except (AdmHttpError, Exception) as exc:
            errors += 1
            item_out.update({"action": "download_error", "error": str(exc)[:300]})
            results.append(item_out)
            checkpoint.mark_failed(nome_base)
            log.error("errore: %s", exc)

    if checkpoint.data.get("status") == "running":
        checkpoint.set_status("completed" if blocked == 0 else "blocked")

    print(
        json.dumps(
            {
                "fonte": "ADM",
                "limits": {
                    "max_attempts": max_attempts,
                    "upload": "DISABLED_NOT_IN_SCOPE",
                },
                "output_dir": str(cfg.output_dir),
                "checkpoint": {
                    "path": str(checkpoint.path),
                    "status": checkpoint.data.get("status"),
                    "last_document": checkpoint.data.get("last_document"),
                },
                "metrics": {
                    "discovered": len(items),
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
