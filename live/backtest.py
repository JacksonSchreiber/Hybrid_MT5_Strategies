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
TEST MODE (phase 3, 2026-10-05): a draw from the group's TEST partition; every Test id ever drawn goes into the ledger
(<root>/web/backtest/test_ledger.json) at once and is never drawn again ("one pass per signal ever"; a session cannot be restarted,
only resumed). Nothing is revealed during a Test session - no per-card result, no tally, no CSV rows, the session list shows no R -
until its last card is decided (except the aggregate grade at each block checkpoint); then the full report. Every Test card takes TWO steps (coach): the trader commits a plan + take/skip
(/bt/commit, immutable), only then the blind advisor's verdict + its v1 lever stack appear ("Advisor recommended", with its nearest
preset; follow-up questions allowed - later versions count toward the combined result only), and the trader gives his final
(/bt/decide). The advisor batch (live/backtest_advisor.py, run by the advisor runner) is queued when the session starts: one blind
consult per served card, in session order. Test needs the preset menu FROZEN (menu_v1.json "frozen").
GRADING (the session summary): the trader alone (his first commit), the advisor alone (its blind v1 verdict + stack) and both together
(the final), each against Minimal (every signal taken with Minimal - the grading comparator) and the raw signal, at model cost and
+1 pip; the selection inputs (take rate on signals that reached +1R minus take rate on signals that went to the stop without reaching
it, with a two-proportion z) and the settings input (R of the chosen plan minus Minimal on the taken signals, paired t).

