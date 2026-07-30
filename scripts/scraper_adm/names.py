"""Naming canonico documenti ADM."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse


# Tollera tipografie del sito: ] mancante, trattini strani, data incompleta
_TITLE_RE = re.compile(
    r"\[(?P<protocollo>\d+)\s+del\s+(?P<data>\d{2}/\d{2}/\d{4})\]?\s*"
    r"[–\-—?]?\s*(?P<title>.+)$",
    re.IGNORECASE,
)
_TITLE_LOOSE_RE = re.compile(
    r"\[(?P<protocollo>\d+)\s+del\s+(?P<data>\d{1,2}/\d{2}/\d{4}|\d{1,2}/\d{4})\]?\s*"
    r"[–\-—?]?\s*(?P<title>.+)$",
    re.IGNORECASE,
)
_PROTO_FROM_URL_RE = re.compile(r"/(\d{4,})\.", re.IGNORECASE)
_CIRCOLARE_RE = re.compile(
    r"Circolare\s*n\.?\s*(?P<numero>\d+)\s*/\s*(?P<anno>\d{4})",
    re.IGNORECASE,
)
_CIRCOLARE_ALT_RE = re.compile(
    r"Circolare\s+(?P<numero>\d+)\s*[-/]\s*(?P<anno>\d{4})",
    re.IGNORECASE,
)


def _safe_token(text: str, max_len: int = 80) -> str:
    text = (text or "").strip()
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:max_len] or "doc").strip("_")


def _normalize_date(data: str) -> tuple[str, str] | None:
    """Ritorna (iso YYYY-MM-DD, compact YYYYMMDD) o None."""
    parts = (data or "").split("/")
    if len(parts) == 2:
        # tipografia sito: 21/1024 → 21/10/2024
        dd, mmyy = parts
        if len(mmyy) == 4:
            mm, yy = mmyy[:2], mmyy[2:]
            parts = [dd, mm, "20" + yy]
        else:
            return None
    if len(parts) != 3:
        return None
    dd, mm, yyyy = parts
    if len(yyyy) == 2:
        yyyy = "20" + yyyy
    try:
        ddi, mmi, yyi = int(dd), int(mm), int(yyyy)
        if not (1 <= ddi <= 31 and 1 <= mmi <= 12 and 1990 <= yyi <= 2100):
            return None
    except ValueError:
        return None
    iso = f"{yyi:04d}-{mmi:02d}-{ddi:02d}"
    compact = f"{yyi:04d}{mmi:02d}{ddi:02d}"
    return iso, compact


def parse_list_title(text: str, *, url: str = "") -> dict:
    raw = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()
    m = _TITLE_RE.search(raw) or _TITLE_LOOSE_RE.search(raw)
    protocollo = ""
    title = raw
    data_iso = ""
    data_compact = ""
    if m:
        protocollo = m.group("protocollo")
        title = (m.group("title") or "").strip() or raw
        norm = _normalize_date(m.group("data"))
        if norm:
            data_iso, data_compact = norm
    if not protocollo and url:
        mu = _PROTO_FROM_URL_RE.search(url)
        if mu:
            protocollo = mu.group(1)
    # titolo senza protocollo ma con "Circolare n. X/YYYY" → usa URL protocollo
    if not protocollo:
        return {"ok": False, "error": "title non interpretabile", "raw": raw}

    if not data_compact:
        # fallback data da URL tipo 21-07-2026
        mu = re.search(r"(\d{2})-(\d{2})-(\d{4})", url or "")
        if mu:
            data_iso = f"{mu.group(3)}-{mu.group(2)}-{mu.group(1)}"
            data_compact = f"{mu.group(3)}{mu.group(2)}{mu.group(1)}"
        else:
            data_iso = "1970-01-01"
            data_compact = "19700101"

    numero = ""
    anno = data_iso[:4] if data_iso else ""
    mc = _CIRCOLARE_RE.search(title) or _CIRCOLARE_ALT_RE.search(title) or _CIRCOLARE_RE.search(raw)
    if not mc:
        mc = _CIRCOLARE_ALT_RE.search(raw)
    if mc:
        numero = mc.group("numero")
        anno = mc.group("anno")

    if numero:
        nome_base = f"ADM_Circolare_{numero}_{anno}_{protocollo}"
    else:
        nome_base = f"ADM_Doc_{protocollo}_{data_compact}"

    return {
        "ok": True,
        "fonte": "ADM",
        "tipo": "Circolare" if numero else "Documento",
        "protocollo": protocollo,
        "data": data_iso,
        "numero": numero,
        "anno": anno,
        "title": title,
        "nomeBase": nome_base,
        "nomeFile": f"{nome_base}.pdf",
        "rawTitle": raw,
    }


def meta_from_url(url: str) -> dict:
    """Fallback naming quando il title HTML non è nel formato lista recente."""
    from urllib.parse import unquote, urlparse
    import hashlib

    path = unquote(urlparse(url).path)
    fname = filename_from_pdf_url(url) or "doc.pdf"
    stem = fname[:-4] if fname.lower().endswith(".pdf") else fname
    # protocollo se presente in testa al filename
    m = re.match(r"^(\d{4,})", stem)
    protocollo = m.group(1) if m else hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]
    mc = _CIRCOLARE_RE.search(stem) or _CIRCOLARE_ALT_RE.search(stem)
    numero = mc.group("numero") if mc else ""
    anno = mc.group("anno") if mc else ""
    if not anno:
        my = re.search(r"(20\d{2}|19\d{2})", stem)
        anno = my.group(1) if my else "0000"
    if numero:
        nome_base = f"ADM_Circolare_{numero}_{anno}_{protocollo}"
    else:
        safe = _safe_token(stem, max_len=60)
        nome_base = f"ADM_Doc_{protocollo}_{safe}"
    return {
        "ok": True,
        "fonte": "ADM",
        "tipo": "Circolare" if numero else "Documento",
        "protocollo": str(protocollo),
        "data": "",
        "numero": numero,
        "anno": anno,
        "title": stem,
        "nomeBase": nome_base,
        "nomeFile": f"{nome_base}.pdf",
        "rawTitle": stem,
        "fromUrl": True,
    }


def filename_from_pdf_url(url: str) -> str | None:
    """Fallback: nome file dall'URL documents/.../name.pdf/uuid."""
    path = unquote(urlparse(url).path)
    # .../468276.21-07-2026-Circolare+19-con-allegati.pdf/<uuid>
    parts = [p for p in path.split("/") if p]
    for part in reversed(parts):
        if part.lower().endswith(".pdf") or ".pdf." in part.lower():
            name = part
            if not name.lower().endswith(".pdf"):
                # es. file.pdf.pdf → tieni fino al primo .pdf
                idx = name.lower().find(".pdf")
                name = name[: idx + 4]
            return _safe_token(name[:-4]) + ".pdf"
    return None
