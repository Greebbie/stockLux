/* Luxtock dashboard — talks only to the backend API; no LLM dependency. */
const $ = (sel) => document.querySelector(sel);
const state = { view: "overview", ticker: null, dataVersion: 0, quant: null, stocksCache: {} };

const SIG_LABEL = { chain: "chain", narrative: "narrative", fundamentals: "fundamentals",
                    valuation: "valuation", flows: "flows", sentiment: "sentiment",
                    competition: "competition", macro: "macro" };

// pt_low near spot makes rr_proxy explode; cap the *display* only — same
// rule as luxtock/screen.py's RR_PROXY_DISPLAY_CAP (screen.json stays honest).
const RR_PROXY_DISPLAY_CAP = 10;

// mirror of luxtock/screen.py's UNIVERSE_STALE_DAYS — index membership
// drifts ad hoc; a snapshot older than a quarter has likely diverged.
const UNIVERSE_STALE_DAYS = 90;

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));
const sgn = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const pretty = (s) => String(s ?? "—").replace(/_/g, " ");

function analyzeCmd(ticker) {
  return `claude "Read framework/methodology.md and framework/playbooks/analyze.md, then run a full analysis of ${ticker}"`;
}

function cmdBlock(text) {
  return `<div class="cmd" onclick="navigator.clipboard.writeText(this.dataset.c).then(()=>toast('Copied'))" data-c="${esc(text)}">${esc(text)} ⧉</div>`;
}

function signalDots(signals) {
  if (!signals) return '<span class="muted">—</span>';
  return Object.keys(SIG_LABEL).map((k) => {
    const v = signals[k] || "no_signal";
    return `<span title="${SIG_LABEL[k]}: ${esc(v)}"><span class="sig sig-${esc(v)}"></span></span>`;
  }).join("");
}

function setupCell(quant, ticker) {
  const t = quant && quant.tickers && quant.tickers[ticker];
  const s = t && t.scores;
  if (!s || s.composite == null) return '<span class="muted">—</span>';
  const band = s.band || "n/a";
  const cls = { strong: "chip-strong", fair: "chip-fair", weak: "chip-weak" }[band] || "chip-na";
  const coverage = s.coverage != null ? `coverage ${Math.round(s.coverage * 100)}%` : "coverage —";
  return `<span class="chip ${cls}" title="${esc(coverage)}">${Math.round(s.composite)} <span class="muted">${esc(band)}</span></span>`;
}

function screenScoreChip(r) {
  if (r.depression_score == null) return '<span class="muted">—</span>';
  const band = r.band || "n/a";
  const cls = { strong: "chip-strong", fair: "chip-fair", weak: "chip-weak" }[band] || "chip-na";
  const coverage = r.coverage != null ? `coverage ${Math.round(r.coverage * 100)}%` : "coverage —";
  return `<span class="chip ${cls}" title="${esc(coverage)}">${Math.round(r.depression_score)} <span class="muted">${esc(band)}</span></span>`;
}

function screenRrCell(rr) {
  if (rr == null) return "—";
  return rr > RR_PROXY_DISPLAY_CAP ? ">10" : fmt(rr, 2);
}

function screenTickerCell(ticker) {
  const cmd = `luxtock add ${ticker}`;
  return `<span class="ticker">${esc(ticker)}</span>
    <button class="ghost" data-c="${esc(cmd)}" onclick="navigator.clipboard.writeText(this.dataset.c).then(()=>toast('Copied'))">⧉ add</button>`;
}

function screenFetchFailedBadge(r) {
  return r.fetch_failed
    ? ` <span class="badge warn" title="${esc(r.fetch_failed)}">fetch failed</span>` : "";
}

const QUANT_FACTOR_GROUPS = [
  ["Valuation", ["valuation_gap_pct", "gap_to_floor_pct", "rr_ratio", "ev_return_pct"]],
  ["Momentum", ["rev_90d_pct", "rev_breadth"]],
  ["Positioning", ["short_pct_float", "put_call_oi_ratio", "inst_pct", "rec_mean",
                    "n_analysts", "pt_spread_pct", "pt_upside_pct"]],
  ["Trend", ["rsi_14", "dist_50dma_pct", "dist_200dma_pct", "atr_pct_14", "rel_strength_3m"]],
  ["Deltas & info", ["d14_price_pct", "d14_short_pct_float", "d14_rsi", "paired_premium_pct", "price"]],
];

