"""Luxtock CLI: ui / refresh / add / export. Data dir = ./data under cwd."""
from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from . import refresh as refresh_mod
from . import store

app = typer.Typer(help="Luxtock — local-first equity research workbench", no_args_is_help=True)


def _data_dir() -> Path:
    return Path.cwd() / "data"


@app.command()
def refresh() -> None:
    """Fetch quotes (quotes.json) and flow data (flows.json) for the whole watchlist."""
    data_dir = _data_dir()
    wl = store.load_watchlist(data_dir)
    if not wl["stocks"]:
        typer.echo("watchlist is empty — add a stock first: `luxtock add <TICKER> --thesis <id>`")
        raise typer.Exit(1)
    quotes = refresh_mod.refresh_data(data_dir)
    for s in wl["stocks"]:
        q = quotes["quotes"].get(s["ticker"], {})
        mark = "⚠ stale" if q.get("stale") else "✓"
        typer.echo(f"{s['ticker']:<6} {q.get('price')}  "
                   f"ttm {q.get('ttm_pe')} / fwd {q.get('fwd_pe')}  {mark}")


@app.command()
def add(
    ticker: str,
    thesis: str = typer.Option("", help="thesis id (a filename under data/theses/); optional"),
    layer: str = typer.Option("", help="supply-chain bottleneck layer, e.g. power-semis"),
    name: str = typer.Option("", help="company name"),
    note: str = typer.Option("", help="one-line note"),
    holding: bool = typer.Option(False, "--holding", help="mark as a position the user actually owns"),
    benchmark: str = typer.Option("", help="relative-strength benchmark (e.g. SMH, XLU); default SPY"),
) -> None:
    """Add a stock to the watchlist."""
    data_dir = _data_dir()
    if thesis:
        thesis_ids = [t["id"] for t in store.list_theses(data_dir)]
        if thesis not in thesis_ids:
            typer.echo(f"thesis '{thesis}' not found. Available: {thesis_ids or '(none — create one under data/theses/)'}")
            raise typer.Exit(1)
    try:
        wl = store.add_stock(store.load_watchlist(data_dir), ticker=ticker.upper(),
                             thesis=thesis, layer=layer, name=name, note=note,
                             holding=holding, benchmark=benchmark.upper())
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    store.save_watchlist(data_dir, wl)
    suffix = f" → thesis {thesis}" if thesis else ""
    typer.echo(f"added {ticker.upper()}{suffix}")


@app.command()
def hold(
    ticker: str,
    off: bool = typer.Option(False, "--off", help="clear the holding flag instead of setting it"),
) -> None:
    """Mark a watchlist name as held (or clear it with --off). Gates hold/trim/exit verdicts."""
    data_dir = _data_dir()
    try:
        wl = store.set_holding(store.load_watchlist(data_dir), ticker.upper(), not off)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    store.save_watchlist(data_dir, wl)
    typer.echo(f"{ticker.upper()} holding = {not off}")


@app.command()
def pair(
    ticker: str,
    paired_ticker: str = typer.Argument("", help="home-market listing (e.g. 000660.KS); empty with --off"),
    ratio: float = typer.Option(1.0, help="underlying shares per ONE US share (10 ADR = 1 common → 0.1)"),
    currency: str = typer.Option("USD", help="home-market quote currency (e.g. KRW); USD skips FX"),
    off: bool = typer.Option(False, "--off", help="remove the pairing"),
) -> None:
    """Pair a listing with its home-market line — refresh then tracks parity & premium."""
    data_dir = _data_dir()
    paired = None if off else {"ticker": paired_ticker, "ratio": ratio, "currency": currency.upper()}
    try:
        wl = store.set_paired(store.load_watchlist(data_dir), ticker.upper(), paired)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    store.save_watchlist(data_dir, wl)
    typer.echo(f"{ticker.upper()} paired = {paired or '(removed)'}")


@app.command()
def quant() -> None:
    """Deterministic feature vector + setup scores per ticker → data/quant.json."""
    from .quant import build_quant

    result = build_quant(_data_dir())
    for ticker, entry in result["tickers"].items():
        f, s = entry["features"], entry["scores"]
        gap = f"{f['valuation_gap_pct']:+.1f}%" if f.get("valuation_gap_pct") is not None else "—"
        ev = f"{f['ev_return_pct']:+.1f}%" if f.get("ev_return_pct") is not None else "—"
        comp = f"{s['composite']:.0f}" if s.get("composite") is not None else "—"
        typer.echo(f"{ticker:<6} px {f.get('price') or '—':>9}  gap {gap:>7}  EV {ev:>7}  "
                   f"setup {comp:>3} [{s.get('band') or 'n/a'}]  coverage {s['coverage']:.0%}")
    typer.echo("written: data/quant.json")


