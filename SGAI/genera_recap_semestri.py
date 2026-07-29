#!/usr/bin/env python3
"""Tabella sentenze 2025 per materia x semestre (S1 gen-giu / S2 lug-dic) dal log MEF."""
from __future__ import annotations

import csv
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "log_download_mef.txt"
OUT_CSV = BASE / "mef_recap_semestri_2025.csv"
OUT_MD = BASE / "mef_recap_semestri_2025.md"

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

RE_OK = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*\[(S[12])\|[^\]]*\|([A-Z]\d{3})[^\]]*\]\s*"
    r"RICERCA OK -> ~([\d.]+) sul portale"
)
RE_PAG = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*\[(S[12])\|[^\]]*\|([A-Z]\d{3})\|p\.\d+\]\s*"
    r"PAGINA .*sul_portale~([\d.]+)"
)


def parse() -> dict[tuple[str, str], tuple[int, str]]:
    """(semestre, materia) -> (totale_portale, timestamp ultimo)."""
    latest: dict[tuple[str, str], tuple[int, str]] = {}
    if not LOG.exists():
        return latest
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_OK.search(line) or RE_PAG.search(line)
        if not m:
            continue
        ts, sem, mat, tot_s = m.group(1), m.group(2), m.group(3), m.group(4)
        tot = int(tot_s.replace(".", ""))
        k = (sem, mat)
        if k not in latest or ts >= latest[k][1]:
            latest[k] = (tot, ts)
    return latest


def main() -> None:
    latest = parse()
    rows = []
    tot_s1 = tot_s2 = 0
    known_s1 = known_s2 = 0
    for mat in MATERIA_KEYS:
        s1 = latest.get(("S1", mat))
        s2 = latest.get(("S2", mat))
        n1 = s1[0] if s1 else None
        n2 = s2[0] if s2 else None
        if n1 is not None:
            tot_s1 += n1
            known_s1 += 1
        if n2 is not None:
            tot_s2 += n2
            known_s2 += 1
        rows.append(
            {
                "codice": mat,
                "materia": MATERIE[mat],
                "s1_gen_giu": n1 if n1 is not None else "",
                "s2_lug_dic": n2 if n2 is not None else "",
                "totale": (n1 or 0) + (n2 or 0) if (n1 is not None or n2 is not None) else "",
            }
        )

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["codice", "materia", "s1_gen_giu", "s2_lug_dic", "totale"],
        )
        w.writeheader()
        w.writerows(rows)
        w.writerow(
            {
                "codice": "",
                "materia": "TOTALE (materie con dato)",
                "s1_gen_giu": tot_s1,
                "s2_lug_dic": tot_s2,
                "totale": tot_s1 + tot_s2,
            }
        )

    def fmt(n):
        return f"{n:,}".replace(",", ".") if isinstance(n, int) else (n if n != "" else "—")

    lines = [
        "# Sentenze MEF 2025 per materia e semestre",
        "",
        "Fonte: ultimo `RICERCA OK` / `sul_portale` in `log_download_mef.txt` (non e' un dump ufficiale completo).",
        "",
        f"Materie con dato: S1 {known_s1}/41 · S2 {known_s2}/41",
        "",
        "| Cod | Materia | S1 gen-giu | S2 lug-dic | Totale |",
        "|-----|---------|----------:|----------:|-------:|",
    ]
    for r in rows:
        n1 = r["s1_gen_giu"]
        n2 = r["s2_lug_dic"]
        tot = r["totale"]
        lines.append(
            f"| {r['codice']} | {r['materia']} | {fmt(n1) if n1 != '' else '—'} | "
            f"{fmt(n2) if n2 != '' else '—'} | {fmt(tot) if tot != '' else '—'} |"
        )
    lines += [
        f"| | **TOTALE (con dato)** | **{fmt(tot_s1)}** | **{fmt(tot_s2)}** | **{fmt(tot_s1 + tot_s2)}** |",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Scritto {OUT_CSV.name} e {OUT_MD.name}")
    print(f"S1 tot={tot_s1} ({known_s1} materie) | S2 tot={tot_s2} ({known_s2} materie)")


if __name__ == "__main__":
    main()
