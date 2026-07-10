"""Module 3 -- generic price-alert checks against the watchlist.

Stateless by design: `run_checks` writes nothing to disk and re-evaluates
every rule from scratch on every call. Nothing is remembered between runs,
so an unresolved condition (e.g. a price still below invalidation) re-fires
on every invocation -- that is intentional, not a bug, since this module
has no storage layer to dedupe against.

Rules evaluated per watchlist ticker (only for names with both a latest
memo and a current quote price -- names missing either are silently
skipped, since there is nothing to check):

1. entry_plan present and action in (enter, wait_for_pullback): price at or
   below a tranche level fires one "entry_tranche" alert for the deepest
   (lowest) tranche reached.
2. entry_plan present: price at or below invalidation fires "invalidation"
   (warning) and supersedes rule 1 -- only invalidation is emitted, never
   both.
3. buy_range present: price strictly below buy_range[0] fires "below_floor".
4. holding true and buy_range present: price at or above
   buy_range[1] * 1.25 fires "trim_threshold" (warning) -- the
   methodology's trim rule.
5. price_targets present: price at or below bear fires "through_bear"
   (warning); price at or above bull fires "at_bull_target" (info).
6. `band_flip` (framework/quant.md v1.1 addition #4): for each ticker with
   two or more distinct dated rows in data/quant_history.jsonl, compare its
   two most recent distinct dates; if both carry a non-null band and the
   band differs, fire an info-level "band_flip" alert. Reads the ledger
   defensively -- a missing/unparseable file yields no band_flip alerts.

Portfolio-level flags from `portfolio.portfolio_report` are appended last,
mapped to the same {ticker, kind, level, detail} shape with
ticker="PORTFOLIO".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import portfolio, store

TRIM_MULTIPLE = 1.25
ENTRY_ACTIONS = ("enter", "wait_for_pullback")


def _load_quotes(data_dir: Path) -> dict:
    p = Path(data_dir) / "quotes.json"
    if not p.exists():
        return {"quotes": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _price(quotes: dict, ticker: str) -> float | None:
    q = quotes.get("quotes", {}).get(ticker)
    price = q.get("price") if q else None
    return price if isinstance(price, (int, float)) else None


def _alert(ticker: str, kind: str, level: str, detail: str) -> dict:
    return {"ticker": ticker, "kind": kind, "level": level, "detail": detail}


def _entry_plan_alerts(ticker: str, price: float, action: str | None, entry_plan: dict) -> list[dict]:
    tranches = entry_plan.get("tranches")
    invalidation = entry_plan.get("invalidation")

    if isinstance(invalidation, (int, float)) and price <= invalidation:
        return [_alert(
            ticker, "invalidation", "warning",
            f"{ticker} price {price:.2f} is at/below invalidation "
            f"{invalidation:.2f} -- entry plan invalid, forces re-analysis",
        )]

    if action not in ENTRY_ACTIONS:
        return []
    if not (isinstance(tranches, list) and tranches):
        return []

    reached = [t for t in tranches if isinstance(t, (int, float)) and price <= t]
    if not reached:
        return []
    deepest = min(reached)
    return [_alert(
        ticker, "entry_tranche", "info",
        f"{ticker} price {price:.2f} has reached entry tranche {deepest:.2f} "
        f"(deepest of {len(tranches)} tranches)",
    )]


def _buy_range_alerts(ticker: str, price: float, holding: bool, buy_range: list) -> list[dict]:
    if not (isinstance(buy_range, list) and len(buy_range) == 2):
        return []
    floor, top = buy_range[0], buy_range[1]
    alerts: list[dict] = []
    if isinstance(floor, (int, float)) and price < floor:
        alerts.append(_alert(
            ticker, "below_floor", "info",
            f"{ticker} price {price:.2f} is below the buy-range floor {floor:.2f}",
        ))
    if holding and isinstance(top, (int, float)):
        trim_level = top * TRIM_MULTIPLE
        if price >= trim_level:
            alerts.append(_alert(
                ticker, "trim_threshold", "warning",
                f"{ticker} price {price:.2f} is at/above {TRIM_MULTIPLE:.2f}x the "
                f"buy-range top {top:.2f} ({trim_level:.2f}) -- trim rule triggered",
            ))
    return alerts


def _price_target_alerts(ticker: str, price: float, price_targets: dict) -> list[dict]:
    alerts: list[dict] = []
    bear = price_targets.get("bear")
    bull = price_targets.get("bull")
    if isinstance(bear, (int, float)) and price <= bear:
        alerts.append(_alert(
            ticker, "through_bear", "warning",
            f"{ticker} price {price:.2f} is at/below the bear target {bear:.2f}",
        ))
    if isinstance(bull, (int, float)) and price >= bull:
        alerts.append(_alert(
            ticker, "at_bull_target", "info",
            f"{ticker} price {price:.2f} is at/above the bull target {bull:.2f}",
        ))
    return alerts


def _ticker_alerts(ticker: str, price: float, holding: bool, meta: dict) -> list[dict]:
    alerts: list[dict] = []

    entry_plan = meta.get("entry_plan")
    if isinstance(entry_plan, dict):
        alerts.extend(_entry_plan_alerts(ticker, price, meta.get("action"), entry_plan))

    buy_range = meta.get("buy_range")
    alerts.extend(_buy_range_alerts(ticker, price, holding, buy_range))

    price_targets = meta.get("price_targets")
    if isinstance(price_targets, dict):
        alerts.extend(_price_target_alerts(ticker, price, price_targets))

    return alerts


def _load_quant_history(data_dir: Path) -> list[dict]:
    """Read data/quant_history.jsonl defensively: missing file -> [];
    unparseable / malformed (missing date or ticker) lines are skipped."""
    p = Path(data_dir) / "quant_history.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not row.get("date") or not row.get("ticker"):
            continue
        rows.append(row)
    return rows


def _fmt_composite(v) -> str:
    return f"{v:.1f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else "n/a"


def _band_flip_alerts(data_dir: Path) -> list[dict]:
    rows = _load_quant_history(data_dir)

    by_ticker: dict[str, dict[str, dict]] = {}
    for row in rows:
        # last occurrence for a given (ticker, date) wins -- mirrors the
        # writer's "same-date rerun replaces that date's rows" contract.
        by_ticker.setdefault(row["ticker"], {})[str(row["date"])] = row

    alerts: list[dict] = []
    for ticker, by_date in by_ticker.items():
        dates_desc = sorted(by_date, reverse=True)
        if len(dates_desc) < 2:
            continue
        newest, prev = by_date[dates_desc[0]], by_date[dates_desc[1]]
        new_band, old_band = newest.get("band"), prev.get("band")
        if new_band is None or old_band is None or new_band == old_band:
            continue
        alerts.append(_alert(
            ticker, "band_flip", "info",
            f"{ticker} setup band {old_band} → {new_band} "
            f"(composite {_fmt_composite(prev.get('composite'))}→"
            f"{_fmt_composite(newest.get('composite'))})",
        ))
    return alerts


def _portfolio_alerts(data_dir: Path) -> list[dict]:
    report = portfolio.portfolio_report(data_dir)
    return [
        _alert("PORTFOLIO", flag["kind"], flag["level"], flag["detail"])
        for flag in report["flags"]
    ]


def run_checks(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    wl = store.load_watchlist(data_dir)
    quotes = _load_quotes(data_dir)

    alerts: list[dict] = []
    for entry in wl["stocks"]:
        ticker = entry["ticker"]
        memo = store.latest_memo(data_dir, ticker)
        if not memo:
            continue
        price = _price(quotes, ticker)
        if price is None:
            continue
        alerts.extend(_ticker_alerts(ticker, price, bool(entry.get("holding")), memo["meta"]))

    alerts.extend(_band_flip_alerts(data_dir))
    alerts.extend(_portfolio_alerts(data_dir))

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "alerts": alerts,
    }
