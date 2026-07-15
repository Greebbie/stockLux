"""Historical daily-close backfill: yfinance → data/history.jsonl.

Fills price-only rows for every watchlist ticker AND its relative-strength
benchmark (default SPY) so the calibration ledger (forward returns,
realized-price matching, MAE/MFE) has depth from day one instead of waiting
a year of daily refreshes. Snapshot-only fields (short interest, put/call,
revisions, pt_mean) cannot be reconstructed retroactively and stay absent;
backfilled rows are marked source="backfill". See framework/quant.md v1.2.

Append-only and non-destructive: existing (date, ticker) keys always win,
and rows dated today or later are never written — the daily refresh owns
the current day's richer snapshot row. Deterministic given a `downloader`;
the network lives only in the default `_yf_daily_closes`.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from luxtock import store
from luxtock.history import HISTORY_FILE, _existing_keys

DEFAULT_YEARS = 2
DEFAULT_BENCHMARK = "SPY"


def watch_symbols(watchlist: dict) -> list[str]:
    """Watchlist tickers plus each name's relative-strength benchmark."""
    symbols: set[str] = set()
    for s in watchlist.get("stocks", []):
        if s.get("ticker"):
            symbols.add(s["ticker"])
            symbols.add(s.get("benchmark") or DEFAULT_BENCHMARK)
    return sorted(symbols)


def _yf_daily_closes(
    symbols: list[str], start: date,
) -> dict[str, list[tuple[str, float]]]:
    """Fetch daily closes per symbol from yfinance (network).

    auto_adjust=False keeps Yahoo's split-adjusted (but not dividend-
    adjusted) Close — the convention memo targets are quoted in. A symbol
    that errors yields no rows rather than failing the whole batch.
    """
    import yfinance as yf

    out: dict[str, list[tuple[str, float]]] = {}
    for sym in symbols:
        try:
            df = yf.Ticker(sym).history(
                start=start.isoformat(), interval="1d", auto_adjust=False,
            )
        except Exception:
            out[sym] = []
            continue
        rows: list[tuple[str, float]] = []
        if df is not None and not df.empty and "Close" in df:
            for ts, close in df["Close"].items():
                if close == close:  # NaN guard
                    rows.append((ts.date().isoformat(), float(close)))
        out[sym] = rows
    return out


def backfill_history(
    data_dir: Path,
    years: int = DEFAULT_YEARS,
    days: int | None = None,
    downloader=None,
    today: date | None = None,
) -> int:
    """Append price-only history rows for every watchlist symbol + benchmark.

    `days`, when given, overrides `years` — the daily top-up window.
    Returns the number of rows written. Never overwrites: (date, ticker)
    pairs already in history.jsonl are skipped, as is anything dated
    `today` or later.
    """
    data_dir = Path(data_dir)
    today = today or date.today()
    window = timedelta(days=days) if days is not None else timedelta(
        days=round(years * 365.25))
    start = today - window

    symbols = watch_symbols(store.load_watchlist(data_dir))
    if not symbols:
        return 0
    fetch = downloader or _yf_daily_closes
    closes = fetch(symbols, start)

    path = data_dir / HISTORY_FILE
    seen = _existing_keys(path)
    today_iso = today.isoformat()
    fresh: list[dict] = []
    for sym in symbols:
        for day, price in closes.get(sym, []):
            if day >= today_iso or (day, sym) in seen:
                continue
            seen.add((day, sym))
            fresh.append({"date": day, "ticker": sym,
                          "price": price, "source": "backfill"})
    fresh.sort(key=lambda r: (r["date"], r["ticker"]))
    if fresh:
        with path.open("a", encoding="utf-8") as fh:
            for row in fresh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)