@app.command()
def screen(
    top: int = typer.Option(15, help="qualified rows to print"),
    min_drawdown: float = typer.Option(15.0, "--min-drawdown", help="Gate A drawdown floor, stored positive (%)"),
    max_deep: int = typer.Option(100, "--max-deep", help="cap on Stage-A survivors proceeding to Stage B"),
    universe: str = typer.Option("", "--universe", help="universe.json path override (default data/universe.json)"),
) -> None:
    """Candidate discoverer: market-wide beaten-down/quality-discount funnel (spec: framework/screen.md)."""
    from .screen import (
        NOTICE_CANDIDATES_ONLY,
        NOTICE_RR_PROXY,
        RR_PROXY_DISPLAY_CAP,
        UNIVERSE_STALE_DAYS,
        build_screen,
        universe_age_days,
    )

    data_dir = _data_dir()
    universe_path = Path(universe) if universe else data_dir / "universe.json"
    try:
        result = build_screen(data_dir, universe_path=universe_path,
                              min_drawdown_pct=min_drawdown, max_deep=max_deep,
                              progress_fn=typer.echo)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)

    all_qualified = [r for r in result["results"] if not r["disqualified"]]
    qualified = [r for r in all_qualified if r["track"] != "hypergrowth"]
    qualified.sort(key=lambda r: (r["depression_score"] is None, -(r["depression_score"] or 0)))
    rows = qualified[:top]
    if not rows:
        typer.echo("no qualified candidates")
    for r in rows:
        f, fu = r["features"], r["fundamentals"]
        price = f"{f['price']:.2f}" if f.get("price") is not None else "—"
        dd = f"{f['drawdown_pct']:.1f}%" if f.get("drawdown_pct") is not None else "—"
        rev = f"{fu['rev_90d_pct']:+.1f}%" if fu.get("rev_90d_pct") is not None else "—"
        fwd_pe = f"{fu['fwd_pe']:.1f}" if fu.get("fwd_pe") is not None else "—"
        if fu.get("rr_proxy") is None:
            rr = "—"
        elif fu["rr_proxy"] > RR_PROXY_DISPLAY_CAP:
            rr = ">10"
        else:
            rr = f"{fu['rr_proxy']:.2f}"
        score = f"{r['depression_score']:.0f}" if r["depression_score"] is not None else "—"
        typer.echo(f"{r['ticker']:<6} {price:>8}  dd {dd:>7}  {r['track']:<17}  "
                   f"rev90d {rev:>7}  fwdPE {fwd_pe:>6}  rr {rr:>6}  "
                   f"score {score:>3} [{r['band']}]  {','.join(r['flags']) or '—'}")

    hypergrowth = [r for r in all_qualified if r["track"] == "hypergrowth"]
    hypergrowth.sort(key=lambda r: (r["depression_score"] is None, -(r["depression_score"] or 0)))
    if hypergrowth:
        typer.echo("")
        typer.echo("hypergrowth track — most speculative tier, no earnings anchor:")
        for r in hypergrowth[:top]:
            f, fu = r["features"], r["fundamentals"]
            price = f"{f['price']:.2f}" if f.get("price") is not None else "—"
            dd = f"{f['drawdown_pct']:.1f}%" if f.get("drawdown_pct") is not None else "—"
            rg = f"{fu['revenue_growth'] * 100:+.0f}%" if fu.get("revenue_growth") is not None else "—"
            ev_s = f"{fu['ev_sales']:.1f}" if fu.get("ev_sales") is not None else "—"
            gs = f"{fu['gs_like']:.2f}" if fu.get("gs_like") is not None else "—"
            runway = f"{fu['runway_years']:.1f}y" if fu.get("runway_years") is not None else "—"
            score = f"{r['depression_score']:.0f}" if r["depression_score"] is not None else "—"
            typer.echo(f"{r['ticker']:<6} {price:>8}  dd {dd:>7}  rg {rg:>6}  "
                       f"EV/S {ev_s:>6}  gs {gs:>5}  runway {runway:>6}  "
                       f"score {score:>3} [{r['band']}]")

    if result["stage_b_cap_dropped"]:
        typer.echo(f"note: {result['stage_b_cap_dropped']} Stage-A survivor(s) dropped by --max-deep "
                   f"({result['stage_a_survivors']} qualified, {max_deep} proceeded to Stage B)")
    universe_as_of = result["universe_as_of"]
    age_days = universe_age_days(universe_as_of)
    if age_days is not None and age_days > UNIVERSE_STALE_DAYS:
        typer.echo(
            f"warning: universe snapshot is {age_days} days old (as of {universe_as_of}) — "
            "index membership has likely drifted; regenerate data/universe.json",
            err=True,
        )
    typer.echo(NOTICE_CANDIDATES_ONLY)
    typer.echo(NOTICE_RR_PROXY)
    typer.echo("written: data/screen.json")


