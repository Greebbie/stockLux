"""Screen layer — market-wide candidate-discovery funnel (spec: framework/screen.md).

Stage A batch-downloads ~1y of daily closes for the scan universe and gates
on drawdown depth / price / history length (cheap, no per-ticker network
calls). Stage B throttles per-ticker fundamentals for the survivors and
computes the disqualifier flags + `depression_score`. No LLM anywhere in the
funnel: two runs against the same network responses give the same output.

Immutable style: nothing here mutates its inputs; every function returns a
new dict/list. Mirrors luxtock/quant.py's knot/piecewise-linear/renormalize
conventions.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yfinance as yf

from luxtock import store
from luxtock.quotes import FIELDS as _QUOTE_FIELDS
from luxtock.quotes import _ANALYST_FIELDS, extract_next_earnings, extract_revisions

SCREEN_FILE = "screen.json"
SCREEN_HISTORY_FILE = "screen_history.jsonl"
UNIVERSE_FILE = "universe.json"

# ---------------------------------------------------------------------------
# Stage A tunables
# ---------------------------------------------------------------------------
STAGE_A_CHUNK_SIZE = 100
STAGE_A_MIN_HISTORY_DAYS = 200
STAGE_A_MAX_LOOKBACK_DAYS = 252  # window for the trailing high used in drawdown_pct
STAGE_A_DMA_WINDOW_DAYS = 200
STAGE_A_RET_6M_LOOKBACK_DAYS = 126
STAGE_A_MIN_PRICE = 5.0
DEFAULT_MIN_DRAWDOWN_PCT = 15.0
DEFAULT_MAX_DEEP = 100
TRACK_BEATEN_DOWN_THRESHOLD = -30.0  # drawdown_pct <= this -> "beaten_down" track
# Share of the Stage-B cap reserved for the quality_discount band — without a
# reservation, depth-ranked capping lets deep drawdowns crowd the shallow
# (GOOGL-2024) shape out of Stage B entirely whenever the market is stressed.
QUALITY_TRACK_CAP_SHARE = 0.3

# ---------------------------------------------------------------------------
# Stage B tunables
# ---------------------------------------------------------------------------
STAGE_B_SLEEP_SECONDS = 0.2  # small inter-ticker throttle (network hygiene)
STAGE_B_RETRIES = 1  # one retry beyond the first attempt

# yfinance .info keys for the three quality fields ("the chassis")
_QUALITY_FIELDS = {
    "operating_margin": "operatingMargins",
    "return_on_equity": "returnOnEquity",
    "revenue_growth": "revenueGrowth",
}

# yfinance .info keys for the hypergrowth-track fields
_HYPERGROWTH_FIELDS = {
    "enterprise_value": "enterpriseValue",
    "total_revenue": "totalRevenue",
    "gross_margin": "grossMargins",
    "total_cash": "totalCash",
    "free_cashflow": "freeCashflow",
}

# revenue_growth (fraction) at/above which a no-earnings-base survivor is
# upgraded to track: hypergrowth in Stage B.
HYPERGROWTH_GROWTH_FLOOR = 0.30
# no_runway gate: a hypergrowth burner with less than this many years of cash
# left at its current burn rate is a value trap, not an opportunity.
NO_RUNWAY_YEARS = 0.75

# ---------------------------------------------------------------------------
# hard disqualifier gates
# ---------------------------------------------------------------------------
MIN_MARKET_CAP_USD = 2_000_000_000.0
REV_90D_COLLAPSE_PCT = -10.0
REV_BREADTH_EXODUS = -0.5

# ---------------------------------------------------------------------------
# CLI / bands
# ---------------------------------------------------------------------------
DEFAULT_TOP = 15
BAND_STRONG_MIN = 75.0
BAND_FAIR_MIN = 55.0
COVERAGE_NA_THRESHOLD = 0.5
RR_PROXY_DISPLAY_CAP = 10.0  # pt_low near spot makes rr_proxy explode; cap the *display* only — stored value stays honest
UNIVERSE_STALE_DAYS = 90  # index membership drifts ad hoc; a snapshot older than a quarter has likely diverged

NOTICE_CANDIDATES_ONLY = "candidates only — not analyzed, no verdicts"
NOTICE_RR_PROXY = "rr_proxy is sell-side-derived, screening signal only"


# ---------------------------------------------------------------------------
# numeric primitives (mirrors luxtock/quant.py)
# ---------------------------------------------------------------------------


def _num(x: object) -> float | None:
    """Coerce a raw JSON/yfinance value to float; reject bool and non-numerics."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


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


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ---------------------------------------------------------------------------
# Stage A — bulk price funnel
# ---------------------------------------------------------------------------


