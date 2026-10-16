"""backtest.py - trader + coach 2026-10-04 (backtest tab, phase 2): the server side of the Backtest tab - PRACTICE mode, the lever
builder's evaluation, the per-signal report, and the advisor-recommendation hooks (the advisor consult itself comes later).

DATA (data dir = CFG.backtest.data_dir, default <root>/backtest; built by pipeline/backtest_*.py, uploaded by the parent):
  split_v1.json (hid -> key/group/part/year/up/det), cards_v1.json.gz (BLIND cards), results_v1.json.gz (per trade R under raw, the
  menu presets, options and the group default + flags + the post-signal path: SERVER-SIDE ONLY), menu_v1.json, info_v1.json
  (Practice-only lines tagged tested/observed), frames_v1/ (index.json + one <symbol>.bin each: the M1 frames the plain-Python rule
  engine (live/backtest_engine.py) evaluates a custom plan on).
STATE (<root>/web/backtest/): sessions/<sid>.json, reports/<sid>.csv + <sid>.summary.json, advisor/<sid>/<hid>.json.
SECRECY: the browser gets the blind card and the salted hashed id only; outcomes, results and the post-signal path go out only in
the reveal, after the decision is recorded server-side. The comparison is ALWAYS the raw signal (trader 2026-10-04).
SESSIONS (coach item 11): a stratified random draw from the group's Practice partition matching its year x direction x detector
proportions, shuffled, no two signals of one symbol within 91 days (coach 2026-10-04; was 182), thin strata may fill from the
neighbouring year (same direction + detector), research-locked trades never served (live lock reader).
Review 2026-10-04: no two signals of ANY symbol in the group within GROUP_GAP_DAYS (the crosses share legs - a revealed path must not
be a later card's chart), and cards without the event calendar or a regime tag are not served (they only happen early: they revealed
2012). Cards file: cards_v2 (events as class + coarse timing only; v1 fingerprinted the symbol).
Sizes: Practice 25 signals (coach 2026-10-06; was the Test sizes); Test commodity bloc 100, USD majors 35 (trader 2026-10-06: majors
stay 35), taken in BLOCKS of 20 (coach 2026-10-06): after each block the session stops at a checkpoint (/bt/next_block starts the
next one; leaving there and resuming later is fine). At a checkpoint the trader sees the AGGREGATE grade (trader 2026-10-08,
overriding the coach's "nothing at a checkpoint"): trader alone / advisor alone / combined for the block just finished and so far -
no per-card result, chart or CSV row.
ADVISOR PLAYS THE REST (trader 2026-10-08): at a checkpoint the trader can hand the rest of a Test session to the advisor
(/bt/advisor_only): his session ends there (graded on the cards he played), the blind advisor answers every remaining card, and its
results on those cards are revealed card by card (summary "advisor_only"; the CSV keeps his own decisions). Those ids stay spent.
TEST MODE (phase 3, 2026-10-05): a draw from the group's TEST partition; every Test id ever drawn goes into the ledger
(<root>/web/backtest/test_ledger.json) at once and is never drawn again ("one pass per signal ever"; a session cannot be restarted,
only resumed). Nothing is revealed during a Test session - no per-card result, no tally, no CSV rows, the session list shows no R -
until its last card is decided (except the aggregate grade at each block checkpoint); then the full report. Every Test card takes TWO steps (coach): the trader commits a plan + take/skip
(/bt/commit, immutable), only then the blind advisor's verdict + its v1 lever stack appear ("Advisor recommended", with its nearest
preset; follow-up questions allowed - later versions count toward the combined result only), and the trader gives his final
(/bt/decide). The advisor batch (live/backtest_advisor.py, run by the advisor runner) is queued when the session starts: one blind
consult per served card, in session order. Test needs the preset menu FROZEN (menu_v1.json "frozen").
GRADING (the session summary): the trader alone (his first commit), the advisor alone (its blind v1 verdict + stack) and both together
(the final), each against the raw signal (every signal taken raw - the only comparison; Minimal removed 2026-10-08), at model cost and
+1 pip; the selection inputs (take rate on signals that reached +1R minus take rate on signals that went to the stop without reaching
it, with a two-proportion z) and the settings input (R of the chosen plan minus the raw signal on the taken signals, paired t).

ADVISOR RECOMMENDATION (schema "advisor-levers/v1" - what the advisor emits, as a fenced block in its reply):
    ```levers
    {"schema": "advisor-levers/v1", "nearest_preset": "ruthless", "rationale": "one plain-words line",
     "plan": {"entry": {"pct": 100}, "tp2": true, "trim_to_cap": false, "levers": [{"type": "bank", "pct": 25, "at": 1.0, "tp1": true}, ...]}}
    ```
  nearest_preset: raw or one of the served preset keys (ruthless / slow / banker / pyramid); plan: a levers/v1 plan (see
  live/backtest_engine.py - only levers the engine evaluates, within the 200% cap); anything else is REJECTED with the reason logged.
  Versions per (session, card): v1 = generated BLIND from the pre-generated card bundle (blind_v1: true); later versions come from
  conversation ("v2 - updated after your question"); every version is kept with its time. Advisor-alone grading uses v1; the
  combined result uses the latest. The advisor never sees outcomes, results, combo data or the trader's first commit: nothing from
  a session's decisions is ever written into anything an advisor reads (recommendations are write-only from the advisor side here).
  When the slot is visible is a per-mode flag (CFG.backtest.advisor_slot: practice "off" by default, test "after_commit" = the
  coach's two-step: the trader commits, the advisor's plan is revealed, the trader gives his final).
"""
from __future__ import annotations
import csv, datetime as dt, gzip, hashlib, io, json, os, random, re, secrets, threading, time
import numpy as np
from live import backtest_engine as E

GAP_DAYS = 91                # coach 2026-10-04: one symbol at most once per THREE months (the longest history a card shows + a month)
GROUP_GAP_DAYS = 15          # >= the reveal horizon (60 H4 bars ~ 10 trading days)
MAX_BODY = 64 * 1024
PRACTICE_SIZE = 25           # coach 2026-10-06: a Practice session is 25 signals (same stratified draw, same three-month rule)
TEST_SIZES = {"com": 100, "maj": 35}
BLOCK = 20                   # coach 2026-10-06: a Test session is taken in blocks of 20, nothing revealed until the end
SIZES = TEST_SIZES           # the groups
DEFAULTS = {"tab_visible": False, "data_dir": None, "advisor_slot": {"practice": "off", "test": "after_commit"}, "dev": False}
_LOCK = threading.RLock()       # review 2 #1: reentrant (store() reloads under it; card()/report_csv() also call store() before locking)
_STORE: dict = {}
PRESET_KEYS = ("ruthless", "slow", "banker", "pyramid")     # trader 2026-10-08: Minimal removed ("shaped off the data and invalid")
LOADABLE = ("default", "raw", "advisor", "advisor_v1") + PRESET_KEYS      # what the builder can load (the "loaded_preset" a plan started from)
FRAME_CAP_DAYS = 40
PIP_COST = 1.0                                              # the +1 pip round-trip summary (coach 2026-10-04): cost = gross units x 1 pip / stop pips
KIND = {0: "stopped out", 1: "TP2 hit", 2: "closed by a cut", 3: "still open at the frame end (closed at the last close)",
        4: "banked out (everything was banked)"}


def settings(cfg: dict) -> dict:
    s = dict(DEFAULTS); s.update(cfg.get("backtest") or {})
    s["advisor_slot"] = dict(DEFAULTS["advisor_slot"], **((cfg.get("backtest") or {}).get("advisor_slot") or {}))
    return s


def data_dir(cfg: dict) -> str:
    return settings(cfg)["data_dir"] or os.path.join(cfg["root"], "backtest")


def web_dir(cfg: dict, *sub) -> str:
    d = os.path.join(cfg["root"], "web", "backtest", *sub); os.makedirs(d, exist_ok=True); return d


def store(cfg: dict) -> dict:
    """the read-only data, loaded once (reloaded when the results file changes)."""
    d = data_dir(cfg)
    cf = next((os.path.join(d, n) for n in ("cards_v3.json.gz", "cards_v2.json.gz", "cards_v1.json.gz") if os.path.exists(os.path.join(d, n))),
              os.path.join(d, "cards_v1.json.gz"))      # v3 = with the detector overlays (coach: required)
    key = (d, os.path.getmtime(os.path.join(d, "results_v1.json.gz")), cf, os.path.getmtime(cf))
    if _STORE.get("key") != key:
        with _LOCK:
            split = json.load(open(os.path.join(d, "split_v1.json")))
            cards = json.load(gzip.open(cf))["cards"]
            res = json.load(gzip.open(os.path.join(d, "results_v1.json.gz")))
            frames = E.Frames(os.path.join(d, "frames_v1"))
            ok = {h for h in split["trades"] if h in cards and h in res["trades"] and h in frames.idx and servable(cards[h])}
            menu_file = json.load(open(os.path.join(d, "menu_v1.json")))
            fi = os.path.join(d, "frames_inv_v1"); frames_inv = E.Frames(fi) if os.path.exists(os.path.join(fi, "index.json")) else None
            _STORE.clear(); _STORE.update(key=key, split=split, cards=cards, results=res["trades"], menu=menu_view(menu_file), menu_file=menu_file,
                                          info=info_view(json.load(open(os.path.join(d, "info_v1.json")))), frames=frames, frames_inv=frames_inv, eligible=ok)
    return _STORE