const QUANT_FACTOR_LABEL = {
  valuation_gap_pct: "valuation gap", gap_to_floor_pct: "gap to floor", rr_ratio: "risk/reward",
  ev_return_pct: "EV return", rev_90d_pct: "90d EPS revision", rev_breadth: "revision breadth",
  short_pct_float: "short % float", put_call_oi_ratio: "put/call OI", inst_pct: "institutional %",
  rec_mean: "analyst rec (mean)", n_analysts: "# analysts", pt_spread_pct: "PT spread",
  pt_upside_pct: "PT upside", rsi_14: "RSI-14", dist_50dma_pct: "dist 50dma",
  dist_200dma_pct: "dist 200dma", atr_pct_14: "ATR % (14d)", rel_strength_3m: "rel strength (3m)",
  d14_price_pct: "14d price Δ", d14_short_pct_float: "14d short % Δ", d14_rsi: "14d RSI Δ",
  paired_premium_pct: "paired premium", price: "price",
};

// Percent-family formatting buckets — see spec in framework/quant.md.
const QUANT_PERCENT_1DP = new Set(["valuation_gap_pct", "gap_to_floor_pct", "ev_return_pct",
  "rev_90d_pct", "pt_spread_pct", "pt_upside_pct", "d14_price_pct", "paired_premium_pct",
  "dist_50dma_pct", "dist_200dma_pct", "atr_pct_14", "rel_strength_3m"]);
const QUANT_FRACTION_PERCENT = new Set(["short_pct_float", "inst_pct", "d14_short_pct_float"]);
const QUANT_RATIO_2DP = new Set(["rr_ratio", "put_call_oi_ratio", "rec_mean", "rev_breadth"]);
const QUANT_PLAIN_1DP = new Set(["rsi_14", "d14_rsi"]);
const QUANT_COUNT_INT = new Set(["n_analysts"]);

function quantFactorValue(key, v) {
  if (v == null) return "—";
  const n = Number(v);
  if (QUANT_PERCENT_1DP.has(key)) return `${n.toFixed(1)}%`;
  if (QUANT_FRACTION_PERCENT.has(key)) return `${(n * 100).toFixed(1)}%`;
  if (QUANT_RATIO_2DP.has(key)) return n.toFixed(2);
  if (QUANT_PLAIN_1DP.has(key)) return n.toFixed(1);
  if (QUANT_COUNT_INT.has(key)) return String(Math.round(n));
  if (key === "price") return n.toFixed(2);
  return n.toFixed(2);
}

function quantBarColor(v) {
  if (v == null) return "var(--none)";
  if (v >= 70) return "var(--good)";
  if (v >= 50) return "var(--amber)";
  return "var(--bad)";
}

function quantScoreBar(label, value, weight) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const color = quantBarColor(value);
  const valueText = value == null ? "no data" : String(Math.round(value));
  return `<div class="quant-bar-row">
    <div class="quant-bar-label"><span>${esc(label)}</span><span class="muted">${esc(weight)}</span></div>
    <div class="quant-bar-track"><div class="quant-bar-fill${value == null ? " empty" : ""}"
      style="width:${pct}%;background:${color}"></div></div>
    <div class="quant-bar-value" style="color:${value == null ? "var(--muted)" : color}">${esc(valueText)}</div>
  </div>`;
}

function quantFactorTable(features) {
  const f = features || {};
  const groups = QUANT_FACTOR_GROUPS.map(([label, keys]) => {
    const rows = keys.map((k) => `<tr><td>${esc(QUANT_FACTOR_LABEL[k] || pretty(k))}</td>
      <td>${quantFactorValue(k, f[k])}</td></tr>`).join("");
    return `<tr class="factor-group"><th colspan="2">${esc(label)}</th></tr>${rows}`;
  }).join("");
  return `<table class="quant-factors">${groups}</table>`;
}

function quantChip(scores) {
  if (!scores || scores.composite == null) return '<span class="muted">—</span>';
  const band = scores.band || "n/a";
  const cls = { strong: "chip-strong", fair: "chip-fair", weak: "chip-weak" }[band] || "chip-na";
  return `<span class="chip ${cls}">${Math.round(scores.composite)} <span class="muted">${esc(band)}</span></span>`;
}

