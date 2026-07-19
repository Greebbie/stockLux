import json
from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner

from luxtock import screen
from luxtock.cli import app
from luxtock.screen import build_screen

runner = CliRunner()


def _closes_for(n: int = 200, base: float = 100.0, final: float | None = None) -> list[float]:
    """n flat closes at `base`, with the last one replaced by `final` (defaults
    to `base`, i.e. no drawdown) — the standard synthetic Stage-A fixture."""
    final = base if final is None else final
    return [base] * (n - 1) + [final]


GOOD_FUNDAMENTALS_QUOTE = {
    "fwd_eps": 5.0, "ttm_eps": 4.0, "fwd_pe": 8.0, "ttm_pe": 12.0,
    "market_cap": 10_000_000_000,
    "analyst": {"pt_mean": 90.0, "pt_low": 50.0, "pt_high": 120.0,
                "n_analysts": 10, "rec_mean": 2.0},
    "revisions": {"fwd_eps_change_90d_pct": 10.0, "up_last_30d": 6, "down_last_30d": 1},
    "next_earnings": "2026-08-01",
    "operating_margin": 0.30, "return_on_equity": 0.20, "revenue_growth": 0.15,
}


# ---------------------------------------------------------------------------
# depth_component knots (weight 0.15) — spec's narrative-order cliff at -75
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d,expected", [
    (-10, 25.0),      # shallower than -15 -> flat extrapolation of the -15 knot
    (-15, 25.0),       # exact knot
    (-22.5, 32.5),     # midpoint of -30..-15: 40 -> 25
    (-30, 40.0),       # exact knot
    (-37.5, 60.0),     # midpoint of -45..-30: 80 -> 40
    (-45, 80.0),       # exact knot
    (-52.5, 90.0),     # midpoint of -60..-45: 100 -> 80
    (-60, 100.0),      # exact knot (peak)
    (-67.5, 85.0),     # midpoint of -75..-60: 70 -> 100
    (-74.999, pytest.approx(70.0, abs=0.01)),  # just above the cliff
    (-75, 40.0),        # cliff: flat floor, NOT the interpolated ~70
    (-80, 40.0),        # below the cliff -> still flat 40
])
def test_depth_component_knots(d, expected):
    assert screen._depth_component(d) == expected


# ---------------------------------------------------------------------------
# quality sub-components (margin / roe / growth)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("m,expected", [
    (0, 20.0), (5, 20.0), (10, 40.0), (15, 60.0), (22.5, 75.0), (30, 90.0),
    (30.0001, 100.0), (50, 100.0),
])
def test_margin_component_knots(m, expected):
    assert screen._margin_component(m) == pytest.approx(expected)


@pytest.mark.parametrize("e,expected", [
    (0, 20.0), (5, 20.0), (10, 40.0), (15, 60.0), (20, 75.0), (25, 90.0),
    (25.0001, 100.0), (40, 100.0),
])
def test_roe_component_knots(e, expected):
    assert screen._roe_component(e) == pytest.approx(expected)


@pytest.mark.parametrize("g,expected", [
    (-5, 10.0), (0, 10.0),               # flat floor at/below zero
    (0.0001, pytest.approx(40.0, abs=0.01)),  # discontinuity: jumps straight to ~40
    (5, 55.0), (10, 70.0), (17.5, 85.0), (25, 100.0), (30, 100.0),
])
def test_growth_component_knots(g, expected):
    assert screen._growth_component(g) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# value sub-components (pe_compression / peg)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("f,expected", [
    (0.3, 100.0), (0.5, 100.0), (0.65, 85.0), (0.8, 70.0), (0.9, 60.0),
    (1.0, 50.0), (1.15, 35.0), (1.3, 20.0), (1.3001, 0.0), (2.0, 0.0),
])
def test_pe_compression_component_knots(f, expected):
    assert screen._compression_component(f) == pytest.approx(expected)


@pytest.mark.parametrize("x,expected", [
    (0.5, 100.0), (0.8, 100.0), (1.0, 85.0), (1.2, 70.0), (1.6, 55.0),
    (2.0, 40.0), (2.5, 25.0), (3.0, 10.0), (3.0001, 0.0), (5.0, 0.0),
])
def test_peg_component_knots(x, expected):
    assert screen._peg_component(x) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# resilience (rev / breadth) and rr_proxy knots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("r,expected", [
    (-20, 0.0), (-10, 0.0), (-5, 25.0), (0, 50.0), (7.5, 67.5),
    (15, 85.0), (32.5, 92.5), (50, 100.0), (60, 100.0),
])
def test_rev_knots(r, expected):
    assert screen._interp(r, screen.REV_KNOTS) == pytest.approx(expected)


@pytest.mark.parametrize("x,expected", [
    (0.2, 10.0), (0.5, 10.0), (0.75, 25.0), (1.0, 40.0), (1.5, 55.0),
    (2.0, 70.0), (3.0, 85.0), (4.0, 100.0), (5.0, 100.0),
])
def test_rr_proxy_knots(x, expected):
    assert screen._interp(x, screen.RR_PROXY_KNOTS) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Stage A features / Gate A
# ---------------------------------------------------------------------------


def test_compute_stage_a_features_none_under_200_days():
    assert screen.compute_stage_a_features(_closes_for(n=199)) is None


def test_compute_stage_a_features_exact_200_days_computes_formulas():
    closes = _closes_for(n=200, base=100.0, final=70.0)
    f = screen.compute_stage_a_features(closes)
    assert f["price"] == 70.0
    assert f["drawdown_pct"] == pytest.approx(-30.0)
    assert f["dist_200dma_pct"] == pytest.approx((70 / 99.85 - 1) * 100, abs=1e-6)
    assert f["ret_6m_pct"] == pytest.approx(-30.0)  # closes[-126] is still in the flat region


def test_gate_a_drawdown_boundary():
    shallow = screen.compute_stage_a_features(_closes_for(final=85.5))  # -14.5%
    exact = screen.compute_stage_a_features(_closes_for(final=85.0))    # exactly -15%
    assert screen.gate_a(shallow, screen.DEFAULT_MIN_DRAWDOWN_PCT) is False
    assert screen.gate_a(exact, screen.DEFAULT_MIN_DRAWDOWN_PCT) is True


def test_gate_a_price_floor_boundary():
    at_floor = screen.compute_stage_a_features(_closes_for(base=10.0, final=5.0))    # -50%, price 5.0
    below_floor = screen.compute_stage_a_features(_closes_for(base=10.0, final=4.99))  # price < 5
    assert screen.gate_a(at_floor, 15.0) is True
    assert screen.gate_a(below_floor, 15.0) is False


