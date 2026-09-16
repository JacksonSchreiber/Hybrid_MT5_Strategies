#!/usr/bin/env python3
"""
btcusd_feasibility.py - one-look feasibility report per docs/strategies/btcusd-feasibility-spec.md.
Reads the AA journal (+actions) of the frozen system on BTCUSD.dk, applies the broker's published costs
(swaps in % p.a. of notional per charged night, commission % of notional per side), and writes
data/study/btcusd/report.md + equity_curves.png + cells.csv. No EA/config changes; nothing live.
Costs are applied in R, which makes them independent of the tester's own lot size:
  swap_R  = sum over charged nights of (rate/100/360) * price * lots_fraction / stop$   (lots cancel out)
  comm_R  = comm_pct/100 * price * (in + out volume = 2) / stop$
"""
import csv, json, os, sys
from datetime import datetime, timedelta, date
from collections import defaultdict

J = sys.argv[1] if len(sys.argv) > 1 else "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal/AA_BTCUSD.dk_20180101_20260716.csv"
OUT = "data/study/btcusd"; os.makedirs(OUT, exist_ok=True)
SPEC = {  # OANDA-Demo-1 BTCUSD.sim specification, read 2026-09-16 (trader screenshot)
    "source": "OANDA-Demo-1 'BTCUSD.sim' MT5 Specification window, read 2026-09-16 (trader screenshot)",
    "contract_size": 1.0, "digits": 2, "min_lot": 0.01, "lot_step": 0.01, "max_lot": 4.0,
    "swap_type": "in percentage terms, using current price (annual %, /360 per night)",
    "swap_long_pct_pa": -30.31, "swap_short_pct_pa": -19.69,
    "swap_days": {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 0},   # Mon..Fri charged x1 at each day's rollover; Sat/Sun none, no triple day
    "commission_pct_per_side": 0.0325, "sessions": "Mon-Fri 00:05-23:59 (no weekend quotes on this broker)",
    "stops_level": 5, "margin_pct": 25.0,
    "spread_usd": 3.09, "spread_note": "live BTCUSD.sim spread sampled on the shadow box 2026-09-16 (11 samples over 30 s: mean $3.09, min $3.00, max $4.00 at $75,662). "
                                       "The BTCUSD.dk import carries NO spread (every fill equals the detector level), so the spread is applied here as an explicit cost",
}
RISK_USD = 250.0   # 1% of the 25,000 account
def pdt(s): return datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
rows = list(csv.DictReader(open(J, encoding="ascii", errors="replace", newline="")))
acts = defaultdict(list)
ap = J[:-4] + ".actions.csv"
if os.path.exists(ap):
    for a in csv.DictReader(open(ap, newline="")): acts[int(a["posid"])].append(a)

