import json
import threading

from fastapi.testclient import TestClient

from luxtock import refresh as refresh_mod
from luxtock import store
from luxtock.server import build_overview, create_app


def client(data_dir):
    return TestClient(create_app(data_dir))


def test_overview_joins_quote_memo_staleness(data_dir):
    ov = build_overview(data_dir)
    mu = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert mu["quote"]["price"] == 1032.0
    assert mu["memo"]["action"] == "enter"
    assert mu["memo"]["buy_range"] == [38, 55]
    # (1032/900 - 1) * 100 = 14.7
    assert mu["staleness"]["price_deviation_pct"] == 14.7
    # analysis date 2026-06-01: days only grows, so >30 holds forever
    assert mu["staleness"]["needs_reanalysis"] is True
    assert ov["quotes_fetched_at"] == "2026-07-04T00:00:00+00:00"


def test_overview_flags_broken_memo_without_crashing(data_dir):
    ov = build_overview(data_dir)
    ceg = next(r for r in ov["rows"] if r["ticker"] == "CHPT")
    assert ceg["memo_errors"]  # validation errors present
    assert ceg["memo"]["action"] == "yolo"  # passed through as-is; UI shows a warning


def test_overview_flags_holding_action_mismatch(data_dir):
    # memo says `enter`, but the user actually holds the name → format warning
    wl = json.loads((data_dir / "watchlist.json").read_text(encoding="utf-8"))
    wl["stocks"][0]["holding"] = True  # ON
    (data_dir / "watchlist.json").write_text(json.dumps(wl), encoding="utf-8")
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert any("holding" in e for e in on["memo_errors"])


def test_overview_renders_row_without_thesis(data_dir):
    # a watchlist entry with no `thesis` key must not crash build_overview
    # and the row must simply omit the field
    wl = json.loads((data_dir / "watchlist.json").read_text(encoding="utf-8"))
    del wl["stocks"][1]["thesis"]  # CHPT
    (data_dir / "watchlist.json").write_text(json.dumps(wl), encoding="utf-8")
    ov = build_overview(data_dir)
    chpt = next(r for r in ov["rows"] if r["ticker"] == "CHPT")
    assert "thesis" not in chpt
    res = client(data_dir).get("/api/overview")
    assert res.status_code == 200


def test_get_overview_endpoint(data_dir):
    res = client(data_dir).get("/api/overview")
    assert res.status_code == 200
    assert len(res.json()["rows"]) == 2


def test_overview_survives_datetime_date_in_memo(data_dir):
    memo = (data_dir / "analyses" / "ON" / "2026-06-01.md").read_text(encoding="utf-8")
    (data_dir / "analyses" / "ON" / "2026-06-02.md").write_text(
        memo.replace("date: 2026-06-01", "date: 2026-06-02T10:00:00"), encoding="utf-8")
    res = client(data_dir).get("/api/overview")
    assert res.status_code == 200
    mu = next(r for r in res.json()["rows"] if r["ticker"] == "ON")
    assert mu["staleness"]["days_since_analysis"] is not None


def test_get_stock_detail(data_dir):
    res = client(data_dir).get("/api/stocks/ON")
    body = res.json()
    assert body["quote"]["price"] == 1032.0
    assert body["flows"]["signals"]["accumulation_hint"] is True
    assert body["memos"][0]["body"].startswith("# ON analysis body")


def test_get_stock_detail_rejects_path_traversal_ticker(data_dir):
    res = client(data_dir).get("/api/stocks/..%5C..")
    assert res.status_code == 422


def test_overview_survives_malformed_price_at_analysis(data_dir):
    memo = (data_dir / "analyses" / "ON" / "2026-06-01.md").read_text(encoding="utf-8")
    (data_dir / "analyses" / "ON" / "2026-06-03.md").write_text(
        memo.replace("price_at_analysis: 900", 'price_at_analysis: "$1,032"'),
        encoding="utf-8")
    res = client(data_dir).get("/api/overview")
    assert res.status_code == 200
    mu = next(r for r in res.json()["rows"] if r["ticker"] == "ON")
    assert mu["staleness"]["price_deviation_pct"] is None


