// signal_plan.js - trader 2026-10-12 (docs/live_plans.md): the live signal page's plan card - the Practice lever builder (backtest.js's plan
// editing, same "levers/v1" config, same palette / presets / undo / drag order / validation) on a LIVE signal. Approve posts the plan to
// /signal_plan; the web app validates it again (live limits: entry at most 100%, every add under a 1R cap on the open risk), writes the
// EA's approve task and the plan record, and the plan runner (live/plan_live.py) carries it out. Data: the JSON in #sp-data.
(function () {
  var CAP = 200;
  var uid = function () { return Math.random().toString(36).slice(2, 10); };
  var clone = function (o) { return JSON.parse(JSON.stringify(o)); };
  var TYPE_LABEL = { add_reach: 'Add', add_bar: 'Add', add_tp1: 'Add', bank: 'Take profit', move: 'Stop', trail: 'Stop', cut_avg: 'Cut', cut_unless: 'Cut' };
  var PALETTE = [
    { name: 'Adds', items: [
      { type: 'add_reach', label: 'Add at a level', help: 'Add a share when the trade reaches +X R (optionally within N hours)', tpl: { pct: 50, at: 2.0, within_h: 24, stop: 0 } },
      { type: 'add_bar', label: 'Add at a bar close', help: 'Add a share at the bar-N close (optionally early at +X R; optionally wait at the bar close while the average R is weak; the total risk kept at or below 1R)', tpl: { pct: 50, bar: 6, early_at: null, hold_avg: null, stop: 'trade', wait_avg: null, risk_cap: 1.0 } },
      { type: 'add_tp1', label: 'Add at TP1', help: 'Add a share when TP1 is reached', tpl: { pct: 50, min_R: 1.0, stop: 1.0 } }] },
    { name: 'Take profit', items: [
      { type: 'bank', label: 'Bank a share', help: 'Bank a % of what is open at +X R', tpl: { pct: 25, at: 1.0, tp1: false } }] },
    { name: 'Stop moves', items: [
      { type: 'move', label: 'Move the stop', help: 'Move the stop to a level when +X R is reached (tighten only)', tpl: { to: 0, when: 1.0, tp1: false, legs: 'all', src: 'exit' } },
      { type: 'trail', label: 'Trail the stop', help: 'Every N hours, the stop goes to X R below the highest price of those hours (raise only, or follow it both ways)', tpl: { every_h: 1, dist: 1.0, lower: false } }] },
    { name: 'Cuts', items: [
      { type: 'cut_avg', label: 'Cut on a weak average', help: 'Close at N hours if the average R since entry (or over the last 12 h) is below X', tpl: { at_h: 1, below: 0, window_h: null } },
      { type: 'cut_unless', label: 'Cut unless it got going', help: 'Close at N hours unless +X R was reached', tpl: { at_h: 24, reach: 1.5 } }] }];
  var STOP_OPTS = [['trade', "the trade's stop"], ['-1', '-1R (signal stop)'], ['-0.5', '-0.5R'], ['0', 'entry'], ['0.25', '+0.25R'], ['0.5', '+0.5R'], ['1', '+1R'], ['2', '+2R']];

  window.spApp = function () {
    var D = JSON.parse(document.getElementById('sp-data').textContent);
    return {
      D: D, err: '', busy: false, done: '', inverse: false, entryMode: 'market', rv: null, edit: D.mode === 'edit', sugNote: '',
      plan: { entry: 100, tp2: true, trim: false, levers: [] }, hist: [], hpos: -1, presetSel: '', presetAbout: '',
      palette: PALETTE, stopOpts: STOP_OPTS, sortable: null,
      init: function () {
        var self = this;
        if (this.edit) this.fromConfig(D.plan, D.ids);       // trader 2026-10-13: editing a running plan - its levers keep their ids
        else this.loadPreset(D.presets[0].key);
        this.hist = [this.snap()]; this.hpos = 0;
        if (this.edit && D.suggest) {                        // the positions reviewer's edit_plan, loaded as an unsaved change (Undo goes back)
          this.fromConfig(D.suggest.plan, D.suggest.ids); this.commit();
          this.sugNote = "The reviewer's suggestion is loaded: " + (D.suggest.changes.join('; ') || 'see the levers') + '. Press Review the change to see what it does, then Save - or Undo to go back to your plan.';
        }
        if (this.edit && D.suggest_err) this.err = D.suggest_err;
        this.$nextTick(function () { self.bindSort(); });
        document.addEventListener('keydown', function (e) {
          var path = e.composedPath ? e.composedPath() : [e.target];
          for (var q = 0; q < path.length; q++) { var tg = path[q] && path[q].tagName; if (tg && /^(INPUT|TEXTAREA|SELECT|SL-INPUT|SL-TEXTAREA)$/.test(tg)) return; }
          var z = (e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z'), y = (e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y');
          if (z && !e.shiftKey) { e.preventDefault(); self.undo(); } else if ((z && e.shiftKey) || y) { e.preventDefault(); self.redo(); }
        });
      },
      // ---------------------------------------------------------------- formatting
      fmtR: function (v) { if (v === null || v === undefined) return '-'; v = +v; return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + 'R'; },
      typeLabel: function (t) { return TYPE_LABEL[t] || t; },
      kindOf: function (t) { return t.indexOf('add') === 0 ? 'add' : t === 'bank' ? 'bank' : (t === 'move' || t === 'trail') ? 'move' : t.indexOf('cut') === 0 ? 'cut' : ''; },
      lotsOf: function (pct) { var x = D.lots * (+pct || 0) / 100, s = D.step || 0.01; return (Math.round(x / s) * s).toFixed(2); },
      dirShown: function () { return this.inverse ? (D.direction === 'BUY' ? 'SELL' : 'BUY') : D.direction; },
      // ---------------------------------------------------------------- plan <-> config
      toConfig: function () {
        return { schema: 'levers/v1', entry: { pct: +this.plan.entry }, tp2: !!this.plan.tp2, cap_pct: CAP, trim_to_cap: !!this.plan.trim,
                 levers: this.plan.levers.map(function (l) { var c = clone(l); delete c.id; return c; }) };
      },
      fromConfig: function (cfg, ids) {
        this.plan = { entry: cfg.entry.pct, tp2: cfg.tp2 !== false, trim: !!cfg.trim_to_cap,
                      levers: (cfg.levers || []).map(function (l, i) { var c = clone(l); c.id = (ids && ids[i]) || uid(); return c; }) };
      },
      ids: function () { return this.plan.levers.map(function (l) { return l.id; }); },
      snap: function () { return JSON.stringify({ c: this.toConfig(), ids: this.ids() }); },     // the ids too: an edit keeps each lever's id through undo
      commit: function () {
        var s = this.snap(); if (this.hpos >= 0 && this.hist[this.hpos] === s) return;
        this.hist = this.hist.slice(0, this.hpos + 1); this.hist.push(s); if (this.hist.length > 300) this.hist.shift(); this.hpos = this.hist.length - 1;
        this.presetSel = ''; this.presetAbout = ''; this.rv = null;
      },
      restore: function (s) { var h = JSON.parse(s); this.fromConfig(h.c, h.ids); this.presetSel = ''; this.rv = null; this.$nextTick(this.bindSort.bind(this)); },
      undo: function () { if (this.hpos <= 0) return; this.hpos--; this.restore(this.hist[this.hpos]); },
      redo: function () { if (this.hpos >= this.hist.length - 1) return; this.hpos++; this.restore(this.hist[this.hpos]); },
      // ---------------------------------------------------------------- editing a running plan (trader 2026-10-13)
      leverState: function (L) { return (D.state && D.state[L.id]) || ''; },
      editPost: function (preview) {
        var self = this; if (!self.v.ok || self.busy) return; self.busy = true; self.err = '';
        var body = new URLSearchParams({ key: D.key, plan: JSON.stringify(self.toConfig()), ids: JSON.stringify(self.ids()), base_rev: String(D.rev), preview: preview ? '1' : '0' });
        fetch('/plan_edit', { method: 'POST', body: body }).then(function (r) { return r.json(); }).then(function (j) {
          self.busy = false;
          if (!j.ok) { self.err = j.error || 'refused'; if (preview) self.rv = null; return; }
          if (preview) { self.rv = j; return; }
          self.rv = null; self.done = j.msg; setTimeout(function () { location.reload(); }, 1500);
        }).catch(function (e) { self.busy = false; self.err = 'network error: ' + e; });
      },
      review: function () { this.editPost(true); },
      save: function () { if (!this.rv) return; this.editPost(false); },
      loadPreset: function (key) {
        var p = D.presets.filter(function (x) { return x.key === key; })[0]; if (!p) return;
        var cfg = clone(p.cfg);
        if (cfg.entry && cfg.entry.pct > 100) cfg.entry.pct = 100;                // live: the entry is at most 100%
        this.fromConfig(cfg); this.commit(); this.presetSel = key; this.presetAbout = p.about || '';
        this.$nextTick(this.bindSort.bind(this));
      },
      // ---------------------------------------------------------------- edits (as Practice)
      add: function (it) { var c = clone(it.tpl); c.type = it.type; c.id = uid();
        if (c.type === 'cut_avg') { var prev = this.plan.levers.filter(function (l) { return l.type === 'cut_avg'; }); if (prev.length) c.at_h = (+prev[prev.length - 1].at_h || 0) + 4; }
        this.plan.levers.push(c); this.commit(); this.$nextTick(this.bindSort.bind(this)); },
      toggleWin: function (L, ev) { L.window_h = ev.target.checked ? 12 : null; this.commit(); },
      addByKey: function (key) { var p = key.split('|'); var it = null; PALETTE.forEach(function (g) { g.items.forEach(function (x) { if (x.type === p[0] && x.label === p[1]) it = x; }); }); if (it) this.add(it); },
      remove: function (idx) { this.plan.levers.splice(idx, 1); this.commit(); },
      num: function (ev, allowNull) { var s = ev.target.value; if ((s === '' || s === null) && allowNull) return null; var v = parseFloat(s); return isFinite(v) ? v : undefined; },
      setF: function (L, k, ev, allowNull) { var v = this.num(ev, allowNull);
        if (v === undefined || (v === null && !allowNull)) { ev.target.value = (L[k] === null || L[k] === undefined) ? '' : L[k]; return; }
        L[k] = v; this.commit(); },
      setB: function (L, k, ev) { L[k] = !!ev.target.checked; this.commit(); },
      setS: function (L, k, ev) { L[k] = ev.target.value; this.commit(); },
      setStop: function (L, ev) { var v = ev.target.value; L.stop = v === 'trade' ? 'trade' : parseFloat(v); this.commit(); },
      toggleEarly: function (L, ev) { if (ev.target.checked) { L.early_at = 1.0; } else { L.early_at = null; L.hold_avg = null; } this.commit(); },
      toggleHold: function (L, ev) { L.hold_avg = ev.target.checked ? 0.5 : null; this.commit(); },
      toggleWait: function (L, ev) { L.wait_avg = ev.target.checked ? 0.0 : null; this.commit(); },
      toggleCap: function (L, ev) { L.risk_cap = ev.target.checked ? 1.0 : null; this.commit(); },
      flip: function (L, k) { if (L[k] === null || L[k] === undefined) return; L[k] = L[k] === 0 ? 0 : -L[k]; this.commit(); },
      setEntry: function (ev) { var v = parseFloat(ev.target.value); if (isFinite(v)) { this.plan.entry = Math.min(100, v); ev.target.value = this.plan.entry; this.commit(); } else ev.target.value = this.plan.entry; },
      setTp2: function (ev) { this.plan.tp2 = !!ev.target.checked; this.commit(); },
      setTrim: function (ev) { this.plan.trim = !!ev.target.checked; this.commit(); },
      bindSort: function () {
        var el = document.getElementById('bt-stack'), self = this;
        if (!el || !window.Sortable) return;
        if (this.sortable && this.sortable.el === el) return;
        if (this.sortable) { try { this.sortable.destroy(); } catch (e) {} }
        this.sortable = Sortable.create(el, { handle: '.bt-grip', animation: 160, draggable: '.bt-lever', ghostClass: 'bt-ghost',
          onEnd: function (e) {
            if (e.oldIndex === e.newIndex) return;
            var item = e.item, parent = e.from; parent.removeChild(item); parent.insertBefore(item, parent.children[e.oldIndex] || null);
            var arr = self.plan.levers.slice(), m = arr.splice(e.oldIndex, 1)[0]; arr.splice(e.newIndex, 0, m); self.plan.levers = arr; self.commit();
          } });
      },
      // ---------------------------------------------------------------- validation (the server re-checks)
      get v() {
        var msgs = [], ok = true, e = +this.plan.entry, adds = 0;
        if (!(e >= 1 && e <= 100)) { msgs.push({ level: 'dn', text: 'Live: the entry must be 1-100% (the 1% risk rule).' }); ok = false; }
        this.plan.levers.forEach(function (l, i) {
          var n = i + 1;
          if (l.type.indexOf('add') === 0) { adds += +l.pct || 0; if (!(l.pct >= 1 && l.pct <= 200)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': an add must be 1-200%.' }); ok = false; } }
          if (l.type === 'bank' && !(l.pct >= 0 && l.pct <= 100)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': a bank must be 0-100%.' }); ok = false; }
          if ((l.type === 'bank' || l.type === 'add_reach') && !(l.at > 0)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': the level must be above 0R.' }); ok = false; }
          if (l.type === 'move') { if (!(l.to >= -1)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': a stop can never go below -1R.' }); ok = false; }
            else if (l.to >= l.when) msgs.push({ level: 'warn', text: 'Lever ' + n + ': the new stop is at or above its trigger - it would close the trade at once.' }); }
          if ((l.type === 'add_reach' || l.type === 'add_bar' || l.type === 'add_tp1') && l.stop !== 'trade' && !(l.stop >= -1)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': a stop can never go below -1R.' }); ok = false; }
          if (l.type === 'add_bar' && l.risk_cap != null && !(l.risk_cap >= 0.1 && l.risk_cap <= 10)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': the risk cap must be 0.1-10R.' }); ok = false; }
          if ((l.type === 'cut_avg' || l.type === 'cut_unless') && !(l.at_h >= 0.25)) { msgs.push({ level: 'dn', text: 'Lever ' + n + ': the cut time must be at least 15 minutes.' }); ok = false; }
        });
        if (this.plan.levers.filter(function (l) { return l.type === 'trail'; }).length > 1) { msgs.push({ level: 'dn', text: 'At most one trailing stop in a plan.' }); ok = false; }
        var worst = Math.round((e + adds) * 100) / 100, over = worst > CAP + 1e-9;
        if (over && !this.plan.trim) { msgs.unshift({ level: 'dn', text: 'Adds would reach ' + worst + '% - over the ' + CAP + '% cap. Remove or shrink an add, or turn on "trim adds to fit the cap".' }); ok = false; }
        else if (over) msgs.unshift({ level: 'warn', text: 'Adds would reach ' + worst + '%; the later adds are trimmed to fit the ' + CAP + '% cap.' });
        return { ok: ok, worst: worst, over: over, msgs: msgs };
      },
      liveNotes: function () {        // what the plan becomes on this account: lot steps, the 1R cap on adds
        var out = [], self = this, s = D.step || 0.01, mn = D.vmin || 0.01, ent = D.lots * this.plan.entry / 100;
        if (Math.round(ent / s) * s < mn - 1e-9) out.push({ level: 'dn', text: 'The entry is under the minimum lot (' + mn + ') - raise it.' });
        this.plan.levers.forEach(function (l, i) {
          var n = i + 1;
          if (l.type === 'bank') { var b = ent * l.pct / 100, r = Math.round(b / s) * s;
            if (r < mn - 1e-9) out.push({ level: 'warn', text: 'Lever ' + n + ': ' + l.pct + '% of ' + ent.toFixed(2) + ' lots is under the minimum lot - nothing would be banked (unless adds grow the position first).' });
            else if (Math.abs(r - b) > 1e-9) out.push({ level: '', text: 'Lever ' + n + ': bank ' + l.pct + '% = ' + r.toFixed(2) + ' of ' + ent.toFixed(2) + ' lots (' + Math.round(100 * r / ent) + '%, the lot step).' }); }
          if (l.type.indexOf('add') === 0) { var a = D.lots * l.pct / 100, ra = Math.round(a / s) * s;
            out.push({ level: ra < mn - 1e-9 ? 'warn' : '', text: 'Lever ' + n + ': add ' + l.pct + '% = ' + (ra < mn - 1e-9 ? 'under the minimum lot - it would be skipped' : ra.toFixed(2) + ' lots') + '.' }); }
        });
        if (this.plan.levers.some(function (l) { return l.type.indexOf('add') === 0; }))
          out.push({ level: '', text: 'Live: every add keeps the total open risk at or below 1R - the stops are raised to make room, and if they cannot be, the add is skipped (the 1% risk rule).' });
        if (!this.plan.tp2) out.push({ level: '', text: 'TP2 off: no take-profit at the broker - the trade runs until a stop, a cut or your close.' });
        return out;
      },
      // ---------------------------------------------------------------- the chart (Inverse redraws it in place)
      setInverse: function (ev) {
        this.inverse = !!ev.target.checked;
        var C0 = window.SP_CHART; if (!C0 || !window.HybridChart) return;
        var el = document.getElementById(C0.id); if (!el) return;
        var L = C0.data.levels || {}, e = +L.entry, mir = function (p) { return (p === null || p === undefined || p === '' || !+p) ? p : +(2 * e - p).toFixed(C0.data.digits || 5); };
        var d = Object.assign({}, C0.data, { levels: this.inverse ? Object.assign({}, L, { sl: mir(L.sl), tp1: mir(L.tp1), tp2: mir(L.tp2), tp: mir(L.tp) }) : L });
        var box = el.querySelector('.hc-box'); if (box) box.innerHTML = '';
        HybridChart.mount(el, d);
      },
      // ---------------------------------------------------------------- approve
      approve: function () {
        var self = this; if (!self.v.ok || self.busy) return;
        var what = (self.inverse ? 'INVERSE (' + self.dirShown() + ') ' : '') + D.symbol + ' ' + (self.inverse ? '' : D.direction) + ' at ' + (self.entryMode === 'market' ? 'market' : 'the original entry (pending)');
        if (!confirm('Approve ' + what + ' with this plan?\n\nEntry ' + self.plan.entry + '% = ' + self.lotsOf(self.plan.entry) + ' lots, ' + self.plan.levers.length + ' levers, TP2 ' + (self.plan.tp2 ? 'on' : 'off') + '.')) return;
        self.busy = true; self.err = '';
        var body = new URLSearchParams({ key: D.key, plan: JSON.stringify(self.toConfig()), inverse: self.inverse ? '1' : '0', entry_mode: self.entryMode, preset: self.presetSel || '' });
        fetch('/signal_plan', { method: 'POST', body: body }).then(function (r) { return r.json(); }).then(function (j) {
          self.busy = false;
          if (!j.ok) { self.err = j.error || 'refused'; return; }
          self.done = j.msg || 'sent'; setTimeout(function () { location.href = '/signal/' + encodeURIComponent(D.key) + '?msg=' + encodeURIComponent(j.msg || 'approved with your plan') + '&ok=1'; }, 2500);
        }).catch(function (e) { self.busy = false; self.err = String(e); });
      }
    };
  };
})();
