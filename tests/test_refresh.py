"""Refresh concurrency: the module-level lock shared by the `luxtock ui`
auto-refresh thread and the server's POST /api/refresh thread."""
import threading

import pytest

from luxtock import refresh


def test_try_refresh_data_runs_and_returns_result_when_free(tmp_path, monkeypatch):
    sentinel = {"fetched_at": "2026-07-19T00:00:00+00:00", "quotes": {}}
    monkeypatch.setattr(refresh, "refresh_data", lambda data_dir: sentinel)
    assert refresh.try_refresh_data(tmp_path) is sentinel
    assert refresh.refresh_in_progress() is False  # lock released afterwards


def test_try_refresh_data_skips_when_already_in_progress(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_refresh(data_dir):
        calls.append(data_dir)
        started.set()
        assert release.wait(timeout=5)
        return {"quotes": {}}

    monkeypatch.setattr(refresh, "refresh_data", slow_refresh)
    t = threading.Thread(target=refresh.try_refresh_data, args=(tmp_path,))
    t.start()
    try:
        assert started.wait(timeout=5)
        assert refresh.refresh_in_progress() is True
        assert refresh.try_refresh_data(tmp_path) is None  # skipped, not queued
    finally:
        release.set()
        t.join(timeout=5)
    assert len(calls) == 1  # the overlapping attempt never ran
    assert refresh.refresh_in_progress() is False


def test_lock_released_when_refresh_raises(tmp_path, monkeypatch):
    def boom(data_dir):
        raise RuntimeError("fetch blew up")

    monkeypatch.setattr(refresh, "refresh_data", boom)
    with pytest.raises(RuntimeError):
        refresh.try_refresh_data(tmp_path)
    assert refresh.refresh_in_progress() is False
