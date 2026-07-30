"""CLI scraper Italgiure SN-Cassazione (Civile sez. 5 + Sezioni Unite)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .runner import dry_run, run_download


def _parse_sezioni(raw: str) -> tuple[str, ...]:
    value = (raw or "all").strip().lower()
    if value in {"all", "5+u", "5u"}:
        return ("5", "U")
    if value in {"5", "quinta", "q"}:
        return ("5",)
    if value in {"u", "unite", "su", "sezioni-unite", "sezioni_unite"}:
        return ("U",)
    raise argparse.ArgumentTypeError(
        "--sezione deve essere: all | 5 | U (default all = Quinta+Unite)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scraper Italgiure Civile sez. Quinta + Sezioni Unite (locale, no upload SGAI)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--fixture", type=Path, default=None)
    # Opzioni avanzate (NON usare se vuoi tutto il corpus Quinta+Unite)
    common.add_argument(
        "--sezione",
        type=_parse_sezioni,
        default=("5", "U"),
        help=argparse.SUPPRESS,
    )
    common.add_argument("--anno", default=None, help=argparse.SUPPRESS)

    p_dry = sub.add_parser(
        "dry-run",
        parents=[common],
        help="Conta + campione metadati Solr, nessun download",
    )
    p_dry.add_argument(
        "--sample",
        type=int,
        default=20,
        help="Quanti documenti mostrare nel campione (default 20)",
    )

    p_run = sub.add_parser(
        "run",
        parents=[common],
        help="Scarica TUTTI i PDF Civile Quinta + Sezioni Unite",
    )
    p_run.add_argument(
        "--max",
        type=int,
        default=None,
        help="Solo test: limita i tentativi. Senza --max scarica TUTTO (~61k).",
    )
    p_run.add_argument("--output-dir", type=Path, default=None)
    p_run.add_argument("--no-resume", action="store_true")

    args = parser.parse_args(argv)
    cfg = Config()
    cfg.sezioni = tuple(args.sezione)
    cfg.anno = str(args.anno) if args.anno else None
    if getattr(args, "output_dir", None):
        cfg.output_dir = args.output_dir

    if args.cmd == "dry-run":
        return dry_run(cfg, fixture=args.fixture, sample=max(0, int(args.sample)))
    if args.cmd == "run":
        max_attempts = args.max
        if max_attempts is not None and int(max_attempts) < 1:
            print('{"error":"--max se usato deve essere >= 1","max":%s}' % max_attempts)
            return 2
        return run_download(
            cfg,
            max_attempts=max_attempts,
            fixture=args.fixture,
            resume=not args.no_resume,
        )
    parser.error(f"comando sconosciuto: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
