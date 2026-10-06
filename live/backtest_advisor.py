"""backtest_advisor.py - trader + coach 2026-10-05 (backtest tab, Test mode): the BLIND backtest advisor. One consult per served Test
card, pre-generated in a batch when the Test session starts, in session order; follow-up questions after the trader's first commit.

QUEUE (<root>/web/backtest/advq/): one file per request.
  <sid>_<i:03d>_init.json      written by live/backtest.py start() for every card of a Test session: the BLIND card only (the same data
                               the trader sees - bars and levels in R from the entry, synthetic time, regime, swings, the nearest V/W
                               event bucket - plus the group's Practice-only notes and the frozen menu). NO symbol, date, outcome,
                               results, combo data or trader commit ever goes into it.
  <sid>_<i:03d>_q<n>.json      a follow-up question (POST /bt/ask), after the first commit; answered in the same consult session.
The advisor runner's tick calls poll(): follow-ups first, then the initial consults in session order, at most max_parallel at once.
A claimed file is renamed .running; a crash leaves it so and recover() (first poll of a process) puts it back. A failed consult is
retried once after 60 s, then the card is marked failed (the trader proceeds without the advisor; the report records it).
STATUS (<root>/web/backtest/advisor/<sid>/<hid>.status.json, written by the runner only): state queued/running/done/failed, verdict
TAKE/SKIP + confidence + why, the consult session id (follow-ups resume it), the follow-up thread. The lever-stack versions live in
<hid>.json (live/backtest.py add_recommendation: v1 from the batch is blind_v1; conversation versions count toward the combined result).
WORK DIR (<advisor live_dir>/../backtest/): CLAUDE.md (the blind role, written here), .claude/settings.json and bundles/<token>/
{card.md, h4.png, d1.png, h4_bars.csv} - <token> is a random id per card (no session id, card number or path in it).
TOOLS (review 2 #3): the consult runs with its working directory = that ONE card's bundle folder and `--restricted` (file tools
confined to the working directory, user/project/local settings ignored, code-running tools and WebFetch removed), `--tools Read`
(the only tool that exists in the session), `--strict-mcp-config` (no MCP servers), an explicit --disallowedTools list as a second
belt, and the role text sent in the prompt itself (stdin) - so it can read its own card and nothing else: not the sessions, the
split, other cards' bundles or the live advisor's files. NO web: a search on a blind card could reveal the date, the symbol or the
outcome (trader 2026-10-04).
PACING (review 2 #9): queue_session keeps the blind batch about ADVISOR_AHEAD cards ahead of the trader's position (called at the
start, on every card load and after every final), not all cards at once. Follow-ups: at most FOLLOWUPS_PER_CARD per card and
FOLLOWUPS_PER_SESSION per session, 3 waiting at a time, one consult at a time per card, only after the card's blind consult is done.
LOCKS (review 2 #4/#5): the webapp and the advisor runner are separate processes, so the status file's read-modify-write and the
follow-up numbering take a per-card lock FILE (O_EXCL; stale after 30 s); temp files have unique names.
CONFIG: CFG.backtest.advisor = {model, effort, timeout_s, max_parallel, max_turns} (defaults: the advisor's first model, medium, 600 s, 2, 30).
"""
from __future__ import annotations
import glob, json, os, re, subprocess, threading, time, uuid
from live import backtest as BT

ROLE_VERSION = "blind-backtest-advisor v2 (2026-10-05)"
DENIED = "Bash,Glob,Grep,LS,WebSearch,WebFetch,Write,Edit,MultiEdit,NotebookEdit,Task,Agent,TodoWrite"
ADVISOR_AHEAD = 10
FOLLOWUPS_PER_CARD = 5
FOLLOWUPS_PER_SESSION = 50
LOCK_STALE_S = 30
VERDICT_RE = re.compile(r"VERDICT:\s*\**\s*(TAKE|SKIP)\b", re.I)
CONF_RE = re.compile(r"CONFIDENCE:\s*\**\s*(\d{1,3})", re.I)
WHY_RE = re.compile(r"WHY:\s*(.+)", re.I)
_MU = threading.Lock()
_THREADS: dict = {}
_RECOVERED = {"done": False}


