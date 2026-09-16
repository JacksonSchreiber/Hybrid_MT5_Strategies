#!/usr/bin/env python3
"""
cost_journal.py - re-price a journal trade by trade with the broker's published costs (coach 2026-09-16, window 14).
The tester models no swaps/commission and the .dk import carries no spread; for BTC those are the dominant costs.
Reuses the feasibility study's logic: everything in R, so the tester's lot size does not matter.
  swap_R   = sum over charged nights (rate/100/360 x entry price x lot fraction) / stop$   (fraction 0.5 after the +1R bank)
  comm_R   = 2 x commission% x price / stop$ ;   spread_R = spread$ / stop$   (once per round trip)
Usage: cost_journal.py <journal.csv> [--symbol BTCUSD] [--out <path>]   -> <journal>.costed.csv + totals on stdout
Params come from config/symbol_rules.json[<symbol>]["costs"] (defaults = OANDA BTCUSD.sim as read 2026-09-16).
"""
import argparse, csv, json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
RISK_FRACTION = 0.01
def pdt(s): return datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
def charged_nights(t0, t1, bank_t, swap_days):
    full = half = 0; d = (t0 + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    while d <= t1:
        if swap_days.get((d - timedelta(days=1)).weekday(), 0):
            if bank_t and d > bank_t: half += 1
            else: full += 1
        d += timedelta(days=1)
    return full, half
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("journal"); ap.add_argument("--symbol", default=None); ap.add_argument("--out", default=None); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.journal, encoding="ascii", errors="replace", newline="")))
    sym = a.symbol or (rows[0]["symbol"] if rows else "BTCUSD")
    root = sym.split(".")[0].upper()
    cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "symbol_rules.json").read_text(encoding="utf-8")).get(root, {}).get("costs")
    if not cfg: sys.exit(f"no costs for {root} in config/symbol_rules.json")
    swap_days = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 0}   # Mon-Fri x1
    acts = defaultdict(list); ap_ = a.journal[:-4] + ".actions.csv"
    if os.path.exists(ap_):
        for x in csv.DictReader(open(ap_, newline="")): acts[int(x["posid"])].append(x)
    out_rows = []; tot_off = tot_on = 0.0; n = 0; per_det = defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        rr = dict(r); rr["costed_r"] = ""; rr["cost_swap_r"] = ""; rr["cost_comm_r"] = ""; rr["cost_spread_r"] = ""; rr["cost_nights"] = ""
        if r["decision"] in ("approved", "approved_pending") and r.get("r_multiple") and r.get("exit_time"):
            entry = float(r["entry"]); stop = abs(entry - float(r["sl"])); d = 1 if r["direction"] == "BUY" else -1
            bank = next((pdt(x["bar_time"]) for x in acts.get(int(r["posid"]), []) if x["action"] == "AUTO_1R"), None)
            nf, nh = charged_nights(pdt(r["signal_time"]), pdt(r["exit_time"]), bank, swap_days)
            rate = cfg["swap_long_pct_pa"] if d > 0 else cfg["swap_short_pct_pa"]
            swap_R = (rate / 100.0 / 360.0) * entry * (nf + 0.5 * nh) / stop
            comm_R = -(cfg["commission_pct_per_side"] / 100.0) * entry * 2.0 / stop
            spread_R = -cfg["spread_usd"] / stop
            r_off = float(r["r_multiple"]); r_on = r_off + swap_R + comm_R + spread_R
            rr.update({"costed_r": f"{r_on:.3f}", "cost_swap_r": f"{swap_R:.3f}", "cost_comm_r": f"{comm_R:.3f}", "cost_spread_r": f"{spread_R:.3f}", "cost_nights": nf + nh})
            tot_off += r_off; tot_on += r_on; n += 1; per_det[r["strategy"]][0] += 1; per_det[r["strategy"]][1] += r_off; per_det[r["strategy"]][2] += r_on
        out_rows.append(rr)
    out = a.out or (a.journal[:-4] + ".costed.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else ["signal_id"]); w.writeheader(); w.writerows(out_rows)
    summary = {"journal": os.path.basename(a.journal), "symbol": sym, "closed_trades": n, "total_r_off": round(tot_off, 3), "total_r_on": round(tot_on, 3),
               "avg_r_off": round(tot_off / n, 4) if n else None, "avg_r_on": round(tot_on / n, 4) if n else None,
               "per_detector": {k: {"n": v[0], "total_off": round(v[1], 3), "total_on": round(v[2], 3)} for k, v in per_det.items()},
               "signals_per_detector": {k: sum(1 for r in rows if r["strategy"] == k) for k in sorted({r["strategy"] for r in rows})},
               "costs": cfg, "out": out}
    print(json.dumps(summary, indent=1) if a.json else json.dumps(summary))
if __name__ == "__main__": main()
