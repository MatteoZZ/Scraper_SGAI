"""Client SPARQL Cellar + download PDF/PDFA item."""
from __future__ import annotations

import http.client
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
# Niente */*: Cellar su alcuni item "pdf" restituisce JPEG/HTML invece del PDF.
ACCEPT_PDF = (
    "application/pdf, application/pdf;type=pdfa1a, "
    "application/pdf;type=pdfa2a, application/pdf;type=pdfx"
)
ACCEPT_PDF_STRICT = "application/pdf"

# Preferisci PDF/A e pdfx: il tipo letterale "pdf" a volte è uno stream non scaricabile (406)
# mentre esiste un sibling pdfx con il binario vero.
_TYPE_RANK = {
    "pdfa1a": 100,
    "pdfa2a": 95,
    "pdfa1b": 90,
    "pdfa2b": 85,
    "pdfx": 80,
    "pdfx4": 75,
    "pdf1x": 70,
    "pdf": 10,
}


def _type_rank(manif_type: str) -> int:
    t = (manif_type or "").strip().lower()
    if t in _TYPE_RANK:
        return _TYPE_RANK[t]
    if t.startswith("pdfa"):
        return 88
    if t.startswith("pdfx"):
        return 72
    if t.startswith("pdf"):
        return 20
    return 0


