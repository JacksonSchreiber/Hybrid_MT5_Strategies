r"""
positions_review.py - trader 2026-10-04: an ON-DEMAND advisor that looks over ALL open positions and suggests management
changes (news / an event coming, open too long for its group, giving back R, a stop that could come up, bank part,
correlated positions stacking the same risk). ADVISORY ONLY - nothing is executed; every suggestion links to the
position page where the existing manual buttons are (SL -> BE, SL -> TP1, Close 50%, Close all).

Same pattern as brief_runner: the web app drops a request file, the advisor runner's tick picks it up and runs ONE
headless Claude Code consult in a thread (one review at a time). The consult runs in its OWN work folder next to the
live advisor's (so it loads THIS role file, not the signal-scorecard CLAUDE.md), reads-only (Read/Glob/Grep), with the
live advisor folder added read-only for the doctrine and library. Web allowed and encouraged (trader 2026-10-04): WebSearch /
WebFetch for recent news on the currencies/indices in play, plus the latest Context brief, attached when one exists.

Files: <root>/advisor/positions/<UTC>.json (the last KEEP reviews), status.json, reviews.log, requests/.
Bundle: <advisor work parent>/positions/bundle/{review.md, market-brief.md, <posid>-h4.png, <posid>-d1.png}.
"""
from __future__ import annotations
import glob, json, os, re, shutil, subprocess, sys, threading, time, uuid
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import charts
from live import brief_runner

KEEP = 10
ACTIONS = ("hold", "close", "raise_sl", "bank", "move_to_be", "other")
URGENCY = ("now", "today", "watch")
ACTION_TXT = {"hold": "HOLD", "close": "CLOSE", "raise_sl": "RAISE SL", "bank": "BANK", "move_to_be": "SL → BE", "other": "OTHER"}
DENIED = "Bash,Write,Edit,Task,Agent,NotebookEdit"

ROLE = """# Position reviewer (live, sighted, ADVISORY ONLY)

The trader asked you, on demand, to look over ALL of his open positions and say whether he should change anything. You
never execute anything - you can only read files. The trader decides and acts with the buttons on each position page.

## What you read
- `bundle/review.md` first: every open position with its numbers (R now, best R so far, time in trade, stop and targets in
  R, size, what the EA will still do on its own), the economic events for its currencies in the next 48 h, and the
  portfolio block (net currency exposure, measured correlations between the open positions, account headroom).
- The charts it lists: `bundle/<posid>-h4.png` and `-d1.png` (current bars, entry/SL/TP lines; read the ones you need).
- `bundle/market-brief.md` if present: the latest Context brief (web-researched, DATED - treat anything older than about a
  day as stale). You also have WebSearch/WebFetch and the trader encourages you to use them: check recent news, central-bank
  comments and anything that could change a position (public sources only, never paywalled; name the source and its time).
- The live doctrine: `{advisor}/CLAUDE.md` (the signal advisor's role file - read it ONLY for the management rules the EA
  enforces: bank at +1R, BE floor, ratchet, staged add, pyramid, the commodity-bloc rules; do NOT answer in its scorecard
  format) and `{advisor}/library/INDEX.md` for evidence-backed notes.

## What to look for
- An event in the next 48 h the position is exposed to (high-impact V/W class especially) - size and stop vs the event.
- A position open far longer than its group usually needs (the group line says how the group behaves), stalled, or giving
  back a lot of its best R.
- A stop that could come up under the doctrine floors, or a reason to bank part (structure broken, target unlikely).
- Correlated positions stacking the same risk (the portfolio block's numbers are authoritative - don't redo the maths).
- The EA already manages banks, adds, the pyramid and the bloc rules automatically: the DEFAULT is HOLD. Suggest a change
  only with a concrete reason you can point at.

## Answer format (exactly)
First a short markdown summary (at most 8 lines): the portfolio-level picture (event and correlation risk) and the
positions that need attention. Then ONE fenced block:

```json
{{"positions": [{{"key": "<key exactly as in review.md>", "action": "hold|close|raise_sl|bank|move_to_be|other",
  "params": {{}}, "urgency": "now|today|watch", "reason": "1-2 lines, concrete"}}],
 "portfolio": "one short paragraph"}}
```

Every position in review.md appears exactly once. params: raise_sl {{"to_R": 0.5}} (R from entry, + = in the trade's
favour) or {{"to_price": 1.2345}}; bank {{"pct": 50}}; close {{}}; move_to_be {{}}; other {{"note": "..."}}; hold {{}}.
In the reason, say which button does it where one exists (SL → BE, SL → TP1, Close 50%, Close all) - anything else is
"by hand in MT5" (the EA logs it as an external edit). urgency: now = act before the next H4 bar, today = within the day,
watch = nothing to do yet.
"""


