"""Tests for config.py — Settings."""

from __future__ import annotations

from pathlib import Path

from bp_ecg_watcher.config import Settings


class TestSettings:
    """Tests for the Settings model."""

    def test_input_and_output_directory_are_paths(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert isinstance(s.input_directory, Path)
        assert isinstance(s.output_directory, Path)

    def test_default_dask_cluster_settings(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert s.n_workers == 24
        assert s.cores_per_worker == 8
        assert s.mem_per_worker_gb == 16
        assert s.dask_scheduler is None

    def test_default_redis_url(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert s.redis_url == "redis://localhost:6379"

    def test_default_processing_parameters(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert s.rasterization_dpi == 300
        assert s.image_max_side_px == 1200
        assert s.zstd_level == 9

    def test_override_via_kwargs(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
            environment="prod",
            redis_url="redis://redis-host:6379",
            dask_scheduler="tcp://scheduler:8786",
            n_workers=48,
        )
        assert s.environment == "prod"
        assert s.redis_url == "redis://redis-host:6379"
        assert s.dask_scheduler == "tcp://scheduler:8786"
        assert s.n_workers == 48

    def test_environment_defaults_to_dev(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert s.environment == "dev"

