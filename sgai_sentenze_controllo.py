#!/usr/bin/env python3
"""
SGAI — Controllo sentenze (tutto in uno)

Cosa fa:
  1. Accende l'EC2 se spenta
  2. Scarica il LISTONE COMPLETO con i nomi file come salvati sul server
     (es. Sentenza_V10_13747_2021.pdf, Sentenza_U01_1_2025(1).pdf)
  3. Analizza i DUPLICATI (stesso nome base con (1), (2), ecc.)
  4. Salva file pronti per il collega

Uso:
  python sgai_sentenze_controllo.py

Opzioni:
  --no-wake           non accende EC2
  --local-csv FILE    salta download, analizza CSV gia scaricato
  --output-dir DIR    cartella output
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import ssl
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WAKE_URL = "https://91k2hfw1n3.execute-api.eu-north-1.amazonaws.com/wake-up"
EC2_STATUS_URL = "https://r2hsvqju7dcd3m6ev5zjvqe3rq0ohgok.lambda-url.eu-north-1.on.aws/"
WAKE_TARGET = "SGAI-Production"
API_BASE = "https://sgailegal.com"
DATASET = "SENTENZE BANCA DATI MEF"

PROBE_PATHS = [
    "/v1/admin/sentenze-inventory?summary_only=true",
    "/v1/user/login",
]

CSV_FIELDS_LIGHT = [
    "nome_file",
    "nome_base",
    "status",
    "processed",
    "hasEmbedding",
    "chunk_num",
    "id",
]


def _request_json(url: str, method: str = "GET", body: dict | None = None, timeout: int = 30) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ec2_status() -> dict:
    try:
        return _request_json(EC2_STATUS_URL, timeout=25)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "instance_state": "unknown"}


def wake_ec2() -> dict:
    payload = {"force_start": True, "target_instance": WAKE_TARGET}
    try:
        return _request_json(WAKE_URL, method="POST", body=payload, timeout=90)
    except HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def api_ready(base_url: str, timeout: int = 15) -> bool:
    for path in PROBE_PATHS:
        try:
            url = f"{base_url.rstrip('/')}{path}"
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False


def wait_for_api(base_url: str, max_wait_sec: int = 300, poll_sec: int = 10) -> bool:
    deadline = time.time() + max_wait_sec
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if api_ready(base_url):
            print(f"  API pronta (tentativo {attempt})")
            return True
        print(f"  Attendo API... tentativo {attempt} ({poll_sec}s)")
        time.sleep(poll_sec)
    return False


def fetch_inventory_summary(base_url: str, dataset: str, timeout: int = 120) -> dict:
    qs = urlencode({"dataset": dataset, "summary_only": "true"})
    url = f"{base_url.rstrip('/')}/v1/admin/sentenze-inventory?{qs}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(payload)
    return payload.get("data") or {}


def download_csv(base_url: str, dataset: str, output: Path, timeout: int = 3600) -> None:
    params = {"dataset": dataset, "status": "all", "embedding": "all"}
    url = f"{base_url.rstrip('/')}/v1/admin/sentenze-export?{urlencode(params)}"
    req = Request(url, headers={"Accept": "text/csv"})
    with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp, output.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def nome_base(nome_file: str) -> str:
    """Nome logico senza .pdf e senza suffisso (1), (2)..."""
    base = (nome_file or "").strip()
    base = base.split(" - ")[0].strip()
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)
    base = re.sub(r"\s+", " ", base)
    return base


def ensure_server_ready(base_url: str, no_wake: bool, max_wait: int) -> dict:
    print("[1/5] Stato EC2...")
    status = ec2_status()
    state = (status.get("instance_state") or "unknown").lower()
    print(f"      Istanza: {status.get('instance_id', '?')} -> {state}")

    print("[2/5] Avvio server (se serve)...")
    if api_ready(base_url, timeout=10):
        print("      API gia raggiungibile.")
        return status

    if no_wake:
        print("      API non raggiungibile (--no-wake).")
        if not wait_for_api(base_url, max_wait_sec=30):
            raise RuntimeError("Server non raggiungibile")
        return status

    if state not in ("running", "pending"):
        print("      EC2 non running -> wake-up...")
        print(f"      Wake: {json.dumps(wake_ec2(), ensure_ascii=False)}")
    else:
        print("      EC2 running ma API giu -> wake-up...")
        wake_ec2()

    print(f"      Attendo API (max {max_wait}s)...")
    if not wait_for_api(base_url, max_wait_sec=max_wait):
        raise RuntimeError("API non pronta. Riprova o avvia da home.sgailegal.com")
    return status


def build_cache_files(rows: list[dict], out_dir: Path) -> dict[str, str]:
    """Genera cache veloce per worker: nomi base unici + manifest jsonl."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in rows:
        groups[item["nome_base"].lower()].append(item)

    keys_path = out_dir / "cache_nomi_base.txt"
    jsonl_path = out_dir / "cache_manifest.jsonl"
    with keys_path.open("w", encoding="utf-8") as f:
        for key in sorted(groups.keys()):
            f.write(groups[key][0]["nome_base"] + "\n")

    with jsonl_path.open("w", encoding="utf-8") as f:
        for key in sorted(groups.keys()):
            group = groups[key]
            has_done = any(r["status"] == "done" for r in group)
            has_emb = any(str(r["hasEmbedding"]).lower() == "true" for r in group)
            best = next(
                (r for r in group if r["status"] == "done" and str(r["hasEmbedding"]).lower() == "true"),
                next((r for r in group if r["status"] == "done"), group[0]),
            )
            f.write(json.dumps({
                "b": group[0]["nome_base"],
                "n": len(group),
                "d": has_done,
                "e": has_emb,
                "dup": len(group) > 1,
                "bf": best["nome_file"],
                "bs": best["status"],
                "be": str(best["hasEmbedding"]).lower() == "true",
            }, ensure_ascii=False) + "\n")

    return {
        "cache_nomi_base": str(keys_path),
        "cache_manifest": str(jsonl_path),
    }