def test_gate_a_history_length_excludes_before_reaching_gate():
    # 199 days -> compute_stage_a_features already returns None regardless of drawdown
    assert screen.compute_stage_a_features(_closes_for(n=199, final=10.0)) is None


# ---------------------------------------------------------------------------
# track tagging
# ---------------------------------------------------------------------------


def test_track_beaten_down_at_exact_minus_30_boundary():
    assert screen.track_for(-30.0) == "beaten_down"


def test_track_quality_discount_just_above_minus_30():
    assert screen.track_for(-29.999) == "quality_discount"


def test_track_quality_discount_at_gate_boundary():
    assert screen.track_for(-15.0) == "quality_discount"


def test_track_beaten_down_deep():
    assert screen.track_for(-60.0) == "beaten_down"


# ---------------------------------------------------------------------------
# Stage A scan: chunking / ranking / cap
# ---------------------------------------------------------------------------


def test_stage_a_scan_chunks_downloader_calls():
    tickers = [f"T{i}" for i in range(250)]
    seen_chunk_sizes = []

    def downloader(chunk):
        seen_chunk_sizes.append(len(chunk))
        return {t: [] for t in chunk}

    screen.stage_a_scan(tickers, downloader=downloader, chunk_size=100)
    assert seen_chunk_sizes == [100, 100, 50]


def test_stage_a_scan_ranks_survivors_by_drawdown_depth():
    closes_map = {
        "SHALLOW": _closes_for(final=80.0),   # -20%
        "DEEP": _closes_for(final=40.0),      # -60%
        "MID": _closes_for(final=60.0),       # -40%
    }
    result = screen.stage_a_scan(
        list(closes_map), downloader=lambda c: {t: closes_map[t] for t in c})
    assert [t for t, _ in result["survivors"]] == ["DEEP", "MID", "SHALLOW"]
    assert result["survivor_count"] == 3
    assert result["cap_dropped"] == 0


def test_stage_a_scan_caps_and_reports_dropped():
    closes_map = {
        "A": _closes_for(final=50.0), "B": _closes_for(final=40.0), "C": _closes_for(final=30.0),
    }
    result = screen.stage_a_scan(
        list(closes_map), downloader=lambda c: {t: closes_map[t] for t in c}, max_deep=1)
    assert [t for t, _ in result["survivors"]] == ["C"]  # deepest wins the one slot
    assert result["survivor_count"] == 3
    assert result["cap_dropped"] == 2


def test_stage_a_cap_reserves_quality_discount_slots():
    # 8 deep + 4 shallow, max_deep=10, reservation 30% -> 3 quality slots,
    # 7 beaten slots; without the quota all 8 deep names would crowd the
    # shallow band down to 2.
    closes_map = {f"D{i}": _closes_for(final=30.0 + i) for i in range(8)}       # -70%..-63%
    closes_map.update({f"Q{i}": _closes_for(final=75.0 + i) for i in range(4)}) # -25%..-22%
    result = screen.stage_a_scan(
        list(closes_map), downloader=lambda c: {t: closes_map[t] for t in c}, max_deep=10)
    tracks = [screen.track_for(f["drawdown_pct"]) for _, f in result["survivors"]]
    assert len(result["survivors"]) == 10
    assert tracks.count("beaten_down") == 7
    assert tracks.count("quality_discount") == 3
    assert result["cap_dropped"] == 2
    # deepest-first ordering is preserved across the merged pick
    drawdowns = [f["drawdown_pct"] for _, f in result["survivors"]]
    assert drawdowns == sorted(drawdowns)


def test_stage_a_cap_quota_backfills_beaten_when_quality_scarce():
    # 12 deep + 1 shallow, max_deep=10 -> reservation min(1, 3) = 1: the
    # unused quality slots go back to the beaten band (9 + 1).
    closes_map = {f"D{i}": _closes_for(final=30.0 + i) for i in range(12)}
    closes_map["Q0"] = _closes_for(final=80.0)
    result = screen.stage_a_scan(
        list(closes_map), downloader=lambda c: {t: closes_map[t] for t in c}, max_deep=10)
    tracks = [screen.track_for(f["drawdown_pct"]) for _, f in result["survivors"]]
    assert tracks.count("beaten_down") == 9
    assert tracks.count("quality_discount") == 1


def test_stage_a_cap_quota_backfills_quality_when_beaten_scarce():
    # 2 deep + 12 shallow, max_deep=10 -> beaten takes its 2, quality fills
    # the remaining 8.
    closes_map = {f"D{i}": _closes_for(final=30.0 + i) for i in range(2)}
    closes_map.update({f"Q{i}": _closes_for(final=75.0 + i) for i in range(12)})
    result = screen.stage_a_scan(
        list(closes_map), downloader=lambda c: {t: closes_map[t] for t in c}, max_deep=10)
    tracks = [screen.track_for(f["drawdown_pct"]) for _, f in result["survivors"]]
    assert tracks.count("beaten_down") == 2
    assert tracks.count("quality_discount") == 8


def test_stage_a_scan_excludes_shallow_and_short_history():
    closes_map = {
        "SHALLOW": _closes_for(final=95.0),         # -5%, fails drawdown gate
        "SHORT": _closes_for(n=100, final=10.0),    # deep but < 200 days
        "OK": _closes_for(final=50.0),              # -50%, survives
    }
    result = screen.stage_a_scan(
        list(closes_map), downloader=lambda c: {t: closes_map[t] for t in c})
    assert [t for t, _ in result["survivors"]] == ["OK"]


# ---------------------------------------------------------------------------
# default Stage A downloader (network mocked at yf.download)
# ---------------------------------------------------------------------------


def test_default_stage_a_downloader_multi_ticker(monkeypatch):
    idx = pd.date_range("2026-01-01", periods=3)
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Close"]])
    df = pd.DataFrame([[1.0, 2.0], [1.1, 2.1], [1.2, 2.2]], index=idx, columns=cols)
    monkeypatch.setattr(screen.yf, "download", lambda *a, **k: df)
    out = screen._default_stage_a_downloader(["AAA", "BBB"])
    assert out["AAA"] == [1.0, 1.1, 1.2]
    assert out["BBB"] == [2.0, 2.1, 2.2]


def test_default_stage_a_downloader_single_ticker(monkeypatch):
    idx = pd.date_range("2026-01-01", periods=2)
    df = pd.DataFrame({"Close": [5.0, 5.5]}, index=idx)
    monkeypatch.setattr(screen.yf, "download", lambda *a, **k: df)
    out = screen._default_stage_a_downloader(["AAA"])
    assert out["AAA"] == [5.0, 5.5]


