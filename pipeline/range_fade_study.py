#!/usr/bin/env python3
"""range_fade_study.py - coach item 27 (2026-10-08): a range-FADE detector on the commodity bloc, FROZEN spec, one run.

THE DETECTOR (H4; frozen - a change is a new pre-registration). Bar-index rule: for a trigger test on closed bar i, every context
value (the box, its touches, ATR, the EMA slope) comes from the closed bars BEFORE i (window i-24 .. i-1, ATR and EMA at i-1); the D1
regime tag is the last COMPLETED day strictly before bar i's day. Bar i itself is only tested for the pierce-and-close-back.
  1. Box: the highest high / lowest low of the 24 closed H4 bars before the trigger bar; each edge touched at least twice inside the
     window (a touch = a bar whose high is within 0.25 ATR of the box high / whose low within 0.25 ATR of the box low; the bar that
     sets the extreme counts); height >= 2.5 ATR(14). ATR = Wilder's ATR(14) on H4 (the study convention, breakout_backtest.py).
     Regime: the frozen D1 tag (trader_selection_study.regime_series) is CHOP, OR the H4 EMA20 moved within +-0.1 ATR over the last
     12 bars (EMA20[i-1] - EMA20[i-13], in ATR at i-1).
  2. Trigger: bar i's high trades above the box high and its close is back inside by at least 0.1 ATR (close <= box high - 0.1 ATR
     and above the box low): SELL; mirror for BUY. Entry at bar i's close.
  3. Stop 0.5 ATR beyond the box extreme (box high + 0.5 ATR for a SELL); TP1 the box midpoint; TP2 the opposite edge.
  4. Validity: once a bar closes outside a box (a box = the 24-bar window's (high, low) pair that meets the touch and height rules),
     that box gives no signal again; a new box is a different (high, low) pair that meets the rules. One signal per touch: after a
     signal the detector is disarmed until a later bar trades through that box's midpoint, or a later bar closes outside that box
     (it is then spent, and the next box that forms is a new one) - checked after that bar's own trigger test.
     [Implementation note, run 2: run 1 re-armed ONLY on the midpoint, so after a box broke away the detector never fired again
     (53 signals, all 2012-14; data/study/range_fade/report_run1_implementation_fault.md). Item 4's "until a new box forms" also
     re-arms it - the coach's spec text is unchanged; the holdout was not opened.]
     A trigger that fails ONLY the 2.5 ATR floor is counted ("removed by the floor") and changes no state.
  5. Gates, as live (applied in time order across the run's symbols): spread - the entry spread over 0.10R of the stop is refused
     (live max_spread_r; the live hold-and-re-offer is not modelled); events - a symbol-relevant (base / quote / All) class V event
     within 6 h after the entry, or a class W event within 72 h, skips the signal (doctrine: V-class automatic skip 6 h; W-class
     weekend-hold, approximated as 72 h); one position per symbol (the live one-setup lock); correlation - the code-5 veto
     (live/correlation.py): this signal's 1R + the open unbanked risk of the study's other open trades x weight (1.0 at signed 60-day
     D1 log-return correlation >= 0.6, 0.5 at 0.3-0.6, 0 below; closed weekday days strictly before the signal day) above 1.5R is
     refused (raw management never banks or moves a stop, so every open trade carries its full 1R).
THE TEST: raw management on the M1 engine (100% at the signal, the signal stop, out at TP2 or the stop; still open at the 40-day
replay end -> closed at the last close), model spread (pipeline/ask_side_study spread profile - a bar MINIMUM, understated for FX) and
+1 pip round trip (1 pip / stop pips per trade) side by side; the gate reads +1 pip. Minimal is NOT reported (trader 2026-10-08:
"minimal is shaped and should not ever be compared" - the coach asked for it for reference only).
  Development: AUDUSD, NZDUSD, USDCAD, signals 2012-2024 (their own gate interactions only).
  Holdout, ONE look: AUDNZD, AUDCAD, NZDCAD (all years) + 2025-26 on all six - simulated with all six together; opened only by
  `look`, after `dev` has written the frozen spec and the development result into the report.
GATE (the breakout candidates' gate): mean >= +0.15R at +1 pip; n >= 150 development signals; both halves of 2012-24 positive (split
at 2018-07-01, +1 pip); no symbol > 50% of net development R (+1 pip); the holdout not negative (+1 pip).
    python3 pipeline/range_fade_study.py dev     -> data/study/range_fade/report.md (spec + development) + dev_trades.csv
    python3 pipeline/range_fade_study.py look    -> appends the holdout section + holdout_trades.csv (refuses without dev)
"""
from __future__ import annotations
import bisect, csv, datetime as dt, json, math, os, statistics as stt, sys, time
import numpy as np
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_cards as BC                    # noqa: E402
from pipeline import backtest_rules as BR                    # noqa: E402
from live import backtest_engine as E                        # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "study", "range_fade"); REPORT = os.path.join(OUT, "report.md")
BOX_N, ATR_N, TOUCH_ATR, MIN_TOUCH, MIN_H_ATR = 24, 14, 0.25, 2, 2.5
SLOPE_N, SLOPE_MAX, BACK_ATR, SL_ATR = 12, 0.1, 0.1, 0.5
SPREAD_MAX_R, V_H, W_H, CORR_CAP, CORR_N = 0.10, 6, 72, 1.5, 60
DEV = ("AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"); HOLD_X = ("AUDNZD.dk", "AUDCAD.dk", "NZDCAD.dk"); DEV_LAST, HOLD_FROM = 2024, 2025
HALF = "2018.07.01"
RAW = {"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": True, "levers": []}
KIND = {0: "stopped", 1: "TP2", 3: "replay end"}


