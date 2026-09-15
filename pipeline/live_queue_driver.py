#!/usr/bin/env python3
"""
live_queue_driver.py - reference client for the Phase-3 live file queue (docs/live-queue-schema.md).

Watches <root>/signals/ for status:"open" signals, prints the decision summary, writes tasks the EA
consumes, and tails acks/ + audit_<SYM>.log. Doubles as the web app's reference client (M2).

  python3 live_queue_driver.py --root <Common>/Files/live --symbol EURUSD.dk --watch            # interactive
  python3 live_queue_driver.py --root ... --symbol ... --watch --auto skip:6                     # unattended
  python3 live_queue_driver.py --root ... --symbol ... --positions                               # position verbs
  python3 live_queue_driver.py --root ... --symbol ... --task approve --signal 12 --entry-mode market
  python3 live_queue_driver.py --root ... --symbol ... --task close50 --position 1234567

Tasks are written atomically (.tmp + os.replace) with a uuid4 task_id; every write is echoed and the ack,
when it lands, is printed. stdlib only.
"""
import argparse, glob, json, os, sys, time, uuid
from datetime import datetime, timezone

def now_iso(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def load(p):
    try:
        with open(p, encoding="ascii", errors="replace") as f: return json.load(f)
    except Exception as e: return {"_error": str(e)}

def write_task(root, symbol, verb, params, signal_id=None, position_id=None, issued_by="driver"):
    tid = uuid.uuid4().hex
    t = {"schema_version": 1, "task_id": tid, "symbol": symbol, "verb": verb, "params": params,
         "issued_at": now_iso(), "issued_by": issued_by}
    if signal_id is not None: t["signal_id"] = int(signal_id)
    if position_id is not None: t["position_id"] = int(position_id)
    d = os.path.join(root, "tasks"); os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, tid + ".json.tmp"); dst = os.path.join(d, tid + ".json")
    with open(tmp, "w", encoding="ascii") as f: json.dump(t, f, separators=(",", ":"))
    os.replace(tmp, dst)
    print(f"-> task {tid[:8]} {verb} {params} sig={signal_id} pos={position_id}")
    return tid

def wait_ack(root, tid, timeout):
    p = os.path.join(root, "acks", tid + ".json"); t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(p):
            a = load(p); print(f"<- ack {tid[:8]} {a.get('result')} {a.get('reason')} refs={a.get('refs')}"); return a
        time.sleep(1)
    print(f"<- (no ack for {tid[:8]} within {timeout}s)"); return None

def summary(sig):
    lv = sig.get("levels", {}); rr = sig.get("rr", {}); sz = sig.get("sizing", {}); rg = sig.get("regime", {})
    ev = sig.get("events", []); eg = sig.get("election_gate", {})
    lines = [f"#{sig.get('signal_id')} {sig.get('symbol')} {sig.get('strategy')} {sig.get('direction')}  {sig.get('sigtime_text')}  session={sig.get('session')}",
             f"  entry {lv.get('entry')}  sl {lv.get('sl')}  tp1 {lv.get('tp1')}  tp2 {lv.get('tp2')}  tp {lv.get('tp')}  RR {rr.get('text')}",
             f"  lots {sz.get('lots')} (risk {sz.get('risk_pct_effective')}, mult {sz.get('risk_mult_applied')})  regime {rg.get('pretty')}  class {sig.get('decision_class')}",
             f"  deadline {sig.get('deadline')}  delays {sig.get('delay_count')} (implicit streak {sig.get('implicit_streak')})  entry_modes {sig.get('entry_modes')}",
             f"  trading_enabled={sig.get('trading_enabled')}  election_gate={eg.get('hit')} {eg.get('event','')}"]
    for e in ev[:6]: lines.append(f"  event {e.get('t_utc')} {e.get('ccy')} {e.get('name')} [{e.get('label')}]{' BINDING' if e.get('binding') else ''}")
    return "\n".join(lines)

def open_signals(root, symbol):
    out = []
    for p in sorted(glob.glob(os.path.join(root, "signals", f"{symbol}-*.json"))):
        s = load(p)
        if s.get("status") == "open": out.append(s)
    return out

