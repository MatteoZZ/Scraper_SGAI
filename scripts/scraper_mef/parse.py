"""Estrazione metadati da riga tabella HTML, title link o pagina dettaglio."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable

from .names import row_to_filename, title_to_filename
from .portal_to_filename import build_filename, normalize_tipo

DETAIL_NUM_ANNO_RE = re.compile(
    r"n\.?\s*(\d+)\s*/\s*(\d{4})",
    re.IGNORECASE,
)
DETAIL_CORTE_RE = re.compile(
    r"CGT\s*(1|2)\s*[°º]?\s+([A-Za-zÀ-ÿ'\- ]+?)(?:\s*$|\s{2,}|\n|<)",
    re.IGNORECASE,
)
DETAIL_TIPO_RE = re.compile(
    r"\b(Sentenza|Ordinanza|Decreto|Pronuncia)\b",
    re.IGNORECASE,
)


@dataclass
class PortalRow:
    tipo: str
    numero: str
    anno: str
    corte: str
    title: str | None = None
    href: str | None = None
    row_index: int | None = None
    extra: dict = field(default_factory=dict)

    def to_meta(self) -> dict:
        # Preferisci celle tabella (includono tipo); title come fallback
        meta = row_to_filename(self.numero, self.anno, self.corte, self.tipo)
        if meta.get("ok"):
            if self.title:
                meta["portalTitle"] = self.title
            if self.href:
                meta["href"] = self.href
            return meta
        if self.title:
            tmeta = title_to_filename(self.title)
            if tmeta.get("ok"):
                # applica tipo dalla riga se title non lo porta
                if self.tipo:
                    rebuilt = build_filename(
                        tmeta.get("cortePortale") or self.corte,
                        tmeta["numero"],
                        tmeta["anno"],
                        tipo=self.tipo,
                    )
                    if rebuilt.get("ok"):
                        rebuilt["portalTitle"] = self.title
                        if self.href:
                            rebuilt["href"] = self.href
                        return rebuilt
                if self.href:
                    tmeta["href"] = self.href
                return tmeta
        return meta


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_td = False
        self._current_row: list[str] | None = None
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._current_row = []
        elif tag == "td" and self._current_row is not None:
            self._in_td = True
            self._buf = ""

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td and self._current_row is not None:
            self._current_row.append(self._buf.strip())
            self._in_td = False
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data):
        if self._in_td:
            self._buf += data


def parse_table_html(html: str) -> list[PortalRow]:
    """Parse semplice per fixture (senza link). Non usare per live con Visualizza."""
    parser = _TableParser()
    parser.feed(html)
    out: list[PortalRow] = []
    for i, cells in enumerate(parser.rows):
        if len(cells) < 4:
            continue
        tipo, numero, anno, corte = cells[0], cells[1], cells[2], cells[3]
        if not re.fullmatch(r"\d+", numero or ""):
            continue
        if not re.fullmatch(r"\d{4}", anno or ""):
            continue
        out.append(
            PortalRow(
                tipo=tipo or "Sentenza",
                numero=numero,
                anno=anno,
                corte=corte,
                row_index=i,
            )
        )
    return out


def rows_from_link_dicts(items: list[dict]) -> list[PortalRow]:
    """
    Costruisce righe da oggetti {href, title, cells/tipo/numero/...}
    prodotti da JS link.closest('tr') — associazione per riga DOM, non per indice filtrato.
    """
    out: list[PortalRow] = []
    for i, item in enumerate(items or []):
        cells = item.get("cells") or []
        tipo = (item.get("tipo") or (cells[0] if len(cells) > 0 else "") or "Sentenza").strip()
        numero = (item.get("numero") or (cells[1] if len(cells) > 1 else "") or "").strip()
        anno = (item.get("anno") or (cells[2] if len(cells) > 2 else "") or "").strip()
        corte = (item.get("corte") or (cells[3] if len(cells) > 3 else "") or "").strip()
        title = item.get("title") or item.get("download_title")
        href = item.get("href") or item.get("download_href")
        out.append(
            PortalRow(
                tipo=tipo or "Sentenza",
                numero=numero,
                anno=anno,
                corte=corte,
                title=title,
                href=href,
                row_index=int(item.get("row_index", i)),
                extra={"raw": item},
            )
        )
    return out


def parse_detail_text(text: str, *, page_url: str = "") -> dict:
    """
    Estrae numero/anno/corte/tipo dal testo (o HTML) della pagina dettaglio MEF.
    Fonte autoritativa per allineare PDF ↔ metadati.
    """
    raw = text or ""
    # preferisci testo visibile: strip tag grezzi
    plain = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    plain = re.sub(r"<style[\s\S]*?</style>", " ", plain, flags=re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()

    m_num = DETAIL_NUM_ANNO_RE.search(plain)
    m_corte = DETAIL_CORTE_RE.search(plain)
    m_tipo = DETAIL_TIPO_RE.search(plain)

    numero = m_num.group(1) if m_num else ""
    anno = m_num.group(2) if m_num else ""
    corte = ""
    if m_corte:
        corte = f"CGT {m_corte.group(1)}° {m_corte.group(2).strip()}"
    tipo = normalize_tipo(m_tipo.group(1) if m_tipo else "Sentenza")

    if not (numero and anno and corte):
        return {
            "ok": False,
            "error": "metadati dettaglio incompleti",
            "numero": numero,
            "anno": anno,
            "cortePortale": corte,
            "tipo": tipo,
            "pageUrl": page_url,
            "snippet": plain[:240],
        }
    result = build_filename(corte, numero, anno, tipo=tipo)
    result["pageUrl"] = page_url
    result["source"] = "detail_page"
    return result


def metas_match(list_meta: dict, detail_meta: dict) -> list[str]:
    """Verifica allineamento lista ↔ dettaglio (numero/anno/codice; tipo soft)."""
    errors: list[str] = []
    if not detail_meta.get("ok"):
        errors.append(detail_meta.get("error") or "dettaglio non ok")
        return errors
    for key in ("numero", "anno", "codice"):
        a = str(list_meta.get(key, "")).strip()
        b = str(detail_meta.get(key, "")).strip()
        if a and b and a != b:
            errors.append(f"mismatch {key}: lista={a} dettaglio={b}")
    # tipo: avvisa ma non blocca se lista vuota; se entrambi presenti e diversi → errore
    ta = str(list_meta.get("tipo", "")).strip().lower()
    tb = str(detail_meta.get("tipo", "")).strip().lower()
    if ta and tb and ta != tb:
        errors.append(f"mismatch tipo: lista={list_meta.get('tipo')} dettaglio={detail_meta.get('tipo')}")
    return errors


def validate_row(row: PortalRow) -> list[str]:
    errors: list[str] = []
    if not row.numero or not re.fullmatch(r"\d+", row.numero):
        errors.append("numero mancante o non numerico")
    if not row.anno or not re.fullmatch(r"\d{4}", row.anno):
        errors.append("anno mancante o non valido")
    if not row.corte:
        errors.append("corte mancante")
    if errors:
        return errors
    meta = row.to_meta()
    if not meta.get("ok"):
        errors.append(meta.get("error") or "nome non calcolabile")
    return errors


def iter_validated(rows: Iterable[PortalRow]) -> list[tuple[PortalRow, dict, list[str]]]:
    results = []
    for row in rows:
        errs = validate_row(row)
        meta = row.to_meta() if not errs else {}
        results.append((row, meta, errs))
    return results
