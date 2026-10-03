r"""
monitor.py - Telegram notifier + watchdog in one loop (spec W9, O1, O2). Outbound only (S5): never reads Telegram.

Every interval it compares the queue root with a persisted seen-state and sends: new signal (deep link), signal
auto-skip/expiry/invalidation, task acks (fill / rejection), +1R bank, position closed with R, heartbeat stale
(then recovered), AutoTrading flags off, MT5 process absent (restart via the scheduled task, then alert), web app
down (restart), FTMO headroom below thresholds, calendar coverage < 14 d (daily), a daily summary, and a
"recovered" message when it starts within a few minutes of boot.
"""
from __future__ import annotations
import csv, glob, json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C

class Monitor:
    def __init__(self, cfg: dict, dry: bool = False):
        self.cfg = cfg; self.dry = dry; self.log = C.Log("monitor", cfg["logs_dir"]); self.m = cfg["monitor"]
        self.state_path = os.path.join(cfg["root"], "monitor", "state.json")
        self.st = C.load_json(self.state_path, None) or {"signals": {}, "acks": [], "positions": {}, "hb_stale": {}, "flags_off": {}, "headroom": {}, "last_summary": "", "last_calendar_warn": "", "mt5_restarts": []}
        self.base = cfg["web"]["base_url"].rstrip("/")

    def save(self): C.atomic_write_json(self.state_path, self.st)

    def send(self, text: str) -> None:
        self.log("TG: " + text.replace("\n", " | ")[:300])
        if not self.dry: C.telegram_send(self.cfg, text, self.log)

    # ------------------------------------------------------------------ checks
    def signals(self):
        seen = self.st["signals"]
        for s in C.list_signals(self.cfg):
            key = s["signal_key"]; stt = s.get("status"); prev = seen.get(key)
            lv = s.get("levels") or {}; rr = s.get("rr") or {}
            # re-present after a delay (bar close or explicit delay): remind the trader to revisit
            dc = int(s.get("delay_count") or 0); pdc = int(self.st.setdefault("delays", {}).get(key, 0) or 0)
            if stt == "open" and prev == "open" and dc > pdc:
                dl = C.parse_iso(s.get("deadline")); left = max(0, int(s.get("max_age_bars") or 3) - int(s.get("implicit_streak") or 0))
                take_note = " TAKE-class: expiring is journaled but not charged (§11.9b, 2026-09-21); a skip is legal only for event (2) or correlation (5)." if s.get("decision_class") == "TAKE" else ""
                self.send(f"REVISIT #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']} [{s.get('decision_class', '')}]: re-presented at bar close (delay {dc}); {left} bar(s) of silence left before it expires, deadline {C.fmt_dt(dl)}.{take_note} "
                          f"Price {'bid ' + str((s.get('sizing') or {}).get('mkt_bid')) if (s.get('sizing') or {}).get('mkt_bid') else ''}entry {lv.get('entry')} SL {lv.get('sl')}.\n{self.base}/signal/{key}")
            self.st["delays"][key] = dc
            if prev == stt: continue
            if prev is None and stt == "open":
                dl = C.parse_iso(s.get("deadline"))
                self.send(f"{'RE-OFFERED' if s.get('reoffer') else 'NEW'} SIGNAL #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']} [{s.get('decision_class')}]\n"
                          + (C.reoffer_line(s, self.cfg) + "\n" if s.get("reoffer") else "") +
                          f"entry {lv.get('entry')} SL {lv.get('sl')} TP1 {lv.get('tp1')} ({rr.get('tp1')}R) · {(s.get('sizing') or {}).get('lots')} lots\n"
                          f"{(s.get('regime') or {}).get('pretty', '')} · deadline {C.fmt_dt(dl)} ({C.rel_time(dl)})\n{self.base}/signal/{key}")
            elif prev == "open" and stt in ("expired", "rejected", "auto_skipped"):
                ar = str(s.get("auto_reason", ""))
                why = {"expired": "no decision before the deadline (skip code 8)", "rejected": "invalidated (price through SL while parked)",
                       "auto_skipped": ("EA auto-reject (code 10): FTMO headroom - " + ar.replace("EA_AUTO_REJECT ", "") + " - not a trader decision" if ar.startswith("EA_AUTO_REJECT")
                                        else "election gate: " + ar)}[stt]
                if stt == "expired" and s.get("decision_class") == "TAKE": why += " — TAKE-class: journaled, NOT charged (§11.9b amended 2026-09-21); it goes in the monthly export with its blind outcome"
                self.send(f"signal #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']} [{s.get('decision_class', '')}]: {stt} — {why}")
            elif prev == "open" and stt == "skipped" and str(s.get("auto_reason", "")).startswith("superseded"):
                self.send(f"signal #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']}: auto-skipped (code 9) - {s.get('auto_reason')}")
            elif prev == "open" and stt in ("approved", "approved_pending", "skipped"):
                pass  # the ack message covers it
            if prev == "approved_pending" and stt == "rejected":
                self.send(f"PENDING ORDER INVALIDATED: #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']} - price traded through the SL {lv.get('sl')} before the order at {lv.get('entry')} filled; the EA deleted it.")
            if stt == "approved_pending":
                row = C.row_state(self.cfg, s["symbol"], s["signal_id"]) or {}
                tag = f"pend-exp:{key}"
                if row.get("decision") == "expired" and tag not in self.st["acks"]:
                    self.st["acks"].append(tag)
                    self.send(f"PENDING ORDER EXPIRED unfilled: #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']} at {lv.get('entry')} - the EA cancelled it after {C.PENDING_EXPIRY_BARS} H4 bars.")
            elif prev is None and stt != "open":
                pass  # decided before the monitor started
            seen[key] = stt
        # advisor failures
        for p in glob.glob(os.path.join(self.cfg["root"], "advisor", "verdicts", "*.json")):
            rec = C.load_json(p)
            if not rec or not rec.get("consults"): continue
            last = rec["consults"][-1]; mid = rec.get("model_id") or "advisor"; tag = f"adv-fail:{rec['signal_key']}:{mid}:{last.get('ts')}"
            if last.get("interrupted"): continue                     # restart artefact: the runner re-runs it by itself
            if not last.get("ok") and tag not in self.st["acks"] and C.parse_iso(last.get("ts")) and C.now_utc() - C.parse_iso(last["ts"]) < timedelta(hours=6):
                self.st["acks"].append(tag)
                if last.get("rate_limited"):
                    n = self._rate_limits_today()
                    self.send(f"ADVISOR RATE-LIMITED: {mid} on {rec['signal_key']} - no verdict from it; the other advisor's verdict stands. "
                              f"{n} rate-limit error(s) today. Opus-high on every signal is real subscription load - see the dashboard's advisor counts.")
                elif last.get("timeout"): self.send(f"advisor {mid} TIMED OUT on {rec['signal_key']} ({str(last.get('error'))[:80]}) - the other advisor's verdict stands")
                else: self.send(f"advisor {mid} consult FAILED for {rec['signal_key']}: {str(last.get('error'))[:200]}")

    def acks(self):
        seen = set(self.st["acks"])
        for p in sorted(glob.glob(os.path.join(self.cfg["root"], "acks", "*.json")), key=os.path.getmtime):
            a = C.load_json(p)
            if not a or a["task_id"] in seen: continue
            ex = C.parse_iso(a.get("executed_at"))
            if ex and C.now_utc() - ex > timedelta(hours=12): seen.add(a["task_id"]); continue   # history before the monitor existed
            seen.add(a["task_id"]); refs = a.get("refs") or {}; tgt = f"#{a.get('signal_id')}" if a.get("signal_id") else f"pos {a.get('position_id')}"
            if a.get("result") == "accepted":
                if a.get("verb") == "approve":
                    kind = "PENDING ORDER placed" if refs.get("entry_mode", "").startswith("pending") else "FILLED"
                    self.send(f"{kind}: {tgt} {a['symbol']} {refs.get('lots')} lots · posid {refs.get('posid')} ticket {refs.get('order_ticket')} · SL {refs.get('sl')} TP {refs.get('tp')}")
                elif a.get("verb") == "cancel_pending":
                    self.send(f"PENDING ORDER CANCELLED by you: #{a.get('signal_id')} {a['symbol']} (order ticket {refs.get('order_ticket')})")
                elif a.get("verb") == "test_signal":
                    self.send(f"TEST signal published on {a['symbol']} (#{refs.get('signal_id')}) - approve or skip it: {self.base}/signal/{a['symbol']}-{refs.get('signal_id')}")
                elif a.get("verb") in ("close", "close50", "sl_be", "ratchet_tp1", "skip", "delay"):
                    self.send(f"{a['verb']} accepted: {tgt} {a['symbol']}")
            else:
                self.send(f"TASK {a.get('result', '').upper()}: {a.get('verb')} {tgt} {a['symbol']} — {C.reason_text(a.get('reason'))}")
        self.st["acks"] = sorted(seen)[-2000:]

    def positions(self):
        seen = self.st["positions"]
        for p in C.list_positions(self.cfg):
            k = f"{p['symbol']}-{p['posid']}"; prev = seen.get(k) or {}
            if not prev and self.st.get("positions_initialised") and int(p.get("tranche") or 0) == 2:
                self.send(f"STAGED ADD: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · {p.get('lots_live')} lots added at {p.get('entry')} "
                          f"(bar-{self.cfg.get('staged_add_bar', 6)} close) · SL {p.get('sl_live')}\n{self.base}/position/{k}")
            elif not prev and self.st.get("positions_initialised") and int(p.get("tranche") or 0) == 4:
                self.send(f"IMPROVING ADD (day 2): pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · {p.get('lots_live')} lots at {p.get('entry')} "
                          f"· shares the stop {p.get('sl_live')} · target TP2\n{self.base}/position/{k}")
            elif not prev and self.st.get("positions_initialised") and int(p.get("tranche") or 0) == 3:
                stp = (f"stop {p.get('sl_live')} (+R stop raise - shorts)" if int(p.get("short_raise") or 0) == 1 else f"stop at entry {p.get('sl_live')}")
                self.send(f"PYRAMID ADD (+1.5R): pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · {p.get('lots_live')} lots at {p.get('entry')} "
                          f"· {stp} · target TP2\n{self.base}/position/{k}")
            elif not prev and self.st.get("positions_initialised") and p.get("strategy") == "Inverse":
                self.send(f"INVERSE FILLED: pos {p['posid']} {p['symbol']} {p['direction']} at {p.get('entry')} · {p.get('lots_live')} lots · "
                          f"SL {p.get('sl_live')} (the parent was stopped)\n{self.base}/position/{k}")
            elif not prev and self.st.get("positions_initialised"):
                self.send(f"FILLED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} at {p.get('entry')} · {p.get('lots_live')} lots · SL {p.get('sl_live')} (pending order or market fill)\n{self.base}/position/{k}")
            if p.get("banked") and not prev.get("banked"):
                li, ll = float(p.get("lots_init") or 0), float(p.get("lots_live") or 0)
                if int(p.get("tranche") or 0) == 3:
                    pass                                                   # a pyramid add is born banked: nothing to announce
                elif li > 0 and ll >= li - 1e-9:
                    self.send(f"+1R REACHED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · stop moved to entry ({p.get('sl_live')}). "
                              f"Nothing banked: {li:g} lots is too small to split off 25%.")
                else:
                    share = (li - ll) / li if li else 0
                    self.send(f"+1R BANKED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · closed {li - ll:.2f} of {li:g} lots "
                              f"({share:.0%}) at +1R = {C.r_fmt(p.get('banked_r'))} on this position · {ll:g} lots run · stop now {p.get('sl_live')} (entry)")
            if int(p.get("staged_state") or 0) == 4 and int(prev.get("staged") or 0) != 4 and self.st.get("positions_initialised"):
                self.send(f"ADD HELD: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · the trial averaged below 0 to the bar-6 close, so the "
                          f"75% add waits for +1R. {p.get('staged_note') or ''}\n{self.base}/position/{k}")
            if p.get("ratcheted") and not prev.get("ratcheted"): self.send(f"SL ratcheted to TP1: pos {p['posid']} {p['symbol']}")
            sr, sk = int(p.get("short_raise") or 0), f"{p['symbol']}-{p.get('signal_id')}"          # coach item 25: once per signal
            if sr in (1, 2) and sk not in self.st.setdefault("short_raise_sent", []) and self.st.get("positions_initialised"):
                self.st["short_raise_sent"] = (self.st["short_raise_sent"] + [sk])[-500:]
                if sr == 1:
                    self.send(f"SHORT STOP RAISED: {p['symbol']} {p['strategy']} SELL signal #{p.get('signal_id')} reached +1.5R · the stop on every "
                              f"ticket of the signal moved to {p.get('short_raise_stop')} (+0.25R locked in on the runner). Shorts-only rule, "
                              f"scored against the stop left at entry (web/short_raise.csv).\n{self.base}/position/{k}")
                else:
                    self.send(f"SHORT STOP RAISE REFUSED: {p['symbol']} {p['strategy']} SELL signal #{p.get('signal_id')} reached +1.5R but the "
                              f"stop could not be moved (see the EA audit log) - the stop is still at entry.\n{self.base}/position/{k}")
            if int(p.get("staged_state") or 0) == 3 and prev and prev.get("staged", 0) != 3:
                self.send(f"STAGED ADD SKIPPED: pos {p['posid']} {p['symbol']} {p['strategy']} - {p.get('staged_note')}")
            xe = p.get("last_external_edit") or ""
            if xe and xe != prev.get("xedit", "") and prev:          # an SL/TP moved in the trader's own MT5 (journaled EXTERNAL_*)
                self.send(f"EDITED IN MT5 (outside the app): pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · {xe.split(' ', 1)[-1]} · open {C.r_fmt(p.get('open_r'))} - journaled for the coach")
            seen[k] = {"banked": bool(p.get("banked")), "ratcheted": bool(p.get("ratcheted")), "open": True, "xedit": xe,
                       "staged": int(p.get("staged_state") or 0)}
        self.st["positions_initialised"] = True
        for p in C.list_positions(self.cfg, closed=True):
            k = f"{p['symbol']}-{p['posid']}"; prev = seen.get(k) or {}
            if prev.get("open") or (k not in seen and C.parse_iso(p.get("ts")) and C.now_utc() - C.parse_iso(p["ts"]) < timedelta(hours=12)):
                tot = (p.get("banked_r") or 0) + (p.get("closenow_r") or 0)
                tr, sst = int(p.get("tranche") or 0), int(p.get("staged_state") or 0)
                full, li = float(p.get("full_lots") or 0), float(p.get("lots_init") or 0)
                if tr == 1 and sst != 2 and full > 0 and li > 0:
                    # trial phase: only the first tranche was on, so the loss/gain is a fraction of a full position
                    share = li / full
                    head = (f"CLOSED IN TRIAL: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · {C.r_fmt(tot * share)} "
                            f"(trial size: {li:g} of {full:g} lots = {share:.0%} of the position)")
                elif tr in (1, 2) and full > 0 and li > 0:
                    head = (f"CLOSED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · {C.r_fmt(tot * li / full)} "
                            f"(tranche {tr} of 2: {li:g} of {full:g} lots)")
                elif tr == 4:
                    head = f"CLOSED: improving add pos {p['posid']} {p['symbol']} {p['direction']} · {C.r_fmt(tot)} on the add ({li:g} lots)"
                elif tr == 3:
                    head = f"CLOSED: pyramid add pos {p['posid']} {p['symbol']} {p['direction']} · {C.r_fmt(tot)} on the add ({li:g} lots)"
                else:
                    head = f"CLOSED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · total {C.r_fmt(tot)} (banked {C.r_fmt(p.get('banked_r'))})"
                self.send(f"{head} after {p.get('bars_open')} bars\n{self.base}/position/{k}")
            seen[k] = {"banked": bool(p.get("banked")), "ratcheted": bool(p.get("ratcheted")), "open": False}
        # coach queue B: the EA's day-2 short cut (actions file DAY2_CUT), one message per signal
        sent = self.st.setdefault("day2_cut_sent", [])
        for path in glob.glob(os.path.join(self.cfg["root"], "journal", "*.actions.csv")):
            try:
                with open(path, encoding="ascii", errors="replace", newline="") as fh:
                    for a in csv.DictReader(fh):
                        if a.get("action") != "DAY2_CUT": continue
                        sym = os.path.basename(path).split("_")[0]; key = f"{sym}-{a.get('signal_id')}"
                        if key in sent: continue
                        sent.append(key)
                        if self.st.get("positions_initialised"):
                            self.send(f"DAY-2 SHORT CUT: {sym} signal #{a.get('signal_id')} · the short averaged at or below +0.5R over its first 48h, "
                                      f"so the EA closed it at {a.get('price')} (open {a.get('open_r')}R).")
            except OSError: pass
        self.st["day2_cut_sent"] = sent[-500:]

    def heartbeats(self):
        """instance health, trader ruling 2026-09-22: no per-symbol noise. A symbol is DOWN only after `down_after_s` (300 s =
        five missed beats) of silence - a single missed read is the monitor opening the file while the EA atomically replaces
        it. One aggregated message when instances go down (at most every 30 min while the set grows), one 'all back' message
        only if a down alert went out. AutoTrading and drawdown are ACCOUNT-level: evaluated once, not per instance."""
        stale_s = self.m["heartbeat_stale_s"]; down_after = float(self.m.get("down_after_s", 300)); now = C.now_utc()
        first = self.st.setdefault("hb_first_stale", {}); alerted = set(self.st.setdefault("hb_down_alerted", []))
        syms = C.symbols(self.cfg); down = []; hbs = {}
        for sym in syms:
            hb = C.heartbeat(self.cfg, sym); age = C.heartbeat_age_s(hb); hbs[sym] = hb
            stale = hb is None or age is None or age > stale_s or hb.get("status") != "running"
            if not stale: first.pop(sym, None); continue
            t0 = C.parse_iso(first.get(sym)) if first.get(sym) else None
            if t0 is None: first[sym] = C.now_iso(); continue
            if (now - t0).total_seconds() >= down_after: down.append(sym)
        new_down = [x for x in down if x not in alerted]
        last = C.parse_iso(self.st.get("hb_last_down_msg"))
        if new_down and (not alerted or not last or (now - last).total_seconds() >= 1800):
            self.send(f"EA INSTANCES DOWN ({len(down)}/{len(syms)}, silent 5+ min): {', '.join(sorted(down))}" + (" - the whole terminal looks down" if len(down) == len(syms) else ""))
            self.st["hb_last_down_msg"] = C.now_iso(); alerted |= set(down)
        if alerted and not down:
            self.send(f"all {len(syms)} EA instances beating again")
            alerted = set()
        self.st["hb_down_alerted"] = sorted(alerted & set(down)) if down else sorted(alerted)
        self.st["hb_stale"] = {x: (x in down) for x in syms}                       # kept for any reader of the old key
        # AutoTrading: one message listing every instance that is disarmed (a terminal-wide disarm = one line, not 47)
        off = sorted(x for x, h in hbs.items() if h and not (h.get("terminal_trade_allowed") and h.get("mql_trade_allowed")))
        prev_off = set(k for k, v in self.st["flags_off"].items() if v)
        if set(off) - prev_off: self.send(f"AUTOTRADING DISARMED on {len(off)}/{len(syms)}: {', '.join(off)} - approvals will be refused")
        elif prev_off and not off: self.send("AutoTrading armed again on every instance")
        self.st["flags_off"] = {x: (x in off) for x in syms}
        # coach 2026-09-23: a blank regime tag is a quiet outage (no TrendCont, no TAKE class) - surface it once, aggregated
        blank = sorted(x for x, h in hbs.items() if h and h.get("regime_ready") is False)
        prev_blank = set(self.st.get("regime_blank") or [])
        if set(blank) - prev_blank:
            self.send(f"REGIME BLANK on {len(blank)}/{len(syms)}: {', '.join(blank)} - D1 history short (need 211 bars), so TrendCont sees no trend and every signal there drops out of the TAKE class. The EA is pulling D1 history; this should clear by itself.")
        elif prev_blank and not blank:
            self.send("regime tagged again on every instance (D1 history complete)")
        self.st["regime_blank"] = blank
        # drawdown: the account, once (every instance reports the same equity/headroom)
        hb = next((h for h in sorted((h for h in hbs.values() if h and (h.get("ftmo") or {}).get("initial_balance")),
                                     key=lambda h: C.parse_iso(h.get("ts")) or now, reverse=True)), None)
        if hb:
            f = hb["ftmo"]; ib = f.get("initial_balance") or 0
            # One warning per band, on the way DOWN only. Headroom is equity minus the open risk-to-stop, so it ticks
            # with every quote: sitting a few dollars either side of a band re-fired the alert every poll (2026-09-24,
            # six alerts in four minutes across the 1% line). A band can only warn again once headroom has recovered
            # REARM above it.
            REARM = 1.15
            agg = float(hb.get("aggregate_risk_to_stop") or 0)
            for name, hd in (("daily", f.get("headroom_daily")), ("max", f.get("headroom_max"))):
                if hd is None: continue
                key = f"account:{name}"
                worst = self.st["headroom"].get(key)          # tightest band warned at since the last recovery
                lvl = next((t for t in sorted(self.m["headroom_warn_pct"]) if hd < t * ib), None)
                if lvl is not None and (worst is None or lvl < worst):
                    self.st["headroom"][key] = lvl
                    self.send(f"DRAWDOWN WARNING: {name}-loss headroom {hd:,.0f} < {lvl * 100:.0f}% of initial "
                              f"({ib:,.0f}) · equity {hb.get('equity')} · this already deducts {agg:,.0f} of open "
                              f"risk-to-stop, so it is what you would have left if every open stop were hit.")
                elif worst is not None and hd > worst * ib * REARM:
                    self.st["headroom"][key] = None
                    self.send(f"drawdown headroom recovered: {name}-loss headroom back to {hd:,.0f} "
                              f"({hd / ib * 100:.1f}% of initial) - warnings re-armed")

    def processes(self):
        if os.name != "nt": return
        try: out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {self.m['mt5_process']}.exe", "/FI", "SESSION ne 0"], capture_output=True, text=True, timeout=20).stdout
        except Exception as e: self.log(f"tasklist failed: {e}"); return
        # a terminal64 in session 0 is a stray copy launched by the MetaTrader5 package (never the trading terminal): kill it
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq 0 } | ForEach-Object { Stop-Process -Id $_.Id -Force; 'killed stray ' + $_.Id }"], capture_output=True, text=True, timeout=30)
        except Exception: pass
        if self.m["mt5_process"].lower() not in out.lower() or "console" not in out.lower():
            recent = [t for t in self.st["mt5_restarts"] if C.parse_iso(t) and C.now_utc() - C.parse_iso(t) < timedelta(minutes=30)]
            if len(recent) >= 3: 
                if len(recent) == 3: self.send("MT5 PROCESS ABSENT and 3 restarts in 30 min failed — manual intervention needed"); self.st["mt5_restarts"].append(C.now_iso())
                return
            self.log("terminal64 absent -> Start-ScheduledTask " + self.m["mt5_task"])
            subprocess.run(["schtasks", "/Run", "/TN", self.m["mt5_task"]], capture_output=True, timeout=20)
            self.st["mt5_restarts"] = recent + [C.now_iso()]
            self.send(f"MT5 PROCESS ABSENT — restarted via task {self.m['mt5_task']} (attempt {len(recent)+1})")
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=10) as r: ok = r.status == 200
        except Exception: ok = False
        if not ok:
            self.st["web_fail"] = self.st.get("web_fail", 0) + 1
            if self.st["web_fail"] >= 4:      # four consecutive misses (~60 s); a real restart (stop, then start)
                subprocess.run(["schtasks", "/End", "/TN", self.m["web_task"]], capture_output=True, timeout=20)
                time.sleep(2)
                subprocess.run(["schtasks", "/Run", "/TN", self.m["web_task"]], capture_output=True, timeout=20)
                if not self.st.get("web_down"): self.send(f"WEB APP DOWN — restarted via task {self.m['web_task']}")
                self.st["web_down"] = True
        else:
            self.st["web_fail"] = 0
            if self.st.get("web_down"): self.st["web_down"] = False; self.send("web app back up")

    def calendar(self):
        """daily ForexFactory refresh (coach ruling 2026-09-16) + staleness watchdog (>7 d) + coverage warning."""
        now = C.now_utc(); today = now.strftime("%Y-%m-%d"); cal = self.cfg.get("calendar") or {}
        p = os.path.join(self.cfg["common_files"], "econ_events.csv")
        try: age_d = (time.time() - os.path.getmtime(p)) / 86400
        except OSError: age_d = 999
        hh, mm = (cal.get("refresh_utc") or "02:30").split(":")
        due = now.strftime("%H:%M") >= f"{hh}:{mm}" and self.st.get("last_calendar_refresh") != today
        if due or (age_d > 1.5 and self.st.get("last_calendar_refresh") != today):
            self.st["last_calendar_refresh"] = today
            try:
                from live.calendar_refresh import Refresher
                r = Refresher(self.cfg, self.log).run(); self.log("calendar refresh: " + json.dumps(r)[:600])
                if r.get("ok"): self.st["calendar_fail"] = 0; self.send(f"calendar refreshed: {r['rows']} rows, coverage to {r['last_utc']} UTC, {r['forward_V']} V-class in the forward window")
                else: self.st["calendar_fail"] = self.st.get("calendar_fail", 0) + 1; self.send(f"CALENDAR REFRESH FAILED: {r.get('error')} (deployed file untouched, {age_d:.1f} d old)")
            except Exception as e:
                self.send(f"CALENDAR REFRESH CRASHED: {e!r}")
        if age_d > cal.get("stale_days", 7) and self.st.get("last_stale_warn") != today:
            self.st["last_stale_warn"] = today; self.send(f"CALENDAR STALE: econ_events.csv is {age_d:.1f} days old (> {cal.get('stale_days', 7)} d) — the feed refresh is not landing")
        if self.st["last_calendar_warn"] == today: return
        _, cov = C.load_events(self.cfg)
        if not cov or cov < now + timedelta(days=self.m["calendar_min_days"]):
            self.send(f"CALENDAR COVERAGE: econ_events.csv ends {cov.strftime('%Y-%m-%d') if cov else 'never (file missing)'} — under {self.m['calendar_min_days']} d ahead. The election gate cannot see beyond it.")
            self.st["last_calendar_warn"] = today

    def summary(self):
        now = C.now_utc(); hh, mm = self.m["daily_summary_utc"].split(":")
        if now.strftime("%H:%M") < f"{hh}:{mm}" or self.st["last_summary"] == now.strftime("%Y-%m-%d"): return
        self.st["last_summary"] = now.strftime("%Y-%m-%d")
        rows = C.journal_rows(self.cfg, months=2); day = now.strftime("%Y.%m.%d")
        today_rows = [r for r in rows if (r.get("signal_time") or "").startswith(day)]
        closed = [float(r["r_multiple"]) for r in rows if r.get("r_multiple") and (r.get("exit_time") or "").startswith(day)]
        parts = [f"DAILY {now.strftime('%a %d %b')}: signals {len(today_rows)} (" + ", ".join(f"{d}:{sum(1 for r in today_rows if r.get('decision') == d)}" for d in ("approved", "approved_pending", "skipped", "rejected")) + ")",
                 f"closed today: {len(closed)} · {sum(closed):+.2f}R" if closed else "closed today: none"]
        # one account line (every instance reports the same account) + instance health: ~40 charts after the expansion
        syms = C.symbols(self.cfg); hbs = [C.heartbeat(self.cfg, x) or {} for x in syms]
        acct = next((h for h in hbs if (h.get("ftmo") or {}).get("initial_balance")), hbs[0] if hbs else {}); f = acct.get("ftmo") or {}
        alive = sum(1 for h in hbs if h.get("status") == "running" and (C.heartbeat_age_s(h) or 1e9) < self.cfg["monitor"]["heartbeat_stale_s"])
        parts.append(f"account: equity {acct.get('equity')} · daily headroom {f.get('headroom_daily')} · max headroom {f.get('headroom_max')} · aggregate risk-to-stop {acct.get('aggregate_risk_to_stop')}")
        parts.append(f"instances alive {alive}/{len(syms)} · open positions {len(C.list_positions(self.cfg))}")
        try:
            from live import brief_runner; b = brief_runner.latest_brief(self.cfg, 2.0)
            parts.append("context brief available (" + C.rel_time(b["t"]) + ")" if b else "no context brief in the last 2 days - generate one from the Context page if you want the advisor to have it")
        except Exception: pass
        parts.append(f"{self.base}/")
        self.send("\n".join(parts))

    def _rate_limits_today(self) -> int:
        day = C.now_utc().strftime("%Y-%m-%d"); n = 0
        try:
            with open(os.path.join(self.cfg["root"], "advisor", "consults.log"), encoding="utf-8", errors="replace") as f:
                for ln in f:
                    x = ln.split("|")
                    if len(x) > 6 and x[0].startswith(day) and x[6] == "rate_limited": n += 1
        except OSError: pass
        return n

    def opus_fallback(self):
        """coach 2026-09-21 (pre-approved): flip Opus to step A, then B, when the trigger fires; never back automatically."""
        from live import advisor_runner as AR
        fb = self.cfg["advisor"].get("opus_fallback") or {}; mid = fb.get("model_id", "opus-high")
        if not AR.model_cfg(self.cfg, mid): return
        pol = AR.opus_policy(self.cfg); step = pol.get("step", "none")
        if step == "B": return
        since = C.parse_iso(pol.get("since")) if step == "A" else None           # step B needs strain AFTER step A began
        now = C.now_utc(); week = now - timedelta(days=7)
        rl_days = set(); per_day: dict = {}
        try:
            with open(os.path.join(self.cfg["root"], "advisor", "consults.log"), encoding="utf-8", errors="replace") as f:
                for ln in f:
                    x = ln.rstrip("\n").split("|")
                    if len(x) < 7 or x[1] != mid or x[3] != "verdict": continue
                    t = C.parse_iso(x[0])
                    if not t or t < week or (since and t < since): continue
                    if x[6] == "rate_limited": rl_days.add(x[0][:10])
                    # A TIMEOUT is a latency problem, not subscription strain - this ladder exists for the latter, and
                    # counting timeouts escalated it twice on a single slow consult (2026-09-22 and 2026-09-28, when the
                    # cap was 200 s against a 176 s mean). Only availability failures count.
                    avail = (x[4] == "ok") or x[6] == "timeout"
                    per_day.setdefault(x[0][:10], {})[x[2]] = avail                  # last attempt per signal wins
        except OSError: return
        # ...and a percentage needs a denominator: 1 of 3 is not a trend. MIN_DAY signals before a day can trigger.
        MIN_DAY = 8
        bad_days = {d: (sum(1 for ok in v.values() if not ok), len(v)) for d, v in per_day.items()
                    if len(v) >= MIN_DAY and sum(1 for ok in v.values() if not ok) / len(v) > 0.20}
        reason = None
        if len(rl_days) >= 2: reason = f"rate-limit errors on {len(rl_days)} days in the last 7 ({', '.join(sorted(rl_days))})"
        elif bad_days:
            d, (nb, nt) = sorted(bad_days.items())[-1]; reason = f"Opus unavailable on {nb}/{nt} signals ({nb / nt:.0%}) on {d}"
        if not reason: return
        new_step = "A" if step == "none" else "B"
        pol["history"] = (pol.get("history") or []) + [{"step": new_step, "since": C.now_iso(), "reason": reason}]
        pol.update(step=new_step, since=C.now_iso(), reason=reason)
        C.atomic_write_json(os.path.join(self.cfg["root"], "advisor", "opus_policy.json"), pol)
        what = "no Opus on TAKE-class signals" if new_step == "A" else "Opus on the 8 tested symbols only (and still not on TAKE-class)"
        self.log(f"opus fallback -> step {new_step}: {reason}")
        self.send(f"OPUS FALLBACK step {new_step} is now active ({what}) - trigger: {reason}. Pre-approved by the coach; logged in advisor/opus_policy.json with the start time.")

    def eligibility(self):
        from live import eligibility as ELIG
        ELIG.refresh(self.cfg, 25, self.log)          # prices newly closed positions from the broker deals (cached)

    # Coach 2026-09-24 lessons entry: two calibration asymmetries go back under review after 100 more TrendCont
    # decisions. Counting them by hand is how a watch item quietly expires, so the monitor counts and flags once.
    TC_WATCH_N = 100
    TC_WATCH_FROM = "2026-09-24"

    def trendcont_watch(self):
        p = os.path.join(self.cfg["root"], "advisor", "trendcont_watch.json")
        st = C.load_json(p) or {"count": 0, "flagged": False}
        n = sum(1 for r in C.journal_rows(self.cfg)
                if r.get("strategy") == "TrendCont" and (r.get("signal_time") or "")[:10].replace(".", "-") >= self.TC_WATCH_FROM
                and (r.get("decision", "").startswith("approved")
                     or (r.get("decision") == "skipped" and r.get("skip_reason") in ("1", "2", "3", "4", "5", "6"))))
        if n != st.get("count"): st["count"] = n; C.atomic_write_json(p, st)
        if n >= self.TC_WATCH_N and not st.get("flagged"):
            st["flagged"] = True; C.atomic_write_json(p, st)
            self.send(f"COACH WATCH ITEM DUE: {n} TrendCont decisions since {self.TC_WATCH_FROM}. Report all three together:\n"
                      "1) TREND_DOWN over-taking - 57% taken vs 25% in TREND_UP, and the only negative selection gap (-0.19R).\n"
                      "2) US100 under-taking - 20% taken, where his picks returned +0.84R against -0.18R for his passes.\n"
                      "3) PRE-REGISTERED out-of-sample test of the D1-extension driver: the >1.5 ATR bucket's take-minus-skip "
                      "gap must exceed the <=1.5 bucket's by >= +0.30R for it to become a rule.\n"
                      "Re-run pipeline/trader_selection_study.py and pipeline/trader_driver_study.py with the live rows added.")

    # Coach 2026-09-24: the opinion layer is now the thing being graded, so count it live. One line per TrendCont
    # verdict since the deploy, its OPINION verdict joined to the journal's outcome; flag once at n=50.
    OPINION_N = 50
    OPINION_FROM = "2026-09-30T20:22"                       # coach 2026-09-30: restarts at the D1-withdrawal redeploy (UTC)

    def opinion_watch(self):
        p = os.path.join(self.cfg["root"], "advisor", "opinion_watch.json")
        st = C.load_json(p) or {"n": 0, "flagged": False}
        lg = os.path.join(self.cfg["advisor"]["live_dir"], "verdicts.live.log")
        ops: dict[tuple[str, str], str] = {}
        try:
            with open(lg, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    parts = [x.strip() for x in ln.split("|")]
                    if len(parts) < 8 or parts[0][:16] < self.OPINION_FROM: continue
                    if "TrendCont" not in parts[4] or "reply" in ln: continue
                    op = next((x[8:] for x in parts if x.startswith("opinion:")), "")
                    w = op.split("(")[0].strip()
                    if w and w != "-": ops[(parts[2], parts[3].lstrip("#"))] = w
        except OSError: return
        rows = {(r.get("symbol"), r.get("signal_id")): r for r in C.journal_rows(self.cfg)}
        paired = []
        for (sym, sid), w in ops.items():
            r = rows.get((sym, sid))
            try:
                if r and r.get("closed") != "0" and r.get("r_multiple"): paired.append((w, float(r["r_multiple"])))
            except ValueError: pass
        n = len(ops)
        if n != st.get("n") or len(paired) != st.get("paired"):
            st["n"] = n; st["paired"] = len(paired); C.atomic_write_json(p, st)
        if n >= self.OPINION_N and not st.get("flagged"):
            st["flagged"] = True; C.atomic_write_json(p, st)
            tk = [r for w, r in paired if w.startswith("TAKE")]; sk = [r for w, r in paired if w == "SKIP"]
            m = lambda xs: f"{sum(xs)/len(xs):+.3f}R over {len(xs)}" if xs else "no closed rows yet"
            self.send(f"COACH COUNTER DUE: {n} TrendCont opinions since {self.OPINION_FROM} ({len(paired)} with a closed "
                      f"outcome).\nopinion TAKE: {m(tk)}\nopinion SKIP (blind outcome): {m(sk)}\n"
                      "That is the first read on whether the advisor's own view carries anything.")

    # Equity history (trader 2026-09-30): the broker's deal history gives the BALANCE curve for free, but equity - what
    # the account is worth with positions open, and what FTMO actually measures - exists only in the moment. So sample
    # it every EQUITY_EVERY_S from the newest heartbeat, with the two floors beside it so the chart can show the room.
    EQUITY_EVERY_S = 300

    def equity_sample(self):
        now = time.time()
        if now - self.st.get("equity_last", 0) < self.EQUITY_EVERY_S: return
        hbs = [C.heartbeat(self.cfg, s) for s in C.symbols(self.cfg)]
        hbs = [h for h in hbs if h and h.get("equity") is not None]
        if not hbs: return
        hb = max(hbs, key=lambda h: C.parse_iso(h.get("ts")) or C.now_utc())
        f = hb.get("ftmo") or {}
        p = os.path.join(self.cfg["root"], "web", "equity.csv")
        new = not os.path.exists(p)
        C.append_line(p, ("ts,equity,balance,daily_floor,max_floor,agg_risk\n" if new else "") +
                      f"{C.now_iso()},{hb.get('equity')},{hb.get('balance')},{f.get('daily_floor', '')},{f.get('max_floor', '')},"
                      f"{hb.get('aggregate_risk_to_stop', '')}")
        self.st["equity_last"] = now

    SHADOW_EVERY_S = 3600

    def shadow_bank(self):
        """coach 2026-09-30 item 8: the bank shadow log, hourly; one Telegram line when the 30-fill revisit point is met."""
        now = time.time()
        if now - self.st.get("shadow_last", 0) < self.SHADOW_EVERY_S: return
        from live import shadow_bank
        n, be, ok = shadow_bank.write(self.cfg)
        sn, s_rdd, c_rdd = shadow_bank.write_staged(self.cfg)          # coach item 18: early read at 20, grading at 40
        try:                                                             # coach item 21: Inverse alerts
            from live import inverse_shadow
            iv = inverse_shadow.write(self.cfg)
            if iv["slip_first10"] is not None and iv["slip_first10"] > 0.05 and not self.st.get("inv_slip_flag"):
                self.st["inv_slip_flag"] = True
                self.send(f"COACH WATCH ITEM - Inverse fills: the first 10 filled at an average of {iv['slip_first10']:+.3f}R beyond the parent's stop "
                          f"(threshold 0.05R; the backtest's edge is gone near 0.10R). web/inverse_shadow.csv has each fill.")
            if iv["closed"] >= 25 and iv["mean_R"] is not None and iv["mean_R"] <= -0.15 and not self.st.get("inv_stop_flag"):
                self.st["inv_stop_flag"] = True
                self.send(f"COACH EARLY STOP - Inverse: {iv['closed']} closed fills average {iv['mean_R']:+.3f}R (<= -0.15R). The ruling is OFF: "
                          f"set live.json inverse_signal to 0.")
            if iv["closed"] >= 50 and not self.st.get("inv_read_flag"):
                self.st["inv_read_flag"] = True
                self.send(f"COACH READ DUE - Inverse: {iv['closed']} closed fills, mean {iv['mean_R']:+.3f}R vs the backtest +0.20R. web/inverse_shadow.csv.")
        except Exception as e:
            self.guard_error("inverse_shadow", e)
        ipn, ip_inc = shadow_bank.inverse_pyramid_watch(self.cfg)       # coach 2026-10-01: early stop for the pyramid on Inverses
        if ipn >= 10 and ip_inc is not None and not self.st.get("inv_pyr_flag"):
            self.st["inv_pyr_flag"] = True
            if ip_inc < 0:
                self.send(f"COACH EARLY STOP - pyramid on Inverses: the first 10 adds average {ip_inc:+.3f}R vs no add. The ruling is OFF: "
                          f"set live.json pyramid_on_inverse to 0 (provisioning/live.json + deploy --config) and tell the coach.")
            else:
                self.send(f"Pyramid on Inverses: the first 10 adds average {ip_inc:+.3f}R vs no add - the early stop did not trigger.")
        try:                                                             # coach 2026-10-01 watch flag (no rule): TrendCont index shorts
            from live import eligibility as ELIG
            rows = C.journal_rows(self.cfg)
            firsts = {(r.get("symbol"), r.get("signal_id")): r for r in rows if int(ELIG._f(r.get("tranche")) or 0) <= 1}
            sig_r: dict = {}
            for r in rows:
                root = (r.get("symbol") or "").split(".")[0]
                if root not in ("US100", "US500", "US30"): continue
                key = (r.get("symbol"), r.get("signal_id")); f0 = firsts.get(key)
                if not f0 or f0.get("strategy") != "TrendCont" or (f0.get("direction") or "").upper() != "SELL": continue
                if r.get("decision") not in ("approved", "approved_pending") or not r.get("exit_time"): continue
                rr = ELIG._f(r.get("r_multiple"))
                if rr is None: continue
                sig_r[key] = sig_r.get(key, 0.0) + rr * ELIG.price_r_scale(r)                      # full-position R per signal (r_multiple is in the first tranche's 1R)
            ix_n = len(sig_r)
            if ix_n >= 20 and not self.st.get("ix_short_flag"):
                self.st["ix_short_flag"] = True
                self.send(f"COACH WATCH FLAG - TrendCont index shorts on the live book: {ix_n} closed signals, mean "
                          f"{sum(sig_r.values()) / ix_n:+.3f}R (full-position R; backtest 2012-24 -0.164, 2025-26 -0.189). No rule - for the coach.")
            self.st["ix_short_n"] = ix_n
        except Exception as e:
            self.guard_error("index-short watch", e)
        try:                                                             # coach item 25: shorts stop raise vs stop-at-entry, 15 / 30
            from live import short_raise_shadow
            srn, sr_sum = short_raise_shadow.write(self.cfg)
            if srn >= 15 and not self.st.get("short_raise_flag_15"):
                self.st["short_raise_flag_15"] = True
                self.send(f"COACH EARLY READ - shorts stop raise: {srn} armed shorts resolved, sum of actual - stop-at-entry counterfactual "
                          f"{sr_sum:+.3f}R ({sr_sum / srn:+.3f}R each). No ruling at 15; judged at 30. web/short_raise.csv.")
            if srn >= 30 and not self.st.get("short_raise_flag_30"):
                self.st["short_raise_flag_30"] = True
                if sr_sum < 0:
                    self.send(f"COACH GUARD TRIPPED - shorts stop raise: {srn} armed shorts, sum of actual - counterfactual {sr_sum:+.3f}R (< 0). "
                              f"The rule is RETIRED: set live.json short_raise_at_pyramid_r to 0 (provisioning/live.json + deploy --config) and tell the coach.")
                else:
                    self.send(f"COACH GRADING POINT - shorts stop raise: {srn} armed shorts, sum of actual - counterfactual {sr_sum:+.3f}R (>= 0): "
                              f"the guard holds. web/short_raise.csv.")
        except Exception as e:
            self.guard_error("short_raise_shadow", e)
        try:                                                             # coach item 26: re-offered takes vs on-time takes at n=20
            from live import reoffer_shadow
            ro = reoffer_shadow.write(self.cfg)
            if ro["taken"] >= 20 and not self.st.get("reoffer_flag_20"):
                self.st["reoffer_flag_20"] = True
                f_ = lambda v: "n/a" if v is None else f"{v:+.3f}R"
                if ro["taken_mean"] < -0.10:
                    self.send(f"COACH GUARD TRIPPED - re-offered spread rejects: {ro['taken']} re-offered takes average {f_(ro['taken_mean'])} (< -0.10R) vs "
                              f"on-time takes {f_(ro['ontime_mean'])} (n={ro['ontime']}). The rule is OFF: set live.json reoffer_spread_rejects to 0 "
                              f"(provisioning/live.json + deploy --config) and tell the coach. Skipped re-offers' shadow: {f_(ro['skipped_mean'])} (n={ro['skipped']}).")
                else:
                    self.send(f"COACH GRADING POINT - re-offered spread rejects: {ro['taken']} re-offered takes average {f_(ro['taken_mean'])} vs on-time "
                              f"takes {f_(ro['ontime_mean'])} (n={ro['ontime']}); skipped re-offers' shadow {f_(ro['skipped_mean'])} (n={ro['skipped']}). "
                              f"The -0.10R guard holds. web/reoffer.csv.")
        except Exception as e:
            self.guard_error("reoffer_shadow", e)
        try:                                                             # coach queue A / B / I: one counter per rule, read at n=30.
            from live import queue_shadow                                #   trader 2026-10-03: never retired automatically - tell the trader
            qs = queue_shadow.write(self.cfg)
            names = {"A": "add gate (hold the add until +1R)", "B": "shorts day-2 cut", "I": "improving-longs add"}
            for rule, (qn, qsum) in qs.items():
                if qn >= 30 and not self.st.get(f"queue_flag_{rule}"):
                    self.st[f"queue_flag_{rule}"] = True
                    verdict = ("BELOW ZERO - the coach's bar says retire; per your ruling it stays ON until you decide" if qsum < 0 else "at or above zero - holds")
                    self.send(f"COACH GRADING POINT - {names[rule]}: {qn} resolved, sum of actual - counterfactual {qsum:+.3f}R ({qsum / qn:+.3f}R each): "
                              f"{verdict}. web/queue_rules.csv.")
            self.st["queue_counts"] = {k: v[0] for k, v in qs.items()}
        except Exception as e:
            self.guard_error("queue_shadow", e)
        try:                                                             # trader 2026-10-03: live trades into the Data visualizers tab
            from live import rpaths_live
            self.st["rpaths_live_n"] = rpaths_live.write(self.cfg)
        except Exception as e:
            self.guard_error("rpaths_live", e)
        mn, m_diff = shadow_bank.write_manual(self.cfg)                  # coach item 22: graded at n=20 manual sizings
        if mn >= 20 and not self.st.get("manual_flag_20"):
            self.st["manual_flag_20"] = True
            self.send(f"COACH GRADING POINT - manual sizing (Full now / Promote now): {mn} closed trades, mean actual - bar-6 counterfactual "
                      f"{m_diff:+.3f}R (pre-registered bar >= +0.10R or the lever is retired). web/manual_sizing.csv.")
        try:                                                             # coach item 22: the advisor Size line, read at n=30 cards
            lg = os.path.join(self.cfg["advisor"]["live_dir"], "verdicts.live.log")
            with open(lg, encoding="utf-8", errors="replace") as fh:
                ns = sum(1 for ln in fh if "| size:" in ln and "| size:-" not in ln)
            if ns >= 30 and not self.st.get("size_line_flag_30"):
                self.st["size_line_flag_30"] = True
                self.send(f"COACH READ DUE - the advisor's Size line has been given on {ns} cards (verdicts.live.log 'size:' field).")
        except OSError: pass
        fn_, f_mean = shadow_bank.pyramid_fill_watch(self.cfg)            # coach watch 2026-10-01: add fills vs the modelled +1.5R
        if fn_ >= 10 and f_mean is not None and f_mean < 1.55 and not self.st.get("pyr_fill_flag"):
            self.st["pyr_fill_flag"] = True
            self.send(f"COACH WATCH ITEM - pyramid fills: the first {fn_} live adds filled at an average of +{f_mean:.2f}R "
                      f"(modelled +1.50R; flag threshold +1.55R). web/staged_entry.csv has each fill.")
        pn, p_mean, p_ddr = shadow_bank.pyramid_stats(self.cfg)         # coach item 20: graded at n=30 pyramided trades
        if pn >= 30 and not self.st.get("pyr_flag_30"):
            self.st["pyr_flag_30"] = True
            self.send(f"COACH GRADING POINT - pyramid: {pn} closed pyramided signals. Mean R vs the no-add counterfactual "
                      f"{p_mean:+.3f} (needs >= 0); drawdown {p_ddr:.2f}x the counterfactual's (needs <= 1.25x). web/staged_entry.csv has the rows.")
        for mark in (20, 40):
            if sn >= mark and not self.st.get(f"staged_flag_{mark}"):
                self.st[f"staged_flag_{mark}"] = True
                f_ = lambda v: "n/a (no drawdown yet)" if v is None else f"{v:.2f}"
                self.send(f"COACH {'EARLY READ' if mark == 20 else 'GRADING POINT'} - staged entry: {sn} closed staged signals. "
                          f"R per unit of worst drawdown: staged {f_(s_rdd)} vs all-at-signal {f_(c_rdd)}"
                          + (" (the mode needs >= 1.0x)" if mark == 40 else " (no ruling at 20)") + ". web/staged_entry.csv has the rows.")
        self.st["shadow_last"] = now; self.st["shadow"] = [n, be, ok]
        if ok >= 30 and not self.st.get("shadow_flagged"):
            self.st["shadow_flagged"] = True
            self.send(f"COACH REVISIT POINT: {ok} live TrendCont stop-to-entry exits filled within 0.05R of modelled "
                      f"(of {be}; {n} closed TrendConts in the bank shadow log). web/shadow_bank.csv has the rows.")

    SPREAD_EVERY_S = 300

    def spread_sample(self):
        """coach 2026-10-01 item 23 follow-up: the spread profile was MT5's bar-MINIMUM spread (EURUSD ~ 0). Sample the live
        bid/ask of every symbol every 5 minutes to web/spread_live.csv (ts, symbol, utc hour, spread in price) so the profile
        can be rebuilt by symbol and hour from what the box actually quoted (with sizing.spread from the published signals)."""
        now = time.time()
        if now - self.st.get("spread_last", 0) < self.SPREAD_EVERY_S: return
        from live import mt5feed
        p = os.path.join(self.cfg["root"], "web", "spread_live.csv"); new = not os.path.exists(p)
        hr = C.now_utc().hour; lines = []
        for sym in C.symbols(self.cfg):
            try: k = mt5feed.tick(sym)
            except Exception: k = None
            if k and k.get("bid") and k.get("ask"): lines.append(f"{C.now_iso()},{sym},{hr},{float(k['ask']) - float(k['bid']):.6f}")
        if lines: C.append_line(p, ("ts,symbol,hour_utc,spread\n" if new else "") + "\n".join(lines))
        self.st["spread_last"] = now

    def guard_error(self, name: str, e: Exception):
        """coach 2026-10-03 item 7: a guard module that fails (import error, crash) is ALERTED, not swallowed - once per name+error per day."""
        self.log(f"{name} error: {e!r}")
        key = f"{name}|{type(e).__name__}|{time.strftime('%Y-%m-%d')}"
        sent = self.st.setdefault("guard_err_sent", [])
        if key in sent: return
        self.st["guard_err_sent"] = (sent + [key])[-200:]
        self.send(f"GUARD ERROR - {name} failed: {type(e).__name__}: {str(e)[:200]}. Its live grading is NOT running until fixed.")

    def tick(self):
        for fn in (self.signals, self.acks, self.positions, self.heartbeats, self.processes, self.calendar, self.summary, self.reminders, self.eligibility, self.opus_fallback, self.trendcont_watch, self.opinion_watch, self.equity_sample, self.shadow_bank, self.spread_sample):
            try: fn()
            except Exception as e: self.guard_error(fn.__name__, e)
        self.save()

    def loop(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        boot_s = None
        if os.name == "nt":
            try: boot_s = float(subprocess.run(["powershell", "-NoProfile", "-Command", "((Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalSeconds"], capture_output=True, text=True, timeout=30).stdout.strip())
            except Exception: pass
        self.log(f"monitor up (dry={self.dry}); uptime_s={boot_s}")
        # one boot check per boot: a service restart inside the first 15 min must not re-announce a reboot
        boot_key = str(int(time.time() - boot_s)) if boot_s is not None else ""
        self.boot_check_due = (boot_s is not None and boot_s < 900 and abs(int(self.st.get("last_boot_checked", "0") or 0) - int(boot_key or 0)) > 30); self.boot_t0 = time.time()
        if self.boot_check_due:
            self.st["last_boot_checked"] = boot_key; self.save()
            self.send(f"Box is back up (monitor started {int(boot_s)} s after boot). Checking MT5, the EAs, the feed and the web app now...")
        while True:
            self.tick()
            if self.boot_check_due: self.boot_check()
            time.sleep(self.m["interval_s"])

    def boot_check(self):
        """after a reboot: report once when EVERY expected EA heartbeat is fresh + web up + feed connected, or fail loudly."""
        want = (self.cfg.get("expected_symbols") or C.symbols(self.cfg)); fresh = []
        for sym in want:
            hb = C.heartbeat(self.cfg, sym); age = C.heartbeat_age_s(hb)
            if hb and hb.get("status") == "running" and age is not None and age < 150 and C.parse_iso(hb.get("ts")) and C.parse_iso(hb["ts"]).timestamp() > self.boot_t0 - 60: fresh.append(sym)
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=5) as r: web_ok = r.status == 200
        except Exception: web_ok = False
        feed_ok = True
        try:
            from live import mt5feed; feed_ok = bool(mt5feed.status().get("connected"))
        except Exception: pass
        if len(fresh) == len(want) and want and web_ok and feed_ok:
            self.boot_check_due = False
            self.send(f"ALL SYSTEMS UP after reboot: {len(fresh)}/{len(want)} EAs heartbeating ({', '.join(sorted(fresh))}), terminal feed connected, web app up, trading {'ENABLED' if C.kill_switch(self.cfg) else 'disabled'}. {int(time.time() - self.boot_t0)} s after monitor start.")
        elif time.time() - self.boot_t0 > 600:
            self.boot_check_due = False
            self.send(f"REBOOT RECOVERY INCOMPLETE after 10 min: EAs up {len(fresh)}/{len(want)} (missing {', '.join(sorted(set(want) - set(fresh))) or '-'}), web {'up' if web_ok else 'DOWN'}, feed {'ok' if feed_ok else 'NOT CONNECTED'}. Intervene: {self.base}/")

    def reminders(self):
        """Friday 18:00 UTC: Contabo snapshot reminder before the Saturday maintenance reboot (provider snapshots expire)."""
        now = C.now_utc(); key = now.strftime("%Y-%m-%d")
        if now.weekday() == 4 and now.strftime("%H:%M") >= "18:00" and self.st.get("last_snapshot_reminder") != key:
            self.st["last_snapshot_reminder"] = key
            self.send("Reminder: the box installs Windows updates and reboots tomorrow (Saturday) at 14:00 UTC. Take a Contabo snapshot now if you want a rollback point (their snapshots expire).")

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); ap.add_argument("--dry", action="store_true", help="log instead of sending"); ap.add_argument("--once", action="store_true")
    a = ap.parse_args(); mon = Monitor(C.load_config(a.config), dry=a.dry)
    if a.once: os.makedirs(os.path.dirname(mon.state_path), exist_ok=True); mon.tick(); return
    mon.loop()

if __name__ == "__main__": main()
