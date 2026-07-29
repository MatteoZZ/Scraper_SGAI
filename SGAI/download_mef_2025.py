#!/usr/bin/env python3
"""
Scarica TUTTE le sentenze dal portale MEF (Banca dati Giurisprudenza Tributaria),
rinomina: Sentenza_{CODICE}_{NUMERO}_{ANNO}.pdf

NON usa italgiure/Cassazione — solo portale MEF + portal_to_filename.py (istruzioni SGAI).

Il portale MEF ha **Akamai anti-bot**: senza browser reale la ricerca va in 403 / Access Denied.
Edge minimizzato e' obbligatorio (non headless). Non esiste API pubblica terminale-only.

Esempi:
  # Test rapido 2025
  python download_mef_2025.py --year 2025 --trimestre 1 --materia A010 --max-pagine 1

  # Riprendi D040 Q1 dalla pagina 21 (dopo ricerca OK in Edge)
  python download_mef_2025.py --year 2025 --trimestre 1 --materia D040 --start-pagina 21

  # Tutto il 2025 — solo sentenze NON in cache_nomi_base_2025.txt
  python download_mef_2025.py --year 2025

  # Tutto il DB MEF da 2000 a oggi (lungo: giorni/settimane)
  python download_mef_2025.py --start-year 2000 --end-year 2025

  # Riprendi dopo interruzione
  python download_mef_2025.py --resume

  # Scarica anche se gia sul server SGAI (solo salta se file locale esiste)
  python download_mef_2025.py --year 2025 --no-skip-server

  # 4 processi in parallelo (un trimestre ciascuno, profilo Edge e porta CDP diversi):
  python download_mef_2025.py --year 2025 --trimestre 1 --profile-dir .edge_profile_mef_q1 --cdp-port 9222
  ...

  # 2 processi in parallelo (un semestre ciascuno):
  python download_mef_2025.py --year 2025 --semestre 1 --profile-dir .edge_profile_mef_s1 --cdp-port 9222
  python download_mef_2025.py --year 2025 --semestre 2 --profile-dir .edge_profile_mef_s2 --cdp-port 9223
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

from playwright.sync_api import Error as PlaywrightError

try:
    from mef_vpn import (
        get_public_ip,
        is_ip_burned_signal,
        make_vpn_service,
        variable_download_delay,
    )
except ImportError:  # pragma: no cover
    get_public_ip = None  # type: ignore
    make_vpn_service = None  # type: ignore
    variable_download_delay = None  # type: ignore

    def is_ip_burned_signal(
        *,
        consecutive_403: int = 0,
        search_failed_after_heal: bool = False,
        local_403_heals: int = 0,
        access_denied_page: bool = False,
    ) -> bool:
        if access_denied_page or local_403_heals >= 2:
            return True
        if search_failed_after_heal and consecutive_403 >= 3:
            return True
        return consecutive_403 >= 4
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

SGAI_PKG = Path(r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713")
if str(SGAI_PKG) not in sys.path:
    sys.path.insert(0, str(SGAI_PKG))

from portal_to_filename import parse_portal_html_row, parse_portal_title  # noqa: E402
from sgai_sentenze_cache import SentenzeCache  # noqa: E402

BASE_URL = "https://bancadatigiurisprudenza.giustiziatributaria.gov.it/ricerca"
SITE_ORIGIN = "https://bancadatigiurisprudenza.giustiziatributaria.gov.it"
# Portale 2026: nella tabella c'e' "Visualizza provvedimento", il PDF si scarica dalla pagina dettaglio
VISUALIZZA_SELECTOR = 'a[title^="Visualizza provvedimento"]'
DETTAGLIO_SCARICA_BTN = 'button[title="Scarica il pdf del provvedimento"]'
DOWNLOAD_LINK_SELECTOR = VISUALIZZA_SELECTOR
DOWNLOAD_LINK_FALLBACK = VISUALIZZA_SELECTOR
DEFAULT_CACHE_DIR = SGAI_PKG / "mia_cache"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "downloads_mef"
LOG_PATH = Path(__file__).resolve().parent / "log_download_mef.txt"
SESSION_LOG = Path(__file__).resolve().parent / "processed_mef_downloads.json"
CHECKPOINT_PATH = Path(__file__).resolve().parent / "mef_download_checkpoint.json"
LOCK_PATH = Path(__file__).resolve().parent / "mef_download.lock"
PAGE_LOG_PATH = Path(__file__).resolve().parent / "mef_pagine_log.csv"
# Coordinamento multi-worker: un solo search/submit alla volta.
# Cooldown dopo 403: solo gli ALTRI worker aspettano (breve); lo stesso PID non si auto-blocca.
SEARCH_SLOT_PATH = Path(__file__).resolve().parent / "mef_search_global.lock"
AKAMAI_COOLDOWN_PATH = Path(__file__).resolve().parent / "mef_akamai_cooldown.json"
SEARCH_PACE_SEC = 2.0  # pausa minima dopo search/paginazione
AKAMAI_COOLDOWN_SEC = 20  # altri worker: backoff breve dopo 403
SEARCH_SLOT_STALE_SEC = 60  # lock ricerca abbandonato dopo 60s
MAX_AUTO_PAGE_WALK = 25  # mai 100+ click '>' (dopo: navigazione manuale)
# Rewrite pageNumber solo per gap grandi: su gap=1 provoca 403 inutili (basta '>').
DIRECT_REWRITE_MIN_GAP = 15
# Anti-Akamai: senza pausa le pagine skip-only (gia_locali=10) sparano '>' e prendono 403.
PAGE_DELAY_SEC = 12.0  # attesa base (min) prima di ogni click '>'
PAGE_DELAY_SKIP_EXTRA_SEC = 4.0  # extra se la pagina non ha scaricato nulla
# Range variabile intorno a PAGE_DELAY; skip-only un po' più lento, senza allungare minuti.
PAGE_DELAY_JITTER_MIN = 0.90
PAGE_DELAY_JITTER_MAX = 1.35
PAGE_DELAY_SKIP_JITTER_MIN = 1.05
PAGE_DELAY_SKIP_JITTER_MAX = 1.55
_skip_page_streak = 0  # pagine consecutive solo-skip (per pause "respiro")
PAGINATION_403_RETRIES = 3
PAGINATION_403_BASE_WAIT = 45  # secondi * tentativo dopo 403 su '>'
# True = niente mutex/cooldown condiviso (un solo worker su questo PC). Default False.
SOLO_MODE = False
# Auto-heal: se si impianta (403 persistenti), prova da solo cooldown/UA/profilo/VPN.
AUTO_HEAL = True
HEALER: "AutoHealer | None" = None
_RUN_LOCK_PATH = LOCK_PATH
_RUN_CHECKPOINT_PATH = CHECKPOINT_PATH
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parent / ".edge_profile_mef"
CDP_PORT_DEFAULT = 9222
EDGE_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
]
# Browser Chromium reale via CDP: "edge" | "opera"
BROWSER_ENGINE = "edge"
_last_403_log_ts = 0.0
_last_cooldown_signal_ts = 0.0

# Anti-Akamai "guida collega": ritmi lenti + proxy + sessioni brevi
EDGE_PROXY = ""  # es. http://user:pass@host:port oppure http://host:port
DOWNLOAD_DELAY_MIN = 14.0  # secondi tra un PDF e il successivo (variabile)
DOWNLOAD_DELAY_MAX = 32.0
SESSION_ROTATE_EVERY = 25  # dopo N download nuovi → rotazione identità (auto-heal)
GLOBAL_BLOCK_COOLDOWN_SEC = 900  # 15 min dopo blocchi duri (403 a raffica)
_session_dl_count = 0
_consecutive_403 = 0
# Dopo un 403: alza temporaneamente le pause tra pagine (si resetta su download OK)
_soft_mode_until = 0.0
VPN_SERVICE = None  # impostato in main() da --vpn / MEF_VPN

# User-Agent realistici (rotazione in auto-heal)
USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
    ),
]
# Cooldown auto-heal (secondi): fisso ~2 minuti (+ piccolo jitter in AutoHealer)
# Cooldown progressivo AUTO-HEAL (secondi) — SOLO se l'IP e' cambiato (VPN/proxy ok).
# Senza cambio IP: fail-fast dopo i retry VPN (niente giornate di wait a vuoto).
HEAL_COOLDOWNS_SEC = (600, 900, 1200, 1800, 2700)
# Pausa tra un retry VPN e l'altro se l'IP non e' cambiato
HEAL_NO_IP_WAIT_SEC = (25, 55)  # range random
MAX_HEAL_WITHOUT_IP_CHANGE = 1  # dopo 1 ciclo fallito senza nuovo IP → esci
# Quante volte ritentare rotate Proton finche' l'IP pubblico non cambia
VPN_ROTATE_MAX_ATTEMPTS = 5
LISTONE_CSV = SGAI_PKG / "dati" / "listone_sentenze.csv"
CACHE_NOMI_BASE_DIR = SGAI_PKG / "dati"

TRIMESTRI = [
    ("01-01", "03-31"),
    ("04-01", "06-30"),
    ("07-01", "09-30"),
    ("10-01", "12-31"),
]

SEMESTRI = [
    ("01-01", "06-30"),  # S1 gen-giu
    ("07-01", "12-31"),  # S2 lug-dic
]

MATERIE = {
    "D040": "Accertamento imposte",
    "E020": "Accise armonizzate - Alcole",
    "E010": "Accise armonizzate - Prodotti energetici ed elettricità",
    "E030": "Accise non armonizzate",
    "D010": "Agevolazioni",
    "B050": "Bollo",
    "H010": "Catasto",
    "B060": "Concessioni governative",
    "D070": "Condono",
    "D060": "Contenzioso",
    "C050": "Cosap",
    "G010": "Demanio",
    "B140": "Diritti e tributi indiretti vari",
    "F010": "Dogane",
    "C080": "Iciap",
    "A030": "Ilor",
    "B130": "Imposta erariale di trascrizione",
    "B070": "Imposta sulle assicurazioni",
    "C020": "Imu ex Ici",
    "B090": "Intrattenimenti",
    "C070": "Invim",
    "B030": "Ipotecarie e catastali",
    "C010": "Irap",
    "A020": "Ires (ex Irpeg)",
    "A010": "Irpef",
    "B010": "Iva",
    "C030": "Pubblicità e pubbliche affissioni",
    "H030": "Pubblicità immobiliare",
    "B110": "Radiodiffusioni",
    "D080": "Rapporti con l'AF",
    "B020": "Registro",
    "D030": "Rimborsi",
    "D020": "Riscossione",
    "H020": "Servizi estimativi (OMI)",
    "B040": "Successioni e donazioni",
    "C040": "Tarsu",
    "B080": "Tassa sui contratti di borsa",
    "B100": "Tasse automobilistiche",
    "C060": "Tosap",
    "C090": "Tributi locali vari",
    "D050": "Violazioni e sanzioni",
}

MATERIA_KEYS = list(MATERIE.keys())

TRIMESTRE_DATE_MAP = {
    ("01-01", "03-31"): 0,
    ("04-01", "06-30"): 1,
    ("07-01", "09-30"): 2,
    ("10-01", "12-31"): 3,
}


def configure_run_paths(trimestre: int = 0, semestre: int = 0, worker: str = "") -> None:
    """Lock/checkpoint separati per trimestre/semestre (+ worker a/b se paralleli)."""
    global _RUN_LOCK_PATH, _RUN_CHECKPOINT_PATH
    base = Path(__file__).resolve().parent
    w = (worker or "").strip().lower()
    if semestre in (1, 2) and w in ("a", "b"):
        _RUN_LOCK_PATH = base / f"mef_download_s{semestre}{w}.lock"
        _RUN_CHECKPOINT_PATH = base / f"mef_download_checkpoint_s{semestre}{w}.json"
    elif semestre in (1, 2):
        _RUN_LOCK_PATH = base / f"mef_download_s{semestre}.lock"
        _RUN_CHECKPOINT_PATH = base / f"mef_download_checkpoint_s{semestre}.json"
    elif trimestre in (1, 2, 3, 4):
        _RUN_LOCK_PATH = base / f"mef_download_q{trimestre}.lock"
        _RUN_CHECKPOINT_PATH = base / f"mef_download_checkpoint_q{trimestre}.json"
    else:
        _RUN_LOCK_PATH = LOCK_PATH
        _RUN_CHECKPOINT_PATH = CHECKPOINT_PATH


def materie_for_args(args) -> List[str]:
    """Lista materie da elaborare; con --worker a/b spezza le 41 a meta' (no overlap)."""
    if args.materia:
        return [args.materia]
    keys = list(MATERIA_KEYS)
    w = (getattr(args, "worker", "") or "").strip().lower()
    if w not in ("a", "b"):
        return keys
    mid = (len(keys) + 1) // 2  # 21 + 20
    if w == "a":
        return keys[:mid]
    return keys[mid:]


def materia_start_in_slice(materie: List[str], global_idx: int) -> int:
    """Converte materia_idx globale (MATERIA_KEYS) in offset nella slice del worker."""
    if not materie:
        return 0
    if 0 <= global_idx < len(MATERIA_KEYS):
        cod = MATERIA_KEYS[global_idx]
        if cod in materie:
            return materie.index(cod)
    for i, cod in enumerate(materie):
        try:
            if MATERIA_KEYS.index(cod) >= global_idx:
                return i
        except ValueError:
            continue
    return len(materie)


def periodi_for_args(args) -> Tuple[List[Tuple[str, str]], str]:
    """(lista (da,a), etichetta log) — trimestri o semestri."""
    if args.semestre:
        return SEMESTRI, "semestri"
    return TRIMESTRI, "trimestri"


class BrowserClosedError(RuntimeError):
    """Edge/CDP chiuso o disconnesso durante l'automazione."""


class SessionRotateNeeded(RuntimeError):
    """Sessione breve scaduta: cambia profilo/UA/proxy via AUTO-HEAL."""


class IpBurnedStop(RuntimeError):
    """IP bruciato e nessun cambio IP disponibile: stop subito (niente cooldown eterni)."""


_LOG_CTX: Dict[str, Any] = {
    "worker": "",
    "anno": "",
    "periodo": "",
    "materia": "",
    "pagina": 0,
}


def worker_tag(args) -> str:
    w = (getattr(args, "worker", "") or "").strip().upper()
    if getattr(args, "semestre", 0) == 1:
        return f"S1{w}" if w in ("A", "B") else "S1"
    if getattr(args, "semestre", 0) == 2:
        return f"S2{w}" if w in ("A", "B") else "S2"
    if getattr(args, "trimestre", 0):
        return f"Q{args.trimestre}"
    return "RUN"


def periodo_short(da: str, a: str) -> str:
    if da == "01-01" and a == "06-30":
        return "S1 gen-giu"
    if da == "07-01" and a == "12-31":
        return "S2 lug-dic"
    for i, (d0, a0) in enumerate(TRIMESTRI, start=1):
        if da == d0 and a == a0:
            return f"Q{i}"
    return f"{da}..{a}"


def periodo_label(da: str, a: str) -> str:
    if da == "01-01" and a == "06-30":
        return "SEMESTRE 1 gen-giu"
    if da == "07-01" and a == "12-31":
        return "SEMESTRE 2 lug-dic"
    for i, (d0, a0) in enumerate(TRIMESTRI, start=1):
        if da == d0 and a == a0:
            return f"TRIMESTRE {i}"
    return f"periodo {da}-{a}"


def set_log_ctx(**kwargs: Any) -> None:
    for key, value in kwargs.items():
        if value is not None:
            _LOG_CTX[key] = value


def log_prefix() -> str:
    parts: List[str] = []
    if _LOG_CTX.get("worker"):
        parts.append(str(_LOG_CTX["worker"]))
    if _LOG_CTX.get("anno"):
        parts.append(str(_LOG_CTX["anno"]))
    if _LOG_CTX.get("periodo"):
        parts.append(str(_LOG_CTX["periodo"]))
    if _LOG_CTX.get("materia"):
        parts.append(str(_LOG_CTX["materia"]))
    pag = _LOG_CTX.get("pagina")
    if pag:
        parts.append(f"p.{pag}")
    return "|".join(parts)


def log_msg(message: str, *, level: str = "", skip_ctx: bool = False) -> None:
    tag = "" if skip_ctx else log_prefix()
    lvl = f"{level} " if level else ""
    if tag:
        body = f"[{tag}] {lvl}{message}"
    else:
        body = f"{lvl}{message}" if lvl else message
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {body}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_banner(title: str) -> None:
    log_msg("=" * 60, skip_ctx=True)
    log_msg(title, skip_ctx=True)
    log_msg("=" * 60, skip_ctx=True)


def log_run_header(args, *, start_year: int, end_year: int, output_dir: Path, resume: bool,
                   period_desc: str, n_materie: int, profile_dir: Path, cdp_port: int) -> None:
    log_banner("DOWNLOAD PORTALE MEF — naming SGAI")
    log_msg(f"Worker: {worker_tag(args)} | profilo: {profile_dir.name} | CDP :{cdp_port}", skip_ctx=True)
    log_msg(f"Anno/i: {start_year}..{end_year} | output: {output_dir.resolve()}", skip_ctx=True)
    log_msg(f"Resume: {resume} | checkpoint: {_RUN_CHECKPOINT_PATH.name}", skip_ctx=True)
    log_msg(f"Periodo: {period_desc} | materie: {n_materie}", skip_ctx=True)
    log_msg("-" * 60, skip_ctx=True)


def log_materia_start(materia_codice: str, materia_idx: int, n_materie: int) -> None:
    nome = MATERIE.get(materia_codice, materia_codice)
    set_log_ctx(materia=materia_codice, pagina=0)
    log_msg(f"--- MATERIA {materia_idx + 1}/{n_materie}: {materia_codice} ({nome}) ---")


def log_pagina_riepilogo(
    pagina: int,
    *,
    nuovi: int,
    skip_server: int,
    skip_local: int,
    skip_meta: int,
    falliti: int = 0,
    totale_portale: str | int | None = None,
) -> None:
    set_log_ctx(pagina=pagina)
    extra = f" | sul_portale~{totale_portale}" if totale_portale not in (None, "", "?") else ""
    fail_s = f" falliti={falliti}" if falliti else ""
    log_msg(
        f"PAGINA {pagina} -> nuovi={nuovi} gia_sgai={skip_server} "
        f"gia_locali={skip_local} meta={skip_meta}{fail_s}{extra}"
    )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_download_lock() -> None:
    lock_path = _RUN_LOCK_PATH
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text(encoding="ascii").strip())
        except ValueError:
            old_pid = 0
        if _pid_alive(old_pid):
            raise SystemExit(
                f"Download MEF gia in esecuzione (PID {old_pid}, lock {lock_path.name}). "
                "Chiudi l'altra istanza o usa --trimestre diverso / --profile-dir diverso."
            )
        lock_path.unlink(missing_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="ascii")


