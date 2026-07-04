import pandas as pd

from stocklux import flows


def make_history(up_volume: int, down_volume: int, days: int = 120) -> pd.DataFrame:
    """Price oscillates 100/101 sideways; up and down days get different volume."""
    closes, vols = [], []
    price = 100.0
    for i in range(days):
        up = i % 2 == 0
        price += 1.0 if up else -1.0
        closes.append(price)
        vols.append(up_volume if up else down_volume)
    return pd.DataFrame({"Close": closes, "Volume": vols})


def test_accumulation_pattern_detected():
    sig = flows.compute_volume_signals(make_history(2_000_000, 1_000_000))
    assert sig["up_down_volume_ratio"] == 2.0
    assert sig["obv_slope_20"] > 0
    assert sig["accumulation_hint"] is True


def test_distribution_pattern_not_flagged():
    sig = flows.compute_volume_signals(make_history(1_000_000, 2_000_000))
    assert sig["up_down_volume_ratio"] == 0.5
    assert sig["accumulation_hint"] is False


def test_short_history_returns_nulls():
    sig = flows.compute_volume_signals(make_history(1_000_000, 1_000_000, days=30))
    assert sig["up_down_volume_ratio"] is None
    assert sig["obv_slope_20"] is None
    assert sig["accumulation_hint"] is False


def test_all_down_days_yields_null_ratio_not_nan():
    """With zero up-days up_vol is NaN; ratio must degrade to None, not NaN (JSON serialization)."""
    closes = [200.0 - i for i in range(70)]  # strictly declining, zero up-days
    vols = [1_000_000] * 70
    sig = flows.compute_volume_signals(pd.DataFrame({"Close": closes, "Volume": vols}))
    assert sig["up_down_volume_ratio"] is None
    assert sig["accumulation_hint"] is False


def make_trend_history(daily_return: float, days: int = 260) -> pd.DataFrame:
    """Smooth geometric trend with OHLC columns (2% intraday range)."""
    closes = [100.0 * (1 + daily_return) ** i for i in range(days)]
    return pd.DataFrame({
        "Close": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000] * days,
    })


def test_trend_signals_uptrend():
    flat_bench = make_trend_history(0.0)
    sig = flows.compute_trend_signals(make_trend_history(0.005), flat_bench)
    assert sig["dist_50dma_pct"] > 0
    assert sig["dist_200dma_pct"] > 0
    assert sig["rsi_14"] == 100.0  # monotonic rise, no down days
    assert sig["atr_pct_14"] > 0
    assert sig["rel_strength_3m"] > 0  # outperforms a flat benchmark


def test_trend_signals_downtrend_underperforms():
    flat_bench = make_trend_history(0.0)
    sig = flows.compute_trend_signals(make_trend_history(-0.005), flat_bench)
    assert sig["dist_50dma_pct"] < 0
    assert sig["rsi_14"] < 50
    assert sig["rel_strength_3m"] < 0


def test_trend_signals_short_history_returns_nulls():
    sig = flows.compute_trend_signals(make_trend_history(0.005, days=30))
    assert sig == flows._EMPTY_TREND


def test_trend_signals_without_ohlc_or_benchmark_degrades():
    """Close-only history: dma/rsi computed, atr and rel strength stay None."""
    sig = flows.compute_trend_signals(make_history(1_000_000, 1_000_000))
    assert sig["dist_50dma_pct"] is not None
    assert sig["rsi_14"] is not None
    assert sig["atr_pct_14"] is None
    assert sig["rel_strength_3m"] is None


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        if self.symbol == "FAIL":
            raise RuntimeError("down")
        return {"sharesShort": 5_000_000, "shortPercentOfFloat": 0.03,
                "shortRatio": 1.8, "heldPercentInstitutions": 0.82}

    @property
    def options(self):
        return ()  # no options data

    def history(self, period="6mo"):
        return make_history(2_000_000, 1_000_000)


def test_fetch_flows(monkeypatch):
    monkeypatch.setattr(flows.yf, "Ticker", FakeTicker)
    out = flows.fetch_flows(["ON"])
    f = out["flows"]["ON"]
    assert f["short_pct_float"] == 0.03
    assert f["inst_pct"] == 0.82
    assert f["signals"]["accumulation_hint"] is True
    assert f["stale"] is False
    assert set(f["trend"]) == set(flows._EMPTY_TREND)
    assert f["trend"]["dist_50dma_pct"] is not None  # computed from history
    assert f["trend"]["benchmark"] == "SPY"  # default benchmark


def test_fetch_flows_per_ticker_benchmark(monkeypatch):
    monkeypatch.setattr(flows.yf, "Ticker", FakeTicker)
    out = flows.fetch_flows(["ON"], benchmarks={"ON": "SMH"})
    assert out["flows"]["ON"]["trend"]["benchmark"] == "SMH"


def test_fetch_flows_failure_keeps_prev(monkeypatch):
    monkeypatch.setattr(flows.yf, "Ticker", FakeTicker)
    prev = {"flows": {"FAIL": {"short_pct_float": 0.11, "stale": False}}}
    out = flows.fetch_flows(["FAIL"], prev)
    assert out["flows"]["FAIL"]["short_pct_float"] == 0.11
    assert out["flows"]["FAIL"]["stale"] is True
    expected_keys = {
        "shares_short", "short_pct_float", "short_ratio", "inst_pct",
        "insider_net_6m", "put_call_oi_ratio", "signals", "trend",
        "stale", "fetched_at"
    }
    assert set(out["flows"]["FAIL"]) == expected_keys
