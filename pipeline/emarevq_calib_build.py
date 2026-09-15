#!/usr/bin/env python3
"""emarevq_calib_build.py — build the EMArevQ calibration sample (item 2).

Reads ONLY data/study/emarevq/triggers_blind.csv (+ QDM M1 bars) — no outcome data.
For each EMArev trigger: aggregate H4, validate H4-clock alignment against to_entry, compute
the whole-climb quiet metrics (A max-bar-range/ATR, B climb length, C dominant-bar share),
signed normalized margins, bucket by pass/fail boundary, select ~6 each from
{clear-pass, clear-fail, borderline-fail-A/-B/-C} (seeded), and render each as a BLIND H4
candlestick (candles + EMA20 + one trigger-bar marker, no symbol/date/outcome/price/metrics,
bars only up to the trigger). Writes:
  data/study/emarevq/charts/NN.png          (30 blind charts, numbered)
  data/study/emarevq/key.csv                (chart# -> symbol/time/metrics/bucket; NOT shown to trader)
Draft thresholds A=1.2, B=8, C=0.30 (frozen procedure in the spec).
"""
import csv, sys, math, random, bisect
from datetime import datetime, timedelta
from pathlib import Path
from statistics import pstdev
sys.path.insert(0, str(Path(__file__).resolve().parent))
from breakout_backtest import load_m1, agg_h4, ema, wilder_atr, M1DIR, SYMS

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "study" / "emarevq"
CH = OUT / "charts"; CH.mkdir(parents=True, exist_ok=True)

A_DRAFT, B_DRAFT, C_DRAFT = 1.2, 8, 0.30
CLIMB_CAP = 40          # max bars to scan back for the EMA20 touch
CTX_BARS = 42           # bars shown on the blind chart (ending AT the trigger)
SEED = 42
EMA_P, ATR_P = 20, 14
QNAME = {v[0]: k for k, v in SYMS.items()}   # ".dk label" -> QDM csv name


def parse_t(s):
    s = s.strip()
    return datetime.strptime(s[:19], "%Y.%m.%d %H:%M:%S") if len(s) > 16 \
        else datetime.strptime(s, "%Y.%m.%d %H:%M")


def build_symbol(label, trigs):
    qn = QNAME[label]
    path = M1DIR / f"{qn}-M1-No Session.csv"
    if not path.exists():
        print(f"  {label}: no M1 — skip {len(trigs)}"); return []
    T, O, H, L, C = load_m1(str(path))
    h4 = agg_h4(T, O, H, L, C)
    topen = [b["t"] for b in h4]
    cc = [b["c"] for b in h4]; hh = [b["h"] for b in h4]; ll = [b["l"] for b in h4]
    em = ema(cc, EMA_P); at = wilder_atr(hh, ll, cc, ATR_P)
    out = []
    for tr in trigs:
        st = parse_t(tr["signal_time"])
        si = bisect.bisect_left(topen, st)
        if si >= len(h4) or topen[si] != st:      # exact bar must exist
            out.append({**tr, "drop": "no_bar"}); continue
        if em[si] is None or at[si] is None or at[si] <= 0:
            out.append({**tr, "drop": "warmup"}); continue
        # H4-clock alignment: trigger bar close ~ to_entry
        to_entry = float(tr["to_entry"])
        if abs(cc[si] - to_entry) > 0.10 * at[si]:
            out.append({**tr, "drop": "align"}); continue
        d = 1 if tr["direction"].upper().startswith("B") else -1
        # climb start = last bar (scanning back) whose [low,high] contains EMA20
        start = None
        for j in range(si, max(-1, si - CLIMB_CAP) - 1, -1):
            if ll[j] <= em[j] <= hh[j]:
                start = j; break
        if start is None or start >= si:
            out.append({**tr, "drop": "no_climb"}); continue
        climb = list(range(start, si + 1))
        ema_ref = em[start]
        # A: max bar range / ATR over the climb
        Amax = max((hh[k] - ll[k]) / at[k] for k in climb if at[k])
        # B: climb length
        Blen = len(climb)
        # extreme + total_stretch (distance away from EMA20)
        if d > 0:   # long fade: excursion DOWN, extreme = min low
            ext = min(ll[k] for k in climb)
        else:
            ext = max(hh[k] for k in climb)
        total_stretch = abs(ext - ema_ref)
        if total_stretch <= 0:
            out.append({**tr, "drop": "flat"}); continue
        # C: max per-bar NEW-GROUND contribution / total_stretch
        run = 0.0; Cmax = 0.0
        for k in climb:
            dist = (ema_ref - ll[k]) if d > 0 else (hh[k] - ema_ref)
            contrib = max(0.0, dist - run)
            if dist > run:
                run = dist
            Cmax = max(Cmax, contrib / total_stretch)
        # smoothness metrics over the climb closes (price only, no outcome):
        cl = [cc[k] for k in climb]
        steps = len(cl) - 1
        if steps >= 1:
            # D: fraction of steps moving in the drift-away direction
            #   long fade (d>0): excursion DOWN -> in-direction = close falls
            dircnt = sum(1 for i in range(1, len(cl))
                         if (cl[i] < cl[i - 1]) == (d > 0))
            D = dircnt / steps
            # R2: linear fit of close vs index
            xs = list(range(len(cl))); mx = sum(xs) / len(xs); my = sum(cl) / len(cl)
            sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in cl)
            sxy = sum((xs[i] - mx) * (cl[i] - my) for i in range(len(cl)))
            R2 = (sxy * sxy / (sxx * syy)) if sxx > 0 and syy > 0 else 0.0
        else:
            D = R2 = 0.0
        out.append({**tr, "drop": "", "si": si, "start": start,
                    "A": Amax, "B": Blen, "C": Cmax, "D": D, "R2": R2,
                    "h4o": [h4[k]["o"] for k in range(max(0, si - CTX_BARS + 1), si + 1)],
                    "h4h": [hh[k] for k in range(max(0, si - CTX_BARS + 1), si + 1)],
                    "h4l": [ll[k] for k in range(max(0, si - CTX_BARS + 1), si + 1)],
                    "h4c": [cc[k] for k in range(max(0, si - CTX_BARS + 1), si + 1)],
                    "h4e": [em[k] for k in range(max(0, si - CTX_BARS + 1), si + 1)]})
    return out