def _default_stage_a_downloader(tickers: list[str]) -> dict[str, list[float]]:
    """Batch-download ~1y of daily closes for one chunk (network)."""
    out: dict[str, list[float]] = {t: [] for t in tickers}
    if not tickers:
        return out
    try:
        df = yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                          auto_adjust=False, progress=False, threads=True)
    except Exception:
        return out
    if df is None or df.empty:
        return out
    for t in tickers:
        try:
            series = df[t]["Close"] if len(tickers) > 1 else df["Close"]
        except Exception:
            continue
        out[t] = [float(c) for c in series.dropna().tolist()]
    return out


def compute_stage_a_features(closes: list[float]) -> dict | None:
    """Stage A feature table (see framework/screen.md) from a chronological
    (oldest -> newest) list of daily closes. None if fewer than
    STAGE_A_MIN_HISTORY_DAYS points — insufficient history fails Gate A."""
    if len(closes) < STAGE_A_MIN_HISTORY_DAYS:
        return None
    price = closes[-1]
    lookback = closes[-min(STAGE_A_MAX_LOOKBACK_DAYS, len(closes)):]
    high = max(lookback)
    drawdown_pct = (price / high - 1) * 100 if high else None
    window = closes[-STAGE_A_DMA_WINDOW_DAYS:]
    mean_200 = sum(window) / len(window)
    dist_200dma_pct = (price / mean_200 - 1) * 100 if mean_200 else None
    ret_6m_pct = (price / closes[-STAGE_A_RET_6M_LOOKBACK_DAYS] - 1) * 100
    return {
        "price": price,
        "drawdown_pct": drawdown_pct,
        "dist_200dma_pct": dist_200dma_pct,
        "ret_6m_pct": ret_6m_pct,
    }


def gate_a(features: dict, min_drawdown_pct: float) -> bool:
    """Gate A: drawdown_pct <= -min_drawdown_pct; price >= $5. (History-length
    is enforced upstream — compute_stage_a_features returns None otherwise.)"""
    d = features["drawdown_pct"]
    price = features["price"]
    return (d is not None and d <= -min_drawdown_pct
            and price is not None and price >= STAGE_A_MIN_PRICE)


def track_for(drawdown_pct: float) -> str:
    """Display-metadata tag — both tracks share one scoring formula."""
    return "beaten_down" if drawdown_pct <= TRACK_BEATEN_DOWN_THRESHOLD else "quality_discount"


def stage_a_scan(
    tickers: list[str], *, downloader=None,
    min_drawdown_pct: float = DEFAULT_MIN_DRAWDOWN_PCT,
    max_deep: int = DEFAULT_MAX_DEEP,
    chunk_size: int = STAGE_A_CHUNK_SIZE,
) -> dict:
    """Batch price funnel: chunked download -> per-ticker features -> Gate A
    -> rank by drawdown depth -> cap at max_deep. Returns
    {"survivors": [(ticker, features), ...] (capped, deepest first),
     "survivor_count": int (uncapped), "cap_dropped": int}."""
    downloader = downloader or _default_stage_a_downloader
    closes_by_ticker: dict[str, list[float]] = {}
    for chunk in _chunks(tickers, chunk_size):
        closes_by_ticker.update(downloader(chunk))

    survivors: list[tuple[str, dict]] = []
    for t in tickers:
        closes = closes_by_ticker.get(t) or []
        features = compute_stage_a_features(closes)
        if features is None:
            continue
        if gate_a(features, min_drawdown_pct):
            survivors.append((t, features))

    survivors.sort(key=lambda tf: tf[1]["drawdown_pct"])  # deepest (most negative) first
    capped = _cap_with_track_quota(survivors, max_deep)
    return {
        "survivors": capped,
        "survivor_count": len(survivors),
        "cap_dropped": max(0, len(survivors) - len(capped)),
    }


