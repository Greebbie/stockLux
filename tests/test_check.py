import json

import pytest

from luxtock import check


def _write_watchlist(data_dir, stocks, cash_usd=None):
    wl = {"stocks": stocks}
    if cash_usd is not None:
        wl["cash_usd"] = cash_usd
    (data_dir / "watchlist.json").write_text(json.dumps(wl, ensure_ascii=False), encoding="utf-8")


def _write_quotes(data_dir, prices: dict):
    (data_dir / "quotes.json").write_text(json.dumps({
        "fetched_at": "2026-07-10T00:00:00+00:00",
        "quotes": {t: {"price": p} for t, p in prices.items()},
    }, ensure_ascii=False), encoding="utf-8")


def _append_quant_history(data_dir, rows):
    p = data_dir / "quant_history.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _entry(ticker, holding=False, layer="l", thesis="t"):
    return {"ticker": ticker, "name": ticker, "thesis": thesis, "layer": layer,
            "added": "2026-07-01", "note": "", "holding": holding}


def _write_memo(data_dir, ticker, date, *, action=None, entry_plan=None,
                 buy_range=None, price_targets=None):
    d = data_dir / "analyses" / ticker
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", f'ticker: "{ticker}"', f"date: {date}"]
    if action is not None:
        lines.append(f"action: {action}")
    if buy_range is not None:
        lines.append(f"buy_range: [{buy_range[0]}, {buy_range[1]}]")
    if price_targets is not None:
        lines.append("price_targets:")
        for k, v in price_targets.items():
            lines.append(f"  {k}: {v}")
    if entry_plan is not None:
        lines.append("entry_plan:")
        lines.append(f"  tranches: [{', '.join(str(t) for t in entry_plan['tranches'])}]")
        lines.append(f"  invalidation: {entry_plan['invalidation']}")
    lines.append("---")
    lines.append("body")
    (d / f"{date}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# skip conditions
# ---------------------------------------------------------------------------

def test_skip_when_no_memo(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 100.0})
    result = check.run_checks(tmp_path)
    assert result["alerts"] == []


def test_skip_when_no_price(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    # no quotes.json at all
    result = check.run_checks(tmp_path)
    assert result["alerts"] == []


def test_empty_data_safe(tmp_path):
    result = check.run_checks(tmp_path)
    assert result["alerts"] == []
    assert "checked_at" in result


# ---------------------------------------------------------------------------
# entry_tranche
# ---------------------------------------------------------------------------

def test_entry_tranche_fires_at_first_tranche_boundary(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 100.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "entry_tranche"]
    assert len(alerts) == 1
    assert alerts[0]["ticker"] == "A"
    assert alerts[0]["level"] == "info"
    assert "100" in alerts[0]["detail"]


def test_entry_tranche_deepest_only(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 85.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "entry_tranche"]
    assert len(alerts) == 1
    assert "90" in alerts[0]["detail"]
    assert "100" not in alerts[0]["detail"]


def test_entry_tranche_exact_boundary_on_deepest_tranche(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 80.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "entry_tranche"]
    assert len(alerts) == 1
    assert "80" in alerts[0]["detail"]


def test_entry_tranche_not_reached_above_all_tranches(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 101.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "entry_tranche"] == []


def test_entry_tranche_requires_enter_or_wait_action(tmp_path):
    _write_watchlist(tmp_path, [_entry("A", holding=True)])
    _write_quotes(tmp_path, {"A": 85.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="hold",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "entry_tranche"] == []


# ---------------------------------------------------------------------------
# invalidation supersedes entry_tranche
# ---------------------------------------------------------------------------

def test_invalidation_fires_at_boundary(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 70.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    inval = [a for a in result["alerts"] if a["kind"] == "invalidation"]
    assert len(inval) == 1
    assert inval[0]["level"] == "warning"
    assert "70" in inval[0]["detail"]


def test_invalidation_supersedes_entry_tranche(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 65.0})  # below invalidation and all tranches
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    kinds = [a["kind"] for a in result["alerts"]]
    assert "invalidation" in kinds
    assert "entry_tranche" not in kinds


def test_invalidation_not_reached_above_boundary(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 71.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70})
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "invalidation"] == []


# ---------------------------------------------------------------------------
# below_floor
# ---------------------------------------------------------------------------

