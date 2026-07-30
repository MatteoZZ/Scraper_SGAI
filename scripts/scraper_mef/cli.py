"""CLI scraper MEF locale."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import SkipIndex
from .client import MefClient
from .config import Config, ROOT
from .logging_utils import setup_logger
from .metrics import Metrics
from .parse import iter_validated
from .runner import run_scraper

log = setup_logger()


def _build_index(cfg: Config, args: argparse.Namespace) -> SkipIndex:
    output_dir = Path(args.output_dir) if args.output_dir else cfg.output_dir
    server_files: list[Path] = []
    if getattr(args, "server_cache", None):
        server_files.extend(Path(p) for p in args.server_cache)
    if not server_files:
        server_files.extend(cfg.default_server_caches())
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in server_files:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return SkipIndex(
        output_dir=output_dir,
        server_cache_files=uniq,
        embedded_files=uniq,
        min_pdf_bytes=cfg.min_pdf_bytes,
        max_pdf_bytes=cfg.max_pdf_bytes,
    )


def cmd_dry_run(fixture: Path, cfg: Config, args: argparse.Namespace) -> int:
    if not fixture.exists():
        log.error("Fixture non trovata: %s", fixture)
        return 2

    index = _build_index(cfg, args)
    rows = MefClient().rows_from_fixture(fixture)
    metrics = Metrics(discovered=len(rows))
    results = []
    for row, meta, errors in iter_validated(rows):
        if errors:
            metrics.skipped_invalid += 1
            results.append({"action": "skip_invalid", "errors": errors, "row": row.__dict__})
            continue
        decision = index.decide(meta["nomeBase"])
        if decision.action == "skip_local":
            metrics.skipped_local += 1
        elif decision.action == "skip_server":
            metrics.skipped_server += 1
        elif decision.action == "skip_embedded":
            metrics.skipped_embedded += 1
        else:
            metrics.would_download += 1
        results.append(
            {
                **decision.to_dict(),
                "nomeFile": meta["nomeFile"],
                "nomeBase": meta["nomeBase"],
                "codice": meta["codice"],
                "tipo": meta.get("tipo"),
            }
        )
    print(
        json.dumps(
            {
                "sources": index.sources,
                "output_dir": str(index.output_dir),
                "metrics": metrics.as_dict(),
                "items": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_probe(nome: str, cfg: Config, args: argparse.Namespace) -> int:
    index = _build_index(cfg, args)
    base = nome[:-4] if nome.lower().endswith(".pdf") else nome
    decision = index.decide(base)
    print(
        json.dumps(
            {
                "nomeBase": base,
                "local_exists": bool(index.local_path(base) and index.local_path(base).exists()),
                "local_valid": index.is_local(base),
                "local_errors": index.local_errors(base) if index.local_path(base) else [],
                "server_known": index.is_server(base),
                "embedded_known": index.is_embedded(base),
                **decision.to_dict(),
                "output_dir": str(index.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    mode = "live" if args.live else "simulate"
    if int(args.max) < 1:
        log.error("--max deve essere >= 1 (ricevuto %s); nessun tentativo", args.max)
        return 2
    fixture = args.fixture
    if mode == "simulate":
        output_dir = Path(args.output_dir) if args.output_dir else (ROOT / ".tmp_out_simulate")
    else:
        output_dir = Path(args.output_dir) if args.output_dir else cfg.output_dir

    server_caches = [Path(p) for p in (args.server_cache or [])]
    if args.concurrency is not None:
        cfg.max_download_concurrency = max(1, min(2, int(args.concurrency)))

    return run_scraper(
        cfg=cfg,
        mode=mode,
        max_downloads=args.max,
        fixture=fixture,
        output_dir=output_dir,
        server_caches=server_caches,
        cdp_url=args.cdp,
        resume=not args.no_resume,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper MEF locale (Fase 2)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_skip_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--output-dir", type=Path, default=None)
        p.add_argument(
            "--server-cache",
            action="append",
            default=[],
            help="File cache nomi (ripetibile). Oppure env MEF_SCRAPER_SERVER_CACHES",
        )

    p_dry = sub.add_parser("dry-run", help="Solo skip A/B/C, nessun download")
    p_dry.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "fixtures" / "sample_rows.html",
    )
    add_skip_args(p_dry)

    p_probe = sub.add_parser("probe", help="Skip per un singolo nome")
    p_probe.add_argument("nome")
    add_skip_args(p_probe)

    p_run = sub.add_parser(
        "run",
        help="Download limitato con skip A/B/C (default: --simulate)",
    )
    p_run.add_argument(
        "--max",
        type=int,
        default=1,
        help="Max TENTATIVI download (successi + falliti), >= 1. Default 1. --max 0 = errore.",
    )
    p_run.add_argument(
        "--simulate",
        action="store_true",
        default=True,
        help="PDF sintetico da fixture (default, sicuro, no portale)",
    )
    p_run.add_argument(
        "--live",
        action="store_true",
        help="Download reale via CDP (browser già aperto sulla lista MEF)",
    )
    p_run.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "fixtures" / "sample_rows.html",
    )
    p_run.add_argument(
        "--cdp",
        default="http://127.0.0.1:9222",
        help="URL CDP per --live",
    )
    p_run.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Download concorrenti (1–2 stub, default 1). Non è multi-worker.",
    )
    p_run.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignora checkpoint (azzera posizione/processed/failed di questa sessione)",
    )
    add_skip_args(p_run)

    args = parser.parse_args(argv)
    cfg = Config()

    if getattr(args, "live", False):
        args.simulate = False

    if args.cmd == "dry-run":
        if args.output_dir is None:
            args.output_dir = cfg.output_dir
        return cmd_dry_run(args.fixture, cfg, args)
    if args.cmd == "probe":
        if args.output_dir is None:
            args.output_dir = cfg.output_dir
        return cmd_probe(args.nome, cfg, args)
    if args.cmd == "run":
        return cmd_run(cfg, args)

    parser.error(f"comando sconosciuto: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
