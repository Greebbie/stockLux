# Playbook: Full-Dimension Single-Stock Analysis

Produce a complete analysis memo for one stock. Read
`framework/methodology.md` first — it is the source of truth for output
discipline and the data contract. Write memos and reports in English.

## Preconditions (execute in order)

1. Read `data/quotes.json` and `data/flows.json`. If either does not exist or
   `fetched_at` is more than 24 hours old, run `stocklux refresh` yourself,
   then re-read them (prompt the user only if you cannot execute shell
   commands).
2. Read `data/watchlist.json` to find the thesis this ticker is attached to,
   and read the full text of `data/theses/<thesis>.md`. Note the entry's
   `holding` flag (missing = false) — it constrains which verdicts are legal
   (see the methodology's position-context rule). **Check the thesis's
   `last_audited`**: if null or more than 90 days ago, the memo's overall
   confidence caps at medium (methodology: "The thesis itself is under
   test") — the analysis proceeds, but the summary must carry the cap line
   and the report back to the user must recommend
   `/lux-audit-thesis <id>`.
3. Read the **latest memo** under `data/analyses/<TICKER>/` if one exists. If
   none exists, this is a first-time analysis.
4. **Collect user views.** Gather every view the user has expressed on this
   name: in the current conversation, in the watchlist `note`, in the thesis
   file, and in the prior memo's Divergence section (an unresolved divergence
   carries forward until its deciding observable has printed). These feed
   step 2 as **[INFERENCE-USER]** inputs.

## Steps

### 1. Delta scan (required whenever a prior memo exists, skip on first analysis)

- Current price vs. the prior `price_at_analysis`, with deviation percentage.
- Search online for news on this ticker and its supply chain over the
  interval, and answer: **what drove this deviation?**
  - A narrative-level event (new contract, guidance change, competitive
    landscape shift) → do a full rewrite this round
  - Pure sentiment/macro noise (broad market, sector rotation, panic selling)
    → state explicitly that "price moved but the narrative didn't," an
    incremental update is acceptable, but the valuation ruling must be
    recomputed at the new price
- Check whether the prior memo's `review_trigger` has fired. If it has → full
  rewrite, and state explicitly in the Delta section that "the trigger
  condition has been hit."
- **Set `mode` honestly** (`full` / `incremental`) and respect the cadence:
  at most 2 incremental updates in a row — check the prior memos' `mode`;
  if the last 2 were both incremental, this round is a full rewrite no
  matter how quiet the interval was (the dashboard flags violations). A
  fired trigger or narrative-level event forces a full rewrite regardless.
- **Incremental updates must be self-contained.** The latest memo is what
  gets exported and read alone; "nothing changed" must never hide what was
  already in motion. Every dimension is still re-ruled at the current
  price — "carried forward" is only legal for rulings whose evidence you
  actually re-checked this round. When doing an incremental update, the Delta section
  must also restate — one line each — the prior memo's price-move
  attribution and every standing open signal (e.g. an unresolved oversupply
  hint, a pending lawsuit, an active short campaign), marked "carried
  forward", so a reader who opens only this memo sees the full live picture.

### 2. Eight-dimension analysis

Work through the dimension table from the methodology one by one. Requirements:

- `chain`: map this ticker's position in the supply chain (who it buys from
  upstream, who it sells to downstream), and write the explicit pass-through
  math — how much of the thesis's aggregate growth passes through to this
  ticker's revenue/earnings, and with what lag (weeks/months/quarters/years).
- `fundamentals`: alongside the reported numbers, cite the `revisions` block
  from quotes.json (90-day forward-EPS estimate change, analysts revising
  up/down last 30 days) — sustained upward revisions with a flat price is a
  classic entry setup; downward revisions against a rising price is an
  explicit warning on the price targets (also feed this into `sentiment`).
- `flows`: first cite the hard numbers from flows.json (short interest as %
  of float, institutional ownership, net insider buying/selling, put/call OI,
  the price/volume accumulation_hint, and the `trend` block — 50/200-dma
  distance, RSI-14, ATR%, 3-month relative strength vs. the entry's
  benchmark). When `data/history.jsonl` has depth, cite the *change* — a
  short-interest or put/call move over weeks outranks any single print.
  Then search online for recent 13F
  change reporting, block trades, and unusual options activity news, and
  synthesize one **[INFERENCE]**: "smart money is likely accumulating /
  distributing / indeterminate" + confidence level. (13F filings are disclosed
  45 days after quarter-end; finding no new 13F outside the disclosure window
  is normal — in that case synthesize from flows.json hard data alone and
  note "outside the 13F window.")
- `sentiment`: anchor on the hard data first — quotes.json's `analyst` block
  (PT mean/high/low, analyst count, recommendation mean): a narrow PT spread
  plus a unanimous recommendation is quantifiable crowding. Then search
  X/Reddit/financial media for discussion density and direction around this
  ticker and its theme; judge crowding — "does everyone already know this
  story" — and whether retail narrative and institutional narrative have
  diverged.