def _cap_with_track_quota(
    survivors: list[tuple[str, dict]], max_deep: int,
) -> list[tuple[str, dict]]:
    """Cap Stage-A survivors at max_deep while reserving QUALITY_TRACK_CAP_SHARE
    of the slots for the quality_discount band (deepest-first within each band;
    unused reservation backfills the other band). Result stays deepest-first."""
    if len(survivors) <= max_deep:
        return survivors
    quality = [tf for tf in survivors if track_for(tf[1]["drawdown_pct"]) == "quality_discount"]
    beaten = [tf for tf in survivors if track_for(tf[1]["drawdown_pct"]) == "beaten_down"]
    reserved = min(len(quality), round(max_deep * QUALITY_TRACK_CAP_SHARE))
    picked_beaten = beaten[:max_deep - reserved]
    picked_quality = quality[:max_deep - len(picked_beaten)]
    picked = picked_beaten + picked_quality
    return sorted(picked, key=lambda tf: tf[1]["drawdown_pct"])


# ---------------------------------------------------------------------------
# Stage B — per-ticker fundamentals (throttled)
# ---------------------------------------------------------------------------


def _default_stage_b_fetch_one(ticker: str) -> dict:
    """Fetch the field set `luxtock refresh` extracts plus the three quality
    fields, via a single yf.Ticker(...).info call. Reuses quotes.py's field
    maps/extractors rather than widening quotes.json's own schema."""
    t = yf.Ticker(ticker)
    info = t.info
    price = info.get("currentPrice")
    if price is None:
        price = info.get("regularMarketPrice")
    if price is None:
        raise ValueError(f"empty quote payload for {ticker} (no price)")
    quote = {k: info.get(v) for k, v in _QUOTE_FIELDS.items()}
    quote["price"] = price
    quote["analyst"] = {k: info.get(v) for k, v in _ANALYST_FIELDS.items()}
    try:
        quote["revisions"] = extract_revisions(t.eps_trend, t.eps_revisions)
    except Exception:
        quote["revisions"] = None
    try:
        quote["next_earnings"] = extract_next_earnings(t.calendar)
    except Exception:
        quote["next_earnings"] = None
    for out_key, info_key in _QUALITY_FIELDS.items():
        quote[out_key] = info.get(info_key)
    for out_key, info_key in _HYPERGROWTH_FIELDS.items():
        quote[out_key] = info.get(info_key)
    return quote


STAGE_B_PROGRESS_EVERY = 20  # progress_fn cadence during the slow Stage-B loop


def stage_b_fetch(tickers: list[str], *, fetch_one=None, sleep_fn=None,
                  progress_fn=None) -> dict[str, dict]:
    """Throttled per-ticker fundamentals fetch with one retry. Returns
    ticker -> {"quote": <dict or None>, "fetch_failed": <reason or None>} —
    a failed ticker keeps quote=None (visible, never silently dropped).
    progress_fn (optional) receives a status line every
    STAGE_B_PROGRESS_EVERY tickers — this loop is the slow, throttled part
    of a scan and must not look hung from the CLI."""
    fetch_one = fetch_one or _default_stage_b_fetch_one
    sleep_fn = sleep_fn or time.sleep
    out: dict[str, dict] = {}
    for i, t in enumerate(tickers):
        if progress_fn and i > 0 and i % STAGE_B_PROGRESS_EVERY == 0:
            progress_fn(f"stage B: {i}/{len(tickers)} tickers fetched")
        if i > 0:
            sleep_fn(STAGE_B_SLEEP_SECONDS)
        quote, reason = None, None
        for attempt in range(STAGE_B_RETRIES + 1):
            if attempt > 0:
                sleep_fn(STAGE_B_SLEEP_SECONDS)
            try:
                quote = fetch_one(t)
                reason = None
                break
            except Exception as e:
                reason = str(e) or e.__class__.__name__
        out[t] = {"quote": quote, "fetch_failed": reason}
    return out


