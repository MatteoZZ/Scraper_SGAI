"""Orchestrazione comando `run` (Fase 2): skip A/B/C + download limitato, no upload."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .cache import SkipIndex
from .checkpoint import Checkpoint
from .client import LiveMefClient, MefBlockedError, MefClient, MefHttpError
from .config import Config
from .download import (
    cleanup_tmp_dir,
    coherence_nome_vs_meta,
    ingest_pdf_bytes,
    make_minimal_pdf,
)
from .limits import RunLimits
from .logging_utils import setup_logger
from .metrics import Metrics
from .parse import PortalRow, metas_match, validate_row
from .upload import UploadDisabledError, enqueue_upload

log = setup_logger()


def _build_index(cfg: Config, output_dir: Path, server_caches: list[Path]) -> SkipIndex:
    files = list(server_caches) if server_caches else cfg.default_server_caches()
    return SkipIndex(
        output_dir=output_dir,
        server_cache_files=files,
        embedded_files=files,
        min_pdf_bytes=cfg.min_pdf_bytes,
        max_pdf_bytes=cfg.max_pdf_bytes,
    )


FetchFn = Callable[[PortalRow], tuple[bytes, dict | None]]


def process_rows(
    rows: list[PortalRow],
    *,
    index: SkipIndex,
    checkpoint: Checkpoint,
    metrics: Metrics,
    limits: RunLimits,
    cfg: Config,
    output_dir: Path,
    max_attempts: int,
    page_number: int,
    fetch_pdf: FetchFn,
    require_detail_meta: bool = False,
    resume: bool = True,
) -> list[dict[str, Any]]:
    """
    Loop core testabile.
    --max = max_attempts conta OGNI tentativo download (successo o fallimento),
    non solo i PDF salvati. Skip A/B/C e invalid non consumano il budget.
    """
    results: list[dict[str, Any]] = []
    max_attempts = int(max_attempts)
    if max_attempts < 1:
        raise ValueError(f"max_attempts deve essere >= 1 (ricevuto {max_attempts})")
    # Allinea pagina: se è cambiata rispetto al checkpoint, azzera last_row_index
    checkpoint.sync_page(page_number)
    checkpoint.set_status("running")

    for row in rows:
        if limits.stop.should_stop:
            checkpoint.set_status("stopped")
            break
        if metrics.attempts >= max_attempts:
            break

        row_index = int(row.row_index if row.row_index is not None else 0)
        metrics.discovered += 1

        errs = validate_row(row)
        if errs:
            metrics.skipped_invalid += 1
            results.append({"action": "skip_invalid", "errors": errs, "row": row.__dict__})
            # avanza posizione anche su invalid per non restare bloccati
            checkpoint.set_position(page=page_number, row_index=row_index + 1)
            continue

        list_meta = row.to_meta()
        nome_base = list_meta["nomeBase"]

        if resume and checkpoint.is_done(nome_base):
            # Skip solo se PDF locale ancora valido OPPURE cache server autorevole.
            # Altrimenti invalida processed/failed e riscarica (file cancellato/corrotto).
            local_ok = index.is_local(nome_base)
            server_ok = index.is_server(nome_base) or index.is_embedded(nome_base)
            if local_ok or server_ok:
                metrics.skipped_checkpoint += 1
                results.append(
                    {
                        "action": "skip_checkpoint",
                        "nomeBase": nome_base,
                        "nomeFile": list_meta["nomeFile"],
                        "reason": "local_valid" if local_ok else "server_authoritative",
                    }
                )
                checkpoint.set_position(page=page_number, row_index=row_index + 1)
                continue
            checkpoint.invalidate_done(nome_base)
            results.append(
                {
                    "action": "checkpoint_invalidated",
                    "nomeBase": nome_base,
                    "detail": "processed/failed senza PDF locale valido né conferma server",
                }
            )

        if resume and row_index < int(checkpoint.data.get("last_row_index") or 0):
            # stessa pagina: salta solo se il doc è ancora "done" (local/server ok).
            # Se invalidato (PDF mancante/corrotto) deve essere riprocessato.
            if (
                int(checkpoint.data.get("last_page") or 0) == page_number
                and checkpoint.is_done(nome_base)
            ):
                metrics.skipped_checkpoint += 1
                results.append(
                    {
                        "action": "skip_checkpoint_row",
                        "row_index": row_index,
                        "nomeBase": nome_base,
                    }
                )
                continue

        decision = index.decide(nome_base)
        item: dict[str, Any] = {
            **decision.to_dict(),
            "nomeFile": list_meta["nomeFile"],
            "nomeBase": nome_base,
            "tipo": list_meta.get("tipo"),
            "row_index": row_index,
        }
        if decision.action == "skip_local":
            metrics.skipped_local += 1
            results.append(item)
            checkpoint.set_position(page=page_number, row_index=row_index + 1)
            continue
        if decision.action == "skip_server":
            metrics.skipped_server += 1
            results.append(item)
            checkpoint.set_position(page=page_number, row_index=row_index + 1)
            continue
        if decision.action == "skip_embedded":
            metrics.skipped_embedded += 1
            results.append(item)
            checkpoint.set_position(page=page_number, row_index=row_index + 1)
            continue

        if limits.stop.should_stop:
            item["action"] = "stopped_before_download"
            results.append(item)
            checkpoint.set_status("stopped")
            break

        if metrics.attempts >= max_attempts:
            item["action"] = "would_download_capped"
            metrics.would_download += 1
            results.append(item)
            break

        # --- tentativo download (conta sempre) ---
        metrics.attempts += 1
        item["attempt"] = metrics.attempts
        dest = output_dir / list_meta["nomeFile"]
        tmp = cfg.tmp_dir / f"{nome_base}.bin"

        try:
            with limits.download_slot():
                log.info(
                    "DOWNLOAD attempt=%s/%s %s",
                    metrics.attempts,
                    max_attempts,
                    list_meta["nomeFile"],
                )
                data, detail_meta = fetch_pdf(row)

            if require_detail_meta or detail_meta is not None:
                if not detail_meta:
                    raise RuntimeError("metadati dettaglio assenti")
                mismatch = metas_match(list_meta, detail_meta)
                if mismatch:
                    raise RuntimeError("PDF-metadati disallineati: " + "; ".join(mismatch))
                # nome canonico dal dettaglio (fonte autoritativa)
                final_meta = detail_meta
            else:
                final_meta = list_meta

            coh = coherence_nome_vs_meta(final_meta["nomeBase"], final_meta)
            if coh:
                raise RuntimeError("; ".join(coh))

            dest = output_dir / final_meta["nomeFile"]
            outcome = ingest_pdf_bytes(
                data,
                dest,
                min_bytes=cfg.min_pdf_bytes,
                max_bytes=cfg.max_pdf_bytes,
                overwrite=decision.layer == "A_locale_corrupt",
            )
            if not outcome.get("ok"):
                raise RuntimeError("PDF non valido: " + "; ".join(outcome.get("errors") or []))

            metrics.downloaded += 1
            checkpoint.mark_processed(
                final_meta["nomeBase"], page=page_number, row_index=row_index + 1
            )
            index.server_keys.add(final_meta["nomeBase"].lower())
            item.update(
                {
                    "action": "downloaded",
                    "nomeFile": final_meta["nomeFile"],
                    "nomeBase": final_meta["nomeBase"],
                    "tipo": final_meta.get("tipo"),
                    "sha256": outcome["sha256"],
                    "size": outcome["size"],
                    "path": outcome["path"],
                    "detail_ok": True,
                }
            )
            try:
                enqueue_upload(dest, enabled=cfg.upload_enabled)
                metrics.uploaded += 1
            except UploadDisabledError:
                item["upload"] = "disabled"
            except NotImplementedError as exc:
                item["upload"] = f"stub:{exc}"
            results.append(item)
            cleanup_tmp_dir(cfg.tmp_dir, keep_newest=20)
            if metrics.attempts < max_attempts and not limits.stop.should_stop:
                limits.pause_between_downloads(log=lambda m: log.info(m))

        except MefBlockedError as exc:
            metrics.errors += 1
            metrics.blocked += 1
            reason = f"HTTP {exc.status}: {exc}"[:300]
            item.update(
                {
                    "action": "blocked",
                    "error": reason,
                    "http_status": exc.status,
                }
            )
            results.append(item)
            checkpoint.mark_failed(nome_base, page=page_number, row_index=row_index + 1)
            checkpoint.set_status("blocked", block_reason=reason)
            log.error("BLOCCATO (stop): %s", reason)
            break

        except (MefHttpError, Exception) as exc:
            metrics.errors += 1
            item.update({"action": "download_error", "error": str(exc)[:300]})
            results.append(item)
            checkpoint.mark_failed(nome_base, page=page_number, row_index=row_index + 1)
            log.error("errore download: %s", exc)
            # tentativo fallito consuma --max; continua finché budget
            if metrics.attempts >= max_attempts:
                break
            if not limits.stop.should_stop:
                limits.pause_between_downloads(log=lambda m: log.info(m))

    if checkpoint.data.get("status") == "running":
        if limits.stop.should_stop:
            checkpoint.set_status("stopped")
        elif metrics.blocked:
            checkpoint.set_status("blocked")
        else:
            checkpoint.set_status("completed")
    return results


def run_scraper(
    *,
    cfg: Config,
    mode: str,
    max_downloads: int,
    fixture: Path | None,
    output_dir: Path,
    server_caches: list[Path],
    cdp_url: str,
    resume: bool = True,
) -> int:
    """
    mode:
      - simulate: fixture + PDF sintetico (no portale)
      - live: CDP browser già aperto sulla pagina risultati MEF
    max_downloads: budget TENTATIVI (non solo successi). --max 0 → errore.
    """
    max_attempts = int(max_downloads)
    if max_attempts < 1:
        log.error("--max deve essere >= 1 (ricevuto %s); nessun tentativo", max_downloads)
        return 2
    limits = RunLimits(
        max_download_concurrency=cfg.max_download_concurrency,
        delay_min=cfg.download_delay_min,
        delay_max=cfg.download_delay_max,
        page_delay=cfg.page_delay_sec,
    )
    limits.stop.install_signals()

    index = _build_index(cfg, output_dir, server_caches)
    checkpoint = Checkpoint(cfg.checkpoint_path)
    if not resume:
        checkpoint.data["last_row_index"] = 0
        checkpoint.data["last_page"] = 0
        checkpoint.data["processed"] = []
        checkpoint.data["failed"] = []
        checkpoint.data["status"] = "idle"
        checkpoint.data["block_reason"] = None
        checkpoint.save()
    elif checkpoint.data.get("status") == "completed":
        # Nuova run: riparti dalla riga 0 ma conserva processed/failed (idempotenza)
        checkpoint.data["last_row_index"] = 0
        checkpoint.data["status"] = "idle"
        checkpoint.data["block_reason"] = None
        checkpoint.save()

    metrics = Metrics()
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    if mode == "simulate":
        if not fixture or not fixture.exists():
            log.error("simulate richiede --fixture esistente")
            return 2
        rows = MefClient().rows_from_fixture(fixture)
        for i, row in enumerate(rows):
            if row.row_index is None:
                row.row_index = i

        def _fetch_sim(row: PortalRow) -> tuple[bytes, dict | None]:
            # simulate: nessun dettaglio reale; meta lista usata come "detail"
            return make_minimal_pdf(cfg.min_pdf_bytes), row.to_meta()

        results = process_rows(
            rows,
            index=index,
            checkpoint=checkpoint,
            metrics=metrics,
            limits=limits,
            cfg=cfg,
            output_dir=output_dir,
            max_attempts=max_attempts,
            page_number=int(checkpoint.data.get("last_page") or 1) or 1,
            fetch_pdf=_fetch_sim,
            require_detail_meta=False,
            resume=resume,
        )

    elif mode == "live":
        log.info("LIVE: connessione CDP %s (browser già aperto sulla lista risultati)", cdp_url)
        try:
            with LiveMefClient(cdp_url=cdp_url) as client:
                rows = client.current_rows()
                if not rows:
                    log.error("Nessun link Visualizza sulla tab MEF — fai prima una Ricerca")
                    return 2
                page_number = client.current_page_number()
                # Se la pagina live è diversa da quella nel checkpoint, azzera le righe
                checkpoint.sync_page(page_number)

                def _fetch_live(row: PortalRow) -> tuple[bytes, dict | None]:
                    tmp = cfg.tmp_dir / f"live_{row.row_index or 0}.pdf"
                    data, detail_meta = client.fetch_row_pdf(row, tmp)
                    return data, detail_meta

                results = process_rows(
                    rows,
                    index=index,
                    checkpoint=checkpoint,
                    metrics=metrics,
                    limits=limits,
                    cfg=cfg,
                    output_dir=output_dir,
                    max_attempts=max_attempts,
                    page_number=page_number,
                    fetch_pdf=_fetch_live,
                    require_detail_meta=True,
                    resume=resume,
                )
        except MefBlockedError as exc:
            log.error("LIVE bloccato: %s", exc)
            checkpoint.set_status("blocked", block_reason=str(exc)[:300])
            return 3
        except Exception as exc:
            log.error("LIVE fallito: %s", exc)
            log.error(
                "Avvia Edge/Opera con remote debugging (es. porta 9222) "
                "e lascia aperta la pagina risultati MEF, poi riprova."
            )
            checkpoint.set_status("error", block_reason=str(exc)[:300])
            return 2
    else:
        log.error("mode sconosciuta: %s", mode)
        return 2

    payload = {
        "mode": mode,
        "limits": {
            "max_attempts": max_attempts,
            "note": "--max limita tutti i tentativi (successi + falliti), non solo i download ok",
            "download_concurrency": limits.max_download_concurrency,
            "delay_min": limits.delay_min,
            "delay_max": limits.delay_max,
            "page_delay": limits.page_delay,
            "upload_enabled": cfg.upload_enabled,
            "pagination": "NOT_IMPLEMENTED",
            "multi_worker": "NOT_IMPLEMENTED",
            "sgai_upload": "DISABLED_STUB",
        },
        "output_dir": str(output_dir),
        "sources": index.sources,
        "checkpoint": {
            "path": str(checkpoint.path),
            "last_page": checkpoint.data.get("last_page"),
            "last_row_index": checkpoint.data.get("last_row_index"),
            "last_document": checkpoint.data.get("last_document"),
            "status": checkpoint.data.get("status"),
            "block_reason": checkpoint.data.get("block_reason"),
        },
        "metrics": metrics.as_dict(),
        "items": results,
        "stopped": limits.stop.should_stop,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    log.info(
        "run fine: attempts=%s downloaded=%s errors=%s blocked=%s status=%s",
        metrics.attempts,
        metrics.downloaded,
        metrics.errors,
        metrics.blocked,
        checkpoint.data.get("status"),
    )
    if metrics.blocked:
        return 3
    return 0 if metrics.errors == 0 else 1
