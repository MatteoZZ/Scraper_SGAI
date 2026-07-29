#!/usr/bin/env python3
"""Monitor terminale download MEF — barre scaricati/min e falliti/min (totale + per worker)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, List, Tuple

BASE = Path(__file__).resolve().parent
LOG_PATH = BASE / "log_download_mef.txt"

TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
CTX_RE = re.compile(r"\[([^\]]+)\]")
OK_RE = re.compile(r"OK SCARICATO\b", re.I)
FAIL_RE = re.compile(r"Errore download dettaglio", re.I)
WORKER_KNOWN = ("S1A", "S1B", "S2A", "S2B", "S1", "S2", "Q1", "Q2", "Q3", "Q4", "RUN")


@dataclass
class WorkerState:
    worker: str
    periodo: str = "—"
    materia: str = "—"
    pagina: str = "—"
    stato: str = "idle"
    last_ts: datetime | None = None


@dataclass
class Event:
    ts: datetime
    worker: str
    kind: str  # "ok" | "fail"


@dataclass
class MonitorState:
    events: Deque[Event] = field(default_factory=deque)
    workers: Dict[str, WorkerState] = field(default_factory=dict)
    log_offset: int = 0


def parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_worker_ctx(tag: str) -> Tuple[str, str, str, str]:
    """Da 'S1|2025|S1 gen-giu|D040|p.56' -> worker, periodo, materia, pagina."""
    parts = tag.split("|")
    worker = parts[0] if parts else "RUN"
    periodo = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "—")
    materia = parts[3] if len(parts) > 3 else "—"
    pagina = parts[4].replace("p.", "p.") if len(parts) > 4 else "—"
    if pagina.startswith("p."):
        pagina = pagina
    return worker, periodo, materia, pagina


def infer_stato(msg: str) -> str | None:
    m = msg.lower()
    if "ok scaricato" in m:
        return "scarica…"
    if "403" in m or "akamai" in m:
        return "Akamai 403"
    if "attendo link visualizza" in m:
        return "attesa tabella"
    if "attendo ricerca manuale" in m:
        return "ricerca manuale"
    if "crash browser" in m or "crashed" in m:
        return "crash browser"
    if "ricerca ok" in m:
        return "ricerca OK"
    if "pagina" in m and "nuovi=" in m:
        if "gia_locali=10" in m.replace(" ", ""):
            return "skip locali"
        return "elabora pagina"
    if "browser pronto" in m:
        return "avvio"
    if "fine sessione" in m or "sessione playwright chiusa" in m:
        return "fermo"
    return None


def load_checkpoint_stats() -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    mapping = {
        "S1": BASE / "mef_download_checkpoint_s1.json",
        "S2": BASE / "mef_download_checkpoint_s2.json",
        "Q1": BASE / "mef_download_checkpoint_q1.json",
        "Q2": BASE / "mef_download_checkpoint_q2.json",
        "Q3": BASE / "mef_download_checkpoint_q3.json",
        "Q4": BASE / "mef_download_checkpoint_q4.json",
    }
    for worker, path in mapping.items():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stats = data.get("stats") or {}
            out[worker] = (
                int(stats.get("downloaded", 0)),
                int(stats.get("failed", 0)),
            )
        except Exception:
            pass
    return out


def worker_alive(worker: str) -> bool:
    locks = {
        "S1": BASE / "mef_download_s1.lock",
        "S2": BASE / "mef_download_s2.lock",
        "Q1": BASE / "mef_download_q1.lock",
        "Q2": BASE / "mef_download_q2.lock",
        "Q3": BASE / "mef_download_q3.lock",
        "Q4": BASE / "mef_download_q4.lock",
    }
    lock = locks.get(worker)
    if not lock or not lock.exists():
        return False
    try:
        pid = int(lock.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def ingest_line(state: MonitorState, line: str) -> None:
    line = line.rstrip("\n")
    if not line.strip():
        return
    ts_m = TS_RE.match(line)
    if not ts_m:
        return
    ts = parse_ts(ts_m.group(1))
    if ts is None:
        return

    ctx_tags = CTX_RE.findall(line)
    worker = "RUN"
    periodo = materia = pagina = "—"
    for tag in ctx_tags:
        if "|" in tag or tag in WORKER_KNOWN:
            worker, periodo, materia, pagina = parse_worker_ctx(tag)
            break
        if tag in WORKER_KNOWN:
            worker = tag

    body = line[ts_m.end() :].strip()
    if body.startswith("["):
        body = CTX_RE.sub("", body, count=1).strip()

    ws = state.workers.setdefault(worker, WorkerState(worker=worker))
    ws.last_ts = ts
    if materia != "—":
        ws.materia = materia
    if periodo != "—":
        ws.periodo = periodo
    if pagina != "—":
        ws.pagina = pagina

    stato = infer_stato(body)
    if stato:
        ws.stato = stato

    if OK_RE.search(body):
        state.events.append(Event(ts=ts, worker=worker, kind="ok"))
    elif FAIL_RE.search(body):
        state.events.append(Event(ts=ts, worker=worker, kind="fail"))


def read_new_lines(state: MonitorState, *, log_path: Path = LOG_PATH) -> None:
    if not log_path.exists():
        return
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(state.log_offset)
        for line in fh:
            ingest_line(state, line)
        state.log_offset = fh.tell()


def bootstrap_log(state: MonitorState, *, log_path: Path = LOG_PATH, tail_bytes: int = 400_000) -> None:
    """Carica la coda iniziale dall'ultima parte del log."""
    if not log_path.exists():
        return
    size = log_path.stat().st_size
    start = max(0, size - tail_bytes)
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(start)
        if start > 0:
            fh.readline()
        for line in fh:
            ingest_line(state, line)
        state.log_offset = fh.tell()


