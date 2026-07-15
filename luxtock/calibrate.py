"""Module 3 — probability calibration ledger (data/calibration.json).

Grades matured memo price targets against realized history and tracks
immature memos so the ledger is useful from day one. Pure/deterministic:
reads memo frontmatter (via luxtock.store), data/history.jsonl and
data/quotes.json, never mutates its inputs. See framework/quant.md
"Module 3 — luxtock/calibrate.py" for the spec this implements.

`score_calibration` also reports benchmark-relative excess-return metrics
(n_excess, mean_excess_return_pct, excess_hit_rate) per framework/quant.md
v1.2.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from luxtock import store

MATURITY_DAYS = 365
REALIZED_MATCH_WINDOW_DAYS = 14
_TIERS = ("bear", "base", "bull")
_TARGET_KEYS = ("bear", "base", "bull")
_PROB_KEYS = ("p_bear", "p_base", "p_bull")

# v1.1 addition #3 (framework/quant.md "v1.1 additions") — score calibration:
# join data/quant_history.jsonl rows with future prices to grade "win rate by
# setup score". Forward windows are ≥N days (nearest row at/after N days out).
FORWARD_WINDOWS = (30, 90)
_BANDS = ("strong", "fair", "weak")
_MIN_QUARTILE_ROWS = 8

# v1.2 addition #2 (framework/quant.md "v1.2 additions") — the benchmark
# ORIGIN price must be anchored near the quant_history row's own date; a
# benchmark whose history starts later than this tolerance would otherwise
# get its origin nearest-at/after-matched to the same row as the forward
# leg, silently zeroing bench_ret and inflating excess to equal the
# absolute return.
BENCH_ORIGIN_TOLERANCE_DAYS = 7


def _meta_date(meta: dict) -> date | None:
    v = meta.get("date")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (ValueError, TypeError):
        return None


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _full_price_targets(pt) -> bool:
    """A memo carries 'full' price targets when it has bear/base/bull AND
    the tier probabilities (the v2 contract) — the shape Brier grading
    needs. Grandfathered pre-policy memos lacking probabilities cannot be
    graded and are excluded from the matured ledger."""
    if not isinstance(pt, dict):
        return False
    return all(_is_number(pt.get(k)) for k in (*_TARGET_KEYS, *_PROB_KEYS))


def _has_bear_base_bull(pt) -> bool:
    if not isinstance(pt, dict):
        return False
    return all(_is_number(pt.get(k)) for k in _TARGET_KEYS)


def _load_quotes(data_dir: Path) -> dict:
    p = Path(data_dir) / "quotes.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_history(data_dir: Path) -> list[dict]:
    p = Path(data_dir) / "history.jsonl"
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
        d = _parse_iso_date(row.get("date"))
        if d is None or not row.get("ticker"):
            continue
        rows.append({"date": d, "ticker": row["ticker"], "price": row.get("price")})
    return rows


def _parse_iso_date(v) -> date | None:
    try:
        return date.fromisoformat(str(v))
    except (ValueError, TypeError):
        return None


def _load_quant_history(data_dir: Path) -> list[dict]:
    """Read data/quant_history.jsonl defensively.

    Missing file -> []. Unparseable / malformed (missing date or ticker)
    lines are skipped rather than raising. Row schema (framework/quant.md):
    date, ticker, composite, band, valuation, momentum, positioning, trend,
    coverage, dispersion, price, valuation_gap_pct, ev_return_pct,
    paired_premium_pct.
    """
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
        if not isinstance(row, dict):
            continue
        d = _parse_iso_date(row.get("date"))
        ticker = row.get("ticker")
        if d is None or not ticker:
            continue
        rows.append({**row, "date": d, "ticker": ticker})
    return rows


def _forward_price_dated(
    ticker: str, target_date: date,
    history_rows: list[dict], quant_history_rows: list[dict],
) -> tuple[float, date] | tuple[None, None]:
    """Nearest (price, date) at/after target_date for ticker; history.jsonl
    first, falling back to quant_history's own price field."""
    for rows in (history_rows, quant_history_rows):
        candidates = [
            r for r in rows
            if r["ticker"] == ticker and r["date"] >= target_date and _is_number(r.get("price"))
        ]
        if candidates:
            best = min(candidates, key=lambda r: r["date"])
            return best["price"], best["date"]
    return None, None


