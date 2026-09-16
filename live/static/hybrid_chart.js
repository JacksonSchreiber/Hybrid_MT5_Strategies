// hybrid_chart.js - interactive signal/position chart on Lightweight Charts (self-hosted).
// mount(el, data): data = {bars_h4, bars_d1 [[t,o,h,l,c,v]], levels{entry,sl,tp1,tp2}, overlay{zone,zone2,leg,aux,swings_hi,swings_lo},
//                        signal_t (epoch), digits, marks:[{t,label}], title}
(function () {
  function ema(vals, n) { var k = 2 / (n + 1), out = [], p = vals[0]; for (var i = 0; i < vals.length; i++) { p = i ? p + k * (vals[i] - p) : vals[0]; out.push(p); } return out; }
  function iso(s) { return s ? Math.floor(new Date(s).getTime() / 1000) : null; }
  var EMA = { 20: '#ffd700', 50: '#00bfff', 200: '#ee82ee' };
  var FIB_LV = [0.0, 0.5, 0.618, 0.705, 0.786, 0.886, 1.0], FIB_LT = ['0.0', '50.0', '61.8', '70.5 OTE', '78.6', '88.6 SL', '100.0'];
  function imbState(r, iformed, lo, hi, dir) { var inv = false; for (var j = iformed + 1; j < r.length; j++) { var b = r[j], enters = b[2] >= lo && b[3] <= hi;
      if (!inv) { var through = dir > 0 ? b[4] < lo : b[4] > hi; if (through) { inv = true; continue; } if (enters) return 2; } else if (enters) return 2; } return inv ? 1 : 0; }
  function imbalances(bars) { var r = bars.length > 1 ? bars.slice(0, -1) : bars, cnt = r.length, z = [], out = []; if (cnt < 25) return out;
    for (var i = Math.max(2, cnt - 120); i < cnt; i++) {
      if (r[i-2][2] < r[i][3]) z.push([r[i-2][2], r[i][3], 1, i]); if (r[i-2][3] > r[i][2]) z.push([r[i][2], r[i-2][3], -1, i]);
      if (r[i][3] > r[i-1][2]) z.push([r[i-1][2], r[i][3], 1, i]); if (r[i][2] < r[i-1][3]) z.push([r[i][2], r[i-1][3], -1, i]);
      if (i >= 21) { var s = 0; for (var k = i - 20; k < i; k++) s += r[k][5]; var avg = s / 20; if (avg > 0 && r[i][5] > 2 * avg) z.push([r[i][3], r[i][2], r[i][4] >= r[i][1] ? 1 : -1, i]); } }
    for (var q = z.length - 1; q >= 0 && out.length < 15; q--) { var st = imbState(r, z[q][3], z[q][0], z[q][1], z[q][2]); if (st === 2) continue; out.push({ t0: r[z[q][3]][0], t1: r[cnt-1][0], lo: z[q][0], hi: z[q][1], state: st }); }
    return out; }
  function swings(bars) { var r = bars.length > 1 ? bars.slice(0, -1) : bars, nb = Math.min(14 * 6 + 8, r.length), out = []; if (nb < 12) return out; var w = r.slice(-nb);
    for (var i = 2; i < w.length - 2 && out.length < 120; i++) { var sh = true, sl = true; for (var k = 1; k <= 2; k++) { if (!(w[i][2] > w[i-k][2] && w[i][2] > w[i+k][2])) sh = false; if (!(w[i][3] < w[i-k][3] && w[i][3] < w[i+k][3])) sl = false; }
      if (sh) out.push({ t: w[i][0], p: w[i][2], kind: 'hi' }); if (sl) out.push({ t: w[i][0], p: w[i][3], kind: 'lo' }); } return out; }
  window.HybridChart = {
    mount: function (id, d) {
      var el = typeof id === 'string' ? document.getElementById(id) : id;
      if (!el) { console.log('HybridChart: container not found', id); return; }
      var tf = 'h4', chart, box = el.querySelector('.hc-box'), bar = el.querySelector('.hc-bar'), status = el.querySelector('.hc-status'), timer = null, gen = 0;
      if (!box || !(box instanceof Element)) { if (status) { status.textContent = 'chart error: box element missing (' + (el.tagName || typeof el) + ')'; status.style.color = '#f85149'; } return; }
      function api(tf, n) { return fetch('/api/bars/' + encodeURIComponent(d.symbol) + '?tf=' + tf + '&n=' + n, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
      function start() {
        if (timer) { clearInterval(timer); timer = null; }
        if (!d.live) { build((tf === 'h4' ? d.bars_h4 : d.bars_d1) || [], null); return; }
        var my = ++gen;
        api(tf, 500).then(function (j) { if (my !== gen) return;
          if (!j.bars) { build((tf === 'h4' ? d.bars_h4 : d.bars_d1) || [], null); return; }   // symbol unknown to the terminal: EA bars
          build(j.bars, j.tick);
          timer = setInterval(function () { api(tf, 3).then(function (u) { if (my !== gen || !u.bars) return; update(u.bars, u.tick); }).catch(function () {}); }, 5000);
        }).catch(function () { build((tf === 'h4' ? d.bars_h4 : d.bars_d1) || [], null); });
      }
      var cs, emaS = {}, closes = [], barsAll = [];
      function setStatus(tick) { if (!status) return; status.textContent = tick ? ('bid ' + tick.bid.toFixed(d.digits || 5) + ' ask ' + tick.ask.toFixed(d.digits || 5) + ' · live') : (d.live ? 'feed unavailable' : 'bars as of the signal'); }
      function update(last, tick) {
        // series.update() accepts only the current bar or a newer one: push the forming bar and any new bar, never older ones
        var lastT = barsAll.length ? barsAll[barsAll.length - 1][0] : 0;
        last.sort(function (a, b) { return a[0] - b[0]; }).forEach(function (b) {
          if (b[0] < lastT) return;
          if (b[0] === lastT) barsAll[barsAll.length - 1] = b; else barsAll.push(b);
          try { cs.update({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }); } catch (e) { console.log('update skipped', e); }
        });
        closes = barsAll.map(function (b) { return b[4]; });
        [20, 50, 200].forEach(function (n) { var e = ema(closes, n); emaS[n].setData(barsAll.map(function (b, i) { return { time: b[0], value: e[i] }; }).slice(n)); });
        setStatus(tick);
      }
      function showErr(where, e) { if (status) { status.textContent = 'chart error in ' + where + ': ' + (e && e.message || e); status.style.color = '#f85149'; } console.log('chart error', where, e); }
      function guard(where, fn) { try { fn(); } catch (e) { showErr(where, e); } }
      function build(bars, tick) { try { build0(bars, tick); } catch (e) { showErr('build', e); } }
      function build0(bars, tick) {
        if (chart) { chart.remove(); }
        barsAll = bars.slice();
        var h = Math.max(320, Math.min(560, window.innerHeight * 0.55));
        chart = LightweightCharts.createChart(box, { height: h, layout: { background: { color: '#0d1117' }, textColor: '#c9d1d9' },
          grid: { vertLines: { color: '#1e242e' }, horzLines: { color: '#1e242e' } }, crosshair: { mode: 0 },
          rightPriceScale: { borderColor: '#30363d', scaleMargins: { top: 0.08, bottom: 0.08 } },
          timeScale: { borderColor: '#30363d', timeVisible: tf === 'h4', secondsVisible: false, rightOffset: 6 },
          localization: { priceFormatter: function (p) { return p.toFixed(d.digits || 5); } }, handleScale: true, handleScroll: true });
        cs = chart.addCandlestickSeries({ upColor: '#3fb950', downColor: '#f85149', borderUpColor: '#3fb950', borderDownColor: '#f85149', wickUpColor: '#3fb950', wickDownColor: '#f85149',
          priceFormat: { type: 'price', precision: d.digits || 5, minMove: Math.pow(10, -(d.digits || 5)) } });
        cs.setData(bars.map(function (b) { return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }; }));
        closes = bars.map(function (b) { return b[4]; });
        [20, 50, 200].forEach(function (n) {
          var e = ema(closes, n), ls = chart.addLineSeries({ color: EMA[n], lineWidth: n === 200 ? 2 : 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          ls.setData(bars.map(function (b, i) { return { time: b[0], value: e[i] }; }).slice(n)); emaS[n] = ls;
        });
        setStatus(tick);
        var lv = d.levels || {};
        [['entry', '#3fb950', 'entry', 2, 0], ['sl', '#f85149', 'SL', 2, 2], ['tp1', '#58a6ff', 'TP1', 1, 2], ['tp2', '#58a6ff', 'TP2', 1, 3]].forEach(function (L) {
          if (lv[L[0]] > 0) cs.createPriceLine({ price: lv[L[0]], color: L[1], lineWidth: L[3], lineStyle: L[4], axisLabelVisible: true, title: L[2] });
        });
        var ov = d.overlay || {};
        function box(t0, t1, hi, lo, fill, line) {   // filled band between lo..hi over [t0,t1] (Lightweight Charts has no rectangles)
          var z = chart.addBaselineSeries({ baseValue: { type: 'price', price: lo }, topFillColor1: fill, topFillColor2: fill, topLineColor: line, bottomLineColor: line, bottomFillColor1: 'rgba(0,0,0,0)', bottomFillColor2: 'rgba(0,0,0,0)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          var pts = bars.filter(function (b) { return b[0] >= t0 && b[0] <= t1; }).map(function (b) { return { time: b[0], value: hi }; });
          if (pts.length === 1) pts.push({ time: pts[0].time + (tf === 'h4' ? 14400 : 86400), value: hi });
          if (pts.length) z.setData(pts);
        }
        if (tf === 'h4' && bars.length) guard('zones', function () {
          var zf = iso(ov.zone && ov.zone.from) || bars[0][0], zt = iso(ov.zone && ov.zone.to) || bars[bars.length - 1][0];
          if (ov.zone && ov.zone.hi > 0) box(zf, zt, ov.zone.hi, ov.zone.lo, 'rgba(112,128,144,0.35)', 'rgba(112,128,144,0.8)');
          if (ov.zone2 && ov.zone2.hi > 0) box(zf, zt, ov.zone2.hi, ov.zone2.lo, 'rgba(47,79,79,0.5)', 'rgba(47,79,79,0.9)');
          imbalances(bars).forEach(function (z) { box(z.t0, z.t1, z.hi, z.lo, z.state === 0 ? 'rgba(147,112,219,0.18)' : 'rgba(147,112,219,0.08)', 'rgba(147,112,219,0.5)'); });
          if (d.strategy === 'DeepFib' && ov.leg && ov.leg.p0 > 0 && ov.leg.p1 > 0) FIB_LV.forEach(function (lv, i) { var gp = lv >= 0.618 && lv <= 0.786;
            cs.createPriceLine({ price: ov.leg.p1 + lv * (ov.leg.p0 - ov.leg.p1), color: gp ? '#ffd700' : '#808080', lineWidth: 1, lineStyle: gp ? 0 : 1, axisLabelVisible: false, title: FIB_LT[i] }); });
        });
        if (tf === 'h4' && ov.leg && ov.leg.p0 > 0 && ov.leg.p1 > 0 && iso(ov.leg.t0) && iso(ov.leg.t1)) {
          var lg = chart.addLineSeries({ color: '#ee82ee', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          var t0 = iso(ov.leg.t0), t1 = iso(ov.leg.t1); if (t0 < t1) lg.setData([{ time: t0, value: ov.leg.p0 }, { time: t1, value: ov.leg.p1 }]);
        }
        (ov.aux || []).forEach(function (a) { if (a.price > 0 && tf === 'h4') cs.createPriceLine({ price: a.price, color: '#c0c0c0', lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: a.label || '' }); });
        var marks = [];
        if (tf === 'h4') {
          swings(bars).forEach(function (s) { marks.push({ time: s.t, position: s.kind === 'hi' ? 'aboveBar' : 'belowBar', color: '#8b949e', shape: s.kind === 'hi' ? 'arrowDown' : 'arrowUp', size: 0.5 }); });
          (ov.swings_hi || []).forEach(function (s) { var t = iso(s.t); if (t) marks.push({ time: t, position: 'aboveBar', color: '#ffc878', shape: 'arrowDown', size: 0.9, text: 'swing hi' }); });
          (ov.swings_lo || []).forEach(function (s) { var t = iso(s.t); if (t) marks.push({ time: t, position: 'belowBar', color: '#ffc878', shape: 'arrowUp', size: 0.9, text: 'swing lo' }); });
        }
        if (d.signal_t) marks.push({ time: tf === 'h4' ? d.signal_t : d.signal_t - (d.signal_t % 86400), position: 'belowBar', color: '#ffffff', shape: 'circle', text: 'signal', size: 1 });
        (d.marks || []).forEach(function (m) { marks.push({ time: m.t, position: 'aboveBar', color: '#ff8c00', shape: 'square', text: m.label, size: 1 }); });
        var set = {}; marks.forEach(function (m) { set[m.time] = m; }); marks = Object.values(set).sort(function (a, b) { return a.time - b.time; });
        guard('markers', function () { cs.setMarkers(marks); });
        var n = bars.length, from = Math.max(0, n - (tf === 'h4' ? 90 : 130));
        chart.timeScale().setVisibleLogicalRange({ from: from, to: n + 5 });
        new ResizeObserver(function () { chart.applyOptions({ width: box.clientWidth }); }).observe(box);
      }
      bar.querySelectorAll('button').forEach(function (b) { b.onclick = function () { tf = b.dataset.tf; bar.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); }); start(); }; });
      start();
    }
  };
})();
