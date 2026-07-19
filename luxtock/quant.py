"""Module 1 — deterministic feature extraction and setup scoring.

Pure functions over data/quotes.json, data/flows.json, data/history.jsonl and
memo frontmatter (see framework/quant.md for the specification). No network
calls, no LLM calls: two runs on the same files give the same output.

Immutable style: nothing here mutates its inputs; every function returns a
new dict.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from luxtock import store

QUANT_FILE = "quant.json"
QUANT_HISTORY_FILE = "quant_history.jsonl"

# A "d14" delta only deserves the name when the two rows are roughly two
# weeks apart: under the floor there is too little separation to call it a
# delta at all; over the ceiling a sparse history would silently relabel a
# month-plus move as a two-week one.
D14_MIN_GAP_DAYS = 7
D14_MAX_GAP_DAYS = 21

# The full feature vector — order mirrors framework/quant.md's Features table.
# `coverage` (score_features) is the fraction of these keys that are non-null.
FEATURE_KEYS: tuple[str, ...] = (
    "price", "valuation_gap_pct", "gap_to_floor_pct", "rr_ratio", "ev_return_pct",
    "rev_90d_pct", "rev_breadth",
    "rsi_14", "dist_50dma_pct", "dist_200dma_pct", "atr_pct_14", "rel_strength_3m",
    "short_pct_float", "put_call_oi_ratio", "inst_pct",
    "rec_mean", "n_analysts", "pt_spread_pct", "pt_upside_pct",
    "d14_price_pct", "d14_short_pct_float", "d14_rsi",
    "paired_premium_pct",
)

# ---------------------------------------------------------------------------
# numeric primitives
# ---------------------------------------------------------------------------


def _num(x: object) -> float | None:
    """Coerce a raw JSON/YAML value to float; reject bool and non-numerics."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ratio_pct(numerator: object, denominator: object) -> float | None:
    """(numerator / denominator - 1) x 100, or None if either side is unusable."""
    n, d = _num(numerator), _num(denominator)
    if n is None or d is None or d == 0:
        return None
    return (n / d - 1) * 100


