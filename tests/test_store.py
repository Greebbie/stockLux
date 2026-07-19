import json

import pytest

from luxtock import store


def test_load_watchlist_missing_returns_empty(tmp_path):
    assert store.load_watchlist(tmp_path) == {"stocks": []}


def test_ensure_dirs_creates_all_subdirs(tmp_path):
    store.ensure_dirs(tmp_path)
    for sub in ("theses", "analyses", "retrospects"):
        assert (tmp_path / sub).is_dir()


def test_add_stock_returns_new_object():
    wl = {"stocks": []}
    out = store.add_stock(wl, ticker="ON", thesis="ev-adoption", layer="power-semis")
    assert wl == {"stocks": []}  # original object not mutated
    assert out["stocks"][0]["ticker"] == "ON"
    assert out["stocks"][0]["thesis"] == "ev-adoption"
    assert out["stocks"][0]["added"]  # has a date


def test_add_stock_rejects_bad_ticker():
    with pytest.raises(ValueError):
        store.add_stock({"stocks": []}, ticker="mu!", thesis="x")


def test_add_stock_without_thesis_omits_key():
    # thesis is optional: omitted entirely → no "thesis" key on the entry
    out = store.add_stock({"stocks": []}, ticker="ON", layer="power-semis")
    assert "thesis" not in out["stocks"][0]
    # explicit empty string behaves the same as omitted
    out2 = store.add_stock({"stocks": []}, ticker="MU", thesis="")
    assert "thesis" not in out2["stocks"][0]


def test_watchlist_mutators_preserve_top_level_keys():
    # cash_usd (and any future top-level key) must survive every mutator
    wl = {"stocks": [], "cash_usd": 7100.0}
    wl = store.add_stock(wl, ticker="MU", thesis="x")
    assert wl["cash_usd"] == 7100.0
    wl = store.set_holding(wl, "MU", True)
    assert wl["cash_usd"] == 7100.0
    wl = store.set_shares(wl, "MU", 5)
    assert wl["cash_usd"] == 7100.0
    wl = store.remove_stock(wl, "MU")
    assert wl["cash_usd"] == 7100.0


def test_set_shares_returns_new_object():
    wl = {"stocks": [{"ticker": "MU", "holding": True}]}
    out = store.set_shares(wl, "MU", 5)
    assert wl["stocks"][0].get("shares") is None  # original not mutated
    assert out["stocks"][0]["shares"] == 5.0


def test_set_shares_zero_removes_field():
    wl = {"stocks": [{"ticker": "MU", "holding": True, "shares": 5.0}]}
    out = store.set_shares(wl, "MU", 0)
    assert "shares" not in out["stocks"][0]


def test_set_shares_rejects_negative_and_unknown():
    wl = {"stocks": [{"ticker": "MU", "holding": True}]}
    with pytest.raises(ValueError):
        store.set_shares(wl, "MU", -1)
    with pytest.raises(ValueError):
        store.set_shares(wl, "ZZZ", 1)


def test_set_cash_sets_and_clears():
    wl = {"stocks": []}
    out = store.set_cash(wl, 7100.0)
    assert out["cash_usd"] == 7100.0
    assert "cash_usd" not in wl  # original not mutated
    cleared = store.set_cash(out, None)
    assert "cash_usd" not in cleared
    with pytest.raises(ValueError):
        store.set_cash(wl, -5)


def test_add_stock_rejects_duplicate():
    wl = store.add_stock({"stocks": []}, ticker="ON", thesis="x")
    with pytest.raises(ValueError):
        store.add_stock(wl, ticker="ON", thesis="x")


def test_remove_stock():
    wl = store.add_stock({"stocks": []}, ticker="ON", thesis="x")
    assert store.remove_stock(wl, "ON") == {"stocks": []}


def test_add_stock_holding_defaults_false():
    wl = store.add_stock({"stocks": []}, ticker="ON", thesis="x")
    assert wl["stocks"][0]["holding"] is False


def test_add_stock_holding_true():
    wl = store.add_stock({"stocks": []}, ticker="ON", thesis="x", holding=True)
    assert wl["stocks"][0]["holding"] is True


def test_set_holding_returns_new_object():
    wl = store.add_stock({"stocks": []}, ticker="ON", thesis="x")
    out = store.set_holding(wl, "ON", True)
    assert wl["stocks"][0]["holding"] is False  # original not mutated
    assert out["stocks"][0]["holding"] is True
    back = store.set_holding(out, "ON", False)
    assert back["stocks"][0]["holding"] is False


def test_set_holding_unknown_ticker_raises():
    with pytest.raises(ValueError):
        store.set_holding({"stocks": []}, "ZZZ", True)


