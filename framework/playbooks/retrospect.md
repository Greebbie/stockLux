# Playbook: Retrospect (Calibration Loop)

Read `framework/methodology.md` first. This playbook closes the loop the
others leave open: it grades past calls against what actually happened, so
the framework learns whether its multiples, confidence levels, and dimension
rulings are systematically biased. Without this, the methodology can be
wrong in the same direction forever and never notice. Write the report in
English.

## When to run

- The scan playbook flags memos whose `price_targets.horizon` has elapsed.
- Or on demand (e.g. quarterly), grading everything gradable.

## Preconditions

1. Read `data/quotes.json`; if `fetched_at` is more than 24 hours old, run
   `luxtock refresh` yourself, then re-read (prompt the user only if you
   cannot execute shell commands).
2. Run `luxtock calibrate` and read `data/calibration.json` — the
   deterministic ledger (realized tiers, Brier scores, MAE/MFE, the
   tracking table for immature memos) is the numeric backbone of this
   playbook; the steps below add the judgment layer on top (verdict
   quality, what-missed diagnosis, divergence outcomes). Never hand-compute
   a number the ledger already carries.
3. Read every memo under `data/analyses/` (all dates, not just the latest).
4. Read `data/retrospects/` for prior retrospect reports, so already-graded
   memos are not re-graded (each report lists the memos it covered).
5. Check `data/screen_history.jsonl` exists and note its date range (filter
   it — grep by date/ticker; never load the whole file). It feeds step 4;
   if it is missing or has no rows old enough, step 4 reports "no gradable
   screen runs yet" and is skipped, not silently omitted.

## Steps

### 1. Select gradable memos

A memo is gradable when it has `price_targets` and its horizon has elapsed
(memo date + horizon ≤ today). Also gradable early: memos superseded by a
newer memo whose delta scan declared the trigger hit or the narrative
changed — grade those against the price at supersession, and say so.
Additionally, memos with catalyst **checkpoints** whose dates have passed
may be **path-graded** at those checkpoints before the horizon matures
(did price/estimates stand roughly where the base path said they should?)
— interim path grades accumulate calibration samples far faster than
endpoint-only grading; mark them "interim" so they are re-graded at
maturity. Exclude memos already fully covered by a prior retrospect.

If nothing is gradable, report "nothing to grade yet — earliest memo
matures on <date>" and stop.

### 2. Grade each memo

For each gradable memo, one row:

- **Realized price**: from quotes.json for watchlist names; for names since
  dropped, look up online and cite source and date (the standing exception
  for names not in quotes.json).
- **Tier realized**: closest of bear/base/bull to the realized price, plus
  the error vs. base as a percentage.
- **Path (MAE/MFE)**: the within-horizon maximum adverse and maximum
  favorable excursion vs. the memo price. Use `data/history.jsonl` for the
  window it covers; for anything earlier, historical path data may be
  looked up online with source and date cited (an extension of the
  standing exception — path grading is impossible otherwise). An `enter` that ended right but drew
  down through the entry plan's invalidation first is a worse call than
  the endpoint says.
- **Verdict directionally right?** Judge the `action` against what followed:
  `enter`/`hold` want the price toward base/bull; `trim`/`exit`/
  `thesis_broken` want bear-side or underperformance; `wait_for_pullback`
  is right if a better entry printed inside the horizon — when the memo
  carries an `entry_plan`, grade it concretely: did tranche 1 print? all
  tranches? or did the price run away without ever pulling back (the
  verdict cost the position)? `watch_only`/
  `no_edge`/`crowded_theme`/`good_company_bad_price` are graded on whether
  staying out cost little (did it beat the good-buy range logic?). One line
  of justification each — this is a judgment call, label it **[INFERENCE]**.
- **Entry plan quality** (memos that carry one): were the tranche levels
  touched (usable) or decoration? Did the invalidation price get hit, and
  if so, was the forced re-analysis run — and was invalidating right, or
  did it shake out a good position?
- **What missed**: for wrong calls, name the dimension whose ruling was the
  culprit (e.g. `competition` ruled neutral, price war happened anyway), or
  "exogenous" if nothing in the eight dimensions could have caught it.
- **Divergence outcomes**: if the memo carried a Divergence section, rule
  who turned out right — the user or the analysis — now that the deciding
  observable has (or hasn't) printed. Track this honestly in both
  directions; it is how the framework learns whose judgment to weight where.

### 3. Aggregate calibration (needs ≥5 graded memos to mean anything; below
that, present per-memo grades only and say the sample is too small)

- **Multiple bias**: did realized prices systematically land below base
  (multiples too generous) or above (too stingy)? Split by multiple class
  (cyclical / stable grower / high growth) where the sample allows.