def describe(cfg: dict) -> str:
    """a plan's levers in plain words (the preset descriptions no longer lean on Minimal)."""
    R = lambda v: "entry" if v == 0 else f"{v:+g}R"
    st = lambda v: "the trade stop" if v == "trade" else R(v)
    out = [f"{cfg['entry']['pct']:g}% at the signal"]
    for l in cfg["levers"]:
        t = l["type"]; tp = " (or TP1 if nearer)" if l.get("tp1") else ""
        if t == "bank": out.append(f"bank {l['pct']:g}% at +{l['at']:g}R{tp}")
        elif t == "move": out.append(f"stop to {R(l['to'])} at {l['when']:+g}R{tp}")
        elif t == "add_reach": out.append(f"+{l['pct']:g}% if +{l['at']:g}R" + (f" within {l['within_h']:g} h" if l.get("within_h") else "") + f" (its stop at {st(l['stop'])})")
        elif t == "add_bar": out.append(f"+{l['pct']:g}% at the bar-{l['bar']} close" + (f" or early at +{l['early_at']:g}R" if l.get("early_at") is not None else "")
                                        + (f" (held while the average R < {l['hold_avg']:+g}R)" if l.get("hold_avg") is not None else ""))
        elif t == "add_tp1": out.append(f"+{l['pct']:g}% at TP1 (its stop at {st(l['stop'])})")
        elif t == "cut_avg": out.append(f"close at {l['at_h']:g} h if the average R" + (f" of the last {l['window_h']:g} h" if l.get("window_h") else "") + " is below " + ("0R" if l["below"] == 0 else f"{l['below']:+g}R"))
        elif t == "trail": out.append(f"every {l['every_h']:g} h the stop to {l['dist']:g}R below the high" + (" (both ways)" if l.get("lower") else ""))
        elif t == "cut_unless": out.append(f"close at {l['at_h']:g} h unless +{l['reach']:g}R was reached")
    if not cfg.get("tp2", True): out.append("no TP2 exit")
    if cfg.get("trim_to_cap"): out.append("trimmed to the 200% cap")
    return "; ".join(out)


def menu_view(m: dict) -> dict:
    """trader 2026-10-08: "get rid of the minimal preset, since it is shaped off the data and invalid" - the menu as the tab and the
    advisor see it: no Minimal, the other presets described by their own levers, every group's preloaded plan = the raw signal (the only
    comparison). The frozen menu file itself is unchanged (its results stay reproducible)."""
    v = dict(m); raw = m["raw"]
    v["presets"] = [dict(p, about=describe(p["cfg"])) for p in m["presets"] if p["key"] != "minimal"]
    v["defaults"] = {g: {"preset": "raw", "label": "Raw signal", "about": "In at the signal, out at TP2 or the stop - nothing added", "cfg": raw,
                         "status": "the raw signal - the only comparison (trader 2026-10-08)"} for g in m["defaults"]}
    return v


def info_view(info: dict) -> dict:
    """the group notes without the lines built on Minimal (its own line and the bloc default 'Minimal + 1h cut')."""
    v = dict(info); v["groups"] = {g: dict(gi, lines=[l for l in gi["lines"] if "minimal" not in l["text"].lower()]) for g, gi in info["groups"].items()}
    return v


# ------------------------------------------------------------------------------------------------------------- sampler
def servable(c: dict) -> bool:
    """review 2026-10-04: a card without the event calendar or a regime tag would reveal its period (all early, mostly 2012)."""
    return c.get("calendar") == "ok" and bool((c.get("regime") or {}).get("tag"))


def locked(t: dict, lock: dict) -> bool:
    r = t["key"][0].split(".")[0]
    return r in lock["locked"] or (t["year"] >= lock["period_from"] and r not in lock["exposed"])


def _date(t: dict) -> dt.date:
    return dt.date(int(t["key"][1][:4]), int(t["key"][1][5:7]), int(t["key"][1][8:10]))


def largest_remainder(sizes: dict, frac: float, total: int, tiebreak) -> dict:
    q = {s: n * frac for s, n in sizes.items()}; out = {s: int(v) for s, v in q.items()}
    rest = total - sum(out.values())
    for s in sorted(q, key=lambda s: (-(q[s] - int(q[s])), tiebreak(s)))[:max(0, rest)]: out[s] += 1
    return out


def sample_session(split: dict, g: str, part: str, n: int, seed, lock: dict, eligible: set, exclude: set = frozenset()) -> dict:
    """pipeline/backtest_split.sample_session, ported (the box has no pipeline package) + the eligible filter (cards/results/frames)."""
    pool = {h: t for h, t in split["trades"].items() if t["group"] == g and t["part"] == part and not locked(t, lock) and h in eligible and h not in exclude}
    st: dict = {}
    for h, t in pool.items(): st.setdefault((t["year"], "L" if t["up"] else "S", t["det"]), []).append(h)
    full: dict = {}
    for h, t in split["trades"].items():
        if t["group"] == g and t["part"] == part and not locked(t, lock) and h in eligible:
            k = (t["year"], "L" if t["up"] else "S", t["det"]); full[k] = full.get(k, 0) + 1
    tot = sum(full.values())
    quota = largest_remainder(full, n / tot, n, lambda s: hashlib.sha256(f"{seed}|{s}".encode()).hexdigest()) if tot else {}
    rng = random.Random(f"{seed}|{g}|{part}|{n}")
    picked: list = []; last: dict = {}; short = {}; alld: list = []; picked_set: set = set()
    def ok(h):
        t = pool[h]; s = t["key"][0].split(".")[0]; d = _date(t)
        return all(abs((d - d2).days) >= GAP_DAYS for d2 in last.get(s, [])) and all(abs((d - d2).days) >= GROUP_GAP_DAYS for d2 in alld)
    for s in sorted(quota, key=lambda s: (len(st.get(s, [])) / max(1, quota[s]), s)):
        cand = sorted(st.get(s, [])); rng.shuffle(cand); got = 0
        for h in cand:
            if got >= quota[s]: break
            if ok(h):
                picked.append(h); picked_set.add(h); got += 1; t = pool[h]; last.setdefault(t["key"][0].split(".")[0], []).append(_date(t)); alld.append(_date(t))
        if got < quota[s]: short[s] = quota[s] - got
    # coach 2026-10-04: a thin stratum may fill from the neighbouring year (same direction + detector, the nearest year first)
    filled = 0
    for s in sorted(short):
        y0, dr, dt_ = s
        cand = [h for h, t in pool.items() if h not in picked_set and (t["year"], "L" if t["up"] else "S", t["det"]) != s
                and ("L" if t["up"] else "S") == dr and t["det"] == dt_]
        rng.shuffle(cand); cand.sort(key=lambda h: abs(pool[h]["year"] - y0))
        for h in cand:
            if short[s] <= 0: break
            if ok(h):
                picked.append(h); picked_set.add(h); short[s] -= 1; filled += 1; t = pool[h]
                last.setdefault(t["key"][0].split(".")[0], []).append(_date(t)); alld.append(_date(t))
    short = {k: v for k, v in short.items() if v > 0}
    rng.shuffle(picked)
    return {"ids": picked, "shortfall": {"|".join(map(str, k)): v for k, v in short.items()}, "n_target": n, "filled_from_neighbours": filled}


# ------------------------------------------------------------------------------------------------------------- sessions
def card_no(v) -> int:
    """a card number from a request (review 2: a clean error instead of Python's 'invalid literal for int()')."""
    if v in (None, ""): return -1
    if not re.fullmatch(r"[0-9]{1,4}", str(v).strip()): raise ValueError("bad card number")
    return int(v)


def _sp(cfg, sid): return os.path.join(web_dir(cfg, "sessions"), f"{sid}.json")
def _safe_sid(sid: str) -> bool: return bool(re.fullmatch(r"[0-9a-f]{12}", sid or ""))
def _now() -> str: return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_session(cfg, sid):
    if not _safe_sid(sid): raise ValueError("bad session id")
    try: return json.load(open(_sp(cfg, sid)))
    except FileNotFoundError: raise ValueError("unknown session") from None
    except (OSError, ValueError): raise ValueError("the session file could not be read") from None


def save_session(cfg, s):
    tmp = _sp(cfg, s["sid"]) + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, _sp(cfg, s["sid"]))


def _ledger_path(cfg) -> str: return os.path.join(web_dir(cfg), "test_ledger.json")


def load_ledger(cfg) -> dict:
    """every Test id ever drawn (coach: one pass per signal ever) + the Test sessions."""
    try: return json.load(open(_ledger_path(cfg)))
    except FileNotFoundError: return {"ids": [], "sessions": []}


def _save_ledger(cfg, led: dict) -> None:
    tmp = _ledger_path(cfg) + ".tmp"; json.dump(led, open(tmp, "w")); os.replace(tmp, _ledger_path(cfg))


def start(cfg: dict, group: str, mode: str, lock: dict, n_override=None) -> dict:
    if group not in SIZES: raise ValueError("unknown group")
    if mode not in ("practice", "test"): raise ValueError("mode must be practice or test")
    S = store(cfg); seed = secrets.randbelow(10 ** 9); n = TEST_SIZES[group] if mode == "test" else PRACTICE_SIZE; menu = S["menu"]
    if n_override not in (None, ""):
        if not settings(cfg)["dev"]: raise ValueError("a custom session size is a dev option")
        if not re.fullmatch(r"[0-9]{1,4}", str(n_override).strip()) or int(n_override) < 1:      # review 2 #13e
            raise ValueError("the session size must be a whole number of at least 1")
        n = min(n, int(n_override))
    frozen = menu.get("frozen") or {}
    if mode == "test" and not frozen.get("at"): raise ValueError("the preset menu is not frozen - a Test session needs a frozen menu")
    with _LOCK:
        if mode == "test":                          # the ledger + review 2 #2a: Test ids whose future a served Practice reveal showed
            excl = set(load_ledger(cfg)["ids"]) | practice_shadow(cfg, S, group)
        else:                                       # review 2 #2b: while a Test session is open, no Practice card near its undecided ids
            excl = open_test_shadow(cfg, S, group)
        r = sample_session(S["split"], group, mode, n, seed, lock, S["eligible"], exclude=excl)
        if not r["ids"]: raise ValueError("no signals left to draw for this group")
        sid = secrets.token_hex(6)
        s = {"sid": sid, "group": group, "mode": mode, "seed": seed, "n": len(r["ids"]), "n_target": n, "shortfall": r["shortfall"],
             "created": _now(), "menu": menu["version"], "menu_frozen_at": frozen.get("at"), "ids": r["ids"], "pos": 0, "shown": {},
             "decisions": [], "commits": {}, "adv_loaded": {}, "block": BLOCK if mode == "test" else None, "block_ok": {}}
        if mode == "test":                          # the advisor role this session starts on (an older session keeps v2 for its first block)
            from live import backtest_advisor as BA
            s["adv_role"] = BA.ROLE_VERSION
        if mode == "test":                          # the ledger takes the ids at once: a drawn Test signal is spent even if never decided
            led = load_ledger(cfg); led["ids"] += r["ids"]
            led["sessions"].append({"sid": sid, "group": group, "n": len(r["ids"]), "at": _now(), "menu": menu["version"], "seed": seed})
            _save_ledger(cfg, led)
        save_session(cfg, s)
    if mode == "test": _top_up_advisor(cfg, s, S)   # the first ~10 cards' blind consults; the rest follow the trader (review 2 #9)
    return {"sid": sid, "n": s["n"], "seed": seed, "group": group, "mode": mode, "shortfall": r["shortfall"]}


