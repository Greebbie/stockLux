"""Deterministic quote fetcher: yfinance → quotes.json structure.

Failure semantics: a failed ticker keeps its previous values with stale=True;
with no previous record, all fields are null.
"""
from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

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


def _fetch_one(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info
    quote = {k: info.get(v) for k, v in FIELDS.items()}
    if quote["price"] is None:
        quote["price"] = info.get("regularMarketPrice")
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
    return quote


def fetch_quotes(tickers: list[str], prev: dict | None = None) -> dict:
    prev_quotes = (prev or {}).get("quotes", {})
    quotes: dict = {}
    for t in tickers:
        try:
            quotes[t] = _fetch_one(t)
        except Exception:
            prev_q = prev_quotes.get(t) or {}
            old = {k: prev_q.get(k) for k in FIELDS}
            rev = prev_q.get("revisions")
            old["revisions"] = rev if isinstance(rev, dict) else dict(_EMPTY_REVISIONS)
            analyst = prev_q.get("analyst")
            old["analyst"] = (analyst if isinstance(analyst, dict)
                              else {k: None for k in _ANALYST_FIELDS})
            old["next_earnings"] = prev_q.get("next_earnings")
            old["fetched_at"] = prev_q.get("fetched_at")
            old["stale"] = True
            quotes[t] = old
    return {"fetched_at": _now(), "quotes": quotes}
