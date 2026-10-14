"""plan_live_levels_check.py - trader 2026-10-14: custom levels and armed (waiting) plans on the live signal page, and the live advisor's own
plan (live/plan_live.py validate_levels / validate_arm / cond_met / approve / Runner.armed / advisor_plan). Synthetic BUY signal: entry 1.0000,
stop 0.9900 (1R = 0.0100), TP2 1.0200, no spread.
  L1 levels: unchanged -> none; a stop / TP on the wrong side refused; Inverse + levels refused; a custom entry gets its 12 h expiry
  L2 the record takes the custom levels: card stop / TP / 1R, a custom entry = a pending order; the R frame counts from the fill to YOUR stop
  L3 the EA's approve params: lv_* only for what changed, pend_bars = ceil(hours / 4)
  L4 the wait: a default 48 h limit; bad conditions refused
  L5 conditions: touch / close (only once its bar closed, and only bars closing after `since`) / after (UTC + server offset) / hours
  L6 approve(): an armed plan writes no task; levels on an EA without plan_levels refused; with it the task carries them; armed blocks a 2nd
  L7 the runner, armed: steps met IN ORDER (a close before the touch does not count), a keep-alive delay when the next bar close would expire
     the signal, then the approve with the record's params, lever hours re-anchored to the trigger bar
  L8 the runner, cancel: a cancel level touched -> cancelled, nothing sent; the signal no longer open -> cancelled
  L9 the advisor's plan block: valid -> the builder's config with levels + wait; bad JSON / wrong-side stop -> refused; levels on an old EA warn
  python3 pipeline/plan_live_levels_check.py   (exit 0 = PASS)"""
from __future__ import annotations
import glob, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import plan_live as PL
from live import common as C
from live import mt5feed
from live import verdict_fmt as VF

OK = True
T0 = 1_790_000_000 - 1_790_000_000 % 14400          # a server-clock H4 open


def check(name, cond, info=""):
    global OK
    OK &= bool(cond); print(f"{'ok  ' if cond else 'FAIL'} {name} {info}")


def refused(fn, *a):
    try: fn(*a); return ""
    except ValueError as e: return str(e)


SIG = {"signal_key": "SIM.sim-7", "symbol": "SIM.sim", "signal_id": 7, "strategy": "TrendCont", "direction": "BUY", "status": "open",
       "levels": {"entry": 1.0, "sl": 0.99, "tp1": 1.01, "tp2": 1.02, "two_target": False, "tp": 1.02}, "sizing": {"lots": 0.5},
       "decision_bar": "2026-10-14T08:00:00Z", "implicit_streak": 0, "max_age_bars": 3}
PLAN = {"schema": "levers/v1", "entry": {"pct": 100}, "tp2": True, "levers": [{"type": "move", "to": 0, "when": 1.0, "tp1": False, "legs": "all"}]}