def _derive_fundamentals(quote: dict | None, price: float | None) -> dict:
    """Fundamentals + derived screen-only values (rev_breadth, rr_proxy,
    pe_compression, eps_growth_pct, peg_like, ev_sales, gs_like,
    runway_years) — see framework/screen.md."""
    quote = quote or {}
    analyst = quote.get("analyst") if isinstance(quote.get("analyst"), dict) else {}
    revisions = quote.get("revisions") if isinstance(quote.get("revisions"), dict) else {}

    fwd_eps = _num(quote.get("fwd_eps"))
    ttm_eps = _num(quote.get("ttm_eps"))
    fwd_pe = _num(quote.get("fwd_pe"))
    ttm_pe = _num(quote.get("ttm_pe"))
    market_cap = _num(quote.get("market_cap"))
    pt_mean = _num(analyst.get("pt_mean"))
    pt_low = _num(analyst.get("pt_low"))
    pt_high = _num(analyst.get("pt_high"))
    n_analysts = _num(analyst.get("n_analysts"))
    rec_mean = _num(analyst.get("rec_mean"))
    rev_90d_pct = _num(revisions.get("fwd_eps_change_90d_pct"))
    up = _num(revisions.get("up_last_30d"))
    down = _num(revisions.get("down_last_30d"))
    next_earnings = quote.get("next_earnings")
    operating_margin = _num(quote.get("operating_margin"))
    return_on_equity = _num(quote.get("return_on_equity"))
    revenue_growth = _num(quote.get("revenue_growth"))
    enterprise_value = _num(quote.get("enterprise_value"))
    total_revenue = _num(quote.get("total_revenue"))
    gross_margin = _num(quote.get("gross_margin"))
    total_cash = _num(quote.get("total_cash"))
    free_cashflow = _num(quote.get("free_cashflow"))

    rev_breadth = None
    if up is not None and down is not None and (up + down) != 0:
        rev_breadth = (up - down) / (up + down)

    rr_proxy = None
    if (pt_mean is not None and pt_low is not None and price is not None
            and price > pt_low):
        rr_proxy = (pt_mean - price) / (price - pt_low)

    pe_compression = None
    if fwd_pe is not None and ttm_pe is not None and ttm_pe > 0:
        pe_compression = fwd_pe / ttm_pe

    eps_growth_pct = None
    if fwd_eps is not None and ttm_eps is not None and ttm_eps > 0:
        eps_growth_pct = (fwd_eps / ttm_eps - 1) * 100

    peg_like = None
    if eps_growth_pct is not None and fwd_pe is not None and fwd_pe > 0:
        peg_like = fwd_pe / max(eps_growth_pct, 1.0)

    ev_sales = None
    if enterprise_value is not None and total_revenue is not None and total_revenue > 0:
        ev_sales = enterprise_value / total_revenue

    gs_like = None
    if ev_sales is not None and revenue_growth is not None and revenue_growth > 0:
        gs_like = ev_sales / (revenue_growth * 100)

    runway_years = None
    if total_cash is not None and free_cashflow is not None and free_cashflow < 0:
        runway_years = total_cash / abs(free_cashflow)

    return {
        "fwd_eps": fwd_eps, "ttm_eps": ttm_eps, "fwd_pe": fwd_pe, "ttm_pe": ttm_pe,
        "market_cap": market_cap,
        "pt_mean": pt_mean, "pt_low": pt_low, "pt_high": pt_high,
        "n_analysts": n_analysts, "rec_mean": rec_mean,
        "rev_90d_pct": rev_90d_pct, "up_last_30d": up, "down_last_30d": down,
        "next_earnings": next_earnings,
        "operating_margin": operating_margin, "return_on_equity": return_on_equity,
        "revenue_growth": revenue_growth,
        "enterprise_value": enterprise_value, "total_revenue": total_revenue,
        "gross_margin": gross_margin, "total_cash": total_cash,
        "free_cashflow": free_cashflow,
        "rev_breadth": rev_breadth, "rr_proxy": rr_proxy,
        "pe_compression": pe_compression, "eps_growth_pct": eps_growth_pct,
        "peg_like": peg_like, "ev_sales": ev_sales, "gs_like": gs_like,
        "runway_years": runway_years,
    }


def resolve_track(stage_a_track: str, fundamentals: dict) -> str:
    """Stage B track finalization: upgrade to hypergrowth when there is no
    (or a non-positive) earnings base AND revenue is compounding >=30%/yr.
    All other survivors keep their Stage-A drawdown-based tag."""
    fwd_eps = fundamentals.get("fwd_eps")
    revenue_growth = fundamentals.get("revenue_growth")
    no_earnings = fwd_eps is None or fwd_eps <= 0
    high_growth = revenue_growth is not None and revenue_growth >= HYPERGROWTH_GROWTH_FLOOR
    return "hypergrowth" if no_earnings and high_growth else stage_a_track


# ---------------------------------------------------------------------------
# hard disqualifiers (value-trap gates)
# ---------------------------------------------------------------------------