def _forward_price(
    ticker: str, target_date: date,
    history_rows: list[dict], quant_history_rows: list[dict],
) -> float | None:
    """Nearest price at/after target_date for ticker; history.jsonl first,
    falling back to quant_history's own price field."""
    price, _ = _forward_price_dated(ticker, target_date, history_rows, quant_history_rows)
    return price


def _forward_return_pct(origin_price, future_price) -> float | None:
    if not _is_number(origin_price) or not _is_number(future_price) or origin_price == 0:
        return None
    return (future_price / origin_price - 1) * 100


def _bucket_stats(pairs: list[tuple[float, float | None]]) -> dict:
    rets = [r for r, _ in pairs]
    n = len(rets)
    excesses = [e for _, e in pairs if e is not None]
    n_x = len(excesses)
    return {
        "n": n,
        "mean_return_pct": sum(rets) / n,
        "hit_rate": sum(1 for r in rets if r > 0) / n,
        "n_excess": n_x,
        "mean_excess_return_pct": (sum(excesses) / n_x) if n_x else None,
        "excess_hit_rate": (sum(1 for e in excesses if e > 0) / n_x) if n_x else None,
    }


def _bucket_by_band(scored: list[tuple[dict, float, float | None]]) -> list[dict]:
    buckets: dict[str, list[tuple[float, float | None]]] = {b: [] for b in _BANDS}
    for row, ret, excess in scored:
        band = row.get("band")
        if band in buckets:
            buckets[band].append((ret, excess))
    return [
        {"band": band, **_bucket_stats(pairs)}
        for band in _BANDS if (pairs := buckets[band])
    ]


def _bucket_by_quartile(scored: list[tuple[dict, float, float | None]]) -> list[dict]:
    valid = [t for t in scored if _is_number(t[0].get("composite"))]
    if len(valid) < _MIN_QUARTILE_ROWS:
        return []
    valid.sort(key=lambda t: t[0]["composite"])
    n = len(valid)
    base, remainder = divmod(n, 4)
    sizes = [base + 1 if i < remainder else base for i in range(4)]
    buckets = []
    idx = 0
    for qi, size in enumerate(sizes, start=1):
        chunk = valid[idx: idx + size]
        idx += size
        if not chunk:
            continue
        pairs = [(ret, excess) for _, ret, excess in chunk]
        buckets.append({"quartile": f"Q{qi}", **_bucket_stats(pairs)})
    return buckets


def _window_calibration(
    days: int, quant_history_rows: list[dict], history_rows: list[dict],
    benchmark_map: dict[str, str],
) -> dict:
    scored: list[tuple[dict, float, float | None]] = []
    for row in quant_history_rows:
        target_date = row["date"] + timedelta(days=days)
        future_price = _forward_price(row["ticker"], target_date, history_rows, quant_history_rows)
        ret = _forward_return_pct(row.get("price"), future_price)
        if ret is None:
            continue
        excess = None
        bench = benchmark_map.get(row["ticker"])
        if bench:
            b0, b0_date = _forward_price_dated(bench, row["date"], history_rows, quant_history_rows)
            if b0_date is not None and (b0_date - row["date"]).days > BENCH_ORIGIN_TOLERANCE_DAYS:
                b0 = None
            b1 = _forward_price(bench, target_date, history_rows, quant_history_rows)
            bench_ret = _forward_return_pct(b0, b1)
            if bench_ret is not None:
                excess = ret - bench_ret
        scored.append((row, ret, excess))
    return {
        "n_scored": len(scored),
        "by_band": _bucket_by_band(scored),
        "by_quartile": _bucket_by_quartile(scored),
    }


