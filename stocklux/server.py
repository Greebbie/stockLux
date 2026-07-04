"""FastAPI backend: reads/writes data/, computes staleness deterministically, serves the dashboard."""
from __future__ import annotations

import re
import threading
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .refresh import load_json, refresh_data

_MEMO_SUMMARY_KEYS = (
    "date", "action", "confidence", "buy_range", "multiple_basis",
    "price_at_analysis", "verdict", "thesis_health", "signals", "review_trigger",
    "price_targets", "top_risks", "entry_plan",
)
_THESIS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")

AUDIT_STALE_DAYS = 90
MAX_CONSECUTIVE_INCREMENTAL = 2


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _audit_freshness_errors(meta: dict, thesis_meta: dict | None) -> list[str]:
    """The methodology caps memo confidence at medium when the thesis is
    unaudited or its audit is >90 days older than the memo ("The thesis
    itself is under test"). Flag high-confidence memos that violate it."""
    if meta.get("confidence") != "high":
        return []
    thesis = meta.get("thesis")
    last = (thesis_meta or {}).get("last_audited")
    if not last:
        return [f"confidence 'high' on a never-audited thesis — capped at "
                f"medium until `/lux-audit-thesis {thesis}` runs"]
    try:
        age = (_as_date(meta["date"]) - _as_date(last)).days
    except (ValueError, TypeError):
        return []
    if age > AUDIT_STALE_DAYS:
        return [f"confidence 'high' but thesis audit is {age} days older "
                f"than the memo (>{AUDIT_STALE_DAYS}d) — capped at medium; "
                f"rerun `/lux-audit-thesis {thesis}`"]
    return []


def _incremental_chain_errors(data_dir: Path, ticker: str, latest_meta: dict) -> list[str]:
    """Incremental updates are an efficiency valve, not a way of life: after
    MAX_CONSECUTIVE_INCREMENTAL of them in a row, the next analysis must be
    a full rewrite (methodology, delta-scan rules)."""
    if latest_meta.get("mode") != "incremental":
        return []
    chain = 0
    for path in reversed(store.list_memos(data_dir, ticker)):
        try:
            meta, _ = store.parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            break
        if meta.get("mode") == "incremental":
            chain += 1
        else:
            break
    if chain > MAX_CONSECUTIVE_INCREMENTAL:
        return [f"{chain} consecutive incremental updates (max "
                f"{MAX_CONSECUTIVE_INCREMENTAL}) — the next analysis must be "
                f"a full rewrite"]
    return []


def build_overview(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    wl = store.load_watchlist(data_dir)
    quotes = load_json(data_dir / "quotes.json") or {"quotes": {}}
    theses = {t["id"]: t["meta"] for t in store.list_theses(data_dir)}
    rows = []
    for s in wl["stocks"]:
        quote = quotes["quotes"].get(s["ticker"], {})
        memo = store.latest_memo(data_dir, s["ticker"],
                                 holding=bool(s.get("holding", False)))
        row = {**s, "quote": quote, "memo": None, "memo_errors": [], "staleness": None}
        if memo:
            meta = memo["meta"]
            row["memo"] = {k: meta.get(k) for k in _MEMO_SUMMARY_KEYS}
            row["memo_errors"] = (memo["errors"]
                                  + _audit_freshness_errors(meta, theses.get(meta.get("thesis")))
                                  + _incremental_chain_errors(data_dir, s["ticker"], meta))
            price, base = quote.get("price"), meta.get("price_at_analysis")
            deviation = None
            if isinstance(price, (int, float)) and isinstance(base, (int, float)) and base != 0:
                deviation = round((price / base - 1) * 100, 1)
            days = None
            if meta.get("date"):
                try:
                    days = (date.today() - _as_date(meta["date"])).days
                except (ValueError, TypeError):
                    pass
            row["staleness"] = {
                "price_deviation_pct": deviation,
                "days_since_analysis": days,
                "needs_reanalysis": bool(
                    (deviation is not None and abs(deviation) > 15) or (days or 0) > 30
                ),
            }
        rows.append(row)
    return {
        "rows": rows,
        "quotes_fetched_at": quotes.get("fetched_at"),
        "theses": theses,
    }


class AddStockBody(BaseModel):
    ticker: str
    thesis: str
    layer: str = ""
    name: str = ""
    note: str = ""
    benchmark: str = ""


class ThesisBody(BaseModel):
    content: str


def create_app(data_dir: Path) -> FastAPI:
    data_dir = Path(data_dir)
    app = FastAPI(title="StockLux")

    @app.get("/api/overview")
    def overview():
        return build_overview(data_dir)

    @app.get("/api/stocks/{ticker}")
    def stock_detail(ticker: str):
        if not store.TICKER_RE.match(ticker):
            raise HTTPException(422, f"invalid ticker: {ticker}")
        quotes = load_json(data_dir / "quotes.json") or {"quotes": {}}
        flows = load_json(data_dir / "flows.json") or {"flows": {}}
        memos = [store.load_memo(p) for p in reversed(store.list_memos(data_dir, ticker))]
        return {"ticker": ticker, "quote": quotes["quotes"].get(ticker),
                "flows": flows["flows"].get(ticker), "memos": memos}

    @app.post("/api/watchlist")
    def add_stock(body: AddStockBody):
        thesis_ids = [t["id"] for t in store.list_theses(data_dir)]
        if body.thesis not in thesis_ids:
            raise HTTPException(422, f"thesis not found: {body.thesis}")
        try:
            wl = store.add_stock(
                store.load_watchlist(data_dir), ticker=body.ticker.upper(),
                thesis=body.thesis, layer=body.layer, name=body.name, note=body.note,
                benchmark=body.benchmark.upper())
        except ValueError as e:
            raise HTTPException(422, str(e))
        store.save_watchlist(data_dir, wl)
        return {"ok": True}

    @app.delete("/api/watchlist/{ticker}")
    def delete_stock(ticker: str):
        store.save_watchlist(
            data_dir, store.remove_stock(store.load_watchlist(data_dir), ticker))
        return {"ok": True}

    @app.get("/api/theses")
    def theses():
        return store.list_theses(data_dir)

    @app.put("/api/theses/{thesis_id}")
    def put_thesis(thesis_id: str, body: ThesisBody):
        if not _THESIS_ID_RE.match(thesis_id):
            raise HTTPException(422, "thesis id must be lowercase letters/digits/hyphens")
        p = data_dir / "theses" / f"{thesis_id}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.content, encoding="utf-8")
        return {"ok": True}

    @app.post("/api/refresh")
    def trigger_refresh():
        threading.Thread(target=refresh_data, args=(data_dir,), daemon=True).start()
        return {"ok": True, "message": "background refresh started"}

    @app.get("/api/status")
    def status():
        mtimes = [p.stat().st_mtime for p in data_dir.rglob("*") if p.is_file()]
        return {"data_version": max(mtimes) if mtimes else 0}

    web = Path(__file__).parent / "web"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
