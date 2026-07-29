#!/usr/bin/env python3
"""
1) Raccoglie nomi Sentenza_*_2025 da D:\\Old\\Downloads_M (normalizza ' (1)' ecc.)
2) Aggiorna cache skip usata dal downloader
3) Elimina da downloads_mef i PDF gia' presenti su D:
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

D_ROOT = Path(r"D:\Old\Downloads_M")
DOWNLOADS_MEF = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\downloads_mef")
# Path hardcoded in download_mef_2025.py
CACHE_2025 = Path(
    r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713"
    r"\dati\cache_nomi_base_2025.txt"
)
CACHE_ALL = Path(
    r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713"
    r"\dati\cache_nomi_base.txt"
)
# Copia locale in SGAI (backup + uso futuro)
LOCAL_SKIP = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\mef_skip_from_d_2025.txt")
PROCESSED = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\processed_mef_downloads.json")

STEM_RE = re.compile(
    r"^(sentenza_[a-z]\d{2}_\d+_\d{4})(?:\s*\(\d+\))?$",
    re.IGNORECASE,
)


def normalize_stem(stem: str) -> str | None:
    s = stem.strip().lower()
    m = STEM_RE.match(s)
    if m:
        return m.group(1)
    # fallback: togli " (n)" finale
    s2 = re.sub(r"\s*\(\d+\)$", "", s).strip()
    if s2.startswith("sentenza_") and s2.count("_") >= 3 and s2.endswith("_2025"):
        return s2
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
    text = "\n".join(sorted(names)) + ("\n" if names else "")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    if not D_ROOT.exists():
        raise SystemExit(f"Manca {D_ROOT}")

    print(f"Scansione {D_ROOT} per Sentenza_*_2025.pdf ...")
    d_keys: set[str] = set()
    d_files_by_key: dict[str, Path] = {}
    n_pdf = 0
    for p in D_ROOT.rglob("*.pdf"):
        n_pdf += 1
        if n_pdf % 50000 == 0:
            print(f"  ... {n_pdf} pdf")
        name = p.name
        if not name.lower().startswith("sentenza_"):
            continue
        if "_2025" not in name.lower():
            continue
        key = normalize_stem(p.stem)
        if not key or not key.endswith("_2025"):
            continue
        d_keys.add(key)
        # tieni un path "pulito" preferito (senza (1))
        prev = d_files_by_key.get(key)
        if prev is None or " (" not in p.stem:
            d_files_by_key[key] = p

    print(f"PDF totali visti: {n_pdf}")
    print(f"Nomi unici 2025 su D:: {len(d_keys)}")

    LOCAL_SKIP.write_text("\n".join(sorted(d_keys)) + "\n", encoding="utf-8")
    print(f"Scritto {LOCAL_SKIP} ({len(d_keys)} nomi)")

    # Merge in cache_2025
    if CACHE_2025.exists():
        bak = CACHE_2025.with_suffix(
            CACHE_2025.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(CACHE_2025, bak)
        print(f"Backup cache: {bak.name}")
    old_cache = load_lines(CACHE_2025)
    merged = old_cache | d_keys
    added = merged - old_cache
    write_sorted(CACHE_2025, merged)
    print(f"cache_nomi_base_2025: {len(old_cache)} -> {len(merged)} (+{len(added)})")

    # Opzionale: aggiungi anche alla cache full (stessi nomi 2025)
    if CACHE_ALL.exists():
        bak_all = CACHE_ALL.with_suffix(
            CACHE_ALL.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(CACHE_ALL, bak_all)
        old_all = load_lines(CACHE_ALL)
        merged_all = old_all | d_keys
        write_sorted(CACHE_ALL, merged_all)
        print(
            f"cache_nomi_base (full): {len(old_all)} -> {len(merged_all)} "
            f"(+{len(merged_all - old_all)})"
        )

    # Merge in processed session
    import json

    processed: set[str] = set()
    if PROCESSED.exists():
        data = json.loads(PROCESSED.read_text(encoding="utf-8"))
        processed = {str(x).strip().lower() for x in data.get("processed", [])}
    before_p = len(processed)
    processed |= d_keys
    PROCESSED.write_text(
        json.dumps(
            {
                "processed": sorted(processed),
                "last_update": datetime.now().isoformat(),
                "note": "merged 2025 names from D:\\Old\\Downloads_M",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"processed_mef_downloads: {before_p} -> {len(processed)}")

    # Elimina duplicati da downloads_mef
    if not DOWNLOADS_MEF.exists():
        print(f"Nessuna cartella {DOWNLOADS_MEF}")
        return

    deleted = 0
    kept = 0
    for p in sorted(DOWNLOADS_MEF.glob("Sentenza_*.pdf")):
        key = normalize_stem(p.stem)
        if key and key in d_keys:
            p.unlink()
            deleted += 1
            if deleted <= 15:
                print(f"  DEL {p.name} (gia su D:)")
        else:
            kept += 1
    if deleted > 15:
        print(f"  ... altri {deleted - 15} eliminati")
    print(f"downloads_mef: eliminati={deleted}, restano={kept}")
    print("OK — da ora il downloader salta i 2025 presenti su D: (via cache).")


if __name__ == "__main__":
    main()