def process_listone(full_csv: Path, out_dir: Path) -> dict:
    print("[4/5] Analisi nomi e duplicati...")

    rows = []
    exact_names: dict[str, list[str]] = defaultdict(list)
    base_groups: dict[str, list[dict]] = defaultdict(list)

    with full_csv.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nome = (row.get("name") or "").strip()
            if not nome:
                continue
            base = nome_base(nome)
            item = {
                "id": row.get("id", ""),
                "nome_file": nome,
                "nome_base": base,
                "status": row.get("status", ""),
                "processed": row.get("processed", ""),
                "hasEmbedding": row.get("hasEmbedding", ""),
                "chunk_num": row.get("chunk_num", ""),
            }
            rows.append(item)
            exact_names[nome.lower()].append(row.get("id", ""))
            base_groups[base.lower()].append(item)

    rows.sort(key=lambda r: r["nome_file"].lower())

    # listone leggero
    light_path = out_dir / "listone_sentenze.csv"
    with light_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS_LIGHT)
        writer.writeheader()
        writer.writerows(rows)

    # solo nomi, uno per riga
    nomi_path = out_dir / "listone_nomi.txt"
    with nomi_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(r["nome_file"] + "\n")

    # duplicati per nome_base (stessa sentenza con (1), (2)...)
    dup_base = {k: v for k, v in base_groups.items() if len(v) > 1}
    dup_exact = {k: v for k, v in exact_names.items() if len(v) > 1}

    dup_rows = []
    for base_key, group in sorted(dup_base.items(), key=lambda x: (-len(x[1]), x[0])):
        nomi = [g["nome_file"] for g in group]
        dup_rows.append({
            "tipo": "nome_base",
            "nome_base": group[0]["nome_base"],
            "occorrenze": len(group),
            "nomi_file": " | ".join(nomi),
        })

    dup_detail_path = out_dir / "duplicati_dettaglio.csv"
    with dup_detail_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["tipo", "nome_base", "nome_file", "id", "status", "hasEmbedding"])
        writer.writeheader()
        for base_key, group in sorted(dup_base.items()):
            for g in group:
                writer.writerow({
                    "tipo": "nome_base",
                    "nome_base": g["nome_base"],
                    "nome_file": g["nome_file"],
                    "id": g["id"],
                    "status": g["status"],
                    "hasEmbedding": g["hasEmbedding"],
                })
        for nome_key, ids in sorted(dup_exact.items()):
            for doc_id in ids:
                writer.writerow({
                    "tipo": "nome_esatto",
                    "nome_base": nome_key,
                    "nome_file": nome_key,
                    "id": doc_id,
                    "status": "",
                    "hasEmbedding": "",
                })

    dup_summary_path = out_dir / "duplicati_riepilogo.csv"
    with dup_summary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["tipo", "nome_base", "occorrenze", "nomi_file"])
        writer.writeheader()
        writer.writerows(dup_rows)

    cache_files = build_cache_files(rows, out_dir)
    print(f"      Cache worker: {Path(cache_files['cache_nomi_base']).name}")

    stats = {
        "totaleNomi": len(rows),
        "nomiUniciEsatti": len(exact_names),
        "nomiBaseUnici": len(base_groups),
        "gruppiDuplicatiNomeBase": len(dup_base),
        "fileCoinvoltiInDuplicati": sum(len(v) for v in dup_base.values()),
        "nomiEsattiDuplicati": len(dup_exact),
        "esempiNomi": [r["nome_file"] for r in rows[:5]],
        "esempiDuplicati": dup_rows[:10],
    }

    print(f"      Totale nomi: {stats['totaleNomi']:,}")
    print(f"      Gruppi duplicati (nome base): {stats['gruppiDuplicatiNomeBase']:,}")
    print(f"      File coinvolti in duplicati: {stats['fileCoinvoltiInDuplicati']:,}")

    return {
        "stats": stats,
        "files": {
            "listone_completo": str(full_csv),
            "listone_sentenze": str(light_path),
            "listone_nomi": str(nomi_path),
            "duplicati_dettaglio": str(dup_detail_path),
            "duplicati_riepilogo": str(dup_summary_path),
            **cache_files,
        },
    }


