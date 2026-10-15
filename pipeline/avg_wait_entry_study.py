#!/usr/bin/env python3
"""avg_wait_entry_study.py - trader 2026-10-15: "what happens to overall R if on a signal, we wait 4 hours and check the average before
entering? if the average is positive, enter, negative, skip until it turns positive waiting up to 24 hours".

The rule (raw management otherwise: one full entry, the signal's stop and TP2, out at TP2 / the stop / the 40-day replay end):
  - 4 h = the close of the signal's entry H4 bar (bar index, so weekends don't count); 24 h = the bar-6 close.
  - the average = the average R since the signal: the mean of every M1 bid close from the signal bar close, against the card entry and
    1R, signed by the direction (the same average as every earlier average rule and the 12 h average cut).
  - at 4 h: average > 0 -> enter at that minute's close (+ the entry-side spread). Otherwise enter at the first later minute whose
    average is > 0, up to 24 h; never -> skip (0R).
  - the stop and TP2 stay at the signal's prices. Cancelled (0R) if the stop was touched or TP2 reached before the entry.
  - the EA's R:R floor for a market entry after a wait: TP2 at least 1.0R from the fill, else refused (0R). Shown with and without.
  - R at the 1% rule: a later fill has a different stop distance, so its R is re-based to the bid distance fill -> stop
    (R = P&L / (1 + fill R)). The un-re-based number (same lots as raw, more than 1% at risk when the fill is above the signal) is shown
    once for scale.
Pre-registered lines (nothing else is tried): raw | enter at 4 h always (the delay alone) | 4 h check only (positive -> enter, else skip)
| the rule, checking every minute | the rule, checking at H4 closes only | the rule on the 12 h pill's average (the last 720 M1 closes,
which at 4 h includes 8 h from before the signal - the number the signal page shows).
Population: the combo builder's signals (AA journals, all four detectors) on the open symbols only - the research lock's locked symbols
are never loaded, and 2025-26 only for the exposed (commodity bloc) symbols. Model spread; +1 pip round trip shown too.
    python3 pipeline/avg_wait_entry_study.py        -> data/study/avg_wait_entry/report.md + trades.csv
"""
from __future__ import annotations
import csv, os, statistics as st, sys, time
import numpy as np
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import oos_queue_study as Q                    # noqa: E402
from pipeline import oos_build_inputs as OI                  # noqa: E402
from pipeline import trendcont_invert_study as TI            # noqa: E402  (replay: the raw self-check)
from live import symbol_groups as SG                         # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "avg_wait_entry")
LOCK = SG.RESEARCH_LOCK
RR_FLOOR = 1.0
LINES = [("raw", "raw signal (entered at the signal)"), ("d4", "enter at 4 h always"), ("g4", "4 h check only (else skip)"),
         ("rule", "THE RULE: 4 h check, then wait for a positive average up to 24 h"), ("rule_h4", "the rule, checked at H4 closes only"),
         ("rule_pill", "the rule on the 12 h pill's average")]


def is_open(x):
    r = OI.root(x["sym"])
    return r not in LOCK["locked"] and (int(x["t"][:4]) < LOCK["period_from"] or r in LOCK["exposed"])


def population():
    """CB.population() without ever loading a locked symbol's journal."""
    new = [s for s in OI.NEW if s != "US30.dk" and OI.root(s) not in LOCK["locked"]]
    bad, drag = Q.check_new(new)
    ALL = Q.pop_original(set(CB.ORIG), aa_trendcont=True) + Q.pop_new([s for s in new if s not in bad], drag) + CB.holdout_rows()
    return sorted([x for x in ALL if is_open(x)], key=lambda x: (x["sym"], x["t"])), bad


def exit_from(F, a, tp2):
    """a full position held from minute a (its fill already taken): the exit in the card's R. Stop first inside a minute (the engine's
    order), a gap through the stop fills at the open, TP2 fills at TP2, else the last close of the replay."""
    xl, xh = F["xl"][a:], F["xh"][a:]
    hit = xl <= -1.0
    if tp2 is not None: hit = hit | (xh >= tp2)
    if not hit.any(): return float(F["xc"][F["n"] - 1]), "end"
    m = a + int(hit.argmax())
    if F["xl"][m] <= -1.0: return float(min(-1.0, F["xo"][m])), "stop"
    return float(tp2), "tp2"


