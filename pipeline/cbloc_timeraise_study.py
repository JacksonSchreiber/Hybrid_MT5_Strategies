#!/usr/bin/env python3
"""cbloc_timeraise_study.py - trader 2026-10-04: a simple stop raise over time on the USD commodity-bloc symbols (AUDUSD, NZDUSD,
USDCAD), run on the RAW signal and on the current live setup for the bloc (live stack + T+L1.0).
  TIME RAISE  at the bar-3 close (0.5 day) the stop goes to entry, at the bar-6 close (1 day) to +0.25R, at the bar-18 close (3 days)
              to +0.5R - tighten only (a stop already higher, e.g. after the bank or the bloc lock, stays).
              "close" = when the price is already at/through the new stop at that bar close, the trade is closed there (market)
              "skip"  = then that raise is skipped and the stop stays where it was (the next step tries again)
  RAW         100% at the signal, the signal's stop, out at TP2 / the stop / the window end
  LIVE (bloc) 25% at the signal + 75% at the bar-6 close, bank per detector at +1R/TP1 -> stop to entry, +50% pyramid at +1.5R,
              T+L1.0: at the bar-6 close everything is closed unless the bid high reached +1.5R; at +1.5R every stop to +1R
Per minute: stop -> +1.5R flag -> bank -> pyramid + lock -> TP2 -> bloc 24h cut -> time raise -> staged add.
Kernel = numba; with the time raise off it reproduces cbloc_timestop_study.run_h (live, T+L1.0) and the raw replay exactly (checked).
SCALED (model cost = the M1 bar minimum spread, a LOWER bound - real FX costs are higher).
    python3 pipeline/cbloc_timeraise_study.py     -> data/study/cbloc_timeraise_report.txt
    python3 pipeline/cbloc_timeraise_study.py --levels 0,0.5,0.8 --raw    other stop levels (bars 3/6/18), raw only
                                                  -> data/study/cbloc_timeraise_raw_0_0.5_0.8_report.txt
    python3 pipeline/cbloc_timeraise_study.py --hours 4,24 --levels 0,0.5 --raw   steps at hours after the entry (any hour: 6 = 2h
                                                  into the 2nd H4 bar), up to three steps
    ... --avgcheck 0.5        + an AVERAGE CHECK at that hour: if the average bid close since the entry (in R) is below 0 there,
                              the trade is closed at that minute's close (applies with the time raise only)
    ... --bank 0.5 [--bank-be]  + BANK that share at TP1 (capped at TP2; no TP1 = no bank), with --bank-be the stop also goes to
                              entry at the bank (as live); applies with the time raise only
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline import cbloc_timestop_study as CT          # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_timeraise_report.txt")
HOURS = [12, 24, 72]; LVLS = np.array([0.0, 0.25, 0.5])
if "--levels" in sys.argv: LVLS = np.array([float(v) for v in sys.argv[sys.argv.index("--levels") + 1].split(",")])
if "--hours" in sys.argv: HOURS = [float(v) for v in sys.argv[sys.argv.index("--hours") + 1].split(",")]
assert len(HOURS) == len(LVLS) <= 3
RAW_ONLY = "--raw" in sys.argv
AVGH = float(sys.argv[sys.argv.index("--avgcheck") + 1]) if "--avgcheck" in sys.argv else None
BANKF_RULE = float(sys.argv[sys.argv.index("--bank") + 1]) if "--bank" in sys.argv else None
BANK_BE = "--bank-be" in sys.argv
if "--levels" in sys.argv or "--hours" in sys.argv or RAW_ONLY:
    REPORT = REPORT.replace("_report.txt", ("_raw" if RAW_ONLY else "") + "_" + "_".join(f"{h:g}h{v:g}" for h, v in zip(HOURS, LVLS))
                            + (f"_avg{AVGH:g}h" if AVGH is not None else "")
                            + (f"_bank{BANKF_RULE:g}{'be' if BANK_BE else ''}" if BANKF_RULE is not None else "") + "_report.txt")
# (name, setup 0 raw / 1 live bloc / 2 live standard (check only), time raise 0 off / 1 close / 2 skip)
V = [("raw", 0, 0), ("raw + time raise (close)", 0, 1), ("raw + time raise (skip)", 0, 2),
     ("live (bloc T+L1.0)", 1, 0), ("live + time raise (close)", 1, 1), ("live + time raise (skip)", 1, 2), ("_live standard", 2, 0)]
LBL = [("entry" if v == 0 else f"{v:+g}R") for v in LVLS]
KIND = {0: "stop (not a time-raised stop)", 1: "TP2", 2: "24h cut", 3: "window end"}
KIND.update({10 + q: f"closed at {h:g}h (below {LBL[q]})" for q, h in enumerate(HOURS)})
if AVGH is not None: KIND[20] = f"closed at the {AVGH:g}h average check (< 0R)"


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, has_tp2, tp2_R, bank_on, bank_R, bankf, F0, k_add, up, pyr_on, pyr_R, pyr_f,
         tstop, rmode, rto, sr, trig, rk, rl, tr, ak, bank_be):
    ls = np.zeros(4); le = np.zeros(4); nl = 1; ls[0] = F0; le[0] = e0
    locked = 0.0; stop = -1.0; banked = False; trigp = True; reached = False; q = 0; rstop = -1
    add_pend = F0 < 1.0 and k_add >= 0 and k_add < n
    cs = 0.0
    for j in range(n):
        cs += bc[j]
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            v = locked
            for a in range(nl): v += ls[a] * (r - le[a])
            return v, 0, rstop, j
        if bh[j] >= trig: reached = True
        if bank_on and (not banked) and xh[j] >= bank_R:
            banked = True; tot = 0.0
            for a in range(nl): tot += ls[a] * (bank_R - le[a])
            locked += bankf * tot
            for a in range(nl): ls[a] = ls[a] * (1 - bankf)
            if bank_be and be > stop: stop = be; rstop = -1
        if pyr_on and banked and trigp and bh[j] >= pyr_R:
            ls[nl] = pyr_f; le[nl] = (pyr_R if pyr_R > bo[j] else bo[j]) + eo[j]; nl += 1
            if rmode == 1:
                if not up and be + sr > stop: stop = be + sr; rstop = -1
            elif rmode == 2:
                if be + rto > stop: stop = be + rto; rstop = -1
            trigp = False
        if has_tp2 and xh[j] >= tp2_R:
            v = locked
            for a in range(nl): v += ls[a] * (tp2_R - le[a])
            return v, 1, rstop, j
        if tstop and k_add >= 0 and j == k_add and not reached:
            v = locked
            for a in range(nl): v += ls[a] * (xc[j] - le[a])
            return v, 2, rstop, j
        if ak >= 0 and j == ak and cs / (j + 1) < 0.0:
            v = locked
            for a in range(nl): v += ls[a] * (xc[j] - le[a])
            return v, 20, rstop, j
        if tr > 0 and q < 3 and j == rk[q]:
            lvl = be + rl[q]
            if lvl > stop:
                if xc[j] <= lvl:
                    if tr == 1:
                        v = locked
                        for a in range(nl): v += ls[a] * (xc[j] - le[a])
                        return v, 10 + q, rstop, j
                else:
                    stop = lvl; rstop = q
            q += 1
        if add_pend and j == k_add:
            ls[nl] = 1 - F0; le[nl] = bc[j] + eo[j]; nl += 1; add_pend = False
    v = locked
    for a in range(nl): v += ls[a] * (xc[n - 1] - le[a])
    return v, 3, rstop, n - 1


def do(sym):
    S = CB.sym_obj(sym); out = []; dmax = 0.0
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; h4, i0, k0 = x["bars"], p["i0"], p["k0"]
        def kt(H):     # the last minute before H trading hours after the entry (H4 bar b = H // 4, then H % 4 hours into it). Inside the
            # entry bar the clock starts at the first M1 minute: Sunday-open bars have no M1 data for their first 60-120 min (~2% of
            # trades), and without this the sub-4h checks found no minute and every later step was skipped (fixed 2026-10-04)
            b, r = int(H // 4), int(round((H % 4) * 60))
            if i0 + b >= len(h4): return 1 << 40
            return max(0, int(np.searchsorted(S.t, max(A.tmin(h4[i0 + b][0]), int(S.t[k0])) + r)) - 1 - k0)
        assert (kt(12) == p["kbar"][3] or p["kbar"][3] < 0) and (kt(24) == p["kbar"][6] or p["kbar"][6] < 0)
        rk = np.full(3, -1, dtype=np.int64); rk[:len(HOURS)] = [kt(H) for H in HOURS]
        lv = np.zeros(3); lv[:len(LVLS)] = LVLS
        akj = kt(AVGH) if AVGH is not None else -1
        ka = p["kbar"][6]; tp2 = p["tp2"]; R = {}
        for name, su, tr in V:
            live = su > 0; rb = BANKF_RULE is not None and su == 0 and tr > 0
            R[name] = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], p["be"], tp2 is not None,
                           tp2 if tp2 is not None else 0.0, live or rb, p["bank_R"](1.0) if live else p["bank_R"](99.0),
                           p["bankf_det"] if live else (BANKF_RULE or 0.0), A.F0 if live else 1.0, ka if live else -1,
                           p["up"], live, A.PYR_R, A.PYR_F, su == 1, 2 if su == 1 else 1, CB.BLOC_LOCK, CT.K.SR, CT.TRIG, rk, lv, tr, akj if tr > 0 else -1, live or BANK_BE)
        # exactness: time raise off == run_h (live bloc / live standard) and the raw replay
        k_add = ka if ka >= 0 else None
        for name, ts, rt in (("live (bloc T+L1.0)", 1, CB.BLOC_LOCK), ("_live standard", None, None)):
            v = CT.run_h(F, p["e0"], p["be"], p["bank_R"](1.0), tp2, p["bankf_det"], A.F0, k_add, p["up"], ts, rt)[0]
            dmax = max(dmax, abs(R[name][0] - v))
        xl, xh, xo, xc = F["xl"], F["xh"], F["xo"], F["xc"]
        hit = np.nonzero((xl <= -1.0) | ((xh >= tp2) if tp2 is not None else np.zeros(p["n"], bool)))[0]
        raw = ((min(-1.0, xo[hit[0]]) if xl[hit[0]] <= -1.0 else tp2) if hit.size else xc[p["n"] - 1]) - p["e0"]
        dmax = max(dmax, abs(R["raw"][0] - raw))
        # per variant: (R, exit kind, which time raise set the stop, trading hours to the exit, best R (exit side) before the exit)
        for name in R:
            v, kd, rs, j = R[name]; tm = S.t[k0 + j]; hb = int(np.searchsorted(S.h4t, tm, side="right")) - 1
            R[name] = (v, kd, rs, 4 * ((hb - i0) + (tm - S.h4t[hb]) / 240.0), float(F["xh"][:j + 1].max()))
        R["_tp1"] = (p["bank_R"](99.0) if tp2 is None or p["bank_R"](99.0) < tp2 - 1e-9 else None,)
        out.append((x["t"], x["up"], sym, x["strat"], R))
    return out, dmax


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: rr = pool.map(do, SYMS)
    res = sorted([z for r, _ in rr for z in r], key=lambda z: z[0]); dmax = max(d for _, d in rr)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades ({', '.join(s.split('.')[0] for s in SYMS)}), "
         f"{time.time() - T0:.0f}s; time raise off vs run_h (live bloc, live standard) and the raw replay: max |diff| {dmax:.1e} "
         f"({'EXACT' if dmax < 1e-12 else 'MISMATCH'})\n"]
    col = lambda nm, f=lambda z: True: [z[4][nm][0] for z in res if f(z)]
    if BANKF_RULE is not None:
        t1 = [z[4]["_tp1"][0] for z in res if z[4]["_tp1"][0] is not None and z[4]["_tp1"][0] < 50]
        nm = "raw + time raise (close)"; hit = sum(1 for z in res if z[4]["_tp1"][0] is not None and z[4]["_tp1"][0] < 50 and z[4][nm][4] >= z[4]["_tp1"][0])
        L.append(f"  TP1 below TP2 on {len(t1)} of {len(res)} trades, median TP1 {st.median(t1):+.2f}R (quarter {sorted(t1)[len(t1) // 4]:+.2f}, three quarters "
                 f"{sorted(t1)[3 * len(t1) // 4]:+.2f}); reached under the rule on {hit} trades\n")
    L[-1] = L[-1].rstrip("\n") + f"; time raise {' / '.join(f'{l} at {h:g}h' for l, h in zip(LBL, HOURS))}{f' + average check at {AVGH:g}h (< 0R -> close)' if AVGH is not None else ''}{f' + bank {BANKF_RULE:.0%} at TP1' + (' (stop to entry)' if BANK_BE else ' (stop unchanged)') if BANKF_RULE is not None else ''}\n"
    for base, names in (("raw", V[1:3]), ("live (bloc T+L1.0)", V[4:6]))[:1 if RAW_ONLY else 2]:
        b = col(base)
        L.append(f"  {base:28} {st.mean(b):+.4f}R  total {sum(b):+7.1f}  DD {maxdd(b):6.1f}  tot/DD {sum(b) / maxdd(b):+6.2f}" + " " * 25 +
                 f"| long {st.mean(col(base, lambda z: z[1])):+.4f} short {st.mean(col(base, lambda z: not z[1])):+.4f} | "
                 f"2012-24 {st.mean(col(base, lambda z: z[0][:4] < '2025')):+.4f} 2025-26 {st.mean(col(base, lambda z: z[0][:4] >= '2025')):+.4f} | "
                 + " ".join(f"{s[:6]} {st.mean(col(base, lambda z, s=s: z[2] == s)):+.3f}" for s in SYMS))
        for nm, *_ in names:
            v = col(nm)
            L.append(f"  {nm:28} {st.mean(v):+.4f}R  total {sum(v):+7.1f}  DD {maxdd(v):6.1f}  tot/DD {sum(v) / maxdd(v):+6.2f}"
                     f" | vs base {st.mean(v) - st.mean(b):+.4f} (t {paired_t(b, v):+.2f})"
                     f" | long {st.mean(col(nm, lambda z: z[1])):+.4f} short {st.mean(col(nm, lambda z: not z[1])):+.4f} | "
                     f"2012-24 {st.mean(col(nm, lambda z: z[0][:4] < '2025')):+.4f} 2025-26 {st.mean(col(nm, lambda z: z[0][:4] >= '2025')):+.4f} | "
                     + " ".join(f"{s[:6]} {st.mean(col(nm, lambda z, s=s: z[2] == s)):+.3f}" for s in SYMS))
        L.append("")
    L.append("How the trades ended (time raise 'close'), with the base result of the same trades:")
    for base, nm in (("raw", "raw + time raise (close)"), ("live (bloc T+L1.0)", "live + time raise (close)"))[:1 if RAW_ONLY else 2]:
        L.append(f"  {nm}:")
        for k in sorted({z[4][nm][1] for z in res}):
            zs = [z for z in res if z[4][nm][1] == k and (k != 0 or z[4][nm][2] < 0)]      # stops on a time-raised stop: below
            for side, sf in (("long ", lambda z: z[1]), ("short", lambda z: not z[1])):
                ss = [z for z in zs if sf(z)]
                if not ss: continue
                L.append(f"    {KIND[k]:30} {side} n {len(ss):4}  avg {st.mean(z[4][nm][0] for z in ss):+.3f}R  (base {st.mean(z[4][base][0] for z in ss):+.3f}R)")
        stp = [z for z in res if z[4][nm][1] == 0 and z[4][nm][2] >= 0]
        for q in range(len(HOURS)):
            ss = [z for z in stp if z[4][nm][2] == q]
            if ss: L.append(f"    stopped on the raised stop ({LBL[q]:6}) n {len(ss):4}  avg {st.mean(z[4][nm][0] for z in ss):+.3f}R  (base {st.mean(z[4][base][0] for z in ss):+.3f}R)")
    # where R is won and lost (trader 2026-10-04): every exit type, with what the same trades made on the raw signal
    def bucket(r):
        kd, rs = r[1], r[2]
        if kd == 0: return f"stopped on the raised stop ({LBL[rs]})" if rs >= 0 else "stopped at the signal stop (-1R)"
        return {1: "TP2", 2: "24h cut", 3: "still open at the window end"}.get(kd, KIND.get(kd))
    for nm in [n for n, su, tr in V if su == 0 and tr != 2] + ([] if RAW_ONLY else [n for n, su, tr in V if su == 1 and tr != 2]):
        base = "raw" if nm.startswith("raw") else "live (bloc T+L1.0)"
        L.append(f"\nWHERE R IS WON AND LOST - {nm}" + ("" if nm == base else f" (vs {base} on the same trades)"))
        L.append(f"  {'how the trade ended':38} {'trades':>6} {'avg R':>7} {'total R':>8} {'long':>7} {'short':>7}" +
                 ("" if nm == base else f" {'base':>8} {'gained':>7}") + f" {'med h':>6} {'best R':>6}")
        groups: dict = {}
        for z in res: groups.setdefault(bucket(z[4][nm]), []).append(z)
        for b_, zs in sorted(groups.items(), key=lambda kv: sum(z[4][nm][0] for z in kv[1])):
            v = [z[4][nm][0] for z in zs]; bb = sum(z[4][base][0] for z in zs)
            L.append(f"  {b_:38} {len(zs):6} {st.mean(v):+7.3f} {sum(v):+8.1f} {sum(z[4][nm][0] for z in zs if z[1]):+7.1f} "
                     f"{sum(z[4][nm][0] for z in zs if not z[1]):+7.1f}" + ("" if nm == base else f" {bb:+8.1f} {sum(v) - bb:+7.1f}") +
                     f" {st.median(z[4][nm][3] for z in zs):6.1f} {st.mean(z[4][nm][4] for z in zs):+6.2f}")
        v = [z[4][nm][0] for z in res]; bb = sum(z[4][base][0] for z in res)
        L.append(f"  {'TOTAL':38} {len(v):6} {st.mean(v):+7.3f} {sum(v):+8.1f} {sum(z[4][nm][0] for z in res if z[1]):+7.1f} "
                 f"{sum(z[4][nm][0] for z in res if not z[1]):+7.1f}" + ("" if nm == base else f" {bb:+8.1f} {sum(v) - bb:+7.1f}"))
    # ... and by time: when the exit happened (trading hours after the entry; an exit exactly at a check falls in the bucket ending there)
    EDGES = [4, 8, 12, 24, 48, 72, 120, float("inf")]
    LAB = ["0-4h", "4-8h", "8-12h", "12-24h", "1-2 days", "2-3 days", "3-5 days", "5+ days"]
    for nm in [n for n, su, tr in V if su == 0 and tr != 2] + ([] if RAW_ONLY else [n for n, su, tr in V if su == 1 and tr != 2]):
        L.append(f"\nWHEN R IS WON AND LOST - {nm}  (by time of the exit)")
        L.append(f"  {'exit time':10} {'trades':>6} {'avg R':>7} {'total R':>8} {'won R':>7} {'lost R':>8} {'long':>7} {'short':>7}  {'mostly':<s}")
        for e, lab in zip(EDGES, LAB):
            lo = EDGES[EDGES.index(e) - 1] if e != EDGES[0] else -1
            zs = [z for z in res if lo < z[4][nm][3] + 1e-9 <= e + 1e-6]
            if not zs: continue
            v = [z[4][nm][0] for z in zs]; cnt: dict = {}
            for z in zs: b_ = bucket(z[4][nm]); cnt[b_] = cnt.get(b_, 0) + 1
            top = ", ".join(f"{k} {n}" for k, n in sorted(cnt.items(), key=lambda kv: -kv[1])[:2])
            L.append(f"  {lab:10} {len(zs):6} {st.mean(v):+7.3f} {sum(v):+8.1f} {sum(x for x in v if x > 0):+7.1f} {sum(x for x in v if x < 0):+8.1f} "
                     f"{sum(z[4][nm][0] for z in zs if z[1]):+7.1f} {sum(z[4][nm][0] for z in zs if not z[1]):+7.1f}  {top}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
