"""Naming canonico documenti Italgiure (Solr SN-Cassazione)."""
from __future__ import annotations

import re
from typing import Any

from .config import PDF_ATTACH_BASE

_TIPO_MAP = {
    "ordinanza": "O",
    "sentenza": "S",
    "decreto": "D",
    "o": "O",
    "s": "S",
    "d": "D",
}


def _first(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        return _first(value[0])
    return str(value).strip()


def _safe_token(text: str, max_len: int = 80) -> str:
    text = (text or "").strip()
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:max_len] or "doc").strip("_")


def normalize_filename_field(filename: str) -> str:
    """Normalizza path Solr `./YYYYMMDD/....pdf` → path pulito con `.clean.pdf`."""
    fn = (filename or "").strip()
    if not fn:
        return ""
    if not fn.startswith("./"):
        fn = "./" + fn.lstrip("/")
    if fn.endswith(".clean.pdf"):
        return fn
    if fn.endswith(".pdf"):
        return fn[:-4] + ".clean.pdf"
    return fn + ".clean.pdf"


def build_pdf_url(filename: str) -> str:
    clean = normalize_filename_field(filename)
    if not clean:
        return ""
    # Il server accetta id non percent-encoded (come lo script locale storico)
    return f"{PDF_ATTACH_BASE}{clean}"


def tipo_code(tipoprov: str, doc_id: str = "") -> str:
    key = (tipoprov or "").strip().lower()
    if key in _TIPO_MAP:
        return _TIPO_MAP[key]
    # fallback: ultimo carattere id snciv...O / ...S
    if doc_id:
        last = doc_id[-1].upper()
        if last in {"O", "S", "D"}:
            return last
    return "X"


def sezione_label(szdec: str) -> str:
    s = (szdec or "").strip().upper()
    if s in {"5", "U"}:
        return s
    return _safe_token(s or "X", max_len=8)


def meta_from_solr_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Ritorna metadati + nome file canonico da un documento Solr."""
    doc_id = _first(doc.get("id"))
    if not doc_id:
        return {"ok": False, "error": "id assente", "raw": doc}

    filename = _first(doc.get("filename"))
    if not filename:
        return {"ok": False, "error": "filename assente", "id": doc_id}

    szdec = _first(doc.get("szdec"))
    anno = _first(doc.get("anno"))
    numdec = _first(doc.get("numdec")) or _first(doc.get("numcard"))
    tipoprov = _first(doc.get("tipoprov"))
    datdep = _first(doc.get("datdep"))
    kind = _first(doc.get("kind")) or "snciv"
    tipo = tipo_code(tipoprov, doc_id)
    sez = sezione_label(szdec)

    if not anno:
        m = re.search(r"(20\d{2}|19\d{2})", doc_id)
        anno = m.group(1) if m else "0000"
    if not numdec:
        m = re.search(r"n(\d+)", filename, re.IGNORECASE)
        numdec = m.group(1) if m else "0"

    nome_base = f"ITALGIURE_Civile_{sez}_{anno}_{numdec}_{tipo}_{_safe_token(doc_id, 40)}"
    pdf_url = build_pdf_url(filename)
    if not pdf_url:
        return {"ok": False, "error": "url PDF non costruibile", "id": doc_id}

    return {
        "ok": True,
        "fonte": "ITALGIURE",
        "kind": kind,
        "id": doc_id,
        "szdec": sez,
        "sezione": "Quinta" if sez == "5" else ("SezioniUnite" if sez == "U" else sez),
        "anno": anno,
        "numdec": numdec,
        "tipoprov": tipoprov or tipo,
        "tipo": tipo,
        "datdep": datdep,
        "ecli": _first(doc.get("ecli")),
        "filenameRemote": filename,
        "url": pdf_url,
        "nomeBase": nome_base,
        "nomeFile": f"{nome_base}.pdf",
    }