def test_add_stock_endpoint_validates_thesis(data_dir):
    c = client(data_dir)
    res = c.post("/api/watchlist", json={"ticker": "WOLF", "thesis": "nope"})
    assert res.status_code == 422
    res = c.post("/api/watchlist", json={"ticker": "WOLF", "thesis": "ev-adoption",
                                          "layer": "power-semis"})
    assert res.status_code == 200
    assert any(s["ticker"] == "WOLF" for s in store.load_watchlist(data_dir)["stocks"])


def test_add_stock_endpoint_without_thesis_succeeds(data_dir):
    c = client(data_dir)
    # thesis omitted entirely from the body
    res = c.post("/api/watchlist", json={"ticker": "WOLF", "layer": "power-semis"})
    assert res.status_code == 200
    entry = next(s for s in store.load_watchlist(data_dir)["stocks"] if s["ticker"] == "WOLF")
    assert "thesis" not in entry
    # explicit empty string is also accepted (no validation against data/theses/)
    res2 = c.post("/api/watchlist", json={"ticker": "SMCI", "thesis": ""})
    assert res2.status_code == 200
    entry2 = next(s for s in store.load_watchlist(data_dir)["stocks"] if s["ticker"] == "SMCI")
    assert "thesis" not in entry2


def test_add_stock_endpoint_rejects_bad_ticker(data_dir):
    res = client(data_dir).post("/api/watchlist",
                                 json={"ticker": "bad!!", "thesis": "ev-adoption"})
    assert res.status_code == 422


def test_delete_stock(data_dir):
    c = client(data_dir)
    assert c.delete("/api/watchlist/CHPT").status_code == 200
    assert all(s["ticker"] != "CHPT" for s in store.load_watchlist(data_dir)["stocks"])


def test_put_thesis_writes_file(data_dir):
    c = client(data_dir)
    content = "---\nid: dc-power\nname: Power\nstatus: intact\ncreated: 2026-07-04\n---\nnew thesis body"
    assert c.put("/api/theses/dc-power", json={"content": content}).status_code == 200
    assert (data_dir / "theses" / "dc-power.md").read_text(encoding="utf-8") == content
    assert c.put("/api/theses/../evil", json={"content": "x"}).status_code in (404, 405, 422)
    assert not (data_dir / "theses" / "evil.md").exists()
    assert not (data_dir.parent / "evil.md").exists()


def test_status_returns_data_version(data_dir):
    res = client(data_dir).get("/api/status")
    assert res.json()["data_version"] > 0


def test_static_dashboard_served(data_dir):
    res = client(data_dir).get("/")
    assert res.status_code == 200
    assert "Luxtock" in res.text


def test_static_assets_served(data_dir):
    c = client(data_dir)
    assert c.get("/app.js").status_code == 200
    assert c.get("/style.css").status_code == 200


def test_all_responses_carry_no_cache_header(data_dir):
    # without Cache-Control the browser heuristically caches the SPA and
    # users see a stale dashboard after upgrades; no-cache forces
    # revalidation (304 when unchanged — cheap, always current)
    c = client(data_dir)
    for path in ("/", "/app.js", "/style.css", "/api/overview"):
        assert c.get(path).headers.get("cache-control") == "no-cache", path


def _write_high_confidence_memo(data_dir, memo_date="2026-06-20"):
    memo = (data_dir / "analyses" / "ON" / "2026-06-01.md").read_text(encoding="utf-8")
    memo = memo.replace("confidence: medium", "confidence: high")
    memo = memo.replace("date: 2026-06-01", f"date: {memo_date}")
    (data_dir / "analyses" / "ON" / f"{memo_date}.md").write_text(memo, encoding="utf-8")


def _set_thesis_audited(data_dir, last_audited):
    p = data_dir / "theses" / "ev-adoption.md"
    text = p.read_text(encoding="utf-8").replace(
        "created: 2026-07-01", f"created: 2026-07-01\nlast_audited: {last_audited}")
    p.write_text(text, encoding="utf-8")