def experiment_session(cfg: dict, group: str, lock: dict, n=None) -> dict:
    """trader 2026-10-08: a FRESH Test draw for an advisor experiment - the same sampler and exclusions as a Test session, the ids go
    into the Test ledger at once (spent: one pass per signal ever), the trader never plays it (advisor_only_from 0, experiment_only) and
    no Test advisor batch is queued; its experiments (live/backtest_experiment.py) show on its end page."""
    if group not in SIZES: raise ValueError("unknown group")
    S = store(cfg); seed = secrets.randbelow(10 ** 9); n = int(n or TEST_SIZES[group]); menu = S["menu"]; frozen = menu.get("frozen") or {}
    with _LOCK:
        excl = set(load_ledger(cfg)["ids"]) | practice_shadow(cfg, S, group)
        r = sample_session(S["split"], group, "test", n, seed, lock, S["eligible"], exclude=excl)
        if not r["ids"]: raise ValueError("no signals left to draw for this group")
        sid = secrets.token_hex(6)
        s = {"sid": sid, "group": group, "mode": "test", "seed": seed, "n": len(r["ids"]), "n_target": n, "shortfall": r["shortfall"],
             "created": _now(), "menu": menu["version"], "menu_frozen_at": frozen.get("at"), "ids": r["ids"], "pos": 0, "shown": {},
             "decisions": [], "commits": {}, "adv_loaded": {}, "block": BLOCK, "block_ok": {}, "advisor_only_from": 0, "advisor_only_at": _now(),
             "experiment_only": True}
        led = load_ledger(cfg); led["ids"] += r["ids"]
        led["sessions"].append({"sid": sid, "group": group, "n": len(r["ids"]), "at": _now(), "menu": menu["version"], "seed": seed, "experiment": True})
        _save_ledger(cfg, led); save_session(cfg, s)
    return {"sid": sid, "n": s["n"], "group": group, "shortfall": r["shortfall"]}


def _top_up_advisor(cfg, s, S) -> None:
    """keep the blind batch about ADVISOR_AHEAD cards ahead of the trader's position (never fatal - the ids are spent)."""
    try:
        from live import backtest_advisor as BA
        BA.queue_session(cfg, s, S)
    except Exception as e:
        with open(os.path.join(web_dir(cfg), "advisor_rejects.log"), "a") as f: f.write(f"{_now()}|{s['sid']}|-|queue|advisor batch not queued: {e!r}\n")


# review 2 #2 - the split is random per trade (not time-blocked), so a Practice reveal (60 H4 bars ~ 14 calendar days after its
# signal) can show the future of a Test signal of the same symbol, and a Practice card's ~60-day history can show the future of a
# Test signal drawn before it. Cheap mitigations (the design question - a time-blocked split - is the coach's):
REVEAL_DAYS = 14             # what a Practice reveal shows after its signal
HISTORY_DAYS = 60            # what a card's chart shows before its signal


def _served(s: dict) -> list:
    """the hids of a session's cards the trader has seen (shown or decided)."""
    seen = {int(k) for k in s.get("shown", {})} | {d["i"] for d in s.get("decisions", [])}
    return [s["ids"][i] for i in sorted(seen) if i < len(s["ids"])]


def _sessions(cfg) -> list:
    out = []
    for f in os.listdir(web_dir(cfg, "sessions")):
        if f.endswith(".json"):
            try: out.append(json.load(open(os.path.join(web_dir(cfg, "sessions"), f))))
            except (OSError, ValueError): pass
    return out


def _near(split: dict, group: str, part: str, anchors: list, lo_days: int, hi_days: int) -> set:
    """ids of `part` in `group` whose signal date d satisfies a + lo_days <= d <= a + hi_days for an anchor (symbol, date) of the
    same symbol."""
    by: dict = {}
    for sym, d in anchors: by.setdefault(sym, []).append(d)
    out = set()
    for h, t in split["trades"].items():
        if t["group"] != group or t["part"] != part: continue
        ds = by.get(t["key"][0])
        if not ds: continue
        d = _date(t)
        if any(lo_days <= (d - a).days <= hi_days for a in ds): out.add(h)
    return out


def practice_shadow(cfg: dict, S: dict, group: str) -> set:
    """#2a: Test ids within REVEAL_DAYS after a SERVED Practice card of the same symbol (that reveal showed their future)."""
    sp = S["split"]["trades"]; anchors = []
    for s in _sessions(cfg):
        if s.get("mode") != "practice" or s.get("group") != group: continue
        anchors += [(sp[h]["key"][0], _date(sp[h])) for h in _served(s) if h in sp]
    return _near(S["split"], group, "test", anchors, 0, REVEAL_DAYS)


def open_test_shadow(cfg: dict, S: dict, group: str) -> set:
    """#2b: while a Test session is open, Practice cards of the same symbol from REVEAL_DAYS before to HISTORY_DAYS after any of its
    undecided Test ids (their reveal, or their chart's history, would show that Test card's future)."""
    sp = S["split"]["trades"]; anchors = []
    for s in _sessions(cfg):
        if s.get("mode") != "test" or s.get("group") != group or len(s.get("decisions", [])) >= s.get("n", 0): continue
        anchors += [(sp[h]["key"][0], _date(sp[h])) for h in s["ids"][len(s.get("decisions", [])):] if h in sp]
    return _near(S["split"], group, "practice", anchors, -REVEAL_DAYS, HISTORY_DAYS)


def list_sessions(cfg: dict) -> list:
    out = []
    for f in sorted(os.listdir(web_dir(cfg, "sessions")), reverse=True):
        if not f.endswith(".json"): continue
        try: s = json.load(open(os.path.join(web_dir(cfg, "sessions"), f)))
        except (OSError, ValueError): continue
        hide = s["mode"] == "test" and not trader_done(s)                    # Test: nothing revealed until the trader's last card
        out.append({"sid": s["sid"], "group": s["group"], "mode": s["mode"], "created": s["created"], "n": s["n"], "done": len(s["decisions"]), "block": s.get("block"),
                    "tally": None if hide else tally(s), "advisor_only": s.get("advisor_only_from"), "experiment": bool(s.get("experiment_only"))})
    return sorted(out, key=lambda x: x["created"], reverse=True)[:50]


def trader_done(s: dict) -> bool:
    """the trader has nothing left to play: the last card is decided, or he handed the rest to the advisor (advisor_only_from)."""
    return s["pos"] >= s["n"] or s.get("advisor_only_from") is not None


def tally(s: dict) -> dict:
    d = s["decisions"]
    return {"decided": len(d), "taken": sum(1 for x in d if x["decision"] == "take"), "skipped": sum(1 for x in d if x["decision"] == "skip"),
            "R_choice": round(sum(x["R_choice"] for x in d), 3), "R_raw": round(sum(x["R_raw"] for x in d), 3),
            "R_default": round(sum(x["R_default"] for x in d), 3),
            "R_choice_1pip": round(sum(x.get("R_choice_1pip", x["R_choice"]) for x in d), 3), "R_raw_1pip": round(sum(x.get("R_raw_1pip", x["R_raw"]) for x in d), 3),
            "R_default_1pip": round(sum(x.get("R_default_1pip", x["R_default"]) for x in d), 3)}


def advisor_visible(cfg: dict, s: dict, i: int) -> bool:
    flag = settings(cfg)["advisor_slot"].get(s["mode"], "off")
    if flag == "always": return True
    if flag == "after_commit": return str(i) in s["commits"]
    return False


