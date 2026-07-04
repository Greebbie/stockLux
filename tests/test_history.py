import json

from stocklux import history


def make_payloads(day="2026-07-05", price=975.56, stale=False):
    quotes = {"fetched_at": f"{day}T16:00:00+00:00", "quotes": {
        "MU": {"price": price, "fwd_eps": 149.6, "stale": stale,
               "revisions": {"fwd_eps_change_90d_pct": 52.5,
                             "up_last_30d": 29, "down_last_30d": 0},
               "analyst": {"pt_mean": 1500.0}},
    }}
    flows = {"flows": {
        "MU": {"short_pct_float": 0.0369, "put_call_oi_ratio": 4.26,
               "trend": {"rsi_14": 49.0, "dist_50dma_pct": 14.5,
                         "rel_strength_3m": 151.2}},
    }}
    return quotes, flows


def test_snapshot_rows_flatten_quote_and_flow():
    rows = history.snapshot_rows(*make_payloads())
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-07-05"
    assert r["ticker"] == "MU"
    assert r["price"] == 975.56
    assert r["short_pct_float"] == 0.0369
    assert r["rsi_14"] == 49.0
    assert r["fwd_eps_change_90d_pct"] == 52.5
    assert r["pt_mean"] == 1500.0


def test_stale_tickers_never_recorded():
    assert history.snapshot_rows(*make_payloads(stale=True)) == []


def test_append_dedupes_same_day(tmp_path):
    quotes, flows = make_payloads()
    assert history.append_history(tmp_path, quotes, flows) == 1
    assert history.append_history(tmp_path, quotes, flows) == 0  # same (date, ticker)
    lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_append_accumulates_across_days(tmp_path):
    history.append_history(tmp_path, *make_payloads(day="2026-07-05"))
    history.append_history(tmp_path, *make_payloads(day="2026-07-06", price=1001.0))
    lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["date"] for r in rows] == ["2026-07-05", "2026-07-06"]
    assert rows[1]["price"] == 1001.0


def test_corrupt_line_does_not_break_append(tmp_path):
    (tmp_path / "history.jsonl").write_text("not json\n", encoding="utf-8")
    assert history.append_history(tmp_path, *make_payloads()) == 1
