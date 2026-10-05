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
proportions, shuffled, no two signals of one symbol within 182 days, research-locked trades never served (live lock reader).
Sizes (coach): commodity bloc 100, USD majors 35. Test mode (one pass ever, ledger, two-step advisor reveal) comes in a later phase.

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

GAP_DAYS = 182
SIZES = {"com": 100, "maj": 35}
DEFAULTS = {"tab_visible": False, "data_dir": None, "advisor_slot": {"practice": "off", "test": "after_commit"}, "dev": False}
_LOCK = threading.Lock()
_STORE: dict = {}
PRESET_KEYS = ("minimal", "ruthless", "slow", "banker", "pyramid")
KIND = {0: "stopped out", 1: "TP2 hit", 2: "closed by a cut", 3: "still open at the frame end (closed at the last close)"}


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
    d = data_dir(cfg); key = (d, os.path.getmtime(os.path.join(d, "results_v1.json.gz")))
    if _STORE.get("key") != key:
        with _LOCK:
            split = json.load(open(os.path.join(d, "split_v1.json")))
            cards = json.load(gzip.open(os.path.join(d, "cards_v1.json.gz")))["cards"]
            res = json.load(gzip.open(os.path.join(d, "results_v1.json.gz")))
            frames = E.Frames(os.path.join(d, "frames_v1"))
            ok = {h for h in split["trades"] if h in cards and h in res["trades"] and h in frames.idx}
            _STORE.clear(); _STORE.update(key=key, split=split, cards=cards, results=res["trades"], menu=json.load(open(os.path.join(d, "menu_v1.json"))),
                                          info=json.load(open(os.path.join(d, "info_v1.json"))), frames=frames, eligible=ok)
    return _STORE


# ------------------------------------------------------------------------------------------------------------- sampler
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
    picked: list = []; last: dict = {}; short = {}
    def ok(h):
        t = pool[h]; s = t["key"][0].split(".")[0]; d = _date(t)
        return all(abs((d - d2).days) >= GAP_DAYS for d2 in last.get(s, []))
    for s in sorted(quota, key=lambda s: (len(st.get(s, [])) / max(1, quota[s]), s)):
        cand = sorted(st.get(s, [])); rng.shuffle(cand); got = 0
        for h in cand:
            if got >= quota[s]: break
            if ok(h):
                picked.append(h); got += 1; t = pool[h]; last.setdefault(t["key"][0].split(".")[0], []).append(_date(t))
        if got < quota[s]: short[s] = quota[s] - got
    rng.shuffle(picked)
    return {"ids": picked, "shortfall": {"|".join(map(str, k)): v for k, v in short.items()}, "n_target": n}


# ------------------------------------------------------------------------------------------------------------- sessions
def _sp(cfg, sid): return os.path.join(web_dir(cfg, "sessions"), f"{sid}.json")
def _safe_sid(sid: str) -> bool: return bool(re.fullmatch(r"[0-9a-f]{12}", sid or ""))
def _now() -> str: return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_session(cfg, sid):
    if not _safe_sid(sid): raise ValueError("bad session id")
    return json.load(open(_sp(cfg, sid)))


def save_session(cfg, s):
    tmp = _sp(cfg, s["sid"]) + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, _sp(cfg, s["sid"]))


def start(cfg: dict, group: str, mode: str, lock: dict) -> dict:
    if group not in SIZES: raise ValueError("unknown group")
    if mode != "practice": raise ValueError("only Practice mode is built so far (Test mode comes in a later phase)")
    S = store(cfg); seed = secrets.randbelow(10 ** 9)
    r = sample_session(S["split"], group, "practice", SIZES[group], seed, lock, S["eligible"])
    sid = secrets.token_hex(6)
    s = {"sid": sid, "group": group, "mode": mode, "seed": seed, "n": len(r["ids"]), "n_target": SIZES[group], "shortfall": r["shortfall"],
         "created": _now(), "menu": S["menu"]["version"], "ids": r["ids"], "pos": 0, "shown": {}, "decisions": [], "commits": {}, "adv_loaded": {}}
    with _LOCK: save_session(cfg, s)
    return {"sid": sid, "n": s["n"], "seed": seed, "group": group, "mode": mode, "shortfall": r["shortfall"]}


