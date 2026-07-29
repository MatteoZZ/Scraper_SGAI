#!/usr/bin/env python3
"""Elenco pagine da ritentare da mef_pagine_log.csv (+ log testuale per interruzioni).

Considera l'ULTIMA riga per (periodo, materia, pagina). Non ritenta se la pagina
e' gia stata elaborata (tutti skip locali/server o scaricati).
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "log_download_mef.txt"
PAGE_LOG = BASE / "mef_pagine_log.csv"
OUT_CSV = BASE / "mef_pagine_retry.csv"
OUT_TXT = BASE / "mef_pagine_retry.txt"
OUT_CMD = BASE / "mef_pagine_retry_cmds.ps1"

TRIM_MAP = {
    ("01-01", "03-31"): 1,
    ("04-01", "06-30"): 2,
    ("07-01", "09-30"): 3,
    ("10-01", "12-31"): 4,
}
SEM_MAP = {
    ("01-01", "06-30"): 1,
    ("07-01", "12-31"): 2,
}


def _int(row: dict, key: str, default: int = 0) -> int:
    try:
        return int(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def period_from_row(row: dict) -> tuple[str, int, str]:
    """(tipo, numero, etichetta) — tipo 'semestre' o 'trimestre'."""
    sem = _int(row, "semestre")
    periodo = (row.get("periodo") or "").strip()
    if sem in (1, 2):
        return "semestre", sem, f"S{sem}"
    if periodo.startswith("S1"):
        return "semestre", 1, "S1"
    if periodo.startswith("S2"):
        return "semestre", 2, "S2"
    trim = _int(row, "trimestre")
    if periodo.startswith("Q") and periodo[1:2].isdigit():
        return "trimestre", int(periodo[1]), periodo
    return "trimestre", trim, f"Q{trim}" if trim else "Q?"


def pagina_ok(row: dict) -> bool:
    """True se l'ultima elaborazione della pagina non richiede retry."""
    pag = _int(row, "pagina")
    if pag < 1:
        return False
    s = _int(row, "scaricati")
    sl = _int(row, "skip_local")
    ss = _int(row, "skip_server")
    sm = _int(row, "skip_meta")
    if s > 0:
        return True
    if sl >= 10 or ss >= 10:
        return True
    if sl + ss + s >= 10:
        return True
    if sm >= 10 and sl == 0 and ss == 0 and s == 0:
        return False
    if s == 0 and sl == 0 and ss == 0 and sm == 0:
        return False
    return sl + ss > 0


def motivo_retry(row: dict) -> str:
    sm = _int(row, "skip_meta")
    if sm >= 10:
        return "meta_fallita"
    return "vuota"


def normalize_csv_rows() -> dict[tuple, dict]:
    """Ultima riga per chiave (anno, tipo, periodo_n, materia, pagina)."""
    if not PAGE_LOG.exists():
        return {}
    last: dict[tuple, dict] = {}
    with PAGE_LOG.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row.get("materia"):
                continue
            pag = _int(row, "pagina")
            if pag < 1:
                continue
            tipo, periodo_n, label = period_from_row(row)
            anno = _int(row, "anno", 2025)
            key = (anno, tipo, periodo_n, row["materia"], pag)
            last[key] = {
                **row,
                "_tipo": tipo,
                "_periodo_n": periodo_n,
                "_label": label,
                "_anno": anno,
                "_pagina": pag,
            }
    return last


def parse_csv_retry() -> list[dict]:
    out: list[dict] = []
    for key, row in sorted(normalize_csv_rows().items()):
        if pagina_ok(row):
            continue
        tipo, periodo_n, label = row["_tipo"], row["_periodo_n"], row["_label"]
        out.append(
            {
                "motivo": motivo_retry(row),
                "anno": row["_anno"],
                "tipo": tipo,
                "periodo": periodo_n,
                "periodo_label": label,
                "materia": row["materia"],
                "pagina": row["_pagina"],
                "ultimo_evento": row.get("timestamp", ""),
                "scaricati": _int(row, "scaricati"),
                "skip_local": _int(row, "skip_local"),
                "skip_server": _int(row, "skip_server"),
                "skip_meta": _int(row, "skip_meta"),
            }
        )
    return out


