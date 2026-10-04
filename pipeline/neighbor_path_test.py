#!/usr/bin/env python3
"""neighbor_path_test.py - trader 2026-10-04: does a "similar trades" lookup (nearest neighbours in the same symbol group, matched on
signal-time features) predict a trade's outcome and path better than the plain group average? Offline, before any chart is built.

Population: the Data visualizers' 8,606 historical trades (combo build 20261004_1402: outcomes under every management combo + the H4
price path). Groups: live/symbol_groups.py.
FEATURES (all from H4 bars that closed at or before the signal bar - nothing after the signal; signed with the trade direction):
  stop_atr   |entry - stop| / ATR14            vol_reg  ATR14 / ATR100           er20   efficiency ratio of the last 20 bars (signed)
  ema50_atr  (close - EMA50) / ATR14           ema_gap  (EMA20 - EMA50) / ATR14  mom6   6-bar change / ATR14
  trend120   120-bar (~20 trading days) change / ATR14   rng30   position in the last 30 bars' range (0 = at the adverse end)
  hour_s/c   entry hour (sin/cos)              tp2_R    TP2 distance in R        tp1_R  TP1 distance in R
POOL per trade: same group + same direction + same detector (if >= 80 candidates, else same group + direction); candidates come
only from OTHER calendar years (leave-year-out) or only from EARLIER years (past-only, trades from 2016 on). Features are z-scored
on the pool; k nearest by Euclidean distance (k = 50 unless stated).
PREDICTIONS: kNN = mean of the k neighbours; BASE = mean of the whole pool (the "plain group average", same direction/detector);
GROUP = mean of the whole group, other years (ignores direction/detector too).
TARGETS: raw R (100% at the signal, signal stop, TP2), live-setup R, reached TP2 (raw), best R in the first 24h, price at the 24h
close, the price path over the first 12 H4 bars.
SKILL = 1 - MSE(kNN) / MSE(BASE): > 0 means the neighbours beat the plain average. Quintiles: trades sorted by the kNN prediction.
PRESET TEST: for every trade, pick the management preset whose neighbours did best, vs the best preset for the group (chosen on other
years) vs always the live setup - realised R of each policy.
    python3 pipeline/neighbor_path_test.py        -> data/study/neighbor_path_report.txt
"""
from __future__ import annotations
import json, math, os, statistics as st, sys, time
import numpy as np
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import combo_pack as CP                        # noqa: E402
from live import symbol_groups as SG                         # noqa: E402

RUN = os.path.join(CB.OUTDIR, "20261004_1402")
REPORT = os.path.join(A.STUDY, "neighbor_path_report.txt")
FEAT = ["stop_atr", "vol_reg", "er20", "ema50_atr", "ema_gap", "mom6", "trend120", "rng30", "hour_s", "hour_c", "tp2_R", "tp1_R"]
PRESETS = {   # name -> combo_build.find arguments (stage, gateA, bank, pyr, raise, cutB, addI, bloc)
    "raw (nothing added)":         ("off", 0, "off", "off", "off", "off", "off", "off"),
    "live setup":                  ("25@6", 0, "det@1.0", "0.5@1.5", "0.25", "off", "off", "off"),
    "live + bloc rules":           ("25@6", 0, "det@1.0", "0.5@1.5", "off", "off", "off", "on"),
    "100% + bank/pyr":             ("off", 0, "det@1.0", "0.5@1.5", "0.25", "off", "off", "off"),
    "100% + 24h cut/lock + bank":  ("off", 0, "det@1.0", "off", "off", "off", "off", "on"),
    "100% + quick bank 50%@0.75":  ("off", 0, "0.5@0.75", "off", "0.5", "off", "off", "off"),
}


def ema(x, n):
    a = 2 / (n + 1); e = float(np.mean(x[:n]))
    for v in x[n:]: e = a * v + (1 - a) * e
    return e


def features(x):
    b = x["bars"]; i0 = x["i0"]
    if i0 < 135: return None
    h = np.array([r[2] for r in b[i0 - 130:i0]]); l = np.array([r[3] for r in b[i0 - 130:i0]]); c = np.array([r[4] for r in b[i0 - 131:i0]])
    pc = c[:-1]; c = c[1:]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr14, atr100 = tr[-14:].mean(), tr[-100:].mean()
    if atr14 <= 0 or atr100 <= 0: return None
    sg = 1.0 if x["up"] else -1.0; R = abs(x["e"] - x["s"])
    d = np.abs(np.diff(c[-21:])).sum()
    lo, hi = l[-30:].min(), h[-30:].max(); pos = (c[-1] - lo) / (hi - lo) if hi > lo else 0.5
    hr = int(b[i0][0][11:13])
    return {"stop_atr": R / atr14, "vol_reg": atr14 / atr100, "er20": sg * (c[-1] - c[-21]) / d if d > 0 else 0.0,
            "ema50_atr": sg * (c[-1] - ema(c, 50)) / atr14, "ema_gap": sg * (ema(c, 20) - ema(c, 50)) / atr14,
            "mom6": sg * (c[-1] - c[-7]) / atr14, "trend120": sg * (c[-1] - c[-121]) / atr14, "rng30": pos if x["up"] else 1 - pos,
            "hour_s": math.sin(2 * math.pi * hr / 24), "hour_c": math.cos(2 * math.pi * hr / 24),
            "tp2_R": (x["tp2"] - x["e"]) * sg / R if x["tp2"] else 6.0, "tp1_R": (x["tp1"] - x["e"]) * sg / R if x["tp1"] else 6.0}