def card(cfg: dict, sid: str) -> dict:
    """the current card of a session (blind) - or the end-of-session summary."""
    S = store(cfg)                                  # review 2 #1: load/reload the data BEFORE taking the lock
    with _LOCK:
        s = load_session(cfg, sid); i = s["pos"]
        if trader_done(s):
            out = {"done": True, "tally": tally(s), "n": s["n"], "sid": sid, "mode": s["mode"], "advisor_only_from": s.get("advisor_only_from")}
            if s["mode"] == "test":                 # refreshed: an advisor v1 that arrived after the trader's final still counts
                write_report(cfg, s, S); out["summary"] = load_summary(cfg, sid)
                from live import backtest_experiment as BX          # offline advisor experiments on this session's cards (2026-10-08)
                out["experiments"] = BX.list_summaries(cfg, sid)
            return out
        b = s.get("block")                          # Test blocks (coach 2026-10-06): a checkpoint before cards 21, 41, ... - no card
        if b and i and i % b == 0 and str(i) not in s.get("block_ok", {}):
            write_report(cfg, s, S); sm = load_summary(cfg, sid) or {}     # trader 2026-10-08: the aggregate grade (block + so far) only
            return {"block_break": True, "sid": sid, "mode": s["mode"], "group": s["group"], "i": i, "n": s["n"], "block": b, "blocks_done": i // b,
                    "blocks": -(-s["n"] // b), "tally": {"decided": len(s["decisions"])}, "grading": sm.get("grading")}
        if str(i) not in s["shown"]: s["shown"][str(i)] = time.time(); save_session(cfg, s)
    if s["mode"] == "test": _top_up_advisor(cfg, s, S)     # review 2 #9: the batch runs ~10 cards ahead of the trader
    hid = s["ids"][i]; c = dict(S["cards"][hid]); c.pop("hid", None)      # no id before the decision
    g = s["group"]; info = S["info"]["groups"][g]; test = s["mode"] == "test"
    out = {"sid": sid, "i": i, "n": s["n"], "mode": s["mode"], "card": c, "info": info, "block": s.get("block"),
           "tally": {"decided": len(s["decisions"])} if test else tally(s),
           "default": S["menu"]["defaults"][g], "advisor": None, "advisor_flag": settings(cfg)["advisor_slot"].get(s["mode"], "off"),
           "committed": s["commits"].get(str(i))}
    if advisor_visible(cfg, s, i): out.update(_advisor_payload(cfg, s, i, hid))
    return out


def _gate(s: dict) -> None:
    """Test blocks: no card of the next block can be committed, decided or consulted before the trader starts that block."""
    b = s.get("block"); i = s["pos"]
    if s.get("advisor_only_from") is not None: raise ValueError("the advisor plays the rest of this session - nothing left to play")
    if b and i and i % b == 0 and i < s["n"] and str(i) not in s.get("block_ok", {}): raise ValueError("start the next block first")


def next_block(cfg: dict, sid: str) -> dict:
    """the trader starts the next block of a Test session (only at a checkpoint)."""
    with _LOCK:
        s = load_session(cfg, sid); i = s["pos"]; b = s.get("block")
        if s.get("advisor_only_from") is not None: raise ValueError("the advisor plays the rest of this session - nothing left to play")
        if s["mode"] != "test" or not b or not i or i % b or i >= s["n"]: raise ValueError("not at a block checkpoint")
        if str(i) in s.get("block_ok", {}): raise ValueError("this block has already started")
        s.setdefault("block_ok", {})[str(i)] = _now(); save_session(cfg, s)
    return {"ok": True, "sid": sid, "i": i, "block": i // b + 1}


def advisor_only(cfg: dict, sid: str) -> dict:
    """trader 2026-10-08: at a Test checkpoint, hand the rest of the session to the advisor - the trader's play ends here, every
    remaining card gets its blind consult, and the advisor's results on them are revealed as they come (summary "advisor_only")."""
    S = store(cfg)
    with _LOCK:
        s = load_session(cfg, sid); i = s["pos"]; b = s.get("block")
        if s["mode"] != "test" or not b: raise ValueError("only a Test session can be handed to the advisor")
        if s.get("advisor_only_from") is not None: raise ValueError("the advisor already plays the rest of this session")
        if not i or i % b or i >= s["n"] or str(i) in s.get("block_ok", {}): raise ValueError("only at a block checkpoint")
        s["advisor_only_from"] = i; s["advisor_only_at"] = _now(); save_session(cfg, s)
    from live import backtest_advisor as BA
    q = BA.queue_session(cfg, s, S, ahead=s["n"])
    return {"ok": True, "sid": sid, "from": i, "queued": q}


def _advisor_only(cfg, S, s) -> dict | None:
    """the advisor alone on the cards the trader handed over: its blind verdict + v1 plan, graded like the advisor-alone column
    (a TAKE whose stack was rejected = the raw signal), overall, per block and card by card."""
    a0 = s.get("advisor_only_from")
    if a0 is None or s.get("experiment_only"): return None          # an experiment-only draw has no Test advisor batch
    from live import backtest_advisor as BA
    b = s.get("block") or 10 ** 9; rows, cards, failed = [], [], 0
    for i in range(a0, s["n"]):
        hid = s["ids"][i]; st = BA.get_status(cfg, s["sid"], hid)
        if st.get("state") == "failed": failed += 1
        if st.get("state") != "done": continue
        gi = _grade_inputs(cfg, S, s, {"hid": hid, "i": i, "decision": "skip", "plan": None, "first": {"decision": "skip", "plan": None}})
        R = S["results"][hid]; sp = R.get("stop_pips"); gr = R.get("gross") or {}
        raw1 = R["R"]["raw"] - (PIP_COST * gr.get("raw", 1.0) / sp if sp else 0.0)
        rows.append({**gi, "blk": i // b, "R_raw": R["R"]["raw"], "R_raw_1pip": round(raw1, 4)})
        adv = advisor_record(cfg, s["sid"], hid)
        b1 = next((v for v in adv["versions"] if v.get("blind_v1")), None) if adv else None
        c = S["cards"][hid]
        cards.append({"card": i + 1, "detector": c.get("detector"), "direction": c.get("direction"), "verdict": gi["adv_verdict"],
                      "confidence": gi["adv_confidence"], "R_adv": gi["R_adv_alone"], "plan_graded": gi["adv_plan_graded"],
                      "R_plan_if_taken": round(_evaluate(S, hid, b1["plan"])["R"], 4) if (b1 and b1.get("valid")) else None,
                      "R_raw": R["R"]["raw"], "nearest_preset": (b1 or {}).get("nearest_preset"),
                      "rationale": (b1 or {}).get("rationale") or st.get("why") or "", "role": (gi.get("adv_role") or "")[23:25]})
    blocks = sorted({r["blk"] for r in rows})
    return {"from": a0, "total": s["n"] - a0, "answered": len(rows), "failed": failed, "at": s.get("advisor_only_at"),
            "grade": _grade(rows, "advisor"),
            "by_block": [{"block": k + 1, "cards": f"{max(a0, k * b) + 1}-{min(s['n'], (k + 1) * b)}",
                          "advisor_alone": _grade([r for r in rows if r["blk"] == k], "advisor")} for k in blocks],
            "cards": cards}


def _advisor_payload(cfg, s, i, hid) -> dict:
    """what the page may see of the advisor for card i (only once the slot is visible): the latest valid stack, the blind v1 stack
    when a later version replaced it (review 2 #13d: both loadable), its verdict, the consult state and the follow-up thread."""
    from live import backtest_advisor as BA
    st = BA.card_view(cfg, s["sid"], i, hid) if s["mode"] == "test" else {"state": "none"}
    lat = advisor_latest(cfg, s["sid"], hid); rec = advisor_record(cfg, s["sid"], hid); v1 = None
    b1 = next((v for v in (rec or {}).get("versions", []) if v.get("blind_v1") and v.get("valid")), None)
    if b1 and lat and b1["v"] != lat["v"]:
        v1 = {"v": b1["v"], "note": "v1 (blind)", "nearest_preset": b1["nearest_preset"], "rationale": b1["rationale"], "plan": b1["plan"]}
    return {"advisor": lat, "advisor_v1": v1, "adv": st}


def advisor_view(cfg: dict, sid: str, i: int) -> dict:
    """GET /api/bt/advisor - polled by the page while the advisor works or answers a follow-up (current card, slot visible only)."""
    s = load_session(cfg, sid)
    if i != s["pos"] or i >= s["n"]: raise ValueError("this card is not the current one")
    _gate(s)
    if not advisor_visible(cfg, s, i): raise ValueError("the advisor opens after your first commit")
    return {"ok": True, "i": i, **_advisor_payload(cfg, s, i, s["ids"][i])}


def ask(cfg: dict, sid: str, i: int, text: str) -> dict:
    """POST /bt/ask - a follow-up question to the advisor about the current card, after the first commit (coach: allowed; later
    versions count toward the combined result only)."""
    text = (text or "").strip()[:2000]
    if not text: raise ValueError("the question is empty")
    s = load_session(cfg, sid)
    if s["mode"] != "test": raise ValueError("follow-up questions are a Test-mode feature")
    if i != s["pos"] or i >= s["n"]: raise ValueError("this card is not the current one")
    _gate(s)
    if not advisor_visible(cfg, s, i): raise ValueError("the advisor opens after your first commit")
    from live import backtest_advisor as BA
    if BA.get_status(cfg, sid, s["ids"][i]).get("state") != "done":          # review 2 #4: v1 must be the blind answer, never a reply
        raise ValueError("the advisor has not given its view on this card yet - ask once it has")
    return BA.queue_followup(cfg, s, i, s["ids"][i], text)


_EVAL: dict = {}
_EVAL_MAX = 50000


def _evaluate(S, hid, cfg_plan, inverse: bool = False):
    """one plan on one trade - cached by (data build, hid, plan) (review 2 #10: write_report re-reads every row at each decide)."""
    k = (S.get("key"), hid, json.dumps(cfg_plan, sort_keys=True, separators=(",", ":")), inverse)
    r = _EVAL.get(k)
    if r is None:
        F, ctx = (S["frames_inv"] if inverse else S["frames"]).get(hid)       # inverse: the flipped trade's own frame (trader 2026-10-09)
        r = E.evaluate(cfg_plan, F, ctx)
        tm = ctx["tm"]; j = min(r["exit_j"], len(tm) - 1)
        r["exit_h"] = round(float(tm[j]) / 60.0, 1)
        if len(_EVAL) >= _EVAL_MAX: _EVAL.clear()
        _EVAL[k] = r
    return dict(r)


def path_view(S, hid, plan_n, inverse: bool = False) -> dict:
    """trader 2026-10-08 (a reveal that showed raw at -1R while its chart ended at +3.3R, and an exit marker 11 bars late): the
    post-signal H4 bars from the frame on the REAL 4-hour grid - clock buckets from the entry-bar open with the empty ones (weekends)
    dropped, the same grid the chart draws - to where the raw trade ended (the frame runs to the first -1R touch or 40 days); the
    plan's and the raw trade's exits as BAR positions and TRADING hours (the clock hours include weekends); and a note when raw came
    close to TP2 without filling it (a SELL closes at the ask, a BUY at the bid)."""
    F, ctx = (S["frames_inv"] if inverse else S["frames"]).get(hid); n = F["n"]; tm = np.asarray(ctx["tm"][:n]); kb = tm // 240
    starts = np.flatnonzero(np.r_[True, kb[1:] != kb[:-1]]); ends = np.r_[starts[1:] - 1, n - 1]
    rank = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, n]))
    blow = F["xl"] + (F["bh"] - F["xh"])                       # the bid low (xl is the exit side: a SELL's is the ask)
    up = bool(ctx["up"]); bars = []
    for a, b in zip(starts, ends):
        o, h, l, c = float(F["bo"][a]), float(F["bh"][a:b + 1].max()), float(blow[a:b + 1].min()), float(F["bc"][b])
        bars.append([round(o, 3), round(h, 3), round(l, 3), round(c, 3)] if up else [round(-o, 3), round(-l, 3), round(-h, 3), round(-c, 3)])
    def at(j):
        j = min(int(j), n - 1); return {"bar": int(rank[j]), "th": round(float(rank[j]) * 4 + (float(tm[j]) - float(kb[j]) * 240) / 60.0, 1), "clock_h": round(float(tm[j]) / 60.0, 1)}
    def peak(r):                                               # trader 2026-10-13: the highest R the trade reached before it ended -
        j = min(int(r["exit_j"]), n - 1)                       # the exit-side high (the price you could close at, as banks use);
        if r["kind"] == 1: return {}                           # a stop's own minute is left out (the kernel checks stops first, so
        k = j if r["kind"] == 0 else j + 1                     # that minute's high may have come after it); TP2 needs no peak
        if k <= 0: return {"peak_R": round(float(F["xo"][0]), 2), "peak": at(0)}
        jp = int(np.argmax(F["xh"][:k])); return {"peak_R": round(float(F["xh"][jp]), 2), "peak": at(jp)}
    raw = E.evaluate({"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": True, "levers": []}, F, ctx)
    out = {"raw_exit": dict(at(raw["exit_j"]), kind=KIND.get(raw["kind"], ""), **peak(raw)), "sell": not up}
    if plan_n:
        ev = E.evaluate(plan_n, F, ctx); out["plan_exit"] = dict(at(ev["exit_j"]), **peak(ev))
    last = max(out["raw_exit"]["bar"], out.get("plan_exit", {}).get("bar", 0)) + 1        # through the later of the two exits
    old = S["results"][hid].get("reveal") or []                # the 60-bar reveal also shows the path AFTER an early stop - kept;
    out["post_bars"] = old + bars[len(old):last] if last > len(old) else old    # longer when a trade ran past it (99.6% of bars agree)
    tp2 = ctx.get("tp2")
    if tp2 is not None and raw["kind"] != 1:
        bx, bb = float(F["xh"].max()), float(F["bh"].max())
        if bb >= tp2 - 0.25:
            out["raw_note"] = (f"Raw got within {tp2 - bx:.2f}R of TP2 without filling it: its best exit price was {bx:+.2f}R while the bid reached {bb:+.2f}R"
                               + (" (a SELL closes at the ask)" if not up else "") + f", then it {KIND.get(raw['kind'], 'ended')} after {out['raw_exit']['th']:g} trading hours.")
    return out


def _rtxt(v) -> str: return ("+" if v > 0.0005 else "−" if v < -0.0005 else "") + f"{abs(v):.2f}R"


def timeline(S, hid, cfg_plan, inverse: bool = False, post_bars: list | None = None) -> dict:
    """trader 2026-10-16 ("when i execute a trade i can step through it bar by bar or hour by hour to see how it plays out"): the
    Practice reveal as steps - one per trading hour on the frame (the same M1 frame the reveal's R comes from), then one per H4 bar
    for the revealed bars past the frame's end (the frame stops at the first -1R touch). Each hour tick carries its H4 bar so far
    (price R in the chart's orientation: the bid, a SELL negated - path_view's bars) and, per line (the plan, the raw signal), the
    state at the hour's close: open size, R if closed now (the exit-side close), banked R, the stops (chart levels + R if hit) and
    what happened in the hour. The state is plan_live.run_state (backtest_engine.kern line for line and resumable by segment -
    pipeline/plan_live_parity.py) run hour by hour on the one unchanged plan; its end must equal kern's result exactly, else no timeline."""
    from live import plan_live as PL
    with _LOCK: F, ctx = (S["frames_inv"] if inverse else S["frames"]).get(hid)
    n = int(F["n"]); tm = np.asarray(ctx["tm"][:n]); kb = tm // 240; hb = tm // 60; up = bool(ctx["up"])
    s4 = np.flatnonzero(np.r_[True, kb[1:] != kb[:-1]]); rank = np.repeat(np.arange(len(s4)), np.diff(np.r_[s4, n]))
    hs = np.flatnonzero(np.r_[True, hb[1:] != hb[:-1]]); he = np.r_[hs[1:], n]
    nbars = len(post_bars) if post_bars else int(rank[-1]) + 1
    blow = F["xl"] + (F["bh"] - F["xh"])                       # the bid low (path_view)
    chart = (lambda v: round(float(v), 3)) if up else (lambda v: round(-float(v), 3))
    ticks = []; capped = False
    for a, b in zip(hs, he):
        k = int(rank[a])
        if k >= nbars: capped = True; break
        a4 = int(s4[k]); o, h, l, c = float(F["bo"][a4]), float(F["bh"][a4:b].max()), float(blow[a4:b].min()), float(F["bc"][b - 1])
        ticks.append({"k": k, "bar": [round(o, 3), round(h, 3), round(l, 3), round(c, 3)] if up else [round(-o, 3), round(-l, 3), round(-h, 3), round(-c, 3)],
                      "th": int(k * 4 + (int(hb[b - 1]) - int(kb[b - 1]) * 4) + 1), "px": round(float(F["xc"][b - 1]), 3), "a": int(a), "b": int(b)})
    nh = len(ticks); last_k = ticks[-1]["k"] if ticks else -1
    if not capped and post_bars:                               # the frame ended first: its last bar completed, then whole bars
        for k in range(max(last_k, 0), nbars):
            if k > last_k or post_bars[k] != ticks[-1]["bar"]: ticks.append({"k": k, "bar": post_bars[k], "th": None, "px": None})

    def run(cfg_n, lbl):
        P = E.compile_cfg(cfg_n, ctx, True); init = None; res = None; steps = []; events = []
        adds = [c for c in cfg_n["levers"] if c["type"].startswith("add")]; cuts = [c for c in cfg_n["levers"] if c["type"].startswith("cut")]
        prev = {"nl": 1, "alive": [True], "lst": [-1.0], "ls": [P["F0"]], "bk": [False] * P["nbk"], "mv": [False] * P["nmv"],
                "ad": [False] * P["nad"], "held": [False] * P["nad"]}
        for t in range(nh):
            a, b = ticks[t]["a"], ticks[t]["b"]
            res, st = PL.run_state(F, ctx, P, a, b, init)
            nl = int(st["nl"]); alive = [bool(st["alive"][q]) for q in range(nl)]; lst = [float(st["lst"][q]) for q in range(nl)]
            ls = [float(st["ls"][q]) for q in range(nl)]; le = [float(st["le"][q]) for q in range(nl)]; locked = float(st["locked"])
            ev = []; moved = False; added = False
            for q in range(P["nbk"]):
                if st["bk_done"][q] and not prev["bk"][q] and P["bk_pct"][q] > 0:
                    ev.append(f"banked {P['bk_pct'][q] * 100:.0f}% of the open position at {_rtxt(P['bk_lvl'][q])}")
            for q in range(P["nmv"]):
                if st["mv_done"][q] and not prev["mv"][q]:
                    moved = True; ev.append(f"{_rtxt(P['mv_when'][q])} reached: stop moved to {_rtxt(ctx['be'] + P['mv_to'][q] - le[0])}")
            for q in range(prev["nl"], nl):
                a_ = int(st["src"][q]); added = True
                what = {"add_bar": "bar add", "add_reach": "add at a level", "add_tp1": "TP1 add"}.get(adds[a_]["type"], "add") if 0 <= a_ < len(adds) else "add"
                ev.append(f"{what}: {float(st['sz0'][q]) * 100:.0f}% more at {_rtxt(float(st['lp'][q]))}, its stop at {_rtxt(lst[q] - le[0])}")
            for q in range(P["nad"]):
                if st["ad_done"][q] and not prev["ad"][q] and not any(int(st["src"][x]) == q for x in range(nl)):
                    ev.append("an add was skipped (no room left under the size cap or the risk cap)")
                if st["ad_held"][q] and not prev["held"][q]: ev.append("an add is waiting: the average R is below its level")
            for q in range(min(prev["nl"], nl)):
                if prev["alive"][q] and not alive[q] and res is None:
                    ev.append(("the first entry" if q == 0 else f"an add ({ls[q] * 100:.0f}%)") + " was stopped out")
            pq = next((q for q in range(min(prev["nl"], nl)) if alive[q] and prev["alive"][q]), None)
            if pq is not None and abs(lst[pq] - prev["lst"][pq]) > 1e-9 and not moved:
                why = "trail" if (P["tr_on"] and bool(np.asarray(P["tr_chk"][a:b]).any())) else ("risk cap" if added else "stop")
                ev.append(f"{why}: stop {'raised' if lst[pq] > prev['lst'][pq] else 'lowered'} to {_rtxt(lst[pq] - le[0])}")
            on = [q for q in range(nl) if alive[q] and ls[q] > 1e-12]
            if res is None:
                xc = float(F["xc"][b - 1])
                steps.append({"open": round(sum(ls[q] for q in on), 4), "now": round(locked + sum(ls[q] * (xc - le[q]) for q in on), 4),
                              "banked": round(locked, 4), "worst": round(locked + sum(ls[q] * (lst[q] - le[q]) for q in on), 4),
                              "stops": sorted({chart(lst[q]) for q in on}), "stop_R": round(lst[on[0]] - le[0], 4) if on else None})
            else:
                R, kind, cut = float(res[0]), int(res[1]), int(res[6])
                if kind == 2 and 0 <= cut < len(cuts):
                    cc = cuts[cut]
                    why = (f"cut at {cc['at_h']:g} h: the average R{' over the last ' + format(cc['window_h'], 'g') + ' h' if cc.get('window_h') else ''} "
                           f"was below {_rtxt(cc['below'])}") if cc["type"] == "cut_avg" else f"cut at {cc['at_h']:g} h: {_rtxt(cc['reach'])} was never reached"
                else: why = KIND.get(kind, "ended")
                ev.append(f"closed - {why}: {_rtxt(R)}")
                steps.append({"open": 0.0, "now": round(R, 4), "banked": round(R, 4), "worst": round(R, 4), "stops": [], "stop_R": None})
            if ev: events.append({"t": t, "text": ev})
            if res is not None: break
            prev = {"nl": nl, "alive": alive, "lst": lst, "ls": ls, "bk": [bool(x) for x in st["bk_done"][:P["nbk"]]],
                    "mv": [bool(x) for x in st["mv_done"][:P["nmv"]]], "ad": [bool(x) for x in st["ad_done"][:P["nad"]]],
                    "held": [bool(x) for x in st["ad_held"][:P["nad"]]]}
            init = st
        ref = E.run(E.kern, F, ctx, P)
        if res is None or abs(float(res[0]) - float(ref[0])) > 1e-9 or int(res[1]) != int(ref[1]) or int(res[2]) != int(ref[2]):
            raise RuntimeError(f"{lbl}: the hourly replay ended at {res and tuple(res[:3])} but kern says {tuple(ref[:3])}")
        return {"steps": steps, "events": events, "exit_t": len(steps) - 1, "R": round(float(res[0]), 4), "kind": KIND.get(int(res[1]), "")}

    out = {"ticks": [{k: v for k, v in t.items() if k not in ("a", "b")} for t in ticks], "hourly": nh,
           "raw": run(E.validate({"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": True, "levers": []})[0], "raw")}
    if cfg_plan: out["plan"] = run(cfg_plan, "plan")
    return out


def decide(cfg: dict, sid: str, i: int, decision: str, plan: dict | None, loaded_preset: str = "", inverse: bool = False) -> dict:
    """record the decision server-side, THEN evaluate and reveal. loaded_preset = the preset the plan was loaded from when editing
    began (coach 2026-10-04: the "nearest preset" for grading a custom plan)."""
    if decision not in ("take", "skip"): raise ValueError("decision must be take or skip")
    if loaded_preset not in LOADABLE + ("",): raise ValueError("unknown loaded preset")
    S = store(cfg)
    with _LOCK:
        s = load_session(cfg, sid)
        if i != s["pos"]: raise ValueError("this card is not the current one (already decided, or out of order)")
        _gate(s)
        if s["mode"] == "test" and str(i) not in s["commits"]: raise ValueError("a Test card takes two steps: commit your first call first")
        hid = s["ids"][i]; R = S["results"][hid]; g = s["group"]
        plan_bad = None
        if decision == "take":
            if plan is None: raise ValueError("a take needs a plan")
            plan_n, warn = E.validate(plan)
        else:                                         # a skip is never blocked: an invalid plan is just not evaluated
            try: plan_n, warn = (E.validate(plan) if plan else (None, []))
            except Exception as e: plan_n, warn, plan_bad = None, [], str(e)[:200]
        if inverse and (s["mode"] != "practice" or not S.get("frames_inv")): raise ValueError("the inverse is a Practice option (and needs the inverse frames)")
        ev = _evaluate(S, hid, plan_n, inverse) if plan_n else None
        raw_inv = _evaluate(S, hid, {"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": True, "levers": []}, True)["R"] if S.get("frames_inv") and hid in S["frames_inv"].idx else None
        dflt = "raw"; mv = S["menu"]                  # trader 2026-10-08: the raw signal is the only comparison (Minimal removed)
        adv_avail = bool(advisor_visible(cfg, s, i) and advisor_latest(cfg, sid, hid))      # could the trader see a plan before his final?
        adv_cols = _adv_cols(cfg, S, s, i, hid, plan_n)
        t_shown = s["shown"].get(str(i), time.time())
        sp = R.get("stop_pips"); gr = R.get("gross") or {}
        cost = (lambda units: round(PIP_COST * units / sp, 4)) if sp else (lambda units: 0.0)       # +1 pip round trip, in R
        lp = loaded_preset or None
        if lp == "default": lp = "raw"                                                           # the preloaded plan = the raw signal
        rec = {"i": i, "hid": hid, "decision": decision, "plan": plan_n, "t_decided": _now(), "decision_s": round(time.time() - t_shown, 1),
               "R_choice": round(ev["R"], 4) if (decision == "take" and ev) else 0.0, "R_plan": round(ev["R"], 4) if ev else None,
               "R_raw": R["R"]["raw"], "R_default": R["R"][dflt], "default": dflt, "R_presets": {k: R["R"][k] for k in PRESET_KEYS},
               "banked_1R": R["banked_1R"], "hit_tp2": R["hit_tp2"], "kind": ev["kind"] if ev else None, "exit_h": ev["exit_h"] if ev else None,
               "first": s["commits"].get(str(i)), "plan_invalid": plan_bad, "n_levers": len(plan_n["levers"]) if plan_n else 0,
               "loaded_preset": lp, "R_loaded": R["R"].get(lp, "") if lp and lp not in ("advisor", "advisor_v1") else "",
               "R_choice_1pip": round((ev["R"] - cost(ev["gross"])) if (decision == "take" and ev) else 0.0, 4),
               "R_raw_1pip": round(R["R"]["raw"] - cost(gr.get("raw", 1.0)), 4), "R_default_1pip": round(R["R"][dflt] - cost(gr.get(dflt, 1.0)), 4),
               "adv_available": adv_avail, "inverse": bool(inverse), "R_raw_inverse": round(raw_inv, 4) if raw_inv is not None else None, **adv_cols}
        s["decisions"].append(rec); s["pos"] = i + 1
        save_session(cfg, s); write_report(cfg, s, S)
    if s["mode"] == "test":                           # Test: nothing is revealed until the session ends (coach)
        _top_up_advisor(cfg, s, S)
        return {"ok": True, "decision": decision, "recorded": True, "warnings": warn, "decided": len(s["decisions"]), "next": s["pos"] < s["n"]}
    labels = {p["key"]: p["label"] for p in mv["presets"]}
    pv = path_view(S, hid, plan_n, bool(inverse))
    try: tl = timeline(S, hid, plan_n, bool(inverse), pv["post_bars"])
    except Exception as e:                            # the step-through is extra: the reveal goes out without it
        tl = None
        with open(os.path.join(web_dir(cfg), "timeline_errors.log"), "a") as f: f.write(f"{_now()}|{sid}|{i}|{e!r}\n")
    return {"ok": True, "decision": decision, "warnings": warn, "R_choice": rec["R_choice"], "R_plan": rec["R_plan"], "R_raw": rec["R_raw"],
            "R_default": rec["R_default"], "default_key": dflt,
            "presets": [{"key": k, "label": labels[k], "R": round(_evaluate(S, hid, next(p["cfg"] for p in mv["presets"] if p["key"] == k), True)["R"], 4) if inverse else R["R"][k]}
                        for k in PRESET_KEYS],
            "inverse": bool(inverse), "R_raw_inverse": rec["R_raw_inverse"],
            "banked_1R": rec["banked_1R"], "hit_tp2": rec["hit_tp2"], "kind": KIND.get(rec["kind"], "") if ev else "", "exit_h": rec["exit_h"],
            "adds_fired": ev["adds"] if ev else 0, "advisor": adv_cols or None, "tally": tally(s), "next": s["pos"] < s["n"], **pv, "timeline": tl}


def commit_first(cfg: dict, sid: str, i: int, decision: str, plan: dict | None) -> dict:
    """the coach's two-step (used where the advisor slot is 'after_commit'): store the trader's first commit server-side (never
    passed to any advisor bundle), then the advisor's plan becomes visible; the final decision goes through decide()."""
    if decision not in ("take", "skip"): raise ValueError("decision must be take or skip")
    plan_bad = None
    if decision == "take":                             # review 2 #8: a take needs a valid plan
        if not plan: raise ValueError("a take needs a plan")
        plan_n = E.validate(plan)[0]
    else:                                              # review 2 #7: a skip is never blocked by an invalid plan (as decide())
        try: plan_n = E.validate(plan)[0] if plan else None
        except Exception as e: plan_n, plan_bad = None, str(e)[:200]
    with _LOCK:
        s = load_session(cfg, sid)
        if i != s["pos"]: raise ValueError("this card is not the current one")
        _gate(s)
        if str(i) in s["commits"]: raise ValueError("the first commit for this card is already recorded")
        s["commits"][str(i)] = {"decision": decision, "plan": plan_n, "t": _now(), "plan_invalid": plan_bad}; save_session(cfg, s)
    return {"ok": True, "committed": s["commits"][str(i)], **_advisor_payload(cfg, s, i, s["ids"][i])}


def mark_adv_loaded(cfg: dict, sid: str, i: int) -> dict:
    with _LOCK:
        s = load_session(cfg, sid)
        if i != s["pos"] or i >= s["n"]: raise ValueError("this card is not the current one")
        _gate(s)
        s["adv_loaded"][str(i)] = _now(); save_session(cfg, s)
    return {"ok": True}


def _adv_cols(cfg, S, s, i, hid, plan_final) -> dict:
    """the advisor's columns for one decided card, computed from its records (fresh at every report write, so a v1 that arrived after
    the trader's final still counts for the advisor-alone grade)."""
    adv = advisor_record(cfg, s["sid"], hid)
    a1 = next((v for v in adv["versions"] if v["valid"] and v.get("blind_v1")), None) if adv else None
    al = next((v for v in reversed(adv["versions"]) if v["valid"]), None) if adv else None
    if not (a1 or al): return {}
    R = S["results"][hid]
    return {"adv_v1_stack": json.dumps(a1["plan"], separators=(",", ":")) if a1 else "",
            "adv_nearest_preset": (a1 or al)["nearest_preset"], "R_adv_v1": round(_evaluate(S, hid, a1["plan"])["R"], 4) if a1 else "",
            "R_adv_nearest": R["R"].get((a1 or al)["nearest_preset"], ""), "R_adv_latest": round(_evaluate(S, hid, al["plan"])["R"], 4) if al else "",
            "adv_versions": len(adv["versions"]), "adv_loaded": bool(s["adv_loaded"].get(str(i))),
            "adv_final_unchanged": bool(al and plan_final and json.dumps(al["plan"], sort_keys=True) == json.dumps(plan_final, sort_keys=True))}


def _grade_inputs(cfg, S, s, d) -> dict:
    """per decided card: the trader alone (first commit; Practice has none -> the final), the advisor alone (blind v1 verdict + stack)
    and the combined final, at model cost and +1 pip, + the raw signal and the selection classes."""
    hid = d["hid"]; R = S["results"][hid]; sp = R.get("stop_pips"); gr = R.get("gross") or {}
    cost = (lambda u: PIP_COST * u / sp) if sp else (lambda u: 0.0)
    def plan_R(dec, plan):
        if dec != "take" or not plan: return 0.0, 0.0, None
        ev = _evaluate(S, hid, plan); return ev["R"], ev["R"] - cost(ev["gross"]), ev["R"]
    fst = d.get("first") or {"decision": d["decision"], "plan": d["plan"]}
    tR, tR1, tP = plan_R(fst.get("decision"), fst.get("plan"))
    out = {"R_first": round(tR, 4), "R_first_1pip": round(tR1, 4), "first_taken": fst.get("decision") == "take", "R_first_plan": tP}
    from live import backtest_advisor as BA
    st = BA.get_status(cfg, s["sid"], hid) if s["mode"] == "test" else {}
    adv = advisor_record(cfg, s["sid"], hid)
    b1 = next((v for v in adv["versions"] if v.get("blind_v1")), None) if adv else None       # the blind answer (valid or not)
    vd = st.get("verdict")
    if vd in ("TAKE", "SKIP") and b1 is not None:
        if b1.get("valid"):
            aR, aR1, aP = plan_R("take" if vd == "TAKE" else "skip", b1["plan"]); src = "its own plan"
        else:                                          # review 2 #6: its stack was rejected - the verdict still counts, as the raw signal
            mR = R["R"]["raw"]; take = vd == "TAKE"                  # (2026-10-08: was Minimal - removed, shaped off the data)
            aR, aR1, aP = (mR, mR - cost(gr.get("raw", 1.0)), mR) if take else (0.0, 0.0, None); src = "the raw signal (its plan was rejected)"
        out.update(adv_graded=True, R_adv_alone=round(aR, 4), R_adv_alone_1pip=round(aR1, 4), adv_taken=vd == "TAKE", R_adv_plan=aP, adv_plan_graded=src)
    else: out.update(adv_graded=False, R_adv_alone="", R_adv_alone_1pip="", adv_taken="", R_adv_plan=None, adv_plan_graded="")
    out.update(adv_verdict=vd or "", adv_confidence=st.get("confidence", ""), adv_state=st.get("state", "") if s["mode"] == "test" else "",
               adv_role=(st.get("role") or ("blind-backtest-advisor v2 (2026-10-05)" if st.get("state") == "done" else "")) if s["mode"] == "test" else "",
               reached_1R=bool(R["banked_1R"]), went_to_stop=bool((not R["banked_1R"]) and R["R"]["raw"] <= -0.9))
    return out


def _grade(rows: list, actor: str) -> dict:
    """one actor's grade over the session's cards. actor: trader / advisor / combined."""
    if actor == "trader": rs = [(r["first_taken"], r["R_first"], r["R_first_1pip"], r["R_first_plan"], r) for r in rows]
    elif actor == "advisor": rs = [(r["adv_taken"], r["R_adv_alone"], r["R_adv_alone_1pip"], r["R_adv_plan"], r) for r in rows if r["adv_graded"]]
    else: rs = [(r["final_taken"], r["R_choice"], r["R_choice_1pip"], r["R_final_plan"], r) for r in rows]
    n = len(rs)
    if not n: return {"n": 0}
    tot = sum(x[1] for x in rs); tot1 = sum(x[2] for x in rs)
    rw = sum(x[4]["R_raw"] for x in rs); rw1 = sum(x[4]["R_raw_1pip"] for x in rs)
    a = [x for x in rs if x[4]["reached_1R"]]; b = [x for x in rs if x[4]["went_to_stop"]]
    ta, tb = sum(1 for x in a if x[0]), sum(1 for x in b if x[0])
    pa, pb = (ta / len(a) if a else None), (tb / len(b) if b else None)
    z = None
    if a and b:
        p = (ta + tb) / (len(a) + len(b)); se = (p * (1 - p) * (1 / len(a) + 1 / len(b))) ** 0.5
        z = round((pa - pb) / se, 2) if se > 0 else None
    dd = [x[3] - x[4]["R_raw"] for x in rs if x[0] and x[3] is not None]          # the plan minus the raw signal on the trades taken
    t = None
    if len(dd) >= 2:
        m = sum(dd) / len(dd); sd = (sum((v - m) ** 2 for v in dd) / (len(dd) - 1)) ** 0.5
        t = round(m / (sd / len(dd) ** 0.5), 2) if sd > 0 else None
    return {"n": n, "taken": sum(1 for x in rs if x[0]), "take_rate": round(sum(1 for x in rs if x[0]) / n, 3),
            "total_R": round(tot, 3), "vs_raw": round(tot - rw, 3),
            "at_1pip": {"total_R": round(tot1, 3), "vs_raw": round(tot1 - rw1, 3)},
            "selection": {"take_rate_reached_1R": round(pa, 3) if pa is not None else None, "n_reached_1R": len(a),
                          "take_rate_went_to_stop": round(pb, 3) if pb is not None else None, "n_went_to_stop": len(b),
                          "spread_points": round(100 * (pa - pb), 1) if (pa is not None and pb is not None) else None, "z": z},
            "settings_on_taken": {"n": len(dd), "mean_vs_raw": round(sum(dd) / len(dd), 4) if dd else None, "t": t}}


# ------------------------------------------------------------------------------------------------------------- report
COLS = ["hid", "group", "detector", "direction", "mode", "seed", "menu_version", "decision", "decision_time_s", "chosen_config", "n_levers",
        "loaded_preset", "R_loaded_preset", "R_choice", "R_plan_if_taken", "R_raw", "R_default", "default"] + [f"R_{k}" for k in PRESET_KEYS] + \
       ["banked_1R", "hit_tp2", "first_decision", "first_config", "adv_v1_stack", "adv_nearest_preset", "R_adv_v1", "R_adv_nearest",
        "R_adv_latest", "adv_versions", "adv_loaded", "adv_final_unchanged", "skip_plan_invalid", "R_choice_1pip", "R_raw_1pip", "R_default_1pip",
        "R_first", "R_first_1pip", "adv_verdict", "adv_confidence", "R_adv_alone", "R_adv_alone_1pip", "adv_state", "adv_available_at_final",
        "reached_1R", "went_to_stop", "adv_plan_graded", "adv_role"]
REPORT_HEADER = (f"# Backtest report - frames capped at {FRAME_CAP_DAYS} trading days after the entry (a trade still open then is closed at the "
                 f"last close); R at model cost (a lower bound - read the differences, not the levels); *_1pip columns = after +1 pip round "
                 f"trip (gross units x 1 pip / stop pips); comparison = the raw signal (R_default = raw since 2026-10-08; Minimal removed)")


def write_report(cfg: dict, s: dict, S: dict) -> None:
    buf = io.StringIO(); buf.write(REPORT_HEADER + "\n"); w = csv.writer(buf); w.writerow(COLS); rows = []
    for d in s["decisions"]:
        d.update(_adv_cols(cfg, S, s, d["i"], d["hid"], d["plan"]))       # fresh: a late v1 still counts for the advisor-alone grade
        gi = _grade_inputs(cfg, S, s, d)
        rows.append({**gi, "blk": d["i"] // (s.get("block") or 10 ** 9), "final_taken": d["decision"] == "take", "R_choice": d["R_choice"], "R_choice_1pip": d.get("R_choice_1pip", d["R_choice"]),
                     "R_final_plan": d["R_plan"] if d["decision"] == "take" else None, "R_raw": d["R_raw"], "R_raw_1pip": d.get("R_raw_1pip", d["R_raw"])})
        c = S["cards"][d["hid"]]; fst = d.get("first") or {}
        w.writerow([d["hid"], c["group"], c["detector"], c["direction"], s["mode"], s["seed"], s["menu"], d["decision"], d["decision_s"],
                    json.dumps(d["plan"], separators=(",", ":")) if d["plan"] else "", d.get("n_levers", ""), d.get("loaded_preset") or "",
                    d.get("R_loaded", ""), d["R_choice"], d["R_plan"] if d["R_plan"] is not None else "",
                    d["R_raw"], d["R_default"], d["default"]] + [d["R_presets"][k] for k in PRESET_KEYS] +
                   [d["banked_1R"], d["hit_tp2"], fst.get("decision", ""), json.dumps(fst.get("plan"), separators=(",", ":")) if fst.get("plan") else ""] +
                   [d.get(k, "") for k in ("adv_v1_stack", "adv_nearest_preset", "R_adv_v1", "R_adv_nearest", "R_adv_latest", "adv_versions", "adv_loaded", "adv_final_unchanged")]
                   + [d.get("plan_invalid") or "", d.get("R_choice_1pip", ""), d.get("R_raw_1pip", ""), d.get("R_default_1pip", "")]
                   + [gi["R_first"], gi["R_first_1pip"], gi["adv_verdict"], gi["adv_confidence"], gi["R_adv_alone"], gi["R_adv_alone_1pip"],
                      gi["adv_state"], d.get("adv_available", ""), gi["reached_1R"], gi["went_to_stop"],
                      gi.get("adv_plan_graded", ""), gi.get("adv_role", "")])
    rd = web_dir(cfg, "reports")
    tmp = os.path.join(rd, f"{s['sid']}.csv.tmp"); open(tmp, "w", newline="").write(buf.getvalue()); os.replace(tmp, os.path.join(rd, f"{s['sid']}.csv"))
    t = tally(s); n = max(1, t["decided"])
    summ = {"sid": s["sid"], "group": s["group"], "mode": s["mode"], "seed": s["seed"], "menu_version": s["menu"], "created": s["created"],
            "signals": s["n"], **t, "R_choice_per_signal": round(t["R_choice"] / n, 4), "R_raw_per_signal": round(t["R_raw"] / n, 4),
            "vs_raw": round(t["R_choice"] - t["R_raw"], 3), "vs_default": round(t["R_choice"] - t["R_default"], 3),
            "at_1pip": {"R_choice": t["R_choice_1pip"], "R_raw": t["R_raw_1pip"], "R_default": t["R_default_1pip"],
                        "vs_raw": round(t["R_choice_1pip"] - t["R_raw_1pip"], 3), "vs_default": round(t["R_choice_1pip"] - t["R_default_1pip"], 3),
                        "R_choice_per_signal": round(t["R_choice_1pip"] / n, 4), "R_raw_per_signal": round(t["R_raw_1pip"] / n, 4)},
            "frame_cap_days": FRAME_CAP_DAYS, "comparator": "raw", "menu_frozen_at": s.get("menu_frozen_at"),
            "advisor_only": _advisor_only(cfg, S, s),
            "grading": {"trader_alone": _grade(rows, "trader"), "advisor_alone": _grade(rows, "advisor"), "combined": _grade(rows, "combined"),
                        "by_block": [{"block": k + 1, "cards": f"{k * s['block'] + 1}-{min(s['n'], (k + 1) * s['block'])}",
                                      "decided": sum(1 for r in rows if r["blk"] == k),
                                      "adv_roles": sorted({r["adv_role"][23:25] for r in rows if r["blk"] == k and r.get("adv_role")}),
                                      **{a: _grade([r for r in rows if r["blk"] == k], x) for a, x in
                                         (("trader_alone", "trader"), ("advisor_alone", "advisor"), ("combined", "combined"))}}
                                     for k in range(-(-s["n"] // s["block"]) if s.get("block") else 0)
                                     if any(r["blk"] == k for r in rows)],
                        "definitions": "trader alone = the first commit (Practice: the final); advisor alone = its blind v1 verdict + v1 stack (a TAKE "
                                       "whose stack was rejected is graded as the raw signal; cards without a blind verdict are left out of its "
                                       "grade); combined = the final. vs_raw = against every signal taken raw (the only comparison - trader "
                                       "2026-10-08: Minimal removed, shaped off the data); selection = take rate on signals that reached +1R minus "
                                       "take rate on signals that went to the -1R stop without reaching +1R (two-proportion z); settings_on_taken = "
                                       "R of the plan minus the raw signal on the signals the actor took (paired t). Model cost; at_1pip = after a +1 pip "
                                       "round trip."},
            "note": "comparison = the raw signal (every signal taken raw); model cost - read the difference, not the level; at_1pip = after +1 pip "
                    f"round trip; frames capped at {FRAME_CAP_DAYS} trading days"}
    json.dump(summ, open(os.path.join(rd, f"{s['sid']}.summary.json"), "w"), indent=1)
    save_session(cfg, s)                                # keeps the refreshed advisor columns on the decisions


def load_summary(cfg: dict, sid: str) -> dict | None:
    p = os.path.join(web_dir(cfg, "reports"), f"{sid}.summary.json")
    return json.load(open(p)) if os.path.exists(p) else None


def report_csv(cfg: dict, sid: str) -> bytes:
    if not _safe_sid(sid): raise ValueError("bad session id")
    try: s = load_session(cfg, sid)
    except ValueError: s = None
    if s and s["mode"] == "test" and not trader_done(s):             # Test: no outcome leaves the server before the trader's play ends
        return (REPORT_HEADER + "\n# Test session in progress - the report is available once its last card is decided\n" + ",".join(COLS) + "\n").encode()
    if s and s["mode"] == "test":                       # refresh once at the end (a late advisor v1 still counts)
        S = store(cfg)                                  # review 2 #1: never call store() while holding the lock
        with _LOCK: write_report(cfg, s, S)
    p = os.path.join(web_dir(cfg, "reports"), f"{sid}.csv")
    return open(p, "rb").read() if os.path.exists(p) else (REPORT_HEADER + "\n" + ",".join(COLS) + "\n").encode()


# ------------------------------------------------------------------------------------------------------------- advisor hooks
FENCE = re.compile(r"```levers\s*(\{.*?\})\s*```", re.S)


def parse_recommendation(text_or_obj, menu_keys=PRESET_KEYS + ("raw",)) -> dict:
    """advisor text (with a ```levers {...}``` block) or an already-parsed object -> {nearest_preset, rationale, plan}. Raises
    ValueError with the reason when it is not a valid advisor-levers/v1 recommendation."""
    if isinstance(text_or_obj, str):
        m = FENCE.findall(text_or_obj)
        if not m: raise ValueError("no ```levers {...}``` block in the advisor's reply")
        try: obj = json.loads(m[-1])
        except ValueError as e: raise ValueError(f"the levers block is not valid JSON: {e}")
    else: obj = text_or_obj
    if not isinstance(obj, dict) or obj.get("schema") != "advisor-levers/v1": raise ValueError("schema must be advisor-levers/v1")
    np_ = obj.get("nearest_preset")
    if np_ not in menu_keys: raise ValueError(f"nearest_preset must be one of {', '.join(menu_keys)}")
    rat = " ".join(str(obj.get("rationale") or "").split())[:600]     # 2026-10-08: collapsed to one line, cut at 600 (v3 asks for the reason
    if not rat: raise ValueError("rationale is missing")                #   behind each change; length alone no longer rejects a plan)
    if obj.get("plan") is not None and not isinstance(obj.get("plan"), dict): raise ValueError("plan must be an object")
    plan = dict(obj.get("plan") or {}); plan.setdefault("schema", E.SCHEMA)
    plan_n, warn = E.validate(plan)                     # only levers the engine evaluates, within the cap (raises otherwise)
    return {"nearest_preset": np_, "rationale": rat, "plan": plan_n, "warnings": warn}


def _ap(cfg, sid, hid):
    if not _safe_sid(sid) or not re.fullmatch(r"[0-9a-f]{16}", hid or ""): raise ValueError("bad id")
    return os.path.join(web_dir(cfg, "advisor", sid), f"{hid}.json")


def advisor_record(cfg, sid, hid) -> dict | None:
    p = _ap(cfg, sid, hid)
    return json.load(open(p)) if os.path.exists(p) else None


def add_recommendation(cfg: dict, sid: str, hid: str, text_or_obj, source: str = "conversation", blind: bool = False) -> dict:
    """append a version (valid or rejected-with-reason). v1 from the blind batch carries blind_v1: true."""
    p = _ap(cfg, sid, hid)
    with _LOCK:
        rec = json.load(open(p)) if os.path.exists(p) else {"sid": sid, "hid": hid, "versions": []}
        v = len(rec["versions"]) + 1
        first_blind = blind and source == "blind batch" and not any(x.get("blind_v1") for x in rec["versions"])   # review 2 #4
        ent = {"v": v, "at": _now(), "source": source, "blind_v1": bool(first_blind or (blind and source == "dev" and v == 1))}
        try:
            ent.update(parse_recommendation(text_or_obj), valid=True, reason=None)
        except Exception as e:                        # any malformed block lands in the rejects log, never propagates
            ent.update(valid=False, reason=str(e)[:300] or type(e).__name__)
            with open(os.path.join(web_dir(cfg), "advisor_rejects.log"), "a") as f: f.write(f"{_now()}|{sid}|{hid}|v{v}|{source}|{e}\n")
        ent["note"] = "blind (from the card bundle)" if ent["blind_v1"] else (f"v{v} · updated after your question" if v > 1 else "v1")
        rec["versions"].append(ent)
        tmp = p + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, p)
    return ent


def advisor_latest(cfg, sid, hid) -> dict | None:
    rec = advisor_record(cfg, sid, hid)
    if not rec: return None
    vs = [v for v in rec["versions"] if v["valid"]]
    if not vs: return None
    last = vs[-1]
    return {"v": last["v"], "note": last["note"], "nearest_preset": last["nearest_preset"], "rationale": last["rationale"], "plan": last["plan"],
            "versions": [{"v": v["v"], "at": v["at"], "note": v["note"], "valid": v["valid"]} for v in rec["versions"]]}


def dev_inject(cfg: dict, sid: str, text: str, blind: bool) -> dict:
    """DEV ONLY (CFG.backtest.dev true): add a sample recommendation for the session's current card."""
    if not settings(cfg)["dev"]: raise ValueError("dev endpoints are off")
    s = load_session(cfg, sid)
    if s["pos"] >= s["n"]: raise ValueError("session finished")
    return add_recommendation(cfg, sid, s["ids"][s["pos"]], text, source="dev", blind=blind)


def init_payload(cfg: dict) -> dict:
    S = store(cfg)
    return {"menu": S["menu"], "groups": [{"key": g, "name": S["info"]["groups"][g]["name"], "practice": PRACTICE_SIZE, "test": TEST_SIZES[g],
                                           "block": BLOCK} for g in SIZES],
            "sessions": list_sessions(cfg), "advisor_slot": settings(cfg)["advisor_slot"], "dev": bool(settings(cfg)["dev"]),
            "comparison": "raw", "menu_frozen": (S["menu"].get("frozen") or {}).get("at"),
            "test_left": {g: _test_left(cfg, S, g) for g in SIZES}}


def _test_left(cfg, S, g) -> int:
    """Test signals of the group not yet drawn (served cards only, ledger excluded; the research lock is applied at draw time)."""
    used = set(load_ledger(cfg)["ids"])
    return sum(1 for h, t in S["split"]["trades"].items() if t["group"] == g and t["part"] == "test" and h in S["eligible"] and h not in used)


def page_body(static_v: str) -> str:
    v = static_v
    return (f'<link rel="stylesheet" href="/static/vendor/shoelace/themes/dark.css?v={v}"><link rel="stylesheet" href="/static/backtest.css?v={v}">'
            f'<script type="module" src="/static/vendor/shoelace/shoelace-autoloader.js?v={v}"></script>'
            f'<script src="/static/lw.js?v={v}"></script><script src="/static/hybrid_chart.js?v={v}"></script>'
            f'<script src="/static/vendor/sortable/Sortable.min.js?v={v}"></script><script src="/static/backtest.js?v={v}"></script>'
            f'<script defer src="/static/vendor/alpine/alpine.min.js?v={v}"></script>'
            '<div id="bt-app" class="sl-theme-dark" x-data="btApp()" x-cloak>' + body_html() + '</div>')


def body_html() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "backtest.html")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else "<p>backtest.html missing</p>"
