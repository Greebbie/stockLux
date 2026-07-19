"""Data layer: watchlist I/O, memo/thesis frontmatter parsing and validation.

Immutable style: transform functions take an object and return a new one;
nothing is mutated in place.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

ACTIONS = [
    "enter", "wait_for_pullback", "hold", "watch_only",
    "good_company_bad_price", "crowded_theme", "thesis_broken",
    "no_edge", "trim", "exit",
]
SIGNAL_KEYS = [
    "chain", "narrative", "fundamentals", "valuation",
    "flows", "sentiment", "competition", "macro",
]
SIGNAL_VALUES = ["favorable", "neutral", "unfavorable", "no_signal"]
THESIS_STATUS = ["intact", "weakening", "damaged", "dead"]
CONFIDENCE = ["high", "medium", "low"]
ANALYSIS_MODES = ["full", "incremental"]


def write_text_atomic(path: Path, text: str) -> None:
    """Atomic UTF-8 file write: temp file in the same directory + os.replace
    (atomic on both POSIX and Windows). A bare write_text truncates before
    writing, so a crash mid-write destroys the previous contents; this never
    exposes a partially written or empty file."""
    p = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=p.parent, prefix=f"{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise


def write_json_atomic(path: Path, data: dict) -> None:
    """Atomic JSON write in the repo's standard encoding (UTF-8, indent=2)."""
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def ensure_dirs(data_dir: Path) -> None:
    for sub in ("theses", "analyses", "retrospects"):
        (Path(data_dir) / sub).mkdir(parents=True, exist_ok=True)


def load_watchlist(data_dir: Path) -> dict:
    p = Path(data_dir) / "watchlist.json"
    if not p.exists():
        return {"stocks": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_watchlist(data_dir: Path, watchlist: dict) -> None:
    p = Path(data_dir) / "watchlist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(watchlist, ensure_ascii=False, indent=2), encoding="utf-8")


def add_stock(
    watchlist: dict, *, ticker: str, thesis: str = "",
    layer: str = "", name: str = "", note: str = "", holding: bool = False,
    benchmark: str = "",
) -> dict:
    if not TICKER_RE.match(ticker):
        raise ValueError(f"invalid ticker: {ticker} (uppercase, e.g. MU, BRK.B)")
    if benchmark and not TICKER_RE.match(benchmark):
        raise ValueError(f"invalid benchmark ticker: {benchmark}")
    if any(s["ticker"] == ticker for s in watchlist["stocks"]):
        raise ValueError(f"{ticker} is already on the watchlist")
    entry = {
        "ticker": ticker, "name": name, "layer": layer,
        "added": datetime.now(timezone.utc).date().isoformat(), "note": note,
        "holding": holding,
    }
    if thesis:
        entry["thesis"] = thesis
    if benchmark:
        entry["benchmark"] = benchmark
    return {**watchlist, "stocks": watchlist["stocks"] + [entry]}


def set_holding(watchlist: dict, ticker: str, holding: bool) -> dict:
    if not any(s["ticker"] == ticker for s in watchlist["stocks"]):
        raise ValueError(f"{ticker} is not on the watchlist")
    return {**watchlist, "stocks": [
        {**s, "holding": holding} if s["ticker"] == ticker else s
        for s in watchlist["stocks"]
    ]}


def set_shares(watchlist: dict, ticker: str, shares: float) -> dict:
    """Set the optional share count on a holding (0 removes the field).

    Sizing input for `luxtock portfolio` — still no cost basis or P&L,
    by design.
    """
    if shares < 0:
        raise ValueError(f"shares must be >= 0, got {shares}")
    if not any(s["ticker"] == ticker for s in watchlist["stocks"]):
        raise ValueError(f"{ticker} is not on the watchlist")

    def _apply(s: dict) -> dict:
        if s["ticker"] != ticker:
            return s
        if shares == 0:
            return {k: v for k, v in s.items() if k != "shares"}
        return {**s, "shares": float(shares)}

    return {**watchlist, "stocks": [_apply(s) for s in watchlist["stocks"]]}


def set_paired(watchlist: dict, ticker: str, paired: dict | None) -> dict:
    """Set/replace/remove the optional paired-listing config on a watchlist
    entry (None removes it). `paired` = {"ticker", "ratio", "currency"?} where
    ratio = underlying shares represented by ONE US share (10 ADR = 1 common
    -> ratio 0.1); currency defaults to "USD" (no FX fetch needed then).
    """
    if not any(s["ticker"] == ticker for s in watchlist["stocks"]):
        raise ValueError(f"{ticker} is not on the watchlist")

    def _apply(s: dict) -> dict:
        if s["ticker"] != ticker:
            return s
        if paired is None:
            return {k: v for k, v in s.items() if k != "paired"}
        return {**s, "paired": {
            "ticker": paired["ticker"],
            "ratio": float(paired["ratio"]),
            "currency": paired.get("currency") or "USD",
        }}

    if paired is not None:
        paired_ticker = paired.get("ticker")
        if not paired_ticker:
            raise ValueError("paired ticker must be non-empty")
        ratio = paired.get("ratio")
        if ratio is None or ratio <= 0:
            raise ValueError(f"paired ratio must be > 0, got {ratio}")

    return {**watchlist, "stocks": [_apply(s) for s in watchlist["stocks"]]}


def set_cash(watchlist: dict, cash_usd: float | None) -> dict:
    """Set the optional top-level cash balance (None removes it)."""
    if cash_usd is None:
        return {k: v for k, v in watchlist.items() if k != "cash_usd"}
    if cash_usd < 0:
        raise ValueError(f"cash_usd must be >= 0, got {cash_usd}")
    return {**watchlist, "cash_usd": float(cash_usd)}


def remove_stock(watchlist: dict, ticker: str) -> dict:
    return {**watchlist, "stocks": [s for s in watchlist["stocks"] if s["ticker"] != ticker]}


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

MEMO_REQUIRED = [
    "ticker", "date", "action", "confidence",
    "price_at_analysis", "verdict", "thesis_health", "review_trigger",
]
_VERDICTS = ["below_range", "in_range", "above_range"]

# Memos dated on/after this must carry tier probabilities, an entry plan for
# enter/wait_for_pullback, and pass the risk/reward >= 2 gate on `enter`
# (methodology "Grandfathering"). Older memos are validated to the old contract.
POLICY_V2_DATE = date(2026, 7, 5)
MIN_RISK_REWARD = 2.0
_PROB_KEYS = ("p_bear", "p_base", "p_bull")


def _memo_date(meta: dict) -> date | None:
    v = meta.get("date")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2)


