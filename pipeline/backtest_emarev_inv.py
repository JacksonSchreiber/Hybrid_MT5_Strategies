#!/usr/bin/env python3
"""backtest_emarev_inv.py — EMArev-Inverse ("ride the stretch") campaign.

Spec: docs/strategies/emarev-inverse-spec.md. The EA (InpUseEmaRevInv, default OFF)
LOGS the frozen-EMArev signal stream inverted (no trades) + dumps H4 bars; this driver
runs one tester pass per symbol to collect them, then simulates the SIX cells
(entry E1/E2 x exit X1/X2/X3) in Python from the H4 bars, and reports the 6-cell table
with subsets (SHOCK/ALL/scheduled) and both holdouts (symbol + 2023-24 date).

Stages:  collect  — run 4 symbols, gather signals + h4 bars into data/backtests/emarev_inv/
         report   — simulate 6 cells from collected data + acceptance evaluation
         (default runs collect then report)

Intrabar rule (H4): when one H4 bar contains both the stop and the target, the STOP is
taken first (conservative). X1 gets an M1 sensitivity check on EUR/GBP separately.
"""
from __future__ import annotations
import csv, os, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COMMON = Path("/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files")
JDIR = COMMON / "journal"
RESULTS = REPO / "data" / "backtests" / "emarev_inv"
RESULTS.mkdir(parents=True, exist_ok=True)

SYMBOLS = {  # symbol -> (from, to, symbol_group)   full available .dk range
    "EURUSD.dk": ("2016.08.01", "2025.12.31", "fit"),
    "GBPUSD.dk": ("2013.08.01", "2024.12.31", "fit"),
    "USDJPY.dk": ("2020.01.01", "2025.12.31", "holdout"),
    "XAUUSD.dk": ("2020.01.01", "2025.12.31", "holdout"),
}
DATE_HOLDOUT = (2023, 2024)
BUF_ATR = 0.10          # README sl_buffer_atr (spread not reconstructable in Python)
CAL_V_BLOCK_H = 6.0     # forward V <= this -> no arm
TIME_EXIT_BARS = 12     # X2
TRAIL_ATR = 1.5         # X3 trail distance
TRAIL_ARM_R = 0.25      # X3 arms once >= this R
X3_MAX_BARS = 120       # safety horizon for the trail
FTMO_DAILY_R = 5.0
ENTRIES = ["E1", "E2"]
EXITS = ["X1", "X2", "X3"]


# ------------------------------------------------------------- collect
def run_symbol(sym, frm, to):
    (JDIR / "emarev_inv_signals.csv").unlink(missing_ok=True)
    base = sym.split(".")[0]
    cmd = [str(HERE / "mt5_verify.sh"), "--mode", "SKIP", "--strat", "EmaRevInv",
           "--symbol", sym, "--from", frm, "--to", to, "--model", "1", "--timeout", "5400"]
    print(f"  RUN {sym} {frm}..{to} ...", flush=True)
    subprocess.run(cmd, env=dict(os.environ), capture_output=True, text=True, timeout=6000)
    # collect signals (shared file, truncated per run) + the per-symbol h4 dump
    for src, dst in [(JDIR / "emarev_inv_signals.csv", RESULTS / f"{sym}.signals.csv"),
                     (JDIR / f"h4bars_{base}.csv", RESULTS / f"{sym}.h4.csv")]:
        for _ in range(60):
            if src.exists():
                break
            time.sleep(0.5)
        if src.exists():
            dst.write_text(src.read_text(encoding="utf-8", errors="replace"))
    ns = sum(1 for _ in open(RESULTS / f"{sym}.signals.csv")) - 1 if (RESULTS / f"{sym}.signals.csv").exists() else 0
    nb = sum(1 for _ in open(RESULTS / f"{sym}.h4.csv")) - 1 if (RESULTS / f"{sym}.h4.csv").exists() else 0
    print(f"    -> {ns} signals, {nb} H4 bars")


def stage_collect():
    print("=== STAGE collect: log EMArev-inverse signals + H4 bars (4 symbols) ===")
    for sym, (frm, to, _) in SYMBOLS.items():
        run_symbol(sym, frm, to)


