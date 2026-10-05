#!/usr/bin/env python3
"""backtest_split_guard_check.py - review 2 #2: serve K Practice sessions, draw the planned Test sessions with the shipped guard
(live/backtest.start: ledger + practice_shadow) and measure the exposure left (same symbol / any group symbol, reveal and history
directions); then check guard (b) - a Practice draw while a Test session is open. Works in a temp root; writes nothing else."""
import os, sys, json, shutil, datetime as dt, collections, bisect, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, ROOT)
from live import backtest as BT
from live import symbol_groups as SG
TMP = tempfile.mkdtemp(prefix="bt_split_")
cfg = {"root": TMP, "backtest": {"data_dir": os.path.join(ROOT, "data", "backtest"), "dev": True}}
S = BT.store(cfg); sp = S["split"]["trades"]; LOCK = SG.RESEARCH_LOCK
D = lambda h: BT._date(sp[h]); SYM = lambda h: sp[h]["key"][0]

def served_all(sid):
    s = BT.load_session(cfg, sid); s["shown"] = {str(i): 0 for i in range(s["n"])}; BT.save_session(cfg, s); return s

def exposure(test_ids, prac_ids, group_syms):
    pr = sorted((D(h), SYM(h)) for h in prac_ids); prd = [p[0] for p in pr]
    out = collections.Counter()
    for h in test_ids:
        d, s = D(h), SYM(h)
        lo = bisect.bisect_left(prd, d - dt.timedelta(days=14)); hi = bisect.bisect_right(prd, d)
        w = pr[lo:hi]
        out["same_reveal_14d_before"] += any(p[1] == s for p in w)
        out["group_reveal_14d_before"] += bool(w)
        lo2 = bisect.bisect_left(prd, d); hi2 = bisect.bisect_right(prd, d + dt.timedelta(days=60))
        w2 = pr[lo2:hi2]
        out["same_history_60d_after"] += any(p[1] == s for p in w2)
        out["group_history_60d_after"] += bool(w2)
    return out

L = []
for g, n_prac, n_test, k_prac, k_test in (("com", 100, 100, 3, 2), ("maj", 35, 35, 3, 3)):
    prac = []
    for k in range(k_prac):
        r = BT.start(cfg, g, "practice", LOCK, None); prac += served_all(r["sid"])["ids"]
    before = BT._test_left(cfg, S, g); shadow = BT.practice_shadow(cfg, S, g)
    tests = []; sizes = []
    for k in range(k_test):
        r = BT.start(cfg, g, "test", LOCK, None); s = BT.load_session(cfg, r["sid"]); tests += s["ids"]; sizes.append(s["n"])
        # finish it (decisions not needed for the measurement; mark it done so the next Practice draw isn't held by (b))
        s["decisions"] = [{"i": i, "decision": "skip"} for i in range(s["n"])]; s["pos"] = s["n"]; BT.save_session(cfg, s)
    ex = exposure(tests, prac, None); nt = len(tests)
    L.append(f"{g}: {k_prac} Practice sessions served ({len(prac)} cards, {len(set(prac))} distinct); Test pool {before} -> {before - len(shadow & set(h for h, t in sp.items() if t['group'] == g and t['part'] == 'test'))} after the 14-day shadow "
             f"({len(shadow)} Test ids shadowed); Test sessions drawn {sizes} (targets {n_test})")
    L.append(f"   drawn Test ids {nt}: SAME symbol served Practice in the 14 days before (reveal covers it): {ex['same_reveal_14d_before']} "
             f"(was the 68% upper bound); ANY group symbol in the 14 days before: {ex['group_reveal_14d_before']} ({100 * ex['group_reveal_14d_before'] / max(1, nt):.0f}%)")
    L.append(f"   history direction (a served Practice card's 60-day chart shows the Test future): same symbol {ex['same_history_60d_after']} "
             f"({100 * ex['same_history_60d_after'] / max(1, nt):.0f}%), any group symbol {ex['group_history_60d_after']} ({100 * ex['group_history_60d_after'] / max(1, nt):.0f}%)")
    # (b): open a Test session (n small), then a Practice draw must avoid its undecided ids' windows (same symbol)
    r = BT.start(cfg, g, "test", LOCK, "8"); s_open = BT.load_session(cfg, r["sid"])
    rp = BT.start(cfg, g, "practice", LOCK, None); sp_ids = BT.load_session(cfg, rp["sid"])["ids"]
    viol = sum(1 for p in sp_ids for t in s_open["ids"] if SYM(p) == SYM(t) and -14 <= (D(p) - D(t)).days <= 60)
    gviol = sum(1 for p in sp_ids for t in s_open["ids"] if -14 <= (D(p) - D(t)).days <= 60)
    L.append(f"   (b) Test session open ({s_open['n']} ids): a new Practice draw of {len(sp_ids)} cards has {viol} same-symbol cards within -14..+60 days "
             f"of an undecided Test id (group-wide pairs: {gviol})")

print("\n".join(L))
shutil.rmtree(TMP, ignore_errors=True)