def wilder_atr(h, l, c, n=ATR_N):
    tr = [h[0] - l[0]] + [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1])) for k in range(1, len(c))]
    out = [None] * len(c); s = None
    for k in range(len(c)):
        if k == n - 1: s = sum(tr[:n]) / n
        elif k >= n: s = (s * (n - 1) + tr[k]) / n
        out[k] = s
    return out


def detect(sym: str, h4: list, ctx: dict, years: tuple) -> tuple[list, dict]:
    """the frozen detector on one symbol's H4 bars -> (signals, counters)."""
    o = [b[1] for b in h4]; h = [b[2] for b in h4]; l = [b[3] for b in h4]; c = [b[4] for b in h4]
    atr = wilder_atr(h, l, c); ema20 = BC.ema(c, 20)
    sig, invalid, armed, mid_arm, last_sig, sig_box = [], set(), True, None, -1, None
    cnt = {"triggers": 0, "removed_by_floor": 0, "not_armed": 0, "box_invalidated": 0, "regime_fail": 0}
    for i in range(BOX_N + ATR_N + SLOPE_N + 1, len(h4)):
        j = i - 1; a = atr[j]
        if not a: continue
        y = int(h4[i][0][:4])
        win = range(i - BOX_N, i); bh = max(h[k] for k in win); bl = min(l[k] for k in win)
        th = sum(1 for k in win if h[k] >= bh - TOUCH_ATR * a); tl = sum(1 for k in win if l[k] <= bl + TOUCH_ATR * a)
        shape = th >= MIN_TOUCH and tl >= MIN_TOUCH; tall = (bh - bl) >= MIN_H_ATR * a; key = (bh, bl)
        sday = BC.day_of(h4[i][0]); kd = ctx["day_idx"].get(sday)
        tag = ctx["regime"][kd - 1] if kd else ""
        flat = abs(ema20[j] - ema20[j - SLOPE_N]) <= SLOPE_MAX * a
        reg = tag == "CHOP" or flat
        up_t = l[i] < bl and c[i] >= bl + BACK_ATR * a and c[i] < bh          # BUY: pierced below, closed back inside
        dn_t = h[i] > bh and c[i] <= bh - BACK_ATR * a and c[i] > bl          # SELL
        if (up_t or dn_t) and shape and years[0] <= y <= years[1]:
            cnt["triggers"] += 1
            if key in invalid: cnt["box_invalidated"] += 1
            elif not armed: cnt["not_armed"] += 1
            elif not reg: cnt["regime_fail"] += 1
            elif not tall: cnt["removed_by_floor"] += 1
            else:
                up = bool(up_t); e = c[i]; mid = (bh + bl) / 2
                s = (bl - SL_ATR * a) if up else (bh + SL_ATR * a); tp2 = bh if up else bl
                sig.append({"sym": sym, "i": i, "t": h4[i][0], "year": y, "up": up, "e": e, "s": s, "tp1": mid, "tp2": tp2,
                            "height_atr": (bh - bl) / a, "regime": tag, "flat": flat, "atr": a})
                armed, mid_arm, last_sig, sig_box = False, mid, i, (bh, bl)
        if shape and tall and (c[i] > bh or c[i] < bl): invalid.add(key)       # closed outside the box: that box is spent
        if not armed and i > last_sig and (l[i] <= mid_arm <= h[i] or c[i] > sig_box[0] or c[i] < sig_box[1]): armed = True   # midpoint, or the box broke
    return sig, cnt


