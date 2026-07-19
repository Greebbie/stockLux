import json

import pandas as pd
import pytest

from luxtock import quotes


@pytest.fixture(autouse=True)
def _block_cboe_network(monkeypatch):
    """fetch_quotes' failure path defaults to a live Cboe fallback
    (_cboe_price) unless a test overrides it. Block the real network call
    by default so tests that exercise the pre-existing (non-fallback)
    failure behavior never hit the internet; tests that want the fallback
    stub urlopen directly or pass an explicit fallback_price_fn."""
    def _no_network(*a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(quotes.urllib.request, "urlopen", _no_network)


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


class EmptyInfoTicker(FakeTicker):
    """yfinance rate-limit/transient failures often return a near-empty
    info dict WITHOUT raising — must behave exactly like an exception."""

    @property
    def info(self):
        return {}


def test_fetch_quotes_empty_payload_keeps_prev_and_marks_stale(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", EmptyInfoTicker)
    prev = {"quotes": {"ON": {"price": 99.0, "ttm_pe": 18.0, "stale": False,
                              "fetched_at": "2026-07-01T00:00:00+00:00"}}}
    out = quotes.fetch_quotes(["ON"], prev)
    q = out["quotes"]["ON"]
    assert q["price"] == 99.0        # NOT overwritten with None
    assert q["ttm_pe"] == 18.0
    assert q["stale"] is True        # NOT a fresh-looking stale: false
    assert q["fetched_at"] == "2026-07-01T00:00:00+00:00"  # NOT a fresh timestamp
    expected_keys = set(quotes.FIELDS) | {
        "revisions", "analyst", "next_earnings", "stale", "fetched_at"}
    assert set(q) == expected_keys


def test_fetch_quotes_empty_payload_without_prev_gives_nulls(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", EmptyInfoTicker)
    out = quotes.fetch_quotes(["ON"])
    assert out["quotes"]["ON"]["price"] is None
    assert out["quotes"]["ON"]["stale"] is True


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


# ---------------------------------------------------------------------------
# paired-listing premium tracking
# ---------------------------------------------------------------------------

# SK Hynix ADR real-world example from data/watchlist.json + data/quotes.json:
# 10 ADR = 1 Seoul common share -> ratio 0.1. US price 171.41, Seoul price
# 2,180,000 KRW, fx 0.000661 USD per KRW.
_US_PRICE = 171.41
_KRW_PRICE = 2_180_000.0
_FX_KRW_USD = 0.000661
_RATIO = 0.1
_EXPECTED_PARITY = _KRW_PRICE * _RATIO * _FX_KRW_USD  # ~144.098
_EXPECTED_PREMIUM = (_US_PRICE / _EXPECTED_PARITY - 1) * 100  # ~+19.0%


class PairedFakeTicker:
    """Routes yf.Ticker(symbol) to the right fake payload by symbol."""

    _US_INFO = {**GOOD_INFO, "currentPrice": _US_PRICE}
    _PAIRED_INFO = {"currentPrice": _KRW_PRICE}
    _FX_INFO = {"regularMarketPrice": _FX_KRW_USD}

    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        if self.symbol in ("FAILPAIR", "FAILFXUSD=X"):
            raise RuntimeError("fetch down")
        if self.symbol == "000660.KS":
            return dict(self._PAIRED_INFO)
        if self.symbol == "KRWUSD=X":
            return dict(self._FX_INFO)
        return dict(self._US_INFO)


def test_fetch_quotes_paired_computes_parity_and_premium(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", PairedFakeTicker)
    paired = {"SKHYV": {"ticker": "000660.KS", "ratio": _RATIO, "currency": "KRW"}}
    out = quotes.fetch_quotes(["SKHYV"], paired=paired)
    p = out["quotes"]["SKHYV"]["paired"]
    assert p["ticker"] == "000660.KS"
    assert p["price"] == pytest.approx(_KRW_PRICE)
    assert p["currency"] == "KRW"
    assert p["fx_usd"] == pytest.approx(_FX_KRW_USD)
    assert p["parity_usd"] == pytest.approx(_EXPECTED_PARITY, rel=1e-4)
    assert p["premium_pct"] == pytest.approx(_EXPECTED_PREMIUM, rel=1e-3)
    assert p["premium_pct"] == pytest.approx(19.0, abs=0.1)
    assert p["fetched_at"]


def test_fetch_quotes_without_paired_omits_key(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", PairedFakeTicker)
    out = quotes.fetch_quotes(["MU"])
    assert "paired" not in out["quotes"]["MU"]


def test_fetch_quotes_paired_usd_currency_skips_fx(monkeypatch):
    calls = []

    class RecordingTicker(PairedFakeTicker):
        def __init__(self, symbol):
            calls.append(symbol)
            super().__init__(symbol)

    monkeypatch.setattr(quotes.yf, "Ticker", RecordingTicker)
    paired = {"MU": {"ticker": "PEER", "ratio": 1.0, "currency": "USD"}}
    out = quotes.fetch_quotes(["MU"], paired=paired)
    p = out["quotes"]["MU"]["paired"]
    assert p["fx_usd"] == 1.0
    assert p["currency"] == "USD"
    assert not any(c.endswith("USD=X") for c in calls)


def test_fetch_quotes_paired_defaults_currency_to_usd_when_absent(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", PairedFakeTicker)
    paired = {"MU": {"ticker": "PEER", "ratio": 1.0}}
    out = quotes.fetch_quotes(["MU"], paired=paired)
    assert out["quotes"]["MU"]["paired"]["currency"] == "USD"


def test_fetch_quotes_paired_ticker_failure_degrades_to_null(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", PairedFakeTicker)
    paired = {"MU": {"ticker": "FAILPAIR", "ratio": 1.0, "currency": "USD"}}
    out = quotes.fetch_quotes(["MU"], paired=paired)
    p = out["quotes"]["MU"]["paired"]
    assert p["price"] is None
    assert p["parity_usd"] is None
    assert p["premium_pct"] is None
    assert p["ticker"] == "FAILPAIR"


def test_fetch_quotes_paired_fx_failure_degrades_to_null(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", PairedFakeTicker)
    paired = {"MU": {"ticker": "000660.KS", "ratio": _RATIO, "currency": "FAILFX"}}
    out = quotes.fetch_quotes(["MU"], paired=paired)
    p = out["quotes"]["MU"]["paired"]
    assert p["price"] == pytest.approx(_KRW_PRICE)  # paired ticker itself resolved fine
    assert p["fx_usd"] is None
    assert p["parity_usd"] is None
    assert p["premium_pct"] is None


def test_fetch_quotes_paired_fetch_failure_falls_back_to_prior_values(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", PairedFakeTicker)
    paired = {"MU": {"ticker": "FAILPAIR", "ratio": _RATIO, "currency": "USD"}}
    prev = {"quotes": {"MU": {"paired": {
        "ticker": "FAILPAIR", "price": 500.0, "currency": "USD", "fx_usd": 1.0,
        "parity_usd": 50.0, "premium_pct": 5.0, "fetched_at": "2026-07-01T00:00:00+00:00",
    }}}}
    out = quotes.fetch_quotes(["MU"], prev, paired=paired)
    p = out["quotes"]["MU"]["paired"]
    assert p["price"] == pytest.approx(500.0)  # stale fetch keeps prior value
    assert p["parity_usd"] == pytest.approx(500.0 * _RATIO)


def test_fetch_quotes_us_ticker_total_failure_keeps_prior_paired_block(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)  # FAIL raises on .info
    paired = {"FAIL": {"ticker": "000660.KS", "ratio": 0.1, "currency": "KRW"}}
    prev = {"quotes": {"FAIL": {"price": 99.0, "paired": {
        "ticker": "000660.KS", "price": 2_000_000.0, "currency": "KRW", "fx_usd": 0.0007,
        "parity_usd": 140.0, "premium_pct": 3.0, "fetched_at": "2026-07-01T00:00:00+00:00",
    }}}}
    out = quotes.fetch_quotes(["FAIL"], prev, paired=paired)
    assert out["quotes"]["FAIL"]["stale"] is True
    assert out["quotes"]["FAIL"]["paired"]["premium_pct"] == pytest.approx(3.0)


def test_fetch_quotes_us_ticker_total_failure_without_prior_paired_gives_nulls(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    paired = {"FAIL": {"ticker": "000660.KS", "ratio": 0.1, "currency": "KRW"}}
    out = quotes.fetch_quotes(["FAIL"], paired=paired)
    p = out["quotes"]["FAIL"]["paired"]
    assert p["ticker"] == "000660.KS"
    assert p["price"] is None
    assert p["premium_pct"] is None


# ---------------------------------------------------------------------------
# Cboe price-only fallback (Task 2)
# ---------------------------------------------------------------------------


def test_cboe_symbol_uppercases_and_maps_dash_to_dot():
    assert quotes._cboe_symbol("AAPL") == "AAPL"
    assert quotes._cboe_symbol("BRK-B") == "BRK.B"


class _FakeCboeResponse:
    """Minimal context-manager stand-in for urllib.request.urlopen's return
    value — just enough surface (.read(), context-manager protocol) for
    _cboe_price to consume."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _cboe_body(**data) -> bytes:
    return json.dumps({"data": data}).encode()


def test_cboe_price_parses_valid_json(monkeypatch):
    monkeypatch.setattr(quotes.urllib.request, "urlopen",
                         lambda req, timeout=None: _FakeCboeResponse(
                             _cboe_body(current_price=151.23, close=150.0)))
    assert quotes._cboe_price("AAPL") == pytest.approx(151.23)


def test_cboe_price_falls_back_to_close_when_current_price_zero(monkeypatch):
    monkeypatch.setattr(quotes.urllib.request, "urlopen",
                         lambda req, timeout=None: _FakeCboeResponse(
                             _cboe_body(current_price=0, close=490.91)))
    assert quotes._cboe_price("SPX") == pytest.approx(490.91)


def test_cboe_price_none_when_both_price_fields_missing(monkeypatch):
    monkeypatch.setattr(quotes.urllib.request, "urlopen",
                         lambda req, timeout=None: _FakeCboeResponse(_cboe_body()))
    assert quotes._cboe_price("AAPL") is None


def test_cboe_price_none_on_http_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no network")
    monkeypatch.setattr(quotes.urllib.request, "urlopen", boom)
    assert quotes._cboe_price("NOPE") is None


def test_cboe_price_none_on_empty_body(monkeypatch):
    monkeypatch.setattr(quotes.urllib.request, "urlopen",
                         lambda req, timeout=None: _FakeCboeResponse(b""))
    assert quotes._cboe_price("AAPL") is None


# ---------------------------------------------------------------------------
# fetch_quotes wired to the Cboe fallback on the failure path
# ---------------------------------------------------------------------------


def test_fetch_quotes_failure_with_working_fallback_sets_price_and_source(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    prev = {"quotes": {"FAIL": {"price": 99.0, "ttm_pe": 18.0, "fwd_eps": 3.0, "stale": False}}}
    out = quotes.fetch_quotes(["FAIL"], prev, fallback_price_fn=lambda t: 123.45)
    q = out["quotes"]["FAIL"]
    assert q["stale"] is True
    assert q["price"] == 123.45
    assert q["price_source"] == "cboe"
    assert q["price_as_of"]
    assert q["ttm_pe"] == 18.0  # fundamentals stay from prev, unaffected by the price refresh
    assert q["fwd_eps"] == 3.0


def test_fetch_quotes_failure_with_fallback_returning_none_matches_pre_change_behavior(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    prev = {"quotes": {"FAIL": {"price": 99.0, "stale": False}}}
    out = quotes.fetch_quotes(["FAIL"], prev, fallback_price_fn=lambda t: None)
    q = out["quotes"]["FAIL"]
    assert q["price"] == 99.0
    assert q["stale"] is True
    assert "price_source" not in q
    assert "price_as_of" not in q


def test_fetch_quotes_failure_default_fallback_blocked_in_tests_keeps_prev_price(monkeypatch):
    # No explicit fallback_price_fn: the module default (_cboe_price) is
    # used, but the autouse fixture blocks its network call -> None -> same
    # as pre-change behavior, and no real network touched.
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    prev = {"quotes": {"FAIL": {"price": 99.0, "stale": False}}}
    out = quotes.fetch_quotes(["FAIL"], prev)
    q = out["quotes"]["FAIL"]
    assert q["price"] == 99.0
    assert "price_source" not in q


def test_fetch_quotes_success_after_fallback_clears_price_source(monkeypatch):
    monkeypatch.setattr(quotes.yf, "Ticker", FakeTicker)
    prev = {"quotes": {"ON": {"price": 90.0, "stale": True,
                              "price_source": "cboe",
                              "price_as_of": "2026-07-18T00:00:00+00:00"}}}
    out = quotes.fetch_quotes(["ON"], prev)
    q = out["quotes"]["ON"]
    assert q["stale"] is False
    assert q["price"] == 100.0  # fresh yfinance price, not the stale 90.0
    assert "price_source" not in q
    assert "price_as_of" not in q