def list_sessions(cfg: dict) -> list:
    out = []
    for f in sorted(os.listdir(web_dir(cfg, "sessions")), reverse=True):
        if not f.endswith(".json"): continue
        try: s = json.load(open(os.path.join(web_dir(cfg, "sessions"), f)))
        except (OSError, ValueError): continue
        out.append({"sid": s["sid"], "group": s["group"], "mode": s["mode"], "created": s["created"], "n": s["n"], "done": len(s["decisions"]),
                    "tally": tally(s)})
    return sorted(out, key=lambda x: x["created"], reverse=True)[:50]


def tally(s: dict) -> dict:
    d = s["decisions"]
    return {"decided": len(d), "taken": sum(1 for x in d if x["decision"] == "take"), "skipped": sum(1 for x in d if x["decision"] == "skip"),
            "R_choice": round(sum(x["R_choice"] for x in d), 3), "R_raw": round(sum(x["R_raw"] for x in d), 3),
            "R_default": round(sum(x["R_default"] for x in d), 3)}


def advisor_visible(cfg: dict, s: dict, i: int) -> bool:
    flag = settings(cfg)["advisor_slot"].get(s["mode"], "off")
    if flag == "always": return True
    if flag == "after_commit": return str(i) in s["commits"]
    return False


def card(cfg: dict, sid: str) -> dict:
    """the current card of a session (blind) - or the end-of-session summary."""
    with _LOCK:
        s = load_session(cfg, sid); i = s["pos"]
        if i >= s["n"]: return {"done": True, "tally": tally(s), "n": s["n"], "sid": sid}
        if str(i) not in s["shown"]: s["shown"][str(i)] = time.time(); save_session(cfg, s)
    S = store(cfg); hid = s["ids"][i]; c = dict(S["cards"][hid])
    g = s["group"]; info = S["info"]["groups"][g]
    out = {"sid": sid, "i": i, "n": s["n"], "mode": s["mode"], "card": c, "info": info, "tally": tally(s),
           "default": S["menu"]["defaults"][g], "advisor": None, "advisor_flag": settings(cfg)["advisor_slot"].get(s["mode"], "off")}
    if advisor_visible(cfg, s, i): out["advisor"] = advisor_latest(cfg, sid, hid)
    return out


def _evaluate(S, hid, cfg_plan):
    F, ctx = S["frames"].get(hid)
    r = E.evaluate(cfg_plan, F, ctx)
    tm = ctx["tm"]; j = min(r["exit_j"], len(tm) - 1)
    r["exit_h"] = round(float(tm[j]) / 60.0, 1)
    return r


