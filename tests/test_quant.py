import json
from datetime import datetime, timezone

import pytest

from luxtock import quant


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def blank_features(**overrides) -> dict:
    """A features dict with every key None, except the given overrides —
    used to isolate a single sub-score component through score_features()."""
    f = {k: None for k in quant.FEATURE_KEYS}
    f.update(overrides)
    return f


# ---------------------------------------------------------------------------
# piecewise knot values — valuation (gap / ev)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("g,expected", [
    (-30, 100.0),   # clamps below the first knot
    (-20, 100.0),   # exact knot
    (-10, 80.0),    # midpoint of -20..0: 100 -> 60
    (0, 60.0),      # exact knot
    (15, 30.0),     # exact knot
    (40, 0.0),      # exact knot
    (50, 0.0),      # clamps above the last knot
])
def test_gap_component_knots(g, expected):
    scores = quant.score_features(blank_features(valuation_gap_pct=g))
    assert scores["valuation"] == pytest.approx(expected)


@pytest.mark.parametrize("e,expected", [
    (-30, 0.0),
    (-20, 0.0),
    (-10, 25.0),   # midpoint of -20..0: 0 -> 50
    (0, 50.0),
    (15, 75.0),    # midpoint of 0..30: 50 -> 100
    (30, 100.0),
    (40, 100.0),
])
def test_ev_component_knots(e, expected):
    scores = quant.score_features(blank_features(ev_return_pct=e))
    assert scores["valuation"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# piecewise knot values — momentum (rev)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("r,expected", [
    (-30, 0.0),
    (-20, 0.0),
    (-10, 20.0),   # midpoint of -20..0: 0 -> 40
    (0, 40.0),
    (25, 65.0),    # midpoint of 0..50: 40 -> 90
    (50, 90.0),
    (75, 95.0),    # midpoint of 50..100: 90 -> 100
    (100, 100.0),
    (150, 100.0),
])
def test_rev_component_knots(r, expected):
    scores = quant.score_features(blank_features(rev_90d_pct=r))
    assert scores["momentum"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# piecewise knot values — trend (rsi / dma)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rsi,expected", [
    (10, 50.0),
    (25, 50.0),
    (30, 65.0),     # midpoint 25..35: 50 -> 80
    (35, 80.0),
    (42.5, 70.0),   # midpoint 35..50: 80 -> 60
    (50, 60.0),
    (60, 45.0),     # midpoint 50..70: 60 -> 30
    (70, 30.0),
    (77.5, 15.0),   # midpoint 70..85: 30 -> 0
    (85, 0.0),
    (95, 0.0),
])
def test_rsi_component_knots(rsi, expected):
    scores = quant.score_features(blank_features(rsi_14=rsi))
    assert scores["trend"] == pytest.approx(expected)


@pytest.mark.parametrize("d,expected", [
    (-15, 60.0),
    (-10, 80.0),      # boundary: -10 <= d < -3
    (-3.5, 80.0),
    (-3, 70.0),        # boundary: |d| <= 3
    (0, 70.0),
    (3, 70.0),
    (3.0001, 50.0),    # boundary: 3 < d <= 15
    (15, 50.0),
    (15.0001, 25.0),   # boundary: d > 15
    (25, 25.0),
])
def test_dma_component_knots(d, expected):
    scores = quant.score_features(blank_features(dist_50dma_pct=d))
    assert scores["trend"] == pytest.approx(expected)


