"""plan_live_sim.py - trader 2026-10-12 (docs/live_plans.md): drive the live plan RUNNER (live/plan_live.Runner) minute by minute through
box frames against a fake broker + fake EA (tasks executed at the next minute's open, broker stops / TPs checked on each minute's range),
and compare the realised R with the Practice kernel's R on the same frame. BUY and a mirrored SELL of the same path must agree (sign
handling). Differences come only from execution timing (the runner acts on a minute after it closes / its level was touched; the kernel
fills at the level). Lot step 0.0001 on 1.0 full lots, so rounding is negligible.
  python3 pipeline/plan_live_sim.py [--data data/backtest] [--frames 30] [--maxn 2500]"""
from __future__ import annotations
import argparse, json, os, sys, time, types
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import backtest_engine as BE
from live import plan_live as PL
from live import common as C
from live import mt5feed

R_ = 0.01; E_ = 1.0; T0 = 1_790_000_000 - 1_790_000_000 % 14400

PLANS = {
    "live_rules_bloc": {"entry": {"pct": 100}, "tp2": True, "levers": [{"type": "bank", "pct": 25, "at": 1.0, "tp1": True}, {"type": "move", "to": 0.0, "when": 1.0, "tp1": True},
                        {"type": "cut_unless", "at_h": 24, "reach": 1.5}, {"type": "move", "to": 1.0, "when": 1.5}]},
    "avg_cuts_trail": {"entry": {"pct": 100}, "tp2": False, "levers": [{"type": "cut_avg", "at_h": 1, "below": 0}, {"type": "cut_avg", "at_h": 5, "below": 0},
                       {"type": "trail", "every_h": 1, "dist": 1.0, "lower": False}, {"type": "bank", "pct": 50, "at": 1.5}]},
    "adds_capped": {"entry": {"pct": 50}, "tp2": True, "levers": [{"type": "add_bar", "pct": 50, "bar": 2, "early_at": 0.75, "hold_avg": None, "stop": "trade", "risk_cap": 1.0},
                    {"type": "add_reach", "pct": 50, "at": 1.5, "within_h": 48, "stop": 0}, {"type": "move", "to": 0.25, "when": 1.0, "tp1": True}]},
    "trail_lower": {"entry": {"pct": 100}, "tp2": True, "levers": [{"type": "trail", "every_h": 2, "dist": 0.75, "lower": True}]},
}


class Sim:
    """one trade: the fake terminal (bars, tick, positions, spec) and the fake EA (tasks -> acks) the runner talks to."""
    def __init__(self, F, ctx, sell: bool):
        self.n = F["n"]; tm = ctx["tm"]; self.t = T0 + np.asarray(tm, dtype=np.int64) * 60
        sp = np.asarray(F["eo"]) * R_; bo, bh, bl, bc = (np.asarray(F[k]) * R_ + E_ for k in ("xo", "xh", "xl", "xc"))   # up frames: x* = the bid
        if not sell: self.o, self.h, self.l, self.c = bo, bh, bl, bc
        else: self.o, self.h, self.l, self.c = 2 - (bo + sp), 2 - (bl + sp), 2 - (bh + sp), 2 - (bc + sp)
        self.sp = sp; self.d = -1 if sell else 1; self.j = 0
        self.h4 = [int(T0 + o * 60) for o in ctx["off"] if o is not None]
        self.pos = {}; self.closed = {}; self.next_id = 1000; self.tasks = []; self.acks = {}; self.pnl = 0.0; self.full = 1.0
    def bid(self, j): return self.o[j]
    def ask(self, j): return self.o[j] + self.sp[j]
    def open_leg(self, j, lots, sl, tp):
        pid = self.next_id; self.next_id += 1
        px = self.ask(j) if self.d > 0 else self.bid(j)
        self.pos[pid] = {"ticket": pid, "symbol": "SIM", "volume": lots, "sl": sl, "tp": tp, "price_open": px, "time": int(self.t[j])}
        return pid, px
    def close_vol(self, pid, vol, px):
        p = self.pos[pid]; self.pnl += self.d * (px - p["price_open"]) * vol / R_ / self.full
        p["volume"] = round(p["volume"] - vol, 6)
        if p["volume"] <= 1e-9: self.closed[pid] = self.pos.pop(pid)
    def exit_px(self, j): return self.bid(j) if self.d > 0 else self.ask(j)
    def minute(self, j):
        """broker events inside minute j: tasks first (at the open), then stops / TPs on the range (stop first)."""
        for t in self.tasks: self.exec(t, j)
        self.tasks = []
        for pid in list(self.pos):
            p = self.pos[pid]
            lo_x = self.l[j] if self.d > 0 else self.h[j] + self.sp[j]          # exit-side extreme against the trade
            hi_x = self.h[j] if self.d > 0 else self.l[j] + self.sp[j]
            ox = self.exit_px(j)
            if p["sl"] and (self.d * (lo_x - p["sl"]) <= 0):
                px = p["sl"] if self.d * (ox - p["sl"]) > 0 else ox; self.close_vol(pid, p["volume"], px); continue
            if p["tp"] and (self.d * (hi_x - p["tp"]) >= 0):
                self.close_vol(pid, p["volume"], p["tp"])
    def exec(self, t, j):
        v, pid, pr = t["verb"], t.get("position_id"), t["params"]; refs = {}; res, why = "accepted", "ok"
        if v == "close":
            if pid in self.pos: self.close_vol(pid, self.pos[pid]["volume"], self.exit_px(j))
            else: res, why = "rejected", "position_closed"
        elif v == "close_part":
            if pid in self.pos: self.close_vol(pid, min(pr["lots"], self.pos[pid]["volume"]), self.exit_px(j))
            else: res, why = "rejected", "position_closed"
        elif v == "set_sl":
            if pid not in self.pos: res, why = "rejected", "position_closed"
            else:
                ox = self.exit_px(j)
                if self.d * (ox - pr["sl"]) <= 0: res, why = "rejected", "sl_through_price"
                else: self.pos[pid]["sl"] = pr["sl"]
        elif v == "plan_add":
            npid, px = self.open_leg(j, pr["lots"], pr["sl"], pr["tp"] or 0)
            refs = {"posid": npid, "lots": pr["lots"], "entry": px, "sl": pr["sl"]}
        self.acks[t["task_id"]] = {"task_id": t["task_id"], "verb": v, "result": res, "reason": why, "refs": refs}
    def bars(self, j0, j1):
        return [[int(self.t[k]), self.o[k], self.h[k], self.l[k], self.c[k], 0, self.sp[k]] for k in range(j0, j1)]


