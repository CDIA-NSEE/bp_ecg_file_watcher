"""Tests for dedup/redis_store.py — RedisStore."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
import redis

from bp_ecg_watcher.dedup import redis_store as rs_module
from bp_ecg_watcher.dedup.redis_store import RedisStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture(autouse=True)
def isolate_process_pools():
    """Ensure module-level pool cache is empty before and after each test."""
    rs_module._PROCESS_POOLS.clear()
    yield
    rs_module._PROCESS_POOLS.clear()


@pytest.fixture()
def store(fake_server: fakeredis.FakeServer) -> RedisStore:
    """``RedisStore`` backed by fakeredis."""
    with (
        patch.object(redis.ConnectionPool, "from_url", return_value=MagicMock()),
        patch.object(
            redis,
            "Redis",
            side_effect=lambda **_kw: fakeredis.FakeRedis(server=fake_server),
        ),
    ):
        yield RedisStore("redis://fake:6379")


# ---------------------------------------------------------------------------
# try_reserve
# ---------------------------------------------------------------------------


class TestTryReserve:
    def test_first_reservation_succeeds(self, store: RedisStore) -> None:
        assert store.try_reserve("abc123") is True

    def test_second_reservation_for_same_hash_fails(self, store: RedisStore) -> None:
        store.try_reserve("abc123")
        assert store.try_reserve("abc123") is False

    def test_different_hashes_are_independent(self, store: RedisStore) -> None:
        assert store.try_reserve("hash_a") is True
        assert store.try_reserve("hash_b") is True

    def test_reservation_stores_pending_value(
        self, fake_server: fakeredis.FakeServer, store: RedisStore
    ) -> None:
        store.try_reserve("pend123")
        raw = fakeredis.FakeRedis(server=fake_server).get("bp_ecg:processed:pend123")
        assert raw == b"pending"


# ---------------------------------------------------------------------------
# mark_complete
# ---------------------------------------------------------------------------


class TestMarkComplete:
    def test_overwrites_pending_with_output_path(
        self, fake_server: fakeredis.FakeServer, store: RedisStore
    ) -> None:
        store.try_reserve("done123")
        store.mark_complete("done123", "/out/done123.pdf.zst")
        raw = fakeredis.FakeRedis(server=fake_server).get("bp_ecg:processed:done123")
        assert raw == b"/out/done123.pdf.zst"

    def test_completed_hash_cannot_be_reserved_again(self, store: RedisStore) -> None:
        store.try_reserve("abc")
        store.mark_complete("abc", "/out/abc.pdf.zst")
        assert store.try_reserve("abc") is False


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_released_hash_can_be_reserved_again(self, store: RedisStore) -> None:
        store.try_reserve("retry_me")
        store.release("retry_me")
        assert store.try_reserve("retry_me") is True

    def test_release_nonexistent_hash_is_noop(self, store: RedisStore) -> None:
        store.release("never_reserved")  # must not raise


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_reserve_complete_blocks_duplicate(self, store: RedisStore) -> None:
        assert store.try_reserve("full_flow") is True
        store.mark_complete("full_flow", "/out/full_flow.pdf.zst")
        assert store.try_reserve("full_flow") is False

    def test_reserve_fail_release_retry(self, store: RedisStore) -> None:
        store.try_reserve("fail_retry")
        store.release("fail_retry")
        assert store.try_reserve("fail_retry") is True
        store.mark_complete("fail_retry", "/out/fail_retry.pdf.zst")
        assert store.try_reserve("fail_retry") is False


# ---------------------------------------------------------------------------
# _get_pool: module-level fallback
# ---------------------------------------------------------------------------


class TestGetPool:
    def test_same_url_returns_same_pool_object(self) -> None:
        from bp_ecg_watcher.dedup.redis_store import _get_pool

        with patch.object(redis.ConnectionPool, "from_url", return_value=MagicMock()) as mock:
            pool_a = _get_pool("redis://localhost:6379")
            pool_b = _get_pool("redis://localhost:6379")
            assert pool_a is pool_b
            mock.assert_called_once()

    def test_different_urls_return_different_pools(self) -> None:
        from bp_ecg_watcher.dedup.redis_store import _get_pool

        with patch.object(
            redis.ConnectionPool, "from_url", side_effect=lambda *a, **kw: MagicMock()
        ):
            pool_a = _get_pool("redis://host-a:6379")
            pool_b = _get_pool("redis://host-b:6379")
            assert pool_a is not pool_b

