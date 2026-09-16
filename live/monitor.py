r"""
monitor.py - Telegram notifier + watchdog in one loop (spec W9, O1, O2). Outbound only (S5): never reads Telegram.

Every interval it compares the queue root with a persisted seen-state and sends: new signal (deep link), signal
auto-skip/expiry/invalidation, task acks (fill / rejection), +1R bank, position closed with R, heartbeat stale
(then recovered), AutoTrading flags off, MT5 process absent (restart via the scheduled task, then alert), web app
down (restart), FTMO headroom below thresholds, calendar coverage < 14 d (daily), a daily summary, and a
"recovered" message when it starts within a few minutes of boot.
"""
from __future__ import annotations
import glob, json, os, subprocess, sys, time, urllib.request
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
            if prev == stt: continue
            lv = s.get("levels") or {}; rr = s.get("rr") or {}
            if prev is None and stt == "open":
                dl = C.parse_iso(s.get("deadline"))
                self.send(f"NEW SIGNAL #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']} [{s.get('decision_class')}]\n"
                          f"entry {lv.get('entry')} SL {lv.get('sl')} TP1 {lv.get('tp1')} ({rr.get('tp1')}R) · {(s.get('sizing') or {}).get('lots')} lots\n"
                          f"{(s.get('regime') or {}).get('pretty', '')} · deadline {C.fmt_dt(dl)} ({C.rel_time(dl)})\n{self.base}/signal/{key}")
            elif prev == "open" and stt in ("expired", "rejected", "auto_skipped"):
                why = {"expired": "no decision before the deadline (skip code 8)", "rejected": "invalidated (price through SL while parked)", "auto_skipped": "election gate: " + str(s.get("auto_reason", ""))}[stt]
                self.send(f"signal #{s['signal_id']} {s['symbol']} {s['strategy']} {s['direction']}: {stt} — {why}")
            elif prev == "open" and stt in ("approved", "approved_pending", "skipped"):
                pass  # the ack message covers it
            elif prev is None and stt != "open":
                pass  # decided before the monitor started
            seen[key] = stt
        # advisor failures
        for p in glob.glob(os.path.join(self.cfg["root"], "advisor", "verdicts", "*.json")):
            rec = C.load_json(p)
            if not rec or not rec.get("consults"): continue
            last = rec["consults"][-1]; tag = f"adv-fail:{rec['signal_key']}:{last.get('ts')}"
            if not last.get("ok") and tag not in self.st["acks"] and C.parse_iso(last.get("ts")) and C.now_utc() - C.parse_iso(last["ts"]) < timedelta(hours=6):
                self.st["acks"].append(tag); self.send(f"advisor consult FAILED for {rec['signal_key']}: {str(last.get('error'))[:200]}")

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
                elif a.get("verb") == "test_signal":
                    self.send(f"TEST signal published on {a['symbol']} (#{refs.get('signal_id')}) - approve or skip it: {self.base}/signal/{a['symbol']}-{refs.get('signal_id')}")
                elif a.get("verb") in ("close", "close50", "sl_be", "ratchet_tp1", "skip", "delay"):
                    self.send(f"{a['verb']} accepted: {tgt} {a['symbol']}")
            else:
                self.send(f"TASK {a.get('result', '').upper()}: {a.get('verb')} {tgt} {a['symbol']} — {a.get('reason')}")
        self.st["acks"] = sorted(seen)[-2000:]

    def positions(self):
        seen = self.st["positions"]
        for p in C.list_positions(self.cfg):
            k = f"{p['symbol']}-{p['posid']}"; prev = seen.get(k) or {}
            if p.get("banked") and not prev.get("banked"):
                self.send(f"+1R BANKED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · banked {C.r_fmt(p.get('banked_r'))}, {p.get('lots_live')} lots run · SL now {p.get('sl_live')}")
            if p.get("ratcheted") and not prev.get("ratcheted"): self.send(f"SL ratcheted to TP1: pos {p['posid']} {p['symbol']}")
            seen[k] = {"banked": bool(p.get("banked")), "ratcheted": bool(p.get("ratcheted")), "open": True}
        for p in C.list_positions(self.cfg, closed=True):
            k = f"{p['symbol']}-{p['posid']}"; prev = seen.get(k) or {}
            if prev.get("open") or (k not in seen and C.parse_iso(p.get("ts")) and C.now_utc() - C.parse_iso(p["ts"]) < timedelta(hours=12)):
                tot = (p.get("banked_r") or 0) + (p.get("closenow_r") or 0)
                self.send(f"CLOSED: pos {p['posid']} {p['symbol']} {p['strategy']} {p['direction']} · total {C.r_fmt(tot)} (banked {C.r_fmt(p.get('banked_r'))}) after {p.get('bars_open')} bars\n{self.base}/position/{k}")
            seen[k] = {"banked": bool(p.get("banked")), "ratcheted": bool(p.get("ratcheted")), "open": False}

    def heartbeats(self):
        stale_s = self.m["heartbeat_stale_s"]
        for sym in C.symbols(self.cfg):
            hb = C.heartbeat(self.cfg, sym); age = C.heartbeat_age_s(hb)
            stale = hb is None or age is None or age > stale_s or hb.get("status") != "running"
            was = self.st["hb_stale"].get(sym, False)
            if stale and not was:
                self.send(f"HEARTBEAT LOST: {sym} — last beat {C.rel_time(C.parse_iso(hb.get('ts')) if hb else None)} (status {hb.get('status') if hb else 'no file'})")
            elif was and not stale:
                self.send(f"heartbeat recovered: {sym} (beat {C.rel_time(C.parse_iso(hb.get('ts')))})")
            self.st["hb_stale"][sym] = stale
            if hb:
                off = not (hb.get("terminal_trade_allowed") and hb.get("mql_trade_allowed"))
                if off and not self.st["flags_off"].get(sym): self.send(f"AUTOTRADING DISARMED on {sym}: terminal_trade_allowed={hb.get('terminal_trade_allowed')} mql_trade_allowed={hb.get('mql_trade_allowed')} — approvals will be refused")
                elif not off and self.st["flags_off"].get(sym): self.send(f"AutoTrading armed again on {sym}")
                self.st["flags_off"][sym] = off
                f = hb.get("ftmo") or {}; ib = f.get("initial_balance") or 0
                if ib:
                    for name, hd in (("daily", f.get("headroom_daily")), ("max", f.get("headroom_max"))):
                        if hd is None: continue
                        lvl = next((t for t in sorted(self.m["headroom_warn_pct"]) if hd < t * ib), None)
                        prev = self.st["headroom"].get(f"{sym}:{name}")
                        if lvl is not None and lvl != prev: self.send(f"DRAWDOWN WARNING {sym}: {name}-loss headroom {hd:,.0f} < {lvl*100:.0f}% of initial ({ib:,.0f}) · equity {hb.get('equity')}")
                        self.st["headroom"][f"{sym}:{name}"] = lvl

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
        for sym in C.symbols(self.cfg):
            hb = C.heartbeat(self.cfg, sym) or {}; f = hb.get("ftmo") or {}
            parts.append(f"{sym}: equity {hb.get('equity')} · daily headroom {f.get('headroom_daily')} · max headroom {f.get('headroom_max')} · open {len(hb.get('open_positions') or [])}")
        try:
            from live import brief_runner; b = brief_runner.latest_brief(self.cfg, 2.0)
            parts.append("context brief available (" + C.rel_time(b["t"]) + ")" if b else "no context brief in the last 2 days - generate one from the Context page if you want the advisor to have it")
        except Exception: pass
        parts.append(f"{self.base}/")
        self.send("\n".join(parts))

    def tick(self):
        for fn in (self.signals, self.acks, self.positions, self.heartbeats, self.processes, self.calendar, self.summary, self.reminders):
            try: fn()
            except Exception as e: self.log(f"{fn.__name__} error: {e!r}")
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