def _interp(x: float, knots: tuple[tuple[float, float], ...]) -> float:
    """Piecewise-linear interpolation; flat extrapolation beyond the end knots."""
    if x <= knots[0][0]:
        return knots[0][1]
    if x >= knots[-1][0]:
        return knots[-1][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return knots[-1][1]  # unreachable


def _weighted_avg(components: tuple[tuple[float | None, float], ...]) -> float | None:
    """Weighted mean over the available (non-None) components — dropping a
    missing component and renormalizing the remaining weights."""
    available = [(v, w) for v, w in components if v is not None]
    total_w = sum(w for _, w in available)
    if not available or total_w == 0:
        return None
    return sum(v * w for v, w in available) / total_w


# ---------------------------------------------------------------------------
# feature extraction
# ---------------------------------------------------------------------------


def _pt_spread_pct(analyst: dict) -> float | None:
    pt_high, pt_low, pt_mean = (
        _num(analyst.get("pt_high")), _num(analyst.get("pt_low")), _num(analyst.get("pt_mean")),
    )
    if pt_high is None or pt_low is None or pt_mean is None or pt_mean == 0:
        return None
    return (pt_high - pt_low) / pt_mean * 100


def _d14(history_rows: list[dict], ticker: str) -> tuple[float | None, float | None, float | None]:
    """Deltas vs. the history row closest to 14 days before the latest row.

    None (all three) if fewer than 2 rows exist for this ticker, or if the
    row closest to 14 days before the latest one is itself outside the
    [D14_MIN_GAP_DAYS, D14_MAX_GAP_DAYS] window (too little separation to
    call it a "d14" delta, or so much that it isn't one).
    """
    rows = sorted(
        (r for r in history_rows if r.get("ticker") == ticker and r.get("date")),
        key=lambda r: r["date"],
    )
    if len(rows) < 2:
        return None, None, None
    latest = rows[-1]
    latest_date = date.fromisoformat(latest["date"])
    best = min(
        rows[:-1],
        key=lambda r: abs((latest_date - date.fromisoformat(r["date"])).days - 14),
    )
    gap_days = (latest_date - date.fromisoformat(best["date"])).days
    if gap_days < D14_MIN_GAP_DAYS or gap_days > D14_MAX_GAP_DAYS:
        return None, None, None

    d_price = _ratio_pct(latest.get("price"), best.get("price"))
    ls, bs = _num(latest.get("short_pct_float")), _num(best.get("short_pct_float"))
    d_short = ls - bs if ls is not None and bs is not None else None
    lr, br = _num(latest.get("rsi_14")), _num(best.get("rsi_14"))
    d_rsi = lr - br if lr is not None and br is not None else None
    return d_price, d_short, d_rsi


def compute_features(
    ticker: str,
    quotes_entry: dict | None,
    flows_entry: dict | None,
    memo_meta: dict | None,
    history_rows: list[dict],
) -> dict:
    """Pure feature extraction — see framework/quant.md Features table.

    Every field is nullable; missing/malformed inputs degrade to None rather
    than raising, per the "missing data degrades gracefully" principle.
    """
    quotes_entry = quotes_entry or {}
    flows_entry = flows_entry or {}
    memo_meta = memo_meta or {}

    price = _num(quotes_entry.get("price"))
    analyst = quotes_entry.get("analyst") if isinstance(quotes_entry.get("analyst"), dict) else {}
    revisions = (
        quotes_entry.get("revisions") if isinstance(quotes_entry.get("revisions"), dict) else {}
    )
    trend = flows_entry.get("trend") if isinstance(flows_entry.get("trend"), dict) else {}
    paired = quotes_entry.get("paired") if isinstance(quotes_entry.get("paired"), dict) else {}

    buy_range = memo_meta.get("buy_range")
    buy_low = buy_high = None
    if isinstance(buy_range, list) and len(buy_range) == 2:
        buy_low, buy_high = buy_range[0], buy_range[1]

    price_targets = memo_meta.get("price_targets")
    price_targets = price_targets if isinstance(price_targets, dict) else {}
    bear, base, bull = price_targets.get("bear"), price_targets.get("base"), price_targets.get("bull")
    p_bear, p_base, p_bull = (
        price_targets.get("p_bear"), price_targets.get("p_base"), price_targets.get("p_bull"),
    )

    n_base, n_bear = _num(base), _num(bear)
    rr_ratio = None
    if price is not None and n_base is not None and n_bear is not None and price > n_bear:
        rr_ratio = (n_base - price) / (price - n_bear)

    ev_return_pct = None
    tier_nums = (_num(bear), _num(base), _num(bull), _num(p_bear), _num(p_base), _num(p_bull))
    if price and all(v is not None for v in tier_nums):
        n_bear2, n_base2, n_bull2, n_pbear, n_pbase, n_pbull = tier_nums
        weighted = n_pbear * n_bear2 + n_pbase * n_base2 + n_pbull * n_bull2
        ev_return_pct = (weighted / price - 1) * 100

    up, down = _num(revisions.get("up_last_30d")), _num(revisions.get("down_last_30d"))
    rev_breadth = None
    if up is not None and down is not None and (up + down) != 0:
        rev_breadth = (up - down) / (up + down)

    d14_price_pct, d14_short_pct_float, d14_rsi = _d14(history_rows, ticker)

    return {
        "price": price,
        "valuation_gap_pct": _ratio_pct(price, buy_high),
        "gap_to_floor_pct": _ratio_pct(price, buy_low),
        "rr_ratio": rr_ratio,
        "ev_return_pct": ev_return_pct,
        "rev_90d_pct": _num(revisions.get("fwd_eps_change_90d_pct")),
        "rev_breadth": rev_breadth,
        "rsi_14": _num(trend.get("rsi_14")),
        "dist_50dma_pct": _num(trend.get("dist_50dma_pct")),
        "dist_200dma_pct": _num(trend.get("dist_200dma_pct")),
        "atr_pct_14": _num(trend.get("atr_pct_14")),
        "rel_strength_3m": _num(trend.get("rel_strength_3m")),
        "short_pct_float": _num(flows_entry.get("short_pct_float")),
        "put_call_oi_ratio": _num(flows_entry.get("put_call_oi_ratio")),
        "inst_pct": _num(flows_entry.get("inst_pct")),
        "rec_mean": _num(analyst.get("rec_mean")),
        "n_analysts": _num(analyst.get("n_analysts")),
        "pt_spread_pct": _pt_spread_pct(analyst),
        "pt_upside_pct": _ratio_pct(analyst.get("pt_mean"), price),
        "d14_price_pct": d14_price_pct,
        "d14_short_pct_float": d14_short_pct_float,
        "d14_rsi": d14_rsi,
        # Informational only — paired-listing premium (e.g. US ADR vs. its
        # home-market line) is never used in a sub-score; the framework's
        # scoring weights are frozen by governance (framework/quant.md).
        "paired_premium_pct": _num(paired.get("premium_pct")),
    }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

GAP_KNOTS: tuple[tuple[float, float], ...] = ((-20.0, 100.0), (0.0, 60.0), (15.0, 30.0), (40.0, 0.0))
EV_KNOTS: tuple[tuple[float, float], ...] = ((-20.0, 0.0), (0.0, 50.0), (30.0, 100.0))
REV_KNOTS: tuple[tuple[float, float], ...] = ((-20.0, 0.0), (0.0, 40.0), (50.0, 90.0), (100.0, 100.0))
RSI_KNOTS: tuple[tuple[float, float], ...] = (
    (25.0, 50.0), (35.0, 80.0), (50.0, 60.0), (70.0, 30.0), (85.0, 0.0),
)


def _dma_component(d: float) -> float:
    if d < -10:
        return 60.0
    if d < -3:
        return 80.0
    if d <= 3:
        return 70.0
    if d <= 15:
        return 50.0
    return 25.0


def _rs_component(rs: float) -> float:
    return 65.0 if rs > 0 else 40.0


def _valuation_score(f: dict) -> float | None:
    g, e = f["valuation_gap_pct"], f["ev_return_pct"]
    gap_c = _interp(g, GAP_KNOTS) if g is not None else None
    ev_c = _interp(e, EV_KNOTS) if e is not None else None
    return _weighted_avg(((gap_c, 0.7), (ev_c, 0.3)))


def _momentum_score(f: dict) -> float | None:
    r, b = f["rev_90d_pct"], f["rev_breadth"]
    rev_c = _interp(r, REV_KNOTS) if r is not None else None
    breadth_c = (b + 1) / 2 * 100 if b is not None else None
    return _weighted_avg(((rev_c, 0.6), (breadth_c, 0.4)))


def _positioning_score(f: dict) -> float | None:
    rec_mean, pt_spread = f["rec_mean"], f["pt_spread_pct"]
    crowding_c = None
    if rec_mean is not None and pt_spread is not None:
        crowding_c = clamp01((rec_mean - 1.0) / 1.5) * 60 + clamp01(pt_spread / 80) * 40
    putcall, short = f["put_call_oi_ratio"], f["short_pct_float"]
    putcall_c = clamp01(putcall / 3) * 100 if putcall is not None else None
    short_c = clamp01(short / 0.15) * 100 if short is not None else None
    return _weighted_avg(((crowding_c, 0.5), (putcall_c, 0.3), (short_c, 0.2)))


def _trend_score(f: dict) -> float | None:
    rsi, dma, rs = f["rsi_14"], f["dist_50dma_pct"], f["rel_strength_3m"]
    rsi_c = _interp(rsi, RSI_KNOTS) if rsi is not None else None
    dma_c = _dma_component(dma) if dma is not None else None
    rs_c = _rs_component(rs) if rs is not None else None
    return _weighted_avg(((rsi_c, 0.5), (dma_c, 0.3), (rs_c, 0.2)))


def _band(composite: float | None, coverage: float, valuation: float | None) -> str | None:
    # Band requires a valuation sub-score: a name with no memo (no buy_range/
    # targets) can otherwise earn a band purely from trend/positioning, which
    # would make its composite look comparable to fully-scored names.
    if composite is None or coverage < 0.35 or valuation is None:
        return None
    if composite >= 70:
        return "strong"
    if composite >= 50:
        return "fair"
    return "weak"


def score_features(features: dict) -> dict:
    """Sub-scores, composite, band, coverage and components_used — see
    framework/quant.md."""
    valuation = _valuation_score(features)
    momentum = _momentum_score(features)
    positioning = _positioning_score(features)
    trend = _trend_score(features)
    composite = _weighted_avg((
        (valuation, 0.40), (momentum, 0.25), (positioning, 0.15), (trend, 0.20),
    ))
    coverage = sum(1 for k in FEATURE_KEYS if features.get(k) is not None) / len(FEATURE_KEYS)
    components_used = sorted(
        name for name, value in (
            ("valuation", valuation), ("momentum", momentum),
            ("positioning", positioning), ("trend", trend),
        ) if value is not None
    )
    available_subscores = [v for v in (valuation, momentum, positioning, trend) if v is not None]
    dispersion = (
        max(available_subscores) - min(available_subscores)
        if len(available_subscores) >= 2 else None
    )
    mixed = dispersion is not None and dispersion >= 40
    return {
        "valuation": valuation,
        "momentum": momentum,
        "positioning": positioning,
        "trend": trend,
        "composite": composite,
        "band": _band(composite, coverage, valuation),
        "coverage": coverage,
        "components_used": components_used,
        "dispersion": dispersion,
        "mixed": mixed,
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _history_rows_for(data_dir: Path, ticker: str) -> list[dict]:
    path = Path(data_dir) / "history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("ticker") == ticker:
            rows.append(row)
    return rows


def _quant_history_row(today: str, ticker: str, entry: dict) -> dict:
    """One quant_history.jsonl row for a ticker — see framework/quant.md's
    v1.1 "Score history" addition for the schema."""
    features, scores = entry["features"], entry["scores"]
    return {
        "date": today,
        "ticker": ticker,
        "composite": scores["composite"],
        "band": scores["band"],
        "valuation": scores["valuation"],
        "momentum": scores["momentum"],
        "positioning": scores["positioning"],
        "trend": scores["trend"],
        "coverage": scores["coverage"],
        "dispersion": scores["dispersion"],
        "price": features["price"],
        "valuation_gap_pct": features["valuation_gap_pct"],
        "ev_return_pct": features["ev_return_pct"],
        "paired_premium_pct": features["paired_premium_pct"],
    }


def _append_quant_history(data_dir: Path, today: str, tickers: dict) -> None:
    """Append one row per ticker to data/quant_history.jsonl. A same-date
    rerun replaces that date's rows for freshness: existing rows are kept
    verbatim (byte-for-byte) unless their `date` matches today, in which
    case they are dropped before appending the fresh rows. Lines that fail
    to parse as JSON are preserved untouched — never destroyed."""
    path = Path(data_dir) / QUANT_HISTORY_FILE
    kept_lines: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if row.get("date") != today:
                kept_lines.append(line)

    fresh_lines = [
        json.dumps(_quant_history_row(today, ticker, entry), ensure_ascii=False)
        for ticker, entry in tickers.items()
    ]

    all_lines = kept_lines + fresh_lines
    text = "\n".join(all_lines)
    if all_lines:
        text += "\n"
    store.write_text_atomic(path, text)


def build_quant(data_dir: Path) -> dict:
    """Load quotes/flows/history + the latest memo per watchlist ticker,
    score every name, write data/quant.json, append a same-date-replacing
    snapshot to data/quant_history.jsonl, and return the quant.json dict."""
    data_dir = Path(data_dir)
    watchlist = store.load_watchlist(data_dir)
    quotes = _load_json(data_dir / "quotes.json").get("quotes", {})
    flows = _load_json(data_dir / "flows.json").get("flows", {})

    tickers: dict = {}
    for stock in watchlist.get("stocks", []):
        ticker = stock["ticker"]
        memo = store.latest_memo(data_dir, ticker)
        memo_meta = memo["meta"] if memo else None
        history_rows = _history_rows_for(data_dir, ticker)
        features = compute_features(
            ticker, quotes.get(ticker), flows.get(ticker), memo_meta, history_rows,
        )
        tickers[ticker] = {"features": features, "scores": score_features(features)}

    now = datetime.now(timezone.utc)
    result = {
        "computed_at": now.isoformat(),
        "tickers": tickers,
    }
    out_path = data_dir / QUANT_FILE
    store.write_json_atomic(out_path, result)
    _append_quant_history(data_dir, now.date().isoformat(), tickers)
    return result