HOLDING_ONLY_ACTIONS = ["hold", "trim", "exit"]
NON_HOLDING_ONLY_ACTIONS = ["enter", "wait_for_pullback"]


def validate_memo(meta: dict, *, holding: bool | None = None) -> list[str]:
    errors = [f"missing field: {f}" for f in MEMO_REQUIRED if f not in meta]
    action = meta.get("action")
    if holding is True and action in NON_HOLDING_ONLY_ACTIONS:
        errors.append(
            f"action '{action}' is only legal when the watchlist entry is not "
            f"holding (this name has holding=true)")
    if holding is False and action in HOLDING_ONLY_ACTIONS:
        errors.append(
            f"action '{action}' requires holding=true on the watchlist entry "
            f"(set it with `luxtock hold <TICKER>`)")
    if "ticker" in meta and not isinstance(meta["ticker"], str):
        errors.append(
            f"ticker must be a string (YAML parses bare ON/NO/YES as booleans — "
            f"quote it): {meta['ticker']!r}")
    if "thesis" in meta and not (isinstance(meta["thesis"], str) and meta["thesis"].strip()):
        errors.append(f"thesis must be a non-empty string when present: {meta['thesis']!r}")
    if "action" in meta and meta["action"] not in ACTIONS:
        errors.append(f"invalid action: {meta['action']} (must be one of the ten states)")
    if "confidence" in meta and meta["confidence"] not in CONFIDENCE:
        errors.append(f"invalid confidence: {meta['confidence']} (high/medium/low)")
    if "thesis_health" in meta and meta["thesis_health"] not in THESIS_STATUS:
        errors.append(f"invalid thesis_health: {meta['thesis_health']}")
    if "verdict" in meta and meta["verdict"] not in _VERDICTS:
        errors.append(f"invalid verdict: {meta['verdict']}")
    br = meta.get("buy_range")
    if br is not None and not (isinstance(br, list) and len(br) == 2):
        errors.append("buy_range must be [low, high] or null")
    pt = meta.get("price_targets")
    if pt is not None:
        if not isinstance(pt, dict):
            errors.append("price_targets must be {bear, base, bull, horizon} or null")
        else:
            for k in ("bear", "base", "bull"):
                if not isinstance(pt.get(k), (int, float)):
                    errors.append(f"price_targets.{k} must be a number")

    memo_dt = _memo_date(meta)
    v2 = memo_dt is not None and memo_dt >= POLICY_V2_DATE

    if v2 and isinstance(pt, dict):
        probs = [pt.get(k) for k in _PROB_KEYS]
        if not all(isinstance(p, (int, float)) for p in probs):
            errors.append(
                "price_targets must carry p_bear/p_base/p_bull "
                "(required for memos dated on/after 2026-07-05)")
        elif not all(0 <= p <= 1 for p in probs) or abs(sum(probs) - 1.0) > 0.01:
            errors.append(
                f"tier probabilities must each be in [0,1] and sum to 1.0 "
                f"(got {sum(probs):.2f})")

    ep = meta.get("entry_plan")
    if v2 and action in NON_HOLDING_ONLY_ACTIONS and not isinstance(ep, dict):
        errors.append(
            f"action '{action}' requires entry_plan {{tranches, invalidation}} "
            f"(required for memos dated on/after 2026-07-05)")
    if isinstance(ep, dict):
        tranches = ep.get("tranches")
        if not (isinstance(tranches, list) and 1 <= len(tranches) <= 3
                and all(isinstance(x, (int, float)) for x in tranches)):
            errors.append("entry_plan.tranches must be a list of 1-3 numbers")
        if not isinstance(ep.get("invalidation"), (int, float)):
            errors.append("entry_plan.invalidation must be a number")

    if v2 and action == "enter" and isinstance(pt, dict):
        base, bear = pt.get("base"), pt.get("bear")
        px = meta.get("price_at_analysis")
        if (all(isinstance(x, (int, float)) for x in (base, bear, px))
                and px > bear):
            rr = (base - px) / (px - bear)
            if rr < MIN_RISK_REWARD:
                errors.append(
                    f"risk/reward {rr:.1f} < {MIN_RISK_REWARD:.0f} — 'enter' fails "
                    f"the precedence-rule-6 gate (base {base} / bear {bear} / "
                    f"price {px})")

    signals = meta.get("signals") or {}
    for k, v in signals.items():
        if k not in SIGNAL_KEYS:
            errors.append(f"unknown signal dimension: {k}")
        elif v not in SIGNAL_VALUES:
            errors.append(f"invalid signal value: {k}={v}")
    if v2:
        missing = [k for k in SIGNAL_KEYS if k not in signals]
        if missing:
            errors.append(
                f"all eight dimensions must be ruled (missing: {', '.join(missing)}) "
                f"— a skipped dimension is a skipped analysis")
        mode = meta.get("mode")
        if mode not in ANALYSIS_MODES:
            errors.append(
                "mode must be 'full' or 'incremental' "
                "(required for memos dated on/after 2026-07-05)")
    return errors


def list_memos(data_dir: Path, ticker: str) -> list[Path]:
    d = Path(data_dir) / "analyses" / ticker
    return sorted(d.glob("*.md")) if d.exists() else []


def load_memo(path: Path, *, holding: bool | None = None) -> dict:
    meta, body = parse_frontmatter(Path(path).read_text(encoding="utf-8"))
    return {"meta": meta, "body": body,
            "errors": validate_memo(meta, holding=holding), "path": str(path)}


def latest_memo(data_dir: Path, ticker: str, *, holding: bool | None = None) -> dict | None:
    memos = list_memos(data_dir, ticker)
    return load_memo(memos[-1], holding=holding) if memos else None


def list_theses(data_dir: Path) -> list[dict]:
    d = Path(data_dir) / "theses"
    paths = sorted(d.glob("*.md")) if d.exists() else []
    out = []
    for p in paths:
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        out.append({"id": p.stem, "meta": meta, "body": body, "path": str(p)})
    return out