def settings(cfg: dict) -> dict:
    from live import advisor_runner as AR
    m0 = AR.models(cfg)[0]
    s = {"model": m0.get("model", "opus"), "effort": "medium", "timeout_s": 600, "max_parallel": 2, "max_turns": 30}
    s.update(((cfg.get("backtest") or {}).get("advisor")) or {})
    return s


def qdir(cfg) -> str: return BT.web_dir(cfg, "advq")
def work_dir(cfg) -> str: return os.path.abspath(os.path.join(cfg["advisor"]["live_dir"], "..", "backtest"))
def _sp(cfg, sid, hid) -> str: return os.path.join(BT.web_dir(cfg, "advisor", sid), f"{hid}.status.json")
def _now() -> str: return BT._now()


def get_status(cfg: dict, sid: str, hid: str) -> dict:
    try: return json.load(open(_sp(cfg, sid, hid)))
    except (OSError, ValueError): return {"state": "none"}


class _CardLock:
    """a cross-process lock file (the webapp and the advisor runner are separate processes): O_EXCL create, retried; a lock older than
    LOCK_STALE_S is treated as left by a crash and taken over."""
    def __init__(self, path: str): self.path = path + ".lock"
    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, str(os.getpid()).encode()); os.close(fd); return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > LOCK_STALE_S: os.remove(self.path); continue
                except OSError: pass
                if time.time() - t0 > LOCK_STALE_S + 5: raise TimeoutError(f"lock busy: {self.path}")
                time.sleep(0.02)
    def __exit__(self, *a):
        try: os.remove(self.path)
        except OSError: pass


def _tmpname(path: str) -> str:
    return f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"      # review 2 #4: never shared by two writers


def _set_status(cfg, sid, hid, merge=None, **kw) -> dict:
    """read-modify-write of a card's status under its lock; merge(st) may edit the FRESH status (e.g. append to the thread)."""
    p = _sp(cfg, sid, hid)
    with _CardLock(p):
        st = get_status(cfg, sid, hid)
        if st.get("state") == "none" and "state" not in kw: st.pop("state", None)
        if merge: merge(st)
        st.update(kw); st["updated"] = _now()
        tmp = _tmpname(p); json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, p)
    return st


def _write(path: str, obj: dict) -> None:
    tmp = _tmpname(path); json.dump(obj, open(tmp, "w")); os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------------------- queueing (webapp side)
def queue_session(cfg: dict, s: dict, S: dict, ahead: int = ADVISOR_AHEAD) -> int:
    """one blind request per card of a Test session, in session order, for the cards up to `ahead` past the trader's position
    (review 2 #9 - called at the start, on every card load and after every final; cards already queued/done are skipped).
    Returns how many were queued now."""
    g = s["group"]; info = S["info"]["groups"][g]; menu = S["menu"]; dfl = menu["defaults"][g]
    presets = [{"key": p["key"], "label": p["label"], "about": p["about"], "cfg": p["cfg"]} for p in menu["presets"]]
    upto = min(len(s["ids"]), int(s.get("pos", 0)) + ahead); n = 0
    for i in range(upto):
        hid = s["ids"][i]; q = os.path.join(qdir(cfg), f"{s['sid']}_{i:03d}_init.json")
        if get_status(cfg, s["sid"], hid).get("state", "none") != "none" or os.path.exists(q) or os.path.exists(q + ".running"): continue
        c = dict(S["cards"][hid]); c.pop("hid", None)
        token = uuid.uuid4().hex[:16]                  # the bundle folder's name: no session id, card number or path (review 2 #3)
        job = {"kind": "init", "sid": s["sid"], "i": i, "hid": hid, "bundle": token, "created": time.time(), "group": g, "group_name": info["name"],
               "card": c, "info_lines": info["lines"], "preload": {"label": dfl["label"], "about": dfl["about"], "cfg": dfl["cfg"]},
               "presets": presets, "raw": menu["raw"], "menu_version": menu["version"]}
        _set_status(cfg, s["sid"], hid, state="queued", session=s["sid"], card=i, bundle=token)   # status first: the runner may claim at once
        _write(q, job); n += 1
    return n


