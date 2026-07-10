import json
from datetime import date, timedelta

from luxtock import calibrate

AS_OF = date(2025, 1, 15)  # > memo_date 2024-01-01 + 365d (2024-12-31) => matured


def write_memo(tmp_path, ticker, memo_date, *, bear, base, bull,
                p_bear=0.3, p_base=0.4, p_bull=0.3, price_at_analysis=100.0,
                action="watch_only", horizon="12mo"):
    d = tmp_path / "analyses" / ticker
    d.mkdir(parents=True, exist_ok=True)
    text = f"""---
ticker: "{ticker}"
date: {memo_date.isoformat()}
thesis: test-thesis
action: {action}
confidence: medium
buy_range: [{bear}, {bull}]
price_targets:
  bear: {bear}
  base: {base}
  bull: {bull}
  p_bear: {p_bear}
  p_base: {p_base}
  p_bull: {p_bull}
  horizon: {horizon}
price_at_analysis: {price_at_analysis}
verdict: in_range
thesis_health: intact
top_risks: [x]
review_trigger: "test trigger"
signals:
  chain: favorable
  narrative: favorable
  fundamentals: favorable
  valuation: favorable
  flows: neutral
  sentiment: neutral
  competition: neutral
  macro: neutral
---
# {ticker} test memo
"""
    (d / f"{memo_date.isoformat()}.md").write_text(text, encoding="utf-8")


def append_history(tmp_path, rows):
    p = tmp_path / "history.jsonl"
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def write_quotes(tmp_path, prices: dict):
    (tmp_path / "quotes.json").write_text(
        json.dumps({"quotes": {t: {"price": p} for t, p in prices.items()}}),
        encoding="utf-8")


def append_quant_history(tmp_path, rows):
    p = tmp_path / "quant_history.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Empty-safe
# ---------------------------------------------------------------------------

def test_empty_data_is_safe(tmp_path):
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    assert result["matured"] == []
    assert result["tracking"] == []
    assert result["aggregate"] == {"n": 0, "mean_brier": None}
    assert result["as_of"] == AS_OF.isoformat()
    # file was written
    on_disk = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert on_disk == result


def test_as_of_defaults_to_today(tmp_path):
    result = calibrate.calibrate(tmp_path)
    assert result["as_of"] == date.today().isoformat()


# ---------------------------------------------------------------------------
# Realized tier branches (bear / base / bull, incl. midpoint boundaries)
# ---------------------------------------------------------------------------

MEMO_DATE = date(2024, 1, 1)
MATURITY = date(2024, 12, 31)  # memo_date + 365d


def _matured_case(tmp_path, ticker, realized_price):
    write_memo(tmp_path, ticker, MEMO_DATE, bear=80, base=100, bull=140)
    append_history(tmp_path, [
        {"date": MATURITY.isoformat(), "ticker": ticker, "price": realized_price},
    ])


def test_realized_tier_bear_below_midpoint(tmp_path):
    _matured_case(tmp_path, "BEARX", 85)  # < (80+100)/2 = 90
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "BEARX")
    assert entry["realized_tier"] == "bear"


def test_realized_tier_bear_at_lower_midpoint_boundary(tmp_path):
    _matured_case(tmp_path, "BOUNDBEAR", 90)  # == (80+100)/2, bear per <=
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "BOUNDBEAR")
    assert entry["realized_tier"] == "bear"


def test_realized_tier_base_between_midpoints(tmp_path):
    _matured_case(tmp_path, "BASEX", 100)  # strictly between 90 and 120
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "BASEX")
    assert entry["realized_tier"] == "base"


def test_realized_tier_bull_at_upper_midpoint_boundary(tmp_path):
    _matured_case(tmp_path, "BOUNDBULL", 120)  # == (100+140)/2, bull per >=
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "BOUNDBULL")
    assert entry["realized_tier"] == "bull"


def test_realized_tier_bull_above_midpoint(tmp_path):
    _matured_case(tmp_path, "BULLX", 130)  # > 120
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "BULLX")
    assert entry["realized_tier"] == "bull"


