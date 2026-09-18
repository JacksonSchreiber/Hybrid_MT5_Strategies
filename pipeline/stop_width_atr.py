#!/usr/bin/env python3
r"""
stop_width_atr.py - §10.2 stop-width measurement: median stop distance in ATR(14) units, per symbol and per detector,
over every graded journal + parked AA baseline. Windows-side script: attaches READ-ONLY to the ALREADY-RUNNING local
terminal (never launches one - it aborts if no interactive terminal64 is present, and again if the process count grew)
and pulls .dk H4 history to compute ATR at each signal bar.
  python.exe pipeline\stop_width_atr.py --out data\study\stop_width_atr.json
"""
import argparse, csv, glob, json, os, statistics as st, subprocess, sys
from datetime import datetime, timedelta, timezone

JDIR = r"C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\Common\Files\journal"
TERM = r"C:\Program Files\OANDA MetaTrader 5\terminal64.exe"

def interactive_terminals() -> int:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if "terminal64.exe" in line.lower())

def atr_series(rates, n=14):
    """Wilder ATR over MT5 rates (ascending). Returns {bar_time: atr_at_that_bar_computed_from_PRIOR_bars}."""
    out = {}; prev_close = None; tr = []; atr = None
    for b in rates:
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        t = int(b["time"])
        if prev_close is not None:
            out[t] = atr           # ATR as known at the OPEN of this bar (prior bars only) - what the EA sizes from
            cur = max(h - l, abs(h - prev_close), abs(l - prev_close))
            tr.append(cur)
            if atr is None:
                if len(tr) >= n: atr = sum(tr[-n:]) / n
            else: atr = (atr * (n - 1) + cur) / n
        else:
            out[t] = None; tr.append(h - l)
        prev_close = c
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=r"data\study\stop_width_atr.json"); ap.add_argument("--journals", default=JDIR)
    a = ap.parse_args()
    before = interactive_terminals()
    if before == 0: sys.exit("no terminal64 running - start MT5 first (this script never launches one)")
    import MetaTrader5 as m
    if not m.initialize(path=TERM): sys.exit(f"initialize failed: {m.last_error()}")
    if interactive_terminals() > before:
        m.shutdown(); sys.exit("a NEW terminal was launched - aborting (read-only attach only)")
    rows = {}
    srcs = sorted(glob.glob(os.path.join(a.journals, "baselines", "*_AA_ALL.csv"))) + \
           [p for p in sorted(glob.glob(os.path.join(a.journals, "*.csv")))
            if not any(x in os.path.basename(p) for x in (".actions.", ".delays.", ".costed.", ".inv.", ".part."))]
    for p in srcs:
        try: rr = list(csv.DictReader(open(p, encoding="ascii", errors="replace", newline="")))
        except OSError: continue
        for r in rr:
            if r.get("decision") not in ("approved", "approved_pending"): continue
            try:
                e = float(r["orig_entry"]); s = float(r["orig_sl"])           # the DETECTOR's stop, before any edit
                t = datetime.strptime(r["signal_time"], "%Y.%m.%d %H:%M:%S")
            except Exception: continue
            if e <= 0 or s <= 0: continue
            rows.setdefault(r["symbol"], []).append((t, abs(e - s), r["strategy"], os.path.basename(p)))
    res = {}
    for sym, items in sorted(rows.items()):
        m.symbol_select(sym, True)
        lo = min(t for t, *_ in items) - timedelta(days=40); hi = max(t for t, *_ in items) + timedelta(days=2)
        rates = m.copy_rates_range(sym, m.TIMEFRAME_H4, lo, hi)
        if rates is None or len(rates) == 0:
            print(f"{sym}: no H4 history ({m.last_error()})"); continue
        atrs = atr_series(rates)
        keys = sorted(atrs)
        vals = []
        for t, stop, det, src in items:
            ts = int(t.replace(tzinfo=timezone.utc).timestamp())
            k = None
            for kk in reversed(keys):                      # the bar the signal fired on (its open <= signal time)
                if kk <= ts: k = kk; break
            atr = atrs.get(k) if k else None
            if not atr or atr <= 0: continue
            vals.append((stop / atr, det))
        if not vals: print(f"{sym}: no ATR matches"); continue
        per_det = {}
        for d in sorted({d for _, d in vals}):
            v = sorted(x for x, dd in vals if dd == d)
            per_det[d] = {"n": len(v), "median_atr": round(st.median(v), 3), "p25": round(v[len(v)//4], 3), "p75": round(v[3*len(v)//4], 3)}
        allv = sorted(x for x, _ in vals)
        res[sym] = {"n": len(allv), "median_atr": round(st.median(allv), 3), "p25": round(allv[len(allv)//4], 3),
                    "p75": round(allv[3*len(allv)//4], 3), "min": round(allv[0], 3), "max": round(allv[-1], 3), "per_detector": per_det}
        print(f"{sym:12s} n={len(allv):5d} median={res[sym]['median_atr']:.3f} ATR  p25={res[sym]['p25']:.3f} p75={res[sym]['p75']:.3f}")
    m.shutdown()
    json.dump(res, open(a.out, "w"), indent=1)
    print("wrote", a.out)

if __name__ == "__main__": main()
