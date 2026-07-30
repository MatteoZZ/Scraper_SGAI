"""CLI scraper ADM circolari dogane."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .runner import dry_run, run_download


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper circolari ADM (locale, no upload SGAI)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dry = sub.add_parser("dry-run", help="Elenca PDF dalla lista, nessun download")
    p_dry.add_argument("--fixture", type=Path, default=None)
    p_dry.add_argument("--list-url", default=None)
    p_dry.add_argument(
        "--all-years",
        action="store_true",
        help="Scansiona archivio circolari dogane (tutte le pagine anno)",
    )

    p_run = sub.add_parser("run", help="Download limitato PDF ADM")
    p_run.add_argument(
        "--max",
        type=int,
        default=1,
        help="Max tentativi download (>=1). Default 1. --max 0 = errore.",
    )
    p_run.add_argument("--fixture", type=Path, default=None)
    p_run.add_argument("--output-dir", type=Path, default=None)
    p_run.add_argument("--list-url", default=None)
    p_run.add_argument("--no-resume", action="store_true")
    p_run.add_argument(
        "--all-years",
        action="store_true",
        help="Scarica da archivio circolari dogane (tutte le pagine anno)",
    )

    args = parser.parse_args(argv)
    cfg = Config()
    if getattr(args, "list_url", None):
        cfg.list_url = args.list_url
    if getattr(args, "output_dir", None):
        cfg.output_dir = args.output_dir

    if args.cmd == "dry-run":
        return dry_run(cfg, fixture=args.fixture, all_years=args.all_years)
    if args.cmd == "run":
        if int(args.max) < 1:
            print('{"error":"--max deve essere >= 1","max":%s}' % args.max)
            return 2
        return run_download(
            cfg,
            max_attempts=args.max,
            fixture=args.fixture,
            resume=not args.no_resume,
            all_years=args.all_years,
        )
    parser.error(f"comando sconosciuto: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
