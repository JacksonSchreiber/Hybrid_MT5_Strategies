#!/usr/bin/env python3
"""archive_bundles.py - coach 2026-09-24 item 2: build blind bundles for record TrendCont setups.

10 setups that ran to >= +2R and 10 that went straight to the stop, drawn from the graded record
(data/study/trendcont/<SYM>.csv). Each bundle is what the advisor would have seen at the signal bar and NOTHING after
it: the charts are cut at that bar, the axis shows "-N bars" instead of dates, and the card carries no date, no
outcome and no calendar. The symbol stays visible, as the coach specified.

    python3 pipeline/archive_bundles.py --out /tmp/arc            # build + manifest
The manifest (which id is a winner) is written next to the bundles as manifest.json and MUST NOT be copied to the box.
"""
from __future__ import annotations
import argparse, csv, glob, json, os, random, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import charts, overlays as OV                                     # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REC = os.path.join(ROOT, "data", "study", "trendcont")
BARS = {tf: os.path.join(ROOT, "data", "study", tf) for tf in ("h4", "d1")}
WIN_R, LOSS_R, LOSS_MFE = 2.0, -0.95, 0.25


def load_bars(sym: str, tf: str) -> list[list]:
    p = os.path.join(BARS[tf], f"{sym}.csv")
    if not os.path.exists(p): return []
    out = []
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            t = int(datetime.strptime(r["time"], "%Y.%m.%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
            out.append([t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), 0])
    return out


def atr14(bars, i, n=14):
    if i < n: return 0.0
    tr = [max(bars[j][2] - bars[j][3], abs(bars[j][2] - bars[j - 1][4]), abs(bars[j][3] - bars[j - 1][4]))
          for j in range(max(1, i - 200), i + 1)]
    if len(tr) < n: return 0.0
    a = sum(tr[:n]) / n
    for x in tr[n:]: a = (a * (n - 1) + x) / n
    return a


def pick(seed: int):
    wins, losses = [], []
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            try: rm, mfe = float(r["r_multiple"]), float(r.get("mfe_r") or 0)
            except (TypeError, ValueError): continue
            if rm >= WIN_R: wins.append(r)
            elif rm <= LOSS_R and mfe <= LOSS_MFE: losses.append(r)
    rnd = random.Random(seed)

    def spread(rows, k):                          # round-robin by symbol so one instrument cannot dominate the set
        by = {}
        for r in rows: by.setdefault(r["symbol"], []).append(r)
        for v in by.values(): rnd.shuffle(v)
        out, syms = [], sorted(by)
        while len(out) < k and any(by.values()):
            for s in syms:
                if by[s] and len(out) < k: out.append(by[s].pop())
        return out
    return spread(wins, 10), spread(losses, 10), len(wins), len(losses)


def card(r: dict, bars: list[list], i: int) -> str:
    e, s = float(r["orig_entry"]), float(r["orig_sl"])
    tp1, tp2 = float(r["orig_tp1"] or 0), float(r["orig_tp2"] or 0)
    R = abs(e - s); up = r["direction"].upper().startswith("B")
    dg = max(2, min(5, len(f"{e}".split(".")[1]) if "." in f"{e}" else 2))
    a = atr14(bars, i)
    sw = OV.swings(bars[:i + 1], 14, 2); kind = "hi" if up else "lo"
    tbl = OV.swing_table(bars[:i + 1], 5)
    row = lambda xs: " · ".join(f"{x['p']:.{dg}f} ({x['bars_ago']} bars ago)" for x in xs) or "(none in the window)"
    j0 = next((i - x["bars_ago"] for x in sw if x["kind"] == kind and x["bars_ago"] > 0), max(0, i - 7))
    xs = [(bars[j][2] - bars[j][3]) / a for j in range(max(0, j0), i + 1)] if a > 0 else []
    pair = max((min(xs[k], xs[k + 1]) for k in range(len(xs) - 1)), default=0.0)
    slam = bool(xs) and (max(xs) >= 2.0 or pair >= 1.5)
    return f"""# ARCHIVE SETUP — TrendCont {r['direction']} — {r['symbol']}
_Record card for a calibration read (CLAUDE.live.md applies). The charts are cut at the signal bar and the axis counts
bars, not dates: everything after the signal bar is withheld deliberately, and so are the date, the calendar, the open
exposure and the spread. That is the design of this card, NOT a failed bundle — score the structural steps and treat
the news and correlation checks as not applicable. Decision class: DISCRETION._

- **Symbol:** {r['symbol']}
- **Regime (D1, as recorded at the signal):** {r.get('regime') or '(blank)'} · with-trend: {r.get('with_trend') or '-'}
- **Strategy read:** TrendCont — a pullback inside an established trend, entering on the resumption.
- **Proposed levels:** entry {e:.{dg}f}, SL {s:.{dg}f}, TP1 {tp1:.{dg}f}{f', TP2 {tp2:.{dg}f}' if tp2 else ''} (partial {float(r.get('partial_frac') or 0.5):.0%} at TP1)
- **Risk geometry:** SL 1.0R · TP1 {abs(tp1 - e) / R if tp1 else 0:.2f}R{f' · TP2 {abs(tp2 - e) / R:.2f}R' if tp2 else ''}
- **Recent swing structure (EA values — authoritative over your read of the image):**
    - swing highs, newest first: {row(tbl['hi'])}
    - swing lows, newest first: {row(tbl['lo'])}
    - 5-bar fractal (2 left / 2 right) on H4 over a 14-day rolling window, the same set the chart marks. Bars ago counts
      back from the right edge, which IS the signal bar on this card.
- **Step 2 numbers (H4 ATR(14) = {a:.{dg}f}):** pullback bar ranges as multiples of ATR, oldest to newest: {' · '.join(f'{x:.2f}' for x in xs) or '-'}.
  largest {max(xs) if xs else 0:.2f}x, best consecutive pair both >= {pair:.2f}x. By the guide's thresholds
  (slam = one bar >= 2x, or two consecutive >= 1.5x): **{'SLAM' if slam else 'no slam'}**.

Images: `d1.png` (daily context) · `h4.png` (H4, right edge = the signal bar).
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "study", "arc"))
    ap.add_argument("--seed", type=int, default=20260924)
    a = ap.parse_args()
    wins, losses, nw, nl = pick(a.seed)
    print(f"pool: {nw} winners (>= +{WIN_R}R), {nl} straight-to-stop losers (<= {LOSS_R}R, mfe <= {LOSS_MFE}R)")
    rows = [(r, True) for r in wins] + [(r, False) for r in losses]
    random.Random(a.seed).shuffle(rows)                       # ids carry no information about the outcome
    os.makedirs(a.out, exist_ok=True)
    man = []
    for n, (r, won) in enumerate(rows, 1):
        sym = r["symbol"]; key = f"arc-{n:02d}"
        h4, d1 = load_bars(sym, "h4"), load_bars(sym, "d1")
        ih = {b[0]: k for k, b in enumerate(h4)}
        t = int(datetime.strptime(r["signal_time"], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        i = ih.get(t)
        if i is None or i < 60: print(f"  {key} {sym}: signal bar not in the H4 file - skipped"); continue
        # coach 2026-09-30: the last D1 bar dated STRICTLY before the signal day (the signal day's own bar closes after it)
        day0 = t - t % 86400
        jd = max((k for k, b in enumerate(d1) if b[0] < day0), default=None)
        if jd is None or jd < 30: print(f"  {key} {sym}: no D1 history - skipped"); continue
        e, s = float(r["orig_entry"]), float(r["orig_sl"])
        lv = {"entry": e, "sl": s, "tp1": float(r["orig_tp1"] or 0), "tp2": float(r["orig_tp2"] or 0)}
        dg = max(2, min(5, len(f"{e}".split(".")[1]) if "." in f"{e}" else 2))
        b = os.path.join(a.out, key); os.makedirs(b, exist_ok=True)
        head = f"{sym}  TrendCont {r['direction']}  (archive card)"
        charts.render(h4[:i + 1], title="H4  " + head, levels=lv, n_show=charts.H4_BARS, signal_t=t, digits=dg,
                      strategy="TrendCont", imbal=True, hide_dates=True).save(os.path.join(b, "h4.png"), "PNG")
        charts.render(d1[:jd + 1], title="D1  " + head, levels=lv, n_show=charts.D1_BARS, digits=dg,
                      hide_dates=True).save(os.path.join(b, "d1.png"), "PNG")
        open(os.path.join(b, "setup.md"), "w", encoding="utf-8").write(card(r, h4, i))
        man.append({"key": key, "symbol": sym, "signal_time": r["signal_time"], "won": won,
                    "r_multiple": float(r["r_multiple"]), "mfe_r": float(r.get("mfe_r") or 0), "direction": r["direction"]})
        print(f"  {key} {sym:11} {r['direction']:5} {'WIN ' if won else 'LOSS'} r={float(r['r_multiple']):+.2f}")
    json.dump(man, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print(f"\n{len(man)} bundles in {a.out} (manifest.json stays HERE - never copy it to the box)")


if __name__ == "__main__": main()
