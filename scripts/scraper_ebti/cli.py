"""CLI EBTI — scarica dump ufficiale (tutti i BTI), no filtro."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "downloads_out"
FULL_URL = (
    "https://ec.europa.eu/taxation_customs/dds2/ebti/"
    "ebti_export_management.jsp?message=extractFull"
)
CONSULT_URL = (
    "https://ec.europa.eu/taxation_customs/dds2/ebti/ebti_consultation.jsp?Lang=en"
)
UA = "Mozilla/5.0 (compatible; SGAI-ebti-scraper/0.1; +local)"


def _setup_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("scraper_ebti")


def _stream_download(url: str, dest: Path, *, referer: str) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Referer": referer, "Accept": "*/*"},
    )
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        ctype = resp.headers.get("content-type", "")
        clen = resp.headers.get("content-length")
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                size += len(chunk)
                if size and size % (50 * 1024 * 1024) < 1024 * 1024:
                    logging.getLogger("scraper_ebti").info(
                        "progress %s MB ...", size // (1024 * 1024)
                    )
    os.replace(tmp, dest)
    return {
        "path": str(dest),
        "sha256": h.hexdigest(),
        "size": size,
        "content_type": ctype,
        "content_length_header": clen,
    }


def dry_run() -> int:
    _setup_log()
    print(
        json.dumps(
            {
                "fonte": "EBTI",
                "note": "Dump ufficiale = ZIP di CSV annuali (non PDF). Usa: python -m scripts.scraper_ebti list",
                "urls": {
                    "consultation": CONSULT_URL,
                    "extractFull": FULL_URL,
                },
                "action": "would_download_extractFull",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def list_dump(*, output_dir: Path | None = None) -> int:
    """Elenca contenuto dello zip già scaricato."""
    import zipfile

    out = output_dir or DEFAULT_OUT
    zpath = out / "EBTI_extractFull.zip"
    if not zpath.exists():
        # fallback nome .bin rinominato
        cands = list(out.glob("EBTI_extractFull.*"))
        zpath = next((p for p in cands if p.suffix.lower() == ".zip"), zpath)
    if not zpath.exists():
        print(json.dumps({"error": "zip non trovato", "dir": str(out)}))
        return 2
    with zipfile.ZipFile(zpath) as zf:
        infos = zf.infolist()
        files = [
            {"name": i.filename, "bytes": i.file_size, "compressed": i.compress_size}
            for i in infos
            if not i.is_dir()
        ]
    print(
        json.dumps(
            {
                "fonte": "EBTI",
                "zip": str(zpath),
                "zip_size": zpath.stat().st_size,
                "entries": len(files),
                "files": files,
                "hint": "Sono CSV per anno (EBTI_2004.csv …). Estrai con: python -m scripts.scraper_ebti extract",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def extract_dump(*, output_dir: Path | None = None, year: str | None = None) -> int:
    import zipfile

    log = _setup_log()
    out = output_dir or DEFAULT_OUT
    zpath = out / "EBTI_extractFull.zip"
    if not zpath.exists():
        print(json.dumps({"error": "zip non trovato", "expected": str(zpath)}))
        return 2
    dest = out / "extracted"
    dest.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        if year:
            names = [n for n in names if year in n]
            if not names:
                print(json.dumps({"error": f"nessun file per anno {year}"}))
                return 2
        for name in names:
            target = dest / Path(name).name
            log.info("extract %s -> %s", name, target)
            with zf.open(name) as src, open(target, "wb") as fh:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
            extracted.append({"name": target.name, "bytes": target.stat().st_size})
    # anteprima primo CSV
    preview = None
    if extracted:
        sample = dest / extracted[-1]["name"]
        try:
            with sample.open("r", encoding="utf-8", errors="replace", newline="") as f:
                import csv

                reader = csv.reader(f, delimiter=";")
                header = next(reader)
                if header and header[0].startswith("\ufeff"):
                    header[0] = header[0].lstrip("\ufeff")
                row = next(reader, None)
                preview = {
                    "file": sample.name,
                    "columns": [str(c) for c in header[:15]],
                    "sample_row": [str(c)[:80] for c in (row[:15] if row else [])],
                }
        except Exception as exc:
            preview = {"error": str(exc)}
    print(
        json.dumps(
            {
                "fonte": "EBTI",
                "extracted_dir": str(dest),
                "files": extracted,
                "preview": preview,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_download(*, output_dir: Path | None = None, force: bool = False) -> int:
    log = _setup_log()
    out = output_dir or DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "EBTI_extractFull.bin"
    marker = out / "EBTI_extractFull.DONE.json"
    if marker.exists() and dest.exists() and not force:
        log.info("Dump già presente: %s (usa --force per riscaricare)", dest)
        print(json.dumps({"fonte": "EBTI", "action": "skip_local", "path": str(dest)}))
        return 0
    log.info("Download EBTI extractFull (può essere centinaia di MB)...")
    try:
        info = _stream_download(FULL_URL, dest, referer=CONSULT_URL)
    except urllib.error.HTTPError as exc:
        log.error("HTTP %s", exc.code)
        return 3 if exc.code in (403, 429) else 1
    except Exception as exc:
        log.error("errore: %s", exc)
        return 1

    # Rinomina in base a firma file
    name = "EBTI_extractFull.bin"
    head = dest.read_bytes()[:4]
    if head[:2] == b"PK":
        name = "EBTI_extractFull.zip"
    elif head[:5] == b"%PDF-":
        name = "EBTI_extractFull.pdf"
    elif head[:1] in (b"<", b"{") or b"<?xml" in dest.read_bytes()[:200]:
        name = "EBTI_extractFull.xml"
    final = out / name
    if final != dest:
        if final.exists():
            final.unlink()
        os.replace(dest, final)
        info["path"] = str(final)
    marker.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "fonte": "EBTI",
                "action": "downloaded",
                "upload": "DISABLED",
                **info,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper EBTI dump ufficiale")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dry-run")
    p_run = sub.add_parser("run", help="Scarica extractFull (tutti i BTI)")
    p_run.add_argument("--output-dir", type=Path, default=None)
    p_run.add_argument("--force", action="store_true")
    p_list = sub.add_parser("list", help="Elenca i CSV dentro lo zip scaricato")
    p_list.add_argument("--output-dir", type=Path, default=None)
    p_ex = sub.add_parser("extract", help="Scompatta lo zip in downloads_out/extracted")
    p_ex.add_argument("--output-dir", type=Path, default=None)
    p_ex.add_argument("--year", default=None, help="Solo un anno, es. 2026")
    args = parser.parse_args(argv)
    if args.cmd == "dry-run":
        return dry_run()
    if args.cmd == "run":
        return run_download(output_dir=args.output_dir, force=args.force)
    if args.cmd == "list":
        return list_dump(output_dir=args.output_dir)
    if args.cmd == "extract":
        return extract_dump(output_dir=args.output_dir, year=args.year)
    return 2


if __name__ == "__main__":
    sys.exit(main())