def review_dir(cfg) -> str: return os.path.join(cfg["root"], "advisor", "positions")
def work_dir(cfg) -> str: return os.path.abspath(os.path.join(cfg["advisor"]["live_dir"], "..", "positions"))
def settings(cfg) -> dict:
    pr = cfg.get("positions_review") or {}; ms = (cfg.get("advisor") or {}).get("models") or [{}]
    return {"model": pr.get("model") or ms[0].get("model") or (cfg.get("advisor") or {}).get("model") or "opus",
            "effort": pr.get("effort") or "medium", "timeout_s": int(pr.get("timeout_s") or 900), "telegram": pr.get("telegram", True),
            "events_h": int(pr.get("events_h") or 48)}


# ----------------------------------------------------------------------------- per-position facts
def _f(x, d=0.0):
    try: return float(x)
    except (TypeError, ValueError): return d

def _R(p) -> float: return abs(_f(p.get("entry")) - _f(p.get("sl_risk_basis") or p.get("sl_live")))
def _up(p) -> bool: return str(p.get("direction", "")).upper().startswith("B")
def _inR(p, price) -> float | None:
    R = _R(p)
    if not R or price in (None, "", 0): return None
    v = (_f(price) - _f(p.get("entry"))) / R
    return v if _up(p) else -v

def key_of(p) -> str: return f"{p['symbol']}-{p['posid']}"

def _bars(p, tf: str) -> tuple[list[list], bool]:
    """(bars, fresh) - the terminal feed when available, else the signal's embedded bars (as of the signal: STALE)."""
    try:
        from live import mt5feed
        b = mt5feed.bars(p["symbol"], tf, 500)
        if b: return b, True
    except Exception: pass
    sig = None
    try: sig = C.signal(_CFG, f"{p['symbol']}-{p.get('signal_id')}") if p.get("signal_id") else None
    except Exception: sig = None
    return ((sig or {}).get(f"bars_{tf}") or []), False

def _excursion(p, h4: list[list]) -> tuple[float | None, float | None]:
    """best / worst R reached since the entry bar, from H4 highs/lows (broker-clock epochs on both sides)."""
    t0 = int(_f(p.get("opened_at"))); R = _R(p)
    if not t0 or not R or not h4: return None, None
    t0 -= t0 % 14400
    xs = [b for b in h4 if int(b[0]) >= t0]
    if not xs: return None, None
    e = _f(p.get("entry"))
    if _up(p): return (max(b[2] for b in xs) - e) / R, (min(b[3] for b in xs) - e) / R
    return (e - min(b[3] for b in xs)) / R, (e - max(b[2] for b in xs)) / R

def _hours(p) -> str:
    try:
        from live import exposure as EXP
        return EXP._hours_in_trade(p, EXP._server_offset_h(p["symbol"]))
    except Exception:
        b = p.get("bars_open"); return f"~{int(b) * 4}h ({b} H4 bars)" if b is not None else "?"

STAGED = {1: "TRIAL: the staged add is still to come at the bar-6 close (add due {due})", 4: "staged add HELD until +1R",
          2: "staged add done", 3: "staged add skipped"}
BLOC = {0: "commodity-bloc 24h check pending (closes at the 6th H4 bar unless +1.5R)", 1: "bloc: cut", 2: "bloc: passed the 24h check",
        3: "bloc: rules not applied to this trade", 5: "bloc: waiting"}