def load():
    cm = json.load(open(os.path.join(RUN, "combos_meta.json"))); rp = json.load(open(os.path.join(RUN, "rpaths.json")))["trades"]
    nt = cm["n_trades"]; combos = CB.grid()
    def slc(ci):
        with open(os.path.join(RUN, "combos.bin"), "rb") as f:
            f.seek(ci * nt * CP.PK.itemsize); return np.frombuffer(f.read(nt * CP.PK.itemsize), dtype=CP.PK)
    P = {k: slc(CB.find(combos, *v[:7], bloc=v[7])) for k, v in PRESETS.items()}
    popk = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    grp = {s: k for k, n, c_, ss, _ in SG.GROUPS for s in ss}
    T = []
    for i, (s_, t_, k_, up) in enumerate(cm["trades"]):
        raw = P["raw (nothing added)"]
        if raw["kind"][i] == 255: continue
        f = features(popk[(s_, t_, k_)])
        if f is None: continue
        rt = rp[i]["rt"]
        T.append({"i": i, "sym": s_.split(".")[0], "g": grp.get(s_.split(".")[0], "?"), "det": k_, "up": bool(up), "y": int(t_[:4]),
                  "f": np.array([f[k] for k in FEAT]), "raw": raw["R"][i] / 1000, "live": P["live setup"]["R"][i] / 1000,
                  "tp2": 1.0 if raw["kind"][i] == 3 else 0.0, "f6": rp[i]["f6"], "r24": rt[5] / 100 if len(rt) >= 6 else None,
                  "path": np.array(rt[:12]) / 100 if len(rt) >= 12 else None, "P": {k: P[k]["R"][i] / 1000 for k in PRESETS}})
    return T


def knn(T, k=50, mode="loyo", feats=None, min_det=80):
    """per trade -> dict of predictions (kNN / BASE / GROUP) for every target + neighbour index list."""
    fi = [FEAT.index(f) for f in (feats or FEAT)]
    F = np.array([t["f"][fi] for t in T]); g = np.array([t["g"] for t in T]); det = np.array([t["det"] for t in T])
    up = np.array([t["up"] for t in T]); y = np.array([t["y"] for t in T])
    out = [None] * len(T)
    for a, t in enumerate(T):
        if mode == "past" and t["y"] < 2016: continue
        other = (y != t["y"]) if mode == "loyo" else (y < t["y"])
        gm = (g == t["g"]) & other
        pool = gm & (up == t["up"]) & (det == t["det"])
        if pool.sum() < min_det: pool = gm & (up == t["up"])
        idx = np.nonzero(pool)[0]
        if len(idx) < k + 10: continue
        X = F[idx]; mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1
        dist = (((X - mu) / sd - (F[a] - mu) / sd) ** 2).sum(1)
        nb = idx[np.argpartition(dist, k)[:k]]
        out[a] = {"nb": nb, "pool": idx, "group": np.nonzero(gm)[0]}
    return out


def mean_of(T, ids, key):
    v = [T[j][key] for j in ids if T[j][key] is not None]
    return float(np.mean(v)) if v else None


def evaluate(T, N, key, L, label):
    rows = [(a, mean_of(T, n["nb"], key), mean_of(T, n["pool"], key), mean_of(T, n["group"], key)) for a, n in enumerate(N) if n]
    rows = [(a, p, b, gg) for a, p, b, gg in rows if T[a][key] is not None and p is not None and b is not None and gg is not None]
    act = np.array([T[a][key] for a, *_ in rows]); pk = np.array([p for _, p, _, _ in rows]); pb = np.array([b for _, _, b, _ in rows])
    pg = np.array([gg for *_, gg in rows])
    mse = lambda p: float(np.mean((act - p) ** 2))
    cor = lambda p: float(np.corrcoef(p, act)[0, 1]) if np.std(p) > 0 else 0.0
    q = np.argsort(np.argsort(pk)) * 5 // len(pk)
    qm = [act[q == j].mean() for j in range(5)]
    top, bot = act[q == 4], act[q == 0]
    tt = (top.mean() - bot.mean()) / math.sqrt(top.var(ddof=1) / len(top) + bot.var(ddof=1) / len(bot))
    L.append(f"  {label:28} n {len(act):5}  skill vs BASE {1 - mse(pk) / mse(pb):+.4f} (vs GROUP {1 - mse(pk) / mse(pg):+.4f})  corr kNN {cor(pk):+.3f} "
             f"BASE {cor(pb):+.3f}  | actual by kNN quintile (low -> high): " + " ".join(f"{v:+.3f}" for v in qm) + f"  top-bottom {top.mean() - bot.mean():+.3f} (t {tt:+.1f})")
    return 1 - mse(pk) / mse(pb)