def test_save_and_load_roundtrip(tmp_path):
    wl = store.add_stock({"stocks": []}, ticker="ON", thesis="x", note="中文备注 utf-8 test")
    store.save_watchlist(tmp_path, wl)
    assert store.load_watchlist(tmp_path) == wl
    # file must be UTF-8 without ASCII escaping
    raw = (tmp_path / "watchlist.json").read_text(encoding="utf-8")
    assert "中文备注 utf-8 test" in raw


VALID_MEMO = """---
ticker: "ON"
date: 2026-07-04
thesis: ev-adoption
layer: memory
action: enter
confidence: medium
buy_range: [38, 55]
multiple_basis: "8-12x cyclical EPS"
price_at_analysis: 1032
verdict: below_range
thesis_health: intact
top_risks: [ev-demand-stall, oversupply]
review_trigger: "EV unit sales turn negative YoY"
signals:
  chain: favorable
  narrative: favorable
  fundamentals: favorable
  valuation: favorable
  flows: neutral
  sentiment: unfavorable
  competition: neutral
  macro: no_signal
---
# ON full analysis

Body content.
"""


def test_parse_frontmatter():
    meta, body = store.parse_frontmatter(VALID_MEMO)
    assert meta["ticker"] == "ON"
    assert meta["buy_range"] == [38, 55]
    assert body.startswith("# ON full analysis")


def test_parse_no_frontmatter():
    meta, body = store.parse_frontmatter("just text")
    assert meta == {} and body == "just text"


def test_validate_memo_ok():
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    assert store.validate_memo(meta) == []


def test_validate_memo_thesis_absent_is_legal():
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    del meta["thesis"]
    assert store.validate_memo(meta) == []


def test_validate_memo_thesis_present_must_be_non_empty_string():
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    errors = store.validate_memo({**meta, "thesis": ""})
    assert any("thesis" in e for e in errors)
    errors2 = store.validate_memo({**meta, "thesis": 123})
    assert any("thesis" in e for e in errors2)


def test_validate_memo_catches_errors():
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    bad = {**meta, "action": "buy_buy_buy", "verdict": "cheap",
           "signals": {**meta["signals"], "chain": "great", "unknown_dim": "favorable"}}
    del bad["ticker"]
    errors = store.validate_memo(bad)
    joined = "\n".join(errors)
    assert "ticker" in joined
    assert "action" in joined
    assert "verdict" in joined
    assert "chain" in joined
    assert "unknown_dim" in joined


def test_latest_memo_picks_newest(tmp_path):
    d = tmp_path / "analyses" / "ON"
    d.mkdir(parents=True)
    (d / "2026-06-01.md").write_text(VALID_MEMO.replace("2026-07-04", "2026-06-01"),
                                     encoding="utf-8")
    (d / "2026-07-04.md").write_text(VALID_MEMO, encoding="utf-8")
    memo = store.latest_memo(tmp_path, "ON")
    assert str(memo["meta"]["date"]) == "2026-07-04"
    assert memo["errors"] == []


def test_latest_memo_none_when_missing(tmp_path):
    assert store.latest_memo(tmp_path, "ZZZ") is None


def test_list_theses(tmp_path):
    d = tmp_path / "theses"
    d.mkdir()
    (d / "ev-adoption.md").write_text(
        "---\nid: ev-adoption\nname: EV adoption\nstatus: intact\ncreated: 2026-07-01\n---\nthesis body",
        encoding="utf-8")
    out = store.list_theses(tmp_path)
    assert out[0]["id"] == "ev-adoption"
    assert out[0]["meta"]["status"] == "intact"
    assert out[0]["body"] == "thesis body"


def test_validate_memo_price_targets_ok():
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    good = {**meta, "price_targets": {"bear": 30, "base": 48, "bull": 66,
                                       "horizon": "12mo"}}
    assert store.validate_memo(good) == []


def test_validate_memo_price_targets_bad_shape():
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    assert store.validate_memo({**meta, "price_targets": "1650"})
    errors = store.validate_memo({**meta, "price_targets": {"bear": "low", "base": 1650,
                                                             "bull": 2400}})
    assert any("bear" in e for e in errors)


def test_validate_memo_holding_gates_actions():
    meta, _ = store.parse_frontmatter(VALID_MEMO)  # action: enter
    # enter is legal only when not holding
    assert store.validate_memo(meta, holding=False) == []
    assert any("holding" in e for e in store.validate_memo(meta, holding=True))
    # hold is legal only when holding
    held = {**meta, "action": "hold"}
    assert store.validate_memo(held, holding=True) == []
    assert any("holding" in e for e in store.validate_memo(held, holding=False))
    # watch_only is legal either way; holding unknown (None) checks nothing
    either = {**meta, "action": "watch_only"}
    assert store.validate_memo(either, holding=True) == []
    assert store.validate_memo(either, holding=False) == []
    assert store.validate_memo(meta) == []  # no holding context → no gate


