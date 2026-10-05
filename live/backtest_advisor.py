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
WORK DIR (<advisor live_dir>/../backtest/): CLAUDE.md (the blind role, written here), .claude/settings.json and bundles/<sid>/<i>/
{card.md, h4.png, d1.png, h4_bars.csv}. TOOLS: Read/Glob/Grep only - NO web: a search on a blind card could reveal the date, the
symbol or the outcome (trader 2026-10-04).
CONFIG: CFG.backtest.advisor = {model, effort, timeout_s, max_parallel, max_turns} (defaults: the advisor's first model, medium, 600 s, 2, 30).
"""
from __future__ import annotations
import glob, json, os, re, subprocess, threading, time, uuid
from live import backtest as BT

ROLE_VERSION = "blind-backtest-advisor v1 (2026-10-05)"
DENIED = "Bash,WebSearch,WebFetch,Write,Edit,MultiEdit,NotebookEdit,Task,Agent,TodoWrite"
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


def _set_status(cfg, sid, hid, **kw) -> dict:
    st = get_status(cfg, sid, hid); st.update(kw); st["updated"] = _now()
    p = _sp(cfg, sid, hid); tmp = p + ".tmp"; json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, p)
    return st


def _write(path: str, obj: dict) -> None:
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w")); os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------------------- queueing (webapp side)
def queue_session(cfg: dict, s: dict, S: dict) -> None:
    """one blind request per card of a Test session, in session order (called by backtest.start)."""
    g = s["group"]; info = S["info"]["groups"][g]; menu = S["menu"]; dfl = menu["defaults"][g]
    presets = [{"key": p["key"], "label": p["label"], "about": p["about"], "cfg": p["cfg"]} for p in menu["presets"]]
    for i, hid in enumerate(s["ids"]):
        c = dict(S["cards"][hid]); c.pop("hid", None)
        job = {"kind": "init", "sid": s["sid"], "i": i, "hid": hid, "created": time.time(), "group": g, "group_name": info["name"],
               "card": c, "info_lines": info["lines"], "preload": {"label": dfl["label"], "about": dfl["about"], "cfg": dfl["cfg"]},
               "presets": presets, "raw": menu["raw"], "menu_version": menu["version"]}
        _set_status(cfg, s["sid"], hid, state="queued", session=s["sid"], card=i)   # status first: the runner may claim the job at once
        _write(os.path.join(qdir(cfg), f"{s['sid']}_{i:03d}_init.json"), job)


def queue_followup(cfg: dict, s: dict, i: int, hid: str, text: str) -> dict:
    pend = [f for f in glob.glob(os.path.join(qdir(cfg), f"{s['sid']}_{i:03d}_q*.json*")) if not f.endswith(".tmp")]
    k = len(get_status(cfg, s["sid"], hid).get("thread") or []) + len(pend) + 1
    if len(pend) >= 3: raise ValueError("the advisor is still answering your earlier questions")
    _write(os.path.join(qdir(cfg), f"{s['sid']}_{i:03d}_q{k:02d}.json"),
           {"kind": "followup", "sid": s["sid"], "i": i, "hid": hid, "text": text, "created": time.time()})
    return {"ok": True, "queued": True}


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
            "thread": [{"q": t["q"], "a": t["a"], "at": t["at"], "new_version": t.get("new_version")} for t in st.get("thread") or []],
            "pending": pend}


# ------------------------------------------------------------------------------------------------------------- the role + bundle
ROLE = f"""# Blind backtest advisor ({ROLE_VERSION})

You advise a discretionary trader on ONE historical trade signal at a time, shown BLIND: no symbol, no dates, prices in R from the
entry, time counted back from the signal bar. You are graded on your FIRST answer for each card, against the trader and against a
plain default plan ("Minimal"). You cannot look anything up - you have no web access, and you must not try to work out the symbol or
the period: judge the setup on the card.

Read the card's `card.md` (it names its folder) and its two charts `h4.png` (the last ~2 months of H4 bars) and `d1.png` (the last
44 days; the last day is partial, up to the signal). `h4_bars.csv` has the H4 bars as numbers if you need precision.

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
  - {{"type": "add_bar", "pct": 75, "bar": 6, "early_at": null, "hold_avg": null, "stop": "trade"}}  add at the bar-N close (optionally
    early at +X R, held while the average R since entry is below hold_avg)
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
    sp = os.path.join(wd, ".claude", "settings.json")
    want = {"effortLevel": settings(cfg)["effort"], "permissions": {"allow": ["Read", "Glob", "Grep"], "deny": DENIED.split(",")}}
    try: cur = json.load(open(sp))
    except (OSError, ValueError): cur = None
    if cur != want: _write(sp, want)
    return wd


def _fr(v) -> str:
    return "-" if v is None else f"{v:+.2f}R"