def prune_events(state: MonitorState, window: timedelta) -> None:
    cutoff = datetime.now() - window
    while state.events and state.events[0].ts < cutoff:
        state.events.popleft()


def rates_in_window(state: MonitorState, window: timedelta) -> Tuple[Dict[str, float], Dict[str, float]]:
    cutoff = datetime.now() - window
    ok: Dict[str, int] = defaultdict(int)
    fail: Dict[str, int] = defaultdict(int)
    for ev in state.events:
        if ev.ts < cutoff:
            continue
        if ev.kind == "ok":
            ok[ev.worker] += 1
        else:
            fail[ev.worker] += 1
    minutes = window.total_seconds() / 60.0
    ok_pm = {w: c / minutes for w, c in ok.items()}
    fail_pm = {w: c / minutes for w, c in fail.items()}
    return ok_pm, fail_pm


def _console_unicode() -> bool:
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def _glyphs() -> dict[str, str]:
    if _console_unicode():
        return {
            "pipe": "│",
            "eq": "═",
            "dash": "─",
            "on": "●",
            "off": "○",
            "fill": "█",
            "empty": "░",
        }
    return {
        "pipe": "|",
        "eq": "=",
        "dash": "-",
        "on": "*",
        "off": "o",
        "fill": "#",
        "empty": ".",
    }