def daily_closes(h4: list) -> dict:
    out = {}
    for b in h4:
        d = BC.day_of(b[0])
        if dt.datetime.strptime(d, "%Y.%m.%d").weekday() < 5: out[d] = b[4]
    return out


def corr60(a: dict, b: dict, before: str):
    days = [d for d in sorted(set(a) & set(b)) if d < before][-(CORR_N + 1):]
    if len(days) < 31: return None
    ra = [math.log(a[days[k]] / a[days[k - 1]]) for k in range(1, len(days))]; rb = [math.log(b[days[k]] / b[days[k - 1]]) for k in range(1, len(days))]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb); va = sum((x - ma) ** 2 for x in ra); vb = sum((x - mb) ** 2 for x in rb)
    if va <= 0 or vb <= 0: return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / math.sqrt(va * vb)


def run(symbols: tuple, years: tuple) -> tuple[list, dict]:
    """detector + raw M1 replay per symbol, then the live gates in time order across the symbols. -> (trades incl. refused, counters)."""
    ev = BC.events_feed(); evt = [x[0] for x in ev]; trades, cnt, closes = [], {}, {}
    for sym in symbols:
        S = CB.sym_obj(sym); h4 = S.h4; ctx = BC.prep_symbol(h4); closes[sym] = daily_closes(h4)
        sig, c1 = detect(sym, h4, ctx, years); cnt[sym] = dict(c1, no_m1=0)
        for x0 in sig:
            x = {"sym": sym, "t": x0["t"], "strat": "RangeFade", "bars": h4, "i0": x0["i"] + 1, "e": x0["e"], "s": x0["s"], "tp1": x0["tp1"],
                 "tp2": x0["tp2"], "up": x0["up"], "cost": 0.0}
            p = CB.prep(S, x)
            if p is None: cnt[sym]["no_m1"] += 1; continue
            F = dict(p["F"]); fctx, _, _ = BR.full_ctx(S, x, p)
            r = E.run(BR.NB, F, fctx, E.compile_cfg(E.validate(RAW)[0], fctx, True))
            k0 = p["k0"]; t_in = int(S.t[k0]); t_out = int(S.t[min(k0 + int(r[2]), len(S.t) - 1)])
            pips = abs(x["e"] - x["s"]) / (10 * A.TICK[sym])
            trades.append(dict(x0, R=float(r[0]), R1=float(r[0]) - 1.0 / pips, kind=KIND.get(int(r[1]), str(r[1])), t_in=t_in, t_out=t_out,
                               spread_R=float(F["spR"][0]), stop_pips=pips, root=sym.split(".")[0]))
    trades.sort(key=lambda z: (z["t_in"], z["sym"])); open_, g = [], {"spread": 0, "event_V": 0, "event_W": 0, "lock": 0, "correlation": 0}
    for z in trades:
        base, quote = z["root"][:3], z["root"][3:6]; why = None
        if z["spread_R"] > SPREAD_MAX_R: why = "spread"
        if not why:
            k = bisect.bisect_right(evt, z["t_in"])
            while k < len(ev) and ev[k][0] <= z["t_in"] + W_H * 60:
                tm, cy, _, cl = ev[k]; k += 1
                if cy in (base, quote) or cy.lower() == "all":
                    if cl == "V" and tm <= z["t_in"] + V_H * 60: why = "event_V"; break
                    if cl == "W": why = "event_W"; break
        open_ = [q for q in open_ if q["t_out"] > z["t_in"]]
        if not why and any(q["sym"] == z["sym"] for q in open_): why = "lock"
        if not why:
            day = BC.day_of(z["t"]); risk = 1.0
            for q in open_:
                cr = corr60(closes[z["sym"]], closes[q["sym"]], day)
                sc = 0.0 if cr is None else (cr if q["up"] == z["up"] else -cr)
                risk += 1.0 if sc >= 0.6 else (0.5 if sc >= 0.3 else 0.0)
            if risk > CORR_CAP + 1e-9: why = "correlation"
        z["refused"] = why
        if why: g[why] += 1
        else: open_.append(z)
    cnt["gates"] = g
    return trades, cnt