def sim(S, x):
    u = A.setup(S, x, CB.MODE)
    if not u: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], CB.MODE, u["scale"])
    n, h4, i0, k0, tp2 = F["n"], x["bars"], u["i0"], u["k0"], u["tp2_R"]
    kbar = lambda N: (int(np.searchsorted(S.t, A.tmin(h4[i0 + N][0]))) - 1 - k0) if i0 + N < len(h4) else -1
    ks = [kbar(N) for N in range(1, 7)]
    if not (0 <= ks[0] < n): return None
    ks = [k if 0 <= k < n else n - 1 for k in ks]
    k4, k24 = ks[0], ks[5]
    bc = F["bc"]; avg = np.cumsum(bc[:k24 + 1]) / np.arange(1, k24 + 2)
    if k0 < 720: return None
    sg = 1.0 if u["up"] else -1.0
    pre = (np.asarray(S.c[k0 - 720:k0], dtype=float) - u["e"]) * sg / u["R"]
    z = np.concatenate([pre, bc[:k24 + 1]]); cz = np.concatenate([[0.0], np.cumsum(z)])
    pill = (cz[720 + 1:720 + k24 + 2] - cz[1:k24 + 2]) / 720.0                  # pill[j] = mean of the 720 closes ending at minute j
    # minutes before which the setup is gone: the stop touched / TP2 reached (exit side, like the trade itself)
    gone = F["xl"][:k24 + 1] <= -1.0
    if tp2 is not None: gone = gone | (F["xh"][:k24 + 1] >= tp2)
    first_gone = int(gone.argmax()) if gone.any() else 10 ** 9
    pips = abs(x["e"] - x["s"]) / (10 * A.TICK[x["sym"]])

    def entered(j):
        """fill at minute j's close -> dict, or the reason it did not happen."""
        if j is None: return {"why": "never positive"}
        if first_gone <= j: return {"why": "stop/TP2 before entry"}
        f = float(bc[j] + F["eo"][j]); risk = 1.0 + float(bc[j])
        rr = (tp2 - f) / (1.0 + f) if tp2 is not None else 99.0
        ex, kind = exit_from(F, j + 1, tp2)
        return {"j": j, "f": float(bc[j]), "rr": rr, "orig": ex - f, "R": (ex - f) / risk, "R1": (ex - f) / risk - 1.0 / pips / risk,
                "kind": kind}

    first = lambda m, lo, hi: (lo + int(m[lo:hi + 1].argmax())) if m[lo:hi + 1].any() else None
    res = {}
    ex, kind = exit_from(F, 0, tp2); f0 = float(F["eo"][0])
    res["raw"] = {"j": 0, "f": 0.0, "rr": 99.0, "orig": ex - f0, "R": ex - f0, "R1": ex - f0 - 1.0 / pips, "kind": kind}
    res["d4"] = entered(k4)
    res["g4"] = entered(k4 if avg[k4] > 0 else None)
    res["rule"] = entered(first(avg > 0, k4, k24))
    hs = [k for k in ks if avg[k] > 0]
    res["rule_h4"] = entered(hs[0] if hs else None)
    res["rule_pill"] = entered(first(pill > 0, k4, k24))
    return {"raw_check": TI.replay(S, x), "res": res, "k4": k4, "k24": k24, "tp2": tp2}


def val(r, key="R", floor=True):
    if "j" not in r: return 0.0
    if floor and r["rr"] < RR_FLOOR: return 0.0
    return r[key]


def taken(r, floor=True): return "j" in r and (not floor or r["rr"] >= RR_FLOOR)


def maxdd(v):
    c = np.cumsum(v); return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], c]))[1:] - c)) if len(v) else 0.0


def tstat(d):
    if len(d) < 2: return 0.0
    sd = st.stdev(d); return st.mean(d) / (sd / len(d) ** 0.5) if sd else 0.0


