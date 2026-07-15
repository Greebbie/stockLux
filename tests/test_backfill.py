import json
from datetime import date

from luxtock import store
from luxtock.backfill import backfill_history, watch_symbols


def _fake_downloader(rows_by_symbol):
    def fetch(symbols, start):
        return {s: rows_by_symbol.get(s, []) for s in symbols}
    return fetch


def test_watch_symbols_includes_default_benchmark(data_dir):
    wl = store.load_watchlist(data_dir)
    syms = watch_symbols(wl)
    assert "ON" in syms
    assert "CHPT" in syms
    assert "SPY" in syms  # default benchmark when a stock has none


def test_watch_symbols_uses_explicit_benchmark():
    wl = {"stocks": [{"ticker": "MU", "benchmark": "SMH"}]}
    assert watch_symbols(wl) == ["MU", "SMH"]


def test_backfill_appends_price_only_rows(data_dir):
    n = backfill_history(
        data_dir,
        downloader=_fake_downloader({
            "ON": [("2026-07-01", 950.0), ("2026-07-02", 960.0)],
            "SPY": [("2026-07-01", 620.0)],
        }),
        today=date(2026, 7, 15),
    )
    assert n == 3
    rows = [json.loads(l) for l in
            (data_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()]
    on_rows = [r for r in rows if r["ticker"] == "ON"]
    assert on_rows[0] == {"date": "2026-07-01", "ticker": "ON",
                          "price": 950.0, "source": "backfill"}
    assert {r["ticker"] for r in rows} == {"ON", "SPY"}


def test_backfill_never_overwrites_existing_rows(data_dir):
    existing = {"date": "2026-07-01", "ticker": "ON", "price": 111.0, "rsi_14": 55}
    (data_dir / "history.jsonl").write_text(
        json.dumps(existing) + "\n", encoding="utf-8")
    n = backfill_history(
        data_dir,
        downloader=_fake_downloader({"ON": [("2026-07-01", 950.0)]}),
        today=date(2026, 7, 15),
    )
    assert n == 0
    rows = [json.loads(l) for l in
            (data_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == [existing]  # snapshot row wins, byte-for-byte untouched


def test_backfill_skips_today_and_future(data_dir):
    n = backfill_history(
        data_dir,
        downloader=_fake_downloader({
            "ON": [("2026-07-15", 950.0), ("2026-07-16", 960.0)],
        }),
        today=date(2026, 7, 15),
    )
    assert n == 0  # refresh owns today's richer snapshot row


def test_backfill_is_idempotent(data_dir):
    dl = _fake_downloader({"ON": [("2026-07-01", 950.0)]})
    assert backfill_history(data_dir, downloader=dl, today=date(2026, 7, 15)) == 1
    assert backfill_history(data_dir, downloader=dl, today=date(2026, 7, 15)) == 0


def test_backfill_days_overrides_years(data_dir):
    seen = {}

    def spy_downloader(symbols, start):
        seen["start"] = start
        return {s: [] for s in symbols}

    backfill_history(data_dir, days=30, downloader=spy_downloader,
                     today=date(2026, 7, 15))
    assert seen["start"] == date(2026, 6, 15)


def test_backfill_empty_watchlist_returns_zero(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"stocks": []}), encoding="utf-8")
    assert backfill_history(d, downloader=_fake_downloader({})) == 0