function quantCard(ticker, t) {
  const s = t.scores || {};
  const coverage = s.coverage != null ? `${Math.round(s.coverage * 100)}%` : "—";
  return `<div class="quant-card">
    <div class="quant-card-header">
      <span class="ticker">${esc(ticker)}</span>
      ${quantChip(s)}
      <span class="muted">coverage ${coverage}</span>
    </div>
    <div class="quant-bars">
      ${quantScoreBar("Valuation", s.valuation, "40%")}
      ${quantScoreBar("Momentum", s.momentum, "25%")}
      ${quantScoreBar("Positioning", s.positioning, "15%")}
      ${quantScoreBar("Trend", s.trend, "20%")}
    </div>
    ${quantFactorTable(t.features)}
  </div>`;
}

function verdictBadge(memo) {
  if (!memo) return '<span class="muted">not analyzed</span>';
  const map = { below_range: ["✓ below range", "ok"], in_range: ["○ in range", "warn"],
                above_range: ["✗ above range", "no"] };
  const [label, cls] = map[memo.verdict] || ["—", ""];
  return `<span class="badge ${cls}">${label}</span>`;
}

function stalenessNote(st) {
  if (!st) return "";
  const bits = [];
  if (st.price_deviation_pct != null && Math.abs(st.price_deviation_pct) > 15)
    bits.push(`price ${st.price_deviation_pct}% off analysis`);
  if (st.days_since_analysis > 30) bits.push(`memo ${st.days_since_analysis} days old`);
  return bits.length
    ? `<div class="muted" style="color:var(--amber)">⚠ ${bits.join(" · ")} — re-analysis suggested</div>`
    : "";
}

function targetCell(memo, price) {
  const pt = memo && memo.price_targets;
  if (!pt || pt.base == null) return '<span class="muted">—</span>';
  let upside = "";
  if (price != null && Number(price) > 0) {
    const pct = ((pt.base / Number(price) - 1) * 100).toFixed(0);
    upside = ` <span class="muted">(${pct > 0 ? "+" : ""}${pct}%)</span>`;
  }
  return `${fmt(pt.base, 0)}${upside}`;
}

function verdictCard(meta, currentPrice) {
  const pt = meta.price_targets || {};
  const br = meta.buy_range;
  const range = Array.isArray(br) && br.length === 2 ? `${br[0]}–${br[1]}` : "—";
  const prob = (p) => (p == null ? "" : ` <span class="muted">${Math.round(p * 100)}%</span>`);
  const targets = pt.base != null
    ? `bear ${fmt(pt.bear, 0)}${prob(pt.p_bear)} / <b>base ${fmt(pt.base, 0)}</b>${prob(pt.p_base)} / bull ${fmt(pt.bull, 0)}${prob(pt.p_bull)}
       <span class="muted">(${esc(pt.horizon || "12mo")})</span>`
    : "—";
  const ep = meta.entry_plan;
  const entryPlan = ep && Array.isArray(ep.tranches)
    ? `tranches ${ep.tranches.map((t) => fmt(t, 0)).join(" / ")} · <b>invalidation ${fmt(ep.invalidation, 0)}</b>`
    : "—";
  const risks = Array.isArray(meta.top_risks) && meta.top_risks.length
    ? esc(meta.top_risks.join(" · ")) : "—";
  const cells = [
    ["Verdict", `<b>${esc(pretty(meta.action))}</b> <span class="muted">confidence ${esc(meta.confidence || "—")}</span>`],
    ["Price now vs at analysis", `${fmt(currentPrice, 2)} <span class="muted">/ ${fmt(meta.price_at_analysis, 2)}</span>`],
    ["Good-buy range", range],
    ["Price targets", targets],
    ["Entry plan", entryPlan],
    ["Thesis health", esc(meta.thesis_health || "—")],
    ["Multiple basis", esc(meta.multiple_basis || "—")],
    ["Top risks", risks],
    ["Review trigger", esc(meta.review_trigger || "—")],
  ];
  return `<div class="verdict-card">${cells.map(([k, v]) =>
    `<div class="vc-item"><div class="vc-k">${k}</div><div class="vc-v">${v}</div></div>`).join("")}</div>`;
}

