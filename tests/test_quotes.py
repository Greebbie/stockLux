import pandas as pd

from stocklux import quotes

GOOD_INFO = {
    "currentPrice": 100.0, "trailingPE": 20.0, "forwardPE": 10.0,
    "trailingEps": 5.0, "forwardEps": 10.0, "marketCap": 1_000_000,
    "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 80.0,
}


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        if self.symbol == "FAIL":
            raise RuntimeError("network down")
        return dict(GOOD_INFO)


def test_fetch_quotes_maps_fields(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    out = quotes.fetch_quotes(["ON"])
    q = out["quotes"]["ON"]
    assert q["price"] == 100.0
    assert q["ttm_pe"] == 20.0
    assert q["fwd_pe"] == 10.0
    assert q["high_52w"] == 120.0
    assert q["stale"] is False
    assert out["fetched_at"]
    expected_keys = set(quotes.FIELDS) | {
        "revisions", "analyst", "next_earnings", "stale", "fetched_at"}
    assert set(q) == expected_keys


def test_fetch_quotes_failure_keeps_prev_and_marks_stale(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    prev = {"quotes": {"FAIL": {"price": 99.0, "ttm_pe": 18.0, "stale": False}}}
    out = quotes.fetch_quotes(["FAIL"], prev)
    assert out["quotes"]["FAIL"]["price"] == 99.0
    assert out["quotes"]["FAIL"]["stale"] is True
    expected_keys = set(quotes.FIELDS) | {
        "revisions", "analyst", "next_earnings", "stale", "fetched_at"}
    assert set(out["quotes"]["FAIL"]) == expected_keys


def test_fetch_quotes_failure_without_prev_gives_nulls(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    out = quotes.fetch_quotes(["FAIL"])
    assert out["quotes"]["FAIL"]["price"] is None
    assert out["quotes"]["FAIL"]["stale"] is True
    expected_keys = set(quotes.FIELDS) | {
        "revisions", "analyst", "next_earnings", "stale", "fetched_at"}
    assert set(out["quotes"]["FAIL"]) == expected_keys


def test_extract_revisions_maps_plus_1y_row():
    eps_trend = pd.DataFrame(
        {"current": [10.0, 12.0], "90daysAgo": [10.0, 10.0]},
        index=["0q", "+1y"],
    )
    eps_revisions = pd.DataFrame(
        {"upLast30days": [1, 7], "downLast30days": [0, 2]},
        index=["0q", "+1y"],
    )
    rev = quotes.extract_revisions(eps_trend, eps_revisions)
    assert rev["fwd_eps_change_90d_pct"] == 20.0
    assert rev["up_last_30d"] == 7
    assert rev["down_last_30d"] == 2


def test_extract_revisions_negative_base_keeps_sign():
    """Estimate improving from -2 to -1 must read as +50%, not -50%."""
    eps_trend = pd.DataFrame({"current": [-1.0], "90daysAgo": [-2.0]}, index=["+1y"])
    rev = quotes.extract_revisions(eps_trend, None)
    assert rev["fwd_eps_change_90d_pct"] == 50.0


def test_extract_revisions_missing_data_gives_nulls():
    assert quotes.extract_revisions(None, None) == quotes._EMPTY_REVISIONS
    empty = pd.DataFrame()
    assert quotes.extract_revisions(empty, empty) == quotes._EMPTY_REVISIONS


def test_fetch_quotes_without_revision_data_keeps_null_block(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    out = quotes.fetch_quotes(["ON"])
    assert out["quotes"]["ON"]["revisions"] == quotes._EMPTY_REVISIONS
    assert out["quotes"]["ON"]["next_earnings"] is None  # FakeTicker has no calendar


def test_analyst_block_maps_info_fields(monkeypatch):
    class WithTargets(FakeTicker):
        @property
        def info(self):
            return {**GOOD_INFO, "targetMeanPrice": 120.0, "targetHighPrice": 140.0,
                    "targetLowPrice": 100.0, "numberOfAnalystOpinions": 30,
                    "recommendationMean": 1.8}

    monkeypatch.setattr(quotes.yf, "Ticker", WithTargets)
    analyst = quotes.fetch_quotes(["ON"])["quotes"]["ON"]["analyst"]
    assert analyst == {"pt_mean": 120.0, "pt_high": 140.0, "pt_low": 100.0,
                       "n_analysts": 30, "rec_mean": 1.8}


def test_extract_next_earnings_from_calendar_dict():
    assert quotes.extract_next_earnings(
        {"Earnings Date": ["2026-09-29", "2026-10-05"]}) == "2026-09-29"
    assert quotes.extract_next_earnings({"Earnings Date": "2026-09-29"}) == "2026-09-29"
    assert quotes.extract_next_earnings({"Earnings Date": []}) is None
    assert quotes.extract_next_earnings({}) is None
    assert quotes.extract_next_earnings(None) is None


def test_missing_price_falls_back_to_market_price(monkeypatch):
    class NoCurrentPrice(FakeTicker):
        @property
        def info(self):
            d = dict(GOOD_INFO)
            del d["currentPrice"]
            d["regularMarketPrice"] = 101.5
            return d

    monkeypatch.setattr(quotes.yf, "Ticker", NoCurrentPrice)
    out = quotes.fetch_quotes(["SPY"])
    assert out["quotes"]["SPY"]["price"] == 101.5