def test_below_floor_fires_strictly_below(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 95.0})
    _write_memo(tmp_path, "A", "2026-07-08", buy_range=[96, 156])
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "below_floor"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "info"
    assert "96" in alerts[0]["detail"]


def test_below_floor_exact_boundary_does_not_fire(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 96.0})
    _write_memo(tmp_path, "A", "2026-07-08", buy_range=[96, 156])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "below_floor"] == []


# ---------------------------------------------------------------------------
# trim_threshold
# ---------------------------------------------------------------------------

def test_trim_threshold_fires_at_boundary_when_holding(tmp_path):
    _write_watchlist(tmp_path, [_entry("A", holding=True)])
    _write_quotes(tmp_path, {"A": 195.0})  # 156 * 1.25 = 195
    _write_memo(tmp_path, "A", "2026-07-08", buy_range=[96, 156])
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "trim_threshold"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "warning"


def test_trim_threshold_just_below_boundary_no_fire(tmp_path):
    _write_watchlist(tmp_path, [_entry("A", holding=True)])
    _write_quotes(tmp_path, {"A": 194.99})
    _write_memo(tmp_path, "A", "2026-07-08", buy_range=[96, 156])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "trim_threshold"] == []


def test_trim_threshold_requires_holding(tmp_path):
    _write_watchlist(tmp_path, [_entry("A", holding=False)])
    _write_quotes(tmp_path, {"A": 195.0})
    _write_memo(tmp_path, "A", "2026-07-08", buy_range=[96, 156])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "trim_threshold"] == []


# ---------------------------------------------------------------------------
# through_bear / at_bull_target
# ---------------------------------------------------------------------------

def test_through_bear_fires_at_boundary(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 120.0})
    _write_memo(tmp_path, "A", "2026-07-08",
                price_targets={"bear": 120, "base": 175, "bull": 288})
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "through_bear"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "warning"


def test_through_bear_not_fired_above_bear(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 120.01})
    _write_memo(tmp_path, "A", "2026-07-08",
                price_targets={"bear": 120, "base": 175, "bull": 288})
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "through_bear"] == []


def test_at_bull_target_fires_at_boundary(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 288.0})
    _write_memo(tmp_path, "A", "2026-07-08",
                price_targets={"bear": 120, "base": 175, "bull": 288})
    result = check.run_checks(tmp_path)
    alerts = [a for a in result["alerts"] if a["kind"] == "at_bull_target"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "info"


def test_at_bull_target_not_fired_below_bull(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 287.99})
    _write_memo(tmp_path, "A", "2026-07-08",
                price_targets={"bear": 120, "base": 175, "bull": 288})
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "at_bull_target"] == []


# ---------------------------------------------------------------------------
# portfolio flags passthrough
# ---------------------------------------------------------------------------

def test_portfolio_flags_passthrough(tmp_path):
    _write_watchlist(tmp_path, [
        _entry("A", holding=True, layer="l1", thesis="t1"),
    ])
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "stocks": [{**_entry("A", holding=True), "shares": 1}],
    }), encoding="utf-8")
    _write_quotes(tmp_path, {"A": 100.0})
    result = check.run_checks(tmp_path)
    portfolio_alerts = [a for a in result["alerts"] if a["ticker"] == "PORTFOLIO"]
    assert len(portfolio_alerts) >= 1
    single_name = [a for a in portfolio_alerts if a["kind"] == "single_name"]
    assert len(single_name) == 1
    assert single_name[0]["level"] == "warning"
    assert "detail" in single_name[0]


def test_no_portfolio_flags_when_none_triggered(tmp_path):
    _write_watchlist(tmp_path, [])
    _write_quotes(tmp_path, {})
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["ticker"] == "PORTFOLIO"] == []


# ---------------------------------------------------------------------------
# real-world fixture sanity (SKHYV-shaped memo)
# ---------------------------------------------------------------------------

def test_multiple_alerts_can_fire_for_same_ticker(tmp_path):
    _write_watchlist(tmp_path, [_entry("A")])
    _write_quotes(tmp_path, {"A": 85.0})
    _write_memo(tmp_path, "A", "2026-07-08", action="wait_for_pullback",
                entry_plan={"tranches": [100, 90, 80], "invalidation": 70},
                buy_range=[96, 156])
    result = check.run_checks(tmp_path)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "entry_tranche" in kinds
    assert "below_floor" in kinds


