"""Tests for queue_manager/dispatcher.py — DaskRunner."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
import redis
from distributed import Client

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup import redis_store as rs_module
from bp_ecg_watcher.queue_manager.dispatcher import DaskRunner, build_client


# Top-level picklable stubs used instead of MagicMock (Dask serialises tasks).
def _noop_process_file(zip_path: Path, **_kw: object) -> None:
    return None


def _raising_process_file(zip_path: Path, **_kw: object) -> None:
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dask_client():
    """Thread-based LocalCluster reused across all dispatcher tests."""
    with Client(
        n_workers=2,
        threads_per_worker=1,
        processes=False,
        dashboard_address=None,
    ) as client:
        yield client


@pytest.fixture()
def fake_server():
    return fakeredis.FakeServer()


@pytest.fixture(autouse=True)
def mock_redis_connections(fake_server: fakeredis.FakeServer):
    """Redirect all Redis connections to fakeredis for the duration of a test."""
    rs_module._PROCESS_POOLS.clear()
    with (
        patch.object(
            redis.ConnectionPool,
            "from_url",
            return_value=MagicMock(),
        ),
        patch.object(
            redis,
            "Redis",
            side_effect=lambda **_kw: fakeredis.FakeRedis(server=fake_server),
        ),
    ):
        yield
    rs_module._PROCESS_POOLS.clear()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        input_directory=tmp_path / "input",
        output_directory=tmp_path / "output",
        redis_url="redis://fake:6379",
    )


def _make_zip_with_2page_pdf(path: Path) -> Path:
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Page 1")
    c.showPage()
    c.drawString(100, 750, "Page 2")
    c.showPage()
    c.save()
    zip_path = path / "exam.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("exam.pdf", buf.getvalue())
    return zip_path


# ---------------------------------------------------------------------------
# build_client: mode selection
# ---------------------------------------------------------------------------


class TestBuildClient:
    def test_remote_mode_when_dask_scheduler_set(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "in",
            output_directory=tmp_path / "out",
            dask_scheduler="tcp://scheduler:8786",
        )
        with patch("bp_ecg_watcher.queue_manager.dispatcher.Client") as mock_client:
            build_client(s)
            mock_client.assert_called_once_with(address="tcp://scheduler:8786")

    def test_local_mode_when_no_scheduler_no_slurm(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "in",
            output_directory=tmp_path / "out",
        )
        with (
            patch("bp_ecg_watcher.queue_manager.dispatcher.Client") as mock_client,
            patch("bp_ecg_watcher.queue_manager.dispatcher.LocalCluster") as mock_lc,
            patch.dict("os.environ", {}, clear=True),
        ):
            build_client(s)
            mock_lc.assert_called_once()
            mock_client.assert_called_once()

    def test_slurm_mode_when_slurm_job_id_set(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "in",
            output_directory=tmp_path / "out",
        )
        mock_cluster = MagicMock()
        with (
            patch.dict("os.environ", {"SLURM_JOB_ID": "42"}),
            patch(
                "bp_ecg_watcher.queue_manager.dispatcher.Client"
            ) as mock_client,
            patch(
                "dask_jobqueue.SLURMCluster", return_value=mock_cluster
            ),
        ):
            build_client(s)
            mock_cluster.scale.assert_called_once_with(s.n_workers)
            mock_client.assert_called_once_with(mock_cluster)


# ---------------------------------------------------------------------------
# DaskRunner: orchestration
# ---------------------------------------------------------------------------


class TestDaskRunner:
    def test_empty_input_returns_zero_counts(
        self, settings: Settings, dask_client: Client
    ) -> None:
        runner = DaskRunner(settings)
        processed, failed = runner.run([], _client=dask_client)
        assert processed == 0
        assert failed == 0

    def test_exception_in_worker_counts_as_failed(
        self, settings: Settings, dask_client: Client, tmp_path: Path
    ) -> None:
        zip_path = tmp_path / "x.zip"
        zip_path.write_bytes(b"x")

        with patch(
            "bp_ecg_watcher.queue_manager.dispatcher.process_file",
            new=_raising_process_file,
        ):
            runner = DaskRunner(settings)
            processed, failed = runner.run([zip_path], _client=dask_client)

        assert failed == 1
        assert processed == 0

    def test_successful_task_counts_as_processed(
        self, settings: Settings, dask_client: Client, tmp_path: Path
    ) -> None:
        paths = [tmp_path / f"f{i}.zip" for i in range(3)]
        for p in paths:
            p.write_bytes(b"dummy")

        with patch(
            "bp_ecg_watcher.queue_manager.dispatcher.process_file",
            new=_noop_process_file,
        ):
            runner = DaskRunner(settings)
            processed, failed = runner.run(paths, _client=dask_client)

        assert processed == 3
        assert failed == 0


# ---------------------------------------------------------------------------
# DaskRunner: integration (real PDF processing with fakeredis)
# ---------------------------------------------------------------------------


class TestDaskRunnerIntegration:
    def test_valid_zip_produces_output_file(
        self, settings: Settings, dask_client: Client, tmp_path: Path
    ) -> None:
        settings.output_directory.mkdir(parents=True, exist_ok=True)
        zip_path = _make_zip_with_2page_pdf(tmp_path)

        runner = DaskRunner(settings)
        processed, failed = runner.run([zip_path], _client=dask_client)

        assert processed == 1
        assert failed == 0
        assert len(list(settings.output_directory.glob("*.pdf.zst"))) == 1

    def test_broken_zip_is_skipped_not_failed(
        self, settings: Settings, dask_client: Client, tmp_path: Path
    ) -> None:
        settings.output_directory.mkdir(parents=True, exist_ok=True)
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")

        runner = DaskRunner(settings)
        processed, failed = runner.run([bad], _client=dask_client)

        assert processed == 1  # process_file returns None on skip — not an exception
        assert failed == 0

    def test_duplicate_zip_is_skipped(
        self,
        settings: Settings,
        fake_server: fakeredis.FakeServer,
        dask_client: Client,
        tmp_path: Path,
    ) -> None:
        settings.output_directory.mkdir(parents=True, exist_ok=True)
        zip_path = _make_zip_with_2page_pdf(tmp_path)

        runner = DaskRunner(settings)
        # First run — should process
        runner.run([zip_path], _client=dask_client)
        first_count = len(list(settings.output_directory.glob("*.pdf.zst")))

        # Second run — should skip (duplicate detected in fakeredis)
        runner.run([zip_path], _client=dask_client)
        second_count = len(list(settings.output_directory.glob("*.pdf.zst")))

        assert first_count == 1
        assert second_count == 1  # no new file written