def _session_followups(cfg, sid) -> int:
    n = 0
    for f in glob.glob(os.path.join(BT.web_dir(cfg, "advisor", sid), "*.status.json")):
        try: n += int(json.load(open(f)).get("q_seq") or 0)
        except (OSError, ValueError): pass
    return n


def queue_followup(cfg: dict, s: dict, i: int, hid: str, text: str) -> dict:
    """review 2 #5: numbered under the card's lock (no two questions share a number), at most 3 waiting, FOLLOWUPS_PER_CARD per card
    and FOLLOWUPS_PER_SESSION per session (#9)."""
    p = _sp(cfg, s["sid"], hid); out = {}
    with _CardLock(p):
        st = get_status(cfg, s["sid"], hid)
        if st.get("state") != "done": raise ValueError("the advisor has not given its view on this card yet - ask once it has")
        pend = [f for f in glob.glob(os.path.join(qdir(cfg), f"{s['sid']}_{i:03d}_q*.json*")) if f.endswith((".json", ".running"))]
        seq = int(st.get("q_seq") or (len(st.get("thread") or []) + len(pend)))
        if len(pend) >= 3: raise ValueError("the advisor is still answering your earlier questions")
        if seq >= FOLLOWUPS_PER_CARD: raise ValueError(f"at most {FOLLOWUPS_PER_CARD} questions per card")
        if _session_followups(cfg, s["sid"]) >= FOLLOWUPS_PER_SESSION: raise ValueError(f"at most {FOLLOWUPS_PER_SESSION} questions per session")
        k = seq + 1
        _write(os.path.join(qdir(cfg), f"{s['sid']}_{i:03d}_q{k:02d}.json"),
               {"kind": "followup", "sid": s["sid"], "i": i, "hid": hid, "k": k, "text": text, "created": time.time()})
        st["q_seq"] = k; st["updated"] = _now()
        tmp = _tmpname(p); json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, p)
        out = {"ok": True, "queued": True, "k": k}
    return out


def card_view(cfg: dict, sid: str, i: int, hid: str) -> dict:
    """the page's view of the advisor for one card: state, verdict, the thread and the questions still waiting."""
    st = get_status(cfg, sid, hid)
    pend = []
    for f in sorted(glob.glob(os.path.join(qdir(cfg), f"{sid}_{i:03d}_q*.json*"))):
        if f.endswith(".tmp"): continue
        try: pend.append(json.load(open(f))["text"])
        except (OSError, ValueError, KeyError): pass
    if st.get("state") == "none" and os.path.exists(os.path.join(qdir(cfg), f"{sid}_{i:03d}_init.json")): st["state"] = "queued"
    return {"state": st.get("state", "none"), "verdict": st.get("verdict"), "confidence": st.get("confidence"), "why": st.get("why"),
            "error": st.get("error") if st.get("state") == "failed" else None,
            "thread": [{"q": t["q"], "a": t["a"], "at": t["at"], "new_version": t.get("new_version")}
                       for t in sorted(st.get("thread") or [], key=lambda t: (t.get("k") or 0))],
            "pending": pend}


