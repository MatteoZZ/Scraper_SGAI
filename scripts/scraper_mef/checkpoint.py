"""Checkpoint atomico per resume reale (pagina / riga / documento)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Checkpoint:
    """
    Stato ripresa:
      - last_page: pagina risultati (1-based; 0 = non impostata)
      - last_row_index: prossima riga da processare sulla pagina corrente (0-based)
      - last_nome_base / last_document: ultimo documento toccato
      - processed: nomiBase già gestiti con successo (skip su resume)
      - failed: nomiBase con tentativo fallito (non ritentare nella stessa run/resume)
      - status: idle|running|stopped|blocked|error|completed
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "version": 2,
            "last_page": 0,
            "last_row_index": 0,
            "last_nome_base": None,
            "last_document": None,
            "processed": [],
            "failed": [],
            "status": "idle",
            "block_reason": None,
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return
        self.data.update(loaded)
        self.data.setdefault("processed", [])
        self.data.setdefault("failed", [])
        self.data.setdefault("last_row_index", 0)
        self.data.setdefault("last_page", 0)
        self.data.setdefault("status", "idle")
        self.data.setdefault("version", 2)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def set_status(self, status: str, *, block_reason: str | None = None) -> None:
        self.data["status"] = status
        if block_reason is not None:
            self.data["block_reason"] = block_reason
        self.save()

    def set_position(self, *, page: int | None = None, row_index: int | None = None) -> None:
        if page is not None:
            self.data["last_page"] = int(page)
        if row_index is not None:
            self.data["last_row_index"] = int(row_index)
        self.save()

    def sync_page(self, page: int) -> None:
        """
        Aggiorna last_page. Se la pagina cambia rispetto al checkpoint,
        azzera last_row_index (altrimenti le prime righe della nuova pagina
        verrebbero saltate).
        """
        page = int(page)
        prev = int(self.data.get("last_page") or 0)
        if prev != 0 and prev != page:
            self.data["last_row_index"] = 0
        self.data["last_page"] = page
        self.save()

    def unmark_processed(self, nome_base: str) -> None:
        processed = self.data.setdefault("processed", [])
        if nome_base in processed:
            processed.remove(nome_base)
            self.save()

    def unmark_failed(self, nome_base: str) -> None:
        failed = self.data.setdefault("failed", [])
        if nome_base in failed:
            failed.remove(nome_base)
            self.save()

    def invalidate_done(self, nome_base: str) -> None:
        """Rimuove processed/failed per consentire ridownload."""
        self.unmark_processed(nome_base)
        self.unmark_failed(nome_base)

    def mark_processed(self, nome_base: str, *, page: int | None = None, row_index: int | None = None) -> None:
        processed = self.data.setdefault("processed", [])
        if nome_base not in processed:
            processed.append(nome_base)
        failed = self.data.setdefault("failed", [])
        if nome_base in failed:
            failed.remove(nome_base)
        self.data["last_nome_base"] = nome_base
        self.data["last_document"] = nome_base
        if page is not None:
            self.data["last_page"] = int(page)
        if row_index is not None:
            self.data["last_row_index"] = int(row_index)
        self.save()

    def mark_failed(self, nome_base: str, *, page: int | None = None, row_index: int | None = None) -> None:
        failed = self.data.setdefault("failed", [])
        if nome_base and nome_base not in failed:
            failed.append(nome_base)
        self.data["last_nome_base"] = nome_base
        self.data["last_document"] = nome_base
        if page is not None:
            self.data["last_page"] = int(page)
        if row_index is not None:
            self.data["last_row_index"] = int(row_index)
        self.save()

    def is_done(self, nome_base: str) -> bool:
        if not nome_base:
            return False
        return nome_base in self.data.get("processed", []) or nome_base in self.data.get(
            "failed", []
        )

    def should_skip_row(self, row_index: int, nome_base: str | None) -> bool:
        """True se già oltre questa riga o documento già processato/fallito."""
        if nome_base and self.is_done(nome_base):
            return True
        last = int(self.data.get("last_row_index") or 0)
        # last_row_index = prossima riga da fare: salta indici minori
        if nome_base is None and row_index < last:
            return True
        return False