def release_download_lock() -> None:
    try:
        lock_path = _RUN_LOCK_PATH
        if lock_path.exists() and lock_path.read_text(encoding="ascii").strip() == str(os.getpid()):
            lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def wait_akamai_cooldown() -> None:
    """Backoff breve solo se un ALTRO worker ha appena preso 403. Stesso PID: nessun wait."""
    if SOLO_MODE:
        return
    if not AKAMAI_COOLDOWN_PATH.exists():
        return
    try:
        data = json.loads(AKAMAI_COOLDOWN_PATH.read_text(encoding="utf-8"))
        until = float(data.get("until", 0))
        owner_pid = int(data.get("pid", 0) or 0)
    except Exception:
        AKAMAI_COOLDOWN_PATH.unlink(missing_ok=True)
        return
    # Chi ha preso il 403 non si auto-blocca: altrimenti un solo worker perde minuti a vuoto.
    if owner_pid == os.getpid():
        return
    remain = until - time.time()
    if remain <= 0:
        AKAMAI_COOLDOWN_PATH.unlink(missing_ok=True)
        return
    # Cap: fino al cooldown globale (10 min) se un worker ha segnalato blocco duro.
    max_wait = float(GLOBAL_BLOCK_COOLDOWN_SEC)
    remain = min(remain, max_wait)
    log_msg(f"  Altro worker in 403: attendo {int(remain)}s (cooldown condiviso)...")
    while remain > 0:
        time.sleep(min(2.0, remain))
        remain = min(until - time.time(), max_wait)
        if remain <= 0:
            break
        try:
            data = json.loads(AKAMAI_COOLDOWN_PATH.read_text(encoding="utf-8"))
            until = float(data.get("until", 0))
            if int(data.get("pid", 0) or 0) == os.getpid():
                return
        except Exception:
            break
    AKAMAI_COOLDOWN_PATH.unlink(missing_ok=True)


def mark_akamai_cooldown(seconds: int | None = None) -> None:
    """Segnala 403. Dopo tanti 403 di fila → cooldown globale lungo (10 min default)."""
    global _last_cooldown_signal_ts, _last_403_log_ts, _consecutive_403
    now = time.time()
    _consecutive_403 += 1
    if seconds is None:
        if _consecutive_403 >= 3:
            seconds = GLOBAL_BLOCK_COOLDOWN_SEC
        else:
            seconds = AKAMAI_COOLDOWN_SEC
    if now - _last_cooldown_signal_ts < 15 and _consecutive_403 < 3:
        return
    _last_cooldown_signal_ts = now
    until = now + max(5, int(seconds))
    try:
        prev_until = 0.0
        if AKAMAI_COOLDOWN_PATH.exists():
            prev = json.loads(AKAMAI_COOLDOWN_PATH.read_text(encoding="utf-8"))
            prev_until = float(prev.get("until", 0))
        if until > prev_until:
            AKAMAI_COOLDOWN_PATH.write_text(
                json.dumps({"until": until, "pid": os.getpid(), "ts": now}),
                encoding="utf-8",
            )
            if now - _last_403_log_ts > 15:
                if seconds >= GLOBAL_BLOCK_COOLDOWN_SEC:
                    log_msg(
                        f"  403 a raffica ({_consecutive_403}) → cooldown GLOBALE "
                        f"{int(seconds)}s (tutti i worker)",
                        level="WARN",
                    )
                else:
                    log_msg("  403 su search/submit (segnalato)", level="WARN")
                _last_403_log_ts = now
    except OSError:
        pass


def clear_403_streak() -> None:
    global _consecutive_403
    _consecutive_403 = 0


def enter_soft_mode(minutes: float = 20.0) -> None:
    """Dopo 403 reale: ritmi un po' più lenti (non su rotazione sessione)."""
    global _soft_mode_until, PAGE_DELAY_SEC
    _soft_mode_until = max(_soft_mode_until, time.time() + minutes * 60.0)
    PAGE_DELAY_SEC = max(PAGE_DELAY_SEC, 16.0)
    log_msg(
        f"  Soft-mode attivo fino a +{minutes:.0f} min "
        f"(page-delay>={PAGE_DELAY_SEC:.0f}s, download più lenti)",
        level="WARN",
    )


def in_soft_mode() -> bool:
    return time.time() < _soft_mode_until


def sleep_bot(seconds: float, label: str = "pausa bot") -> None:
    """Sleep lungo con heartbeat ogni ~60s (così vedi che è vivo, non morto)."""
    seconds = max(1.0, float(seconds))
    end = time.time() + seconds
    log_msg(f"  {label}: attendo {seconds / 60.0:.1f} min ({int(seconds)}s)...", level="WARN")
    while True:
        left = end - time.time()
        if left <= 0:
            break
        chunk = min(60.0, left)
        time.sleep(chunk)
        left = end - time.time()
        if left > 5:
            log_msg(f"  {label}: ancora ~{left / 60.0:.1f} min...")


def pausa_tra_download() -> None:
    """Pausa variabile tra PDF (triangolare); in soft-mode più lunga."""
    dmin, dmax = DOWNLOAD_DELAY_MIN, DOWNLOAD_DELAY_MAX
    if in_soft_mode():
        dmin = max(dmin, 18.0)
        dmax = max(dmax, 32.0)
    if variable_download_delay is not None:
        delay = variable_download_delay(dmin, dmax)
    else:
        delay = random.uniform(dmin, dmax)
    # micro-jitter extra
    delay += random.uniform(0.0, 2.5)
    log_msg(f"  Pausa download variabile {delay:.1f}s (range {dmin:.0f}-{dmax:.0f})...")
    time.sleep(delay)


def _search_slot_owner() -> tuple[int, float] | None:
    if not SEARCH_SLOT_PATH.exists():
        return None
    try:
        lines = SEARCH_SLOT_PATH.read_text(encoding="ascii").strip().splitlines()
        pid = int(lines[0])
        ts = float(lines[1]) if len(lines) > 1 else 0.0
        return pid, ts
    except Exception:
        return None