def parse_log_incomplete() -> list[dict]:
    """Pagine avviate ma non chiuse (formato log vecchio e nuovo)."""
    if not LOG.exists():
        return []
    rows: list[dict] = []
    cur_tipo = "trimestre"
    cur_period = 0
    cur_mat = ""
    cur_year = 2025
    open_pages: dict[int, str] = {}
    ts_re = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

    def flush_open() -> None:
        for pag, ts in sorted(open_pages.items()):
            if pag > 0 and cur_mat and cur_period:
                rows.append(
                    {
                        "motivo": "interrotta",
                        "anno": cur_year,
                        "tipo": cur_tipo,
                        "periodo": cur_period,
                        "periodo_label": f"S{cur_period}" if cur_tipo == "semestre" else f"Q{cur_period}",
                        "materia": cur_mat,
                        "pagina": pag,
                        "ultimo_evento": ts,
                    }
                )
        open_pages.clear()

    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        ts_m = ts_re.match(line)
        ts = ts_m.group(1) if ts_m else ""

        m = re.search(
            r"\|(\d{4})\|([^|\]]+)\|(\w+)\].*RICERCA (?:inviata|OK)",
            line,
        )
        if m:
            flush_open()
            cur_year = int(m.group(1))
            cur_mat = m.group(3)
            periodo = m.group(2)
            if periodo.startswith("S1"):
                cur_tipo, cur_period = "semestre", 1
            elif periodo.startswith("S2"):
                cur_tipo, cur_period = "semestre", 2
            elif periodo.startswith("Q") and periodo[1:2].isdigit():
                cur_tipo, cur_period = "trimestre", int(periodo[1])
            continue

        m = re.search(r"Ricerca anno=(\d{4}) (\d{2}-\d{2})-(\d{2}-\d{2}) materia=(\w+)", line)
        if m:
            flush_open()
            cur_year = int(m.group(1))
            cur_mat = m.group(4)
            da, a = m.group(2), m.group(3)
            if (da, a) in SEM_MAP:
                cur_tipo, cur_period = "semestre", SEM_MAP[(da, a)]
            else:
                cur_tipo, cur_period = "trimestre", TRIM_MAP.get((da, a), 0)

        m_start = re.search(r"elaboro (\d+) righe", line)
        if m_start and cur_mat:
            ctx = re.search(r"\|p\.(\d+)\]", line)
            if ctx:
                open_pages[int(ctx.group(1))] = ts
            continue

        m_start_old = re.search(r"Pagina (\d+): (\d+) righe", line)
        if m_start_old and cur_mat:
            open_pages[int(m_start_old.group(1))] = ts

        m_end = re.search(r"PAGINA (-?\d+) ->", line)
        if m_end and cur_mat:
            open_pages.pop(int(m_end.group(1)), None)
            continue

        m_end_old = re.search(r"Pagina (-?\d+) fine:", line)
        if m_end_old and cur_mat:
            open_pages.pop(int(m_end_old.group(1)), None)

    flush_open()
    return rows


def merge_retries(csv_rows: list[dict], log_rows: list[dict]) -> list[dict]:
    """CSV ha priorita'; dal log solo se nessuna riga CSV indica pagina gia' OK."""
    latest = normalize_csv_rows()

    def any_csv_ok(anno: int, materia: str, pagina: int) -> bool:
        for key, row in latest.items():
            if key[0] == anno and key[3] == materia and key[4] == pagina and pagina_ok(row):
                return True
        return False

    by_key: dict[tuple, dict] = {}

    for row in csv_rows:
        key = (row["anno"], row["tipo"], row["periodo"], row["materia"], row["pagina"])
        by_key[key] = row

    for row in log_rows:
        if any_csv_ok(row["anno"], row["materia"], row["pagina"]):
            continue
        key = (row["anno"], row["tipo"], row["periodo"], row["materia"], row["pagina"])
        if key not in by_key or row["motivo"] == "interrotta":
            by_key[key] = row

    return sorted(
        by_key.values(),
        key=lambda r: (r["tipo"], int(r["periodo"]), r["materia"], int(r["pagina"])),
    )


def build_cmd(anno: int, tipo: str, periodo: int, materia: str, pages: list[int]) -> str:
    pag = ",".join(map(str, sorted(pages)))
    if tipo == "semestre":
        period_flag = f"--semestre {periodo}"
        prof = f".edge_profile_mef_s{periodo}"
        port = 9222 if periodo == 1 else 9223
    else:
        period_flag = f"--trimestre {periodo}"
        prof = f".edge_profile_mef_q{periodo}"
        port = 9223 + periodo  # Q1=9224 ... Q4=9227 (non collide con S1/S2)
    return (
        f"python download_mef_2025.py --year {anno} {period_flag} "
        f"--materia {materia} --pagine {pag} "
        f"--profile-dir {prof} --cdp-port {port}"
    )


def main() -> None:
    csv_rows = parse_csv_retry()
    log_rows = parse_log_incomplete()
    out = merge_retries(csv_rows, log_rows)

    headers = [
        "motivo",
        "anno",
        "tipo",
        "periodo",
        "periodo_label",
        "materia",
        "pagina",
        "ultimo_evento",
        "scaricati",
        "skip_local",
        "skip_server",
        "skip_meta",
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    by_group: dict[tuple, list[int]] = defaultdict(list)
    for r in out:
        by_group[(r["anno"], r["tipo"], int(r["periodo"]), r["materia"])].append(int(r["pagina"]))

    lines: list[str] = []
    cmds: list[str] = [
        "# Comandi retry pagine — un processo per riga (profilo/porta dedicati)",
        f"# Generato: {PAGE_LOG.name} + log testuale",
        "",
    ]
    for key in sorted(by_group.keys(), key=lambda k: (k[1], k[2], k[3])):
        anno, tipo, periodo, materia = key
        pages = sorted(set(by_group[key]))
        label = f"S{periodo}" if tipo == "semestre" else f"Q{periodo}"
        lines.append(f"{label} {materia}: {','.join(map(str, pages))}")
        cmds.append(f"# {label} {materia} — {len(pages)} pagine")
        cmds.append(build_cmd(anno, tipo, periodo, materia, pages))
        cmds.append("")

    OUT_TXT.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    OUT_CMD.write_text("\n".join(cmds) + "\n", encoding="utf-8")

    print(f"Scritto: {OUT_CSV} ({len(out)} pagine da ritentare)")
    print(f"Scritto: {OUT_TXT}")
    print(f"Scritto: {OUT_CMD}")
    for line in lines:
        print(f"  {line}")
    if not out:
        print("Nessuna pagina da ritentare — tutto elaborato o gia' in cache locale.")


if __name__ == "__main__":
    main()