def _v2_meta(**over):
    """A memo dated on/after POLICY_V2_DATE, fully compliant with the
    2026-07-05 contract (probabilities, entry plan, RR 2.2 >= 2)."""
    meta, _ = store.parse_frontmatter(VALID_MEMO)
    v2 = {**meta, "date": "2026-07-05", "price_at_analysis": 975, "mode": "full",
          "price_targets": {"bear": 600, "base": 1800, "bull": 2400,
                            "p_bear": 0.2, "p_base": 0.55, "p_bull": 0.25,
                            "horizon": "12mo"},
          "entry_plan": {"tranches": [975, 900], "invalidation": 700}}
    return {**v2, **over}


def test_v2_compliant_memo_passes():
    assert store.validate_memo(_v2_meta()) == []


def test_v2_requires_tier_probabilities():
    pt = {"bear": 600, "base": 1800, "bull": 2400, "horizon": "12mo"}
    errors = store.validate_memo(_v2_meta(price_targets=pt))
    assert any("p_bear" in e for e in errors)


def test_v2_probabilities_must_sum_to_one():
    meta = _v2_meta()
    pt = {**meta["price_targets"], "p_base": 0.4}  # sum 0.85
    errors = store.validate_memo({**meta, "price_targets": pt})
    assert any("sum to 1.0" in e for e in errors)


def test_v2_enter_requires_entry_plan():
    errors = store.validate_memo(_v2_meta(entry_plan=None))
    assert any("entry_plan" in e for e in errors)
    # but a non-entry verdict doesn't need one
    ok = store.validate_memo(_v2_meta(action="watch_only", entry_plan=None))
    assert ok == []


def test_entry_plan_shape_validated_whenever_present():
    errors = store.validate_memo(
        _v2_meta(entry_plan={"tranches": [1, 2, 3, 4], "invalidation": "low"}))
    joined = "\n".join(errors)
    assert "tranches" in joined
    assert "invalidation" in joined


def test_v2_risk_reward_gate_blocks_thin_enter():
    meta = _v2_meta()
    pt = {**meta["price_targets"], "base": 1200}  # RR (1200-975)/(975-600) = 0.6
    errors = store.validate_memo({**meta, "price_targets": pt})
    assert any("risk/reward" in e for e in errors)
    # the same targets under watch_only are fine — the gate is enter-only
    ok = store.validate_memo(
        {**meta, "price_targets": pt, "action": "watch_only", "entry_plan": None})
    assert ok == []


def test_pre_policy_memo_is_grandfathered():
    """Dated before 2026-07-05: no probabilities, no entry plan, no RR gate."""
    meta, _ = store.parse_frontmatter(VALID_MEMO)  # date 2026-07-04, action enter
    old = {**meta, "price_targets": {"bear": 900, "base": 1000, "bull": 1100,
                                     "horizon": "12mo"}}
    assert store.validate_memo(old) == []


def test_v2_requires_mode():
    meta = _v2_meta()
    del meta["mode"]
    assert any("mode" in e for e in store.validate_memo(meta))
    assert any("mode" in e for e in store.validate_memo(_v2_meta(mode="lazy")))


def test_v2_requires_all_eight_signals():
    meta = _v2_meta()
    partial = dict(meta["signals"])
    del partial["competition"], partial["macro"]
    errors = store.validate_memo({**meta, "signals": partial})
    assert any("competition" in e and "macro" in e for e in errors)
    # pre-policy memos are not retro-flagged
    old, _ = store.parse_frontmatter(VALID_MEMO)
    old_partial = {**old, "signals": partial}
    assert not any("eight" in e for e in store.validate_memo(old_partial))


def test_add_stock_benchmark():
    wl = store.add_stock({"stocks": []}, ticker="MU", thesis="x", benchmark="SMH")
    assert wl["stocks"][0]["benchmark"] == "SMH"
    # omitted → field absent (defaults to SPY at fetch time)
    wl2 = store.add_stock({"stocks": []}, ticker="CEG", thesis="x")
    assert "benchmark" not in wl2["stocks"][0]
    with pytest.raises(ValueError):
        store.add_stock({"stocks": []}, ticker="ON", thesis="x", benchmark="bad!")


def test_validate_memo_rejects_boolean_ticker():
    """YAML parses bare ON/NO/YES as booleans — must be caught, not silently accepted."""
    meta, _ = store.parse_frontmatter(VALID_MEMO.replace('ticker: "ON"', "ticker: ON"))
    assert meta["ticker"] is True
    assert any("ticker" in e for e in store.validate_memo(meta))


# ---------------------------------------------------------------------------
# set_paired
# ---------------------------------------------------------------------------


