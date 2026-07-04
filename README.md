# StockLux

Local-first equity research workbench. Not a trading bot, not a signal service —
a disciplined pipeline for analyzing stocks against your own investment theses.

**Design: deterministic data, LLM-agnostic analysis, files as the database.**

```
┌────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ any LLM agent  │      │      data/       │      │   dashboard      │
│ Claude / Codex │─────▶│ watchlist.json   │◀─────│  stocklux ui     │
│ Gemini / ...   │write │ theses/*.md      │ read │  (localhost)     │
└────────────────┘      │ analyses/**.md   │      └──────────────────┘
                        │ quotes.json      │◀── deterministic fetcher
                        └──────────────────┘    (yfinance, no API key)
```

- **Files as database** — watchlist, theses, and analysis memos are JSON/Markdown
  in `data/` (gitignored). No server dependency, no lock-in.
- **LLM-agnostic** — any agent CLI executes the playbooks in `framework/`.
  The tool itself calls no LLM and manages no API keys.
- **Deterministic hard data** — price, P/E, short interest, institutional
  ownership, and volume/OBV accumulation signals are fetched by code.
  The LLM judges; it never sources numbers.
- **Offline-readable** — the dashboard and exported reports render the latest
  memos without any LLM running.

## Quick start

```bash
pip install -e .
mkdir -p data && cp -r examples/. data/    # Windows: xcopy /E /I examples data\
stocklux refresh                           # fetch quotes + flow data
stocklux ui                                # dashboard → http://127.0.0.1:8321
```

On Windows, double-click **`StockLux.bat`** in the repo root instead: it opens
the dashboard if the server is already running, or starts it (closing that
console window stops the server).

Run analyses from any agent CLI:

```
claude "/lux-run"                      # one command: refresh → audit → scan → analyze → retrospect
claude "/lux-analyze ON"               # full 8-dimension analysis → structured memo
claude "/lux-audit-thesis ev-adoption" # stress-test a thesis against consensus
claude "/lux-discover ev-adoption"     # screen the supply chain for new candidates
claude "/lux-scan"                     # flag watchlist entries needing re-analysis
claude "/lux-retrospect"               # grade matured price targets, calibrate the method
```

Slash commands are prefixed `lux-` so they never collide with generic global
commands (`/analyze`, `/scan`, …) from other tools. Any agent CLI without
slash-command support can run the same flows by prompt, e.g.
`claude "Read framework/playbooks/analyze.md and analyze ON"`.

Export a memo as a report:

```bash
stocklux export ON --pdf               # self-contained HTML + PDF → output/
```

## Analysis contract

Defined in `framework/methodology.md`, enforced by frontmatter validation.
Output is a ten-state verdict (enter / wait for pullback / hold / watch only /
good company bad price / crowded theme / thesis broken / no edge / trim / exit),
never Buy/Sell. Every memo must include:

| Element | Description |
|---|---|
| 8-dimension signals | chain, narrative, fundamentals, valuation, flows, sentiment, competition, macro |
| Good-buy range | scenario EPS × justified multiple (cyclicals 8–12x peak earnings, utilities 18–22x, high-growth 30–50x) |
| Price targets | bear / base / bull, 12-month horizon, each with explicit EPS × multiple derivation |
| Claim labels | fact (with source), consensus, or inference (with confidence level) |
| Thesis-killer | what event invalidates the narrative |
| Review trigger | what event demands re-analysis; the dashboard also flags price deviation >15% and stale memos automatically |
| Catalysts | 2–5 dated events in the next 1–2 quarters and which scenario tier each discriminates |
| Divergence | when the user's own view conflicts with a dimension ruling: edge or blind spot, plus the observable that settles it |

The verdict is derived from the signal table via explicit precedence rules
(thesis dead > valuation cap > crowding > no-edge > …), not vibes — the same
evidence lands on the same verdict. User views are first-class inputs: they
map into the eight dimensions as labeled `[INFERENCE-USER]` claims, never
silently adopted or dropped. The retrospect playbook closes the loop by
grading matured price targets and proposing (never self-applying)
methodology calibration.

Deliberately out of scope: position tracking, P&L, trade history. Analysis
stays decoupled from personal cost basis and emotions by design. The single
exception is a boolean `holding` flag per name (`stocklux hold <TICKER>`) —
just enough to pick between enter/wait verdicts and hold/trim/exit verdicts,
with no cost basis or share counts.

## Layout

```
framework/    methodology + 5 playbooks (public, reusable)
stocklux/     Python package: CLI, fetchers, FastAPI dashboard
examples/     starter data
data/         private layer (gitignored): watchlist, theses, memos
output/       exported reports (gitignored)
```

## Acknowledgements

Analysis policy, strategy, and tooling optimized with **Claude Fable 5**
(Anthropic) via Claude Code.

## Disclaimer

StockLux produces analysis, not advice. **Not investment advice.** Market data
comes from yfinance (unofficial Yahoo Finance API) with no accuracy guarantee;
verify against official sources before trading.