# ---------------------------------------------------------------------------
# Brier score correctness (hand-computed)
# ---------------------------------------------------------------------------

def test_brier_score_hand_computed(tmp_path):
    write_memo(tmp_path, "BRIERX", MEMO_DATE, bear=80, base=100, bull=140,
               p_bear=0.5, p_base=0.3, p_bull=0.2)
    append_history(tmp_path, [
        {"date": MATURITY.isoformat(), "ticker": "BRIERX", "price": 85},  # realized: bear
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "BRIERX")
    assert entry["realized_tier"] == "bear"
    # one-hot [1, 0, 0]; brier = (0.5-1)^2 + (0.3-0)^2 + (0.2-0)^2
    expected = (0.5 - 1) ** 2 + (0.3 - 0) ** 2 + (0.2 - 0) ** 2
    assert abs(entry["brier"] - expected) < 1e-9
    assert abs(expected - 0.38) < 1e-9
    assert result["aggregate"]["n"] == 1
    assert abs(result["aggregate"]["mean_brier"] - expected) < 1e-9


def test_uninformative_uniform_prior_brier_is_two_thirds(tmp_path):
    write_memo(tmp_path, "UNIFORMX", MEMO_DATE, bear=80, base=100, bull=140,
               p_bear=1 / 3, p_base=1 / 3, p_bull=1 / 3)
    append_history(tmp_path, [
        {"date": MATURITY.isoformat(), "ticker": "UNIFORMX", "price": 85},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "UNIFORMX")
    assert abs(entry["brier"] - 0.6667) < 1e-3


# ---------------------------------------------------------------------------
# +/-14 day skip path
# ---------------------------------------------------------------------------

def test_realized_price_skipped_outside_14_day_window(tmp_path):
    write_memo(tmp_path, "SKIPX", MEMO_DATE, bear=80, base=100, bull=140)
    # 20 days away from maturity 2024-12-31 -> outside +/-14d window
    far_date = date(2025, 1, 20)
    append_history(tmp_path, [
        {"date": far_date.isoformat(), "ticker": "SKIPX", "price": 95},
    ])
    result = calibrate.calibrate(tmp_path, as_of=date(2025, 2, 1))
    entry = next(m for m in result["matured"] if m["ticker"] == "SKIPX")
    assert entry["realized_price"] is None
    assert entry["realized_tier"] is None
    assert entry["brier"] is None
    assert entry["note"] is not None
    # excluded from the graded aggregate
    assert result["aggregate"]["n"] == 0
    assert result["aggregate"]["mean_brier"] is None


def test_realized_price_found_within_14_day_window(tmp_path):
    write_memo(tmp_path, "NEARX", MEMO_DATE, bear=80, base=100, bull=140)
    near_date = date(2025, 1, 10)  # 10 days after maturity, within window
    append_history(tmp_path, [
        {"date": near_date.isoformat(), "ticker": "NEARX", "price": 95},
    ])
    result = calibrate.calibrate(tmp_path, as_of=date(2025, 2, 1))
    entry = next(m for m in result["matured"] if m["ticker"] == "NEARX")
    assert entry["realized_price"] == 95
    assert entry["note"] is None


def test_no_history_rows_at_all_is_skipped_with_note(tmp_path):
    write_memo(tmp_path, "NOHIST", MEMO_DATE, bear=80, base=100, bull=140)
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "NOHIST")
    assert entry["realized_price"] is None
    assert entry["note"] is not None


# ---------------------------------------------------------------------------
# MAE / MFE path stats
# ---------------------------------------------------------------------------

def test_mae_mfe_from_price_path(tmp_path):
    write_memo(tmp_path, "PATHX", MEMO_DATE, bear=80, base=100, bull=140,
               price_at_analysis=100.0)
    append_history(tmp_path, [
        {"date": "2024-01-01", "ticker": "PATHX", "price": 100.0},   # 0%
        {"date": "2024-02-20", "ticker": "PATHX", "price": 110.0},   # +10%
        {"date": "2024-04-10", "ticker": "PATHX", "price": 90.0},    # -10%
        {"date": "2024-07-19", "ticker": "PATHX", "price": 105.0},   # +5%
        {"date": "2024-12-31", "ticker": "PATHX", "price": 102.0},   # +2% (also realized price)
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "PATHX")
    assert abs(entry["mae_pct"] - (-10.0)) < 1e-9
    assert abs(entry["mfe_pct"] - 10.0) < 1e-9
    assert entry["realized_price"] == 102.0


def test_mae_mfe_none_when_price_at_analysis_missing(tmp_path):
    d = tmp_path / "analyses" / "NOPRICEX"
    d.mkdir(parents=True)
    text = f"""---
ticker: "NOPRICEX"
date: {MEMO_DATE.isoformat()}
thesis: test-thesis
action: watch_only
confidence: medium
buy_range: [80, 140]
price_targets:
  bear: 80
  base: 100
  bull: 140
  p_bear: 0.3
  p_base: 0.4
  p_bull: 0.3
  horizon: 12mo
price_at_analysis: null
verdict: in_range
thesis_health: intact
top_risks: [x]
review_trigger: "test trigger"
signals:
  chain: favorable
  narrative: favorable
  fundamentals: favorable
  valuation: favorable
  flows: neutral
  sentiment: neutral
  competition: neutral
  macro: neutral
---
# NOPRICEX test memo
"""
    (d / f"{MEMO_DATE.isoformat()}.md").write_text(text, encoding="utf-8")
    # realized price is found (row at maturity date), but price_at_analysis
    # is null so path stats can't be computed against a baseline.
    append_history(tmp_path, [
        {"date": MATURITY.isoformat(), "ticker": "NOPRICEX", "price": 95},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    entry = next(m for m in result["matured"] if m["ticker"] == "NOPRICEX")
    assert entry["realized_price"] == 95
    assert entry["mae_pct"] is None
    assert entry["mfe_pct"] is None


# ---------------------------------------------------------------------------
# price_targets missing/null memos are skipped entirely
# ---------------------------------------------------------------------------

def test_memo_without_full_price_targets_excluded_from_matured(tmp_path):
    d = tmp_path / "analyses" / "NULLTARGET"
    d.mkdir(parents=True)
    text = f"""---
ticker: "NULLTARGET"
date: {MEMO_DATE.isoformat()}
thesis: test-thesis
action: watch_only
confidence: medium
buy_range: null
price_targets: null
price_at_analysis: 100.0
verdict: in_range
thesis_health: intact
top_risks: [x]
review_trigger: "test trigger"
signals:
  chain: favorable
  narrative: favorable
  fundamentals: favorable
  valuation: favorable
  flows: neutral
  sentiment: neutral
  competition: neutral
  macro: neutral
---
# NULLTARGET test memo
"""
    (d / f"{MEMO_DATE.isoformat()}.md").write_text(text, encoding="utf-8")
    append_history(tmp_path, [
        {"date": MATURITY.isoformat(), "ticker": "NULLTARGET", "price": 95},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    assert all(m["ticker"] != "NULLTARGET" for m in result["matured"])


# ---------------------------------------------------------------------------
# Tracking: percentile math, incl. clamping outside [bear, bull]
# ---------------------------------------------------------------------------

TRACK_MEMO_DATE = date(2026, 6, 1)  # immature relative to AS_OF (2025-01-15 wouldn't work; use later as_of)
TRACK_AS_OF = date(2026, 7, 11)


def test_tracking_percentile_within_range(tmp_path):
    write_memo(tmp_path, "TRACKMID", TRACK_MEMO_DATE, bear=80, base=100, bull=140)
    write_quotes(tmp_path, {"TRACKMID": 90.0})
    result = calibrate.calibrate(tmp_path, as_of=TRACK_AS_OF)
    entry = next(t for t in result["tracking"] if t["ticker"] == "TRACKMID")
    # (90-80)/(140-80)*100 = 16.666...
    assert abs(entry["pct_between_bear_bull"] - 16.6667) < 1e-3
    assert entry["above_base"] is False
    assert entry["current_price"] == 90.0


def test_tracking_percentile_clamps_above_bull(tmp_path):
    write_memo(tmp_path, "TRACKHIGH", TRACK_MEMO_DATE, bear=80, base=100, bull=140)
    write_quotes(tmp_path, {"TRACKHIGH": 200.0})
    result = calibrate.calibrate(tmp_path, as_of=TRACK_AS_OF)
    entry = next(t for t in result["tracking"] if t["ticker"] == "TRACKHIGH")
    assert entry["pct_between_bear_bull"] == 100.0
    assert entry["above_base"] is True


def test_tracking_percentile_clamps_below_bear(tmp_path):
    write_memo(tmp_path, "TRACKLOW", TRACK_MEMO_DATE, bear=80, base=100, bull=140)
    write_quotes(tmp_path, {"TRACKLOW": 20.0})
    result = calibrate.calibrate(tmp_path, as_of=TRACK_AS_OF)
    entry = next(t for t in result["tracking"] if t["ticker"] == "TRACKLOW")
    assert entry["pct_between_bear_bull"] == 0.0
    assert entry["above_base"] is False


def test_tracking_months_elapsed(tmp_path):
    write_memo(tmp_path, "TRACKMONTHS", TRACK_MEMO_DATE, bear=80, base=100, bull=140)
    write_quotes(tmp_path, {"TRACKMONTHS": 100.0})
    result = calibrate.calibrate(tmp_path, as_of=TRACK_AS_OF)
    entry = next(t for t in result["tracking"] if t["ticker"] == "TRACKMONTHS")
    days = (TRACK_AS_OF - TRACK_MEMO_DATE).days
    assert entry["months_elapsed"] == round(days / 30.4375, 1)


def test_tracking_excludes_already_matured_memo(tmp_path):
    # memo dated far enough back that as_of makes it matured -> not tracked
    write_memo(tmp_path, "OLDMATURED", MEMO_DATE, bear=80, base=100, bull=140)
    append_history(tmp_path, [
        {"date": MATURITY.isoformat(), "ticker": "OLDMATURED", "price": 95},
    ])
    write_quotes(tmp_path, {"OLDMATURED": 95.0})
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    assert all(t["ticker"] != "OLDMATURED" for t in result["tracking"])
    # but it does show up matured
    assert any(m["ticker"] == "OLDMATURED" for m in result["matured"])


def test_tracking_skips_ticker_missing_from_quotes(tmp_path):
    write_memo(tmp_path, "NOQUOTE", TRACK_MEMO_DATE, bear=80, base=100, bull=140)
    # no quotes.json written at all
    result = calibrate.calibrate(tmp_path, as_of=TRACK_AS_OF)
    assert all(t["ticker"] != "NOQUOTE" for t in result["tracking"])


def test_tracking_uses_latest_memo_per_ticker(tmp_path):
    write_memo(tmp_path, "MULTI", date(2026, 1, 1), bear=50, base=70, bull=90)
    write_memo(tmp_path, "MULTI", TRACK_MEMO_DATE, bear=80, base=100, bull=140)
    write_quotes(tmp_path, {"MULTI": 90.0})
    result = calibrate.calibrate(tmp_path, as_of=TRACK_AS_OF)
    entries = [t for t in result["tracking"] if t["ticker"] == "MULTI"]
    assert len(entries) == 1
    assert entries[0]["memo_date"] == TRACK_MEMO_DATE.isoformat()


# ---------------------------------------------------------------------------
# v1.1 addition #3 -- score_calibration (join quant_history with forward
# prices, bucket by band / composite quartile). See framework/quant.md.
# ---------------------------------------------------------------------------

SC_DATE = date(2026, 1, 1)
SC_D30 = SC_DATE + timedelta(days=30)
SC_D90 = SC_DATE + timedelta(days=90)


def test_score_calibration_empty_when_no_ledger(tmp_path):
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    assert result["score_calibration"] == {
        "n_rows": 0,
        "windows": {
            "30d": {"n_scored": 0, "by_band": [], "by_quartile": []},
            "90d": {"n_scored": 0, "by_band": [], "by_quartile": []},
        },
    }
    on_disk = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert on_disk["score_calibration"] == result["score_calibration"]


def test_score_calibration_skips_unparseable_and_malformed_lines(tmp_path):
    p = tmp_path / "quant_history.jsonl"
    p.write_text(
        "not json at all\n"
        + json.dumps({"ticker": "X"}) + "\n"  # missing date
        + json.dumps({"date": "2026-01-01"}) + "\n"  # missing ticker
        + json.dumps({
            "date": "2026-01-01", "ticker": "OKX", "composite": 80,
            "band": "strong", "price": 100,
        }) + "\n",
        encoding="utf-8",
    )
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    assert result["score_calibration"]["n_rows"] == 1


def test_score_calibration_no_matchable_rows_reports_n_rows(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "NOMATCH", "composite": 60,
         "band": "fair", "price": 100},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    sc = result["score_calibration"]
    assert sc["n_rows"] == 1
    assert sc["windows"]["30d"] == {"n_scored": 0, "by_band": [], "by_quartile": []}
    assert sc["windows"]["90d"] == {"n_scored": 0, "by_band": [], "by_quartile": []}


def test_forward_return_matches_row_at_exact_30d_boundary(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "FWD30", "composite": 75,
         "band": "strong", "price": 100},
    ])
    append_history(tmp_path, [
        {"date": (SC_DATE + timedelta(days=29)).isoformat(), "ticker": "FWD30", "price": 90},
        {"date": SC_D30.isoformat(), "ticker": "FWD30", "price": 110},
        {"date": (SC_DATE + timedelta(days=45)).isoformat(), "ticker": "FWD30", "price": 200},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    assert window["n_scored"] == 1
    strong = next(b for b in window["by_band"] if b["band"] == "strong")
    assert strong["n"] == 1
    assert abs(strong["mean_return_pct"] - 10.0) < 1e-9  # (110/100-1)*100
    assert strong["hit_rate"] == 1.0
    # no history row >= +90d, so the 90d window has nothing to score
    assert result["score_calibration"]["windows"]["90d"]["n_scored"] == 0


def test_forward_return_picks_nearest_at_or_after_not_furthest(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "FWD30B", "composite": 60,
         "band": "fair", "price": 50},
    ])
    append_history(tmp_path, [
        {"date": (SC_DATE + timedelta(days=31)).isoformat(), "ticker": "FWD30B", "price": 55},
        {"date": (SC_DATE + timedelta(days=50)).isoformat(), "ticker": "FWD30B", "price": 100},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    fair = next(b for b in window["by_band"] if b["band"] == "fair")
    assert abs(fair["mean_return_pct"] - 10.0) < 1e-9  # (55/50-1)*100, not the +50d row


def test_forward_price_prefers_history_jsonl_over_quant_history_price(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "BOTHX", "composite": 55,
         "band": "fair", "price": 100},
        {"date": (SC_DATE + timedelta(days=31)).isoformat(), "ticker": "BOTHX",
         "composite": 58, "band": "fair", "price": 999},
    ])
    append_history(tmp_path, [
        {"date": (SC_DATE + timedelta(days=31)).isoformat(), "ticker": "BOTHX", "price": 120},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    fair = next(b for b in window["by_band"] if b["band"] == "fair")
    # history.jsonl (120) wins over quant_history's own price field (999)
    assert abs(fair["mean_return_pct"] - 20.0) < 1e-9  # (120/100-1)*100


def test_forward_price_falls_back_to_quant_history_when_no_history_jsonl(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "FALLX", "composite": 55,
         "band": "fair", "price": 50},
        {"date": (SC_DATE + timedelta(days=31)).isoformat(), "ticker": "FALLX",
         "composite": 58, "band": "fair", "price": 60},
    ])
    # no history.jsonl at all
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    fair = next(b for b in window["by_band"] if b["band"] == "fair")
    assert fair["n"] == 1  # only the first row has a +30d target it can reach
    assert abs(fair["mean_return_pct"] - 20.0) < 1e-9  # (60/50-1)*100


def test_band_bucket_hand_computed_mean_and_hit_rate(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "F1", "composite": 55, "band": "fair", "price": 100},
        {"date": SC_DATE.isoformat(), "ticker": "F2", "composite": 58, "band": "fair", "price": 100},
        {"date": SC_DATE.isoformat(), "ticker": "F3", "composite": 52, "band": "fair", "price": 100},
    ])
    append_history(tmp_path, [
        {"date": SC_D30.isoformat(), "ticker": "F1", "price": 105},  # +5%
        {"date": SC_D30.isoformat(), "ticker": "F2", "price": 97},   # -3%
        {"date": SC_D30.isoformat(), "ticker": "F3", "price": 110},  # +10%
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    fair = next(b for b in window["by_band"] if b["band"] == "fair")
    assert fair["n"] == 3
    assert abs(fair["mean_return_pct"] - 4.0) < 1e-9  # (5 - 3 + 10) / 3
    assert abs(fair["hit_rate"] - (2 / 3)) < 1e-9


def test_null_band_rows_excluded_from_by_band(tmp_path):
    append_quant_history(tmp_path, [
        {"date": SC_DATE.isoformat(), "ticker": "NULLBAND", "composite": 40,
         "band": None, "price": 100},
    ])
    append_history(tmp_path, [
        {"date": SC_D30.isoformat(), "ticker": "NULLBAND", "price": 120},
    ])
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    assert window["n_scored"] == 1
    assert window["by_band"] == []


def test_quartile_bucketing_skipped_below_8_scored_rows(tmp_path):
    quant_rows, hist_rows = [], []
    for i in range(5):
        t = f"Q{i}"
        quant_rows.append({"date": SC_DATE.isoformat(), "ticker": t,
                            "composite": 50 + i, "band": "fair", "price": 100})
        hist_rows.append({"date": SC_D30.isoformat(), "ticker": t, "price": 105})
    append_quant_history(tmp_path, quant_rows)
    append_history(tmp_path, hist_rows)
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    assert window["n_scored"] == 5
    assert window["by_quartile"] == []


def test_quartile_bucketing_hand_computed_with_8_rows(tmp_path):
    composites = [10, 20, 30, 40, 50, 60, 70, 80]
    quant_rows, hist_rows = [], []
    for i, c in enumerate(composites):
        t = f"QT{i}"
        quant_rows.append({"date": SC_DATE.isoformat(), "ticker": t,
                            "composite": c, "band": "fair", "price": 100})
        future_price = 100 + c / 10  # so forward_return_pct == c / 10
        hist_rows.append({"date": SC_D30.isoformat(), "ticker": t, "price": future_price})
    append_quant_history(tmp_path, quant_rows)
    append_history(tmp_path, hist_rows)
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    assert window["n_scored"] == 8
    quartiles = window["by_quartile"]
    assert [q["quartile"] for q in quartiles] == ["Q1", "Q2", "Q3", "Q4"]
    expected_means = [1.5, 3.5, 5.5, 7.5]  # mean of {1,2} {3,4} {5,6} {7,8}
    for q, expected in zip(quartiles, expected_means):
        assert q["n"] == 2
        assert abs(q["mean_return_pct"] - expected) < 1e-9
        assert q["hit_rate"] == 1.0


def test_quartile_bucketing_excludes_rows_with_null_composite(tmp_path):
    quant_rows, hist_rows = [], []
    for i in range(8):
        t = f"NC{i}"
        composite = None if i == 0 else 10 * i  # only 7 rows have numeric composite
        quant_rows.append({"date": SC_DATE.isoformat(), "ticker": t,
                            "composite": composite, "band": "fair", "price": 100})
        hist_rows.append({"date": SC_D30.isoformat(), "ticker": t, "price": 110})
    append_quant_history(tmp_path, quant_rows)
    append_history(tmp_path, hist_rows)
    result = calibrate.calibrate(tmp_path, as_of=AS_OF)
    window = result["score_calibration"]["windows"]["30d"]
    assert window["n_scored"] == 8  # all 8 still have a valid forward return
    assert window["by_quartile"] == []  # but only 7 have numeric composite (<8)
