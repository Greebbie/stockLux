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
   `stocklux refresh` yourself, then re-read (prompt the user only if you
   cannot execute shell commands).
2. Read every memo under `data/analyses/` (all dates, not just the latest).
3. Read `data/retrospects/` for prior retrospect reports, so already-graded
   memos are not re-graded (each report lists the memos it covered).

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

### 4. Propose adjustments (propose, never apply)

If calibration shows a systematic bias, propose concrete methodology edits —
e.g. "cyclical multiples graded 3/4 too generous; propose 8–12x → 7–10x" —
as a short list. **Do not edit `framework/methodology.md` yourself**: the
methodology is the user's policy; changing it requires their explicit
sign-off.

## Output

- Write `data/retrospects/<today's date, YYYY-MM-DD>.md`: the per-memo grade
  table (ticker / memo date / action / base target / realized / tier /
  right? / what missed), the calibration section, the divergence outcomes,
  and the proposed adjustments. List the memo files covered so the next run
  can skip them.
- Report back to the user: hit rate, the single largest systematic bias
  found (or "no systematic bias detectable yet"), any divergence verdicts,
  and the proposed methodology adjustments awaiting their sign-off.
