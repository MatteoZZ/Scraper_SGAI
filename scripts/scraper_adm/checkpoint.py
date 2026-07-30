"""Checkpoint atomico ADM."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "version": 1,
            "fonte": "ADM",
            "list_url": None,
            "processed": [],
            "failed": [],
            "last_document": None,
            "status": "idle",
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            self.data.update(loaded)
            self.data.setdefault("processed", [])
            self.data.setdefault("failed", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def set_status(self, status: str) -> None:
        self.data["status"] = status
        self.save()

    def is_done(self, nome_base: str) -> bool:
        return nome_base in self.data.get("processed", []) or nome_base in self.data.get(
            "failed", []
        )

    def mark_processed(self, nome_base: str) -> None:
        processed = self.data.setdefault("processed", [])
        if nome_base not in processed:
            processed.append(nome_base)
        failed = self.data.setdefault("failed", [])
        if nome_base in failed:
            failed.remove(nome_base)
        self.data["last_document"] = nome_base
        self.save()

    def mark_failed(self, nome_base: str) -> None:
        failed = self.data.setdefault("failed", [])
        if nome_base and nome_base not in failed:
            failed.append(nome_base)
        self.data["last_document"] = nome_base
        self.save()

    def invalidate_done(self, nome_base: str) -> None:
        for key in ("processed", "failed"):
            lst = self.data.setdefault(key, [])
            if nome_base in lst:
                lst.remove(nome_base)
        self.save()
