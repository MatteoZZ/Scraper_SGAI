#!/usr/bin/env python3
"""
Portale MEF (2026) -> nome file SGAI standard.

Il portale nuovo espone i dati in tabella HTML:
  td[0] = tipo       (es. Sentenza)
  td[1] = numero     (es. 1205)
  td[2] = anno       (es. 2026)
  td[3] = corte      (es. CGT 2° Lombardia)

I link hanno title tipo:
  Visualizza provvedimento n. 1205/2026 CGT 2° Lombardia

Il PDF scaricato puo avere nome gibberish: usare sempre questi metadati
per costruire il nome con cui noi salviamo i file:
  Sentenza_V70_1205_2026.pdf

Uso:
  python portal_to_filename.py --corte "CGT 2° Lombardia" --numero 1205 --anno 2026
  python portal_to_filename.py --title "Visualizza provvedimento n. 1205/2026 CGT 2° Lombardia"
  python portal_to_filename.py --test-esempi
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CODICI_PATH = Path(__file__).resolve().parent / "data" / "codici_corte.json"
CODICI_CORTE: dict[str, str] = {}

# Estrae "n. 1205/2026 CGT 2° Lombardia" da qualsiasi title portale 2026/legacy
PORTAL_PROV_RE = re.compile(
    r"n\.?\s*(\d+)\s*/\s*(\d{4})\s+CGT\s*(1|2)\s*[°º]?\s*(.+)$",
    re.IGNORECASE,
)

# Esempi reali dal portale aggiornato (luglio 2026)
ESEMPI_PORTALE = [
    ("1205", "2026", "CGT 2° Lombardia", "V70", "Sentenza_V70_1205_2026.pdf"),
    ("1489", "2026", "CGT 2° Puglia", "Z31", "Sentenza_Z31_1489_2026.pdf"),
    ("2686", "2026", "CGT 2° Lazio", "Z18", "Sentenza_Z18_2686_2026.pdf"),
    ("3338", "2026", "CGT 2° Sicilia", "Z46", "Sentenza_Z46_3338_2026.pdf"),
    ("386", "2026", "CGT 1° Bologna", "U55", "Sentenza_U55_386_2026.pdf"),
    ("13747", "2021", "CGT 1° Napoli", "V10", "Sentenza_V10_13747_2021.pdf"),
]


def _load_codici() -> None:
    global CODICI_CORTE
    if CODICI_CORTE:
        return
    data = json.loads(CODICI_PATH.read_text(encoding="utf-8"))
    CODICI_CORTE = data.get("corteToCodice") or {}


def _normalize_place(place: str) -> str:
    """Normalizza sede corte: spazi e trattini → underscore (Emilia-Romagna → EMILIA_ROMAGNA)."""
    text = (place or "").strip().upper()
    text = text.replace("Â°", "").replace("°", "")
    text = text.replace("'", "'").replace("'", "'")
    text = text.replace("-", "_")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def corte_portale_to_codice(corte_portale: str) -> str | None:
    """CGT 2° Lombardia -> V70, CGT 1° Bologna -> U55, ecc."""
    _load_codici()
    corte = (corte_portale or "").replace("Â°", "°").strip()
    m = re.match(r"CGT\s*(1|2)[°º]?\s+(.+)", corte, re.IGNORECASE)
    if not m:
        return None
    key = f"{m.group(1)}°_{_normalize_place(m.group(2))}"
    if key in CODICI_CORTE:
        return CODICI_CORTE[key]
    for corte_key, codice in CODICI_CORTE.items():
        if corte_key.upper() == key.upper():
            return codice
    return None


def codice_to_corte_label(codice: str) -> dict | None:
    _load_codici()
    codice = (codice or "").upper()
    for corte_key, code in CODICI_CORTE.items():
        if code.upper() == codice:
            grado, *rest = corte_key.split("_", 1)
            return {
                "codice": code,
                "grado": grado,
                "denominazione": rest[0].replace("_", " ") if rest else "",
                "cortePortale": f"CGT {grado} {rest[0].replace('_', ' ') if rest else ''}".strip(),
            }
    return None


def normalize_tipo(tipo: str | None) -> str:
    """Tipo provvedimento → token filename (Sentenza, Ordinanza, Decreto, ...)."""
    raw = (tipo or "Sentenza").strip()
    if not raw:
        return "Sentenza"
    # rimuovi caratteri non sicuri per nome file
    cleaned = re.sub(r"[^\wÀ-ÿ]+", "_", raw, flags=re.UNICODE).strip("_")
    if not cleaned:
        return "Sentenza"
    # Title-case semplice: Sentenza / Ordinanza
    parts = [p for p in cleaned.split("_") if p]
    return "_".join(p[:1].upper() + p[1:].lower() for p in parts)


def build_filename(
    corte_portale: str,
    numero: str | int,
    anno: str | int,
    tipo: str = "Sentenza",
) -> dict:
    codice = corte_portale_to_codice(corte_portale)
    tipo_norm = normalize_tipo(tipo)
    if not codice:
        return {
            "ok": False,
            "error": f"Codice corte non trovato per: {corte_portale}",
            "cortePortale": corte_portale,
            "numero": str(numero),
            "anno": str(anno),
            "tipo": tipo_norm,
        }
    base = f"{tipo_norm}_{codice}_{numero}_{anno}"
    return {
        "ok": True,
        "tipo": tipo_norm,
        "codice": codice,
        "numero": str(numero),
        "anno": str(anno),
        "cortePortale": corte_portale,
        "nomeBase": base,
        "nomeFile": f"{base}.pdf",
        "corte": codice_to_corte_label(codice),
    }


def parse_portal_title(title: str) -> dict | None:
    """
    Supporta title portale nuovo e legacy:
      Visualizza provvedimento n. 1205/2026 CGT 2° Lombardia
      Visualizza sommario automatico del provvedimento n. 1205/2026 CGT 2° Puglia
      Scarica il pdf della sentenza n. 13747/2021 CGT 1° Napoli
    """
    raw = (title or "").strip()
    if not raw:
        return None
    m = PORTAL_PROV_RE.search(raw)
    if not m:
        return None
    numero, anno, grado, luogo = m.group(1), m.group(2), m.group(3), m.group(4).strip()
    corte = f"CGT {grado}° {luogo}"
    result = build_filename(corte, numero, anno)
    result["portalTitle"] = raw
    return result


def parse_portal_row(
    numero: str | int,
    anno: str | int,
    corte_portale: str,
    tipo: str = "Sentenza",
) -> dict:
    """
    Da riga tabella portale 2026:
      celle: [tipo, numero, anno, corte, data, importo, ...]
    """
    return build_filename(corte_portale, numero, anno, tipo=tipo)


def parse_portal_html_row(cells: list[str]) -> dict | None:
    """cells = testi delle prime 4 td della riga tabella."""
    if len(cells) < 4:
        return None
    tipo, numero, anno, corte = [c.strip() for c in cells[:4]]
    if not numero or not anno or not corte:
        return None
    return parse_portal_row(numero, anno, corte, tipo=tipo or "Sentenza")


def run_test_esempi() -> int:
    ok = 0
    fail = 0
    print("Test mappatura portale -> nome file SGAI\n")
    for numero, anno, corte, codice_atteso, file_atteso in ESEMPI_PORTALE:
        result = build_filename(corte, numero, anno)
        passed = (
            result.get("ok")
            and result.get("codice") == codice_atteso
            and result.get("nomeFile") == file_atteso
        )
        status = "OK" if passed else "FAIL"
        if passed:
            ok += 1
        else:
            fail += 1
        print(f"[{status}] {corte} n.{numero}/{anno}")
        print(f"       atteso: {codice_atteso} -> {file_atteso}")
        print(f"       ottenuto: {result.get('codice')} -> {result.get('nomeFile')}")
        if not passed:
            print(f"       errore: {result.get('error', '')}")
        print()
    print(f"Risultato: {ok} OK, {fail} FAIL")
    return 0 if fail == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Portale MEF 2026 -> nome file SGAI")
    parser.add_argument("--title", default="", help="Attributo title del link in tabella")
    parser.add_argument("--corte", default="", help='Es. "CGT 2° Lombardia"')
    parser.add_argument("--numero", default="")
    parser.add_argument("--anno", default="")
    parser.add_argument("--tipo", default="Sentenza")
    parser.add_argument("--test-esempi", action="store_true", help="Verifica mappatura prefissi")
    args = parser.parse_args()

    if args.test_esempi:
        return run_test_esempi()

    if args.title:
        result = parse_portal_title(args.title)
        if not result:
            print(json.dumps({"ok": False, "error": "Title non interpretabile"}, ensure_ascii=False))
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.corte and args.numero and args.anno:
        result = build_filename(args.corte, args.numero, args.anno, tipo=args.tipo)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    parser.error("Specificare --title, oppure --corte --numero --anno, oppure --test-esempi")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
