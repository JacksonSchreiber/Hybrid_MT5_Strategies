/* rpaths.js - the "Paths" tab: how trades travel to their exit, as R vs time.
 * Data: /static/rpaths.json (pipeline/rpaths_build.py). Each trade: s symbol, d detector, l long?, y year, o outcome
 * (tp2 | be | sl), tp TP2 in R, f6 best R in the first 6 H4 bars, xb exit time in H4 bars, src source, rt R per closed H4 bar
 * (x100, last = exit level), pg 41 points entry->exit (x100).
 * Layers: heatmap (share of trades in each time x R cell), fan (median + middle 50% / 80% bands), faint sample paths.
 * Drag a box on the chart: the share of the selection whose path passed through it. */
(function () {
  "use strict";
  const C = {
    surface: "#161b22", grid: "#21262d", axis: "#8b949e", text: "#c9d1d9", dim: "#8b949e",
    hues: { tp2: [57, 135, 229], be: [217, 89, 38], sl: [144, 133, 233], all: [57, 135, 229] },   // blue, orange, violet (dark-mode slots 1, 2, 7)
    hueHex: { tp2: "#3987e5", be: "#d95926", sl: "#9085e9", all: "#3987e5" },
    stop: "#f85149", bank: "#8b949e",
  };
  const OUTCOME = { tp2: "TP2 winners", be: "Back to entry", sl: "Stopped out", all: "All trades" };
  const DETS = ["TrendCont", "SweepMSS", "DeepFib", "EMArevQ"];
  const state = {
    show: "tp2", cmp: "none", time: "rt", dir: "all", det: "all", start: "all", period: "all", shade: "open",
    heat: true, fan: true, samples: false, source: "backtest", brush: null, avgCarry: false, avgDays: 10, avgBand: "hl",
  };
  let DATA = null, root = null;

  // ---------------------------------------------------------------- helpers
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const pct = (v, d = 0) => (v * 100).toFixed(d) + "%";
  const fmtR = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2).replace(/\.?0+$/, "") + "R";
  const quant = (arr, q) => { if (!arr.length) return NaN; const a = arr.slice().sort((x, y) => x - y); const i = (a.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i); return a[lo] + (a[hi] - a[lo]) * (i - lo); };
  function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

  function select(outcome) {
    return DATA.trades.filter(t =>
      (state.source === "all" || t.src === state.source) &&
      (outcome === "all" || t.o === outcome) &&
      (state.dir === "all" || (state.dir === "long") === (t.l === 1)) &&
      (state.det === "all" || t.d === state.det) &&
      (state.start === "all" || (state.start === "fast" ? t.f6 >= 1.5 : t.f6 < 1.0)) &&
      (state.period === "all" || (state.period === "dev" ? t.y < 2025 : t.y >= 2025)));
  }
  const pathOf = t => state.time === "rt" ? t.rt : t.pg;
  // x column of path index i: real time = bar i+1 (column 0 is the entry, R 0 for every trade); progress = i of 40
  function geometry(groups) {
    let ncol;
    if (state.time === "pg") ncol = DATA.pg_points;
    else {
      const ex = [].concat(...groups.map(g => g.map(t => t.rt.length)));
      ncol = Math.max(13, Math.min(DATA.cap_bars, Math.ceil(quant(ex, 0.85) || 30))) + 1;   // to where 85% have exited
    }
    let ymax = 2;
    groups.forEach(g => g.forEach(t => { const p = pathOf(t); for (let i = 0; i < p.length; i++) if (p[i] / 100 > ymax) ymax = p[i] / 100; }));
    ymax = Math.min(7, Math.ceil(Math.min(ymax, 6.5) * 2) / 2 + 0.25);
    return { ncol, ymin: -1.25, ymax, dy: 0.25 };
  }
  // value of trade t at column c (null when it has exited)
  function valAt(t, c) {
    const p = pathOf(t);
    if (state.time === "pg") return p[c] / 100;
    if (c === 0) return 0;
    return c - 1 < p.length ? p[c - 1] / 100 : null;
  }
  function stats(group, G) {
    const nb = Math.round((G.ymax - G.ymin) / G.dy);
    const cells = [], alive = [], fans = [];
    for (let c = 0; c < G.ncol; c++) {
      const col = new Array(nb).fill(0), vals = [];
      for (const t of group) {
        const v = valAt(t, c); if (v == null) continue;
        vals.push(v);
        const b = Math.max(0, Math.min(nb - 1, Math.floor((v - G.ymin) / G.dy)));
        col[b]++;
      }
      cells.push(col); alive.push(vals.length);
      fans.push(vals.length >= 5 ? [0.1, 0.25, 0.5, 0.75, 0.9].map(q => quant(vals, q)) : null);
    }
    return { cells, alive, fans, nb, n: group.length };
  }

  // ---------------------------------------------------------------- UI
  function seg(label, key, opts, help) {
    const wrap = el("div", "rp-ctl");
    wrap.appendChild(el("div", "rp-lbl", label + (help ? ` <span class="rp-q" title="${help}">?</span>` : "")));
    const g = el("div", "rp-seg");
    opts.forEach(([v, t, dis]) => {
      const b = el("button", state[key] === v ? "on" : "", t); b.type = "button";
      if (dis) { b.disabled = true; b.title = dis; }
      b.onclick = () => { state[key] = v; state.brush = null; if (key === "show" && state.cmp === v) state.cmp = "none"; render(); };
      g.appendChild(b);
    });
    wrap.appendChild(g); return wrap;
  }
  function toggle(label, key) {
    const b = el("button", "rp-tog" + (state[key] ? " on" : ""), (state[key] ? "✓ " : "") + label); b.type = "button";
    b.onclick = () => { state[key] = !state[key]; render(); }; return b;
  }

  function render() {
    root.innerHTML = "";
    const groups = [{ key: state.show, trades: select(state.show) }];
    if (state.cmp !== "none") groups.push({ key: state.cmp, trades: select(state.cmp) });
    // controls
    const ctl = el("div", "card rp-ctls");
    const nLive = DATA.trades.filter(t => t.src === "live").length;
    ctl.appendChild(seg("Data", "source", [["backtest", "Historical"], ["live", `Live only (${nLive})`, nLive ? null : "no closed live trades yet - they appear here automatically"],
      ["all", "Combined", nLive ? null : "no closed live trades yet"]],
      "Historical: 13 years of backtest trades. Live only: your closed live trades, added automatically every hour. Combined: both together."));
    ctl.appendChild(seg("Show", "show", Object.entries(OUTCOME).map(([k, v]) => [k, v])));
    ctl.appendChild(seg("Compare with", "cmp", [["none", "Nothing"]].concat(Object.entries(OUTCOME).filter(([k]) => k !== "all").map(([k, v]) => [k, v, k === state.show ? "already shown" : null])),
      "Draws a second panel with the same filters, and each group's typical path on both panels"));
    ctl.appendChild(seg("Time axis", "time", [["rt", "Days since entry"], ["pg", "Progress to exit"]],
      "Days: real speed. Progress: every trade stretched to the same length (0% = entry, 100% = the exit), so you compare shapes"));
    const more = el("details", "rp-more"); more.open = window.innerWidth > 700 || root.dataset.more === "1";
    more.ontoggle = () => { root.dataset.more = more.open ? "1" : "0"; };
    more.appendChild(el("summary", "k", "Filters & layers"));
    const mg = el("div", "rp-grid");
    mg.appendChild(seg("Direction", "dir", [["all", "All"], ["long", "Long"], ["short", "Short"]]));
    mg.appendChild(seg("Detector", "det", [["all", "All"]].concat(DETS.map(d => [d, d]))));
    mg.appendChild(seg("Start", "start", [["all", "All"], ["fast", "Fast"], ["slow", "Slow"]],
      "Fast = reached +1.5R within the first 6 H4 bars (one trading day). Slow = stayed under +1R for those 6 bars"));
    mg.appendChild(seg("Period", "period", [["all", "All"], ["dev", "2012–24"], ["hold", "2025–26"]]));
    mg.appendChild(seg("Shading", "shade", [["open", "% of trades still open"], ["all", "% of all trades shown"]],
      "Still open: each time column adds up to 100% of the trades not yet exited. All: share of every trade in the selection (exited trades drop out)"));
    const lay = el("div", "rp-ctl"); lay.appendChild(el("div", "rp-lbl", "Layers"));
    const lg = el("div", "rp-seg"); lg.appendChild(toggle("Heatmap", "heat")); lg.appendChild(toggle("Typical path", "fan")); lg.appendChild(toggle("Sample paths", "samples"));
    lay.appendChild(lg); mg.appendChild(lay);
    more.appendChild(mg); ctl.appendChild(more);
    root.appendChild(ctl);

    if (!groups[0].trades.length) { root.appendChild(el("div", "card k", "No trades match these filters.")); return; }
    const G = geometry(groups.map(g => g.trades));
    const S = groups.map(g => ({ ...g, st: stats(g.trades, G) }));
    // tiles
    const tiles = el("div", "rp-tiles");
    S.forEach(g => {
      const ex = g.trades.map(t => t.xb / 6), below = g.trades.filter(t => pathOf(t).some(v => v < 0)).length;
      const t = el("div", "card rp-tile");
      t.innerHTML = `<div class="rp-sw" style="background:${C.hueHex[g.key]}"></div><div><div class="k">${OUTCOME[g.key]}</div>` +
        `<div class="big v">${g.trades.length.toLocaleString()}</div><div class="k">trades · median exit <b>${quant(ex, 0.5).toFixed(1)} days</b> (mean ${(ex.reduce((a, b) => a + b, 0) / ex.length).toFixed(1)}) · ${pct(below / g.trades.length)} went below entry at some point</div></div>`;
      tiles.appendChild(t);
    });
    root.appendChild(tiles);
    // charts
    const panels = el("div", "rp-panels" + (S.length > 1 ? " two" : ""));
    const vmax = Math.max(...S.map(g => maxShare(g.st, G)));
    S.forEach((g, i) => panels.appendChild(chartCard(g, S, i, G, vmax)));
    root.appendChild(panels);
    // brush readout
    const ro = el("div", "card rp-readout");
    if (state.brush) {
      const b = state.brush;
      const lines = S.map(g => { const h = brushHits(g.trades, b, G); return `<div><span class="rp-sw sm" style="background:${C.hueHex[g.key]}"></span><b>${pct(h / g.trades.length, 1)}</b> of the ${g.trades.length.toLocaleString()} ${g.key === "tp2" ? "TP2 winners" : OUTCOME[g.key].toLowerCase()} (${h}) passed through this box</div>`; }).join("");
      ro.innerHTML = `<div class="row"><div><div class="k">Selected box: ${fmtR(b.r0)} to ${fmtR(b.r1)}, ${xLabel(b.c0, G)} to ${xLabel(b.c1, G)}</div>${lines}</div>` +
        `<button type="button" class="btn rp-clear" style="margin-left:auto;padding:6px 12px">Clear</button></div>`;
      ro.querySelector(".rp-clear").onclick = () => { state.brush = null; render(); };
    } else ro.innerHTML = `<div class="k">Tip: <b>drag a box</b> on the chart to see what share of the trades passed through that area. Hover any cell for its numbers.</div>`;
    root.appendChild(ro);
    root.appendChild(avgCard());
    // table view
    root.appendChild(tableView(S, G));
    const nh = DATA.trades.filter(t => t.src !== "live").length, nl = DATA.trades.length - nh;
    root.appendChild(el("div", "k rp-foot", `Historical: ${nh.toLocaleString()} trades (all four detectors, 7 symbols, 2012–2026) replayed on M1 with the live stop rules (bank at +1R → stop to entry, shorts' stop to +0.25R at +1.5R, runner to TP2), built ${DATA.built}. ` +
      `Live: ${nl} closed live trade${nl === 1 ? "" : "s"} from the journal and the MT5 feed${DATA.live_built ? ", updated " + DATA.live_built : ""} (refreshed hourly). The line is the price in the trade's own R from the signal's entry.`));
  }
  function maxShare(st, G) {
    let m = 0.0001;
    const last = state.time === "pg" ? G.ncol - 1 : G.ncol;   // entry and (in progress time) exit columns are certainties, not information
    for (let c = 1; c < last; c++) { const den = state.shade === "open" ? st.alive[c] : st.n; if (den < 5) continue; for (const v of st.cells[c]) m = Math.max(m, v / den); }
    return m;
  }
  function xLabel(c, G) { return state.time === "pg" ? Math.round(c / (G.ncol - 1) * 100) + "%" : "day " + (c / 6).toFixed(1); }
  function brushHits(trades, b, G) {
    let h = 0;
    for (const t of trades) { for (let c = Math.max(0, b.c0); c <= Math.min(G.ncol - 1, b.c1); c++) { const v = valAt(t, c); if (v != null && v >= b.r0 && v <= b.r1) { h++; break; } } }
    return h;
  }

  function chartCard(g, S, idx, G, vmax) {
    const card = el("div", "card rp-chart");
    card.appendChild(el("div", "row", `<span class="rp-sw" style="background:${C.hueHex[g.key]}"></span><b>${OUTCOME[g.key]}</b><span class="k">${g.trades.length.toLocaleString()} trades</span>`));
    const box = el("div", "rp-cv"); const cv = el("canvas"); const tip = el("div", "rp-tip"); box.appendChild(cv); box.appendChild(tip); card.appendChild(box);
    card.appendChild(legend(g.key, vmax));
    requestAnimationFrame(() => draw(cv, tip, g, S, idx, G, vmax));
    return card;
  }
  function legend(key, vmax) {
    const d = el("div", "rp-leg");
    const stops = [0, 0.25, 0.5, 0.75, 1].map(f => heatColor(key, f * vmax, vmax)).join(",");
    d.innerHTML = `<span class="k">${state.shade === "open" ? "share of trades still open" : "share of all trades shown"}</span>` +
      `<span class="rp-ramp" style="background:linear-gradient(90deg,${stops})"></span><span class="k">0% – ${pct(vmax, 0)}</span>` +
      (state.fan ? `<span class="rp-key"><i class="med"></i>typical path (median)</span><span class="rp-key"><i class="b50"></i>middle 50%</span><span class="rp-key"><i class="b80"></i>middle 80%</span>` : "");
    return d;
  }
  function heatColor(key, v, vmax) {
    const a = Math.pow(Math.max(0, Math.min(1, v / vmax)), 0.6);
    const [r, g, b] = C.hues[key]; const s = [22, 27, 34];
    const w = Math.max(0, a - 0.75) / 0.25 * 0.45;              // top quarter brightens toward white for contrast on dark
    const ch = i => Math.round((1 - a) * s[i] + a * ([r, g, b][i] + (255 - [r, g, b][i]) * w));
    return `rgb(${ch(0)},${ch(1)},${ch(2)})`;
  }

  function draw(cv, tip, g, S, idx, G, vmax) {
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    const pad = { l: 44, r: 14, t: 10, b: state.time === "rt" ? 58 : 30 };
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const X = c => pad.l + (c / (G.ncol - 1)) * pw, Y = r => pad.t + (1 - (r - G.ymin) / (G.ymax - G.ymin)) * ph;
    const st = g.st, colW = pw / (G.ncol - 1), rowH = ph / st.nb;
    ctx.fillStyle = C.surface; ctx.fillRect(0, 0, W, H);
    // heatmap
    if (state.heat) {
      for (let c = 0; c < G.ncol; c++) {
        const den = state.shade === "open" ? st.alive[c] : st.n; if (!den) continue;
        ctx.globalAlpha = Math.min(1, 0.25 + st.alive[c] / 40);              // columns with few trades left fade out
        for (let b = 0; b < st.nb; b++) {
          const v = st.cells[c][b] / den; if (v <= 0) continue;
          ctx.fillStyle = heatColor(g.key, v, vmax);
          ctx.fillRect(X(c) - colW / 2, Y(G.ymin + (b + 1) * G.dy), Math.ceil(colW) + 0.5, Math.ceil(rowH) + 0.5);
        }
      }
    }
    ctx.globalAlpha = 1;
    // grid + reference lines
    ctx.font = "11px -apple-system,Segoe UI,Roboto,sans-serif"; ctx.textBaseline = "middle";
    for (let r = Math.ceil(G.ymin); r <= G.ymax; r++) {
      ctx.strokeStyle = r === 0 ? "#6e7681" : C.grid; ctx.lineWidth = r === 0 ? 1.2 : 1; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(pad.l, Y(r)); ctx.lineTo(W - pad.r, Y(r)); ctx.stroke();
      ctx.fillStyle = C.dim; ctx.textAlign = "right"; ctx.fillText((r > 0 ? "+" : "") + r + "R", pad.l - 6, Y(r));
    }
    const ref = (r, col, label) => { ctx.strokeStyle = col; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(pad.l, Y(r)); ctx.lineTo(W - pad.r, Y(r)); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = col; ctx.textAlign = "left"; ctx.fillText(label, pad.l + 4, Y(r) - 7); };
    ref(-1, C.stop, "stop −1R"); ref(1, C.bank, "bank +1R");
    // x axis
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = C.dim;
    if (state.time === "pg") { [0, 25, 50, 75, 100].forEach(p => { ctx.textAlign = p === 100 ? "right" : p === 0 ? "left" : "center"; ctx.fillText(p === 0 ? "entry" : p === 100 ? "exit" : p + "%", X(p / 100 * (G.ncol - 1)), pad.t + ph + 6); }); ctx.textAlign = "center"; }
    else {
      const days = (G.ncol - 1) / 6, narrow = pw < 520, room = Math.max(2, Math.floor(pw / (narrow ? 38 : 60)));
      const step = [1, 2, 3, 5, 10, 15].find(k => days / k <= room) || 20;
      for (let d = 0; d <= days + 1e-9; d += step) { const lb = d === 0 ? (narrow ? "0" : "entry") : (narrow ? d + "d" : "day " + d);
        ctx.textAlign = X(d * 6) + ctx.measureText(lb).width / 2 > W - 2 ? "right" : "center"; ctx.fillText(lb, Math.min(X(d * 6), W - 2), pad.t + ph + 6); }
      ctx.textAlign = "center";
    }
    // strip: share of the group still open at each moment (real time only - in progress time every trade is open until 100%)
    if (state.time === "rt") {
      const sy = pad.t + ph + 24, sh = 22;
      ctx.fillStyle = "rgba(139,148,158,0.10)"; ctx.fillRect(pad.l, sy, pw, sh);
      ctx.fillStyle = "rgba(139,148,158,0.45)"; ctx.beginPath(); ctx.moveTo(X(0), sy + sh);
      for (let c = 0; c < G.ncol; c++) ctx.lineTo(X(c), sy + sh - (st.alive[c] / st.n) * sh);
      ctx.lineTo(X(G.ncol - 1), sy + sh); ctx.closePath(); ctx.fill();
      ctx.fillStyle = C.dim; ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillText("open", pad.l - 6, sy + sh / 2);
      const last = st.alive[G.ncol - 1]; ctx.textAlign = "right"; ctx.fillStyle = C.text; ctx.fillText(pct(last / st.n) + " still open here →", W - pad.r - 6, sy + sh / 2);
    }
    // sample paths
    if (state.samples) {
      const rnd = rng(7 + idx), pick = g.trades.slice().sort(() => rnd() - 0.5).slice(0, 40);
      ctx.strokeStyle = "rgba(230,237,243,0.22)"; ctx.lineWidth = 1;
      pick.forEach(t => { ctx.beginPath(); let st0 = true; for (let c = 0; c < G.ncol; c++) { const v = valAt(t, c); if (v == null) break; const y = Y(Math.max(G.ymin, Math.min(G.ymax, v))); if (st0) { ctx.moveTo(X(c), y); st0 = false; } else ctx.lineTo(X(c), y); } ctx.stroke(); });
    }
    // fan
    if (state.fan) {
      const band = (lo, hi, fill) => { ctx.fillStyle = fill; ctx.beginPath(); let started = false, last = 0;
        for (let c = 0; c < G.ncol; c++) { const f = st.fans[c]; if (!f) break; const x = X(c), y = Y(f[hi]); if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); last = c; }
        for (let c = last; c >= 0; c--) { const f = st.fans[c]; if (f) ctx.lineTo(X(c), Y(f[lo])); }
        ctx.closePath(); ctx.fill(); };
      band(0, 4, "rgba(230,237,243,0.07)"); band(1, 3, "rgba(230,237,243,0.13)");
      const line = (fans, col, dash, w) => { ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash); ctx.beginPath(); let s0 = true;
        for (let c = 0; c < G.ncol; c++) { const f = fans[c]; if (!f) break; if (s0) { ctx.moveTo(X(c), Y(f[2])); s0 = false; } else ctx.lineTo(X(c), Y(f[2])); } ctx.stroke(); ctx.setLineDash([]); };
      S.forEach((o, j) => { if (j !== idx) line(o.st.fans, C.hueHex[o.key], [6, 4], 2); });   // the other group's typical path, dashed in its colour
      line(st.fans, "#f0f6fc", [], 2.2);
    }
    // brush rectangle
    const drawBrush = b => { if (!b) return; ctx.save(); ctx.strokeStyle = "#f0f6fc"; ctx.lineWidth = 1.5; ctx.setLineDash([5, 3]); ctx.fillStyle = "rgba(240,246,252,0.08)";
      const x0 = X(b.c0) - colW / 2, x1 = X(b.c1) + colW / 2, y0 = Y(b.r1), y1 = Y(b.r0); ctx.fillRect(x0, y0, x1 - x0, y1 - y0); ctx.strokeRect(x0, y0, x1 - x0, y1 - y0); ctx.restore(); };
    drawBrush(state.brush);
    // interaction: hover tooltip + drag box (pointer events: mouse and touch)
    const toCell = e => { const r = cv.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top;
      const c = Math.round((x - pad.l) / pw * (G.ncol - 1)); const rv = G.ymin + (1 - (y - pad.t) / ph) * (G.ymax - G.ymin);
      return { c: Math.max(0, Math.min(G.ncol - 1, c)), r: rv, x, y, inside: x >= pad.l && x <= W - pad.r && y >= pad.t && y <= H - pad.b }; };
    // the drag lives on the canvas: a redraw during the drag re-binds these handlers and must not lose it
    cv.onpointerdown = e => { const p = toCell(e); if (!p.inside) return; cv.__drag = { c: p.c, r: p.r }; try { cv.setPointerCapture(e.pointerId); } catch (_) {} };
    cv.onpointermove = e => {
      const p = toCell(e);
      if (cv.__drag) { tip.style.display = "none"; state.brush = norm(cv.__drag, p); redrawAll(); return; }
      if (!p.inside) { tip.style.display = "none"; return; }
      const b = Math.max(0, Math.min(st.nb - 1, Math.floor((p.r - G.ymin) / G.dy)));
      const den = state.shade === "open" ? st.alive[p.c] : st.n, n = st.cells[p.c][b], f = st.fans[p.c];
      const lo = G.ymin + b * G.dy, hi = lo + G.dy;
      tip.innerHTML = `<b>${xLabel(p.c, G)}</b>${state.time === "rt" && p.c ? ` <span class="k">(H4 bar ${p.c})</span>` : ""}<br>${fmtR(lo)} to ${fmtR(hi)}: <b>${den ? pct(n / den, 1) : "–"}</b> of ${state.shade === "open" ? "trades still open" : "all shown"} <span class="k">(${n} of ${den})</span><br>` +
        `<span class="k">still open: ${st.alive[p.c]} of ${st.n} (${pct(st.alive[p.c] / st.n)})${f ? ` · median ${fmtR(+f[2].toFixed(2))} · middle 50% ${fmtR(+f[1].toFixed(2))}…${fmtR(+f[3].toFixed(2))}` : ""}</span>`;
      tip.style.display = "block";
      const tw = tip.offsetWidth; tip.style.left = Math.min(W - tw - 4, Math.max(4, p.x + 12)) + "px"; tip.style.top = Math.max(4, p.y - tip.offsetHeight - 10) + "px";
    };
    cv.onpointerup = e => { if (!cv.__drag) return; const p = toCell(e); const b = norm(cv.__drag, p); cv.__drag = null;
      state.brush = (b.c1 - b.c0 >= 0 && b.r1 - b.r0 > 0.05) ? b : null; render(); };
    cv.onpointerleave = () => { if (!cv.__drag) tip.style.display = "none"; };
    function norm(a, p) { return { c0: Math.min(a.c, p.c), c1: Math.max(a.c, p.c), r0: Math.max(G.ymin, Math.min(a.r, p.r)), r1: Math.min(G.ymax, Math.max(a.r, p.r)) }; }
    function redrawAll() { document.querySelectorAll(".rp-chart canvas").forEach(x => x.__redraw && x.__redraw()); }
    cv.__redraw = () => draw(cv, tip, g, S, idx, G, vmax);
  }

  // ---------------------------------------------------------------- average R every half day, by outcome (trader 2026-10-02)
  function avgCard() {
    const card = el("div", "card rp-avg");
    card.appendChild(el("div", "row", `<b>Average R every half day</b><span class="k">same filters as above · days since entry</span>`));
    const ctl = el("div", "rp-grid");
    const segs = el("div", "rp-ctl"); segs.appendChild(el("div", "rp-lbl", `After a trade exits <span class="rp-q" title="Still open only: the average at day 2 is over the trades not yet closed. Keep at exit level: a closed trade stays in the average at the level it exited (TP2, entry or -1R), so every line covers the whole group.">?</span>`));
    const g1 = el("div", "rp-seg");
    [[false, "Still open only"], [true, "Keep at exit level"]].forEach(([v, t]) => { const b = el("button", state.avgCarry === v ? "on" : "", t); b.type = "button"; b.onclick = () => { state.avgCarry = v; render(); }; g1.appendChild(b); });
    segs.appendChild(g1); ctl.appendChild(segs);
    const sd = el("div", "rp-ctl"); sd.appendChild(el("div", "rp-lbl", "Days shown")); const g2 = el("div", "rp-seg");
    [5, 10, 20, 30].forEach(v => { const b = el("button", state.avgDays === v ? "on" : "", v + " days"); b.type = "button"; b.onclick = () => { state.avgDays = v; render(); }; g2.appendChild(b); });
    sd.appendChild(g2); ctl.appendChild(sd);
    const bd = el("div", "rp-ctl"); bd.appendChild(el("div", "rp-lbl", `Spread band <span class="rp-q" title="Typical high & low: the band's top is the average of the trades sitting ABOVE the line at that moment, its bottom the average of those BELOW - how far a typical trade sits from the average, up and down (it can be lopsided). ±1 standard deviation: the usual symmetric spread; about two thirds of trades sit inside it.">?</span>`));
    const g3 = el("div", "rp-seg");
    [["off", "Off"], ["hl", "Typical high & low"], ["sd", "±1 standard deviation"]].forEach(([v, t]) => { const b = el("button", state.avgBand === v ? "on" : "", t); b.type = "button"; b.onclick = () => { state.avgBand = v; render(); }; g3.appendChild(b); });
    bd.appendChild(g3); ctl.appendChild(bd); card.appendChild(ctl);
    const groups = ["tp2", "be", "sl"].map(k => ({ key: k, trades: select(k) })).filter(g => g.trades.length);
    const steps = Math.min(state.avgDays * 2, Math.floor(DATA.cap_bars / 3));
    const series = groups.map(g => ({ key: g.key, pts: [...Array(steps + 1).keys()].map(i => {
      const bar = i * 3, vals = [];
      for (const t of g.trades) {
        const v = bar === 0 ? 0 : (bar - 1 < t.rt.length ? t.rt[bar - 1] / 100 : (state.avgCarry ? t.rt[t.rt.length - 1] / 100 : null));
        if (v != null) vals.push(v);
      }
      const n = vals.length; if (n < 5) return { day: i / 2, mean: null, n, of: g.trades.length };
      const mean = vals.reduce((a, b) => a + b, 0) / n;
      const sd = Math.sqrt(vals.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n);
      const up = vals.filter(v => v > mean), dn = vals.filter(v => v < mean);
      const hi = up.length ? up.reduce((a, b) => a + b, 0) / up.length : mean, lo = dn.length ? dn.reduce((a, b) => a + b, 0) / dn.length : mean;
      return { day: i / 2, mean, n, of: g.trades.length, sd, hi, lo };
    }) }));
    const box = el("div", "rp-cv"); const cv = el("canvas"); cv.style.height = "clamp(260px,40vw,380px)"; const tip = el("div", "rp-tip"); box.appendChild(cv); box.appendChild(tip); card.appendChild(box);
    const leg = el("div", "rp-leg"); series.forEach(sr => leg.appendChild(el("span", "rp-key", `<i style="background:${C.hueHex[sr.key]};height:3px"></i>${OUTCOME[sr.key]} (${sr.pts[0].of.toLocaleString()})`)));
    card.appendChild(leg);
    card.appendChild(el("div", "k", (state.avgCarry ? "Every trade stays in its line after it closes, at its exit level - so the lines end where each group finishes on average."
      : "Each point averages the trades of that group still open at that moment (points with fewer than 5 open trades are left out).")
      + (state.avgBand === "hl" ? " Shaded: the typical high and low - the average of the trades above the line, and of those below it."
         : state.avgBand === "sd" ? " Shaded: ±1 standard deviation around the line (about two thirds of trades sit inside it)." : "")));
    requestAnimationFrame(() => drawAvg(cv, tip, series, steps));
    return card;
  }
  function drawAvg(cv, tip, series, steps) {
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr; const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    const pad = { l: 44, r: 14, t: 12, b: 28 }, pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const bandOf = p => state.avgBand === "hl" ? [p.lo, p.hi] : state.avgBand === "sd" ? [p.mean - p.sd, p.mean + p.sd] : [p.mean, p.mean];
    let lo = -1, hi = 1; series.forEach(sr => sr.pts.forEach(p => { if (p.mean != null) { const [a, b] = bandOf(p); lo = Math.min(lo, a); hi = Math.max(hi, b); } }));
    lo = Math.floor(lo * 2) / 2 - 0.25; hi = Math.ceil(hi * 2) / 2 + 0.25;
    const X = d => pad.l + (d / (steps / 2)) * pw, Y = r => pad.t + (1 - (r - lo) / (hi - lo)) * ph;
    ctx.fillStyle = C.surface; ctx.fillRect(0, 0, W, H);
    ctx.font = "11px -apple-system,Segoe UI,Roboto,sans-serif"; ctx.textBaseline = "middle";
    const ystep = hi - lo > 4 ? 1 : 0.5;
    for (let r = Math.ceil(lo / ystep) * ystep; r <= hi + 1e-9; r += ystep) {
      ctx.strokeStyle = Math.abs(r) < 1e-9 ? "#6e7681" : C.grid; ctx.lineWidth = Math.abs(r) < 1e-9 ? 1.2 : 1;
      ctx.beginPath(); ctx.moveTo(pad.l, Y(r)); ctx.lineTo(W - pad.r, Y(r)); ctx.stroke();
      ctx.fillStyle = C.dim; ctx.textAlign = "right"; ctx.fillText(fmtR(+r.toFixed(2)).replace("0R", "0R"), pad.l - 6, Y(r));
    }
    const days = steps / 2, narrow = pw < 520, room = Math.max(2, Math.floor(pw / (narrow ? 38 : 60)));
    const xs = [0.5, 1, 2, 5, 10].find(k => days / k <= room) || 10;
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = C.dim;
    for (let d = 0; d <= days + 1e-9; d += xs) { const lb = d === 0 ? (narrow ? "0" : "entry") : (narrow ? d + "d" : "day " + d);
      ctx.textAlign = X(d) + ctx.measureText(lb).width / 2 > W - 2 ? "right" : "center"; ctx.fillText(lb, Math.min(X(d), W - 2), pad.t + ph + 8); }
    if (state.avgBand !== "off") series.forEach(sr => {
      const pts = sr.pts.filter(p => p.mean != null); if (pts.length < 2) return;
      const [r, g, b] = C.hues[sr.key];
      ctx.fillStyle = `rgba(${r},${g},${b},0.16)`; ctx.beginPath();
      pts.forEach((p, i) => { const y = Y(bandOf(p)[1]); i ? ctx.lineTo(X(p.day), y) : ctx.moveTo(X(p.day), y); });
      for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(X(pts[i].day), Y(bandOf(pts[i])[0]));
      ctx.closePath(); ctx.fill();
      ctx.strokeStyle = `rgba(${r},${g},${b},0.55)`; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      [1, 0].forEach(e => { ctx.beginPath(); pts.forEach((p, i) => { const y = Y(bandOf(p)[e]); i ? ctx.lineTo(X(p.day), y) : ctx.moveTo(X(p.day), y); }); ctx.stroke(); });
      ctx.setLineDash([]);
    });
    series.forEach(sr => {
      ctx.strokeStyle = C.hueHex[sr.key]; ctx.lineWidth = 2; ctx.beginPath(); let started = false;
      sr.pts.forEach(p => { if (p.mean == null) { started = false; return; } if (!started) { ctx.moveTo(X(p.day), Y(p.mean)); started = true; } else ctx.lineTo(X(p.day), Y(p.mean)); });
      ctx.stroke();
      sr.pts.forEach(p => { if (p.mean == null) return; ctx.fillStyle = C.surface; ctx.beginPath(); ctx.arc(X(p.day), Y(p.mean), 4, 0, 7); ctx.fill();
        ctx.fillStyle = C.hueHex[sr.key]; ctx.beginPath(); ctx.arc(X(p.day), Y(p.mean), 3, 0, 7); ctx.fill(); });
    });
    const show = e => {
      const r = cv.getBoundingClientRect(), x = e.clientX - r.left;
      const i = Math.max(0, Math.min(steps, Math.round((x - pad.l) / pw * steps)));
      drawAvgBase(); ctx.strokeStyle = "rgba(240,246,252,0.35)"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(X(i / 2), pad.t); ctx.lineTo(X(i / 2), pad.t + ph); ctx.stroke(); ctx.setLineDash([]);
      tip.innerHTML = `<b>${i === 0 ? "entry" : "day " + (i / 2).toFixed(1)}</b><br>` + series.map(sr => { const p = sr.pts[i];
        const band = p.mean == null || state.avgBand === "off" ? "" : state.avgBand === "hl"
          ? ` <span class="k">· typical high ${fmtR(+p.hi.toFixed(2))} / low ${fmtR(+p.lo.toFixed(2))}</span>` : ` <span class="k">· ±${p.sd.toFixed(2)}R (1 SD)</span>`;
        return `<span class="rp-sw sm" style="background:${C.hueHex[sr.key]}"></span>${OUTCOME[sr.key]}: <b>${p.mean == null ? "–" : fmtR(+p.mean.toFixed(2))}</b>${band} <span class="k">(${p.n} of ${p.of}${state.avgCarry ? "" : " still open"})</span>`; }).join("<br>");
      tip.style.display = "block"; const tw = tip.offsetWidth;
      tip.style.left = Math.min(W - tw - 4, Math.max(4, X(i / 2) + 12)) + "px"; tip.style.top = (pad.t + 6) + "px";
    };
    let base = null; const drawAvgBase = () => { if (base) ctx.putImageData(base, 0, 0); };
    base = ctx.getImageData(0, 0, cv.width, cv.height);
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.scale(dpr, dpr);
    cv.onpointermove = show; cv.onpointerdown = show; cv.onpointerleave = () => { tip.style.display = "none"; drawAvgBase(); };
  }

  function tableView(S, G) {
    const d = el("details", "card rp-table"); d.appendChild(el("summary", "k", "Data table (the typical path by time)"));
    const step = state.time === "pg" ? 4 : (G.ncol > 60 ? 6 : 3);
    let h = `<table><tr><th>${state.time === "pg" ? "progress" : "time"}</th>`;
    S.forEach(g => h += `<th>${OUTCOME[g.key]}: still open</th><th>10%</th><th>25%</th><th>median</th><th>75%</th><th>90%</th>`);
    h += "</tr>";
    for (let c = 0; c < G.ncol; c += step) {
      h += `<tr><td>${xLabel(c, G)}</td>`;
      S.forEach(g => { const f = g.st.fans[c]; h += `<td>${g.st.alive[c]}</td>` + (f ? f.map(v => `<td>${fmtR(+v.toFixed(2))}</td>`).join("") : "<td colspan=5 class=k>too few</td>"); });
      h += "</tr>";
    }
    d.appendChild(el("div", "rp-tablewrap", h + "</table>")); return d;
  }

  // ---------------------------------------------------------------- boot
  window.RPaths = {
    mount(id, url) {
      root = document.getElementById(id);
      root.innerHTML = '<div class="card k">Loading the trade paths…</div>';
      fetch(url).then(r => r.json()).then(d => { DATA = d; render(); window.addEventListener("resize", () => { clearTimeout(window.__rpT); window.__rpT = setTimeout(render, 150); }); })
        .catch(e => { root.innerHTML = '<div class="card bad">Could not load the path data: ' + e + "</div>"; });
    },
  };
})();