def compute_flags(fundamentals: dict, track: str = "beaten_down") -> list[str]:
    """Hard disqualifier gates — track-dependent. `track: hypergrowth` swaps
    `no_earnings_base`/`estimates_collapsing` (undefined without an earnings
    base) for the burn-side traps `no_runway`/`growth_unpriced`;
    `revision_exodus`/`too_small` apply to every track."""
    flags: list[str] = []
    if track == "hypergrowth":
        free_cashflow = fundamentals["free_cashflow"]
        runway_years = fundamentals["runway_years"]
        if (free_cashflow is not None and free_cashflow < 0
                and runway_years is not None and runway_years < NO_RUNWAY_YEARS):
            flags.append("no_runway")
        if fundamentals["gs_like"] is None:
            flags.append("growth_unpriced")
    else:
        fwd_eps = fundamentals["fwd_eps"]
        if fwd_eps is None or fwd_eps <= 0:
            flags.append("no_earnings_base")
        rev_90d = fundamentals["rev_90d_pct"]
        if rev_90d is not None and rev_90d < REV_90D_COLLAPSE_PCT:
            flags.append("estimates_collapsing")
    rev_breadth = fundamentals["rev_breadth"]
    if rev_breadth is not None and rev_breadth < REV_BREADTH_EXODUS:
        flags.append("revision_exodus")
    market_cap = fundamentals["market_cap"]
    if market_cap is not None and market_cap < MIN_MARKET_CAP_USD:
        flags.append("too_small")
    return flags


# ---------------------------------------------------------------------------
# depression_score components — all knot tables ascending in x
# ---------------------------------------------------------------------------

# depth_component (weight 0.15): narrative reads deep-to-shallow in the spec
# (d ≤ −75 → 40 flat; then rising to a peak at −60; falling back to a lower
# shallow-band floor at −15) — the cliff at exactly −75 is intentional (a
# hard floor once drawdown "reads as damage", not a smooth continuation).
DEPTH_KNOTS: tuple[tuple[float, float], ...] = (
    (-75.0, 70.0), (-60.0, 100.0), (-45.0, 80.0), (-30.0, 40.0), (-15.0, 25.0),
)
DEPTH_FLOOR_THRESHOLD = -75.0
DEPTH_FLOOR_SCORE = 40.0

MARGIN_KNOTS: tuple[tuple[float, float], ...] = ((5.0, 20.0), (15.0, 60.0), (30.0, 90.0))
ROE_KNOTS: tuple[tuple[float, float], ...] = ((5.0, 20.0), (15.0, 60.0), (25.0, 90.0))
MARGIN_CEILING, MARGIN_CEILING_SCORE = 30.0, 100.0
ROE_CEILING, ROE_CEILING_SCORE = 25.0, 100.0

# growth: the discontinuity at g=0 is spec-intentional (shrinking revenue is
# a different regime, not a lower shade of growing).
GROWTH_KNOTS: tuple[tuple[float, float], ...] = ((0.0, 40.0), (10.0, 70.0), (25.0, 100.0))
GROWTH_FLOOR_THRESHOLD, GROWTH_FLOOR_SCORE = 0.0, 10.0

PE_COMPRESSION_KNOTS: tuple[tuple[float, float], ...] = (
    (0.5, 100.0), (0.8, 70.0), (1.0, 50.0), (1.3, 20.0),
)
PE_COMPRESSION_CEILING, PE_COMPRESSION_CEILING_SCORE = 1.3, 0.0

PEG_KNOTS: tuple[tuple[float, float], ...] = ((0.8, 100.0), (1.2, 70.0), (2.0, 40.0), (3.0, 10.0))
PEG_CEILING, PEG_CEILING_SCORE = 3.0, 0.0

REV_KNOTS: tuple[tuple[float, float], ...] = ((-10.0, 0.0), (0.0, 50.0), (15.0, 85.0), (50.0, 100.0))
RR_PROXY_KNOTS: tuple[tuple[float, float], ...] = ((0.5, 10.0), (1.0, 40.0), (2.0, 70.0), (4.0, 100.0))

QUALITY_WEIGHT, VALUE_WEIGHT, RESILIENCE_WEIGHT = 0.25, 0.25, 0.25
DEPTH_WEIGHT, RR_PROXY_WEIGHT = 0.15, 0.10


def _depth_component(d: float) -> float:
    if d <= DEPTH_FLOOR_THRESHOLD:
        return DEPTH_FLOOR_SCORE
    return _interp(d, DEPTH_KNOTS)


def _margin_component(m: float) -> float:
    if m > MARGIN_CEILING:
        return MARGIN_CEILING_SCORE
    return _interp(m, MARGIN_KNOTS)


def _roe_component(e: float) -> float:
    if e > ROE_CEILING:
        return ROE_CEILING_SCORE
    return _interp(e, ROE_KNOTS)


