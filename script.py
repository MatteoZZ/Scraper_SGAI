import os
import pathlib
import re
import sys
import time
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Dict, Any, Optional, Tuple, List
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import socket
import pickle
from datetime import datetime
import random

import requests

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Mobile Safari/537.36"
]
user_agent_idx = 0
MAX_CAPTCHAS_BEFORE_ROTATION = 5
captcha_in_row = 0

SOLR_ENDPOINT = (
    "https://www.italgiure.giustizia.it/sncass/isapi/hc.dll/"
    "sn.solr/sn-collection/select?app.query="
)
PDF_BASE_URL = (
    "https://www.italgiure.giustizia.it/xway/application/nif/clean/"
    "hc.dll?verbo=attach&db=snciv&id="
)

captcha_queue = []
captcha_resolved = threading.Event()
current_captcha_url = None
server_port = 8080

CHECKPOINT_FILE = "download_checkpoint.pkl"
PROCESSED_FILES_LOG = "processed_files.json"

# Formato nome file scaricato (campi: datdep, cgtn, regione, numero, anno)
# Esempio: 25-05-2026_CGT2_Lombardia_1205-2026.pdf
DOWNLOAD_FILENAME_TEMPLATE = "{datdep}_{cgtn}_{regione}_{numero}-{anno}.pdf"

# Modalita' naming:
# - "completo": richiede CGT N° Regione (tabella / OCR con pattern CGT 2° Lombardia)
# - "italgiure": usa anche il tribunale impugnato dall'OCR (CTR/CGT merito) se manca la tabella
# - "semplice": solo datdep_numero-anno (senza CGT/regione)
# - "sgai": Sentenza_CODICE_NUMERO_ANNO.pdf (pacchetto SGAI)
NAMING_MODE = "italgiure"

# Anni per cui controllare la cache SGAI e salvare come Sentenza_CODICE_NUMERO_ANNO.pdf
SGAI_CACHE_YEARS = {"2025"}
SGAI_PACKAGE_DIR = pathlib.Path(
    r"C:\Users\meko srl\Downloads\SGAI_Pacchetto_Collega_Sentenze_20260713"
)
SGAI_DEFAULT_CACHE_DIR = SGAI_PACKAGE_DIR / "mia_cache"
SGAI_API_BASE = "https://sgailegal.com"
SGAI_CHECK_PATH = "/v1/admin/sentenze-check"
SGAI_WAKE_URL = "https://91k2hfw1n3.execute-api.eu-north-1.amazonaws.com/wake-up"
CORTE_OVERRIDE_CSV = pathlib.Path(__file__).resolve().parent / "corte_override.csv"
OSCURATI_LOG_CSV = pathlib.Path(__file__).resolve().parent / "oscurati_pending.csv"
NAMING_ISSUES_CSV = pathlib.Path(__file__).resolve().parent / "naming_issues.csv"
PREFLIGHT_AHEAD_DEFAULT = 3
DEFAULT_WORKERS = 1
_captcha_lock = threading.Lock()
_print_lock = threading.Lock()

_sgai_cache = None
_corte_overrides: Optional[Dict[Tuple[str, str], str]] = None

class CaptchaHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        global current_captcha_url
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            html = f"""
            <!DOCTYPE html>
            <html><head><title>Risolvi Captcha - Cassazione PDF Downloader</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }}
                .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .status {{ padding: 15px; margin: 20px 0; border-radius: 5px; font-weight: bold; }}
                .waiting {{ background-color: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }}
                .success {{ background-color: #d4edda; border: 1px solid #c3e6cb; color: #155724; }}
                .button {{ background-color: #007bff; color: white; padding: 15px 30px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; text-decoration: none; display: inline-block; margin: 10px 5px; }}
                .button:hover {{ background-color: #0056b3; }}
                .danger {{ background-color: #dc3545; }}
                .danger:hover {{ background-color: #c82333; }}
                .url-box {{ background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 15px; border-radius: 5px; word-break: break-all; font-family: monospace; margin: 15px 0; }}
                iframe {{ width: 100%; height: 600px; border: 1px solid #ccc; border-radius: 5px; }}
            </style>
            <script>
                function markAsResolved() {{
                    fetch('/resolve', {{method: 'POST'}})
                        .then(response => response.json())
                        .then(data => {{
                            if (data.status === 'success') {{
                                document.getElementById('status').innerHTML = 
                                    '<div class="status success">✅ Captcha risolto! Il download continuerà automaticamente.</div>';
                                document.getElementById('controls').style.display = 'none';
                                setTimeout(() => {{ window.location.reload(); }}, 3000);
                            }}
                        }});
                }}
                function skipCurrent() {{
                    fetch('/skip', {{method: 'POST'}})
                        .then(response => response.json())
                        .then(data => {{
                            if (data.status === 'success') {{
                                document.getElementById('status').innerHTML = 
                                    '<div class="status success">⏭️ File saltato. Continuando con il prossimo...</div>';
                                document.getElementById('controls').style.display = 'none';
                                setTimeout(() => {{ window.location.reload(); }}, 2000);
                            }}
                        }});
                }}
                setTimeout(() => {{ if (!{json.dumps(bool(current_captcha_url))}) window.location.reload(); }}, 5000);
            </script>
            </head><body>
            <div class="container">
                <h1>🏛️ Cassazione PDF Downloader</h1>
                <h2>Risoluzione Captcha</h2>
                <div id="status">
            """
            if current_captcha_url:
                html += f"""
                        <div class="status waiting">
                            ⏳ Captcha rilevato! Risolvi il captcha nell'iframe qui sotto, poi clicca "Captcha Risolto".
                        </div>
                    </div>
                    <div class="url-box">
                        <strong>URL del captcha:</strong><br>
                        <a href="{current_captcha_url}" target="_blank">{current_captcha_url}</a>
                    </div>
                    <iframe src="{current_captcha_url}"></iframe>
                    <div id="controls">
                        <button class="button" onclick="markAsResolved()">✅ Captcha Risolto - Continua Download</button>
                        <button class="button danger" onclick="skipCurrent()">⏭️ Salta Questo File</button>
                    </div>
                """
            else:
                html += f"""
                        <div class="status success">
                            ✅ Nessun captcha in attesa. Il download è in corso...
                        </div>
                    </div>
                    <p>Questa pagina si aggiornerà automaticamente quando sarà necessario risolvere un captcha.</p>
                """
            html += """
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))

    def do_POST(self):
        global captcha_resolved, current_captcha_url
        parsed_path = urlparse(self.path)
        if parsed_path.path in ['/resolve', '/skip']:
            captcha_resolved.set()
            current_captcha_url = None
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'success'}).encode('utf-8'))

class DownloadCheckpoint:
    def __init__(self, checkpoint_file: str = CHECKPOINT_FILE, log_file: str = PROCESSED_FILES_LOG):
        self.checkpoint_file = checkpoint_file
        self.log_file = log_file
        self.processed_files = set()
        self.failed_files = set()
        self.current_page = 0
        self.current_position = 0
        self.total_downloaded = 0
        self.total_failed = 0
        self.session_start = datetime.now()
        self._lock = threading.Lock()
        self.load_checkpoint()
        self.load_processed_files()

    def load_checkpoint(self):
        try:
            if os.path.exists(self.checkpoint_file):
                with open(self.checkpoint_file, 'rb') as f:
                    data = pickle.load(f)
                    self.current_page = data.get('current_page', 0)
                    self.current_position = data.get('current_position', 0)
                    self.total_downloaded = data.get('total_downloaded', 0)
                    self.total_failed = data.get('total_failed', 0)
                    print(f"📂 Checkpoint caricato: pagina {self.current_page}, posizione {self.current_position}")
                    print(f"📊 Statistiche precedenti: {self.total_downloaded} scaricati, {self.total_failed} falliti")
        except Exception as e:
            print(f"⚠️ Errore caricamento checkpoint: {e}")

    def save_checkpoint(self):
        try:
            data = {
                'current_page': self.current_page,
                'current_position': self.current_position,
                'total_downloaded': self.total_downloaded,
                'total_failed': self.total_failed,
                'timestamp': datetime.now().isoformat()
            }
            with open(self.checkpoint_file, 'wb') as f:
                pickle.dump(data, f)
        except Exception as e:
            print(f"⚠️ Errore salvataggio checkpoint: {e}")

    def load_processed_files(self):
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.processed_files = set(data.get('processed', []))
                    self.failed_files = set(data.get('failed', []))
                    print(f"📋 File già processati: {len(self.processed_files)} scaricati, {len(self.failed_files)} falliti")
        except Exception as e:
            print(f"⚠️ Errore caricamento file processati: {e}")

    def save_processed_files(self):
        try:
            data = {
                'processed': list(self.processed_files),
                'failed': list(self.failed_files),
                'last_update': datetime.now().isoformat(),
                'session_stats': {
                    'session_start': self.session_start.isoformat(),
                    'total_downloaded': self.total_downloaded,
                    'total_failed': self.total_failed
                }
            }
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Errore salvataggio file processati: {e}")

    def is_processed(self, file_id: str) -> bool:
        return file_id in self.processed_files
    def is_failed(self, file_id: str) -> bool:
        return file_id in self.failed_files
    def mark_processed(self, file_id: str, success: bool = True):
        with self._lock:
            if success:
                self.processed_files.add(file_id)
                self.failed_files.discard(file_id)
                self.total_downloaded += 1
            else:
                self.failed_files.add(file_id)
                self.total_failed += 1
            self.save_processed_files()
    def update_position(self, page: int, position: int):
        with self._lock:
            self.current_page = page
            self.current_position = position
            self.save_checkpoint()
    def should_skip_to_position(self, page: int, position: int) -> bool:
        return page < self.current_page or (page == self.current_page and position < self.current_position)

    def mark_page_complete(self, page: int) -> None:
        """Avanza checkpoint quando una pagina e' completata (anche fuori ordine)."""
        with self._lock:
            if not hasattr(self, "_completed_pages"):
                self._completed_pages: set[int] = set()
            self._completed_pages.add(page)
            while self.current_page in self._completed_pages:
                self._completed_pages.discard(self.current_page)
                self.current_page += 1
                self.current_position = 0
            self.save_checkpoint()
    def reset_checkpoint(self, *, keep_processed: bool = False) -> None:
        try:
            if os.path.exists(self.checkpoint_file):
                os.remove(self.checkpoint_file)
            if not keep_processed and os.path.exists(self.log_file):
                os.remove(self.log_file)
                self.processed_files = set()
                self.failed_files = set()
            print(
                "🔄 Checkpoint resettato"
                + (" (processed_files.json conservato)" if keep_processed else "")
            )
        except Exception as e:
            print(f"⚠️ Errore reset checkpoint: {e}")

    def unmark_file(self, file_id: str) -> None:
        with self._lock:
            self.processed_files.discard(file_id)
            self.failed_files.discard(file_id)
            self.save_processed_files()

    def set_position(self, page: int, position: int) -> None:
        with self._lock:
            self.current_page = page
            self.current_position = position
            self.save_checkpoint()

def first_value(field: Any) -> Optional[str]:
    if field is None:
        return None
    if isinstance(field, list):
        field = field[0] if field else None
    if field is None:
        return None
    value = str(field).strip()
    return value or None


REGIONE_ALIASES = {
    "emilia romagna": "Emilia-Romagna",
    "emilia-romagna": "Emilia-Romagna",
    "trentino alto adige": "Trentino-Alto Adige",
    "friuli venezia giulia": "Friuli-Venezia Giulia",
    "valle d aosta": "Valle d'Aosta",
    "valle daosta": "Valle d'Aosta",
}

CITTA_TO_REGIONE = {
    "roma": "Lazio",
    "milano": "Lombardia",
    "napoli": "Campania",
    "torino": "Piemonte",
    "venezia": "Veneto",
    "firenze": "Toscana",
    "palermo": "Sicilia",
    "catania": "Sicilia",
    "bologna": "Emilia-Romagna",
}

OCR_REGIONE_FIXES = {
    "v e neto": "Veneto",
    "v eneto": "Veneto",
    "ve neto": "Veneto",
    "l om bardia": "Lombardia",
    "lombard ia": "Lombardia",
    "l a sicilian": "Sicilia",
    "l a sicilia": "Sicilia",
    "si cilian": "Sicilia",
    "si- cilian": "Sicilia",
    "sicilian": "Sicilia",
    "emilia romagna": "Emilia-Romagna",
    "emilia romag": "Emilia-Romagna",
    "friuli venezia": "Friuli-Venezia Giulia",
    "trentino alto": "Trentino-Alto Adige",
    "la campania": "Campania",
    "la sicilia": "Sicilia",
    "la toscana": "Toscana",
    "la puglia": "Puglia",
    "la sardegna": "Sardegna",
}

# Parole OCR spesso incollate: (regex, sostituzione)
OCR_GLUE_FIXES: List[Tuple[str, str]] = [
    (r"(?i)avverso(?=la\b)", "avverso "),
    (r"(?i)avverso(?=il\b)", "avverso "),
    (r"(?i)avverso(?=sentenza\b)", "avverso "),
    (r"(?i)avverso(?=l['\u2019])", "avverso l'"),
    (r"(?i)della(?=CORTE\b)", "della "),
    (r"(?i)della(?=Commissione\b)", "della "),
    (r"(?i)sentenza(?=della\b)", "sentenza della "),
    (r"(?i)sentenza(?=di\b)", "sentenza di "),
    (r"(?i)pronunciata(?=dalla\b)", "pronunciata dalla "),
    (r"(?i)pronuncia(?=di\b)", "pronuncia di "),
    (r"(?i)depositata(?=il\b)", "depositata il "),
    (r"(?i)depositata(?=dalla\b)", "depositata dalla "),
    (r"(?i)indicare(?=la\b)", "indicare "),
    (r"(?i)indicare(?=il\b)", "indicare "),
    (r"(?i)relativamente(?=all)", "relativamente all"),
    (r"(?i)ricorso(?=per\b)", "ricorso per "),
    (r"(?i)ORDINANZA(?=DI\b)", "ORDINANZA DI "),
    (r"(?i)ORDINANZA(?=sul\b)", "ORDINANZA sul "),
    (r"(?i)decidendo(?=sul\b)", "decidendo sul "),
    (r"(?i)contro(?=AGENZIA\b)", "contro AGENZIA "),
    (r"(?i)contro(?=l['\u2019])", "contro l'"),
    (r"(?i)regionale(?=della\b)", "regionale della "),
    (r"(?i)regionale(?=del\b)", "regionale del "),
]

# Verbi OCR che chiudono il nome regione/luogo
REGIONE_STOP_VERBS = (
    "accol", "rigett", "riform", "annull", "revoc", "resp", "condann", "dichi",
    "inparte", "parzial", "conferm", "cass", "annull", "respint", "rigetta",
)

REGIONI_VALIDE = {
    "abruzzo",
    "basilicata",
    "calabria",
    "campania",
    "emilia-romagna",
    "friuli-venezia giulia",
    "lazio",
    "liguria",
    "lombardia",
    "marche",
    "molise",
    "piemonte",
    "puglia",
    "sardegna",
    "sicilia",
    "toscana",
    "trentino-alto adige",
    "umbria",
    "valle d'aosta",
    "veneto",
}