# ------------------------------------------------------------------------------------------------------------- the role + bundle
ROLE = f"""# Blind backtest advisor ({ROLE_VERSION})

You advise a discretionary trader on ONE historical trade signal at a time, shown BLIND: no symbol, no dates, prices in R from the
entry, time counted back from the signal bar. You are graded on your FIRST answer for each card, against the trader and against a
plain default plan ("Minimal"). You cannot look anything up - you have no web access, and you must not try to work out the symbol or
the period: judge the setup on the card.

Your working folder holds exactly one card: read `card.md` and its two charts `h4.png` (the last ~2 months of H4 bars) and `d1.png`
(the last 44 days; the last day is partial, up to the signal). `h4_bars.csv` has the H4 bars as numbers if you need precision. You
can only read files in this folder.

Answer in exactly this shape (the server parses it):

VERDICT: TAKE or SKIP
CONFIDENCE: a whole number 0-100
WHY: one or two plain lines

```levers
{{"schema": "advisor-levers/v1", "nearest_preset": "<minimal|ruthless|slow|banker|pyramid>", "rationale": "<one plain line>",
 "plan": {{"entry": {{"pct": 100}}, "tp2": true, "trim_to_cap": false, "levers": [ ... ]}}}}
```

The levers block is required even when you say SKIP (it is the plan you would use if the trade were taken). Rules for the plan:
- Use only these lever types (the engine evaluates nothing else); values in R are profit-R (a short's +1R is in its favour):
  - {{"type": "bank", "pct": 25, "at": 1.0, "tp1": false}}  bank a % of what is open at +X R (tp1: or at TP1 if nearer)
  - {{"type": "move", "to": 0, "when": 1.0, "tp1": false, "legs": "all"}}  stop to `to` R when +`when` R is reached (tighten only;
    legs: all / original / adds)
  - {{"type": "add_reach", "pct": 75, "at": 2.0, "within_h": 24, "stop": 0}}  add a % of full size when +X R is reached (within N hours or
    null = any time); its stop in R, or "trade" = the original leg's stop at that moment
  - {{"type": "add_bar", "pct": 75, "bar": 6, "early_at": null, "hold_avg": null, "stop": "trade", "wait_avg": null}}  add at the bar-N
    close (optionally early at +X R; hold_avg holds THAT EARLY ADD while the average R since entry is below it - it needs early_at, and
    at the bar close the add goes in regardless; wait_avg, independent: at the bar close, wait while the average R is below it and add
    the first minute it gets there - never, if it never does)
  - {{"type": "add_tp1", "pct": 50, "min_R": 1.0, "stop": 1.0}}  add at TP1 (only if TP1 is above min_R)
  - {{"type": "cut_avg", "at_h": 1, "below": 0}}  close at N hours if the average R since the entry is below X
  - {{"type": "cut_unless", "at_h": 24, "reach": 1.5}}  close at N hours unless +X R was reached
- entry.pct + every add must stay within 200% of the ruled size (or set trim_to_cap true to trim later adds); at most 20 levers per kind.
- nearest_preset = the menu preset (listed in card.md) closest to your plan.
Keep it short. Do not write files.
"""


def _ensure_role(cfg) -> str:
    wd = work_dir(cfg); os.makedirs(os.path.join(wd, ".claude"), exist_ok=True); os.makedirs(os.path.join(wd, "bundles"), exist_ok=True)
    p = os.path.join(wd, "CLAUDE.md")
    if not os.path.exists(p) or ROLE_VERSION not in open(p, encoding="utf-8").read():
        tmp = p + ".tmp"; open(tmp, "w", encoding="utf-8").write(ROLE); os.replace(tmp, p)
    sp = os.path.join(wd, ".claude", "settings.json")         # ignored under --restricted; kept as a deny-only second belt
    want = {"effortLevel": settings(cfg)["effort"], "permissions": {"allow": [], "deny": DENIED.split(",")}}
    try: cur = json.load(open(sp))
    except (OSError, ValueError): cur = None
    if cur != want: _write(sp, want)
    return wd


def _fr(v) -> str:
    return "-" if v is None else f"{v:+.2f}R"


