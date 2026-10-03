#!/usr/bin/env python3
"""mt5_parallel_compare.py - the parallel tester's proof: each parallel pass's journal vs the serial run of the same config.
    python3 pipeline/mt5_parallel_compare.py <par_out_dir> <serial_dir> <SYMBOL>
Serial files: <serial_dir>/<SYMBOL>_{off,A,B,I}.csv (pipeline/queue_repro_run.sh). Parallel passes are matched by their params.txt.
Per config: byte-identical? else per-signal full-position R differences (signal_time + strategy).
"""
import csv, filecmp, glob, os, sys

par, ser, sym = sys.argv[1], sys.argv[2], sys.argv[3]
f = lambda x: float(x) if x not in (None, "") else None
WANT = {"off": (0, 0.0, 0.0), "A": (1, 0.0, 0.0), "B": (0, 0.5, 0.0), "I": (0, 0.0, 0.5)}


def params(d):
    p = dict(kv.split("=", 1) for kv in open(os.path.join(d, "params.txt")).read().strip().split(";") if "=" in kv)
    return int(p["InpAddGate"]), float(p["InpShortCutAvg"]), float(p["InpImprovingAdd"])


def tots(p):
    g = {}
    for r in csv.DictReader(open(p, errors="replace")):
        if r.get("decision") in ("approved", "approved_pending") and r.get("r_multiple") not in (None, ""):
            g.setdefault((r["signal_time"][:16], r["strategy"]), []).append(r)
    out = {}
    for k, rs in g.items():
        first = next((r for r in rs if int(f(r.get("tranche")) or 0) <= 1), rs[0]); full = f(first.get("full_lots")) or f(first["lots"])
        out[k] = sum(f(r["r_multiple"]) * (f(r["lots"]) / full) for r in rs)
    return out


passes = {}
for d in glob.glob(os.path.join(par, f"{sym}_*")):
    if os.path.isdir(d) and os.path.exists(os.path.join(d, "params.txt")): passes[params(d)] = d
print(f"{len(passes)} parallel passes found")
ok = True
for name, key in WANT.items():
    s = os.path.join(ser, f"{sym}_{name}.csv"); d = passes.get(key)
    if not d or not os.path.exists(s): print(f"  {name}: missing ({'no pass' if not d else 'no serial'})"); ok = False; continue
    j = [x for x in glob.glob(os.path.join(d, "AA_*.csv")) if not any(t in x for t in (".actions.", ".inv.", ".part.", ".delays."))][0]
    if filecmp.cmp(j, s, shallow=False): print(f"  {name}: BYTE-IDENTICAL to the serial run"); continue
    P, S = tots(j), tots(s); both = set(P) & set(S)
    diff = [abs(P[k] - S[k]) for k in both]
    mx = max(diff) if diff else float("nan"); nd = sum(1 for x in diff if x > 1e-9)
    print(f"  {name}: not byte-identical; signals par {len(P)} serial {len(S)} matched {len(both)}; differing R {nd}, max |diff| {mx:.4f}")
    ok &= (len(P) == len(S) == len(both) and nd == 0)
print("PARALLEL == SERIAL" if ok else "DIFFERENCES - inspect before relying on parallel runs")