@app.command()
def backfill(
    years: int = typer.Option(2, help="how far back to fetch daily closes"),
    days: int = typer.Option(0, help="override: only the last N days (top-up)"),
) -> None:
    """Backfill daily closes (yfinance) into data/history.jsonl — tickers + benchmarks."""
    from .backfill import backfill_history

    n = backfill_history(_data_dir(), years=years, days=days or None)
    typer.echo(f"backfilled {n} rows into data/history.jsonl")


@app.command()
def shares(
    ticker: str,
    count: float = typer.Argument(..., help="share count (0 clears the field)"),
) -> None:
    """Set the share count on a holding — sizing input for `luxtock portfolio`."""
    data_dir = _data_dir()
    try:
        wl = store.set_shares(store.load_watchlist(data_dir), ticker.upper(), count)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    store.save_watchlist(data_dir, wl)
    typer.echo(f"{ticker.upper()} shares = {count if count else '(cleared)'}")


@app.command()
def cash(
    amount: float = typer.Argument(..., help="cash balance in USD (negative clears it)"),
) -> None:
    """Set the portfolio cash balance (context for `luxtock portfolio` weights)."""
    data_dir = _data_dir()
    wl = store.set_cash(store.load_watchlist(data_dir), None if amount < 0 else amount)
    store.save_watchlist(data_dir, wl)
    typer.echo(f"cash_usd = {wl.get('cash_usd', '(cleared)')}")


@app.command()
def portfolio() -> None:
    """Concentration & bear-stress report over sized holdings (spec: framework/quant.md)."""
    from .portfolio import portfolio_report

    report = portfolio_report(_data_dir())
    if not report["positions"]:
        typer.echo("no holdings — mark one with `luxtock hold <TICKER>` and size it "
                   "with `luxtock shares <TICKER> <N>`")
        raise typer.Exit(0)
    for p in report["positions"]:
        if p.get("unsized"):
            typer.echo(f"{p['ticker']:<6} (unsized — `luxtock shares {p['ticker']} <N>`)")
        else:
            typer.echo(f"{p['ticker']:<6} {p['shares']:>8.2f} sh × {p['price']:>10.2f} "
                       f"= {p['value']:>12,.0f}  {p['weight_pct']:>5.1f}%")
    typer.echo(f"cash   {report['cash_usd']:>12,.0f}   total {report['total_value']:,.0f}")
    for group_kind, groups in report["groups"].items():
        for gname, w in sorted(groups.items(), key=lambda kv: -kv[1]):
            typer.echo(f"  {group_kind[3:]:<8} {gname:<14} {w:.1f}%")
    bs = report["bear_stress"]
    if bs.get("drawdown_pct") is not None:
        # drawdown_pct is a loss magnitude (positive number = loss) — label
        # it as a drawdown rather than sign-formatting it like a return.
        typer.echo(f"bear stress drawdown: {bs['drawdown_pct']:.1f}% "
                   f"(covered: {', '.join(bs['covered_tickers']) or '—'}"
                   f"{'; uncovered: ' + ', '.join(bs['uncovered_tickers']) if bs['uncovered_tickers'] else ''})")
    for f in report["flags"]:
        typer.echo(f"[{f['level'].upper()}] {f['kind']}: {f['detail']}")


@app.command()
def check(
    quiet: bool = typer.Option(False, "--quiet", help="print nothing when there are no alerts"),
) -> None:
    """Price alerts vs memo levels (tranches/invalidation/trim/bear/bull) + portfolio flags.

    Stateless — re-fires on every run; exit code 1 when any alert is active (cron-friendly).
    """
    from .check import run_checks

    result = run_checks(_data_dir())
    alerts = result["alerts"]
    if not alerts:
        if not quiet:
            typer.echo("no alerts")
        raise typer.Exit(0)
    for a in alerts:
        typer.echo(f"[{a['level'].upper():<7}] {a['ticker']:<9} {a['kind']}: {a['detail']}")
    raise typer.Exit(1)


