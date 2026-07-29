#!/usr/bin/env python3
"""
Genera recap per trimestre/materia e file pagine da log_download_mef.txt.

Output:
  mef_recap_2025.csv      — tutte le 41 materie x 4 trimestri
  mef_recap_2025.md       — stessa tabella in markdown
  mef_pagine_log.csv      — backfill da log se mancante (o integra)
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "log_download_mef.txt"
PAGE_LOG = BASE / "mef_pagine_log.csv"
RECAP_CSV = BASE / "mef_recap_2025.csv"
RECAP_MD = BASE / "mef_recap_2025.md"

MATERIE = {
    "D040": "Accertamento imposte",
    "E020": "Accise armonizzate - Alcole",
    "E010": "Accise armonizzate - Prodotti energetici",
    "E030": "Accise non armonizzate",
    "D010": "Agevolazioni",
    "B050": "Bollo",
    "H010": "Catasto",
    "B060": "Concessioni governative",
    "D070": "Condono",
    "D060": "Contenzioso",
    "C050": "Cosap",
    "G010": "Demanio",
    "B140": "Diritti e tributi indiretti vari",
    "F010": "Dogane",
    "C080": "Iciap",
    "A030": "Ilor",
    "B130": "Imposta erariale di trascrizione",
    "B070": "Imposta sulle assicurazioni",
    "C020": "Imu ex Ici",
    "B090": "Intrattenimenti",
    "C070": "Invim",
    "B030": "Ipotecarie e catastali",
    "C010": "Irap",
    "A020": "Ires (ex Irpeg)",
    "A010": "Irpef",
    "B010": "Iva",
    "C030": "Pubblicita e pubbliche affissioni",
    "H030": "Pubblicita immobiliare",
    "B110": "Radiodiffusioni",
    "D080": "Rapporti con l'AF",
    "B020": "Registro",
    "D030": "Rimborsi",
    "D020": "Riscossione",
    "H020": "Servizi estimativi (OMI)",
    "B040": "Successioni e donazioni",
    "C040": "Tarsu",
    "B080": "Tassa sui contratti di borsa",
    "B100": "Tasse automobilistiche",
    "C060": "Tosap",
    "C090": "Tributi locali vari",
    "D050": "Violazioni e sanzioni",
}
MATERIA_KEYS = list(MATERIE.keys())

TRIM_LABELS = ["Q1 gen-mar", "Q2 apr-giu", "Q3 lug-set", "Q4 ott-dic"]
TRIM_MAP = {
    ("01-01", "03-31"): 0,
    ("04-01", "06-30"): 1,
    ("07-01", "09-30"): 2,
    ("10-01", "12-31"): 3,
}


def parse_log() -> tuple[dict, list[dict]]:
    """Ritorna (stats per (trim, mat), righe pagine dal log)."""
    by_key = defaultdict(
        lambda: {
            "totale_portale": None,
            "nuovi": 0,
            "skip_local": 0,
            "skip_server": 0,
            "skip_meta": 0,
            "pagine": 0,
            "ricerca_ok": False,
            "toccata": False,
        }
    )
    page_rows: list[dict] = []

    if not LOG.exists():
        return by_key, page_rows

    cur_trim: int | None = None
    cur_mat: str | None = None
    cur_year = 2025

    ts_re = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        ts_m = ts_re.match(line)
        ts = ts_m.group(1) if ts_m else ""

        m = re.search(r"Ricerca anno=(\d{4}) (\d{2}-\d{2})-(\d{2}-\d{2}) materia=(\w+)", line)
        if m:
            cur_year = int(m.group(1))
            cur_trim = TRIM_MAP.get((m.group(2), m.group(3)))
            cur_mat = m.group(4)
            if cur_trim is not None:
                by_key[(cur_trim, cur_mat)]["toccata"] = True

        m_ok = re.search(r"Ricerca OK materia=(\w+): ~([\d.]+) risultati", line)
        if m_ok and cur_trim is not None and cur_mat == m_ok.group(1):
            k = (cur_trim, cur_mat)
            by_key[k]["ricerca_ok"] = True
            by_key[k]["totale_portale"] = int(m_ok.group(2).replace(".", ""))

        if "Pagina" in line and "fine:" in line and cur_trim is not None and cur_mat:
            sm = re.search(
                r"Pagina (-?\d+) fine: scaricati=(\d+) skip_server=(\d+) skip_local=(\d+)"
                r"(?: skip_meta=(\d+))?",
                line,
            )
            if sm:
                pag = int(sm.group(1))
                nuovi = int(sm.group(2))
                skip_s = int(sm.group(3))
                skip_l = int(sm.group(4))
                skip_m = int(sm.group(5)) if sm.group(5) else 0
                k = (cur_trim, cur_mat)
                by_key[k]["nuovi"] += nuovi
                by_key[k]["skip_server"] += skip_s
                by_key[k]["skip_local"] += skip_l
                by_key[k]["skip_meta"] += skip_m
                by_key[k]["pagine"] += 1
                page_rows.append(
                    {
                        "timestamp": ts,
                        "anno": cur_year,
                        "trimestre": cur_trim + 1,
                        "materia": cur_mat,
                        "pagina": pag,
                        "scaricati": nuovi,
                        "skip_server": skip_s,
                        "skip_local": skip_l,
                        "skip_meta": skip_m,
                    }
                )

    return by_key, page_rows


def stato_cell(d: dict) -> str:
    if not d["toccata"]:
        return "non iniziata"
    if d["toccata"] and not d["ricerca_ok"]:
        return "crash / no ricerca"
    if d["ricerca_ok"] and d["pagine"] == 0:
        return "ricerca OK, 0 pagine"
    tot = d["totale_portale"]
    elab = d["nuovi"] + d["skip_local"] + d["skip_server"]
    if tot and elab >= tot * 0.85:
        return "quasi completa"
    if d["pagine"] <= 2:
        return "iniziata"
    return "parziale"


def write_recap(by_key: dict) -> None:
    headers = [
        "trimestre",
        "codice",
        "materia",
        "sul_portale",
        "nuovi_scaricati",
        "gia_locali",
        "gia_sgai",
        "pagine_elaborate",
        "stato",
    ]
    rows_out = []
    for t in range(4):
        for code in MATERIA_KEYS:
            d = by_key[(t, code)]
            rows_out.append(
                {
                    "trimestre": TRIM_LABELS[t],
                    "codice": code,
                    "materia": MATERIE[code],
                    "sul_portale": d["totale_portale"] if d["totale_portale"] is not None else "",
                    "nuovi_scaricati": d["nuovi"],
                    "gia_locali": d["skip_local"],
                    "gia_sgai": d["skip_server"],
                    "pagine_elaborate": d["pagine"],
                    "stato": stato_cell(d),
                }
            )

    with RECAP_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        w.writerows(rows_out)

    lines = [
        "# Recap download MEF 2025 per trimestre e materia",
        "",
        f"Generato da `{LOG.name}`. Righe: 41 materie x 4 trimestri = 164 ricerche possibili.",
        "",
        "| Trim | Cod | Materia | Portale | Nuovi | Locali | SGAI | Pagine | Stato |",
        "|------|-----|---------|--------:|------:|-------:|-----:|-------:|-------|",
    ]
    for r in rows_out:
        port = r["sul_portale"] if r["sul_portale"] != "" else "-"
        nome = r["materia"][:28].replace("|", " ")
        lines.append(
            f"| {r['trimestre'][:2]} | {r['codice']} | {nome} | {port} | "
            f"{r['nuovi_scaricati']} | {r['gia_locali']} | {r['gia_sgai']} | "
            f"{r['pagine_elaborate']} | {r['stato']} |"
        )
    RECAP_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_page_log(page_rows: list[dict]) -> int:
    """Rigenera mef_pagine_log.csv da log_download_mef.txt."""
    if not page_rows:
        return 0
    headers = [
        "timestamp",
        "anno",
        "trimestre",
        "materia",
        "pagina",
        "scaricati",
        "skip_server",
        "skip_local",
        "skip_meta",
    ]
    with PAGE_LOG.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        w.writerows(page_rows)
    return len(page_rows)


def main() -> None:
    by_key, page_rows = parse_log()
    write_recap(by_key)
    n_pages = write_page_log(page_rows)
    pdf_count = len(list((BASE / "downloads_mef").glob("Sentenza_*_2025.pdf")))
    print(f"Scritto: {RECAP_CSV}")
    print(f"Scritto: {RECAP_MD}")
    if n_pages:
        print(f"Aggiornato: {PAGE_LOG} ({n_pages} righe da log)")
    else:
        print(f"{PAGE_LOG.name}: nessuna pagina nel log")
    print(f"PDF in downloads_mef/: {pdf_count}")


if __name__ == "__main__":
    main()
