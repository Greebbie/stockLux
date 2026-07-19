# Luxtock — Agent Integration Guide

This repository is a personal investment research desk. Your job (as an LLM
agent) is to **run playbooks** and write the results back to structured
files. The dashboard and quote fetching are handled by deterministic code and
are not your concern.

## Read first

Before any analysis action, read `framework/methodology.md` — the output
discipline, the eight dimensions, the ten-state verdict, and the memo data
contract all live there. A memo that violates the contract will be flagged
with a format warning on the dashboard.

## Playbooks (choose by user intent)

Claude Code slash commands live in `.claude/commands/` with a `lux-` prefix
(`/lux-run`, `/lux-analyze`, `/lux-audit-thesis`, `/lux-discover`,
`/lux-scan`, `/lux-retrospect`) to avoid collisions with generic global
command names. Each is a thin wrapper: read the methodology, then run the
playbook below.

| user intent | playbook | output |
|---|---|---|
| "bring everything current" (one command) | `framework/playbooks/run.md` | orchestrates the others in dependency order |
| analyze a stock | `framework/playbooks/analyze.md` | `data/analyses/<TICKER>/<YYYY-MM-DD>.md` |
| audit a thesis | `framework/playbooks/audit-thesis.md` | updates `data/theses/<id>.md` |
| discover new names along a thesis | `framework/playbooks/discover.md` | updates `data/watchlist.json` + a report |
| scan the watchlist | `framework/playbooks/scan.md` | reports to the user only, writes no files |
| grade matured price targets / calibrate | `framework/playbooks/retrospect.md` | `data/retrospects/<YYYY-MM-DD>.md` |

## Hard rules

1. Price/P-E figures must always be quoted from `data/quotes.json` — never
   look up prices online yourself. If the data is more than 24 hours old,
   run `luxtock refresh` yourself (it is deterministic code); prompt the
   user only when your environment cannot execute shell commands. The only
   exception (consistent
   with the methodology): candidates surfaced by the discover flow that are
   not yet on the watchlist may be looked up online, with source and date cited.
2. Flow hard data is cited from `data/flows.json`; it is a proxy signal (13F
   filings lag ~45 days), so conclusions must carry a confidence level.
3. Read and write all files as UTF-8. Use memo frontmatter enum values
   verbatim (see the methodology).
4. Never commit, never push — leave files in the working tree for the user to
   commit themselves.
5. Write all memos and reports in English.
6. When the user has expressed a view on a name or thesis, it must surface in
   the analysis as **[INFERENCE-USER]** mapped into a dimension — never
   silently adopted, never silently dropped (see "User views & divergence"
   in the methodology).
7. `hold` / `trim` / `exit` verdicts are only legal when the watchlist
   entry has `holding: true`; `enter` / `wait_for_pullback` only when it
   does not. Derive verdicts by walking the methodology's precedence rules.
8. A thesis is a **hypothesis under test, never a verified premise**. If its
   `last_audited` is null or more than 90 days old, every memo built on it
   caps overall confidence at medium and must say so (see the methodology's
   "The thesis itself is under test").

## Data layout

- `data/watchlist.json` — the tracked list; entries may carry an optional
  `holding: true` flag (`luxtock hold <TICKER>`) marking names the user
  actually owns — no share counts or P&L, by design
- `data/theses/*.md` — the **desk's working hypotheses** (analyst-owned,
  audited by the audit playbook). Attached to a name only when a shared
  macro assumption spans several names; most names need none — the memo is
  the unit of falsifiability. The user challenges via `## Rebuttal`
  sections (those may not be rewritten without authorization) and the
  Divergence machinery; theses are never the structural spine of analysis
  or presentation.
- `data/analyses/<TICKER>/*.md` — analysis memos, filename is the date
- `data/retrospects/*.md` — calibration reports grading matured price targets
- `data/quotes.json` / `data/flows.json` — deterministically fetched hard
  data, read-only
- `data/history.jsonl` — append-only per-ticker snapshot log written by
  `luxtock refresh` (price, short interest, revision momentum, trend);
  read-only for agents — cite it for *changes over time* and for
  retrospect path grading. It grows forever by design: **filter it**
  (grep by ticker, tail by date) — never load the whole file into context
- `data/quant.json` — deterministic feature vector + setup scores per
  ticker, written by `luxtock quant` (spec: `framework/quant.md`).
  Read-only for agents: cite the numbers, never recompute or restyle them.
  Memos dated on/after 2026-07-12 cite the snapshot in their Summary and
  entry plan.
- `data/calibration.json` — probability ledger written by
  `luxtock calibrate`: Brier scores for matured price targets + a
  tracking table for immature ones. Read-only for agents; the retrospect
  playbook is its consumer.
- `data/screen.json` / `data/universe.json` — the market-wide funnel written
  by `luxtock screen` (spec: `framework/screen.md`) and the scan universe it
  reads. Read-only for agents: cite `screen.json` verbatim, never recompute.
  Its rows are **candidates, not verdicts** — a name enters the desk only via
  `luxtock add`; `rr_proxy` must always be labeled a sell-side proxy, never
  presented as framework R/R. `track: hypergrowth` rows (no earnings base,
  ≥30% revenue growth) carry the same 0-100 score scale but the lowest
  confidence tier by construction — cite with that caveat. `universe.json`'s
  optional `extra_tickers` block is the user's manually curated side list
  for non-index listings; treat it the same as the main `tickers` list.

## CLI toolbox (works from any LLM CLI or plain shell)

Everything below is deterministic code — no LLM involvement. Any agent
(Claude Code, Codex, Gemini CLI, …) or cron job can run these; all state
lives in plain files under `data/`.

| command | purpose |
|---|---|
| `luxtock refresh` | fetch quotes/flows (+ paired-listing parity & premium), append history |
| `luxtock add <T> --thesis <id>` / `hold` / `shares <T> <N>` / `cash <N>` | watchlist, holding flag, position sizing |
| `luxtock pair <T> <HOME_TICKER> --ratio R --currency C` | pair a US listing with its home-market line (premium tracked on refresh) |
| `luxtock quant` | feature vector + setup scores → `data/quant.json` (spec: `framework/quant.md`) |
| `luxtock screen` | market-wide beaten-down/quality-discount/hypergrowth candidate funnel → `data/screen.json` (spec: `framework/screen.md`) |
| `luxtock portfolio` | concentration & bear-stress report + flags |
| `luxtock check` | price alerts vs memo levels + portfolio flags; exit 1 when any alert (cron-friendly) |
| `luxtock calibrate` | Brier ledger for matured targets + tracking table → `data/calibration.json` |
| `luxtock export <T> --pdf` | self-contained HTML/PDF report of the latest memo + quant snapshot |
| `luxtock report --pdf` | ONE desk-level HTML/PDF: portfolio, quant table, all verdicts, alerts, calibration |
| `luxtock ui` | live dashboard (reads `data/` directly) |

The operator's mechanical rules live in `framework/operating-contract.md`.

## Getting started

```bash
pip install -e .
mkdir -p data && cp -r examples/. data/   # sample watchlist + thesis + one memo
# Windows: xcopy /E /I examples data\
luxtock refresh              # fetch quotes
luxtock ui                   # open the research desk
```
