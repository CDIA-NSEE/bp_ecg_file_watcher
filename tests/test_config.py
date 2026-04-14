"""Tests for config.py — Settings."""

from __future__ import annotations

import os
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

    def test_default_worker_counts(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert s.io_workers == 8
        assert s.cpu_workers == (os.cpu_count() or 4)

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
            io_workers=16,
            cpu_workers=8,
        )
        assert s.environment == "prod"
        assert s.io_workers == 16
        assert s.cpu_workers == 8

    def test_environment_defaults_to_dev(self, tmp_path: Path) -> None:
        s = Settings(
            input_directory=tmp_path / "input",
            output_directory=tmp_path / "output",
        )
        assert s.environment == "dev"
