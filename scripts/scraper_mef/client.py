"""
Client portale MEF.

- fixture: HTML locale
- simulate: nessun browser (PDF sintetico)
- live: Playwright via CDP su browser già aperto

Live: ogni PDF è legato al proprio link Visualizza (href) e ai metadati
della pagina dettaglio — mai all'indice tra sole righe "valide" filtrate.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .parse import PortalRow, parse_detail_text, parse_table_html, rows_from_link_dicts

SITE_ORIGIN = "https://bancadatigiurisprudenza.giustiziatributaria.gov.it"
VISUALIZZA_SELECTOR = 'a[title^="Visualizza provvedimento"]'
DETTAGLIO_SCARICA_BTN = 'button[title="Scarica il pdf del provvedimento"]'

# JS: associa ogni Visualizza alla sua <tr> (non a un array filtrato a parte)
EXTRACT_LINKED_ROWS_JS = """
() => {
    const links = [...document.querySelectorAll('a[title^="Visualizza provvedimento"]')];
    const headers = [...document.querySelectorAll("table thead th")]
        .map(th => th.innerText.trim().toLowerCase());
    const idx = {
        tipo: headers.findIndex(h => h === "tipo"),
        numdec: headers.findIndex(h => h.includes("numero")),
        anno: headers.findIndex(h => h === "anno"),
        autorita: headers.findIndex(h => h.includes("autorit")),
    };
    const pick = (cells, key) => {
        const i = idx[key];
        return i >= 0 && cells[i] ? cells[i].trim() : null;
    };
    return links.map((link, i) => {
        const row = link.closest("tr");
        const cells = row ? [...row.querySelectorAll("td")].map(td => td.innerText.trim()) : [];
        return {
            row_index: i,
            href: link.getAttribute("href") || "",
            title: link.getAttribute("title") || "",
            tipo: pick(cells, "tipo") || (cells[0] || "Sentenza"),
            numero: pick(cells, "numdec") || (cells[1] || ""),
            anno: pick(cells, "anno") || (cells[2] || ""),
            corte: pick(cells, "autorita") || (cells[3] || ""),
            cells,
        };
    });
}
"""


class MefHttpError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        self.status = int(status)
        super().__init__(message or f"HTTP {status}")


class MefBlockedError(MefHttpError):
    """403/429 o segnali di blocco WAF/CAPTCHA — stop + checkpoint."""


class MefClient:
    def rows_from_fixture(self, html_path: Path) -> list[PortalRow]:
        html = html_path.read_text(encoding="utf-8")
        return parse_table_html(html)

    def rows_from_html(self, html: str) -> list[PortalRow]:
        return parse_table_html(html)


class LiveMefClient:
    """Si collega a Edge/Chromium già avviato con remote debugging."""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222") -> None:
        self.cdp_url = cdp_url
        self._pw = None
        self._browser = None
        self.page = None

    def __enter__(self) -> "LiveMefClient":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("Nessun context CDP — avvia Edge/Opera con remote debugging")
        pages = contexts[0].pages
        if not pages:
            raise RuntimeError("Nessuna tab aperta nel browser CDP")
        chosen = pages[0]
        for p in pages:
            try:
                if "giustiziatributaria" in (p.url or ""):
                    chosen = p
                    break
            except Exception:
                continue
        self.page = chosen
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def current_page_number(self) -> int:
        assert self.page is not None
        try:
            text = self.page.locator("a.page-link.active").first.inner_text(timeout=2000)
            return int(text.strip())
        except Exception:
            return 1

    def current_rows(self) -> list[PortalRow]:
        """Righe legate 1:1 al link Visualizza della stessa <tr>."""
        assert self.page is not None
        try:
            items = self.page.evaluate(EXTRACT_LINKED_ROWS_JS)
            rows = rows_from_link_dicts(items or [])
            if rows:
                return rows
        except Exception:
            pass
        # fallback fixture-like (senza href) — sconsigliato in live
        html = self.page.content()
        return parse_table_html(html)

    def extract_detail_meta(self) -> dict:
        assert self.page is not None
        page = self.page
        try:
            text = page.inner_text("body", timeout=15000)
        except Exception:
            text = page.content()
        return parse_detail_text(text, page_url=page.url)

    def _raise_if_blocked(self, status: int | None = None, body_snippet: str = "") -> None:
        snippet = (body_snippet or "").lower()
        if status in (403, 429):
            raise MefBlockedError(status, f"blocco HTTP {status}")
        if status and status >= 500:
            raise MefHttpError(status, f"HTTP {status}")
        if any(x in snippet for x in ("captcha", "access denied", "akamai", "bot manager")):
            raise MefBlockedError(status or 403, "possibile WAF/CAPTCHA")

    def open_detail(self, row: PortalRow) -> dict:
        """Apre il dettaglio via href della riga; ritorna metadati pagina dettaglio."""
        assert self.page is not None
        page = self.page
        href = (row.href or "").strip()
        if href.startswith("/"):
            href = f"{SITE_ORIGIN}{href}"
        if href.startswith("http"):
            resp = None
            try:
                # goto non espone sempre status; controlla dopo
                page.goto(href, timeout=60000, wait_until="domcontentloaded")
            except Exception as exc:
                msg = str(exc)
                if "403" in msg:
                    raise MefBlockedError(403, msg) from exc
                if "429" in msg:
                    raise MefBlockedError(429, msg) from exc
                raise
        else:
            # fallback: click sul Visualizza con stesso title/href se possibile
            if row.row_index is None:
                raise RuntimeError("riga senza href né row_index")
            page.locator(VISUALIZZA_SELECTOR).nth(row.row_index).click()
        page.wait_for_url("**/ricerca/dettaglio/**", timeout=60000)
        # segnali blocco sulla pagina
        try:
            body = page.content()
            self._raise_if_blocked(body_snippet=body[:2000])
        except MefBlockedError:
            raise
        except Exception:
            pass
        page.wait_for_selector(DETTAGLIO_SCARICA_BTN, timeout=30000)
        return self.extract_detail_meta()

    def download_pdf_bytes(self, tmp_path: Path) -> bytes:
        """Dalla pagina dettaglio già aperta, scarica i bytes PDF."""
        assert self.page is not None
        page = self.page
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with page.expect_download(timeout=90000) as download_info:
                page.locator(DETTAGLIO_SCARICA_BTN).click()
            download_info.value.save_as(str(tmp_path))
        except Exception:
            pdf_url = page.evaluate(
                """() => {
                    const btn = document.querySelector(
                      'button[title="Scarica il pdf del provvedimento"]'
                    );
                    return btn ? (btn.getAttribute('data-url') || btn.dataset?.url || '') : '';
                }"""
            )
            if not pdf_url:
                onclick = page.locator(DETTAGLIO_SCARICA_BTN).get_attribute("onclick") or ""
                m = re.search(r"https?://[^'\"\\s]+\.pdf", onclick, re.I)
                pdf_url = m.group(0) if m else ""
            if not pdf_url:
                raise RuntimeError("URL PDF non trovato sul dettaglio")
            if pdf_url.startswith("/"):
                pdf_url = f"{SITE_ORIGIN}{pdf_url}"
            resp = page.context.request.get(pdf_url, timeout=90000)
            status = resp.status
            body = resp.body()
            if status in (403, 429):
                raise MefBlockedError(status, body[:200].decode("utf-8", "ignore"))
            if status >= 500:
                raise MefHttpError(status, f"HTTP {status} su PDF")
            if not resp.ok:
                raise MefHttpError(status, f"HTTP {status} su PDF")
            tmp_path.write_bytes(body)

        data = tmp_path.read_bytes()
        if data.lstrip().lower().startswith(b"<!doctype") or data.lstrip().lower().startswith(
            b"<html"
        ):
            raise RuntimeError("download HTML invece di PDF")
        if not data.startswith(b"%PDF"):
            raise RuntimeError("download non è un PDF valido")
        return data

    def back_to_list(self, lista_url: str) -> None:
        assert self.page is not None
        page = self.page
        try:
            if page.url != lista_url:
                page.goto(lista_url, timeout=60000)
                page.wait_for_selector(VISUALIZZA_SELECTOR, timeout=45000)
        except Exception:
            pass
        time.sleep(0.3)

    def fetch_row_pdf(self, row: PortalRow, tmp_path: Path) -> tuple[bytes, dict]:
        """
        Apre dettaglio della riga (via href), legge metadati dettaglio, scarica PDF.
        Ritorna (pdf_bytes, detail_meta).
        """
        assert self.page is not None
        lista_url = self.page.url
        try:
            detail_meta = self.open_detail(row)
            data = self.download_pdf_bytes(tmp_path)
            return data, detail_meta
        finally:
            self.back_to_list(lista_url)