ADVISOR RECOMMENDATION (schema "advisor-levers/v1" - what the advisor emits, as a fenced block in its reply):
    ```levers
    {"schema": "advisor-levers/v1", "nearest_preset": "ruthless", "rationale": "one plain-words line",
     "plan": {"entry": {"pct": 100}, "tp2": true, "trim_to_cap": false, "levers": [{"type": "bank", "pct": 25, "at": 1.0, "tp1": true}, ...]}}
    ```
  nearest_preset: one of the menu-v1 preset keys (minimal / ruthless / slow / banker / pyramid); plan: a levers/v1 plan (see
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
PRESET_KEYS = ("minimal", "ruthless", "slow", "banker", "pyramid")
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
            _STORE.clear(); _STORE.update(key=key, split=split, cards=cards, results=res["trades"], menu=json.load(open(os.path.join(d, "menu_v1.json"))),
                                          info=json.load(open(os.path.join(d, "info_v1.json"))), frames=frames, eligible=ok)
    return _STORE


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
        hide = s["mode"] == "test" and len(s["decisions"]) < s["n"]          # Test: nothing revealed until the last card is decided
        out.append({"sid": s["sid"], "group": s["group"], "mode": s["mode"], "created": s["created"], "n": s["n"], "done": len(s["decisions"]), "block": s.get("block"),
                    "tally": None if hide else tally(s)})
    return sorted(out, key=lambda x: x["created"], reverse=True)[:50]


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
        if i >= s["n"]:
            out = {"done": True, "tally": tally(s), "n": s["n"], "sid": sid, "mode": s["mode"]}
            if s["mode"] == "test":                 # refreshed: an advisor v1 that arrived after the trader's final still counts
                write_report(cfg, s, S); out["summary"] = load_summary(cfg, sid)
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
    if b and i and i % b == 0 and i < s["n"] and str(i) not in s.get("block_ok", {}): raise ValueError("start the next block first")


def next_block(cfg: dict, sid: str) -> dict:
    """the trader starts the next block of a Test session (only at a checkpoint)."""
    with _LOCK:
        s = load_session(cfg, sid); i = s["pos"]; b = s.get("block")
        if s["mode"] != "test" or not b or not i or i % b or i >= s["n"]: raise ValueError("not at a block checkpoint")
        if str(i) in s.get("block_ok", {}): raise ValueError("this block has already started")
        s.setdefault("block_ok", {})[str(i)] = _now(); save_session(cfg, s)
    return {"ok": True, "sid": sid, "i": i, "block": i // b + 1}


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


def _evaluate(S, hid, cfg_plan):
    """one plan on one trade - cached by (data build, hid, plan) (review 2 #10: write_report re-reads every row at each decide)."""
    k = (S.get("key"), hid, json.dumps(cfg_plan, sort_keys=True, separators=(",", ":")))
    r = _EVAL.get(k)
    if r is None:
        F, ctx = S["frames"].get(hid)
        r = E.evaluate(cfg_plan, F, ctx)
        tm = ctx["tm"]; j = min(r["exit_j"], len(tm) - 1)
        r["exit_h"] = round(float(tm[j]) / 60.0, 1)
        if len(_EVAL) >= _EVAL_MAX: _EVAL.clear()
        _EVAL[k] = r
    return dict(r)


def decide(cfg: dict, sid: str, i: int, decision: str, plan: dict | None, loaded_preset: str = "") -> dict:
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
        ev = _evaluate(S, hid, plan_n) if plan_n else None
        dflt = R["default"]; mv = S["menu"]
        adv_avail = bool(advisor_visible(cfg, s, i) and advisor_latest(cfg, sid, hid))      # could the trader see a plan before his final?
        adv_cols = _adv_cols(cfg, S, s, i, hid, plan_n)
        t_shown = s["shown"].get(str(i), time.time())
        sp = R.get("stop_pips"); gr = R.get("gross") or {}
        cost = (lambda units: round(PIP_COST * units / sp, 4)) if sp else (lambda units: 0.0)       # +1 pip round trip, in R
        lp = loaded_preset or None
        if lp == "default": lp = R.get("preload") or R["default"]                               # the preloaded plan's own key
        rec = {"i": i, "hid": hid, "decision": decision, "plan": plan_n, "t_decided": _now(), "decision_s": round(time.time() - t_shown, 1),
               "R_choice": round(ev["R"], 4) if (decision == "take" and ev) else 0.0, "R_plan": round(ev["R"], 4) if ev else None,
               "R_raw": R["R"]["raw"], "R_default": R["R"][dflt], "default": dflt, "R_presets": {k: R["R"][k] for k in PRESET_KEYS},
               "banked_1R": R["banked_1R"], "hit_tp2": R["hit_tp2"], "kind": ev["kind"] if ev else None, "exit_h": ev["exit_h"] if ev else None,
               "first": s["commits"].get(str(i)), "plan_invalid": plan_bad, "n_levers": len(plan_n["levers"]) if plan_n else 0,
               "loaded_preset": lp, "R_loaded": R["R"].get(lp, "") if lp and lp not in ("advisor", "advisor_v1") else "",
               "R_choice_1pip": round((ev["R"] - cost(ev["gross"])) if (decision == "take" and ev) else 0.0, 4),
               "R_raw_1pip": round(R["R"]["raw"] - cost(gr.get("raw", 1.0)), 4), "R_default_1pip": round(R["R"][dflt] - cost(gr.get(dflt, 1.0)), 4),
               "R_minimal": R["R"]["minimal"], "adv_available": adv_avail, **adv_cols}
        s["decisions"].append(rec); s["pos"] = i + 1
        save_session(cfg, s); write_report(cfg, s, S)
    if s["mode"] == "test":                           # Test: nothing is revealed until the session ends (coach)
        _top_up_advisor(cfg, s, S)
        return {"ok": True, "decision": decision, "recorded": True, "warnings": warn, "decided": len(s["decisions"]), "next": s["pos"] < s["n"]}
    labels = {p["key"]: p["label"] for p in mv["presets"]}
    return {"ok": True, "decision": decision, "warnings": warn, "R_choice": rec["R_choice"], "R_plan": rec["R_plan"], "R_raw": rec["R_raw"],
            "R_default": rec["R_default"], "default_label": labels.get(dflt, dflt) + " (grading comparator)", "default_key": dflt,
            "preload_label": mv["defaults"][g]["label"], "R_preload": R["R"].get(R.get("preload") or dflt),
            "presets": [{"key": k, "label": labels[k], "R": R["R"][k]} for k in PRESET_KEYS],
            "banked_1R": rec["banked_1R"], "hit_tp2": rec["hit_tp2"], "kind": KIND.get(rec["kind"], "") if ev else "", "exit_h": rec["exit_h"],
            "adds_fired": ev["adds"] if ev else 0, "post_bars": R["reveal"], "advisor": adv_cols or None, "tally": tally(s), "next": s["pos"] < s["n"]}


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
    and the combined final, at model cost and +1 pip, + Minimal / raw and the selection classes."""
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
        else:                                          # review 2 #6: its stack was rejected - the verdict still counts, with Minimal
            mR = R["R"]["minimal"]; take = vd == "TAKE"
            aR, aR1, aP = (mR, mR - cost(gr.get("minimal", 1.0)), mR) if take else (0.0, 0.0, None); src = "Minimal (its plan was rejected)"
        out.update(adv_graded=True, R_adv_alone=round(aR, 4), R_adv_alone_1pip=round(aR1, 4), adv_taken=vd == "TAKE", R_adv_plan=aP, adv_plan_graded=src)
    else: out.update(adv_graded=False, R_adv_alone="", R_adv_alone_1pip="", adv_taken="", R_adv_plan=None, adv_plan_graded="")
    out.update(adv_verdict=vd or "", adv_confidence=st.get("confidence", ""), adv_state=st.get("state", "") if s["mode"] == "test" else "",
               adv_role=(st.get("role") or ("blind-backtest-advisor v2 (2026-10-05)" if st.get("state") == "done" else "")) if s["mode"] == "test" else "",
               R_minimal=R["R"]["minimal"], R_minimal_1pip=round(R["R"]["minimal"] - cost(gr.get("minimal", 1.0)), 4),
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
    mn = sum(x[4]["R_minimal"] for x in rs); mn1 = sum(x[4]["R_minimal_1pip"] for x in rs)
    rw = sum(x[4]["R_raw"] for x in rs); rw1 = sum(x[4]["R_raw_1pip"] for x in rs)
    a = [x for x in rs if x[4]["reached_1R"]]; b = [x for x in rs if x[4]["went_to_stop"]]
    ta, tb = sum(1 for x in a if x[0]), sum(1 for x in b if x[0])
    pa, pb = (ta / len(a) if a else None), (tb / len(b) if b else None)
    z = None
    if a and b:
        p = (ta + tb) / (len(a) + len(b)); se = (p * (1 - p) * (1 / len(a) + 1 / len(b))) ** 0.5
        z = round((pa - pb) / se, 2) if se > 0 else None
    dd = [x[3] - x[4]["R_minimal"] for x in rs if x[0] and x[3] is not None]
    t = None
    if len(dd) >= 2:
        m = sum(dd) / len(dd); sd = (sum((v - m) ** 2 for v in dd) / (len(dd) - 1)) ** 0.5
        t = round(m / (sd / len(dd) ** 0.5), 2) if sd > 0 else None
    return {"n": n, "taken": sum(1 for x in rs if x[0]), "take_rate": round(sum(1 for x in rs if x[0]) / n, 3),
            "total_R": round(tot, 3), "vs_minimal": round(tot - mn, 3), "vs_raw": round(tot - rw, 3),
            "at_1pip": {"total_R": round(tot1, 3), "vs_minimal": round(tot1 - mn1, 3), "vs_raw": round(tot1 - rw1, 3)},
            "selection": {"take_rate_reached_1R": round(pa, 3) if pa is not None else None, "n_reached_1R": len(a),
                          "take_rate_went_to_stop": round(pb, 3) if pb is not None else None, "n_went_to_stop": len(b),
                          "spread_points": round(100 * (pa - pb), 1) if (pa is not None and pb is not None) else None, "z": z},
            "settings_on_taken": {"n": len(dd), "mean_vs_minimal": round(sum(dd) / len(dd), 4) if dd else None, "t": t}}


# ------------------------------------------------------------------------------------------------------------- report
COLS = ["hid", "group", "detector", "direction", "mode", "seed", "menu_version", "decision", "decision_time_s", "chosen_config", "n_levers",
        "loaded_preset", "R_loaded_preset", "R_choice", "R_plan_if_taken", "R_raw", "R_default", "default"] + [f"R_{k}" for k in PRESET_KEYS] + \
       ["banked_1R", "hit_tp2", "first_decision", "first_config", "adv_v1_stack", "adv_nearest_preset", "R_adv_v1", "R_adv_nearest",
        "R_adv_latest", "adv_versions", "adv_loaded", "adv_final_unchanged", "skip_plan_invalid", "R_choice_1pip", "R_raw_1pip", "R_default_1pip",
        "R_first", "R_first_1pip", "adv_verdict", "adv_confidence", "R_adv_alone", "R_adv_alone_1pip", "adv_state", "adv_available_at_final",
        "R_minimal", "R_minimal_1pip", "reached_1R", "went_to_stop", "adv_plan_graded", "adv_role"]
REPORT_HEADER = (f"# Backtest report - frames capped at {FRAME_CAP_DAYS} trading days after the entry (a trade still open then is closed at the "
                 f"last close); R at model cost (a lower bound - read the differences, not the levels); *_1pip columns = after +1 pip round "
                 f"trip (gross units x 1 pip / stop pips); comparison = the raw signal; grading comparator (R_default) = Minimal")


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
                      gi["adv_state"], d.get("adv_available", ""), gi["R_minimal"], gi["R_minimal_1pip"], gi["reached_1R"], gi["went_to_stop"],
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
            "frame_cap_days": FRAME_CAP_DAYS, "comparator": "minimal", "menu_frozen_at": s.get("menu_frozen_at"),
            "grading": {"trader_alone": _grade(rows, "trader"), "advisor_alone": _grade(rows, "advisor"), "combined": _grade(rows, "combined"),
                        "by_block": [{"block": k + 1, "cards": f"{k * s['block'] + 1}-{min(s['n'], (k + 1) * s['block'])}",
                                      "decided": sum(1 for r in rows if r["blk"] == k),
                                      "adv_roles": sorted({r["adv_role"][23:25] for r in rows if r["blk"] == k and r.get("adv_role")}),
                                      **{a: _grade([r for r in rows if r["blk"] == k], x) for a, x in
                                         (("trader_alone", "trader"), ("advisor_alone", "advisor"), ("combined", "combined"))}}
                                     for k in range(-(-s["n"] // s["block"]) if s.get("block") else 0)
                                     if any(r["blk"] == k for r in rows)],
                        "definitions": "trader alone = the first commit (Practice: the final); advisor alone = its blind v1 verdict + v1 stack (a TAKE "
                                       "whose stack was rejected is graded with Minimal; cards without a blind verdict are left out of its "
                                       "grade); combined = the final. total_R vs_minimal = against every "
                                       "signal taken with Minimal (the grading comparator); selection = take rate on signals that reached +1R minus "
                                       "take rate on signals that went to the -1R stop without reaching +1R (two-proportion z); settings_on_taken = "
                                       "R of the plan minus Minimal on the signals the actor took (paired t). Model cost; at_1pip = after a +1 pip "
                                       "round trip."},
            "note": "comparison = the raw signal (every signal taken raw); grading comparator (R_default) = Minimal; model cost - read the "
                    f"difference, not the level; at_1pip = after +1 pip round trip; frames capped at {FRAME_CAP_DAYS} trading days"}
    json.dump(summ, open(os.path.join(rd, f"{s['sid']}.summary.json"), "w"), indent=1)
    save_session(cfg, s)                                # keeps the refreshed advisor columns on the decisions


def load_summary(cfg: dict, sid: str) -> dict | None:
    p = os.path.join(web_dir(cfg, "reports"), f"{sid}.summary.json")
    return json.load(open(p)) if os.path.exists(p) else None


def report_csv(cfg: dict, sid: str) -> bytes:
    if not _safe_sid(sid): raise ValueError("bad session id")
    try: s = load_session(cfg, sid)
    except ValueError: s = None
    if s and s["mode"] == "test" and len(s["decisions"]) < s["n"]:     # Test: no outcome leaves the server before the session ends
        return (REPORT_HEADER + "\n# Test session in progress - the report is available once its last card is decided\n" + ",".join(COLS) + "\n").encode()
    if s and s["mode"] == "test":                       # refresh once at the end (a late advisor v1 still counts)
        S = store(cfg)                                  # review 2 #1: never call store() while holding the lock
        with _LOCK: write_report(cfg, s, S)
    p = os.path.join(web_dir(cfg, "reports"), f"{sid}.csv")
    return open(p, "rb").read() if os.path.exists(p) else (REPORT_HEADER + "\n" + ",".join(COLS) + "\n").encode()


# ------------------------------------------------------------------------------------------------------------- advisor hooks
FENCE = re.compile(r"```levers\s*(\{.*?\})\s*```", re.S)


def parse_recommendation(text_or_obj, menu_keys=PRESET_KEYS) -> dict:
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
    rat = str(obj.get("rationale") or "").strip()
    if not rat or len(rat) > 300: raise ValueError("rationale must be one line (1-300 characters)")
    if obj.get("plan") is not None and not isinstance(obj.get("plan"), dict): raise ValueError("plan must be an object")
    plan = dict(obj.get("plan") or {}); plan.setdefault("schema", E.SCHEMA)
    plan_n, warn = E.validate(plan)                     # only levers the engine evaluates, within the cap (raises otherwise)
    return {"nearest_preset": np_, "rationale": rat.splitlines()[0], "plan": plan_n, "warnings": warn}


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
