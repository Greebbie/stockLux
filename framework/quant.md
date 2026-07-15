# Luxtock Quant Layer — Specification v1

The quant layer turns the desk's hard data into **deterministic, reproducible
numbers**: a per-ticker feature vector, a transparent setup score, a
portfolio concentration report, and a calibration ledger that grades the
analyst's probabilities once targets mature. Design principles (retail-fit):

1. **No black box.** Every score is a documented piecewise-linear formula
   over named features. Anyone can recompute any number by hand.
2. **Code computes, the analyst only supplies probabilities.** Features and
   scores come from deterministic code over `data/*.json(l)` and memo
   frontmatter — never from an LLM. Two runs on the same files give the
   same output.
3. **Missing data degrades gracefully.** A null feature drops its component
   and renormalizes the remaining weights; every score carries a `coverage`
   fraction so thin data is visible, never papered over.
4. **Scores time entries; they never override rulings.** The composite is a
   *setup quality* number (is this a good moment/price to act), not a
   company quality number. The memo verdict machinery stays in charge.
5. **Uncalibrated until proven otherwise.** All weights below are v1 priors.
   The calibration ledger (`luxtock calibrate`) exists to grade and revise
   them; until it has depth, treat bands as ordinal (higher = better), not
   as probabilities.

## Module 1 — `luxtock/quant.py` (features + setup score)

### Inputs
- `data/quotes.json`, `data/flows.json` (latest snapshot)
- `data/history.jsonl` (per-ticker rows; schema: date, ticker, price,
  fwd_eps, short_pct_float, put_call_oi_ratio, rsi_14, dist_50dma_pct,
  rel_strength_3m, fwd_eps_change_90d_pct, up_last_30d, down_last_30d,
  pt_mean)
- Latest memo frontmatter per ticker under `data/analyses/<T>/` (fields:
  buy_range, price_targets{bear,base,bull,p_*}, price_at_analysis)

### Features (all nullable)
| feature | formula / source |
|---|---|
| price | quotes |
| valuation_gap_pct | (price / buy_range[1] − 1) × 100 — positive = above the good-buy ceiling |
| gap_to_floor_pct | (price / buy_range[0] − 1) × 100 |
| rr_ratio | (base − price) / (price − bear); None if price ≤ bear |
| ev_return_pct | (Σ pᵢ·targetᵢ / price − 1) × 100 |
| rev_90d_pct | quotes revisions.fwd_eps_change_90d_pct |
| rev_breadth | (up − down)/(up + down) over last 30d; None if both 0/None |
| rsi_14, dist_50dma_pct, dist_200dma_pct, atr_pct_14, rel_strength_3m | flows trend block |
| short_pct_float, put_call_oi_ratio, inst_pct | flows |
| rec_mean, n_analysts | quotes analyst block |
| pt_spread_pct | (pt_high − pt_low)/pt_mean × 100 |
| pt_upside_pct | (pt_mean/price − 1) × 100 |
| d14_price_pct, d14_short_pct_float, d14_rsi | change vs the history row closest to 14 days before the latest row (None if < 2 rows or gap < 7 days) |

### Sub-scores (0–100; `clamp01(x) = max(0, min(1, x))`; linear between knots)
**valuation_score** = 0.7·gap + 0.3·ev (renormalize if one missing; None if both missing)
- gap_component from valuation_gap_pct g: g ≤ −20 → 100; −20→0: 100→60;
  0→+15: 60→30; +15→+40: 30→0; g > 40 → 0
- ev_component from ev_return_pct e: e ≤ −20 → 0; −20→0: 0→50; 0→+30:
  50→100; e > 30 → 100

**momentum_score** = 0.6·rev + 0.4·breadth
- rev_component from rev_90d_pct r: r ≤ −20 → 0; −20→0: 0→40; 0→50:
  40→90; 50→100: 90→100; r > 100 → 100
- breadth_component = (rev_breadth + 1)/2 × 100

**positioning_score** (contrarian) = 0.5·crowding + 0.3·putcall + 0.2·short
- crowding_component = clamp01((rec_mean − 1.0)/1.5)·60 +
  clamp01(pt_spread_pct/80)·40 — unanimous buys with a tight PT band score 0
- putcall_component = clamp01(put_call_oi_ratio/3)·100
- short_component = clamp01(short_pct_float/0.15)·100

**trend_score** = 0.5·rsi + 0.3·dma + 0.2·rs
- rsi_component from rsi_14: ≤25 → 50; 25→35: 50→80; 35→50: 80→60;
  50→70: 60→30; 70→85: 30→0; >85 → 0
- dma_component from dist_50dma_pct d: d < −10 → 60; −10 ≤ d < −3 → 80;
  |d| ≤ 3 → 70; 3 < d ≤ 15 → 50; d > 15 → 25
- rs_component: rel_strength_3m > 0 → 65; ≤ 0 → 40

