#!/usr/bin/env python3
"""m1_cache.py - trader 2026-10-03: a parse-once cache of each symbol's M1 arrays for the combo builder.

ask_side_study.Sym parses a ~330 MB CSV with pandas every time (~1 GB peak RAM, ~30 s). This writes the PARSED arrays once as raw
.npy files (float64 / int64 - bit-identical to Sym's) under data/cache/m1/<SYM>/, and CachedSym opens them memory-mapped, so many
worker processes share one copy through the OS page cache. The spread-by-hour array, ref price, tick and H4 bars are derived exactly
as Sym derives them. Cache fidelity is checked by verify(): every array must equal a freshly parsed Sym.
    python3 pipeline/m1_cache.py [SYM ...]        build (all registered symbols when none given)
    python3 pipeline/m1_cache.py --verify SYM     cached vs CSV-parsed, array by array
"""
from __future__ import annotations
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A          # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache", "m1")
ARRS = ("t", "o", "h", "l", "c")


def csv_path(sym):
    return os.path.join(A.ROOT, "data", "qdm_csv", "fleet", f"{A.SRC[sym]}-M1-No Session.csv")


def build(sym, prof=None, refs=None):
    d = os.path.join(CACHE, sym); os.makedirs(d, exist_ok=True)
    src = csv_path(sym); stamp = f"{os.path.getsize(src)}:{int(os.path.getmtime(src))}"
    sp = os.path.join(d, "source.txt")
    if os.path.exists(sp) and open(sp).read().strip() == stamp and all(os.path.exists(os.path.join(d, k + ".npy")) for k in ARRS):
        return False
    S = A.Sym(sym, prof or A.spread_profile(), refs or A.ref_prices())
    for k in ARRS: np.save(os.path.join(d, k + ".npy"), getattr(S, k))
    open(sp, "w").write(stamp)
    return True


class CachedSym:
    """drop-in for ask_side_study.Sym (need_dk=False): the same attributes, M1 arrays memory-mapped read-only."""
    def __init__(self, sym, prof, refs):
        self.sym = sym; root = sym.split(".")[0]; d = os.path.join(CACHE, sym)
        for k in ARRS: setattr(self, k, np.load(os.path.join(d, k + ".npy"), mmap_mode="r"))
        self.sp = prof[root + ".sim"][(self.t // 60) % 24]
        self.ref = refs.get(root + ".sim") or float(np.median(self.c[-20000:]))
        self.tick = A.TICK[sym]
        self.h4 = A.load(sym, "h4"); self.h4idx = {b[0][:16]: k for k, b in enumerate(self.h4)}
        self.h4t = np.array([A.tmin(b[0]) for b in self.h4], dtype=np.int64)
        self.adj = None


def verify(sym, prof=None, refs=None):
    prof = prof or A.spread_profile(); refs = refs or A.ref_prices()
    P = A.Sym(sym, prof, refs); Cq = CachedSym(sym, prof, refs); bad = []
    for k in ARRS + ("sp", "h4t"):
        a, b = getattr(P, k), np.asarray(getattr(Cq, k))
        if a.dtype != b.dtype or a.shape != b.shape or not np.array_equal(a, b): bad.append(k)
    for k in ("ref", "tick"):
        if getattr(P, k) != getattr(Cq, k): bad.append(k)
    if P.h4 != Cq.h4: bad.append("h4")
    return bad


if __name__ == "__main__":
    from pipeline import oos_queue_study as Q        # registers the new symbols (SRC / TICK / ref prices)  # noqa: E402
    from pipeline.per_symbol_stats import ORIG         # noqa: E402
    from pipeline import oos_build_inputs as OI        # noqa: E402
    args = sys.argv[1:]
    if args and args[0] == "--verify":
        for s in args[1:]: b = verify(s, refs=Q.REFS); print(f"{s}: {'IDENTICAL to the CSV-parsed Sym' if not b else 'MISMATCH ' + str(b)}")
        sys.exit(0)
    syms = args or ORIG + [s for s in OI.NEW if s in A.SRC]
    prof = A.spread_profile()
    for s in syms:
        t = time.time(); did = build(s, prof, Q.REFS)
        print(f"{s}: {'built' if did else 'up to date'} ({time.time() - t:.0f}s)", flush=True)
