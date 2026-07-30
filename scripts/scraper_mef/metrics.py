"""Contatori scraper."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Metrics:
    discovered: int = 0
    skipped_local: int = 0
    skipped_server: int = 0
    skipped_embedded: int = 0
    skipped_invalid: int = 0
    skipped_checkpoint: int = 0
    would_download: int = 0
    attempts: int = 0
    downloaded: int = 0
    uploaded: int = 0
    errors: int = 0
    blocked: int = 0

    def as_dict(self) -> dict:
        return asdict(self)