def decide(cfg: dict, sid: str, i: int, decision: str, plan: dict | None) -> dict:
    """record the decision server-side, THEN evaluate and reveal."""
    if decision not in ("take", "skip"): raise ValueError("decision must be take or skip")
    S = store(cfg)
    with _LOCK:
        s = load_session(cfg, sid)
        if i != s["pos"]: raise ValueError("this card is not the current one (already decided, or out of order)")
        hid = s["ids"][i]; R = S["results"][hid]; g = s["group"]
        if decision == "take":
            if plan is None: raise ValueError("a take needs a plan")
            plan_n, warn = E.validate(plan)
        else:
            plan_n, warn = (E.validate(plan) if plan else (None, []))
        ev = _evaluate(S, hid, plan_n) if plan_n else None
        dflt = R["default"]; mv = S["menu"]
        adv = advisor_record(cfg, sid, hid)
        a1 = next((v for v in adv["versions"] if v["valid"] and v.get("blind_v1")), None) if adv else None
        al = next((v for v in reversed(adv["versions"]) if v["valid"]), None) if adv else None
        adv_cols = {}
        if a1 or al:
            adv_cols = {"adv_v1_stack": json.dumps(a1["plan"], separators=(",", ":")) if a1 else "",
                        "adv_nearest_preset": (a1 or al)["nearest_preset"], "R_adv_v1": round(_evaluate(S, hid, a1["plan"])["R"], 4) if a1 else "",
                        "R_adv_nearest": R["R"].get((a1 or al)["nearest_preset"], ""), "R_adv_latest": round(_evaluate(S, hid, al["plan"])["R"], 4) if al else "",
                        "adv_versions": len(adv["versions"]), "adv_loaded": bool(s["adv_loaded"].get(str(i))),
                        "adv_final_unchanged": bool(al and plan_n and json.dumps(al["plan"], sort_keys=True) == json.dumps(plan_n, sort_keys=True))}
        t_shown = s["shown"].get(str(i), time.time())
        rec = {"i": i, "hid": hid, "decision": decision, "plan": plan_n, "t_decided": _now(), "decision_s": round(time.time() - t_shown, 1),
               "R_choice": round(ev["R"], 4) if (decision == "take" and ev) else 0.0, "R_plan": round(ev["R"], 4) if ev else None,
               "R_raw": R["R"]["raw"], "R_default": R["R"][dflt], "default": dflt, "R_presets": {k: R["R"][k] for k in PRESET_KEYS},
               "banked_1R": R["banked_1R"], "hit_tp2": R["hit_tp2"], "kind": ev["kind"] if ev else None, "exit_h": ev["exit_h"] if ev else None,
               "first": s["commits"].get(str(i)), **adv_cols}
        s["decisions"].append(rec); s["pos"] = i + 1
        save_session(cfg, s); write_report(cfg, s, S)
    labels = {p["key"]: p["label"] for p in mv["presets"]}
    return {"ok": True, "decision": decision, "warnings": warn, "R_choice": rec["R_choice"], "R_plan": rec["R_plan"], "R_raw": rec["R_raw"],
            "R_default": rec["R_default"], "default_label": mv["defaults"][g]["label"], "default_key": dflt,
            "presets": [{"key": k, "label": labels[k], "R": R["R"][k]} for k in PRESET_KEYS],
            "banked_1R": rec["banked_1R"], "hit_tp2": rec["hit_tp2"], "kind": KIND.get(rec["kind"], "") if ev else "", "exit_h": rec["exit_h"],
            "adds_fired": ev["adds"] if ev else 0, "post_bars": R["reveal"], "advisor": adv_cols or None, "tally": tally(s), "next": s["pos"] < s["n"]}


def commit_first(cfg: dict, sid: str, i: int, decision: str, plan: dict | None) -> dict:
    """the coach's two-step (used where the advisor slot is 'after_commit'): store the trader's first commit server-side (never
    passed to any advisor bundle), then the advisor's plan becomes visible; the final decision goes through decide()."""
    if decision not in ("take", "skip"): raise ValueError("decision must be take or skip")
    plan_n = E.validate(plan)[0] if plan else None
    with _LOCK:
        s = load_session(cfg, sid)
        if i != s["pos"]: raise ValueError("this card is not the current one")
        s["commits"][str(i)] = {"decision": decision, "plan": plan_n, "t": _now()}; save_session(cfg, s)
    S = store(cfg)
    return {"ok": True, "advisor": advisor_latest(cfg, sid, s["ids"][i])}


def mark_adv_loaded(cfg: dict, sid: str, i: int) -> dict:
    with _LOCK:
        s = load_session(cfg, sid); s["adv_loaded"][str(i)] = _now(); save_session(cfg, s)
    return {"ok": True}


# ------------------------------------------------------------------------------------------------------------- report
COLS = ["hid", "group", "detector", "direction", "mode", "seed", "menu_version", "decision", "decision_time_s", "chosen_config",
        "R_choice", "R_plan_if_taken", "R_raw", "R_default", "default"] + [f"R_{k}" for k in PRESET_KEYS] + \
       ["banked_1R", "hit_tp2", "first_decision", "first_config", "adv_v1_stack", "adv_nearest_preset", "R_adv_v1", "R_adv_nearest",
        "R_adv_latest", "adv_versions", "adv_loaded", "adv_final_unchanged"]