def _ea_pending(p) -> str:
    out = []
    ss = int(_f(p.get("staged_state"), -1)); tr = int(_f(p.get("tranche")))
    if tr == 1 and ss in STAGED: out.append(STAGED[ss].format(due=p.get("add_due") or "?"))
    if tr >= 2: out.append({2: "this is the staged add tranche", 3: "this is the pyramid add (+1.5R)", 4: "this is the improving add (day 2)"}.get(tr, f"tranche {tr}"))
    out.append("banked" if p.get("banked") else "not banked yet (the EA banks at +1R / TP1)")
    if p.get("ratcheted"): out.append("stop ratcheted to TP1")
    if int(_f(p.get("short_raise"))) == 1: out.append("shorts stop raised to +0.25R")
    if p.get("bloc"): out.append(BLOC.get(int(_f(p.get("bloc_state"), -1)), f"bloc state {p.get('bloc_state')}") + (" · stops locked at +1R" if p.get("bloc_lock") else ""))
    btn = [x for x, ok in (("SL → BE", p.get("be_placeable")), ("SL → TP1", p.get("ratchet_placeable"))) if ok]
    out.append("buttons available now: " + ", ".join(btn + ["Close 50%", "Close all"]))
    return "; ".join(out)

def _verdict_word(p) -> str:
    try:
        from live import advisor_runner as AR
        for m in AR.models(_CFG):
            rec = AR.load_record(_CFG, f"{p['symbol']}-{p.get('signal_id')}", m["id"])
            oks = [c for c in (rec or {}).get("consults", []) if c.get("ok")]
            if oks:
                w = AR.verdict_word(oks[-1].get("text")); return f"{w} ({m['id']})" if w else ""
    except Exception: pass
    return ""

def _group_line(p) -> str:
    try:
        from live import symbol_groups as SG
        g = SG.group_of(p["symbol"])
        if g: return f"{g['name']} — {g['strategy']}" + (f" Advice: {g['advice']}" if g.get("advice") else "")
    except Exception: pass
    return "(no group)"

