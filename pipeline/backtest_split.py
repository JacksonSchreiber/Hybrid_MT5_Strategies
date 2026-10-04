#!/usr/bin/env python3
"""backtest_split.py - coach 2026-10-04 (backtest tab, phase 1): the FROZEN Practice / Test split and the session sampler.

SPLIT (version 1): per symbol group, a fixed-seed 70/30 Practice/Test split, stratified by year x direction x detector. Within each
stratum the trades are shuffled with a seed derived from (seed, group, stratum); the Test count per stratum is set by largest
remainder so the group total is round(30%). EVERY trade of the group is assigned, research-locked ones too - the tab filters locked
trades at serve time with the live lock reader (webapp.research_lock()), so editing the lock never moves a partition.
Output data/backtest/split_v1.json: {version, seed, created, groups{key: {name, syms, n, practice, test}}, sha256 (of the sorted
hid -> [key, group, part] map), trades{hid: {key: [sym, t, strat], group, part, year, up, det}}}. hid = the first 16 hex of
sha256(salt | sym | t | strat); the salt lives in data/backtest/salt.txt (git-ignored data dir, never in the repo or the browser).

SESSIONS (coach item 11): a stratified random draw from one group's partition matching that partition's year x direction x detector
proportions (largest remainder), shown in shuffled order; no two signals of the same symbol closer than 182 days; Test signals are
never drawn twice (data/backtest/test_ledger.json records every Test id ever drawn).
    python3 pipeline/backtest_split.py            build split_v1.json (refuses to overwrite a different split) + feasibility report
    python3 pipeline/backtest_split.py --report   feasibility only (max session sizes under the six-month rule, lock applied)
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, random, secrets, sys, time
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import symbol_groups as SG                         # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "backtest")
SPLIT = os.path.join(OUT, "split_v1.json"); SALT = os.path.join(OUT, "salt.txt"); LEDGER = os.path.join(OUT, "test_ledger.json")
REPORT = os.path.join(OUT, "split_v1_report.txt")
VERSION, SEED, TEST_FRAC, GAP_DAYS = 1, 20261004, 0.30, 182
GROUPS = ("com", "maj")                                      # commodity bloc, USD majors (coach item 6)


def salt() -> str:
    os.makedirs(OUT, exist_ok=True)
    if not os.path.exists(SALT): open(SALT, "w").write(secrets.token_hex(16))
    return open(SALT).read().strip()


def hid_of(s: str, key) -> str:
    return hashlib.sha256(f"{s}|{key[0]}|{key[1]}|{key[2]}".encode()).hexdigest()[:16]


def group_syms(g: str) -> list[str]:
    return next(ss for k, n, c, ss, _ in SG.GROUPS if k == g)


def group_name(g: str) -> str:
    return next(n for k, n, c, ss, _ in SG.GROUPS if k == g)


def stratum(x) -> tuple:
    return (int(x["t"][:4]), "L" if x["up"] else "S", x["strat"])


def largest_remainder(sizes: dict, frac: float, total: int, tiebreak) -> dict:
    """{stratum: n} -> {stratum: count} summing to `total`, each floor(n*frac) plus one for the largest remainders."""
    q = {s: n * frac for s, n in sizes.items()}; out = {s: int(v) for s, v in q.items()}
    rest = total - sum(out.values())
    for s in sorted(q, key=lambda s: (-(q[s] - int(q[s])), tiebreak(s)))[:max(0, rest)]: out[s] += 1
    return out


def build_split(pop) -> dict:
    sl = salt(); trades = {}; groups = {}
    for g in GROUPS:
        syms = set(group_syms(g))
        xs = sorted([x for x in pop if x["sym"].split(".")[0] in syms], key=lambda x: (x["sym"], x["t"], x["strat"]))
        st: dict = {}
        for x in xs: st.setdefault(stratum(x), []).append(x)
        tb = lambda s, g=g: hashlib.sha256(f"{SEED}|{g}|{s}".encode()).hexdigest()
        n_test = largest_remainder({s: len(v) for s, v in st.items()}, TEST_FRAC, round(TEST_FRAC * len(xs)), tb)
        for s, v in sorted(st.items()):
            v = sorted(v, key=lambda x: (x["sym"], x["t"], x["strat"]))
            random.Random(f"{SEED}|{g}|{s}").shuffle(v)
            for k, x in enumerate(v):
                key = (x["sym"], x["t"], x["strat"]); h = hid_of(sl, key)
                assert h not in trades, "hid collision"
                trades[h] = {"key": list(key), "group": g, "part": "test" if k < n_test[s] else "practice", "year": int(x["t"][:4]),
                             "up": bool(x["up"]), "det": x["strat"]}
        groups[g] = {"name": group_name(g), "syms": sorted(syms), "n": len(xs), "practice": sum(1 for t in trades.values() if t["group"] == g and t["part"] == "practice"),
                     "test": sum(1 for t in trades.values() if t["group"] == g and t["part"] == "test")}
    canon = json.dumps({h: [t["key"], t["group"], t["part"]] for h, t in sorted(trades.items())}, separators=(",", ":"))
    return {"version": VERSION, "seed": SEED, "test_frac": TEST_FRAC, "created": time.strftime("%Y-%m-%d %H:%M"), "groups": groups,
            "sha256": hashlib.sha256(canon.encode()).hexdigest(), "trades": trades}


def load_split() -> dict:
    return json.load(open(SPLIT))


def locked(t: dict, lock: dict) -> bool:
    r = t["key"][0].split(".")[0]
    return r in lock["locked"] or (t["year"] >= lock["period_from"] and r not in lock["exposed"])


def _date(t: dict) -> dt.date:
    return dt.date(int(t["key"][1][:4]), int(t["key"][1][5:7]), int(t["key"][1][8:10]))


def max_session(split: dict, g: str, part: str, lock: dict, exclude: set = frozenset()) -> dict:
    """the most signals one session could hold under the six-month rule (greedy earliest-first = the optimum per symbol)."""
    out = {}
    for s in group_syms(g):
        ds = sorted(_date(t) for h, t in split["trades"].items()
                    if t["group"] == g and t["part"] == part and t["key"][0].split(".")[0] == s and not locked(t, lock) and h not in exclude)
        n, last = 0, None
        for d in ds:
            if last is None or (d - last).days >= GAP_DAYS: n += 1; last = d
        out[s] = n
    return out


def sample_session(split: dict, g: str, part: str, n: int, seed, lock: dict, record: bool = False) -> dict:
    """{ids: [...] (shuffled), shortfall: {stratum: missing}, n_target, menu-free}. Test draws exclude every Test id ever drawn;
    record=True appends the draw to the ledger (only a real Test session does this)."""
    used = set(json.load(open(LEDGER))["ids"]) if (part == "test" and os.path.exists(LEDGER)) else set()
    pool = {h: t for h, t in split["trades"].items() if t["group"] == g and t["part"] == part and not locked(t, lock) and h not in used}
    st: dict = {}
    for h, t in pool.items(): st.setdefault((t["year"], "L" if t["up"] else "S", t["det"]), []).append(h)
    full = {}
    for h, t in split["trades"].items():                     # proportions = the whole partition (not just what is left)
        if t["group"] == g and t["part"] == part and not locked(t, lock): full[(t["year"], "L" if t["up"] else "S", t["det"])] = full.get((t["year"], "L" if t["up"] else "S", t["det"]), 0) + 1
    tot = sum(full.values())
    quota = largest_remainder(full, n / tot, n, lambda s: hashlib.sha256(f"{seed}|{s}".encode()).hexdigest()) if tot else {}
    rng = random.Random(f"{seed}|{g}|{part}|{n}")
    picked: list = []; last: dict = {}; short = {}
    def ok(h):
        t = pool[h]; s = t["key"][0].split(".")[0]; d = _date(t)
        return all(abs((d - d2).days) >= GAP_DAYS for d2 in last.get(s, []))
    order = sorted(quota, key=lambda s: (len(st.get(s, [])) / max(1, quota[s]), s))   # tightest strata first
    for s in order:
        cand = sorted(st.get(s, [])); rng.shuffle(cand); got = 0
        for h in cand:
            if got >= quota[s]: break
            if ok(h):
                picked.append(h); got += 1; t = pool[h]; last.setdefault(t["key"][0].split(".")[0], []).append(_date(t))
        if got < quota[s]: short[s] = quota[s] - got
    rng.shuffle(picked)
    if record and part == "test":
        led = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {"ids": [], "sessions": []}
        led["ids"] += picked; led["sessions"].append({"group": g, "n": len(picked), "seed": str(seed), "at": time.strftime("%Y-%m-%d %H:%M")})
        json.dump(led, open(LEDGER, "w"))
    return {"ids": picked, "shortfall": short, "n_target": n}


def report(split: dict) -> str:
    lock = SG.RESEARCH_LOCK
    L = [f"split v{split['version']} seed {split['seed']} created {split['created']} sha256 {split['sha256'][:16]}..",
         f"lock used for the report: symbol_groups.RESEARCH_LOCK default (the tab uses the live lock reader at serve time)"]
    for g, gi in split["groups"].items():
        L.append(f"\n{gi['name']} ({', '.join(gi['syms'])}): {gi['n']} trades -> practice {gi['practice']}, test {gi['test']} "
                 f"({100 * gi['test'] / gi['n']:.1f}% test)")
        for part in ("practice", "test"):
            nl = sum(1 for t in split["trades"].values() if t["group"] == g and t["part"] == part and not locked(t, lock))
            m = max_session(split, g, part, lock)
            L.append(f"  {part:8} {nl} served after the lock; max signals in ONE session under the six-month rule: {sum(m.values())} "
                     f"({', '.join(f'{s} {v}' for s, v in m.items())})")
        tgt = {"com": 100, "maj": 50}[g]
        for part in ("practice", "test"):
            r = sample_session(split, g, part, tgt, "feasibility", lock)
            L.append(f"  trial draw of {tgt} from {part}: got {len(r['ids'])}" + (f", short in {len(r['shortfall'])} strata ({sum(r['shortfall'].values())} signals)" if r["shortfall"] else ""))
        # how many disjoint Test sessions of the target size fit (draw, exclude, repeat; not recorded)
        used: set = set(); k = 0
        while True:
            m = max_session(split, g, "test", lock, used)
            if sum(m.values()) < tgt: break
            pool = sorted(h for h, t in split["trades"].items() if t["group"] == g and t["part"] == "test" and not locked(t, lock) and h not in used)
            last: dict = {}; got = []
            for h in sorted(pool, key=lambda h: split["trades"][h]["key"][1]):
                t = split["trades"][h]; s = t["key"][0].split(".")[0]; d = _date(t)
                if all(abs((d - d2).days) >= GAP_DAYS for d2 in last.get(s, [])) and len(got) < tgt:
                    got.append(h); last.setdefault(s, []).append(d)
            if len(got) < tgt: break
            used |= set(got); k += 1
        L.append(f"  disjoint Test sessions of {tgt} that fit (six-month rule, no reuse): {k}")
    return "\n".join(L)


def main():
    from pipeline import combo_build as CB
    if "--report" not in sys.argv:
        sp = build_split(CB.population())
        if os.path.exists(SPLIT):
            old = load_split()
            if old["sha256"] != sp["sha256"]: raise SystemExit(f"split_v1.json exists with a different sha ({old['sha256'][:12]} vs {sp['sha256'][:12]}) - frozen; bump VERSION for a new split")
            sp = old
        else:
            json.dump(sp, open(SPLIT, "w"), separators=(",", ":"))
    r = report(load_split()); open(REPORT, "w").write(r + "\n"); print(r)


if __name__ == "__main__": main()
