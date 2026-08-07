"""Partizionamento multi-worker: stesso output_dir, checkpoint separati."""
from __future__ import annotations

import zlib
from pathlib import Path


def shard_index(key: str, workers: int) -> int:
    """Indice worker stabile 0..workers-1 per una chiave (nomeBase / work URI)."""
    n = max(1, int(workers))
    if n == 1:
        return 0
    return zlib.crc32((key or "").encode("utf-8")) % n


def shard_owns(key: str, *, worker_id: int, workers: int) -> bool:
    return shard_index(key, workers) == int(worker_id)


def worker_checkpoint_path(base: Path, *, worker_id: int, workers: int) -> Path:
    """`.checkpoint.json` → `.checkpoint.w0of2.json` se workers>1."""
    if int(workers) <= 1:
        return base
    return base.with_name(f"{base.stem}.w{int(worker_id)}of{int(workers)}{base.suffix}")


def validate_workers(worker_id: int, workers: int) -> None:
    w = int(workers)
    i = int(worker_id)
    if w < 1:
        raise ValueError("--workers deve essere >= 1")
    if i < 0 or i >= w:
        raise ValueError(f"--worker-id deve essere in 0..{w - 1} (ricevuto {i})")