def stats(xs: list, k: str) -> dict:
    v = [z[k] for z in xs]; n = len(v)
    if not n: return {"n": 0}
    m = sum(v) / n; sd = stt.stdev(v) if n > 1 else 0.0
    return {"n": n, "mean": m, "total": sum(v), "t": (m / (sd / n ** 0.5)) if sd > 0 else None, "win": sum(1 for x in v if x > 0) / n}


def fmt_row(label, xs):
    a, b = stats(xs, "R"), stats(xs, "R1")
    if not a["n"]: return f"| {label} | 0 | - | - | - | - | - | - |"
    t = lambda s: f"{s['t']:+.2f}" if s.get("t") is not None else "-"
    return f"| {label} | {a['n']} | {a['mean']:+.3f} | {a['total']:+.1f} | {t(a)} | {b['mean']:+.3f} | {b['total']:+.1f} | {t(b)} | {a['win'] * 100:.0f}% |"


HDR = "| cell | n | mean R (model) | total R (model) | t (model) | mean R (+1 pip) | total R (+1 pip) | t (+1 pip) | win % |\n|---|---|---|---|---|---|---|---|---|"


def section(title: str, tr: list, cnt: dict, syms: tuple) -> list:
    tk = [z for z in tr if not z["refused"]]
    L = [f"## {title}", "", f"Detector signals {len(tr)} (after the M1 replay); refused by the gates {sum(1 for z in tr if z['refused'])} "
         f"({cnt['gates']}); taken **{len(tk)}**.", ""]
    L += ["Detector counters per symbol (triggers on a box with the touch rule; why they did not signal):", ""]
    L += [f"- {s.split('.')[0]}: " + ", ".join(f"{k} {v}" for k, v in cnt[s].items()) for s in syms]
    L += ["", HDR, fmt_row("ALL", tk)]
    L += [fmt_row(s.split(".")[0], [z for z in tk if z["sym"] == s]) for s in syms]
    L += [fmt_row("BUY", [z for z in tk if z["up"]]), fmt_row("SELL", [z for z in tk if not z["up"]])]
    L += [fmt_row(f"before {HALF}", [z for z in tk if z["t"] < HALF]), fmt_row(f"from {HALF}", [z for z in tk if z["t"] >= HALF])]
    L += ["", "Signals per year (taken): " + ", ".join(f"{y} {sum(1 for z in tk if z['year'] == y)}" for y in sorted({z['year'] for z in tk}))]
    L += ["Exit kinds: " + ", ".join(f"{k} {sum(1 for z in tk if z['kind'] == k)}" for k in sorted({z['kind'] for z in tk}))]
    hs = sorted(z["height_atr"] for z in tk)
    if hs: L += [f"Box height in ATR (taken): min {hs[0]:.2f}, quartiles {hs[len(hs) // 4]:.2f} / {hs[len(hs) // 2]:.2f} / {hs[3 * len(hs) // 4]:.2f}, max {hs[-1]:.2f}; "
                 + ", ".join(f"{lo}-{hi} ATR {sum(1 for x in hs if lo <= x < hi)}" for lo, hi in ((2.5, 3), (3, 4), (4, 5), (5, 7), (7, 99)))]
    sp = sorted(z["stop_pips"] for z in tk)
    if sp: L += [f"Stop distance in pips (taken): median {sp[len(sp) // 2]:.1f}, 10th pct {sp[len(sp) // 10]:.1f} - the +1 pip cost is 1/that in R."]
    L += [f"Regime route (taken): CHOP tag {sum(1 for z in tk if z['regime'] == 'CHOP')}, flat EMA only {sum(1 for z in tk if z['regime'] != 'CHOP')}"]
    srt = sorted(tk, key=lambda z: z["R1"])
    L += ["", "Ten largest losers / winners (+1 pip): " + "; ".join(f"{z['root']} {z['t'][:10]} {'BUY' if z['up'] else 'SELL'} {z['R1']:+.2f}" for z in srt[:10]),
          " / " + "; ".join(f"{z['root']} {z['t'][:10]} {'BUY' if z['up'] else 'SELL'} {z['R1']:+.2f}" for z in srt[::-1][:10]), ""]
    return L