**composite** = weighted mean over the *available* sub-scores, weights
valuation 0.40 / momentum 0.25 / positioning 0.15 / trend 0.20,
renormalized. `coverage` = available features ÷ total features. Bands:
≥70 `strong` / 50–70 `fair` / <50 `weak`; band is `null` when
coverage < 0.35 **or the valuation sub-score is missing** (a name without
a memo-anchored valuation opinion must not wear a band that looks
comparable to fully-scored names — its composite is trend/positioning
only). `components_used` lists which sub-scores fed the composite.

### Output
`data/quant.json`:
```json
{"computed_at": "...", "tickers": {"MU": {"features": {...},
 "scores": {"valuation": ..., "momentum": ..., "positioning": ...,
 "trend": ..., "composite": ..., "band": "fair", "coverage": 0.9}}}}
```
CLI `luxtock quant` prints one row per ticker (price, gap, EV, composite,
band, coverage) and writes the file.

## Module 2 — `luxtock/portfolio.py` (concentration & stress)

- `data/watchlist.json` entries gain an optional `shares` (float ≥ 0) and
  the top level an optional `cash_usd` (float). No cost basis, no P&L —
  unchanged by design.
- Weights: value = shares × price (quotes); weight = value / (Σ values +
  cash_usd). Names with `holding` but no `shares` are listed as "unsized".
- Groupings: by `layer` and by `thesis`; report the largest group weight
  for each.
- Flags (retail heuristics, documented not enforced): single name ≥ 25%
  `caution`, ≥ 35% `warning`; any layer/thesis group ≥ 40% `caution`,
  ≥ 60% `warning`; bear-stress drawdown ≥ 20% `warning`.
- Bear stress: for each sized holding with a latest-memo bear target,
  stressed value = shares × bear; unsized/missing-target names carry at
  current value; report portfolio drawdown % vs current.
- Output: dict from `portfolio_report(data_dir)`; CLI `luxtock portfolio`
  renders a table + flags.

## Module 3 — `luxtock/calibrate.py` (probability ledger)

- A memo is **matured** when memo date + 365 ≤ as_of date and it carries
  full price_targets.
- Realized price at maturity = the history.jsonl row for that ticker
  closest to the maturity date (±14 days; else skip with a note).
- Realized tier: `bear` if realized ≤ (bear+base)/2; `bull` if realized ≥
  (base+bull)/2; else `base`.
- Brier (multi-class) = Σ over tiers (pᵢ − oᵢ)² where o is the one-hot
  realized tier. Lower is better; 0.667 ≈ uninformative uniform prior.
- Path stats over memo→maturity from history: MAE (max adverse excursion
  %) and MFE (max favorable excursion %) vs price_at_analysis.
- **Tracking section** for immature memos: months elapsed, current price's
  position within [bear, bull] as a percentile, and whether it is above/
  below base — so the ledger is useful from day one.
- Output `data/calibration.json` {as_of, matured: [...], tracking: [...],
  aggregate: {n, mean_brier}}; CLI `luxtock calibrate`. Empty-safe: with
  0 matured memos it reports n=0 and the tracking table only.

## v1.1 additions (structural — no weight/knot changes)

1. **Sub-score dispersion.** `score_features` also returns `dispersion`
   (max − min over the available sub-scores, None if < 2 available) and
   `mixed: true` when dispersion ≥ 40 — a composite built from conflicting
   components is flagged, not averaged away silently.
2. **Score history.** Every `luxtock quant` run appends one row per ticker
   to `data/quant_history.jsonl`: {date (UTC), ticker, composite, band,
   valuation, momentum, positioning, trend, coverage, dispersion, price,
   valuation_gap_pct, ev_return_pct, paired_premium_pct}. Append-only, one
   row per ticker per date (same-date rerun replaces that date's rows for
   freshness). This is the dataset that will eventually validate or refute
   the v1 weights.
3. **Score calibration.** `luxtock calibrate` gains a `score_calibration`
   section: joins quant_history rows with prices ≥30/≥90 days later (from
   history.jsonl / quant_history itself) and reports mean forward return
   and hit-rate (return > 0) bucketed by band and by composite quartile.
   Empty-safe; fills as the ledgers deepen. **This — not assertion — is
   where "win rate by setup score" comes from.**
4. **Band-flip alert.** `luxtock check` compares each ticker's two most
   recent quant_history rows and emits an info-level `band_flip` alert
   when the band changed (e.g. fair → strong).

## v1.2 additions (data depth — no weight/knot changes)

