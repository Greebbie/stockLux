import json

from fastapi.testclient import TestClient

from stocklux import store
from stocklux.server import build_overview, create_app


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
    assert "StockLux" in res.text


def test_static_assets_served(data_dir):
    c = client(data_dir)
    assert c.get("/app.js").status_code == 200
    assert c.get("/style.css").status_code == 200


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


def test_medium_confidence_on_unaudited_thesis_not_flagged(data_dir):
    # the fixture memo is confidence: medium on a never-audited thesis — the
    # cap is already respected, so no warning
    ov = build_overview(data_dir)
    on = next(r for r in ov["rows"] if r["ticker"] == "ON")
    assert not any("audit" in e for e in on["memo_errors"])


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
