"""Scan D: for PDFs / Sentenza_* and compare with local skip sources."""
from __future__ import annotations

import json
from pathlib import Path

ROOTS = [
    Path(r"D:\Old\Downloads_M"),
    Path(r"D:\_app\Downloads_M"),
    Path(r"D:\Old"),
    Path(r"D:\_app"),
]
CACHE_2025 = Path(
    r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713"
    r"\dati\cache_nomi_base_2025.txt"
)
CACHE_ALL = Path(
    r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713"
    r"\dati\cache_nomi_base.txt"
)
PROCESSED = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\processed_mef_downloads.json")
LOCAL_DL = Path(r"C:\Users\meko srl\.cursor\Matteo_folder\SGAI\downloads_mef")


def load_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        ln.strip().lower()
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if ln.strip()
    }


def stem_key(p: Path) -> str:
    return p.stem.strip().lower()


def main() -> None:
    print("=== Scan D: (puo' richiedere minuti) ===")
    all_pdf: list[Path] = []
    sentenza: list[Path] = []
    for root in ROOTS:
        if not root.exists():
            print(f"MISSING {root}")
            continue
        print(f"Scanning {root} ...")
        n = 0
        for p in root.rglob("*.pdf"):
            n += 1
            all_pdf.append(p)
            if p.name.lower().startswith("sentenza_"):
                sentenza.append(p)
            if n % 5000 == 0:
                print(f"  ... {n} pdf in {root}")
        print(f"  done {root}: pdf={n}")

    # dedupe by path
    all_pdf = list(dict.fromkeys(all_pdf))
    sentenza = list(dict.fromkeys(sentenza))
    print(f"\nTOTAL pdf under scanned roots: {len(all_pdf)}")
    print(f"TOTAL Sentenza_*.pdf: {len(sentenza)}")
    for p in sentenza[:8]:
        print(f"  ex: {p}")

    cache2025 = load_names(CACHE_2025)
    cache_all = load_names(CACHE_ALL)
    processed: set[str] = set()
    if PROCESSED.exists():
        data = json.loads(PROCESSED.read_text(encoding="utf-8"))
        processed = {str(x).strip().lower() for x in data.get("processed", [])}

    local_stems = {stem_key(p) for p in LOCAL_DL.glob("Sentenza_*.pdf")} if LOCAL_DL.exists() else set()

    if not sentenza:
        print("\nNessun Sentenza_*.pdf su D: nei path scansionati.")
        # sample non-SGAI names
        other = [p for p in all_pdf if not p.name.lower().startswith("sentenza_")]
        print(f"PDF non-SGAI trovati: {len(other)}")
        for p in other[:10]:
            print(f"  ex: {p.name}")
        return

    keys = {stem_key(p) for p in sentenza}
    in_2025 = keys & cache2025
    in_all = keys & cache_all
    in_proc = keys & processed
    in_local = keys & local_stems
    nowhere = keys - cache_all - processed - local_stems

    print("\n=== Confronto Sentenza_* su D: vs cache/skip ===")
    print(f"cache_nomi_base_2025: {len(cache2025)}")
    print(f"cache_nomi_base (full): {len(cache_all)}")
    print(f"processed_mef_downloads: {len(processed)}")
    print(f"downloads_mef locali: {len(local_stems)}")
    print(f"unici su D: {len(keys)}")
    print(f"  gia in cache 2025: {len(in_2025)}")
    print(f"  gia in cache full: {len(in_all)}")
    print(f"  gia in processed:  {len(in_proc)}")
    print(f"  gia in downloads_mef: {len(in_local)}")
    print(f"  NON in nessuna skip-list: {len(nowhere)}")
    print("\nNota: se i PDF su D: NON si chiamano Sentenza_CODICE_NUMERO_ANNO.pdf")
    print("lo script MEF non puo' riconoscerli come gia scaricati.")


if __name__ == "__main__":
    main()
