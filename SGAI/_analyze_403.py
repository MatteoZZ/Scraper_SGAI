import re
from collections import Counter
from pathlib import Path
from datetime import datetime

log = Path(__file__).resolve().parent.joinpath("log_download_mef.txt").read_text(
    encoding="utf-8", errors="ignore"
).splitlines()

ts_re = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
page_re = re.compile(
    r"PAGINA (\d+) -> nuovi=(\d+) gia_sgai=(\d+) gia_locali=(\d+) meta=(\d+)"
)

events = []
for i, line in enumerate(log):
    m = ts_re.match(line)
    if not m:
        continue
    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    events.append((ts, i, line))


def classify(line: str) -> str | None:
    if "block-cooldown" in line or "Ritmo anti-Akamai" in line:
        return None
    if "403 a raffica" in line:
        return "raffica_counter"
    if "403 su Ricerca" in line:
        return "ricerca"
    if "403 su '>'" in line or "403 su '>'" in line:
        return "paginazione"
    if "search/submit HTTP 403" in line:
        return "submit_http"
    if "403 su search/submit" in line:
        return "submit_segnalato"
    if "Akamai 403" in line:
        return "akamai_msg"
    if "403" in line and ("WARN" in line or "search/submit" in line):
        return "other_403"
    return None


primary_kinds = {"ricerca", "paginazione", "submit_http", "submit_segnalato", "akamai_msg"}
primary = []
for idx, (ts, i, line) in enumerate(events):
    kind = classify(line)
    if kind is None or kind not in primary_kinds:
        continue
    last_page = None
    skip_streak = 0
    last_ricerca_ok = None
    for j in range(idx - 1, max(-1, idx - 250), -1):
        t2, _i2, l2 = events[j]
        pm = page_re.search(l2)
        if pm:
            if last_page is None:
                last_page = {
                    "page": int(pm.group(1)),
                    "nuovi": int(pm.group(2)),
                    "locali": int(pm.group(4)),
                    "meta": int(pm.group(5)),
                }
            if int(pm.group(2)) == 0 and int(pm.group(4)) + int(pm.group(5)) >= 8:
                skip_streak += 1
            else:
                # stop streak on non-skip page but keep counting only streak
                if last_page and skip_streak == 0:
                    pass
                break
        if "RICERCA OK" in l2 or "Init OK" in l2:
            last_ricerca_ok = t2
            break
        if (ts - t2).total_seconds() > 7200:
            break
    dt_ok = (ts - last_ricerca_ok).total_seconds() / 60 if last_ricerca_ok else None
    primary.append(
        {
            "ts": ts,
            "kind": kind,
            "line": line[28:160],
            "last_page": last_page,
            "skip_streak": skip_streak,
            "min_since_ok": round(dt_ok, 1) if dt_ok is not None else None,
        }
    )

print("=== Primary 403 events ===", len(primary))
print("Kinds:", Counter(b["kind"] for b in primary))

skip_buckets = Counter()
mins_ok = []
for b in primary:
    ss = b["skip_streak"]
    if ss == 0:
        skip_buckets["0 (no skip streak / first hit)"] += 1
    elif ss < 5:
        skip_buckets["1-4 skip pages"] += 1
    elif ss < 10:
        skip_buckets["5-9 skip pages"] += 1
    elif ss < 15:
        skip_buckets["10-14 skip pages"] += 1
    else:
        skip_buckets["15+ skip pages"] += 1
    if b["min_since_ok"] is not None:
        mins_ok.append(b["min_since_ok"])

print("\n=== Skip streak before 403 ===")
for k, v in skip_buckets.most_common():
    print(f"  {k}: {v}")

print("\n=== Minutes since RICERCA/Init OK ===")
if mins_ok:
    mins_ok_s = sorted(mins_ok)
    print(
        f"  n={len(mins_ok)} min={mins_ok_s[0]} median={mins_ok_s[len(mins_ok_s)//2]} max={mins_ok_s[-1]}"
    )
    for lo, hi, name in [
        (0, 1, "0-1m (quasi subito)"),
        (1, 5, "1-5m"),
        (5, 10, "5-10m"),
        (10, 20, "10-20m"),
        (20, 999, "20m+"),
    ]:
        c = sum(1 for x in mins_ok if lo <= x < hi)
        print(f"  {name}: {c}")

print("\n=== Last 30 primary 403 ===")
for b in primary[-30:]:
    lp = b["last_page"]
    lp_s = (
        f"p.{lp['page']} locali={lp['locali']} nuovi={lp['nuovi']}"
        if lp
        else "no-page"
    )
    print(
        f"{b['ts'].strftime('%m-%d %H:%M:%S')} | {b['kind']:16} | skip={b['skip_streak']:2} | +{b['min_since_ok']}m | {lp_s}"
    )

# Sessions OK -> first hard 403
print("\n=== Session RICERCA/Init OK -> first 403 ===")
sessions = []
last_ok = None
for ts, _i, line in events:
    if "RICERCA OK" in line or "Init OK:" in line:
        last_ok = ts
        continue
    if not last_ok:
        continue
    if not (
        "search/submit HTTP 403" in line
        or "403 su Ricerca" in line
        or "403 su '>'" in line
    ):
        continue
    n_pages = n_skip = n_dl = 0
    for ts2, _i2, l2 in events:
        if ts2 <= last_ok:
            continue
        if ts2 >= ts:
            break
        pm = page_re.search(l2)
        if pm:
            n_pages += 1
            if int(pm.group(2)) == 0:
                n_skip += 1
        if "SCARICATO" in l2:
            n_dl += 1
    dur = (ts - last_ok).total_seconds() / 60
    sessions.append((last_ok, ts, dur, n_pages, n_skip, n_dl))
    last_ok = None

for s in sessions[-25:]:
    print(
        f"  OK {s[0].strftime('%m-%d %H:%M')} -> 403 {s[1].strftime('%H:%M')} | "
        f"{s[2]:5.1f} min | pages={s[3]:3} skip={s[4]:3} dl={s[5]}"
    )

imm = [s for s in sessions if s[3] <= 1]
print(f"\nImmediate 403 (0-1 pages): {len(imm)} / {len(sessions)} sessions")
long = [s for s in sessions if s[3] >= 5]
if long:
    pages = sorted(s[3] for s in long)
    durs = sorted(s[2] for s in long)
    skips = sorted(s[4] for s in long)
    print(
        f"Sessions with >=5 pages then 403: n={len(long)}\n"
        f"  pages walked: min={pages[0]} med={pages[len(pages)//2]} max={pages[-1]}\n"
        f"  skip pages:   min={skips[0]} med={skips[len(skips)//2]} max={skips[-1]}\n"
        f"  duration min: min={durs[0]:.1f} med={durs[len(durs)//2]:.1f} max={durs[-1]:.1f}"
    )

# Heal then still 403
print("\n=== After long cooldown, still 403 on first ricerca? ===")
heal_fail = 0
heal_ok = 0
for idx, (ts, _i, line) in enumerate(events):
    if "cooldown" in line and "attendo" in line and "min" in line:
        # find next RICERCA / 403
        for j in range(idx + 1, min(idx + 40, len(events))):
            t2, _i2, l2 = events[j]
            if "RICERCA OK" in l2 or "Tabella pronta" in l2:
                heal_ok += 1
                break
            if "403" in l2 and ("Ricerca" in l2 or "search/submit" in l2):
                heal_fail += 1
                break
print(f"  after cooldown -> ricerca OK: {heal_ok}")
print(f"  after cooldown -> still 403: {heal_fail}")