function riskRewardTargets(pt, currentPrice) {
  if (!pt || pt.base == null) return '<p class="muted">no price targets on file</p>';
  const rows = ["bear", "base", "bull"].map((tier) => {
    const target = pt[tier];
    const prob = pt[`p_${tier}`];
    const vs = (target != null && currentPrice) ? `${sgn(((target / currentPrice - 1) * 100), 0)}%` : "—";
    return `<tr><td class="muted">${tier}</td><td>${fmt(target, 0)}</td>
      <td>${prob != null ? Math.round(prob * 100) + "%" : "—"}</td><td>${vs}</td></tr>`;
  }).join("");
  return `<table class="rr-table"><tr><th>Tier</th><th>Target</th><th>Prob</th><th>vs current</th></tr>${rows}</table>
    <div class="muted" style="margin-top:4px">horizon ${esc(pt.horizon || "12mo")}</div>`;
}

function riskRewardStats(features) {
  const f = features || {};
  const ev = f.ev_return_pct != null ? quantFactorValue("ev_return_pct", f.ev_return_pct) : "—";
  const rr = f.rr_ratio != null ? quantFactorValue("rr_ratio", f.rr_ratio) : "—";
  return `<div class="rr-row">EV return <b>${ev}</b> <span class="muted">·</span> R:R <b>${rr}</b></div>`;
}

function entryPlanBlock(ep) {
  if (!ep || !Array.isArray(ep.tranches)) return '<span class="muted">no entry plan on file</span>';
  return `tranches <b>${ep.tranches.map((t) => fmt(t, 0)).join(" / ")}</b>
    <span class="muted">·</span> invalidation <b>${fmt(ep.invalidation, 0)}</b>`;
}

function metaTags(meta) {
  const tags = [
    meta.action ? ["action", pretty(meta.action)] : null,
    meta.confidence ? ["confidence", meta.confidence] : null,
    meta.thesis_health ? ["thesis health", pretty(meta.thesis_health)] : null,
  ].filter(Boolean);
  return tags.map(([k, v]) => `<span class="tag-meta">${esc(k)}: ${esc(v)}</span>`).join(" ");
}

function riskTags(risks) {
  if (!Array.isArray(risks) || !risks.length) return '<span class="muted">—</span>';
  return risks.map((r) => `<span class="tag-risk">${esc(pretty(r))}</span>`).join(" ");
}

function riskRewardPanel(meta, currentPrice, features) {
  if (!meta) {
    return `<div class="rr-panel"><h3>Risk / Reward</h3>
      <p class="muted">not analyzed yet — run an analysis to populate targets</p></div>`;
  }
  const br = meta.buy_range;
  const range = Array.isArray(br) && br.length === 2 ? `${fmt(br[0], 0)}–${fmt(br[1], 0)}` : "—";
  return `<div class="rr-panel">
    <h3>Risk / Reward</h3>
    <div class="rr-row">${verdictBadge(meta)} ${metaTags(meta)}</div>
    <div class="rr-row">Price now <b>${fmt(currentPrice, 2)}</b>
      <span class="muted">vs at analysis ${fmt(meta.price_at_analysis, 2)}</span></div>
    <div class="rr-row">Buy range <b>${range}</b></div>
    ${riskRewardTargets(meta.price_targets, currentPrice)}
    ${riskRewardStats(features)}
    <div class="rr-row">Entry plan: ${entryPlanBlock(meta.entry_plan)}</div>
    <div class="rr-row">Top risks: ${riskTags(meta.top_risks)}</div>
    <details class="rr-details"><summary>Review triggers</summary>
      <div class="muted">${esc(meta.review_trigger || "—")}</div></details>
  </div>`;
}

function expandedRowHtml(ticker, stockData) {
  const qt = state.quant && state.quant.tickers && state.quant.tickers[ticker];
  const leftHtml = qt
    ? quantCard(ticker, qt)
    : `<p class="muted">no quant snapshot for ${esc(ticker)}</p>`;
  const memos = (stockData && stockData.memos) || [];
  const latest = memos[0];
  const currentPrice = stockData && stockData.quote ? stockData.quote.price : null;
  const rightHtml = riskRewardPanel(latest ? latest.meta : null, currentPrice, qt && qt.features);
  let bottomHtml = "";
  if (latest) {
    const errs = latest.errors && latest.errors.length
      ? `<div class="badge no">frontmatter warnings: ${esc(latest.errors.join("; "))}</div>` : "";
    bottomHtml = `<details class="panel">
      <summary>Full analysis (latest memo) <span class="muted">${esc(String(latest.meta.date || ""))}</span></summary>
      ${errs}<div class="memo-body">${marked.parse(latest.body)}</div>
    </details>`;
    if (memos.length > 1) {
      const n = memos.length - 1;
      bottomHtml += `<div class="muted" style="margin-top:8px">${n} older memo${n === 1 ? "" : "s"} — see detail view</div>`;
    }
  }
  return `<div class="expand-wrap">
    <div class="expand-grid">
      <div class="expand-col"><h3>Quant model</h3>${leftHtml}</div>
      <div class="expand-col">${rightHtml}</div>
    </div>
    ${bottomHtml}
  </div>`;
}

