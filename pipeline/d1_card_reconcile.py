#!/usr/bin/env python3
"""d1_card_reconcile.py - coach pipeline rule (2026-09-30): reconcile the STUDY computation of D1 extension against the
value the EA published on live cards (sizing.d1_ext), before any D1 study result goes to the coach.

Bar-index rule under test: the last D1 bar dated STRICTLY before the signal's day (broker clock on both sides - the
card's signal_time and the feed's bar epochs are both broker time), weekend bars dropped, EMA20 of D1 closes,
ATR14 two ways: the studies' pipeline/trendcont_step_study.atr14 (Wilder) and MT5's iATR (simple 14-bar mean of TR).
    python3 pipeline/d1_card_reconcile.py <cards dir: signal JSONs> <bars dir: SYMBOL.json from the feed /bars?tf=d1>
"""
import datetime as dt, glob, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step13_study import ema          # noqa: E402
from pipeline.trendcont_step_study import atr14          # noqa: E402

cards, bars_dir = sys.argv[1], sys.argv[2]
rows = []
for p in sorted(glob.glob(os.path.join(cards, "*.json"))):
    s = json.load(open(p)); ea = (s.get("sizing") or {}).get("d1_ext")
    if ea is None: continue
    sym = s["symbol"]; bp = os.path.join(bars_dir, sym + ".json")
    if not os.path.exists(bp): continue
    raw = (json.load(open(bp)) or {}).get("bars") or []
    b = [[dt.datetime.fromtimestamp(x[0], dt.timezone.utc).strftime("%Y.%m.%d"), x[1], x[2], x[3], x[4]] for x in raw]
    b = [x for x in b if dt.date(int(x[0][:4]), int(x[0][5:7]), int(x[0][8:10])).weekday() < 5]
    day = s["signal_time"][:10].replace("-", ".")
    jd = max((k for k, x in enumerate(b) if x[0] < day), default=-1)
    if jd < 40: continue
    closes = [x[4] for x in b[:jd + 1]]; e20 = ema(closes, 20)[-1]
    tr = [max(b[k][2] - b[k][3], abs(b[k][2] - b[k - 1][4]), abs(b[k][3] - b[k - 1][4])) for k in range(jd - 13, jd + 1)]
    a_simple = sum(tr) / 14; a_wilder = atr14(b, jd)
    sg = 1 if str(s.get("direction")).upper().startswith("B") else -1
    v_s = (closes[-1] - e20) / a_simple * sg; v_w = (closes[-1] - e20) / a_wilder * sg if a_wilder else float("nan")
    rows.append((s.get("signal_key") or os.path.basename(p)[:-5], s["signal_time"], float(ea), v_s, v_w, b[jd][0]))
print(f"{'card':22} {'signal (broker)':22} {'D1 bar used':11} {'EA':>7} {'study/simple':>12} {'study/Wilder':>12}")
for k, t, ea, vs, vw, d in rows:
    print(f"{k:22} {t:22} {d:11} {ea:+7.2f} {vs:+12.2f} {vw:+12.2f}")
if rows:
    ds = [abs(r[2] - r[3]) for r in rows]; dw = [abs(r[2] - r[4]) for r in rows]
    print(f"\nn={len(rows)} cards. mean |EA - study| : simple-ATR {sum(ds)/len(ds):.3f}  (max {max(ds):.3f}) · Wilder-ATR "
          f"{sum(dw)/len(dw):.3f} (max {max(dw):.3f}). PASS = every card within 0.05 ATR on the ATR the EA uses.")
