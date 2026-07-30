"""Upload verso SGAI — disabilitato fino a Gate 2 / PILOTA AUTORIZZATO."""
from __future__ import annotations

from pathlib import Path


class UploadDisabledError(RuntimeError):
    pass


def enqueue_upload(path: Path, *, enabled: bool = False) -> dict:
    if not enabled:
        raise UploadDisabledError(
            "Upload SGAI disabilitato (MEF_SCRAPER_UPLOAD!=1 / Gate 2 non aperto)"
        )
    raise NotImplementedError("Upload reale non implementato in Fase 2 stub")