def _events(p, hours: int) -> list[str]:
    try: evs = C.events_for(_CFG, p["symbol"], C.now_utc(), days=max(1, (hours + 23) // 24), cap=12)
    except Exception: return ["(event feed unavailable)"]
    evs = [e for e in evs if e["hours_until"] <= hours]
    return [f"in {e['hours_until']:.0f} h · {e['ccy']} {e['name']} [{e['cls']} {e['label']}]{' · BINDING' if e.get('binding') else ''}" for e in evs] or [f"none in the next {hours} h"]


# ----------------------------------------------------------------------------- bundle
_CFG: dict = {}

def build_bundle(cfg: dict, positions: list[dict]) -> tuple[str, list[dict]]:
    """writes the bundle; returns (bundle dir, per-position snapshot rows)."""
    global _CFG; _CFG = cfg
    s = settings(cfg); bdir = os.path.join(work_dir(cfg), "bundle")
    shutil.rmtree(bdir, ignore_errors=True); os.makedirs(bdir, exist_ok=True)
    now = C.now_utc(); lines = [f"# Open positions review — {now.strftime('%A %d %B %Y, %H:%M UTC')}", "",
                                f"{len(positions)} open position(s). Keys below are exactly what your JSON must use.", ""]
    snap = []
    for p in positions:
        k = key_of(p); h4, fresh4 = _bars(p, "h4"); d1, fresh1 = _bars(p, "d1")
        best, worst = _excursion(p, h4) if fresh4 else (None, None)      # embedded bars end at the signal: no excursion from them
        lv = {"entry": _f(p.get("entry")) or None, "sl": _f(p.get("sl_live")) or None, "tp1": _f(p.get("tp1")) or None, "tp2": _f(p.get("tp_live") or p.get("tp2")) or None}
        dg = max(2, min(5, len(str(p.get("entry")).split(".")[1]) if "." in str(p.get("entry")) else 2))
        t0 = int(_f(p.get("opened_at"))); mark = [(t0 - t0 % 14400, "entry")] if t0 else None
        head = f"{p['symbol']} {p.get('strategy')} {p.get('direction')} pos {p['posid']} · open {C.r_fmt(p.get('open_r'))}"
        n4 = n1 = 0
        try:
            charts.render(h4, title="H4  " + head + ("" if fresh4 else "   (STALE: bars as of the signal)"), levels=lv, n_show=charts.H4_BARS, digits=dg, marks=mark).save(os.path.join(bdir, f"{p['posid']}-h4.png"))
            charts.render(d1, title="D1  " + head + ("   (last candle still forming)" if fresh1 else "   (STALE: bars as of the signal)"), levels=lv, n_show=charts.D1_BARS, digits=dg).save(os.path.join(bdir, f"{p['posid']}-d1.png"))
            n4, n1 = min(len(h4), charts.H4_BARS), min(len(d1), charts.D1_BARS)
        except Exception as e: lines.append(f"(chart render failed for {k}: {e!r})")
        R = _R(p); vw = _verdict_word(p)
        size = (f"{_f(p.get('lots_live')):g} lots of {_f(p.get('full_lots') or p.get('lots_init')):g} full"
                f" (risk at entry {_f(p.get('risk_pct_effective')) * 100:.2f}%)")
        row = {"key": k, "symbol": p["symbol"], "posid": p["posid"], "direction": p.get("direction"), "strategy": p.get("strategy"),
               "open_r": p.get("open_r"), "banked_r": p.get("banked_r"), "bars_open": p.get("bars_open"), "best_r": best,
               "sl_r": _inR(p, p.get("sl_live")), "hours": _hours(p)}
        snap.append(row)
        lines += [f"## {k}", f"- **{p['symbol']} {p.get('direction')}** · {p.get('strategy')} · signal #{p.get('signal_id')} [{p.get('decision_class', '')}]"
                  + (f" · advisor at entry: {vw}" if vw else ""),
                  f"- group: {_group_line(p)}",
                  f"- R now (open) **{C.r_fmt(p.get('open_r'))}** · banked {C.r_fmt(p.get('banked_r'))} · if closed now {C.r_fmt(p.get('closenow_r'))}"
                  + (f" · best since entry {best:+.2f}R · worst {worst:+.2f}R" if best is not None else " · best/worst since entry: n/a (no live bars)"),
                  f"- time in trade: {row['hours']}",
                  f"- levels: entry {p.get('entry')} · original SL {p.get('sl_risk_basis')} (1R = {R:.{dg}f}) · SL now {p.get('sl_live')}"
                  + (f" ({row['sl_r']:+.2f}R)" if row["sl_r"] is not None else "") + f" · TP1 {p.get('tp1') or '-'}"
                  + (f" ({_inR(p, p.get('tp1')):+.2f}R)" if _inR(p, p.get('tp1')) is not None else "") + f" · TP2/TP now {p.get('tp_live') or '-'}"
                  + (f" ({_inR(p, p.get('tp_live')):+.2f}R)" if _inR(p, p.get('tp_live')) is not None else ""),
                  f"- size: {size}",
                  f"- the EA, on its own: {_ea_pending(p)}",
                  f"- charts: `bundle/{p['posid']}-h4.png` ({n4} bars{'' if fresh4 else ', STALE'}), `bundle/{p['posid']}-d1.png` ({n1} bars{'' if fresh1 else ', STALE'})",
                  f"- events (next {s['events_h']} h): " + "; ".join(_events(p, s["events_h"])), ""]
    lines += ["## Portfolio", portfolio_block(cfg, positions), ""]
    b = brief_runner.latest_brief(cfg, 2.0)
    if b:
        shutil.copyfile(b["path"], os.path.join(bdir, "market-brief.md"))
        lines.append(f"News: `bundle/market-brief.md` = the Context brief generated {b['t'].strftime('%Y-%m-%d %H:%M UTC')} "
                     f"({(now - b['t']).total_seconds() / 3600:.0f} h ago){' — FLAGGED wording: ' + ', '.join(b['flags']) if b.get('flags') else ''}.")
    else:
        lines.append("News: no Context brief from the last 2 days — check the web yourself for recent news on these positions.")
    C.atomic_write_text(os.path.join(bdir, "review.md"), "\n".join(lines) + "\n")
    return bdir, snap


def portfolio_block(cfg: dict, positions: list[dict]) -> str:
    """server-computed: net currency/cluster exposure, measured correlations between the open positions, account headroom."""
    from live import exposure as EXP
    from live import correlation as CORR
    out = []
    entries = []
    for p in positions:
        rp = _f(p.get("risk_pct_effective"))
        if rp: entries += EXP.legs(p["symbol"], p.get("direction", ""), rp * 100)
    out.append(f"- net by currency / cluster (risk % at entry, signed): {EXP.net_line(entries)}")
    unb = sum(CORR._unbanked_pct(p) for p in positions)
    out.append(f"- unbanked risk still on the table: {unb:.2f}% of the account · open R total {sum(_f(p.get('open_r')) for p in positions):+.2f}"
               f" · banked R total {sum(_f(p.get('banked_r')) for p in positions):+.2f}")
    closes = {}; pairs = []
    syms = sorted({p["symbol"] for p in positions})
    for s_ in syms:
        try: closes[s_] = CORR._closes(s_)
        except Exception: closes[s_] = {}
    for i, a in enumerate(positions):
        for b in positions[i + 1:]:
            if a["symbol"] == b["symbol"]:
                if a.get("direction") == b.get("direction"): pairs.append((1.0, a, b, "same symbol, same direction"))
                continue
            c = CORR.corr60(closes.get(a["symbol"], {}), closes.get(b["symbol"], {}))
            if c is None: continue
            sg = c if a.get("direction") == b.get("direction") else -c
            if abs(sg) >= CORR.W_MID: pairs.append((sg, a, b, f"corr {c:+.2f} → signed {sg:+.2f}"))
    pairs.sort(key=lambda x: -x[0])
    if pairs:
        out.append("- measured 60-day correlation between open positions (signed by direction; ≥ +0.6 = the same bet, ≤ −0.6 = a hedge):")
        out += [f"    - {key_of(a)} {a.get('direction')} × {key_of(b)} {b.get('direction')}: {txt}" for sg, a, b, txt in pairs[:15]]
    else:
        out.append("- measured correlation between open positions: no pair at |signed| ≥ 0.3 (or the feed is unavailable)")
    hb = EXP.newest_heartbeat(cfg)
    if hb:
        f = hb.get("ftmo") or {}; eq = _f(hb.get("equity")); agg = _f(hb.get("aggregate_risk_to_stop"))
        out.append(f"- account: aggregate risk-to-stop {agg:,.0f} ({(agg / eq * 100) if eq else 0:.2f}% of equity {eq:,.0f})"
                   + (f" · daily headroom {f.get('headroom_daily', 0):,.0f} · max-loss headroom {f.get('headroom_max', 0):,.0f}" if f.get("initial_balance") else ""))
    else:
        out.append("- account: no heartbeat — headroom unknown")
    return "\n".join(out)


# ----------------------------------------------------------------------------- consult
def call_claude(cfg: dict, prompt: str, cwd: str, s: dict) -> dict:
    from live import advisor_runner as AR
    cmd = cfg["advisor"]["claude_cmd"]; sid = str(uuid.uuid4())
    args = [cmd, "-p", "--output-format", "json", "--model", s["model"], "--effort", s["effort"], "--session-id", sid,
            "--allowedTools", "Read,Glob,Grep,WebSearch,WebFetch", "--disallowedTools", DENIED, "--max-turns", "60",
            "--add-dir", os.path.abspath(cfg["advisor"]["live_dir"])]
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=cwd, env=AR.claude_env(cfg), input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=s["timeout_s"])         # prompt via stdin (cmd /c truncates multi-line args)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {s['timeout_s']}s", "elapsed_s": round(time.time() - t0, 1), "session_id": sid}
    el = round(time.time() - t0, 1)
    try: j = json.loads(p.stdout)
    except ValueError: return {"ok": False, "error": f"non-json output (rc={p.returncode}): {(p.stderr or p.stdout)[:400]}", "elapsed_s": el, "session_id": sid}
    if j.get("is_error") or p.returncode != 0: return {"ok": False, "error": str(j.get("result") or p.stderr)[:600], "elapsed_s": el, "session_id": sid}
    return {"ok": True, "text": j.get("result", ""), "elapsed_s": el, "session_id": j.get("session_id", sid), "turns": j.get("num_turns"), "cost_usd": j.get("total_cost_usd")}


JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

def parse(text: str, keys: list[str]) -> tuple[str, list[dict], str, list[str]]:
    """(summary markdown, recommendations [one per key, in bundle order], portfolio paragraph, warnings)."""
    warn = []; data = None; summary = text or ""
    for m in reversed(list(JSON_RE.finditer(text or ""))):
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict) and isinstance(d.get("positions"), list): data = d; summary = (text[:m.start()]).strip(); break
        except ValueError: continue
    if data is None: warn.append("no valid JSON block found - showing the advisor's text only")
    recs = {}
    for r in (data or {}).get("positions") or []:
        if not isinstance(r, dict): continue
        k = str(r.get("key", ""))
        if k not in keys: warn.append(f"unknown key in the answer: {k!r}"); continue
        a = str(r.get("action", "")).lower().strip()
        if a not in ACTIONS: warn.append(f"{k}: unknown action {a!r} -> other"); a = "other"
        u = str(r.get("urgency", "")).lower().strip()
        if u not in URGENCY: u = "watch"
        prm = r.get("params") if isinstance(r.get("params"), dict) else {}
        recs[k] = {"key": k, "action": a, "urgency": u, "params": {str(x): prm[x] for x in list(prm)[:6]}, "reason": str(r.get("reason", ""))[:500]}
    out = []
    for k in keys:
        if k in recs: out.append(recs[k])
        else:
            out.append({"key": k, "action": "other", "urgency": "watch", "params": {}, "reason": "(not covered by the advisor's answer)", "missing": True})
            if data is not None: warn.append(f"{k}: not in the answer")
    return summary, out, str((data or {}).get("portfolio", ""))[:1500], warn