def test_set_paired_sets_new_object():
    wl = store.add_stock({"stocks": []}, ticker="SKHYV", thesis="x")
    paired = {"ticker": "000660.KS", "ratio": 0.1, "currency": "KRW"}
    out = store.set_paired(wl, "SKHYV", paired)
    assert "paired" not in wl["stocks"][0]  # original not mutated
    assert out["stocks"][0]["paired"] == paired
    assert out["stocks"][0]["paired"] is not paired  # defensive copy, not the same object


def test_set_paired_defaults_currency_to_usd():
    wl = store.add_stock({"stocks": []}, ticker="MU", thesis="x")
    out = store.set_paired(wl, "MU", {"ticker": "MAERSK.CO", "ratio": 2.0})
    assert out["stocks"][0]["paired"]["currency"] == "USD"


def test_set_paired_replaces_existing():
    wl = store.add_stock({"stocks": []}, ticker="SKHYV", thesis="x")
    wl = store.set_paired(wl, "SKHYV", {"ticker": "000660.KS", "ratio": 0.1, "currency": "KRW"})
    out = store.set_paired(wl, "SKHYV", {"ticker": "000660.KS", "ratio": 0.2, "currency": "KRW"})
    assert out["stocks"][0]["paired"]["ratio"] == 0.2


def test_set_paired_none_removes_field():
    wl = store.add_stock({"stocks": []}, ticker="SKHYV", thesis="x")
    wl = store.set_paired(wl, "SKHYV", {"ticker": "000660.KS", "ratio": 0.1, "currency": "KRW"})
    out = store.set_paired(wl, "SKHYV", None)
    assert "paired" not in out["stocks"][0]


def test_set_paired_validates_ratio_positive():
    wl = store.add_stock({"stocks": []}, ticker="SKHYV", thesis="x")
    with pytest.raises(ValueError):
        store.set_paired(wl, "SKHYV", {"ticker": "000660.KS", "ratio": 0})
    with pytest.raises(ValueError):
        store.set_paired(wl, "SKHYV", {"ticker": "000660.KS", "ratio": -0.1})


def test_set_paired_validates_ticker_non_empty():
    wl = store.add_stock({"stocks": []}, ticker="SKHYV", thesis="x")
    with pytest.raises(ValueError):
        store.set_paired(wl, "SKHYV", {"ticker": "", "ratio": 0.1})
    with pytest.raises(ValueError):
        store.set_paired(wl, "SKHYV", {"ratio": 0.1})


def test_set_paired_unknown_ticker_raises():
    with pytest.raises(ValueError):
        store.set_paired({"stocks": []}, "ZZZ", {"ticker": "000660.KS", "ratio": 0.1})


def test_set_paired_preserves_top_level_keys():
    wl = {"stocks": [], "cash_usd": 7100.0}
    wl = store.add_stock(wl, ticker="SKHYV", thesis="x")
    wl = store.set_paired(wl, "SKHYV", {"ticker": "000660.KS", "ratio": 0.1, "currency": "KRW"})
    assert wl["cash_usd"] == 7100.0
    wl = store.set_paired(wl, "SKHYV", None)
    assert wl["cash_usd"] == 7100.0


# ---------------------------------------------------------------------------
# Atomic writes (temp file + os.replace — atomic on POSIX and Windows)
# ---------------------------------------------------------------------------

def test_write_text_atomic_writes_utf8_and_leaves_no_temp_droppings(tmp_path):
    p = tmp_path / "out.txt"
    store.write_text_atomic(p, "héllo — snapshot ✓")
    assert p.read_text(encoding="utf-8") == "héllo — snapshot ✓"
    assert [f.name for f in tmp_path.iterdir()] == ["out.txt"]


def test_write_text_atomic_overwrites_existing_file(tmp_path):
    p = tmp_path / "out.txt"
    p.write_text("old", encoding="utf-8")
    store.write_text_atomic(p, "new")
    assert p.read_text(encoding="utf-8") == "new"
    assert [f.name for f in tmp_path.iterdir()] == ["out.txt"]


def test_write_json_atomic_round_trips(tmp_path):
    p = tmp_path / "out.json"
    data = {"a": 1, "nested": {"é": [1, 2]}, "s": "✓"}
    store.write_json_atomic(p, data)
    assert json.loads(p.read_text(encoding="utf-8")) == data
    assert [f.name for f in tmp_path.iterdir()] == ["out.json"]


def test_write_text_atomic_keeps_old_content_when_replace_fails(tmp_path, monkeypatch):
    p = tmp_path / "out.txt"
    p.write_text("old", encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(OSError):
        store.write_text_atomic(p, "new")
    assert p.read_text(encoding="utf-8") == "old"  # target never truncated
    assert [f.name for f in tmp_path.iterdir()] == ["out.txt"]  # temp cleaned up
