"""
Normalizzazione nome file SGAI.

Usa la mappa corti in data/codici_corte.json tramite portal_to_filename.py
(fonte allineata al pacchetto collega). Quando in repo comparirà
api/utils/sentenze_utils.py, andrà usato quello come fonte canonica unica.
"""
from __future__ import annotations

from . import portal_to_filename as portal


def row_to_filename(numero: str, anno: str, corte: str, tipo: str = "Sentenza") -> dict:
    return portal.build_filename(corte, numero, anno, tipo=tipo)


def title_to_filename(title: str) -> dict:
    result = portal.parse_portal_title(title)
    if not result:
        return {"ok": False, "error": "Title non interpretabile", "portalTitle": title}
    return result