def test_default_stage_a_downloader_survives_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(screen.yf, "download", boom)
    assert screen._default_stage_a_downloader(["AAA", "BBB"]) == {"AAA": [], "BBB": []}


def test_default_stage_a_downloader_empty_input():
    assert screen._default_stage_a_downloader([]) == {}


# ---------------------------------------------------------------------------
# Stage B: throttled fetch with one retry
# ---------------------------------------------------------------------------


def test_stage_b_fetch_retries_once_then_succeeds():
    calls = []

    def fetch_one(t):
        calls.append(t)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return {"fwd_eps": 1.0}

    out = screen.stage_b_fetch(["T"], fetch_one=fetch_one, sleep_fn=lambda s: None)
    assert out["T"] == {"quote": {"fwd_eps": 1.0}, "fetch_failed": None}
    assert len(calls) == 2


def test_stage_b_fetch_records_reason_after_retry_exhausted():
    def fetch_one(t):
        raise RuntimeError("boom")

    out = screen.stage_b_fetch(["T"], fetch_one=fetch_one, sleep_fn=lambda s: None)
    assert out["T"]["quote"] is None
    assert out["T"]["fetch_failed"] == "boom"


def test_stage_b_fetch_throttles_between_tickers_not_before_first():
    sleeps = []
    screen.stage_b_fetch(["A", "B", "C"], fetch_one=lambda t: {"ok": t}, sleep_fn=sleeps.append)
    assert len(sleeps) == 2  # inter-ticker only; no retries needed


def test_default_stage_b_fetch_one_maps_fields(monkeypatch):
    info = {
        "currentPrice": 50.0, "trailingPE": 15.0, "forwardPE": 10.0,
        "trailingEps": 2.0, "forwardEps": 3.0, "marketCap": 5_000_000_000,
        "fiftyTwoWeekHigh": 80.0, "fiftyTwoWeekLow": 40.0,
        "targetMeanPrice": 60.0, "targetHighPrice": 70.0, "targetLowPrice": 45.0,
        "numberOfAnalystOpinions": 20, "recommendationMean": 2.0,
        "operatingMargins": 0.25, "returnOnEquity": 0.18, "revenueGrowth": 0.12,
    }

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def info(self):
            return dict(info)

        @property
        def eps_trend(self):
            return None

        @property
        def eps_revisions(self):
            return None

        @property
        def calendar(self):
            return None

    monkeypatch.setattr(screen.yf, "Ticker", FakeTicker)
    q = screen._default_stage_b_fetch_one("T")
    assert q["price"] == 50.0
    assert q["fwd_eps"] == 3.0
    assert q["ttm_eps"] == 2.0
    assert q["market_cap"] == 5_000_000_000
    assert q["analyst"]["pt_mean"] == 60.0
    assert q["operating_margin"] == 0.25
    assert q["return_on_equity"] == 0.18
    assert q["revenue_growth"] == 0.12


def test_default_stage_b_fetch_one_raises_without_price(monkeypatch):
    class NoPriceTicker:
        def __init__(self, symbol):
            pass

        @property
        def info(self):
            return {}

    monkeypatch.setattr(screen.yf, "Ticker", NoPriceTicker)
    with pytest.raises(ValueError):
        screen._default_stage_b_fetch_one("T")


# ---------------------------------------------------------------------------
# derived fundamentals: rr_proxy / pe_compression / eps_growth_pct / peg_like
# ---------------------------------------------------------------------------


def test_rr_proxy_none_when_price_at_or_below_pt_low():
    quote = {"analyst": {"pt_mean": 100.0, "pt_low": 50.0}}
    at_low = screen._derive_fundamentals(quote, price=50.0)
    below_low = screen._derive_fundamentals(quote, price=40.0)
    assert at_low["rr_proxy"] is None
    assert below_low["rr_proxy"] is None


def test_rr_proxy_none_when_pt_data_missing():
    assert screen._derive_fundamentals({}, price=100.0)["rr_proxy"] is None
    quote = {"analyst": {"pt_mean": None, "pt_low": 50.0}}
    assert screen._derive_fundamentals(quote, price=100.0)["rr_proxy"] is None


def test_rr_proxy_computed_when_data_present():
    quote = {"analyst": {"pt_mean": 90.0, "pt_low": 50.0}}
    f = screen._derive_fundamentals(quote, price=70.0)
    assert f["rr_proxy"] == pytest.approx((90.0 - 70.0) / (70.0 - 50.0))


def test_pe_compression_none_when_ttm_pe_missing_or_nonpositive():
    assert screen._derive_fundamentals({"fwd_pe": 10.0}, price=1.0)["pe_compression"] is None
    q = {"fwd_pe": 10.0, "ttm_pe": 0.0}
    assert screen._derive_fundamentals(q, price=1.0)["pe_compression"] is None
    q2 = {"fwd_pe": 10.0, "ttm_pe": -5.0}
    assert screen._derive_fundamentals(q2, price=1.0)["pe_compression"] is None


def test_pe_compression_computed():
    q = {"fwd_pe": 8.0, "ttm_pe": 16.0}
    assert screen._derive_fundamentals(q, price=1.0)["pe_compression"] == pytest.approx(0.5)


def test_eps_growth_pct_none_unless_both_present_and_ttm_positive():
    assert screen._derive_fundamentals({"fwd_eps": 5.0}, price=1.0)["eps_growth_pct"] is None
    q = {"fwd_eps": 5.0, "ttm_eps": 0.0}
    assert screen._derive_fundamentals(q, price=1.0)["eps_growth_pct"] is None
    q2 = {"fwd_eps": 5.0, "ttm_eps": -1.0}
    assert screen._derive_fundamentals(q2, price=1.0)["eps_growth_pct"] is None


def test_eps_growth_pct_computed():
    q = {"fwd_eps": 6.0, "ttm_eps": 4.0}
    f = screen._derive_fundamentals(q, price=1.0)
    assert f["eps_growth_pct"] == pytest.approx(50.0)


def test_peg_like_none_when_eps_growth_or_fwd_pe_missing_or_nonpositive():
    # eps_growth_pct missing (no ttm_eps)
    q = {"fwd_eps": 6.0, "fwd_pe": 10.0}
    assert screen._derive_fundamentals(q, price=1.0)["peg_like"] is None
    # fwd_pe <= 0
    q2 = {"fwd_eps": 6.0, "ttm_eps": 4.0, "fwd_pe": 0.0}
    assert screen._derive_fundamentals(q2, price=1.0)["peg_like"] is None


