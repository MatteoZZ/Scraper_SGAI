"""Curia fiscale = directory CELLAR 4.10 + B-10 (PDF/PDFA)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.scraper_eurlex.config import Config
from scripts.scraper_eurlex.runner import dry_run, run_download


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scraper Curia materie fiscali (directory 4.10 + B-10, PDF/PDFA)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_dry = sub.add_parser("dry-run")
    p_dry.add_argument("--sample", type=int, default=10)
    p_run = sub.add_parser("run", help="Scarica TUTTA la giurisprudenza fiscale directory")
    p_run.add_argument("--max", type=int, default=None)
    p_run.add_argument("--no-resume", action="store_true")
    p_run.add_argument("--output-dir", type=Path, default=None)

    args = parser.parse_args(argv)
    cfg = Config()
    cfg.caselaw_only = True
    cfg.name_prefix = "CURIA"
    cfg.mode = "curia_directory"
    # pagine più piccole = SPARQL più leggero sul Cellar (spesso in timeout)
    cfg.page_size = 25
    # solo ITA: il giro ENG dopo ITA è quasi sempre vuoto (stessi work) e perde 15+ min
    cfg.languages = ("ITA",)
    root = Path(__file__).resolve().parent
    cfg.output_dir = getattr(args, "output_dir", None) or (root / "downloads_out")
    cfg.checkpoint_path = root / ".checkpoint.json"

    if args.cmd == "dry-run":
        return dry_run(cfg, sample=max(0, int(args.sample)))
    if args.cmd == "run":
        if args.max is not None and int(args.max) < 1:
            print('{"error":"--max se usato deve essere >= 1"}')
            return 2
        return run_download(cfg, max_attempts=args.max, resume=not args.no_resume)
    return 2


if __name__ == "__main__":
    sys.exit(main())