async function toggleRow(ticker) {
  const detail = document.getElementById(`detail-${ticker}`);
  const chev = document.getElementById(`chev-${ticker}`);
  if (!detail) return;
  const cell = detail.querySelector("td");
  const opening = detail.style.display === "none";
  if (!opening) {
    detail.style.display = "none";
    if (chev) chev.classList.remove("open");
    return;
  }
  if (chev) chev.classList.add("open");
  detail.style.display = "table-row";
  if (!state.stocksCache[ticker]) {
    cell.innerHTML = '<p class="muted">Loading…</p>';
    try {
      state.stocksCache[ticker] = await api(`/api/stocks/${ticker}`);
    } catch (e) {
      cell.innerHTML = `<p class="muted">Failed to load: ${esc(e.message)}</p>`;
      return;
    }
  }
  cell.innerHTML = expandedRowHtml(ticker, state.stocksCache[ticker]);
}

async function renderOverview() {
  const [ov, quant] = await Promise.all([api("/api/overview"), api("/api/quant")]);
  state.quant = quant;
  $("#quotes-age").textContent = ov.quotes_fetched_at
    ? `quotes as of ${new Date(ov.quotes_fetched_at).toLocaleString()}` : "no quote data";
  const byLayer = {};
  for (const r of ov.rows) (byLayer[r.layer || "(no layer)"] ||= []).push(r);
  const layers = Object.keys(byLayer).sort((a, b) => a.localeCompare(b));
  let html = "";
  for (const layer of layers) {
    const rows = byLayer[layer].slice().sort((a, b) => a.ticker.localeCompare(b.ticker));
    html += `<div class="layer-heading">${esc(layer)}</div>`;
    html += `<table><tr><th></th><th>Ticker</th><th>Layer</th><th>Price</th><th>Buy range</th>
             <th>Target (base)</th><th>Range</th><th>Verdict</th><th>Signals</th><th>Setup</th><th>Status</th></tr>`;
    for (const r of rows) {
      const q = r.quote || {};
      const m = r.memo;
      const range = m && m.buy_range ? `${m.buy_range[0]}–${m.buy_range[1]}` : "—";
      const err = r.memo_errors && r.memo_errors.length
        ? ` <span class="badge no" title="${esc(r.memo_errors.join("; "))}">memo format warning</span>` : "";
      html += `<tr class="row-main" onclick="toggleRow('${r.ticker}')">
        <td class="expand-cell"><span class="chevron" id="chev-${r.ticker}">▸</span></td>
        <td><span class="ticker" onclick="event.stopPropagation(); showStock('${r.ticker}')">${r.ticker}</span>${r.holding ? ' <span class="badge ok" title="user holds a position — hold/trim/exit verdicts apply">held</span>' : ""}
            <div class="muted">${esc(r.name)}</div></td>
        <td class="muted">${esc(r.layer)}</td>
        <td>${fmt(q.price, 2)}${q.stale ? (q.price_source
          ? ' <span class="badge warn" title="fundamentals stale — price via Cboe fallback">stale</span>'
          : ' <span class="badge warn">stale</span>') : ""}</td>
        <td>${range}</td>
        <td>${targetCell(m, q.price)}</td>
        <td>${verdictBadge(m)}${err}</td>
        <td>${m ? esc(pretty(m.action)) : "—"}</td>
        <td>${signalDots(m && m.signals)}</td>
        <td>${setupCell(quant, r.ticker)}</td>
        <td>${stalenessNote(r.staleness) || '<span class="muted">·</span>'}</td>
      </tr>
      <tr class="row-detail" id="detail-${r.ticker}" style="display:none"><td colspan="11"></td></tr>`;
    }
    html += "</table>";
  }
  html += `<h2>Analysis commands</h2><p class="muted">Copy into any agent CLI (Claude Code / Codex / Gemini …)</p>`;
  html += cmdBlock(`claude "Read framework/playbooks/scan.md and sweep the whole watchlist"`);
  $("#view").innerHTML = html || "<p class='muted'>Watchlist is empty — add stocks under Manage.</p>";
}