def bundle_dir(cfg: dict, token: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{16}", token or ""): raise ValueError("bad bundle token")
    return os.path.join(work_dir(cfg), "bundles", token)


def overlay_lines(c: dict) -> list:
    """review 2 #12: the detector overlay in the card's own axes - price-R and 'bars ago' (as h4_bars.csv), never synthetic times."""
    ov = c.get("overlay") or {}; bars = c["h4"]["bars"]; n = len(bars); idx = {b[0]: k for k, b in enumerate(bars)}
    ago = lambda t: (n - 1 - idx[t]) if t in idx else None
    out = ["- Detector overlay (as drawn on h4.png; prices in price-R, times in bars ago - 0 = the signal bar):"]
    z = ov.get("zone") or {}
    if z: out.append(f"  - zone from {ago(z.get('from'))} to {ago(z.get('to'))} bars ago, between {z.get('lo'):+.2f}R and {z.get('hi'):+.2f}R")
    z2 = ov.get("zone2") or {}
    if z2 and z2.get("hi") is not None and z2.get("hi") != z2.get("lo"): out.append(f"  - second zone between {z2.get('lo'):+.2f}R and {z2.get('hi'):+.2f}R")
    lg = ov.get("leg") or {}
    if lg: out.append(f"  - fib leg from {lg.get('p0'):+.2f}R ({ago(lg.get('t0'))} bars ago) to {lg.get('p1'):+.2f}R ({ago(lg.get('t1'))} bars ago)")
    for a in ov.get("aux") or []: out.append(f"  - line {a.get('label')}: {a.get('price'):+.2f}R")
    for key, nm, col in (("swings_hi", "labelled swing highs", 2), ("swings_lo", "labelled swing lows", 3)):
        xs = [f"{bars[idx[x['t']]][col]:+.2f}R ({ago(x['t'])}b)" for x in ov.get(key) or [] if x.get("t") in idx]
        if xs: out.append(f"  - {nm}: " + ", ".join(xs))
    return out


def write_bundle(cfg: dict, job: dict) -> str:
    """bundles/<sid>/<i>/: card.md + h4.png + d1.png + h4_bars.csv - from the blind card in the queue file only."""
    wd = _ensure_role(cfg); c = job["card"]; d = bundle_dir(cfg, job["bundle"]); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "CLAUDE.md"), "w", encoding="utf-8").write(ROLE)      # the role also goes in the prompt (stdin)
    sell = c.get("direction") == "SELL"; sg = -1 if sell else 1; lv = c.get("levels") or {}
    sw = c.get("swings") or {}; rg = c.get("regime") or {}
    ev = "; ".join(f"nearest class {e['cls']} event: {e['when']}" for e in c.get("events") or []) or "no V or W event in the next 14 days"
    em = lambda tf: ", ".join(f"EMA{n} {(c[tf].get('ema') or {}).get(n, [None])[-1]:+.2f}" for n in ("20", "50", "200")
                              if (c[tf].get("ema") or {}).get(n))
    lines = [f"# Blind card - {job['group_name']}", "",
             f"- Detector: {c.get('detector')} · direction: {c.get('direction')} · regime (completed days): {rg.get('tag') or '-'}"
             + (f" (with the trend: {'yes' if rg.get('with_trend') else 'no'})" if rg.get("with_trend") is not None else ""),
             "- Prices are R from the entry in PRICE terms: the entry is 0; for a BUY the signal's stop is -1R and higher is better; for a SELL "
             "the stop is +1R and LOWER is better. Levers and your plan are in PROFIT terms (a SELL's profit-R = minus its price-R).",
             f"- Levels (price-R): entry {_fr(lv.get('entry'))}, SL {_fr(lv.get('sl'))}, TP1 {_fr(lv.get('tp1'))}, TP2 {_fr(lv.get('tp2'))}"
             + (f"  ->  in profit-R: TP1 {_fr(sg * lv['tp1'])}, TP2 {_fr(sg * lv['tp2'])}" if lv.get("tp1") is not None and lv.get("tp2") is not None else ""),
             "- Swing highs (price-R, bars ago, newest first): " + ", ".join(f"{x['p']:+.2f} ({x['bars_ago']}b)" for x in (sw.get("hi") or [])[:6]),
             "- Swing lows  (price-R, bars ago, newest first): " + ", ".join(f"{x['p']:+.2f} ({x['bars_ago']}b)" for x in (sw.get("lo") or [])[:6]),
             f"- High-impact events ahead (class + coarse timing only): {ev}",
             f"- At the signal bar - H4: {em('h4')}; D1: {em('d1')}",
             "- The H4 chart ends at the signal bar's close; the D1 chart's last bar is partial (up to the signal).", ""]
    if c.get("overlay"): lines += overlay_lines(c) + [""]
    lines += ["## Group notes (Practice partition only; 'tested' = measured, 'observed' = descriptive)"]
    lines += [f"- [{l['tag']}] {l['text']}" for l in job.get("info_lines") or []]
    lines += ["", f"## The trader's preloaded plan for this group: {job['preload']['label']}", f"- {job['preload']['about']}",
              "", f"## Menu presets (v{job.get('menu_version', '1')[-1:]}, frozen) - name the nearest one in your levers block"]
    lines += [f"- `{p['key']}` - {p['label']}: {p['about']}" for p in job.get("presets") or []]
    open(os.path.join(d, "card.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    with open(os.path.join(d, "h4_bars.csv"), "w") as f:
        f.write("bars_ago,open,high,low,close\n")
        bars = c["h4"]["bars"]; n = len(bars)
        for k, b in enumerate(bars): f.write(f"{n - 1 - k},{b[1]:.3f},{b[2]:.3f},{b[3]:.3f},{b[4]:.3f}\n")
    try:
        from live import charts
        for tf, name in (("h4", "h4.png"), ("d1", "d1.png")):
            bars = c[tf]["bars"]
            img = charts.render(bars, title=f"{tf.upper()} · blind card · price in R from the entry · {c.get('direction')}", levels=lv,
                                overlay=(c.get("overlay") if tf == "h4" else None), n_show=len(bars), digits=2, hide_dates=True, strategy=c.get("detector", ""),
                                signal_t=bars[c[tf].get("sig", len(bars) - 1)][0] if bars else None, emas_in=c[tf].get("ema"))
            img.save(os.path.join(d, name + ".tmp"), "PNG"); os.replace(os.path.join(d, name + ".tmp"), os.path.join(d, name))
    except Exception as e:                             # the text + CSV still carry the card
        open(os.path.join(d, "charts_unavailable.txt"), "w").write(f"chart rendering failed: {e!r}\n")
    return d


# ------------------------------------------------------------------------------------------------------------- the consult
def consult_args(cmd: str, s: dict, sid: str, resume: bool) -> list:
    """the claude CLI arguments of a blind consult (review 2 #3; checked by the tests): one tool (Read), confined to the working
    directory (= the card's bundle folder) by --restricted, no MCP servers, only project settings, explicit deny list."""
    return [cmd, "-p", "--output-format", "json", "--model", s["model"], "--effort", s["effort"]] + \
           (["--resume", sid] if resume else ["--session-id", sid]) + \
           ["--restricted", "--tools", "Read", "--allowedTools", "Read(./**)", "--disallowedTools", DENIED,
            "--strict-mcp-config", "--setting-sources", "project", "--max-turns", str(int(s["max_turns"]))]


def call_claude(cfg: dict, prompt: str, s: dict, resume: str | None = None, cwd: str | None = None) -> dict:
    from live import advisor_runner as AR
    cmd = cfg["advisor"]["claude_cmd"]; sid = resume or str(uuid.uuid4())
    if not cwd or not os.path.isdir(cwd): return {"ok": False, "error": "no bundle folder for this card", "elapsed_s": 0.0, "session_id": sid}
    args = consult_args(cmd, s, sid, bool(resume))
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=cwd, env=AR.claude_env(cfg), input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=int(s["timeout_s"]))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {s['timeout_s']}s", "elapsed_s": round(time.time() - t0, 1), "session_id": sid}
    el = round(time.time() - t0, 1)
    try: j = json.loads(p.stdout)
    except ValueError: return {"ok": False, "error": f"non-json output (rc={p.returncode}): {(p.stderr or p.stdout)[:400]}", "elapsed_s": el, "session_id": sid}
    if j.get("is_error") or p.returncode != 0: return {"ok": False, "error": str(j.get("result") or p.stderr)[:600], "elapsed_s": el, "session_id": sid}
    return {"ok": True, "text": j.get("result", ""), "elapsed_s": el, "session_id": j.get("session_id", sid), "cost_usd": j.get("total_cost_usd")}


def parse_verdict(text: str) -> tuple[str | None, int | None, str | None]:
    m = VERDICT_RE.search(text or ""); c = CONF_RE.search(text or ""); w = WHY_RE.search(text or "")
    conf = max(0, min(100, int(c.group(1)))) if c else None
    return (m.group(1).upper() if m else None), conf, (w.group(1).strip()[:300] if w else None)


def init_prompt() -> str:
    """the role (sent here, on stdin, so it does not depend on CLAUDE.md loading) + the task. No session id, card number or path."""
    return (ROLE + "\n\n---\n\nAdvise on the blind card in your working folder (read card.md, h4.png and d1.png). "
            "Answer exactly in the shape given above: VERDICT, CONFIDENCE, WHY, then the levers block.")


def _do_init(cfg, log, job, consult) -> None:
    sid, i, hid = job["sid"], job["i"], job["hid"]
    if not job.get("bundle"): job["bundle"] = uuid.uuid4().hex[:16]          # a job queued before review 2
    _set_status(cfg, sid, hid, state="running", started=_now(), attempts=job.get("attempts", 0) + 1, bundle=job["bundle"])
    d = write_bundle(cfg, job)
    s = settings(cfg)
    r = (consult or call_claude)(cfg, init_prompt(), s, None, d)
    if not r.get("ok"): raise RuntimeError(r.get("error") or "consult failed")
    text = r.get("text") or ""
    vd, conf, why = parse_verdict(text)
    ent = BT.add_recommendation(cfg, sid, hid, text, source="blind batch", blind=True)
    _set_status(cfg, sid, hid, state="done", verdict=vd, confidence=conf, why=why, session_id=r.get("session_id"), text=text[:6000],
                elapsed_s=r.get("elapsed_s"), cost_usd=r.get("cost_usd"), stack_valid=ent.get("valid"), stack_reason=ent.get("reason"),
                finished=_now(), error=None if vd else "no VERDICT line in the answer")
    log(f"backtest advisor {sid}#{i}: {vd or 'no verdict'} ({conf}) stack {'ok' if ent.get('valid') else 'REJECTED: ' + str(ent.get('reason'))} in {r.get('elapsed_s')}s")


def _do_followup(cfg, log, job, consult) -> None:
    sid, i, hid = job["sid"], job["i"], job["hid"]
    st = get_status(cfg, sid, hid)
    if st.get("state") != "done" or not st.get("session_id"): raise RuntimeError("the card's blind consult is not done")   # review 2 #4
    prompt = (f"Follow-up from the trader about the same blind card: {job['text']}\n\nAnswer briefly in plain words. "
              "If you change your recommended plan, end with a new ```levers``` block in the same schema; otherwise no block.")
    d = bundle_dir(cfg, st.get("bundle") or "0" * 16)
    r = (consult or call_claude)(cfg, prompt, settings(cfg), st.get("session_id"), d)
    if not r.get("ok"): raise RuntimeError(r.get("error") or "consult failed")
    text = r.get("text") or ""; nv = None
    if BT.FENCE.search(text):
        ent = BT.add_recommendation(cfg, sid, hid, text, source="conversation")
        nv = ent["v"] if ent.get("valid") else f"rejected: {ent.get('reason')}"
    entry = {"q": job["text"], "a": BT.FENCE.sub("(new plan below)", text).strip()[:4000], "at": _now(), "new_version": nv, "k": job.get("k")}
    def merge(fresh):                                  # review 2 #5: append to the FRESH thread under the card lock (no lost answers)
        fresh["thread"] = list(fresh.get("thread") or []) + [entry]
        fresh["session_id"] = r.get("session_id") or fresh.get("session_id")
    _set_status(cfg, sid, hid, merge=merge)
    log(f"backtest advisor {sid}#{i}: follow-up answered" + (f" (new stack v{nv})" if isinstance(nv, int) else ""))


def _work(cfg, log, path, job, consult) -> None:
    try:
        (_do_init if job["kind"] == "init" else _do_followup)(cfg, log, job, consult)
        try: os.remove(path)
        except FileNotFoundError: pass
    except Exception as e:
        att = job.get("attempts", 0) + 1
        try: os.remove(path)
        except FileNotFoundError: pass
        if att < 2:                                    # one retry after a minute (e.g. a rate limit)
            job["attempts"] = att; job["not_before"] = time.time() + 60
            _write(path[:-len(".running")], job)
            if job["kind"] == "init": _set_status(cfg, job["sid"], job["hid"], state="queued", error=f"retrying: {str(e)[:200]}")
        elif job["kind"] == "init":
            _set_status(cfg, job["sid"], job["hid"], state="failed", error=str(e)[:300], finished=_now())
        else:
            entry = {"q": job["text"], "a": f"(the advisor could not answer: {str(e)[:200]})", "at": _now(), "new_version": None, "k": job.get("k")}
            _set_status(cfg, job["sid"], job["hid"], merge=lambda fresh: fresh.__setitem__("thread", list(fresh.get("thread") or []) + [entry]))
        log(f"backtest advisor {job['sid']}#{job['i']} {job['kind']}: {'retry' if att < 2 else 'FAILED'}: {e!r}")


def recover(cfg) -> None:
    for f in glob.glob(os.path.join(qdir(cfg), "*.json.running")):
        try: os.replace(f, f[:-len(".running")])
        except OSError: pass


def poll(cfg: dict, log, consult=None) -> None:
    """called by the advisor runner's tick (every few seconds): start queued consults up to max_parallel."""
    if not os.path.isdir(os.path.join(cfg["root"], "web", "backtest")): return
    if not _RECOVERED["done"]: recover(cfg); _RECOVERED["done"] = True
    with _MU:
        for k in [k for k, t in _THREADS.items() if not t.is_alive()]: _THREADS.pop(k, None)
        cap = max(1, int(settings(cfg)["max_parallel"]))
        if len(_THREADS) >= cap: return
        busy = {os.path.basename(k).split("_")[0] + "_" + os.path.basename(k).split("_")[1] for k in _THREADS}   # "<sid>_<i>" running
        jobs = []
        for f in glob.glob(os.path.join(qdir(cfg), "*.json")):
            try: j = json.load(open(f))
            except (OSError, ValueError): continue
            if j.get("not_before", 0) > time.time(): continue
            if j.get("kind") == "followup" and get_status(cfg, j["sid"], j["hid"]).get("state") != "done": continue   # review 2 #4
            jobs.append((0 if j.get("kind") == "followup" else 1, j.get("created", 0), j.get("i", 0), j.get("k", 0), f, j))
        for _, _, _, _, f, j in sorted(jobs, key=lambda x: x[:4]):
            if len(_THREADS) >= cap: break
            card = os.path.basename(f).split("_")[0] + "_" + os.path.basename(f).split("_")[1]
            if card in busy: continue                  # review 2 #5: one consult at a time per card (follow-ups in question order)
            run = f + ".running"
            try: os.replace(f, run)
            except OSError: continue
            if j["kind"] == "init": _ensure_role(cfg)
            busy.add(card)
            t = threading.Thread(target=_work, args=(cfg, log, run, j, consult), daemon=True); _THREADS[run] = t; t.start()