1. **Price backfill.** `luxtock backfill [--years N | --days N]`
   (luxtock/backfill.py) fetches daily closes via yfinance — split-adjusted
   but not dividend-adjusted (`auto_adjust=False`), the convention memo
   targets are quoted in — for every watchlist ticker **and its
   relative-strength benchmark** (default SPY), appending price-only rows
   to data/history.jsonl marked `source: "backfill"`. Snapshot-only fields
   (short interest, put/call, revisions, pt_mean) cannot be reconstructed
   retroactively and stay absent. Non-destructive by construction: existing
   (date, ticker) rows always win, and rows dated today or later are never
   written — the refresh snapshot owns the current day. Benchmark rows are
   inert for quant (it iterates the watchlist only) and feed calibration.
   Backfilled watchlist-ticker rows, however, are not inert for quant:
   once history.jsonl has a row ≤14d back, `_d14` picks it up and
   `d14_price_pct` (and coverage) activate immediately, so composite/
   coverage can shift on first-backfill day — that's better data, not a
   scoring change.
2. **Benchmark-relative score calibration.** Every `score_calibration`
   bucket additionally reports `n_excess`, `mean_excess_return_pct` and
   `excess_hit_rate`: the same forward returns measured against the
   ticker's watchlist benchmark over the identical window. The absolute
   origin price is the quant_history row's own recorded price (no
   matching); the forward legs (stock and benchmark) use nearest-at/after
   matching; the benchmark origin additionally requires a nearest-at/after
   match within 7 days of the row's date, else the row falls out of the
   excess stats (absolute fields are unaffected). Once the ledger has
   depth, **excess** hit-rate — not absolute — is the number that
   validates or refutes the v1 weights: a bull market must not be allowed
   to grade the bands.
3. **Daily pass.** `luxtock daily` = refresh → 30-day backfill top-up
   (gap-healing; idempotent thanks to (date, ticker) dedup) → quant →
   calibrate → check summary. Exits 0 even when alerts fire (scheduler-
   friendly); `luxtock check` keeps its exit-1 contract for cron use.
   scripts/register_daily_task.ps1 registers a Windows scheduled task
   running it (default 09:00 local — after the US close for an Asia
   timezone); the user runs the registration script manually.

## Memo contract hook

Memos written on/after 2026-07-12 must cite the quant snapshot (composite,
band, valuation_gap, coverage) in the Summary section and in the entry plan
when one exists, sourced from `data/quant.json` — the analyst reads the
numbers, never recomputes them. Scores inform timing and sizing language;
they never override the verdict precedence rules.

## v1 implementation notes (behavior the code locks in)

- `coverage` denominator = the 22 keys of `quant.FEATURE_KEYS`.
- Band gating requires a non-null valuation sub-score (2026-07-12 review
  fix); `components_used` was added for composite transparency; `_d14`
  matches history rows strictly by ticker key.
- `paired_premium_pct` (2026-07-12): watchlist entries may carry a
  `paired` config ({ticker, ratio, currency}) — refresh then computes
  parity_usd = paired_price × fx_usd × ratio and premium_pct vs. the US
  price, logs it to history, and quant carries it as feature #23.
  **Informational only — not in any sub-score** (per governance); it is
  the timing indicator for dual-listed names (e.g. an ADR vs. its
  home-market line).
- `luxtock check` (2026-07-12): stateless price alerts vs the latest
  memo's entry tranches / invalidation / trim threshold (1.25× good-buy
  ceiling, holding only) / bear / bull, plus portfolio flags; exit code 1
  when any alert is active. Re-fires every run by design.
- Deferred to v2 pending calibration evidence (governance rule below):
  put/call direction logic (high put OI in a crowded long book is hedging,
  not contrarian pessimism — consider using the d14 *change* instead of
  the level), ATR-normalized valuation gap (a −20% gap means less on an
  11%-ATR name), revision *deceleration* (d14 of fwd-EPS estimates) in the
  momentum score, and an outlier-robust PT-spread (min/max spread is
  fragile to one stale analyst).
- `crowding_component` is atomic: if either `rec_mean` or `pt_spread_pct`
  is missing, the whole component drops and positioning renormalizes over
  put/call + short.
- `dma_component` and `rs_component` are step functions (no interpolation);
  gap/ev/rev/rsi components interpolate linearly with flat extrapolation.
- d14 deltas pick the single row closest to 14 days before the latest row,
  then require that row to be ≥ 7 days away; otherwise all three deltas
  are null.
- Calibration: "full price_targets" (matured ledger) requires bear/base/
  bull **and** all three probabilities; tracking needs only bear/base/bull.
  A matured memo whose realized price has no history row within ±14 days
  stays in the ledger with null grades and an explanatory `note`, excluded
  from the aggregate. MAE/MFE are signed (min/max % excursion vs.
  `price_at_analysis`).
- Portfolio: a sized holding with no quote price degrades to "unsized";
  cash sits unchanged on both sides of the bear stress; thresholds are
  inclusive (≥).

## Calibration governance

Weights and knots above may only change when `data/calibration.json` has
≥ 20 matured memos and a change is justified by bucket hit-rates in a
retrospect report — never mid-flight because a score "feels wrong."
