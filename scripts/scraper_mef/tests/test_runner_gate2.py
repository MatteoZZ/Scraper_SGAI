"""Test bloccanti Gate 2 / PR #6: max attempts, resume, mismatch, 403/429, duplicati."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from scraper_mef.cache import SkipIndex  # noqa: E402
from scraper_mef.checkpoint import Checkpoint  # noqa: E402
from scraper_mef.client import MefBlockedError, MefHttpError  # noqa: E402
from scraper_mef.config import Config  # noqa: E402
from scraper_mef.download import make_minimal_pdf  # noqa: E402
from scraper_mef.limits import RunLimits  # noqa: E402
from scraper_mef.metrics import Metrics  # noqa: E402
from scraper_mef.parse import PortalRow  # noqa: E402
from scraper_mef.runner import process_rows  # noqa: E402


def _rows(*specs: tuple[str, str, str, str]) -> list[PortalRow]:
    out = []
    for i, (tipo, num, anno, corte) in enumerate(specs):
        out.append(
            PortalRow(
                tipo=tipo,
                numero=num,
                anno=anno,
                corte=corte,
                href=f"/ricerca/dettaglio/{num}",
                row_index=i,
            )
        )
    return out


class TestRunnerGate2(unittest.TestCase):
    def _ctx(self, tmp: str):
        out = Path(tmp) / "out"
        out.mkdir()
        cfg = Config()
        cfg.tmp_dir = Path(tmp) / "tmp"
        cfg.tmp_dir.mkdir()
        cfg.min_pdf_bytes = 1000
        cfg.max_pdf_bytes = 5_000_000
        cfg.upload_enabled = False
        # delay zero per test veloci
        limits = RunLimits(
            max_download_concurrency=1,
            delay_min=0.0,
            delay_max=0.0,
            page_delay=0.0,
        )
        index = SkipIndex(
            output_dir=out,
            server_cache_files=[],
            min_pdf_bytes=cfg.min_pdf_bytes,
            max_pdf_bytes=cfg.max_pdf_bytes,
        )
        cp = Checkpoint(Path(tmp) / "cp.json")
        return cfg, limits, index, cp, out

    def test_max_counts_failed_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(
                ("Sentenza", "1", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "2", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "3", "2026", "CGT 2° Lombardia"),
            )
            calls = {"n": 0}

            def fetch(row: PortalRow):
                calls["n"] += 1
                raise RuntimeError("boom simulato")

            metrics = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=2,
                page_number=1,
                fetch_pdf=fetch,
                require_detail_meta=False,
                resume=False,
            )
            self.assertEqual(metrics.attempts, 2)
            self.assertEqual(calls["n"], 2)
            self.assertEqual(metrics.downloaded, 0)
            self.assertEqual(metrics.errors, 2)

    def test_resume_skips_processed_and_row_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(
                ("Sentenza", "10", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "11", "2026", "CGT 2° Lombardia"),
            )

            def fetch_ok(row: PortalRow):
                return make_minimal_pdf(1200), row.to_meta()

            metrics = Metrics()
            process_rows(
                rows[:1],
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=1,
                page_number=1,
                fetch_pdf=fetch_ok,
                resume=False,
            )
            self.assertEqual(metrics.downloaded, 1)

            # seconda run: resume, non deve ridscaricare
            metrics2 = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics2,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=5,
                page_number=1,
                fetch_pdf=fetch_ok,
                resume=True,
            )
            self.assertGreaterEqual(metrics2.skipped_checkpoint, 1)
            # secondo doc scaricato una volta
            self.assertEqual(metrics2.downloaded, 1)
            self.assertEqual(metrics2.attempts, 1)

    def test_detail_mismatch_counts_as_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(("Sentenza", "1205", "2026", "CGT 2° Lombardia"))

            def fetch_mismatch(row: PortalRow):
                wrong = row.to_meta()
                wrong = dict(wrong)
                wrong["numero"] = "999"
                wrong["nomeBase"] = "Sentenza_V70_999_2026"
                wrong["nomeFile"] = "Sentenza_V70_999_2026.pdf"
                return make_minimal_pdf(1200), wrong

            metrics = Metrics()
            items = process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=1,
                page_number=1,
                fetch_pdf=fetch_mismatch,
                require_detail_meta=True,
                resume=False,
            )
            self.assertEqual(metrics.attempts, 1)
            self.assertEqual(metrics.downloaded, 0)
            self.assertEqual(items[0]["action"], "download_error")
            self.assertIn("disallineati", items[0]["error"])

    def test_blocked_403_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(
                ("Sentenza", "1", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "2", "2026", "CGT 2° Lombardia"),
            )

            def fetch_403(row: PortalRow):
                raise MefBlockedError(403, "forbidden")

            metrics = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=5,
                page_number=1,
                fetch_pdf=fetch_403,
                resume=False,
            )
            self.assertEqual(metrics.blocked, 1)
            self.assertEqual(metrics.attempts, 1)
            self.assertEqual(cp.data["status"], "blocked")

    def test_blocked_429_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(
                ("Sentenza", "1", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "2", "2026", "CGT 2° Lombardia"),
            )

            def fetch_429(row: PortalRow):
                raise MefBlockedError(429, "too many requests")

            metrics = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=5,
                page_number=1,
                fetch_pdf=fetch_429,
                resume=False,
            )
            self.assertEqual(metrics.blocked, 1)
            self.assertEqual(metrics.attempts, 1)
            self.assertEqual(cp.data["status"], "blocked")
            self.assertIn("429", str(cp.data.get("block_reason") or ""))

    def test_processed_missing_pdf_is_redownloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(("Sentenza", "77", "2026", "CGT 2° Lombardia"))
            nome = "Sentenza_V70_77_2026"
            cp.mark_processed(nome, page=1, row_index=1)
            # nessun PDF in out → deve invalidare e riscaricare
            calls = {"n": 0}

            def fetch(row: PortalRow):
                calls["n"] += 1
                return make_minimal_pdf(1200), row.to_meta()

            metrics = Metrics()
            items = process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=1,
                page_number=1,
                fetch_pdf=fetch,
                resume=True,
            )
            self.assertEqual(calls["n"], 1)
            self.assertEqual(metrics.downloaded, 1)
            self.assertTrue(any(i.get("action") == "checkpoint_invalidated" for i in items))
            self.assertTrue((out / f"{nome}.pdf").exists())

    def test_page_change_resets_row_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            cp.set_position(page=1, row_index=5)
            rows = _rows(
                ("Sentenza", "1", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "2", "2026", "CGT 2° Lombardia"),
            )

            def fetch(row: PortalRow):
                return make_minimal_pdf(1200), row.to_meta()

            metrics = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=2,
                page_number=2,  # pagina nuova
                fetch_pdf=fetch,
                resume=True,
            )
            self.assertEqual(cp.data["last_page"], 2)
            self.assertEqual(metrics.downloaded, 2)
            self.assertEqual(metrics.skipped_checkpoint, 0)

    def test_max_zero_rejected(self):
        from scraper_mef.cli import main

        code = main(["run", "--max", "0", "--simulate"])
        self.assertEqual(code, 2)

    def test_http_500_counts_attempt_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(
                ("Sentenza", "1", "2026", "CGT 2° Lombardia"),
                ("Sentenza", "2", "2026", "CGT 2° Lombardia"),
            )
            state = {"n": 0}

            def fetch(row: PortalRow):
                state["n"] += 1
                if state["n"] == 1:
                    raise MefHttpError(500, "server")
                return make_minimal_pdf(1200), row.to_meta()

            metrics = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=metrics,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=2,
                page_number=1,
                fetch_pdf=fetch,
                resume=False,
            )
            self.assertEqual(metrics.attempts, 2)
            self.assertEqual(metrics.downloaded, 1)
            self.assertEqual(metrics.errors, 1)

    def test_duplicate_idempotent_second_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, limits, index, cp, out = self._ctx(tmp)
            rows = _rows(("Sentenza", "50", "2026", "CGT 2° Lombardia"))

            def fetch(row: PortalRow):
                return make_minimal_pdf(1200), row.to_meta()

            m1 = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=m1,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=1,
                page_number=1,
                fetch_pdf=fetch,
                resume=False,
            )
            # status completed → reset row index ma keep processed
            cp.data["status"] = "completed"
            cp.data["last_row_index"] = 0
            cp.save()

            m2 = Metrics()
            process_rows(
                rows,
                index=index,
                checkpoint=cp,
                metrics=m2,
                limits=limits,
                cfg=cfg,
                output_dir=out,
                max_attempts=1,
                page_number=1,
                fetch_pdf=fetch,
                resume=True,
            )
            self.assertEqual(m2.downloaded, 0)
            self.assertEqual(m2.attempts, 0)
            # skip_local oppure skip_checkpoint
            self.assertGreaterEqual(m2.skipped_local + m2.skipped_checkpoint, 1)

    def test_no_hardcoded_developer_paths_in_config(self):
        """Default output/cache devono stare sotto il modulo, non path assoluti personali."""
        import scraper_mef.config as cfgmod

        # garanzia: non esistono più costanti assolute tipiche del PC sviluppatore
        self.assertFalse(hasattr(cfgmod, "DEFAULT_MATTEO_SGAI"))
        self.assertFalse(hasattr(cfgmod, "DEFAULT_SERVER_CACHES"))
        cfg = Config()
        self.assertEqual(cfg.output_dir, cfgmod.ROOT / "downloads_out")
        caches = cfg.default_server_caches()
        self.assertEqual(len(caches), 1)
        self.assertEqual(caches[0], cfgmod.DEFAULT_CACHE_KEYS)


if __name__ == "__main__":
    unittest.main()
