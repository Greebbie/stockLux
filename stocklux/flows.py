"""Flow-data fetcher + volume/OBV accumulation signals. Deterministic, no LLM.

Honesty note (for humans and agents reading this data): these are all proxy
signals — 13F lags ~45 days, short interest updates biweekly, no dark-pool or
realtime flow. "Smart money quietly accumulating" is always an inference.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

_EMPTY_SIGNALS = {
    "up_down_volume_ratio": None,
    "obv_slope_20": None,
    "obv_slope_60": None,
    "accumulation_hint": False,
}

_EMPTY_TREND = {
    "dist_50dma_pct": None,
    "dist_200dma_pct": None,
    "rsi_14": None,
    "atr_pct_14": None,
    "rel_strength_3m": None,
    "benchmark": None,
}

# rel_strength_3m default benchmark; a watchlist entry's `benchmark` field
# (e.g. SMH for a semi, XLU for a utility) overrides it so relative strength
# reads against the sector, not the broad market's beta.
_BENCHMARK = "SPY"

_FLOW_FIELDS = {
    "shares_short": "sharesShort",
    "short_pct_float": "shortPercentOfFloat",
    "short_ratio": "shortRatio",
    "inst_pct": "heldPercentInstitutions",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_volume_signals(history: pd.DataFrame) -> dict:
    df = history.tail(120).copy()
    if len(df) < 60 or "Close" not in df or "Volume" not in df:
        return dict(_EMPTY_SIGNALS)
    delta = df["Close"].diff()
    up_vol = df.loc[delta > 0, "Volume"].mean()
    down_vol = df.loc[delta < 0, "Volume"].mean()
    ratio = (
        round(float(up_vol / down_vol), 2)
        if down_vol and not pd.isna(down_vol) and not pd.isna(up_vol)
        else None
    )

    direction = delta.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (df["Volume"] * direction).fillna(0).cumsum()
    mean_vol = float(df["Volume"].tail(60).mean())

    def slope(n: int) -> float:
        s = obv.tail(n).to_numpy(dtype=float)
        k = np.polyfit(np.arange(len(s)), s, 1)[0]
        return round(float(k / mean_vol), 3)

    s20, s60 = slope(20), slope(60)
    px_change_60 = float(df["Close"].iloc[-1] / df["Close"].iloc[-60] - 1)
    hint = abs(px_change_60) < 0.05 and s20 > 0 and (ratio or 0) > 1.2
    return {
        "up_down_volume_ratio": ratio,
        "obv_slope_20": s20,
        "obv_slope_60": s60,
        "accumulation_hint": bool(hint),
    }


def compute_trend_signals(
    history: pd.DataFrame, bench_history: pd.DataFrame | None = None
) -> dict:
    """Timing-layer inputs for the entry plan: trend, momentum, volatility,
    relative strength. All values are None when there is not enough history."""
    out = dict(_EMPTY_TREND)
    if history is None or "Close" not in history:
        return out
    close = history["Close"].dropna()
    if len(close) < 60:
        return out
    px = float(close.iloc[-1])

    out["dist_50dma_pct"] = round((px / float(close.tail(50).mean()) - 1) * 100, 1)
    if len(close) >= 200:
        out["dist_200dma_pct"] = round((px / float(close.tail(200).mean()) - 1) * 100, 1)

    delta = close.diff().tail(14)
    gains = float(delta.clip(lower=0).mean())
    losses = float((-delta.clip(upper=0)).mean())
    if losses == 0:
        out["rsi_14"] = 100.0 if gains > 0 else 50.0
    else:
        out["rsi_14"] = round(100 - 100 / (1 + gains / losses), 1)

    if {"High", "Low"} <= set(history.columns):
        prev_close = history["Close"].shift(1)
        tr = pd.concat(
            [
                history["High"] - history["Low"],
                (history["High"] - prev_close).abs(),
                (history["Low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        out["atr_pct_14"] = round(float(tr.tail(14).mean()) / px * 100, 1)

    if bench_history is not None and "Close" in bench_history:
        bench = bench_history["Close"].dropna()
        if len(close) >= 64 and len(bench) >= 64:
            own_3m = px / float(close.iloc[-64]) - 1
            bench_3m = float(bench.iloc[-1]) / float(bench.iloc[-64]) - 1
            out["rel_strength_3m"] = round((own_3m - bench_3m) * 100, 1)
    return out


def _fetch_one(ticker: str, bench_symbol: str = _BENCHMARK,
               bench_history: pd.DataFrame | None = None) -> dict:
    t = yf.Ticker(ticker)
    info = t.info  # raises on failure; fetch_flows handles the fallback
    flow = {k: info.get(v) for k, v in _FLOW_FIELDS.items()}
    flow.update({"insider_net_6m": None, "put_call_oi_ratio": None,
                 "signals": dict(_EMPTY_SIGNALS), "trend": dict(_EMPTY_TREND),
                 "stale": False, "fetched_at": _now()})
    try:
        ins = t.insider_transactions
        if ins is not None and not ins.empty and {"Shares", "Text"} <= set(ins.columns):
            buys = ins.loc[ins["Text"].str.contains("Buy", case=False, na=False), "Shares"].sum()
            sells = ins.loc[ins["Text"].str.contains("Sale", case=False, na=False), "Shares"].sum()
            flow["insider_net_6m"] = int(buys - sells)
    except Exception:
        pass
    try:
        expirations = t.options
        if expirations:
            chain = t.option_chain(expirations[0])
            call_oi = float(chain.calls["openInterest"].sum())
            put_oi = float(chain.puts["openInterest"].sum())
            if call_oi:
                flow["put_call_oi_ratio"] = round(put_oi / call_oi, 2)
    except Exception:
        pass
    try:
        history = t.history(period="1y")
        flow["signals"] = compute_volume_signals(history)
        trend = compute_trend_signals(history, bench_history)
        if trend["rel_strength_3m"] is not None:
            trend["benchmark"] = bench_symbol
        flow["trend"] = trend
    except Exception:
        pass
    return flow


def _fetch_benchmark(symbol: str) -> pd.DataFrame | None:
    try:
        return yf.Ticker(symbol).history(period="1y")
    except Exception:
        return None


def fetch_flows(tickers: list[str], prev: dict | None = None,
                benchmarks: dict[str, str] | None = None) -> dict:
    """benchmarks maps ticker -> benchmark symbol (from the watchlist's
    optional `benchmark` field); unmapped tickers compare against SPY."""
    prev_flows = (prev or {}).get("flows", {})
    benchmarks = benchmarks or {}
    bench_cache: dict[str, pd.DataFrame | None] = {}
    flows_out: dict = {}
    for t in tickers:
        bench_symbol = benchmarks.get(t) or _BENCHMARK
        if bench_symbol not in bench_cache:
            bench_cache[bench_symbol] = _fetch_benchmark(bench_symbol)
        try:
            flows_out[t] = _fetch_one(t, bench_symbol, bench_cache[bench_symbol])
        except Exception:
            # Schema-complete fallback: normalize to all flow keys
            prev_f = prev_flows.get(t) or {}
            old = {k: prev_f.get(k) for k in _FLOW_FIELDS}
            old["insider_net_6m"] = prev_f.get("insider_net_6m")
            old["put_call_oi_ratio"] = prev_f.get("put_call_oi_ratio")
            # Ensure signals/trend are always dicts
            sig = prev_f.get("signals")
            old["signals"] = sig if isinstance(sig, dict) else dict(_EMPTY_SIGNALS)
            trend = prev_f.get("trend")
            old["trend"] = trend if isinstance(trend, dict) else dict(_EMPTY_TREND)
            old["fetched_at"] = prev_f.get("fetched_at")
            old["stale"] = True
            flows_out[t] = old
    return {"fetched_at": _now(), "flows": flows_out}
