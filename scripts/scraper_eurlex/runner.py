from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from scripts.scraper_common.checkpoint import Checkpoint
from scripts.scraper_common.pdfutil import ingest_pdf_bytes, validate_local_pdf

from .client import (
    EurlexBlockedError,
    EurlexClient,
    EurlexHttpError,
    _is_transient,
    check_dns,
)
from .config import Config


def _setup_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("scraper_eurlex")


def dry_run(cfg: Config, *, sample: int = 20) -> int:
    _setup_log()
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
    log = _setup_log()
    if max_attempts is not None and max_attempts < 1:
        log.error("--max se usato deve essere >= 1")
        return 2
    budget = "ALL" if max_attempts is None else str(max_attempts)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Checkpoint(cfg.checkpoint_path, fonte=cfg.name_prefix)
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
    checkpoint.set_status("running")
    log.info(
        "%s: numFound=%s budget=%s mode=%s lingue=%s — "
        "le righe 'catalogo/resume skip' NON sono download; DOWNLOAD appare solo sui mancanti",
        cfg.name_prefix,
        num if num >= 0 else "?",
        budget,
        cfg.mode,
        ",".join(client.languages),
    )

    attempts = downloaded = errors = blocked = skipped_local = skipped_checkpoint = 0
    results: list[dict[str, Any]] = []
    # Non marcare failed i DNS/rete: al resume ritentano
    # e ripulisci failed precedenti da crash rete
    if resume and checkpoint.data.get("failed"):
        checkpoint.data["failed"] = []
        checkpoint.save()

    try:
        meta_iter = client.iter_pdf_items(
            eurovoc=cfg.eurovoc,
            page_size=cfg.page_size,
            caselaw_only=cfg.caselaw_only,
        )
        for meta in meta_iter:
            if max_attempts is not None and attempts >= max_attempts:
                break
            nome_base = meta["nomeBase"]
            dest = cfg.output_dir / meta["nomeFile"]
            if resume and checkpoint.is_done(nome_base):
                if not validate_local_pdf(
                    dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
                ):
                    skipped_checkpoint += 1
                    if skipped_checkpoint % 100 == 0:
                        log.info(
                            "resume: skip checkpoint %s (già ok)",
                            skipped_checkpoint,
                        )
                    continue
                checkpoint.invalidate_done(nome_base)
            if dest.exists() and not validate_local_pdf(
                dest, min_bytes=cfg.min_pdf_bytes, max_bytes=cfg.max_pdf_bytes
            ):
                skipped_local += 1
                checkpoint.mark_processed(nome_base)
                continue

            attempts += 1
            try:
                log.info("DOWNLOAD %s/%s %s", attempts, budget, meta["nomeFile"])
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
    except KeyboardInterrupt:
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
            log.error(
                "Rete/SPARQL lenta o assente: %s — rilancia, riparte dal checkpoint",
                exc,
            )
            checkpoint.set_status("network_pause")
            print(
                json.dumps(
                    {
                        "fonte": cfg.name_prefix,
                        "action": "network_pause",
                        "error": str(exc)[:300],
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
    print(
        json.dumps(
            {
                "fonte": cfg.name_prefix,
                "limits": {"max_attempts": max_attempts, "budget": budget, "upload": "DISABLED"},
                "output_dir": str(cfg.output_dir),
                "metrics": {
                    "numFound": num,
                    "attempts": attempts,
                    "downloaded": downloaded,
                    "errors": errors,
                    "blocked": blocked,
                    "skipped_local": skipped_local,
                    "skipped_checkpoint": skipped_checkpoint,
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
