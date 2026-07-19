"""Deterministic quote fetcher: yfinance → quotes.json structure.

Failure semantics: a failed ticker keeps its previous values with stale=True;
with no previous record, all fields are null. On the failure path, price
(only) may be refreshed from a keyless Cboe delayed-quotes fallback:
fundamentals, analyst, and revisions data stay stale (frozen), but `price`
can be fresher than the rest — `price_source`/`price_as_of` mark that a
fallback fetch supplied it.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

import yfinance as yf

# Keyless price-only fallback for when yfinance rate-limits/outages leave a
# ticker's fundamentals stale — Cboe's delayed-quotes endpoint needs no API
# key and no fundamentals, only a ~15min-delayed last price, which is fine
# for a failure-path fallback.
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{symbol}.json"
CBOE_TIMEOUT_SECONDS = 5.0  # short: a dead fallback must not stall a refresh

FIELDS = {
    "price": "currentPrice",
    "ttm_pe": "trailingPE",
    "fwd_pe": "forwardPE",
    "ttm_eps": "trailingEps",
    "fwd_eps": "forwardEps",
    "market_cap": "marketCap",
    "high_52w": "fiftyTwoWeekHigh",
    "low_52w": "fiftyTwoWeekLow",
}

_EMPTY_REVISIONS = {
    "fwd_eps_change_90d_pct": None,
    "up_last_30d": None,
    "down_last_30d": None,
}

# Analyst price-target dispersion: a narrow pt_high/pt_low spread with a
# unanimous rec_mean is quantifiable crowding evidence for the sentiment ruling.
_ANALYST_FIELDS = {
    "pt_mean": "targetMeanPrice",
    "pt_high": "targetHighPrice",
    "pt_low": "targetLowPrice",
    "n_analysts": "numberOfAnalystOpinions",
    "rec_mean": "recommendationMean",
}

_REVISION_ROW = "+1y"  # next fiscal year — the fwd-EPS estimate the market trades on


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_revisions(eps_trend, eps_revisions) -> dict:
    """Analyst estimate-revision momentum from yfinance eps_trend/eps_revisions
    DataFrames. Change is (current - 90daysAgo) / |90daysAgo| so the sign stays
    meaningful for negative estimates. All values None when data is missing."""
    out = dict(_EMPTY_REVISIONS)
    try:
        if eps_trend is not None and _REVISION_ROW in eps_trend.index:
            current = float(eps_trend.loc[_REVISION_ROW, "current"])
            ago = float(eps_trend.loc[_REVISION_ROW, "90daysAgo"])
            if ago == ago and current == current and ago != 0:  # NaN-safe
                out["fwd_eps_change_90d_pct"] = round((current - ago) / abs(ago) * 100, 1)
    except Exception:
        pass
    try:
        if eps_revisions is not None and _REVISION_ROW in eps_revisions.index:
            up = eps_revisions.loc[_REVISION_ROW, "upLast30days"]
            down = eps_revisions.loc[_REVISION_ROW, "downLast30days"]
            out["up_last_30d"] = int(up) if up == up else None
            out["down_last_30d"] = int(down) if down == down else None
    except Exception:
        pass
    return out


def extract_next_earnings(calendar) -> str | None:
    """Next earnings date from yfinance Ticker.calendar (dict in current
    versions; 'Earnings Date' maps to a list of dates or a single date)."""
    try:
        if isinstance(calendar, dict):
            earnings = calendar.get("Earnings Date")
            if isinstance(earnings, (list, tuple)):
                return str(earnings[0]) if earnings else None
            if earnings is not None:
                return str(earnings)
    except Exception:
        pass
    return None


def _cboe_symbol(ticker: str) -> str:
    """Cboe's delayed-quote symbol convention: class shares use a dot, not a
    dash (yfinance's BRK-B -> BRK.B); plain tickers are unchanged (uppercased)."""
    return ticker.upper().replace("-", ".")


def _cboe_price(ticker: str) -> float | None:
    """Keyless ~15min-delayed last price from Cboe's delayed-quotes JSON
    endpoint. Returns None on any failure (HTTP error, missing/short body,
    absent current_price/close, ValueError) — never raises. Runs only on the
    primary-fetch failure path, so it must stay small and defensive."""
    url = CBOE_URL.format(symbol=_cboe_symbol(ticker))
    request = urllib.request.Request(url, headers={"User-Agent": "luxtock/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=CBOE_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = (json.loads(body) or {}).get("data") or {}
        price = data.get("current_price") or data.get("close")
        if not price:
            return None
        return float(price)
    except Exception:
        return None


def _fetch_price(symbol: str) -> float | None:
    info = yf.Ticker(symbol).info
    price = info.get("currentPrice")
    if price is None:
        price = info.get("regularMarketPrice")
    return price


def _null_paired(paired_cfg: dict) -> dict:
    return {
        "ticker": paired_cfg["ticker"],
        "price": None,
        "currency": paired_cfg.get("currency") or "USD",
        "fx_usd": None,
        "parity_usd": None,
        "premium_pct": None,
        "fetched_at": None,
    }


def _fetch_paired(paired_cfg: dict, us_price: float | None, prev_paired: dict | None = None) -> dict:
    """Paired-listing premium for a US ADR/line vs. its home-market line.

    ratio = underlying shares represented by ONE US share. parity_usd (per US
    share) = paired_price x fx_usd x ratio; premium_pct = (us_price / parity
    - 1) x 100. A failed sub-fetch (paired price or FX) falls back to the
    prior value for that field rather than nulling the whole block, matching
    the module's stale/prev-fallback convention.
    """
    ticker = paired_cfg["ticker"]
    ratio = float(paired_cfg["ratio"])
    currency = paired_cfg.get("currency") or "USD"
    prev_paired = prev_paired if isinstance(prev_paired, dict) else {}

    try:
        price = _fetch_price(ticker)
    except Exception:
        price = prev_paired.get("price")

    if currency == "USD":
        fx = 1.0
    else:
        try:
            fx = _fetch_price(f"{currency}USD=X")
        except Exception:
            fx = prev_paired.get("fx_usd")

    parity_usd = None
    if price is not None and fx is not None:
        parity_usd = price * fx * ratio

    premium_pct = None
    if parity_usd and us_price is not None:
        premium_pct = (us_price / parity_usd - 1) * 100

    return {
        "ticker": ticker,
        "price": price,
        "currency": currency,
        "fx_usd": fx,
        "parity_usd": parity_usd,
        "premium_pct": premium_pct,
        "fetched_at": _now(),
    }


def _fetch_one(ticker: str, paired_cfg: dict | None = None, prev_paired: dict | None = None) -> dict:
    t = yf.Ticker(ticker)
    info = t.info
    quote = {k: info.get(v) for k, v in FIELDS.items()}
    if quote["price"] is None:
        quote["price"] = info.get("regularMarketPrice")
    if quote["price"] is None:
        # yfinance often returns a near-empty info dict WITHOUT raising
        # (rate limit / transient outage). A priceless quote is a failed
        # fetch: raise so the caller's prev-value/stale fallback preserves
        # the previous good record instead of overwriting it with nulls.
        raise ValueError(f"empty quote payload for {ticker} (no price)")
    quote["analyst"] = {k: info.get(v) for k, v in _ANALYST_FIELDS.items()}
    try:
        quote["revisions"] = extract_revisions(t.eps_trend, t.eps_revisions)
    except Exception:
        quote["revisions"] = dict(_EMPTY_REVISIONS)
    try:
        quote["next_earnings"] = extract_next_earnings(t.calendar)
    except Exception:
        quote["next_earnings"] = None
    quote["stale"] = False
    quote["fetched_at"] = _now()
    if paired_cfg:
        quote["paired"] = _fetch_paired(paired_cfg, quote["price"], prev_paired)
    return quote


def fetch_quotes(
    tickers: list[str], prev: dict | None = None, paired: dict[str, dict] | None = None,
    *, fallback_price_fn=None,
) -> dict:
    """paired maps ticker -> {"ticker", "ratio", "currency"?} (from the
    watchlist's optional `paired` field) for names with a paired-listing
    premium to track; unmapped tickers get no "paired" key at all.

    fallback_price_fn (default `_cboe_price`) is called on the failure path
    only, after `old` is assembled from the previous record. A returned
    price refreshes `old["price"]` and stamps `price_source`/`price_as_of`
    (fundamentals/analyst/revisions stay stale — only price is live); a
    returned None reproduces the pre-fallback behavior exactly (previous
    price kept, no price_source key)."""
    fallback_price_fn = fallback_price_fn or _cboe_price
    prev_quotes = (prev or {}).get("quotes", {})
    paired = paired or {}
    quotes: dict = {}
    for t in tickers:
        paired_cfg = paired.get(t)
        prev_q = prev_quotes.get(t) or {}
        try:
            prev_paired = prev_q.get("paired") if paired_cfg else None
            quotes[t] = _fetch_one(t, paired_cfg, prev_paired)
        except Exception:
            old = {k: prev_q.get(k) for k in FIELDS}
            rev = prev_q.get("revisions")
            old["revisions"] = rev if isinstance(rev, dict) else dict(_EMPTY_REVISIONS)
            analyst = prev_q.get("analyst")
            old["analyst"] = (analyst if isinstance(analyst, dict)
                              else {k: None for k in _ANALYST_FIELDS})
            old["next_earnings"] = prev_q.get("next_earnings")
            old["fetched_at"] = prev_q.get("fetched_at")
            old["stale"] = True
            fallback_price = fallback_price_fn(t)
            if fallback_price is not None:
                old["price"] = fallback_price
                old["price_source"] = "cboe"
                old["price_as_of"] = _now()
            if paired_cfg:
                prev_paired = prev_q.get("paired")
                old["paired"] = prev_paired if isinstance(prev_paired, dict) else _null_paired(paired_cfg)
            quotes[t] = old
    return {"fetched_at": _now(), "quotes": quotes}
