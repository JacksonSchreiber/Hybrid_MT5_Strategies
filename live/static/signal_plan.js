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
  var LVN = { entry: 'Entry', sl: 'Stop', tp1: 'TP1', tp2: 'TP2' };
  var LVK = ['entry', 'sl', 'tp1', 'tp2'];
  var STOP_OPTS = [['trade', "the trade's stop"], ['-1', '-1R (signal stop)'], ['-0.5', '-0.5R'], ['0', 'entry'], ['0.25', '+0.25R'], ['0.5', '+0.5R'], ['1', '+1R'], ['2', '+2R']];

  window.spApp = function () {
    var D = JSON.parse(document.getElementById('sp-data').textContent);
    return {
      D: D, err: '', busy: false, done: '', inverse: false, entryMode: 'market', rv: null, edit: D.mode === 'edit', sugNote: '',
      plan: { entry: 100, tp2: true, trim: false, levers: [], levels: {}, armOn: false, when: [], cancel: [] }, hist: [], hpos: -1, presetSel: '', presetAbout: '',
      palette: PALETTE, stopOpts: STOP_OPTS, sortable: null, LVN: LVN,
      init: function () {
        var self = this;
        window.addEventListener('sp-load-plan', function (e) {     // 2026-10-14: "Load into the builder" on the advisor's plan
          if (self.edit || !e.detail || !e.detail.plan) return;
          var cfg = clone(e.detail.plan); if (cfg.entry && cfg.entry.pct > 100) cfg.entry.pct = 100;
          if (self.inverse) { self.inverse = false; }
          var left = '';                                       // an EA without plan_levels: its levels are left out, said so (they'd only block the approve)
          if (cfg.levels && !self.lvOk()) {
            left = LVK.filter(function (k) { return cfg.levels[k] !== null && cfg.levels[k] !== undefined; }).map(function (k) { return LVN[k] + ' ' + self.fp(cfg.levels[k]); }).join(', ');
            delete cfg.levels;
          }
          self.fromConfig(cfg); self.commit(); self.presetSel = '';
          self.presetAbout = (e.detail.label || "The advisor's plan") + (left ? ' - its levels (' + left + ') were left out: the EA on this chart cannot take custom levels until the 2026-10-14 EA update.' : '');
          self.$nextTick(function () { self.bindSort(); self.redraw(); var el = document.getElementById('sp-app'); if (el) el.scrollIntoView({ behavior: 'smooth' }); });
        });
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
      lotsOf: function (pct) { var x = this.fullLots() * (+pct || 0) / 100, s = D.step || 0.01; return (Math.round(x / s) * s).toFixed(2); },
      // ---------------------------------------------------------------- levels (2026-10-14)
      base: function (k) { var b = D.base || {}; return b[k] === undefined ? null : b[k]; },
      eff: function (k) { var v = this.plan.levels[k]; return (v === null || v === undefined || v === '') ? this.base(k) : +v; },
      lvOk: function () { return !!D.plan_levels && !this.inverse; },
      lvChanged: function (k) { var v = this.plan.levels[k], b = this.base(k);
        if (v === null || v === undefined || v === '') return false; if (b === null) return true; return Math.abs(+v - b) > 1e-9 * Math.max(1, Math.abs(b)); },
      anyLvChanged: function () { var self = this; return LVK.some(function (k) { return self.lvChanged(k); }); },
      pointStep: function () { return D.point || 0.00001; },
      dir: function () { return D.direction === 'BUY' ? 1 : -1; },
      R1: function () { return Math.abs(this.eff('entry') - this.eff('sl')); },
      fullLots: function () { var R0 = Math.abs(this.base('entry') - this.base('sl')), R1 = this.R1(), s = D.step || 0.01;
        if (!this.anyLvChanged() || !(R1 > 0) || !(R0 > 0)) return D.lots; return Math.floor(D.lots * R0 / R1 / s + 1e-9) * s; },
      fp: function (v) { return v === null || v === undefined ? '-' : (+v).toFixed(D.digits || 5); },
      lvNote: function (k) {
        var e = this.eff('entry'), R1 = this.R1(), d = this.dir();
        if (k === 'entry') return this.lvChanged('entry') ? '' : 'the signal\'s';
        if (k === 'sl') return R1 > 0 ? '1R = ' + this.fp(R1) + (this.lvChanged('sl') || this.lvChanged('entry') ? ' (the signal\'s ' + this.fp(Math.abs(this.base('entry') - this.base('sl'))) + ')' : '') : '';
        var v = this.eff(k); if (v === null || !(R1 > 0)) return k === 'tp1' ? 'none (TP1 levers off)' : 'none';
        return this.fmtR(d * (v - e) / R1) + (k === 'tp2' && !this.plan.tp2 ? ' (TP2 is off below)' : '');
      },
      orderType: function () { var e = this.eff('entry'), t = D.tick || {}, buy = this.dir() > 0, m = buy ? t.ask : t.bid;
        if (!m) return (buy ? 'BUY' : 'SELL') + ' LIMIT or STOP, by where the price is when it is sent';
        return buy ? (e < m ? 'a BUY LIMIT - the price is above it now' : 'a BUY STOP - the price is below it now') : (e > m ? 'a SELL LIMIT - the price is below it now' : 'a SELL STOP - the price is above it now'); },
      sizeNote: function () { var R1 = this.R1(); if (!(R1 > 0)) return '';
        return 'Size at the same 1% risk: about ' + this.fullLots().toFixed(2) + ' lots for 100% (the signal\'s ' + D.lots.toFixed(2) + ') - the EA sizes it exactly from your entry and stop. Every lever\'s R counts from your fill to your stop.'; },
      setLevel: function (k, ev) { var s = ev.target.value;
        if (s === '' || s === null) { this.plan.levels[k] = (k === 'tp1') ? null : this.base(k); ev.target.value = this.plan.levels[k] === null ? '' : this.plan.levels[k]; this.commit(); this.redraw(); return; }
        var v = parseFloat(s); if (!(v > 0)) { ev.target.value = this.plan.levels[k] === null ? '' : this.plan.levels[k]; return; }
        this.plan.levels[k] = v; this.commit(); this.redraw(); },
      resetLevel: function (k) { this.plan.levels[k] = this.base(k); this.commit(); this.redraw(); },
      setEntryH: function (ev) { var v = parseFloat(ev.target.value); if (v >= 1 && v <= 120) { this.plan.levels.entry_h = v; this.commit(); } else ev.target.value = this.plan.levels.entry_h; },
      // ---------------------------------------------------------------- the wait (2026-10-14)
      defCond: function (type) { var b = this.base('entry'), up = this.dir() > 0;
        if (type === 'touch' || type === 'close') return { type: type, side: up ? 'above' : 'below', px: b, tf: 'h1' };
        if (type === 'after') return { type: 'after', t: new Date(Date.now() + 3600e3).toISOString().slice(0, 16) + ':00Z' };
        return { type: 'hours', h: 24 }; },
      setArm: function (ev) { this.plan.armOn = !!ev.target.checked;
        if (this.plan.armOn && !this.plan.when.length) this.plan.when.push(this.defCond('close'));
        if (this.plan.armOn && !this.plan.cancel.length) this.plan.cancel.push({ type: 'hours', h: 24 });
        this.commit(); },
      addCond: function (which) { this.plan[which].push(this.defCond(which === 'cancel' ? 'hours' : 'touch')); this.commit(); },
      setCond: function (c, k, ev) { var v = ev.target.value;
        if (k === 'type') { var n = this.defCond(v); Object.keys(c).forEach(function (x) { delete c[x]; }); Object.assign(c, n); }
        else if (k === 'px' || k === 'h') { var f = parseFloat(v); if (!(f > 0)) { ev.target.value = c[k]; return; } c[k] = f; }
        else c[k] = v;
        this.commit(); },
      setCondT: function (c, ev) { var d = new Date(ev.target.value); if (isNaN(d.getTime())) return; c.t = d.toISOString().slice(0, 19) + 'Z'; this.commit(); },
      localDT: function (t) { var d = new Date(t); if (isNaN(d.getTime())) return ''; var p = function (n) { return (n < 10 ? '0' : '') + n; };
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()); },
      condCfg: function (c) { var o = { type: c.type };
        if (c.type === 'touch' || c.type === 'close') { o.side = c.side; o.px = +c.px; if (c.type === 'close') o.tf = c.tf; }
        else if (c.type === 'after') o.t = c.t; else o.h = +c.h; return o; },
      dirShown: function () { return this.inverse ? (D.direction === 'BUY' ? 'SELL' : 'BUY') : D.direction; },
      // ---------------------------------------------------------------- plan <-> config
      toConfig: function () {
        var self = this, out = { schema: 'levers/v1', entry: { pct: +this.plan.entry }, tp2: !!this.plan.tp2, cap_pct: CAP, trim_to_cap: !!this.plan.trim,
                 levers: this.plan.levers.map(function (l) { var c = clone(l); delete c.id; return c; }) };
        if (!this.edit) {
          var lv = {}; LVK.forEach(function (k) { if (self.lvChanged(k)) lv[k] = +self.plan.levels[k]; });
          if (lv.entry !== undefined) lv.entry_h = +this.plan.levels.entry_h || 12;
          if (Object.keys(lv).length) out.levels = lv;
          if (this.plan.armOn && this.plan.when.length) out.arm = { when: this.plan.when.map(this.condCfg), cancel: this.plan.cancel.map(this.condCfg) };
        }
        return out;
      },
      fromConfig: function (cfg, ids) {
        var self = this, lv = {}, cl = cfg.levels || {};
        LVK.forEach(function (k) { lv[k] = (cl[k] !== undefined && cl[k] !== null) ? +cl[k] : self.base(k); });
        lv.entry_h = cl.entry_h || 12;
        var arm = cfg.arm || null;
        this.plan = { entry: cfg.entry.pct, tp2: cfg.tp2 !== false, trim: !!cfg.trim_to_cap, levels: lv,
                      armOn: !!(arm && arm.when && arm.when.length), when: arm ? clone(arm.when || []) : [], cancel: arm ? clone(arm.cancel || []) : [],
                      levers: (cfg.levers || []).map(function (l, i) { var c = clone(l); c.id = (ids && ids[i]) || uid(); return c; }) };
      },
      ids: function () { return this.plan.levers.map(function (l) { return l.id; }); },
      snap: function () { return JSON.stringify({ c: this.toConfig(), ids: this.ids() }); },     // the ids too: an edit keeps each lever's id through undo
      commit: function () {
        var s = this.snap(); if (this.hpos >= 0 && this.hist[this.hpos] === s) return;
        this.hist = this.hist.slice(0, this.hpos + 1); this.hist.push(s); if (this.hist.length > 300) this.hist.shift(); this.hpos = this.hist.length - 1;
        this.presetSel = ''; this.presetAbout = ''; this.rv = null;
      },
      restore: function (s) { var h = JSON.parse(s); this.fromConfig(h.c, h.ids); this.presetSel = ''; this.rv = null; this.$nextTick(this.bindSort.bind(this)); this.redraw(); },
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
        if (!this.edit && this.hist.length) { var cur = this.toConfig(); if (cur.levels && !cfg.levels) cfg.levels = cur.levels; if (cur.arm && !cfg.arm) cfg.arm = cur.arm; }   // a preset is levers: your levels and wait stay
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
        if (!this.edit) {
          var d = this.dir(), en = this.eff('entry'), sl = this.eff('sl'), t1 = this.eff('tp1'), t2 = this.eff('tp2'), R1 = Math.abs(en - sl);
          if (!(d * (en - sl) > 0)) { msgs.push({ level: 'dn', text: 'The stop must be ' + (d > 0 ? 'below' : 'above') + ' the entry.' }); ok = false; }
          if (t2 !== null && !(d * (t2 - en) > 0)) { msgs.push({ level: 'dn', text: 'TP2 must be ' + (d > 0 ? 'above' : 'below') + ' the entry.' }); ok = false; }
          if (t1 !== null && !(d * (t1 - en) > 0)) { msgs.push({ level: 'dn', text: 'TP1 must be ' + (d > 0 ? 'above' : 'below') + ' the entry.' }); ok = false; }
          if (ok && this.plan.tp2 && t2 !== null && R1 > 0 && Math.abs(t2 - en) < R1 && this.anyLvChanged()) msgs.push({ level: 'warn', text: 'TP2 is under 1R from the entry - the EA refuses a market entry below its 1.0 R:R floor.' });
          if (this.anyLvChanged() && !this.lvOk()) { msgs.push({ level: 'dn', text: 'Custom levels are not available here (see the Levels box) - reset them to the signal\'s.' }); ok = false; }
          if (this.plan.armOn) {
            if (!this.plan.when.length) msgs.push({ level: 'warn', text: 'The wait has no steps - the order goes in at once.' });
            this.plan.when.concat(this.plan.cancel).forEach(function (c) { if ((c.type === 'touch' || c.type === 'close') && !(+c.px > 0)) { msgs.push({ level: 'dn', text: 'A wait condition needs a price.' }); ok = false; } });
            if (this.plan.when.length && !this.plan.cancel.some(function (c) { return c.type === 'hours'; })) msgs.push({ level: 'warn', text: 'No time limit on the wait - it is cancelled after 48 h.' });
            if (this.plan.when.length && !this.lvChanged('entry')) msgs.push({ level: '', text: 'When the wait is over it enters at market; the EA then checks the R:R to TP2 from that price (min 1.0) and refuses if the price has run too far - set your own levels if it might.' });
          }
        }
        var worst = Math.round((e + adds) * 100) / 100, over = worst > CAP + 1e-9;
        if (over && !this.plan.trim) { msgs.unshift({ level: 'dn', text: 'Adds would reach ' + worst + '% - over the ' + CAP + '% cap. Remove or shrink an add, or turn on "trim adds to fit the cap".' }); ok = false; }
        else if (over) msgs.unshift({ level: 'warn', text: 'Adds would reach ' + worst + '%; the later adds are trimmed to fit the ' + CAP + '% cap.' });
        return { ok: ok, worst: worst, over: over, msgs: msgs };
      },
      liveNotes: function () {        // what the plan becomes on this account: lot steps, the 1R cap on adds
        var out = [], self = this, s = D.step || 0.01, mn = D.vmin || 0.01, FL = this.fullLots(), ent = FL * this.plan.entry / 100;
        if (Math.round(ent / s) * s < mn - 1e-9) out.push({ level: 'dn', text: 'The entry is under the minimum lot (' + mn + ') - raise it.' });
        this.plan.levers.forEach(function (l, i) {
          var n = i + 1;
          if (l.type === 'bank') { var b = ent * l.pct / 100, r = Math.round(b / s) * s;
            if (r < mn - 1e-9) out.push({ level: 'warn', text: 'Lever ' + n + ': ' + l.pct + '% of ' + ent.toFixed(2) + ' lots is under the minimum lot - nothing would be banked (unless adds grow the position first).' });
            else if (Math.abs(r - b) > 1e-9) out.push({ level: '', text: 'Lever ' + n + ': bank ' + l.pct + '% = ' + r.toFixed(2) + ' of ' + ent.toFixed(2) + ' lots (' + Math.round(100 * r / ent) + '%, the lot step).' }); }
          if (l.type.indexOf('add') === 0) { var a = FL * l.pct / 100, ra = Math.round(a / s) * s;
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
        if (this.inverse && this.anyLvChanged()) { var self = this; LVK.forEach(function (k) { self.plan.levels[k] = self.base(k); }); this.commit(); }
        this.redraw();
      },
      redraw: function () {           // the chart's lines: Inverse mirrors them; custom levels (2026-10-14) move them
        if (this.edit) return;
        var C0 = window.SP_CHART; if (!C0 || !window.HybridChart) return;
        var el = document.getElementById(C0.id); if (!el) return;
        var L = C0.data.levels || {}, e = +L.entry, dg = C0.data.digits || 5, self = this, mir = function (p) { return (p === null || p === undefined || p === '' || !+p) ? p : +(2 * e - p).toFixed(dg); };
        var lv = this.inverse ? Object.assign({}, L, { sl: mir(L.sl), tp1: mir(L.tp1), tp2: mir(L.tp2), tp: mir(L.tp) }) : Object.assign({}, L);
        if (!this.inverse && this.anyLvChanged()) { lv.entry = this.eff('entry'); lv.sl = this.eff('sl'); lv.tp1 = this.eff('tp1'); if (this.eff('tp2') !== null) { lv.tp2 = this.eff('tp2'); if (!L.tp2) lv.tp = lv.tp2; } }
        var key = JSON.stringify(lv); if (key === this._drawn) return; this._drawn = key;
        var box = el.querySelector('.hc-box'); if (box) box.innerHTML = '';
        HybridChart.mount(el, Object.assign({}, C0.data, { levels: lv }));
      },
      // ---------------------------------------------------------------- approve
      approve: function () {
        var self = this; if (!self.v.ok || self.busy) return;
        var cfg = self.toConfig(), lvl = cfg.levels ? Object.keys(cfg.levels).filter(function (k) { return k !== 'entry_h'; }).map(function (k) { return LVN[k] + ' ' + self.fp(cfg.levels[k]); }).join(', ') : '';
        var what = (self.inverse ? 'INVERSE (' + self.dirShown() + ') ' : '') + D.symbol + ' ' + (self.inverse ? '' : D.direction) + ' at ' + (self.lvChanged('entry') ? 'your entry ' + self.fp(self.eff('entry')) + ' (pending)' : (self.entryMode === 'market' ? 'market' : 'the original entry (pending)'));
        var arm = cfg.arm ? '\n\nIt WAITS (nothing is sent now): ' + cfg.arm.when.length + ' step(s), then the order goes in.' : '';
        if (!confirm((cfg.arm ? 'Arm ' : 'Approve ') + what + ' with this plan?\n\nEntry ' + self.plan.entry + '% = ' + self.lotsOf(self.plan.entry) + ' lots, ' + self.plan.levers.length + ' levers, TP2 ' + (self.plan.tp2 ? 'on' : 'off') + '.' + (lvl ? '\nYour levels: ' + lvl + '.' : '') + arm)) return;
        self.busy = true; self.err = '';
        var body = new URLSearchParams({ key: D.key, plan: JSON.stringify(cfg), inverse: self.inverse ? '1' : '0', entry_mode: self.entryMode, preset: self.presetSel || '' });
        fetch('/signal_plan', { method: 'POST', body: body }).then(function (r) { return r.json(); }).then(function (j) {
          self.busy = false;
          if (!j.ok) { self.err = j.error || 'refused'; return; }
          self.done = j.msg || 'sent'; setTimeout(function () { location.href = '/signal/' + encodeURIComponent(D.key) + '?msg=' + encodeURIComponent(j.msg || 'approved with your plan') + '&ok=1'; }, 2500);
        }).catch(function (e) { self.busy = false; self.err = String(e); });
      }
    };
  };
})();
