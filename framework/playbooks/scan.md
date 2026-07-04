# Playbook: Watchlist Scan

Read `framework/methodology.md` first (it governs output discipline). This
playbook is a light, fast pass answering one question: **which names need to
be re-analyzed, and why.** It does not perform deep analysis itself. Write the
report in English.

## Steps

1. Read `data/watchlist.json`, `data/quotes.json`, and the frontmatter of the
   latest memo for each ticker.
2. For each ticker, check six things:
   - **Never analyzed**: no memo exists at all → needs a first full
     analysis; a watchlist slot without a memo is a name nobody actually
     evaluated (this is how discover candidates graduate or get pruned).
   - **Price deviation**: current price vs. `price_at_analysis` deviating by
     >15%?
   - **Invalidation hit**: if the latest memo carries an `entry_plan`, is the
     current price below its `invalidation` level? That is a forced
     re-analysis per the methodology — it outranks every other reason.
   - **Trigger hit**: search online quickly for news on this ticker over the
     last two weeks — has the event described in `review_trigger` happened?
   - **Staleness**: is the memo more than 30 days old?
   - **Targets expired**: has the memo's `price_targets.horizon` elapsed
     (memo date + horizon ≤ today)? Expired targets don't force a re-analysis
     by themselves, but flag them — they are due for the retrospect playbook
     (`/lux-retrospect`), which is how the framework calibrates itself.
   - **Earnings imminent**: is quotes.json `next_earnings` within 7 days?
     Not a rerun reason by itself, but flag it — an `enter`/
     `wait_for_pullback` verdict executed days before a print is a coin-flip
     on the report, not the thesis; note "consider waiting for earnings."
3. For the watchlist as a whole, check three things:
   - **Audit freshness**: for each thesis in use, is `last_audited` null or
     more than 90 days old? If so, recommend `/lux-audit-thesis <id>` —
     every memo on that thesis is confidence-capped at medium until the
     audit runs (methodology: "The thesis itself is under test").
   - Has there been major layer-level news for the underlying thesis
     (something that affects the whole layer, not just one ticker — e.g. a
     key raw-material contract price shift or new export-control rules)?
   - **Thesis concentration**: what share of the watchlist — and separately,
     of the `holding: true` names — hangs on a single thesis? Above ~60%,
     say so explicitly: these verdicts are correlated, not independent
     bets; one thesis-killer event fires them all at once.
   - **Watchlist hygiene (prune candidates)**: list names that are dead
     weight — never analyzed >30 days after being added (discover
     candidates that never graduated), two consecutive `no_edge` memos, or
     `thesis_broken`/`exit` already acted on. Suggest removal (the user
     decides; never remove names yourself, and never suggest pruning a
     `holding: true` name). Separately, if the watchlist exceeds ~20 active
     names, say so: beyond that, scan quality and the user's attention both
     dilute — the list should earn its slots.

## Output

Do not write memo files. Output a table directly to the user:

| ticker | needs rerun? | reason (invalidation hit / deviation X% / trigger hit: … / N days stale / targets expired / none) |

Plus one summary line: which 1–2 tickers to rerun first and why they are most
urgent. Names with `holding: true` outrank non-held names at equal urgency —
a stale call on something the user owns is the more expensive mistake. If
any memos have expired price targets, add one line suggesting `/lux-retrospect`.
If everything is normal, state explicitly "no action needed from this scan."
