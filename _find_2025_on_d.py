"""Find Sentenza_*_2025 on D: (root + known folders)."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

STEM_RE = re.compile(r"^(sentenza_[a-z]\d{2}_\d+_2025)$", re.I)


def norm(stem: str) -> str | None:
    s = stem.strip().lower()
    s = re.sub(r"\s+-\s+\d{4}-\d{2}-\d{2}t.*$", "", s, flags=re.I)
    s = re.sub(r"\s*\(\d+\)$", "", s).strip()
    m = STEM_RE.match(s)
    return m.group(1).lower() if m else None


def scan_dir(d: Path, recursive: bool) -> tuple[set[str], list[Path], Counter]:
    keys: set[str] = set()
    files: list[Path] = []
    years: Counter = Counter()
    it = d.rglob("Sentenza_*.pdf") if recursive else d.glob("Sentenza_*.pdf")
    for p in it:
        if not p.is_file():
            continue
        parts = p.stem.replace(" ", "_").split("_")
        if len(parts) >= 4:
            y = parts[-1].split("(")[0]
            if y.isdigit():
                years[y] += 1
        k = norm(p.stem)
        if k:
            keys.add(k)
            files.append(p)
    return keys, files, years


def main() -> None:
    root = Path("D:/")
    print("=== ROOT D: (non ricorsivo) ===")
    k, files, years = scan_dir(root, recursive=False)
    print("years:", years.most_common())
    print("unique 2025:", len(k), "files:", len(files))
    for p in files[:10]:
        print(" ", p.name)

    for d, rec in (
        (Path("D:/Old/Downloads_M"), True),
        (Path("D:/Old"), False),
        (Path("D:/_app"), True),
    ):
        if not d.exists():
            print(f"MISSING {d}")
            continue
        print(f"=== {d} recursive={rec} ===")
        k2, f2, y2 = scan_dir(d, recursive=rec)
        print("years sample:", y2.most_common(8))
        print("unique 2025:", len(k2), "files:", len(f2))
        for p in f2[:5]:
            print(" ", p)


if __name__ == "__main__":
    main()
