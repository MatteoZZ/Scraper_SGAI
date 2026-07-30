"""Punto 3 — limiti di carico, delay, stop pulito."""
from __future__ import annotations

import random
import signal
import threading
import time
from contextlib import contextmanager


class StopController:
    """Stop pulito: completa il file corrente, non partire con uno nuovo."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._installed = False

    def request_stop(self, *_args) -> None:
        self._stop.set()

    @property
    def should_stop(self) -> bool:
        return self._stop.is_set()

    def install_signals(self) -> None:
        if self._installed:
            return
        try:
            signal.signal(signal.SIGINT, self.request_stop)
            signal.signal(signal.SIGTERM, self.request_stop)
        except Exception:
            # Windows / thread secondari
            pass
        self._installed = True


class RunLimits:
    """
    Limiti Fase 2:
    - max 1 download alla volta (semaforo)
    - delay variabile tra download
    - non accumulare tmp all'infinito (cleanup caller)
    """

    def __init__(
        self,
        *,
        max_download_concurrency: int = 1,
        delay_min: float = 18.0,
        delay_max: float = 32.0,
        page_delay: float = 25.0,
    ) -> None:
        self.max_download_concurrency = max(1, min(2, int(max_download_concurrency)))
        self.delay_min = float(delay_min)
        self.delay_max = max(self.delay_min, float(delay_max))
        self.page_delay = float(page_delay)
        self._sem = threading.Semaphore(self.max_download_concurrency)
        self.stop = StopController()

    @contextmanager
    def download_slot(self):
        self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()

    def pause_between_downloads(self, log=None) -> None:
        if self.delay_max <= 0:
            return
        delay = random.uniform(self.delay_min, self.delay_max)
        # jitter anti-pattern fisso
        delay += random.uniform(0.2, 2.5)
        if log:
            log(f"pausa download {delay:.1f}s (limite {self.delay_min:.0f}-{self.delay_max:.0f}s)")
        # interruptible sleep
        end = time.time() + delay
        while time.time() < end:
            if self.stop.should_stop:
                return
            time.sleep(min(0.5, end - time.time()))

    def pause_between_pages(self, log=None) -> None:
        delay = random.triangular(
            self.page_delay * 0.85,
            self.page_delay * 1.35,
            self.page_delay,
        )
        if log:
            log(f"pausa pagina {delay:.1f}s")
        end = time.time() + delay
        while time.time() < end:
            if self.stop.should_stop:
                return
            time.sleep(min(0.5, end - time.time()))