def write_trades(path, tr):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sym", "signal_bar", "dir", "entry", "sl", "tp1", "tp2", "height_atr", "regime", "flat_ema", "spread_R", "stop_pips",
                                       "R_model", "R_1pip", "exit", "refused"])
        for z in tr: w.writerow([z["root"], z["t"], "BUY" if z["up"] else "SELL", z["e"], z["s"], z["tp1"], z["tp2"], round(z["height_atr"], 3), z["regime"],
                                 int(z["flat"]), round(z["spread_R"], 4), round(z["stop_pips"], 1), round(z["R"], 4), round(z["R1"], 4), z["kind"], z["refused"] or ""])


def dev_gate(tk: list) -> list:
    a = stats(tk, "R1"); h1 = stats([z for z in tk if z["t"] < HALF], "R1"); h2 = stats([z for z in tk if z["t"] >= HALF], "R1")
    net = sum(z["R1"] for z in tk); share = {s: sum(z["R1"] for z in tk if z["sym"] == s) for s in DEV}
    top = max(share.values()) / net if net > 0 else None
    return [("mean >= +0.15R a trade at +1 pip", a.get("n") and a["mean"] >= 0.15, f"{a.get('mean', 0):+.3f}R"),
            ("n >= 150 development signals", a.get("n", 0) >= 150, f"n {a.get('n', 0)}"),
            ("both halves of 2012-24 positive (+1 pip)", h1.get("n") and h2.get("n") and h1["total"] > 0 and h2["total"] > 0,
             f"{h1.get('total', 0):+.1f}R / {h2.get('total', 0):+.1f}R"),
            ("no symbol > 50% of net R (+1 pip)", net > 0 and top is not None and top <= 0.5,
             "net R not positive" if net <= 0 else ", ".join(f"{s.split('.')[0]} {v / net * 100:.0f}%" for s, v in share.items()))]