function screenSortByScoreDesc(a, b) {
  // nulls last, same as the CLI's `(score is None, -score)` sort key
  if (a.depression_score == null) return b.depression_score == null ? 0 : 1;
  if (b.depression_score == null) return -1;
  return b.depression_score - a.depression_score;
}

async function renderScreen() {
  const s = await api("/api/screen");
  if (!s.computed_at) {
    $("#view").innerHTML = `<p class="muted">no screen run yet</p>${cmdBlock("luxtock screen")}`;
    return;
  }
  const results = s.results || [];
  const qualified = results.filter((r) => !r.disqualified);
  const disqualified = results.filter((r) => r.disqualified);
  const standard = qualified.filter((r) => r.track !== "hypergrowth").sort(screenSortByScoreDesc);
  const hypergrowth = qualified.filter((r) => r.track === "hypergrowth").sort(screenSortByScoreDesc);

  const universeAgeDays = (Date.now() - Date.parse(s.universe_as_of)) / 86400000;
  const universeStaleBadge = Number.isFinite(universeAgeDays) && universeAgeDays > UNIVERSE_STALE_DAYS
    ? ` <span class="badge warn" title="index membership has likely drifted — regenerate data/universe.json">universe stale</span>`
    : "";

  let html = `<div class="muted">screened ${esc(new Date(s.computed_at).toLocaleString())}
    · universe ${s.universe_size} tickers (as of ${esc(s.universe_as_of)})
    · ${s.stage_a_survivors} passed stage A${universeStaleBadge}</div>`;

  html += `<table><tr><th>Ticker</th><th>Price</th><th>Drawdown</th><th>Track</th>
    <th>Rev 90d</th><th>Fwd P/E</th><th>R/R proxy</th><th>Score</th></tr>`;
  for (const r of standard) {
    const f = r.features || {}, fu = r.fundamentals || {};
    html += `<tr>
      <td>${screenTickerCell(r.ticker)}${screenFetchFailedBadge(r)}</td>
      <td>${fmt(f.price, 2)}</td>
      <td>${fmt(f.drawdown_pct, 1)}%</td>
      <td class="muted">${esc(pretty(r.track))}</td>
      <td>${fu.rev_90d_pct != null ? sgn(fu.rev_90d_pct, 1) + "%" : "—"}</td>
      <td>${fmt(fu.fwd_pe, 1)}</td>
      <td>${screenRrCell(fu.rr_proxy)}</td>
      <td>${screenScoreChip(r)}</td>
    </tr>`;
  }
  html += standard.length ? "</table>" : `</table><p class="muted">no qualified candidates</p>`;

  if (hypergrowth.length) {
    html += `<h3>hypergrowth track — most speculative tier, no earnings anchor:</h3>`;
    html += `<table><tr><th>Ticker</th><th>Price</th><th>Drawdown</th><th>Rev growth</th>
      <th>EV/S</th><th>GS</th><th>Runway</th><th>Score</th></tr>`;
    for (const r of hypergrowth) {
      const f = r.features || {}, fu = r.fundamentals || {};
      const revGrowth = fu.revenue_growth != null ? `${sgn(fu.revenue_growth * 100, 0)}%` : "—";
      const runway = fu.runway_years != null ? `${fmt(fu.runway_years, 1)}y` : "—";
      html += `<tr>
        <td>${screenTickerCell(r.ticker)}${screenFetchFailedBadge(r)}</td>
        <td>${fmt(f.price, 2)}</td>
        <td>${fmt(f.drawdown_pct, 1)}%</td>
        <td>${revGrowth}</td>
        <td>${fmt(fu.ev_sales, 1)}</td>
        <td>${fmt(fu.gs_like, 2)}</td>
        <td>${runway}</td>
        <td>${screenScoreChip(r)}</td>
      </tr>`;
    }
    html += "</table>";
  }

  if (disqualified.length) {
    html += `<details class="panel"><summary>${disqualified.length} disqualified</summary>
      <table><tr><th>Ticker</th><th>Price</th><th>Drawdown</th><th>Flags</th><th>Score</th></tr>`;
    for (const r of disqualified) {
      const f = r.features || {};
      const flags = (r.flags || []).map((fl) => `<span class="tag-risk">${esc(pretty(fl))}</span>`).join(" ")
        || '<span class="muted">—</span>';
      html += `<tr>
        <td>${esc(r.ticker)}${screenFetchFailedBadge(r)}</td>
        <td>${fmt(f.price, 2)}</td>
        <td>${fmt(f.drawdown_pct, 1)}%</td>
        <td>${flags}</td>
        <td>${screenScoreChip(r)}</td>
      </tr>`;
    }
    html += "</table></details>";
  }

  html += `<div class="muted" style="margin-top:12px">candidates only — not analyzed, no verdicts</div>
    <div class="muted">rr_proxy is sell-side-derived, screening signal only</div>`;

  $("#view").innerHTML = html;
}