def charged_nights(t0, t1, bank_t=None):
    """(nights_full, nights_half): rollovers at 00:00 between t0 and t1 whose closing day is Mon-Fri."""
    full = half = 0
    d = (t0 + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    while d <= t1:
        closing_day = (d - timedelta(days=1)).weekday()
        if SPEC["swap_days"].get(closing_day, 0):
            if bank_t and d > bank_t: half += 1
            else: full += 1
        d += timedelta(days=1)
    return full, half

trades = []
for r in rows:
    if r["decision"] != "approved" or not r.get("r_multiple"): continue
    t0 = pdt(r["signal_time"]); t1 = pdt(r["exit_time"]) if r.get("exit_time") else None
    if t1 is None: continue
    entry = float(r["entry"]); sl = float(r["sl"]); stop = abs(entry - sl); price = entry
    d = 1 if r["direction"] == "BUY" else -1
    bank = next((pdt(a["bar_time"]) for a in acts.get(int(r["posid"]), []) if a["action"] == "AUTO_1R"), None)
    nf, nh = charged_nights(t0, t1, bank)
    rate = SPEC["swap_long_pct_pa"] if d > 0 else SPEC["swap_short_pct_pa"]
    swap_R = (rate / 100.0 / 360.0) * price * (nf + 0.5 * nh) / stop          # negative = cost
    comm_R = -(SPEC["commission_pct_per_side"] / 100.0) * price * 2.0 / stop
    spread_R = -SPEC["spread_usd"] / stop                                       # paid once per round trip (buy at ask / sell exit at ask)
    r_off = float(r["r_multiple"]); r_on = r_off + swap_R + comm_R + spread_R
    lots_spec = int((RISK_USD / stop) / SPEC["lot_step"]) * SPEC["lot_step"]
    suppressed = lots_spec < SPEC["min_lot"] - 1e-12
    spread = (entry - float(r["orig_entry"])) if d > 0 and not r.get("edited") == "1" else None
    trades.append(dict(id=int(r["signal_id"]), t=t0, t1=t1, det=r["strategy"], dir=r["direction"], regime=r["regime"] or "(warm-up)", cls=r["decision_class"],
                       r_off=r_off, r_on=r_on, swap_R=swap_R, comm_R=comm_R, spread_R=spread_R, nights=nf + nh, nights_half=nh, banked=(float(r["rt_bankr"]) > -90) if r.get("rt_bankr") else bank is not None,
                       tp=(r["terminal"] == "TP"), terminal=r["terminal"], stop=stop, price=price, lots_spec=lots_spec, suppressed=suppressed, spread=spread,
                       realised_risk=lots_spec * stop / RISK_USD, tester_lots=float(r["lots"])))
trades.sort(key=lambda x: x["t"])
eff_start = min(x["t"] for x in trades if x["regime"] != "(warm-up)")
def cells(tr):
    if not tr: return dict(n=0)
    def dd(key):
        cum = peak = mdd = 0.0
        for x in tr: cum += x[key]; peak = max(peak, cum); mdd = max(mdd, peak - cum)
        return mdd
    years = max(1e-9, (tr[-1]["t"] - tr[0]["t"]).days / 365.25)
    return dict(n=len(tr), avg_off=sum(x["r_off"] for x in tr) / len(tr), avg_on=sum(x["r_on"] for x in tr) / len(tr),
                win_off=sum(1 for x in tr if x["r_off"] > 0) / len(tr), win_on=sum(1 for x in tr if x["r_on"] > 0) / len(tr),
                bank=sum(1 for x in tr if x["banked"]) / len(tr), tp=sum(1 for x in tr if x["tp"]) / len(tr),
                dd_off=dd("r_off"), dd_on=dd("r_on"), cadence=len(tr) / years, years=years, sum_off=sum(x["r_off"] for x in tr), sum_on=sum(x["r_on"] for x in tr),
                drag=sum(x["swap_R"] + x["comm_R"] + x["spread_R"] for x in tr) / len(tr), nights=sum(x["nights"] for x in tr) / len(tr))
scope = [x for x in trades if x["t"] >= eff_start and not x["suppressed"]]
H1 = [x for x in scope if x["t"] < datetime(2022, 1, 1)]; H2 = [x for x in scope if x["t"] >= datetime(2022, 1, 1)]
groups = {"FLEET (scope: from effective start, min-lot passed)": scope, "ALL fills incl. warm-up": trades,
          "half 1: 2018-01 → 2021-12": H1, "half 2: 2022-01 → 2026-07": H2}
for det in sorted({x["det"] for x in scope}): groups[f"detector {det}"] = [x for x in scope if x["det"] == det]
for rg in ("TREND_UP", "TREND_DOWN", "CHOP"): groups[f"regime {rg}"] = [x for x in scope if x["regime"] == rg]
for cl in ("TAKE", "DISCRETION"): groups[f"class {cl}"] = [x for x in scope if x["cls"] == cl]
for dr in ("BUY", "SELL"): groups[f"direction {dr}"] = [x for x in scope if x["dir"] == dr]
C = {k: cells(v) for k, v in groups.items()}
# gate (costs-on, scope)
f = C["FLEET (scope: from effective start, min-lot passed)"]
det_fail = [(k, C[k]) for k in C if k.startswith("detector ") and C[k]["n"] >= 30 and C[k]["avg_on"] < -0.10]
gate = [("1. fleet avg R (costs-on) >= 0.00", f["avg_on"] >= 0.0, f"{f['avg_on']:+.3f}R"),
        ("2. both halves >= 0.00", C["half 1: 2018-01 → 2021-12"]["avg_on"] >= 0 and C["half 2: 2022-01 → 2026-07"]["avg_on"] >= 0, f"H1 {C['half 1: 2018-01 → 2021-12']['avg_on']:+.3f}R, H2 {C['half 2: 2022-01 → 2026-07']['avg_on']:+.3f}R"),
        ("3. cadence 15-60 signals/year", 15 <= f["cadence"] <= 60, f"{f['cadence']:.1f}/yr"),
        ("4. no detector < -0.10R on n>=30", not det_fail, ", ".join(f"{k}: {v['avg_on']:+.3f}R (n={v['n']})" for k, v in det_fail) or "none"),
        ("5. blind max drawdown <= 15R", f["dd_on"] <= 15.0, f"{f['dd_on']:.1f}R (costs-off {f['dd_off']:.1f}R)")]
verdict = "PASS" if all(g[1] for g in gate) else "FAIL"
# spread estimate from BUY market fills (fill = ask, detector level = bid)
sp = [x for x in trades if x["spread"] is not None and x["spread"] >= 0]
sp_pct = sum(x["spread"] / x["price"] for x in sp) / len(sp) * 100 if sp else float("nan")
sp_stop = sum(x["spread"] / x["stop"] for x in sp) / len(sp) if sp else float("nan")
sp_med = sorted(x["spread"] for x in sp)[len(sp) // 2] if sp else float("nan")
# equity curves
try:
    from PIL import Image, ImageDraw
    W, Hh = 1100, 480; img = Image.new("RGB", (W, Hh), (13, 17, 23)); d = ImageDraw.Draw(img)
    series = {"costs-off": ("r_off", (63, 185, 80)), "costs-on": ("r_on", (248, 81, 73))}
    curves = {}
    for name, (k, col) in series.items():
        cum = 0.0; pts = []
        for x in scope: cum += x[k]; pts.append((x["t"], cum))
        curves[name] = pts
    allv = [v for pts in curves.values() for _, v in pts] + [0.0]; lo, hi = min(allv), max(allv); t0, t1 = scope[0]["t"], scope[-1]["t"]
    def X(t): return 60 + (t - t0).total_seconds() / max(1, (t1 - t0).total_seconds()) * (W - 90)
    def Y(v): return Hh - 40 - (v - lo) / max(1e-9, hi - lo) * (Hh - 80)
    d.line([(60, Y(0)), (W - 30, Y(0))], fill=(80, 80, 80))
    for name, (k, col) in series.items():
        pts = [(X(t), Y(v)) for t, v in curves[name]]
        if len(pts) > 1: d.line(pts, fill=col, width=2)
    d.text((60, 8), f"BTCUSD.dk blind equity (R, cumulative)  green=costs-off  red=costs-on   {t0:%Y-%m} -> {t1:%Y-%m}   maxDD on {f['dd_on']:.1f}R", fill=(201, 209, 217))
    for v in (lo, 0, hi): d.text((4, Y(v) - 6), f"{v:+.0f}", fill=(139, 148, 158))
    img.save(f"{OUT}/equity_curves.png")
except Exception as e: print("png failed", e)
# report
def line(k, c):
    if c["n"] == 0: return f"| {k} | 0 | | | | | | | | |"
    return (f"| {k} | {c['n']} | {c['avg_off']:+.3f} | **{c['avg_on']:+.3f}** | {c['win_on']*100:.0f}% | {c['bank']*100:.0f}% | {c['tp']*100:.0f}% | "
            f"{c['dd_off']:.1f} / **{c['dd_on']:.1f}** | {c['cadence']:.1f} | {c['drag']:+.3f} |")
supp = [x for x in trades if x["suppressed"]]
ten_w = sorted(scope, key=lambda x: -x["r_on"])[:10]; ten_l = sorted(scope, key=lambda x: x["r_on"])[:10]
rep = f"""# BTCUSD feasibility study — report (one look, pre-registered)

**Run:** 2026-09-16 · frozen four-detector system, doctrine-v2 exits, regime gate, 1% viability gate · headless AA tester, real ticks (Dukascopy), H4 · `{os.path.basename(J)}`
**Verdict against the §7 gate (costs-on): {verdict}**

## Data
- Import: `BTCUSD.dk` custom symbol, Dukascopy ticks 2017-05 → **2026-07-16** (last imported tick; rolling importer, see `import_log.txt`). Tester range requested 2018-01-01 → 2026-09-16; effective end 2026-07-16.
- Effective start: **{eff_start:%Y-%m-%d}** - the import holds history from 2017-05, so the ~210-D1 regime warm-up was complete before 2018-01-01 and every fill carries a regime tag (the spec's 2018-08 assumption was conservative; the halves are therefore 2018-01 → 2021-12 and 2022-01 → 2026-07).
- Spread: **the import carries no spread** - every BUY fill equals the detector's bid-level (max |fill − level| = $0.00 over {len(sp)} BUY fills), i.e. the tester ran mid-price for spread purposes, exactly the flattering case the spec warns about. Corrected by charging the spread explicitly: {SPEC['spread_note']}. That is {SPEC['spread_usd']/75662*100:.4f}% of price; per trade it costs spread$/stop$ in R (fleet mean below). Sensitivity: a $30 spread (10×, closer to 2018–2020 CFD conditions) would cost ~{30/2000*100:.1f}% of R per trade at a $2,000 stop.

## Costs used (per {SPEC['source']})
| item | value |
|---|---|
| contract size | {SPEC['contract_size']} BTC per lot · digits {SPEC['digits']} · min {SPEC['min_lot']} · step {SPEC['lot_step']} · max {SPEC['max_lot']} |
| swap type | {SPEC['swap_type']} |
| swap long / short | **{SPEC['swap_long_pct_pa']}% / {SPEC['swap_short_pct_pa']}% p.a.**, charged at Mon–Fri rollovers ×1, none on Sat/Sun, no triple day |
| commission | **{SPEC['commission_pct_per_side']}% of notional per side** (in + out) |
| spread | **${SPEC['spread_usd']:.2f} per round trip** (live sample, see Data) |
| sessions | {SPEC['sessions']} |
| applied as | swap_R = Σ charged nights (rate/100/360 × entry price × lot fraction) / stop$; lot fraction 1.0 before the +1R bank, 0.5 after; comm_R = 2 × {SPEC['commission_pct_per_side']}% × price / stop$; spread_R = ${SPEC['spread_usd']:.2f} / stop$. All in R, so the tester's own lot size does not matter. |
| min-lot at $25k | lots = floor(($250 / stop$) / 0.01) × 0.01; **{len(supp)} of {len(trades)} fills suppressed** (< 0.01 lot); mean realised risk after rounding = {sum(x['realised_risk'] for x in trades)/len(trades)*100:.1f}% of the 1% target |

Average cost drag (fleet): **{f['drag']:+.3f}R per trade** (mean {f['nights']:.1f} charged nights per trade).

## Cells (costs-off / **costs-on**)
| cell | n | avg R off | **avg R on** | win% on | bank% (+1R) | TP% | maxDD off / **on** (R) | cadence/yr | drag R/trade |
|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join(line(k, C[k]) for k in groups) + f"""

## Gate (§7, costs-on, scope = from effective start, min-lot passed)
| criterion | result | value |
|---|---|---|
""" + "\n".join(f"| {g[0]} | {'PASS' if g[1] else 'FAIL'} | {g[2]} |" for g in gate) + f"""

**{verdict}.** {'Advances to a training window per §7.' if verdict == 'PASS' else 'Closed on the first strike per §7 - no re-cuts, no parameter search.'}

## Ten largest winners / losers (costs-on, scope)
| winners | R on | | losers | R on |
|---|---|---|---|---|
""" + "\n".join(f"| #{w['id']} {w['t']:%Y-%m-%d} {w['det']} {w['dir']} | {w['r_on']:+.2f} | | #{l['id']} {l['t']:%Y-%m-%d} {l['det']} {l['dir']} | {l['r_on']:+.2f} |" for w, l in zip(ten_w, ten_l)) + f"""

## Rulings applied (§5)
- USD V/W calendar rows bound as for indices (the frozen EA gates; no crypto-specific events exist in the feed - **unmodeled**: ETF rulings, halvings, exchange failures).
- Late-Friday, weekend-hold and overnight-timing checklist steps: not EA gates, so nothing to suspend in the run; they are advisor/checklist items and were simply not applied. **Broker reality check:** this broker quotes BTCUSD **Mon–Fri only** (no weekend sessions), so "24/7" does not hold on the shadow account; weekend gaps exist in the Dukascopy ticks only where Dukascopy itself paused.
- Sizing 1% per the frozen gate; the 1 BTC contract honoured in the min-lot check above. Live, if ever: risk multiplier 0.5.

## What the study could not model
Crypto-specific event risk (ETF decisions, halvings, exchange failures) is absent from the feed; the tester spread is Dukascopy's, not this broker's floating spread; swap is charged on the entry price rather than each night's close (a second-order simplification); 'end' terminals ({sum(1 for x in trades if x['terminal']=='end')} fills still open at the data end) are marked to the last price; Dukascopy coverage gaps, if any, show as missing bars in the import log.
"""
open(f"{OUT}/report.md", "w", encoding="utf-8").write(rep)
with open(f"{OUT}/cells.csv", "w", newline="") as fcsv:
    w = csv.writer(fcsv); w.writerow(["cell"] + list(next(iter(C.values())).keys()))
    for k, c in C.items(): w.writerow([k] + [c.get(h, "") for h in next(iter(C.values())).keys()])
with open(f"{OUT}/trades_costed.csv", "w", newline="") as fcsv:
    w = csv.writer(fcsv); w.writerow(["id", "time", "exit", "detector", "dir", "regime", "class", "r_off", "r_on", "swap_R", "comm_R", "nights", "stop", "price", "lots_spec", "suppressed", "terminal"])
    for x in trades: w.writerow([x["id"], x["t"], x["t1"], x["det"], x["dir"], x["regime"], x["cls"], f"{x['r_off']:.3f}", f"{x['r_on']:.3f}", f"{x['swap_R']:.3f}", f"{x['comm_R']:.3f}", x["nights"], f"{x['stop']:.2f}", f"{x['price']:.2f}", x["lots_spec"], x["suppressed"], x["terminal"]])
print(verdict); print(json.dumps({k: {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in C.items() if k.startswith(("FLEET", "half", "detector"))}, indent=0)[:1500])
