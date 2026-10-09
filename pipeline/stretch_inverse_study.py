#!/usr/bin/env python3
"""stretch_inverse_study.py - trader 2026-10-09: is a TrendCont signal better traded INVERTED when price is stretched far from its EMA?
  - commodity bloc (6 symbols, 2012-2026): stretched far past the EMA in the signal's direction;
  - USD majors (EURUSD, GBPUSD; 2012-2024 only - their 2025+ is research-locked): stretched far BELOW the EMA.
Stretch, at the signal bar (no look-ahead): H4 = (signal close - H4 EMA20) / H4 Wilder ATR14; D1 = (last COMPLETED day's close - D1
EMA20) / D1 ATR14, days strictly before the signal day. "In the signal's direction" = signed by the trade (BUY +, SELL -).
Inverted = the direction flipped at the same entry, stop and TP2 mirrored at the same distances; raw management on the M1 engine,
model spread and +1 pip. Every cut here is IN-SAMPLE and several thresholds are looked at: a bucket that looks good is a hypothesis.
    python3 pipeline/stretch_inverse_study.py    -> data/study/stretch_inverse/report.md + trades.csv
"""
from __future__ import annotations
import csv, os, statistics as st, sys, time
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_cards as BC                    # noqa: E402
from pipeline import range_fade_study as RF                  # noqa: E402  (wilder_atr)
from pipeline import trendcont_invert_study as TI            # noqa: E402  (replay)
from live import symbol_groups as SG                         # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "stretch_inverse")
BLOC = [s + ".dk" for s in next(g for g in SG.GROUPS if g[0] == "com")[3]]; MAJ = ["EURUSD.dk", "GBPUSD.dk"]


def features(sym_bars, i):
    c = [b[4] for b in sym_bars]; h = [b[2] for b in sym_bars]; l = [b[3] for b in sym_bars]
    return c, h, l


def main():
    os.makedirs(OUT, exist_ok=True); t0 = time.time()
    pop = [x for x in CB.population() if x["sym"] in BLOC + MAJ and x["strat"] == "TrendCont"]
    rows = []
    for sym in BLOC + MAJ:
        xs = [x for x in pop if x["sym"] == sym]
        if not xs: continue
        bars = xs[0]["bars"]; c = [b[4] for b in bars]; h = [b[2] for b in bars]; l = [b[3] for b in bars]
        ema4 = BC.ema(c, 20); atr4 = RF.wilder_atr(h, l, c); ctx = BC.prep_symbol(bars); days = ctx["days"]
        dc = [d[4] for d in days]; dh = [d[2] for d in days]; dl = [d[3] for d in days]; ema1 = BC.ema(dc, 20); atr1 = RF.wilder_atr(dh, dl, dc)
        S = CB.sym_obj(sym); grp = "bloc" if sym in BLOC else "majors"
        for x in xs:
            y = int(x["t"][:4])
            if grp == "majors" and y >= 2025: continue                    # research lock: majors' 2025+ is sealed
            i = x["i0"] - 1
            if i < 60 or not atr4[i]: continue
            kd = ctx["day_idx"].get(BC.day_of(bars[i][0]))
            if not kd or kd < 25 or not atr1[kd - 1]: continue
            s4 = (c[i] - ema4[i]) / atr4[i]; s1 = (dc[kd - 1] - ema1[kd - 1]) / atr1[kd - 1]; sg = 1 if x["up"] else -1
            o = TI.replay(S, x)
            e = x["e"]; inv = dict(x, up=not x["up"], s=2 * e - x["s"], tp1=(2 * e - x["tp1"]) if x["tp1"] else None, tp2=(2 * e - x["tp2"]) if x["tp2"] else None)
            iv = TI.replay(S, inv)
            if o is None or iv is None: continue
            rows.append({"grp": grp, "sym": sym.split(".")[0], "year": y, "dir": "BUY" if x["up"] else "SELL", "s4": s4, "s1": s1, "s4d": s4 * sg, "s1d": s1 * sg,
                         "raw1": o[1], "inv1": iv[1], "raw": o[0], "inv": iv[0]})
    with open(os.path.join(OUT, "trades.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    def line(lbl, xs):
        if not xs: return f"| {lbl} | 0 | | | | |"
        a = [z["inv1"] for z in xs]; b = [z["raw1"] for z in xs]; m = sum(a) / len(a); sd = st.stdev(a) if len(a) > 1 else 0
        return f"| {lbl} | {len(xs)} | {m:+.3f} | {(m / (sd / len(a) ** 0.5)) if sd else 0:+.2f} | {sum(b) / len(b):+.3f} | {sum(a):+.1f} |"
    H = "| stretch bucket | n | INVERTED mean R (+1 pip) | t | original raw mean (+1 pip) | inverted total R |\n|---|---|---|---|---|---|"
    L = [f"# Inverse on a stretched TrendCont signal (trader 2026-10-09) - {len(rows)} signals, {time.time() - t0:.0f}s", "",
         "Stretch in ATR from the EMA20 at the signal bar (H4) or the last completed day (D1). IN-SAMPLE; several buckets are looked at.", ""]
    for grp, key, title, buckets in (
            ("bloc", "s4d", "Commodity bloc - H4 stretch IN the signal's direction", [(-99, 0), (0, 1), (1, 2), (2, 3), (3, 99)]),
            ("bloc", "s1d", "Commodity bloc - D1 stretch IN the signal's direction", [(-99, 0), (0, 0.5), (0.5, 1), (1, 1.5), (1.5, 99)]),
            ("majors", "s4", "USD majors - H4 stretch (negative = price BELOW the EMA)", [(-99, -3), (-3, -2), (-2, -1), (-1, 0), (0, 99)]),
            ("majors", "s1", "USD majors - D1 stretch (negative = price BELOW the EMA)", [(-99, -1.5), (-1.5, -1), (-1, -0.5), (-0.5, 0), (0, 99)])):
        xs = [z for z in rows if z["grp"] == grp]
        L += [f"## {title}", "", H] + [line(f"{lo:g} .. {hi:g}" if abs(lo) < 99 and abs(hi) < 99 else (f">= {lo:g}" if hi >= 99 else f"< {hi:g}"),
                                            [z for z in xs if lo <= z[key] < hi]) for lo, hi in buckets]
        if grp == "bloc":
            top = [z for z in xs if z[key] >= buckets[-1][0]]
            L += ["", "Top bucket by period: " + "; ".join(f"{p}: " + line(p, [z for z in top if (z['year'] <= 2024) == (p == '2012-2024')]).split('|')[2].strip()
                                                           + " signals, mean " + line(p, [z for z in top if (z['year'] <= 2024) == (p == '2012-2024')]).split('|')[3].strip()
                                                           for p in ("2012-2024", "2025-2026"))]
        L += ["", "All of this group: " + line("all", xs).replace("|", " ").strip(), ""]
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L)); print("\n".join(L))


if __name__ == "__main__": main()