def test_checked_at_is_iso_utc_timestamp(tmp_path):
    result = check.run_checks(tmp_path)
    # Should be parseable as an ISO timestamp with UTC offset
    from datetime import datetime
    parsed = datetime.fromisoformat(result["checked_at"])
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# v1.1 addition #4 -- band_flip alerts (framework/quant.md). Independent of
# watchlist/memo state: driven entirely by data/quant_history.jsonl.
# ---------------------------------------------------------------------------

def test_band_flip_fires_on_change(tmp_path):
    _append_quant_history(tmp_path, [
        {"date": "2026-07-01", "ticker": "BF1", "composite": 55, "band": "fair"},
        {"date": "2026-07-08", "ticker": "BF1", "composite": 75, "band": "strong"},
    ])
    result = check.run_checks(tmp_path)
    flips = [a for a in result["alerts"] if a["kind"] == "band_flip"]
    assert len(flips) == 1
    assert flips[0]["ticker"] == "BF1"
    assert flips[0]["level"] == "info"
    assert "fair" in flips[0]["detail"]
    assert "strong" in flips[0]["detail"]
    assert "55.0" in flips[0]["detail"]
    assert "75.0" in flips[0]["detail"]


def test_band_flip_silent_on_same_band(tmp_path):
    _append_quant_history(tmp_path, [
        {"date": "2026-07-01", "ticker": "BF2", "composite": 55, "band": "fair"},
        {"date": "2026-07-08", "ticker": "BF2", "composite": 58, "band": "fair"},
    ])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_silent_on_single_date(tmp_path):
    _append_quant_history(tmp_path, [
        {"date": "2026-07-01", "ticker": "BF3", "composite": 55, "band": "fair"},
    ])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_silent_on_null_band(tmp_path):
    _append_quant_history(tmp_path, [
        {"date": "2026-07-01", "ticker": "BF4", "composite": 20, "band": None},
        {"date": "2026-07-08", "ticker": "BF4", "composite": 60, "band": "fair"},
    ])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_uses_two_most_recent_distinct_dates_only(tmp_path):
    _append_quant_history(tmp_path, [
        {"date": "2026-06-01", "ticker": "BF5", "composite": 80, "band": "strong"},
        {"date": "2026-07-01", "ticker": "BF5", "composite": 55, "band": "fair"},
        {"date": "2026-07-08", "ticker": "BF5", "composite": 58, "band": "fair"},
    ])
    result = check.run_checks(tmp_path)
    # the two most recent dates are both "fair" -- the older "strong" row
    # (a 3rd distinct date back) must not be considered.
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_missing_ledger_is_safe(tmp_path):
    result = check.run_checks(tmp_path)  # no quant_history.jsonl at all
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_malformed_lines_skipped(tmp_path):
    p = tmp_path / "quant_history.jsonl"
    p.write_text("not json\n" + json.dumps({"ticker": "X"}) + "\n", encoding="utf-8")
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_same_date_rerun_does_not_count_as_two_dates(tmp_path):
    # Writer contract: a same-date rerun replaces that date's row. Two rows
    # sharing a date must collapse to one distinct date, not fire a flip.
    _append_quant_history(tmp_path, [
        {"date": "2026-07-01", "ticker": "BF6", "composite": 55, "band": "fair"},
        {"date": "2026-07-01", "ticker": "BF6", "composite": 82, "band": "strong"},
    ])
    result = check.run_checks(tmp_path)
    assert [a for a in result["alerts"] if a["kind"] == "band_flip"] == []


def test_band_flip_evaluates_tickers_independently(tmp_path):
    _append_quant_history(tmp_path, [
        {"date": "2026-07-01", "ticker": "M1", "composite": 55, "band": "fair"},
        {"date": "2026-07-08", "ticker": "M1", "composite": 75, "band": "strong"},
        {"date": "2026-07-01", "ticker": "M2", "composite": 55, "band": "fair"},
        {"date": "2026-07-08", "ticker": "M2", "composite": 58, "band": "fair"},
    ])
    result = check.run_checks(tmp_path)
    flipped_tickers = {a["ticker"] for a in result["alerts"] if a["kind"] == "band_flip"}
    assert flipped_tickers == {"M1"}