def _growth_component(g: float) -> float:
    if g <= GROWTH_FLOOR_THRESHOLD:
        return GROWTH_FLOOR_SCORE
    return _interp(g, GROWTH_KNOTS)


def _compression_component(f: float) -> float:
    if f > PE_COMPRESSION_CEILING:
        return PE_COMPRESSION_CEILING_SCORE
    return _interp(f, PE_COMPRESSION_KNOTS)


def _peg_component(x: float) -> float:
    if x > PEG_CEILING:
        return PEG_CEILING_SCORE
    return _interp(x, PEG_KNOTS)


def _band(score: float | None, coverage: float) -> str:
    if coverage < COVERAGE_NA_THRESHOLD or score is None:
        return "n/a"
    if score >= BAND_STRONG_MIN:
        return "strong"
    if score >= BAND_FAIR_MIN:
        return "fair"
    return "weak"


# ---------------------------------------------------------------------------
# hypergrowth-track score components (separate formula, same 0-100 scale;
# reuses _depth_component / RR_PROXY_KNOTS from the standard track)
# ---------------------------------------------------------------------------

GS_KNOTS: tuple[tuple[float, float], ...] = (
    (0.08, 100.0), (0.15, 70.0), (0.30, 40.0), (0.50, 10.0),
)
GS_CEILING, GS_CEILING_SCORE = 0.5, 0.0

GROWTH_INTENSITY_KNOTS: tuple[tuple[float, float], ...] = ((30.0, 50.0), (60.0, 80.0), (100.0, 100.0))

# gross-margin component: the cliff at m<=20 is spec-intentional (sub-20%
# gross margin is a different business, not a lower shade of this one).
MARGIN_HG_KNOTS: tuple[tuple[float, float], ...] = ((20.0, 30.0), (50.0, 70.0), (80.0, 100.0))
MARGIN_HG_FLOOR_THRESHOLD, MARGIN_HG_FLOOR_SCORE = 20.0, 10.0

# runway component: knots top out at 80, not 100 — a burner never scores
# 100 (the cap is intentional); FCF >= 0 (no runway question) is handled
# separately in _runway_component.
RUNWAY_KNOTS: tuple[tuple[float, float], ...] = ((0.5, 0.0), (1.0, 30.0), (2.0, 60.0), (3.0, 80.0))
RUNWAY_NO_BURN_SCORE = 100.0

GS_WEIGHT, GROWTH_INTENSITY_WEIGHT = 0.30, 0.25
MARGIN_HG_WEIGHT, RUNWAY_WEIGHT = 0.15, 0.10
HG_DEPTH_WEIGHT, HG_RR_PROXY_WEIGHT = 0.10, 0.10


def _gs_component(x: float) -> float:
    if x > GS_CEILING:
        return GS_CEILING_SCORE
    return _interp(x, GS_KNOTS)


def _growth_intensity_component(g: float) -> float:
    return _interp(g, GROWTH_INTENSITY_KNOTS)


def _margin_hg_component(m: float) -> float:
    if m <= MARGIN_HG_FLOOR_THRESHOLD:
        return MARGIN_HG_FLOOR_SCORE
    return _interp(m, MARGIN_HG_KNOTS)


def _runway_component(free_cashflow: float | None, runway_years: float | None) -> float | None:
    """FCF >= 0 -> no runway question, ceiling score. FCF < 0 with an
    unknown runway_years (e.g. missing total_cash) -> component missing
    (None), not assumed safe or assumed a burner."""
    if free_cashflow is not None and free_cashflow >= 0:
        return RUNWAY_NO_BURN_SCORE
    if runway_years is None:
        return None
    return _interp(runway_years, RUNWAY_KNOTS)


