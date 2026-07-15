#!/usr/bin/env python3
"""
Confronta una lista dal portale MEF con la cache SGAI e trova cosa manca.

Esempio:
  python trova_mancanti.py --input sentenze_portale.csv --output mancanti.csv

Formato input CSV (header obbligatorio):
  corte,numero,anno
  CGT 2° Lombardia,1205,2026
  CGT 1° Napoli,13747,2021

Oppure con colonne gia pronte:
  nome_base
  Sentenza_V70_1205_2026
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from portal_to_filename import build_filename, parse_portal_row
from sgai_sentenze_cache import SentenzeCache, DEFAULT_CACHE_DIR


def _load_portale_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV vuoto o senza header: {path}")

        fields = {name.strip().lower(): name for name in reader.fieldnames}
        for raw in reader:
            if "nome_base" in fields or "nomebase" in fields:
                key = fields.get("nome_base") or fields.get("nomebase")
                nome_base = (raw.get(key) or "").strip()
                if not nome_base:
                    continue
                rows.append({
                    "corte": "",
                    "numero": "",
                    "anno": "",
                    "nomeBase": nome_base,
                    "codice": "",
                    "nomeFile": f"{nome_base}.pdf" if not nome_base.lower().endswith(".pdf") else nome_base,
                    "ok": True,
                })
                continue

            corte_key = fields.get("corte") or fields.get("autorita_emittente") or fields.get("autorità emittente")
            numero_key = fields.get("numero") or fields.get("numdec")
            anno_key = fields.get("anno")
            if not corte_key or not numero_key or not anno_key:
                raise ValueError(
                    "Header atteso: corte,numero,anno oppure nome_base. "
                    f"Trovato: {reader.fieldnames}"
                )

            corte = (raw.get(corte_key) or "").strip().replace("CGT_1_", "CGT 1° ").replace("CGT_2_", "CGT 2° ")
            corte = corte.replace("_", " ")
            numero = (raw.get(numero_key) or "").strip()
            anno = (raw.get(anno_key) or "").strip()
            if not corte or not numero or not anno:
                continue

            meta = parse_portal_row(numero, anno, corte)
            rows.append({
                "corte": corte,
                "numero": numero,
                "anno": anno,
                **meta,
            })
    return rows


def analyze(rows: list[dict], cache: SentenzeCache) -> dict:
    presenti: list[dict] = []
    mancanti: list[dict] = []
    incompleti: list[dict] = []
    errori: list[dict] = []

    for row in rows:
        if not row.get("ok", True):
            errori.append({
                **row,
                "motivo": row.get("error", "mappatura corte non trovata"),
            })
            continue

        nome_base = row.get("nomeBase") or ""
        codice = row.get("codice") or ""
        numero = str(row.get("numero") or "")
        anno = str(row.get("anno") or "")

        if codice and numero and anno:
            result = cache.check(codice=codice, numero=numero, anno=anno)
        else:
            result = cache.check(nome_base_param=nome_base)

        item = {
            "corte": row.get("corte", ""),
            "numero": numero,
            "anno": anno,
            "codice": codice,
            "nomeBase": result.get("nomeBase") or nome_base,
            "nomeFile": row.get("nomeFile") or f"{nome_base}.pdf",
            "has": result.get("has", False),
            "skip": result.get("download", {}).get("skip", False),
            "reason": result.get("download", {}).get("reason", ""),
            "hasDone": result.get("hasDone", False),
            "hasEmbedding": result.get("hasEmbedding", False),
        }

        if not item["has"]:
            mancanti.append(item)
        elif item["skip"]:
            presenti.append(item)
        else:
            incompleti.append(item)

    return {
        "totaleInput": len(rows),
        "presenti": presenti,
        "mancanti": mancanti,
        "incompleti": incompleti,
        "errori": errori,
        "summary": {
            "totaleInput": len(rows),
            "presentiConEmbedding": len(presenti),
            "mancanti": len(mancanti),
            "presentiMaIncompleti": len(incompleti),
            "erroriMappatura": len(errori),
        },
    }


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Trova sentenze mancanti sul server SGAI")
    parser.add_argument("--input", required=True, help="CSV dal portale MEF o lista nome_base")
    parser.add_argument("--output", default="mancanti.csv", help="CSV output solo mancanti")
    parser.add_argument("--cache-dir", default="", help="Cartella cache (default: mia_cache)")
    parser.add_argument("--report", default="report_mancanti.json", help="Report JSON completo")
    parser.add_argument("--include-incompleti", action="store_true", help="Metti anche incompleti in mancanti.csv")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERRORE: file non trovato: {input_path}", file=sys.stderr)
        return 1

    cache = SentenzeCache(cache_dir=args.cache_dir or DEFAULT_CACHE_DIR)
    rows = _load_portale_rows(input_path)
    if not rows:
        print("ERRORE: nessuna riga valida nel CSV input", file=sys.stderr)
        return 1

    result = analyze(rows, cache)
    summary = result["summary"]

    out_rows = list(result["mancanti"])
    if args.include_incompleti:
        out_rows.extend(result["incompleti"])

    output_path = Path(args.output)
    _write_csv(
        output_path,
        out_rows,
        fieldnames=[
            "corte", "numero", "anno", "codice", "nomeBase", "nomeFile",
            "has", "skip", "reason", "hasDone", "hasEmbedding",
        ],
    )

    report_path = Path(args.report)
    report_path.write_text(
        json.dumps(
            {
                "input": str(input_path.resolve()),
                "cacheDir": str(cache.cache_dir.resolve()),
                "summary": summary,
                "mancanti": result["mancanti"][:20],
                "incompleti": result["incompleti"][:20],
                "errori": result["errori"][:20],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("=" * 60)
    print("  CONFRONTO PORTALE vs CACHE SGAI")
    print("=" * 60)
    print(f"  Righe input:                 {summary['totaleInput']:>8,}")
    print(f"  Gia presenti (skip=true):    {summary['presentiConEmbedding']:>8,}")
    print(f"  MANCANTI:                    {summary['mancanti']:>8,}")
    print(f"  Presenti ma incompleti:      {summary['presentiMaIncompleti']:>8,}")
    print(f"  Errori mappatura corte:      {summary['erroriMappatura']:>8,}")
    print("=" * 60)
    print(f"  File mancanti:  {output_path.resolve()}")
    print(f"  Report JSON:    {report_path.resolve()}")
    print()
    print("Legenda:")
    print("  MANCANTI        = non esistono sul server SGAI")
    print("  INCOMPLETI      = esistono ma senza embedding (puoi riscaricare)")
    print("  PRESENTI+SKIP   = gia ok, non scaricare")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
