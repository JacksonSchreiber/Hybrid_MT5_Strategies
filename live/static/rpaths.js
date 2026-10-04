/* rpaths.js - the "Paths" tab: how trades travel to their exit, as R vs time.
 * Data: /static/rpaths.json (pipeline/rpaths_build.py). Each trade: s symbol, d detector, l long?, y year, o outcome
 * (tp2 | be | sl), tp TP2 in R, f6 best R in the first 6 H4 bars, xb exit time in H4 bars, src source, rt R per closed H4 bar
 * (x100, last = exit level), pg 41 points entry->exit (x100).
 * Layers: heatmap (share of trades in each time x R cell), fan (median + middle 50% / 80% bands), faint sample paths.
 * Drag a box on the chart: the share of the selection whose path passed through it.
 * 2026-10-03: all 16 downloaded symbols (dt date, g 0 original / 1 new, p partial years, rb / ra full-position R x1000 under the live
 * stack / live + A+B+I); a Symbols show/hide filter on every chart (remembered per browser); the "By symbol" card.
 * 2026-10-03 (research tool): EVERY chart follows the management dials. Historical trades carry only their price path (rt, x100, long
 * enough for any combo's exit); a dial setting fetches that combo's exits for every trade from /combo/<build>/<index> (9-byte records:
 * R int16 x1000, exit H4 bars uint16 x10, exit kind, event bars for the staged add / bank / pyramid / I add - pipeline/combo_pack.py).
 * Y axis: the price (as before) or the position's value (rebuilt from the events; its last point is the exact R). Research lock: hide
 * 2025-26 and the nine new symbols while forming an idea. Live trades always show what actually happened. */