def write_report(cfg: dict, s: dict, S: dict) -> None:
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(COLS)
    for d in s["decisions"]:
        c = S["cards"][d["hid"]]; fst = d.get("first") or {}
        w.writerow([d["hid"], c["group"], c["detector"], c["direction"], s["mode"], s["seed"], s["menu"], d["decision"], d["decision_s"],
                    json.dumps(d["plan"], separators=(",", ":")) if d["plan"] else "", d["R_choice"], d["R_plan"] if d["R_plan"] is not None else "",
                    d["R_raw"], d["R_default"], d["default"]] + [d["R_presets"][k] for k in PRESET_KEYS] +
                   [d["banked_1R"], d["hit_tp2"], fst.get("decision", ""), json.dumps(fst.get("plan"), separators=(",", ":")) if fst.get("plan") else ""] +
                   [d.get(k, "") for k in ("adv_v1_stack", "adv_nearest_preset", "R_adv_v1", "R_adv_nearest", "R_adv_latest", "adv_versions", "adv_loaded", "adv_final_unchanged")])
    rd = web_dir(cfg, "reports")
    tmp = os.path.join(rd, f"{s['sid']}.csv.tmp"); open(tmp, "w", newline="").write(buf.getvalue()); os.replace(tmp, os.path.join(rd, f"{s['sid']}.csv"))
    t = tally(s); n = max(1, t["decided"])
    summ = {"sid": s["sid"], "group": s["group"], "mode": s["mode"], "seed": s["seed"], "menu_version": s["menu"], "created": s["created"],
            "signals": s["n"], **t, "R_choice_per_signal": round(t["R_choice"] / n, 4), "R_raw_per_signal": round(t["R_raw"] / n, 4),
            "vs_raw": round(t["R_choice"] - t["R_raw"], 3), "vs_default": round(t["R_choice"] - t["R_default"], 3),
            "note": "comparison = the raw signal (every signal taken raw); model cost - read the difference, not the level"}
    json.dump(summ, open(os.path.join(rd, f"{s['sid']}.summary.json"), "w"), indent=1)


def report_csv(cfg: dict, sid: str) -> bytes:
    if not _safe_sid(sid): raise ValueError("bad session id")
    p = os.path.join(web_dir(cfg, "reports"), f"{sid}.csv")
    return open(p, "rb").read() if os.path.exists(p) else (",".join(COLS) + "\n").encode()


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
        ent = {"v": v, "at": _now(), "source": source, "blind_v1": bool(blind and v == 1)}
        try:
            ent.update(parse_recommendation(text_or_obj), valid=True, reason=None)
        except ValueError as e:
            ent.update(valid=False, reason=str(e))
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
    return {"menu": S["menu"], "groups": [{"key": g, "name": S["info"]["groups"][g]["name"], "size": SIZES[g]} for g in SIZES],
            "sessions": list_sessions(cfg), "advisor_slot": settings(cfg)["advisor_slot"], "dev": bool(settings(cfg)["dev"]),
            "comparison": "raw"}


def page_body(static_v: str) -> str:
    v = static_v
    return (f'<link rel="stylesheet" href="/static/vendor/shoelace/themes/dark.css?v={v}"><link rel="stylesheet" href="/static/backtest.css?v={v}">'
            f'<script type="module" src="/static/vendor/shoelace/shoelace-autoloader.js?v={v}"></script>'
            f'<script src="/static/lw.js?v={v}"></script><script src="/static/hybrid_chart.js?v={v}"></script>'
            f'<script src="/static/vendor/sortable/Sortable.min.js?v={v}"></script><script src="/static/backtest.js?v={v}"></script>'
            f'<script defer src="/static/vendor/alpine/alpine.min.js?v={v}"></script>'
            '<div id="bt-app" class="sl-theme-dark" x-data="btApp()" x-init="init()" x-cloak>' + body_html() + '</div>')


def body_html() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "backtest.html")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else "<p>backtest.html missing</p>"
