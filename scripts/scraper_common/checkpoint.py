"""Checkpoint atomico riusabile."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _replace_with_retry(src: Path, dest: Path, *, attempts: int = 8) -> None:
    last: OSError | None = None
    for i in range(attempts):
        try:
            os.replace(src, dest)
            return
        except OSError as exc:
            last = exc
            winerr = getattr(exc, "winerror", None)
            if winerr not in (32, 5) and not isinstance(exc, PermissionError):
                raise
            time.sleep(0.15 * (i + 1))
    assert last is not None
    raise last


class Checkpoint:
    def __init__(self, path: Path, *, fonte: str) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "version": 1,
            "fonte": fonte,
            "query": None,
            "processed": [],
            "failed": [],
            "last_document": None,
            "status": "idle",
            "catalog_cursor": None,
        }
        # set in-memory: con 6k+ voci, `x in list` rendeva il resume lentissimo
        self._processed: set[str] = set()
        self._failed: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            self.data.update(loaded)
            self.data.setdefault("processed", [])
            self.data.setdefault("failed", [])
            self.data.setdefault("catalog_cursor", None)
            proc = list(self.data.get("processed") or [])
            fail = list(self.data.get("failed") or [])
            # dedupe preservando ordine
            self.data["processed"] = list(dict.fromkeys(proc))
            self.data["failed"] = list(dict.fromkeys(fail))
            self._processed = set(self.data["processed"])
            self._failed = set(self.data["failed"])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with open(tmp, "rb+") as fh:
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp, self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def set_status(self, status: str) -> None:
        self.data["status"] = status
        self.save()

    def set_catalog_cursor(self, cursor: dict[str, Any] | None) -> None:
        self.data["catalog_cursor"] = cursor
        self.save()

    def is_done(self, nome_base: str) -> bool:
        return nome_base in self._processed or nome_base in self._failed

    def mark_processed(self, nome_base: str) -> None:
        if nome_base not in self._processed:
            self._processed.add(nome_base)
            self.data.setdefault("processed", []).append(nome_base)
        if nome_base in self._failed:
            self._failed.discard(nome_base)
            failed = self.data.setdefault("failed", [])
            if nome_base in failed:
                failed.remove(nome_base)
        self.data["last_document"] = nome_base
        self.save()

    def mark_failed(self, nome_base: str) -> None:
        if nome_base and nome_base not in self._failed:
            self._failed.add(nome_base)
            self.data.setdefault("failed", []).append(nome_base)
        self.data["last_document"] = nome_base
        self.save()

    def invalidate_done(self, nome_base: str) -> None:
        if nome_base in self._processed:
            self._processed.discard(nome_base)
            proc = self.data.setdefault("processed", [])
            if nome_base in proc:
                proc.remove(nome_base)
        if nome_base in self._failed:
            self._failed.discard(nome_base)
            failed = self.data.setdefault("failed", [])
            if nome_base in failed:
                failed.remove(nome_base)
        self.save()

    def clear_failed(self) -> None:
        if not self._failed and not self.data.get("failed"):
            return
        self._failed.clear()
        self.data["failed"] = []
        self.save()
