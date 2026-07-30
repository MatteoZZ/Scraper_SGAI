"""Client SPARQL Cellar + download PDF/PDFA item."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterator

import urllib.error
import urllib.parse
import urllib.request

from .config import LANG_PREF

log = logging.getLogger("scraper_eurlex")

PDF_TYPE_FILTER = 'FILTER(STRSTARTS(STR(?t), "pdf"))'
ACCEPT_PDF = (
    "application/pdf, application/pdf;type=pdfa1a, "
    "application/pdf;type=pdfa2a, */*"
)


class EurlexHttpError(Exception):
    def __init__(self, status: int, message: str = "", *, transient: bool = False) -> None:
        self.status = int(status)
        self.transient = transient
        super().__init__(message or f"HTTP {status}")


class EurlexBlockedError(EurlexHttpError):
    pass


def _safe(celex: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (celex or "doc").strip())[:80]


def _is_dns_failure(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        k in msg
        for k in (
            "getaddrinfo",
            "11001",
            "11002",
            "name or service not known",
            "nodename nor servname",
        )
    )


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, EurlexHttpError) and exc.transient:
        return True
    if _is_dns_failure(exc):
        return True
    msg = str(exc).lower()
    keys = (
        "timed out",
        "timeout",
        "temporarily",
        "connection reset",
        "connection aborted",
        "network is unreachable",
        "10054",
        "10060",
    )
    return any(k in msg for k in keys)


def check_dns(host: str = "publications.europa.eu") -> None:
    """Fallisce subito se il DNS non risolve l'host EUR-Lex."""
    import socket

    try:
        socket.getaddrinfo(host, 443)
    except OSError as exc:
        raise EurlexHttpError(
            0,
            f"DNS non risolve {host}: {exc}. "
            "Prova: nslookup publications.europa.eu — poi flushdns / cambia DNS / VPN",
            transient=True,
        ) from exc


def _prefer_https(url: str) -> str:
    if url.startswith("http://publications.europa.eu"):
        return "https://" + url[len("http://") :]
    return url