def _score_calibration(
    quant_history_rows: list[dict], history_rows: list[dict],
    benchmark_map: dict[str, str],
) -> dict:
    """framework/quant.md v1.1 addition #3 + v1.2 excess-return metrics.
    Empty-safe: 0 quant_history rows yields n_rows=0 and empty buckets."""
    return {
        "n_rows": len(quant_history_rows),
        "windows": {
            f"{days}d": _window_calibration(
                days, quant_history_rows, history_rows, benchmark_map)
            for days in FORWARD_WINDOWS
        },
    }


def _ticker_dirs(data_dir: Path) -> list[str]:
    d = Path(data_dir) / "analyses"
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def _realized_tier(realized: float, bear: float, base: float, bull: float) -> str:
    lo_mid = (bear + base) / 2
    hi_mid = (base + bull) / 2
    if realized <= lo_mid:
        return "bear"
    if realized >= hi_mid:
        return "bull"
    return "base"


def _brier(probs: dict, realized_tier: str) -> float:
    return sum(
        (probs[f"p_{tier}"] - (1.0 if tier == realized_tier else 0.0)) ** 2
        for tier in _TIERS
    )


def _find_realized_price(
    history_rows: list[dict], ticker: str, maturity_date: date,
) -> tuple[float | None, str | None]:
    candidates = [
        r for r in history_rows
        if r["ticker"] == ticker and _is_number(r.get("price"))
    ]
    if not candidates:
        return None, f"no history rows for {ticker}"
    best = min(candidates, key=lambda r: abs((r["date"] - maturity_date).days))
    diff = abs((best["date"] - maturity_date).days)
    if diff > REALIZED_MATCH_WINDOW_DAYS:
        return None, (
            f"nearest history row ({best['date'].isoformat()}) is {diff}d from "
            f"maturity {maturity_date.isoformat()} — outside the "
            f"±{REALIZED_MATCH_WINDOW_DAYS}d window"
        )
    return best["price"], None


def _path_stats(
    rows: list[dict], price_at_analysis,
) -> tuple[float | None, float | None]:
    if not rows or not _is_number(price_at_analysis) or price_at_analysis == 0:
        return None, None
    pct = [
        (r["price"] / price_at_analysis - 1) * 100
        for r in rows if _is_number(r.get("price"))
    ]
    if not pct:
        return None, None
    return min(pct), max(pct)


def _build_matured_entry(
    ticker: str, meta: dict, as_of: date, history_rows: list[dict],
) -> dict | None:
    memo_date = _meta_date(meta)
    if memo_date is None:
        return None
    maturity_date = memo_date + timedelta(days=MATURITY_DAYS)
    if maturity_date > as_of:
        return None  # not matured yet

    pt = meta.get("price_targets")
    if not _full_price_targets(pt):
        return None  # can't be graded; not "full" price targets

    bear, base, bull = pt["bear"], pt["base"], pt["bull"]
    probs = {k: pt[k] for k in _PROB_KEYS}
    price_at_analysis = meta.get("price_at_analysis")

    realized_price, note = _find_realized_price(history_rows, ticker, maturity_date)
    if realized_price is None:
        return {
            "ticker": ticker,
            "memo_date": memo_date.isoformat(),
            "targets": {"bear": bear, "base": base, "bull": bull},
            "probs": probs,
            "realized_price": None,
            "realized_tier": None,
            "brier": None,
            "mae_pct": None,
            "mfe_pct": None,
            "note": note,
        }

    realized_tier = _realized_tier(realized_price, bear, base, bull)
    brier = _brier(probs, realized_tier)
    window_rows = [
        r for r in history_rows
        if r["ticker"] == ticker and memo_date <= r["date"] <= maturity_date
    ]
    mae_pct, mfe_pct = _path_stats(window_rows, price_at_analysis)

    return {
        "ticker": ticker,
        "memo_date": memo_date.isoformat(),
        "targets": {"bear": bear, "base": base, "bull": bull},
        "probs": probs,
        "realized_price": realized_price,
        "realized_tier": realized_tier,
        "brier": brier,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "note": None,
    }


