# Luxtock Screen Layer — Specification v1

`luxtock screen` is a **candidate discoverer**: a deterministic, market-wide
funnel that surfaces *good businesses the market has beaten down or left for
dead*. It hunts two related shapes through one funnel:

- **beaten-down** — deep drawdown, earnings base intact ("UNH-2025 /
  MU-2026 shape": price crushed, estimates flat-to-rising);
- **quality-discount** — drawdown may be modest, but an obviously strong
  business (high margins/returns, growing revenue and EPS) trades at a
  multiple its quality doesn't explain ("GOOGL-2024 shape": pre-TPU-rerate,
  narrative overhang compressing the multiple while the machine kept
  earning);
- **hypergrowth** — no (or negative) earnings base, but revenue compounding
  ≥30%/yr and a growth-adjusted sales multiple the market has knocked down
  ("NBIS shape": early AI-infra builds where the value is the growth curve
  and the buildout, not this year's EPS). Scored by a separate formula
  (below) because every earnings-anchored metric is undefined here —
  consistent with the methodology's "no earnings base → EV/ARR against
  peers" rule.

It produces *candidates with proxy scores*, never verdicts:
a screened name has no bear/base/bull targets and no eight-dimension analysis
until it runs through the analyze playbook. The screen is to `/lux-discover`
what a metal detector is to an excavation.

Design principles (inherited from the quant layer, `framework/quant.md`):

1. **No black box** — every score is a documented piecewise-linear formula.
2. **Code computes** — no LLM anywhere in the funnel; two runs on the same
   data give the same output.
3. **Missing data degrades gracefully** — null features drop their component
   and renormalize; every row carries `coverage`.
4. **Candidates, not calls.** The output table must always carry the notice:
   *candidates only — run the analyze playbook before any verdict*. The
   ten-state machinery and the R/R ≥ 2 gate live entirely downstream.

**Honesty note — what shape this screen can and cannot catch.** The screen
finds the *estimates-diverging-from-price* shape: an intact or rising forward
earnings base against a −30%+ drawdown (UNH 2025, MU mid-2026). It is
structurally blind to the *pre-turnaround deep value* shape (AMD 2015:
negative earnings, story-driven recovery) — those names are excluded by the
`no_earnings_base` gate **by design**, because without a forward earnings
base every "cheap" signal in this funnel is undefined. Turnarounds are
narrative work; no deterministic screen should pretend otherwise.

**Honesty note — cyclicals fool the value components.** A cyclical at peak
earnings prints a *low* forward P/E and a *high* pe_compression score at
exactly the wrong moment (MU at 5.7× fwd in July 2026 is the textbook
case) — low-multiple-at-peak is the classic cyclical trap, and this screen
cannot see cycle position. Any candidate from a known cyclical industry
(memory, shipping, commodity chemicals, E&P…) must get the methodology's
dual-anchor treatment (peak *and* normalized EPS) in analysis before its
screen score is taken at face value; agents citing such rows must say so.

**Honesty note — the hypergrowth track is the most speculative tier.** It
has no earnings anchor at all: EV/Sales multiples are sentiment-fragile,
revenue growth decays without warning, and dilution/cash-burn can void the
equity case even when the buildout succeeds. Hypergrowth rows carry the
same score scale but are *not* comparable in confidence to the other two
tracks; any downstream citation carries low confidence by default, and a
real analysis must do the methodology's EV/ARR-vs-peers work.

**Honesty note — `rr_proxy` is sell-side-derived.** True framework R/R
requires analyst-built bear/base targets. The screen's `rr_proxy` =
(pt_mean − price) / (price − pt_low) substitutes *sell-side consensus* for
both legs. Sell-side targets lag and skew optimistic (MU's pt_mean sat +75%
above spot through a −32% drawdown), so `rr_proxy` is a **screening signal
only** and must be labeled as such wherever it is displayed or cited.

## Module — `luxtock/screen.py`

### Inputs

- `data/universe.json` — the scan universe: `{as_of, source, tickers: [...],
  extra_tickers: {note, tickers: [...]}}`. `tickers` is index membership
  (S&P 500 + Nasdaq-100 snapshot, seeded from `examples/universe.json`);
  `extra_tickers` is a **user-curated side list** for names outside index
  membership (NBIS-type US listings, recent IPOs) — merged into the scan,
  deduped against the main list. The seed ships with a small starter set of
  verified-listed growth names and a note saying the list is the user's to
  maintain. Updating either list is a manual, infrequent act;
  `luxtock screen --universe <path>` overrides. Snapshots older than ~90
  days trigger a staleness warning in the CLI and dashboard; regeneration
  stays a manual, user-driven step.
- `data/watchlist.json` — watchlist tickers are **excluded** from results
  (already covered by the desk).
- Network: yfinance — Stage A batch price history, Stage B per-ticker info.

### Stage A — bulk price funnel (cheap)

Batch-download ~1y of daily closes for the whole universe (chunked batch
calls, e.g. 100 tickers per request). Per ticker compute:

| feature | formula |
|---|---|
| price | last close |
| drawdown_pct | (price / max(close, 252d) − 1) × 100 |
| dist_200dma_pct | (price / mean(close, 200d) − 1) × 100 |
| ret_6m_pct | (price / close[−126] − 1) × 100 |

**Gate A** (all must hold): drawdown_pct ≤ −15 (CLI `--min-drawdown`,
default 15, stored positive — wide on purpose: the quality-discount shape
lives in the −15…−30 band and is separated from noise by Stage B quality
metrics, not by drawdown depth); price ≥ $5; ≥ 200 trading days of history.
At most `--max-deep` (default 100) survivors proceed to Stage B (cap exists
for network hygiene; when it binds, the CLI must say how many were dropped —
no silent truncation). Each survivor is tagged `track: beaten_down`
(drawdown ≤ −30) or `track: quality_discount` (−30 < drawdown ≤ −15); both
tracks share one scoring formula. **The cap reserves 30% of its slots for
the quality_discount band** (deepest-first within each band; an unused
reservation backfills the other band): in a stressed market the ≥30% pool
alone can exceed the cap, and pure depth-ranking would crowd the
GOOGL-2024 shape out of Stage B entirely — the reservation keeps both
target shapes in the funnel at all times.

### Stage B — per-ticker fundamentals (throttled)

For each survivor fetch the same field set `luxtock refresh` extracts:
fwd_eps, ttm_eps, fwd_pe, ttm_pe, market_cap, analyst block (pt_mean,
pt_low, pt_high, n_analysts, rec_mean), revisions block (fwd_eps_change_90d_pct,
up_last_30d, down_last_30d), next_earnings — plus the **quality fields**
(the 底盘): operating_margin, return_on_equity, revenue_growth (yfinance
`operatingMargins`, `returnOnEquity`, `revenueGrowth`, each as a fraction).
Throttle with a small inter-ticker sleep and one retry; a ticker whose
fetch fails is kept in the output with `fetch_failed: <reason>` and null
fundamentals (visible, never silently dropped).

Additionally fetch the **hypergrowth fields**: enterprise_value
(`enterpriseValue`), total_revenue (`totalRevenue`), gross_margin
(`grossMargins`, fraction), total_cash (`totalCash`), free_cashflow
(`freeCashflow`).

Derived: `rev_breadth` = (up − down)/(up + down) (None if both 0/None);
`rr_proxy` = (pt_mean − price)/(price − pt_low), None if pt data missing or
price ≤ pt_low; `pe_compression` = fwd_pe / ttm_pe (None if either missing
or ttm_pe ≤ 0); `eps_growth_pct` = (fwd_eps / ttm_eps − 1) × 100 (None
unless both present and ttm_eps > 0); `peg_like` = fwd_pe /
max(eps_growth_pct, 1) (None if either missing or fwd_pe ≤ 0);
`ev_sales` = enterprise_value / total_revenue (None unless both present and
total_revenue > 0); `gs_like` = ev_sales / (revenue_growth × 100) (None
unless both present and revenue_growth > 0); `runway_years` =
total_cash / |free_cashflow| when free_cashflow < 0 (None when FCF ≥ 0 or
either missing — a non-burner has no runway question).

### Track assignment (finalized in Stage B)

Stage A tags by drawdown alone; Stage B upgrades a survivor to
`track: hypergrowth` when **fwd_eps is None or ≤ 0 AND revenue_growth ≥
0.30** — the name has no earnings base but is compounding revenue ≥30%/yr.
All other survivors keep their Stage-A tag (`beaten_down` /
`quality_discount`). A name *with* an earnings base and high growth stays
in the standard tracks — `peg` already rewards it there.

### Hard disqualifiers (value-trap gates)

Evaluated per ticker; each failure appends a named flag. A ticker with any
flag is `disqualified: true` — kept in `screen.json` with its flags (the
near-misses are information) but excluded from the ranked CLI table.

Standard tracks (`beaten_down` / `quality_discount`):

| flag | condition | rationale |
|---|---|---|
| `no_earnings_base` | fwd_eps missing or ≤ 0 | cheap-on-earnings is undefined without earnings |
| `estimates_collapsing` | rev_90d_pct < −10 | cheap-and-deteriorating is a value trap, not an opportunity (discover playbook rule) |
| `revision_exodus` | rev_breadth < −0.5 | the sell side is walking out |
| `too_small` | market_cap < $2B | liquidity/coverage floor |

`track: hypergrowth` replaces `no_earnings_base` (definitionally absent
there) with burn-side traps; `revision_exodus` and `too_small` still apply,
`estimates_collapsing` does not (EPS-change percentages are meaningless on
a negative base):

| flag | condition | rationale |
|---|---|---|
| `no_runway` | free_cashflow < 0 and runway_years < 0.75 | the buildout dies of cash before the curve pays |
| `growth_unpriced` | gs_like missing (no ev_sales or no positive growth) | without a growth-adjusted multiple the track has no valuation leg at all |
| `revision_exodus` / `too_small` | as above | as above |

### Score — `depression_score` (0–100; linear between knots; renormalize on missing)

Weighting philosophy: **valuation + quality carry half the score (0.25 +
0.25)** — the target is "obviously good business, obviously mispriced", not
"whatever fell furthest". Drawdown depth is deliberately a minor input.

**quality_component** (weight 0.25 — the 底盘) =
0.4·margin + 0.3·roe + 0.3·growth (renormalize on missing; None if all
missing). Inputs as percentages (fraction × 100):
- margin from operating_margin m: m ≤ 5 → 20; 5→15: 20→60; 15→30: 60→90;
  m > 30 → 100
- roe from return_on_equity e: e ≤ 5 → 20; 5→15: 20→60; 15→25: 60→90;
  e > 25 → 100 (None if equity ≤ 0 upstream)
- growth from revenue_growth g: g ≤ 0 → 10; 0→10: 40→70; 10→25: 70→100;
  g > 25 → 100 (discontinuity at 0 is intentional: shrinking revenue is a
  different regime, not a lower shade of growing)

**value_component** (weight 0.25) = 0.5·compression + 0.5·peg
(renormalize if one missing):
- compression from pe_compression f: f ≤ 0.5 → 100; 0.5→0.8: 100→70;
  0.8→1.0: 70→50; 1.0→1.3: 50→20; f > 1.3 → 0. (A forward multiple far
  below trailing = the market refuses to pay for earnings it can see.)
- peg from peg_like x: x ≤ 0.8 → 100; 0.8→1.2: 100→70; 1.2→2.0: 70→40;
  2.0→3.0: 40→10; x > 3 → 0. (GOOGL-2024 door: growth priced as if absent.)

**resilience_component** (weight 0.25) = 0.6·rev + 0.4·breadth
(renormalize if one missing):
- rev from rev_90d_pct r: r ≤ −10 → 0; −10→0: 0→50; 0→+15: 50→85;
  +15→+50: 85→100; r > 50 → 100
- breadth = (rev_breadth + 1)/2 × 100

**depth_component** (weight 0.15) from drawdown_pct d (negative). Explicit
knots (d → score), linear between: −15 → 25, −30 → 40, −45 → 80,
−60 → 100 (peak), −75 → 70; hard floor d ≤ −75 → 40 (the cliff at −75 is
intentional — beyond it drawdown reads as damage, not opportunity; the
curve approaches 70 as d → −75 from above, then drops to the floor).
Deeper is better *until* it reads as damage; the shallow band scores low
here and must earn its place through quality and value.

**rr_proxy_component** (weight 0.10) from rr_proxy x:
x ≤ 0.5 → 10; 0.5→1: 10→40; 1→2: 40→70; 2→4: 70→100; x > 4 → 100.
Sell-side proxy; see honesty note.

`depression_score` = weighted mean over available components, weights
renormalized; `coverage` = available components' weight share. Bands:
≥ 75 `strong` / 55–74 `fair` / < 55 `weak`; a row with coverage < 0.5
reports band `n/a`.

### Hypergrowth-track score (separate formula, same 0–100 scale)

Rows with `track: hypergrowth` replace the standard components entirely
(the standard ones are earnings-anchored and undefined here). All knots
linear between, flat beyond ends unless a cliff is stated:

- **gs_component** (weight 0.30) from gs_like x: x ≤ 0.08 → 100;
  0.08→0.15: 100→70; 0.15→0.30: 70→40; 0.30→0.50: 40→10; x > 0.5 → 0
  (cliff). The NBIS test: EV/S ~12 on ~100% growth → 0.12 → ~80; a
  fashionable EV/S 60 on 40% growth → 1.5 → 0.
- **growth_intensity** (weight 0.25) from revenue_growth g (pct):
  g = 30 → 50 (eligibility floor); 30→60: 50→80; 60→100: 80→100;
  g > 100 → 100.
- **margin_component** (weight 0.15) from gross_margin m (pct): m ≤ 20 → 10
  (cliff — sub-20% gross margin is a different business, not a lower shade);
  20→50: 30→70; 50→80: 70→100; m > 80 → 100.
- **runway_component** (weight 0.10): free_cashflow ≥ 0 → 100; else from
  runway_years y: y ≤ 0.5 → 0; 0.5→1: 0→30; 1→2: 30→60; 2→3: 60→80;
  y > 3 → 80 (a burner never scores 100 — the cap is intentional).
- **depth_component** (weight 0.10) and **rr_proxy_component** (weight
  0.10): same functions as the standard track.

Same renormalization, coverage, and band rules. See the hypergrowth
honesty note: same scale, lower confidence tier by construction.

**Behavioral note (falling knives):** the `estimates_collapsing` gate means
a name in the teeth of its estimate cuts (UNH mid-2025 at the worst prints)
is excluded *while the knife is falling* and only surfaces once revisions
stabilize — which is the intended entry geometry, catching the knife after
it sticks, at the cost of never bottom-ticking.

## Outputs

- `data/screen.json` (atomic write): `{computed_at, universe_as_of,
  universe_size, stage_a_survivors, stage_b_cap_dropped, results: [...]}` —
  every Stage-B ticker with features, flags, track, components, score,
  coverage, disqualified. Read-only for agents: cite, never recompute.
- `data/screen_history.jsonl` (append-only): one compact line per
  *qualified* candidate per run (date, ticker, track, price, drawdown,
  score, band) — `screen.json` is overwritten every run, this ledger is
  the memory. It exists so a future retrospect can grade the screen's hit
  rate the way `calibrate` grades memo targets. Filter it (grep by ticker,
  tail by date); never load whole. Read-only for agents.
- CLI table: top `--top` (default 15) qualified standard-track rows —
  ticker, price, drawdown, track, rev_90d, fwd P/E, rr_proxy, score [band],
  flags. Qualified `hypergrowth` rows print in their **own short block
  underneath** (their columns differ: ticker, price, drawdown, revenue
  growth, EV/S, gs, runway, score [band]) with a one-line header naming the
  track and its speculative tier. Both blocks are followed by the two
  mandatory notices: *candidates only — not analyzed, no verdicts*;
  *rr_proxy is sell-side-derived, screening signal only*. `rr_proxy` prints
  as `>10` beyond the display cap (an artifact of `pt_low` sitting near
  spot); `screen.json` keeps the uncapped value.
- Dashboard Screen tab: renders `screen.json` read-only, mirroring the CLI's
  grouping (standard track, hypergrowth block, collapsed disqualified panel)
  and both mandatory notices — same candidates-only framing, no watchlist
  mutation from this view.

## Downstream contract (agent-facing)

A screened candidate enters the desk only through the existing doors:
`luxtock add <T>` onto the watchlist, then the analyze playbook produces the
memo (real targets, real R/R, ten-state verdict). Agents may cite
`screen.json` numbers verbatim in a discover report but must label
`rr_proxy` as a sell-side proxy and must never present `depression_score`
as a verdict or a probability. The screen is uncalibrated v1 priors
throughout; the calibration ledger does not grade it (nothing here is a
probability), but retrospects may grade *hit rate* informally once
candidates mature.