def main():
    T0 = time.time(); T = load(); L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(T)} trades with features and outcomes\n"]
    groups = sorted({t["g"] for t in T}, key=lambda gk: -sum(1 for t in T if t["g"] == gk))
    L.append("trades per group: " + ", ".join(f"{gk} {sum(1 for t in T if t['g'] == gk)}" for gk in groups) + "\n")
    for mode, nm in (("loyo", "LEAVE-YEAR-OUT (neighbours from other years)"), ("past", "PAST-ONLY (neighbours from earlier years, trades 2016+)")):
        N = knn(T, 50, mode); L.append(f"=== {nm}, k = 50 ===")
        for key, lab in (("raw", "raw R"), ("live", "live-setup R"), ("tp2", "reached TP2 (raw)"), ("f6", "best R first 24h"), ("r24", "price at the 24h close")):
            evaluate(T, N, key, L, lab)
        # the path over 12 bars: RMSE of the kNN mean path vs the pool mean path, trades with 12 bars of path
        ids = [a for a, n in enumerate(N) if n and T[a]["path"] is not None]
        ek = eb = 0.0; m = 0
        for a in ids:
            pk = [T[j]["path"] for j in N[a]["nb"] if T[j]["path"] is not None]; pb = [T[j]["path"] for j in N[a]["pool"] if T[j]["path"] is not None]
            if len(pk) < 10: continue
            ek += float(np.mean((np.mean(pk, 0) - T[a]["path"]) ** 2)); eb += float(np.mean((np.mean(pb, 0) - T[a]["path"]) ** 2)); m += 1
        L.append(f"  {'12-bar price path':28} n {m:5}  skill vs BASE {1 - ek / eb:+.4f}  (RMSE kNN {math.sqrt(ek / m):.3f}R, BASE {math.sqrt(eb / m):.3f}R per bar)")
        L.append("")
        if mode == "loyo":
            L.append("  raw R skill by group (leave-year-out, k = 50):")
            for gk in groups:
                if sum(1 for t in T if t["g"] == gk) < 300: continue
                rows_L = []; evaluate(T, [N[a] if T[a]["g"] == gk else None for a in range(len(T))], "raw", rows_L, f"{gk}")
                L.append("  " + rows_L[0].strip())
            L.append("")
            # k sensitivity and feature contributions
            L.append("  k sensitivity (raw R, leave-year-out): " + "  ".join(
                f"k={k}: {evaluate(T, knn(T, k, 'loyo'), 'raw', [], ''):+.4f}" for k in (25, 100, 200)))
            L.append("\n  WHICH FEATURES MATTER (leave-year-out, raw R skill vs BASE): all features " + f"{evaluate(T, N, 'raw', [], ''):+.4f}")
            for f in FEAT:
                s1 = evaluate(T, knn(T, 50, "loyo", [f]), "raw", [], ""); s2 = evaluate(T, knn(T, 50, "loyo", [x for x in FEAT if x != f]), "raw", [], "")
                L.append(f"    {f:10} alone {s1:+.4f}   all but this one {s2:+.4f}  (drop changes skill by {s2 - evaluate(T, N, 'raw', [], ''):+.4f})")
            L.append("")
            # preset choice
            L.append("  PRESET CHOICE (leave-year-out): realised R per trade of each policy")
            pol = {"kNN picks the preset its neighbours did best with": [], "best preset for the group (other years)": [], "always the live setup": []}
            per = {k: [] for k in PRESETS}
            for a, n in enumerate(N):
                if not n: continue
                nbm = {k: np.mean([T[j]["P"][k] for j in n["nb"]]) for k in PRESETS}
                gpm = {k: np.mean([T[j]["P"][k] for j in n["group"]]) for k in PRESETS}
                pol["kNN picks the preset its neighbours did best with"].append(T[a]["P"][max(nbm, key=nbm.get)])
                pol["best preset for the group (other years)"].append(T[a]["P"][max(gpm, key=gpm.get)])
                pol["always the live setup"].append(T[a]["P"]["live setup"])
                for k in PRESETS: per[k].append(T[a]["P"][k])
            base = np.array(pol["best preset for the group (other years)"])
            for k, v in pol.items():
                v = np.array(v); d = v - base
                L.append(f"    {k:52} {v.mean():+.4f}R/trade" + ("" if k.startswith("best") else f"  (vs group-best {d.mean():+.4f}, t {d.mean() / (d.std(ddof=1) / math.sqrt(len(d))):+.2f})"))
            L.append("    each preset taken on every trade: " + ", ".join(f"{k} {np.mean(v):+.4f}" for k, v in per.items()))
            L.append("")
    L.append(f"({time.time() - T0:.0f}s)")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