# Provincia (chiave codici_corte 1°_*) -> regione
PROVINCIA_TO_REGIONE = {
    "AGRIGENTO": "Sicilia", "ALESSANDRIA": "Piemonte", "ANCONA": "Marche", "AREZZO": "Toscana",
    "ASCOLI_PICENO": "Marche", "ASTI": "Piemonte", "AVELLINO": "Campania", "BARI": "Puglia",
    "BELLUNO": "Veneto", "BENEVENTO": "Campania", "BERGAMO": "Lombardia", "BIELLA": "Piemonte",
    "BOLOGNA": "Emilia-Romagna", "BOLZANO": "Trentino-Alto Adige", "BRESCIA": "Lombardia",
    "BRINDISI": "Puglia", "CAGLIARI": "Sardegna", "CALTANISSETTA": "Sicilia", "CAMPOBASSO": "Molise",
    "CASERTA": "Campania", "CATANIA": "Sicilia", "CATANZARO": "Calabria", "CHIETI": "Abruzzo",
    "COMO": "Lombardia", "COSENZA": "Calabria", "CREMONA": "Lombardia", "CROTONE": "Calabria",
    "CUNEO": "Piemonte", "ENNA": "Sicilia", "FERRARA": "Emilia-Romagna", "FIRENZE": "Toscana",
    "FOGGIA": "Puglia", "FORLÌ": "Emilia-Romagna", "FORLI": "Emilia-Romagna",
    "FROSINONE": "Lazio", "GENOVA": "Liguria", "GORIZIA": "Friuli-Venezia Giulia",
    "GROSSETO": "Toscana", "IMPERIA": "Liguria", "ISERNIA": "Molise", "L'AQUILA": "Abruzzo",
    "LA_SPEZIA": "Liguria", "LATINA": "Lazio", "LECCE": "Puglia", "LECCO": "Lombardia",
    "LIVORNO": "Toscana", "LODI": "Lombardia", "LUCCA": "Toscana", "MACERATA": "Marche",
    "MANTOVA": "Lombardia", "MASSA_CARRARA": "Toscana", "MATERA": "Basilicata", "MESSINA": "Sicilia",
    "MILANO": "Lombardia", "MODENA": "Emilia-Romagna", "MONZA": "Lombardia", "NAPOLI": "Campania",
    "NOVARA": "Piemonte", "NUORO": "Sardegna", "ORISTANO": "Sardegna", "PADOVA": "Veneto",
    "PALERMO": "Sicilia", "PARMA": "Emilia-Romagna", "PAVIA": "Lombardia", "PERUGIA": "Umbria",
    "PESARO": "Marche", "PESCARA": "Abruzzo", "PIACENZA": "Emilia-Romagna", "PISA": "Toscana",
    "PISTOIA": "Toscana", "PORDENONE": "Friuli-Venezia Giulia", "POTENZA": "Basilicata",
    "PRATO": "Toscana", "RAGUSA": "Sicilia", "RAVENNA": "Emilia-Romagna",
    "REGGIO_CALABRIA": "Calabria", "REGGIO_NELL'EMILIA": "Emilia-Romagna", "RIETI": "Lazio",
    "RIMINI": "Emilia-Romagna", "ROMA": "Lazio", "ROVIGO": "Veneto", "SALERNO": "Campania",
    "SASSARI": "Sardegna", "SAVONA": "Liguria", "SIENA": "Toscana", "SIRACUSA": "Sicilia",
    "SONDRIO": "Lombardia", "TARANTO": "Puglia", "TERAMO": "Abruzzo", "TERNI": "Umbria",
    "TREVISO": "Veneto", "TRIESTE": "Friuli-Venezia Giulia", "TRENTO": "Trentino-Alto Adige",
    "TORINO": "Piemonte", "TRAPANI": "Sicilia", "UDINE": "Friuli-Venezia Giulia",
    "VARESE": "Lombardia", "VENEZIA": "Veneto", "VERBANIA": "Piemonte", "VERCELLI": "Piemonte",
    "VERONA": "Veneto", "VIBO_VALENTIA": "Calabria", "VICENZA": "Veneto", "VITERBO": "Lazio",
    "AOSTA": "Valle d'Aosta",
}

REGIONE_2GRADO_LABEL = {
    "abruzzo": "Abruzzo",
    "basilicata": "Basilicata",
    "calabria": "Calabria",
    "campania": "Campania",
    "emilia-romagna": "Emilia-Romagna",
    "friuli-venezia giulia": "Friuli-Venezia Giulia",
    "lazio": "Lazio",
    "liguria": "Liguria",
    "lombardia": "Lombardia",
    "marche": "Marche",
    "molise": "Molise",
    "piemonte": "Piemonte",
    "puglia": "Puglia",
    "sardegna": "Sardegna",
    "sicilia": "Sicilia",
    "toscana": "Toscana",
    "trentino-alto adige": "Trentino-Alto Adige",
    "umbria": "Umbria",
    "valle d'aosta": "Valle d'Aosta",
    "veneto": "Veneto",
}

_CORTI_INDEX: Optional[Dict[str, Any]] = None


def _regione_lookup_key(regione: str) -> str:
    return regione.lower().replace("-", " ").strip()


def _normalized_regioni_valide() -> set[str]:
    return {_regione_lookup_key(name) for name in REGIONI_VALIDE}


def is_valid_regione(regione: str) -> bool:
    if not regione:
        return False
    if re.search(r"\b[a-zA-ZÀ-ÿ]\s+[a-zA-ZÀ-ÿ]\b", regione) and any(
        len(word) == 1 for word in regione.split()
    ):
        return False
    return _regione_lookup_key(regione) in _normalized_regioni_valide()


def normalize_regione_name(regione: str) -> Optional[str]:
    regione = re.sub(r"\s+", " ", regione.strip())
    regione = re.sub(r"^(della|del|di|dell')\s*", "", regione, flags=re.IGNORECASE)
    regione = re.sub(r"^dell.\s*", "", regione, flags=re.IGNORECASE)
    regione = re.sub(r"[\uFFFD\u00AD]", "", regione)
    regione = re.sub(r"\s*-\s*", "-", regione)
    regione = regione.strip(" ,.;-_")
    if not regione:
        return None

    fix_key = regione.lower()
    if fix_key in OCR_REGIONE_FIXES:
        regione = OCR_REGIONE_FIXES[fix_key]

    city_key = _regione_lookup_key(regione)
    if city_key in CITTA_TO_REGIONE:
        regione = CITTA_TO_REGIONE[city_key]

    alias_key = _regione_lookup_key(regione)
    if alias_key in REGIONE_ALIASES:
        regione = REGIONE_ALIASES[alias_key]

    if re.fullmatch(r"[A-ZÀ-ÿ\s\-']+", regione):
        words = regione.split()
        if len(words) == 2 and words[0] in {"EMILIA", "TRENTINO", "FRIULI"}:
            regione = "-".join(word.capitalize() for word in words)
        else:
            regione = " ".join(word.capitalize() for word in words)
    elif regione:
        regione = regione[0].upper() + regione[1:]

    return regione if is_valid_regione(regione) else None


def parse_autorita_emittente(autorita: str) -> Tuple[Optional[str], Optional[str]]:
    """Estrae CGTn e Regione da ogni riga, es. 'CGT 1° Sicilia', 'CGT 2° Lombardia'."""
    if not autorita:
        return None, None

    text = autorita.strip().replace("Â°", "°").replace("º", "°")

    match = re.match(r"^CGT\s+(\d+)\s*°\s+(.+?)\s*$", text, flags=re.IGNORECASE)
    if match:
        grado, regione = match.groups()
        regione = normalize_regione_name(regione)
        if regione:
            return f"CGT{grado}", regione

    match = re.search(
        r"CGT\s+(\d+)\s*°\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        grado, regione = match.groups()
        regione = normalize_regione_name(regione)
        if regione:
            return f"CGT{grado}", regione

    return None, None


def extract_autorita_from_ocr_strict(ocr_text: str) -> Tuple[Optional[str], Optional[str]]:
    if not ocr_text:
        return None, None
    return parse_autorita_emittente(" ".join(ocr_text.split()))


def _normalize_ocr_text(ocr_text: str) -> str:
    text = (
        ocr_text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\ufffd", "")
    )
    text = " ".join(text.split())
    for pattern, repl in OCR_GLUE_FIXES:
        text = re.sub(pattern, repl, text)
    # OCR spesso attacca il luogo al numero: LOMBARDIAn.4816 / MILANOn.3860 / ROM An.
    text = re.sub(r"(?<=[A-Za-zÀ-ÿ])n\.", " n.", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)(commissione\s+tributaria\s+regionale)\s*([A-Za-zÀ-ÿ]+)\s+n\.",
        r"\1 \2 n.",
        text,
    )
    # Numeri spezzati: n. 5012 /1 3 /2 2 -> n. 5012/1322
    text = re.sub(
        r"(?i)(n\.\s*)(\d+(?:\s*/\s*\d+)*)",
        lambda m: m.group(1) + re.sub(r"\s+", "", m.group(2)),
        text,
    )
    return text


def _region_prefix_pattern() -> str:
    return r"(?:(?:della|di)\s+|del\s+|dell')?"


def _regione_stop_pattern() -> str:
    verb_stops = "|".join(REGIONE_STOP_VERBS)
    return (
        r"(?=\s*n\.|\s+depositat|\s+depositata|\s+avverso|\s+accol|\s+rigett|"
        rf"\s+riform|\s+annull|\s+revoc|\s+resp|\s+condann|\s+dichi|"
        rf"\s+{verb_stops}|,|\.|;|$|\s\d|\s+sez\b)"
    )


def fuzzy_match_regione(luogo: str) -> Optional[str]:
    """Ripara regioni spezzate nell'OCR, es. 'l a Si- cilian' -> Sicilia."""
    if not luogo:
        return None
    compact = re.sub(r"[\s\-'\.]+", "", luogo.lower())
    if not compact:
        return None
    for region_key, label in REGIONE_2GRADO_LABEL.items():
        norm = region_key.replace("-", "").replace(" ", "")
        if norm in compact or compact in norm:
            return label
        if len(norm) >= 5 and norm[:5] in compact:
            return label
    return None


def is_correction_ordinanza(ocr_text: Optional[str]) -> bool:
    text = (ocr_text or "").lower()
    return ("correzione" in text and "errore materiale" in text) or (
        "correzione di errore materiale" in text
    )


def normalize_ref_year(year: str, doc_anno: Optional[str] = None) -> str:
    year = (year or "").strip()
    if len(year) == 4 and year.isdigit():
        return year
    if len(year) == 2 and year.isdigit():
        if doc_anno and len(str(doc_anno)) == 4:
            return str(doc_anno)[:2] + year
        return "20" + year
    return year


def extract_referenced_cassazione_pairs(ocr_text: str) -> List[Tuple[str, str]]:
    """Estrae (numero, anno) di sentenze/ordinanze Cassazione citate nel testo."""
    text = _normalize_ocr_text(ocr_text or "")
    patterns: List[Tuple[str, int, int]] = [
        (r"(?i)ordinanza\s+(?:numero\s+)?n?\.?\s*(\d+)\s*/\s*(\d{2,4})", 1, 2),
        (r"(?i)ordinanza\s+n\.\s*(\d+)\s+del\s+\d{1,2}/\d{1,2}/(\d{4})", 1, 2),
        (r"(?i)CASSAZIONE\s+ROMA\s*n\.\s*(\d+)\s*/\s*(\d{4})", 1, 2),
        (r"(?i)Corte\s+Suprema\s+di\s+Cassazione[^.]{0,100}?n\.\s*(\d+)\s*/\s*(\d{4})", 1, 2),
        (
            r"(?i)Corte\s+Suprema\s+di\s+Cassazione\s+il\s+\d+\s+\S+\s+(\d{4}),\s*n\.\s*(\d+)",
            2,
            1,
        ),
        (r"(?i)ordinanza\s+(?:di\s+questa\s+Corte\s+)?n\.\s*(\d+)\s*/\s*(\d{4})", 1, 2),
    ]
    seen: set[Tuple[str, str]] = set()
    pairs: List[Tuple[str, str]] = []
    for pattern, num_group, year_group in patterns:
        for match in re.finditer(pattern, text):
            numero = match.group(num_group)
            anno = match.group(year_group)
            if not numero or not anno:
                continue
            key = (numero, anno)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
    return pairs


