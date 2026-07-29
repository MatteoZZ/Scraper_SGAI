from pathlib import Path
import re

root = Path("D:/")
pat = re.compile(r"^Sentenza_[A-Za-z]\d{2}_\d+_2025(?: \(\d+\))?\.pdf$", re.I)
n = 0
ex = []
years = {}
# Non-recursive: only files directly on D:\
for p in root.iterdir():
    if not p.is_file() or p.suffix.lower() != ".pdf":
        continue
    name = p.name
    if not name.lower().startswith("sentenza_"):
        continue
    # year = last _token before .pdf
    stem = p.stem
    # strip (1)
    stem_clean = re.sub(r"\s*\(\d+\)$", "", stem)
    parts = stem_clean.split("_")
    if len(parts) >= 4 and parts[-1].isdigit():
        y = parts[-1]
        years[y] = years.get(y, 0) + 1
    if pat.match(name) or (len(parts) >= 4 and parts[-1] == "2025"):
        n += 1
        if len(ex) < 12:
            ex.append(name)

print("years_on_D_root:", sorted(years.items(), key=lambda x: -x[1])[:10])
print("count_2025_on_D_root:", n)
for e in ex:
    print(" ", e)
