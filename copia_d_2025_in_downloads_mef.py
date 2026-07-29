#!/usr/bin/env python3
"""Copia Sentenza_*_2025.pdf da D:\\ (root) in downloads_mef, senza sovrascrivere."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

LIST_FILE = Path(__file__).resolve().parent / "_d_root_2025_list.txt"
DEST = Path(__file__).resolve().parent / "downloads_mef"
SRC_DIR = Path("D:/")


def refresh_list() -> list[str]:
    subprocess.run(
        f'dir /b "D:\\Sentenza_*_2025.pdf" > "{LIST_FILE}"',
        shell=True,
        check=False,
    )
    if not LIST_FILE.exists():
        return []
    return [
        ln.strip()
        for ln in LIST_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        if ln.strip()
    ]


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    print("Aggiorno lista D:\\Sentenza_*_2025.pdf ...")
    names = refresh_list()
    print(f"Da copiare (lista): {len(names)}")

    copied = 0
    skipped = 0
    missing = 0
    errors = 0

    for i, name in enumerate(names, 1):
        # solo nome file pulito (no path)
        name = Path(name).name
        if not re.match(r"(?i)^Sentenza_[A-Za-z]\d{2}_\d+_2025\.pdf$", name):
            # salta varianti strane; le (1) le gestiamo a parte se servono
            continue
        src = SRC_DIR / name
        dst = DEST / name
        if dst.exists():
            skipped += 1
        elif not src.exists():
            missing += 1
        else:
            try:
                shutil.copy2(src, dst)
                copied += 1
            except OSError as exc:
                errors += 1
                if errors <= 10:
                    print(f"  ERR {name}: {exc}")
        if i % 5000 == 0:
            print(f"  ... {i}/{len(names)} copied={copied} skipped={skipped}")

    print(
        f"FATTO: copied={copied} already_in_downloads_mef={skipped} "
        f"missing_on_D={missing} errors={errors}"
    )
    print(f"Dest: {DEST}")


if __name__ == "__main__":
    main()