def table(rows, L, key="R", floor=True):
    L.append("| line | taken | R / signal | total R | vs raw / signal | t vs raw | max DD R |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    raw = [val(z["res"]["raw"], key, False) for z in rows]
    for k, lbl in LINES:
        v = [val(z["res"][k], key, floor) if k != "raw" else val(z["res"]["raw"], key, False) for z in rows]
        d = [a - b for a, b in zip(v, raw)]
        tk = sum(1 for z in rows if (taken(z["res"][k], floor) if k != "raw" else True))
        L.append(f"| {lbl} | {tk} ({tk / len(rows) * 100:.0f}%) | {st.mean(v):+.3f} | {sum(v):+.1f} | "
                 f"{st.mean(d):+.3f} | {tstat(d) if k != 'raw' else 0:+.2f} | {maxdd(v):.1f} |")


def main():
    os.makedirs(OUT, exist_ok=True); t0 = time.time()
    pop, bad = population()
    print(f"population {len(pop)} open signals; not ready: {bad}", flush=True)
    rows = []; worst = 0.0
    for sym in sorted({x["sym"] for x in pop}):
        S = CB.sym_obj(sym); grp = (SG.group_of(OI.root(sym)) or {"name": "other"})["name"]
        for x in [x for x in pop if x["sym"] == sym]:
            o = sim(S, x)
            if o is None or o["raw_check"] is None: continue
            worst = max(worst, abs(o["raw_check"][0] - o["res"]["raw"]["R"]))
            rows.append(dict(o, sym=OI.root(sym), grp=grp, t=x["t"], year=int(x["t"][:4]), strat=x["strat"], dir="BUY" if x["up"] else "SELL"))
        print(f"{sym}: {sum(1 for z in rows if z['sym'] == OI.root(sym))} trades, {time.time() - t0:.0f}s", flush=True)
    rows.sort(key=lambda z: z["t"])

    with open(os.path.join(OUT, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "grp", "t", "strat", "dir", "tp2_R"] + [f"{k}_{c}" for k, _ in LINES for c in ("R", "R1", "orig", "fill", "rr", "min", "exit", "why")])
        for z in rows:
            out = [z["sym"], z["grp"], z["t"], z["strat"], z["dir"], f"{z['tp2']:.3f}" if z["tp2"] is not None else ""]
            for k, _ in LINES:
                r = z["res"][k]
                out += ([f"{r['R']:.4f}", f"{r['R1']:.4f}", f"{r['orig']:.4f}", f"{r['f']:.4f}", f"{r['rr']:.3f}", r["j"], r["kind"], ""]
                        if "j" in r else ["", "", "", "", "", "", "", r["why"]])
            w.writerow(out)

    L = [f"# Wait 4 h, enter on a positive average (up to 24 h) - trader 2026-10-15", "",
         f"{len(rows)} signals (open symbols; locked never loaded; 2025-26 bloc only), {time.time() - t0:.0f}s. Raw self-check vs the "
         f"rule engine's raw replay: max |diff| {worst:.2e}R.", "",
         "Every line counts every signal (a skip = 0R), paired with raw on the same signals. R at the 1% rule (a later fill re-based to its "
         "own stop distance). Model spread. The EA's R:R floor (TP2 >= 1.0R from the fill) applied unless said.", ""]
    def block(title, rs):
        if not rs: return
        L.extend([f"## {title} ({len(rs)} signals)", ""]); table(rs, L); L.append("")
    block("All open signals", rows)
    L.extend(["### Same, +1 pip round trip", ""]); table(rows, L, key="R1"); L.append("")
    L.extend(["### Same, without the EA's R:R floor", ""]); table(rows, L, floor=False); L.append("")
    L.extend(["### Same, NOT re-based (same lots as raw - more than 1% at risk when the fill is above the signal)", ""])
    table(rows, L, key="orig"); L.append("")

    # what the rule does, signal by signal
    rr = [z["res"]["rule"] for z in rows]; raw = [z["res"]["raw"]["R"] for z in rows]
    ent = [i for i, r in enumerate(rr) if taken(r)]
    at4 = [i for i in ent if rr[i]["j"] == rows[i]["k4"]]
    why = {}
    for r in rr:
        w_ = r.get("why") or ("R:R floor refused" if r["rr"] < RR_FLOOR else None)
        if w_: why[w_] = why.get(w_, 0) + 1
    sk = [i for i in range(len(rows)) if i not in set(ent)]
    hrs = [(rr[i]["j"] - rows[i]["k4"]) / 60.0 for i in ent if i not in set(at4)]
    L.extend(["## What the rule did", "",
              f"- entered {len(ent)} of {len(rows)}: {len(at4)} at the 4 h check, {len(ent) - len(at4)} later "
              f"(median {st.median(hrs) if hrs else 0:.1f} trading h after the 4 h check)." if ent else "- entered none.",
              f"- skipped {len(sk)}: " + ", ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])) + ".",
              f"- the skipped signals' raw R: {st.mean([raw[i] for i in sk]):+.3f} / signal, total {sum(raw[i] for i in sk):+.1f}R "
              f"(what skipping them saved or cost).",
              f"- the entered signals: raw {st.mean([raw[i] for i in ent]):+.3f} / signal (total {sum(raw[i] for i in ent):+.1f}R) vs entered "
              f"late {st.mean([rr[i]['R'] for i in ent]):+.3f} (total {sum(rr[i]['R'] for i in ent):+.1f}R); median fill "
              f"{st.median([rr[i]['f'] for i in ent]):+.2f}R from the signal price (the stop is then {1 + st.median([rr[i]['f'] for i in ent]):.2f}x as far).",
              f"- exits of the entered: " + ", ".join(f"{k} {sum(1 for i in ent if rr[i]['kind'] == k)}" for k in ("tp2", "stop", "end")) +
              f" | the same signals raw: " + ", ".join(f"{k} {sum(1 for i in ent if rows[i]['res']['raw']['kind'] == k)}" for k in ("tp2", "stop", "end")), ""])

    # the benchmark: skipping the same share of signals at random keeps raw's average per trade (expected R = raw mean x share taken)
    L.extend(["## Against skipping the same share at random", "",
              "Expected R / signal of taking the same share of signals at random = raw's mean x the share the rule takes.", "",
              "| group | signals | raw | the rule | random skip, same share | rule - random | same, +1 pip |", "|---|---:|---:|---:|---:|---:|---:|"])
    for g in [None] + sorted({z["grp"] for z in rows}):
        rs = [z for z in rows if g is None or z["grp"] == g]; fr = sum(1 for z in rs if taken(z["res"]["rule"])) / len(rs)
        m = {k: (st.mean([z["res"]["raw"][k] for z in rs]), st.mean([val(z["res"]["rule"], k) for z in rs])) for k in ("R", "R1")}
        L.append(f"| {g or 'all'} | {len(rs)} | {m['R'][0]:+.3f} | {m['R'][1]:+.3f} | {m['R'][0] * fr:+.3f} | {m['R'][1] - m['R'][0] * fr:+.3f} | "
                 f"{m['R1'][1] - m['R1'][0] * fr:+.3f} |")
    L.append("")
    for g in sorted({z["grp"] for z in rows}): block(g, [z for z in rows if z["grp"] == g])
    for s_ in sorted({z["strat"] for z in rows}): block(f"Detector {s_}", [z for z in rows if z["strat"] == s_])
    for d in ("BUY", "SELL"): block(d, [z for z in rows if z["dir"] == d])
    block("2012-2018", [z for z in rows if z["year"] <= 2018]); block("2019-2024", [z for z in rows if 2019 <= z["year"] <= 2024])
    block("2025-2026 (commodity bloc only - the rest is research-locked)", [z for z in rows if z["year"] >= 2025])
    for s_ in sorted({z["sym"] for z in rows}): block(s_, [z for z in rows if z["sym"] == s_])
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L)); print("\n".join(L))


if __name__ == "__main__": main()
