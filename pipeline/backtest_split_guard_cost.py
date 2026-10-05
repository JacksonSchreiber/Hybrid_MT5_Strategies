"""backtest_split_guard_cost.py - what wider Test exclusions would cost: the eligible Test pool left after K served Practice sessions
under each guard rule (same symbol / any group symbol, reveal-only / history+reveal). Works in a temp root; writes nothing else."""
import os, sys, shutil, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, ROOT)
from live import backtest as BT
from live import symbol_groups as SG
TMP = tempfile.mkdtemp(prefix="bt_split_")
cfg = {"root": TMP, "backtest": {"data_dir": os.path.join(ROOT, "data", "backtest"), "dev": True}}
S = BT.store(cfg); sp = S["split"]["trades"]; LOCK = SG.RESEARCH_LOCK
out = []
for g, K in (("com", (1, 3, 6)), ("maj", (1, 3, 6))):
    test = {h for h, t in sp.items() if t["group"] == g and t["part"] == "test" and h in S["eligible"]}
    served = []; k0 = 0
    for K_ in K:
        while k0 < K_:
            r = BT.start(cfg, g, "practice", LOCK, None); s = BT.load_session(cfg, r["sid"])
            s["shown"] = {str(i): 0 for i in range(s["n"])}; BT.save_session(cfg, s); served += s["ids"]; k0 += 1
        anc = [(sp[h]["key"][0], BT._date(sp[h])) for h in served]
        def grp(lo, hi):
            ds = [a[1] for a in anc]
            return {h for h in test if any(lo <= (BT._date(sp[h]) - a).days <= hi for a in ds)}
        rules = {"same symbol, reveal 0..+14 (shipped)": BT._near(S["split"], g, "test", anc, 0, 14) & test,
                 "same symbol, history+reveal -60..+14": BT._near(S["split"], g, "test", anc, -60, 14) & test,
                 "any group symbol, reveal 0..+14": grp(0, 14),
                 "any group symbol, -60..+14": grp(-60, 14)}
        out.append(f"{g} after {K_} served Practice sessions ({len(set(served))} distinct cards), eligible Test pool {len(test)}:")
        out += [f"   {nm:40} excludes {len(v):4} -> {len(test) - len(v):4} left" for nm, v in rules.items()]
print("\n".join(out)); shutil.rmtree(TMP, ignore_errors=True)