def patch(sim: Sim):
    def bars_sp(sym, tf, t_from=None, t_to=None, n=None):
        j = sim.j
        if tf == "h4": return [[x, 0, 0, 0, 0, 0, 0] for x in sim.h4 if x <= sim.t[j] and (t_from is None or x >= t_from)]
        if n is not None: return sim.bars(max(0, j + 1 - n), j + 1)
        k0 = int(np.searchsorted(sim.t, t_from)); return sim.bars(k0, j + 1)
    mt5feed.bars_sp = bars_sp
    mt5feed.positions = lambda: [dict(p) for p in sim.pos.values()]
    mt5feed.tick = lambda sym: {"t": int(sim.t[sim.j]) + 30, "bid": sim.c[sim.j], "ask": sim.c[sim.j] + sim.sp[sim.j]}
    mt5feed.spec = lambda sym: {"point": 1e-5, "digits": 5, "volume_min": 0.0001, "volume_step": 0.0001, "stops_level": 0}
    def write_task(cfg, symbol, verb, params, signal_id=None, position_id=None, issued_by="web"):
        tid = f"t{len(sim.acks) + len(sim.tasks)}_{time.time_ns()}"; sim.tasks.append({"task_id": tid, "verb": verb, "params": params, "position_id": position_id}); return tid
    C.write_task = write_task
    C.ack = lambda cfg, tid: sim.acks.get(tid) if tid != "approve" else {"result": "accepted", "refs": {"posid": 999}}
    C.position = lambda cfg, key: {"entry_mode": "plan_market", "opened_at": sim.pos.get(999, sim.closed.get(999, {})).get("time"), "entry": sim.pos.get(999, sim.closed.get(999, {})).get("price_open"), "full_lots": 1.0}
    C.list_positions = lambda cfg, closed=False: []


