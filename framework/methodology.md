# StockLux Analysis Methodology

Any agent working in this repository must read this file in full before doing
any analysis. It defines the output discipline, the eight analysis dimensions,
the ten-state verdicts, and the memo data contract. **This is analysis, not
advice — call it exactly as it is.**

## Output discipline (violating any rule invalidates the analysis)

1. **No vague conclusions.** Every conclusion must land on numbers: the
   scenario EPS growth range, the scenario P/E, the good-buy price range, and
   where the current price sits relative to that range (with a deviation
   percentage). Phrases like "worth watching" or "has potential" are banned.
2. **Multiples must be justified.** The good-buy range = scenario EPS × the
   multiple the market will still pay once the thesis has played out:
   - Cyclical peak earnings: 8–12x (never 25x for peak cyclical earnings)
   - Utilities / regulated assets: 18–22x
   - Stable growers (10–20%/yr): 20–28x
   - High growth (30%+/yr): 30–50x
   - No earnings base (e.g. high-growth cloud): use EV/ARR or EV/Backlog
     against peers instead, and note this explicitly
   - **Rate-regime flex**: in a rising real-yield environment take the lower
     half of the chosen multiple band; in an easing environment the upper
     half is defensible. Say which half you used and why (one line).
   - **Cyclicals get two anchors.** Peak-EPS × 8–12x alone overstates the
     good-buy range late in a cycle — "below range" can mean *cheap*, or it
     can mean *the market is already pricing normalized earnings while you
     price the peak*. Also compute normalized (mid-cycle) EPS × 12–16x, show
     both anchors, and take the **stricter lower bound** as the good-buy
     floor. If the two anchors disagree by more than ~40%, cap the
     `valuation` ruling's confidence at low — the disagreement *is* the
     cycle-position uncertainty.
3. **Keep four claim types clearly labeled:**
   - **[FACT]** verifiable, with source and date
   - **[CONSENSUS]** market/sell-side consensus (e.g. consensus forward EPS)
   - **[INFERENCE]** your own judgment for this analysis, must carry a
     confidence level (high/medium/low) + one sentence on what would
     disprove it
   - **[INFERENCE-USER]** a view the user has expressed, mapped into the
     dimension it belongs to (see "User views & divergence" below), with a
     confidence level and the observable that would settle it. Never adopt a
     user view silently (that launders it into your own conclusion) and never
     drop it silently (that steamrolls the user) — it must appear under this
     label and be ruled on.
4. **Look at the right line.** Bookings-type companies (GPU cloud, power
   equipment) are judged on orders/backlog/utilization, not reported revenue;
   cyclicals are judged on unit pricing and supply growth, not static P/E.
5. **Every memo must include** a thesis-killer (what happening means the
   narrative is dead) and a review_trigger (what happening means the analysis
   should be rerun), written as a specific, observable event.
6. **Cover all dimensions.** Give a signal ruling for each of the eight
   dimensions below. If a dimension genuinely has no signal, write "no_signal"
   and give one sentence explaining why — do not silently skip it; a blank is
   information too.
7. **Price figures must always be quoted from `data/quotes.json`** (to stay
   consistent with the dashboard) — do not look up prices online yourself. If
   `fetched_at` is more than 24 hours old, run `stocklux refresh` yourself
   before continuing (prompt the user only if your environment cannot execute
   shell commands). The only exception: candidates
   surfaced by the discover flow that are **not yet on the watchlist** are not
   in quotes.json, so they may be looked up online with source and date cited;
   once added to the watchlist, quotes.json becomes the price of record.
8. **Price targets carry probabilities; entries carry a plan.**
   - Each of bear/base/bull carries a probability (`p_bear`/`p_base`/
     `p_bull`, summing to 1.0), each an **[INFERENCE]** with one line of
     justification. Round numbers (0.2/0.55/0.25) are fine; fake precision
     is not. These are what the retrospect calibrates against.
   - **Risk/reward gate**: risk/reward = (base − current price) / (current
     price − bear). An `enter` verdict requires risk/reward ≥ 2 (see
     precedence rule 6) — a discount that doesn't pay for the bear tail is
     not an entry edge.
   - Every memo whose action is `enter` or `wait_for_pullback` must carry an
     **entry plan**: 1–3 tranche price levels tied to observable structure
     (good-buy boundary, prior consolidation, moving-average distance from
     the `trend` block in `data/flows.json`), plus an **invalidation price**
     below which the market is saying something the eight dimensions missed
     — hitting it forces a re-analysis, never an automatic exit.

