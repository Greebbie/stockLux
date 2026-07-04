"""StockLux CLI: ui / refresh / add / export. Data dir = ./data under cwd."""
from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from . import refresh as refresh_mod
from . import store

app = typer.Typer(help="StockLux — local-first equity research workbench", no_args_is_help=True)


def _data_dir() -> Path:
    return Path.cwd() / "data"


@app.command()
def refresh() -> None:
    """Fetch quotes (quotes.json) and flow data (flows.json) for the whole watchlist."""
    data_dir = _data_dir()
    wl = store.load_watchlist(data_dir)
    if not wl["stocks"]:
        typer.echo("watchlist is empty — add a stock first: `stocklux add <TICKER> --thesis <id>`")
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
    thesis: str = typer.Option(..., help="thesis id (a filename under data/theses/)"),
    layer: str = typer.Option("", help="supply-chain bottleneck layer, e.g. power-semis"),
    name: str = typer.Option("", help="company name"),
    note: str = typer.Option("", help="one-line note"),
    holding: bool = typer.Option(False, "--holding", help="mark as a position the user actually owns"),
    benchmark: str = typer.Option("", help="relative-strength benchmark (e.g. SMH, XLU); default SPY"),
) -> None:
    """Add a stock to the watchlist."""
    data_dir = _data_dir()
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
    typer.echo(f"added {ticker.upper()} → thesis {thesis}")


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
        threading.Thread(target=refresh_mod.refresh_data, args=(data_dir,),
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