class EurlexClient:
    def __init__(
        self,
        *,
        sparql_url: str,
        user_agent: str,
        timeout: float = 180.0,
        sparql_timeout: float = 150.0,
        name_prefix: str = "EURLEX",
        mode: str = "eurovoc",
        languages: tuple[str, ...] | None = None,
    ) -> None:
        self.sparql_url = sparql_url
        self.user_agent = user_agent
        self.timeout = timeout
        self.sparql_timeout = sparql_timeout
        self.name_prefix = name_prefix
        self.mode = mode
        self.languages = languages or LANG_PREF

    def _get_once(
        self, url: str, *, accept: str = "*/*", timeout: float | None = None
    ) -> bytes:
        url = _prefer_https(url)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": accept},
        )
        to = self.timeout if timeout is None else timeout
        try:
            with urllib.request.urlopen(req, timeout=to) as resp:
                status = getattr(resp, "status", 200) or 200
                data = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise EurlexBlockedError(exc.code, f"HTTP {exc.code}") from exc
            raise EurlexHttpError(exc.code, f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EurlexHttpError(0, f"network: {exc}", transient=True) from exc
        if status in (403, 429):
            raise EurlexBlockedError(status, f"HTTP {status}")
        return data

    def _get(
        self,
        url: str,
        *,
        accept: str = "*/*",
        retries: int = 6,
        timeout: float | None = None,
    ) -> bytes:
        last: Exception | None = None
        dns_hits = 0
        for i in range(retries):
            try:
                return self._get_once(url, accept=accept, timeout=timeout)
            except EurlexBlockedError:
                raise
            except Exception as exc:
                last = exc
                if not _is_transient(exc) and not isinstance(exc, EurlexHttpError):
                    raise
                if isinstance(exc, EurlexHttpError) and not exc.transient and exc.status != 0:
                    raise
                if _is_dns_failure(exc):
                    dns_hits += 1
                    # DNS rotto: inutile ritentare 6 volte a raffica
                    if dns_hits >= 2:
                        raise EurlexHttpError(
                            0,
                            f"DNS persistente: {exc}. "
                            "Controlla internet/VPN/DNS e rilancia.",
                            transient=True,
                        ) from exc
                wait = min(60.0, 2.0 * (2**i))
                label = "DNS" if _is_dns_failure(exc) else "rete/timeout"
                log.warning(
                    "%s fallita (tentativo %s/%s): %s — attendo %.0fs",
                    label,
                    i + 1,
                    retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
        assert last is not None
        raise last

    def _sparql_once(self, query: str) -> bytes:
        """POST SPARQL (GET con query lunghe va spesso in timeout sul Cellar)."""
        body = urllib.parse.urlencode(
            {"query": query, "format": "application/sparql-results+json"}
        ).encode("utf-8")
        req = urllib.request.Request(
            _prefer_https(self.sparql_url),
            data=body,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.sparql_timeout) as resp:
                status = getattr(resp, "status", 200) or 200
                data = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise EurlexBlockedError(exc.code, f"HTTP {exc.code}") from exc
            raise EurlexHttpError(exc.code, f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EurlexHttpError(0, f"network: {exc}", transient=True) from exc
        if status in (403, 429):
            raise EurlexBlockedError(status, f"HTTP {status}")
        return data

    def sparql_json(self, query: str, *, retries: int = 5) -> dict[str, Any]:
        last: Exception | None = None
        for i in range(retries):
            try:
                raw = self._sparql_once(query)
                return json.loads(raw.decode("utf-8"))
            except EurlexBlockedError:
                raise
            except Exception as exc:
                last = exc
                if _is_dns_failure(exc):
                    raise
                wait = min(90.0, 5.0 * (2**i))
                log.warning(
                    "SPARQL timeout/rete (tentativo %s/%s, timeout=%.0fs): %s — attendo %.0fs",
                    i + 1,
                    retries,
                    self.sparql_timeout,
                    exc,
                    wait,
                )
                time.sleep(wait)
        assert last is not None
        raise last

    def _scopes(self, *, eurovoc: str, caselaw_only: bool) -> list[tuple[str, str]]:
        if self.mode == "curia_directory":
            return [
                (
                    "dir_4.10",
                    """
  ?work cdm:case-law_is_about_concept_new_case-law ?code .
  FILTER(CONTAINS(STR(?code), "4.10"))
""",
                ),
                (
                    "dir_B-10",
                    """
  ?work cdm:case-law_is_about_concept_case-law ?code .
  FILTER(CONTAINS(STR(?code), "B-10") || CONTAINS(STR(?code), "B.10"))
""",
                ),
            ]
        scope = f"?work cdm:work_is_about_concept_eurovoc <{eurovoc}> ."
        if caselaw_only:
            scope += """
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(STRSTARTS(STR(?celex), "6"))
"""
        return [("eurovoc", scope)]

    def count_works(self, *, eurovoc: str, caselaw_only: bool = False) -> int:
        total = 0
        # COUNT pesante: pochi tentativi, non bloccare la run
        old_to = self.sparql_timeout
        self.sparql_timeout = min(old_to, 90.0)
        try:
            for name, scope in self._scopes(eurovoc=eurovoc, caselaw_only=caselaw_only):
                q = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT (COUNT(DISTINCT ?work) AS ?c) WHERE {{
  {scope}
  ?exp cdm:expression_belongs_to_work ?work .
  ?manif cdm:manifestation_manifests_expression ?exp .
  ?manif cdm:manifestation_type ?t .
  {PDF_TYPE_FILTER}
}}
"""
                try:
                    data = self.sparql_json(q, retries=2)
                    bindings = data.get("results", {}).get("bindings") or []
                    n = int(bindings[0]["c"]["value"]) if bindings else 0
                    log.info("count %s = %s", name, n)
                    total += n
                except Exception as exc:
                    log.warning("count %s fallito: %s", name, exc)
        finally:
            self.sparql_timeout = old_to
        return total

    def iter_pdf_items(
        self,
        *,
        eurovoc: str,
        page_size: int = 50,
        caselaw_only: bool = False,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        yielded = 0
        for scope_name, scope in self._scopes(
            eurovoc=eurovoc, caselaw_only=caselaw_only
        ):
            for lang in self.languages:
                offset = 0
                while True:
                    q = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?work ?celex ?item WHERE {{
  {scope}
  OPTIONAL {{ ?work cdm:resource_legal_id_celex ?celex }}
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/{lang}> .
  ?manif cdm:manifestation_manifests_expression ?exp .
  ?manif cdm:manifestation_type ?t .
  {PDF_TYPE_FILTER}
  ?item cdm:item_belongs_to_manifestation ?manif .
}}
ORDER BY ?celex ?work
LIMIT {int(page_size)}
OFFSET {int(offset)}
"""
                    data = self.sparql_json(q)
                    bindings = data.get("results", {}).get("bindings") or []
                    if not bindings:
                        break
                    page_new = 0
                    page_dup = 0
                    for b in bindings:
                        work = b.get("work", {}).get("value", "")
                        if not work:
                            continue
                        if work in seen:
                            page_dup += 1
                            continue
                        seen.add(work)
                        celex = (
                            b.get("celex", {}).get("value")
                            or work.rsplit("/", 1)[-1]
                        )
                        item = b.get("item", {}).get("value", "")
                        if not item:
                            continue
                        pref = self.name_prefix
                        page_new += 1
                        yield {
                            "ok": True,
                            "fonte": pref,
                            "scope": scope_name,
                            "celex": celex,
                            "lang": lang,
                            "work": work,
                            "url": _prefer_https(item),
                            "nomeBase": f"{pref}_{_safe(celex)}_{lang}",
                            "nomeFile": f"{pref}_{_safe(celex)}_{lang}.pdf",
                        }
                        yielded += 1
                        if limit is not None and yielded >= limit:
                            return
                    log.info(
                        "catalogo %s %s offset=%s: ricevuti=%s nuovi=%s già_in_lista=%s "
                        "(non è un download: il runner poi salta i PDF già in cartella)",
                        scope_name,
                        lang,
                        offset,
                        len(bindings),
                        page_new,
                        page_dup,
                    )
                    offset += len(bindings)
                    if len(bindings) < page_size:
                        break
                    # Cellar si satura se le pagine arrivano a raffica
                    time.sleep(1.5)

    def download_pdf(self, url: str) -> bytes:
        data = self._get(_prefer_https(url), accept=ACCEPT_PDF, retries=6)
        if data[:5] != b"%PDF-":
            raise EurlexHttpError(0, "risposta non PDF (Accept/type errato)")
        return data
