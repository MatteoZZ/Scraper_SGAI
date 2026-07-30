"""HTTP client Solr + PDF Italgiure."""
from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from .config import REFERER, SOLR_FIELDS, SOLR_SORT
from .download import looks_like_captcha, looks_like_html
from .names import meta_from_solr_doc

log = logging.getLogger("scraper_italgiure")


class ItalgiureHttpError(Exception):
    def __init__(self, status: int, message: str = "", *, transient: bool = False) -> None:
        self.status = int(status)
        self.transient = transient
        super().__init__(message or f"HTTP {status}")


class ItalgiureBlockedError(ItalgiureHttpError):
    pass


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ItalgiureHttpError) and exc.transient:
        return True
    msg = str(exc).lower()
    keys = (
        "getaddrinfo",
        "11002",
        "11001",
        "timed out",
        "timeout",
        "temporarily",
        "connection reset",
        "connection aborted",
        "name or service not known",
        "network is unreachable",
        "10054",
        "10060",
        "10053",
    )
    return any(k in msg for k in keys)


def build_solr_query(
    *,
    kind: str = "snciv",
    sezioni: tuple[str, ...] = ("5", "U"),
    anno: str | None = None,
) -> str:
    sez = " OR ".join(f'szdec:"{s}"' for s in sezioni)
    parts = [f'kind:"{kind}"', f"({sez})"]
    if anno:
        parts.append(f'anno:"{anno}"')
    return " AND ".join(parts)


class ItalgiureClient:
    def __init__(
        self,
        *,
        user_agent: str,
        solr_url: str,
        ssl_verify: bool = False,
        timeout: float = 60.0,
    ) -> None:
        self.user_agent = user_agent
        self.solr_url = solr_url
        self.ssl_verify = ssl_verify
        self.timeout = timeout
        self._ssl_ctx = None if ssl_verify else ssl._create_unverified_context()

    def _open(self, req: urllib.request.Request):
        return urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx)

    def _headers(self, *, accept: str = "*/*") -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
            "Referer": REFERER,
            "Origin": "https://www.italgiure.giustizia.it",
            "X-Requested-With": "XMLHttpRequest",
        }

    def solr_select(
        self,
        query: str,
        *,
        start: int = 0,
        rows: int = 50,
        fl: str = SOLR_FIELDS,
        sort: str = SOLR_SORT,
        retries: int = 8,
    ) -> dict[str, Any]:
        payload = urllib.parse.urlencode(
            {
                "q": query,
                "start": str(start),
                "rows": str(rows),
                "wt": "json",
                "fl": fl,
                "hl": "false",
                "sort": sort,
                "app.query": "",
            }
        ).encode("utf-8")
        url = self.solr_url + "?app.query="
        last: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    **self._headers(accept="application/json"),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                with self._open(req) as resp:
                    status = getattr(resp, "status", 200) or 200
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    raise ItalgiureBlockedError(exc.code, f"HTTP {exc.code}") from exc
                last = ItalgiureHttpError(exc.code, f"HTTP {exc.code}")
                if attempt + 1 >= retries:
                    raise last from exc
                wait = min(60.0, 2.0 * (2**attempt))
                log.warning(
                    "Solr HTTP %s (tentativo %s/%s) — attendo %.0fs",
                    exc.code,
                    attempt + 1,
                    retries,
                    wait,
                )
                time.sleep(wait)
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = ItalgiureHttpError(0, f"network: {exc}", transient=True)
                if attempt + 1 >= retries:
                    raise last from exc
                wait = min(60.0, 2.0 * (2**attempt))
                log.warning(
                    "rete/DNS Solr fallita (tentativo %s/%s): %s — attendo %.0fs",
                    attempt + 1,
                    retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue

            if status in (403, 429):
                raise ItalgiureBlockedError(status, f"HTTP {status}")
            if looks_like_html(raw) or looks_like_captcha(raw):
                raise ItalgiureBlockedError(
                    status, "risposta Solr non JSON (blocco/captcha)"
                )
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ItalgiureHttpError(status, f"JSON invalido: {exc}") from exc

        raise last or ItalgiureHttpError(0, "solr_select fallito", transient=True)

    def count(self, query: str) -> int:
        data = self.solr_select(query, start=0, rows=0)
        return int(data.get("response", {}).get("numFound", 0))

    def iter_docs(
        self,
        query: str,
        *,
        rows: int = 50,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        start = 0
        yielded = 0
        while True:
            data = self.solr_select(query, start=start, rows=rows)
            docs = data.get("response", {}).get("docs") or []
            if not docs:
                break
            for doc in docs:
                yield doc
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            start += len(docs)
            num_found = int(data.get("response", {}).get("numFound", 0))
            if start >= num_found:
                break

    def list_metas(
        self,
        query: str,
        *,
        rows: int = 50,
        limit: int | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        num_found = self.count(query)
        metas: list[dict[str, Any]] = []
        for doc in self.iter_docs(query, rows=rows, limit=limit):
            metas.append(meta_from_solr_doc(doc))
        return num_found, metas

    def list_metas_from_fixture(self, path: Path) -> tuple[int, list[dict[str, Any]]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "response" in payload:
            docs = payload["response"].get("docs") or []
            num_found = int(payload["response"].get("numFound", len(docs)))
        elif isinstance(payload, list):
            docs = payload
            num_found = len(docs)
        else:
            raise ValueError("fixture Solr non riconosciuta")
        return num_found, [meta_from_solr_doc(d) for d in docs]

    def download_pdf(self, url: str, *, retries: int = 6) -> bytes:
        last: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(
                url,
                headers={
                    **self._headers(accept="application/pdf,*/*"),
                    "Upgrade-Insecure-Requests": "1",
                },
                method="GET",
            )
            try:
                with self._open(req) as resp:
                    status = getattr(resp, "status", 200) or 200
                    data = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    raise ItalgiureBlockedError(exc.code, f"HTTP {exc.code}") from exc
                last = ItalgiureHttpError(exc.code, f"HTTP {exc.code}")
                if attempt + 1 >= retries:
                    raise last from exc
                time.sleep(min(45.0, 2.0 * (2**attempt)))
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = ItalgiureHttpError(0, f"network: {exc}", transient=True)
                if attempt + 1 >= retries:
                    raise last from exc
                wait = min(60.0, 2.0 * (2**attempt))
                log.warning(
                    "rete/DNS PDF fallita (tentativo %s/%s): %s — attendo %.0fs",
                    attempt + 1,
                    retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue

            if status in (403, 429):
                raise ItalgiureBlockedError(status, f"HTTP {status}")
            if looks_like_captcha(data) or (
                looks_like_html(data) and data[:5] != b"%PDF-"
            ):
                raise ItalgiureBlockedError(status, "captcha/HTML al posto del PDF")
            return data

        raise last or ItalgiureHttpError(0, "download_pdf fallito", transient=True)
