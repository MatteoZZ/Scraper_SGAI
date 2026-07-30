"""Download PDF + verifiche rafforzate + salvataggio atomico."""
from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path


def is_pdf_bytes(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head[:200]


def has_pdf_eof(data: bytes) -> bool:
    """%%EOF tipicamente in coda; assenza = PDF troncato/incompleto."""
    if not data:
        return False
    tail = data[-8192:] if len(data) > 8192 else data
    return b"%%EOF" in tail


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_minimal_pdf(min_bytes: int = 1200) -> bytes:
    """PDF sintetico valido per pypdf (solo --simulate; non è una sentenza reale)."""
    from pypdf import PdfWriter

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buf)
    data = buf.getvalue()
    if len(data) >= min_bytes:
        return data
    # Padding dopo %%EOF: non altera trailer/catalogo; has_pdf_eof resta vero
    pad_len = min_bytes - len(data)
    return data + (b"\n%" + b"0" * max(0, pad_len - 2) + b"\n")[:pad_len]


def verify_pdf_structure(data: bytes) -> list[str]:
    """
    Validazione strutturale con pypdf: trailer + catalogo /Root.
    %PDF- + %%EOF da soli non bastano (payload non-PDF possono passarli).
    """
    errors: list[str] = []
    try:
        from pypdf import PdfReader
    except ImportError:
        return ["pypdf non disponibile (richiesto per validazione strutturale)"]

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001 — qualsiasi fallimento parser = PDF invalido
        return [f"parser PDF fallito: {exc}"]

    try:
        trailer = reader.trailer
        if trailer is None:
            errors.append("trailer PDF assente")
        else:
            root = trailer.get("/Root")
            if root is None:
                errors.append("catalogo /Root assente nel trailer")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"trailer PDF non leggibile: {exc}")

    try:
        root_obj = reader.root_object
        if root_obj is None:
            errors.append("root_object assente")
        else:
            rtype = root_obj.get("/Type") if hasattr(root_obj, "get") else None
            if rtype is not None and str(rtype) not in ("/Catalog", "Catalog"):
                errors.append(f"root non è Catalog: {rtype}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"catalogo PDF non accessibile: {exc}")

    try:
        # Accesso pagine: fallisce su strutture rotte anche se header/EOF ok
        _ = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pagine PDF non leggibili: {exc}")

    return errors


def verify_pdf_structure_path(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"non leggibile: {exc}"]
    return verify_pdf_structure(data)


def verify_pdf(
    data: bytes,
    *,
    min_bytes: int,
    max_bytes: int,
) -> list[str]:
    errors: list[str] = []
    if not data:
        errors.append("vuoto")
        return errors
    if looks_like_html(data):
        errors.append("contenuto HTML invece di PDF")
    if len(data) < min_bytes:
        errors.append(f"troppo piccolo: {len(data)} < {min_bytes}")
    if len(data) > max_bytes:
        errors.append(f"troppo grande: {len(data)} > {max_bytes}")
    if not is_pdf_bytes(data):
        errors.append("firma PDF assente (%PDF-)")
    elif not has_pdf_eof(data):
        errors.append("%%EOF assente (PDF incompleto/troncato)")
    # bytes nulli eccessivi all'inizio = spesso download corrotto
    if data[:64].count(b"\x00") > 32:
        errors.append("troppi null bytes in testa (corrotto)")
    # Se i check grezzi falliscono, non serve il parser
    if errors:
        return errors
    errors.extend(verify_pdf_structure(data))
    return errors


def validate_local_pdf(
    path: Path,
    *,
    min_bytes: int,
    max_bytes: int,
) -> list[str]:
    """Validazione file già su disco: size, firma, EOF, struttura pypdf."""
    if not path.exists():
        return ["non esiste"]
    if not path.is_file():
        return ["non è un file"]
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"stat fallita: {exc}"]
    if size <= 0:
        return ["size 0"]
    errors: list[str] = []
    if size < min_bytes:
        errors.append(f"troppo piccolo: {size} < {min_bytes}")
    if size > max_bytes:
        errors.append(f"troppo grande: {size} > {max_bytes}")
    try:
        with path.open("rb") as fh:
            if size <= 2_000_000:
                data = fh.read()
                head = data[:1024]
                tail = data[-8192:] if len(data) > 8192 else data
            else:
                head = fh.read(1024)
                fh.seek(max(0, size - 8192))
                tail = fh.read(8192)
                data = None
    except OSError as exc:
        return [f"non leggibile: {exc}"]
    if looks_like_html(head):
        errors.append("contenuto HTML invece di PDF")
    if not is_pdf_bytes(head):
        errors.append("firma PDF assente (%PDF-)")
    elif not has_pdf_eof(tail):
        errors.append("%%EOF assente (PDF incompleto/troncato)")
    if head[:64].count(b"\x00") > 32:
        errors.append("troppi null bytes in testa (corrotto)")
    if errors:
        return errors
    if data is not None:
        errors.extend(verify_pdf_structure(data))
    else:
        errors.extend(verify_pdf_structure_path(path))
    return errors


def local_pdf_ok(path: Path, *, min_bytes: int, max_bytes: int) -> bool:
    return not validate_local_pdf(path, min_bytes=min_bytes, max_bytes=max_bytes)


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def save_pdf_atomic(dest: Path, data: bytes, *, overwrite: bool = False) -> dict:
    """Scrive via file .tmp poi os.replace. Ritorna info sha/size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        raise FileExistsError(str(dest))
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return {
        "path": str(dest),
        "sha256": sha256_bytes(data),
        "size": len(data),
    }


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


def cleanup_tmp_dir(tmp_dir: Path, keep_newest: int = 20) -> int:
    """Non accumulare migliaia di PDF temp: tiene solo i più recenti."""
    if not tmp_dir.exists():
        return 0
    files = sorted(tmp_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[keep_newest:]:
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


_NOME_RE = re.compile(
    r"^(?P<tipo>[A-Za-zÀ-ÿ]+)_(?P<codice>[A-Za-z]\d{2})_(?P<numero>\d+)_(?P<anno>\d{4})$"
)


def parse_nome_base(nome_base: str) -> dict | None:
    base = nome_base[:-4] if nome_base.lower().endswith(".pdf") else nome_base
    m = _NOME_RE.match(base)
    if not m:
        return None
    return m.groupdict()


def coherence_nome_vs_meta(nome_base: str, meta: dict) -> list[str]:
    """Controlla che nome file e metadati (numero/anno/codice/tipo) coincidano."""
    parsed = parse_nome_base(nome_base)
    if not parsed:
        return [f"nomeBase non canonico: {nome_base}"]
    errors = []
    for key in ("tipo", "codice", "numero", "anno"):
        expected = str(meta.get(key, "")).strip()
        got = str(parsed.get(key, "")).strip()
        if key == "tipo":
            if expected and got.lower() != expected.lower():
                errors.append(f"coerenza {key}: nome={got} meta={expected}")
        elif expected and got != expected:
            errors.append(f"coerenza {key}: nome={got} meta={expected}")
    return errors
