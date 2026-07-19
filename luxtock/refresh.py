"""refresh_data: shared refresh logic used by both the CLI and the server."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .flows import fetch_flows
from .history import append_history
from .quotes import fetch_quotes
from .store import load_watchlist, write_json_atomic

# Two entry points can refresh concurrently (the `luxtock ui` auto-refresh
# thread and POST /api/refresh). They share this lock via try_refresh_data
# so two refreshes never interleave their quotes/flows/history writes.
_REFRESH_LOCK = threading.Lock()


def load_json(path: Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def refresh_in_progress() -> bool:
    """True while some thread is inside try_refresh_data."""
    if _REFRESH_LOCK.acquire(blocking=False):
        _REFRESH_LOCK.release()
        return False
    return True


def try_refresh_data(data_dir: Path) -> dict | None:
    """Non-blocking refresh: run refresh_data unless one is already in
    progress, in which case skip and return None (never queue a second)."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        return None
    try:
        return refresh_data(data_dir)
    finally:
        _REFRESH_LOCK.release()


def refresh_data(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    stocks = load_watchlist(data_dir)["stocks"]
    tickers = [s["ticker"] for s in stocks]
    benchmarks = {s["ticker"]: s["benchmark"] for s in stocks if s.get("benchmark")}
    paired = {s["ticker"]: s["paired"] for s in stocks if s.get("paired")}
    quotes = fetch_quotes(tickers, load_json(data_dir / "quotes.json"), paired)
    flows = fetch_flows(tickers, load_json(data_dir / "flows.json"), benchmarks)
    data_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(data_dir / "quotes.json", quotes)
    write_json_atomic(data_dir / "flows.json", flows)
    append_history(data_dir, quotes, flows)
    return quotes


def quotes_stale(data_dir: Path, hours: float = 12) -> bool:
    # 12h is the CLI's auto-refresh threshold (luxtock ui). The methodology's
    # 24h rule is a different gate: it is where an *agent* must stop and ask
    # the user to refresh. Intentionally stricter here — cheap to refresh
    # early, expensive to analyze on stale data.
    q = load_json(Path(data_dir) / "quotes.json")
    if not q or not q.get("fetched_at"):
        return True
    fetched = datetime.fromisoformat(q["fetched_at"])
    return (datetime.now(timezone.utc) - fetched).total_seconds() > hours * 3600
