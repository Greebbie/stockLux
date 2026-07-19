import json

from typer.testing import CliRunner

from luxtock import refresh, store
from luxtock.cli import app

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Luxtock" in result.output


def _fake_quotes(tickers, prev=None, paired=None):
    return {"fetched_at": "2026-07-04T00:00:00+00:00",
            "quotes": {t: {"price": 100.0, "ttm_pe": 20.0, "fwd_pe": 10.0, "stale": False}
                       for t in tickers}}


def _fake_flows(tickers, prev=None, benchmarks=None):
    return {"fetched_at": "2026-07-04T00:00:00+00:00",
            "flows": {t: {"short_pct_float": 0.02, "stale": False} for t in tickers}}


def _setup_data(tmp_path):
    d = tmp_path / "data"
    store.ensure_dirs(d)
    (d / "theses" / "ev-adoption.md").write_text(
        "---\nid: ev-adoption\nname: Token\nstatus: intact\ncreated: 2026-07-01\n---\nbody",
        encoding="utf-8")
    store.save_watchlist(d, store.add_stock({"stocks": []}, ticker="ON", thesis="ev-adoption"))
    return d


def test_refresh_writes_files(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("luxtock.refresh.fetch_quotes", _fake_quotes)
    monkeypatch.setattr("luxtock.refresh.fetch_flows", _fake_flows)
    result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 0, result.output
    q = json.loads((d / "quotes.json").read_text(encoding="utf-8"))
    assert q["quotes"]["ON"]["price"] == 100.0
    assert (d / "flows.json").exists()
    assert "ON" in result.output
    # refresh also appends to the snapshot log
    hist = (d / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(hist[0])["ticker"] == "ON"


def test_refresh_empty_watchlist_exits_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 1


def test_add_command(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "chpt", "--thesis", "ev-adoption", "--layer", "charging"])
    assert result.exit_code == 0, result.output
    wl = store.load_watchlist(d)
    assert any(s["ticker"] == "CHPT" for s in wl["stocks"])


def test_add_holding_flag(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "chpt", "--thesis", "ev-adoption", "--holding"])
    assert result.exit_code == 0, result.output
    wl = store.load_watchlist(d)
    entry = next(s for s in wl["stocks"] if s["ticker"] == "CHPT")
    assert entry["holding"] is True


def test_hold_command_sets_and_clears(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["hold", "on"])
    assert result.exit_code == 0, result.output
    assert store.load_watchlist(d)["stocks"][0]["holding"] is True
    result = runner.invoke(app, ["hold", "ON", "--off"])
    assert result.exit_code == 0, result.output
    assert store.load_watchlist(d)["stocks"][0]["holding"] is False


def test_hold_unknown_ticker_exits_1(tmp_path, monkeypatch):
    _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["hold", "ZZZ"])
    assert result.exit_code == 1


def test_add_unknown_thesis_fails(tmp_path, monkeypatch):
    _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "CHPT", "--thesis", "nope"])
    assert result.exit_code == 1


def test_add_without_thesis_succeeds(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "chpt", "--layer", "charging"])
    assert result.exit_code == 0, result.output
    wl = store.load_watchlist(d)
    entry = next(s for s in wl["stocks"] if s["ticker"] == "CHPT")
    assert "thesis" not in entry
    # confirmation echoes without a thesis suffix
    assert result.output.strip() == "added CHPT"


def test_quotes_stale(tmp_path):
    assert refresh.quotes_stale(tmp_path) is True  # no file = stale
    (tmp_path / "quotes.json").write_text(
        json.dumps({"fetched_at": "2020-01-01T00:00:00+00:00", "quotes": {}}),
        encoding="utf-8")
    assert refresh.quotes_stale(tmp_path) is True  # too old


def test_ui_starts_server(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    called = {}

    def fake_run(app_obj, host, port):
        called["host"], called["port"] = host, port

    monkeypatch.setattr("luxtock.cli.uvicorn.run", fake_run)
    monkeypatch.setattr("luxtock.refresh.refresh_data", lambda dd: {})
    result = runner.invoke(app, ["ui", "--no-browser", "--port", "9999"])
    assert result.exit_code == 0, result.output
    assert called == {"host": "127.0.0.1", "port": 9999}
    assert (d / "theses").exists()  # ensure_dirs took effect


def test_main_survives_gbk_stdout():
    """main()'s stream reconfigure must switch a GBK stdout to utf-8 (Windows console)."""
    import io
    import sys as _sys

    gbk_out = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    old_stdout = _sys.stdout
    try:
        _sys.stdout = gbk_out
        # mirror the reconfigure loop in main()
        for stream in (_sys.stdout, _sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, OSError):
                pass
        # stdout must now be utf-8
        assert gbk_out.encoding == "utf-8"
    finally:
        _sys.stdout = old_stdout


def test_portfolio_prints_bear_stress_as_unsigned_drawdown(tmp_path, monkeypatch):
    # drawdown_pct is a loss magnitude — a 19% loss must never print "+19.0%"
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    wl = store.set_shares(store.set_holding(store.load_watchlist(d), "ON", True), "ON", 10)
    store.save_watchlist(d, wl)
    (d / "quotes.json").write_text(json.dumps({
        "fetched_at": "2026-07-19T00:00:00+00:00",
        "quotes": {"ON": {"price": 100.0}}}), encoding="utf-8")
    memo_dir = d / "analyses" / "ON"
    memo_dir.mkdir(parents=True, exist_ok=True)
    (memo_dir / "2026-07-08.md").write_text(
        '---\nticker: "ON"\ndate: 2026-07-08\n'
        "price_targets:\n  bear: 81\n  base: 120\n  bull: 150\n---\nbody\n",
        encoding="utf-8")
    result = runner.invoke(app, ["portfolio"])
    assert result.exit_code == 0, result.output
    assert "bear stress drawdown: 19.0%" in result.output
    assert "+19.0%" not in result.output


def test_backfill_command(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "luxtock.backfill._yf_daily_closes",
        lambda symbols, start: {s: [("2026-07-01", 100.0)] for s in symbols})
    result = runner.invoke(app, ["backfill"])
    assert result.exit_code == 0, result.output
    assert "backfilled 2 rows" in result.output  # ON + its default benchmark SPY
    hist = (d / "history.jsonl").read_text(encoding="utf-8")
    assert '"source": "backfill"' in hist


def test_daily_command_runs_pipeline(tmp_path, monkeypatch):
    d = _setup_data(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("luxtock.refresh.fetch_quotes", _fake_quotes)
    monkeypatch.setattr("luxtock.refresh.fetch_flows", _fake_flows)
    monkeypatch.setattr("luxtock.backfill._yf_daily_closes",
                        lambda symbols, start: {s: [] for s in symbols})
    result = runner.invoke(app, ["daily"])
    assert result.exit_code == 0, result.output
    assert (d / "quotes.json").exists()
    assert (d / "quant.json").exists()
    assert (d / "calibration.json").exists()
    assert "alerts" in result.output


def test_daily_empty_watchlist_exits_0(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["daily"])
    assert result.exit_code == 0
    assert "watchlist is empty" in result.output