def compute_hypergrowth_score(fundamentals: dict, stage_a_features: dict) -> dict:
    """Hypergrowth-track depression_score — replaces the earnings-anchored
    standard components entirely. See framework/screen.md."""
    x = fundamentals.get("gs_like")
    gs_c = _gs_component(x) if x is not None else None

    g = fundamentals.get("revenue_growth")
    growth_intensity_c = _growth_intensity_component(g * 100) if g is not None else None

    m = fundamentals.get("gross_margin")
    margin_c = _margin_hg_component(m * 100) if m is not None else None

    runway_c = _runway_component(fundamentals.get("free_cashflow"), fundamentals.get("runway_years"))

    d = stage_a_features.get("drawdown_pct")
    depth_c = _depth_component(d) if d is not None else None

    rr = fundamentals.get("rr_proxy")
    rr_c = _interp(rr, RR_PROXY_KNOTS) if rr is not None else None

    components = (
        (gs_c, GS_WEIGHT), (growth_intensity_c, GROWTH_INTENSITY_WEIGHT),
        (margin_c, MARGIN_HG_WEIGHT), (runway_c, RUNWAY_WEIGHT),
        (depth_c, HG_DEPTH_WEIGHT), (rr_c, HG_RR_PROXY_WEIGHT),
    )
    score = _weighted_avg(components)
    coverage = sum(w for v, w in components if v is not None)
    return {
        "gs_component": gs_c,
        "growth_intensity_component": growth_intensity_c,
        "margin_component": margin_c,
        "runway_component": runway_c,
        "depth_component": depth_c,
        "rr_proxy_component": rr_c,
        "depression_score": score,
        "coverage": coverage,
        "band": _band(score, coverage),
    }


