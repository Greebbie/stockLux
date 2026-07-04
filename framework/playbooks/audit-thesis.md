# Playbook: Thesis Audit (Narrative Stress Test)

Stress-test a single personal narrative in `data/theses/<id>.md`. It is the
user's subjective judgment — your job is not to agree with it, it's to hold it
up to the light. Read `framework/methodology.md` first. Write the audit in
English.

**Cadence**: audits expire after 90 days (methodology: "The thesis itself is
under test") — a thesis with `last_audited` null or older than that caps
every memo built on it at medium confidence, so run this playbook at least
quarterly per active thesis, and immediately when the scan flags it.

## Steps

### 0. Answer open rebuttals first

If the thesis file contains any `## Rebuttal <date>` section dated after the
last audit, the user is formally disputing the previous audit's ruling. This
audit must answer each rebuttal point **point by point** before anything
else: concede the points where the user is right (with what changed your
mind), hold the line where the evidence still says otherwise (with the
evidence), and for anything unresolvable today, name the observable that
will settle it and put it on the indicator checklist (step 5). Never edit or
delete the user's rebuttal text — answer it in this audit's section.

### 1. Steel-man (build it up to its strongest form first)

Restate the thesis in its strongest possible form: the most favorable
evidence, the smoothest pass-through chain, the best-case scenario. If even
the strongest form doesn't hold up, skip straight to the ruling.

### 2. Dissect the payload assumptions

- List every assumption this narrative depends on, and flag the **payload
  assumption** (the one that, if it breaks, collapses everything).
- Quantify the payload assumption: what number has to happen, within what
  timeframe (e.g. "token demand growth must keep outpacing unit cost decline"
  → needs a concrete estimate of X vs. Y).

### 3. Check against consensus

Search online for mainstream sell-side/buy-side views on the same theme:

- Where does this thesis diverge from consensus?
- For each divergence, answer: is this the user's edge (something the user
  knows or has thought through more deeply than the market), or the user's
  blind spot (something the market knows that the user doesn't)? Label each
  **[INFERENCE]** + confidence level.
- How crowded is this theme: if everyone already believes it, the price may
  already be pre-spent even if the narrative is correct.

### 4. Adverse scenarios

- The two most likely ways the narrative turns out wrong, and the leading
  indicator for each.
- Ways the narrative is right but still doesn't make money (wrong layer
  chosen, valuation pre-spent, timing mismatch).

### 5. Ruling

- `status`: intact / weakening / damaged / dead (verbatim), and what changed
  relative to the last audit.
- Updated kill conditions and an observable indicator checklist ("check this
  monthly/quarterly data point to know if it's still right").

## Output

- Update the thesis file's frontmatter: `status` and `last_audited: <today>`.
- Append a `## Audit <today's date>` section at the end of the file, with the
  full content of steps 0–5 above as the body (step 0 only when there were
  open rebuttals). Do not rewrite the user's original narrative body or any
  `## Rebuttal` section — the audit record may only be appended.
- If the ruling downgraded the thesis, tell the user explicitly that they can
  dispute it by appending a `## Rebuttal <date>` section to the thesis file —
  the next audit is obligated to answer it point by point.