def resolve_corte_via_cassazione_reference(
    doc: Dict[str, Any],
    ocr_text: str,
    *,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """
    Per ordinanze di correzione senza CGT nel testo: risale alla sentenza
    Cassazione citata e ne ricava il tribunale impugnato.
    """
    doc_anno = first_value(doc.get("anno"))
    own_numero = first_value(doc.get("numdec"))
    sess = session or requests.Session()
    if session is None:
        sess.headers.update({"User-Agent": USER_AGENTS[0]})

    for ref_num, ref_year in extract_referenced_cassazione_pairs(ocr_text):
        if own_numero and str(ref_num) == str(own_numero):
            continue
        anno = normalize_ref_year(str(ref_year), doc_anno)
        ref_doc = fetch_document_by_numero(str(ref_num), str(anno), session=sess)
        if not ref_doc:
            continue
        ref_ocr = first_value(ref_doc.get("ocr")) or first_value(ref_doc.get("testoocr")) or ""
        cgtn, regione = extract_autorita_from_ocr_italgiure(ref_ocr)
        if cgtn and regione:
            corte = meta_to_corte_portale(cgtn, regione)
            if corte:
                resolved, _ = finalize_corte_portale(corte, ocr_text=ref_ocr)
                if resolved:
                    return resolved
        nested = resolve_corte_via_cassazione_reference(ref_doc, ref_ocr, session=sess)
        if nested:
            return nested
    return None


def extract_autorita_from_ocr_italgiure(ocr_text: str) -> Tuple[Optional[str], Optional[str]]:
    """Per provvedimenti Cassazione: ricava CGTn/regione dal tribunale impugnato."""
    if not ocr_text:
        return None, None

    text = _normalize_ocr_text(ocr_text)
    stop = _regione_stop_pattern()
    region_prefix = _region_prefix_pattern()

    cgtn, regione = parse_autorita_emittente(text)
    if cgtn and regione:
        return cgtn, regione

    patterns = [
        (
            r"(?:avverso\s+)?(?:sentenza|ordinanza|provvedimento|decreto)\s+di\s+"
            r"Commissione\s+tributaria\s+regionale\s+"
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1",
        ),
        (
            r"(?:avverso\s+)?(?:sentenza|ordinanza|provvedimento)\s+di\s+"
            r"Corte\s+di\s+giustizia\s+tributaria\s+"
            r"(?:(?:II|2|secondo)|(?:I|1|primo))\s*°?\s*grado\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT_AUTO",
        ),
        (
            r"Corte\s+di\s+giustizia\s+tributaria\s+di\s+secondo\s+grado\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT2",
        ),
        (
            r"Corte\s+di\s+giustizia\s+tributaria\s+(?:di\s+)?(?:II|2|secondo)\s*°?\s*grado\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT2",
        ),
        (
            r"COMM\.?\s*TRIB\.?\s*REG\.?"
            r"(?:\s*SEZ\.?\s*DIST\.?)?\s*"
            + region_prefix
            + r"([A-ZÀ-ÿ][A-ZÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1",
        ),
        (
            r"Commissione\s+tributaria\s+regionale\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1",
        ),
        (
            r"Corte\s+di\s+giustizia\s+tributaria\s+di\s+primo\s+grado\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1",
        ),
        (
            r"Corte\s+di\s+giustizia\s+tributaria\s+(?:di\s+)?(?:I|1|primo)\s*°?\s*grado\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1",
        ),
        (
            r"Commissione\s+tributaria\s+provinciale\s+(?:di\s+)?"
            r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1_PROV",
        ),
        (
            r"CTP\s+(?:di\s+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1_PROV",
        ),
        (
            r"(?:sentenza|ordinanza|provvedimento)\s+(?:n\.?\s*[\d/]+\s*)?della\s+"
            r"Commissione\s+tributaria\s+regionale\s+"
            + region_prefix
            + r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1",
        ),
        (
            r"sezione\s+staccata\s+(?:di\s+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1_SEZ",
        ),
        (
            r"COMM\.?\s*TRIB\.?\s*PROV\.?\s*(?:DI\s+)?"
            r"([A-ZÀ-ÿ][A-ZÀ-ÿ\s\-']*?)"
            + stop,
            "CGT1_PROV",
        ),
    ]

    for pattern, cgtn_label in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            luogo_raw = match.group(1).strip()
            label = cgtn_label
            if label == "CGT_AUTO":
                full = match.group(0).upper()
                label = "CGT2" if any(x in full for x in ("II", "2", "SECONDO")) else "CGT1"

            if label in ("CGT1_PROV", "CGT1_SEZ"):
                citta = find_provincia_in_text(luogo_raw, text)
                if citta:
                    return "CGT1", _provincia_key_to_label(citta)
                continue

            regione = normalize_regione_name(luogo_raw)
            if not regione:
                regione = fuzzy_match_regione(luogo_raw)
            if label == "CGT2" and regione:
                return label, regione

            citta = find_provincia_in_text(luogo_raw, text)
            if citta:
                return "CGT1", _provincia_key_to_label(citta)
            if regione:
                if label == "CGT1" and is_valid_regione(regione):
                    citta = find_provincia_in_text_for_regione(regione, text)
                    if citta:
                        return "CGT1", _provincia_key_to_label(citta)
                return label, regione

    return None, None


def parse_italgiure_filename(filename: str) -> Dict[str, Optional[str]]:
    """
    Estrae metadati dal nome tecnico italgiure, es.
    snciv@s50@a2025@n32047@tO.clean.pdf
    """
    if not filename:
        return {"anno": None, "numero": None, "sezione": None, "tipo": None}

    base = filename.split(".")[0]
    parts = base.split("@")
    result: Dict[str, Optional[str]] = {
        "anno": None,
        "numero": None,
        "sezione": None,
        "tipo": None,
    }

    for part in parts:
        if part.startswith("a") and part[1:].isdigit():
            result["anno"] = part[1:]
        elif part.startswith("n") and part[1:].isdigit():
            result["numero"] = part[1:]
        elif part.startswith("s") and part[1:].isdigit():
            result["sezione"] = part[1:]
        elif part.startswith("t") and len(part) > 1:
            result["tipo"] = part[1:]

    return result


def resolve_cgtn_regione(doc: Dict[str, Any], mode: str = NAMING_MODE) -> Tuple[Optional[str], Optional[str]]:
    for field_name in ("cgtn_regione", "autorita_emittente", "autorita"):
        raw = first_value(doc.get(field_name))
        if not raw:
            continue
        cgtn, regione = parse_autorita_emittente(raw)
        if cgtn and regione:
            return cgtn, regione

    ocr_text = first_value(doc.get("ocr")) or first_value(doc.get("testoocr")) or ""
    if mode == "completo":
        return extract_autorita_from_ocr_strict(ocr_text)
    if mode == "italgiure":
        return extract_autorita_from_ocr_italgiure(ocr_text)
    return None, None


def format_datdep(datdep: Optional[str]) -> Optional[str]:
    if not datdep:
        return None

    text = datdep.strip()
    match = re.match(r"^(\d{2})[/.-](\d{2})[/.-](\d{4})$", text)
    if match:
        day, month, year = match.groups()
        return f"{day}-{month}-{year}"

    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return f"{digits[6:8]}-{digits[4:6]}-{digits[0:4]}"
    return text


def sanitize_filename_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "-", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def resolve_document_metadata(
    doc: Dict[str, Any],
    remote_filename: Optional[str] = None,
    mode: str = NAMING_MODE,
) -> Dict[str, Optional[str]]:
    parsed = parse_italgiure_filename(remote_filename or "")

    data_deposito = format_datdep(first_value(doc.get("datdep")))
    anno = first_value(doc.get("anno")) or parsed.get("anno")
    numero = (
        first_value(doc.get("numcard"))
        or first_value(doc.get("numprov"))
        or first_value(doc.get("numdec"))
        or parsed.get("numero")
    )
    cgtn, regione = resolve_cgtn_regione(doc, mode=mode)

    return {
        "datdep": data_deposito,
        "cgtn": cgtn,
        "regione": regione,
        "numero": numero,
        "anno": anno,
    }


def build_download_filename(
    doc: Dict[str, Any],
    remote_filename: Optional[str] = None,
    mode: str = NAMING_MODE,
    template: str = DOWNLOAD_FILENAME_TEMPLATE,
) -> Optional[str]:
    meta = resolve_document_metadata(doc, remote_filename=remote_filename, mode=mode)

    if mode == "semplice":
        if not meta["datdep"] or not meta["numero"] or not meta["anno"]:
            return None
        return (
            f"{sanitize_filename_part(meta['datdep'])}_"
            f"{sanitize_filename_part(meta['numero'])}-"
            f"{sanitize_filename_part(meta['anno'])}.pdf"
        )

    if not meta["datdep"] or not meta["numero"] or not meta["anno"]:
        return None

    if mode == "completo" and (not meta["cgtn"] or not meta["regione"]):
        return None

    if not meta["cgtn"] or not meta["regione"]:
        return (
            f"{sanitize_filename_part(meta['datdep'])}_"
            f"{sanitize_filename_part(meta['numero'])}-"
            f"{sanitize_filename_part(meta['anno'])}.pdf"
        )

    values = {
        "datdep": sanitize_filename_part(meta["datdep"]),
        "cgtn": sanitize_filename_part(meta["cgtn"]),
        "regione": sanitize_filename_part(meta["regione"]),
        "numero": sanitize_filename_part(meta["numero"]),
        "anno": sanitize_filename_part(meta["anno"]),
    }

    try:
        local_name = template.format(**values)
    except KeyError:
        return None

    if not local_name.lower().endswith(".pdf"):
        local_name += ".pdf"
    return local_name


def meta_to_corte_portale(cgtn: Optional[str], regione: Optional[str]) -> Optional[str]:
    """CGT1 + Lombardia -> CGT 2° Lombardia (formato portale MEF / SGAI)."""
    if not cgtn or not regione:
        return None
    match = re.match(r"CGT\s*(\d+)", cgtn.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    return f"CGT {match.group(1)}° {regione}"


def _provincia_key_to_label(provincia_key: str) -> str:
    labels = {
        "LA_SPEZIA": "La Spezia",
        "L'AQUILA": "L'Aquila",
        "REGGIO_NELL'EMILIA": "Reggio nell'Emilia",
        "REGGIO_CALABRIA": "Reggio Calabria",
        "MASSA_CARRARA": "Massa Carrara",
        "ASCOLI_PICENO": "Ascoli Piceno",
        "VIBO_VALENTIA": "Vibo Valentia",
        "FORLÌ": "Forlì",
        "FORLI": "Forli",
    }
    if provincia_key in labels:
        return labels[provincia_key]
    return provincia_key.replace("_", " ").title()


def _get_corti_index() -> Dict[str, Any]:
    global _CORTI_INDEX
    if _CORTI_INDEX is not None:
        return _CORTI_INDEX

    codici_path = SGAI_PACKAGE_DIR / "codici_corte.json"
    data = json.loads(codici_path.read_text(encoding="utf-8"))
    corte_map = data.get("corteToCodice") or {}

    regione_to_cities: Dict[str, list[tuple[str, str]]] = {}
    for key in corte_map:
        if not key.startswith("1°_"):
            continue
        provincia_key = key.split("_", 1)[1]
        regione = PROVINCIA_TO_REGIONE.get(provincia_key)
        if not regione:
            continue
        region_key = _regione_lookup_key(regione)
        regione_to_cities.setdefault(region_key, []).append(
            (provincia_key, _provincia_key_to_label(provincia_key))
        )

    for region_key in regione_to_cities:
        regione_to_cities[region_key].sort(key=lambda item: len(item[0]), reverse=True)

    _CORTI_INDEX = {
        "regione_to_cities": regione_to_cities,
        "regione_2grado": REGIONE_2GRADO_LABEL,
    }
    return _CORTI_INDEX


def find_provincia_in_text(luogo_raw: str, ocr_text: str) -> Optional[str]:
    """Se luogo_raw e' gia una provincia valida, ritorna la chiave; altrimenti cerca in OCR."""
    candidate = luogo_raw.upper().replace(" ", "_").replace("'", "'")
    if candidate in PROVINCIA_TO_REGIONE:
        return candidate
    for token in re.split(r"[\s,;]+", luogo_raw):
        key = token.upper().replace("'", "'")
        if key in PROVINCIA_TO_REGIONE:
            return key

    regione = normalize_regione_name(luogo_raw)
    if regione and is_valid_regione(regione):
        return find_provincia_in_text_for_regione(regione, ocr_text)
    return None


def find_provincia_in_text_for_regione(regione: str, ocr_text: str) -> Optional[str]:
    """CGT 1° + regione (es. Puglia): cerca la citta/provincia nell'OCR."""
    if not ocr_text or not regione:
        return None
    index = _get_corti_index()
    region_key = _regione_lookup_key(regione)
    text_upper = ocr_text.upper()
    for provincia_key, label in index["regione_to_cities"].get(region_key, []):
        patterns = [
            rf"\b{re.escape(provincia_key.replace('_', ' '))}\b",
            rf"\b{re.escape(label.upper())}\b",
        ]
        if provincia_key == "ROMA" and re.search(r"\bROMA\b", text_upper):
            return provincia_key
        for pattern in patterns:
            if re.search(pattern, text_upper):
                return provincia_key
    return None


def resolve_corte_portale_ambiguous(
    corte_portale: str,
    ocr_text: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Risolve corti tipo 'CGT 1° Puglia' che non esistono in codici_corte.json.
    Ritorna (corte_risolta, nota) oppure (None, motivo).
    """
    _, corte_portale_to_codice = get_portal_to_filename_module()
    if corte_portale_to_codice(corte_portale):
        return corte_portale, None

    match = re.match(r"CGT\s*(1|2)[°º]?\s+(.+)", corte_portale.strip(), flags=re.IGNORECASE)
    if not match:
        return None, "formato corte non riconosciuto"

    grado, luogo = match.group(1), match.group(2).strip()
    ocr = ocr_text or ""

    if grado == "1":
        citta_key = find_provincia_in_text(luogo, ocr)
        if not citta_key and is_valid_regione(normalize_regione_name(luogo) or ""):
            citta_key = find_provincia_in_text_for_regione(luogo, ocr)
        if citta_key:
            resolved = f"CGT 1° {_provincia_key_to_label(citta_key)}"
            if corte_portale_to_codice(resolved):
                return resolved, f"provincia {citta_key} da OCR"

        regione_norm = normalize_regione_name(luogo)
        if regione_norm and is_valid_regione(regione_norm):
            region_key = _regione_lookup_key(regione_norm)
            label_2 = REGIONE_2GRADO_LABEL.get(region_key)
            if label_2:
                resolved = f"CGT 2° {label_2}"
                if corte_portale_to_codice(resolved):
                    return resolved, f"fallback CGT 2° {label_2} (regione senza 1° omonimo)"

    return None, f"Corte non trovata in codici_corte.json: {corte_portale}"


def finalize_corte_portale(
    corte_portale: Optional[str],
    ocr_text: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Garantisce una corte mappabile in codici_corte.json, con fallback automatici."""
    if not corte_portale:
        return None, None

    _, corte_portale_to_codice = get_portal_to_filename_module()
    if corte_portale_to_codice(corte_portale):
        return corte_portale, None

    resolved, note = resolve_corte_portale_ambiguous(corte_portale, ocr_text=ocr_text or "")
    if resolved:
        return resolved, note
    return None, note


def is_ocr_obscured(ocr_text: Optional[str]) -> bool:
    text = (ocr_text or "").strip().lower()
    return "oscuramento" in text or text == "in fase di valutazione oscuramento"


def load_corte_overrides() -> Dict[Tuple[str, str], str]:
    """Override manuale per sentenze con OCR oscurato: numero,anno,corte"""
    global _corte_overrides
    if _corte_overrides is not None:
        return _corte_overrides

    overrides: Dict[Tuple[str, str], str] = {}
    if CORTE_OVERRIDE_CSV.exists():
        import csv

        with CORTE_OVERRIDE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                numero = (row.get("numero") or "").strip()
                anno = (row.get("anno") or "").strip()
                corte = (row.get("corte") or "").strip()
                if not numero or not anno or not corte:
                    continue
                if numero.startswith("#") or corte.startswith("#"):
                    continue
                overrides[(numero, anno)] = corte
    _corte_overrides = overrides
    return overrides


def lookup_cache_nome_by_numero_anno(
    cache: Any,
    numero: str,
    anno: str,
) -> Optional[str]:
    """Cerca Sentenza_COD_NUM_ANNO nella cache SGAI quando manca il codice."""
    if not numero or not anno:
        return None
    suffix = f"_{numero}_{anno}".lower()
    matches = [
        key for key in cache._load_keys()
        if key.lower().endswith(suffix) and key.lower().startswith("sentenza_")
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def log_oscurato_pending(numero: str, anno: str, datdep: str = "") -> None:
    import csv

    header = ("numero", "anno", "datdep", "nota")
    rows = []
    if OSCURATI_LOG_CSV.exists():
        with OSCURATI_LOG_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    key = (numero, anno)
    if any((r.get("numero"), r.get("anno")) == key for r in rows):
        return
    with OSCURATI_LOG_CSV.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if not rows:
            writer.writeheader()
        writer.writerow({
            "numero": numero,
            "anno": anno,
            "datdep": datdep,
            "nota": "OCR oscurato su italgiure - aggiungere riga in corte_override.csv",
        })


def get_sgai_cache(cache_dir: pathlib.Path):
    global _sgai_cache
    if _sgai_cache is not None:
        return _sgai_cache
    if not SGAI_PACKAGE_DIR.is_dir():
        raise FileNotFoundError(f"Pacchetto SGAI non trovato: {SGAI_PACKAGE_DIR}")
    pkg = str(SGAI_PACKAGE_DIR)
    if pkg not in sys.path:
        sys.path.insert(0, pkg)
    from sgai_sentenze_cache import SentenzeCache

    _sgai_cache = SentenzeCache(cache_dir=str(cache_dir))
    return _sgai_cache


def safe_print(*parts: Any, **kwargs: Any) -> None:
    with _print_lock:
        print(*parts, **kwargs)


def make_worker_session() -> requests.Session:
    sess = requests.Session()
    idx = random.randint(0, len(USER_AGENTS) - 1)
    sess.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": USER_AGENTS[idx],
    })
    return sess


def wake_sgai_server(timeout: int = 90) -> Dict[str, Any]:
    """Accende EC2 SGAI se spenta (endpoint wake-up AWS)."""
    try:
        r = requests.post(
            SGAI_WAKE_URL,
            json={"force_start": True, "target_instance": "SGAI-Production"},
            timeout=timeout,
        )
        return {"ok": r.status_code < 400, "status": r.status_code, "body": r.text[:300]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def probe_italgiure_endpoint(
    numero: str,
    anno: str,
    *,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Verifica su Solr italgiure se il provvedimento esiste."""
    sess = session or make_worker_session()
    try:
        doc = fetch_document_by_numero(str(numero), str(anno), session=sess)
        return {
            "source": "italgiure",
            "endpoint": SOLR_ENDPOINT,
            "ok": True,
            "exists": doc is not None,
            "file_id": get_file_id(doc) if doc else None,
            "numero": str(numero),
            "anno": str(anno),
        }
    except Exception as exc:
        return {
            "source": "italgiure",
            "endpoint": SOLR_ENDPOINT,
            "ok": False,
            "exists": None,
            "error": str(exc),
            "numero": str(numero),
            "anno": str(anno),
        }


def probe_sgai_endpoint(
    codice: str,
    numero: str,
    anno: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: int = 45,
) -> Dict[str, Any]:
    """
    GET /v1/admin/sentenze-check?codice=V70&numero=1205&anno=2026
    Risposta server SGAI: has / download.skip
    """
    url = f"{SGAI_API_BASE}{SGAI_CHECK_PATH}"
    sess = session or make_worker_session()
    try:
        response = sess.get(
            url,
            params={"codice": codice, "numero": str(numero), "anno": str(anno)},
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        download = data.get("download") or {}
        return {
            "source": "sgai_live",
            "endpoint": url,
            "ok": True,
            "exists": bool(data.get("has")),
            "skip": bool(download.get("skip")),
            "reason": download.get("reason") or "",
            "response": payload,
            "codice": codice,
            "numero": str(numero),
            "anno": str(anno),
        }
    except Exception as exc:
        return {
            "source": "sgai_live",
            "endpoint": url,
            "ok": False,
            "exists": None,
            "skip": False,
            "error": str(exc),
            "codice": codice,
            "numero": str(numero),
            "anno": str(anno),
        }


def resolve_sgai_presence(
    codice: str,
    numero: str,
    anno: str,
    *,
    sgai_cache: Any = None,
    session: Optional[requests.Session] = None,
    live: bool = False,
) -> Dict[str, Any]:
    """
    Controlla presenza su SGAI: endpoint live se richiesto, altrimenti cache locale.
    Se live fallisce (timeout/server spento), fallback su cache locale.
    """
    if live:
        live_result = probe_sgai_endpoint(codice, numero, anno, session=session)
        if live_result.get("ok"):
            return live_result
        if sgai_cache:
            cache_info = sgai_cache.check(codice=codice, numero=numero, anno=anno)
            return {
                "source": "sgai_cache_fallback",
                "endpoint": live_result.get("endpoint"),
                "ok": True,
                "exists": cache_info.get("has", False),
                "skip": cache_info.get("download", {}).get("skip", False),
                "reason": cache_info.get("download", {}).get("reason", ""),
                "live_error": live_result.get("error"),
            }
        return live_result

    if sgai_cache:
        cache_info = sgai_cache.check(codice=codice, numero=numero, anno=anno)
        return {
            "source": "sgai_cache",
            "endpoint": str(sgai_cache.cache_dir / "nomi_base.txt"),
            "ok": True,
            "exists": cache_info.get("has", False),
            "skip": cache_info.get("download", {}).get("skip", False),
            "reason": cache_info.get("download", {}).get("reason", ""),
        }
    return {
        "source": "none",
        "ok": False,
        "exists": None,
        "skip": False,
        "error": "Cache SGAI non disponibile",
    }


def check_sentenza_presence(
    doc: Dict[str, Any],
    portal_info: Dict[str, Any],
    *,
    session: Optional[requests.Session] = None,
    sgai_cache: Any = None,
    sgai_live: bool = False,
) -> Dict[str, Any]:
    """Verifica completa: italgiure (Solr) + SGAI (endpoint o cache)."""
    numero = portal_info.get("numero") or first_value(doc.get("numdec"))
    anno = portal_info.get("anno") or first_value(doc.get("anno"))
    codice = portal_info.get("codice")
    sess = session or make_worker_session()

    italgiure = probe_italgiure_endpoint(str(numero), str(anno), session=sess)
    sgai: Dict[str, Any] = {"source": "skipped", "ok": True, "exists": None, "skip": False}
    if codice and numero and anno:
        sgai = resolve_sgai_presence(
            str(codice), str(numero), str(anno),
            sgai_cache=sgai_cache, session=sess, live=sgai_live,
        )

    return {
        "numero": str(numero),
        "anno": str(anno),
        "codice": codice,
        "nomeFile": portal_info.get("nomeFile"),
        "italgiure": italgiure,
        "sgai": sgai,
        "on_italgiure": italgiure.get("exists") is True,
        "on_sgai": sgai.get("exists") is True,
        "skip_download": bool(sgai.get("skip")),
    }


def get_portal_to_filename_module():
    pkg = str(SGAI_PACKAGE_DIR)
    if pkg not in sys.path:
        sys.path.insert(0, pkg)
    from portal_to_filename import build_filename, corte_portale_to_codice

    return build_filename, corte_portale_to_codice


def verify_portal_codici() -> bool:
    """Verifica esempi ufficiali in codici_corte.json e portal_to_filename."""
    codici_path = SGAI_PACKAGE_DIR / "codici_corte.json"
    if not codici_path.exists():
        print(f"❌ codici_corte.json non trovato: {codici_path}")
        return False

    build_filename, _ = get_portal_to_filename_module()
    data = json.loads(codici_path.read_text(encoding="utf-8"))
    corte_map = data.get("corteToCodice") or {}
    if not corte_map:
        print("❌ codici_corte.json: corteToCodice vuoto")
        return False

    ok = 0
    fail = 0
    print("🔎 Verifica mappatura codici_corte.json...")

    for esempio in data.get("esempi", []):
        portale = esempio.get("portale", "")
        codice_atteso = esempio.get("codice", "")
        file_atteso = esempio.get("nomeFile", "")
        match = re.search(
            r"CGT\s*(1|2)\s*°\s+(.+?)\s+n\.\s*(\d+)\s*/\s*(\d{4})",
            portale,
            flags=re.IGNORECASE,
        )
        if not match:
            print(f"  ⚠️ Esempio non parsabile: {portale}")
            fail += 1
            continue
        grado, luogo, numero, anno = match.groups()
        corte = f"CGT {grado}° {luogo.strip()}"
        result = build_filename(corte, numero, anno)
        passed = (
            result.get("ok")
            and result.get("codice") == codice_atteso
            and result.get("nomeFile") == file_atteso
        )
        if passed:
            ok += 1
        else:
            fail += 1
            print(
                f"  ❌ {corte} n.{numero}/{anno}: atteso {file_atteso}, "
                f"ottenuto {result.get('nomeFile')} ({result.get('error', '')})"
            )

    if fail:
        print(f"❌ Verifica codici fallita: {ok} OK, {fail} FAIL")
        return False

    print(f"✅ codici_corte.json verificato: {ok} esempi OK, {len(corte_map)} corti mappate")
    return True


def extract_corte_portale_from_doc(
    doc: Dict[str, Any],
    remote_filename: Optional[str] = None,
    mode: str = "italgiure",
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Ricava la corte nel formato portale MEF: CGT 2° Lombardia."""
    ocr_text = first_value(doc.get("ocr")) or first_value(doc.get("testoocr")) or ""

    for field_name in ("autorita_emittente", "autorita", "cgtn_regione"):
        raw = first_value(doc.get(field_name))
        if not raw:
            continue
        text = (
            raw.strip()
            .replace("CGT_1_", "CGT 1° ")
            .replace("CGT_2_", "CGT 2° ")
            .replace("_", " ")
            .replace("Â°", "°")
        )
        cgtn, regione = parse_autorita_emittente(text)
        if cgtn and regione:
            corte = meta_to_corte_portale(cgtn, regione)
            if corte:
                resolved, _ = finalize_corte_portale(corte, ocr_text=ocr_text)
                if resolved:
                    return resolved
        if re.search(r"CGT\s*\d", text, flags=re.IGNORECASE):
            corte = re.sub(r"\s+", " ", text)
            resolved, _ = finalize_corte_portale(corte, ocr_text=ocr_text)
            if resolved:
                return resolved

    meta = resolve_document_metadata(doc, remote_filename=remote_filename, mode=mode)
    corte = meta_to_corte_portale(meta.get("cgtn"), meta.get("regione"))
    if not corte and extract_referenced_cassazione_pairs(ocr_text):
        corte = resolve_corte_via_cassazione_reference(doc, ocr_text, session=session)
    if not corte:
        return None
    resolved, _ = finalize_corte_portale(corte, ocr_text=ocr_text)
    return resolved


def extract_numero_anno_from_doc(
    doc: Dict[str, Any],
    remote_filename: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Equivalente portale MEF: td numero + td anno."""
    parsed = parse_italgiure_filename(remote_filename or "")
    numero = (
        first_value(doc.get("numdec"))
        or first_value(doc.get("numprov"))
        or first_value(doc.get("numcard"))
        or parsed.get("numero")
    )
    anno = first_value(doc.get("anno")) or parsed.get("anno")
    return numero, anno


def build_portal_sentenza_filename(
    doc: Dict[str, Any],
    remote_filename: Optional[str] = None,
    mode: str = "italgiure",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Nome file standard SGAI/MEF:
    Sentenza_{CODICE}_{NUMERO}_{ANNO}.pdf
    """
    ocr_text = first_value(doc.get("ocr")) or first_value(doc.get("testoocr")) or ""
    numero, anno = extract_numero_anno_from_doc(doc, remote_filename=remote_filename)

    corte_guess = extract_corte_portale_from_doc(
        doc, remote_filename=remote_filename, mode=mode, session=session
    )
    if not corte_guess and numero and anno:
        override = load_corte_overrides().get((str(numero), str(anno)))
        if override:
            corte_guess = override

    corte_portale, resolve_note = finalize_corte_portale(corte_guess, ocr_text=ocr_text)

    if not corte_portale or not numero or not anno:
        return {
            "ok": False,
            "error": "Mancano corte, numero o anno per il naming portale",
            "cortePortale": corte_guess,
            "numero": numero,
            "anno": anno,
            "resolveNote": resolve_note,
        }

    build_filename, corte_portale_to_codice = get_portal_to_filename_module()
    codice = corte_portale_to_codice(corte_portale)
    if not codice:
        return {
            "ok": False,
            "error": resolve_note or f"Corte non trovata in codici_corte.json: {corte_guess}",
            "cortePortale": corte_guess,
            "numero": str(numero),
            "anno": str(anno),
            "resolveNote": resolve_note,
        }

    result = build_filename(corte_portale, numero, anno)
    result["cortePortale"] = corte_portale
    if corte_guess and corte_guess != corte_portale:
        result["corteOriginale"] = corte_guess
    if resolve_note:
        result["resolveNote"] = resolve_note
    if corte_guess and corte_guess != corte_portale and "override" not in (resolve_note or ""):
        if load_corte_overrides().get((str(numero), str(anno))) == corte_guess:
            result["resolveNote"] = "corte da corte_override.csv"
    if not result.get("ok"):
        result["error"] = result.get("error") or "build_filename fallito"
    return result


def build_sgai_filename(
    doc: Dict[str, Any],
    remote_filename: Optional[str] = None,
    mode: str = NAMING_MODE,
) -> Optional[Dict[str, Any]]:
    """Compat: ritorna None se il naming portale non e' disponibile."""
    result = build_portal_sentenza_filename(doc, remote_filename=remote_filename, mode=mode)
    return result if result.get("ok") else None


def should_use_sgai_cache(year_filter: Optional[str]) -> bool:
    return bool(year_filter and str(year_filter) in SGAI_CACHE_YEARS)


def get_file_id(doc: Dict[str, Any]) -> str:
    numcard = doc.get("numcard")
    if isinstance(numcard, list):
        numcard = numcard[0] if numcard else ""
    anno = doc.get("anno")
    if isinstance(anno, list):
        anno = anno[0] if anno else ""
    doc_id = doc.get("id", "")
    return f"{numcard}_{anno}_{doc_id}".strip("_")

def start_captcha_server():
    global server_port
    for port in range(8080, 8090):
        try:
            server = HTTPServer(('0.0.0.0', port), CaptchaHandler)
            server_port = port
            print(f"🌐 Server captcha avviato su porta {port}")
            print(f"   Accedi da: http://localhost:{port}")
            # Qui stampa anche l’IP LAN (per smartphone in wifi)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                print(f"   Da cellulare: http://{local_ip}:{port}")
            except Exception:
                pass
            server.serve_forever()
            break
        except OSError:
            continue
    else:
        print("❌ Impossibile avviare il server captcha")

def fetch_catalog_total(year: str, *, session: requests.Session = None) -> int:
    """Totale provvedimenti Cass. tributaria (snciv szdec 5) per anno su italgiure."""
    filters = ["kind:\"snciv\"", "szdec:\"5\""]
    if year:
        filters.append(f"anno:\"{year}\"")
    payload = {
        "start": "0",
        "rows": "0",
        "q": " AND ".join(filters),
        "wt": "json",
    }
    headers = {
        "User-Agent": USER_AGENTS[user_agent_idx],
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.italgiure.giustizia.it",
        "Referer": "https://www.italgiure.giustizia.it/sncass/",
    }
    sess = session or requests.Session()
    try:
        response = sess.post(SOLR_ENDPOINT, headers=headers, data=payload, timeout=30, verify=False)
        response.raise_for_status()
        return int(response.json().get("response", {}).get("numFound", 0))
    except Exception as exc:
        print(f"⚠️ Impossibile leggere totale catalogo: {exc}", file=sys.stderr)
        return 0


def fetch_documents_page(
    start: int = 0,
    rows: int = 10,
    year: str = None,
    *,
    session: requests.Session = None,
    retries: int = 3,
) -> List[Dict[str, Any]]:
    """Scarica una pagina Solr con retry (evita stop prematuri su errori transient)."""
    for attempt in range(max(1, retries)):
        docs = list(fetch_documents(start=start, rows=rows, year=year, session=session))
        if docs:
            return docs
        if attempt < retries - 1:
            safe_print(f"⚠️ Solr vuoto a start={start}, retry {attempt + 2}/{retries}...")
            time.sleep(random.uniform(3.0, 7.0))
    return []


def fetch_documents(start: int = 0, rows: int = 10, year: str = None, *, session: requests.Session = None) -> Iterable[Dict[str, Any]]:
    yield from _fetch_documents_query(start=start, rows=rows, year=year, numero=None, session=session)


def numero_lookup_variants(numero: str) -> List[str]:
    """
    Varianti numdec su italgiure.
    Es. ordinanza citata come 5902/2025 e' indicizzata come numdec=05902.
    """
    raw = (numero or "").strip()
    if not raw:
        return []
    variants: List[str] = []
    for candidate in (raw, raw.lstrip("0") or raw):
        if candidate not in variants:
            variants.append(candidate)
    if raw.isdigit() or raw.lstrip("0").isdigit():
        core = raw.lstrip("0") or raw
        for width in (4, 5, 6):
            padded = core.zfill(width)
            if padded not in variants:
                variants.append(padded)
    return variants


def fetch_document_by_numero(
    numero: str,
    year: str,
    *,
    session: requests.Session = None,
) -> Optional[Dict[str, Any]]:
    for candidate in numero_lookup_variants(numero):
        docs = list(_fetch_documents_query(start=0, rows=1, year=year, numero=candidate, session=session))
        if docs:
            return docs[0]
    return None


def _fetch_documents_query(
    start: int = 0,
    rows: int = 10,
    year: str = None,
    numero: str = None,
    *,
    session: requests.Session = None,
) -> Iterable[Dict[str, Any]]:
    filters = ["kind:\"snciv\"", "szdec:\"5\""]
    if year:
        filters.append(f"anno:\"{year}\"")
    if numero:
        filters.append(f"numdec:\"{numero}\"")
    query = " AND ".join(filters)

    payload = {
        "start": str(start),
        "rows": str(rows),
        "q": query,
        "fl": (
            "id,filename,szdec,kind,tipoprov,numcard,numdec,numdep,"
            "datdep,ecli,anno,datdec,presidente,relatore,ocr,testoocr,"
            "cgtn_regione,autorita_emittente"
        ),
        "hl": "false",
        "wt": "json",
        "indent": "off",
        "sort": "pd desc,numdec desc",
    }
    headers = {
        "User-Agent": USER_AGENTS[user_agent_idx],
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language":"it-IT,it;q=0.9,en;q=0.4",
        "Origin": "https://www.italgiure.giustizia.it",
        "Referer":"https://www.italgiure.giustizia.it/sncass/",
    }
    sess = session or requests.Session()
    try:
        response = sess.post(
            SOLR_ENDPOINT,
            headers=headers,
            data=payload,
            timeout=30,
            verify=False,
        )
        response.raise_for_status()
        data = response.json()
        for doc in data.get("response", {}).get("docs", []):
            yield doc
    except Exception as exc:
        print(f"❌ Errore query Solr API: {exc}", file=sys.stderr)
        return

def build_pdf_url(filename: str) -> str:
    if not filename.startswith("./"):
        filename = f"./{filename}"
    if not filename.endswith(".clean.pdf"):
        filename = filename.replace(".pdf", ".clean.pdf")
    return f"{PDF_BASE_URL}{filename}"

def is_captcha_page(content: bytes) -> bool:
    content_lower = content.lower()
    indicators = [b'captcha', b'recaptcha', b'google.com/recaptcha', b'checkbox', b'robot', b'verificare', b'verify']
    return any(i in content_lower for i in indicators)

def wait_for_captcha_resolution(url: str):
    global current_captcha_url, captcha_resolved
    current_captcha_url = url
    captcha_resolved.clear()
    print(f"\n🤖 CAPTCHA RILEVATO!")
    print(f"📱 Vai su: http://localhost:{server_port}")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"📱 Da cellulare: http://{local_ip}:{server_port}")
    except Exception:
        pass
    print("⏳ Aspetto che risolvi il captcha...")
    captcha_resolved.wait()
    print("✅ Captcha risolto! Continuando...")

def download_pdf_with_captcha_handling(url: str, dest_path: pathlib.Path, session: requests.Session, file_id: str, checkpoint: 'DownloadCheckpoint') -> bool:
    global captcha_in_row, user_agent_idx

    max_retries = 3
    for attempt in range(max_retries):
        # Random change of User-Agent (umano)
        if random.random() < 0.13:
            prev = user_agent_idx
            while True:
                idx = random.randint(0, len(USER_AGENTS) - 1)
                if idx != prev:
                    user_agent_idx = idx
                    break
            session.headers["User-Agent"] = USER_AGENTS[user_agent_idx]
            print(f"[UA-ROTATE] Nuovo User-Agent: {USER_AGENTS[user_agent_idx]}")

        try:
            print(f"📥 Scaricando: {dest_path.name}")
            headers = {
                "User-Agent": USER_AGENTS[user_agent_idx],
                "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
                "Connection": "keep-alive",
                "Referer": "https://www.italgiure.giustizia.it/sncass/",
                "Upgrade-Insecure-Requests": "1",
            }
            r = session.get(url, timeout=60, verify=False, headers=headers, allow_redirects=True)
            r.raise_for_status()
            if is_captcha_page(r.content):
                captcha_in_row += 1
                print(f"🔒 Captcha rilevato n.{captcha_in_row} (tentativo {attempt + 1}/{max_retries})")
                force_next_ua = (captcha_in_row >= MAX_CAPTCHAS_BEFORE_ROTATION)
                if force_next_ua:
                    print(f"🚦 Troppi captcha. Forzo cambio User-Agent")
                    user_agent_idx = (user_agent_idx + 1) % len(USER_AGENTS)
                    session.headers["User-Agent"] = USER_AGENTS[user_agent_idx]
                wait_for_captcha_resolution(url)
                time.sleep(random.uniform(2, 6))
                continue

            captcha_in_row = 0
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(r.content)
            print(f"✅ Salvato: {dest_path.name} ({len(r.content)} bytes)")
            checkpoint.mark_processed(file_id, success=True)
            return True

        except Exception as exc:
            print(f"❌ Errore download {dest_path.name}: {exc}")
            if attempt < max_retries - 1:
                print("🔄 Riprovo tra 3 secondi...")
                time.sleep(random.uniform(3, 5))

    print(f"💥 Fallito download di {dest_path.name} dopo {max_retries} tentativi")
    checkpoint.mark_processed(file_id, success=False)
    return False


def _ocr_has_cgt_hints(ocr_text: str) -> bool:
    lower = (ocr_text or "").lower()
    hints = (
        "commissione tributaria",
        "corte di giustizia tributaria",
        "comm. trib",
        "ctp ",
        "cgt ",
        "tributaria regionale",
        "tributaria provinciale",
        "sezione staccata",
    )
    return any(h in lower for h in hints)


def _referenced_cassazione_unresolvable(
    ocr_text: str,
    doc_anno: Optional[str],
    *,
    session: Optional[requests.Session] = None,
) -> Optional[Tuple[str, str]]:
    """Ritorna (numero, anno) del primo riferimento Cassazione non trovato su italgiure."""
    sess = session or requests.Session()
    if session is None:
        sess.headers.update({"User-Agent": USER_AGENTS[0]})
    for ref_num, ref_year in extract_referenced_cassazione_pairs(ocr_text):
        anno = normalize_ref_year(str(ref_year), doc_anno)
        if not fetch_document_by_numero(str(ref_num), str(anno), session=sess):
            return ref_num, anno
    return None


def classify_naming_failure(
    doc: Dict[str, Any],
    ocr_text: str,
    portal_info: Dict[str, Any],
    *,
    session: Optional[requests.Session] = None,
) -> Dict[str, str]:
    """Classifica il fallimento e suggerisce l'azione correttiva."""
    numero = str(portal_info.get("numero") or first_value(doc.get("numdec")) or "")
    anno = str(portal_info.get("anno") or first_value(doc.get("anno")) or "")
    lower = (ocr_text or "").lower()

    if is_ocr_obscured(ocr_text):
        return {
            "category": "OSCURATO",
            "hint": "OCR oscurato su italgiure",
            "action": f"Aggiungi in {CORTE_OVERRIDE_CSV.name}: {numero},{anno},CGT 2° <regione>",
        }

    if re.search(r"corte\s+d.appello", lower):
        return {
            "category": "NON_TRIBUTARIO",
            "hint": "Procedimento con Corte d'Appello (non CGT)",
            "action": (
                f"Verifica se e' tributario; se si, override manuale: "
                f"{numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}"
            ),
        }

    if re.search(r"\btar\b|tribunale\s+amministrativo", lower):
        return {
            "category": "NON_TRIBUTARIO",
            "hint": "Procedimento amministrativo (TAR), non CGT",
            "action": f"Override manuale se serve: {numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}",
        }

    if _ocr_has_cgt_hints(ocr_text):
        return {
            "category": "OCR_PARZIALE",
            "hint": "Testo CGT presente ma pattern non riconosciuto",
            "action": f"Override manuale: {numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}",
        }

    missing_ref = _referenced_cassazione_unresolvable(
        ocr_text, anno or None, session=session
    )
    if missing_ref:
        ref_num, ref_anno = missing_ref
        return {
            "category": "RIFERIMENTO_MANCANTE",
            "hint": f"Cita Cass. n.{ref_num}/{ref_anno} assente su italgiure",
            "action": f"Override manuale: {numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}",
        }

    if extract_referenced_cassazione_pairs(ocr_text) or is_correction_ordinanza(ocr_text):
        return {
            "category": "CASSAZIONE_CORREZIONE",
            "hint": "Ordinanza di correzione: CGT non ricavabile automaticamente",
            "action": f"Override manuale: {numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}",
        }

    return {
        "category": "SCONOSCIUTO",
        "hint": "Nessun CGT rilevato nel testo OCR",
        "action": f"Override manuale: {numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}",
    }


def log_naming_failure(
    numero: str,
    anno: str,
    category: str,
    hint: str,
    action: str,
    *,
    page: int = 0,
    doc_idx: int = 0,
) -> None:
    import csv

    header = ("numero", "anno", "pagina", "doc", "categoria", "hint", "azione")
    rows: List[Dict[str, str]] = []
    if NAMING_ISSUES_CSV.exists():
        with NAMING_ISSUES_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    key = (numero, anno, category)
    if any((r.get("numero"), r.get("anno"), r.get("categoria")) == key for r in rows):
        return
    with NAMING_ISSUES_CSV.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if not rows:
            writer.writeheader()
        writer.writerow({
            "numero": numero,
            "anno": anno,
            "pagina": str(page),
            "doc": str(doc_idx),
            "categoria": category,
            "hint": hint,
            "azione": action,
        })


def analyze_document_naming(
    doc: Dict[str, Any],
    *,
    force_portal_naming: bool,
    sgai_cache: Any = None,
    naming_mode: str = "italgiure",
    formato_nome: str = DOWNLOAD_FILENAME_TEMPLATE,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Controlli pre-download: naming, cache, OCR oscurato."""
    file_field = doc.get("filename")
    file_name = file_field[0] if isinstance(file_field, list) and file_field else file_field
    file_id = get_file_id(doc)
    ocr_text = first_value(doc.get("ocr")) or first_value(doc.get("testoocr")) or ""

    result = {
        "file_id": file_id,
        "file_name": file_name,
        "numero": None,
        "anno": None,
        "status": "FAIL",
        "nomeFile": None,
        "error": "",
        "category": "",
        "hint": "",
        "action": "",
        "obscured": is_ocr_obscured(ocr_text),
        "in_cache": False,
        "would_skip": False,
    }

    if not file_name:
        result["error"] = "Nome file tecnico mancante"
        return result

    portal_info = build_portal_sentenza_filename(
        doc, remote_filename=file_name, mode=naming_mode, session=session
    )
    result["numero"] = portal_info.get("numero")
    result["anno"] = portal_info.get("anno")

    if force_portal_naming:
        if portal_info.get("ok"):
            result["status"] = "OK"
            result["nomeFile"] = portal_info["nomeFile"]
            if sgai_cache:
                codice = portal_info["codice"]
                numero = portal_info["numero"]
                anno = portal_info["anno"]
                if sgai_cache.should_skip(codice=codice, numero=numero, anno=anno):
                    result["status"] = "CACHE"
                    result["would_skip"] = True
                    result["error"] = "Gia in cache SGAI con embedding"
                elif sgai_cache.has(codice=codice, numero=numero, anno=anno):
                    result["status"] = "CACHE"
                    result["in_cache"] = True
                    result["error"] = "Gia in cache SGAI"
            return result

        numero = portal_info.get("numero")
        anno = portal_info.get("anno")
        if result["obscured"] and numero and anno:
            override = load_corte_overrides().get((str(numero), str(anno)))
            if override:
                retry = build_portal_sentenza_filename(
                    {**doc, "autorita_emittente": override},
                    remote_filename=file_name,
                    mode=naming_mode,
                )
                if retry.get("ok"):
                    result["status"] = "OK"
                    result["nomeFile"] = retry["nomeFile"]
                    result["error"] = "Risolto via corte_override.csv"
                    return result
            cached_nome = (
                lookup_cache_nome_by_numero_anno(sgai_cache, str(numero), str(anno))
                if sgai_cache else None
            )
            if cached_nome:
                result["status"] = "CACHE"
                result["would_skip"] = True
                result["error"] = f"OCR oscurato, presente in cache: {cached_nome}"
                return result
            result["status"] = "OSCURATO"
            failure = classify_naming_failure(doc, ocr_text, portal_info, session=session)
            result.update({
                "category": failure["category"],
                "hint": failure["hint"],
                "action": failure["action"],
            })
            result["error"] = (
                f"OCR oscurato - aggiungere {numero},{anno},CGT ... in {CORTE_OVERRIDE_CSV.name}"
            )
            log_naming_failure(
                str(numero), str(anno), result["category"], result["hint"], result["action"]
            )
            return result

        failure = classify_naming_failure(doc, ocr_text, portal_info, session=session)
        result.update({
            "category": failure["category"],
            "hint": failure["hint"],
            "action": failure["action"],
        })
        result["error"] = (
            f"[{failure['category']}] {failure['hint']} -> {failure['action']}"
        )
        return result

    if portal_info.get("ok"):
        result["status"] = "OK"
        result["nomeFile"] = portal_info["nomeFile"]
        return result

    local_name = build_download_filename(
        doc, remote_filename=file_name, mode=naming_mode, template=formato_nome
    )
    if local_name:
        result["status"] = "OK"
        result["nomeFile"] = local_name
    else:
        result["error"] = "Metadati insufficienti per il naming"
    return result


def run_preflight_check(
    *,
    year: str,
    pages: int,
    session: requests.Session,
    force_portal_naming: bool,
    sgai_cache: Any,
    naming_mode: str,
    formato_nome: str,
    rows_per_page: int = 10,
    start_page: int = 0,
    verbose: bool = True,
) -> int:
    if verbose:
        print("\n🔍 PREFLIGHT: controllo naming senza download")
        print("=" * 70)
    counts = {"OK": 0, "CACHE": 0, "OSCURATO": 0, "FAIL": 0}
    category_counts: Dict[str, int] = {}
    problems = []

    for page in range(start_page, start_page + pages):
        start = page * rows_per_page
        docs = list(fetch_documents(start=start, rows=rows_per_page, year=year, session=session))
        if not docs:
            break
        if verbose:
            print(f"\nPagina {page + 1}: {len(docs)} documenti")
        for i, doc in enumerate(docs, 1):
            analysis = analyze_document_naming(
                doc,
                force_portal_naming=force_portal_naming,
                sgai_cache=sgai_cache,
                naming_mode=naming_mode,
                formato_nome=formato_nome,
                session=session,
            )
            status = analysis["status"]
            counts[status] = counts.get(status, 0) + 1
            label = analysis.get("nomeFile") or analysis.get("error", "?")
            if verbose:
                print(f"  [{status:8}] doc {i:2} n.{analysis.get('numero')}/{analysis.get('anno')} -> {label}")
            if status in ("FAIL", "OSCURATO"):
                cat = analysis.get("category") or status
                category_counts[cat] = category_counts.get(cat, 0) + 1
                if status == "FAIL":
                    problems.append((page + 1, i, analysis))
                if analysis.get("numero") and analysis.get("anno"):
                    log_naming_failure(
                        str(analysis["numero"]),
                        str(analysis["anno"]),
                        cat,
                        analysis.get("hint", ""),
                        analysis.get("action", label),
                        page=page + 1,
                        doc_idx=i,
                    )

    if verbose:
        print("\n" + "=" * 70)
        print(
            f"Riepilogo: OK={counts.get('OK', 0)} CACHE={counts.get('CACHE', 0)} "
            f"OSCURATO={counts.get('OSCURATO', 0)} FAIL={counts.get('FAIL', 0)}"
        )
        if category_counts:
            print("Categorie problemi:")
            for cat, n in sorted(category_counts.items(), key=lambda x: -x[1]):
                print(f"  - {cat}: {n}")
            print(f"  Dettaglio salvato in {NAMING_ISSUES_CSV.name}")
        if counts.get("OSCURATO", 0):
            print(f"⚠️  Documenti oscurati: vedi {OSCURATI_LOG_CSV.name} / {CORTE_OVERRIDE_CSV.name}")
        if problems:
            print(f"❌ {len(problems)} documenti con errori bloccanti (non oscurati)")
            return 1
        print("✅ Preflight superato: nessun errore bloccante sul naming")
    return 1 if problems else 0


def process_one_document_in_page(
    doc: Dict[str, Any],
    *,
    doc_index: int,
    page_index: int,
    total_in_page: int,
    checkpoint: DownloadCheckpoint,
    download_dir: pathlib.Path,
    args: Any,
    force_portal_naming: bool,
    use_sgai_cache: bool,
    sgai_cache: Any,
    redownload: bool,
    sgai_live: bool,
    parallel_download: bool,
) -> Dict[str, int]:
    """Elabora un singolo documento (usabile da worker paralleli)."""
    stats = {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}
    worker_session = make_worker_session()
    file_id = get_file_id(doc)
    doc_label = f"{doc_index}/{total_in_page}"
    page_label = f"pagina {page_index + 1}"

    safe_print(f"\n📋 Documento {doc_label} ({page_label})")
    if redownload:
        checkpoint.unmark_file(file_id)
        safe_print(f"  🔄 Redownload forzato: {file_id}")

    file_field = doc.get("filename")
    file_name = file_field[0] if isinstance(file_field, list) and file_field else file_field
    if not file_name:
        safe_print("⚠️ Nome file mancante, salto")
        checkpoint.mark_processed(file_id, success=False)
        stats["failed"] += 1
        return stats

    if checkpoint.is_processed(file_id) and not redownload:
        safe_print(f"✅ File già scaricato: {file_id}")
        stats["skipped_done"] = 1
        return stats

    naming_mode = "italgiure" if force_portal_naming else args.naming
    portal_info = build_portal_sentenza_filename(
        doc, remote_filename=file_name, mode=naming_mode, session=worker_session,
    )

    if force_portal_naming and not portal_info.get("ok"):
        ocr_text = first_value(doc.get("ocr")) or first_value(doc.get("testoocr")) or ""
        numero = portal_info.get("numero")
        anno = portal_info.get("anno")
        if is_ocr_obscured(ocr_text) and numero and anno:
            cached_nome = (
                lookup_cache_nome_by_numero_anno(sgai_cache, str(numero), str(anno))
                if sgai_cache else None
            )
            if cached_nome and sgai_cache and sgai_cache.has(nome_base_param=cached_nome):
                safe_print(f"⏭️ OCR oscurato ma gia in cache SGAI: {cached_nome}.pdf - salto")
                checkpoint.mark_processed(file_id, success=True)
                stats["skipped_cache"] += 1
                return stats
            log_oscurato_pending(str(numero), str(anno), datdep=first_value(doc.get("datdep")) or "")
            safe_print(f"⚠️ OCR oscurato n.{numero}/{anno} - vedi {CORTE_OVERRIDE_CSV.name}")
        else:
            failure = classify_naming_failure(doc, ocr_text, portal_info, session=worker_session)
            safe_print(f"⚠️ n.{numero}/{anno} [{failure['category']}]: {failure['hint']}")
            safe_print(f"   → {failure['action']}")
            log_naming_failure(
                str(numero), str(anno), failure["category"], failure["hint"], failure["action"],
                page=page_index + 1, doc_idx=doc_index,
            )
        checkpoint.mark_processed(file_id, success=False)
        stats["failed"] += 1
        return stats

    if force_portal_naming:
        local_name = portal_info["nomeFile"]
    elif portal_info.get("ok"):
        local_name = portal_info["nomeFile"]
    else:
        local_name = build_download_filename(
            doc, remote_filename=file_name, mode=args.naming, template=args.formato_nome,
        )

    presence = check_sentenza_presence(
        doc, portal_info,
        session=worker_session, sgai_cache=sgai_cache if use_sgai_cache else None,
        sgai_live=sgai_live,
    )
    ig = presence["italgiure"]
    sg = presence["sgai"]
    safe_print(
        f"  🔎 italgiure={'SI' if ig.get('exists') else 'NO' if ig.get('exists') is False else '?'} "
        f"| SGAI({sg.get('source')})={'SI' if sg.get('exists') else 'NO' if sg.get('exists') is False else '?'} "
        f"skip={sg.get('skip')}"
    )
    if not ig.get("ok"):
        safe_print(f"     ⚠️ Solr: {ig.get('error')}")
    if sgai_live and not sg.get("ok") and sg.get("live_error"):
        safe_print(f"     ⚠️ SGAI live: {sg.get('live_error')} (fallback cache se disponibile)")

    if use_sgai_cache and portal_info.get("ok") and (sg.get("skip") or presence.get("skip_download")):
        nome_file = portal_info["nomeFile"]
        safe_print(f"⏭️ Gia su SGAI (endpoint): {nome_file}")
        checkpoint.mark_processed(file_id, success=True)
        stats["skipped_cache"] += 1
        return stats

    if not local_name:
        checkpoint.mark_processed(file_id, success=False)
        stats["failed"] += 1
        return stats

    dest_path = download_dir / local_name
    if dest_path.exists() and not checkpoint.is_processed(file_id):
        safe_print(f"📁 File già esistente ma non tracciato: {local_name}")
        checkpoint.mark_processed(file_id, success=True)
        stats["downloaded"] += 1
        return stats

    pdf_url = build_pdf_url(file_name)
    download_fn = lambda: download_pdf_with_captcha_handling(
        pdf_url, dest_path, worker_session, file_id, checkpoint,
    )
    if parallel_download:
        with _captcha_lock:
            ok = download_fn()
    else:
        ok = download_fn()

    if ok:
        stats["downloaded"] += 1
        safe_print(f"✅ Salvato: {local_name}")
    else:
        stats["failed"] += 1
    return stats


def run_exists_check_parallel(
    docs: List[Dict[str, Any]],
    *,
    workers: int,
    sgai_cache: Any,
    sgai_live: bool,
    force_portal_naming: bool,
    naming_mode: str,
) -> List[Dict[str, Any]]:
    """Verifica parallela presenza via endpoint (italgiure + SGAI)."""
    results: List[Dict[str, Any]] = []

    def _task(doc: Dict[str, Any]) -> Dict[str, Any]:
        sess = make_worker_session()
        file_field = doc.get("filename")
        file_name = file_field[0] if isinstance(file_field, list) and file_field else file_field
        portal_info = build_portal_sentenza_filename(
            doc, remote_filename=file_name or "", mode=naming_mode, session=sess,
        )
        row = check_sentenza_presence(
            doc, portal_info, session=sess, sgai_cache=sgai_cache, sgai_live=sgai_live,
        )
        row["naming_ok"] = portal_info.get("ok", False)
        row["naming_error"] = portal_info.get("error")
        return row

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_task, doc) for doc in docs]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


class PageAssigner:
    """Assegna pagine successive ai worker (W1 -> pag 16, W2 -> pag 17, ...)."""

    def __init__(self, start_page: int, max_pages: int):
        self._lock = threading.Lock()
        self._next = start_page
        self.max_pages = max_pages if max_pages > 0 else None
        self.exhausted = False

    def acquire(self) -> Optional[int]:
        with self._lock:
            if self.exhausted:
                return None
            if self.max_pages is not None and self._next >= self.max_pages:
                return None
            page = self._next
            self._next += 1
            return page

    def signal_exhausted(self) -> None:
        with self._lock:
            self.exhausted = True


def run_parallel_download_workers(
    *,
    workers: int,
    checkpoint: DownloadCheckpoint,
    download_dir: pathlib.Path,
    args: Any,
    year_filter: str,
    rows_per_page: int,
    max_pages: int,
    force_portal_naming: bool,
    use_sgai_cache: bool,
    sgai_cache: Any,
    resume_page: int,
    resume_position: int,
) -> Dict[str, int]:
    """
    Ogni worker prende una pagina alla volta in ordine crescente.
    W1 su pagina N, W2 su pagina N+1, W3 su pagina N+2, ecc.
    """
    totals = {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}
    totals_lock = threading.Lock()
    catalog_total = fetch_catalog_total(year_filter, session=make_worker_session())
    catalog_pages = (catalog_total + rows_per_page - 1) // rows_per_page if catalog_total else 0
    if max_pages > 0:
        effective_max_pages = min(max_pages, catalog_pages) if catalog_pages else max_pages
    else:
        effective_max_pages = catalog_pages
    assigner = PageAssigner(resume_page, effective_max_pages)

    if catalog_total:
        print(
            f"📚 Catalogo italgiure {year_filter}: {catalog_total} provvedimenti "
            f"(~{catalog_pages} pagine da 10)"
        )

    def _merge(stats: Dict[str, int]) -> None:
        with totals_lock:
            for k in totals:
                totals[k] += stats.get(k, 0)

    def _worker(worker_id: int) -> None:
        worker_session = make_worker_session()
        safe_print(f"👷 Worker {worker_id} avviato")

        while True:
            page = assigner.acquire()
            if page is None:
                break

            safe_print(f"👷 Worker {worker_id} → pagina {page + 1}")

            start = page * rows_per_page
            docs = fetch_documents_page(
                start=start, rows=rows_per_page, year=year_filter, session=worker_session,
            )
            if not docs:
                if catalog_total and start >= catalog_total:
                    safe_print(f"👷 Worker {worker_id}: fine catalogo ({catalog_total} doc totali)")
                    assigner.signal_exhausted()
                    break
                safe_print(
                    f"👷 Worker {worker_id}: ⚠️ pagina {page + 1} vuota dopo retry "
                    f"(start={start}) — salto pagina, continuo"
                )
                continue

            start_doc = resume_position if page == resume_page else 0
            page_stats = {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}

            for i in range(start_doc + 1, len(docs) + 1):
                doc = docs[i - 1]
                # In parallelo ogni worker possiede la pagina intera: NON usare
                # should_skip_to_position (current_page e' condiviso e un altro
                # worker su pagina N+1 farebbe saltare i doc 2..10 di pagina N).
                # Ripresa parziale solo sulla resume_page iniziale; per il resto
                # si usa is_processed() su ogni singolo file.
                if page == resume_page and (i - 1) < resume_position and not args.redownload:
                    continue
                doc_stats = process_one_document_in_page(
                    doc,
                    doc_index=i,
                    page_index=page,
                    total_in_page=len(docs),
                    checkpoint=checkpoint,
                    download_dir=download_dir,
                    args=args,
                    force_portal_naming=force_portal_naming,
                    use_sgai_cache=use_sgai_cache,
                    sgai_cache=sgai_cache,
                    redownload=args.redownload,
                    sgai_live=args.sgai_live,
                    parallel_download=True,
                )
                for k in page_stats:
                    page_stats[k] += doc_stats[k]
                time.sleep(random.uniform(2.5, 6.0))

            checkpoint.mark_page_complete(page)
            _merge(page_stats)
            total_done = (
                page_stats["downloaded"] + page_stats["failed"]
                + page_stats["skipped_cache"] + page_stats["skipped_done"]
            )
            safe_print(
                f"👷 Worker {worker_id}: pagina {page + 1} finita "
                f"({total_done}/{len(docs)} doc, +{page_stats['downloaded']} scaricati, "
                f"+{page_stats['skipped_cache']} skip cache, +{page_stats['skipped_done']} gia fatto, "
                f"+{page_stats['failed']} fail)"
            )

            if catalog_total and start + len(docs) >= catalog_total:
                safe_print(f"👷 Worker {worker_id}: ultima pagina catalogo ({catalog_total} doc)")
                assigner.signal_exhausted()
            elif len(docs) < rows_per_page:
                safe_print(
                    f"👷 Worker {worker_id}: ⚠️ pagina {page + 1} parziale "
                    f"({len(docs)}/{rows_per_page} doc) — continuo comunque"
                )

            time.sleep(random.uniform(7.0, 15.0))

        safe_print(f"👷 Worker {worker_id} terminato")

    safe_print(f"⚡ Avvio {workers} worker a pagine successive (da pagina {resume_page + 1})")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, wid + 1) for wid in range(workers)]
        for fut in as_completed(futures):
            fut.result()

    return totals


def _load_oscurati_pending(year: str) -> List[Tuple[str, str, str]]:
    """(numero, anno, datdep) da oscurati_pending.csv."""
    import csv

    rows: List[Tuple[str, str, str]] = []
    if not OSCURATI_LOG_CSV.exists():
        return rows
    with OSCURATI_LOG_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            numero = (row.get("numero") or "").strip()
            anno = (row.get("anno") or year).strip()
            if not numero or not anno or anno != year:
                continue
            rows.append((numero, anno, (row.get("datdep") or "").strip()))
    return rows


def _pdf_exists_for_numero_anno(download_dir: pathlib.Path, numero: str, anno: str) -> Optional[str]:
    """True se esiste Sentenza_*_NUMERO_ANNO.pdf in downloads."""
    if not download_dir.is_dir():
        return None
    suffix = f"_{numero}_{anno}.pdf".lower()
    for path in download_dir.glob("*.pdf"):
        if path.name.lower().endswith(suffix):
            return path.name
    return None


def list_oscurati_pending(download_dir: pathlib.Path, year: str) -> int:
    """Stampa provvedimenti oscurati ancora senza PDF in downloads/."""
    pending = _load_oscurati_pending(year)
    overrides = load_corte_overrides()

    if not pending:
        print(f"📭 {OSCURATI_LOG_CSV.name}: nessun provvedimento in sospeso")
        return 0

    missing: List[Tuple[str, str, str, bool]] = []
    found = 0
    for numero, anno, datdep in pending:
        fname = _pdf_exists_for_numero_anno(download_dir, numero, anno)
        has_override = (numero, anno) in overrides
        if fname:
            found += 1
            print(f"✅ n.{numero}/{anno} -> {fname}")
        else:
            missing.append((numero, anno, datdep, has_override))

    print()
    print(f"📋 {OSCURATI_LOG_CSV.name}: {len(pending)} totali | {found} gia scaricati | {len(missing)} mancanti")
    if missing:
        print()
        print("❌ NON SCARICATI (da sistemare in corte_override.csv):")
        for numero, anno, datdep, has_override in missing:
            flag = " [override OK]" if has_override else ""
            dep = f"  deposito {datdep}" if datdep else ""
            print(f"   {numero},{anno},CGT ...{flag}{dep}")

    return len(missing)


def _load_pending_numeros(year: str) -> List[Tuple[str, str, str]]:
    """(numero, anno, fonte) da oscurati_pending e naming_issues."""
    import csv

    seen: set[Tuple[str, str]] = set()
    pending: List[Tuple[str, str, str]] = []
    for csv_path, label in ((OSCURATI_LOG_CSV, "oscurato"), (NAMING_ISSUES_CSV, "naming")):
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                numero = (row.get("numero") or "").strip()
                anno = (row.get("anno") or year).strip()
                if not numero or not anno or anno != year:
                    continue
                key = (numero, anno)
                if key in seen:
                    continue
                seen.add(key)
                pending.append((numero, anno, label))
    return pending


def run_retry_oscurati(
    *,
    checkpoint: DownloadCheckpoint,
    download_dir: pathlib.Path,
    args: Any,
    year_filter: str,
    force_portal_naming: bool,
    use_sgai_cache: bool,
    sgai_cache: Any,
    session: requests.Session,
    only_with_override: bool = False,
) -> Dict[str, int]:
    """Riprova solo numeri in oscurati_pending.csv."""
    overrides = load_corte_overrides()
    pending = _load_oscurati_pending(year_filter)
    totals = {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}

    if not pending:
        print(f"📭 Nessun provvedimento in {OSCURATI_LOG_CSV.name}")
        return totals

    print(f"🔁 Retry oscurati: {len(pending)} numeri da {OSCURATI_LOG_CSV.name}")
    if only_with_override:
        print("   (solo quelli con riga in corte_override.csv)")

    for numero, anno, _datdep in pending:
        if only_with_override and (numero, anno) not in overrides:
            continue
        if _pdf_exists_for_numero_anno(download_dir, numero, anno):
            safe_print(f"⏭️ n.{numero}/{anno}: PDF gia presente in downloads/")
            continue
        doc = fetch_document_by_numero(numero, anno, session=session)
        if not doc:
            safe_print(f"⚠️ n.{numero}/{anno}: non trovato su italgiure")
            totals["failed"] += 1
            continue
        safe_print(f"\n🔁 Retry n.{numero}/{anno}")
        stats = process_single_document(
            doc,
            checkpoint=checkpoint,
            session=session,
            download_dir=download_dir,
            args=args,
            force_portal_naming=force_portal_naming,
            use_sgai_cache=use_sgai_cache,
            sgai_cache=sgai_cache,
            redownload=True,
            page_label="retry-oscurato",
            doc_label="1/1",
        )
        for k in totals:
            totals[k] += stats[k]

    return totals


def run_retry_pending(
    *,
    checkpoint: DownloadCheckpoint,
    download_dir: pathlib.Path,
    args: Any,
    year_filter: str,
    force_portal_naming: bool,
    use_sgai_cache: bool,
    sgai_cache: Any,
    session: requests.Session,
    only_with_override: bool = False,
) -> Dict[str, int]:
    """Riprova numeri segnalati in oscurati_pending / naming_issues."""
    overrides = load_corte_overrides()
    pending = _load_pending_numeros(year_filter)
    totals = {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}

    if not pending:
        print("📭 Nessun provvedimento in sospeso nei CSV di log")
        return totals

    print(f"🔁 Retry pending: {len(pending)} numeri da {OSCURATI_LOG_CSV.name} / {NAMING_ISSUES_CSV.name}")
    if only_with_override:
        print("   (solo quelli con riga in corte_override.csv)")

    for numero, anno, source in pending:
        if only_with_override and (numero, anno) not in overrides:
            continue
        doc = fetch_document_by_numero(numero, anno, session=session)
        if not doc:
            safe_print(f"⚠️ n.{numero}/{anno} ({source}): non trovato su italgiure")
            totals["failed"] += 1
            continue
        safe_print(f"\n🔁 Retry n.{numero}/{anno} ({source})")
        stats = process_single_document(
            doc,
            checkpoint=checkpoint,
            session=session,
            download_dir=download_dir,
            args=args,
            force_portal_naming=force_portal_naming,
            use_sgai_cache=use_sgai_cache,
            sgai_cache=sgai_cache,
            redownload=True,
            page_label=f"retry-{source}",
            doc_label="1/1",
        )
        for k in totals:
            totals[k] += stats[k]

    return totals


def process_single_document(
    doc: Dict[str, Any],
    *,
    checkpoint: DownloadCheckpoint,
    session: requests.Session,
    download_dir: pathlib.Path,
    args: Any,
    force_portal_naming: bool,
    use_sgai_cache: bool,
    sgai_cache: Any,
    redownload: bool,
    page_label: str,
    doc_label: str,
) -> Dict[str, int]:
    stats = {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}
    file_id = get_file_id(doc)
    file_field = doc.get("filename")
    file_name = file_field[0] if isinstance(file_field, list) and file_field else file_field

    print(f"\n📋 Documento {doc_label} ({page_label})")
    if redownload:
        checkpoint.unmark_file(file_id)
        print(f"  🔄 Redownload forzato: {file_id}")

    if not file_name:
        print("⚠️ Nome file mancante, salto")
        checkpoint.mark_processed(file_id, success=False)
        stats["failed"] += 1
        return stats

    analysis = analyze_document_naming(
        doc,
        force_portal_naming=force_portal_naming,
        sgai_cache=sgai_cache if use_sgai_cache else None,
        naming_mode="italgiure" if force_portal_naming else args.naming,
        formato_nome=args.formato_nome,
        session=session,
    )

    if analysis["status"] == "CACHE" and not redownload:
        print(f"⏭️ {analysis['error']}")
        checkpoint.mark_processed(file_id, success=True)
        stats["skipped_cache"] += 1
        return stats

    if analysis["status"] in ("FAIL", "OSCURATO"):
        print(f"⚠️ {analysis['error']}")
        if analysis.get("category"):
            print(f"   Categoria: {analysis['category']}")
        checkpoint.mark_processed(file_id, success=False)
        stats["failed"] += 1
        return stats

    local_name = analysis["nomeFile"]
    if analysis.get("error") and analysis["status"] == "OK":
        print(f"  ℹ️ {analysis['error']}")

    dest_path = download_dir / local_name
    pdf_url = build_pdf_url(file_name)
    if download_pdf_with_captcha_handling(pdf_url, dest_path, session, file_id, checkpoint):
        stats["downloaded"] += 1
        print(f"✅ Salvato: {local_name}")
    else:
        stats["failed"] += 1
    return stats


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description='Cassazione PDF Downloader con gestione captcha')
    parser.add_argument(
        '--reset',
        action='store_true',
        help='Reset checkpoint + processed_files.json (ricomincia da capo)',
    )
    parser.add_argument(
        '--reset-checkpoint-only',
        action='store_true',
        help='Reset solo posizione pagina (conserva processed_files.json e skip gia scaricati)',
    )
    parser.add_argument(
        '--from-page',
        type=int,
        default=0,
        help='Parti da questa pagina (1-based). Utile per recuperare buchi senza reset totale',
    )
    parser.add_argument(
        '--to-page',
        type=int,
        default=0,
        help='Fermati a questa pagina inclusa (1-based). Usa con --from-page',
    )
    parser.add_argument(
        '--list-oscurati',
        action='store_true',
        help='Elenca provvedimenti in oscurati_pending.csv senza PDF in downloads/',
    )
    parser.add_argument(
        '--retry-oscurati',
        action='store_true',
        help='Riprova solo numeri in oscurati_pending.csv (con --retry-oscurati-overrides-only: solo se hanno override)',
    )
    parser.add_argument(
        '--retry-oscurati-overrides-only',
        action='store_true',
        help='Con --retry-oscurati: solo numeri con riga in corte_override.csv',
    )
    parser.add_argument(
        '--retry-pending',
        action='store_true',
        help='Riprova numeri in oscurati_pending.csv / naming_issues.csv',
    )
    parser.add_argument(
        '--retry-pending-overrides-only',
        action='store_true',
        help='Con --retry-pending: solo numeri con riga in corte_override.csv',
    )
    parser.add_argument('--pages', type=int, default=-1, help='Numero di pagine da scaricare (-1 = tutte, default)')
    parser.add_argument('--year', type=str, default='2025', help='Anno da filtrare (default: 2025)')
    parser.add_argument(
        '--naming',
        choices=('italgiure', 'completo', 'semplice', 'sgai'),
        default=NAMING_MODE,
        help=(
            'Formato nome file: italgiure (default, OCR tribunale impugnato), '
            'completo (solo CGT N° Regione), semplice (datdep_numero-anno), '
            'sgai (Sentenza_CODICE_NUMERO_ANNO.pdf)'
        ),
    )
    parser.add_argument(
        '--check-sgai-cache',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Controlla cache SGAI prima del download (default: attivo per anno 2025)',
    )
    parser.add_argument(
        '--cache-dir',
        default=str(SGAI_DEFAULT_CACHE_DIR),
        help='Directory cache SGAI (mia_cache)',
    )
    parser.add_argument(
        '--sync-cache',
        action='store_true',
        help='Sincronizza manifest cache SGAI all\'avvio',
    )
    parser.add_argument(
        '--formato-nome',
        default=DOWNLOAD_FILENAME_TEMPLATE,
        help='Template nome file con {datdep},{cgtn},{regione},{numero},{anno}',
    )
    parser.add_argument('--debug', action='store_true', help='Stampa metadati del primo documento')
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Controlla naming su N pagine senza scaricare (usa con --check-pages)',
    )
    parser.add_argument(
        '--check-pages',
        type=int,
        default=10,
        help='Pagine da validare in --check-only (default: 10)',
    )
    parser.add_argument(
        '--preflight-ahead',
        type=int,
        default=PREFLIGHT_AHEAD_DEFAULT,
        help=(
            'All avvio download, scansiona N pagine avanti e segnala problemi '
            f'(default: {PREFLIGHT_AHEAD_DEFAULT}, 0=disattiva)'
        ),
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=DEFAULT_WORKERS,
        help='Worker paralleli per check/download (default: 1 = sequenziale)',
    )
    parser.add_argument(
        '--sgai-live',
        action='store_true',
        help='Verifica presenza su SGAI via endpoint /v1/admin/sentenze-check (fallback cache)',
    )
    parser.add_argument(
        '--wake-sgai',
        action='store_true',
        help='Prima dei check live, invia wake-up EC2 SGAI',
    )
    parser.add_argument(
        '--exists-check',
        action='store_true',
        help='Solo verifica parallela presenza (italgiure Solr + SGAI), niente download',
    )
    parser.add_argument(
        '--at-page',
        type=int,
        default=0,
        help='Scarica solo questa pagina (1-based, es. 8 = pagina 8)',
    )
    parser.add_argument(
        '--at-doc',
        type=int,
        default=0,
        help='Con --at-page: solo questo documento nella pagina (1-based, es. 7)',
    )
    parser.add_argument(
        '--numero',
        type=str,
        default='',
        help='Scarica/riscarica una sentenza specifica per numero provvedimento (es. 34997)',
    )
    parser.add_argument(
        '--redownload',
        action='store_true',
        help='Con --at-page/--at-doc/--numero: ignora checkpoint e riscarica',
    )
    args = parser.parse_args()

    if args.reset and args.reset_checkpoint_only:
        print("❌ Usa --reset oppure --reset-checkpoint-only, non entrambi")
        return

    checkpoint = DownloadCheckpoint()
    if args.reset:
        checkpoint.reset_checkpoint(keep_processed=False)
        checkpoint = DownloadCheckpoint()  # Reload fresh
    elif args.reset_checkpoint_only:
        checkpoint.reset_checkpoint(keep_processed=True)
        checkpoint = DownloadCheckpoint()

    download_dir = pathlib.Path("downloads")
    rows_per_page = 10
    MAX_PAGES = args.pages
    year_filter = args.year
    use_sgai_cache = (
        args.check_sgai_cache
        if args.check_sgai_cache is not None
        else should_use_sgai_cache(year_filter)
    )
    force_portal_naming = use_sgai_cache or args.naming == "sgai"
    sgai_cache = None
    skipped_sgai_cache = 0
    targeted_run = bool(args.at_page or args.numero)

    if force_portal_naming:
        if not SGAI_PACKAGE_DIR.is_dir():
            print(f"❌ Pacchetto SGAI non trovato: {SGAI_PACKAGE_DIR}")
            return
        if not verify_portal_codici():
            print("❌ Correggere codici_corte.json prima di continuare.")
            return
        overrides = load_corte_overrides()
        if overrides:
            print(f"📎 Override corte caricati: {len(overrides)} da {CORTE_OVERRIDE_CSV.name}")

    if use_sgai_cache or force_portal_naming:
        try:
            sgai_cache = get_sgai_cache(pathlib.Path(args.cache_dir))
            if args.sync_cache and not args.check_only:
                print("🔄 Sincronizzazione cache SGAI...")
                sgai_cache.sync()
        except Exception as exc:
            print(f"⚠️ Cache SGAI non disponibile: {exc}")
            if use_sgai_cache and not args.check_only:
                print("❌ Impossibile continuare senza cache SGAI.")
                return
            sgai_cache = None

    session = requests.Session()
    session.headers.update({
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': USER_AGENTS[user_agent_idx]
    })

    if args.check_only:
        pages = args.check_pages if args.check_pages > 0 else MAX_PAGES
        if pages <= 0:
            pages = 5
        code = run_preflight_check(
            year=year_filter,
            pages=pages,
            session=session,
            force_portal_naming=force_portal_naming,
            sgai_cache=sgai_cache,
            naming_mode=args.naming,
            formato_nome=args.formato_nome,
            rows_per_page=rows_per_page,
        )
        raise SystemExit(code)

    if args.wake_sgai or args.sgai_live:
        print("⏰ Wake SGAI EC2...")
        wake_result = wake_sgai_server()
        print(f"   wake: {wake_result}")

    if args.exists_check:
        pages = args.check_pages if args.check_pages > 0 else 1
        naming_mode = "italgiure" if force_portal_naming else args.naming
        print(f"\n🔎 EXISTS-CHECK parallelo ({args.workers} workers, sgai_live={args.sgai_live})")
        print("=" * 70)
        for page in range(pages):
            start = page * rows_per_page
            docs = list(fetch_documents(start=start, rows=rows_per_page, year=year_filter, session=session))
            if not docs:
                break
            print(f"\nPagina {page + 1}: {len(docs)} documenti")
            rows = run_exists_check_parallel(
                docs,
                workers=args.workers,
                sgai_cache=sgai_cache,
                sgai_live=args.sgai_live,
                force_portal_naming=force_portal_naming,
                naming_mode=naming_mode,
            )
            for row in sorted(rows, key=lambda r: r.get("numero") or ""):
                ig = row["italgiure"]
                sg = row["sgai"]
                nome = row.get("nomeFile") or "?"
                print(
                    f"  n.{row.get('numero')}/{row.get('anno')} "
                    f"italgiure={'SI' if ig.get('exists') else 'NO'} "
                    f"sgai({sg.get('source')})={'SI' if sg.get('exists') else 'NO'} "
                    f"skip={sg.get('skip')} -> {nome}"
                )
                if not ig.get("ok"):
                    print(f"      Solr err: {ig.get('error')}")
                if args.sgai_live and sg.get("live_error"):
                    print(f"      SGAI live err: {sg.get('live_error')}")
        raise SystemExit(0)

    if args.list_oscurati:
        code = list_oscurati_pending(download_dir, year_filter)
        raise SystemExit(0 if code == 0 else 1)

    if args.retry_oscurati:
        server_thread = threading.Thread(target=start_captcha_server, daemon=True)
        server_thread.start()
        time.sleep(2)
        totals = run_retry_oscurati(
            checkpoint=checkpoint,
            download_dir=download_dir,
            args=args,
            year_filter=year_filter,
            force_portal_naming=force_portal_naming,
            use_sgai_cache=use_sgai_cache,
            sgai_cache=sgai_cache,
            session=session,
            only_with_override=args.retry_oscurati_overrides_only,
        )
        print(
            f"\n🏁 Retry oscurati: scaricati={totals['downloaded']} "
            f"skip={totals['skipped_cache']} falliti={totals['failed']}"
        )
        checkpoint.save_checkpoint()
        checkpoint.save_processed_files()
        return

    if args.retry_pending:
        server_thread = threading.Thread(target=start_captcha_server, daemon=True)
        server_thread.start()
        time.sleep(2)
        totals = run_retry_pending(
            checkpoint=checkpoint,
            download_dir=download_dir,
            args=args,
            year_filter=year_filter,
            force_portal_naming=force_portal_naming,
            use_sgai_cache=use_sgai_cache,
            sgai_cache=sgai_cache,
            session=session,
            only_with_override=args.retry_pending_overrides_only,
        )
        print(
            f"\n🏁 Retry pending: scaricati={totals['downloaded']} "
            f"skip={totals['skipped_cache']} falliti={totals['failed']}"
        )
        checkpoint.save_checkpoint()
        checkpoint.save_processed_files()
        return

    if args.numero:
        doc = fetch_document_by_numero(args.numero, year_filter, session=session)
        if not doc:
            print(f"❌ Sentenza n.{args.numero}/{year_filter} non trovata su italgiure")
            return
        server_thread = threading.Thread(target=start_captcha_server, daemon=True)
        server_thread.start()
        time.sleep(2)
        stats = process_single_document(
            doc,
            checkpoint=checkpoint,
            session=session,
            download_dir=download_dir,
            args=args,
            force_portal_naming=force_portal_naming,
            use_sgai_cache=use_sgai_cache,
            sgai_cache=sgai_cache,
            redownload=args.redownload,
            page_label=f"numero {args.numero}",
            doc_label="1/1",
        )
        print(f"\n🏁 Fatto: scaricati={stats['downloaded']} falliti={stats['failed']}")
        return

    if args.from_page:
        if args.from_page < 1:
            print("❌ --from-page deve essere >= 1")
            return
        checkpoint.set_position(args.from_page - 1, 0)

    if args.to_page:
        if args.to_page < 1:
            print("❌ --to-page deve essere >= 1")
            return
        if args.from_page and args.to_page < args.from_page:
            print("❌ --to-page deve essere >= --from-page")
            return
        MAX_PAGES = args.to_page

    if args.at_page:
        if args.at_page < 1:
            print("❌ --at-page deve essere >= 1")
            return
        page_index = args.at_page - 1
        checkpoint.set_position(page_index, 0 if not args.at_doc else args.at_doc - 1)
        MAX_PAGES = page_index + 1
        if args.at_doc and args.at_doc < 1:
            print("❌ --at-doc deve essere >= 1")
            return

    # Start captcha server in background
    server_thread = threading.Thread(target=start_captcha_server, daemon=True)
    server_thread.start()
    time.sleep(2)

    print("🏛️ CASSAZIONE PDF DOWNLOADER CON GESTIONE CAPTCHA E CHECKPOINT")
    print("=" * 70)
    print(f"📁 Directory download: {download_dir.absolute()}")
    print(f"📅 Anno filtro: {year_filter}")
    print(f"📄 Pagine da scaricare: {MAX_PAGES}")
    print(f"🏷️ Naming: {args.naming} | template: {args.formato_nome}")
    if force_portal_naming:
        print("📛 Salvataggio: Sentenza_CODICE_NUMERO_ANNO.pdf (codici_corte.json)")
    if use_sgai_cache:
        print(f"🗄️ Cache SGAI: {args.cache_dir} (skip se gia presente)")
    if args.workers > 1:
        print(f"⚡ Worker paralleli: {args.workers} (ognuno su pagine successive)")
    if args.sgai_live:
        print(f"🌐 Check SGAI live: {SGAI_API_BASE}{SGAI_CHECK_PATH}")
    print(f"🌐 Server captcha: http://localhost:{server_port}")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"📱 Da cellulare: http://{local_ip}:{server_port}")
    except Exception:
        pass
    print(f"💾 Checkpoint: pagina {checkpoint.current_page + 1}, documento {checkpoint.current_position + 1}")
    print(f"📊 File già processati: {len(checkpoint.processed_files)} scaricati, {len(checkpoint.failed_files)} falliti")
    catalog_total = fetch_catalog_total(year_filter, session=session)
    if catalog_total:
        catalog_pages = (catalog_total + rows_per_page - 1) // rows_per_page
        approx_done = len(checkpoint.processed_files)
        print(
            f"📚 Catalogo italgiure {year_filter} (Cass. trib.): {catalog_total} provvedimenti "
            f"(~{catalog_pages} pagine) | tracciati: {approx_done} | ~mancanti: {max(0, catalog_total - approx_done)}"
        )
        print("   (Il DB MEF ~208k include TUTTE le CGT; qui solo Cassazione sez. tributaria)")
    if targeted_run:
        print("🎯 Modalita mirata: niente shuffle, solo target richiesto")

    if (
        force_portal_naming
        and args.preflight_ahead > 0
        and not targeted_run
        and MAX_PAGES != 0
    ):
        print(
            f"\n🔎 Scan preventivo: {args.preflight_ahead} pagine "
            f"da pagina {checkpoint.current_page + 1} (non blocca il download)"
        )
        run_preflight_check(
            year=year_filter,
            pages=args.preflight_ahead,
            session=session,
            force_portal_naming=force_portal_naming,
            sgai_cache=sgai_cache,
            naming_mode=args.naming,
            formato_nome=args.formato_nome,
            rows_per_page=rows_per_page,
            start_page=checkpoint.current_page,
            verbose=True,
        )
        print(f"   Problemi salvati in {NAMING_ISSUES_CSV.name} / {CORTE_OVERRIDE_CSV.name}\n")

    session_downloads = 0
    session_failed = 0
    download_since_session_reset = 0

    try:
        if args.workers > 1 and not targeted_run:
            totals = run_parallel_download_workers(
                workers=args.workers,
                checkpoint=checkpoint,
                download_dir=download_dir,
                args=args,
                year_filter=year_filter,
                rows_per_page=rows_per_page,
                max_pages=MAX_PAGES,
                force_portal_naming=force_portal_naming,
                use_sgai_cache=use_sgai_cache,
                sgai_cache=sgai_cache,
                resume_page=checkpoint.current_page,
                resume_position=checkpoint.current_position,
            )
            session_downloads = totals["downloaded"]
            session_failed = totals["failed"]
            skipped_sgai_cache += totals["skipped_cache"]
        else:
            page = checkpoint.current_page
            seq_catalog_total = catalog_total or fetch_catalog_total(year_filter, session=session)
            while True:
                if MAX_PAGES > 0 and page >= MAX_PAGES:
                    print(f"✅ Raggiunto il limite di pagine richiesto: {MAX_PAGES}")
                    break

                print(f"\n📄 Processando pagina {page + 1}{f' su {MAX_PAGES}' if MAX_PAGES > 0 else ''}")

                if checkpoint.should_skip_to_position(page, 0):
                    print(f"⏭️ Saltando pagina {page + 1} (già processata)")
                    page += 1
                    continue
                start = page * rows_per_page
                docs = fetch_documents_page(
                    start=start, rows=rows_per_page, year=year_filter, session=session,
                )
                if not docs:
                    if seq_catalog_total and start >= seq_catalog_total:
                        print(f"📭 Fine catalogo ({seq_catalog_total} provvedimenti).")
                    else:
                        print(f"⚠️ Pagina {page + 1} vuota dopo retry (start={start}), salto.")
                        page += 1
                        continue
                    break

                if not targeted_run and random.random() < 0.18:
                    random.shuffle(docs)

                target_doc_only = args.at_page and args.at_doc
                doc_range = [args.at_doc] if target_doc_only else list(range(1, len(docs) + 1))

                def _process_doc_index(i: int) -> Dict[str, int]:
                    if i < 1 or i > len(docs):
                        safe_print(f"❌ Documento {i} non esiste in pagina {page + 1} (max {len(docs)})")
                        return {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}
                    doc = docs[i - 1]
                    if checkpoint.should_skip_to_position(page, i - 1) and not args.redownload:
                        safe_print(f"⏭️ Saltando documento {i} (già processato)")
                        return {"downloaded": 0, "failed": 0, "skipped_cache": 0, "skipped_done": 0}
                    if target_doc_only:
                        return process_single_document(
                            doc,
                            checkpoint=checkpoint,
                            session=session,
                            download_dir=download_dir,
                            args=args,
                            force_portal_naming=force_portal_naming,
                            use_sgai_cache=use_sgai_cache,
                            sgai_cache=sgai_cache,
                            redownload=args.redownload,
                            page_label=f"pagina {page + 1}",
                            doc_label=f"{i}/{len(docs)}",
                        )
                    checkpoint.update_position(page, i - 1)
                    if args.debug and page == checkpoint.current_page and i == 1:
                        file_field = doc.get("filename")
                        file_name = file_field[0] if isinstance(file_field, list) and file_field else file_field
                        safe_print('--- DEBUG NAMING ---')
                        portal_info = build_portal_sentenza_filename(
                            doc, remote_filename=file_name, mode="italgiure", session=session,
                        )
                        safe_print('filename tecnico italgiure (IGNORATO per salvataggio):', file_name)
                        safe_print('corte portale:', portal_info.get("cortePortale"))
                        safe_print('numero:', portal_info.get("numero"), 'anno:', portal_info.get("anno"))
                        safe_print('codice:', portal_info.get("codice"))
                        safe_print('nome file finale:', portal_info.get("nomeFile"))
                        if portal_info.get("error"):
                            safe_print('errore:', portal_info.get("error"))
                        safe_print('--- FINE DEBUG ---')
                    return process_one_document_in_page(
                        doc,
                        doc_index=i,
                        page_index=page,
                        total_in_page=len(docs),
                        checkpoint=checkpoint,
                        download_dir=download_dir,
                        args=args,
                        force_portal_naming=force_portal_naming,
                        use_sgai_cache=use_sgai_cache,
                        sgai_cache=sgai_cache,
                        redownload=args.redownload,
                        sgai_live=args.sgai_live,
                        parallel_download=False,
                    )

                for i in doc_range:
                    doc_stats = _process_doc_index(i)
                    session_downloads += doc_stats["downloaded"]
                    session_failed += doc_stats["failed"]
                    skipped_sgai_cache += doc_stats["skipped_cache"]
                    if target_doc_only:
                        checkpoint.update_position(page, i - 1)
                        break
                    time.sleep(random.uniform(2.5, 6.0))
                if target_doc_only:
                    break
                checkpoint.update_position(page + 1, 0)
                if seq_catalog_total and start + len(docs) >= seq_catalog_total:
                    print(f"📄 Ultima pagina catalogo ({seq_catalog_total} provvedimenti).")
                    break
                if len(docs) < rows_per_page:
                    print(
                        f"⚠️ Pagina {page + 1} parziale ({len(docs)}/{rows_per_page} doc) — continuo"
                    )
                page += 1
                time.sleep(random.uniform(7.0, 15.0))
    except KeyboardInterrupt:
        print("\n⏹️ Download interrotto dall'utente")

    print(f"\n🏁 COMPLETATO!")
    print(f"📊 STATISTICHE TOTALI:")
    print(f"   ✅ File scaricati (totale): {checkpoint.total_downloaded}")
    print(f"   ❌ File falliti (totale): {checkpoint.total_failed}")
    print(f"📊 STATISTICHE SESSIONE:")
    print(f"   ✅ File scaricati (sessione): {session_downloads}")
    if skipped_sgai_cache:
        print(f"   ⏭️ Saltati (gia in cache SGAI): {skipped_sgai_cache}")
    print(f"   ❌ File falliti (sessione): {session_failed}")
    print(f"📁 Directory: {download_dir.absolute()}")
    print(f"💾 Checkpoint salvato in: {checkpoint.checkpoint_file}")
    print(f"📋 Log file: {checkpoint.log_file}")

    checkpoint.save_checkpoint()
    checkpoint.save_processed_files()

if __name__ == "__main__":
    main()