def test_high_confidence_on_unaudited_thesis_flagged(data_dir):
    _write_high_confidence_memo(data_dir)  # ev-adoption has no last_audited
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert any("never-audited" in e for e in on["memo_errors"])


def test_high_confidence_on_fresh_audit_passes(data_dir):
    _set_thesis_audited(data_dir, "2026-06-01")  # 19 days before the memo
    _write_high_confidence_memo(data_dir)
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert not any("audit" in e for e in on["memo_errors"])


def test_high_confidence_on_stale_audit_flagged(data_dir):
    _set_thesis_audited(data_dir, "2026-01-01")  # 171 days before the memo
    _write_high_confidence_memo(data_dir)
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert any("audit is" in e and "days older" in e for e in on["memo_errors"])


def test_high_confidence_thesis_independent_scenario_not_flagged(data_dir):
    # a memo that declares scenario_thesis_independent: true derives every
    # scenario input from market data, so the unaudited-thesis cap does not
    # bind even at high confidence (methodology: "Scenario independence rule")
    _write_high_confidence_memo(data_dir)
    p = data_dir / "analyses" / "ON" / "2026-06-20.md"
    memo = p.read_text(encoding="utf-8").replace(
        "confidence: high", "confidence: high\nscenario_thesis_independent: true")
    p.write_text(memo, encoding="utf-8")
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert not any("audit" in e for e in on["memo_errors"])


def test_medium_confidence_on_unaudited_thesis_not_flagged(data_dir):
    # the fixture memo is confidence: medium on a never-audited thesis — the
    # cap is already respected, so no warning
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert not any("audit" in e for e in on["memo_errors"])


def test_high_confidence_memo_without_thesis_key_not_flagged(data_dir):
    # a memo with no `thesis` key has nothing to audit against — the
    # freshness cap must not fire even at confidence: high
    _write_high_confidence_memo(data_dir)
    p = data_dir / "analyses" / "ON" / "2026-06-20.md"
    memo = p.read_text(encoding="utf-8")
    lines = [ln for ln in memo.splitlines() if not ln.startswith("thesis:")]
    p.write_text("\n".join(lines), encoding="utf-8")
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert on["memo"]["confidence"] == "high"
    assert not any("audit" in e for e in on["memo_errors"])
    assert not any("thesis" in e for e in on["memo_errors"])


def _write_memo_with_mode(data_dir, memo_date, mode):
    memo = (data_dir / "analyses" / "ON" / "2026-06-01.md").read_text(encoding="utf-8")
    memo = memo.replace("date: 2026-06-01", f"date: {memo_date}")
    memo = memo.replace("confidence: medium", f"confidence: medium\nmode: {mode}")
    (data_dir / "analyses" / "ON" / f"{memo_date}.md").write_text(memo, encoding="utf-8")


def test_three_consecutive_incrementals_flagged(data_dir):
    for i, mode in enumerate(["incremental", "incremental", "incremental"]):
        _write_memo_with_mode(data_dir, f"2026-06-1{i + 1}", mode)
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert any("full rewrite" in e for e in on["memo_errors"])


def test_two_incrementals_after_full_not_flagged(data_dir):
    for memo_date, mode in [("2026-06-11", "full"), ("2026-06-12", "incremental"),
                            ("2026-06-13", "incremental")]:
        _write_memo_with_mode(data_dir, memo_date, mode)
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert not any("full rewrite" in e for e in on["memo_errors"])


def test_overview_includes_price_targets(data_dir):
    memo = (data_dir / "analyses" / "ON" / "2026-06-01.md").read_text(encoding="utf-8")
    memo = memo.replace("buy_range: [38, 55]",
                        "buy_range: [38, 55]\nprice_targets: {bear: 30, base: 48, bull: 66, horizon: 12mo}")
    (data_dir / "analyses" / "ON" / "2026-07-01.md").write_text(memo, encoding="utf-8")
    res = client(data_dir).get("/api/overview")
    mu = next(r for r in res.json()["rows"] if r["ticker"] == "ON")
    assert mu["memo"]["price_targets"]["base"] == 48