- **Confidence calibration**: were `high` confidence calls actually right
  more often than `medium` and `low`? If not, confidence labels are
  decoration and the report must say so.
- **Probability calibration** (memos with `p_bear`/`p_base`/`p_bull`):
  compare declared tier probabilities against realized tier frequencies —
  did tiers assigned ~55% actually land ~55% of the time? Where the sample
  allows, score each memo Brier-style (sum over tiers of
  (p_tier − realized)² where realized is 1 for the tier that landed, 0
  otherwise; lower is better) and report the average. Systematic
  overconfidence in `p_base` is the expected failure mode — say so
  explicitly if it shows.
- **Dimension diagnosis**: which dimension produced the most wrong rulings,
  and which divergence classifications (edge vs. blind spot) held up.
- **User-vs-framework score**: across all graded divergences, how often was
  the user's view right? A user who keeps winning a dimension has real edge
  there — future analyses should weight their **[INFERENCE-USER]** input on
  that dimension accordingly (and the report should say this explicitly).

### 4. Grade the screen's hit rate

The screen (`framework/screen.md`) is the desk's only unfunded caller: it
names candidates but never gets graded by `calibrate`, because
`depression_score` is not a probability — no Brier, no tier math. This step
grades it **informally**, the way a desk grades a junior analyst's idea
list: did the names it liked go on to work?

Source is `data/screen_history.jsonl` — one append-only line per qualified
candidate per run (date, ticker, track, price at screen, drawdown, score,
band). A row is gradable when its `date` is ≥90 days old — the screen's
entry geometry is "catch the knife after it sticks", so grading younger
rows grades noise. Skip runs already covered by a prior retrospect (each
report lists the screen run dates it covered).

For each gradable run:

- **Forward return per row** since the screen date: quotes.json for names
  now on the watchlist; `data/history.jsonl` for the window it covers;
  anything else looked up online with source and date cited (the standing
  exception). Always compute the same-window benchmark return (SPY) —
  a screen that "wins" in a melt-up proves nothing.
- **Band separation** (the core question): did `strong` rows outperform
  `fair`, and `fair` outperform `weak`, vs. the benchmark? If bands don't
  separate, the score's weights are decoration and the report must say so —
  the exact standard the confidence-calibration check applies to memos.
- **Per-track honesty**: grade `beaten_down`, `quality_discount`, and
  `hypergrowth` separately. Hypergrowth is the declared speculative tier —
  grade it on dispersion (a few big winners paying for blowups is that
  track working as designed; uniform decay is not).
- **Value-trap autopsy**: for rows that fell another ≥20% from their screen
  price, name what they had in common (track, sector, near-missed
  disqualifier). The cyclical-top-low-P/E trap is the documented expected
  failure mode (`framework/screen.md`) — say explicitly whether it showed
  up, and which flag *would* have caught it if any.
- **Funnel conversion**: which screened names were actually added and
  analyzed, and did the memo agree with the screen's enthusiasm? A screen
  whose `strong` names keep drawing `watch_only`/`no_edge` memos is
  mis-ranked even when the prices did fine — the screen's job is to feed
  the analyze playbook, not to beat SPY on its own.

Sample-size honesty mirrors the memo rule: below ~20 gradable rows (or a
single run date), present the per-row table only and state the sample is
too small for band-level claims.

### 5. Propose adjustments (propose, never apply)

If calibration shows a systematic bias, propose concrete methodology edits —
e.g. "cyclical multiples graded 3/4 too generous; propose 8–12x → 7–10x" —
as a short list. The same applies to the screen: if step 4 shows bands not
separating or a repeating trap, propose knot/weight/gate edits to
`framework/screen.md` in the same propose-only form. **Do not edit
`framework/methodology.md` or `framework/screen.md` yourself**: both are
the user's policy; changing them requires their explicit sign-off.

## Output

- Write `data/retrospects/<today's date, YYYY-MM-DD>.md`: the per-memo grade
  table (ticker / memo date / action / base target / realized / tier /
  right? / what missed), the calibration section, the divergence outcomes,
  the screen hit-rate section (per-row table: ticker / screen date / band /
  track / screen price / realized / vs SPY — plus the band-separation
  verdict, or "no gradable screen runs yet"), and the proposed adjustments.
  List the memo files **and screen run dates** covered so the next run can
  skip them.
- Report back to the user: hit rate, the single largest systematic bias
  found (or "no systematic bias detectable yet"), any divergence verdicts,
  whether the screen's bands separated (once gradable), and the proposed
  methodology adjustments awaiting their sign-off.