def compute_depression_score(fundamentals: dict, stage_a_features: dict) -> dict:
    """depression_score + its four sub-components — see framework/screen.md.
    Weighting philosophy: valuation (quality+value) carries half the score;
    drawdown depth is deliberately a minor input."""
    d = stage_a_features.get("drawdown_pct")
    depth_c = _depth_component(d) if d is not None else None

    m, e, g = (fundamentals.get("operating_margin"), fundamentals.get("return_on_equity"),
               fundamentals.get("revenue_growth"))
    margin_c = _margin_component(m * 100) if m is not None else None
    roe_c = _roe_component(e * 100) if e is not None else None
    growth_c = _growth_component(g * 100) if g is not None else None
    quality_c = _weighted_avg(((margin_c, 0.4), (roe_c, 0.3), (growth_c, 0.3)))

    f_pe, x_peg = fundamentals.get("pe_compression"), fundamentals.get("peg_like")
    compression_c = _compression_component(f_pe) if f_pe is not None else None
    peg_c = _peg_component(x_peg) if x_peg is not None else None
    value_c = _weighted_avg(((compression_c, 0.5), (peg_c, 0.5)))

    r, b = fundamentals.get("rev_90d_pct"), fundamentals.get("rev_breadth")
    rev_c = _interp(r, REV_KNOTS) if r is not None else None
    breadth_c = (b + 1) / 2 * 100 if b is not None else None
    resilience_c = _weighted_avg(((rev_c, 0.6), (breadth_c, 0.4)))

    rr = fundamentals.get("rr_proxy")
    rr_c = _interp(rr, RR_PROXY_KNOTS) if rr is not None else None

    components = (
        (quality_c, QUALITY_WEIGHT), (value_c, VALUE_WEIGHT), (resilience_c, RESILIENCE_WEIGHT),
        (depth_c, DEPTH_WEIGHT), (rr_c, RR_PROXY_WEIGHT),
    )
    score = _weighted_avg(components)
    coverage = sum(w for v, w in components if v is not None)
    return {
        "quality_component": quality_c,
        "value_component": value_c,
        "resilience_component": resilience_c,
        "depth_component": depth_c,
        "rr_proxy_component": rr_c,
        "depression_score": score,
        "coverage": coverage,
        "band": _band(score, coverage),
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _load_universe(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"as_of": None, "source": None, "tickers": [], "extra_tickers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def universe_age_days(universe_as_of: str | None, today: date | None = None) -> int | None:
    """Whole days between universe.json's `as_of` (ISO date string) and
    `today` — None when `as_of` is missing/unparseable. `today` param is for
    tests; defaults to the real UTC date."""
    if not universe_as_of:
        return None
    try:
        as_of_date = date.fromisoformat(universe_as_of)
    except (TypeError, ValueError):
        return None
    today = today or datetime.now(timezone.utc).date()
    return (today - as_of_date).days


def _merged_universe_tickers(universe: dict) -> list[str]:
    """Main index-membership tickers + the user-curated extra_tickers side
    list, merged and deduped (main list wins ordering on collisions)."""
    main_tickers = universe.get("tickers") or []
    extra = universe.get("extra_tickers")
    extra_tickers = extra.get("tickers") or [] if isinstance(extra, dict) else []
    return list(dict.fromkeys(list(main_tickers) + list(extra_tickers)))


def build_screen(
    data_dir: Path,
    *,
    universe_path: Path | None = None,
    min_drawdown_pct: float = DEFAULT_MIN_DRAWDOWN_PCT,
    max_deep: int = DEFAULT_MAX_DEEP,
    stage_a_downloader=None,
    stage_b_fetch_one=None,
    sleep_fn=None,
    progress_fn=None,
) -> dict:
    """Run the full Stage A -> Stage B -> score funnel, write data/screen.json
    (atomic) and return the same dict — see framework/screen.md for schema.
    Raises ValueError when the merged universe is empty — an empty scan must
    never silently overwrite a previous screen.json (a mistyped --universe
    path would otherwise read as "the market has no candidates")."""
    data_dir = Path(data_dir)
    universe_path = Path(universe_path) if universe_path else data_dir / UNIVERSE_FILE
    universe = _load_universe(universe_path)
    universe_tickers = _merged_universe_tickers(universe)
    if not universe_tickers:
        raise ValueError(f"universe at {universe_path} is missing or has no tickers")

    watchlist_tickers = {s["ticker"] for s in store.load_watchlist(data_dir).get("stocks", [])}
    scan_tickers = [t for t in universe_tickers if t not in watchlist_tickers]

    if progress_fn:
        progress_fn(f"stage A: downloading ~1y closes for {len(scan_tickers)} tickers…")
    stage_a = stage_a_scan(scan_tickers, downloader=stage_a_downloader,
                            min_drawdown_pct=min_drawdown_pct, max_deep=max_deep)
    survivor_tickers = [t for t, _ in stage_a["survivors"]]
    if progress_fn:
        progress_fn(f"stage A: {stage_a['survivor_count']} qualified "
                    f"(drawdown ≥ {min_drawdown_pct:.0f}%), {len(survivor_tickers)} to stage B")
    stage_b = stage_b_fetch(survivor_tickers, fetch_one=stage_b_fetch_one, sleep_fn=sleep_fn,
                            progress_fn=progress_fn)

    results = []
    for ticker, features in stage_a["survivors"]:
        b = stage_b[ticker]
        fundamentals = _derive_fundamentals(b["quote"], features["price"])
        track = resolve_track(track_for(features["drawdown_pct"]), fundamentals)
        flags = compute_flags(fundamentals, track)
        if track == "hypergrowth":
            score_info = compute_hypergrowth_score(fundamentals, features)
            components = {
                "gs_component": score_info["gs_component"],
                "growth_intensity_component": score_info["growth_intensity_component"],
                "margin_component": score_info["margin_component"],
                "runway_component": score_info["runway_component"],
                "depth_component": score_info["depth_component"],
                "rr_proxy_component": score_info["rr_proxy_component"],
            }
        else:
            score_info = compute_depression_score(fundamentals, features)
            components = {
                "quality_component": score_info["quality_component"],
                "value_component": score_info["value_component"],
                "resilience_component": score_info["resilience_component"],
                "depth_component": score_info["depth_component"],
                "rr_proxy_component": score_info["rr_proxy_component"],
            }
        results.append({
            "ticker": ticker,
            "track": track,
            "fetch_failed": b["fetch_failed"],
            "features": features,
            "fundamentals": fundamentals,
            "flags": flags,
            "disqualified": len(flags) > 0,
            "components": components,
            "depression_score": score_info["depression_score"],
            "coverage": score_info["coverage"],
            "band": score_info["band"],
        })

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "universe_as_of": universe.get("as_of"),
        "universe_size": len(universe_tickers),
        "stage_a_survivors": stage_a["survivor_count"],
        "stage_b_cap_dropped": stage_a["cap_dropped"],
        "results": results,
    }
    store.write_json_atomic(data_dir / SCREEN_FILE, out)
    _append_screen_history(data_dir / SCREEN_HISTORY_FILE, out)
    return out


def _append_screen_history(path: Path, out: dict) -> None:
    """Append one compact point-in-time row per QUALIFIED candidate — the
    ledger that lets a future retrospect grade the screen's hit rate the way
    calibrate grades memo targets. screen.json is overwritten every run;
    this file is the memory. Append-mode (never a rewrite), one JSON line
    per candidate, same discipline as data/history.jsonl."""
    rows = [
        {
            "date": out["computed_at"][:10],
            "computed_at": out["computed_at"],
            "ticker": r["ticker"],
            "track": r["track"],
            "price": (r.get("features") or {}).get("price"),
            "drawdown_pct": (r.get("features") or {}).get("drawdown_pct"),
            "depression_score": r["depression_score"],
            "band": r["band"],
        }
        for r in out["results"] if not r["disqualified"]
    ]
    if not rows:
        return
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
