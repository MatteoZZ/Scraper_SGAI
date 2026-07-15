#!/usr/bin/env python3
"""
SGAI - Cache locale sentenze per worker/scraper

Scarica una volta il manifest compatto dal server e risponde in microsecondi a:
  "questa sentenza ce l'ho gia?"

Uso rapido:
  python sgai_sentenze_cache.py sync
  python sgai_sentenze_cache.py check V10 13747 2021
  python sgai_sentenze_cache.py check --name "Sentenza_V10_13747_2021.pdf"
  python sgai_sentenze_cache.py has V10 13747 2021   # exit 0=si, 1=no

Integrazione worker:
  from sgai_sentenze_cache import SentenzeCache
  cache = SentenzeCache()
  cache.sync()  # una volta all'avvio
  if cache.should_skip("V10", "13747", "2021"):
      continue  # non scaricare
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://sgailegal.com"
DATASET = "SENTENZE BANCA DATI MEF"
_PACKAGE_DIR = Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    """Usa la cache del pacchetto collega se presente, altrimenti exports/."""
    local_candidates = (
        _PACKAGE_DIR / "mia_cache",
        _PACKAGE_DIR / "dati",
        _PACKAGE_DIR.parent / "exports" / "sentenze_cache",
    )
    for candidate in local_candidates:
        if (candidate / "manifest.jsonl").exists():
            return candidate
        if (candidate / "cache_manifest.jsonl").exists():
            return candidate
    return _PACKAGE_DIR / "mia_cache"


DEFAULT_CACHE_DIR = _default_cache_dir()

# Import condiviso se disponibile nel repo
try:
    from api.utils.sentenze_utils import (
        build_manifest_index,
        lookup_in_manifest,
        nome_base,
        parse_sentenza_name,
        resolve_lookup_key,
    )
except ImportError:
    import re

    def nome_base(nome_file: str) -> str:
        base = (nome_file or "").strip()
        base = base.split(" - ")[0].strip()
        base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
        base = re.sub(r"\s*\(\d+\)\s*$", "", base)
        return re.sub(r"\s+", " ", base)

    def resolve_lookup_key(
        nome_file: str = "",
        nome_base_param: str = "",
        codice: str = "",
        numero: str = "",
        anno: str = "",
    ) -> str | None:
        if nome_base_param:
            return nome_base(nome_base_param)
        if nome_file:
            return nome_base(nome_file)
        if codice and numero and anno:
            return f"Sentenza_{codice.upper()}_{numero}_{anno}"
        return None

    def parse_sentenza_name(nome_file: str):
        base = nome_base(nome_file)
        m = re.match(r"^Sentenza_([A-Z0-9]+)_(\d+)_(\d{4})$", base, re.IGNORECASE)
        if not m:
            return None
        return {"nomeBase": base, "codice": m.group(1).upper(), "numero": m.group(2), "anno": m.group(3)}

    def build_manifest_index(rows):
        from collections import defaultdict
        groups = defaultdict(list)
        for row in rows:
            nome = (row.get("name") or row.get("nome_file") or "").strip()
            if not nome:
                continue
            base = nome_base(nome)
            groups[base.lower()].append({
                "id": row.get("id", ""),
                "name": nome,
                "nomeBase": base,
                "status": row.get("status", ""),
                "hasEmbedding": str(row.get("hasEmbedding", "")).lower() == "true",
            })
        index = {}
        for key, files in groups.items():
            has_done = any(f["status"] == "done" for f in files)
            has_emb = any(f["hasEmbedding"] for f in files)
            best = next((f for f in files if f["status"] == "done" and f["hasEmbedding"]), None)
            if not best:
                best = next((f for f in files if f["status"] == "done"), files[0] if files else None)
            index[key] = {
                "nomeBase": files[0]["nomeBase"],
                "copies": len(files),
                "isDuplicate": len(files) > 1,
                "hasDone": has_done,
                "hasEmbedding": has_emb,
                "bestFile": best,
                "files": files,
                "parsed": parse_sentenza_name(files[0]["name"]),
            }
        return {
            "totalFiles": sum(len(v) for v in groups.values()),
            "uniqueBase": len(index),
            "duplicateGroups": sum(1 for v in groups.values() if len(v) > 1),
            "index": index,
        }

    def lookup_in_manifest(manifest, lookup_key):
        entry = manifest.get("index", {}).get(lookup_key.lower())
        if not entry:
            return {
                "has": False,
                "nomeBase": lookup_key,
                "copies": 0,
                "isDuplicate": False,
                "hasDone": False,
                "hasEmbedding": False,
                "files": [],
                "bestFile": None,
                "download": {"skip": False, "reason": "non presente sul server"},
            }
        skip = entry["hasDone"] and entry["hasEmbedding"]
        return {
            "has": True,
            "nomeBase": entry["nomeBase"],
            "parsed": entry.get("parsed"),
            "copies": entry["copies"],
            "isDuplicate": entry["isDuplicate"],
            "hasDone": entry["hasDone"],
            "hasEmbedding": entry["hasEmbedding"],
            "bestFile": entry.get("bestFile"),
            "files": entry.get("files", []),
            "download": {
                "skip": skip,
                "reason": "gia presente con embedding" if skip else "presente",
            },
        }


def _fetch(url: str, timeout: int = 3600) -> bytes:
    req = Request(url, headers={"Accept": "*/*"})
    with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        return resp.read()


def _fetch_json(url: str, timeout: int = 120) -> dict:
    data = _fetch(url, timeout=timeout)
    payload = json.loads(data.decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(payload)
    return payload.get("data") or {}


class SentenzeCache:
    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE_DIR, base_url: str = API_BASE):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.keys_path = self.cache_dir / "nomi_base.txt"
        self.jsonl_path = self.cache_dir / "manifest.jsonl"
        self.meta_path = self.cache_dir / "meta.json"
        self._keys: set[str] | None = None
        self._index: dict | None = None

    def sync(self, force: bool = False, from_csv: str = "") -> dict:
        if from_csv:
            return self.build_from_csv(from_csv)

        summary_url = (
            f"{self.base_url}/v1/admin/sentenze-manifest?"
            f"{urlencode({'dataset': DATASET, 'format': 'summary', 'refresh': 'true' if force else 'false'})}"
        )
        summary = _fetch_json(summary_url, timeout=120)
        if not summary.get("found"):
            raise RuntimeError(f"Dataset non trovato: {DATASET}")

        keys_url = (
            f"{self.base_url}/v1/admin/sentenze-manifest?"
            f"{urlencode({'dataset': DATASET, 'format': 'keys'})}"
        )
        jsonl_url = (
            f"{self.base_url}/v1/admin/sentenze-manifest?"
            f"{urlencode({'dataset': DATASET, 'format': 'jsonl'})}"
        )

        print(f"[sync] Scarico keys ({summary.get('uniqueBase', '?')} nomi base)...")
        self.keys_path.write_bytes(_fetch(keys_url))
        print(f"[sync] Scarico jsonl...")
        self.jsonl_path.write_bytes(_fetch(jsonl_url))

        meta = {
            "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "baseUrl": self.base_url,
            "dataset": DATASET,
            "summary": summary,
            "files": {
                "keys": str(self.keys_path),
                "jsonl": str(self.jsonl_path),
            },
        }
        self.meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        self._keys = None
        self._index = None
        print(f"[sync] Cache salvata in {self.cache_dir}")
        return meta

    def build_from_csv(self, csv_path: str | Path) -> dict:
        import csv

        csv_path = Path(csv_path)
        rows = []
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                nome = (row.get("name") or row.get("nome_file") or "").strip()
                if not nome:
                    continue
                rows.append({
                    "id": row.get("id", ""),
                    "name": nome,
                    "status": row.get("status", ""),
                    "hasEmbedding": row.get("hasEmbedding", ""),
                })

        manifest = build_manifest_index(rows)
        with self.jsonl_path.open("w", encoding="utf-8") as out:
            for key in sorted(manifest["index"].keys()):
                entry = manifest["index"][key]
                best = entry.get("bestFile") or {}
                out.write(json.dumps({
                    "b": entry["nomeBase"],
                    "n": entry["copies"],
                    "d": entry["hasDone"],
                    "e": entry["hasEmbedding"],
                    "dup": entry["isDuplicate"],
                    "bf": best.get("name"),
                    "bs": best.get("status"),
                    "be": best.get("hasEmbedding"),
                }, ensure_ascii=False) + "\n")

        with self.keys_path.open("w", encoding="utf-8") as out:
            for key in sorted(manifest["index"].keys()):
                out.write(manifest["index"][key]["nomeBase"] + "\n")

        meta = {
            "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": str(csv_path),
            "dataset": DATASET,
            "summary": {
                "totalFiles": manifest["totalFiles"],
                "uniqueBase": manifest["uniqueBase"],
                "duplicateGroups": manifest["duplicateGroups"],
            },
            "files": {
                "keys": str(self.keys_path),
                "jsonl": str(self.jsonl_path),
            },
        }
        self.meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        self._keys = None
        self._index = None
        print(f"[build] Cache locale da CSV: {manifest['uniqueBase']:,} nomi base")
        return meta

    def _load_keys(self) -> set[str]:
        if self._keys is None:
            if not self.keys_path.exists():
                raise FileNotFoundError(f"Cache non trovata: {self.keys_path}. Esegui sync prima.")
            self._keys = {
                line.strip().lower()
                for line in self.keys_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        return self._keys

    def _load_index(self) -> dict:
        if self._index is None:
            if not self.jsonl_path.exists():
                raise FileNotFoundError(f"Cache non trovata: {self.jsonl_path}. Esegui sync prima.")
            index = {}
            with self.jsonl_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    key = item["b"].lower()
                    index[key] = {
                        "nomeBase": item["b"],
                        "copies": item.get("n", 1),
                        "isDuplicate": item.get("dup", False),
                        "hasDone": item.get("d", False),
                        "hasEmbedding": item.get("e", False),
                        "bestFile": {
                            "name": item.get("bf"),
                            "status": item.get("bs"),
                            "hasEmbedding": item.get("be", False),
                        },
                    }
            self._index = {"index": index}
        return self._index

    def has(
        self,
        codice: str = "",
        numero: str = "",
        anno: str = "",
        name: str = "",
        nome_base_param: str = "",
    ) -> bool:
        key = resolve_lookup_key(
            nome_file=name,
            nome_base_param=nome_base_param,
            codice=codice,
            numero=numero,
            anno=anno,
        )
        if not key:
            return False
        return key.lower() in self._load_keys()

    def check(
        self,
        codice: str = "",
        numero: str = "",
        anno: str = "",
        name: str = "",
        nome_base_param: str = "",
    ) -> dict:
        key = resolve_lookup_key(
            nome_file=name,
            nome_base_param=nome_base_param,
            codice=codice,
            numero=numero,
            anno=anno,
        )
        if not key:
            raise ValueError("Specificare codice+numero+anno oppure name")
        return lookup_in_manifest(self._load_index(), key)

    def should_skip(
        self,
        codice: str = "",
        numero: str = "",
        anno: str = "",
        name: str = "",
        nome_base_param: str = "",
    ) -> bool:
        return self.check(
            codice=codice,
            numero=numero,
            anno=anno,
            name=name,
            nome_base_param=nome_base_param,
        )["download"]["skip"]


def main() -> int:
    parser = argparse.ArgumentParser(description="SGAI cache sentenze per worker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="Scarica manifest dal server")
    p_sync.add_argument("--base-url", default=API_BASE)
    p_sync.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_sync.add_argument("--from-csv", default="", help="Costruisci cache da CSV locale")
    p_sync.add_argument("--force", action="store_true")

    for cmd in ("check", "has"):
        p = sub.add_parser(cmd, help="Verifica presenza sentenza")
        p.add_argument("codice", nargs="?", default="")
        p.add_argument("numero", nargs="?", default="")
        p.add_argument("anno", nargs="?", default="")
        p.add_argument("--name", default="")
        p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))

    args = parser.parse_args()
    cache = SentenzeCache(cache_dir=args.cache_dir if hasattr(args, "cache_dir") else DEFAULT_CACHE_DIR)

    if args.cmd == "sync":
        cache = SentenzeCache(cache_dir=args.cache_dir, base_url=args.base_url)
        try:
            if args.from_csv:
                cache.build_from_csv(args.from_csv)
            else:
                cache.sync(force=args.force)
            return 0
        except (HTTPError, RuntimeError, FileNotFoundError) as exc:
            print(f"ERRORE sync: {exc}", file=sys.stderr)
            return 1

    result = cache.check(
        codice=args.codice,
        numero=args.numero,
        anno=args.anno,
        name=args.name,
    )
    if args.cmd == "has":
        print("SI" if result["has"] else "NO")
        return 0 if result["has"] else 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