async function showStock(ticker) {
  state.view = "stock";
  state.ticker = ticker;
  const d = await api(`/api/stocks/${ticker}`);
  const q = d.quote || {}, f = d.flows || {}, sig = (f.signals || {});
  const tr = f.trend || {}, rev = q.revisions || {}, an = q.analyst || {};
  let html = `<h2>${ticker}</h2><div class="panel">
    Price <b>${fmt(q.price, 2)}</b> · ttm P/E ${fmt(q.ttm_pe)} · fwd P/E ${fmt(q.fwd_pe)}
    · 52w ${fmt(q.low_52w)}–${fmt(q.high_52w)}
    · next earnings <b>${esc(q.next_earnings || "—")}</b>
    <div class="muted">Flows: short ${f.short_pct_float != null ? (f.short_pct_float * 100).toFixed(1) + "% of float" : "—"}
    · institutions ${f.inst_pct != null ? (f.inst_pct * 100).toFixed(0) + "%" : "—"}
    · put/call OI ${fmt(f.put_call_oi_ratio, 2)}
    · volume ${sig.accumulation_hint ? "possible accumulation (flat price + net buying)" : "no accumulation pattern"}
    (proxy signals; 13F lags ~45 days)</div>
    <div class="muted">Trend: 50dma ${sgn(tr.dist_50dma_pct)}% · 200dma ${sgn(tr.dist_200dma_pct)}%
    · RSI-14 ${fmt(tr.rsi_14, 0)} · ATR ${fmt(tr.atr_pct_14)}%
    · 3m rel strength ${sgn(tr.rel_strength_3m)}pp vs ${esc(tr.benchmark || "SPY")}</div>
    <div class="muted">Estimates: fwd EPS 90d ${sgn(rev.fwd_eps_change_90d_pct)}%
    · revisions ↑${rev.up_last_30d ?? "—"} / ↓${rev.down_last_30d ?? "—"} (30d)
    · analyst PT ${fmt(an.pt_low, 0)}–${fmt(an.pt_high, 0)} (mean ${fmt(an.pt_mean, 0)}, n=${an.n_analysts ?? "—"}, rec ${fmt(an.rec_mean, 1)})</div></div>`;
  html += cmdBlock(analyzeCmd(ticker));
  html += cmdBlock(`luxtock export ${ticker} --pdf`);
  if (d.memos.length) {
    const dates = d.memos.map((m, i) =>
      `<option value="${i}">${esc(String(m.meta.date || m.path))}</option>`).join("");
    html += `<div class="panel"><label class="muted">Memo history:</label>
      <select id="memo-select" onchange="renderMemoBody()" style="width:auto">${dates}</select>
      <div id="memo-card"></div>
      <div id="memo-body" class="memo-body"></div></div>`;
  } else {
    html += `<p class="muted">No analysis memo yet. Copy the command above and run it.</p>`;
  }
  $("#view").innerHTML = html;
  window._memos = d.memos;
  window._currentPrice = q.price;
  if (d.memos.length) renderMemoBody();
}

function renderMemoBody() {
  const memo = window._memos[Number($("#memo-select").value)];
  const errs = memo.errors.length
    ? `<div class="badge no">frontmatter warnings: ${esc(memo.errors.join("; "))}</div>` : "";
  $("#memo-card").innerHTML = verdictCard(memo.meta, window._currentPrice);
  $("#memo-body").innerHTML = errs + marked.parse(memo.body);
}