def write_bundle(cfg: dict, job: dict) -> str:
    """bundles/<sid>/<i>/: card.md + h4.png + d1.png + h4_bars.csv - from the blind card in the queue file only."""
    wd = _ensure_role(cfg); c = job["card"]; d = os.path.join(wd, "bundles", job["sid"], str(job["i"])); os.makedirs(d, exist_ok=True)
    sell = c.get("direction") == "SELL"; sg = -1 if sell else 1; lv = c.get("levels") or {}
    sw = c.get("swings") or {}; rg = c.get("regime") or {}
    ev = "; ".join(f"nearest class {e['cls']} event: {e['when']}" for e in c.get("events") or []) or "no V or W event in the next 14 days"
    em = lambda tf: ", ".join(f"EMA{n} {(c[tf].get('ema') or {}).get(n, [None])[-1]:+.2f}" for n in ("20", "50", "200")
                              if (c[tf].get("ema") or {}).get(n))
    lines = [f"# Blind card {job['i'] + 1} - {job['group_name']} (folder bundles/{job['sid']}/{job['i']}/)", "",
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
    if c.get("overlay"): lines += ["- Detector overlay (price-R): " + json.dumps(c["overlay"], separators=(",", ":"))[:600], ""]
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
                                overlay=None, n_show=len(bars), digits=2, hide_dates=True, strategy=c.get("detector", ""),
                                signal_t=bars[c[tf].get("sig", len(bars) - 1)][0] if bars else None, emas_in=c[tf].get("ema"))
            img.save(os.path.join(d, name + ".tmp"), "PNG"); os.replace(os.path.join(d, name + ".tmp"), os.path.join(d, name))
    except Exception as e:                             # the text + CSV still carry the card
        open(os.path.join(d, "charts_unavailable.txt"), "w").write(f"chart rendering failed: {e!r}\n")
    return d


# ------------------------------------------------------------------------------------------------------------- the consult
def call_claude(cfg: dict, prompt: str, s: dict, resume: str | None = None) -> dict:
    from live import advisor_runner as AR
    cmd = cfg["advisor"]["claude_cmd"]; sid = resume or str(uuid.uuid4())
    args = [cmd, "-p", "--output-format", "json", "--model", s["model"], "--effort", s["effort"]] + \
           (["--resume", sid] if resume else ["--session-id", sid]) + \
           ["--allowedTools", "Read,Glob,Grep", "--disallowedTools", DENIED, "--max-turns", str(int(s["max_turns"]))]
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=work_dir(cfg), env=AR.claude_env(cfg), input=prompt, capture_output=True, text=True, encoding="utf-8",
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


def _do_init(cfg, log, job, consult) -> None:
    sid, i, hid = job["sid"], job["i"], job["hid"]
    _set_status(cfg, sid, hid, state="running", started=_now(), attempts=job.get("attempts", 0) + 1)
    write_bundle(cfg, job)
    prompt = (f"Advise on the blind card in bundles/{sid}/{i}/ (read card.md, h4.png and d1.png there). "
              "Answer exactly in the shape your CLAUDE.md gives: VERDICT, CONFIDENCE, WHY, then the levers block.")
    s = settings(cfg)
    r = (consult or call_claude)(cfg, prompt, s, None)
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
    prompt = (f"Follow-up from the trader about the same blind card (bundles/{sid}/{i}/): {job['text']}\n\nAnswer briefly in plain words. "
              "If you change your recommended plan, end with a new ```levers``` block in the same schema; otherwise no block.")
    r = (consult or call_claude)(cfg, prompt, settings(cfg), st.get("session_id"))
    if not r.get("ok"): raise RuntimeError(r.get("error") or "consult failed")
    text = r.get("text") or ""; nv = None
    if BT.FENCE.search(text):
        ent = BT.add_recommendation(cfg, sid, hid, text, source="conversation")
        nv = ent["v"] if ent.get("valid") else f"rejected: {ent.get('reason')}"
    th = list(st.get("thread") or []) + [{"q": job["text"], "a": BT.FENCE.sub("(new plan below)", text).strip()[:4000], "at": _now(), "new_version": nv}]
    _set_status(cfg, sid, hid, thread=th, session_id=r.get("session_id") or st.get("session_id"))
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
            st = get_status(cfg, job["sid"], job["hid"])
            th = list(st.get("thread") or []) + [{"q": job["text"], "a": f"(the advisor could not answer: {str(e)[:200]})", "at": _now(), "new_version": None}]
            _set_status(cfg, job["sid"], job["hid"], thread=th)
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
        jobs = []
        for f in glob.glob(os.path.join(qdir(cfg), "*.json")):
            try: j = json.load(open(f))
            except (OSError, ValueError): continue
            if j.get("not_before", 0) > time.time(): continue
            jobs.append((0 if j.get("kind") == "followup" else 1, j.get("created", 0), j.get("i", 0), f, j))
        for _, _, _, f, j in sorted(jobs, key=lambda x: x[:3]):
            if len(_THREADS) >= cap: break
            run = f + ".running"
            try: os.replace(f, run)
            except OSError: continue
            if j["kind"] == "init": _ensure_role(cfg)
            t = threading.Thread(target=_work, args=(cfg, log, run, j, consult), daemon=True); _THREADS[run] = t; t.start()
