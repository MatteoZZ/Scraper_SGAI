"""Validazione e salvataggio PDF (Windows-safe)."""
from __future__ import annotations

import hashlib
import io
import os
import time
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_payload_offset(data: bytes, *, max_scan: int = 1024) -> int:
    """Offset of %PDF- magic; Cellar sometimes prefixes \\r\\n/spaces."""
    if not data:
        return -1
    limit = min(len(data), max_scan)
    i = 0
    while i < limit and data[i] in b"\r\n\t\x00 ":
        i += 1
    if i + 5 <= len(data) and data[i : i + 5] == b"%PDF-":
        return i
    return -1


def is_pdf_bytes(data: bytes) -> bool:
    return pdf_payload_offset(data) >= 0


def strip_pdf_prefix(data: bytes) -> bytes:
    off = pdf_payload_offset(data)
    return data if off <= 0 else data[off:]


def looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def has_pdf_eof(data: bytes) -> bool:
    tail = data[-8192:] if len(data) > 8192 else data
    return b"%%EOF" in tail


def verify_pdf_structure(data: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ["pypdf non disponibile"]
    try:
        reader = PdfReader(io.BytesIO(strip_pdf_prefix(data)), strict=False)
    except Exception as exc:  # noqa: BLE001
        return [f"parser PDF fallito: {exc}"]
    errors: list[str] = []
    try:
        trailer = reader.trailer
        if trailer is None or trailer.get("/Root") is None:
            errors.append("trailer/catalogo /Root assente")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"trailer non leggibile: {exc}")
    try:
        _ = reader.root_object
        _ = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"catalogo/pagine non accessibili: {exc}")
    return errors


def verify_pdf(data: bytes, *, min_bytes: int, max_bytes: int) -> list[str]:
    errors: list[str] = []
    if not data:
        return ["vuoto"]
    if looks_like_html(data):
        errors.append("contenuto HTML invece di PDF")
    payload = strip_pdf_prefix(data)
    if len(payload) < min_bytes:
        errors.append(f"troppo piccolo: {len(payload)} < {min_bytes}")
    if len(payload) > max_bytes:
        errors.append(f"troppo grande: {len(payload)} > {max_bytes}")
    if not is_pdf_bytes(data):
        errors.append("firma PDF assente (%PDF-)")
    elif not has_pdf_eof(payload):
        errors.append("%%EOF assente")
    if errors:
        return errors
    errors.extend(verify_pdf_structure(payload))
    return errors


def validate_local_pdf(path: Path, *, min_bytes: int, max_bytes: int) -> list[str]:
    if not path.exists():
        return ["non esiste"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"non leggibile: {exc}"]
    return verify_pdf(data, min_bytes=min_bytes, max_bytes=max_bytes)


def quick_local_pdf_ok(path: Path, *, min_bytes: int, max_bytes: int) -> bool:
    """Check leggero per resume: size + %PDF- + %%EOF, senza leggere tutto / pypdf."""
    if not path.exists():
        return False
    try:
        sz = path.stat().st_size
    except OSError:
        return False
    if sz < min_bytes or sz > max_bytes:
        return False
    try:
        with path.open("rb") as f:
            head = f.read(64)
            if pdf_payload_offset(head) < 0:
                return False
            if sz > 8192:
                f.seek(-min(8192, sz), 2)
            else:
                f.seek(0)
            return b"%%EOF" in f.read()
    except OSError:
        return False


def _replace_with_retry(src: Path, dest: Path, *, attempts: int = 8) -> None:
    last: OSError | None = None
    for i in range(attempts):
        try:
            os.replace(src, dest)
            return
        except OSError as exc:
            last = exc
            winerr = getattr(exc, "winerror", None)
            if winerr not in (32, 5) and not isinstance(exc, PermissionError):
                raise
            time.sleep(0.15 * (i + 1))
    assert last is not None
    raise last


def save_pdf_atomic(dest: Path, data: bytes, *, overwrite: bool = False) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        raise FileExistsError(str(dest))
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_bytes(data)
        with open(tmp, "rb+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return {"path": str(dest), "sha256": sha256_bytes(data), "size": len(data)}


def ingest_pdf_bytes(
    data: bytes,
    dest: Path,
    *,
    min_bytes: int,
    max_bytes: int,
    overwrite: bool = False,
) -> dict:
    errors = verify_pdf(data, min_bytes=min_bytes, max_bytes=max_bytes)
    if errors:
        return {"ok": False, "errors": errors}
    info = save_pdf_atomic(dest, strip_pdf_prefix(data), overwrite=overwrite)
    return {"ok": True, **info}
