#!/usr/bin/env python3
"""
universe_cost_filter.py - the spread-drag method (data/study/spread_drag.md) run over the whole FTMO symbol list, for
the demo universe expansion (coach 2026-09-21). Attach only symbols with round-trip drag <= 0.10R per trade.

Method, per symbol, all from the shadow box's terminal through the web app's /api/bars (the tunnel must be up):
  spread  = MEDIAN of live ask-bid over 12 samples 2.5 s apart (the original method: 12 samples over 30 s), plus the
            max seen, so a spiky sample is visible rather than silently deciding the filter;
  stop    = 1.385 x median Wilder ATR(14) on H4 over the last 360 closed bars (~60 trading days). 1.385 ATR is the lineup
            median detector stop measured in the §10.2 study (data/study/stop_width_atr.md) - new symbols have no graded
            history, so their stop is estimated in ATR units exactly as the detectors size it;
  drag    = spread / stop, charged ONCE per round trip (enter at ask, exit at bid), in R - same definition as the
            original table. A sensitivity column prices a tight 1.0-ATR stop.
Commission and swaps are NOT in the drag (as in the original table); the only commission on record is BTCUSD's
(0.0325 %/side, config/symbol_rules.json), shown for it. USDCNH is excluded regardless (managed price, coach ruling).

  python3 pipeline/universe_cost_filter.py [--base http://10.77.0.1:8080] [--symbols /mnt/c/.../ftmo_symbols.txt]
Writes data/study/universe_cost_filter.md + .csv.
"""
from __future__ import annotations
import argparse, csv, json, statistics, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STOP_ATR = 1.385; CAP = 0.10; SAMPLES = 12; GAP_S = 2.5
EXCLUDE = {"USDCNH": "managed price (coach ruling) - excluded regardless of cost"}

def get(base: str, sym: str, n: int, tf: str = "h4", timeout: float = 15.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base}/api/bars/{sym}?tf={tf}&n={n}", timeout=timeout) as r: return json.load(r)
    except Exception: return None

def wilder_atr(bars: list[list], n: int = 14) -> list[float]:
    out = []; prev = None; atr = None
    for i, b in enumerate(bars):
        h, l, c = b[2], b[3], b[4]
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev)); prev = c
        if i < n: out.append(float("nan")); atr = tr if atr is None else atr + tr
        elif i == n: atr = atr / n; atr = (atr * (n - 1) + tr) / n; out.append(atr)
        else: atr = (atr * (n - 1) + tr) / n; out.append(atr)
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://10.77.0.1:8080")
    ap.add_argument("--symbols", default="/mnt/c/Users/jacks/OneDrive/Trading/ftmo_symbols.txt"); a = ap.parse_args()
    syms = [s.strip() for s in Path(a.symbols).read_text().splitlines() if s.strip()]
    print(f"{len(syms)} symbols; history pass...", file=sys.stderr)
    info = {}
    with ThreadPoolExecutor(6) as ex:
        for s, d in zip(syms, ex.map(lambda s: get(a.base, s, 400), syms)):
            bars = (d or {}).get("bars") or []
            closed = bars[:-1]
            atr = [x for x in wilder_atr(closed)[-360:] if x == x]
            info[s] = {"bars": len(bars), "price": closed[-1][4] if closed else None, "atr": statistics.median(atr) if atr else None, "spreads": []}
    print("spread sampling (12 x 2.5 s)...", file=sys.stderr)
    for k in range(SAMPLES):
        t0 = time.time()
        with ThreadPoolExecutor(6) as ex:
            for s, d in zip(syms, ex.map(lambda s: get(a.base, s, 1, timeout=8.0), syms)):
                t = (d or {}).get("tick") or {}
                if t.get("ask") and t.get("bid") and t["ask"] >= t["bid"]: info[s]["spreads"].append(t["ask"] - t["bid"])
        time.sleep(max(0.0, GAP_S - (time.time() - t0)))
    rows = []
    for s in syms:
        x = info[s]; root = s.split(".")[0].upper(); sp = x["spreads"]
        spread = statistics.median(sp) if sp else None; stop = STOP_ATR * x["atr"] if x["atr"] else None
        drag = spread / stop if (spread is not None and stop) else None
        tight = spread / x["atr"] if (spread is not None and x["atr"]) else None
        if root in EXCLUDE: verdict, why = "EXCLUDED", EXCLUDE[root]
        elif drag is None: verdict, why = "NO DATA", f"bars={x['bars']} spread samples={len(sp)}"
        elif drag <= CAP: verdict, why = "ATTACH", ""
        else: verdict, why = "EXCLUDED", f"drag {drag:.3f}R > {CAP:.2f}R"
        rows.append({"symbol": s, "price": x["price"], "spread_median": spread, "spread_max": max(sp) if sp else None, "samples": len(sp),
                     "atr14_h4_median": x["atr"], "est_stop": stop, "drag_R": drag, "drag_R_tight_1ATR": tight, "verdict": verdict, "note": why})
    rows.sort(key=lambda r: (r["drag_R"] is None, r["drag_R"] or 0))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = REPO / "data" / "study" / "universe_cost_filter.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    def g(v, p=5): return "-" if v is None else (f"{v:.{p}g}" if isinstance(v, float) else str(v))
    md = [f"# Universe cost filter — spread drag over the FTMO list (coach 2026-09-21)", "",
          f"Run {stamp} on the shadow box (OANDA-Demo-1) via `pipeline/universe_cost_filter.py`. Rule: attach iff round-trip "
          f"spread drag ≤ **{CAP:.2f}R**/trade; USDCNH excluded regardless. Stop = {STOP_ATR} × median H4 ATR(14) over the last 360 "
          "closed bars (the §10.2 lineup-median detector stop, in ATR units); spread = median of 12 live samples 2.5 s apart. "
          "Commission and swaps are not in the drag (same as the original table).", "",
          "| # | symbol | verdict | drag (R) | drag at a 1.0-ATR stop | median spread | max spread seen | est. stop | H4 ATR(14) | price | note |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        md.append(f"| {i} | {r['symbol']} | **{r['verdict']}** | {g(r['drag_R'], 3)} | {g(r['drag_R_tight_1ATR'], 3)} | {g(r['spread_median'])} | {g(r['spread_max'])} | "
                  f"{g(r['est_stop'])} | {g(r['atr14_h4_median'])} | {g(r['price'], 6)} | {r['note']} |")
    att = [r["symbol"] for r in rows if r["verdict"] == "ATTACH"]
    md += ["", f"**Attach ({len(att)}):** " + ", ".join(att), "",
           f"**Excluded ({sum(1 for r in rows if r['verdict'] != 'ATTACH')}):** " + ", ".join(f"{r['symbol']} ({r['note'] or r['verdict']})" for r in rows if r["verdict"] != "ATTACH")]
    (REPO / "data" / "study" / "universe_cost_filter.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))

if __name__ == "__main__": main()