def _pdf_magic_ok(data: bytes) -> bool:
    """True se il payload è PDF (anche con \\r\\n iniziali da Cellar)."""
    if not data:
        return False
    i = 0
    limit = min(len(data), 64)
    while i < limit and data[i] in b"\r\n\t\x00 ":
        i += 1
    return data[i : i + 5] == b"%PDF-"


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
    if isinstance(exc, (TimeoutError, http.client.IncompleteRead)):
        return True
    if isinstance(exc, EurlexHttpError):
        # Cellar SPARQL spesso risponde 5xx su OFFSET alti (catalogo ENG/ITA lunghi)
        if exc.transient or exc.status in (500, 502, 503, 504, 408):
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
        "incompleteread",
        "getaddrinfo",
        "11001",
        "11002",
        "10054",
        "10060",
        "http 500",
        "http 502",
        "http 503",
        "sparql request failed",
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
        self,
        url: str,
        *,
        accept: str = "*/*",
        accept_language: str | None = None,
        timeout: float | None = None,
    ) -> bytes:
        url = _prefer_https(url)
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        if accept_language:
            # Cellar vuole ISO 639-3 minuscolo (eng/ita) sulla work URI
            headers["Accept-Language"] = accept_language.strip().lower()
        req = urllib.request.Request(url, headers=headers)
        to = self.timeout if timeout is None else timeout
        try:
            with urllib.request.urlopen(req, timeout=to) as resp:
                status = getattr(resp, "status", 200) or 200
                data = resp.read()
        except http.client.IncompleteRead as exc:
            partial = exc.partial or b""
            if _pdf_magic_ok(partial) and len(partial) > 50_000:
                log.warning(
                    "IncompleteRead ma payload PDF parziale grande (%s B) — riprovo",
                    len(partial),
                )
            raise EurlexHttpError(
                0, f"IncompleteRead ({len(partial)} B)", transient=True
            ) from exc
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise EurlexBlockedError(exc.code, f"HTTP {exc.code}") from exc
            # 5xx = sovraccarico/bug Cellar (tipico con OFFSET grandi), ritentabile
            # 406 = content negotiation: non ritentare lo stesso Accept
            transient = exc.code >= 500 or exc.code == 408
            raise EurlexHttpError(
                exc.code, f"HTTP {exc.code}", transient=transient
            ) from exc
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
        accept_language: str | None = None,
        retries: int = 6,
        timeout: float | None = None,
    ) -> bytes:
        last: Exception | None = None
        dns_hits = 0
        for i in range(retries):
            try:
                return self._get_once(
                    url,
                    accept=accept,
                    accept_language=accept_language,
                    timeout=timeout,
                )
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
            # 5xx = sovraccarico/bug Cellar (tipico con OFFSET grandi), ritentabile
            transient = exc.code >= 500 or exc.code == 408
            raise EurlexHttpError(
                exc.code, f"HTTP {exc.code}", transient=transient
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EurlexHttpError(0, f"network: {exc}", transient=True) from exc
        if status in (403, 429):
            raise EurlexBlockedError(status, f"HTTP {status}")
        return data

    def sparql_json(self, query: str, *, retries: int = 8) -> dict[str, Any]:
        last: Exception | None = None
        for i in range(retries):
            try:
                raw = self._sparql_once(query)
                return json.loads(raw.decode("utf-8"))
            except EurlexBlockedError:
                raise
            except Exception as exc:
                last = exc
                # DNS blip Windows (11001): ritenta, non abortire subito la run
                if _is_dns_failure(exc):
                    wait = min(180.0, 15.0 * (2**i))
                    label = "DNS"
                else:
                    wait = min(90.0, 5.0 * (2**i))
                    label = "SPARQL timeout/rete"
                log.warning(
                    "%s (tentativo %s/%s, timeout=%.0fs): %s — attendo %.0fs",
                    label,
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

    def _candidate_items_for_work(
        self, *, work: str, lang: str
    ) -> list[tuple[str, str]]:
        """Item PDF per work+lingua, ordinati dal migliore (pdfa/pdfx → pdf)."""
        q = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?t ?item WHERE {{
  BIND(<{work}> AS ?work)
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language
    <http://publications.europa.eu/resource/authority/language/{lang}> .
  ?manif cdm:manifestation_manifests_expression ?exp .
  ?manif cdm:manifestation_type ?t .
  {PDF_TYPE_FILTER}
  ?item cdm:item_belongs_to_manifestation ?manif .
}}
LIMIT 40
"""
        data = self.sparql_json(q, retries=4)
        scored: list[tuple[int, str, str]] = []
        seen_u: set[str] = set()
        for b in data.get("results", {}).get("bindings") or []:
            item = b.get("item", {}).get("value", "")
            t = b.get("t", {}).get("value", "pdf")
            if not item:
                continue
            url = _prefer_https(item)
            if url in seen_u:
                continue
            seen_u.add(url)
            rank = _type_rank(t)
            bonus = 1 if item.rstrip("/").endswith("DOC_1") else 0
            scored.append((rank * 10 + bonus, t, url))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(t, u) for _, t, u in scored]

    def _best_item_for_work(
        self, *, work: str, lang: str
    ) -> tuple[str, str] | None:
        cands = self._candidate_items_for_work(work=work, lang=lang)
        return cands[0] if cands else None

    def _page_work_uris(
        self,
        *,
        scope: str,
        lang: str,
        after_work: str,
        page_size: int,
    ) -> list[str]:
        """Keyset su STR(?work): evita OFFSET alti che su Cellar tornano HTTP 500."""
        after_filter = ""
        if after_work:
            # escape minimo per literal SPARQL
            safe = after_work.replace("\\", "\\\\").replace('"', '\\"')
            after_filter = f'FILTER(STR(?work) > "{safe}")'
        q = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?work WHERE {{
  {scope}
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language
    <http://publications.europa.eu/resource/authority/language/{lang}> .
  ?manif cdm:manifestation_manifests_expression ?exp .
  ?manif cdm:manifestation_type ?t .
  {PDF_TYPE_FILTER}
  {after_filter}
}}
ORDER BY ?work
LIMIT {int(page_size)}
"""
        data = self.sparql_json(q)
        out: list[str] = []
        for b in data.get("results", {}).get("bindings") or []:
            w = b.get("work", {}).get("value", "")
            if w:
                out.append(w)
        return out

    def _collect_work_uris(
        self,
        *,
        scope: str,
        lang: str,
        page_size: int = 200,
    ) -> set[str]:
        """Tutte le work URI con PDF in una lingua (per dedupe ITA→ENG al resume)."""
        found: set[str] = set()
        after = ""
        while True:
            page = self._page_work_uris(
                scope=scope, lang=lang, after_work=after, page_size=page_size
            )
            if not page:
                break
            found.update(page)
            after = page[-1]
            if len(page) < page_size:
                break
            time.sleep(0.5)
        return found

    def _items_for_works(
        self, *, works: list[str], lang: str
    ) -> dict[str, dict[str, Any]]:
        """Best PDF item per work (batch VALUES)."""
        if not works:
            return {}
        values = " ".join(f"<{w}>" for w in works)
        q = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?work ?celex ?t ?item WHERE {{
  VALUES ?work {{ {values} }}
  OPTIONAL {{ ?work cdm:resource_legal_id_celex ?celex }}
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_uses_language
    <http://publications.europa.eu/resource/authority/language/{lang}> .
  ?manif cdm:manifestation_manifests_expression ?exp .
  ?manif cdm:manifestation_type ?t .
  {PDF_TYPE_FILTER}
  ?item cdm:item_belongs_to_manifestation ?manif .
}}
"""
        data = self.sparql_json(q, retries=6)
        best_by_work: dict[str, dict[str, Any]] = {}
        for b in data.get("results", {}).get("bindings") or []:
            work = b.get("work", {}).get("value", "")
            item = b.get("item", {}).get("value", "")
            if not work or not item:
                continue
            t = b.get("t", {}).get("value", "pdf")
            celex = b.get("celex", {}).get("value") or work.rsplit("/", 1)[-1]
            score = _type_rank(t) * 10 + (
                1 if item.rstrip("/").endswith("DOC_1") else 0
            )
            prev = best_by_work.get(work)
            if prev is None or score > prev["_score"]:
                best_by_work[work] = {
                    "_score": score,
                    "manif_type": t,
                    "celex": celex,
                    "item": item,
                }
        return best_by_work

    def iter_pdf_items(
        self,
        *,
        eurovoc: str,
        page_size: int = 50,
        caselaw_only: bool = False,
        limit: int | None = None,
        start_cursor: dict[str, Any] | None = None,
        on_cursor: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        yielded = 0
        passes: list[tuple[str, str, str]] = [
            (scope_name, scope, lang)
            for scope_name, scope in self._scopes(
                eurovoc=eurovoc, caselaw_only=caselaw_only
            )
            for lang in self.languages
        ]
        start_i = 0
        start_after = ""
        if start_cursor:
            want_scope = start_cursor.get("scope")
            want_lang = start_cursor.get("lang")
            # supporta vecchio cursore a offset (ignorato) e nuovo after_work
            start_after = str(start_cursor.get("after_work") or "")
            for i, (sn, _, lg) in enumerate(passes):
                if sn == want_scope and lg == want_lang:
                    start_i = i
                    break

        for pi, (scope_name, scope, lang) in enumerate(passes):
            if pi < start_i:
                log.info(
                    "preload dedupe catalogo %s %s (resume cursor)",
                    scope_name,
                    lang,
                )
                try:
                    seen |= self._collect_work_uris(
                        scope=scope, lang=lang, page_size=max(page_size, 100)
                    )
                    log.info("preload %s %s: %s works", scope_name, lang, len(seen))
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "preload dedupe %s %s fallito (%s) — "
                        "rischio doppioni ENG, continuo",
                        scope_name,
                        lang,
                        exc,
                    )
                continue

            after_work = start_after if pi == start_i else ""
            if after_work or (start_i == pi and start_cursor):
                log.info(
                    "riprendo catalogo keyset %s %s after=%s (dedupe=%s)",
                    scope_name,
                    lang,
                    (after_work.rsplit("/", 1)[-1] if after_work else "(start)"),
                    len(seen),
                )

            page_i = 0
            while True:
                if on_cursor is not None:
                    on_cursor(
                        {
                            "scope": scope_name,
                            "lang": lang,
                            "after_work": after_work,
                            "page": page_i,
                        }
                    )
                try:
                    works = self._page_work_uris(
                        scope=scope,
                        lang=lang,
                        after_work=after_work,
                        page_size=page_size,
                    )
                except Exception as exc:
                    # Salva cursore corrente prima di propagare (runner → network_pause)
                    if on_cursor is not None:
                        on_cursor(
                            {
                                "scope": scope_name,
                                "lang": lang,
                                "after_work": after_work,
                                "page": page_i,
                            }
                        )
                    raise

                if not works:
                    break

                best_by_work = self._items_for_works(works=works, lang=lang)
                page_new = 0
                page_dup = 0
                page_noitem = 0
                for work in works:
                    if work in seen:
                        page_dup += 1
                        continue
                    seen.add(work)
                    chosen = best_by_work.get(work)
                    if not chosen:
                        page_noitem += 1
                        continue
                    pref = self.name_prefix
                    celex = chosen["celex"]
                    page_new += 1
                    yield {
                        "ok": True,
                        "fonte": pref,
                        "scope": scope_name,
                        "celex": celex,
                        "lang": lang,
                        "work": work,
                        "manif_type": chosen["manif_type"],
                        "url": _prefer_https(chosen["item"]),
                        "nomeBase": f"{pref}_{_safe(celex)}_{lang}",
                        "nomeFile": f"{pref}_{_safe(celex)}_{lang}.pdf",
                    }
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        return

                # rumore: in console bastano skip checkpoint / DOWNLOAD
                log.debug(
                    "catalogo %s %s page=%s after=%s: works=%s nuovi=%s "
                    "già_in_lista=%s no_item=%s",
                    scope_name,
                    lang,
                    page_i,
                    works[0].rsplit("/", 1)[-1][:36],
                    len(works),
                    page_new,
                    page_dup,
                    page_noitem,
                )
                after_work = works[-1]
                page_i += 1
                if len(works) < page_size:
                    break
                # pagine "tutto già visto": meno pausa; altrimenti rispetta Cellar
                time.sleep(0.4 if page_new == 0 else 1.0)

    def _strip_pdf_bom(self, data: bytes) -> bytes:
        i = 0
        while i < min(len(data), 64) and data[i] in b"\r\n\t\x00 ":
            i += 1
        return data[i:] if i else data

    def _fetch_pdf_bytes(
        self, url: str, *, accept_language: str | None = None
    ) -> bytes:
        last: Exception | None = None
        # Lista pdfa/pdfx PRIMA: Cellar serve i binari come
        # `application/pdf;type=pdfx` e `application/pdf` nudo non fa match
        # (406) — */* ultimo come rete di sicurezza (il magic check valida).
        for accept in (ACCEPT_PDF, ACCEPT_PDF_STRICT, "*/*"):
            try:
                data = self._get(
                    url,
                    accept=accept,
                    accept_language=accept_language,
                    retries=4,
                )
            except EurlexHttpError as exc:
                last = exc
                # 406/400 = questo Accept non negozia: prova il prossimo,
                # NON fermarti (il fallback pdfa/pdfx è quello che funziona).
                if exc.status in (406, 404, 400):
                    continue
                if exc.status in (500, 502, 503, 504):
                    continue
                raise
            if _pdf_magic_ok(data):
                return self._strip_pdf_bom(data)
            kind = "jpeg" if data[:2] == b"\xff\xd8" else "unknown"
            if data.lstrip()[:1] == b"<":
                kind = "html/xml"
            head = data[:40]
            last = EurlexHttpError(
                0,
                f"risposta non PDF ({kind}) da {url} (head={head!r})",
                transient=True,
            )
            log.warning("%s", last)
            # JPEG/RDF: inutile ritentare altri Accept sulla stessa URL
            break
        assert last is not None
        raise last

    def download_pdf(
        self,
        url: str,
        *,
        work: str | None = None,
        lang: str | None = None,
    ) -> bytes:
        tried: set[str] = set()
        last: Exception | None = None
        saw_transient = False
        queue: list[tuple[str, str, str | None]] = [
            ("catalog", _prefer_https(url), None)
        ]
        if work and lang:
            try:
                for t, u in self._candidate_items_for_work(work=work, lang=lang):
                    queue.append((t, u, None))
            except Exception as exc:  # noqa: BLE001
                log.warning("resolve item alternativi fallito: %s", exc)
            # Content negotiation sulla work URI: spesso salva i casi in cui
            # l'item SPARQL "pdf" è JPEG e il binario vero è pdfx / negoziato.
            queue.append(("work-negotiate", _prefer_https(work), lang.lower()))

        for label, cand, alang in queue:
            if cand in tried and label != "work-negotiate":
                continue
            key = f"{cand}|{alang or ''}"
            if key in tried:
                continue
            tried.add(key)
            if label != "work-negotiate":
                tried.add(cand)
            try:
                data = self._fetch_pdf_bytes(cand, accept_language=alang)
                if label != "catalog":
                    log.info(
                        "OK via %s (%s)",
                        label,
                        cand.rsplit("/", 1)[-1],
                    )
                return data
            except Exception as exc:  # noqa: BLE001
                last = exc
                if _is_transient(exc):
                    saw_transient = True
                log.warning("download fallito [%s] (%s): %s", label, cand, exc)

        if last is None:
            last = EurlexHttpError(0, "download_pdf: nessun URL", transient=False)
        # Se tutti i candidati sono falliti ma c'erano errori di rete, resta ritentabile
        transient = saw_transient and not (
            isinstance(last, EurlexHttpError) and last.status in (404, 406)
        )
        if isinstance(last, EurlexHttpError):
            raise EurlexHttpError(
                last.status, str(last), transient=transient
            ) from last
        raise EurlexHttpError(0, str(last), transient=transient) from last