## The eight analysis dimensions

Each dimension gets one line with a signal ruling (`favorable` / `neutral` /
`unfavorable` / `no_signal`) plus one sentence of justification and a
confidence level.

| key | dimension | question to answer |
|---|---|---|
| `chain` | supply chain & pass-through | Where does it sit across the full upstream/downstream chain; when the thesis plays out, how much of the benefit passes through to this name (write the explicit pass-through math) |
| `narrative` | logic chain & narrative | What is the company's own narrative, what is the payload assumption, how tightly is it coupled to the user's thesis |
| `fundamentals` | fundamental quality | Revenue/gross margin/guidance trend, backlog, customer concentration, balance sheet |
| `valuation` | valuation | Scenario EPS → scenario P/E → good-buy range (with multiple justification) → pass/fail |
| `flows` | flows | Read the hard data in `data/flows.json` + search online for 13F reporting/block trades/unusual options activity, then synthesize whether smart money looks like it is accumulating or distributing |
| `sentiment` | sentiment & crowding | Discussion volume and direction on X/Reddit/media, sell-side expectation crowding — "everyone already knows this" means the buy case is pre-spent |
| `competition` | competition & substitution | Peers at the same layer, technology-path risk, whether a cheaper substitute exists at the same layer |
| `macro` | macro & policy | Rates/export controls/subsidies/regulation — only what is directly relevant to this name |

**Honesty note on the flows dimension**: 13F filings lag roughly 45 days, short
interest updates biweekly, and there is no dark-pool or real-time flow data.
"Smart money is quietly accumulating" is always a multi-signal
**[INFERENCE]** and must carry a confidence level — never write it as
**[FACT]**. The `accumulation_hint` in `flows.json` is only a price/volume
pattern hint, not a conclusion.

**Honesty note on the sentiment dimension**: discussion volume and crowding
are always a multi-source-sampled **[INFERENCE]**, never a verifiable fact —
they must carry a confidence level and cite the sampling sources; never write
them as **[FACT]**.

**Timing-layer note (hard data, not a ninth dimension)**: `data/flows.json`
carries a per-ticker `trend` block (distance to 50/200-dma, RSI-14, ATR% of
price, 3-month relative strength vs. the watchlist entry's `benchmark` —
sector ETF like SMH/XLU where set, SPY otherwise) and `data/quotes.json`
carries a `revisions` block (90-day change in next-FY consensus EPS,
analysts revising up/down in the last 30 days), an `analyst` block (price
targets mean/high/low, analyst count, recommendation mean — a narrow
high/low spread with a unanimous recommendation is quantifiable crowding
evidence for the `sentiment` ruling), and `next_earnings` (the next earnings
date, for the catalyst map). All are **[FACT]**-grade inputs: cite the
`trend` block in the entry plan and the `flows` ruling; cite the `revisions`
block in the `fundamentals` and `sentiment` rulings — sustained upward
revisions with a flat price is a classic good entry; downward revisions
against a rising price is an explicit warning on the price targets. These
inputs size and time the entry; they never override a valuation ruling.

**History note**: every `stocklux refresh` appends per-ticker snapshots
(price, short interest, revision momentum, trend readings) to
`data/history.jsonl`. Once it has depth, cite *changes* from it — "short
interest +40% in two weeks" is a stronger flows signal than any single
print — and the retrospect can grade price paths (MAE/MFE) from local data
instead of online lookups for the covered window.

## User views & divergence (merging the user's own views)

The user's private views are **inputs to the eight dimensions, not a ninth
dimension**. Whenever the user has expressed a view on a name (in
conversation, in the watchlist `note`, or in a thesis file), the analysis
must:

