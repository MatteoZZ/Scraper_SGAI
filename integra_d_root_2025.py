#!/usr/bin/env python3
"""
Usa la lista D:\\Sentenza_*_2025.pdf (root) + eventuali (n),
aggiorna cache skip e cancella doppioni da downloads_mef.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

LIST_FILE = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\_d_root_2025_list.txt")
LIST_PAREN = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\_d_root_2025_paren_list.txt")
DOWNLOADS_MEF = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\downloads_mef")
PKG = Path(r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713\dati")
CACHE_2025 = PKG / "cache_nomi_base_2025.txt"
CACHE_ALL = PKG / "cache_nomi_base.txt"
LOCAL_SKIP_2025 = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\mef_skip_from_d_2025.txt")
LOCAL_SKIP_ALL = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\mef_skip_from_d_all.txt")
PROCESSED = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\processed_mef_downloads.json")

STEM_RE = re.compile(r"^(sentenza_[a-z]\d{2}_\d+_2025)$", re.I)


def normalize_name(filename: str) -> str | None:
    stem = Path(filename).stem.strip().lower()
    stem = re.sub(r"\s*\(\d+\)$", "", stem).strip()
    m = STEM_RE.match(stem)
    return m.group(1).lower() if m else None


def load_lines(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        ln.strip().lower()
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if ln.strip()
    }


def write_sorted(path: Path, names: set[str]) -> None:
    path.write_text("\n".join(sorted(names)) + ("\n" if names else ""), encoding="utf-8")


def refresh_dir_lists() -> None:
    # lista principale
    subprocess.run(
        f'dir /b "D:\\Sentenza_*_2025.pdf" > "{LIST_FILE}"',
        shell=True,
        check=False,
    )
    # varianti con (1) (2) — cmd glob limitato; prova pattern comuni
    subprocess.run(
        f'dir /b "D:\\Sentenza_*_2025 (*).pdf" > "{LIST_PAREN}" 2>nul',
        shell=True,
        check=False,
    )


def main() -> None:
    print("Aggiorno liste dir D:\\ ...")
    refresh_dir_lists()

    keys: set[str] = set()
    for path in (LIST_FILE, LIST_PAREN):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            k = normalize_name(line)
            if k:
                keys.add(k)

    print(f"Nomi unici 2025 da D:\\ root: {len(keys)}")
    if not keys:
        raise SystemExit("Nessun nome 2025 trovato — abort")

    # merge con eventuale skip precedente (Old\\Downloads_M)
    old_skip = load_lines(LOCAL_SKIP_2025)
    keys |= {k for k in old_skip if k.endswith("_2025")}
    write_sorted(LOCAL_SKIP_2025, keys)
    print(f"Scritto {LOCAL_SKIP_2025} ({len(keys)})")

    # all skip: aggiungi i 2025
    all_skip = load_lines(LOCAL_SKIP_ALL)
    before_all = len(all_skip)
    all_skip |= keys
    write_sorted(LOCAL_SKIP_ALL, all_skip)
    print(f"mef_skip_from_d_all: {before_all} -> {len(all_skip)}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if CACHE_2025.exists():
        shutil.copy2(CACHE_2025, CACHE_2025.with_suffix(CACHE_2025.suffix + f".bak_{ts}"))
    old25 = load_lines(CACHE_2025)
    merged25 = old25 | keys
    write_sorted(CACHE_2025, merged25)
    print(f"cache_nomi_base_2025: {len(old25)} -> {len(merged25)} (+{len(merged25 - old25)})")

    if CACHE_ALL.exists():
        shutil.copy2(CACHE_ALL, CACHE_ALL.with_suffix(CACHE_ALL.suffix + f".bak_{ts}"))
        old_all = load_lines(CACHE_ALL)
        merged_all = old_all | keys
        write_sorted(CACHE_ALL, merged_all)
        print(f"cache_nomi_base: {len(old_all)} -> {len(merged_all)} (+{len(merged_all - old_all)})")

    processed: set[str] = set()
    if PROCESSED.exists():
        data = json.loads(PROCESSED.read_text(encoding="utf-8"))
        processed = {str(x).strip().lower() for x in data.get("processed", [])}
    before_p = len(processed)
    processed |= keys
    PROCESSED.write_text(
        json.dumps(
            {
                "processed": sorted(processed),
                "last_update": datetime.now().isoformat(),
                "note": "merged 2025 from D:\\ root Sentenza_*_2025.pdf",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"processed: {before_p} -> {len(processed)}")

    deleted = 0
    kept = 0
    examples = []
    if DOWNLOADS_MEF.exists():
        for p in DOWNLOADS_MEF.glob("Sentenza_*_2025.pdf"):
            stem = re.sub(r"\s*\(\d+\)$", "", p.stem.strip().lower())
            if stem in keys:
                p.unlink()
                deleted += 1
                if len(examples) < 15:
                    examples.append(p.name)
            else:
                kept += 1
    for e in examples:
        print(f"  DEL {e}")
    if deleted > 15:
        print(f"  ... +{deleted - 15} altri")
    print(f"downloads_mef: eliminati={deleted}, restano_2025~={kept}")
    print("OK")


if __name__ == "__main__":
    main()