# ------------------------------------------------------------- load
def _dt(s):
    for f in ("%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), f)
        except ValueError:
            pass
    return None


def load_signals(sym):
    p = RESULTS / f"{sym}.signals.csv"
    out = []
    if not p.exists():
        return out
    for r in csv.DictReader(open(p)):
        t = _dt(r["signal_time"])
        if not t:
            continue
        out.append({"t": t, "symbol": sym, "dir": int(r["inv_dir"]),
                    "ema": float(r["ema20"]), "atr": float(r["atr_h4"]),
                    "ext": float(r["extreme"]), "tclose": float(r["trigger_close"]),
                    "cal_v": float(r["cal_v_hours"]), "w_hold": int(r["w_in_hold"]),
                    "shock": int(r["shock_flag"]), "sched": int(r["sched"])})
    return out


def load_bars(sym):
    p = RESULTS / f"{sym}.h4.csv"
    bars = []
    if not p.exists():
        return bars
    for r in csv.DictReader(open(p)):
        t = _dt(r["time"])
        if t:
            bars.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    bars.sort()
    return bars


def load_econ():
    """(t, ccy, class) for V/W/C rows — used to re-derive the calendar flags in Python
    (the EA's econ reader had an off-by-one that zeroed them; re-derive here so the gate
    + scheduled subset are correct without a re-collect)."""
    p = COMMON / "econ_events.csv"
    ev = []
    if not p.exists():
        return ev
    for r in csv.DictReader(open(p, errors="replace")):
        t = _dt((r.get("datetime_utc") or "").strip())
        cls = (r.get("class") or "").strip().upper()
        if t and cls in ("V", "W", "C"):
            ev.append((t, (r.get("ccy") or "").strip(), cls))
    ev.sort()
    return ev


def recompute_flags(sig, econ):
    base, quote = sig["symbol"][:3], sig["symbol"][3:6]
    t = sig["t"]

    def hits(a, b, classes):
        return [e for e in econ if a < e[0] <= b and e[2] in classes
                and (e[1] in (base, quote) or e[1] == "All")]
    fv = [e for e in econ if t < e[0] <= t + timedelta(hours=12) and e[2] == "V"
          and (e[1] in (base, quote) or e[1] == "All")]
    sig["cal_v"] = min(((e[0] - t).total_seconds() / 3600.0 for e in fv), default=999.0)
    sig["w_hold"] = 1 if hits(t, t + timedelta(days=3), ("W",)) else 0
    sched = [e for e in econ if t - timedelta(days=3) <= e[0] <= t and e[2] in ("V", "C")
             and (e[1] in (base, quote) or e[1] == "All")]
    sig["sched"] = 1 if sched else 0


def held_weekend(a, b):
    d = a
    while d <= b:
        if d.weekday() == 5:
            return True
        d += timedelta(days=1)
    return False


# ------------------------------------------------------------- simulate one cell
def sim(sig, bars, entry_v, exit_v):
    """Return dict(r, ...) or None if the cell produced no trade (E2 unfilled)."""
    d, atr, ema, ext, tclose = sig["dir"], sig["atr"], sig["ema"], sig["ext"], sig["tclose"]
    buf = BUF_ATR * atr
    # forward bars strictly after the signal bar
    i0 = next((i for i, b in enumerate(bars) if b[0] > sig["t"]), None)
    if i0 is None:
        return None
    fwd = bars[i0:]
    if not fwd:
        return None
    # entry
    if entry_v == "E1":
        entry = tclose
        start = 0
    else:  # E2 buy/sell-stop at extreme +/- buffer, valid 6 bars
        lvl = ext + buf if d > 0 else ext - buf
        start = None
        for i in range(min(6, len(fwd))):
            _, o, hi, lo, c = fwd[i]
            if (d > 0 and hi >= lvl) or (d < 0 and lo <= lvl):
                start = i + 1
                entry = lvl
                break
        if start is None:
            return None  # cancelled unfilled
    # EMA-stop with cap (2.5 ATR) and degenerate floor (1.0 ATR)
    ema_dist = abs(entry - ema)
    risk = 1.0 * atr if ema_dist < 1.0 * atr else min(ema_dist + buf, 2.5 * atr)
    sl = entry - risk if d > 0 else entry + risk
    tp = None
    if exit_v == "X1":
        tp = ext + 1.0 * atr if d > 0 else ext - 1.0 * atr

    fav = entry            # favorable extreme (for trail)
    trail_on = False
    exit_price = None
    exit_reason = "open_end"
    exit_time = fwd[-1][0]
    slip = 0.0
    horizon = (TIME_EXIT_BARS if exit_v == "X2" else X3_MAX_BARS if exit_v == "X3" else len(fwd))
    for k in range(start, min(len(fwd), start + horizon) if exit_v != "X1" else len(fwd)):
        t, o, hi, lo, c = fwd[k]
        # gap-through stop at bar open?
        if (d > 0 and o <= sl) or (d < 0 and o >= sl):
            exit_price, exit_reason, exit_time = o, "stop_gap", t
            slip = max(0.0, (sl - o) / risk if d > 0 else (o - sl) / risk)
            break
        # conservative: stop before target within a bar
        stop_hit = (lo <= sl) if d > 0 else (hi >= sl)
        tp_hit = (tp is not None) and ((hi >= tp) if d > 0 else (lo <= tp))
        if stop_hit:
            exit_price, exit_reason, exit_time = sl, "stop", t
            break
        if tp_hit:
            exit_price, exit_reason, exit_time = tp, "tp", t
            break
        # update favorable extreme + trail
        fav = max(fav, hi) if d > 0 else min(fav, lo)
        if exit_v == "X3":
            unreal = (fav - entry) / risk if d > 0 else (entry - fav) / risk
            if not trail_on and unreal >= TRAIL_ARM_R:
                trail_on = True
            if trail_on:
                tstop = fav - TRAIL_ATR * atr if d > 0 else fav + TRAIL_ATR * atr
                eff = max(sl, tstop) if d > 0 else min(sl, tstop)
                if (d > 0 and lo <= eff) or (d < 0 and hi >= eff):
                    exit_price, exit_reason, exit_time = eff, "trail", t
                    break
        if exit_v == "X2" and k == start + TIME_EXIT_BARS - 1:
            exit_price, exit_reason, exit_time = c, "time", t
            break
    if exit_price is None:
        exit_price, exit_time = fwd[min(len(fwd) - 1, start)][4], fwd[min(len(fwd) - 1, start)][0]
    r = (exit_price - entry) / risk if d > 0 else (entry - exit_price) / risk
    return {"r": r, "reason": exit_reason, "slip": slip,
            "wknd": held_weekend(sig["t"], exit_time), "t": sig["t"], "symbol": sig["symbol"],
            "shock": sig["shock"], "sched": sig["sched"], "year": sig["t"].year}


def gated(sig):
    return sig["cal_v"] <= CAL_V_BLOCK_H or sig["w_hold"] == 1


# ------------------------------------------------------------- metrics
def metrics(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0}
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    ordered = sorted(trades, key=lambda t: t["t"])
    mc = cur = 0
    for t in ordered:
        cur = cur + 1 if t["r"] <= 0 else 0
        mc = max(mc, cur)
    wknd = [t for t in trades if t["wknd"]]
    wslip = sum(t["slip"] for t in wknd)
    byday = {}
    for t in ordered:
        byday.setdefault(t["t"].date(), 0.0)
        byday[t["t"].date()] += t["r"]
    breaches = sum(1 for v in byday.values() if v <= -FTMO_DAILY_R)
    return {"n": n, "wr": 100.0 * wins / n, "avg_r": sum(rs) / n, "total_r": sum(rs),
            "mcl": mc, "wknd_pct": 100.0 * len(wknd) / n, "wslip": wslip, "ftmo": breaches}


def fmt(m):
    if m.get("n", 0) == 0:
        return "n=0"
    return (f"n={m['n']:4d} WR={m['wr']:3.0f}% avgR={m['avg_r']:+.3f} totR={m['total_r']:+6.1f} "
            f"mCL={m['mcl']:2d} wknd={m['wknd_pct']:3.0f}% wSlipR={m['wslip']:.1f} ftmo={m['ftmo']}")


# ------------------------------------------------------------- report + acceptance
def stage_report():
    # collect trades: {(entry,exit): {symbol: [trades]}} for gate-passing signals; SHOCK subset flagged
    sigs = {s: load_signals(s) for s in SYMBOLS}
    bars = {s: load_bars(s) for s in SYMBOLS}
    econ = load_econ()                       # re-derive calendar flags (EA reader bug)
    for s in SYMBOLS:
        for sg in sigs[s]:
            recompute_flags(sg, econ)
    total_sigs = sum(len(v) for v in sigs.values())
    gated_n = sum(1 for v in sigs.values() for sg in v if gated(sg))
    cells = {}
    for e in ENTRIES:
        for x in EXITS:
            per = {s: [] for s in SYMBOLS}
            for s in SYMBOLS:
                for sg in sigs[s]:
                    if gated(sg):
                        continue
                    tr = sim(sg, bars[s], e, x)
                    if tr:
                        per[s].append(tr)
            cells[(e, x)] = per

    L = ["# EMArev-Inverse — 6-cell campaign report",
         f"_Generated {datetime.now():%Y-%m-%d %H:%M}. Model-1 H4 bars, conservative intrabar "
         "(stop-first). {} EMArev signals total; {} gated out (fwd V<=6h or W-in-hold); "
         "traded set excludes gated._".format(total_sigs, gated_n), "",
         "Spec: [emarev-inverse-spec.md](../../docs/strategies/emarev-inverse-spec.md). "
         "Subsets: **SHOCK** (hypothesis) / ALL (control) / scheduled. Holdouts: symbol "
         "(fit EUR+GBP / hold JPY+XAU) + date (2023-24).", ""]

    def block(title, filt):
        L.append(f"## {title}")
        L.append("| cell | " + " | ".join(SYMBOLS) + " | symFIT | symHOLD | date23-24 | ALLcell |")
        L.append("|" + "---|" * (len(SYMBOLS) + 5))
        for e in ENTRIES:
            for x in EXITS:
                per = cells[(e, x)]
                sel = {s: [t for t in per[s] if filt(t)] for s in SYMBOLS}
                allt = [t for s in SYMBOLS for t in sel[s]]
                fit = [t for s in SYMBOLS if SYMBOLS[s][2] == "fit" for t in sel[s]]
                hold = [t for s in SYMBOLS if SYMBOLS[s][2] == "holdout" for t in sel[s]]
                dh = [t for t in allt if t["year"] in DATE_HOLDOUT]
                cellsum = lambda ts: (f"{metrics(ts)['avg_r']:+.2f}/{metrics(ts)['n']}"
                                      if metrics(ts).get("n") else "·")
                row = [f"**{e}·{x}**"] + [cellsum(sel[s]) for s in SYMBOLS]
                row += [cellsum(fit), cellsum(hold), cellsum(dh), fmt(metrics(allt))]
                L.append("| " + " | ".join(row) + " |")
        L.append("")

    block("SHOCK subset (the hypothesis)  — avgR/n per cell", lambda t: t["shock"] == 1)
    block("ALL (control)  — avgR/n per cell", lambda t: True)
    block("Scheduled stretch-maker (informational)", lambda t: t["sched"] == 1)

    # acceptance evaluation on the SHOCK subset
    L.append("## Acceptance (SHOCK subset, pre-registered)")
    L.append("Pass a cell iff: n>=60, avgR>=+0.25, WR>=30%, same sign symfit&symhold, "
             "date23-24 avgR>=0, positive in >=3/4 symbols; AND the same exit under the "
             "OTHER entry shows avgR>=0 (robustness floor).")
    passes = []
    cellm = {}
    for e in ENTRIES:
        for x in EXITS:
            sh = {s: [t for t in cells[(e, x)][s] if t["shock"] == 1] for s in SYMBOLS}
            allt = [t for s in SYMBOLS for t in sh[s]]
            m = metrics(allt)
            fit = metrics([t for s in SYMBOLS if SYMBOLS[s][2] == "fit" for t in sh[s]])
            hold = metrics([t for s in SYMBOLS if SYMBOLS[s][2] == "holdout" for t in sh[s]])
            dh = metrics([t for t in allt if t["year"] in DATE_HOLDOUT])
            possym = sum(1 for s in SYMBOLS if metrics(sh[s]).get("n") and metrics(sh[s])["avg_r"] > 0)
            cellm[(e, x)] = m
            ok = (m.get("n", 0) >= 60 and m.get("avg_r", -9) >= 0.25 and m.get("wr", 0) >= 30
                  and fit.get("n") and hold.get("n") and (fit["avg_r"] > 0) == (hold["avg_r"] > 0)
                  and fit["avg_r"] > 0 and dh.get("avg_r", -9) >= 0.0 and possym >= 3)
            L.append(f"- {e}·{x}: {fmt(m)} | symfit {fit.get('avg_r',0):+.2f} symhold "
                     f"{hold.get('avg_r',0):+.2f} date23-24 {dh.get('avg_r',0):+.2f} "
                     f"pos {possym}/4 → {'PASS-primary' if ok else 'fail'}")
            if ok:
                passes.append((e, x))
    # robustness floor
    final = []
    for (e, x) in passes:
        other = "E2" if e == "E1" else "E1"
        if cellm[(other, x)].get("avg_r", -9) >= 0.0:
            final.append((e, x))
    L.append("")
    if final:
        L.append(f"### VERDICT: V2 CANDIDATE — cell(s) {', '.join(f'{e}·{x}' for e,x in final)} "
                 "clear all bars incl. the cross-entry robustness floor.")
    else:
        L.append("### VERDICT: REJECTED — no SHOCK-subset cell clears the acceptance bars. "
                 "File the rejection appendix on shock-continuation-result.md; keep the harness; stop.")
    (RESULTS / "REPORT.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nreport -> {RESULTS/'REPORT.md'}")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("all", "collect"):
        stage_collect()
    if stage in ("all", "report"):
        stage_report()