def test_get_quant_endpoint_missing_file_returns_empty_shape(data_dir):
    # fixture data_dir has no quant.json — endpoint must not 500
    res = client(data_dir).get("/api/quant")
    assert res.status_code == 200
    assert res.json() == {"computed_at": None, "tickers": {}}


def test_get_quant_endpoint_returns_file_contents(data_dir):
    quant = {
        "computed_at": "2026-07-10T18:52:07.837756+00:00",
        "tickers": {
            "ON": {
                "features": {"price": 1032.0},
                "scores": {"composite": 85.3, "band": "strong", "coverage": 0.86},
            }
        },
    }
    (data_dir / "quant.json").write_text(json.dumps(quant), encoding="utf-8")
    res = client(data_dir).get("/api/quant")
    assert res.status_code == 200
    assert res.json() == quant


def test_get_screen_endpoint_missing_file_returns_empty_shape(data_dir):
    # fixture data_dir has no screen.json — endpoint must not 500
    res = client(data_dir).get("/api/screen")
    assert res.status_code == 200
    assert res.json() == {"computed_at": None, "results": []}


def test_get_screen_endpoint_returns_file_contents(data_dir):
    screen = {
        "computed_at": "2026-07-10T18:52:07.837756+00:00",
        "universe_as_of": "2026-07-01",
        "universe_size": 500,
        "stage_a_survivors": 42,
        "stage_b_cap_dropped": 0,
        "results": [{"ticker": "AAA", "track": "beaten_down", "disqualified": False,
                     "depression_score": 61.0, "band": "fair"}],
    }
    (data_dir / "screen.json").write_text(json.dumps(screen), encoding="utf-8")
    res = client(data_dir).get("/api/screen")
    assert res.status_code == 200
    body = res.json()
    assert body["computed_at"] == screen["computed_at"]
    assert body["results"][0]["ticker"] == "AAA"


def test_get_portfolio_endpoint_empty_when_no_holdings(data_dir):
    # fixture watchlist has no `holding: true` entries and no shares/cash_usd
    res = client(data_dir).get("/api/portfolio")
    assert res.status_code == 200
    body = res.json()
    assert body["positions"] == []
    assert body["cash_usd"] == 0.0
    assert body["total_value"] == 0.0
    assert body["groups"] == {"by_layer": {}, "by_thesis": {}}
    assert body["flags"] == []
    assert body["bear_stress"]["covered_tickers"] == []


def test_refresh_endpoint_starts_background_refresh(data_dir, monkeypatch):
    calls, done = [], threading.Event()

    def fake_refresh(d):
        calls.append(d)
        done.set()

    monkeypatch.setattr("luxtock.server.try_refresh_data", fake_refresh)
    res = client(data_dir).post("/api/refresh")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "message": "background refresh started"}
    assert done.wait(timeout=5)
    assert calls == [data_dir]


def test_refresh_endpoint_skips_when_refresh_in_progress(data_dir):
    # simulate a refresh mid-flight by holding the shared module-level lock
    assert refresh_mod._REFRESH_LOCK.acquire(blocking=False)
    try:
        res = client(data_dir).post("/api/refresh")
        assert res.status_code == 200
        assert res.json() == {"ok": False, "message": "refresh already in progress"}
    finally:
        refresh_mod._REFRESH_LOCK.release()


def test_get_portfolio_endpoint_with_sized_holdings(data_dir):
    wl = json.loads((data_dir / "watchlist.json").read_text(encoding="utf-8"))
    wl["cash_usd"] = 500.0
    wl["stocks"][0]["holding"] = True  # ON
    wl["stocks"][0]["shares"] = 10.0
    (data_dir / "watchlist.json").write_text(json.dumps(wl), encoding="utf-8")
    res = client(data_dir).get("/api/portfolio")
    assert res.status_code == 200
    body = res.json()
    on = next(p for p in body["positions"] if p["ticker"] == "ON")
    assert on["shares"] == 10.0
    assert on["value"] == 10320.0
    assert body["cash_usd"] == 500.0
    assert body["total_value"] == 10820.0
    assert body["groups"]["by_layer"]["power-semis"] > 0
    assert "bear_stress" in body
