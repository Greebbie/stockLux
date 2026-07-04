/* StockLux dashboard — talks only to the backend API; no LLM dependency. */
const $ = (sel) => document.querySelector(sel);
const state = { view: "overview", ticker: null, dataVersion: 0 };

const SIG_LABEL = { chain: "chain", narrative: "narrative", fundamentals: "fundamentals",
                    valuation: "valuation", flows: "flows", sentiment: "sentiment",
                    competition: "competition", macro: "macro" };

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

async function renderOverview() {
  const ov = await api("/api/overview");
  $("#quotes-age").textContent = ov.quotes_fetched_at
    ? `quotes as of ${new Date(ov.quotes_fetched_at).toLocaleString()}` : "no quote data";
  const byThesis = {};
  for (const r of ov.rows) (byThesis[r.thesis] ||= []).push(r);
  let html = "";
  for (const [thesis, rows] of Object.entries(byThesis)) {
    const meta = ov.theses[thesis] || {};
    html += `<h2>${esc(meta.name || thesis)} <span class="muted">thesis: ${esc(thesis)} · ${esc(meta.status || "?")}</span></h2>`;
    html += `<table><tr><th>Ticker</th><th>Layer</th><th>Price</th><th>Buy range</th>
             <th>Target (base)</th><th>Range</th><th>Verdict</th><th>Signals</th><th>Status</th></tr>`;
    for (const r of rows) {
      const q = r.quote || {};
      const m = r.memo;
      const range = m && m.buy_range ? `${m.buy_range[0]}–${m.buy_range[1]}` : "—";
      const err = r.memo_errors && r.memo_errors.length
        ? ` <span class="badge no" title="${esc(r.memo_errors.join("; "))}">memo format warning</span>` : "";
      html += `<tr>
        <td><span class="ticker" onclick="showStock('${r.ticker}')">${r.ticker}</span>${r.holding ? ' <span class="badge ok" title="user holds a position — hold/trim/exit verdicts apply">held</span>' : ""}
            <div class="muted">${esc(r.name)}</div></td>
        <td class="muted">${esc(r.layer)}</td>
        <td>${fmt(q.price, 2)}${q.stale ? ' <span class="badge warn">stale</span>' : ""}</td>
        <td>${range}</td>
        <td>${targetCell(m, q.price)}</td>
        <td>${verdictBadge(m)}${err}</td>
        <td>${m ? esc(pretty(m.action)) : "—"}</td>
        <td>${signalDots(m && m.signals)}</td>
        <td>${stalenessNote(r.staleness) || '<span class="muted">·</span>'}</td>
      </tr>`;
    }
    html += "</table>";
  }
  html += `<h2>Analysis commands</h2><p class="muted">Copy into any agent CLI (Claude Code / Codex / Gemini …)</p>`;
  html += cmdBlock(`claude "Read framework/playbooks/scan.md and sweep the whole watchlist"`);
  $("#view").innerHTML = html || "<p class='muted'>Watchlist is empty — add stocks under Manage.</p>";
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
  html += cmdBlock(`stocklux export ${ticker} --pdf`);
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
