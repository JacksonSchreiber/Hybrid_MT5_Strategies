import sys, csv, glob, os, json, statistics as st
sys.path.insert(0, "/home/jack/hybrid_project")
from pipeline.trendcont_step_study import REC
from pipeline.trendcont_step13_study import load, ema
DRAG = {k: v["drag_scaled"] for k, v in json.load(open("data/study/spread_drag.json")).items()}

def atr_sma(bars, j):                       # MT5 iATR: simple 14-bar mean of true range ending at bar j
    if j < 15: return 0.0
    tr = [max(bars[i][2]-bars[i][3], abs(bars[i][2]-bars[i-1][4]), abs(bars[i][3]-bars[i-1][4])) for i in range(j-13, j+1)]
    return sum(tr) / 14

def run(strict):
    rows = []
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]; d1 = load(sym, "d1")
        if not d1: continue
        c = [b[4] for b in d1]; e20 = ema(c, 20); days = [b[0][:10] for b in d1]
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            try: rm = float(r["r_multiple"])
            except (TypeError, ValueError): continue
            day = r["signal_time"][:10]
            jd = max((k for k, d in enumerate(days) if (d < day if strict else d <= day)), default=-1)
            if jd < 40: continue
            a = atr_sma(d1, jd)
            if a <= 0: continue
            up = r["direction"].upper().startswith("B")
            rows.append({"ext": ((c[jd]-e20[jd])/a)*(1 if up else -1), "r": rm, "cr": rm - DRAG.get(sym.split('.')[0], 0.007), "t": r["signal_time"]})
    return rows

def report(label, rows):
    hi = [x for x in rows if x["ext"] >= 0.5]; lo = [x for x in rows if x["ext"] < 0.5]
    d = st.mean(x["r"] for x in hi) - st.mean(x["r"] for x in lo)
    se = (st.stdev([x["r"] for x in hi])**2/len(hi) + st.stdev([x["r"] for x in lo])**2/len(lo))**0.5
    ts = sorted(x["t"] for x in rows); mid = ts[len(ts)//2]
    halves = []
    for hf in (lambda x: x["t"] <= mid, lambda x: x["t"] > mid):
        h = [x for x in hi if hf(x)]; l = [x for x in lo if hf(x)]
        halves.append(st.mean(x["r"] for x in h) - st.mean(x["r"] for x in l))
    gated = st.mean(x["cr"] for x in hi)
    print(f"{label:34} n={len(rows)} | above {st.mean(x['r'] for x in hi):+.3f} (n={len(hi)}) below {st.mean(x['r'] for x in lo):+.3f} (n={len(lo)}) "
          f"| diff {d:+.3f} +-{se:.3f} = {d/se:.1f} SE | halves {halves[0]:+.3f} / {halves[1]:+.3f} | gated costed {gated:+.3f}")

report("AS PUBLISHED (d <= day: look-ahead)", run(False))
report("AS THE EA SEES IT (d < day)", run(True))