def _summary_from_rows(rows: list[dict]) -> dict:
    status_counts: dict[str, int] = defaultdict(int)
    with_emb = 0
    for row in rows:
        status = row.get("status") or row.get("run") or "unknown"
        status_counts[str(status)] += 1
        emb_val = row.get("hasEmbedding", row.get("has_embedding", ""))
        if str(emb_val).lower() == "true":
            with_emb += 1
    total = len(rows)
    done = status_counts.get("done", 0)
    return {
        "total": total,
        "parsed": {
            "done": done,
            "running": status_counts.get("running", 0),
            "unstart": status_counts.get("unstart", 0),
            "cancel": status_counts.get("cancel", 0),
            "fail": status_counts.get("fail", 0),
        },
        "embeddings": {
            "withEmbeddings": with_emb,
            "withoutEmbeddings": total - with_emb,
        },
    }


def print_final_report(summary: dict, analysis: dict, out_dir: Path) -> None:
    parsed = summary.get("parsed") or {}
    emb = summary.get("embeddings") or {}
    dup = analysis.get("stats") or {}

    print()
    print("=" * 62)
    print("  LISTONE SENTENZE SGAI")
    print("=" * 62)
    print(f"  Totale file sul server:    {dup.get('totaleNomi', summary.get('total', 0)):>10,}")
    print(f"  Nomi base unici:           {dup.get('nomiBaseUnici', 0):>10,}")
    print(f"  Gruppi duplicati:          {dup.get('gruppiDuplicatiNomeBase', 0):>10,}")
    print(f"  Parsate (done):            {parsed.get('done', 0):>10,}")
    print(f"  Con embedding:             {emb.get('withEmbeddings', 0):>10,}")
    print("=" * 62)
    print()
    print("  FILE PRINCIPALI (da dare al collega):")
    print(f"    listone_nomi.txt         -> tutti i nomi, uno per riga")
    print(f"    listone_sentenze.csv     -> nomi + stato + embedding")
    print(f"    listone_completo.csv     -> export completo dal server")
    print(f"    duplicati_riepilogo.csv  -> gruppi duplicati")
    print(f"    duplicati_dettaglio.csv  -> ogni file duplicato")
    print(f"    cache_nomi_base.txt    -> lookup veloce worker (nomi unici)")
    print(f"    cache_manifest.jsonl   -> dettaglio duplicati/stato per worker")
    print(f"    riepilogo.json           -> numeri + statistiche")
    print()
    print(f"  Cartella: {out_dir.resolve()}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="SGAI listone sentenze + duplicati")
    parser.add_argument("--base-url", default=API_BASE)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--no-wake", action="store_true")
    parser.add_argument("--local-csv", default="", help="Usa CSV gia scaricato, senza server")
    parser.add_argument("--max-wait", type=int, default=300)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir or f"sentenze_dati_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("SGAI - Listone sentenze + duplicati")
    print(f"Output: {out_dir.resolve()}")
    print()

    ec2 = {"instance_state": "skipped"}
    summary = {}

    try:
        if args.local_csv:
            full_csv = Path(args.local_csv)
            if not full_csv.exists():
                print(f"ERRORE: file non trovato: {full_csv}", file=sys.stderr)
                return 1
            print(f"[skip] Uso CSV locale: {full_csv}")
        else:
            ec2 = ensure_server_ready(args.base_url, args.no_wake, args.max_wait)

            print("[3/5] Download listone completo...")
            full_csv = out_dir / "listone_completo.csv"
            download_csv(args.base_url, args.dataset, full_csv)
            mb = full_csv.stat().st_size / (1024 * 1024)
            print(f"      Salvato: {full_csv.name} ({mb:.1f} MB)")

            print("      Lettura riepilogo...")
            inv = fetch_inventory_summary(args.base_url, args.dataset)
            summary = inv.get("summary") or {}

        if args.local_csv:
            full_csv = Path(args.local_csv)
            # copia in output se diversa
            dest = out_dir / "listone_completo.csv"
            if full_csv.resolve() != dest.resolve():
                dest.write_bytes(full_csv.read_bytes())

        analysis = process_listone(out_dir / "listone_completo.csv", out_dir)

        if not summary:
            dup_stats = analysis.get("stats") or {}
            summary = {
                "total": dup_stats.get("totaleNomi", 0),
                "parsed": {"done": 0},
                "embeddings": {"withEmbeddings": 0},
            }
            # Ricalcola done/embedding dal CSV leggero gia prodotto
            light_csv = out_dir / "listone_sentenze.csv"
            if light_csv.exists():
                summary = _summary_from_rows(list(csv.DictReader(
                    light_csv.open("r", encoding="utf-8", newline="")
                )))

        riepilogo = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "dataset": args.dataset,
            "ec2Status": ec2,
            "summary": summary,
            "duplicati": analysis["stats"],
            "files": analysis["files"],
        }
        (out_dir / "riepilogo.json").write_text(
            json.dumps(riepilogo, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print("[5/5] Fatto.")
        print_final_report(summary, analysis, out_dir)
        return 0

    except Exception as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