def run_one(F, ctx, plan, sell):
    sim = Sim(F, ctx, sell); patch(sim)
    cfg = {"root": "/nonexistent"}
    pl, _ = PL.live_validate(plan)
    pl.pop("risk_cap", None) if not plan.get("risk_cap") and not any(c["type"].startswith("add") for c in plan["levers"]) else None
    sig = {"signal_key": "SIM-1", "symbol": "SIM", "signal_id": 1, "strategy": "TrendCont", "direction": "SELL" if sell else "BUY",
           "levels": {"entry": E_ if not sell else E_, "sl": E_ - R_ if not sell else E_ + R_, "tp1": (E_ + ctx["tp1"] * R_) if (ctx["tp1"] and not sell) else ((E_ - ctx["tp1"] * R_) if ctx["tp1"] else None),
                      "tp2": (E_ + ctx["tp2"] * R_) if not sell else (E_ - ctx["tp2"] * R_), "two_target": bool(ctx["tp1"])},
           "decision_bar_server": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0)), "sizing": {"lots": 1.0}}
    if sell:   # the mirrored path: the SELL card's entry / levels mirror round E_ (prices 2 - x), so its R values equal the BUY's
        sig["levels"] = {"entry": 2 - E_, "sl": 2 - (E_ - R_), "tp1": (2 - (E_ + ctx["tp1"] * R_)) if ctx["tp1"] else None, "tp2": 2 - (E_ + ctx["tp2"] * R_), "two_target": bool(ctx["tp1"])}
    rec = PL.new_record(sig, pl, [], False, "market"); rec["approve_task"] = "approve"
    # the first leg at minute 0's open, sized entry% of 1.0 lots, the signal stop, TP2 when on
    sim.full = 1.0
    pid, px = sim.open_leg(0, pl["entry"]["pct"] / 100.0, rec["card"]["sl"], rec["card"]["tp2"] if pl["tp2"] else 0)
    sim.pos[999] = sim.pos.pop(pid); sim.pos[999]["ticket"] = 999
    run = PL.Runner(cfg, log=lambda m: None)
    PL.save_plan = lambda cfg, rec: None
    for j in range(sim.n):
        sim.j = j
        if j > 0: sim.minute(j)
        else:   # minute 0: only the stop / TP check after the entry at the open
            sim.tasks = []; sim.minute(0)
        if not sim.pos and not sim.tasks and j > 0: break
        if rec.get("status") in PL.FINAL: break
        run.step(rec)
    # anything still open at the frame end closes at the last close
    for pid in list(sim.pos): sim.close_vol(pid, sim.pos[pid]["volume"], sim.c[min(sim.j, sim.n - 1)] + (sim.sp[sim.j] if sell else 0))
    # the kernel on the SAME live frame the runner replays (its own direction, prices and fill): the reference for this direction
    F2, ctx2 = PL.frame(sim.bars(0, sim.n) + [sim.bars(sim.n - 1, sim.n)[0][:1] + [sim.c[-1]] * 4 + [0, sim.sp[-1]]], rec,
                        int(sim.t[0]), rec["fill"]["px"], sim.h4)
    ctx2["tm"][-1] = ctx2["tm"][-2] + 1; ctx2["t_next"] = int(ctx2["tm"][-1])        # a closing dummy minute: every real minute counts as closed
    r_k = float(BE.run(BE.kern, F2, ctx2, BE.compile_cfg(pl, ctx2))[0])
    return sim.pnl, rec, r_k


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data/backtest"); ap.add_argument("--frames", type=int, default=30); ap.add_argument("--maxn", type=int, default=2500)
    a = ap.parse_args()
    fr = BE.Frames(os.path.join(a.data, "frames_v1"))
    ids = [h for h in sorted(fr.idx) if fr.idx[h]["up"] and fr.idx[h]["n"] <= a.maxn]
    rng = np.random.default_rng(7); ids = list(rng.choice(ids, size=min(a.frames, len(ids)), replace=False))
    rows = []; t0 = time.time()
    for k, hid in enumerate(ids):
        F, ctx = fr.get(hid)
        for name, plan in PLANS.items():
            cfg = PL.live_validate(plan)[0]
            r_b, rec_b, kb = run_one(F, ctx, plan, False)
            r_s, rec_s, ks = run_one(F, ctx, plan, True)
            rows.append((hid, name, kb, r_b, ks, r_s))
            flag = "  <-- far from the kernel" if max(abs(r_b - kb), abs(r_s - ks)) > 0.35 else ""
            if flag: print(f"{hid} {name}: BUY kernel {kb:+.3f} runner {r_b:+.3f} | SELL kernel {ks:+.3f} runner {r_s:+.3f}{flag}", flush=True)
        if k % 5 == 0: print(f"{k + 1}/{len(ids)} frames ({time.time() - t0:.0f}s)", flush=True)
    a_ = np.array([[r[2], r[3], r[4], r[5]] for r in rows])
    for nm, k, s in (("BUY", 0, 1), ("SELL", 2, 3)):
        dd = np.abs(a_[:, s] - a_[:, k])
        print(f"{nm}: {len(rows)} trades, mean kernel {a_[:, k].mean():+.3f} runner {a_[:, s].mean():+.3f}; |runner-kernel| median {np.median(dd):.3f} "
              f"p90 {np.percentile(dd, 90):.3f} max {dd.max():.3f}")
    print("DONE")


if __name__ == "__main__": main()
