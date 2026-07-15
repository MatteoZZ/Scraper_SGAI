#!/usr/bin/env python3
"""
Calcola fino a che periodo siete coperti confrontando il portale MEF con la cache SGAI.

Input CSV (header flessibile):
  corte,numero,anno,datdep
  CGT 2° Lombardia,1205,2025,25-05-2025

Date accettate: DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, YYYYMMDD

Esempio:
  python copertura_periodo.py --input sentenze_portale.csv --anno 2025
  python copertura_periodo.py --input sentenze_portale.csv --anno 2025 --output-dir report_copertura
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from portal_to_filename import parse_portal_row
from sgai_sentenze_cache import DEFAULT_CACHE_DIR, SentenzeCache


def _normalize_corte(raw: str) -> str:
    text = (raw or "").strip()
    text = text.replace("CGT_1_", "CGT 1° ").replace("CGT_2_", "CGT 2° ")
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_datdep(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None

    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    m = re.match(r"^(\d{2})[/.-](\d{2})[/.-](\d{4})$", text)
    if m:
        d, mth, y = m.groups()
        return date(int(y), int(mth), int(d))

    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    return None


def _field_map(headers: list[str]) -> dict[str, str]:
    return {name.strip().lower(): name for name in headers}


def _pick(fields: dict[str, str], row: dict, *candidates: str) -> str:
    for key in candidates:
        if key in fields:
            return (row.get(fields[key]) or "").strip()
    return ""


@dataclass
class RowItem:
    corte: str
    numero: str
    anno: str
    datdep: date | None
    codice: str
    nome_base: str
    presente: bool
    skip: bool


@dataclass
class CorteStats:
    corte: str
    codice: str
    totale: int = 0
    presenti: int = 0
    mancanti: int = 0
    con_data: int = 0
    copertura_continua_fino: date | None = None
    ultima_presente: date | None = None
    prima_mancante: date | None = None
    buchi_prima_della_fine: int = 0
    items: list[RowItem] = field(default_factory=list)


def load_rows(path: Path, anno_filter: str = "") -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV senza header: {path}")
        fields = _field_map(reader.fieldnames)

        for raw in reader:
            corte = _normalize_corte(_pick(
                fields, raw,
                "corte", "autorita_emittente", "autorità emittente", "autorita",
            ))
            numero = _pick(fields, raw, "numero", "numdec", "numero provvedimento")
            anno = _pick(fields, raw, "anno")
            datdep_raw = _pick(
                fields, raw,
                "datdep", "data_deposito", "data deposito", "data_deposito",
                "datadep", "data",
            )

            if not corte or not numero or not anno:
                continue
            if anno_filter and anno != anno_filter:
                continue

            meta = parse_portal_row(numero, anno, corte)
            if not meta.get("ok"):
                continue

            rows.append({
                "corte": corte,
                "numero": str(numero),
                "anno": str(anno),
                "datdep": _parse_datdep(datdep_raw),
                "codice": meta["codice"],
                "nomeBase": meta["nomeBase"],
            })
    return rows


def analyze(rows: list[dict], cache: SentenzeCache) -> tuple[list[RowItem], dict[str, CorteStats]]:
    items: list[RowItem] = []
    by_corte: dict[str, CorteStats] = {}

    for row in rows:
        result = cache.check(
            codice=row["codice"],
            numero=row["numero"],
            anno=row["anno"],
        )
        presente = bool(result.get("has"))
        skip = bool(result.get("download", {}).get("skip"))

        item = RowItem(
            corte=row["corte"],
            numero=row["numero"],
            anno=row["anno"],
            datdep=row["datdep"],
            codice=row["codice"],
            nome_base=row["nomeBase"],
            presente=presente,
            skip=skip,
        )
        items.append(item)

        key = row["corte"]
        if key not in by_corte:
            by_corte[key] = CorteStats(corte=key, codice=row["codice"])
        stats = by_corte[key]
        stats.items.append(item)
        stats.totale += 1
        if presente:
            stats.presenti += 1
        else:
            stats.mancanti += 1
        if item.datdep:
            stats.con_data += 1

    for stats in by_corte.values():
        dated = [it for it in stats.items if it.datdep]
        dated.sort(key=lambda it: (it.datdep, int(it.numero) if it.numero.isdigit() else it.numero))

        present_dated = [it for it in dated if it.presente]
        missing_dated = [it for it in dated if not it.presente]

        if present_dated:
            stats.ultima_presente = max(it.datdep for it in present_dated if it.datdep)
        if missing_dated:
            stats.prima_mancante = min(it.datdep for it in missing_dated if it.datdep)

        # Copertura continua dall'inizio periodo (senza buchi)
        stats.copertura_continua_fino = None
        for it in dated:
            if it.presente:
                stats.copertura_continua_fino = it.datdep
            else:
                break

        if stats.ultima_presente and missing_dated:
            stats.buchi_prima_della_fine = sum(
                1 for it in missing_dated
                if it.datdep and it.datdep <= stats.ultima_presente
            )

    return items, by_corte


def write_outputs(
    items: list[RowItem],
    by_corte: dict[str, CorteStats],
    out_dir: Path,
    anno: str,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    corte_csv = out_dir / f"copertura_per_corte_{anno or 'tutti'}.csv"
    with corte_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "corte", "codice", "totale_portale", "presenti", "mancanti",
                "con_data", "copertura_continua_fino", "ultima_presente",
                "prima_mancante", "buchi_nel_periodo",
            ],
        )
        writer.writeheader()
        for stats in sorted(by_corte.values(), key=lambda s: s.corte):
            writer.writerow({
                "corte": stats.corte,
                "codice": stats.codice,
                "totale_portale": stats.totale,
                "presenti": stats.presenti,
                "mancanti": stats.mancanti,
                "con_data": stats.con_data,
                "copertura_continua_fino": _fmt_date(stats.copertura_continua_fino),
                "ultima_presente": _fmt_date(stats.ultima_presente),
                "prima_mancante": _fmt_date(stats.prima_mancante),
                "buchi_nel_periodo": stats.buchi_prima_della_fine,
            })

    mancanti_csv = out_dir / f"mancanti_{anno or 'tutti'}.csv"
    with mancanti_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["corte", "codice", "numero", "anno", "datdep", "nome_base"],
        )
        writer.writeheader()
        for it in sorted(
            (x for x in items if not x.presente),
            key=lambda x: (x.corte, x.datdep or date.min, x.numero),
        ):
            writer.writerow({
                "corte": it.corte,
                "codice": it.codice,
                "numero": it.numero,
                "anno": it.anno,
                "datdep": _fmt_date(it.datdep),
                "nome_base": it.nome_base,
            })

    summary = {
        "anno": anno or "tutti",
        "totalePortale": len(items),
        "presenti": sum(1 for x in items if x.presente),
        "mancanti": sum(1 for x in items if not x.presente),
        "corti": len(by_corte),
        "senzaDataDeposito": sum(1 for x in items if not x.datdep),
        "cortiSenzaData": [s.corte for s in by_corte.values() if s.con_data == 0],
    }

    riepilogo_json = out_dir / f"copertura_riepilogo_{anno or 'tutti'}.json"
    riepilogo_json.write_text(
        json.dumps(
            {
                "summary": summary,
                "perCorte": [
                    {
                        "corte": s.corte,
                        "codice": s.codice,
                        "totale": s.totale,
                        "presenti": s.presenti,
                        "mancanti": s.mancanti,
                        "coperturaContinuaFino": _fmt_date(s.copertura_continua_fino),
                        "ultimaPresente": _fmt_date(s.ultima_presente),
                        "primaMancante": _fmt_date(s.prima_mancante),
                        "buchiNelPeriodo": s.buchi_prima_della_fine,
                    }
                    for s in sorted(by_corte.values(), key=lambda x: x.corte)
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "corte_csv": str(corte_csv),
        "mancanti_csv": str(mancanti_csv),
        "riepilogo_json": str(riepilogo_json),
        "summary": summary,
    }


def _fmt_date(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


def print_report(by_corte: dict[str, CorteStats], summary: dict) -> None:
    print("=" * 72)
    print("  COPERTURA PERIODO - PORTALE MEF vs CACHE SGAI")
    print("=" * 72)
    print(f"  Anno filtro:           {summary.get('anno')}")
    print(f"  Righe portale:         {summary.get('totalePortale', 0):>8,}")
    print(f"  Gia sul server:        {summary.get('presenti', 0):>8,}")
    print(f"  Mancanti:              {summary.get('mancanti', 0):>8,}")
    print(f"  Senza data deposito:   {summary.get('senzaDataDeposito', 0):>8,}")
    print("=" * 72)
    print()
    print("  Per corte (ordinate per nome):")
    print(f"  {'CORTE':<28} {'PRES':>5} {'MAN':>5}  {'CONTINUA FINO':<14} {'ULTIMA PRES.':<14}")
    print("  " + "-" * 68)

    for stats in sorted(by_corte.values(), key=lambda s: s.corte):
        print(
            f"  {stats.corte[:28]:<28} {stats.presenti:>5} {stats.mancanti:>5}  "
            f"{_fmt_date(stats.copertura_continua_fino):<14} {_fmt_date(stats.ultima_presente):<14}"
        )

    print()
    print("Legenda:")
    print("  CONTINUA FINO  = ultima data senza buchi dall'inizio del periodo")
    print("  ULTIMA PRES.   = sentenza piu recente gia sul server (anche con buchi)")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copertura temporale portale vs cache SGAI")
    parser.add_argument("--input", required=True, help="CSV portale con corte,numero,anno,datdep")
    parser.add_argument("--anno", default="2025", help="Filtra anno provvedimento (default: 2025)")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--output-dir", default="report_copertura")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERRORE: file non trovato: {input_path}", file=sys.stderr)
        return 1

    rows = load_rows(input_path, anno_filter=args.anno)
    if not rows:
        with input_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            raw_count = sum(1 for _ in reader)
        if raw_count == 0:
            print(
                f"ERRORE: {input_path} contiene solo l'header, nessuna sentenza.\n"
                "Esegui prima log_ricerche.py e attendi che salvi righe nel CSV.",
                file=sys.stderr,
            )
        else:
            print(
                f"ERRORE: {raw_count} righe lette ma nessuna valida per anno={args.anno}.\n"
                "Servono colonne corte,numero,anno (+ datdep consigliata).",
                file=sys.stderr,
            )
        return 1

    without_date = sum(1 for r in rows if not r["datdep"])
    if without_date:
        print(
            f"ATTENZIONE: {without_date} righe senza datdep — "
            "la copertura per periodo sara incompleta.",
            file=sys.stderr,
        )

    cache = SentenzeCache(cache_dir=args.cache_dir)
    items, by_corte = analyze(rows, cache)
    files = write_outputs(items, by_corte, Path(args.output_dir), args.anno)
    print_report(by_corte, files["summary"])

    print("File generati:")
    print(f"  {files['corte_csv']}")
    print(f"  {files['mancanti_csv']}")
    print(f"  {files['riepilogo_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