def test_peg_like_floors_eps_growth_at_one():
    # eps_growth_pct = (5/10 - 1)*100 = -50 -> floored to 1 for the division
    q = {"fwd_eps": 5.0, "ttm_eps": 10.0, "fwd_pe": 8.0}
    f = screen._derive_fundamentals(q, price=1.0)
    assert f["eps_growth_pct"] == pytest.approx(-50.0)
    assert f["peg_like"] == pytest.approx(8.0)  # 8 / max(-50, 1) == 8 / 1


def test_peg_like_computed_normally():
    q = {"fwd_eps": 6.0, "ttm_eps": 4.0, "fwd_pe": 10.0}  # eps_growth_pct=50
    f = screen._derive_fundamentals(q, price=1.0)
    assert f["peg_like"] == pytest.approx(10.0 / 50.0)


# ---------------------------------------------------------------------------
# hard disqualifier gates
# ---------------------------------------------------------------------------


def _fundamentals(**overrides) -> dict:
    base = screen._derive_fundamentals({}, price=None)
    base.update(overrides)
    return base


def test_no_earnings_base_flag_when_fwd_eps_missing_or_nonpositive():
    assert "no_earnings_base" in screen.compute_flags(_fundamentals(fwd_eps=None))
    assert "no_earnings_base" in screen.compute_flags(_fundamentals(fwd_eps=0.0))
    assert "no_earnings_base" in screen.compute_flags(_fundamentals(fwd_eps=-1.0))
    assert "no_earnings_base" not in screen.compute_flags(_fundamentals(fwd_eps=0.01))


def test_estimates_collapsing_flag_boundary():
    assert "estimates_collapsing" not in screen.compute_flags(_fundamentals(rev_90d_pct=-10.0))
    assert "estimates_collapsing" in screen.compute_flags(_fundamentals(rev_90d_pct=-10.01))
    assert "estimates_collapsing" not in screen.compute_flags(_fundamentals(rev_90d_pct=None))


def test_revision_exodus_flag_boundary():
    assert "revision_exodus" not in screen.compute_flags(_fundamentals(rev_breadth=-0.5))
    assert "revision_exodus" in screen.compute_flags(_fundamentals(rev_breadth=-0.51))
    assert "revision_exodus" not in screen.compute_flags(_fundamentals(rev_breadth=None))


def test_too_small_flag_boundary():
    assert "too_small" not in screen.compute_flags(_fundamentals(market_cap=2_000_000_000.0))
    assert "too_small" in screen.compute_flags(_fundamentals(market_cap=1_999_999_999.0))
    assert "too_small" not in screen.compute_flags(_fundamentals(market_cap=None))


def test_disqualified_true_when_any_flag_present():
    flags = screen.compute_flags(_fundamentals(fwd_eps=None))
    assert flags
    flags_clean = screen.compute_flags(_fundamentals(fwd_eps=1.0))
    assert flags_clean == []


# ---------------------------------------------------------------------------
# depression_score: renormalization / coverage / bands / n/a
# ---------------------------------------------------------------------------


def test_depression_score_full_coverage():
    fundamentals = screen._derive_fundamentals(GOOD_FUNDAMENTALS_QUOTE, price=60.0)
    stage_a = {"drawdown_pct": -40.0}
    result = screen.compute_depression_score(fundamentals, stage_a)
    assert result["coverage"] == pytest.approx(1.0)
    assert result["depression_score"] is not None
    assert result["band"] in ("strong", "fair", "weak")


def test_quality_component_renormalizes_when_two_missing():
    # only margin present (roe/growth None) -> quality_c == margin_component alone
    fundamentals = _fundamentals(operating_margin=0.30, return_on_equity=None, revenue_growth=None)
    result = screen.compute_depression_score(fundamentals, {"drawdown_pct": None})
    assert result["quality_component"] == pytest.approx(screen._margin_component(30.0))


def test_depression_score_none_when_all_components_missing():
    fundamentals = _fundamentals()  # everything None
    result = screen.compute_depression_score(fundamentals, {"drawdown_pct": None})
    assert result["depression_score"] is None
    assert result["coverage"] == pytest.approx(0.0)
    assert result["band"] == "n/a"


def test_band_na_when_coverage_below_half():
    # Only resilience (0.25) present -> coverage 0.25 < 0.5 -> n/a despite a
    # computable score.
    fundamentals = _fundamentals(rev_90d_pct=50.0, rev_breadth=1.0)
    result = screen.compute_depression_score(fundamentals, {"drawdown_pct": None})
    assert result["coverage"] == pytest.approx(0.25)
    assert result["depression_score"] is not None
    assert result["band"] == "n/a"


def test_band_real_at_exactly_half_coverage_boundary():
    # quality (0.25) + value (0.25) present -> coverage exactly 0.5 -> NOT n/a
    # ("coverage < 0.5" is a strict inequality).
    fundamentals = _fundamentals(
        operating_margin=0.30, return_on_equity=0.20, revenue_growth=0.15,
        pe_compression=0.4, peg_like=0.5,
    )
    result = screen.compute_depression_score(fundamentals, {"drawdown_pct": None})
    assert result["coverage"] == pytest.approx(0.5)
    assert result["band"] != "n/a"


@pytest.mark.parametrize("score,expected_band", [(75.0, "strong"), (74.999, "fair"),
                                                  (55.0, "fair"), (54.999, "weak")])
def test_band_thresholds(score, expected_band):
    assert screen._band(score, 1.0) == expected_band


def test_band_na_when_score_none_even_at_full_coverage():
    assert screen._band(None, 1.0) == "n/a"


# ---------------------------------------------------------------------------
# build_screen end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def screen_data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(
        json.dumps({"stocks": [{"ticker": "WATCHED"}]}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test",
        "tickers": ["AAA", "BBB", "WATCHED", "CCC"],
    }), encoding="utf-8")
    return d


