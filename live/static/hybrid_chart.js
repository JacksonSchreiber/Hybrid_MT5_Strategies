// hybrid_chart.js - interactive signal/position chart on Lightweight Charts (self-hosted).
// mount(el, data): data = {bars_h4, bars_d1 [[t,o,h,l,c,v]], levels{entry,sl,tp1,tp2}, overlay{zone,zone2,leg,aux,swings_hi,swings_lo},
//                        signal_t (epoch), digits, marks:[{t,label}], title}
(function () {
  function ema(vals, n) { var k = 2 / (n + 1), out = [], p = vals[0]; for (var i = 0; i < vals.length; i++) { p = i ? p + k * (vals[i] - p) : vals[0]; out.push(p); } return out; }
  function iso(s) { return s ? Math.floor(new Date(s).getTime() / 1000) : null; }
  var EMA = { 20: '#ffd700', 50: '#00bfff', 200: '#ee82ee' };
  window.HybridChart = {
    mount: function (el, d) {
      var tf = 'h4', chart, box = el.querySelector('.hc-box'), bar = el.querySelector('.hc-bar'), status = el.querySelector('.hc-status'), timer = null, gen = 0;
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
        last.forEach(function (b) { var i = barsAll.findIndex(function (x) { return x[0] === b[0]; }); if (i >= 0) barsAll[i] = b; else if (!barsAll.length || b[0] > barsAll[barsAll.length - 1][0]) barsAll.push(b); });
        last.forEach(function (b) { cs.update({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }); });
        closes = barsAll.map(function (b) { return b[4]; });
        [20, 50, 200].forEach(function (n) { var e = ema(closes, n); emaS[n].setData(barsAll.map(function (b, i) { return { time: b[0], value: e[i] }; }).slice(n)); });
        setStatus(tick);
      }
      function build(bars, tick) {
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
        if (tf === 'h4' && ov.zone && ov.zone.hi > 0 && bars.length) {
          var from = iso(ov.zone.from) || bars[0][0], last = bars[bars.length - 1][0];
          var z = chart.addBaselineSeries({ baseValue: { type: 'price', price: ov.zone.lo }, topFillColor1: 'rgba(255,215,0,0.22)', topFillColor2: 'rgba(255,215,0,0.22)', topLineColor: 'rgba(255,215,0,0.5)', bottomLineColor: 'rgba(255,215,0,0.5)', bottomFillColor1: 'rgba(0,0,0,0)', bottomFillColor2: 'rgba(0,0,0,0)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          z.setData(bars.filter(function (b) { return b[0] >= from; }).map(function (b) { return { time: b[0], value: ov.zone.hi }; }));
        }
        if (tf === 'h4' && ov.leg && ov.leg.p0 > 0 && ov.leg.p1 > 0 && iso(ov.leg.t0) && iso(ov.leg.t1)) {
          var lg = chart.addLineSeries({ color: '#ee82ee', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          var t0 = iso(ov.leg.t0), t1 = iso(ov.leg.t1); if (t0 < t1) lg.setData([{ time: t0, value: ov.leg.p0 }, { time: t1, value: ov.leg.p1 }]);
        }
        (ov.aux || []).forEach(function (a) { if (a.price > 0 && tf === 'h4') cs.createPriceLine({ price: a.price, color: '#a0a0a0', lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: a.label || '' }); });
        var marks = [];
        if (tf === 'h4') {
          (ov.swings_hi || []).forEach(function (s) { var t = iso(s.t); if (t) marks.push({ time: t, position: 'aboveBar', color: '#c9d1d9', shape: 'arrowDown', size: 0.6 }); });
          (ov.swings_lo || []).forEach(function (s) { var t = iso(s.t); if (t) marks.push({ time: t, position: 'belowBar', color: '#c9d1d9', shape: 'arrowUp', size: 0.6 }); });
        }
        if (d.signal_t) marks.push({ time: tf === 'h4' ? d.signal_t : d.signal_t - (d.signal_t % 86400), position: 'belowBar', color: '#ffffff', shape: 'circle', text: 'signal', size: 1 });
        (d.marks || []).forEach(function (m) { marks.push({ time: m.t, position: 'aboveBar', color: '#ff8c00', shape: 'square', text: m.label, size: 1 }); });
        var set = {}; marks.forEach(function (m) { set[m.time] = m; }); marks = Object.values(set).sort(function (a, b) { return a.time - b.time; });
        cs.setMarkers(marks);
        var n = bars.length, from = Math.max(0, n - (tf === 'h4' ? 110 : 130));
        chart.timeScale().setVisibleLogicalRange({ from: from, to: n + 5 });
        new ResizeObserver(function () { chart.applyOptions({ width: box.clientWidth }); }).observe(box);
      }
      bar.querySelectorAll('button').forEach(function (b) { b.onclick = function () { tf = b.dataset.tf; bar.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); }); start(); }; });
      start();
    }
  };
})();