(function () {
  "use strict";
  const C = {
    surface: "#161b22", grid: "#21262d", axis: "#8b949e", text: "#c9d1d9", dim: "#8b949e",
    hues: { tp2: [57, 135, 229], be: [217, 89, 38], sl: [144, 133, 233], cut: [46, 168, 140], all: [57, 135, 229] },   // blue, orange, violet, teal
    hueHex: { tp2: "#3987e5", be: "#d95926", sl: "#9085e9", cut: "#2ea88c", all: "#3987e5" },
    stop: "#f85149", bank: "#8b949e",
  };
  const OUTCOME = { tp2: "TP2 winners", be: "Back to entry", sl: "Stopped out", cut: "Cut at day 2", all: "All trades" };
  const KIND_OUT = ["sl", "be", "be", "tp2", "cut", "end"];          // combo exit kind -> outcome group ("end" = still open at the window end: All only)
  const DETS = ["TrendCont", "SweepMSS", "DeepFib", "EMArevQ"];
  const state = {
    show: "tp2", cmp: "none", time: "rt", dir: "all", det: "all", start: "all", period: "all", shade: "open",
    heat: true, fan: true, samples: false, source: "backtest", brush: null, avgCarry: false, avgDays: 10, avgBand: "hl",
    hide: new Set(), symCurve: "pooled", symMetric: "mean", mgCmp: "live",
    dial: null, ymode: "price", lock: false,
  };
  const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} } };
  try { const h = JSON.parse(store.get("rp.hide") || "[]"); if (Array.isArray(h)) state.hide = new Set(h); } catch (_) {}
  const saveHide = () => store.set("rp.hide", JSON.stringify([...state.hide]));
  state.ymode = store.get("rp.ymode") === "pos" ? "pos" : "price"; state.lock = store.get("rp.lock") === "1"; state.mgCmp = store.get("rp.mgcmp") || "live";
  let DATA = null, root = null;

  // ---------------------------------------------------------------- helpers
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const pct = (v, d = 0) => (v * 100).toFixed(d) + "%";
  const fmtR = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2).replace(/\.?0+$/, "") + "R";
  const quant = (arr, q) => { if (!arr.length) return NaN; const a = arr.slice().sort((x, y) => x - y); const i = (a.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i); return a[lo] + (a[hi] - a[lo]) * (i - lo); };
  function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

  // ---------------------------------------------------------------- management combos (trader 2026-10-03)
  const DIALS = [["stage", "Staged entry", "First share at the signal, the rest at that bar's close"],
    ["gate", "A: hold weak adds", "The add waits for +1R when the trial averaged below 0 by the add bar (needs staging)"],
    ["bank", "Bank", "Share banked at the level (TP1 if nearer, capped at TP2), then the stop goes to entry. det = the detector's live share (TrendCont/DeepFib 25%, SweepMSS/EMArevQ 50%)"],
    ["pyr", "Pyramid", "Add this share of full size at this R (after the bank when the bank is on)"],
    ["raise", "Shorts stop raise", "Shorts: every stop to this R when +1.5R trades"],
    ["cut", "B: shorts day-2 cut", "Close a short at the day-2 close if its average R is at or below this"],
    ["addi", "I: improving-longs add", "Longs: add this share at the day-2 close when day 2 averages better than day 1"]];
  const DKEY = { stage: "stage", bank: "bank", pyr: "pyr", raise: "raise", cut: "cutB", addi: "addI" };
  const dl = k => DATA.dials[DKEY[k]];
  const di = (k, name) => Math.max(0, dl(k).findIndex(x => String(x[0]) === name));
  const PRESET = {
    raw: () => ({ stage: 0, gate: 0, bank: 0, pyr: 0, raise: 0, cut: 0, addi: 0 }),
    live: () => ({ stage: di("stage", "25@6"), gate: 0, bank: di("bank", "det@1.0"), pyr: di("pyr", "0.5@1.5"), raise: di("raise", "0.25"), cut: 0, addi: 0 }),
    all: () => ({ stage: di("stage", "25@6"), gate: 1, bank: di("bank", "det@1.0"), pyr: di("pyr", "0.5@1.5"), raise: di("raise", "0.25"), cut: di("cut", "0.5"), addi: di("addi", "0.5") }) };
  const PNAME = { raw: "Raw signal", live: "Live setup", all: "Live + A+B+I" };
  function comboIdx(d) {
    const D = DATA.dials, nP = D.pyr.length, nR = D.raise.length, nC = D.cutB.length, nI = D.addI.length;
    const inner = D.bank.length * nP * nR * nC * nI; let block = 0;
    for (let si = 0; si < d.stage; si++) block += D.stage[si][1] >= 1 ? 1 : 2;
    block += D.stage[d.stage][1] >= 1 ? 0 : (d.gate ? 1 : 0);
    return block * inner + ((((d.bank * nP + d.pyr) * nR + d.raise) * nC + d.cut) * nI + d.addi);
  }
  const presetOf = d => Object.keys(PRESET).find(k => comboIdx(PRESET[k]()) === comboIdx(d));
  const selName = () => { const p = presetOf(state.dial); return p ? PNAME[p] : "Your combo"; };
  const CACHE = new Map(); let X = null, NH = 0;                       // X = the selected combo's exits; NH = historical trades
  function fetchCombo(idx) {
    if (CACHE.has(idx)) return Promise.resolve(CACHE.get(idx));
    return fetch(`/combo/${DATA.combo_build}/${idx}`).then(r => { if (!r.ok) throw new Error("combo " + idx + ": HTTP " + r.status); return r.arrayBuffer(); }).then(buf => {
      const dv = new DataView(buf), n = buf.byteLength / DATA.rec_bytes, R = new Float32Array(n), xb = new Float32Array(n), kind = new Uint8Array(n), ev = new Uint8Array(n * 4);
      for (let i = 0, o = 0; i < n; i++, o += 9) { R[i] = dv.getInt16(o, true) / 1000; xb[i] = dv.getUint16(o + 2, true) / 10; kind[i] = dv.getUint8(o + 4);
        for (let q = 0; q < 4; q++) ev[i * 4 + q] = dv.getUint8(o + 5 + q); }
      const c = { idx, R, xb, kind, ev }; CACHE.set(idx, c);
      if (CACHE.size > 40) CACHE.delete(CACHE.keys().next().value);
      return c; });
  }
  function useDial(d) {
    if (DATA.dials.stage[d.stage][1] >= 1) d.gate = 0;
    state.dial = d; store.set("rp.dial", JSON.stringify(d)); state.brush = null;
    const ind = document.getElementById("rp-loading"); if (ind) ind.style.visibility = "visible";
    return fetchCombo(comboIdx(d)).then(c => { X = c; render(); }).catch(e => { root.insertAdjacentHTML("afterbegin", `<div class="card bad">Could not load the combo: ${e.message}</div>`); });
  }
  const isHist = t => t.__i != null;
  const exitXb = t => isHist(t) ? X.xb[t.__i] : t.xb;
  const outOf = t => isHist(t) ? KIND_OUT[X.kind[t.__i]] : t.o;
  // the path of t under the selected combo: time "rt" (one point per H4 bar close, last = the exit) or "pg" (41 points entry -> exit)
  function exitPrice(t, i) {
    const k = X.kind[i];
    if (k === 0) return -1; if (k === 1) return 0; if (k === 2) return +dl("raise")[state.dial.raise][1]; if (k === 3) return t.tp;
    const b = Math.max(1, Math.min(t.rt.length, Math.ceil(X.xb[i]))); return t.rt.length ? t.rt[b - 1] / 100 : 0;
  }
  function posValues(t, i, nb) {      // the position's value (full-position R) at the close of bars 1..nb, from the combo's events
    const d = state.dial, st = DATA.dials.stage[d.stage], F0 = st[1], N = st[2], bk = dl("bank")[d.bank], py = dl("pyr")[d.pyr], ai = dl("addi")[d.addi];
    const pr = b => t.rt[Math.min(t.rt.length, b) - 1] / 100;
    let bankLvl = bk[2] == null ? null : (t.t1 != null ? Math.min(bk[2], t.t1) : bk[2]); if (bankLvl != null && t.tp != null && bankLvl >= t.tp - 1e-9) bankLvl = t.tp;
    const bf = bk[1] == null ? 0 : (bk[1] === "det" ? ((t.d === "TrendCont" || t.d === "DeepFib") ? 0.25 : 0.5) : +bk[1]);
    const ev = q => { const v = X.ev[i * 4 + q]; return v === 255 ? null : v; };
    // events in time order; same-bar ties in the engine's per-minute order: bank, pyramid, (held) add, I add
    const evs = [[ev(1), 0, "bank"], [ev(2), 1, "pyr"], [ev(0), 2, "add"], [ev(3), 3, "imp"]].filter(e => e[0] != null).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const legs = [[F0, 0]]; let locked = 0, k = 0; const out = [];
    for (let b = 1; b <= nb; b++) {
      while (k < evs.length && evs[k][0] <= b) {
        const [eb, , what] = evs[k++];
        if (what === "bank" && bankLvl != null) { let s = 0; for (const L of legs) s += L[0] * (bankLvl - L[1]); locked += bf * s; for (const L of legs) L[0] *= 1 - bf; }
        else if (what === "pyr") legs.push([+py[1], +py[2]]);
        else if (what === "add") legs.push([1 - F0, eb > N ? 1.0 : (t.rt.length >= N ? pr(N) : 0)]);   // held add releases at +1R
        else if (what === "imp" && t.rt.length >= 12) legs.push([+ai[1], pr(12)]);
      }
      const p = pr(b); let v = locked; for (const L of legs) v += L[0] * (p - L[1]); out.push(v);
    }
    return out;
  }
  function comboPath(t, time) {
    const key = X.idx + state.ymode + time;
    if (t.__pk === key) return t.__pv;
    const i = t.__i, xb = X.xb[i], cap = DATA.cap_bars, nb = Math.min(Math.floor(xb), t.rt.length, cap);
    const pos = state.ymode === "pos", vals = pos ? posValues(t, i, nb) : t.rt.slice(0, nb).map(v => v / 100);
    const last = pos ? X.R[i] : exitPrice(t, i), done = xb <= cap && Math.floor(xb) <= t.rt.length;
    let out;
    if (time === "rt") out = done ? vals.concat([last]) : vals;
    else {   // progress: 41 points over (0, 0), (1, v1) ... (nb, v_nb), (xb, last)
      const T = [0], V = [0]; vals.forEach((v, k) => { T.push(k + 1); V.push(v); }); if (done && xb > T[T.length - 1]) { T.push(xb); V.push(last); } else if (done) V[V.length - 1] = last;
      const end = T[T.length - 1]; out = [];
      for (let q = 0; q < 41; q++) { const tt = end * q / 40; let k = 1; while (k < T.length - 1 && T[k] < tt) k++;
        const t0 = T[k - 1], t1 = T[k] ?? t0; out.push(t1 > t0 ? V[k - 1] + (V[k] - V[k - 1]) * (tt - t0) / (t1 - t0) : V[k]); }
    }
    t.__pk = key; t.__pv = out; return out;
  }

  // the dials panel: combined dials split into simple pairs (share x level), mapped back to the grid's indices
  const STG = { share: [0.25, 0.5], bar: [6, 3] }, BNK = { share: ["det", 0.25, 0.5, 0.75], lvl: [0.75, 1.0, 1.25] }, PYRD = { size: [0.25, 0.5], lvl: [1.25, 1.5, 2.0] };
  const dec = d => ({ st: d.stage ? { s: Math.floor((d.stage - 1) / 2), b: (d.stage - 1) % 2 } : null, bk: d.bank ? { s: Math.floor((d.bank - 1) / 3), l: (d.bank - 1) % 3 } : null,
                      py: d.pyr ? { s: Math.floor((d.pyr - 1) / 3), l: (d.pyr - 1) % 3 } : null });
  function dialCard() {
    const card = el("div", "card rp-dials"), d = state.dial, D = dec(d), pre = presetOf(d);
    card.appendChild(el("div", "row", `<b>Management after the signal</b><span class="k">every chart below follows these dials · ${DATA.n_combos.toLocaleString()} combinations precomputed</span>` +
      `<span id="rp-loading" class="k" style="visibility:hidden;margin-left:auto">loading…</span>`));
    const set = nd => useDial({ ...state.dial, ...nd });
    const row = (label, opts, help, dis) => { const w = el("div", "rp-ctl"); w.appendChild(el("div", "rp-lbl", label + (help ? ` <span class="rp-q" title="${help}">?</span>` : "")));
      const g = el("div", "rp-seg"); opts.forEach(([t, on, fn, off]) => { const b = el("button", on ? "on" : "", t); b.type = "button"; if (dis || off) { b.disabled = true; } b.onclick = fn; g.appendChild(b); });
      w.appendChild(g); return w; };
    const top = el("div", "rp-seg"); top.style.margin = "6px 0";
    Object.keys(PRESET).forEach(k => { const b = el("button", pre === k ? "on" : "", PNAME[k] + (k === "raw" ? " (nothing added)" : k === "all" ? " (everything)" : "")); b.type = "button"; b.onclick = () => useDial(PRESET[k]()); top.appendChild(b); });
    const lk = el("button", "rp-tog" + (state.lock ? " on" : ""), (state.lock ? "🔒 " : "") + "Research lock"); lk.type = "button";
    lk.title = "Hide 2025-26 and the nine new symbols while you form an idea; unlock to test it on data you did not look at.";
    lk.onclick = () => { state.lock = !state.lock; store.set("rp.lock", state.lock ? "1" : "0"); state.brush = null; render(); };
    top.appendChild(lk); card.appendChild(top);
    if (state.lock) card.appendChild(el("div", "k", "🔒 Locked: 2025–26 and the nine new symbols are hidden on every chart (the holdout). Unlock to check an idea on them."));
    const g = el("div", "rp-grid");
    g.appendChild(row("Staged entry", [["Off", !D.st, () => set({ stage: 0, gate: 0 })]].concat(STG.share.map((v, i) => [`${v * 100}% first`, D.st && D.st.s === i, () => set({ stage: 1 + i * 2 + (D.st ? D.st.b : 0) })])),
      DIALS[0][2]));
    g.appendChild(row("Add at", STG.bar.map((v, i) => [`bar ${v} close`, D.st && D.st.b === i, () => set({ stage: 1 + (D.st ? D.st.s : 0) * 2 + i })]), "", !D.st));
    g.appendChild(row("A: hold weak adds", [["Off", !d.gate, () => set({ gate: 0 })], ["On", !!d.gate, () => set({ gate: 1 })]], DIALS[1][2], !D.st));
    g.appendChild(row("Shorts stop raise", dl("raise").map((r, i) => [i ? `to +${r[1]}R` : "Off", d.raise === i, () => set({ raise: i })]), DIALS[4][2]));
    g.appendChild(row("Bank share", [["Off", !D.bk, () => set({ bank: 0 })]].concat(BNK.share.map((v, i) => [v === "det" ? "Detector (live)" : `${v * 100}%`, D.bk && D.bk.s === i, () => set({ bank: 1 + i * 3 + (D.bk ? D.bk.l : 1) })])),
      DIALS[2][2]));
    g.appendChild(row("Bank at", BNK.lvl.map((v, i) => [`+${v}R`, D.bk && D.bk.l === i, () => set({ bank: 1 + (D.bk ? D.bk.s : 0) * 3 + i })]), "", !D.bk));
    g.appendChild(row("Pyramid size", [["Off", !D.py, () => set({ pyr: 0 })]].concat(PYRD.size.map((v, i) => [`+${v * 100}%`, D.py && D.py.s === i, () => set({ pyr: 1 + i * 3 + (D.py ? D.py.l : 1) })])),
      DIALS[3][2]));
    g.appendChild(row("Pyramid at", PYRD.lvl.map((v, i) => [`+${v}R`, D.py && D.py.l === i, () => set({ pyr: 1 + (D.py ? D.py.s : 0) * 3 + i })]), "", !D.py));
    g.appendChild(row("B: shorts day-2 cut", dl("cut").map((c, i) => [i ? `avg ≤ ${c[1]}R` : "Off", d.cut === i, () => set({ cut: i })]), DIALS[5][2]));
    g.appendChild(row("I: improving-longs add", dl("addi").map((c, i) => [i ? `+${c[1] * 100}%` : "Off", d.addi === i, () => set({ addi: i })]), DIALS[6][2]));
    card.appendChild(g);
    return card;
  }

  function select(outcome) {
    return DATA.trades.filter(t =>
      (state.source === "all" || t.src === state.source) &&
      !t.nodata && !state.hide.has(t.s) && !(state.lock && (t.y >= 2025 || t.g === 1)) &&
      (outcome === "all" || outOf(t) === outcome) &&
      (state.dir === "all" || (state.dir === "long") === (t.l === 1)) &&
      (state.det === "all" || t.d === state.det) &&
      (state.start === "all" || (state.start === "fast" ? t.f6 >= 1.5 : t.f6 < 1.0)) &&
      (state.period === "all" || (state.period === "dev" ? t.y < 2025 : t.y >= 2025)));
  }
  const pathOf = (t, time = state.time) => isHist(t) ? comboPath(t, time) : (time === "rt" ? t.rt.map(v => v / 100) : t.pg.map(v => v / 100));
  // x column of path index i: real time = bar i+1 (column 0 is the entry, R 0 for every trade); progress = i of 40
  function geometry(groups) {
    let ncol;
    if (state.time === "pg") ncol = DATA.pg_points;
    else {
      const ex = [].concat(...groups.map(g => g.map(t => pathOf(t).length)));
      ncol = Math.max(13, Math.min(DATA.cap_bars, Math.ceil(quant(ex, 0.85) || 30))) + 1;   // to where 85% have exited
    }
    let ymax = 2, ymin = -1;
    groups.forEach(g => g.forEach(t => { const p = pathOf(t); for (let i = 0; i < p.length; i++) { if (p[i] > ymax) ymax = p[i]; if (p[i] < ymin) ymin = p[i]; } }));
    ymax = Math.min(7, Math.ceil(Math.min(ymax, 6.5) * 2) / 2 + 0.25);
    ymin = Math.max(-2.5, Math.floor(ymin * 4) / 4 - 0.25);
    return { ncol, ymin, ymax, dy: 0.25 };
  }
  // value of trade t at column c (null when it has exited)
  function valAt(t, c) {
    const p = pathOf(t);
    if (state.time === "pg") return p[c];
    if (c === 0) return 0;
    return c - 1 < p.length ? p[c - 1] : null;
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
    root.appendChild(dialCard());
    // controls
    const ctl = el("div", "card rp-ctls");
    const nLive = DATA.trades.filter(t => t.src === "live").length;
    ctl.appendChild(seg("Data", "source", [["backtest", "Historical"], ["live", `Live only (${nLive})`, nLive ? null : "no closed live trades yet - they appear here automatically"],
      ["all", "Combined", nLive ? null : "no closed live trades yet"]],
      "Historical: 13 years of backtest trades. Live only: your closed live trades, added automatically every hour. Combined: both together."));
    ctl.appendChild(symbolControl());
    ctl.appendChild(seg("Show", "show", Object.entries(OUTCOME).map(([k, v]) => [k, v])));
    ctl.appendChild(seg("Compare with", "cmp", [["none", "Nothing"]].concat(Object.entries(OUTCOME).filter(([k]) => k !== "all").map(([k, v]) => [k, v, k === state.show ? "already shown" : null])),
      "Draws a second panel with the same filters, and each group's typical path on both panels"));
    ctl.appendChild(seg("Time axis", "time", [["rt", "Days since entry"], ["pg", "Progress to exit"]],
      "Days: real speed. Progress: every trade stretched to the same length (0% = entry, 100% = the exit), so you compare shapes"));
    const ym = seg("Y axis", "ymode", [["price", "Price"], ["pos", "Position value"]],
      "Price: where price is, in the trade's own R from the signal's entry. Position value: what the whole position is worth in full-position R - banked parts, the staged add, the pyramid and the I add included (rebuilt bar by bar from the combo's events; the last point is the exact result).");
    ym.querySelectorAll("button").forEach(b => { const f = b.onclick; b.onclick = () => { f(); store.set("rp.ymode", state.ymode); }; });
    ctl.appendChild(ym);
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
      const ex = g.trades.map(t => exitXb(t) / 6), below = g.trades.filter(t => pathOf(t).some(v => v < 0)).length;
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
    root.appendChild(symCard());
    // table view
    root.appendChild(tableView(S, G));
    const nh = NH, nl = DATA.trades.length - nh;
    root.appendChild(el("div", "k rp-foot", `Historical: ${nh.toLocaleString()} trades (all four detectors, ${symList().filter(x => x.hist).length} symbols, 2012–2026; signals from today's EA build), every management combination replayed on M1 (ask-corrected, live spreads) - shown: ${selName()} (combination ${state.dial ? comboIdx(state.dial) : "-"} of build ${DATA.combo_build}). ` +
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
    const groups = ["tp2", "be", "sl", "cut"].map(k => ({ key: k, trades: select(k) })).filter(g => g.trades.length);
    const steps = Math.min(state.avgDays * 2, Math.floor(DATA.cap_bars / 3));
    const series = groups.map(g => ({ key: g.key, pts: [...Array(steps + 1).keys()].map(i => {
      const bar = i * 3, vals = [];
      for (const t of g.trades) {
        const pt = pathOf(t, "rt");
        const v = bar === 0 ? 0 : (bar - 1 < pt.length ? pt[bar - 1] : (state.avgCarry && pt.length ? pt[pt.length - 1] : null));
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

  // ---------------------------------------------------------------- symbols: show / hide (trader 2026-10-03)
  function symList() {
    const orig = DATA.symbols_orig || [], part = DATA.partial || [], seen = new Map();
    DATA.trades.forEach(t => { if (!seen.has(t.s)) seen.set(t.s, { s: t.s, n: 0, hist: false }); const r = seen.get(t.s); r.n++; if (t.src !== "live") r.hist = true; });
    const grp = s => (DATA.groups || []).find(g => g.syms.includes(s)) || null;
    return [...seen.values()].map(r => ({ ...r, orig: orig.includes(r.s), part: part.includes(r.s), grp: grp(r.s) }))
      .sort((a, b) => (b.orig - a.orig) || (b.hist - a.hist) || a.s.localeCompare(b.s));
  }
  function symbolControl() {
    const L = symList(), wrap = el("div", "rp-ctl");
    const shown = L.filter(x => !state.hide.has(x.s)).length;
    wrap.appendChild(el("div", "rp-lbl", `Symbols <span class="k" style="text-transform:none;letter-spacing:0">(${shown} of ${L.length} shown)</span> <span class="rp-q" title="Tap a symbol to hide or show it on every chart on this page. Original 7 = the symbols the system was built on; New 9 = never used in development. * = partial years only (the tester lacked currency-conversion history). Remembered in this browser.">?</span>`));
    const quick = el("div", "rp-seg"); quick.style.marginBottom = "4px";
    const set = keep => { state.hide = new Set(L.filter(x => !keep(x)).map(x => x.s)); saveHide(); state.brush = null; render(); };
    const isSet = keep => L.every(x => keep(x) === !state.hide.has(x.s));
    [["All", x => true], ["Original 7", x => x.orig], ["New 9", x => x.hist && !x.orig], ["None", x => false]].forEach(([t, f]) => {
      const b = el("button", isSet(f) ? "on" : "", t); b.type = "button"; b.onclick = () => set(f); quick.appendChild(b); });
    wrap.appendChild(quick);
    // groups (live/symbol_groups.py): a button turns every symbol of the group on or off; half-lit = some of it hidden
    if ((DATA.groups || []).length) {
      const gl = el("div", "rp-seg rp-groups"); gl.style.marginBottom = "4px";
      DATA.groups.forEach(G => {
        const mine = L.filter(x => G.syms.includes(x.s)), on = mine.filter(x => !state.hide.has(x.s)).length;
        const b = el("button", on && on === mine.length ? "on" : on ? "part" : "", `<i style="background:${G.col}"></i>${G.name}${mine.length ? ` <span class="k">${on}/${mine.length}</span>` : ""}`);
        b.type = "button"; b.title = G.how + (mine.length ? "" : " - no data yet") + " · " + G.syms.join(", ");
        if (!mine.length) b.disabled = true;
        b.onclick = () => { const all = on === mine.length; mine.forEach(x => all ? state.hide.add(x.s) : state.hide.delete(x.s)); saveHide(); state.brush = null; render(); };
        gl.appendChild(b);
      });
      wrap.appendChild(gl);
    }
    const g = el("div", "rp-seg");
    L.forEach(x => {
      const b = el("button", state.hide.has(x.s) ? "" : "on", x.s + (x.part ? "*" : "") + (x.hist ? "" : " (live)")); b.type = "button";
      b.title = `${x.grp ? x.grp.name + " · " : ""}${x.orig ? "original seven" : x.hist ? "new - never used in development" : "live trades only"} · ${x.n} trades${x.part ? " · partial years" : ""}`;
      if (!x.orig && x.hist) b.style.borderStyle = "dashed";
      if (x.grp) b.style.boxShadow = `inset 4px 0 0 ${x.grp.col}`;
      b.onclick = () => { state.hide.has(x.s) ? state.hide.delete(x.s) : state.hide.add(x.s); saveHide(); state.brush = null; render(); };
      g.appendChild(b);
    });
    wrap.appendChild(g); return wrap;
  }
  const maxDD = v => { let c = 0, pk = 0, dd = 0; for (const x of v) { c += x; pk = Math.max(pk, c); dd = Math.max(dd, pk - c); } return dd; };
  function symRows() {   // historical trades passing every filter except Show / Compare (stats need every outcome)
    return DATA.trades.filter(t => isHist(t) && !t.nodata && !state.hide.has(t.s) && !(state.lock && (t.y >= 2025 || t.g === 1)) &&
      (state.dir === "all" || (state.dir === "long") === (t.l === 1)) && (state.det === "all" || t.d === state.det) &&
      (state.start === "all" || (state.start === "fast" ? t.f6 >= 1.5 : t.f6 < 1.0)) &&
      (state.period === "all" || (state.period === "dev" ? t.y < 2025 : t.y >= 2025))).sort((a, b) => a.dt < b.dt ? -1 : a.dt > b.dt ? 1 : 0);
  }
  // ---------------------------------------------------------------- by symbol (follows the dials at the top)
  const SC = { sel: "#c98bff", cmp: "#3987e5" };
  function symRows2() { return symRows(); }
  function symCard() {
    const card = el("div", "card rp-sym");
    card.appendChild(el("div", "row", `<b>By symbol</b><span class="k">historical · the Symbols, Direction, Detector, Start and Period filters apply · every outcome counted</span>`));
    const ctl = el("div", "rp-grid");
    const mk = (label, key, opts, help, persist) => { const w = el("div", "rp-ctl"); w.appendChild(el("div", "rp-lbl", label + (help ? ` <span class="rp-q" title="${help}">?</span>` : ""))); const g = el("div", "rp-seg");
      opts.forEach(([v, t]) => { const b = el("button", state[key] === v ? "on" : "", t); b.type = "button"; b.onclick = () => { state[key] = v; if (persist) store.set(persist, v); render(); }; g.appendChild(b); }); w.appendChild(g); return w; };
    ctl.appendChild(mk("Compare with", "mgCmp", [["live", "Live setup"], ["raw", "Raw signal"], ["none", "Nothing"]], "", "rp.mgcmp"));
    ctl.appendChild(mk("Bars show", "symMetric", [["mean", "R per trade"], ["total", "Total R"], ["dd", "Worst drawdown"]]));
    ctl.appendChild(mk("Curves", "symCurve", [["pooled", "All shown, combined"], ["each", "Each symbol"]],
      "Combined: one cumulative-R line for every symbol shown, added together. Each symbol: one line per symbol (use the Symbols filter above to pick a few)."));
    card.appendChild(ctl);
    const rows = symRows2();
    if (!rows.length) { card.appendChild(el("div", "k", "No historical trades for the symbols shown.")); return card; }
    const cmpName = state.mgCmp === "none" ? null : PNAME[state.mgCmp];
    let XC = null;
    if (cmpName) {
      const ci = comboIdx(PRESET[state.mgCmp]());
      XC = CACHE.get(ci) || null;
      if (!XC) { fetchCombo(ci).then(() => render()); card.appendChild(el("div", "k", "Loading the comparison…")); return card; }
    }
    const fS = t => X.R[t.__i], fC = XC ? (t => XC.R[t.__i]) : null;
    const L = symList(), by = new Map();
    rows.forEach(t => { if (!by.has(t.s)) by.set(t.s, []); by.get(t.s).push(t); });
    const sum = v => v.reduce((x, y) => x + y, 0);
    const st = [...by.entries()].map(([s, ts]) => {
      const a = ts.map(fS), b = fC ? ts.map(fC) : null, info = L.find(x => x.s === s) || {};
      return { s, n: ts.length, orig: info.orig, part: info.part, grp: info.grp, y0: ts[0].dt.slice(0, 4), y1: ts[ts.length - 1].dt.slice(0, 4),
               am: sum(a) / a.length, at: sum(a), ad: maxDD(a), win: a.filter(x => x > 0).length / a.length,
               bm: b ? sum(b) / b.length : null, bt: b ? sum(b) : null, bd: b ? maxDD(b) : null };
    });
    // pooled summary line
    const pa = rows.map(fS), pb = fC ? rows.map(fC) : null;
    const f3 = v => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(3), f1 = v => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1);
    card.appendChild(el("div", "rp-sum", `<span class="rp-sw sm" style="background:${SC.sel}"></span><b>${selName()}</b>: ${f3(sum(pa) / pa.length)}R a trade · total ${f1(sum(pa))}R · worst DD ${maxDD(pa).toFixed(1)}R` +
      (pb ? `<br><span class="rp-sw sm" style="background:${SC.cmp}"></span>${cmpName}: ${f3(sum(pb) / pb.length)}R a trade · total ${f1(sum(pb))}R · worst DD ${maxDD(pb).toFixed(1)}R` : "") +
      `<br><span class="k">${rows.length.toLocaleString()} trades, ${by.size} symbol${by.size === 1 ? "" : "s"} shown</span>`));
    // bars
    const key = { mean: ["am", "bm"], total: ["at", "bt"], dd: ["ad", "bd"] }[state.symMetric];
    const sorted = st.slice().sort((x, y) => (y[key[0]]) - (x[key[0]]));
    const bbox = el("div", "rp-cv"); const bcv = el("canvas"); bcv.style.height = Math.max(160, 30 + sorted.length * 30) + "px"; bcv.style.cursor = "default";
    const btip = el("div", "rp-tip"); bbox.appendChild(bcv); bbox.appendChild(btip); card.appendChild(bbox);
    const leg = el("div", "rp-leg");
    leg.innerHTML = `<span class="rp-key"><i style="background:${SC.sel}"></i>${selName()}</span>` + (cmpName ? `<span class="rp-key"><i style="background:${SC.cmp}"></i>${cmpName}</span>` : "") +
      `<span class="k">italic name = new symbol (never used in development) · * = partial years</span>`;
    card.appendChild(leg);
    requestAnimationFrame(() => drawBars(bcv, btip, sorted, key, cmpName));
    // curves
    const cbox = el("div", "rp-cv"); const ccv = el("canvas"); ccv.style.height = "clamp(260px,40vw,400px)"; ccv.style.cursor = "crosshair";
    const ctip = el("div", "rp-tip"); cbox.appendChild(ccv); cbox.appendChild(ctip); card.appendChild(cbox);
    card.appendChild(el("div", "k", "Cumulative R by signal date (full position, costs in)." + (cmpName ? " The gap between the lines is what your selection adds or costs against " + cmpName.toLowerCase() + "." : "")));
    requestAnimationFrame(() => drawCurves(ccv, ctip, rows, [...by.keys()], fS, fC, cmpName));
    // table
    const d = el("details", "rp-table"); d.appendChild(el("summary", "k", "Table: every symbol shown"));
    let h = `<table><tr><th>symbol</th><th>group</th><th>years</th><th>trades</th><th>${selName()} R/trade</th><th>win%</th><th>total</th><th>DD</th>` +
            (cmpName ? `<th>${cmpName} R/trade</th><th>total</th><th>DD</th><th>change</th>` : "") + "</tr>";
    st.slice().sort((x, y) => (y.orig - x.orig) || (y.am - x.am)).forEach(r => {
      h += `<tr><td>${r.orig ? "" : "<i>"}${r.s}${r.part ? "*" : ""}${r.orig ? "" : "</i>"}</td><td>${r.grp ? r.grp.name : "–"}</td><td>${r.y0}–${r.y1}</td><td>${r.n}</td>` +
           `<td>${f3(r.am)}</td><td>${(r.win * 100).toFixed(0)}%</td><td>${f1(r.at)}</td><td>${r.ad.toFixed(1)}</td>` +
           (cmpName ? `<td>${f3(r.bm)}</td><td>${f1(r.bt)}</td><td>${r.bd.toFixed(1)}</td><td class="${r.am >= r.bm ? "ok" : "bad"}">${f3(r.am - r.bm)}</td>` : "") + "</tr>";
    });
    d.appendChild(el("div", "rp-tablewrap", h + "</table>")); card.appendChild(d);
    return card;
  }
  function drawBars(cv, tip, rows, key, cmpName) {
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr; const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    const pad = { l: 92, r: 16, t: 8, b: 22 }, pw = W - pad.l - pad.r, rowH = (H - pad.t - pad.b) / rows.length;
    const vals = [].concat(...rows.map(r => cmpName ? [r[key[0]], r[key[1]]] : [r[key[0]]]));
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals); const span = (hi - lo) || 1; lo -= span * 0.04; hi += span * 0.04;
    const X = v => pad.l + (v - lo) / (hi - lo) * pw;
    ctx.fillStyle = C.surface; ctx.fillRect(0, 0, W, H);
    ctx.font = "12px -apple-system,Segoe UI,Roboto,sans-serif"; ctx.textBaseline = "middle";
    const unit = state.symMetric === "mean" ? (v => fmtR(+v.toFixed(3))) : (v => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1) + "R");
    ctx.strokeStyle = "#6e7681"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(X(0), pad.t); ctx.lineTo(X(0), H - pad.b); ctx.stroke();
    ctx.fillStyle = C.dim; ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillText(unit(lo), X(lo) + 14, H - pad.b + 5); ctx.fillText(unit(hi), X(hi) - 14, H - pad.b + 5); ctx.fillText("0", X(0), H - pad.b + 5);
    ctx.textBaseline = "middle";
    rows.forEach((r, i) => {
      const y = pad.t + i * rowH, bh = Math.max(4, rowH * (cmpName ? 0.32 : 0.55));
      ctx.fillStyle = r.orig ? C.text : C.dim; ctx.textAlign = "right"; ctx.font = (r.orig ? "" : "italic ") + "12px -apple-system,Segoe UI,Roboto,sans-serif";
      ctx.fillText(r.s + (r.part ? "*" : ""), pad.l - 8, y + rowH / 2);
      const bars = cmpName ? [[r[key[0]], SC.sel, y + rowH / 2 - bh - 1], [r[key[1]], SC.cmp, y + rowH / 2 + 1]] : [[r[key[0]], SC.sel, y + rowH / 2 - bh / 2]];
      bars.forEach(([v, col, yy]) => { ctx.fillStyle = col; const x0 = X(Math.min(0, v)), x1 = X(Math.max(0, v)); ctx.fillRect(x0, yy, Math.max(1, x1 - x0), bh); });
    });
    cv.onpointermove = e => { const rr = cv.getBoundingClientRect(), i = Math.floor((e.clientY - rr.top - pad.t) / rowH); const r = rows[i];
      if (!r) { tip.style.display = "none"; return; }
      tip.innerHTML = `<b>${r.s}${r.part ? "*" : ""}</b> <span class="k">${r.grp ? r.grp.name + " · " : ""}${r.orig ? "original" : "new"} · ${r.y0}–${r.y1} · ${r.n} trades</span><br>` +
        `<span class="rp-sw sm" style="background:${SC.sel}"></span>${selName()}: <b>${fmtR(+r.am.toFixed(3))}</b>/trade · total ${unit(r.at)} · worst DD ${r.ad.toFixed(1)}R · win ${(r.win * 100).toFixed(0)}%` +
        (cmpName ? `<br><span class="rp-sw sm" style="background:${SC.cmp}"></span>${cmpName}: <b>${fmtR(+r.bm.toFixed(3))}</b>/trade · total ${unit(r.bt)} · worst DD ${r.bd.toFixed(1)}R` : "");
      tip.style.display = "block"; tip.style.left = Math.min(W - tip.offsetWidth - 4, Math.max(4, e.clientX - rr.left + 12)) + "px"; tip.style.top = Math.max(4, e.clientY - rr.top - tip.offsetHeight - 8) + "px"; };
    cv.onpointerleave = () => { tip.style.display = "none"; };
  }
  function drawCurves(cv, tip, rows, syms, fS, fC, cmpName) {
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr; const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    const pad = { l: 52, r: 14, t: 12, b: 26 }, pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const tms = s => Date.parse(s + "T00:00:00Z");
    const t0 = tms(rows[0].dt), t1 = Math.max(t0 + 864e5, tms(rows[rows.length - 1].dt));
    const series = [];
    const cum = (ts, f) => { let v = 0; return ts.map(t => ({ x: tms(t.dt), y: (v += f(t)) })); };
    const sets = [[fS, selName(), SC.sel, []]].concat(fC == null ? [] : [[fC, cmpName, SC.cmp, [5, 4]]]);
    if (state.symCurve === "pooled") sets.forEach(([c, nm, col]) => series.push({ name: nm, col, pts: cum(rows, c), dash: [] }));
    else syms.forEach((s, i) => { const col = `hsl(${Math.round(i * 360 / Math.max(1, syms.length))},70%,62%)`; const ts = rows.filter(t => t.s === s);
      sets.forEach(([c, nm, _, dash]) => series.push({ name: s + (sets.length > 1 ? " · " + nm : ""), col, pts: cum(ts, c), dash })); });
    let lo = 0, hi = 0; series.forEach(sr => sr.pts.forEach(p => { lo = Math.min(lo, p.y); hi = Math.max(hi, p.y); }));
    const sp = (hi - lo) || 1; lo -= sp * 0.05; hi += sp * 0.05;
    const X = x => pad.l + (x - t0) / (t1 - t0) * pw, Y = y => pad.t + (1 - (y - lo) / (hi - lo)) * ph;
    ctx.fillStyle = C.surface; ctx.fillRect(0, 0, W, H);
    ctx.font = "11px -apple-system,Segoe UI,Roboto,sans-serif"; ctx.textBaseline = "middle";
    const ys = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500].find(k => (hi - lo) / k <= 7) || 1000;
    for (let r = Math.ceil(lo / ys) * ys; r <= hi; r += ys) { ctx.strokeStyle = r === 0 ? "#6e7681" : C.grid; ctx.lineWidth = r === 0 ? 1.2 : 1; ctx.beginPath(); ctx.moveTo(pad.l, Y(r)); ctx.lineTo(W - pad.r, Y(r)); ctx.stroke();
      ctx.fillStyle = C.dim; ctx.textAlign = "right"; ctx.fillText((r > 0 ? "+" : "") + r + "R", pad.l - 6, Y(r)); }
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear(), yst = Math.max(1, Math.ceil((y1 - y0 + 1) / Math.max(2, Math.floor(pw / 56))));
    for (let y = y0 + 1; y <= y1; y += yst) { const x = X(Date.UTC(y, 0, 1)); ctx.strokeStyle = C.grid; ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke(); ctx.fillStyle = C.dim; ctx.fillText(String(y), x, pad.t + ph + 6); }
    series.forEach(sr => { ctx.strokeStyle = sr.col; ctx.lineWidth = state.symCurve === "pooled" ? 2 : 1.5; ctx.setLineDash(sr.dash); ctx.beginPath();
      sr.pts.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y))); ctx.stroke(); ctx.setLineDash([]); });
    const base = ctx.getImageData(0, 0, cv.width, cv.height);
    const restore = () => { ctx.putImageData(base, 0, 0); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.scale(dpr, dpr); };
    cv.onpointermove = e => { const rr = cv.getBoundingClientRect(), x = e.clientX - rr.left; const tx = t0 + (x - pad.l) / pw * (t1 - t0);
      restore(); ctx.strokeStyle = "rgba(240,246,252,0.35)"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke(); ctx.setLineDash([]);
      const at = sr => { let v = 0; for (const p of sr.pts) { if (p.x > tx) break; v = p.y; } return v; };
      const lines = series.map(sr => ({ sr, v: at(sr) })).sort((a, b) => b.v - a.v).slice(0, 12);
      tip.innerHTML = `<b>${new Date(tx).toISOString().slice(0, 10)}</b><br>` + lines.map(o => `<span class="rp-sw sm" style="background:${o.sr.col}"></span>${o.sr.name}: <b>${(o.v >= 0 ? "+" : "−") + Math.abs(o.v).toFixed(1)}R</b>`).join("<br>") + (series.length > 12 ? `<br><span class="k">+${series.length - 12} more</span>` : "");
      tip.style.display = "block"; tip.style.left = Math.min(W - tip.offsetWidth - 4, Math.max(4, x + 12)) + "px"; tip.style.top = (pad.t + 6) + "px"; };
    cv.onpointerleave = () => { tip.style.display = "none"; restore(); };
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
      fetch(url).then(r => r.json()).then(d => {
        DATA = d; NH = 0;
        DATA.trades.forEach((t, i) => { if (t.src !== "live") { t.__i = i; NH = i + 1; } });
        let dd = null; try { dd = JSON.parse(store.get("rp.dial") || "null"); } catch (_) {}
        const ok = dd && ["stage", "gate", "bank", "pyr", "raise", "cut", "addi"].every(k => Number.isInteger(dd[k]) && dd[k] >= 0) && dd.stage < DATA.dials.stage.length && dd.bank < DATA.dials.bank.length &&
                   dd.pyr < DATA.dials.pyr.length && dd.raise < DATA.dials.raise.length && dd.cut < DATA.dials.cutB.length && dd.addi < DATA.dials.addI.length;
        return useDial(ok ? dd : PRESET.live()).then(() => window.addEventListener("resize", () => { clearTimeout(window.__rpT); window.__rpT = setTimeout(render, 150); })); })
        .catch(e => { root.innerHTML = '<div class="card bad">Could not load the path data: ' + e + "</div>"; });
    },
  };
})();