def bar(value: float, max_val: float, width: int = 14, *, fill: str = "#", empty: str = ".") -> str:
    if max_val <= 0:
        filled = 0
    else:
        filled = min(width, max(0, int(round(value / max_val * width))))
    return fill * filled + empty * (width - filled)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def render(
    state: MonitorState,
    *,
    window_sec: int,
    scale: float,
    bar_width: int,
    ck_stats: Dict[str, Tuple[int, int]] | None = None,
) -> str:
    window = timedelta(seconds=window_sec)
    prune_events(state, window)
    ok_pm, fail_pm = rates_in_window(state, window)

    active_workers = sorted(
        {w for w in list(state.workers) + list(ok_pm) + list(fail_pm) if w != "RUN"},
        key=lambda w: (0 if w.startswith("S") else 1, w),
    )
    if not active_workers:
        active_workers = [w for w in ("S1", "S2") if state.workers.get(w) or worker_alive(w)]

    total_ok = sum(ok_pm.values())
    total_fail = sum(fail_pm.values())

    peak = max([scale, total_ok, total_fail, *ok_pm.values(), *fail_pm.values(), 1.0])
    g = _glyphs()
    ck_stats = ck_stats or {}
    session_ok = sum(ok for ok, _ in ck_stats.values())
    session_fail = sum(fail for _, fail in ck_stats.values())

    lines: List[str] = []
    w = 62
    now = datetime.now().strftime("%H:%M:%S")
    lines.append(f" MEF DOWNLOAD  {g['pipe']} {now}")
    lines.append(g["eq"] * w)
    lines.append(
        f" TOTALE        scaricati/min  {bar(total_ok, peak, bar_width, fill=g['fill'], empty=g['empty'])}  {total_ok:5.1f}"
    )
    lines.append(
        f"               falliti/min    {bar(total_fail, peak, bar_width, fill=g['fill'], empty=g['empty'])}  {total_fail:5.1f}"
    )
    lines.append(f"               sessione       OK: {session_ok:,}   FAIL: {session_fail:,}")
    lines.append(g["dash"] * w)

    if not active_workers:
        lines.append(" (nessun worker attivo — avvia S1/S2 o attendi log)")
    else:
        for worker in active_workers:
            ws = state.workers.get(worker, WorkerState(worker=worker))
            ok_r = ok_pm.get(worker, 0.0)
            fail_r = fail_pm.get(worker, 0.0)
            alive = g["on"] if worker_alive(worker) else g["off"]
            port = {"S1": "9222", "S2": "9223", "Q1": "9224", "Q2": "9225", "Q3": "9226", "Q4": "9227"}.get(
                worker, "—"
            )
            label = f"{ws.materia} {ws.pagina}" if ws.materia != "—" else ws.stato
            w_ok, w_fail = ck_stats.get(worker, (0, 0))
            lines.append(
                f" {alive} {worker} :{port}  scaricati/min  {bar(ok_r, peak, bar_width, fill=g['fill'], empty=g['empty'])}  {ok_r:5.1f}  ({label})"
            )
            lines.append(
                f"               falliti/min    {bar(fail_r, peak, bar_width, fill=g['fill'], empty=g['empty'])}  {fail_r:5.1f}  {ws.stato}"
                f"   tot {w_ok}/{w_fail}"
            )

    lines.append(g["eq"] * w)
    lines.append(f" finestra: {window_sec}s   {g['on']}=lock attivo   log: {LOG_PATH.name}")
    lines.append(" Ctrl+C per uscire")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor MEF con barre OK/min e FAIL/min")
    parser.add_argument("--interval", type=float, default=2.0, help="Refresh secondi (default 2)")
    parser.add_argument("--window", type=int, default=60, help="Finestra rate in secondi (default 60)")
    parser.add_argument("--scale", type=float, default=12.0, help="Scala barre (PDF/min di riferimento)")
    parser.add_argument("--bar-width", type=int, default=14, help="Larghezza barre")
    parser.add_argument("--log", default=str(LOG_PATH), help="Percorso log")
    args = parser.parse_args()

    try:
        if sys.platform == "win32":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    log_path = Path(args.log)
    state = MonitorState()
    bootstrap_log(state, log_path=log_path)

    try:
        while True:
            read_new_lines(state, log_path=log_path)
            ck_stats = load_checkpoint_stats()

            clear_screen()
            print(render(state, window_sec=args.window, scale=args.scale, bar_width=args.bar_width, ck_stats=ck_stats))
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor chiuso.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
