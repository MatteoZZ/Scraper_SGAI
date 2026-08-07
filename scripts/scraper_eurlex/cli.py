from __future__ import annotations

import argparse
import json
import sys
import time

from .config import Config
from .runner import dry_run, run_download
from scripts.scraper_common.shard import validate_workers, worker_checkpoint_path


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
    p_run.add_argument(
        "--no-keepalive",
        action="store_true",
        help="Non ripartire in automatico dopo network_pause (DNS/500)",
    )
    p_run.add_argument(
        "--keepalive-wait",
        type=int,
        default=60,
        help="Secondi di attesa prima del riparto automatico (default 60)",
    )
    p_run.add_argument(
        "--workers",
        type=int,
        default=1,
        help="N worker paralleli (stesso output, checkpoint separati). Default 1",
    )
    p_run.add_argument(
        "--worker-id",
        type=int,
        default=0,
        help="Id di questo worker 0..workers-1 (es. terminale A: 0, B: 1)",
    )

    args = parser.parse_args(argv)
    cfg = Config()
    cfg.caselaw_only = bool(getattr(args, "caselaw_only", False))

    if args.cmd == "dry-run":
        return dry_run(cfg, sample=max(0, int(args.sample)))
    if args.cmd == "run":
        if args.max is not None and int(args.max) < 1:
            print('{"error":"--max se usato deve essere >= 1"}')
            return 2
        try:
            validate_workers(int(args.worker_id), int(args.workers))
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}))
            return 2
        cfg.workers = int(args.workers)
        cfg.worker_id = int(args.worker_id)
        cfg.checkpoint_path = worker_checkpoint_path(
            cfg.checkpoint_path, worker_id=cfg.worker_id, workers=cfg.workers
        )
        resume = not args.no_resume
        if args.no_keepalive:
            return run_download(cfg, max_attempts=args.max, resume=resume)
        # DNS/Cellar 500: pausa e riparti da soli (altrimenti sembra che "si fermi sempre")
        round_i = 0
        while True:
            round_i += 1
            code = run_download(cfg, max_attempts=args.max, resume=resume)
            if code != 4:
                return code
            wait = max(15, int(args.keepalive_wait))
            print(
                f"[EURLEX] network_pause: attendo {wait}s e riparto "
                f"(round {round_i}, Ctrl+C per stop)",
                flush=True,
            )
            time.sleep(wait)
            resume = True
    return 2


if __name__ == "__main__":
    sys.exit(main())