def test_build_screen_excludes_watchlist_and_applies_gate(screen_data_dir):
    closes_map = {
        "AAA": _closes_for(final=60.0),          # -40% -> survives, beaten_down
        "BBB": _closes_for(final=97.0),           # -3% -> fails drawdown gate
        "CCC": _closes_for(n=100, final=60.0),    # short history -> excluded
        "WATCHED": _closes_for(final=10.0),       # would pass but excluded via watchlist
    }
    seen = []

    def downloader(chunk):
        seen.extend(chunk)
        return {t: closes_map.get(t, []) for t in chunk}

    result = build_screen(
        screen_data_dir, stage_a_downloader=downloader,
        stage_b_fetch_one=lambda t: dict(GOOD_FUNDAMENTALS_QUOTE), sleep_fn=lambda s: None)

    assert "WATCHED" not in seen
    tickers = {r["ticker"] for r in result["results"]}
    assert tickers == {"AAA"}
    assert result["universe_size"] == 4
    assert result["universe_as_of"] == "2026-07-01"
    row = result["results"][0]
    assert row["track"] == "beaten_down"
    assert row["disqualified"] is False
    assert row["fetch_failed"] is None

    on_disk = json.loads((screen_data_dir / "screen.json").read_text(encoding="utf-8"))
    assert on_disk == result


def test_build_screen_preserves_fetch_failure_row(screen_data_dir):
    (screen_data_dir / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["AAA"],
    }), encoding="utf-8")
    closes_map = {"AAA": _closes_for(final=60.0)}

    def fetch_one(t):
        raise RuntimeError("rate limited")

    result = build_screen(
        screen_data_dir, stage_a_downloader=lambda c: {t: closes_map[t] for t in c},
        stage_b_fetch_one=fetch_one, sleep_fn=lambda s: None)

    row = result["results"][0]
    assert row["ticker"] == "AAA"
    assert row["fetch_failed"] == "rate limited"
    assert row["fundamentals"]["fwd_eps"] is None
    assert "no_earnings_base" in row["flags"]
    assert row["disqualified"] is True


def test_build_screen_reports_cap_dropped(screen_data_dir):
    (screen_data_dir / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["AAA", "BBB"],
    }), encoding="utf-8")
    closes_map = {"AAA": _closes_for(final=50.0), "BBB": _closes_for(final=40.0)}

    result = build_screen(
        screen_data_dir, stage_a_downloader=lambda c: {t: closes_map[t] for t in c},
        stage_b_fetch_one=lambda t: {"fwd_eps": 1.0}, sleep_fn=lambda s: None, max_deep=1)

    assert result["stage_a_survivors"] == 2
    assert result["stage_b_cap_dropped"] == 1
    assert len(result["results"]) == 1
    assert result["results"][0]["ticker"] == "BBB"  # deepest drawdown wins the one slot


def test_build_screen_missing_universe_raises_and_writes_nothing(tmp_path):
    # An empty scan must never silently overwrite a previous screen.json —
    # a mistyped --universe path would read as "the market has no candidates".
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or has no tickers"):
        build_screen(d, stage_a_downloader=lambda c: {t: [] for t in c},
                     sleep_fn=lambda s: None)
    assert not (d / "screen.json").exists()