def _build_tracking_entry(ticker: str, meta: dict, as_of: date, quotes: dict) -> dict | None:
    memo_date = _meta_date(meta)
    if memo_date is None:
        return None
    maturity_date = memo_date + timedelta(days=MATURITY_DAYS)
    if maturity_date <= as_of:
        return None  # already matured — belongs on the matured ledger, not tracking

    pt = meta.get("price_targets")
    if not _has_bear_base_bull(pt):
        return None

    bear, base, bull = pt["bear"], pt["base"], pt["bull"]
    q = (quotes.get("quotes") or {}).get(ticker) or {}
    current_price = q.get("price")
    if not _is_number(current_price):
        return None

    months_elapsed = round((as_of - memo_date).days / 30.4375, 1)
    if bull == bear:
        pct_between_bear_bull = None
    else:
        pct_between_bear_bull = max(0.0, min(100.0, (current_price - bear) / (bull - bear) * 100))

    return {
        "ticker": ticker,
        "memo_date": memo_date.isoformat(),
        "months_elapsed": months_elapsed,
        "current_price": current_price,
        "pct_between_bear_bull": pct_between_bear_bull,
        "above_base": current_price >= base,
    }


def _write_calibration(data_dir: Path, result: dict) -> None:
    p = Path(data_dir) / "calibration.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def calibrate(data_dir: Path, as_of: date | None = None) -> dict:
    """Grade matured memo price targets and track immature ones.

    Writes data/calibration.json and returns the same dict. Empty-safe:
    with no matured memos, aggregate is {"n": 0, "mean_brier": None} and
    tracking is still reported. Also joins data/quant_history.jsonl (if
    present) against forward prices to produce `score_calibration` — mean
    forward return and hit-rate by band / composite quartile at +30d and
    +90d (framework/quant.md v1.1 addition #3); empty-safe when the ledger
    is absent or thin.
    """
    data_dir = Path(data_dir)
    as_of = as_of or date.today()

    quotes = _load_quotes(data_dir)
    history_rows = _load_history(data_dir)
    tickers = _ticker_dirs(data_dir)

    matured: list[dict] = []
    for ticker in tickers:
        for memo_path in store.list_memos(data_dir, ticker):
            meta, _ = store.parse_frontmatter(memo_path.read_text(encoding="utf-8"))
            entry = _build_matured_entry(ticker, meta, as_of, history_rows)
            if entry is not None:
                matured.append(entry)

    tracking: list[dict] = []
    for ticker in tickers:
        memo = store.latest_memo(data_dir, ticker)
        if memo is None:
            continue
        entry = _build_tracking_entry(ticker, memo["meta"], as_of, quotes)
        if entry is not None:
            tracking.append(entry)

    briers = [m["brier"] for m in matured if m["brier"] is not None]
    aggregate = {
        "n": len(briers),
        "mean_brier": (sum(briers) / len(briers)) if briers else None,
    }

    quant_history_rows = _load_quant_history(data_dir)
    watchlist = store.load_watchlist(data_dir)
    benchmark_map = {
        s["ticker"]: (s.get("benchmark") or "SPY")
        for s in watchlist.get("stocks", []) if s.get("ticker")
    }
    score_calibration = _score_calibration(quant_history_rows, history_rows, benchmark_map)

    result = {
        "as_of": as_of.isoformat(),
        "matured": matured,
        "tracking": tracking,
        "aggregate": aggregate,
        "score_calibration": score_calibration,
    }
    _write_calibration(data_dir, result)
    return result