async function renderTheses() {
  const theses = await api("/api/theses");
  let html = "";
  for (const t of theses) {
    html += `<h2>${esc(t.meta.name || t.id)}
      <span class="muted">${esc(t.meta.status || "?")} · last audited ${esc(String(t.meta.last_audited || "never"))}</span></h2>
      <div class="panel"><div class="memo-body">${marked.parse(t.body)}</div></div>`;
    html += cmdBlock(`claude "Read framework/playbooks/audit-thesis.md and stress-test thesis ${t.id}"`);
    html += cmdBlock(`claude "Read framework/playbooks/discover.md and screen for new candidates along thesis ${t.id}"`);
  }
  html += `<h2>Create / edit thesis</h2><div class="panel">
    <div class="form-row"><input id="thesis-id" placeholder="id (lowercase-hyphens, e.g. dc-power)"></div>
    <textarea id="thesis-content" placeholder="---&#10;id: dc-power&#10;name: Datacenter power&#10;status: intact&#10;created: ${new Date().toISOString().slice(0, 10)}&#10;---&#10;Narrative, transmission math, load-bearing assumptions, kill conditions, observable indicators"></textarea>
    <br><br><button class="primary" onclick="saveThesis()">Save thesis</button></div>`;
  $("#view").innerHTML = html || "<p class='muted'>No theses yet.</p>";
}

async function saveThesis() {
  const id = $("#thesis-id").value.trim();
  try {
    await api(`/api/theses/${id}`, { method: "PUT",
      body: JSON.stringify({ content: $("#thesis-content").value }) });
    toast(`thesis ${id} saved`);
    renderTheses();
  } catch (e) { toast(`failed: ${e.message}`); }
}

async function renderManage() {
  const ov = await api("/api/overview");
  const thesisOpts = Object.entries(ov.theses)
    .map(([id, m]) => `<option value="${id}">${esc(m.name || id)}</option>`).join("");
  let html = `<h2>Add to watchlist</h2><div class="panel">
    <div class="form-row">
      <input id="add-ticker" placeholder="Ticker, e.g. MU">
      <select id="add-thesis">${thesisOpts || "<option value=''>(create a thesis first)</option>"}</select>
      <input id="add-layer" placeholder="Bottleneck layer, e.g. power-semis">
    </div>
    <div class="form-row">
      <input id="add-name" placeholder="Company name (optional)">
      <input id="add-note" placeholder="One-line note (optional)">
      <input id="add-benchmark" placeholder="RS benchmark, e.g. SMH (default SPY)">
    </div>
    <button class="primary" onclick="addStock()">Add</button></div>
    <h2>Current watchlist</h2><table><tr><th>Ticker</th><th>Thesis</th><th>Layer</th><th></th></tr>`;
  for (const r of ov.rows) {
    html += `<tr><td>${r.ticker}</td><td class="muted">${esc(r.thesis)}</td>
      <td class="muted">${esc(r.layer)}</td>
      <td><button class="ghost" onclick="delStock('${r.ticker}')">Remove</button></td></tr>`;
  }
  html += "</table>";
  $("#view").innerHTML = html;
}

async function addStock() {
  try {
    await api("/api/watchlist", { method: "POST", body: JSON.stringify({
      ticker: $("#add-ticker").value.trim(), thesis: $("#add-thesis").value,
      layer: $("#add-layer").value.trim(), name: $("#add-name").value.trim(),
      note: $("#add-note").value.trim(),
      benchmark: $("#add-benchmark").value.trim() }) });
    toast("added");
    renderManage();
  } catch (e) { toast(`failed: ${e.message}`); }
}

async function delStock(ticker) {
  await api(`/api/watchlist/${ticker}`, { method: "DELETE" });
  toast(`removed ${ticker}`);
  renderManage();
}

function render() {
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === state.view));
  if (state.view === "overview") renderOverview();
  else if (state.view === "screen") renderScreen();
  else if (state.view === "theses") renderTheses();
  else if (state.view === "manage") renderManage();
  else if (state.view === "stock") showStock(state.ticker);
}

document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => { state.view = b.dataset.view; render(); }));

$("#btn-refresh").addEventListener("click", async () => {
  await api("/api/refresh", { method: "POST" });
  toast("background refresh started — updates shortly");
});

setInterval(async () => {
  try {
    const s = await api("/api/status");
    if (s.data_version !== state.dataVersion) {
      const first = state.dataVersion === 0;
      state.dataVersion = s.data_version;
      if (!first) render();
    }
  } catch (_) { /* stay silent while the server is not ready */ }
}, 3000);

render();