def params_text(r: dict) -> str:
    p = r.get("params") or {}; a = r.get("action")
    if a == "raise_sl":
        if "to_R" in p:
            try: return f"to {float(p['to_R']):+.2f}R"
            except (TypeError, ValueError): pass
        if "to_price" in p: return f"to {p['to_price']}"
    if a == "bank" and "pct" in p: return f"{p['pct']}%"
    if a == "other" and p.get("note"): return str(p["note"])[:120]
    return ""


# ----------------------------------------------------------------------------- generate / store
def generate(cfg: dict, log, requested_by: str = "web", consult=None) -> dict:
    s = settings(cfg); rd = review_dir(cfg); os.makedirs(rd, exist_ok=True)
    ts = C.now_utc().strftime("%Y%m%dT%H%M%SZ")
    C.atomic_write_json(os.path.join(rd, "status.json"), {"state": "running", "started": C.now_iso(), "model": s["model"], "by": requested_by})
    res = {"ts": ts, "by": requested_by, "model": s["model"], "effort": s["effort"], "created": C.now_iso()}
    try:
        positions = C.list_positions(cfg)
        res["n_positions"] = len(positions)
        if not positions:
            res.update(ok=True, summary="No open positions — nothing to review.", recs=[], portfolio="", positions=[], warnings=[])
        else:
            wd = work_dir(cfg); os.makedirs(os.path.join(wd, ".claude"), exist_ok=True)
            C.atomic_write_text(os.path.join(wd, "CLAUDE.md"), ROLE.format(advisor=os.path.abspath(cfg["advisor"]["live_dir"]).replace("\\", "/")))
            C.atomic_write_text(os.path.join(wd, ".claude", "settings.json"), json.dumps({"effortLevel": s["effort"], "permissions": {"allow": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"], "deny": DENIED.split(",")}}))
            bdir, snap = build_bundle(cfg, positions)
            prompt = (f"Review all {len(positions)} open positions as of {C.now_iso()}. Read bundle/review.md, then the charts and the brief it lists, "
                      f"and the doctrine as CLAUDE.md says. Answer exactly in CLAUDE.md's format: the short summary, then ONE ```json block with every key.")
            r = (consult or call_claude)(cfg, prompt, wd, s)
            res.update({k: v for k, v in r.items() if k in ("elapsed_s", "session_id", "turns", "cost_usd", "error")})
            res["positions"] = snap
            if r.get("ok"):
                summary, recs, port, warn = parse(r.get("text", ""), [x["key"] for x in snap])
                res.update(ok=True, summary=summary, recs=recs, portfolio=port, warnings=warn, raw=r.get("text", ""))
            else:
                res.update(ok=False, recs=[], summary="", portfolio="", warnings=[])
    except Exception as e:
        res.update(ok=False, error=f"{e!r}", recs=[], summary="", portfolio="", warnings=[])
    C.atomic_write_json(os.path.join(rd, f"{ts}.json"), res)
    for old in sorted(glob.glob(os.path.join(rd, "2*.json")))[:-KEEP]:
        try: os.remove(old)
        except OSError: pass
    cnt = counts(res)
    C.append_line(os.path.join(rd, "reviews.log"), " | ".join([ts, res["model"] + "/" + res["effort"], f"{res.get('elapsed_s', 0)}s", f"positions={res.get('n_positions', 0)}",
                  f"now={cnt['now']} today={cnt['today']} change={cnt['change']}", f"by={requested_by}", ("ERROR " + str(res.get("error"))) if not res.get("ok") else "ok"]))
    C.atomic_write_json(os.path.join(rd, "status.json"), {"state": "idle" if res.get("ok") else "error", "last": ts, "finished": C.now_iso(),
                                                          "error": None if res.get("ok") else res.get("error")})
    log(f"positions review: {'ok' if res.get('ok') else 'ERROR ' + str(res.get('error'))} in {res.get('elapsed_s', 0)}s, {res.get('n_positions', 0)} positions, {cnt}")
    if res.get("ok") and s["telegram"] and res.get("n_positions"):
        try:
            base = (cfg.get("web") or {}).get("base_url", "").rstrip("/")
            C.telegram_send(cfg, f"Positions review ready: {cnt['now']} act now, {cnt['today']} today, {cnt['change']} change(s) suggested of "
                                 f"{res['n_positions']} positions. {base}/review", log)
        except Exception: pass
    return res


def counts(res: dict) -> dict:
    recs = res.get("recs") or []
    ch = [r for r in recs if r.get("action") != "hold" and not r.get("missing")]
    return {"now": sum(1 for r in ch if r.get("urgency") == "now"), "today": sum(1 for r in ch if r.get("urgency") == "today"), "change": len(ch)}

def list_reviews(cfg: dict) -> list[str]:
    return [os.path.basename(p)[:-5] for p in sorted(glob.glob(os.path.join(review_dir(cfg), "2*.json")), reverse=True)]

def load_review(cfg: dict, ts: str | None = None) -> dict | None:
    names = list_reviews(cfg)
    if not names: return None
    ts = ts if ts in names else names[0]
    return C.load_json(os.path.join(review_dir(cfg), f"{ts}.json"))

def status(cfg: dict) -> dict:
    st = C.load_json(os.path.join(review_dir(cfg), "status.json"), {}) or {}
    if glob.glob(os.path.join(review_dir(cfg), "requests", "*.json")) and st.get("state") != "running": st = dict(st, state="queued")
    return st


# ----------------------------------------------------------------------------- request queue (web app asks, the advisor runner reviews)
def request(cfg: dict, by: str = "web") -> bool:
    """False when a review is already queued or running (one at a time)."""
    if status(cfg).get("state") in ("queued", "running"): return False
    C.atomic_write_json(os.path.join(review_dir(cfg), "requests", f"{C.now_utc().strftime('%Y%m%dT%H%M%S')}.json"), {"by": by, "ts": C.now_iso()})
    return True

_worker: threading.Thread | None = None
def poll_requests(cfg: dict, log, consult=None) -> None:
    global _worker
    reqs = sorted(glob.glob(os.path.join(review_dir(cfg), "requests", "*.json")))
    if not reqs or (_worker and _worker.is_alive()): return
    by = (C.load_json(reqs[-1]) or {}).get("by", "web")
    for r in reqs:
        try: os.remove(r)
        except OSError: pass
    _worker = threading.Thread(target=generate, args=(cfg, log, by, consult), daemon=True); _worker.start()

def recover(cfg: dict) -> None:
    """a review that was running when the runner stopped is dead: mark it, so the page does not spin forever."""
    st = C.load_json(os.path.join(review_dir(cfg), "status.json"), {}) or {}
    if st.get("state") == "running":
        C.atomic_write_json(os.path.join(review_dir(cfg), "status.json"), dict(st, state="error", error="interrupted: the advisor runner restarted during the review"))

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); ap.add_argument("--bundle-only", action="store_true"); a = ap.parse_args()
    cfg = C.load_config(a.config); log = C.Log("posreview", cfg["logs_dir"])
    if a.bundle_only:
        global _CFG; _CFG = cfg; print(build_bundle(cfg, C.list_positions(cfg))[0]); return
    r = generate(cfg, log, "cli"); print(json.dumps({k: v for k, v in r.items() if k != "raw"}, indent=1)[:4000]); sys.exit(0 if r.get("ok") else 1)

if __name__ == "__main__": main()
