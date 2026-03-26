"""Tests for config.py — Settings and version helper."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from unittest.mock import patch

from bp_ecg_watcher.config import Settings, _get_package_version


class TestGetPackageVersion:
    """Tests for the _get_package_version helper."""

    def test_returns_installed_version_when_available(self) -> None:
        """Should return the version string from package metadata."""
        with patch.object(importlib.metadata, "version", return_value="1.2.3"):
            assert _get_package_version() == "1.2.3"

    def test_returns_dev_when_package_not_found(self) -> None:
        """Should fall back to 'dev' when the package is not installed."""
        with patch.object(
            importlib.metadata,
            "version",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            assert _get_package_version() == "dev"


class TestSettings:
    """Tests for the Settings model."""

    def test_default_buckets(self, tmp_path: Path) -> None:
        """Default bucket names should match the spec."""
        s = Settings(
            watch_directory=tmp_path,
            minio_endpoint="http://localhost:9000",
            minio_access_key="key",
            minio_secret_key="secret",
        )
        assert s.bucket_images == "bp-ecg-dev-copper"
        assert s.bucket_intake == "bp-ecg-dev-iron"
        assert s.bucket_rejected == "bp-ecg-dev-coal"

    def test_default_workers_and_queue(self, tmp_path: Path) -> None:
        """Default concurrency parameters should match the spec."""
        s = Settings(
            watch_directory=tmp_path,
            minio_endpoint="http://localhost:9000",
            minio_access_key="key",
            minio_secret_key="secret",
        )
        assert s.max_workers == 4
        assert s.queue_maxsize == 50

    def test_default_rasterization_settings(self, tmp_path: Path) -> None:
        """Default rasterization and image resize parameters should match the spec."""
        s = Settings(
            watch_directory=tmp_path,
            minio_endpoint="http://localhost:9000",
            minio_access_key="key",
            minio_secret_key="secret",
        )
        assert s.rasterization_dpi == 300
        assert s.image_max_side_px == 1200

    def test_watcher_version_is_string(self, tmp_path: Path) -> None:
        """watcher_version must always be a non-empty string."""
        s = Settings(
            watch_directory=tmp_path,
            minio_endpoint="http://localhost:9000",
            minio_access_key="key",
            minio_secret_key="secret",
        )
        assert isinstance(s.watcher_version, str)
        assert len(s.watcher_version) > 0

    def test_override_via_kwargs(self, tmp_path: Path) -> None:
        """Settings values should be overridable via constructor kwargs."""
        s = Settings(
            watch_directory=tmp_path,
            minio_endpoint="http://minio:9000",
            minio_access_key="user",
            minio_secret_key="pass",
            environment="prod",
            max_workers=8,
        )
        assert s.environment == "prod"
        assert s.max_workers == 8
