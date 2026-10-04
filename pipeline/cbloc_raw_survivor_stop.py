#!/usr/bin/env python3
"""cbloc_raw_survivor_stop.py - trader 2026-10-03: unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD), raw signal + the 24h cut at +1.5R;
every trade that SURVIVES the cut (reached +1.5R by the bar-6 close) gets its stop moved up to +S R, S in {entry, 0.25 .. 1.5}:
  check  at the 24h check itself (bar-6 close); if price is already at/below +S there, it closes at that close
  touch  the moment it first reaches +1.5R (inside the first 24h)
No bank, no adds. Per minute: stop, (touch move), TP2, the 24h check. SCALED. -> data/study/cbloc_raw_survivor_stop_report.txt"""
import os, sys, statistics as st, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]; CUT = 1.5
STOPS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5]
REPORT = os.path.join(A.STUDY, "cbloc_raw_survivor_stop_report.txt")


def run(F, e0, tp2, k6, cut, S, when):
    xo, xh, xl, xc, bh, n = F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], F["n"]; stop = -1.0; moved = False; peak = -1e9
    for j in range(n):
        if xl[j] <= stop: return min(stop, xo[j]) - e0
        if bh[j] > peak: peak = bh[j]
        if when == "touch" and S is not None and not moved and k6 >= 0 and j <= k6 and bh[j] >= CUT: moved = True; stop = max(stop, e0 + S)
        if tp2 is not None and xh[j] >= tp2: return tp2 - e0
        if cut is not None and k6 >= 0 and j == k6:
            if peak < cut: return xc[j] - e0
            if when == "check" and S is not None and not moved:
                moved = True
                if xc[j] <= e0 + S: return xc[j] - e0
                stop = max(stop, e0 + S)
    return xc[n - 1] - e0


def do(sym):
    S_ = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S_, x)
        if p is None: continue
        F = p["F"]; k6 = p["kbar"][6]
        r = {"raw": run(F, p["e0"], p["tp2"], k6, None, None, None), "cut": run(F, p["e0"], p["tp2"], k6, CUT, None, None)}
        for w in ("check", "touch"):
            for S in STOPS: r[(w, S)] = run(F, p["e0"], p["tp2"], k6, CUT, S, w)
        out.append((sym, x["t"], x["up"], r))
    return out


POP = []
def main():
    global POP
    POP = [x for x in CB.population() if x["sym"] in SYMS]; t0 = time.time()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z[1])
    raw = [z[3]["raw"] for z in res]; cut = [z[3]["cut"] for z in res]
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s",
         f"raw signal {st.mean(raw):+.4f}R/trade DD {maxdd(raw):.1f} | raw + 24h cut 1.5R (stop untouched) {st.mean(cut):+.4f}R/trade DD {maxdd(cut):.1f}\n"]
    for w, title in (("check", "stop moved AT THE 24h CHECK"), ("touch", "stop moved WHEN +1.5R IS FIRST HIT")):
        L.append(f"== {title} ==")
        for S in STOPS:
            v = [z[3][(w, S)] for z in res]; sub = lambda g: st.mean([z[3][(w, S)] for z in res if g(z)])
            L.append(f"  stop -> {'entry ' if S == 0 else '+' + str(S) + 'R'}: {st.mean(v):+.4f}R/trade (vs cut-only {st.mean(v) - st.mean(cut):+.4f}, t {paired_t(cut, v):+.2f})  DD {maxdd(v):5.1f}  "
                     f"total {sum(v):+6.1f} | long {sub(lambda z: z[2]):+.4f} short {sub(lambda z: not z[2]):+.4f} | 2012-24 {sub(lambda z: z[1][:4] < '2025'):+.4f} "
                     f"2025-26 {sub(lambda z: z[1][:4] >= '2025'):+.4f} | " + " ".join(f"{s[:6]} {st.mean([z[3][(w, S)] for z in res if z[0] == s]):+.3f}" for s in SYMS))
        L.append("")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
