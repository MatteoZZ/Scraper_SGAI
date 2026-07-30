"""CLI Massimario DEF — Playwright (Chrome/Edge) keyword + massimate + download."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEYWORDS = ROOT / "keywords.txt"
OUT = ROOT / "downloads_out"
CHECKPOINT = ROOT / ".checkpoint.json"
BASE = "https://def.finanze.it/DocTribFrontend/"
SEARCH_URL = BASE + "callRicAvanzataGiurisprudenza.do?js_enabled=1&reset=y"


def load_keywords(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def load_checkpoint() -> dict:
    if not CHECKPOINT.exists():
        return {"processed_keywords": [], "processed_docs": [], "failed_docs": []}
    try:
        data = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    data.setdefault("processed_keywords", [])
    data.setdefault("processed_docs", [])
    data.setdefault("failed_docs", [])
    return data


def save_checkpoint(data: dict) -> None:
    CHECKPOINT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def dry_run() -> int:
    kws = load_keywords(KEYWORDS)
    print(
        json.dumps(
            {
                "fonte": "MASSIMARIO",
                "portal": SEARCH_URL,
                "keywords": kws,
                "metrics": {"keywords": len(kws)},
                "note": "run = ricerca massimate + download (usa Chrome/Edge di sistema)",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _safe_name(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[^\w\-]+", "_", (text or "doc").strip(), flags=re.UNICODE)
    return (text[:max_len] or "doc").strip("_")


def _launch_browser(p, *, headed: bool):
    """Preferisci Chrome/Edge installati: il chromium Playwright spesso manca o crasha."""
    last = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = {"headless": not headed}
            if channel:
                kwargs["channel"] = channel
            browser = p.chromium.launch(**kwargs)
            print(f"[MASSIMARIO] browser={channel or 'chromium'}", flush=True)
            return browser
        except Exception as exc:
            last = exc
            print(f"[MASSIMARIO] launch fail {channel}: {exc}", flush=True)
    raise RuntimeError(
        f"Impossibile avviare browser. Installa Chrome oppure: playwright install chromium. Ultimo errore: {last}"
    )


def _open_search(page) -> None:
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120000)
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_selector("#parole", state="visible", timeout=60000)


def _submit_search(page, kw: str) -> None:
    page.evaluate(
        """() => {
          const h = document.querySelector('input[name="js_enabled"]');
          if (h) h.value = '1';
          const r = document.querySelector('input[name="reset"]');
          if (r) r.value = '';
        }"""
    )
    page.fill("#parole", kw)
    box = page.locator("#ricercaPresenzaMassima")
    if box.count() and not box.is_checked():
        box.check(force=True)

    # Bottone corretto sul DEF: <button type="submit">Ricerca</button>
    btn = page.locator('button[type="submit"]').filter(has_text=re.compile(r"Ricerca", re.I))
    if btn.count() == 0:
        btn = page.locator('button:has-text("Ricerca")')
    if btn.count() == 0:
        btn = page.locator('button[type="submit"]')
    btn.first.click()
    page.wait_for_load_state("domcontentloaded", timeout=120000)
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(1500)


DETAIL_URL = BASE + "getGiurisprudenzaDetail.do?id={doc_id}"
NEXT_BATCH_URL = BASE + "paginatorXml.do"
# DEF carica 50 risultati per volta; "ulteriori" → paginatorXml.do
MAX_BATCHES = 800  # 800*50 = 40k doc/keyword (copre IVA ~24k)


def _parse_result_batch(html: str) -> tuple[list[str], bool, int | None]:
    """Estrae id provvedimento dal xmlResult embedded + flag ulterioriRisultati."""
    ids = re.findall(
        r'idProvvedimento=\\?"(\{[0-9A-Fa-f\-]{36}\})\\?"',
        html,
    )
    # fallback: link HTML già renderizzati
    if not ids:
        ids = re.findall(
            r'getGiurisprudenzaDetail\.do\?id=(\{[0-9A-Fa-f\-]{36}\})',
            html,
            re.I,
        )
    # dedupe preservando ordine
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    ulteriori = bool(
        re.search(r"ulterioriRisultati>\s*true\s*<", html, re.I)
        or re.search(r"ulterioriRisultati\\?>true", html, re.I)
    )
    total = None
    m = re.search(r"contatoreGiurisprudenza>(\d+)<", html)
    if not m:
        m = re.search(r'id="totDocumentiTrovati">(\d+)<', html)
    if m:
        total = int(m.group(1))
    return ordered, ulteriori, total


def _doc_already_done(doc_id: str, done_docs: set[str]) -> bool:
    bare = doc_id.strip("{}")
    return bool(done_docs & {doc_id, bare, "{" + bare + "}"})


def _save_detail_html(page, doc_id: str) -> Path:
    url = DETAIL_URL.format(doc_id=doc_id)
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(400)
    dest = OUT / f"MASSIMARIO_{_safe_name(doc_id)}.html"
    dest.write_text(page.content(), encoding="utf-8")
    return dest


def run_download(
    *,
    max_keywords: int | None = None,
    max_docs: int | None = None,
    headed: bool = False,
    resume: bool = True,
) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            json.dumps(
                {
                    "error": "playwright non installato",
                    "fix": "pip install playwright && playwright install chromium",
                }
            )
        )
        return 2

    kws = load_keywords(KEYWORDS)
    if max_keywords is not None:
        kws = kws[: max(0, max_keywords)]
    OUT.mkdir(parents=True, exist_ok=True)
    ck = (
        load_checkpoint()
        if resume
        else {"processed_keywords": [], "processed_docs": [], "failed_docs": []}
    )
    done_kw = set(ck["processed_keywords"])
    done_docs = set(ck["processed_docs"])

    results = []
    downloaded = 0
    errors = 0

    with sync_playwright() as p:
        browser = _launch_browser(p, headed=headed)
        context = browser.new_context(
            locale="it-IT",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1360, "height": 900},
        )
        page = context.new_page()

        for kw in kws:
            if kw in done_kw and resume:
                results.append({"keyword": kw, "action": "skip_checkpoint"})
                continue

            print(f"[MASSIMARIO] keyword={kw}", flush=True)
            try:
                # retry apertura form (il DEF a volte risponde vuoto)
                last_exc = None
                for attempt in range(3):
                    try:
                        _open_search(page)
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                        print(f"  reopen form tentativo {attempt+1}: {exc}", flush=True)
                        try:
                            page.screenshot(path=str(OUT / f"_err_form_{_safe_name(kw)}.png"))
                        except Exception:
                            pass
                        time.sleep(2)
                        try:
                            page = context.new_page()
                        except Exception:
                            pass
                if last_exc:
                    raise last_exc

                _submit_search(page, kw)
                html = page.content()
                snap = OUT / f"MASSIMARIO_results_{_safe_name(kw)}.html"
                snap.write_text(html, encoding="utf-8")

                stopped_by_max = False
                kw_links = 0
                kw_saved = 0
                batch_i = 0
                has_more = True
                seen_kw: set[str] = set()

                while has_more and batch_i < MAX_BATCHES:
                    batch_i += 1
                    if batch_i > 1:
                        page.goto(
                            NEXT_BATCH_URL,
                            wait_until="domcontentloaded",
                            timeout=120000,
                        )
                        page.wait_for_timeout(1000)
                        html = page.content()

                    ids, ulteriori, total = _parse_result_batch(html)
                    # paginatorXml a volte rimanda anche i batch precedenti: prendi solo i nuovi
                    fresh = [i for i in ids if i not in seen_kw]
                    for i in fresh:
                        seen_kw.add(i)
                    if batch_i == 1:
                        print(
                            f"  results_html={snap.name} trovati={total} "
                            f"(blocchi da 50 via paginatorXml, solo HTML)",
                            flush=True,
                        )
                    print(
                        f"  batch {batch_i}: +{len(fresh)} nuovi "
                        f"(xml={len(ids)}) ulteriori={ulteriori} tot_kw={len(seen_kw)}",
                        flush=True,
                    )
                    if not ids:
                        if batch_i == 1:
                            parole_val = ""
                            try:
                                if page.locator("#parole").count():
                                    parole_val = page.input_value("#parole")
                            except Exception:
                                pass
                            results.append(
                                {
                                    "keyword": kw,
                                    "action": "no_results",
                                    "results_html": str(snap),
                                    "parole_field": parole_val,
                                }
                            )
                        break

                    if not fresh and ulteriori:
                        # pagina non ha avanzato: evita loop infinito
                        print("  batch senza id nuovi: stop paginazione", flush=True)
                        break

                    kw_links = len(seen_kw)
                    for doc_id in fresh:
                        if max_docs is not None and downloaded >= max_docs:
                            stopped_by_max = True
                            break
                        if _doc_already_done(doc_id, done_docs):
                            continue
                        try:
                            dest = _save_detail_html(page, doc_id)
                            done_docs.add(doc_id)
                            downloaded += 1
                            kw_saved += 1
                            if downloaded % 10 == 0:
                                ck["processed_docs"] = sorted(done_docs)
                                save_checkpoint(ck)
                            print(f"  saved {dest.name}", flush=True)
                            time.sleep(0.35)
                        except Exception as exc:
                            errors += 1
                            ck.setdefault("failed_docs", []).append(doc_id)
                            save_checkpoint(ck)
                            print(f"  error doc {doc_id}: {exc}", flush=True)

                    ck["processed_docs"] = sorted(done_docs)
                    save_checkpoint(ck)

                    if stopped_by_max:
                        break
                    has_more = ulteriori
                    if not has_more:
                        break

                if batch_i == 1 and kw_links == 0:
                    pass  # già no_results
                else:
                    results.append(
                        {
                            "keyword": kw,
                            "action": "searched",
                            "results_html": str(snap),
                            "detail_links": kw_links,
                            "saved_html": kw_saved,
                            "batches": batch_i,
                        }
                    )

                if not stopped_by_max:
                    done_kw.add(kw)
                    ck["processed_keywords"] = sorted(done_kw)
                    save_checkpoint(ck)
                else:
                    print(
                        f"  stop --max-docs raggiunto (keyword {kw} NON chiusa)",
                        flush=True,
                    )
                    break

            except Exception as exc:
                errors += 1
                try:
                    page.screenshot(path=str(OUT / f"_err_{_safe_name(kw)}.png"), full_page=True)
                except Exception:
                    pass
                results.append({"keyword": kw, "action": "error", "error": str(exc)[:400]})
                print(f"  ERROR: {exc}", flush=True)

        browser.close()

    print(
        json.dumps(
            {
                "fonte": "MASSIMARIO",
                "upload": "DISABLED",
                "output_dir": str(OUT),
                "metrics": {
                    "keywords": len(kws),
                    "downloaded_or_saved": downloaded,
                    "errors": errors,
                },
                "items": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scraper Massimario DEF (keyword fiscali + massimate)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dry-run")
    p_run = sub.add_parser("run", help="Cerca e scarica documenti massimati")
    p_run.add_argument("--max-keywords", type=int, default=None)
    p_run.add_argument("--max-docs", type=int, default=None)
    p_run.add_argument("--headed", action="store_true")
    p_run.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "dry-run":
        return dry_run()
    if args.cmd == "run":
        return run_download(
            max_keywords=args.max_keywords,
            max_docs=args.max_docs,
            headed=args.headed,
            resume=not args.no_resume,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