def main() -> int:
    # L1
    check("L1 unchanged levels -> none", PL.validate_levels({"entry": 1.0, "sl": 0.99, "tp2": 1.02}, SIG)[0] is None)
    check("L1 a stop above the entry on a BUY is refused", "below the entry" in refused(PL.validate_levels, {"sl": 1.001}, SIG))
    check("L1 TP2 below the entry is refused", "TP2 must be above" in refused(PL.validate_levels, {"tp2": 0.995}, SIG))
    check("L1 Inverse + levels refused", "Inverse" in refused(PL.validate_levels, {"sl": 0.995}, SIG, True))
    lv, w = PL.validate_levels({"entry": 0.997, "sl": 0.994}, SIG)
    check("L1 a custom entry gets its 12 h expiry", lv == {"entry": 0.997, "sl": 0.994, "entry_h": 12.0}, str(lv))
    check("L1 TP2 under 1R from the entry warns (the EA's floor)", any("R:R floor" in x for x in PL.validate_levels({"sl": 0.97}, SIG)[1]))
    # L2
    plan = PL.live_validate(PLAN)[0]
    rec = PL.new_record(SIG, plan, [], False, "market", {"sl": 0.995, "tp2": 1.03})
    c = rec["card"]
    check("L2 the card takes the custom stop / TP2 / 1R", c["sl"] == 0.995 and c["tp2"] == 1.03 and abs(c["R"] - 0.005) < 1e-12 and rec["entry_mode"] == "market", str(c))
    rec["fill"] = {"t": T0, "px": 1.001}
    d, e, R = PL.rbase(rec)
    check("L2 R counts from the fill to YOUR stop", d == 1 and e == 1.001 and abs(R - 0.006) < 1e-12, f"{(d, e, R)}")
    rp = PL.new_record(SIG, plan, [], False, "market", {"entry": 0.997, "sl": 0.994, "entry_h": 12.0})
    check("L2 a custom entry is a pending order", rp["entry_mode"] == "pending" and rp["card"]["e"] == 0.997)
    r0 = PL.new_record(SIG, plan, [], False, "market")
    check("L2 no levels = the card as before", r0["card"]["sl"] == 0.99 and r0["card"]["tp2"] == 1.02 and r0["card"]["tp1"] is None and r0["status"] == "approving")
    # L3
    p = PL.approve_params(rp)
    check("L3 params: lv_entry / lv_sl, pending, pend_bars 3", p.get("lv_entry") == "0.997" and p.get("lv_sl") == "0.994" and "lv_tp2" not in p
          and p["entry_mode"] == "pending" and p["pend_bars"] == 3 and p["plan"] == 1, str(p))
    check("L3 params without levels: unchanged keys", set(PL.approve_params(r0)) == {"entry_mode", "size", "plan", "entry_pct", "tp2"})
    # L4
    arm, w = PL.validate_arm({"when": [{"type": "close", "tf": "h1", "side": "above", "px": 1.005}]})
    check("L4 a wait without a time limit gets 48 h", arm["cancel"] == [{"type": "hours", "h": 48.0}] and w, str(arm))
    check("L4 a bad tf is refused", "tf must be" in refused(PL.validate_arm, {"when": [{"type": "close", "tf": "d1", "side": "above", "px": 1}]}))
    check("L4 a bad side is refused", "side must be" in refused(PL.validate_arm, {"when": [{"type": "touch", "side": "up", "px": 1}]}))
    check("L4 no steps -> no wait", PL.validate_arm({"when": [], "cancel": [{"type": "hours", "h": 3}]})[0] is None)
    # L5
    bars = [[T0 + 60 * j, 1.0, 1.0 + (0.0002 if j == 70 else 0.0), 1.0 - (0.0003 if j == 30 else 0.0), 1.0 + (0.004 if j == 59 else 0.0)] for j in range(180)]
    tk = {"bid": 1.0, "ask": 1.0}
    cm = PL.cond_met
    check("L5 touch below at minute 30", cm({"type": "touch", "side": "below", "px": 0.9998}, bars, T0, T0 + 179 * 60, tk, 0) == T0 + 30 * 60)
    check("L5 touch from `since` on only", cm({"type": "touch", "side": "below", "px": 0.9998}, bars, T0 + 31 * 60, T0 + 179 * 60, tk, 0) is None)
    check("L5 an H1 close above: the first hour closed at 1.004 (its last minute) -> met at its close",
          cm({"type": "close", "tf": "h1", "side": "above", "px": 1.003}, bars, T0, T0 + 179 * 60, tk, 0) == T0 + 3600)
    check("L5 the forming hour does not count", cm({"type": "close", "tf": "h1", "side": "above", "px": 1.003}, bars[:100], T0 + 3600, T0 + 99 * 60, tk, 0) is None)
    check("L5 a bar that closed before `since` does not count", cm({"type": "close", "tf": "h1", "side": "above", "px": 1.003}, bars, T0 + 3601, T0 + 179 * 60, tk, 0) is None)
    t_after = C.parse_iso("2026-10-14T10:00:00Z"); srv = int(t_after.timestamp()) + 3 * 3600
    check("L5 after: UTC + the server offset", cm({"type": "after", "t": "2026-10-14T10:00:00Z"}, bars, srv - 500, srv + 5, tk, 3 * 3600) == srv
          and cm({"type": "after", "t": "2026-10-14T10:00:00Z"}, bars, srv - 500, srv - 5, tk, 3 * 3600) is None)
    check("L5 hours after since", cm({"type": "hours", "h": 2}, bars, T0, T0 + 7200, tk, 0) == T0 + 7200 and cm({"type": "hours", "h": 2}, bars, T0, T0 + 7199, tk, 0) is None)
    # L6 - approve() in a temp root
    root = tempfile.mkdtemp(); cfg = {"root": root, "server_offset_h": 3, "common_files": root}
    os.makedirs(os.path.join(root, "signals"))
    tasks = lambda: [json.load(open(f)) for f in sorted(glob.glob(os.path.join(root, "tasks", "*.json")))]
    ok, msg, rec = PL.approve(cfg, SIG, dict(PLAN, levels={"sl": 0.995}), False, "market")
    check("L6 levels refused on an EA without plan_levels", not ok and "cannot take custom levels" in msg, msg)
    sig2 = dict(SIG, plan_levels=True)
    ok, msg, rec = PL.approve(cfg, sig2, dict(PLAN, levels={"sl": 0.995, "tp2": 1.03}), False, "market")
    t = tasks()
    check("L6 with plan_levels: one approve task carrying lv_sl / lv_tp2", ok and len(t) == 1 and t[0]["verb"] == "approve" and t[0]["params"].get("lv_sl") == "0.995"
          and t[0]["params"].get("lv_tp2") == "1.03", msg)
    os.remove(PL.plan_path(cfg, SIG["signal_key"]))
    for f in glob.glob(os.path.join(root, "tasks", "*.json")): os.remove(f)
    arm_in = {"when": [{"type": "touch", "side": "below", "px": 0.9990}, {"type": "close", "tf": "h1", "side": "above", "px": 1.0020}],
              "cancel": [{"type": "touch", "side": "below", "px": 0.9950}, {"type": "hours", "h": 24}]}
    ok, msg, rec = PL.approve(cfg, sig2, dict(PLAN, arm=arm_in), False, "market")
    check("L6 an armed plan writes no task", ok and rec["status"] == "armed" and not tasks(), msg)
    ok2, msg2, _ = PL.approve(cfg, sig2, PLAN, False, "market")
    check("L6 an armed plan blocks a second approve", not ok2 and "disarm" in msg2, msg2)
    # L7 - the runner on a fake feed
    C.atomic_write_json(os.path.join(root, "signals", f"{SIG['signal_key']}.json"), dict(sig2))
    now = {"t": T0 + 10}
    path = []                                                   # minute bars [t, o, h, l, c, v, spread]
    def push(px_close, lo=None, hi=None):
        t = T0 + 60 * len(path); path.append([t, px_close, hi if hi is not None else px_close, lo if lo is not None else px_close, px_close, 0, 0.0]); now["t"] = t + 30
    mt5feed.tick = lambda sym: {"t": now["t"], "bid": path[-1][4] if path else 1.0, "ask": path[-1][4] if path else 1.0}
    mt5feed.spec = lambda sym: {"point": 1e-5, "digits": 5, "volume_min": 0.01, "volume_step": 0.01, "stops_level": 0}
    mt5feed.bars_sp = lambda sym, tf, t_from=None, t_to=None, n=None: ([b for b in path if b[0] >= (t_from or 0)] if n is None else path[-n:])
    sent = []
    run = PL.Runner(cfg, log=lambda m: None, notify=sent.append)
    def step():
        r = PL.load_plan(cfg, SIG["signal_key"]); ch = run.step(r)
        if ch: PL.save_plan(cfg, r)
        return r
    push(1.0); r = step()
    check("L7 watching", r["status"] == "armed" and r["arm_state"]["k"] == 0)
    for _ in range(65): push(1.0025)                            # an H1 close above 1.0020 BEFORE the touch: must not count yet
    while (path[-1][0] + 60) % 3600: push(1.0025)
    push(1.0025); r = step()
    check("L7 a close before the touch does not count (steps in order)", r["status"] == "armed" and r["arm_state"]["k"] == 0, str(r["arm_state"]))
    push(1.0, lo=0.9989); r = step()
    check("L7 step 1 met: the touch", r["arm_state"]["k"] == 1, str(r["arm_state"]))
    s_ = dict(sig2, implicit_streak=2); C.atomic_write_json(os.path.join(root, "signals", f"{SIG['signal_key']}.json"), s_)
    push(1.0); r = step()
    d_ = [x for x in tasks() if x["verb"] == "delay"]
    check("L7 a keep-alive delay when the next bar close would expire the signal", len(d_) == 1 and r["arm_state"]["delay_b"] is not None, str(len(d_)))
    push(1.0); r = step()
    check("L7 only one delay per bar", len([x for x in tasks() if x["verb"] == "delay"]) == 1)
    while (path[-1][0] + 120) % 3600: push(1.0010)
    push(1.0025)                                                # the hour's last minute closes at 1.0025
    push(1.0025); r = step()                                    # the next minute: that hour has closed
    ap = [x for x in tasks() if x["verb"] == "approve"]
    check("L7 the last step met -> the approve sent", r["status"] == "approving" and len(ap) == 1 and ap[0]["params"]["plan"] == 1 and ap[0]["issued_by"] == "plan",
          f"{r['status']} {r['arm_state']}")
    check("L7 lever hours re-anchored to the trigger bar", r["card"]["entry_bar_open"] == now["t"] - now["t"] % 14400)
    check("L7 the trader is told", any("the wait is over" in x for x in sent), str(sent))
    # L8 - cancel
    os.remove(PL.plan_path(cfg, SIG["signal_key"])); C.atomic_write_json(os.path.join(root, "signals", f"{SIG['signal_key']}.json"), dict(sig2))
    for f in glob.glob(os.path.join(root, "tasks", "*.json")): os.remove(f)
    run.bars.clear(); path.clear(); sent.clear()
    ok, msg, rec = PL.approve(cfg, sig2, dict(PLAN, arm=arm_in), False, "market")
    push(1.0); step(); push(0.9990, lo=0.9949); r = step()
    check("L8 a cancel level touched -> cancelled, nothing sent", r["status"] == "cancelled" and not tasks() and any("CANCELLED" in x for x in sent), r["status"])
    os.remove(PL.plan_path(cfg, SIG["signal_key"])); run.bars.clear(); path.clear()
    ok, msg, rec = PL.approve(cfg, sig2, dict(PLAN, arm=arm_in), False, "market")
    C.atomic_write_json(os.path.join(root, "signals", f"{SIG['signal_key']}.json"), dict(sig2, status="expired"))
    push(1.0); r = step()
    check("L8 the signal no longer open -> cancelled", r["status"] == "cancelled" and not tasks())
    # L9 - the advisor's plan
    blk = json.dumps({"schema": PL.ADVISOR_PLAN_SCHEMA, "rationale": "stop under the 0.9950 swing low; wait for the H1 close back above 1.0020",
                      "levels": {"entry": None, "sl": 0.9950, "tp1": None, "tp2": 1.0300},
                      "wait": {"when": [{"type": "close", "tf": "h1", "side": "above", "px": 1.0020}], "cancel": [{"type": "hours", "h": 24}]},
                      "entry": {"pct": 100}, "tp2": True, "levers": [{"type": "bank", "pct": 50, "at": 1.5, "tp1": False}]})
    a = PL.advisor_plan(blk, sig2)
    check("L9 a valid plan -> the builder's config with levels + wait", a["ok"] and a["cfg"]["levels"] == {"sl": 0.995, "tp2": 1.03} and a["cfg"]["arm"]["when"][0]["px"] == 1.002
          and a["cfg"]["levers"][0]["type"] == "bank" and len(a["about"]) == 3, str(a.get("error") or a["about"]))
    check("L9 on an EA without plan_levels it warns", any("cannot take custom levels" in x for x in PL.advisor_plan(blk, SIG)["warnings"]))
    check("L9 bad JSON refused", not PL.advisor_plan("{oops", sig2)["ok"])
    check("L9 a wrong-side stop refused", not PL.advisor_plan(json.dumps({"levels": {"sl": 1.01}, "levers": []}), sig2)["ok"])
    txt = "SUMMARY: WAIT — x\nVERDICT: WAIT (confidence: medium)\nWhy: y\nChanges my mind: z\n```plan\n" + blk + "\n```\nNotes: more\n"
    pv = VF.parse(txt)
    check("L9 the parser lifts the plan block out of the reply", pv["plan_text"] == blk and pv["notes"] == "more" and pv["verdict"] == "WAIT")
    pj = VF.parse(txt.replace("```plan", "```json"))
    check("L9 a ```json fence carrying the schema is taken too", pj["plan_text"] == blk)
    # L10 - a pending order (a custom entry) that expired: the EA marks only its row; the plan must end, not wait forever
    os.remove(PL.plan_path(cfg, SIG["signal_key"])); C.atomic_write_json(os.path.join(root, "signals", f"{SIG['signal_key']}.json"), dict(sig2, status="approved_pending"))
    for f in glob.glob(os.path.join(root, "tasks", "*.json")): os.remove(f)
    ok, msg, rec = PL.approve(cfg, sig2, dict(PLAN, levels={"entry": 0.997, "entry_h": 4}), False, "market")
    tid = rec["approve_task"]; os.makedirs(os.path.join(root, "acks"), exist_ok=True)
    C.atomic_write_json(os.path.join(root, "acks", f"{tid}.json"), {"task_id": tid, "result": "accepted", "reason": "ok", "refs": {"posid": 0}, "verb": "approve"})
    C.atomic_write_json(os.path.join(root, "state", "SIM.sim", "7.json"), {"decision": "approved_pending", "pend_bars": 1})
    mt5feed.positions = lambda: []
    r = step(); r = step()
    check("L10 a resting custom-entry order: waiting for the fill", r["status"] == "waiting_fill", r["status"])
    C.atomic_write_json(os.path.join(root, "state", "SIM.sim", "7.json"), {"decision": "expired", "pend_bars": 1})
    r = step()
    check("L10 it expired (marked on the EA's row) -> the plan ends", r["status"] == "done" and "expired unfilled after 1 H4 bars" in r["log"][-1]["msg"], r["status"])
    print("RESULT:", "PASS" if OK else "FAIL")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
