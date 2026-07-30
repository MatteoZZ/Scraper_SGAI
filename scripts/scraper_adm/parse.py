"""Parse lista HTML circolari ADM."""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from .config import SITE_ORIGIN
from .names import meta_from_url, parse_list_title


@dataclass
class AdmItem:
    href: str
    text: str
    row_index: int = 0
    source_page: str = ""

    def to_meta(self) -> dict:
        meta = parse_list_title(self.text, url=self.href)
        # Se il testo del link è solo la coda (es. "Sostituita dalla..."),
        # prova a ricavare protocollo/nome dall'URL.
        if not meta.get("ok"):
            meta = parse_list_title(self.text + " " + self.href, url=self.href)
        if not meta.get("ok"):
            meta = meta_from_url(self.href)
        meta["url"] = self.href
        meta["row_index"] = self.row_index
        if self.source_page:
            meta["sourcePage"] = self.source_page
        return meta


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._in_a = False

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if "/documents/" in href and ".pdf" in href.lower():
            self._href = href
            self._buf = []
            self._in_a = True

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a and self._href:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self.links.append((self._href, text))
            self._in_a = False
            self._href = None

    def handle_data(self, data):
        if self._in_a:
            self._buf.append(data)


def parse_list_html(html: str, *, base_url: str = SITE_ORIGIN) -> list[AdmItem]:
    parser = _LinkParser()
    parser.feed(html or "")
    out: list[AdmItem] = []
    seen: set[str] = set()
    for href, text in parser.links:
        abs_url = urljoin(base_url, href)
        # normalizza senza query per dedupe (t=timestamp cambia)
        key = abs_url.split("?", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(AdmItem(href=abs_url, text=text, row_index=len(out)))
    return out
