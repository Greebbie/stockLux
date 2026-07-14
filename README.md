# Luxtock

Local-first equity research workbench. Not a trading bot, not a signal
service, not a portfolio tracker — a disciplined analysis pipeline:
deterministic quant features, structured memos, falsifiable price targets,
and a calibration ledger that grades them when they mature.

**Design: deterministic data, LLM-agnostic analysis, files as the database.**

```
┌────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ any LLM agent  │      │      data/       │      │   dashboard      │
│ Claude / Codex │─────▶│ watchlist.json   │◀─────│  luxtock ui     │
│ Gemini / ...   │write │ analyses/**.md   │ read │  (localhost)     │
└────────────────┘      │ quotes/flows/    │      └──────────────────┘
                        │ quant/calibration│◀── deterministic code
                        └──────────────────┘    (yfinance, no API key)
```

- **Code computes, the analyst judges.** Prices, revisions, short interest,
  trend, dual-listing premium, factor scores — all fetched/derived by
  deterministic code. The LLM supplies reasoning and probabilities; it
  never sources or restyles numbers.
- **LLM-agnostic.** Any agent CLI runs the playbooks in `framework/`; the
  tool itself calls no LLM and holds no API keys.
- **Analysis, not your book.** No positions, weights, or P&L in any
  analysis view. An optional `shares`/`cash` sizing input exists solely
  for the standalone concentration/stress check (`luxtock portfolio`).
- **Calibrated over time.** Every memo's bear/base/bull targets carry
  probabilities; `luxtock calibrate` Brier-scores them at maturity.
  Win rates are measured, never asserted.

## Quick start

```bash
pip install -e .
mkdir -p data && cp -r examples/. data/    # Windows: xcopy /E /I examples data\
luxtock refresh                           # quotes + flows + history
luxtock quant                             # factor vector + setup scores
luxtock ui                                # dashboard → http://127.0.0.1:8321
```

Windows: double-click **`Luxtock.bat`** to open/start the dashboard.

## CLI (plain commands — usable from any shell, agent, or cron)

| command | purpose |
|---|---|
| `luxtock refresh` | fetch quotes/flows (+ paired-listing premium), append history |
| `luxtock quant` | 23-feature vector + transparent setup score per ticker → `data/quant.json` |
| `luxtock check` | price alerts vs memo levels (tranches, invalidation, trim, bear/bull); exit 1 on alert |
| `luxtock report --pdf` | one desk-level HTML/PDF: quant table, all verdicts, alerts, calibration |
| `luxtock export <T> --pdf` | single-name report: full memo + quant snapshot |
| `luxtock calibrate` | Brier ledger for matured targets + tracking for immature ones |
| `luxtock portfolio` | optional concentration & bear-stress check over sized holdings |
| `luxtock add / hold / shares / cash / pair` | watchlist and sizing/pairing inputs |
| `luxtock ui` | live dashboard (per-stock expandable: factors, risk/reward, full memo) |

Analyses run from any agent CLI:

```
claude "/lux-run"           # refresh → audit → scan → analyze → retrospect
claude "/lux-analyze MU"    # full 8-dimension analysis → structured memo
claude "/lux-scan"          # flag names needing re-analysis
claude "/lux-retrospect"    # grade matured targets, calibrate the method
```

Agents without slash commands run the same flows by prompt, e.g.
`claude "Read framework/playbooks/analyze.md and analyze MU"`.

## Analysis contract

Defined in `framework/methodology.md`, enforced by frontmatter validation
and the dashboard. Every memo carries: eight dimension rulings (chain,
narrative, fundamentals, valuation, flows, sentiment, competition, macro),
a good-buy range from justified multiples (dual anchors for cyclicals),
bear/base/bull targets with probabilities plus risk/reward **and**
probability-weighted EV, labeled claims (fact / consensus / inference with
confidence), a thesis-killer, a review trigger, dated catalysts, and an
entry plan (tranches + invalidation) for actionable verdicts. The verdict
is one of ten states derived by explicit precedence rules — the same
evidence lands on the same verdict.

Three separations keep the analysis honest:

1. **Quant vs judgment** — features and scores come from code
   (`framework/quant.md`); the analyst only supplies probabilities, which
   the retrospect grades (Brier) once targets mature.
2. **Analysis vs the user's views** — user opinions live in one Divergence
   section as labeled claims, ruled edge or blind spot against evidence;
   they never shape dimension rulings or valuations.
3. **Memo vs thesis** — the memo is the unit of falsifiability. Standalone
   thesis files are optional, desk-owned working hypotheses used only when
   one macro assumption spans several names (correlated risk); they are
   audited, never assumed.

Mechanical operating rules (entry tranches, sizing caps, defense rules)
live in `framework/operating-contract.md`.

## Layout

```
framework/    methodology, quant spec, operating contract, 6 playbooks
luxtock/     Python package: CLI, fetchers, quant/report modules, FastAPI dashboard
examples/     starter data
data/         private layer (gitignored): watchlist, memos, quant/calibration ledgers
output/       exported reports (gitignored)
```


## Disclaimer

Luxtock produces analysis, not advice. **Not investment advice.** Market
data comes from yfinance (unofficial Yahoo Finance API) with no accuracy
guarantee; verify against official sources before trading. Setup scores
and probabilities are uncalibrated priors until the calibration ledger has
depth — treat them as ordinal guides, not expected returns.
