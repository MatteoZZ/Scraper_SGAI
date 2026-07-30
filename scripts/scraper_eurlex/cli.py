from __future__ import annotations

import argparse
import sys

from .config import Config
from .runner import dry_run, run_download


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scraper EUR-Lex tema fiscalità (EuroVoc taxation) — locale, no upload"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dry = sub.add_parser("dry-run", help="Conta + campione SPARQL")
    p_dry.add_argument("--sample", type=int, default=10)
    p_dry.add_argument(
        "--caselaw-only",
        action="store_true",
        help="Solo CELEX settore 6 (giurisprudenza)",
    )

    p_run = sub.add_parser("run", help="Scarica TUTTI i PDF fiscalità (senza --max)")
    p_run.add_argument(
        "--max",
        type=int,
        default=None,
        help="Solo test: limita tentativi. Senza --max scarica tutto.",
    )
    p_run.add_argument("--no-resume", action="store_true")
    p_run.add_argument("--caselaw-only", action="store_true")

    args = parser.parse_args(argv)
    cfg = Config()
    cfg.caselaw_only = bool(getattr(args, "caselaw_only", False))

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