def decide(root, symbol, sig, auto, timeout):
    sid = sig["signal_id"]
    if auto:
        verb, _, arg = auto.partition(":")
    else:
        print(summary(sig)); print("  [a]pprove market  [p]ending  [s]kip <1-6>  [d]elay  [i]gnore > ", end="", flush=True)
        ans = sys.stdin.readline().strip().lower()
        if not ans or ans[0] == "i": return
        verb = {"a": "approve", "p": "approve", "s": "skip", "d": "delay"}.get(ans[0]);
        arg = "pending" if ans[0] == "p" else ans[1:].strip()
        if verb is None: return
    if verb == "approve": tid = write_task(root, symbol, "approve", {"entry_mode": arg or "market"}, signal_id=sid)
    elif verb == "skip": tid = write_task(root, symbol, "skip", {"reason_code": int(arg or 6)}, signal_id=sid)
    elif verb == "delay": tid = write_task(root, symbol, "delay", {}, signal_id=sid)
    else: print(f"unknown auto verb {verb}"); return
    wait_ack(root, tid, timeout)

def tail_audit(root, symbol, state):
    p = os.path.join(root, f"audit_{symbol}.log")
    if not os.path.exists(p): return
    with open(p, encoding="ascii", errors="replace") as f:
        f.seek(state.get("pos", 0)); new = f.read(); state["pos"] = f.tell()
    for l in new.splitlines():
        if l.strip(): print("   audit:", l)

def positions(root, symbol):
    for p in sorted(glob.glob(os.path.join(root, "positions", f"{symbol}-*.json"))):
        x = load(p)
        print(f"pos {x.get('posid')} sig#{x.get('signal_id')} {x.get('strategy')} {x.get('direction')} lots={x.get('lots')} open_r={x.get('open_r')} banked={x.get('banked')} sl={x.get('sl')} be_placeable={x.get('be_placeable')} ratchet_placeable={x.get('ratchet_placeable')}")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True); ap.add_argument("--symbol", required=True)
    ap.add_argument("--watch", action="store_true"); ap.add_argument("--auto", help="approve[:market|pending] | skip:N | delay")
    ap.add_argument("--positions", action="store_true", help="list positions/ and exit")
    ap.add_argument("--task", choices=["approve", "skip", "delay", "close", "close50", "sl_be", "ratchet_tp1"])
    ap.add_argument("--signal", type=int); ap.add_argument("--position", type=int)
    ap.add_argument("--entry-mode", default="market"); ap.add_argument("--reason-code", type=int, default=6)
    ap.add_argument("--ack-timeout", type=float, default=30.0); ap.add_argument("--interval", type=float, default=5.0)
    a = ap.parse_args()
    hb = load(os.path.join(a.root, f"heartbeat_{a.symbol}.json"))
    print(f"heartbeat: status={hb.get('status')} ts={hb.get('ts')} trading_enabled={hb.get('trading_enabled')} "
          f"terminal_trade_allowed={hb.get('terminal_trade_allowed')} mql_trade_allowed={hb.get('mql_trade_allowed')} agg_risk={hb.get('aggregate_risk_to_stop')}")
    if a.positions: positions(a.root, a.symbol); return
    if a.task:
        params = {}
        if a.task == "approve": params = {"entry_mode": a.entry_mode}
        if a.task == "skip": params = {"reason_code": a.reason_code}
        if a.task in ("approve", "skip", "delay"):
            if a.signal is None: sys.exit("--signal required")
            tid = write_task(a.root, a.symbol, a.task, params, signal_id=a.signal)
        else:
            if a.position is None: sys.exit("--position required")
            tid = write_task(a.root, a.symbol, a.task, params, position_id=a.position)
        wait_ack(a.root, tid, a.ack_timeout); return
    if not a.watch: ap.print_help(); return
    seen = set(); st = {"pos": os.path.getsize(os.path.join(a.root, f"audit_{a.symbol}.log")) if os.path.exists(os.path.join(a.root, f"audit_{a.symbol}.log")) else 0}
    print(f"watching {a.root} for {a.symbol} signals (Ctrl-C to stop)")
    try:
        while True:
            for s in open_signals(a.root, a.symbol):
                key = (s["signal_id"], s.get("delay_count"), s.get("implicit_streak"))
                if key in seen: continue
                seen.add(key)
                decide(a.root, a.symbol, s, a.auto, a.ack_timeout)
            tail_audit(a.root, a.symbol, st)
            time.sleep(a.interval)
    except KeyboardInterrupt: print("\nbye")

if __name__ == "__main__": main()