def acquire_search_slot(timeout_sec: int = 90) -> None:
    """Un solo worker alla volta puo fare Ricerca / click paginazione (anti-Akamai)."""
    if SOLO_MODE:
        return
    deadline = time.time() + timeout_sec
    logged = False
    while time.time() < deadline:
        wait_akamai_cooldown()
        owner = _search_slot_owner()
        if owner is not None:
            pid, ts = owner
            stale = (time.time() - ts) > SEARCH_SLOT_STALE_SEC
            # Lock dello stesso PID = residuo non rilasciato: non auto-bloccarsi.
            if pid == os.getpid() or stale or not _pid_alive(pid):
                SEARCH_SLOT_PATH.unlink(missing_ok=True)
            else:
                if not logged:
                    log_msg(
                        f"  Attendo slot ricerca (altro MEF PID {pid}) — "
                        "se e' D040/S1 in parallelo, FERMALO: stesso IP = 403. "
                        "Oppure rilancia QUESTO con --solo",
                        level="WARN",
                    )
                    logged = True
                time.sleep(1.0)
                continue
        try:
            fd = os.open(str(SEARCH_SLOT_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}\n{time.time():.3f}\n".encode("ascii"))
            finally:
                os.close(fd)
            return
        except FileExistsError:
            time.sleep(0.3)
    SEARCH_SLOT_PATH.unlink(missing_ok=True)
    raise TimeoutError(
        f"Timeout {timeout_sec}s in attesa dello slot ricerca globale "
        f"({SEARCH_SLOT_PATH.name}). Lock rimosso: riprova."
    )


def release_search_slot() -> None:
    try:
        owner = _search_slot_owner()
        if owner and owner[0] == os.getpid():
            SEARCH_SLOT_PATH.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def search_slot(label: str = "search") -> Iterator[None]:
    """Mutex globale + cooldown + pausa tra un worker e l'altro."""
    if SOLO_MODE:
        yield
        time.sleep(min(SEARCH_PACE_SEC, 1.0))
        return
    acquire_search_slot()
    log_msg(f"  Slot ricerca OK ({label})")
    try:
        yield
    finally:
        release_search_slot()
        time.sleep(SEARCH_PACE_SEC)


def kill_listener_on_port(port: int) -> None:
    """Chiude il processo in ascolto sulla porta CDP (Windows)."""
    try:
        out = subprocess.check_output(
            ["cmd", "/c", f"netstat -ano | findstr :{port}"],
            text=True,
            errors="ignore",
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
    listening: list[int] = []
    other: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or f":{port}" not in parts[1]:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid <= 0:
            continue
        if "LISTENING" in line.upper():
            listening.append(pid)
        else:
            other.append(pid)
    for pid in dict.fromkeys(listening or other):
        log_msg(f"  AUTO-HEAL: taskkill PID {pid} (CDP :{port})")
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    time.sleep(2)


class AutoHealer:
    """Disimpianta worker: cooldown → kill Edge → VPN → nuovo profilo → nuovo UA → ri-ricerca."""

    def __init__(
        self,
        base_profile: Path,
        cdp_port: int,
        *,
        vpn_cmd: str = "",
        vpn_service: Any = None,
        alt_profiles: int = 3,
        warmup: int = 8,
    ) -> None:
        self.base_profile = base_profile.resolve()
        self.cdp_port = cdp_port
        self.vpn_cmd = (vpn_cmd or os.environ.get("MEF_VPN_ROTATE_CMD") or "").strip()
        self.vpn_service = vpn_service
        self.warmup = warmup
        self.profiles: List[Path] = [self.base_profile]
        for i in range(max(0, alt_profiles)):
            self.profiles.append(
                self.base_profile.parent / f"{self.base_profile.name}_heal{i}"
            )
        self.profile_idx = 0
        self.ua_idx = 0
        self.level = 0
        self.rounds = 0
        self.fails_without_ip_change = 0

    @property
    def current_profile(self) -> Path:
        return self.profiles[self.profile_idx % len(self.profiles)]

    def _can_rotate_ip(self) -> bool:
        return bool(self.vpn_service or self.vpn_cmd or VPN_SERVICE)

    def _cooldown(self, *, extra_factor: float = 1.0, short: bool = False) -> None:
        if short or not self._can_rotate_ip():
            wait = random.uniform(*HEAL_NO_IP_WAIT_SEC)
            sleep_bot(
                wait,
                f"AUTO-HEAL pausa BREVE (no cambio IP / fail-fast) "
                f"round {self.rounds}",
            )
            return
        base = HEAL_COOLDOWNS_SEC[min(self.level, len(HEAL_COOLDOWNS_SEC) - 1)]
        wait = float(base) * max(1.0, extra_factor) + random.uniform(30, 90)
        if _consecutive_403 >= 3:
            wait = max(wait, float(GLOBAL_BLOCK_COOLDOWN_SEC) * 1.2 + random.uniform(60, 180))
        sleep_bot(
            wait,
            f"AUTO-HEAL L{self.level} cooldown (round {self.rounds}, "
            f"403streak={_consecutive_403})",
        )

    def _close_browser(self, browser) -> None:
        try:
            if browser is not None and hasattr(browser, "close"):
                browser.close()
        except Exception:
            pass
        kill_listener_on_port(self.cdp_port)

    def _rotate_vpn_once(self, *, reason: str = "heal") -> bool:
        """Un singolo tentativo di rotazione. True solo se IP pubblico e' cambiato."""
        global VPN_SERVICE
        svc = self.vpn_service or VPN_SERVICE
        if svc is not None:
            log_msg(f"  AUTO-HEAL: rotazione VPN ({svc.name}) — {reason}", level="WARN")
            try:
                before = ""
                if get_public_ip is not None:
                    before = get_public_ip() or ""
                    log_msg(f"  VPN IP prima: {before or '?'}")
                result = svc.rotate(wait_ip_change=True, timeout=150.0)
                after = result.new_ip or (get_public_ip() if get_public_ip else "") or ""
                # Verifica SEMPRE sull'IP pubblico attuale (non solo sul flag dello script)
                if get_public_ip is not None:
                    live = get_public_ip() or after
                    if live:
                        after = live
                changed = bool(before and after and before != after)
                if not before and after:
                    changed = True  # non sapevamo il prima, ma abbiamo un IP
                log_msg(
                    f"  VPN IP dopo: {after or '?'} "
                    f"(cambiato={'SI' if changed else 'NO'})",
                    level="WARN",
                )
                if changed:
                    sleep_bot(random.uniform(45, 90), "settle post-cambio VPN")
                    clear_403_streak()
                    reset_akamai_flags()
                    return True
                return False
            except Exception as exc:
                log_msg(f"  AUTO-HEAL VPN service errore: {exc}", level="WARN")
                # fallback su vpn_cmd sotto

        if not self.vpn_cmd:
            if svc is None:
                log_msg(
                    "  AUTO-HEAL: VPN non configurata "
                    "(passa --vpn proton oppure --vpn-rotate-cmd)",
                    level="WARN",
                )
            return False

        before = (get_public_ip() if get_public_ip else "") or ""
        log_msg(f"  AUTO-HEAL: rotazione VPN cmd → {self.vpn_cmd}", level="WARN")
        try:
            subprocess.run(self.vpn_cmd, shell=True, timeout=180, check=False)
        except Exception as exc:
            log_msg(f"  AUTO-HEAL VPN errore: {exc}", level="WARN")
            return False
        time.sleep(12)
        after = (get_public_ip() if get_public_ip else "") or ""
        changed = bool(before and after and before != after)
        log_msg(
            f"  VPN cmd IP: {before or '?'} → {after or '?'} "
            f"(cambiato={'SI' if changed else 'NO'})",
            level="WARN",
        )
        if changed:
            sleep_bot(random.uniform(45, 90), "settle post-cambio VPN")
            clear_403_streak()
            reset_akamai_flags()
        return changed

    def _rotate_vpn(self, *, reason: str = "heal", max_attempts: int | None = None) -> bool:
        """
        Ruota VPN finche' l'IP pubblico non e' diverso (o esaurisce i tentativi).
        True solo se IP cambiato davvero.
        """
        if not self._can_rotate_ip():
            log_msg(
                "  AUTO-HEAL: VPN non configurata "
                "(passa --vpn proton oppure --vpn-rotate-cmd)",
                level="WARN",
            )
            return False

        attempts = max(1, int(max_attempts or VPN_ROTATE_MAX_ATTEMPTS))
        for i in range(1, attempts + 1):
            log_msg(
                f"  VPN: tentativo cambio IP {i}/{attempts} ({reason})",
                level="WARN",
            )
            if self._rotate_vpn_once(reason=f"{reason} [{i}/{attempts}]"):
                log_msg(
                    f"  VPN: OK IP diverso al tentativo {i}/{attempts}",
                    level="WARN",
                )
                return True
            if i < attempts:
                pause = random.uniform(*HEAL_NO_IP_WAIT_SEC)
                log_msg(
                    f"  VPN: IP ancora uguale → ritento tra {pause:.0f}s "
                    f"({i}/{attempts})...",
                    level="WARN",
                )
                time.sleep(pause)
        log_msg(
            f"  VPN: FALLITO — dopo {attempts} tentativi IP ancora uguale. "
            "Apri Proton (Auto-connect / altro paese) o cambia rete a mano.",
            level="WARN",
        )
        return False

    def _next_profile(self) -> Path:
        self.profile_idx = (self.profile_idx + 1) % len(self.profiles)
        prof = self.current_profile
        prof.mkdir(parents=True, exist_ok=True)
        log_msg(f"  AUTO-HEAL: profilo → {prof.name}", level="WARN")
        return prof

    def apply_user_agent(self, page) -> str:
        ua = USER_AGENTS[self.ua_idx % len(USER_AGENTS)]
        self.ua_idx += 1
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send(
                "Network.setUserAgentOverride",
                {"userAgent": ua, "acceptLanguage": "it-IT,it;q=0.9,en;q=0.8"},
            )
        except Exception:
            try:
                page.add_init_script(
                    f"Object.defineProperty(navigator,'userAgent',{{get:()=>'{ua}'}});"
                )
            except Exception:
                pass
        log_msg(f"  AUTO-HEAL: User-Agent → {ua[:72]}...")
        return ua

    def heal(
        self,
        playwright,
        browser,
        page,
        args,
        *,
        anno: int,
        da: str,
        a: str,
        materia_codice: str,
        target_page: int,
        enter_soft: bool = True,
    ) -> Tuple[Any, Any, Path, bool]:
        """Ritorna (browser, page, profile_dir, ok_ricerca)."""
        self.rounds += 1
        self.level = min(self.level + 1, len(HEAL_COOLDOWNS_SEC) - 1)
        log_msg(
            f"=== AUTO-HEAL inizio (L{self.level}, round {self.rounds}, "
            f"target pag.{target_page}, materia {materia_codice}) ===",
            level="WARN",
        )

        # Escalation AUTOMATICA (tu non fai nulla):
        #   A) 1–2 heal LOCALI (stesso IP) — un 403 isolato NON brucia l'IP
        #   B) cambio IP SOLO se Akamai classifica IP bruciato (vedi is_ip_burned_signal)
        self._close_browser(browser)
        browser = None
        if enter_soft:
            enter_soft_mode(15.0)

        search_was_403 = bool(_akamai_403_seen or _search_submit_status == 403)
        streak_on_entry = int(_consecutive_403)
        ip_changed = False
        last_fail = "403" if search_was_403 else "other"
        # Conta solo i fallimenti 403 DEI heal locali (non il 403 che ha aperto l'heal)
        local_403_heals = 0
        access_denied = False

        profile = self.current_profile
        profile.mkdir(parents=True, exist_ok=True)
        try:
            args.profile_dir = str(profile)
        except Exception:
            pass

        def _page_access_denied() -> bool:
            try:
                return errore_ricerca(page) == "access_denied" or (
                    "access denied" in (page.inner_text("body") or "").lower()
                )
            except Exception:
                return False

        def _open_and_search(*, label: str) -> bool:
            nonlocal browser, page, last_fail, local_403_heals, access_denied
            try:
                browser, _ctx, page = open_edge_session(
                    playwright, profile, self.cdp_port, max(self.warmup, 12)
                )
            except Exception as exc:
                log_msg(f"  AUTO-HEAL: riapertura browser fallita ({label}): {exc}", level="WARN")
                last_fail = "browser"
                return False
            attach_akamai_watch(page)
            attach_search_status_watch(page)
            self.apply_user_agent(page)
            sleep_bot(random.uniform(15, 28), f"warmup {label}")
            reset_akamai_flags()
            try:
                ok_local = esegui_ricerca(
                    page, anno, da, a, materia_codice, browser=browser
                )
            except BrowserClosedError:
                log_msg(f"  AUTO-HEAL: browser chiuso in ricerca ({label})", level="WARN")
                last_fail = "browser"
                return False
            if ok_local:
                clear_403_streak()
                return True
            if _page_access_denied():
                access_denied = True
                last_fail = "403"
                local_403_heals += 1
            elif _akamai_403_seen or _search_submit_status == 403:
                last_fail = "403"
                local_403_heals += 1
            elif last_fail != "browser":
                last_fail = "other"
            log_msg(
                f"  AUTO-HEAL: ricerca KO ({label}, motivo={last_fail}, "
                f"local_403={local_403_heals}, streak={_consecutive_403}, "
                f"access_denied={access_denied})",
                level="WARN",
            )
            return False

        # --- A) HEAL LOCALI (minimo 2 tentativi su 403 prima di parlare di burn) ---
        log_msg(
            "  AUTO-HEAL fase A: heal LOCALE #1 (stesso IP — pausa + browser + UA)",
            level="WARN",
        )
        # NON azzerare lo streak qui: serve a classificare IP bruciato
        reset_akamai_flags()
        if enter_soft or search_was_403:
            sleep_bot(random.uniform(45, 90), "pausa locale anti-403 (no VPN)")
        else:
            time.sleep(random.uniform(5, 12))
        ok = _open_and_search(label="fase-A1-locale")

        if not ok and last_fail == "browser":
            log_msg("  AUTO-HEAL: ritento locale dopo browser chiuso (no VPN)", level="WARN")
            self._close_browser(browser)
            browser = None
            time.sleep(random.uniform(8, 14))
            ok = _open_and_search(label="fase-A-retry-browser")

        # Secondo heal locale se ancora 403 (un solo 403 post-A1 NON basta per VPN).
        # Access Denied esplicito = gia' burn forte → salta A2 e vai a classificazione.
        if not ok and last_fail == "403" and not access_denied:
            log_msg(
                "  AUTO-HEAL fase A: heal LOCALE #2 (ancora stesso IP — "
                "403 isolato ≠ IP bruciato)",
                level="WARN",
            )
            self._close_browser(browser)
            browser = None
            if self.level >= 2:
                profile = self._next_profile()
                try:
                    args.profile_dir = str(profile)
                except Exception:
                    pass
            sleep_bot(random.uniform(60, 120), "pausa locale #2 anti-403 (no VPN)")
            reset_akamai_flags()
            ok = _open_and_search(label="fase-A2-locale")

        burned = is_ip_burned_signal(
            consecutive_403=max(streak_on_entry, _consecutive_403),
            search_failed_after_heal=(not ok and last_fail == "403"),
            local_403_heals=local_403_heals,
            access_denied_page=access_denied,
        )
        log_msg(
            f"  AUTO-HEAL classificazione IP: burned={burned} "
            f"(local_403={local_403_heals}, streak={max(streak_on_entry, _consecutive_403)}, "
            f"access_denied={access_denied})",
            level="WARN",
        )

        # --- B) Cambio IP SOLO se IP davvero bruciato ---
        if not ok and burned and last_fail == "403" and self._can_rotate_ip():
            log_msg(
                "  AUTO-HEAL fase B: IP BRUCIATO (Akamai persistente) → CAMBIO IP Proton",
                level="WARN",
            )
            self._close_browser(browser)
            browser = None
            if self.level >= 2:
                profile = self._next_profile()
                try:
                    args.profile_dir = str(profile)
                except Exception:
                    pass
            ip_changed = self._rotate_vpn(
                reason="fase B: IP bruciato da Akamai — serve IP nuovo",
                max_attempts=VPN_ROTATE_MAX_ATTEMPTS,
            )
            if ip_changed:
                self.fails_without_ip_change = 0
                sleep_bot(random.uniform(50, 90), "settle post-nuovo-IP")
                clear_403_streak()
                reset_akamai_flags()
                local_403_heals = 0
                access_denied = False
                ok = _open_and_search(label="fase-B-post-VPN")
                # Nuovo IP subito bruciato (Access Denied / 403) → secondo rotate
                burned2 = is_ip_burned_signal(
                    consecutive_403=_consecutive_403,
                    search_failed_after_heal=(not ok and last_fail == "403"),
                    local_403_heals=local_403_heals,
                    access_denied_page=access_denied,
                )
                if not ok and burned2 and last_fail == "403":
                    log_msg(
                        "  AUTO-HEAL: anche il nuovo IP risulta bruciato → secondo cambio IP",
                        level="WARN",
                    )
                    self._close_browser(browser)
                    browser = None
                    if self._rotate_vpn(
                        reason="fase B2: nuovo IP gia' bruciato",
                        max_attempts=VPN_ROTATE_MAX_ATTEMPTS,
                    ):
                        ip_changed = True
                        sleep_bot(random.uniform(50, 90), "settle post-2° IP")
                        clear_403_streak()
                        reset_akamai_flags()
                        ok = _open_and_search(label="fase-B2-post-VPN")
            else:
                log_msg(
                    "  AUTO-HEAL: Proton non ha dato IP diverso — "
                    "ritento ciclo dall'esterno (automatico)",
                    level="WARN",
                )
                self.fails_without_ip_change += 1

        elif not ok and last_fail == "403" and not burned:
            log_msg(
                "  AUTO-HEAL: 403 ma IP NON classificato bruciato → "
                "Niente VPN, ritento automatico dall'esterno (stesso IP)",
                level="WARN",
            )

        elif not ok and burned and last_fail == "403" and not self._can_rotate_ip():
            log_msg(
                "  AUTO-HEAL: IP bruciato ma VPN off — passa --vpn proton "
                "per il cambio automatico",
                level="WARN",
            )
            self.fails_without_ip_change += 1

        if not ok:
            self._close_browser(browser)
            ck_pg = target_page
            # Con VPN (o 403 non-burned): ritenta dall'esterno, niente panico manuale
            if (self._can_rotate_ip() or not burned) and self.fails_without_ip_change < 4:
                log_msg(
                    f"  AUTO-HEAL ciclo KO (motivo={last_fail}, burned={burned}) — "
                    f"ritento automatico (pag~{ck_pg})",
                    level="WARN",
                )
                return None, None, profile, False
            log_msg("=" * 60, level="WARN")
            log_msg(
                "STOP automatico: troppi fallimenti. "
                f"Checkpoint a pag.{ck_pg} — rilancia con --resume.",
                level="WARN",
            )
            log_msg("=" * 60, level="WARN")
            raise IpBurnedStop(
                f"AUTO-HEAL esaurito: riparti con --resume --start-pagina {ck_pg}"
            )

        page = pick_portal_page(browser, page)
        attach_akamai_watch(page)
        attach_search_status_watch(page)

        # Dopo RICERCA sei SEMPRE a pag.1. L'UI/localStorage può mentire (active=344
        # con dati di pag.1) → ripristino OBBLIGATORIO, mai fidarsi di "già lì".
        if target_page > 1:
            log_msg(
                f"  AUTO-HEAL: ripristino OBBLIGATORIO pagina {target_page} "
                f"(post-ricerca = pag.1, UI attuale={pagina_corrente(page)})...",
                level="WARN",
            )
            time.sleep(random.uniform(4, 9))
            jumped = prova_salto_local_storage(page, target_page)
            landed = pagina_corrente(page)
            if landed != target_page and target_page - max(landed, 1) <= MAX_AUTO_PAGE_WALK:
                salta_a_pagina(page, target_page)
                landed = pagina_corrente(page)
            if landed < target_page:
                log_msg(
                    f"  AUTO-HEAL KO: UI a pag.{landed} (target {target_page}) — "
                    f"NON elaboro da pag.1 (jump={jumped})",
                    level="WARN",
                )
                return browser, page, profile, False
            if landed != target_page:
                log_msg(
                    f"  AUTO-HEAL: UI a pag.{landed} (atteso {target_page}) — "
                    f"uso {landed} come ripresa",
                    level="WARN",
                )

        self.level = max(0, self.level - 1)
        global _session_dl_count
        _session_dl_count = 0
        clear_403_streak()
        log_msg(
            f"=== AUTO-HEAL OK — profilo {profile.name}, UI pag "
            f"{pagina_corrente(page)} ==="
        )
        return browser, page, profile, True


def raise_if_browser_closed(exc: BaseException) -> None:
    msg = str(exc).lower()
    if isinstance(exc, PlaywrightError) and any(k in msg for k in ("closed", "crashed", "target")):
        raise BrowserClosedError(
            "Edge chiuso, crashato o disconnesso. Riprendi con --resume sulla stessa materia/semestre."
        ) from exc


def load_session() -> set[str]:
    if not SESSION_LOG.exists():
        return set()
    try:
        data = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
        return set(data.get("processed", []))
    except Exception:
        return set()


def save_session(processed: set[str]) -> None:
    SESSION_LOG.write_text(
        json.dumps(
            {"processed": sorted(processed), "last_update": datetime.now().isoformat()},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_checkpoint() -> Dict[str, Any]:
    """Carica checkpoint; se manca in SGAI/, prova cartella padre (legacy)."""
    candidates = [_RUN_CHECKPOINT_PATH]
    parent_same = Path(__file__).resolve().parent.parent / _RUN_CHECKPOINT_PATH.name
    if parent_same not in candidates:
        candidates.append(parent_same)
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if path != _RUN_CHECKPOINT_PATH:
                log_msg(
                    f"Checkpoint trovato in {path} — lo uso "
                    f"(pag.~{data.get('start_pagina', '?')})",
                    skip_ctx=True,
                )
                # Copia in path ufficiale cosi' i prossimi save restano in SGAI/
                try:
                    _RUN_CHECKPOINT_PATH.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            return data
        except Exception:
            continue
    return {}


def save_checkpoint(
    *,
    year: int,
    trimestre_idx: int,
    materia_idx: int,
    stats: Dict[str, int],
    start_pagina: int = 1,
    semestre: int = 0,
) -> None:
    _RUN_CHECKPOINT_PATH.write_text(
        json.dumps(
            {
                "year": year,
                "semestre": semestre,
                "trimestre_idx": trimestre_idx,
                "period_idx": trimestre_idx,
                "materia_idx": materia_idx,
                "start_pagina": start_pagina,
                "stats": stats,
                "updated": datetime.now().isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def log_pagina_csv(
    *,
    year: int,
    trimestre_idx: int,
    materia: str,
    pagina: int,
    scaricati: int,
    skip_server: int,
    skip_local: int,
    skip_meta: int,
    semestre: int = 0,
) -> None:
    """Registra ogni pagina elaborata in mef_pagine_log.csv (condiviso tra worker)."""
    new_file = not PAGE_LOG_PATH.exists()
    headers = [
        "timestamp",
        "worker",
        "semestre",
        "periodo",
        "anno",
        "trimestre",
        "materia",
        "pagina",
        "scaricati",
        "skip_server",
        "skip_local",
        "skip_meta",
    ]
    with PAGE_LOG_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(headers)
        writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                _LOG_CTX.get("worker", ""),
                semestre or "",
                _LOG_CTX.get("periodo", ""),
                year,
                trimestre_idx + 1,
                materia,
                pagina,
                scaricati,
                skip_server,
                skip_local,
                skip_meta,
            ]
        )


def load_server_keys(year: int, cache_dir: Path) -> Tuple[set[str], str]:
    """
    Nomi base gia sul server SGAI (da cache_nomi_base[_ANNO].txt o mia_cache/nomi_base.txt).
    Se il nome e' in questo set, NON scaricare.
    """
    if year:
        year_file = CACHE_NOMI_BASE_DIR / f"cache_nomi_base_{year}.txt"
        if year_file.exists():
            keys = {
                line.strip().lower()
                for line in year_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            return keys, str(year_file)

    keys_path = Path(cache_dir) / "nomi_base.txt"
    if keys_path.exists():
        keys = {
            line.strip().lower()
            for line in keys_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        return keys, str(keys_path)

    full_list = CACHE_NOMI_BASE_DIR / "cache_nomi_base.txt"
    if full_list.exists():
        keys = {
            line.strip().lower()
            for line in full_list.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        return keys, str(full_list)

    return set(), "nessuna cache"


def gia_sul_server(nome_base: str, server_keys: set[str]) -> bool:
    return nome_base.strip().lower() in server_keys


def normalizza_corte_portale(corte: str) -> str:
    text = (corte or "").strip().replace("Â°", "°")
    text = text.replace("CGT_1_", "CGT 1° ").replace("CGT_2_", "CGT 2° ")
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text)


def is_ricerca_form_url(url: str) -> bool:
    """True solo sulla pagina form ricerca, non su /ricerca/dettaglio/..."""
    u = (url or "").lower()
    return "giustiziatributaria.gov.it/ricerca" in u and "/dettaglio" not in u


def navigate_to_ricerca_form(page, *, timeout_ms: int = 60000, retries: int = 4) -> None:
    """Porta il tab sulla pagina form e attende il selettore anno."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            if not is_ricerca_form_url(page.url or ""):
                page.goto(BASE_URL, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_selector("#Form\\.ControlInput2", timeout=min(45000, timeout_ms))
            attach_akamai_watch(page)
            attach_search_status_watch(page)
            return
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)
            last_exc = exc
            msg = str(exc).split("\n", 1)[0]
            log_msg(f"  Form ricerca non pronto ({attempt + 1}/{retries}): {msg}")
            time.sleep(2 + attempt)
    raise BrowserClosedError(f"Impossibile caricare form ricerca: {last_exc}")


def prep_form_ricerca(page, anno: int, da: str, a: str, materia_codice: str) -> None:
    """Come log_ricerche.py."""
    mass = page.get_by_role("checkbox", name="Ricerca provvedimenti")
    if mass.count() and mass.is_checked():
        mass.uncheck()

    page.select_option("#Form\\.ControlInput2", str(anno))
    page.fill("#Form\\.ControlInput3", f"{anno}-{da}")
    page.fill("#Form\\.ControlInput4", f"{anno}-{a}")
    page.select_option("#Form\\.ControlInput10", materia_codice)


def count_download_links(page) -> int:
    try:
        return page.locator(VISUALIZZA_SELECTOR).count()
    except PlaywrightError as exc:
        raise_if_browser_closed(exc)
        raise


def count_data_rows(page) -> int:
    try:
        return int(
            page.evaluate(
                """
                () => [...document.querySelectorAll('table tr')].filter(tr => {
                    const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                    return cells.length >= 5 && cells[3]?.includes('CGT');
                }).length
                """
            )
            or 0
        )
    except PlaywrightError as exc:
        raise_if_browser_closed(exc)
        raise


def attendi_ricerca_manuale(
    page, timeout_sec: int = 300, browser=None, *, target_page: int | None = None
) -> bool:
    """Dopo 403: in modalità bot (AUTO_HEAL) NON chiede INVIO — ritorna False per heal."""
    if AUTO_HEAL:
        log_msg(
            "  403 — modalità bot: nessun prompt INVIO, passo ad AUTO-HEAL",
            level="WARN",
        )
        return False
    log_msg(
        "  Akamai 403 — in Edge: anno 2025, date semestre, materia, clic Ricerca.",
        level="WARN",
    )
    if target_page and target_page > 1:
        log_msg(
            f"  Poi in Edge vai alla pagina {target_page} dei risultati "
            "(numeri paginazione / '>') PRIMA di premere INVIO.",
            level="WARN",
        )
    log_msg(
        "  >>> Premi INVIO quando la tabella (e la pagina giusta) e' visibile <<<",
        skip_ctx=True,
    )
    try:
        input()
    except KeyboardInterrupt:
        log_msg("Interrotto (Ctrl+C) — riprendi con --resume.")
        return False

    if browser is not None:
        page = pick_portal_page(browser, page)
        attach_akamai_watch(page)
        attach_search_status_watch(page)

    for tick in range(max(5, timeout_sec // 6)):
        try:
            links = count_download_links(page)
            rows = count_data_rows(page)
            if links > 0 or rows >= 3:
                log_msg(f"  Ricerca manuale OK: {links} link, {rows} righe")
                return True
            body = page.inner_text("body")
            if "Nessun risultato" in body:
                return False
        except BrowserClosedError:
            raise
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)
        if tick and tick % 5 == 0:
            log_msg(f"  Verifico tabella ({tick * 2}s)...")
        time.sleep(2)
    log_msg("  Tabella non rilevata dopo INVIO — rifai la ricerca in Edge.")
    return False


def attendi_risultati_caricati(page, timeout_sec: int = 120) -> bool:
    """Attende link PDF reali — il solo testo 'Risultati di ricerca' non basta."""
    for tick in range(timeout_sec // 2):
        links = count_download_links(page)
        rows = count_data_rows(page)
        if links > 0 or rows >= 3:
            log_msg(f"  Tabella pronta: {links} link, {rows} righe dati")
            return True
        if _akamai_403_seen or _search_submit_status == 403:
            return False
        body = page.inner_text("body")
        if "Nessun risultato" in body:
            return False
        if tick and tick % 5 == 0:
            log_msg(f"  Attendo link PDF ({tick * 2}s, righe={rows}, submit={_search_submit_status})...")
        time.sleep(2)
    return False


def portal_pages(browser_or_context) -> list:
    if hasattr(browser_or_context, "contexts"):
        if browser_or_context.contexts:
            return list(browser_or_context.contexts[0].pages)
        return []
    if hasattr(browser_or_context, "pages"):
        return list(browser_or_context.pages)
    return []


def pick_portal_page(browser_or_context, page):
    """Con CDP, dopo la ricerca la tab attiva puo non essere quella collegata."""
    for candidate in reversed(portal_pages(browser_or_context)):
        url = candidate.url or ""
        if "giustiziatributaria.gov.it" not in url:
            continue
        try:
            candidate.title()
            if count_download_links(candidate) > 0 or "Risultati di ricerca" in candidate.inner_text("body"):
                return candidate
        except PlaywrightError:
            continue
    return page


def download_link_locator(page, index: int):
    return page.locator(VISUALIZZA_SELECTOR).nth(index)


def ricerca_ha_risultati(page) -> bool:
    return count_download_links(page) > 0 or count_data_rows(page) >= 3


def errore_ricerca(page) -> str:
    body = page.inner_text("body").lower()
    if "si è verificato un errore" in body or "si  verificato un errore" in body:
        return "errore_portale"
    if ricerca_ha_risultati(page):
        return ""
    if "indicare almeno una parola" in body:
        return "anno_non_accettato"
    if "access denied" in body:
        return "access_denied"
    return ""


def _salva_debug_ricerca(page, anno: int, materia: str, attempt: int) -> None:
    """Screenshot quando la ricerca fallisce (per capire cosa mostra il portale)."""
    try:
        shot = Path(__file__).resolve().parent / f"mef_errore_{anno}_{materia}_{attempt}.png"
        page.screenshot(path=str(shot), full_page=True)
        log_msg(f"  Screenshot errore: {shot.name}")
    except Exception:
        pass


_search_submit_status: int | None = None
_akamai_403_seen = False
_akamai_watch_active = False
_last_search_body: dict | None = None


def reset_search_status() -> None:
    global _search_submit_status
    _search_submit_status = None


def reset_akamai_flags() -> None:
    """Azzera 403 residui dopo una ricerca riuscita (prima di paginare)."""
    global _akamai_403_seen
    _akamai_403_seen = False
    reset_search_status()


def _store_search_body_from_post(raw: str | None) -> None:
    global _last_search_body
    if not raw:
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    if isinstance(data, dict):
        _last_search_body = data


def attach_search_status_watch(page) -> None:
    def _on_request(req) -> None:
        if "search/submit" in req.url and req.method.upper() == "POST":
            _store_search_body_from_post(req.post_data)

    def _on_response(resp) -> None:
        global _search_submit_status, _last_403_log_ts
        if "search/submit" in resp.url:
            _search_submit_status = resp.status
            if resp.status == 403:
                mark_akamai_cooldown()
            if resp.status != 200:
                now = time.time()
                if now - _last_403_log_ts > 15:
                    log_msg(f"search/submit HTTP {resp.status}", level="WARN")
                    _last_403_log_ts = now

    page.on("request", _on_request)
    page.on("response", _on_response)


def find_edge_executable() -> Path:
    for candidate in (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("msedge.exe non trovato. Installa Microsoft Edge.")


def find_opera_executable() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for candidate in (
        local / "Programs" / "Opera" / "opera.exe",
        local / "Programs" / "Opera GX" / "opera.exe",
        Path(r"C:\Program Files\Opera\opera.exe"),
        Path(r"C:\Program Files (x86)\Opera\opera.exe"),
    ):
        if candidate.exists():
            return candidate
    # versione tipizzata: ...\Opera\133.x\opera.exe
    opera_root = local / "Programs" / "Opera"
    if opera_root.is_dir():
        matches = sorted(opera_root.glob("*/opera.exe"), reverse=True)
        if matches:
            return matches[0]
    raise FileNotFoundError(
        "opera.exe non trovato. Installa Opera oppure usa --browser edge."
    )


def find_browser_executable(engine: str | None = None) -> Path:
    eng = (engine or BROWSER_ENGINE or "edge").strip().lower()
    if eng == "opera":
        return find_opera_executable()
    return find_edge_executable()


def browser_process_name(engine: str | None = None) -> str:
    eng = (engine or BROWSER_ENGINE or "edge").strip().lower()
    return "opera.exe" if eng == "opera" else "msedge.exe"


def browser_label(engine: str | None = None) -> str:
    eng = (engine or BROWSER_ENGINE or "edge").strip().lower()
    return "Opera" if eng == "opera" else "Edge"


def cdp_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_browser_cdp(profile_dir: Path, port: int, wait_sec: int = 40, proxy: str = "") -> None:
    """Avvia Edge/Opera REALE con remote debugging (CDP)."""
    profile_dir = profile_dir.resolve()
    label = browser_label()
    if cdp_port_open(port):
        log_msg(f"{label} gia in ascolto su porta CDP {port}")
        return

    profile_dir.mkdir(parents=True, exist_ok=True)
    exe = find_browser_executable()
    proxy = (proxy or EDGE_PROXY or os.environ.get("MEF_PROXY") or "").strip()
    cmd = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]
    if proxy:
        cmd.append(f"--proxy-server={proxy}")
        log_msg(
            f"Avvio {label} + PROXY "
            f"({proxy.split('@')[-1] if '@' in proxy else proxy})"
        )
    else:
        log_msg(f"Avvio {label} reale: {exe.name} (profilo {profile_dir.name}, CDP :{port})")
    cmd.append(BASE_URL)
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    for _tick in range(max(5, wait_sec)):
        if cdp_port_open(port):
            time.sleep(2)
            log_msg(f"{label} CDP pronto")
            return
        time.sleep(1)
    raise RuntimeError(
        f"{label} non risponde su porta {port} dopo {wait_sec}s "
        f"(profilo forse gia' aperto SENZA debugging). "
        "Lo script proverà a chiudere quel profilo e ritentare."
    )


def start_edge_cdp(profile_dir: Path, port: int, wait_sec: int = 40, proxy: str = "") -> None:
    """Alias compatibile → start_browser_cdp."""
    start_browser_cdp(profile_dir, port, wait_sec=wait_sec, proxy=proxy)


STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['it-IT', 'it', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""


def apply_stealth(page) -> None:
    try:
        page.add_init_script(STEALTH_INIT_SCRIPT)
        page.evaluate(STEALTH_INIT_SCRIPT)
    except Exception:
        pass
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": STEALTH_INIT_SCRIPT},
        )
    except Exception:
        pass


def connect_edge_cdp(playwright, port: int):
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    if not browser.contexts:
        raise RuntimeError("Nessun contesto browser su CDP")
    context = browser.contexts[0]
    page = None
    for candidate in context.pages:
        if is_ricerca_form_url(candidate.url or ""):
            page = candidate
            break
    if page is None:
        for candidate in context.pages:
            if "giustiziatributaria.gov.it" in (candidate.url or ""):
                page = candidate
                break
    if page is None:
        page = context.pages[0] if context.pages else context.new_page()
    apply_stealth(page)
    return browser, context, page


def kill_browser_for_profile(profile_dir: Path) -> int:
    """Termina Edge/Opera che usano questo user-data-dir (cosi' si puo' riaprire con CDP)."""
    profile_s = str(profile_dir.resolve())
    like = profile_s.replace("'", "''").replace("[", "`[").replace("]", "`]")
    proc_name = browser_process_name()
    label = browser_label()
    script = (
        f"$n=0; Get-CimInstance Win32_Process -Filter \"Name='{proc_name}'\" | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine -like '*{like}*' }} | "
        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }}; "
        f"Write-Output $n"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            errors="ignore",
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        killed = int((out or "0").strip().splitlines()[-1] or "0")
    except Exception:
        killed = 0
    if killed:
        log_msg(f"  Chiusi {killed} processi {label} sul profilo {profile_dir.name}")
        time.sleep(2)
    return killed


def kill_edge_for_profile(profile_dir: Path) -> int:
    """Alias compatibile → kill_browser_for_profile."""
    return kill_browser_for_profile(profile_dir)


def _attach_cdp_session(playwright, port: int, warmup: int):
    log_msg(f"Collegamento {browser_label()} via CDP (:{port})")
    browser, context, page = connect_edge_cdp(playwright, port)
    try:
        navigate_to_ricerca_form(page, timeout_ms=90000)
    except (BrowserClosedError, PlaywrightError):
        log_msg("  Tab CDP irrecuperabile — apro nuovo tab ricerca...")
        page = context.new_page()
        navigate_to_ricerca_form(page, timeout_ms=90000)
    time.sleep(max(3, warmup))
    log_msg(f"Browser pronto (CDP / {browser_label()})")
    return browser, context, page


def open_edge_session(playwright, profile_dir: Path, port: int, warmup: int):
    """Preferisce sempre CDP (Edge/Opera reale con --remote-debugging-port)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    label = browser_label()

    # 1) Gia' in ascolto sulla porta → collegati
    if cdp_port_open(port):
        try:
            return _attach_cdp_session(playwright, port, warmup)
        except Exception as exc:
            last_err = exc
            log_msg(f"  CDP :{port} non usabile ({exc}) — riprovo avvio...", level="WARN")
            kill_listener_on_port(port)

    # 2) Avvia browser con debugging sulla porta del worker
    for attempt in range(3):
        try:
            start_browser_cdp(
                profile_dir, port, wait_sec=25 if attempt else 35, proxy=EDGE_PROXY
            )
            if cdp_port_open(port):
                return _attach_cdp_session(playwright, port, warmup)
            raise RuntimeError(f"Porta CDP {port} ancora chiusa dopo avvio")
        except Exception as exc:
            last_err = exc
            log_msg(
                f"  Avvio {label}+CDP fallito ({attempt + 1}/3): {exc}",
                level="WARN",
            )
            log_msg(f"  Chiudo {label} su questo profilo e riprovo...")
            kill_browser_for_profile(profile_dir)
            kill_listener_on_port(port)
            time.sleep(3)

    # 3) Fallback Playwright persistent (solo se profilo libero)
    try:
        log_msg("  Fallback: launch_persistent_context Playwright...")
        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": False,
            "accept_downloads": True,
            "locale": "it-IT",
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--start-minimized",
                "--disable-blink-features=AutomationControlled",
                f"--remote-debugging-port={port}",
            ],
        }
        if BROWSER_ENGINE == "opera":
            launch_kwargs["executable_path"] = str(find_opera_executable())
        else:
            launch_kwargs["channel"] = "msedge"
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        navigate_to_ricerca_form(page, timeout_ms=90000)
        time.sleep(max(5, warmup))
        log_msg(f"Browser pronto ({label})")
        return context, context, page
    except PlaywrightError as exc:
        last_err = exc

    raise RuntimeError(
        f"Impossibile avviare/collegare {label}. "
        f"Chiudi le finestre del profilo {profile_dir.name} e rilancia. "
        f"Dettaglio: {last_err}"
    ) from last_err


def attach_akamai_watch(page) -> None:
    """Rileva 403 su /search/submit dopo click Ricerca (non durante warmup)."""

    def _on_response(resp) -> None:
        global _akamai_403_seen, _last_403_log_ts
        if not _akamai_watch_active:
            return
        if "search/submit" in resp.url and resp.status == 403:
            _akamai_403_seen = True
            mark_akamai_cooldown()
            now = time.time()
            if now - _last_403_log_ts > 15:
                log_msg("Akamai 403 su /public/v1/search/submit", level="WARN")
                _last_403_log_ts = now

    page.on("response", _on_response)


def warmup_portale(page, seconds: int, *, navigate: bool = True) -> None:
    """Attesa cookie Akamai. Con CDP la pagina e' gia aperta da Edge."""
    if navigate:
        log_msg(f"Caricamento {BASE_URL}")
        page.goto(BASE_URL, timeout=90000)
        try:
            page.wait_for_load_state("networkidle", timeout=90000)
        except PlaywrightTimeoutError:
            pass
    else:
        log_msg(f"Attesa Akamai ({seconds}s) — pagina gia aperta in Edge")
    time.sleep(max(3, seconds))


def esegui_ricerca(
    page, anno: int, da: str, a: str, materia_codice: str, *, retries: int = 3, browser=None
) -> bool:
    global _akamai_403_seen, _akamai_watch_active
    log_msg(f"RICERCA inviata ({periodo_label(da, a)})")
    for attempt in range(retries):
        _akamai_403_seen = False
        try:
            with search_slot(f"ricerca {materia_codice}"):
                navigate_to_ricerca_form(page, timeout_ms=60000)
                time.sleep(1.0)
                prep_form_ricerca(page, anno, da, a, materia_codice)
                time.sleep(0.8)
                reset_search_status()
                _akamai_watch_active = True
                page.click('button.btn.btn-primary:has-text("Ricerca")')
                time.sleep(10)
                _akamai_watch_active = False

                if _akamai_403_seen or _search_submit_status == 403:
                    mark_akamai_cooldown()
                    # esci dallo slot prima del prompt manuale
                    pass
                elif attendi_risultati_caricati(page, timeout_sec=60):
                    err = errore_ricerca(page)
                    if err:
                        log_msg(f"  Ricerca fallita ({attempt + 1}/{retries}): {err}")
                        _salva_debug_ricerca(page, anno, materia_codice, attempt + 1)
                        time.sleep(3)
                        continue
                    log_msg_ricerca_ok(page, materia_codice)
                    return True
                else:
                    if not (_akamai_403_seen or _search_submit_status == 403):
                        body = page.inner_text("body")
                        if "Nessun risultato" in body:
                            log_msg("  Nessun risultato")
                            return False
                        log_msg(f"  Tabella senza link ({attempt + 1}/{retries})")
                        _salva_debug_ricerca(page, anno, materia_codice, attempt + 1)
                        time.sleep(3)
                        continue

            if _akamai_403_seen or _search_submit_status == 403:
                if AUTO_HEAL:
                    log_msg(
                        "  403 su Ricerca — esco subito per AUTO-HEAL (niente INVIO)",
                        level="WARN",
                    )
                    return False
                if attendi_ricerca_manuale(page, browser=browser):
                    log_msg_ricerca_ok(page, materia_codice)
                    return True
                time.sleep(5)
                continue
        except TimeoutError as exc:
            log_msg(f"  {exc}", level="WARN")
            return False
        except BrowserClosedError:
            raise
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)
            log_msg(f"  Errore ricerca ({attempt + 1}/{retries}): {exc}")
            time.sleep(3)
        except PlaywrightTimeoutError:
            log_msg(f"  Timeout ricerca ({attempt + 1}/{retries})")
            time.sleep(3)
    return False


def debug_table_structure(page) -> None:
    try:
        info = page.evaluate(
            """
            () => {
                const table = [...document.querySelectorAll("table")].find(t =>
                    t.innerText.includes("Numero provvedimento")
                );
                if (!table) return { error: "tabella risultati non trovata" };
                const row = table.querySelector("tbody tr");
                if (!row) return { error: "nessuna riga tbody" };
                return {
                    links: [...row.querySelectorAll("a")].map(a => ({
                        title: a.getAttribute("title"),
                        href: a.getAttribute("href"),
                        cls: a.className,
                        html: a.outerHTML.slice(0, 300),
                    })),
                    buttons: [...row.querySelectorAll("button")].map(b => ({
                        title: b.getAttribute("title"),
                        cls: b.className,
                        html: b.outerHTML.slice(0, 300),
                    })),
                    icons: [...row.querySelectorAll("i, svg")].map(el => ({
                        cls: el.className?.baseVal || el.className,
                        html: el.outerHTML.slice(0, 200),
                    })),
                };
            }
            """
        )
        log_msg(f"  DEBUG prima riga: {info}")
    except Exception as exc:
        log_msg(f"  DEBUG table fallito: {exc}")


def log_msg_ricerca_ok(page, materia_codice: str) -> None:
    n_links = count_download_links(page)
    n_rows = page.locator("table tbody tr").count()
    body = page.inner_text("body")
    m = re.search(r"Risultati di ricerca\s*\(([\d.]+)\)", body)
    totale = m.group(1).replace(".", "") if m else "?"
    _LOG_CTX["totale_portale"] = totale
    log_msg(
        f"RICERCA OK -> ~{totale} sul portale | {n_rows} righe tabella | {n_links} link"
    )
    reset_akamai_flags()
    if n_links == 0 and n_rows > 0:
        debug_table_structure(page)


def extract_rows_from_page(page) -> List[Dict[str, Any]]:
    """Portale 2026: link 'Visualizza provvedimento' + metadati dalla riga tabella."""
    return page.evaluate(
        """
        () => {
            const links = [...document.querySelectorAll("a[title^='Visualizza provvedimento']")];
            const headers = [...document.querySelectorAll("table thead th")]
                .map(th => th.innerText.trim().toLowerCase());
            const idx = {
                tipo: headers.findIndex(h => h === "tipo"),
                numdec: headers.findIndex(h => h.includes("numero")),
                anno: headers.findIndex(h => h === "anno"),
                autorita: headers.findIndex(h => h.includes("autorit") && h.includes("emittente")),
                datdep: headers.findIndex(h => h.includes("data deposito")),
            };
            const pick = (cells, key) => {
                const i = idx[key];
                return i >= 0 && cells[i] ? cells[i].trim() : null;
            };
            return links.map(link => {
                const row = link.closest("tr");
                const cells = row ? [...row.querySelectorAll("td")].map(td => td.innerText.trim()) : [];
                return {
                    download_href: link.getAttribute("href"),
                    download_title: link.getAttribute("title"),
                    tipo: pick(cells, "tipo") || "Sentenza",
                    numdec: pick(cells, "numdec"),
                    anno: pick(cells, "anno"),
                    autorita_emittente: pick(cells, "autorita"),
                    datdep: pick(cells, "datdep"),
                    cells,
                };
            });
        }
        """
    )


def row_to_meta(row: Dict[str, Any]) -> Dict[str, Any] | None:
    cells = row.get("cells") or []
    meta = parse_portal_html_row(cells) if len(cells) >= 4 else None
    if not meta or not meta.get("ok"):
        meta = parse_portal_title(row.get("download_title") or "")
    if not meta or not meta.get("ok"):
        return None
    meta["autorita_emittente"] = meta.get("cortePortale")
    meta["datdep"] = row.get("datdep")
    return meta


def scarica_pdf(page, link_index: int, dest_path: Path, row: Dict[str, Any] | None = None) -> bool:
    """Apre dettaglio provvedimento e clicca 'Scarica il pdf del provvedimento'."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    lista_url = page.url
    ok = False
    try:
        vis = download_link_locator(page, link_index)
        href = (row.get("download_href") if row else None) or vis.get_attribute("href") or ""
        if href.startswith("/"):
            href = f"{SITE_ORIGIN}{href}"
        if href.startswith("http"):
            page.goto(href, timeout=60000)
        else:
            vis.click()
        page.wait_for_url("**/ricerca/dettaglio/**", timeout=60000)
        page.wait_for_selector(DETTAGLIO_SCARICA_BTN, timeout=30000)
        try:
            with page.expect_download(timeout=90000) as download_info:
                page.locator(DETTAGLIO_SCARICA_BTN).click()
            download_info.value.save_as(dest_path)
        except Exception:
            pdf_url = page.evaluate(
                """() => {
                    const btn = document.querySelector('button[title=\"Scarica il pdf del provvedimento\"]');
                    return btn ? (btn.getAttribute('data-url') || btn.dataset?.url || '') : '';
                }"""
            )
            if not pdf_url:
                onclick = page.locator(DETTAGLIO_SCARICA_BTN).get_attribute("onclick") or ""
                m = re.search(r"https?://[^'\"\\s]+\\.pdf", onclick, re.I)
                pdf_url = m.group(0) if m else ""
            if not pdf_url:
                resp = page.context.request.get(page.url, timeout=90000)
                if resp.ok:
                    m = re.search(r'href=\"([^\"]+\\.pdf[^\"]*)\"', resp.text(), re.I)
                    pdf_url = m.group(1) if m else ""
            if pdf_url:
                if pdf_url.startswith("/"):
                    pdf_url = f"{SITE_ORIGIN}{pdf_url}"
                resp = page.context.request.get(pdf_url, timeout=90000)
                if resp.ok:
                    dest_path.write_bytes(resp.body())
        ok = dest_path.exists() and dest_path.read_bytes().startswith(b"%PDF")
        return ok
    except Exception as exc:
        log_msg(f"  Errore download dettaglio: {exc}")
        raise_if_browser_closed(exc)
        return False
    finally:
        try:
            if page.url != lista_url:
                page.goto(lista_url, timeout=60000)
                page.wait_for_selector(VISUALIZZA_SELECTOR, timeout=45000)
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)


def pagina_corrente(page) -> int:
    try:
        return int(page.query_selector("a.page-link.active").inner_text().strip())
    except Exception:
        return -1


def ultima_pagina(page) -> int:
    try:
        buttons = page.query_selector_all("a.page-link.info-cursor")
        numeri = [int(b.inner_text().strip()) for b in buttons if b.inner_text().strip().isdigit()]
        return max(numeri) if numeri else -1
    except Exception:
        return -1


def attendi_pagina_pronta(page, *, prev: int | None = None, timeout_sec: int = 15) -> bool:
    """Dopo click su '>', attende che la tabella della nuova pagina sia caricata."""
    for tick in range(timeout_sec * 2):
        cur = pagina_corrente(page)
        links = count_download_links(page)
        rows = count_data_rows(page)
        if prev is not None and cur <= prev:
            time.sleep(0.5)
            continue
        if links > 0 or rows >= 3:
            return True
        time.sleep(0.5)
    return False


def _patch_localstorage_pagenumber(page, target: int) -> dict:
    """Imposta pageNumber in paginationDetails + request (+ persist:root se c'e'). Niente fetch."""
    return page.evaluate(
        """(target) => {
          const digPage = (o) => {
            if (!o || typeof o !== 'object') return false;
            let hit = false;
            for (const [k, v] of Object.entries(o)) {
              const lk = String(k).toLowerCase();
              if (typeof v === 'number' &&
                  ['pagenumber','page','currentpage','pageindex','pagina','pageno'].includes(lk)) {
                o[k] = target;
                hit = true;
              } else if (v && typeof v === 'object') {
                if (digPage(v)) hit = true;
              }
            }
            return hit;
          };
          let pd = {};
          try { pd = JSON.parse(localStorage.getItem('paginationDetails') || '{}'); }
          catch (e) { pd = {}; }
          pd.pageNumber = target;
          if (!pd.pageSize) pd.pageSize = 10;
          localStorage.setItem('paginationDetails', JSON.stringify(pd));

          let hasRequest = false;
          try {
            const body = JSON.parse(localStorage.getItem('request') || 'null');
            if (body && typeof body === 'object') {
              if (!body.paginationDetails) body.paginationDetails = {};
              body.paginationDetails.pageNumber = target;
              body.paginationDetails.pageSize =
                body.paginationDetails.pageSize || pd.pageSize || 10;
              localStorage.setItem('request', JSON.stringify(body));
              hasRequest = true;
            }
          } catch (e) {}

          try {
            const rootRaw = localStorage.getItem('persist:root');
            if (rootRaw) {
              let root = JSON.parse(rootRaw);
              if (typeof root === 'object') {
                for (const k of Object.keys(root)) {
                  if (typeof root[k] === 'string' &&
                      (root[k][0] === '{' || root[k][0] === '[')) {
                    try {
                      const inner = JSON.parse(root[k]);
                      if (digPage(inner)) root[k] = JSON.stringify(inner);
                    } catch (e) {}
                  }
                }
                digPage(root);
                localStorage.setItem('persist:root', JSON.stringify(root));
              }
            }
          } catch (e) {}

          return {
            ok: true,
            hasRequest,
            pd: localStorage.getItem('paginationDetails'),
          };
        }""",
        int(target),
    )


def prova_salto_local_storage(page, target: int) -> bool:
    """Salto pagina: patch localStorage.pageNumber + reload (come CTRL+R). Niente fetch extra."""
    log_msg(
        f"  localStorage: pageNumber={target} poi reload (CTRL+R)...",
    )
    reset_akamai_flags()
    try:
        info = _patch_localstorage_pagenumber(page, target)
    except Exception as exc:
        log_msg(f"  localStorage patch fallito: {exc}", level="WARN")
        return False

    if not (info or {}).get("ok"):
        log_msg("  localStorage patch non riuscito", level="WARN")
        return False
    if not (info or {}).get("hasRequest"):
        log_msg(
            "  WARN: localStorage.request assente — serve una Ricerca prima del salto",
            level="WARN",
        )

    for attempt in range(1, 3):
        try:
            log_msg(f"  Reload pagina (tentativo {attempt}/2)...")
            page.reload(wait_until="domcontentloaded", timeout=90000)
            time.sleep(2.5)
            try:
                page.wait_for_selector(VISUALIZZA_SELECTOR, timeout=25000)
            except Exception:
                pass
            if attendi_pagina_pronta(page, timeout_sec=20):
                landed = pagina_corrente(page)
                if landed == target:
                    log_msg(f"  Pagina {target} OK (localStorage + CTRL+R)")
                    return True
                log_msg(
                    f"  Dopo reload UI a pagina {landed} (volevo {target})",
                    level="WARN",
                )
            # Ripatch prima del 2° reload (React a volte riscrive LS)
            _patch_localstorage_pagenumber(page, target)
        except Exception as exc:
            log_msg(f"  Reload fallito: {exc}", level="WARN")
            return False
    return False


def attendi_pagina_manuale(page, target: int, browser=None) -> bool:
    """L'utente va alla pagina target in Edge e conferma con INVIO."""
    if AUTO_HEAL:
        log_msg(
            f"  Pagina {target}: modalità bot — nessun INVIO, fallisce per AUTO-HEAL",
            level="WARN",
        )
        return False
    log_msg(
        f"  >>> OBBLIGATORIO: in Edge vai alla PAGINA {target} dei risultati "
        f"(numeri in basso / freccia), tabella visibile, poi INVIO qui <<<",
        skip_ctx=True,
    )
    try:
        input()
    except KeyboardInterrupt:
        log_msg("Interrotto (Ctrl+C) — riprendi con --resume.")
        return False
    if browser is not None:
        page = pick_portal_page(browser, page)
        attach_akamai_watch(page)
        attach_search_status_watch(page)
    reset_akamai_flags()
    cur = pagina_corrente(page)
    if cur == target and attendi_pagina_pronta(page):
        log_msg(f"  Pagina {target} confermata manualmente.")
        return True
    if cur >= target and attendi_pagina_pronta(page):
        log_msg(f"  Pagina corrente {cur} (attesa {target}) — proseguo da qui.")
        return True
    log_msg(
        f"  Sei a pagina {cur}, serviva pagina {target}. "
        "In Edge vai avanti (numero o '>') e premi INVIO di nuovo."
    )
    return False


def ripristina_pagina_target(page, target: int, browser=None):
    """Dopo ricerca manuale: torna alla pagina di lavoro senza rewrite inutili."""
    if target <= 1:
        return page
    if browser is not None:
        page = pick_portal_page(browser, page)
        attach_akamai_watch(page)
        attach_search_status_watch(page)
    cur = pagina_corrente(page)
    if cur >= target and attendi_pagina_pronta(page):
        log_msg(f"  Ripristino pagina OK: su pagina {cur} (target {target})")
        return page
    gap = target - max(cur, 1)
    log_msg(f"  Ricerca manuale: pagina {cur}, ripristino verso pagina {target} (gap={gap})...")
    reset_akamai_flags()
    # Gap 1: sei sulla pagina appena fatta → un solo '>' (NO rewrite pageNumber).
    if gap == 1:
        log_msg(f"  Gap 1: un solo click '>' verso {target} (niente salto diretto)...")
        ok, page = avanza_pagina_successiva(
            page, cur if cur > 0 else 1, target, browser, skip_only=False
        )
        if ok and pagina_corrente(page) >= target:
            return page
    elif gap > MAX_AUTO_PAGE_WALK:
        log_msg(
            f"  Gap {gap} pagine: troppo grande per click automatici — "
            "vai tu in Edge alla pagina target."
        )
    elif salta_a_pagina(page, target):
        return page
    log_msg(f"  Salto auto a pagina {target} fallito — naviga manualmente in Edge.")
    for attempt in range(5):
        if attendi_pagina_manuale(page, target, browser=browser):
            if browser is not None:
                page = pick_portal_page(browser, page)
                attach_akamai_watch(page)
                attach_search_status_watch(page)
            return page
        if attempt < 4:
            log_msg(f"  Ripeti navigazione manuale verso pagina {target} ({attempt + 2}/5)...")
    log_msg(f"  ATTENZIONE: proseguo da pagina {pagina_corrente(page)} invece di {target}")
    return page


def _patch_pagination_page_number(data: dict, page_number: int) -> dict:
    """Imposta paginationDetails.pageNumber nel body search/submit."""
    out = json.loads(json.dumps(data))  # deep copy
    pag = out.get("paginationDetails")
    if not isinstance(pag, dict):
        pag = {}
        out["paginationDetails"] = pag
    pag["pageNumber"] = int(page_number)
    if "pageSize" not in pag:
        pag["pageSize"] = 10
    return out


def salta_a_pagina_via_pagenumber(page, target: int) -> bool:
    """Un solo search/submit con paginationDetails.pageNumber = target (niente N click '>').

    Riscrive il body della prossima POST /search/submit e triggera un click
    sulla paginazione. Prova pageNumber=target (come UI), poi target-1 (0-based).
    """
    global _akamai_403_seen
    if target <= 1:
        return attendi_pagina_pronta(page)

    candidates = [target]
    if target > 1:
        candidates.append(target - 1)

    pattern = "**/public/v1/search/submit*"
    for page_num in candidates:
        if _akamai_403_seen or _search_submit_status == 403:
            return False
        reset_akamai_flags()
        log_msg(f"  Salto diretto: paginationDetails.pageNumber={page_num} (target UI {target})")
        state = {"rewrote": False, "status": None}

        # IMPORTANTE: handler con 1 solo arg (route). Se ne ha 2, Playwright
        # passa Request come 2° arg e rompe int(page_number).
        target_pn = int(page_num)

        def handle_route(route) -> None:
            req = route.request
            if "search/submit" not in req.url or req.method.upper() != "POST":
                route.continue_()
                return
            try:
                raw = req.post_data or ""
                data = None
                if raw.strip().startswith("{"):
                    data = json.loads(raw)
                elif _last_search_body:
                    data = _last_search_body
                if not isinstance(data, dict):
                    route.continue_()
                    return
                patched = _patch_pagination_page_number(data, target_pn)
                state["rewrote"] = True
                _store_search_body_from_post(json.dumps(patched))
                route.continue_(post_data=json.dumps(patched, separators=(",", ":")))
            except Exception as exc:
                log_msg(f"  Rewrite pageNumber fallito: {exc}", level="WARN")
                try:
                    route.continue_()
                except Exception:
                    try:
                        route.abort()
                    except Exception:
                        pass

        def on_response(resp) -> None:
            if "search/submit" in resp.url:
                state["status"] = resp.status

        try:
            page.unroute(pattern)
        except Exception:
            pass
        page.route(pattern, handle_route)
        page.on("response", on_response)
        try:
            log_msg("  Cerco link paginazione per trigger...")
            cur = pagina_corrente(page)
            trigger = None
            for link in page.query_selector_all("a.page-link.info-cursor"):
                txt = (link.inner_text() or "").strip()
                if txt.isdigit() and (cur < 1 or int(txt) != cur):
                    trigger = link
                    break
            if trigger is None:
                trigger = page.query_selector("a.page-link.info-cursor:text('>')")
            if trigger is None:
                trigger = page.query_selector("a.page-link.active")
            if trigger is None:
                log_msg("  Nessun link paginazione per triggerare search/submit")
                return False

            log_msg(f"  Click trigger paginazione (UI={cur})...")
            # no_wait_after: evita hang se la navigation non completa dopo rewrite
            trigger.click(timeout=8000, no_wait_after=True)
            for _ in range(48):  # max ~12s
                if state["status"] is not None or _akamai_403_seen:
                    break
                time.sleep(0.25)
            log_msg(
                f"  Dopo click: rewrote={state['rewrote']} status={state['status']} "
                f"akamai403={_akamai_403_seen}"
            )
            if _akamai_403_seen or state["status"] == 403 or _search_submit_status == 403:
                mark_akamai_cooldown()
                log_msg("  403 sul salto diretto pageNumber")
                continue
            if not state["rewrote"]:
                log_msg("  search/submit non intercettato — salto diretto saltato")
                continue
            if state["status"] not in (None, 200):
                log_msg(f"  search/submit status {state['status']} dopo rewrite pageNumber")
                continue
            if attendi_pagina_pronta(page, timeout_sec=12):
                landed = pagina_corrente(page)
                if landed == target:
                    log_msg(f"  Pagina {target} pronta (pageNumber={page_num})")
                    return True
                log_msg(
                    f"  Dopo pageNumber={page_num} UI a pagina {landed} (attesa {target})"
                )
                if landed == page_num and page_num != target:
                    continue
        except TimeoutError as exc:
            log_msg(f"  {exc}", level="WARN")
            return False
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)
            log_msg(f"  Errore salto diretto: {exc}", level="WARN")
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass
            try:
                page.unroute(pattern, handle_route)
            except Exception:
                try:
                    page.unroute(pattern)
                except Exception:
                    pass

    return False


def salta_a_pagina(page, target: int) -> bool:
    """Va alla pagina richiesta. Gap grandi: niente rewrite (si pianta) → False (manuale)."""
    global _akamai_403_seen
    reset_akamai_flags()
    if target <= 1:
        return attendi_pagina_pronta(page)

    cur0 = pagina_corrente(page)
    if cur0 == target:
        return attendi_pagina_pronta(page)
    if cur0 > target:
        log_msg(f"  ATTENZIONE: pagina corrente {cur0} oltre target {target}")
        return False

    gap = target - max(cur0, 1)
    log_msg(f"  Salto alla pagina {target} (ora {cur0}, gap={gap})...")

    # Gap enormi (resume p.200+): il rewrite pageNumber si appende/403. Non provarci.
    if gap > MAX_AUTO_PAGE_WALK:
        log_msg(
            f"  Gap={gap} troppo grande per auto-salto (max click {MAX_AUTO_PAGE_WALK}). "
            "Provo localStorage, poi ti chiedo di andare tu in Edge."
        )
        if prova_salto_local_storage(page, target):
            return True
        return False

    # Gap medi: un solo tentativo rewrite breve, poi click
    if gap >= DIRECT_REWRITE_MIN_GAP:
        if salta_a_pagina_via_pagenumber(page, target):
            return True
        log_msg("  Salto diretto pageNumber fallito — provo click paginazione...")
        cur0 = pagina_corrente(page)
        gap = target - max(cur0, 1)
        if gap > MAX_AUTO_PAGE_WALK:
            return False

    log_msg(f"  Gap {gap}: solo click '>' / numeri (niente rewrite pageNumber)")

    for attempt in range(MAX_AUTO_PAGE_WALK + 5):
        cur = pagina_corrente(page)
        if cur == target:
            if attendi_pagina_pronta(page, prev=cur - 1 if cur > 1 else None):
                log_msg(f"  Pagina {target} pronta")
                return True
            log_msg(f"  Pagina {target} visibile ma tabella non pronta (tentativo {attempt + 1})")
        elif cur > target:
            log_msg(f"  ATTENZIONE: pagina corrente {cur} oltre target {target}")
            return False

        if cur < 0:
            log_msg("  Paginazione non leggibile (pagina -1) — serve nuova ricerca")
            return False

        if _akamai_403_seen or _search_submit_status == 403:
            log_msg("  Akamai 403 in paginazione — serve navigazione manuale")
            return False

        try:
            with search_slot(f"salta->{target} da {cur}"):
                clicked = False
                best_jump = None
                for link in page.query_selector_all("a.page-link.info-cursor"):
                    txt = (link.inner_text() or "").strip()
                    if txt.isdigit():
                        n = int(txt)
                        if n == target:
                            link.click(timeout=8000, no_wait_after=True)
                            clicked = True
                            time.sleep(1.5)
                            break
                        if cur > 0 and cur < n <= target and (best_jump is None or n > best_jump):
                            best_jump = n

                if not clicked and best_jump is not None and best_jump != cur:
                    for link in page.query_selector_all("a.page-link.info-cursor"):
                        txt = (link.inner_text() or "").strip()
                        if txt.isdigit() and int(txt) == best_jump:
                            link.click(timeout=8000, no_wait_after=True)
                            clicked = True
                            time.sleep(1.5)
                            break

                if not clicked:
                    next_btn = page.query_selector("a.page-link.info-cursor:text('>')")
                    if not next_btn or not next_btn.is_enabled():
                        log_msg(f"  Impossibile raggiungere pagina {target} (fermo a pagina {cur})")
                        return False
                    prev = cur
                    next_btn.click(timeout=8000, no_wait_after=True)
                    time.sleep(1.5)
                    if not attendi_pagina_pronta(page, prev=prev if prev > 0 else None):
                        if _akamai_403_seen or _search_submit_status == 403:
                            mark_akamai_cooldown()
                            log_msg("  403 dopo click '>' — rifare ricerca")
                            return False
                        log_msg(f"  Attendo caricamento pagina dopo '>' ({attempt + 1})...")
                    continue

                if _akamai_403_seen or _search_submit_status == 403:
                    mark_akamai_cooldown()
                    log_msg("  403 dopo click numero pagina — rifare ricerca")
                    return False
                if not attendi_pagina_pronta(page, prev=cur if cur > 0 else None):
                    log_msg(f"  Attendo caricamento pagina {target} ({attempt + 1})...")
        except TimeoutError as exc:
            log_msg(f"  {exc}", level="WARN")
            return False
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)
            log_msg(f"  Errore click paginazione: {exc}", level="WARN")
            return False

    log_msg(f"  Stop salto auto a pagina {target} (max {MAX_AUTO_PAGE_WALK} click)")
    return False


def pausa_anti_akamai_prima_di_avanti(*, skip_only: bool, next_page: int) -> None:
    """
    Pausa OBBLIGATORIA prima di '>': fortemente variabile (anti-pattern fisso ~22s).
    Skip-only: range più largo + ogni 6–8 skip un 'respiro' lungo.
    """
    global _skip_page_streak
    if skip_only:
        _skip_page_streak += 1
    else:
        _skip_page_streak = 0

    base = max(8.0, float(PAGE_DELAY_SEC))
    if skip_only:
        lo = base * PAGE_DELAY_SKIP_JITTER_MIN + PAGE_DELAY_SKIP_EXTRA_SEC * 0.5
        hi = base * PAGE_DELAY_SKIP_JITTER_MAX + PAGE_DELAY_SKIP_EXTRA_SEC
        # triangolare: più spesso verso il centro-alto del range (più lento)
        mode = lo + (hi - lo) * random.uniform(0.45, 0.70)
        total = float(random.triangular(lo, hi, mode))
        motivo = "solo-skip variabile"
    else:
        lo = base * PAGE_DELAY_JITTER_MIN
        hi = base * PAGE_DELAY_JITTER_MAX
        mode = lo + (hi - lo) * random.uniform(0.35, 0.60)
        total = float(random.triangular(lo, hi, mode))
        motivo = "normale variabile"

    # jitter fine + occasional micro-burst pause
    total += random.uniform(0.3, 3.0)
    if random.random() < 0.08:
        bump = random.uniform(4.0, 10.0)
        total += bump
        motivo += f"+bump{bump:.0f}s"

    if in_soft_mode():
        total += random.uniform(2.0, 6.0)
        motivo += "+soft"

    # Dopo tante skip di fila: respiro (rompe il ritmo da bot, senza bloccare minuti)
    if skip_only and _skip_page_streak >= 10 and _skip_page_streak % 10 == 0:
        breath = random.uniform(15.0, 35.0)
        total += breath
        motivo += f"+respiro{breath:.0f}s(streak={_skip_page_streak})"

    total = max(6.0, total)
    log_msg(
        f"  Pausa anti-Akamai {total:.1f}s prima di pagina {next_page} ({motivo})..."
    )
    time.sleep(total)


def avanza_pagina_successiva(page, prev_page: int, pending_page: int, browser, *, skip_only: bool):
    """Click '>' con pausa + retry su 403. Ritorna (ok, page)."""
    pausa_anti_akamai_prima_di_avanti(skip_only=skip_only, next_page=pending_page)

    for attempt in range(1, PAGINATION_403_RETRIES + 1):
        reset_akamai_flags()
        next_btn = page.query_selector("a.page-link.info-cursor:text('>')")
        if not next_btn or not next_btn.is_enabled():
            log_msg("  Nessun '>' abilitato — fine risultati")
            return False, page
        try:
            with search_slot(f"pagina>{prev_page}#{attempt}"):
                try:
                    page.mouse.wheel(0, random.randint(80, 320))
                except Exception:
                    pass
                time.sleep(random.uniform(0.4, 1.0))
                next_btn.click(timeout=8000, no_wait_after=True)
                time.sleep(1.5)
                blocked = _akamai_403_seen or _search_submit_status == 403
                if blocked:
                    mark_akamai_cooldown()
                else:
                    if attendi_pagina_pronta(
                        page, prev=prev_page if prev_page > 0 else None, timeout_sec=20
                    ):
                        return True, page
                    landed = pagina_corrente(page)
                    if landed > prev_page:
                        return True, page
        except TimeoutError as exc:
            log_msg(f"  {exc}", level="WARN")
            return False, page
        except PlaywrightError as exc:
            raise_if_browser_closed(exc)
            log_msg(f"  Errore click '>': {exc}", level="WARN")
            return False, page

        if _akamai_403_seen or _search_submit_status == 403:
            enter_soft_mode(15.0)
            # 1° 403 su '>': una pausa locale e ritento; poi AUTO-HEAL (locale→VPN)
            if attempt < PAGINATION_403_RETRIES:
                wait = random.uniform(35, 70)
                log_msg(
                    f"  403 su '>': pausa locale {wait:.0f}s poi ritento "
                    f"({attempt}/{PAGINATION_403_RETRIES}) — nessun cambio IP ancora",
                    level="WARN",
                )
                time.sleep(wait)
                reset_akamai_flags()
                continue
            if AUTO_HEAL and HEALER is not None:
                log_msg(
                    "  403 su '>' persistente — passo ad AUTO-HEAL "
                    "(prima locale, poi VPN se serve)",
                    level="WARN",
                )
                return False, page
            wait = PAGINATION_403_BASE_WAIT * attempt + random.uniform(0, 12)
            log_msg(
                f"  403 su '>': pausa {wait:.0f}s poi riprovo "
                f"({attempt}/{PAGINATION_403_RETRIES})",
                level="WARN",
            )
            time.sleep(wait)
            continue

        log_msg(f"  Dopo '>' tabella non pronta — attendo 8s ({attempt}/{PAGINATION_403_RETRIES})")
        time.sleep(8)

    if AUTO_HEAL and HEALER is not None:
        log_msg(
            "  403 persistente — esco per AUTO-HEAL (locale poi VPN se serve)...",
            level="WARN",
        )
        return False, page
    log_msg("  403 persistente in paginazione — ricerca manuale in Edge...")
    if attendi_ricerca_manuale(
        page, timeout_sec=300, browser=browser, target_page=pending_page
    ):
        page = ripristina_pagina_target(page, pending_page, browser=browser)
        reset_akamai_flags()
        return True, page
    return False, page


def processa_risultati(
    page,
    output_dir: Path,
    session: set[str],
    stats: Dict[str, int],
    max_pagine: int,
    server_keys: set[str],
    *,
    start_pagina: int = 1,
    year: int = 0,
    trimestre_idx: int = 0,
    materia_codice: str = "",
    on_page_done: Any = None,
    skip_goto: bool = False,
    browser=None,
) -> bool:
    """Elabora pagine risultati. True = materia finita; False = interrotta (403/errore)."""
    global _session_dl_count
    log_msg(
        f"  Avvio elaborazione risultati / download PDF "
        f"(pausa DL {DOWNLOAD_DELAY_MIN:.0f}-{DOWNLOAD_DELAY_MAX:.0f}s, "
        f"pagina ~{PAGE_DELAY_SEC:.0f}s+)..."
    )
    pending_page = max(1, start_pagina)
    # Floor: non permettere di "lavorare" / avanzare da una pagina molto più bassa
    # del target (bug UI stale post-ricerca → pag.1 con active falso=344).
    page_floor = max(1, start_pagina)
    if start_pagina > 1 and not skip_goto:
        reset_akamai_flags()
        cur_now = pagina_corrente(page)
        gap_now = start_pagina - max(cur_now, 1)
        # MAI fidarsi di "già sulla pagina N": dopo ricerca l'UI può mentire.
        # Ripristino obbligatorio verso start_pagina.
        log_msg(
            f"  Ripristino obbligatorio pagina {start_pagina} "
            f"(UI dichiara {cur_now}, gap={gap_now})...",
            level="WARN",
        )
        restored = False
        if gap_now > MAX_AUTO_PAGE_WALK or cur_now >= start_pagina or cur_now < 1:
            if not prova_salto_local_storage(page, start_pagina):
                log_msg(
                    "  localStorage non ha portato alla pagina.",
                    level="WARN",
                )
            restored = (
                pagina_corrente(page) >= start_pagina and attendi_pagina_pronta(page)
            )
        if not restored:
            if not salta_a_pagina(page, start_pagina):
                log_msg(f"  Salto automatico a pagina {start_pagina} fallito.")
            restored = (
                pagina_corrente(page) >= start_pagina and attendi_pagina_pronta(page)
            )
        if not restored and AUTO_HEAL and HEALER is not None:
            log_msg(
                "  Resume pagina alta fallito — AUTO-HEAL riproverà dall'esterno.",
                level="WARN",
            )
            return False
        if not restored:
            for attempt in range(8):
                if attendi_pagina_manuale(page, start_pagina, browser=browser):
                    restored = True
                    break
                log_msg(
                    f"  Ancora non sei a pagina {start_pagina} "
                    f"(ora {pagina_corrente(page)}). Riprova ({attempt + 2}/8)..."
                )
        if not restored:
            log_msg(f"  ERRORE: impossibile confermare pagina {start_pagina}")
            return False
        landed_ok = pagina_corrente(page)
        log_msg(f"  Pagina confermata: {landed_ok} (target {start_pagina})")
        pending_page = max(pending_page, landed_ok if landed_ok > 0 else start_pagina)
        page_floor = max(page_floor, start_pagina)
    pagine = 0
    tentativi = 0

    while tentativi < 40:
        if max_pagine > 0 and pagine >= max_pagine:
            return True

        if not _akamai_watch_active:
            reset_search_status()

        marker = page.query_selector("span.color-black")
        if marker and "Nessun risultato" in marker.inner_text():
            log_msg("  Nessun risultato in tabella")
            return True

        try:
            page.wait_for_selector(VISUALIZZA_SELECTOR, timeout=10000)
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            raise_if_browser_closed(exc)
            tentativi += 1
            if _akamai_403_seen or _search_submit_status == 403:
                if AUTO_HEAL and HEALER is not None:
                    log_msg(
                        "  Tabella persa (403) — AUTO-HEAL prendera' il controllo...",
                        level="WARN",
                    )
                    reset_akamai_flags()
                    return False
                log_msg("  Tabella persa (403 Akamai) — attendo ricerca manuale in Edge...")
                if attendi_ricerca_manuale(
                    page, timeout_sec=300, browser=browser, target_page=pending_page
                ):
                    page = ripristina_pagina_target(page, pending_page, browser=browser)
                    tentativi = 0
                    reset_akamai_flags()
                    continue
                reset_akamai_flags()
                return False
            # Log solo al 1°, ogni 5 tentativi e all'ultimo (evita spam)
            if tentativi == 1 or tentativi % 5 == 0 or tentativi >= 40:
                log_msg(
                    f"  Tabella senza link 'Visualizza' — riprovo "
                    f"({tentativi}/40, ~{tentativi * 2}s)"
                )
            time.sleep(2)
            continue

        rows = extract_rows_from_page(page)
        if not rows:
            tentativi += 1
            if tentativi == 1 or tentativi % 5 == 0 or tentativi >= 40:
                log_msg(f"  Tabella vuota — riprovo ({tentativi}/40, ~{tentativi * 2}s)")
            time.sleep(2)
            continue

        if len(rows) < 10 and pagina_corrente(page) != ultima_pagina(page):
            tentativi += 1
            if tentativi == 1 or tentativi % 5 == 0 or tentativi >= 40:
                log_msg(
                    f"  Solo {len(rows)} righe (attese 10) — riprovo "
                    f"({tentativi}/40, ~{tentativi * 2}s)"
                )
            time.sleep(2)
            continue

        tentativi = 0
        page_dl = stats["downloaded"]
        page_skip_cache = stats["skipped_cache"]
        page_skip_local = stats["skipped_local"]
        page_skip_meta = stats["skipped_meta"]
        page_failed = stats["failed"]
        cur_page = pagina_corrente(page)
        # Guardia anti-regressione: se il target era alto e l'UI è a pag.1/bassa, STOP.
        if page_floor > 5 and cur_page > 0 and cur_page < page_floor - 1:
            log_msg(
                f"  STOP: UI a pag.{cur_page} ma dovevo essere >= {page_floor} — "
                f"NON riparto da pag.1 (esco per AUTO-HEAL / ripristino)",
                level="WARN",
            )
            if on_page_done:
                on_page_done(page_floor)
            return False
        set_log_ctx(pagina=cur_page if cur_page > 0 else start_pagina)
        if cur_page > 0:
            pending_page = cur_page
        log_msg(f"elaboro {len(rows)} righe")
        for index, row in enumerate(rows):
            meta = row_to_meta(row)
            if not meta:
                stats["skipped_meta"] += 1
                continue

            nome_file = meta["nomeFile"]
            nome_base = meta["nomeBase"]
            session_key = nome_base.lower()

            dest = output_dir / nome_file
            if dest.exists() or session_key in session:
                stats["skipped_local"] += 1
                continue

            if gia_sul_server(nome_base, server_keys):
                stats["skipped_cache"] += 1
                continue

            try:
                # micro-interazione umana prima del download
                try:
                    page.mouse.wheel(0, random.randint(40, 180))
                    time.sleep(random.uniform(0.2, 0.6))
                except Exception:
                    pass
                if scarica_pdf(page, index, dest, row):
                    session.add(session_key)
                    save_session(session)
                    stats["downloaded"] += 1
                    clear_403_streak()
                    log_msg(f"SCARICATO {nome_file}", level="OK")
                    _session_dl_count += 1
                    pausa_tra_download()
                    if (
                        AUTO_HEAL
                        and SESSION_ROTATE_EVERY > 0
                        and _session_dl_count >= SESSION_ROTATE_EVERY
                    ):
                        _session_dl_count = 0
                        if on_page_done:
                            # Mai salvare pag.1 se il floor era alto
                            pg_now = pagina_corrente(page)
                            on_page_done(max(page_floor, pg_now if pg_now > 0 else page_floor))
                        raise SessionRotateNeeded(
                            f"Rotazione sessione dopo {SESSION_ROTATE_EVERY} download"
                        )
                else:
                    stats["failed"] += 1
                    log_msg(f"  Download fallito: {nome_file}", level="WARN")
                    time.sleep(random.uniform(2, 5))
            except SessionRotateNeeded:
                raise
            except BrowserClosedError:
                resume_pg = _LOG_CTX.get("pagina") or start_pagina
                if on_page_done:
                    on_page_done(
                        max(
                            page_floor,
                            int(resume_pg) if resume_pg else page_floor,
                        )
                    )
                raise

        nuovi = stats["downloaded"] - page_dl
        skip_cache = stats["skipped_cache"] - page_skip_cache
        skip_local = stats["skipped_local"] - page_skip_local
        skip_meta = stats["skipped_meta"] - page_skip_meta
        falliti = stats["failed"] - page_failed
        cur_page = pagina_corrente(page)
        if page_floor > 5 and cur_page > 0 and cur_page < page_floor - 1:
            log_msg(
                f"  STOP post-pagina: UI caduta a pag.{cur_page} "
                f"(floor {page_floor}) — checkpoint resta {page_floor}",
                level="WARN",
            )
            if on_page_done:
                on_page_done(page_floor)
            return False
        log_pagina_riepilogo(
            cur_page if cur_page > 0 else start_pagina,
            nuovi=nuovi,
            skip_server=skip_cache,
            skip_local=skip_local,
            skip_meta=skip_meta,
            falliti=falliti,
            totale_portale=_LOG_CTX.get("totale_portale"),
        )
        log_pagina_csv(
            year=year,
            trimestre_idx=trimestre_idx,
            materia=materia_codice,
            pagina=cur_page if cur_page > 0 else start_pagina,
            scaricati=nuovi,
            skip_server=skip_cache,
            skip_local=skip_local,
            skip_meta=skip_meta,
            semestre=_LOG_CTX.get("semestre", 0) or 0,
        )
        if cur_page < 1:
            if on_page_done:
                on_page_done(max(1, page_floor))
            raise BrowserClosedError("Tabella risultati persa dopo crash browser")
        next_pg = cur_page + 1
        page_floor = max(page_floor, next_pg)
        if on_page_done:
            on_page_done(next_pg)
        pending_page = next_pg
        if len(rows) >= 10 and nuovi == 0 and skip_cache == 0 and skip_local == 0 and skip_meta == 0:
            log_msg(f"  Pagina {cur_page} vuota (tabella non pronta?) — riprovo...")
            if attendi_pagina_pronta(page, prev=cur_page - 1 if cur_page > 1 else None):
                tentativi = 0
                continue

        pagine += 1
        if max_pagine > 0 and pagine >= max_pagine:
            return True

        next_btn = page.query_selector("a.page-link.info-cursor:text('>')")
        if next_btn and next_btn.is_enabled() and pagina_corrente(page) != ultima_pagina(page):
            prev_page = pagina_corrente(page)
            skip_only = nuovi == 0
            ok, page = avanza_pagina_successiva(
                page, prev_page, pending_page, browser, skip_only=skip_only
            )
            if not ok:
                log_msg(
                    f"  Interrotto su pagina {prev_page} (prossima era {pending_page}). "
                    "NON passo alla materia successiva — usa --resume.",
                    level="WARN",
                )
                return False
            tentativi = 0
            continue
        log_msg(f"  Fine risultati (ultima pagina {pagina_corrente(page)})")
        return True

    log_msg("  Timeout attesa tabella risultati")
    return False


def parse_pagine_arg(raw: str) -> List[int]:
    if not raw.strip():
        return []
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return sorted(set(out))


def ricerca_e_vai_a_pagina(
    page,
    browser,
    anno: int,
    da: str,
    a: str,
    materia_codice: str,
    target: int,
) -> Any:
    """Nuova ricerca + navigazione a pagina target. Ritorna page aggiornata."""
    global _akamai_403_seen
    _akamai_403_seen = False
    reset_search_status()
    if not esegui_ricerca(page, anno, da, a, materia_codice, browser=browser):
        return None
    page = pick_portal_page(browser, page)
    attach_akamai_watch(page)
    attach_search_status_watch(page)
    if target > 1 and not salta_a_pagina(page, target):
        if AUTO_HEAL:
            log_msg(
                f"  Salto a pagina {target} fallito — modalità bot, niente INVIO",
                level="WARN",
            )
            return None
        if _akamai_403_seen or _search_submit_status == 403:
            log_msg(
                f"  Akamai 403 su pagina {target}. In Edge: stessi filtri (D040 Q1), "
                "clicca Ricerca, poi premi INVIO qui."
            )
            if attendi_ricerca_manuale(page, timeout_sec=300, browser=browser):
                page = pick_portal_page(browser, page)
                attach_akamai_watch(page)
                attach_search_status_watch(page)
                if target > 1 and not salta_a_pagina(page, target):
                    return None
            else:
                return None
        else:
            return None
    return page


def esegui_download_materia(
    playwright,
    browser,
    page,
    profile_dir: Path,
    args,
    *,
    anno: int,
    da: str,
    a: str,
    trimestre_idx: int,
    materia_idx: int,
    materia_codice: str,
    output_dir: Path,
    session: set[str],
    stats: Dict[str, int],
    server_keys: set[str],
    resume_page: int,
) -> Tuple[Any, Any, bool]:
    """Scarica PDF per una materia; ritorna (browser, page, ok)."""
    pagine_list = parse_pagine_arg(args.pagine)

    checkpoint_floor = max(1, resume_page)

    def on_page_done(next_page: int) -> None:
        nonlocal checkpoint_floor
        try:
            next_page = int(next_page)
        except (TypeError, ValueError):
            next_page = checkpoint_floor
        # Mai regressione grossa (es. 344 → 2 per UI stale post-heal)
        if next_page < checkpoint_floor:
            log_msg(
                f"  Checkpoint: blocco regressione {checkpoint_floor}→{next_page} "
                f"(resto a {checkpoint_floor})",
                level="WARN",
            )
            next_page = checkpoint_floor
        else:
            checkpoint_floor = next_page
        save_checkpoint(
            year=anno,
            trimestre_idx=trimestre_idx,
            materia_idx=materia_idx,
            stats=stats,
            start_pagina=next_page,
            semestre=args.semestre,
        )

    cur_page = max(1, resume_page)
    profile_dir_local = Path(getattr(args, "profile_dir", profile_dir))
    # Con VPN/rotate IP: qualche retry. Senza: fail-fast (IpBurnedStop), non 40x20min
    max_heal_rounds = 8 if (AUTO_HEAL and HEALER is not None) else 0
    heal_round = 0
    browser_crash_count = 0

    for crash_attempt in range(4 + max_heal_rounds):
        try:
            if pagine_list:
                finished = True
                for p in pagine_list:
                    log_msg(f"  --pagine: elaboro pagina {p}")
                    page_ok = None
                    for nav_try in range(3):
                        page_ok = ricerca_e_vai_a_pagina(
                            page, browser, anno, da, a, materia_codice, p
                        )
                        if page_ok is not None:
                            page = page_ok
                            break
                        log_msg(f"  Retry ricerca+navigazione pagina {p} ({nav_try + 1}/3)...")
                        time.sleep(3)
                    if page_ok is None:
                        log_msg(f"  SALTATA pagina {p} (ricerca/navigazione fallita)")
                        finished = False
                        continue
                    ok_page = processa_risultati(
                        page,
                        output_dir,
                        session,
                        stats,
                        1,
                        server_keys,
                        start_pagina=1,
                        year=anno,
                        trimestre_idx=trimestre_idx,
                        materia_codice=materia_codice,
                        on_page_done=on_page_done,
                        skip_goto=True,
                        browser=browser,
                    )
                    if not ok_page:
                        finished = False
                if finished:
                    save_checkpoint(
                        year=anno,
                        trimestre_idx=trimestre_idx,
                        materia_idx=materia_idx,
                        stats=stats,
                        start_pagina=1,
                        semestre=args.semestre,
                    )
                    return browser, page, True
            else:
                finished = processa_risultati(
                    page,
                    output_dir,
                    session,
                    stats,
                    args.max_pagine,
                    server_keys,
                    start_pagina=cur_page,
                    year=anno,
                    trimestre_idx=trimestre_idx,
                    materia_codice=materia_codice,
                    on_page_done=on_page_done,
                    browser=browser,
                )
                if finished:
                    save_checkpoint(
                        year=anno,
                        trimestre_idx=trimestre_idx,
                        materia_idx=materia_idx,
                        stats=stats,
                        start_pagina=1,
                        semestre=args.semestre,
                    )
                    return browser, page, True

            # Interrotto (403/stallo): auto-heal invece di uscire
            ck = load_checkpoint()
            cur_page = max(
                1,
                int(_LOG_CTX.get("pagina") or 0),
                int(ck.get("start_pagina", cur_page)),
            )
            if AUTO_HEAL and HEALER is not None:
                while heal_round < max_heal_rounds:
                    heal_round += 1
                    log_msg(
                        f"  {materia_codice} interrotta a pag~{cur_page} — "
                        f"AUTO-HEAL {heal_round}/{max_heal_rounds}",
                        level="WARN",
                    )
                    try:
                        browser, page, profile_dir_local, ok_heal = HEALER.heal(
                            playwright,
                            browser,
                            page,
                            args,
                            anno=anno,
                            da=da,
                            a=a,
                            materia_codice=materia_codice,
                            target_page=cur_page,
                        )
                    except IpBurnedStop as stop_exc:
                        log_msg(str(stop_exc), level="WARN")
                        return browser, page, False
                    profile_dir = profile_dir_local
                    if ok_heal and browser is not None and page is not None:
                        landed = pagina_corrente(page)
                        if landed > 0:
                            cur_page = landed
                        break
                    # Senza IP nuovo heal già ha fatto fail-fast; non loopare
                    if not HEALER._can_rotate_ip():
                        log_msg(
                            f"  STOP senza VPN/rotate. Checkpoint pag {cur_page}. "
                            "Cambia IP e --resume.",
                            level="WARN",
                        )
                        return browser, page, False
                    log_msg("  AUTO-HEAL KO — ritento (VPN/rotate attivo)...", level="WARN")
                else:
                    log_msg(
                        f"  {materia_codice}: troppi cicli AUTO-HEAL. "
                        f"Checkpoint pag {cur_page} — --resume dopo cambio IP.",
                        level="WARN",
                    )
                    return browser, page, False
                continue

            log_msg(
                f"  {materia_codice} NON completata — checkpoint invariato per --resume "
                f"(non avanzo alla materia dopo).",
                level="WARN",
            )
            return browser, page, False
        except SessionRotateNeeded as exc:
            ck = load_checkpoint()
            cur_page = max(
                1,
                int(_LOG_CTX.get("pagina") or 0),
                int(ck.get("start_pagina", cur_page)),
            )
            log_msg(f"  {exc} — cambio identità (pag~{cur_page})", level="WARN")
            if AUTO_HEAL and HEALER is not None:
                # Forza profilo/UA nuovi (livello ≥3); no soft-mode (non è un 403)
                HEALER.level = max(HEALER.level, 3)
                browser, page, profile_dir_local, ok_heal = HEALER.heal(
                    playwright,
                    browser,
                    page,
                    args,
                    anno=anno,
                    da=da,
                    a=a,
                    materia_codice=materia_codice,
                    target_page=max(cur_page, checkpoint_floor),
                    enter_soft=False,
                )
                profile_dir = profile_dir_local
                if ok_heal:
                    landed = pagina_corrente(page)
                    if landed > 0:
                        cur_page = max(landed, checkpoint_floor)
                    # Se heal ha fallito il salto, ok_heal è False — qui landed ok
                    if landed > 0 and landed < checkpoint_floor - 1:
                        log_msg(
                            f"  Rotazione: UI ancora bassa ({landed}) vs floor "
                            f"{checkpoint_floor} — ritento heal",
                            level="WARN",
                        )
                        ok_heal = False
                if not ok_heal:
                    cur_page = max(cur_page, checkpoint_floor)
                continue
            log_msg("  Rotazione sessione richiesta ma AUTO-HEAL off — continuo", level="WARN")
            continue
        except BrowserClosedError:
            ck = load_checkpoint()
            ctx_pg = int(_LOG_CTX.get("pagina") or 0)
            cur_page = max(1, ctx_pg, int(ck.get("start_pagina", cur_page)))
            browser_crash_count += 1
            if browser_crash_count >= 3 and not (AUTO_HEAL and HEALER is not None):
                if args.semestre:
                    log_msg(
                        f"Troppi crash. Riprendi con: --semestre {args.semestre} "
                        f"--materia {materia_codice} --start-pagina {cur_page} --resume"
                    )
                else:
                    log_msg(
                        f"Troppi crash. Riprendi con: --trimestre {trimestre_idx + 1} "
                        f"--materia {materia_codice} --start-pagina {cur_page} --resume"
                    )
                return browser, page, False
            if AUTO_HEAL and HEALER is not None and heal_round < max_heal_rounds:
                heal_round += 1
                log_msg(
                    f"Crash browser — AUTO-HEAL {heal_round}/{max_heal_rounds} "
                    f"(riparto pag {cur_page})...",
                    level="WARN",
                )
                browser, page, profile_dir_local, ok_heal = HEALER.heal(
                    playwright,
                    browser,
                    page,
                    args,
                    anno=anno,
                    da=da,
                    a=a,
                    materia_codice=materia_codice,
                    target_page=cur_page,
                )
                profile_dir = profile_dir_local
                if ok_heal:
                    browser_crash_count = 0
                    landed = pagina_corrente(page)
                    if landed > 0:
                        cur_page = landed
                continue
            log_msg(
                f"Crash browser — riavvio Edge ({browser_crash_count}/3), "
                f"riparto da pagina {cur_page}..."
            )
            try:
                if hasattr(browser, "close"):
                    browser.close()
            except Exception:
                pass
            time.sleep(3)
            try:
                browser, _context, page = open_edge_session(
                    playwright, profile_dir, args.cdp_port, args.warmup_seconds
                )
            except (BrowserClosedError, PlaywrightError) as exc:
                raise_if_browser_closed(exc)
                log_msg(
                    f"Riconnessione Edge fallita ({browser_crash_count}/3): {exc}",
                    level="WARN",
                )
                time.sleep(5)
                continue
            try:
                ricerca_ok = esegui_ricerca(page, anno, da, a, materia_codice, browser=browser)
            except BrowserClosedError:
                log_msg("Browser chiuso durante ricerca post-crash — ritento...", level="WARN")
                time.sleep(3)
                continue
            if not ricerca_ok:
                if AUTO_HEAL and HEALER is not None and heal_round < max_heal_rounds:
                    heal_round += 1
                    log_msg(
                        f"Ricerca fallita dopo crash — AUTO-HEAL "
                        f"{heal_round}/{max_heal_rounds}",
                        level="WARN",
                    )
                    browser, page, profile_dir_local, ok_heal = HEALER.heal(
                        playwright,
                        browser,
                        page,
                        args,
                        anno=anno,
                        da=da,
                        a=a,
                        materia_codice=materia_codice,
                        target_page=cur_page,
                    )
                    profile_dir = profile_dir_local
                    if ok_heal:
                        browser_crash_count = 0
                    continue
                log_msg(
                    "Ricerca fallita dopo crash — ritento (modalita' bot, niente INVIO).",
                    level="WARN",
                )
                time.sleep(30)
                continue
            page = pick_portal_page(browser, page)
            attach_akamai_watch(page)
            attach_search_status_watch(page)
    return browser, page, False


def year_range(args) -> Tuple[int, int, int, int, List[Tuple[str, str]]]:
    """(start_year, end_year, start_period_idx, end_period_idx, periodi)."""
    oggi = datetime.now()
    start_year = args.start_year if args.start_year else args.year
    end_year = args.end_year if args.end_year else (args.year if args.year else oggi.year)
    periodi, _ = periodi_for_args(args)
    max_p = len(periodi) - 1

    if args.semestre:
        start_p = end_p = args.semestre - 1
    else:
        start_p = (args.start_trimestre - 1) if args.start_trimestre else 0
        if args.end_year or not args.year:
            end_p = max_p if end_year < oggi.year else min(max_p, (oggi.month - 1) // 3)
        else:
            end_p = (args.trimestre - 1) if args.trimestre else max_p

    return start_year, end_year, start_p, end_p, periodi


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download portale MEF con naming SGAI Sentenza_CODICE_NUMERO_ANNO.pdf",
    )
    parser.add_argument("--year", type=int, default=0, help="Solo questo anno (alternativa a --start-year)")
    parser.add_argument("--start-year", type=int, default=0, help="Anno iniziale (es. 2000 per tutto il DB)")
    parser.add_argument("--end-year", type=int, default=0, help="Anno finale (default: anno corrente)")
    parser.add_argument("--start-trimestre", type=int, choices=(1, 2, 3, 4), default=0)
    parser.add_argument("--trimestre", type=int, choices=(0, 1, 2, 3, 4), default=0,
                        help="1-4 = solo quel trimestre nell'anno, 0 = tutti")
    parser.add_argument("--semestre", type=int, choices=(0, 1, 2), default=0,
                        help="1-2 = solo quel semestre (gen-giu / lug-dic), 0 = usa --trimestre")
    parser.add_argument(
        "--worker",
        choices=("a", "b"),
        default=None,
        help="Con --semestre: spezza le materie a meta' (a=prime 21, b=ultime 20). "
        "Lock/checkpoint: mef_download_checkpoint_s1a.json ecc.",
    )
    parser.add_argument("--materia", default="", help="Codice materia, es. A010")
    parser.add_argument("--max-pagine", type=int, default=0, help="Limite pagine per singola ricerca")
    parser.add_argument(
        "--start-pagina",
        type=int,
        default=1,
        help="Dopo la ricerca, salta alle pagine precedenti e inizia da questa (es. 21)",
    )
    parser.add_argument(
        "--pagine",
        default="",
        help="Solo queste pagine (virgola), es. 39,41,66 — una ricerca per pagina",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--sync-cache", action="store_true",
                        help="Rigenera mia_cache/nomi_base.txt da listone_sentenze.csv")
    parser.add_argument("--no-skip-cache", action="store_true",
                        help="Ignora cache_nomi_base (scarica tutto tranne file gia in downloads_mef/)")
    parser.add_argument("--resume", action="store_true", help="Riprendi da mef_download_checkpoint.json")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Ricomincia da zero")
    parser.add_argument(
        "--browser",
        choices=("edge", "opera"),
        default="edge",
        help="Browser reale via CDP: edge (default) oppure opera",
    )
    parser.add_argument(
        "--profile-dir",
        default=str(DEFAULT_PROFILE_DIR),
        help="Profilo browser persistente (cookie Akamai). "
        "Con --browser opera usa una cartella nuova, es. .opera_profile_mef",
    )
    parser.add_argument(
        "--warmup-seconds",
        type=int,
        default=8,
        help="Secondi di attesa al primo caricamento portale (Akamai sensor)",
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=PAGE_DELAY_SEC,
        help="Secondi di pausa prima di ogni click '>' (default 12; alza se 403 frequenti)",
    )
    parser.add_argument(
        "--solo",
        action="store_true",
        help="Un solo worker su questo PC: niente mutex/cooldown condiviso con altri. "
        "USA SOLO se hai fermato D040/altri download MEF (stesso IP + 2 worker = 403).",
    )
    parser.add_argument(
        "--no-auto-heal",
        action="store_true",
        help="Disabilita auto-heal (cooldown/UA/profilo/VPN automatici su 403).",
    )
    parser.add_argument(
        "--vpn",
        default="auto",
        choices=("proton", "shell", "auto", "off"),
        help="Servizio VPN: auto/proton usa rotate_proton_vpn.ps1 su IP bruciato; "
        "off=disattiva. Env: MEF_VPN=proton",
    )
    parser.add_argument(
        "--vpn-rotate-cmd",
        default="",
        help="Comando shell custom per ruotare VPN "
        '(es. powershell -File .\\rotate_vpn.ps1). Oppure env MEF_VPN_ROTATE_CMD. '
        "Con --vpn proton usa rotate_proton_vpn.ps1 automaticamente.",
    )
    parser.add_argument(
        "--heal-profiles",
        type=int,
        default=3,
        help="Quanti profili Edge alternativi creare (base_heal0..N) per auto-heal (default 3)",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=CDP_PORT_DEFAULT,
        help="Porta remote debugging Edge (default 9222)",
    )
    parser.add_argument(
        "--proxy",
        default="",
        help="Proxy Edge: http://host:port o http://user:pass@host:port "
        "(oppure env MEF_PROXY). Consigliato residential/mobile.",
    )
    parser.add_argument(
        "--download-delay-min",
        type=float,
        default=DOWNLOAD_DELAY_MIN,
        help="Pausa minima secondi tra un PDF e il successivo (default 12)",
    )
    parser.add_argument(
        "--download-delay-max",
        type=float,
        default=DOWNLOAD_DELAY_MAX,
        help="Pausa massima secondi tra PDF (default 20)",
    )
    parser.add_argument(
        "--session-rotate-every",
        type=int,
        default=SESSION_ROTATE_EVERY,
        help="Dopo N download nuovi ruota profilo/UA via AUTO-HEAL (0=disattiva, default 25)",
    )
    parser.add_argument(
        "--global-block-cooldown",
        type=int,
        default=GLOBAL_BLOCK_COOLDOWN_SEC,
        help="Secondi cooldown globale dopo 403 a raffica (default 900 = 15 min)",
    )
    parser.add_argument(
        "--init-profilo",
        action="store_true",
        help="Apre Edge REALE; fai UNA ricerca manuale, poi INVIO nel terminale",
    )
    return parser.parse_args()


def run_init_profilo(profile_dir: Path, warmup: int, cdp_port: int) -> int:
    label = browser_label()
    log_msg("=" * 60)
    log_msg(f"INIT PROFILO MEF — {label} con CDP (stessa finestra che usera' lo script)")
    log_msg(f"IMPORTANTE: chiudi TUTTE le altre finestre {label} di questo profilo prima")
    log_msg(f"Profilo: {profile_dir}")
    log_msg("1) Aspetta 30-60s sulla home del portale (non cercare subito)")
    log_msg("2) Poi: anno 2025, date 01-01 / 06-30, materia D040 (Accertamento), Ricerca")
    log_msg("3) Se TABELLA ok → INVIO qui. Se 403/Access Denied → serve VPN/altra rete")
    log_msg("=" * 60)
    try:
        with sync_playwright() as playwright:
            kill_edge_for_profile(profile_dir)
            kill_listener_on_port(cdp_port)
            time.sleep(2)
            start_edge_cdp(profile_dir, cdp_port, wait_sec=45, proxy=EDGE_PROXY)
            browser, _context, page = connect_edge_cdp(playwright, cdp_port)
            apply_stealth(page)
            warmup_portale(page, max(warmup, 15), navigate=False)
            log_msg(">>> Aspetta ~30s, poi ricerca MANUALE in QUESTA Edge <<<")
            while True:
                try:
                    input(
                        "Quando la TABELLA e' visibile QUI (non in un'altra Edge), INVIO "
                        "(Ctrl+C esci)... "
                    )
                except KeyboardInterrupt:
                    log_msg("Init interrotto (Ctrl+C).")
                    return 130
                page = pick_portal_page(browser, page)
                apply_stealth(page)
                links = count_download_links(page)
                rows = count_data_rows(page)
                if links > 0 or rows >= 3:
                    log_msg(f"Init OK: {links} link, {rows} righe. Profilo pronto.")
                    break
                body = ""
                try:
                    body = page.inner_text("body")[:500]
                except Exception:
                    pass
                if "Access Denied" in body or "403" in body:
                    log_msg(
                        "Ancora bloccato (403/Access Denied) in questa Edge. "
                        "Cambia VPN/rete oppure riprova tra 20 min. Init NON completato.",
                        level="WARN",
                    )
                    return 2
                log_msg(
                    f"Tabella non trovata (link={links}, righe={rows}). "
                    "Rifai Ricerca in QUESTA finestra e INVIO di nuovo.",
                    level="WARN",
                )
        log_msg(
            "Profilo OK. Poi ad esempio:\n"
            f"  python download_mef_2025.py --year 2025 --semestre 1 --materia D040 "
            f"--start-pagina 320 --profile-dir {profile_dir.name} --cdp-port {cdp_port} "
            f"--resume --solo --no-auto-heal"
        )
        return 0
    except Exception as exc:
        log_msg(f"ERRORE init: {exc}")
        return 1


def main() -> int:
    global PAGE_DELAY_SEC, SOLO_MODE, AUTO_HEAL, HEALER
    global EDGE_PROXY, DOWNLOAD_DELAY_MIN, DOWNLOAD_DELAY_MAX
    global SESSION_ROTATE_EVERY, GLOBAL_BLOCK_COOLDOWN_SEC, VPN_SERVICE
    global BROWSER_ENGINE
    args = parse_args()
    BROWSER_ENGINE = (getattr(args, "browser", None) or os.environ.get("MEF_BROWSER") or "edge").strip().lower()
    if BROWSER_ENGINE not in ("edge", "opera"):
        BROWSER_ENGINE = "edge"
    PAGE_DELAY_SEC = max(3.0, float(args.page_delay))
    SOLO_MODE = bool(getattr(args, "solo", False))
    AUTO_HEAL = not bool(getattr(args, "no_auto_heal", False))
    EDGE_PROXY = (getattr(args, "proxy", "") or os.environ.get("MEF_PROXY") or "").strip()
    dmin = float(getattr(args, "download_delay_min", DOWNLOAD_DELAY_MIN))
    dmax = float(getattr(args, "download_delay_max", DOWNLOAD_DELAY_MAX))
    DOWNLOAD_DELAY_MIN = max(3.0, min(dmin, dmax))
    DOWNLOAD_DELAY_MAX = max(DOWNLOAD_DELAY_MIN, dmax)
    SESSION_ROTATE_EVERY = max(0, int(getattr(args, "session_rotate_every", SESSION_ROTATE_EVERY)))
    GLOBAL_BLOCK_COOLDOWN_SEC = max(
        60, int(getattr(args, "global_block_cooldown", GLOBAL_BLOCK_COOLDOWN_SEC))
    )
    profile_dir = Path(args.profile_dir).resolve()
    configure_run_paths(args.trimestre, args.semestre, getattr(args, "worker", "") or "")

    # Servizio VPN (Proton / shell)
    vpn_choice = (getattr(args, "vpn", None) or os.environ.get("MEF_VPN") or "auto").strip().lower()
    if vpn_choice == "off":
        VPN_SERVICE = None
    elif make_vpn_service is not None:
        VPN_SERVICE = make_vpn_service(
            provider=vpn_choice,
            command=getattr(args, "vpn_rotate_cmd", "") or "",
            log=lambda m: log_msg(m, skip_ctx=True),
        )
    else:
        VPN_SERVICE = None

    try:
        exe = find_browser_executable()
        log_msg(f"Browser: {browser_label()} ({exe})", skip_ctx=True)
    except FileNotFoundError as exc:
        log_msg(str(exc), skip_ctx=True)
        return 1
    if EDGE_PROXY:
        shown = EDGE_PROXY.split("@")[-1] if "@" in EDGE_PROXY else EDGE_PROXY
        log_msg(f"Proxy browser: {shown}", skip_ctx=True)
    else:
        log_msg(
            "Nessun --proxy / MEF_PROXY: IP = rete/VPN di sistema.",
            skip_ctx=True,
        )
    if VPN_SERVICE is not None:
        ip_now = get_public_ip() if get_public_ip else ""
        log_msg(
            f"VPN service: {VPN_SERVICE.name} | IP attuale: {ip_now or '?'} "
            f"(su IP bruciato → cambio server automatico)",
            skip_ctx=True,
        )
    else:
        log_msg(
            "VPN service: off — passa --vpn proton per rotazione automatica Proton",
            skip_ctx=True,
        )
    log_msg(
        f"Ritmo anti-Akamai: DL variabile {DOWNLOAD_DELAY_MIN:.0f}-{DOWNLOAD_DELAY_MAX:.0f}s, "
        f"rotazione ogni {SESSION_ROTATE_EVERY or 'off'} PDF, "
        f"block-cooldown {GLOBAL_BLOCK_COOLDOWN_SEC}s",
        skip_ctx=True,
    )

    if SOLO_MODE:
        log_msg(
            "MODO --solo: nessun lock ricerca globale. "
            "Assicurati che NON girino altri download_mef (S1/D040 ecc.).",
            skip_ctx=True,
        )

    if AUTO_HEAL:
        HEALER = AutoHealer(
            profile_dir,
            args.cdp_port,
            vpn_cmd=getattr(args, "vpn_rotate_cmd", "") or "",
            vpn_service=VPN_SERVICE,
            alt_profiles=max(0, int(getattr(args, "heal_profiles", 3))),
            warmup=args.warmup_seconds,
        )
        vpn_label = "off"
        if VPN_SERVICE is not None:
            vpn_label = VPN_SERVICE.name
        elif HEALER.vpn_cmd:
            vpn_label = "cmd"
        log_msg(
            f"AUTO-HEAL attivo: profili alternativi={len(HEALER.profiles)}, "
            f"VPN={vpn_label}",
            skip_ctx=True,
        )
    else:
        HEALER = None
        log_msg("AUTO-HEAL disattivato (--no-auto-heal)", skip_ctx=True)

    if args.init_profilo:
        return run_init_profilo(profile_dir, args.warmup_seconds, args.cdp_port)

    if not profile_dir.exists() and args.profile_dir != str(DEFAULT_PROFILE_DIR):
        log_msg(
            f"ATTENZIONE: profilo {profile_dir} non trovato. "
            f"Esegui prima: python download_mef_2025.py --init-profilo "
            f"--profile-dir {args.profile_dir} --cdp-port {args.cdp_port}"
        )

    acquire_download_lock()
    try:
        return _run_download(args, profile_dir)
    except KeyboardInterrupt:
        log_msg("Interrotto (Ctrl+C). Riprendi con --resume.", skip_ctx=True)
        return 130
    finally:
        release_download_lock()


def _run_download(args, profile_dir: Path) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.reset_checkpoint and _RUN_CHECKPOINT_PATH.exists():
        _RUN_CHECKPOINT_PATH.unlink()

    cache = SentenzeCache(cache_dir=args.cache_dir)
    if args.sync_cache and LISTONE_CSV.exists():
        log_msg(f"Sync cache da {LISTONE_CSV.name}...")
        cache.build_from_csv(LISTONE_CSV)

    if args.year:
        filter_year = args.year
    elif args.start_year and (not args.end_year or args.start_year == args.end_year):
        filter_year = args.start_year
    else:
        filter_year = 0

    server_keys, keys_source = load_server_keys(filter_year, Path(args.cache_dir))
    if args.no_skip_cache:
        server_keys = set()
        log_msg("ATTENZIONE: --no-skip-cache attivo, ignoro cache_nomi_base")
    else:
        log_msg(f"Cache server: {len(server_keys):,} nomi in {keys_source}")
        if filter_year:
            log_msg(f"  -> salto sentenze {filter_year} gia sul server; scarico solo le MANCANTI")

    session = load_session()
    stats = {
        "downloaded": 0,
        "failed": 0,
        "skipped_cache": 0,
        "skipped_local": 0,
        "skipped_meta": 0,
    }

    start_year, end_year, start_period, end_period, periodi = year_range(args)
    materie = materie_for_args(args)

    ck = load_checkpoint() if args.resume else {}
    # Migrazione: primo --worker a con checkpoint legacy s1/s2
    w = (getattr(args, "worker", "") or "").strip().lower()
    if args.resume and args.semestre in (1, 2) and w == "a" and not ck:
        legacy = Path(__file__).resolve().parent / f"mef_download_checkpoint_s{args.semestre}.json"
        if legacy.exists():
            try:
                ck = json.loads(legacy.read_text(encoding="utf-8"))
                log_msg(f"Checkpoint migrato da {legacy.name} -> {_RUN_CHECKPOINT_PATH.name}")
            except Exception:
                ck = {}

    anno = ck.get("year", start_year)
    if args.semestre:
        # Il semestre viene SEMPRE dal flag CLI, mai dal checkpoint (evita S1 che scarica lug-dic)
        period_idx = args.semestre - 1
        ck_sem = ck.get("semestre") if ck else None
        if args.resume and ck and ck_sem not in (None, 0, args.semestre):
            log_msg(
                f"ATTENZIONE: checkpoint semestre {ck_sem} != --semestre {args.semestre}; "
                "uso solo materia/pagina dal checkpoint."
            )
    elif args.resume:
        period_idx = ck.get("period_idx", ck.get("trimestre_idx", start_period))
    else:
        period_idx = start_period
    if args.materia:
        materia_start = 0
    elif args.resume and ck:
        materia_start = materia_start_in_slice(materie, int(ck.get("materia_idx", 0)))
    else:
        materia_start = 0
    if ck.get("stats"):
        stats.update(ck["stats"])

    da0, a0 = periodi[start_period]
    period_desc = f"{periodo_label(da0, a0)} ({da0}..{a0})"
    if w in ("a", "b"):
        period_desc += f" | worker {w} materie {materie[0]}..{materie[-1]} ({len(materie)})"
    set_log_ctx(worker=worker_tag(args), anno=start_year, periodo=periodo_short(da0, a0), semestre=args.semestre)
    log_run_header(
        args,
        start_year=start_year,
        end_year=end_year,
        output_dir=output_dir,
        resume=args.resume,
        period_desc=period_desc,
        n_materie=len(materie),
        profile_dir=profile_dir,
        cdp_port=args.cdp_port,
    )

    with sync_playwright() as playwright:
        browser, _context, page = open_edge_session(
            playwright, profile_dir, args.cdp_port, args.warmup_seconds
        )

        try:
            while anno <= end_year:
                last_period = end_period if anno == end_year else len(periodi) - 1
                while period_idx <= last_period:
                    da, a = periodi[period_idx]
                    trimestre_idx = period_idx
                    set_log_ctx(anno=anno, periodo=periodo_short(da, a), pagina=0)
                    materia_slice = materie[materia_start:]
                    materia_start = 0

                    for materia_idx_offset, materia_codice in enumerate(materia_slice):
                        materia_idx = (
                            MATERIA_KEYS.index(materia_codice)
                            if materia_codice in MATERIA_KEYS
                            else materia_idx_offset
                        )
                        stats_materia_start = dict(stats)
                        # Progresso rispetto alla slice del worker (non all'indice globale)
                        local_idx = materie.index(materia_codice) if materia_codice in materie else materia_idx_offset
                        log_materia_start(materia_codice, local_idx, len(materie))
                        ck_resume = load_checkpoint() if args.resume else {}
                        if parse_pagine_arg(args.pagine):
                            resume_pg = 1
                        elif args.start_pagina > 1:
                            resume_pg = args.start_pagina
                        elif args.resume and ck_resume.get("materia_idx") == materia_idx:
                            ck_period = ck_resume.get(
                                "trimestre_idx", ck_resume.get("period_idx", period_idx)
                            )
                            if ck_resume.get("year", anno) == anno and ck_period == period_idx:
                                resume_pg = max(1, int(ck_resume.get("start_pagina", 1)))
                            else:
                                resume_pg = 1
                        else:
                            resume_pg = max(1, args.start_pagina)

                        ok = False
                        if parse_pagine_arg(args.pagine):
                            ok = True
                        else:
                            # Ricerca + heal; senza cambio IP → fail-fast (niente loop 20x)
                            max_search_heals = (
                                3
                                if (AUTO_HEAL and HEALER is not None and HEALER._can_rotate_ip())
                                else (1 if (AUTO_HEAL and HEALER is not None) else 0)
                            )
                            search_heal = 0
                            while True:
                                for reconnect in range(2):
                                    try:
                                        ok = esegui_ricerca(
                                            page, anno, da, a, materia_codice, browser=browser
                                        )
                                        page = pick_portal_page(browser, page)
                                        attach_akamai_watch(page)
                                        attach_search_status_watch(page)
                                        break
                                    except BrowserClosedError:
                                        if reconnect:
                                            log_msg(
                                                "Browser disconnesso — ritento via AUTO-HEAL...",
                                                level="WARN",
                                            )
                                            ok = False
                                            break
                                        log_msg("Browser disconnesso — riconnessione CDP...")
                                        browser, _context, page = open_edge_session(
                                            playwright,
                                            profile_dir,
                                            args.cdp_port,
                                            args.warmup_seconds,
                                        )
                                if ok:
                                    break
                                if (
                                    AUTO_HEAL
                                    and HEALER is not None
                                    and search_heal < max_search_heals
                                ):
                                    search_heal += 1
                                    log_msg(
                                        f"  Ricerca KO — AUTO-HEAL pre-download "
                                        f"{search_heal}/{max_search_heals}",
                                        level="WARN",
                                    )
                                    try:
                                        browser, page, profile_dir, ok_heal = HEALER.heal(
                                            playwright,
                                            browser,
                                            page,
                                            args,
                                            anno=anno,
                                            da=da,
                                            a=a,
                                            materia_codice=materia_codice,
                                            target_page=resume_pg,
                                        )
                                    except IpBurnedStop as stop_exc:
                                        log_msg(str(stop_exc), level="WARN")
                                        ok = False
                                        break
                                    if ok_heal:
                                        ok = True
                                        landed = pagina_corrente(page)
                                        if landed > 0:
                                            resume_pg = landed
                                        break
                                    if not HEALER._can_rotate_ip():
                                        break
                                    continue
                                log_msg(
                                    f"  Ricerca {materia_codice} fallita. "
                                    f"STOP — cambia IP e riparti con --resume "
                                    f"--start-pagina {resume_pg}",
                                    level="WARN",
                                )
                                break

                        save_checkpoint(
                            year=anno,
                            trimestre_idx=period_idx,
                            materia_idx=materia_idx,
                            stats=stats,
                            start_pagina=resume_pg,
                            semestre=args.semestre or 0,
                        )
                        if ok:
                            if resume_pg > 1:
                                log_msg(f"  Resume da pagina {resume_pg} (checkpoint)")
                            browser, page, done = esegui_download_materia(
                                playwright,
                                browser,
                                page,
                                profile_dir,
                                args,
                                anno=anno,
                                da=da,
                                a=a,
                                trimestre_idx=trimestre_idx,
                                materia_idx=materia_idx,
                                materia_codice=materia_codice,
                                output_dir=output_dir,
                                session=session,
                                stats=stats,
                                server_keys=server_keys,
                                resume_page=resume_pg,
                            )
                            if not done:
                                if AUTO_HEAL and HEALER is not None:
                                    log_msg(
                                        "  Download interrotto — ritento stessa materia "
                                        "(AUTO-HEAL, niente uscita).",
                                        level="WARN",
                                    )
                                    time.sleep(60)
                                    # ripeti questa materia: indietreggia l'indice del for
                                    # rieseguendo il blocco: più semplice = non incrementare
                                    # usando un while interno sarebbe meglio; qui ritentiamo
                                    # con continue su un flag
                                    # Workaround: richiama heal+download in loop sotto
                                    retry_same = 0
                                    while retry_same < 30 and not done:
                                        retry_same += 1
                                        ck2 = load_checkpoint()
                                        resume_pg = max(
                                            1, int(ck2.get("start_pagina", resume_pg))
                                        )
                                        browser, page, profile_dir, ok_heal = HEALER.heal(
                                            playwright,
                                            browser,
                                            page,
                                            args,
                                            anno=anno,
                                            da=da,
                                            a=a,
                                            materia_codice=materia_codice,
                                            target_page=resume_pg,
                                        )
                                        if not ok_heal:
                                            time.sleep(120)
                                            continue
                                        browser, page, done = esegui_download_materia(
                                            playwright,
                                            browser,
                                            page,
                                            profile_dir,
                                            args,
                                            anno=anno,
                                            da=da,
                                            a=a,
                                            trimestre_idx=trimestre_idx,
                                            materia_idx=materia_idx,
                                            materia_codice=materia_codice,
                                            output_dir=output_dir,
                                            session=session,
                                            stats=stats,
                                            server_keys=server_keys,
                                            resume_page=resume_pg,
                                        )
                                    if not done:
                                        log_msg(
                                            "  Troppi retry — aspetto 10 min e proseguo "
                                            "alla materia dopo (checkpoint salvato).",
                                            level="WARN",
                                        )
                                        time.sleep(600)
                                        continue
                                else:
                                    return 1
                            delta = stats["downloaded"] - stats_materia_start["downloaded"]
                            log_msg(
                                f"--- FINE {materia_codice}: +{delta} nuovi in questa materia "
                                f"(tot sessione: scaricati={stats['downloaded']} "
                                f"skip_sgai={stats['skipped_cache']} skip_locali={stats['skipped_local']}) ---"
                            )

                    period_idx += 1

                period_idx = 0
                anno += 1

            if _RUN_CHECKPOINT_PATH.exists():
                _RUN_CHECKPOINT_PATH.unlink()
        finally:
            log_msg("Sessione Playwright chiusa.")
            try:
                if hasattr(browser, "close"):
                    browser.close()
            except Exception:
                pass

    log_msg(
        "FINE SESSIONE: scaricati={downloaded} skip_sgai={skipped_cache} skip_locali={skipped_local} "
        "falliti={failed} meta={skipped_meta}".format(**stats),
        skip_ctx=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
