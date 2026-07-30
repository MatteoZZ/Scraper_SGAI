"""HTTP client per lista e PDF ADM."""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from .parse import AdmItem, parse_list_html


class AdmHttpError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        self.status = int(status)
        super().__init__(message or f"HTTP {status}")


class AdmBlockedError(AdmHttpError):
    pass


class AdmClient:
    def __init__(self, *, user_agent: str, timeout: float = 60.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", 200) or 200
                data = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise AdmBlockedError(exc.code, f"HTTP {exc.code}") from exc
            raise AdmHttpError(exc.code, f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise AdmHttpError(0, f"network: {exc}") from exc

        if status in (403, 429):
            raise AdmBlockedError(status, f"HTTP {status}")
        if status >= 500:
            raise AdmHttpError(status, f"HTTP {status}")
        return data

    def fetch_list_html(self, list_url: str) -> str:
        return self._get(list_url).decode("utf-8", "replace")

    def list_items(self, list_url: str) -> list[AdmItem]:
        html = self.fetch_list_html(list_url)
        items = parse_list_html(html)
        for it in items:
            it.source_page = list_url
        return items

    def list_items_many(self, list_urls: list[str]) -> list[AdmItem]:
        """Unisce più pagine archivio, dedupe per URL senza query."""
        merged: list[AdmItem] = []
        seen: set[str] = set()
        for url in list_urls:
            try:
                page_items = self.list_items(url)
            except Exception:
                continue
            for it in page_items:
                key = it.href.split("?", 1)[0]
                if key in seen:
                    continue
                seen.add(key)
                it.row_index = len(merged)
                merged.append(it)
        return merged

    def list_items_from_fixture(self, path: Path) -> list[AdmItem]:
        html = path.read_text(encoding="utf-8")
        return parse_list_html(html)

    def download_pdf(self, url: str) -> bytes:
        return self._get(url)