@pytest.mark.parametrize("rs,expected", [
    (10, 65.0),
    (0.0001, 65.0),
    (0, 40.0),
    (-5, 40.0),
])
def test_rs_component_knots(rs, expected):
    scores = quant.score_features(blank_features(rel_strength_3m=rs))
    assert scores["trend"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# renormalization when a component is missing
# ---------------------------------------------------------------------------


def test_valuation_renormalizes_when_ev_missing():
    # gap alone (g=-20 -> gap_component 100); ev missing entirely.
    # 0.7*100 renormalized over weight 0.7 == 100, not 70.
    scores = quant.score_features(blank_features(valuation_gap_pct=-20))
    assert scores["valuation"] == pytest.approx(100.0)


def test_positioning_renormalizes_when_two_components_missing():
    # only put/call present (ratio=3.0 -> putcall_component 100); crowding
    # and short both missing. Renormalized single-component average is 100,
    # not 0.3*100=30.
    scores = quant.score_features(blank_features(put_call_oi_ratio=3.0))
    assert scores["positioning"] == pytest.approx(100.0)


def test_composite_renormalizes_over_available_subscores():
    # Only momentum inputs present -> valuation/positioning/trend are None.
    # composite must equal momentum_score itself (renormalized single term),
    # not 0.25 * momentum_score.
    features = blank_features(rev_90d_pct=50, rev_breadth=1.0)
    scores = quant.score_features(features)
    assert scores["momentum"] == pytest.approx(94.0)  # 0.6*90 + 0.4*100
    assert scores["valuation"] is None
    assert scores["positioning"] is None
    assert scores["trend"] is None
    assert scores["composite"] == pytest.approx(94.0)


# ---------------------------------------------------------------------------
# all-null ticker: coverage low, band null below the 0.35 threshold
# ---------------------------------------------------------------------------


def test_fully_null_ticker_has_zero_coverage_and_null_band():
    features = quant.compute_features("ZZZ", None, None, None, [])
    assert all(v is None for v in features.values())
    scores = quant.score_features(features)
    assert scores["coverage"] == 0.0
    assert scores["composite"] is None
    assert scores["band"] is None
    assert scores["components_used"] == []


def test_new_listing_like_skhyv_has_low_coverage_and_null_band():
    # Mirrors data/analyses/SKHYV/2026-07-11.md + data/quotes.json: price
    # known, memo carries buy_range/price_targets, but quotes analyst/
    # revisions and the entire flows.json block are null (day-old listing).
    quotes_entry = {
        "price": 171.41,
        "analyst": {"pt_mean": None, "pt_high": None, "pt_low": None,
                    "n_analysts": None, "rec_mean": None},
        "revisions": {"fwd_eps_change_90d_pct": None, "up_last_30d": None,
                      "down_last_30d": None},
    }
    flows_entry = {
        "short_pct_float": None, "put_call_oi_ratio": None, "inst_pct": None,
        "trend": {"rsi_14": None, "dist_50dma_pct": None, "dist_200dma_pct": None,
                  "atr_pct_14": None, "rel_strength_3m": None},
    }
    memo_meta = {
        "buy_range": [96, 156],
        "price_targets": {
            "bear": 120, "base": 175, "bull": 288,
            "p_bear": 0.20, "p_base": 0.55, "p_bull": 0.25,
        },
    }
    history_rows = [{"date": "2026-07-10", "ticker": "SKHYV", "price": 171.41}]

    features = quant.compute_features("SKHYV", quotes_entry, flows_entry, memo_meta, history_rows)
    assert features["price"] == pytest.approx(171.41)
    assert features["valuation_gap_pct"] == pytest.approx(9.8782, abs=1e-3)
    assert features["gap_to_floor_pct"] == pytest.approx(78.5521, abs=1e-3)
    assert features["rr_ratio"] == pytest.approx((175 - 171.41) / (171.41 - 120))
    assert features["ev_return_pct"] == pytest.approx(12.1584, abs=1e-3)
    assert features["rsi_14"] is None
    assert features["rev_90d_pct"] is None
    assert features["paired_premium_pct"] is None  # no `paired` block in quotes_entry

    scores = quant.score_features(features)
    # 5 of 23 features available: price, valuation_gap_pct, gap_to_floor_pct,
    # rr_ratio, ev_return_pct.
    assert scores["coverage"] == pytest.approx(5 / 23)
    assert scores["coverage"] < 0.35
    assert scores["band"] is None
    # composite itself may still be computable (valuation-only) even though
    # the band is suppressed by low coverage.
    assert scores["composite"] is not None
    assert scores["components_used"] == ["valuation"]


# ---------------------------------------------------------------------------
# band gating (reviewer fix): band requires BOTH coverage >= 0.35 AND a
# non-None valuation sub-score — a name with no memo can otherwise earn a
# band purely from trend/momentum/positioning.
# ---------------------------------------------------------------------------


def test_band_null_when_valuation_missing_even_at_high_coverage():
    # 9 of 23 features present (coverage 0.3913 >= 0.35), none of them
    # valuation inputs (valuation_gap_pct / ev_return_pct) -> valuation is
    # None, so band must be null even though coverage clears the threshold
    # and composite is still computable from the other sub-scores.
    features = blank_features(
        rev_90d_pct=50, rev_breadth=1.0,
        rsi_14=45, dist_50dma_pct=5, rel_strength_3m=10,
        rec_mean=1.5, pt_spread_pct=40, put_call_oi_ratio=1.0,
        short_pct_float=0.05,
    )
    scores = quant.score_features(features)
    assert scores["valuation"] is None
    assert scores["coverage"] >= 0.35
    assert scores["composite"] is not None
    assert scores["band"] is None


def test_band_set_when_valuation_present_and_coverage_clears_threshold():
    features = blank_features(
        valuation_gap_pct=-20, rev_90d_pct=50, rev_breadth=1.0,
        rsi_14=45, dist_50dma_pct=5, rel_strength_3m=10,
        rec_mean=1.5, pt_spread_pct=40, put_call_oi_ratio=1.0,
    )
    scores = quant.score_features(features)
    assert scores["valuation"] is not None
    assert scores["coverage"] >= 0.35
    assert scores["band"] is not None


# ---------------------------------------------------------------------------
# components_used
# ---------------------------------------------------------------------------


def test_components_used_lists_only_available_subscores():
    features = blank_features(rev_90d_pct=50, rev_breadth=1.0)
    scores = quant.score_features(features)
    assert scores["components_used"] == ["momentum"]


def test_components_used_sorted_when_multiple_available():
    features = blank_features(
        valuation_gap_pct=-20, rev_90d_pct=50, rev_breadth=1.0,
        rsi_14=45, dist_50dma_pct=5, rel_strength_3m=10,
    )
    scores = quant.score_features(features)
    assert scores["components_used"] == ["momentum", "trend", "valuation"]


# ---------------------------------------------------------------------------
# dispersion / mixed (v1.1 addition #1)
# ---------------------------------------------------------------------------


def test_dispersion_none_when_zero_subscores_available():
    scores = quant.score_features(blank_features())
    assert scores["dispersion"] is None
    assert scores["mixed"] is False


def test_dispersion_none_when_only_one_subscore_available():
    # Only valuation available (gap=-20 -> valuation=100); momentum/
    # positioning/trend all None -> fewer than 2 available -> dispersion None.
    scores = quant.score_features(blank_features(valuation_gap_pct=-20))
    assert scores["valuation"] == pytest.approx(100.0)
    assert scores["momentum"] is None
    assert scores["dispersion"] is None
    assert scores["mixed"] is False


def test_dispersion_is_max_minus_min_over_available_subscores():
    # valuation=100 (gap=-20), momentum=0 (rev=-30 -> rev_component 0,
    # breadth missing so momentum == rev_component alone).
    features = blank_features(valuation_gap_pct=-20, rev_90d_pct=-30)
    scores = quant.score_features(features)
    assert scores["valuation"] == pytest.approx(100.0)
    assert scores["momentum"] == pytest.approx(0.0)
    assert scores["positioning"] is None
    assert scores["trend"] is None
    assert scores["dispersion"] == pytest.approx(100.0)
    assert scores["mixed"] is True


def test_mixed_false_when_dispersion_below_40():
    # valuation=30 (gap=15), momentum=40 (rev=0) -> dispersion 10 < 40.
    features = blank_features(valuation_gap_pct=15, rev_90d_pct=0)
    scores = quant.score_features(features)
    assert scores["valuation"] == pytest.approx(30.0)
    assert scores["momentum"] == pytest.approx(40.0)
    assert scores["dispersion"] == pytest.approx(10.0)
    assert scores["mixed"] is False


def test_mixed_true_at_exactly_40_dispersion_boundary():
    # valuation=100 (gap=-20), momentum=60 (rev=20 -> rev_component 60,
    # breadth missing so momentum == rev_component alone) -> dispersion
    # exactly 40, which is >= 40 -> mixed True (inclusive boundary).
    features = blank_features(valuation_gap_pct=-20, rev_90d_pct=20)
    scores = quant.score_features(features)
    assert scores["valuation"] == pytest.approx(100.0)
    assert scores["momentum"] == pytest.approx(60.0)
    assert scores["dispersion"] == pytest.approx(40.0)
    assert scores["mixed"] is True


# ---------------------------------------------------------------------------
# feature formulas (non-knot)
# ---------------------------------------------------------------------------


def test_rr_ratio_none_when_price_at_or_below_bear():
    memo_meta = {"price_targets": {"bear": 100, "base": 120, "bull": 150}}
    at_bear = quant.compute_features("T", {"price": 100}, None, memo_meta, [])
    below_bear = quant.compute_features("T", {"price": 90}, None, memo_meta, [])
    assert at_bear["rr_ratio"] is None
    assert below_bear["rr_ratio"] is None


def test_ev_return_pct_none_without_probabilities():
    # Pre-policy-v2 memos may carry bear/base/bull without p_bear/p_base/p_bull.
    memo_meta = {"price_targets": {"bear": 100, "base": 120, "bull": 150}}
    features = quant.compute_features("T", {"price": 110}, None, memo_meta, [])
    assert features["ev_return_pct"] is None


def test_rev_breadth_none_when_both_zero_or_missing():
    zero = quant.compute_features(
        "T", {"revisions": {"up_last_30d": 0, "down_last_30d": 0}}, None, None, [])
    missing = quant.compute_features("T", {"revisions": {}}, None, None, [])
    partial = quant.compute_features(
        "T", {"revisions": {"up_last_30d": 5, "down_last_30d": None}}, None, None, [])
    assert zero["rev_breadth"] is None
    assert missing["rev_breadth"] is None
    assert partial["rev_breadth"] is None


def test_feature_keys_includes_paired_premium_pct():
    assert "paired_premium_pct" in quant.FEATURE_KEYS
    assert len(quant.FEATURE_KEYS) == 23


def test_paired_premium_pct_read_from_quotes_entry():
    quotes_entry = {"price": 171.41, "paired": {
        "ticker": "000660.KS", "price": 2_180_000.0, "currency": "KRW",
        "fx_usd": 0.000661, "parity_usd": 144.098, "premium_pct": 18.95,
    }}
    features = quant.compute_features("SKHYV", quotes_entry, None, None, [])
    assert features["paired_premium_pct"] == pytest.approx(18.95)


def test_paired_premium_pct_none_when_paired_block_absent():
    features = quant.compute_features("T", {"price": 100}, None, None, [])
    assert features["paired_premium_pct"] is None


def test_paired_premium_pct_none_when_paired_block_malformed():
    features = quant.compute_features("T", {"price": 100, "paired": "not a dict"}, None, None, [])
    assert features["paired_premium_pct"] is None


def test_paired_premium_pct_not_in_any_subscore():
    # Informational only — must not move any sub-score, composite, or band.
    # It DOES count toward coverage (it's a FEATURE_KEYS member), so that
    # field is expected to differ and is excluded from this comparison.
    baseline = quant.score_features(blank_features(valuation_gap_pct=-20))
    with_paired = quant.score_features(blank_features(valuation_gap_pct=-20, paired_premium_pct=99.0))
    assert {k: v for k, v in baseline.items() if k != "coverage"} == \
        {k: v for k, v in with_paired.items() if k != "coverage"}
    assert with_paired["coverage"] > baseline["coverage"]


def test_pt_spread_and_upside():
    quotes_entry = {
        "price": 100,
        "analyst": {"pt_mean": 120, "pt_high": 150, "pt_low": 90},
    }
    features = quant.compute_features("T", quotes_entry, None, None, [])
    assert features["pt_spread_pct"] == pytest.approx((150 - 90) / 120 * 100)
    assert features["pt_upside_pct"] == pytest.approx((120 / 100 - 1) * 100)


# ---------------------------------------------------------------------------
# d14 deltas
# ---------------------------------------------------------------------------


def test_d14_computed_when_two_rows_exactly_14_days_apart():
    rows = [
        {"date": "2026-06-01", "ticker": "T", "price": 100, "short_pct_float": 0.05, "rsi_14": 40},
        {"date": "2026-06-15", "ticker": "T", "price": 110, "short_pct_float": 0.07, "rsi_14": 55},
    ]
    features = quant.compute_features("T", {"price": 110}, None, None, rows)
    assert features["d14_price_pct"] == pytest.approx((110 / 100 - 1) * 100)
    assert features["d14_short_pct_float"] == pytest.approx(0.02)
    assert features["d14_rsi"] == pytest.approx(15.0)


def test_d14_none_with_fewer_than_two_rows():
    rows = [{"date": "2026-06-01", "ticker": "T", "price": 100}]
    features = quant.compute_features("T", {"price": 100}, None, None, rows)
    assert features["d14_price_pct"] is None
    assert features["d14_short_pct_float"] is None
    assert features["d14_rsi"] is None


def test_d14_none_with_zero_rows():
    features = quant.compute_features("T", {"price": 100}, None, None, [])
    assert features["d14_price_pct"] is None


def test_d14_none_when_closest_row_is_under_7_days_away():
    # Mirrors data/history.jsonl's real MU rows: only a 3-day gap exists.
    rows = [
        {"date": "2026-07-07", "ticker": "MU", "price": 938.38},
        {"date": "2026-07-10", "ticker": "MU", "price": 996.87},
    ]
    features = quant.compute_features("MU", {"price": 996.87}, None, None, rows)
    assert features["d14_price_pct"] is None
    assert features["d14_short_pct_float"] is None
    assert features["d14_rsi"] is None


def test_d14_ignores_row_without_ticker_key():
    # Public-API robustness: compute_features can be called with mixed-ticker
    # row lists. A row with no "ticker" key at all must NOT be treated as
    # matching the requested ticker (strict `r.get("ticker") == ticker`).
    rows = [
        {"date": "2026-06-01", "price": 100},  # no ticker key -> must be ignored
        {"date": "2026-06-15", "ticker": "T", "price": 110},
    ]
    features = quant.compute_features("T", {"price": 110}, None, None, rows)
    # Only one row actually belongs to "T" -> fewer than 2 rows -> all None.
    assert features["d14_price_pct"] is None
    assert features["d14_short_pct_float"] is None
    assert features["d14_rsi"] is None


def test_d14_ignores_rows_belonging_to_other_tickers():
    rows = [
        {"date": "2026-06-01", "ticker": "OTHER", "price": 1},
        {"date": "2026-06-15", "ticker": "T", "price": 110},
    ]
    features = quant.compute_features("T", {"price": 110}, None, None, rows)
    assert features["d14_price_pct"] is None


def test_d14_picks_the_row_closest_to_14_days_even_if_its_own_gap_is_short():
    # latest = 2026-06-25. Candidates: 2026-06-01 (gap 24d, |24-14|=10) and
    # 2026-06-20 (gap 5d, |5-14|=9). 06-20 is the closer-to-target row, but
    # its own gap to latest (5d) is < 7 -> the whole d14 triple is None,
    # even though 06-01 alone would have satisfied the >=7 day rule.
    rows = [
        {"date": "2026-06-01", "ticker": "T", "price": 100},
        {"date": "2026-06-20", "ticker": "T", "price": 105},
        {"date": "2026-06-25", "ticker": "T", "price": 108},
    ]
    features = quant.compute_features("T", {"price": 108}, None, None, rows)
    assert features["d14_price_pct"] is None


# ---------------------------------------------------------------------------
# build_quant end-to-end
# ---------------------------------------------------------------------------


OLD_MEMO = """---
ticker: "AAA"
date: 2026-06-01
buy_range: [10, 20]
---
old memo, superseded — must NOT be used
"""

NEW_MEMO = """---
ticker: "AAA"
date: 2026-07-01
buy_range: [80, 100]
price_targets:
  bear: 80
  base: 110
  bull: 140
  p_bear: 0.25
  p_base: 0.5
  p_bull: 0.25
---
latest memo — must be used
"""


@pytest.fixture
def quant_data_dir(tmp_path):
    d = tmp_path / "data"
    (d / "analyses" / "AAA").mkdir(parents=True)
    (d / "watchlist.json").write_text(json.dumps({"stocks": [
        {"ticker": "AAA", "name": "Alpha", "thesis": "t", "layer": "l",
         "added": "2026-07-01", "note": ""},
        {"ticker": "BBB", "name": "Beta", "thesis": "t", "layer": "l",
         "added": "2026-07-01", "note": ""},
    ]}), encoding="utf-8")
    (d / "quotes.json").write_text(json.dumps({
        "fetched_at": "2026-07-20T00:00:00+00:00",
        "quotes": {
            "AAA": {
                "price": 100, "stale": False,
                "analyst": {"pt_mean": 120, "pt_high": 150, "pt_low": 90,
                            "n_analysts": 10, "rec_mean": 1.5},
                "revisions": {"fwd_eps_change_90d_pct": 20, "up_last_30d": 8,
                              "down_last_30d": 2},
                "paired": {"ticker": "AAA.PAIR", "price": 200, "currency": "USD",
                           "fx_usd": 1.0, "parity_usd": 87.0, "premium_pct": 15.0,
                           "fetched_at": "2026-07-20T00:00:00+00:00"},
            },
            "BBB": {"price": 50, "stale": False},
        }}), encoding="utf-8")
    (d / "flows.json").write_text(json.dumps({
        "fetched_at": "2026-07-20T00:00:00+00:00",
        "flows": {
            "AAA": {
                "short_pct_float": 0.05, "put_call_oi_ratio": 1.2, "inst_pct": 0.7,
                "trend": {"rsi_14": 45, "dist_50dma_pct": 5, "dist_200dma_pct": 20,
                          "atr_pct_14": 3, "rel_strength_3m": 10},
            },
        }}), encoding="utf-8")
    (d / "analyses" / "AAA" / "2026-06-01.md").write_text(OLD_MEMO, encoding="utf-8")
    (d / "analyses" / "AAA" / "2026-07-01.md").write_text(NEW_MEMO, encoding="utf-8")
    history_lines = [
        json.dumps({"date": "2026-06-01", "ticker": "AAA", "price": 90,
                    "short_pct_float": 0.04, "rsi_14": 40}),
        json.dumps({"date": "2026-06-20", "ticker": "AAA", "price": 100,
                    "short_pct_float": 0.05, "rsi_14": 45}),
    ]
    (d / "history.jsonl").write_text("\n".join(history_lines) + "\n", encoding="utf-8")
    return d


def test_build_quant_writes_and_returns_matching_dict(quant_data_dir):
    from luxtock import quant as quant_mod

    result = quant_mod.build_quant(quant_data_dir)

    out_path = quant_data_dir / "quant.json"
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk == result

    assert set(result["tickers"].keys()) == {"AAA", "BBB"}
    assert "computed_at" in result


def test_build_quant_uses_latest_memo(quant_data_dir):
    result = quant.build_quant(quant_data_dir)
    aaa = result["tickers"]["AAA"]["features"]
    # New memo's buy_range [80, 100] -> valuation_gap_pct = (100/100-1)*100 = 0
    # If the OLD memo [10, 20] had been used instead, this would be 400.0.
    assert aaa["valuation_gap_pct"] == pytest.approx(0.0)
    assert aaa["gap_to_floor_pct"] == pytest.approx(25.0)
    assert aaa["rr_ratio"] == pytest.approx(0.5)
    assert aaa["ev_return_pct"] == pytest.approx(10.0)


def test_build_quant_computes_d14_from_history(quant_data_dir):
    result = quant.build_quant(quant_data_dir)
    aaa = result["tickers"]["AAA"]["features"]
    assert aaa["d14_price_pct"] == pytest.approx((100 / 90 - 1) * 100)
    assert aaa["d14_short_pct_float"] == pytest.approx(0.01)
    assert aaa["d14_rsi"] == pytest.approx(5.0)


def test_build_quant_full_coverage_ticker_scores(quant_data_dir):
    result = quant.build_quant(quant_data_dir)
    aaa = result["tickers"]["AAA"]
    scores = aaa["scores"]
    assert aaa["features"]["paired_premium_pct"] == pytest.approx(15.0)
    assert scores["coverage"] == pytest.approx(1.0)
    assert scores["valuation"] == pytest.approx(62.0)
    assert scores["momentum"] == pytest.approx(68.0)
    assert scores["positioning"] == pytest.approx(41.1667, abs=1e-3)
    assert scores["trend"] == pytest.approx(61.3333, abs=1e-3)
    assert scores["composite"] == pytest.approx(60.2417, abs=1e-3)
    assert scores["band"] == "fair"
    assert scores["components_used"] == ["momentum", "positioning", "trend", "valuation"]


def test_build_quant_sparse_ticker_no_memo_no_flows(quant_data_dir):
    result = quant.build_quant(quant_data_dir)
    bbb_features = result["tickers"]["BBB"]["features"]
    bbb_scores = result["tickers"]["BBB"]["scores"]
    assert bbb_features["price"] == pytest.approx(50.0)
    assert bbb_features["valuation_gap_pct"] is None
    assert bbb_features["rsi_14"] is None
    assert bbb_scores["coverage"] == pytest.approx(1 / 23)
    assert bbb_scores["composite"] is None
    assert bbb_scores["band"] is None


def test_build_quant_empty_watchlist_writes_empty_tickers(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    result = quant.build_quant(d)
    assert result["tickers"] == {}
    assert (d / "quant.json").exists()


# ---------------------------------------------------------------------------
# quant_history.jsonl (v1.1 addition #2)
# ---------------------------------------------------------------------------


def test_build_quant_appends_quant_history_row_per_ticker(quant_data_dir):
    quant.build_quant(quant_data_dir)
    path = quant_data_dir / "quant_history.jsonl"
    assert path.exists()

    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_ticker = {row["ticker"]: row for row in lines}
    assert set(by_ticker) == {"AAA", "BBB"}

    today = _today()
    aaa = by_ticker["AAA"]
    assert aaa["date"] == today
    expected_keys = {
        "date", "ticker", "composite", "band", "valuation", "momentum",
        "positioning", "trend", "coverage", "dispersion", "price",
        "valuation_gap_pct", "ev_return_pct", "paired_premium_pct",
    }
    assert set(aaa.keys()) == expected_keys
    assert aaa["composite"] == pytest.approx(60.2417, abs=1e-3)
    assert aaa["band"] == "fair"
    assert aaa["coverage"] == pytest.approx(1.0)
    assert aaa["price"] == pytest.approx(100.0)
    assert aaa["valuation_gap_pct"] == pytest.approx(0.0)
    assert aaa["ev_return_pct"] == pytest.approx(10.0)
    assert aaa["paired_premium_pct"] == pytest.approx(15.0)

    bbb = by_ticker["BBB"]
    assert bbb["date"] == today
    assert bbb["composite"] is None
    assert bbb["band"] is None
    assert bbb["price"] == pytest.approx(50.0)


def test_build_quant_same_date_rerun_replaces_rows(quant_data_dir):
    quant.build_quant(quant_data_dir)
    path = quant_data_dir / "quant_history.jsonl"

    # Mutate the underlying quote so the second run's numbers differ.
    quotes = json.loads((quant_data_dir / "quotes.json").read_text(encoding="utf-8"))
    quotes["quotes"]["AAA"]["price"] = 105
    (quant_data_dir / "quotes.json").write_text(json.dumps(quotes), encoding="utf-8")

    quant.build_quant(quant_data_dir)
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    aaa_rows = [row for row in lines if row["ticker"] == "AAA"]
    bbb_rows = [row for row in lines if row["ticker"] == "BBB"]
    # Same-date rerun replaces, never duplicates.
    assert len(aaa_rows) == 1
    assert len(bbb_rows) == 1
    assert aaa_rows[0]["price"] == pytest.approx(105.0)


def test_build_quant_preserves_rows_from_other_dates(quant_data_dir):
    path = quant_data_dir / "quant_history.jsonl"
    old_row = json.dumps({"date": "2020-01-01", "ticker": "AAA", "composite": 1.0})
    path.write_text(old_row + "\n", encoding="utf-8")

    quant.build_quant(quant_data_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert old_row in lines  # untouched, byte-for-byte

    today = _today()
    parsed = [json.loads(l) for l in lines if l.strip()]
    todays_rows = [row for row in parsed if row["date"] == today]
    assert len(todays_rows) == 2  # AAA + BBB
    assert len(parsed) == 3  # old row + 2 fresh rows


def test_build_quant_preserves_unparseable_lines(quant_data_dir):
    path = quant_data_dir / "quant_history.jsonl"
    garbage = "{this is not valid json"
    path.write_text(garbage + "\n", encoding="utf-8")

    quant.build_quant(quant_data_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert garbage in lines  # preserved, never destroyed

    parsed = []
    for line in lines:
        if line == garbage:
            continue
        parsed.append(json.loads(line))
    assert len(parsed) == 2  # AAA + BBB still appended normally


def test_build_quant_creates_missing_quant_history_file(quant_data_dir):
    path = quant_data_dir / "quant_history.jsonl"
    assert not path.exists()

    quant.build_quant(quant_data_dir)
    assert path.exists()
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2


def test_build_quant_writes_empty_quant_history_for_empty_watchlist(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    quant.build_quant(d)
    path = d / "quant_history.jsonl"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == ""
