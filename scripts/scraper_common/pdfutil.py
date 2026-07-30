"""Validazione e salvataggio PDF (Windows-safe)."""
from __future__ import annotations

import hashlib
import io
import os
import time
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_pdf_bytes(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


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
        reader = PdfReader(io.BytesIO(data), strict=False)
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
    if len(data) < min_bytes:
        errors.append(f"troppo piccolo: {len(data)} < {min_bytes}")
    if len(data) > max_bytes:
        errors.append(f"troppo grande: {len(data)} > {max_bytes}")
    if not is_pdf_bytes(data):
        errors.append("firma PDF assente (%PDF-)")
    elif not has_pdf_eof(data):
        errors.append("%%EOF assente")
    if errors:
        return errors
    errors.extend(verify_pdf_structure(data))
    return errors


def validate_local_pdf(path: Path, *, min_bytes: int, max_bytes: int) -> list[str]:
    if not path.exists():
        return ["non esiste"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"non leggibile: {exc}"]
    return verify_pdf(data, min_bytes=min_bytes, max_bytes=max_bytes)


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
    info = save_pdf_atomic(dest, data, overwrite=overwrite)
    return {"ok": True, **info}
