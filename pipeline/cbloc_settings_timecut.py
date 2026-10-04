#!/usr/bin/env python3
"""cbloc_settings_timecut.py - trader 2026-10-03 (settings from the Data visualizers dials): unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD),
no staging, bank 75% at +1R (TP1 if nearer, capped at TP2) with the stop to entry, no pyramid, shorts' stop to +0.5R at +1.5R (after the bank),
B: shorts closed at the day-2 close if their average <= +0.25R, no I, out at TP2 - PLUS a time cut: close if the bid high has not reached
+X R by the close of H4 bar N after the signal (N = 2/3/4/5/6 = 8/12/16/20/24 h), X = 1 / 1.5 / 2.
Mirrors combo_build.run_one (the web tool's engine) check for check; the no-cut row must equal the web tool's combo exactly.
    -> data/study/cbloc_settings_timecut_report.txt"""
import os, sys, statistics as st, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
HOURS = [8, 12, 16, 20, 24]; XS = [1.0, 1.5, 2.0]
REPORT = os.path.join(A.STUDY, "cbloc_settings_timecut_report.txt")


def run(p, kc_cut, X):
    F = p["F"]; xo, xh, xl, xc, bh, n = F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], F["n"]
    e0, be, up, tp2 = p["e0"], p["be"], p["up"], p["tp2"]
    bank_R = p["bank_R"](1.0); bf = 0.75
    kcB = p["k12"] if (p["avg2"] is not None and not up and p["avg2"] <= 0.25) else -1
    size = 1.0; locked = 0.0; stop = -1.0; banked = False; sr_pend = True; peak = -1e9
    for j in range(n):
        if xl[j] <= stop: return locked + size * ((stop if stop < xo[j] else xo[j]) - e0)
        if bh[j] > peak: peak = bh[j]
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bf * (size * (bank_R - e0)); size = size * (1 - bf); stop = be
        if banked and sr_pend and bh[j] >= 1.5:
            if not up: stop = max(stop, be + 0.5)
            sr_pend = False
        if tp2 is not None and xh[j] >= tp2: return locked + size * (tp2 - e0)
        if kcB >= 0 and j == kcB: return locked + size * (xc[j] - e0)
        if X is not None and kc_cut >= 0 and j == kc_cut and peak < X: return locked + size * (xc[j] - e0)
    return locked + size * (xc[n - 1] - e0)


def do(sym):
    S = CB.sym_obj(sym); combos = CB.grid()
    ci = CB.find(combos, "off", 0, "0.75@1.0", "off", "0.5", "0.25", "off"); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        web = float(CB.run_trade(p, [combos[ci]])[0][0])
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + N][0]))) - 1 - p["k0"]) if p["i0"] + N < len(x["bars"]) else -1
        r = {"none": run(p, -1, None), "web": web}
        for h in HOURS:
            k = kb(h // 4)
            for X in XS: r[(h, X)] = run(p, k, X)
        out.append((sym, x["t"], x["up"], r))
    return out


POP = []
def main():
    global POP
    POP = [x for x in CB.population() if x["sym"] in SYMS]; t0 = time.time()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z[1])
    d = max(abs(z[3]["none"] - z[3]["web"]) for z in res)
    base = [z[3]["none"] for z in res]
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s; no-cut row vs the web tool's combo: max |diff| {d:.1e} "
         f"({'EXACT' if d < 1e-12 else 'MISMATCH'})\n"]
    def line(nm, v, ref=None):
        sub = lambda g: st.mean([q for q, z in zip(v, res) if g(z)])
        tr = [q for q, z in zip(v, res) if z[1][:4] < "2022"]; te = [q for q, z in zip(v, res) if z[1][:4] >= "2022"]
        return (f"{nm:26} {st.mean(v):+.4f}R" + (f" (vs no cut {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})" if ref else " " * 31) +
                f"  DD {maxdd(v):5.1f}  tot/DD {sum(v) / maxdd(v):+.2f} | 2012-21 {st.mean(tr):+.4f} | 2022-26 {st.mean(te):+.4f} | long {sub(lambda z: z[2]):+.4f} "
                f"short {sub(lambda z: not z[2]):+.4f} | " + " ".join(f"{s[:6]} {sub(lambda z, s=s: z[0] == s):+.3f}" for s in SYMS))
    L.append(line("your settings, no cut", base))
    for X in XS:
        L.append("")
        for h in HOURS: L.append(line(f"cut if not +{X}R by {h}h", [z[3][(h, X)] for z in res], base))
    L.append("\nR/trade grid (rows: threshold, columns: hours)")
    L.append("          " + "".join(f"{str(h) + 'h':>9}" for h in HOURS))
    for X in XS: L.append(f"  +{X}R   " + "".join(f"{st.mean([z[3][(h, X)] for z in res]):+9.4f}" for h in HOURS))
    L.append("worst DD grid")
    for X in XS: L.append(f"  +{X}R   " + "".join(f"{maxdd([z[3][(h, X)] for z in res]):9.1f}" for h in HOURS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