@app.command()
def calibrate() -> None:
    """Grade matured price targets (Brier ledger) + track immature ones → data/calibration.json."""
    from .calibrate import calibrate as run_calibrate

    result = run_calibrate(_data_dir())
    agg = result["aggregate"]
    typer.echo(f"matured: {agg['n']} graded"
               + (f", mean Brier {agg['mean_brier']:.3f}" if agg.get("mean_brier") is not None else ""))
    for row in result["matured"]:
        note = f"  ({row['note']})" if row.get("note") else ""
        tier = row.get("realized_tier") or "—"
        brier = f"{row['brier']:.3f}" if row.get("brier") is not None else "—"
        typer.echo(f"  {row['ticker']:<6} {row['memo_date']}  tier={tier}  brier={brier}{note}")
    typer.echo(f"tracking: {len(result['tracking'])} immature")
    for row in result["tracking"]:
        typer.echo(f"  {row['ticker']:<6} {row['memo_date']}  {row['months_elapsed']:.1f}mo  "
                   f"bear→bull {row['pct_between_bear_bull']:.0f}%  "
                   f"{'above' if row['above_base'] else 'below'} base")
    typer.echo("written: data/calibration.json")


@app.command()
def daily() -> None:
    """One scheduled pass: refresh → 30d backfill top-up → quant → calibrate → check.

    Always exits 0 unless a step raises — alerts are printed, not signaled
    via exit code (`luxtock check` keeps the exit-1 contract for cron use).
    """
    from .backfill import backfill_history
    from .calibrate import calibrate as run_calibrate
    from .check import run_checks
    from .quant import build_quant

    data_dir = _data_dir()
    wl = store.load_watchlist(data_dir)
    if not wl["stocks"]:
        typer.echo("watchlist is empty — nothing to do")
        raise typer.Exit(0)
    refresh_mod.refresh_data(data_dir)
    n_backfilled = backfill_history(data_dir, days=30)
    build_quant(data_dir)
    cal = run_calibrate(data_dir)
    checks = run_checks(data_dir)
    typer.echo(f"refreshed {len(wl['stocks'])} tickers; "
               f"backfilled {n_backfilled} rows; "
               f"matured {cal['aggregate']['n']}; "
               f"alerts {len(checks['alerts'])}")
    for a in checks["alerts"]:
        typer.echo(f"[{a['level'].upper():<7}] {a['ticker']:<9} {a['kind']}: {a['detail']}")


@app.command()
def export(
    ticker: str,
    pdf: bool = typer.Option(False, "--pdf", help="also print to PDF via local Edge/Chrome"),
    out: str = typer.Option("output", help="output directory"),
) -> None:
    """Export the latest memo as a self-contained HTML report (optionally PDF)."""
    from .export import export_memo

    data_dir = _data_dir()
    try:
        result = export_memo(data_dir, ticker.upper(), Path.cwd() / out, pdf=pdf)
    except FileNotFoundError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    typer.echo(f"HTML: {result['html']}")
    if result["pdf"]:
        typer.echo(f"PDF:  {result['pdf']}")
    elif pdf:
        typer.echo(f"PDF not generated: {result['pdf_error']}")


@app.command()
def report(
    pdf: bool = typer.Option(False, "--pdf", help="also print to PDF via local Edge/Chrome"),
    out: str = typer.Option("output", help="output directory"),
) -> None:
    """One desk-level report: portfolio, quant table, verdicts, alerts, calibration."""
    from .report import export_report

    result = export_report(_data_dir(), Path.cwd() / out, pdf=pdf)
    typer.echo(f"HTML: {result['html']}")
    if result["pdf"]:
        typer.echo(f"PDF:  {result['pdf']}")
    elif pdf:
        typer.echo(f"PDF not generated: {result['pdf_error']}")


@app.command()
def ui(
    port: int = typer.Option(8321, help="port to listen on"),
    no_browser: bool = typer.Option(False, "--no-browser", help="do not open the browser"),
) -> None:
    """Start the research dashboard (http://127.0.0.1:PORT)."""
    data_dir = _data_dir()
    store.ensure_dirs(data_dir)
    wl = store.load_watchlist(data_dir)
    if wl["stocks"] and refresh_mod.quotes_stale(data_dir):
        typer.echo("quotes are older than 12h — refreshing in the background…")
        threading.Thread(target=refresh_mod.try_refresh_data, args=(data_dir,),
                         daemon=True).start()
    from .server import create_app

    application = create_app(data_dir)
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    typer.echo(f"dashboard: http://127.0.0.1:{port}  (Ctrl+C to quit)")
    uvicorn.run(application, host="127.0.0.1", port=port)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    app()
