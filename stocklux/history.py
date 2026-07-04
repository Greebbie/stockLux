"""Append-only per-ticker snapshot log (data/history.jsonl).

Every refresh appends one JSON line per fresh ticker, keyed on (date, ticker).
Over time this builds the framework's own time series — short-interest deltas,
revision-momentum direction, price path for retrospect MAE/MFE grading — none
of which a point-in-time quotes.json/flows.json can answer. Never rewritten,
only appended; deduped so multiple refreshes per day record once.
"""
from __future__ import annotations

import json
from pathlib import Path

HISTORY_FILE = "history.jsonl"


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        keys.add((row.get("date"), row.get("ticker")))
    return keys


def snapshot_rows(quotes: dict, flows: dict) -> list[dict]:
    """One compact row per fresh ticker; stale fetches are never recorded."""
    day = (quotes.get("fetched_at") or "")[:10]
    rows = []
    for ticker, q in (quotes.get("quotes") or {}).items():
        if q.get("stale") or q.get("price") is None or not day:
            continue
        f = (flows.get("flows") or {}).get(ticker) or {}
        trend = f.get("trend") if isinstance(f.get("trend"), dict) else {}
        rev = q.get("revisions") if isinstance(q.get("revisions"), dict) else {}
        analyst = q.get("analyst") if isinstance(q.get("analyst"), dict) else {}
        rows.append({
            "date": day,
            "ticker": ticker,
            "price": q.get("price"),
            "fwd_eps": q.get("fwd_eps"),
            "short_pct_float": f.get("short_pct_float"),
            "put_call_oi_ratio": f.get("put_call_oi_ratio"),
            "rsi_14": trend.get("rsi_14"),
            "dist_50dma_pct": trend.get("dist_50dma_pct"),
            "rel_strength_3m": trend.get("rel_strength_3m"),
            "fwd_eps_change_90d_pct": rev.get("fwd_eps_change_90d_pct"),
            "up_last_30d": rev.get("up_last_30d"),
            "down_last_30d": rev.get("down_last_30d"),
            "pt_mean": analyst.get("pt_mean"),
        })
    return rows


def append_history(data_dir: Path, quotes: dict, flows: dict) -> int:
    """Append today's snapshots, skipping (date, ticker) pairs already logged.
    Returns the number of rows written."""
    path = Path(data_dir) / HISTORY_FILE
    seen = _existing_keys(path)
    rows = [r for r in snapshot_rows(quotes, flows)
            if (r["date"], r["ticker"]) not in seen]
    if rows:
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)