def cmd_dev():
    os.makedirs(OUT, exist_ok=True); t0 = time.time()
    if os.path.exists(REPORT) and "## Development" in open(REPORT).read(): sys.exit("REFUSED: the development section already exists - the spec is frozen, one run")
    tr, cnt = run(DEV, (2012, DEV_LAST)); write_trades(os.path.join(OUT, "dev_trades.csv"), tr)
    tk = [z for z in tr if not z["refused"]]; gate = dev_gate(tk); ok = all(g[1] for g in gate)
    doc = open(__file__).read(); spec = doc[doc.index('"""') + 3:doc.index("    python3 pipeline/range_fade_study.py dev")]
    L = ["# Range-fade detector on the commodity bloc - coach item 27 (2026-10-08)", "", f"Run {time.strftime('%Y-%m-%d %H:%M')}; script pipeline/range_fade_study.py.", "",
         "## The frozen spec (written before the holdout was opened)", "", "```", spec.strip(), "```", "",
         "Data: QDM M1 (bid) + the .dk H4 feed, 2012-01-01 to 2026-10-01 for all six symbols (pipeline/m1_cache). Costs: the model spread "
         "profile (a bar minimum - understates FX spreads) and +1 pip round trip; no swaps or commission. Minimal not reported (trader ruling).", ""]
    L += section("Development - AUDUSD, NZDUSD, USDCAD, 2012-2024", tr, cnt, DEV)
    L += ["### Gate on development (+1 pip)", "", "| criterion | result | value |", "|---|---|---|"] + [f"| {g[0]} | {'PASS' if g[1] else 'FAIL'} | {g[2]} |" for g in gate]
    L += ["", f"**Development {'PASSES' if ok else 'FAILS'} its four criteria.** " + ("The holdout may be opened (`look`)." if ok else
          "The holdout is NOT opened: a failed development needs no look, and the look stays unspent until the coach and trader decide."), "",
          f"({time.time() - t0:.0f}s)", ""]
    open(REPORT, "w").write("\n".join(L)); print("\n".join(L[L.index("### Gate on development (+1 pip)"):]))


def cmd_look():
    rep = open(REPORT).read() if os.path.exists(REPORT) else ""
    if "## Development" not in rep or "## The frozen spec" not in rep: sys.exit("REFUSED: write the frozen spec and the development result first (dev)")
    if "## Holdout" in rep: sys.exit("REFUSED: the holdout has already been opened - one look")
    if "Development PASSES" not in rep and "--anyway" not in sys.argv: sys.exit("REFUSED: development failed - the look needs the coach and trader's word (--anyway)")
    from pipeline import cbloc_holdout_look as HL
    HL.register_holdout()
    tr, cnt = run(DEV + HOLD_X, (2012, 2026))
    hold = [z for z in tr if z["sym"] in HOLD_X or z["year"] >= HOLD_FROM]; write_trades(os.path.join(OUT, "holdout_trades.csv"), hold)
    hc = {s: cnt[s] for s in DEV + HOLD_X}; hc["gates"] = {k: sum(1 for z in hold if z["refused"] == k) for k in cnt["gates"]}
    L = section("Holdout (one look) - AUDNZD, AUDCAD, NZDCAD all years + 2025-26 on all six", hold, hc, DEV + HOLD_X)
    tk = [z for z in hold if not z["refused"]]
    L += ["By holdout part (taken):", "", HDR, fmt_row("the three crosses, 2012-2024", [z for z in tk if z["sym"] in HOLD_X and z["year"] < HOLD_FROM]),
          fmt_row("all six, 2025-2026", [z for z in tk if z["year"] >= HOLD_FROM])]
    hs = stats(tk, "R1"); ok = hs.get("n") and hs["total"] >= 0
    L += ["", f"### Gate on the holdout: not negative at +1 pip - **{'PASS' if ok else 'FAIL'}** ({hs.get('total', 0):+.1f}R over {hs.get('n', 0)} trades)", ""]
    open(REPORT, "a").write("\n".join(L)); print("\n".join(L))


if __name__ == "__main__":
    {"dev": cmd_dev, "look": cmd_look}[sys.argv[1]]()
