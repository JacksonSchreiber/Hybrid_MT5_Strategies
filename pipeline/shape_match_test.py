#!/usr/bin/env python3
"""shape_match_test.py - trader 2026-10-04: match the SHAPE of the last month of bars before the signal (analog matching) and see
whether the closest historical shapes predict the trade better than the plain group average. Companion of neighbor_path_test.py
(same population, targets, pools, leave-year-out scoring and preset test).
SHAPES (signed with the trade direction, measured from the signal-bar close, nothing after the signal):
  H4 close  the last 120 H4 closes (~20 trading days) / ATR14(H4)
  H4 env    + the highs and lows (adverse/favourable side swapped for shorts)
  H4 pure   the 120 closes z-normalised (size removed - shape only)
  D1 close  the last 22 COMPLETED days' closes (days before the signal day, built from H4) / ATR14(D1)
  D1 env    + the daily highs and lows          D1 pure   the 22 closes z-normalised
Distance: Euclidean on the vectors (no per-dimension scaling). POOL: same group + direction + detector (>= 80 else group + direction)
or any symbol with the same direction + detector. Neighbours only from other calendar years.
    python3 pipeline/shape_match_test.py       -> data/study/shape_match_report.txt
"""
from __future__ import annotations
import json, math, os, sys, time
import numpy as np
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import neighbor_path_test as M                 # noqa: E402

REPORT = os.path.join(A.STUDY, "shape_match_report.txt")


def atr(h, l, c):
    pc = c[:-1]; h, l, c = h[1:], l[1:], c[1:]
    return float(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))[-14:].mean())


def shapes(x):
    b = x["bars"]; i0 = x["i0"]; up = x["up"]; sg = 1.0 if up else -1.0
    if i0 < 400: return None
    W = b[i0 - 121:i0]
    o = {}
    h = np.array([r[2] for r in W]); l = np.array([r[3] for r in W]); c = np.array([r[4] for r in W])
    a4 = atr(h, l, c)
    if a4 <= 0: return None
    H, L = (h, l) if up else (l, h)
    cl = sg * (c[1:] - c[-1]) / a4
    o["H4 close"] = cl; o["H4 env"] = np.concatenate([cl, sg * (H[1:] - c[-1]) / a4, sg * (L[1:] - c[-1]) / a4])
    o["H4 pure"] = (cl - cl.mean()) / cl.std() if cl.std() > 0 else cl * 0
    day = b[i0 - 1][0][:10]; days = {}
    for r in b[i0 - 400:i0]:
        d = r[0][:10]
        if d >= day: break
        if d not in days: days[d] = [r[2], r[3], r[4]]
        else: days[d][0] = max(days[d][0], r[2]); days[d][1] = min(days[d][1], r[3]); days[d][2] = r[4]
    dv = list(days.values())
    if len(dv) < 37: return None
    dh = np.array([v[0] for v in dv[-37:]]); dl = np.array([v[1] for v in dv[-37:]]); dc = np.array([v[2] for v in dv[-37:]])
    a1 = atr(dh, dl, dc)
    if a1 <= 0: return None
    dH, dL = (dh, dl) if up else (dl, dh)
    ref = c[-1]
    d_cl = sg * (dc[-22:] - ref) / a1
    o["D1 close"] = d_cl; o["D1 env"] = np.concatenate([d_cl, sg * (dH[-22:] - ref) / a1, sg * (dL[-22:] - ref) / a1])
    o["D1 pure"] = (d_cl - d_cl.mean()) / d_cl.std() if d_cl.std() > 0 else d_cl * 0
    return o


def knn_shape(T, V, k=50, pool_mode="group", min_det=80):
    g = np.array([t["g"] for t in T]); det = np.array([t["det"] for t in T]); up = np.array([t["up"] for t in T]); y = np.array([t["y"] for t in T])
    out = [None] * len(T)
    for a, t in enumerate(T):
        other = y != t["y"]; gm = (g == t["g"]) & other
        if pool_mode == "group":
            pool = gm & (up == t["up"]) & (det == t["det"])
            if pool.sum() < min_det: pool = gm & (up == t["up"])
        else:
            pool = other & (up == t["up"]) & (det == t["det"])
        idx = np.nonzero(pool)[0]
        if len(idx) < max(k, 1) + 10: continue
        dist = ((V[idx] - V[a]) ** 2).sum(1)
        nb = idx[np.argpartition(dist, k)[:k]] if k > 1 else idx[[int(np.argmin(dist))]]
        out[a] = {"nb": nb, "pool": idx, "group": np.nonzero(gm)[0]}
    return out


def path_skill(T, N):
    ek = eb = 0.0; m = 0
    for a, n in enumerate(N):
        if not n or T[a]["path"] is None: continue
        pk = [T[j]["path"] for j in n["nb"] if T[j]["path"] is not None]; pb = [T[j]["path"] for j in n["pool"] if T[j]["path"] is not None]
        if not pk or not pb: continue
        ek += float(np.mean((np.mean(pk, 0) - T[a]["path"]) ** 2)); eb += float(np.mean((np.mean(pb, 0) - T[a]["path"]) ** 2)); m += 1
    return 1 - ek / eb, m


def preset_choice(T, N):
    pk, pg = [], []
    for a, n in enumerate(N):
        if not n: continue
        nbm = {k: np.mean([T[j]["P"][k] for j in n["nb"]]) for k in M.PRESETS}; gpm = {k: np.mean([T[j]["P"][k] for j in n["group"]]) for k in M.PRESETS}
        pk.append(T[a]["P"][max(nbm, key=nbm.get)]); pg.append(T[a]["P"][max(gpm, key=gpm.get)])
    d = np.array(pk) - np.array(pg)
    return np.mean(pk), np.mean(pg), d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))


def main():
    T0 = time.time(); T = M.load()
    cm = json.load(open(os.path.join(M.RUN, "combos_meta.json")))
    popk = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    S = []; keep = []
    for t in T:
        s_, t_, k_, _ = cm["trades"][t["i"]]; sh = shapes(popk[(s_, t_, k_)])
        if sh is not None: S.append(sh); keep.append(t)
    T = keep
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(T)} trades with a full month of bars before the signal\n"]
    def block(title, N):
        L.append(f"--- {title}")
        for key, lab in (("raw", "raw R"), ("live", "live-setup R"), ("tp2", "reached TP2 (raw)"), ("f6", "best R first 24h")):
            M.evaluate(T, N, key, L, lab)
        ps, m = path_skill(T, N); L.append(f"  {'12-bar price path':28} n {m:5}  skill vs BASE {ps:+.4f}")
        a, b, tt = preset_choice(T, N); L.append(f"  preset picked by the matches {a:+.4f}R/trade vs the group's best preset {b:+.4f} (t {tt:+.2f})")
    for kind in ("H4 close", "H4 env", "H4 pure", "D1 close", "D1 env", "D1 pure"):
        V = np.array([s[kind] for s in S]); block(f"{kind}: 50 closest, same group", knn_shape(T, V, 50, "group"))
    for kind in ("H4 env", "D1 env"):
        V = np.array([s[kind] for s in S]); block(f"{kind}: 50 closest, any symbol", knn_shape(T, V, 50, "all"))
    V = np.array([s["H4 env"] for s in S])
    for k in (10, 1):
        block(f"H4 env: the {k} closest, same group", knn_shape(T, V, k, "group"))
    L.append(f"\n({time.time() - T0:.0f}s)")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
