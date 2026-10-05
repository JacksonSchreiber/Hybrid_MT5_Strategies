"""backtest_split_blocks.py - feasible Test session sizes: the current random split (ledger only) vs two time-blocked designs with
buffers (a Practice card shows ~60 days of history and reveals ~14 days, so Practice drops 14 days before and 60 days after each
Test block). Read-only; prints only."""
import os, sys, json, collections, datetime as dt
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, ROOT)
from live import backtest as BT
from live import symbol_groups as SG
cfg = {"root": "/nonexistent", "backtest": {"data_dir": os.path.join(ROOT, "data", "backtest"), "dev": True}}
S = BT.store(cfg); LOCK = SG.RESEARCH_LOCK; sp = S["split"]["trades"]; D = lambda t: BT._date(t)

def in_block(d, design):
    if design == "sep-dec": return d.month >= 9
    return d.year % 2 == 0                                   # alternate years
def part(t, design):
    d = D(t)
    if in_block(d, design): return "test"
    for k in range(1, 15):                                   # Practice whose 14-day reveal reaches a Test block
        if in_block(d + dt.timedelta(days=k), design): return "buffer"
    for k in range(1, 61):                                   # Practice whose 60-day history shows a Test block
        if in_block(d - dt.timedelta(days=k), design): return "buffer"
    return "practice"

def chain(doc, g, n):
    used = set(); got = []
    for k in range(40):
        r = BT.sample_session(doc, g, "test", n, f"x{n}_{k}", LOCK, S["eligible"], exclude=used)
        if len(r["ids"]) < n * 0.9: break
        used |= set(r["ids"]); got.append(len(r["ids"]))
    return len(got)

out = []
for design in ("random (current split_v1)", "sep-dec", "alternate-years"):
    doc = json.loads(json.dumps(S["split"]))
    if not design.startswith("random"):
        for t in doc["trades"].values(): t["part"] = part(t, design)
    out.append(f"\n{design}:")
    for g, sizes in (("com", (100, 70, 50, 35)), ("maj", (35, 25, 20, 15))):
        cnt = collections.Counter(t["part"] for t in doc["trades"].values() if t["group"] == g)
        mx = len(BT.sample_session(doc, g, "test", 400, "max", LOCK, S["eligible"])["ids"])
        prac = len(BT.sample_session(doc, g, "practice", sizes[0], "p", LOCK, S["eligible"])["ids"])
        out.append(f"  {g}: test {cnt['test']}, practice {cnt['practice']}, buffer {cnt.get('buffer', 0)}; largest single Test session {mx}; "
                   f"full Test sessions back to back: " + ", ".join(f"n={n}: {chain(doc, g, n)}" for n in sizes) + f"; a Practice session of {sizes[0]} draws {prac}")
print("\n".join(out))