1. **Map it.** Attach the view to the dimension(s) it actually belongs to
   ("their new product is underrated" → `narrative`/`fundamentals`; "insiders
   are buying" → `flows`; "the competitor is falling behind" → `competition`),
   recorded as **[INFERENCE-USER]** side by side with your own ruling for
   that dimension.
2. **Rule on conflicts.** If the user's view and your ruling for that
   dimension disagree, classify the divergence — the same test the thesis
   audit uses:
   - **edge**: the user knows or has thought through something the market
     (and your evidence) hasn't priced — say what that something is;
   - **blind spot**: the market knows something the user's view ignores —
     say what and cite it.
   Label the classification **[INFERENCE]** + confidence. If you cannot
   classify it, say "unresolved" — do not fake a ruling.
3. **Make it decidable.** For every divergence, name the specific observable
   (a number, a filing, an event) that would settle who is right, and fold it
   into the memo's `review_trigger` so the disagreement gets re-tested
   instead of forgotten. **Divergences expire**: one carries forward at most
   180 days (or two full re-analyses, whichever comes first) waiting for its
   observable. If the observable still hasn't printed by then, the next memo
   rules it "unresolved — expired" and drops it from the carry-forward list;
   an observable that never arrives was not decidable, and undecidable
   disagreements are noise, not signal. The user can always re-assert the
   view to restart the clock — re-assertion is new information.
4. **Record it.** Any memo where at least one user view conflicts with a
   dimension ruling must carry a **Divergence** section in the body (see the
   body structure below). If user views exist and all agree with the
   analysis, one line in the summary suffices ("user view on X concurs,
   absorbed into `narrative`").

The same discipline applies in reverse: if a thesis audit downgrades a
thesis and the user disagrees, the user records a `## Rebuttal <date>`
section in the thesis file, and the next audit must answer it point by point
(see the audit playbook). The framework wins arguments with evidence, not by
ignoring the other side.

### The thesis itself is under test

The same skepticism applies one level up. A thesis file is the user's
**hypothesis, not a verified premise**. The analyze playbook is *conditional*
on it — scenario EPS, the good-buy range, and the price targets all assume
its pass-through math — and `thesis_health: intact` in a memo only means "no
kill evidence has printed yet"; it is never a ruling that the thesis is
right. What earns a thesis its status is the audit playbook (steel-man →
payload dissection → consensus check → adverse scenarios → ruling), and
audits go stale:

- **Freshness window: 90 days.** If the thesis's `last_audited` is null, or
  more than 90 days before the memo's date, the memo's overall `confidence`
  caps at **medium** (folded into confidence propagation below), and the
  summary must carry the explicit line "confidence capped: thesis
  unaudited" (or "audit stale"). A high-conviction call cannot stand on an
  unverified premise, no matter how favorable the conditional evidence.
- The dashboard cross-checks this deterministically and flags
  `confidence: high` memos sitting on unaudited/stale theses as format
  violations.
- The scan playbook flags overdue audits so the gate prompts an audit
  rather than silently degrading every memo forever.

**Thesis lifecycle (keeping the shelf clean).** A thesis stays in the
audit rotation only while at least one watchlist name is attached to it.
When a thesis is ruled `dead` and its names have been exited/removed — or
the user has simply moved on — set `status: retired` in its frontmatter
(with one line on why, and the date). Retired theses are skipped by the
audit gate, the scan, and `/lux-run`; the file itself is never deleted —
it is the record of a bet and its outcome, which the retrospect may still
cite. Un-retire by setting the status back and re-auditing before any new
analysis leans on it.

## The ten-state verdict (legal values for `action`, used verbatim)

`enter` `wait_for_pullback` `hold` `watch_only` `good_company_bad_price`
`crowded_theme` `thesis_broken` `no_edge` `trim` `exit`

**Position context.** The watchlist entry's optional `holding` flag (boolean,
missing = false) says whether the user actually owns the name. `hold`,
`trim`, and `exit` are only legal verdicts when `holding` is true; `enter`
and `wait_for_pullback` are only legal when it is false. The remaining five
states are legal either way. The flag is deliberately minimal — no share
counts, cost basis, or P&L (out of scope by design); it exists solely so the
verdict answers the question the user actually faces.

### Verdict precedence (how the signal table maps to the ten states)

Apply these rules **in order — the first rule that fires decides**. Do not
average signals into a score; precedence, not arithmetic, is what makes two
runs of the same evidence land on the same verdict.

1. `thesis_health: dead` → `thesis_broken`. No other signal can override.
   If holding, the memo must still say what to do with the position
   (normally `exit` logic, stated in the body).
2. The `verdict` field (price vs. good-buy range — not the `valuation`
   dimension signal) is `above_range` → this caps the call: not holding →
   `wait_for_pullback` (fundamentals still favorable) or
   `good_company_bad_price` (quality name, price does the damage); holding →
   at most `hold`, and `trim` when the price sits more than ~25% above the
   top of the good-buy range. Favorable signals elsewhere cannot lift a
   verdict past this cap — "buying here means prepaying for growth beyond
   the scenario."
3. Everything looks favorable **and** `sentiment` says the story is fully
   crowded **and** the crowding shows up in the price (the `verdict` field
   is `in_range` upper-third or `above_range`) → `crowded_theme`. A correct,
   fully-priced narrative has no entry edge left. If the price sits below
   the good-buy range, the buy case is by definition not pre-spent — crowded
   positioning is then a volatility risk priced into confidence, not a
   verdict state.
4. No differentiated view: every dimension ruled `neutral`/`no_signal`, or
   the only `favorable`/`unfavorable` rulings carry low confidence — and no
   **[INFERENCE-USER]** edge survives scrutiny → `no_edge`. Not every name
   owes you a view. (A single decisive ruling — favorable or unfavorable
   with medium+ confidence — is enough to keep this rule from firing.)
5. `thesis_health: damaged` → holding: `trim` (or `exit` if the payload
   assumption itself is what broke); not holding: `watch_only`.
6. Otherwise (thesis intact/weakening, `verdict` field not `above_range`):
   - `verdict: below_range` + `chain`/`fundamentals` favorable → not
     holding: `enter` **only if risk/reward ≥ 2** ((base − current) /
     (current − bear)); below that the discount isn't paying for the bear
     tail — write `watch_only` and state the ratio. Holding: `hold`
     (adding is a sizing decision the framework doesn't make).
   - `verdict: in_range` → holding: `hold`; not holding: `watch_only`, or
     `wait_for_pullback` when the price sits in the upper third of the range.

**Confidence propagation.** The memo's overall `confidence` may not exceed
the confidence of the `valuation` ruling, nor the confidence of the
dimension carrying the thesis's payload assumption, **nor medium when the
thesis's `last_audited` is null or more than 90 days before the memo date**
(see "The thesis itself is under test"). A high-conviction verdict built on
a low-confidence payload — or an unaudited premise — is a contract
violation.

## Memo data contract

Memos are written to `data/analyses/<TICKER>/<YYYY-MM-DD>.md`. The frontmatter
must include:

```yaml
---
ticker: "ON"                   # required — quote tickers YAML would parse as
                                #   booleans (ON, NO, YES, etc.), e.g. "ON"
date: 2026-07-04               # required, today's date
thesis: ev-adoption            # required, filename under data/theses/
layer: power-semis             # bottleneck layer
action: watch_only             # required, one of the ten states (verbatim)
confidence: low                # required: high / medium / low
mode: full                     # required: full / incremental — incremental only under the
                               #   delta-scan conditions, and at most 2 in a row; the next
                               #   analysis after that must be a full rewrite
buy_range: [38, 55]            # good-buy range; may be null if there is no earnings base
price_targets:                 # 12-month bear/base/bull forecast (required unless there is no earnings/valuation base at all, then null)
  bear: 30                     #   bear: low-scenario EPS × low end of the multiple range (state the assumption)
  base: 48                     #   base: mid-scenario EPS × reasonable multiple — the most likely price
  bull: 66                     #   bull: high-scenario EPS × high end of the multiple range
  p_bear: 0.25                 #   tier probabilities, must sum to 1.0 — each an [INFERENCE]
  p_base: 0.55                 #   with one line of justification in the body
  p_bull: 0.20
  horizon: 12mo
multiple_basis: "8-12x cyclical EPS"   # multiple justification
entry_plan:                    # required when action is enter / wait_for_pullback; null otherwise
  tranches: [50, 44, 40]       #   1-3 staged levels tied to observable structure (cite it in the body)
  invalidation: 36             #   below this the market knows something the dimensions missed → forced re-analysis
price_at_analysis: 52          # required, taken from quotes.json
verdict: in_range              # required: below_range / in_range / above_range
thesis_health: intact          # required: intact / weakening / damaged / dead
top_risks: [ev_demand_stall, overcapacity]
review_trigger: "EV sales turn YoY-negative in any major market; capacity utilization falls below 80%"   # required
signals:                       # eight-dimension signals, keys verbatim, values favorable/neutral/unfavorable/no_signal
  chain: favorable
  narrative: favorable
  fundamentals: favorable
  valuation: favorable
  flows: neutral
  sentiment: unfavorable
  competition: neutral
  macro: no_signal
---
```

Body structure (Markdown, written for a human reader, **in English**):

1. **Delta scan** — what has changed since the last analysis, why the price moved
2. **Eight-dimension analysis** — one subsection per dimension: ruling + justification + confidence
3. **Scenario modeling & valuation** — pass-through math, scenario EPS, the
   good-buy derivation (both anchors for cyclicals), and the bear/base/bull
   derivation (each of bear/base/bull must show its EPS assumption × multiple
   assumption explicitly, plus its probability with one line of
   justification; close with the risk/reward ratio. Price targets are
   **[INFERENCE]** and carry a confidence level — never just pick a round
   number)
4. **Same-layer comparables**
5. **Catalysts (next 1–2 quarters)** — 2–5 dated, observable events (earnings
   dates, product launches, policy decisions, capacity ramps), each with:
   date/window, what to watch, which scenario tier (bear/base/bull) it
   discriminates, **and a one-line checkpoint: if the base path is intact,
   roughly where price/estimates should stand at that date**. Checkpoints
   are what let the retrospect grade the path, not just the endpoint. This
   section gives time-flavored verdicts (`wait_for_pullback`, `watch_only`)
   an actual clock; `review_trigger` says when to re-analyze, catalysts say
   when the story gets tested.
6. **Entry plan** — only when the action is `enter` or `wait_for_pullback`:
   the tranche levels with the observable structure each is tied to (cite
   the `trend` block), and the invalidation price with one line on why
   *that* level falsifies the setup. Omit the section for all other verdicts.
7. **Risks & thesis-killer**
8. **Divergence** — required whenever a user view conflicts with a dimension
   ruling (see "User views & divergence"): a small table of user view /
   dimension / your ruling / edge-or-blind-spot classification + confidence /
   the observable that settles it. Omit the section entirely when there is
   no conflict.
9. **Summary & rulings** — signal table roll-up, the ten-state verdict
   derived via the precedence rules (name the rule that fired), and
   "the three most fragile assumptions this call depends on"

**No lazy analyses.** Every memo — full or incremental — must rule all
eight dimensions at the current price (the validator rejects missing
dimensions), and an incremental update may only *carry forward* a ruling
whose evidence genuinely hasn't changed; "carried forward" is a claim, not
a shortcut, and it must be checked, not assumed. Incremental updates are
allowed at most **2 in a row**; the analysis after that must be a full
rewrite (fresh searches, fresh comparables, fresh catalyst dates — the
dashboard flags longer chains). A full rewrite is also mandatory whenever
the delta scan finds a narrative-level event or a fired `review_trigger`,
regardless of the chain count.

**Grandfathering.** Fields introduced by the 2026-07-05 policy update (tier
probabilities, `entry_plan`, `mode`, the eight-signal completeness check,
dual anchors for cyclicals, catalyst checkpoints) are required in memos
dated on or after 2026-07-05. Older memos are graded by the retrospect
under the contract they were written to.
