// backtest.js - trader + coach 2026-10-04 (backtest tab, phase 2): the Alpine.js component behind live/static/backtest.html.
// The plan (a stack of lever cards) serialises to the rule engine's "levers/v1" config (live/backtest_engine.py) and back; presets
// load a stack (one undoable step); every change pushes a history snapshot (Undo / Redo, Ctrl+Z / Ctrl+Shift+Z, per card); the
// server records the decision BEFORE anything is revealed. Self-hosted libs only: Alpine.js, SortableJS, Shoelace (live/static/vendor).
// Test mode (2026-10-05): commit a first call -> the blind advisor's verdict + stack appear (polled while it works; follow-up questions)
// -> the final call; nothing is revealed until the session's last card, then the grade (trader alone / advisor alone / combined).
(function () {
  var CAP = 200;
  var uid = function () { return Math.random().toString(36).slice(2, 10); };
  var clone = function (o) { return JSON.parse(JSON.stringify(o)); };
  var TYPE_LABEL = { add_reach: 'Add', add_bar: 'Add', add_tp1: 'Add', bank: 'Take profit', move: 'Stop', trail: 'Stop', cut_avg: 'Cut', cut_unless: 'Cut' };
  var PALETTE = [
    { name: 'Adds', items: [
      { type: 'add_reach', label: 'Add at a level', help: 'Add a share when the trade reaches +X R (optionally within N hours)', tpl: { pct: 75, at: 2.0, within_h: 24, stop: 0 } },
      { type: 'add_bar', label: 'Add at a bar close', help: 'Add a share at the bar-N close (optionally early at +X R; optionally wait at the bar close while the average R is weak; the total risk kept at or below 1R by default)', tpl: { pct: 75, bar: 6, early_at: null, hold_avg: null, stop: 'trade', wait_avg: null, risk_cap: 1.0 } },
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

  function post(url, data) {
    var body = new URLSearchParams(); Object.keys(data).forEach(function (k) { if (data[k] !== undefined && data[k] !== null) body.append(k, data[k]); });
    return fetch(url, { method: 'POST', body: body }).then(function (r) { return r.json(); });
  }
  function get(url) { return fetch(url, { cache: 'no-store' }).then(function (r) { return r.json(); }); }

  window.btApp = function () {
    return {
      groups: [], menu: { presets: [] }, sessions: [], startGroup: 'com', startMode: 'practice', devN: '', menuFrozen: null, testLeft: {},
      advTimer: null, aoTimer: null, exps: [], inverse: false, askText: '', proceedNoAdv: false, flash: '', summary: null, dev: false, devText: '', devBlind: true,
      sess: null, done: false, brk: null, C: { tally: {}, info: null, card: null, i: 0, n: 0 }, rev: null, busy: false, err: '',
      rp: null, st: { i: -1, unit: 'hour', speed: 1, play: false, done: false }, rpTimer: null, rpTally: null,
      plan: { entry: 100, tp2: true, trim: false, levers: [] }, hist: [], hpos: -1, presetSel: '', presetAbout: '', basePreset: '',
      palette: PALETTE, stopOpts: STOP_OPTS, sortable: null, wide: window.innerWidth > 640,
      init: function () {
        var self = this;
        get('/api/bt/init').then(function (j) { if (j.ok === false) { self.err = j.error; return; }
          self.groups = j.groups; self.menu = j.menu; self.sessions = j.sessions; self.dev = j.dev; self.menuFrozen = j.menu_frozen; self.testLeft = j.test_left || {};
          self.$nextTick(function () { self.syncGroupSelect(0); });
          self.devText = '```levers\n{"schema": "advisor-levers/v1", "nearest_preset": "ruthless", "rationale": "Weak follow-through on this group: cut fast, bank early.",\n "plan": {"entry": {"pct": 100}, "tp2": true, "levers": [{"type": "bank", "pct": 50, "at": 1.0, "tp1": true}, {"type": "move", "to": 0.25, "when": 1.0, "tp1": true, "legs": "all"}, {"type": "cut_avg", "at_h": 1, "below": 0}]}}\n```';
          var m = location.hash.match(/sid=([0-9a-f]{12})/); if (m) self.resume(m[1]); });
        document.addEventListener('keydown', function (e) {     // ONE listener (Alpine calls init() itself - no x-init; review #4)
          if (!self.sess || self.done || self.brk) return;
          var path = e.composedPath ? e.composedPath() : [e.target];   // typing in a field: the browser's own undo, not the plan's
          for (var q = 0; q < path.length; q++) { var tg = path[q] && path[q].tagName; if (tg && /^(TEXTAREA|SELECT|SL-INPUT|SL-TEXTAREA)$/.test(tg)) return;
            if (tg === 'INPUT' && path[q].type !== 'range') return; }
          if (self.rev && self.rp) {                                   // the step-through (trader 2026-10-16): arrows, space, Home / End
            var k = e.key, hit = true;
            if (k === 'ArrowRight') { self.rpPause(); self.rpStep(1); } else if (k === 'ArrowLeft') { self.rpPause(); self.rpStep(-1); }
            else if (k === ' ') self.rpToggle(); else if (k === 'Home') { self.rpPause(); self.rpGo(-1); } else if (k === 'End') self.rpEnd(); else hit = false;
            if (hit) e.preventDefault(); return;
          }
          if (self.rev) return;
          var z = (e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z'), y = (e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y');
          if (z && !e.shiftKey) { e.preventDefault(); self.undo(); } else if ((z && e.shiftKey) || y) { e.preventDefault(); self.redo(); }
        });
      },
      // review #14: Shoelace drops a value set before its options exist - wait for the options, then set it (start screen re-renders too)
      syncGroupSelect: function (n) {   // the select AND its options must be upgraded custom elements (the autoloader loads them lazily)
        var self = this, el = document.querySelector('.bt-w260'), opts = el ? el.querySelectorAll('sl-option') : [];
        var ready = el && this.groups.length && opts.length >= this.groups.length && typeof el.updateComplete !== 'undefined' &&
                    Array.prototype.every.call(opts, function (o) { return typeof o.getTextLabel === 'function'; });
        if (ready) Promise.all([el.updateComplete].concat(Array.prototype.map.call(opts, function (o) { return o.updateComplete; })))
          .then(function () { el.value = self.startGroup; });
        else if ((n || 0) < 80) setTimeout(function () { self.syncGroupSelect((n || 0) + 1); }, 75);
      },
      // ---------------------------------------------------------------- formatting
      fmt: function (v) { if (v === null || v === undefined || v === '') return '-'; v = +v; return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + 'R'; },
      fmtR: function (v) { if (v === null || v === undefined) return '-'; v = +v; return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + 'R'; },
      // review #7: the card stores price-R (a SELL's SL at +1R); every R label on a SELL card is shown in PROFIT terms, like the levers
      sgn: function () { return this.C.card && this.C.card.direction === 'SELL' ? -1 : 1; },
      fmtP: function (v) { if (v === null || v === undefined) return '-'; return this.fmtR(this.sgn() * v); },
      fmtTH: function (x) {             // trading hours (the chart's time), + the clock span when a weekend sits inside it
        if (!x) return ''; var s = this.fmtT(x);
        return x.clock_h - x.th > 6 ? s + ' (' + this.fmtH(x.clock_h) + ' on the clock, with the weekend)' : s;
      },
      fmtT: function (x) { return x.th < 48 ? (x.th < 10 ? (+x.th).toFixed(1) : Math.round(x.th)) + ' trading hours' : (x.th / 24).toFixed(1) + ' trading days'; },
      // trader 2026-10-13: the highest R the trade reached before it ended (not shown for TP2)
      peakTxt: function (x) { return x && x.peak_R !== undefined ? ' · highest ' + this.fmt(x.peak_R) + (x.peak.th < 1 ? ' in the first hour' : ', ' + this.fmtT(x.peak) + ' in') : ''; },
      fmtH: function (h) { h = +h; return h < 48 ? h.toFixed(h < 10 ? 1 : 0) + ' h' : (h / 24).toFixed(1) + ' days'; },
      cls: function (v) { if (v === null || v === undefined || v === '') return ''; return +v > 0.0005 ? 'up' : +v < -0.0005 ? 'dn' : ''; },
      groupName: function (k) { var g = this.groups.filter(function (x) { return x.key === k; })[0]; return g ? g.name : k; },
      labelOf: function (k) { var p = this.menu.presets.filter(function (x) { return x.key === k; })[0]; return p ? p.label : k; },
      typeLabel: function (t) { return TYPE_LABEL[t] || t; },
      kindOf: function (t) { return t.indexOf('add') === 0 ? 'add' : t === 'bank' ? 'bank' : (t === 'move' || t === 'trail') ? 'move' : t.indexOf('cut') === 0 ? 'cut' : ''; },
      // ---------------------------------------------------------------- plan <-> config
      toConfig: function () {
        return { schema: 'levers/v1', entry: { pct: +this.plan.entry }, tp2: !!this.plan.tp2, cap_pct: CAP, trim_to_cap: !!this.plan.trim,
                 levers: this.plan.levers.map(function (l) { var c = clone(l); delete c.id; return c; }) };
      },
      fromConfig: function (cfg) {
        this.plan = { entry: cfg.entry.pct, tp2: cfg.tp2 !== false, trim: !!cfg.trim_to_cap,
                      levers: (cfg.levers || []).map(function (l) { var c = clone(l); c.id = uid(); return c; }) };
      },
      snap: function () { return JSON.stringify(this.toConfig()); },
      histBase: [],
      commit: function () {             // push a history snapshot after any change
        var s = this.snap(); if (this.hpos >= 0 && this.hist[this.hpos] === s) return;
        this.hist = this.hist.slice(0, this.hpos + 1); this.histBase = this.histBase.slice(0, this.hpos + 1);
        this.hist.push(s); this.histBase.push(null); if (this.hist.length > 300) { this.hist.shift(); this.histBase.shift(); } this.hpos = this.hist.length - 1;
        this.presetSel = ''; this.presetAbout = '';
      },
      undo: function () { if (this.hpos <= 0) return; this.hpos--; this.fromConfig(JSON.parse(this.hist[this.hpos])); this.presetSel = ''; this.syncBase(); this.$nextTick(this.bindSort.bind(this)); },
      syncBase: function () { var cur = this.hist[this.hpos], self = this;     // after undo/redo: the base preset is the last one loaded at or before this point
        for (var k = this.hpos; k >= 0; k--) { if (this.histBase[k]) { this.basePreset = this.histBase[k]; return; } } },
      redo: function () { if (this.hpos >= this.hist.length - 1) return; this.hpos++; this.fromConfig(JSON.parse(this.hist[this.hpos])); this.presetSel = ''; this.syncBase(); this.$nextTick(this.bindSort.bind(this)); },
      loadPreset: function (key) {
        if (!key) return; var cfg = null, about = '';
        if (key === 'default') { cfg = this.C.default.cfg; about = this.C.default.about + ' (' + this.C.default.status + ')'; }
        else if (key === 'raw') { cfg = this.menu.raw; about = 'In at the signal, out at TP2 or the stop - nothing added.'; }
        else if (key === 'advisor') { cfg = this.C.advisor.plan; about = 'Advisor: ' + this.C.advisor.rationale;
          post('/bt/adv_loaded', { sid: this.sess.sid, i: this.C.i }); }
        else if (key === 'advisor_v1') { cfg = this.C.advisor_v1.plan; about = 'Advisor (its blind first plan): ' + this.C.advisor_v1.rationale;   // review 2 #13d
          post('/bt/adv_loaded', { sid: this.sess.sid, i: this.C.i }); }
        else { var p = this.menu.presets.filter(function (x) { return x.key === key; })[0]; cfg = p.cfg; about = p.about; }
        if (key !== 'advisor' && key !== 'advisor_v1') {     // trader 2026-10-07: the 1R risk cap starts ticked on a menu plan's bar add too
          cfg = clone(cfg); var capped = 0;
          (cfg.levers || []).forEach(function (l) { if (l.type === 'add_bar' && !('risk_cap' in l)) { l.risk_cap = 1.0; capped++; } });
          if (capped) about += ' · the 1R risk cap is ticked on its bar add (not part of the frozen preset - untick it for the preset as it was)';
        }
        this.fromConfig(cfg); this.commit(); this.presetSel = key; this.presetAbout = about;   // ONE undoable step
        this.basePreset = key; this.histBase[this.hpos] = key;          // coach 2026-10-04: the preset the plan was loaded from (kept through later edits) = "loaded_preset"
        this.$nextTick(this.bindSort.bind(this));
      },
      // ---------------------------------------------------------------- edits
      add: function (it) { var c = clone(it.tpl); c.type = it.type; c.id = uid();
        if (c.type === 'cut_avg') {                 // trader 2026-10-09: each extra average cut starts 4 h after the previous one (the level stays 0)
          var prev = this.plan.levers.filter(function (l) { return l.type === 'cut_avg'; });
          if (prev.length) c.at_h = (+prev[prev.length - 1].at_h || 0) + 4;
        }
        this.plan.levers.push(c); this.commit(); this.$nextTick(this.bindSort.bind(this)); },
      toggleWin: function (L, ev) { L.window_h = ev.target.checked ? 12 : null; this.commit(); },
      addByKey: function (key) { var p = key.split('|'); var it = null; PALETTE.forEach(function (g) { g.items.forEach(function (x) { if (x.type === p[0] && x.label === p[1]) it = x; }); }); if (it) this.add(it); },
      remove: function (idx) { this.plan.levers.splice(idx, 1); this.commit(); },
      // review #6: a blank or non-numeric entry never leaves the screen disagreeing with the plan - the committed value is written back
      num: function (ev, allowNull) { var s = ev.target.value; if ((s === '' || s === null) && allowNull) return null; var v = parseFloat(s); return isFinite(v) ? v : undefined; },
      setF: function (L, k, ev, allowNull) { var v = this.num(ev, allowNull);
        if (v === undefined || (v === null && !allowNull)) { ev.target.value = (L[k] === null || L[k] === undefined) ? '' : L[k]; return; }
        L[k] = v; this.commit(); },
      setB: function (L, k, ev) { L[k] = !!ev.target.checked; this.commit(); },
      setS: function (L, k, ev) { L[k] = ev.target.value; this.commit(); },
      setStop: function (L, ev) { var v = ev.target.value; L.stop = v === 'trade' ? 'trade' : parseFloat(v); this.commit(); },
      toggleEarly: function (L, ev) { if (ev.target.checked) { L.early_at = 1.0; } else { L.early_at = null; L.hold_avg = null; } this.commit(); },
      toggleHold: function (L, ev) { L.hold_avg = ev.target.checked ? 0.5 : null; this.commit(); },
      toggleWait: function (L, ev) { L.wait_avg = ev.target.checked ? 0.0 : null; this.commit(); },   // trader 2026-10-06: independent of the early add
      toggleCap: function (L, ev) { L.risk_cap = ev.target.checked ? 1.0 : null; this.commit(); },    // trader 2026-10-07: on by default
      flip: function (L, k) { if (L[k] === null || L[k] === undefined) return; L[k] = L[k] === 0 ? 0 : -L[k]; this.commit(); },   // the +/- button: some phone number pads have no minus key
      setEntry: function (ev) { var v = parseFloat(ev.target.value); if (isFinite(v)) { this.plan.entry = v; this.commit(); } else ev.target.value = this.plan.entry; },
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
            // put the DOM back where Alpine expects it, then reorder the state (Alpine re-renders by key)
            var item = e.item, parent = e.from; parent.removeChild(item); parent.insertBefore(item, parent.children[e.oldIndex] || null);
            var arr = self.plan.levers.slice(), m = arr.splice(e.oldIndex, 1)[0]; arr.splice(e.newIndex, 0, m); self.plan.levers = arr; self.commit();
          } });
      },
      // ---------------------------------------------------------------- validation (the server re-checks)
      get v() {
        var msgs = [], ok = true, e = +this.plan.entry, adds = 0;
        if (!(e >= 1 && e <= 200)) { msgs.push({ level: 'dn', text: 'The entry must be 1-200%.' }); ok = false; }
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
        var worst = Math.round((e + adds) * 100) / 100, over = worst > CAP + 1e-9;
        if (over && !this.plan.trim) { msgs.unshift({ level: 'dn', text: 'Adds would reach ' + worst + '% - over the ' + CAP + '% cap. Remove or shrink an add, or turn on "trim adds to fit the cap".' }); ok = false; }
        else if (over) msgs.unshift({ level: 'warn', text: 'Adds would reach ' + worst + '%; the later adds are trimmed to fit the ' + CAP + '% cap.' });
        return { ok: ok, worst: worst, over: over, msgs: msgs };
      },
      // ---------------------------------------------------------------- session flow
      start: function () {
        var self = this; self.busy = true;
        if (self.startMode === 'test' && !confirm('Start a TEST session?\n\nEach signal can be tested only once, ever. Nothing is revealed until the last card. ' +
            'Every card takes two steps: your first call, then the advisor\'s view, then your final call.')) { self.busy = false; return; }
        post('/bt/start', { group: self.startGroup, mode: self.startMode, n: (self.dev && self.devN) ? self.devN : null }).then(function (j) { self.busy = false;
          if (j.ok === false) { self.err = j.error; return; } self.resume(j.sid); });
      },
      resume: function (sid) { this.sess = { sid: sid }; location.hash = 'sid=' + sid; this.load(); },
      setInverse: function (ev) { this.inverse = !!ev.target.checked; this.chart(!!this.rev); },   // trader 2026-10-09: redraw, no reload
      dirShown: function () { var d = this.C.card && this.C.card.direction; if (!d) return ''; return this.inverse ? (d === 'BUY' ? 'SELL' : 'BUY') + ' (inverted)' : d; },
      handOver: function () {            // trader 2026-10-08: the advisor plays the rest of this Test session; its results are shown
        var self = this, left = this.brk.n - this.brk.i;
        if (!confirm('Let the advisor play the rest?\n\nYour session ends here and is graded on the ' + this.brk.i + ' cards you played. The advisor answers the other ' +
            left + ' cards blind, then you see its results card by card. Those ' + left + ' signals are used up - you can never play them blind.')) return;
        self.busy = true;
        post('/bt/advisor_only', { sid: self.sess.sid }).then(function (j) { self.busy = false; if (j.ok === false) { self.err = j.error; return; } self.brk = null; self.load(); })
          .catch(function (e) { self.busy = false; self.err = String(e); });
      },
      aoRows: function (A) { return A && A.grade ? [['The advisor alone (its blind first plan)', A.grade]] : []; },
      nextBlock: function () {           // Test (coach 2026-10-06): start the next block of 20
        var self = this; self.busy = true;
        post('/bt/next_block', { sid: self.sess.sid }).then(function (j) { self.busy = false; if (j.ok === false) { self.err = j.error; return; } self.load(); })
          .catch(function (e) { self.busy = false; self.err = String(e); });
      },
      blockOf: function () { var b = this.C && this.C.block; return b ? ' · block ' + (Math.floor(this.C.i / b) + 1) + ' of ' + Math.ceil(this.C.n / b) : ''; },
      sizeOf: function (g) { return this.startMode === 'test' ? g.test : g.practice; },
      leave: function () { var self = this; this.stopPoll(); clearTimeout(this.aoTimer); this.sess = null; this.done = false; this.rev = null; this.summary = null; this.brk = null; location.hash = '';
        get('/api/bt/sessions').then(function (j) { self.sessions = j; }); this.$nextTick(function () { self.syncGroupSelect(0); }); },
      load: function () {
        var self = this; self.busy = true;
        self.stopPoll(); self.askText = ''; self.proceedNoAdv = false; self.rpPause(); self.rp = null; self.rpTally = null;
        get('/api/bt/card?sid=' + self.sess.sid).then(function (j) { self.busy = false;
          if (j.ok === false) { self.err = j.error; return; }
          if (j.done) { self.done = true; self.brk = null; self.C = { tally: j.tally, n: j.n, mode: j.mode }; self.summary = j.summary || null; self.exps = j.experiments || [];
            var A = self.summary && self.summary.advisor_only; clearTimeout(self.aoTimer);      // the advisor still answering the rest: refresh
            if (A && A.answered + A.failed < A.total) self.aoTimer = setTimeout(function () { if (self.sess && self.done) self.load(); }, 20000);
            return; }
          if (j.block_break) { self.brk = j; self.sess.group = j.group; self.C = { i: j.i, n: j.n, mode: j.mode, tally: j.tally }; self.rev = null; return; }   // Test: between blocks
          self.brk = null;
          self.sess.group = j.card ? (self.groups.filter(function (g) { return g.name === j.card.group; })[0] || {}).key : self.sess.group;
          self.C = j; self.rev = null; self.done = false; self.inverse = false;
          self.hist = []; self.histBase = []; self.hpos = -1; self.loadPreset('default');     // per-card history starts at the preloaded plan
          if (j.committed && j.committed.plan) self.fromConfig(j.committed.plan);           // Test, back after a reload: the committed plan
          self.hist = [self.snap()]; self.histBase = ['default']; self.hpos = 0; self.basePreset = 'default';
          if (j.committed) self.maybePoll();
          self.$nextTick(function () { self.chart(false); self.bindSort(); });
        });
      },
      chart: function (reveal) {
        var C0 = this.C.card, el = document.getElementById('bt-chart'); if (!C0 || !el) return;
        var C = C0, rs = this.sgn();
        if (this.inverse) {                     // the inverted trade: the same bars, the direction flipped, SL / TP1 / TP2 mirrored round the entry
          var L = C0.levels, neg = function (v) { return v === null || v === undefined ? v : -v; };
          C = Object.assign({}, C0, { direction: C0.direction === 'BUY' ? 'SELL' : 'BUY', levels: { entry: L.entry, sl: neg(L.sl), tp1: neg(L.tp1), tp2: neg(L.tp2) } }); rs = -rs;
        }
        var box = el.querySelector('.hc-box'); box.innerHTML = '';
        var h4 = C.h4.bars.slice(), ema = clone(C.h4.ema), marks = [], ov = C.overlay || null, keep = false;
        if (reveal && this.rev && this.rp) {      // the step-through: the bars up to the current hour (the last one still forming)
          var t0 = h4[h4.length - 1][0], P = this.rp, T = P.ticks, i = this.st.i, self = this, lab = {};
          if (i >= 0) {
            for (var k = 0; k < T[i].k; k++) { var fb = P.barFinal[k]; h4.push([t0 + 14400 * (k + 1), fb[0], fb[1], fb[2], fb[3], 0]); }
            var cb = T[i].bar; h4.push([t0 + 14400 * (T[i].k + 1), cb[0], cb[1], cb[2], cb[3], 0]);
          }
          ['20', '50', '200'].forEach(function (n) { var e = ema[n], kk = 2 / (+n + 1), p = e[e.length - 1];
            for (var q = C.h4.bars.length; q < h4.length; q++) { p = p + kk * (h4[q][4] - p); e.push(p); } });
          var put = function (q, txt) { var tt = t0 + 14400 * (T[q].k + 1); lab[tt] = lab[tt] ? lab[tt] + ' · ' + txt : txt; };
          if (P.plan) P.plan.events.forEach(function (e) { if (e.t <= i) e.text.forEach(function (x) { var s = self.rpShort(x); if (s) put(e.t, s); }); });
          if (P.plan && i >= P.plan.exit_t) put(P.plan.exit_t, 'exit ' + this.fmt(P.plan.R));
          if (i >= P.raw.exit_t && (!P.plan || P.raw.exit_t !== P.plan.exit_t || Math.abs(P.raw.R - P.plan.R) > 0.005)) put(P.raw.exit_t, 'raw ' + this.fmt(P.raw.R));
          Object.keys(lab).forEach(function (tt) { marks.push({ t: +tt, label: lab[tt] }); });
          var sp = P.plan && i >= 0 && i < P.plan.exit_t ? P.plan.steps[i].stops : [];
          if (sp.length) { ov = Object.assign({}, ov || {}); ov.aux = (ov.aux || []).concat(sp.map(function (v) { return { price: v, label: 'stop now', color: '#ff8c00', width: 2, style: 0 }; })); }
          keep = true;
        } else if (reveal && this.rev && this.rev.post_bars.length) {
          var t = h4[h4.length - 1][0];
          this.rev.post_bars.forEach(function (b, k) { h4.push([t + 14400 * (k + 1), b[0], b[1], b[2], b[3], 0]); });
          ['20', '50', '200'].forEach(function (n) { var e = ema[n], kk = 2 / (+n + 1), p = e[e.length - 1];   // continue the EMA through the revealed bars
            for (var i = C.h4.bars.length; i < h4.length; i++) { p = p + kk * (h4[i][4] - p); e.push(p); } });
          if (this.rev.plan_exit && this.rev.R_plan !== null) marks.push({ t: t + 14400 * (this.rev.plan_exit.bar + 1), label: 'exit ' + this.fmt(this.rev.R_plan) });
          if (this.rev.raw_exit && (!this.rev.plan_exit || this.rev.raw_exit.bar !== this.rev.plan_exit.bar))      // trader 2026-10-08: where raw really ended
            marks.push({ t: t + 14400 * (this.rev.raw_exit.bar + 1), label: 'raw ' + (this.rev.raw_exit.kind === 'TP2 hit' ? 'TP2 ' : (this.rev.raw_exit.kind === 'stopped out' ? 'stop ' : 'end ')) + this.fmt(this.rev.R_raw) });
        }
        HybridChart.mount(el, { blind: true, live: false, bars_h4: h4, bars_d1: C.d1.bars, ema_h4: ema, ema_d1: C.d1.ema, sig_h4: C.h4.sig, sig_d1: C.d1.sig,
                                levels: C.levels, strategy: C.detector, overlay: ov, marks: marks, rsign: rs, keep_range: keep });
      },
      decide: function (d) {
        var self = this; if (d === 'take' && !self.v.ok) return; self.busy = true;
        post('/bt/decide', { sid: self.sess.sid, i: self.C.i, decision: d, plan: JSON.stringify(self.toConfig()), loaded_preset: self.basePreset, inverse: self.inverse ? 1 : null }).then(function (j) {
          self.busy = false; if (j.ok === false) { self.err = j.error; return; }
          if (self.isTest()) {                              // Test: nothing revealed - record and move on
            self.stopPoll(); self.flash = 'Card ' + (self.C.i + 1) + ' recorded (' + d + ').'; setTimeout(function () { self.flash = ''; }, 2500);
            self.load(); window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
          self.rev = j; self.rpPause();
          if (j.timeline && j.timeline.ticks && j.timeline.ticks.length) {    // trader 2026-10-16: step through it - the result card waits for the end
            self.rp = self.rpPrep(j.timeline); self.rpTally = j.tally; self.st = Object.assign({}, self.st, { i: -1, play: false, done: false });
          } else { self.rp = null; self.C.tally = j.tally; }
          self.$nextTick(function () { self.chart(true); });
        }).catch(function (e) { self.busy = false; self.err = String(e); });
      },
      next: function () { this.load(); window.scrollTo({ top: 0, behavior: 'smooth' }); },
      // ---------------------------------------------------------------- the step-through (trader 2026-10-16: "step through it bar by bar or hour by hour")
      // one tick per trading hour on the frame the R comes from, then whole H4 bars past it; the plan's and the raw signal's state per tick
      // come from the server (live/backtest.py timeline: the rule engine run hour by hour). The result card and the header tally wait for the end.
      rpPrep: function (tl) { tl.barFinal = []; tl.ticks.forEach(function (t) { tl.barFinal[t.k] = t.bar; }); tl.N = tl.ticks.length; return tl; },
      rpGo: function (i) { if (!this.rp) return; i = Math.max(-1, Math.min(this.rp.N - 1, i)); this.st.i = i;
        if (i === this.rp.N - 1 && !this.st.done) { this.st.done = true; this.C.tally = this.rpTally; this.rpPause(); }
        var self = this; this.$nextTick(function () { self.chart(true); }); },
      rpStep: function (d) {
        var T = this.rp.ticks, i = this.st.i, N = this.rp.N, j;
        if (this.st.unit === 'hour') return this.rpGo(i + d);
        if (d > 0) { j = i + 1; while (j < N - 1 && T[j + 1].k === T[j].k) j++; return this.rpGo(j); }   // to the close of this bar (or the next)
        var k = i >= 0 ? T[i].k : -1; j = i; while (j >= 0 && T[j].k > k - 1) j--; this.rpGo(j);   // to the close of the bar before
      },
      rpEnd: function () { this.rpPause(); this.rpGo(this.rp.N - 1); },
      rpToggle: function () { if (this.st.play) return this.rpPause(); var self = this;
        if (this.st.i >= this.rp.N - 1) this.rpGo(-1);
        this.st.play = true; this.rpTimer = setInterval(function () { if (!self.rp || self.st.i >= self.rp.N - 1) return self.rpPause(); self.rpStep(1); },
                                                         (this.st.unit === 'hour' ? 450 : 900) / this.st.speed); },
      rpPause: function () { if (this.rpTimer) { clearInterval(this.rpTimer); this.rpTimer = null; } this.st.play = false; },
      rpUnit: function (u) { this.st.unit = u; if (this.st.play) { this.rpPause(); this.rpToggle(); } },
      rpSpeed: function (v) { this.st.speed = +v || 1; if (this.st.play) { this.rpPause(); this.rpToggle(); } },
      rpAt: function (t) { var T = this.rp.ticks[t]; return T ? (T.th !== null ? T.th + ' h' : 'bar ' + (T.k + 1)) : ''; },
      rpWhen: function () {
        var i = this.st.i; if (!this.rp) return ''; if (i < 0) return 'At the entry - the open of the first bar after the signal';
        var T = this.rp.ticks[i];
        return T.th !== null ? 'Bar ' + (T.k + 1) + ' · ' + T.th + ' trading hour' + (T.th === 1 ? '' : 's') + ' in · price ' + this.fmt(T.px)
                             : 'Bar ' + (T.k + 1) + ' · after the trade' + (this.rp.plan ? 's' : '') + ' ended';
      },
      rpLine: function (key) {
        var L = this.rp && this.rp[key], i = this.st.i; if (!L) return '';
        if (i < 0) return 'in at the signal';
        if (i >= L.exit_t) return 'closed · ' + L.kind + ' · ' + this.fmt(L.R) + ' (at ' + this.rpAt(L.exit_t) + ')';
        var s = L.steps[i];
        if (key === 'raw') return this.fmt(s.now) + ' if closed now · stop at ' + this.fmt(s.stop_R);
        return Math.round(s.open * 100) + '% open · ' + this.fmt(s.now) + ' if closed now' + (Math.abs(s.banked) > 0.0005 ? ' · banked ' + this.fmt(s.banked) : '') +
               ' · stop' + (s.stops.length > 1 ? 's from ' : ' at ') + this.fmt(s.stop_R) + ' (' + this.fmt(s.worst) + ' in all if hit)';
      },
      rpEvents: function () {
        var rp = this.rp, i = this.st.i, out = [], self = this; if (!rp) return out;
        [['plan', this.rev && this.rev.decision === 'take' ? 'Your plan' : 'Your plan (not taken)'], ['raw', this.rev && this.rev.inverse ? 'Raw inverted' : 'Raw']].forEach(function (p) {
          var L = rp[p[0]]; if (!L) return;
          L.events.forEach(function (e) { if (e.t <= i) e.text.forEach(function (x, n) { out.push({ id: p[0] + e.t + '_' + n, t: e.t, at: self.rpAt(e.t), who: p[1], text: x, now: e.t === i }); }); });
        });
        return out.sort(function (a, b) { return b.t - a.t || (a.who < b.who ? 1 : -1); });
      },
      rpShort: function (x) {          // the chart's marker label for an event
        if (x.indexOf('banked') === 0) return 'bank'; if (x.indexOf('reached: stop moved') > 0) return 'stop';   // no trail label: the stop line shows it
        if (x.indexOf('% more at') > 0) return 'add'; if (x.indexOf('risk cap') === 0) return 'risk cap';
        if (x.indexOf('skipped') > 0) return 'add skipped'; if (x.indexOf('stopped out') > 0) return 'stopped'; return '';
      },
      // ---------------------------------------------------------------- Test mode: commit -> advisor -> final
      isTest: function () { return this.C && this.C.mode === 'test'; },
      advState: function () { return (this.C && this.C.adv && this.C.adv.state) || 'none'; },
      advWorking: function () { var s = this.advState(); return s === 'queued' || s === 'running'; },
      finalLocked: function () { return this.isTest() && this.advWorking() && !this.proceedNoAdv; },   // review 2 #13a: "Proceed without it" is the gate
      commitFirst: function (d) {
        var self = this; if (d === 'take' && !self.v.ok) return; self.busy = true;
        post('/bt/commit', { sid: self.sess.sid, i: self.C.i, decision: d, plan: JSON.stringify(self.toConfig()) }).then(function (j) {
          self.busy = false; if (j.ok === false) { self.err = j.error; return; }
          self.C.committed = j.committed; self.C.advisor = j.advisor; self.C.advisor_v1 = j.advisor_v1; self.C.adv = j.adv; self.maybePoll();
        }).catch(function (e) { self.busy = false; self.err = String(e); });
      },
      maybePoll: function () {
        var self = this, a = self.C.adv || {};
        if (self.advWorking() || (a.pending && a.pending.length)) { if (!self.advTimer) self.advTimer = setInterval(function () { self.pollAdvisor(); }, 4000); }
        else self.stopPoll();
      },
      stopPoll: function () { if (this.advTimer) { clearInterval(this.advTimer); this.advTimer = null; } },
      pollAdvisor: function () {
        var self = this; if (!self.sess || !self.C || self.C.i === undefined) return self.stopPoll();
        get('/api/bt/advisor?sid=' + self.sess.sid + '&i=' + self.C.i).then(function (j) {
          if (j.ok === false) { self.stopPoll(); return; }
          if (j.i !== self.C.i) return;
          self.C.advisor = j.advisor; self.C.advisor_v1 = j.advisor_v1; self.C.adv = j.adv; self.maybePoll();
        }).catch(function () {});
      },
      askAdvisor: function () {
        var self = this, t = (self.askText || '').trim(); if (!t) return;
        post('/bt/ask', { sid: self.sess.sid, i: self.C.i, text: t }).then(function (j) {
          if (j.ok === false) { self.err = j.error; return; }
          self.askText = ''; self.C.adv = Object.assign({}, self.C.adv || {}, { pending: ((self.C.adv && self.C.adv.pending) || []).concat([t]) });
          self.maybePoll();
        });
      },
      gradeRows: function (g) {
        g = g || (this.summary && this.summary.grading); if (!g) return [];
        return [['Your first call (you alone)', g.trader_alone], ['The advisor alone (its blind first plan)', g.advisor_alone], ['Your final (together)', g.combined]];
      },
      brkTables: function () {        // trader 2026-10-08: at a Test checkpoint, the block just finished + the session so far (totals only)
        var g = this.brk && this.brk.grading; if (!g) return [];
        var bb = (g.by_block || []).filter(function (x) { return x.decided; }), last = bb[bb.length - 1], out = [];
        if (last) out.push({ title: 'Block ' + last.block + ' (cards ' + last.cards + ')', g: last });
        if (bb.length > 1) out.push({ title: 'So far (cards 1-' + this.brk.i + ')', g: g });
        return out;
      },
      byBlock: function (g) { return ((g && g.by_block) || []).filter(function (x) { return x.decided; }); },
      pct: function (v) { return (v === null || v === undefined) ? '-' : Math.round(v * 100) + '%'; },
      devInject: function () {
        var self = this;
        post('/bt/dev/advisor', { sid: self.sess.sid, text: self.devText, blind: self.devBlind ? '1' : '0' }).then(function (j) {
          if (j.ok === false) { self.err = j.error; return; }
          if (!j.valid) { self.err = 'Rejected: ' + j.reason; return; }
          get('/api/bt/card?sid=' + self.sess.sid).then(function (c) { self.C.advisor = c.advisor; });
        });
      }
    };
  };
})();