def render_blind(rec, dst):
    from PIL import Image, ImageDraw
    o, h, l, c, e = rec["h4o"], rec["h4h"], rec["h4l"], rec["h4c"], rec["h4e"]
    n = len(c)
    hi = max(h); lo = min(l); rng = (hi - lo) or 1e-9
    W, Hh, pad = 900, 460, 22
    im = Image.new("RGB", (W, Hh), (13, 17, 23))
    d = ImageDraw.Draw(im)
    cw = max(2, (W - 2 * pad) // n - 2)

    def yv(p):
        return int(pad + (hi - p) / rng * (Hh - 2 * pad))

    def xv(i):
        return pad + i * ((W - 2 * pad) // n) + cw // 2
    for i in range(n):
        col = (63, 185, 80) if c[i] >= o[i] else (248, 81, 73)
        x = xv(i)
        d.line([(x, yv(h[i])), (x, yv(l[i]))], fill=col)
        ty, by = yv(max(o[i], c[i])), yv(min(o[i], c[i]))
        d.rectangle([x - cw // 2, ty, x + cw // 2, max(ty + 1, by)],
                    fill=col if c[i] >= o[i] else (13, 17, 23), outline=col)
    # EMA20 (amber)
    pts = [(xv(i), yv(e[i])) for i in range(n) if e[i] is not None]
    if len(pts) > 1:
        d.line(pts, fill=(210, 153, 34), width=2)
    # trigger-bar marker: a small triangle above the last bar (the signal bar)
    xt = xv(n - 1)
    d.polygon([(xt, yv(h[n - 1]) - 14), (xt - 6, yv(h[n - 1]) - 26),
               (xt + 6, yv(h[n - 1]) - 26)], fill=(88, 166, 255))
    im.save(dst, "PNG")


def main():
    trig = list(csv.DictReader(open(OUT / "triggers_blind.csv")))
    bysym = {}
    for t in trig:
        bysym.setdefault(t["symbol"], []).append(t)
    recs = []
    for label, ts in bysym.items():
        print(f"processing {label} ({len(ts)} triggers)...")
        recs += build_symbol(label, ts)
    ok = [r for r in recs if not r["drop"]]
    from collections import Counter
    drops = Counter(r["drop"] for r in recs if r["drop"])
    ndrop = sum(drops.values())
    print(f"\ntotal {len(recs)}  usable {len(ok)}  dropped {ndrop} {dict(drops)}")
    align_rate = drops.get("align", 0) / len(recs)
    print(f"alignment-drop rate = {100*align_rate:.1f}%  (STOP threshold 5%)")
    if align_rate > 0.05:
        print("!! H4 alignment drop >5% — aggregation misaligned with MT5. STOP; fix before rendering.")
        return
    # signed normalized margins vs draft thresholds (for the key / axis calibration)
    sA = pstdev([r["A"] for r in ok]) or 1e-9
    sB = pstdev([r["B"] for r in ok]) or 1e-9
    sC = pstdev([r["C"] for r in ok]) or 1e-9

    def pctls(vals, qs=(0, 10, 25, 50, 75, 90, 100)):
        s = sorted(vals)
        return {q: s[min(len(s) - 1, int(q / 100 * (len(s) - 1)))] for q in qs}
    # dump ALL usable metrics (blind: no outcome) for the fleet rarity check
    with open(OUT / "all_metrics.csv", "w", newline="") as fh:
        aw = csv.DictWriter(fh, fieldnames=["symbol", "signal_time", "direction", "A", "B", "C", "D", "R2"])
        aw.writeheader()
        for r in ok:
            aw.writerow({"symbol": r["symbol"], "signal_time": r["signal_time"],
                         "direction": r["direction"], "A": f"{r['A']:.3f}", "B": r["B"],
                         "C": f"{r['C']:.3f}", "D": f"{r['D']:.3f}", "R2": f"{r['R2']:.3f}"})
    print("\nmetric distributions over %d usable triggers:" % len(ok))
    print("  A (max bar range/ATR):", {k: round(v, 2) for k, v in pctls([r["A"] for r in ok]).items()})
    print("  B (climb length bars):", pctls([r["B"] for r in ok]))
    print("  C (dominant-bar share):", {k: round(v, 2) for k, v in pctls([r["C"] for r in ok]).items()})

    for r in ok:
        r["mA"] = (A_DRAFT - r["A"]) / sA
        r["mB"] = (r["B"] - B_DRAFT) / sB
        r["mC"] = (C_DRAFT - r["C"]) / sC
        r["passA"] = r["A"] <= A_DRAFT; r["passB"] = r["B"] >= B_DRAFT; r["passC"] = r["C"] < C_DRAFT
        r["nfail"] = (not r["passA"]) + (not r["passB"]) + (not r["passC"])
        # composite "quietness": higher = slower/grindier (quieter). Equal-weight z-sum.
        r["quiet"] = r["mA"] + r["mB"] + r["mC"]

    # The draft thresholds are far too strict (almost nothing passes), so pass/fail buckets are
    # degenerate. Calibrate instead by spanning the FULL quietness spectrum: sort by the composite
    # and pick N charts at evenly-spaced percentiles (spiky -> grindy). The trader's yes/no across
    # this spectrum reveals where his eye draws the line, per-axis via the key's A/B/C.
    N = 30
    ok.sort(key=lambda r: r["quiet"])
    idx = [round(i * (len(ok) - 1) / (N - 1)) for i in range(N)]
    seen = set(); picks = []
    for k in idx:
        while k in seen and k < len(ok) - 1:
            k += 1
        seen.add(k)
        picks.append(("span", ok[k]))
    print("selected:", len(picks), "spanning quietness percentiles 0..100")
    rng = random.Random(SEED)
    rng.shuffle(picks)
    keyf = open(OUT / "key.csv", "w", newline="")
    kw = csv.DictWriter(keyf, fieldnames=["chart", "bucket", "symbol", "signal_time",
                                          "direction", "A_maxRangeATR", "B_len", "C_share",
                                          "passA", "passB", "passC", "draft_pass"])
    kw.writeheader()
    for i, (bucket, r) in enumerate(picks, 1):
        render_blind(r, CH / f"{i:02d}.png")
        kw.writerow({"chart": i, "bucket": bucket, "symbol": r["symbol"],
                     "signal_time": r["signal_time"], "direction": r["direction"],
                     "A_maxRangeATR": f"{r['A']:.2f}", "B_len": r["B"], "C_share": f"{r['C']:.2f}",
                     "passA": int(r["passA"]), "passB": int(r["passB"]), "passC": int(r["passC"]),
                     "draft_pass": int(r["nfail"] == 0)})
    keyf.close()
    print(f"\nrendered {len(picks)} blind charts -> {CH}")
    print(f"key -> {OUT/'key.csv'}  (NOT shown to trader)")


if __name__ == "__main__":
    main()