- For the remaining dimensions, answer the questions from the methodology table.
- **Merge user views.** Attach each collected user view to the dimension it
  belongs to as **[INFERENCE-USER]**, next to your own ruling. Where they
  conflict, classify the divergence (edge / blind spot / unresolved, per the
  methodology's "User views & divergence") and name the observable that
  settles it. A user view never silently changes a ruling — if it genuinely
  updates your judgment, say so and show the evidence that did the updating.

### 3. Scenario modeling & valuation ruling

- Scenario: assume the thesis's pass-through math plays out over 12 months.
  Derive the resulting EPS (or ARR/backlog) change range, with reasoning shown.
- The scenario P/E implied by the current price (compute both ttm and fwd,
  stating clearly whether GAAP or adjusted).
- Good-buy range = scenario EPS × a reasonable multiple (selected from the
  methodology's multiple table, with justification stated; say which half of
  the band the rate regime puts you in). **Cyclicals get two anchors** (per
  the methodology): peak EPS × 8–12x *and* normalized mid-cycle EPS ×
  12–16x — show both, take the stricter lower bound as the good-buy floor,
  and cap valuation confidence at low when the anchors disagree >40%.
- **Bear/base/bull price targets (price_targets, 12-month)**, each with its
  formula and assumptions shown:
  - bear = low-scenario EPS (thesis partially fails / cycle peaks early) ×
    low end of the multiple range
  - base = mid-scenario EPS × mid-range reasonable multiple — the price you
    consider most likely in 12 months
  - bull = high-scenario EPS (thesis fully plays out) × high end of the
    multiple range
  Assign each tier a probability (`p_bear`/`p_base`/`p_bull`, sum 1.0, one
  line of justification each). Price targets are **[INFERENCE]**, carry a
  confidence level; the body must include a small bear/base/bull comparison
  table (EPS assumption / multiple / probability / target price / % vs.
  current price), closing with the **risk/reward ratio**
  ((base − current) / (current − bear)) — the ≥2 gate that precedence
  rule 6 applies to `enter`.
- Ruling: current price below/within/above the range, with deviation
  percentage. If above the range, say so plainly: "buying here means prepaying
  for growth beyond the scenario."

### 4. Same-layer comparables

What other names sit at the same bottleneck layer; is there "the same
exposure at a cheaper price" (e.g. FN relative to ALAB). Produce a small table:
ticker / exposure / current valuation / one-line verdict.

### 5. Catalyst map (next 1–2 quarters)

List 2–5 dated, observable events ahead: earnings dates, product launches,
policy decisions, capacity ramps, contract renewals. For each: date or
window / what specifically to watch / which scenario tier (bear/base/bull)
it discriminates / **a one-line checkpoint — if the base path is intact,
roughly where price or estimates should stand at that date**. The ticker's
own earnings date comes from quotes.json `next_earnings`; search online for
the remaining dates — do not guess them. This section gives
`wait_for_pullback` and `watch_only` a clock, and the checkpoints give the
retrospect a path to grade before the 12-month horizon matures.

### 6. Entry plan (only when the verdict will be `enter` or `wait_for_pullback`)

Write the `entry_plan` frontmatter block and its body section:

- **Tranches (1–3 levels)**: each tied to observable structure — the
  good-buy boundary, a prior consolidation zone, or a moving-average
  distance from the flows.json `trend` block. Not round numbers pulled from
  air; say what each level *is*.
- **Invalidation price**: the level below which the setup is falsified —
  the market is telling you something the eight dimensions missed. One line
  on why that level. Hitting it forces a re-analysis (fold it into
  `review_trigger`), never an automatic exit.
- Use the `trend` block to time, not to override: RSI/dma/relative-strength
  can argue for waiting for a tranche rather than entering at market, but
  they never overturn a valuation ruling.

### 7. Risks & thesis-killer

- thesis-killer: what happening means the whole narrative is dead (inherit
  and update the one from the thesis file).
- Risks specific to this ticker (customer concentration, technology path,
  cyclicality, regulation), one line each.
- New `review_trigger`: written as a specific, observable event. Fold in the
  deciding observables from any open divergences (step 8) and the entry
  plan's invalidation price (step 6, when present) so disagreements and
  broken setups get re-tested instead of forgotten.

### 8. Divergence (only when a user view conflicts with a ruling)

If step 2 produced at least one conflict, write the Divergence section per
the methodology: user view / dimension / your ruling / edge-or-blind-spot
classification + confidence / the observable that settles it. If user views
exist but all concur, one line in the summary instead. If there are no user
views at all, skip this section.

### 9. Summary & rulings

- Eight-dimension signal table (one row per dimension: dimension / ruling /
  one-line justification).
- The ten-state verdict + confidence level, derived by walking the
  methodology's **verdict precedence rules** top-down against the signal
  table and the `holding` flag — name the rule that fired. Apply the
  confidence-propagation cap (overall confidence ≤ valuation confidence,
  ≤ payload-dimension confidence, and ≤ medium when the thesis is unaudited
  or its audit is stale — state which cap bound, e.g. "confidence capped:
  thesis unaudited").
- **"The three most fragile assumptions this call depends on."**

## Output

- Write `data/analyses/<TICKER>/<today's date, YYYY-MM-DD>.md` following the
  methodology's data contract.
- Use frontmatter enum values verbatim; `price_at_analysis` is taken from
  quotes.json.
- Finish by reporting back to the user out loud: the ten-state verdict, the
  good-buy range vs. current price, the risk/reward ratio, the entry plan
  (tranches + invalidation) when one was written, the single most important
  risk — plus,
  when a Divergence section was written, one line per open divergence
  ("your view on X: ruled edge/blind spot/unresolved — settled by Y").