def test_build_screen_appends_screen_history_qualified_only(screen_data_dir):
    closes_map = {"AAA": _closes_for(final=60.0), "BBB": _closes_for(final=55.0),
                  "CCC": _closes_for(n=100), "WATCHED": _closes_for(final=10.0)}
    small_cap = dict(GOOD_FUNDAMENTALS_QUOTE, market_cap=1_000_000)  # too_small -> disqualified

    def fetch_one(t):
        return small_cap if t == "BBB" else dict(GOOD_FUNDAMENTALS_QUOTE)

    for _ in range(2):  # two runs -> history accumulates, never rewrites
        build_screen(
            screen_data_dir, stage_a_downloader=lambda c: {t: closes_map.get(t, []) for t in c},
            stage_b_fetch_one=fetch_one, sleep_fn=lambda s: None)

    lines = [json.loads(ln) for ln in
             (screen_data_dir / "screen_history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2  # AAA twice; disqualified BBB never logged
    assert all(row["ticker"] == "AAA" for row in lines)
    assert {"date", "computed_at", "ticker", "track", "price", "drawdown_pct",
            "depression_score", "band"} <= set(lines[0].keys())


def test_build_screen_progress_fn_reports_stages(screen_data_dir):
    closes_map = {"AAA": _closes_for(final=60.0), "BBB": _closes_for(final=97.0),
                  "CCC": _closes_for(n=100, final=60.0), "WATCHED": _closes_for(final=10.0)}
    lines = []
    build_screen(
        screen_data_dir, stage_a_downloader=lambda c: {t: closes_map.get(t, []) for t in c},
        stage_b_fetch_one=lambda t: dict(GOOD_FUNDAMENTALS_QUOTE), sleep_fn=lambda s: None,
        progress_fn=lines.append)
    assert any("stage A: downloading" in ln for ln in lines)
    assert any("qualified" in ln and "stage B" in ln for ln in lines)


def test_stage_b_fetch_progress_cadence():
    tickers = [f"T{i}" for i in range(45)]
    lines = []
    screen.stage_b_fetch(tickers, fetch_one=lambda t: {"fwd_eps": 1.0},
                         sleep_fn=lambda s: None, progress_fn=lines.append)
    assert lines == ["stage B: 20/45 tickers fetched", "stage B: 40/45 tickers fetched"]


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_screen_prints_notices_and_table(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["AAA"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    closes = _closes_for(final=55.0)  # -45% drawdown
    monkeypatch.setattr(screen, "_default_stage_a_downloader", lambda chunk: {t: closes for t in chunk})
    monkeypatch.setattr(screen, "_default_stage_b_fetch_one", lambda t: dict(GOOD_FUNDAMENTALS_QUOTE))
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert "AAA" in result.output
    assert "beaten_down" in result.output
    assert "candidates only — not analyzed, no verdicts" in result.output
    assert "rr_proxy is sell-side-derived, screening signal only" in result.output
    assert "written: data/screen.json" in result.output


def test_cli_screen_reports_cap_dropped(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["AAA", "BBB"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    closes_map = {"AAA": _closes_for(final=50.0), "BBB": _closes_for(final=40.0)}
    monkeypatch.setattr(screen, "_default_stage_a_downloader",
                        lambda chunk: {t: closes_map[t] for t in chunk})
    monkeypatch.setattr(screen, "_default_stage_b_fetch_one", lambda t: dict(GOOD_FUNDAMENTALS_QUOTE))
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen", "--max-deep", "1"])
    assert result.exit_code == 0, result.output
    assert "dropped" in result.output


def test_cli_screen_empty_universe_errors_out(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": [],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 1
    assert "missing or has no tickers" in result.output
    assert not (d / "screen.json").exists()


def test_cli_screen_bad_universe_path_errors_out(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["screen", "--universe", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "missing or has no tickers" in result.output


def test_cli_screen_no_qualified_candidates(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["AAA"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # AAA has data but fails the drawdown gate -> a real scan with zero hits
    monkeypatch.setattr(screen, "_default_stage_a_downloader",
                        lambda chunk: {t: _closes_for(final=97.0) for t in chunk})
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert "no qualified candidates" in result.output
    assert "candidates only — not analyzed, no verdicts" in result.output


def test_cli_screen_caps_rr_proxy_display_above_ten(tmp_path, monkeypatch):
    # pt_low sitting just under price (CVNA shape) makes the stored rr_proxy
    # explode (72.25); the CLI table must show the display cap, not the raw
    # number — screen.json itself stays uncapped (checked separately).
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["AAA"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    closes = _closes_for(final=85.0)  # -15% drawdown, clears Gate A
    quote = dict(GOOD_FUNDAMENTALS_QUOTE)
    # price will be 85; pt_low 84.6 / pt_mean 113.9 -> rr_proxy = 28.9/0.4 = 72.25
    quote["analyst"] = {**GOOD_FUNDAMENTALS_QUOTE["analyst"], "pt_mean": 113.9, "pt_low": 84.6}
    monkeypatch.setattr(screen, "_default_stage_a_downloader", lambda chunk: {t: closes for t in chunk})
    monkeypatch.setattr(screen, "_default_stage_b_fetch_one", lambda t: dict(quote))
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert ">10" in result.output
    assert "72.2" not in result.output


# ---------------------------------------------------------------------------
# hypergrowth track — component knots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("x,expected", [
    (0.05, 100.0), (0.08, 100.0), (0.115, 85.0), (0.15, 70.0), (0.225, 55.0),
    (0.30, 40.0), (0.40, 25.0), (0.50, 10.0), (0.5001, 0.0), (0.7, 0.0),
])
def test_gs_component_knots(x, expected):
    assert screen._gs_component(x) == pytest.approx(expected)


@pytest.mark.parametrize("g,expected", [
    (10, 50.0), (30, 50.0), (45, 65.0), (60, 80.0), (80, 90.0), (100, 100.0), (120, 100.0),
])
def test_growth_intensity_component_knots(g, expected):
    assert screen._growth_intensity_component(g) == pytest.approx(expected)


@pytest.mark.parametrize("m,expected", [
    (0, 10.0), (20, 10.0),                          # cliff floor at/below 20
    (20.0001, pytest.approx(30.0, abs=0.01)),        # jumps straight to ~30
    (35, 50.0), (50, 70.0), (65, 85.0), (80, 100.0), (90, 100.0),
])
def test_margin_hg_component_knots(m, expected):
    assert screen._margin_hg_component(m) == pytest.approx(expected)


def test_runway_component_fcf_nonnegative_scores_ceiling():
    assert screen._runway_component(0.0, None) == 100.0
    assert screen._runway_component(500.0, None) == 100.0


def test_runway_component_none_when_fcf_negative_and_runway_unknown():
    # FCF < 0 but runway_years missing (e.g. total_cash absent) -> component
    # missing, not assumed safe or assumed a burner.
    assert screen._runway_component(-5.0, None) is None


@pytest.mark.parametrize("y,expected", [
    (0.25, 0.0), (0.5, 0.0), (0.75, 15.0), (1.0, 30.0), (1.5, 45.0),
    (2.0, 60.0), (2.5, 70.0), (3.0, 80.0), (4.0, 80.0),  # cap: never reaches 100
])
def test_runway_component_knots_when_burning(y, expected):
    assert screen._runway_component(-1.0, y) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# ev_sales / gs_like / runway_years derivation
# ---------------------------------------------------------------------------


def test_ev_sales_none_unless_total_revenue_present_and_positive():
    assert screen._derive_fundamentals({"enterprise_value": 100.0}, price=1.0)["ev_sales"] is None
    q = {"enterprise_value": 100.0, "total_revenue": 0.0}
    assert screen._derive_fundamentals(q, price=1.0)["ev_sales"] is None


def test_ev_sales_computed():
    q = {"enterprise_value": 120.0, "total_revenue": 10.0}
    assert screen._derive_fundamentals(q, price=1.0)["ev_sales"] == pytest.approx(12.0)


def test_gs_like_none_unless_ev_sales_and_positive_growth():
    # ev_sales missing
    q = {"revenue_growth": 0.5}
    assert screen._derive_fundamentals(q, price=1.0)["gs_like"] is None
    # revenue_growth missing
    q2 = {"enterprise_value": 120.0, "total_revenue": 10.0}
    assert screen._derive_fundamentals(q2, price=1.0)["gs_like"] is None
    # revenue_growth non-positive
    q3 = {"enterprise_value": 120.0, "total_revenue": 10.0, "revenue_growth": 0.0}
    assert screen._derive_fundamentals(q3, price=1.0)["gs_like"] is None


def test_gs_like_computed():
    # ev_sales = 12.0 (NBIS-shaped), revenue_growth 100% -> gs_like = 12/100 = 0.12
    q = {"enterprise_value": 1200.0, "total_revenue": 100.0, "revenue_growth": 1.0}
    f = screen._derive_fundamentals(q, price=1.0)
    assert f["ev_sales"] == pytest.approx(12.0)
    assert f["gs_like"] == pytest.approx(0.12)


def test_runway_years_none_when_fcf_nonnegative_or_missing():
    assert screen._derive_fundamentals({"total_cash": 100.0}, price=1.0)["runway_years"] is None
    q = {"total_cash": 100.0, "free_cashflow": 0.0}
    assert screen._derive_fundamentals(q, price=1.0)["runway_years"] is None
    q2 = {"total_cash": 100.0, "free_cashflow": 50.0}
    assert screen._derive_fundamentals(q2, price=1.0)["runway_years"] is None
    q3 = {"free_cashflow": -10.0}  # total_cash missing
    assert screen._derive_fundamentals(q3, price=1.0)["runway_years"] is None


def test_runway_years_computed_when_burning():
    q = {"total_cash": 300.0, "free_cashflow": -100.0}
    f = screen._derive_fundamentals(q, price=1.0)
    assert f["runway_years"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# track upgrade (resolve_track) boundary cases
# ---------------------------------------------------------------------------


def test_resolve_track_upgrades_at_fwd_eps_exactly_zero_and_growth_floor():
    fundamentals = _fundamentals(fwd_eps=0.0, revenue_growth=0.30)
    assert screen.resolve_track("beaten_down", fundamentals) == "hypergrowth"


def test_resolve_track_upgrades_when_fwd_eps_missing():
    fundamentals = _fundamentals(fwd_eps=None, revenue_growth=0.50)
    assert screen.resolve_track("quality_discount", fundamentals) == "hypergrowth"


def test_resolve_track_stays_standard_just_under_growth_floor():
    fundamentals = _fundamentals(fwd_eps=None, revenue_growth=0.2999)
    assert screen.resolve_track("beaten_down", fundamentals) == "beaten_down"


def test_resolve_track_stays_standard_when_earnings_base_present():
    # positive fwd_eps -> stays in its standard track even with high growth
    # ("peg already rewards it there" per spec).
    fundamentals = _fundamentals(fwd_eps=0.01, revenue_growth=0.90)
    assert screen.resolve_track("quality_discount", fundamentals) == "quality_discount"


def test_resolve_track_stays_standard_when_growth_missing():
    fundamentals = _fundamentals(fwd_eps=None, revenue_growth=None)
    assert screen.resolve_track("beaten_down", fundamentals) == "beaten_down"


def test_resolve_track_negative_fwd_eps_with_high_growth_upgrades():
    fundamentals = _fundamentals(fwd_eps=-2.0, revenue_growth=1.0)
    assert screen.resolve_track("beaten_down", fundamentals) == "hypergrowth"


# ---------------------------------------------------------------------------
# hypergrowth-track disqualifier gates
# ---------------------------------------------------------------------------


def test_hypergrowth_no_earnings_base_and_estimates_collapsing_do_not_apply():
    fundamentals = _fundamentals(fwd_eps=None, rev_90d_pct=-90.0, gs_like=0.1)
    flags = screen.compute_flags(fundamentals, "hypergrowth")
    assert "no_earnings_base" not in flags
    assert "estimates_collapsing" not in flags


def test_hypergrowth_no_runway_flag_boundary():
    just_ok = _fundamentals(free_cashflow=-10.0, runway_years=0.75, gs_like=0.1)
    just_bad = _fundamentals(free_cashflow=-10.0, runway_years=0.7499, gs_like=0.1)
    assert "no_runway" not in screen.compute_flags(just_ok, "hypergrowth")
    assert "no_runway" in screen.compute_flags(just_bad, "hypergrowth")


def test_hypergrowth_no_runway_never_fires_when_fcf_nonnegative():
    fundamentals = _fundamentals(free_cashflow=5.0, runway_years=None, gs_like=0.1)
    assert "no_runway" not in screen.compute_flags(fundamentals, "hypergrowth")


def test_hypergrowth_no_runway_does_not_fire_when_runway_unknown():
    # FCF < 0 but runway_years couldn't be computed (e.g. missing cash) ->
    # cannot confirm the trap, so it does not fire (graceful degradation).
    fundamentals = _fundamentals(free_cashflow=-10.0, runway_years=None, gs_like=0.1)
    assert "no_runway" not in screen.compute_flags(fundamentals, "hypergrowth")


def test_hypergrowth_growth_unpriced_flag():
    missing = _fundamentals(gs_like=None)
    present = _fundamentals(gs_like=0.2)
    assert "growth_unpriced" in screen.compute_flags(missing, "hypergrowth")
    assert "growth_unpriced" not in screen.compute_flags(present, "hypergrowth")


def test_hypergrowth_revision_exodus_and_too_small_still_apply():
    fundamentals = _fundamentals(gs_like=0.1, rev_breadth=-0.6, market_cap=1_000_000_000.0)
    flags = screen.compute_flags(fundamentals, "hypergrowth")
    assert "revision_exodus" in flags
    assert "too_small" in flags


def test_standard_track_flags_unchanged_by_hypergrowth_addition():
    # regression: default (non-hypergrowth) call sites still get the
    # original standard-track behavior.
    assert "no_earnings_base" in screen.compute_flags(_fundamentals(fwd_eps=None))
    assert "no_runway" not in screen.compute_flags(_fundamentals(fwd_eps=None))
    assert "growth_unpriced" not in screen.compute_flags(_fundamentals(fwd_eps=None))


# ---------------------------------------------------------------------------
# hypergrowth-track score: renormalization / coverage / bands
# ---------------------------------------------------------------------------


GOOD_HYPERGROWTH_FUNDAMENTALS = dict(
    gs_like=0.12, revenue_growth=1.0, gross_margin=0.65,
    free_cashflow=-100.0, runway_years=3.0, rr_proxy=1.5,
)


def test_hypergrowth_score_full_coverage():
    fundamentals = _fundamentals(**GOOD_HYPERGROWTH_FUNDAMENTALS)
    result = screen.compute_hypergrowth_score(fundamentals, {"drawdown_pct": -20.0})
    assert result["coverage"] == pytest.approx(1.0)
    assert result["depression_score"] is not None
    assert result["band"] in ("strong", "fair", "weak")


def test_hypergrowth_score_renormalizes_when_gs_missing():
    fundamentals = _fundamentals(**{**GOOD_HYPERGROWTH_FUNDAMENTALS, "gs_like": None})
    result = screen.compute_hypergrowth_score(fundamentals, {"drawdown_pct": -20.0})
    assert result["gs_component"] is None
    assert result["coverage"] == pytest.approx(0.70)  # 1.0 - GS_WEIGHT(0.30)
    assert result["depression_score"] is not None


def test_hypergrowth_score_none_when_all_components_missing():
    fundamentals = _fundamentals()
    result = screen.compute_hypergrowth_score(fundamentals, {"drawdown_pct": None})
    assert result["depression_score"] is None
    assert result["coverage"] == pytest.approx(0.0)
    assert result["band"] == "n/a"


def test_hypergrowth_score_runway_ceiling_when_fcf_nonnegative():
    fundamentals = _fundamentals(**{**GOOD_HYPERGROWTH_FUNDAMENTALS,
                                     "free_cashflow": 10.0, "runway_years": None})
    result = screen.compute_hypergrowth_score(fundamentals, {"drawdown_pct": -20.0})
    assert result["runway_component"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# universe extra_tickers merge + dedupe
# ---------------------------------------------------------------------------


def test_merged_universe_tickers_appends_extra_and_dedupes():
    universe = {
        "tickers": ["AAA", "BBB"],
        "extra_tickers": {"note": "test", "tickers": ["BBB", "CCC"]},
    }
    assert screen._merged_universe_tickers(universe) == ["AAA", "BBB", "CCC"]


def test_merged_universe_tickers_handles_missing_extra_tickers_block():
    assert screen._merged_universe_tickers({"tickers": ["AAA"]}) == ["AAA"]


def test_merged_universe_tickers_handles_malformed_extra_tickers():
    assert screen._merged_universe_tickers({"tickers": ["AAA"], "extra_tickers": "oops"}) == ["AAA"]


def test_build_screen_merges_extra_tickers_into_scan_and_universe_size(screen_data_dir):
    (screen_data_dir / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test",
        "tickers": ["AAA"],
        "extra_tickers": {"note": "test", "tickers": ["AAA", "ZZZ"]},  # AAA dupes the main list
    }), encoding="utf-8")
    closes_map = {
        "AAA": _closes_for(final=60.0),
        "ZZZ": _closes_for(final=55.0),
    }
    seen = []

    def downloader(chunk):
        seen.extend(chunk)
        return {t: closes_map.get(t, []) for t in chunk}

    result = build_screen(
        screen_data_dir, stage_a_downloader=downloader,
        stage_b_fetch_one=lambda t: dict(GOOD_FUNDAMENTALS_QUOTE), sleep_fn=lambda s: None)

    assert "ZZZ" in seen
    assert result["universe_size"] == 2  # AAA + ZZZ, deduped
    assert {r["ticker"] for r in result["results"]} == {"AAA", "ZZZ"}


# ---------------------------------------------------------------------------
# build_screen end-to-end: hypergrowth row assembly
# ---------------------------------------------------------------------------


def test_build_screen_assembles_hypergrowth_row(screen_data_dir):
    (screen_data_dir / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["HGRO"],
    }), encoding="utf-8")
    closes_map = {"HGRO": _closes_for(final=80.0)}  # -20% drawdown -> quality_discount pre-upgrade

    def fetch_one(t):
        return {
            "fwd_eps": None, "ttm_eps": None, "fwd_pe": None, "ttm_pe": None,
            "market_cap": 10_000_000_000,
            "analyst": {"pt_mean": 100.0, "pt_low": 60.0, "pt_high": 140.0,
                        "n_analysts": 8, "rec_mean": 2.0},
            "revisions": {"fwd_eps_change_90d_pct": None, "up_last_30d": 5, "down_last_30d": 1},
            "next_earnings": None,
            "operating_margin": None, "return_on_equity": None, "revenue_growth": 0.80,
            "enterprise_value": 1_200_000_000.0, "total_revenue": 100_000_000.0,
            "gross_margin": 0.65, "total_cash": 300_000_000.0, "free_cashflow": -100_000_000.0,
        }

    result = build_screen(
        screen_data_dir, stage_a_downloader=lambda c: {t: closes_map[t] for t in c},
        stage_b_fetch_one=fetch_one, sleep_fn=lambda s: None)

    row = result["results"][0]
    assert row["ticker"] == "HGRO"
    assert row["track"] == "hypergrowth"
    assert set(row["components"]) == {
        "gs_component", "growth_intensity_component", "margin_component",
        "runway_component", "depth_component", "rr_proxy_component",
    }
    assert "quality_component" not in row["components"]
    assert row["fundamentals"]["ev_sales"] == pytest.approx(12.0)
    assert row["fundamentals"]["gs_like"] == pytest.approx(0.15)
    assert row["fundamentals"]["runway_years"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# CLI hypergrowth block
# ---------------------------------------------------------------------------


def test_cli_screen_prints_hypergrowth_block(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["HGRO", "STDX"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    closes_map = {
        "HGRO": _closes_for(final=80.0),
        "STDX": _closes_for(final=55.0),
    }

    def fetch_one(t):
        if t == "HGRO":
            return {
                "fwd_eps": None, "ttm_eps": None, "fwd_pe": None, "ttm_pe": None,
                "market_cap": 10_000_000_000,
                "analyst": {"pt_mean": 100.0, "pt_low": 60.0, "pt_high": 140.0,
                            "n_analysts": 8, "rec_mean": 2.0},
                "revisions": {"fwd_eps_change_90d_pct": None, "up_last_30d": 5, "down_last_30d": 1},
                "next_earnings": None,
                "operating_margin": None, "return_on_equity": None, "revenue_growth": 0.80,
                "enterprise_value": 1_200_000_000.0, "total_revenue": 100_000_000.0,
                "gross_margin": 0.65, "total_cash": 300_000_000.0, "free_cashflow": -100_000_000.0,
            }
        return dict(GOOD_FUNDAMENTALS_QUOTE)

    monkeypatch.setattr(screen, "_default_stage_a_downloader", lambda chunk: {t: closes_map[t] for t in chunk})
    monkeypatch.setattr(screen, "_default_stage_b_fetch_one", fetch_one)
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert "hypergrowth track" in result.output
    assert "HGRO" in result.output
    assert "STDX" in result.output
    assert "candidates only — not analyzed, no verdicts" in result.output
    assert "rr_proxy is sell-side-derived, screening signal only" in result.output


def test_cli_screen_no_hypergrowth_block_when_none_qualify(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2026-07-01", "source": "test", "tickers": ["STDX"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(screen, "_default_stage_a_downloader",
                        lambda chunk: {t: _closes_for(final=55.0) for t in chunk})
    monkeypatch.setattr(screen, "_default_stage_b_fetch_one", lambda t: dict(GOOD_FUNDAMENTALS_QUOTE))
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert "hypergrowth track" not in result.output


# ---------------------------------------------------------------------------
# universe staleness warning
# ---------------------------------------------------------------------------


def test_universe_age_days_computes_whole_days():
    assert screen.universe_age_days("2026-07-01", today=date(2026, 7, 19)) == 18


def test_universe_age_days_none_when_as_of_missing():
    assert screen.universe_age_days(None, today=date(2026, 7, 19)) is None
    assert screen.universe_age_days("", today=date(2026, 7, 19)) is None


def test_universe_age_days_none_when_as_of_unparseable():
    assert screen.universe_age_days("not-a-date", today=date(2026, 7, 19)) is None


def test_cli_screen_warns_on_stale_universe(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    (d / "universe.json").write_text(json.dumps({
        "as_of": "2020-01-01", "source": "test", "tickers": ["AAA"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(screen, "_default_stage_a_downloader",
                        lambda chunk: {t: _closes_for(final=97.0) for t in chunk})
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert "universe snapshot is" in result.output
    assert "2020-01-01" in result.output
    assert "regenerate data/universe.json" in result.output


def test_cli_screen_no_warning_on_fresh_universe(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    fresh_as_of = date.today().isoformat()
    (d / "universe.json").write_text(json.dumps({
        "as_of": fresh_as_of, "source": "test", "tickers": ["AAA"],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(screen, "_default_stage_a_downloader",
                        lambda chunk: {t: _closes_for(final=97.0) for t in chunk})
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 0, result.output
    assert "universe snapshot is" not in result.output
