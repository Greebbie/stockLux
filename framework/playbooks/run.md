# Playbook: Run (One-Command Pipeline)

The orchestrator: one command that leaves the whole desk current. It runs the
other playbooks in dependency order and only does the work that is actually
due — a no-op run on a fresh desk should finish with "everything current."
Read `framework/methodology.md` first. Report in English.

## Order of operations (dependencies, not ceremony)

### 1. Refresh hard data

Run `stocklux refresh` yourself if quotes are stale (>24h) — it is
deterministic code, not a judgment call. Only fall back to prompting the
user when the environment cannot execute shell commands.

### 2. Audit gate (before any analysis)

For every thesis **in use by at least one watchlist name** (unused or
`status: retired` theses are skipped — see the methodology's thesis
lifecycle): if `last_audited` is null or more than 90 days old, run the
**audit-thesis playbook** for it now. Audits run first because every memo
written afterwards inherits the confidence cap otherwise — analyzing before
auditing wastes the analysis. Cap the batch at **2 audits per run**
(most-stale first); queue the rest like step 4 queues analyses.

If an audit downgrades a thesis to `damaged`/`dead`, say so prominently and
still continue: the affected names need re-analysis *more*, not less
(precedence rule 1 / rule 5 will drive their verdicts).

### 3. Scan

Run the **scan playbook** logic over the watchlist. Collect the rerun list
with reasons, ranked by urgency:

1. entry-plan invalidation hit (price below the latest memo's invalidation)
2. review_trigger fired
3. thesis downgraded by step 2's audit
4. price deviation >15% vs. `price_at_analysis`
5. memo stale (>30 days) or ticker never analyzed
6. earnings within 7 days (flag only — see scan playbook)

### 4. Analyze what the scan flagged

Run the **analyze playbook** for each flagged ticker, most urgent first.
Cap the batch at **3 full analyses per run** (an analysis is expensive and
the user should see results before burning more); list anything beyond the
cap as "queued — run `/lux-run` again or `/lux-analyze <TICKER>` directly."

### 5. Retrospect if anything matured

If the scan found memos with expired `price_targets.horizon` (or passed
catalyst checkpoints not yet interim-graded), run the **retrospect
playbook**. Calibration debt compounds quietly — don't let it queue forever.

### 6. Report

One consolidated summary, in this order:

- data freshness (refreshed or already fresh)
- audits run and their rulings (+ any Rebuttal invitation)
- per-ticker verdicts from this run's analyses: action / confidence /
  good-buy range vs. price / entry plan if written
- anything queued beyond the batch cap
- retrospect highlights (hit rate, biggest bias) if one ran
- a final line: "dashboard is current — `StockLux.bat` / `stocklux ui`"

## What this playbook never does

- Never edits `framework/methodology.md` (retrospect *proposes*, the user
  disposes).
- Never rewrites thesis bodies or `## Rebuttal` sections.
- Never commits or pushes.
- Never exceeds the analysis batch cap silently — queued work is reported,
  not dropped.
