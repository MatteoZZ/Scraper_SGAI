#!/usr/bin/env python3
"""
Indicizza TUTTI i Sentenza_*.pdf su D:\\Old\\Downloads_M (nomi normalizzati)
e li unisce alla cache di skip del downloader.

Per il 2025: se su D: non ce ne sono, non elimina nulla da downloads_mef.
Se ce ne sono, elimina i duplicati da downloads_mef.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

D_ROOT = Path(r"D:\Old\Downloads_M")
DOWNLOADS_MEF = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\downloads_mef")
PKG = Path(r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713\dati")
CACHE_ALL = PKG / "cache_nomi_base.txt"
CACHE_2025 = PKG / "cache_nomi_base_2025.txt"
LOCAL_SKIP_ALL = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\mef_skip_from_d_all.txt")
LOCAL_SKIP_2025 = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\mef_skip_from_d_2025.txt")
PROCESSED = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\processed_mef_downloads.json")

STEM_RE = re.compile(
    r"^(sentenza_[a-z]\d{2}_\d+_\d{4})(?:\s*\(\d+\))?$",
    re.IGNORECASE,
)


def normalize_stem(stem: str) -> str | None:
    s = stem.strip().lower()
    # togli timestamp tipo " - 2024-12-23T..."
    s = re.sub(r"\s+-\s+\d{4}-\d{2}-\d{2}t.*$", "", s, flags=re.I)
    m = STEM_RE.match(s)
    if m:
        return m.group(1)
    s2 = re.sub(r"\s*\(\d+\)$", "", s).strip()
    if STEM_RE.match(s2):
        return STEM_RE.match(s2).group(1)
    return None


def load_lines(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        ln.strip().lower()
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if ln.strip()
    }


def write_sorted(path: Path, names: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(names)) + ("\n" if names else ""), encoding="utf-8")


def main() -> None:
    print(f"Indicizzo {D_ROOT} ...")
    all_keys: set[str] = set()
    keys_2025: set[str] = set()
    n = 0
    for p in D_ROOT.rglob("Sentenza_*.pdf"):
        n += 1
        if n % 50000 == 0:
            print(f"  ... {n}")
        key = normalize_stem(p.stem)
        if not key:
            continue
        all_keys.add(key)
        if key.endswith("_2025"):
            keys_2025.add(key)
    print(f"File Sentenza_* visti: {n}")
    print(f"Nomi unici (tutti gli anni): {len(all_keys)}")
    print(f"Nomi unici anno 2025: {len(keys_2025)}")

    write_sorted(LOCAL_SKIP_ALL, all_keys)
    write_sorted(LOCAL_SKIP_2025, keys_2025)
    print(f"Scritti {LOCAL_SKIP_ALL.name} e {LOCAL_SKIP_2025.name}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if CACHE_ALL.exists():
        shutil.copy2(CACHE_ALL, CACHE_ALL.with_suffix(CACHE_ALL.suffix + f".bak_{ts}"))
        old = load_lines(CACHE_ALL)
        merged = old | all_keys
        write_sorted(CACHE_ALL, merged)
        print(f"cache_nomi_base.txt: {len(old)} -> {len(merged)} (+{len(merged - old)})")

    if CACHE_2025.exists():
        shutil.copy2(CACHE_2025, CACHE_2025.with_suffix(CACHE_2025.suffix + f".bak_{ts}"))
        old25 = load_lines(CACHE_2025)
        merged25 = old25 | keys_2025
        write_sorted(CACHE_2025, merged25)
        print(f"cache_nomi_base_2025.txt: {len(old25)} -> {len(merged25)} (+{len(merged25 - old25)})")

    processed: set[str] = set()
    if PROCESSED.exists():
        data = json.loads(PROCESSED.read_text(encoding="utf-8"))
        processed = {str(x).strip().lower() for x in data.get("processed", [])}
    before = len(processed)
    processed |= all_keys
    PROCESSED.write_text(
        json.dumps(
            {
                "processed": sorted(processed),
                "last_update": datetime.now().isoformat(),
                "note": "merged names from D:\\Old\\Downloads_M",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"processed_mef_downloads: {before} -> {len(processed)}")

    deleted = 0
    kept = 0
    if DOWNLOADS_MEF.exists() and keys_2025:
        for p in DOWNLOADS_MEF.glob("Sentenza_*_2025.pdf"):
            key = normalize_stem(p.stem)
            if key and key in keys_2025:
                p.unlink()
                deleted += 1
                if deleted <= 20:
                    print(f"  DEL {p.name}")
            else:
                kept += 1
        print(f"downloads_mef 2025: eliminati={deleted}, restano~{kept}")
    else:
        print(
            "Nessuna sentenza 2025 su D: -> nessuna eliminazione da downloads_mef "
            f"(li restano tutti i PDF 2025 locali)."
        )


if __name__ == "__main__":
    main()
