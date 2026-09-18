r"""
advisor_runner.py - live advisor auto-consult (spec §6, CLAUDE.live.md).

For every signal that reaches status "open" it: renders h4/d1 (charts.py), writes a SIGHTED setup.md, runs ONE
headless Claude Code consult (subscription OAuth token, --model/--effort from config, deterministic session id so a
runner restart can still --resume), stores the scorecard in <root>/advisor/verdicts/<key>.json for the web app, and
makes sure a verdicts.live.log line exists. Replies dropped by the web app in <root>/advisor/replies/ are sent as
follow-up turns of the same session. One consult at a time. The advisor never touches the queue (it has no Bash).

Layout the consult runs in (cwd = advisor.live_dir):
   <X>\quick-reference.html          (the role file reads ../quick-reference.html)
   <X>\advisor\CLAUDE.md             (= training/advisor/CLAUDE.live.md, copied by provisioning/deploy_live.sh)
   <X>\advisor\library\  notes.live.md  verdicts.live.log  .claude\settings.json  bundles\<key>\{setup.md,h4.png,d1.png}
"""
from __future__ import annotations
import glob, json, os, re, subprocess, sys, time, uuid, threading
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import charts
from live import overlays as OV
from live import brief_runner

SESSION_NS = uuid.UUID("5f0a3c1e-9b7d-4c1a-8f2e-2d3b4a5c6d7e")
ALLOWED = "Read,Glob,Grep,Write(notes.live.md),Edit(notes.live.md)"   # the RUNNER writes verdicts.live.log from the LOG: line
DISALLOWED = "Bash,WebFetch,WebSearch,Task,Agent,NotebookEdit"

def session_id(key: str) -> str: return str(uuid.uuid5(SESSION_NS, key))

# ----------------------------------------------------------------------------- bundle
def protocol_line(sig: dict) -> str:
    cls = sig.get("decision_class", "")
    if cls == "TAKE":
        return "PROTOCOL: TAKE — TREND regime · gate-class strategy (SweepMSS/DeepFib), both directions · skip legal only for event / W / correlation"
    return "PROTOCOL: DISCRETION — not a gate-class TREND setup (non-gate strategy, or CHOP / blank / warm-up regime; every TrendCont) · trader's read"

def regime_line(sig: dict) -> str:
    rg = sig.get("regime") or {}; tag = rg.get("tag") or ""; wt = str(rg.get("with_trend", ""))
    if not tag: return "REGIME: (warming up / unavailable)"
    if tag == "CHOP": return "REGIME: CHOP"
    rel = "WITH-TREND" if wt == "1" else ("COUNTER-TREND" if wt == "0" else "relation n/a")
    return f"REGIME: {tag} · signal {rel} (D1 200-EMA/ADX)"

def events_block(sig: dict, cfg: dict) -> str:
    sig_t = C.parse_iso(sig.get("signal_time")); horizon = sig_t + timedelta(days=14) if sig_t else None
    lines = []
    for e in sig.get("events") or []:
        t = C.parse_iso(e.get("t_utc")); hrs = e.get("hours_until")
        when = f"{t.strftime('%a %d %b %H:%M')} UTC" if t else e.get("t_utc", "?")
        rel = f"in {hrs:.0f}h" if isinstance(hrs, (int, float)) and hrs < 48 else (f"in {hrs/24:.1f}d" if isinstance(hrs, (int, float)) else "")
        lab = e.get("label") or ""; bind = "  **BINDING**" if e.get("binding") else ""
        lines.append(f"  - {when} ({rel})  {e.get('ccy')} {e.get('name')}  [{e.get('cls')}{(': ' + lab) if lab else ''}]{bind}")
    if not lines: lines.append("  - (no notable events for this symbol inside the window)")
    _, cov_end = C.load_events(cfg)
    if cov_end and horizon and cov_end < horizon:
        lines.append(f"  - CALENDAR COVERAGE ENDS {cov_end.strftime('%Y-%m-%d')} — events after that date are UNKNOWN, not absent. Treat the news check as '?' beyond it.")
    eg = sig.get("election_gate") or {}
    if eg.get("hit"): lines.append(f"  - ELECTION GATE HIT: {eg.get('event')} — the EA auto-skips this signal (code 2).")
    return "\n".join(lines)

def swing_block(sig: dict) -> str:
    """Recent swing structure: the EA's own H4 fractal swings — the SAME set the chart marks
    (overlays.swings mirrors DrawSwingMarkers line for line, and charts.py draws it from the same
    call), so the table and the picture can never disagree. Trader-reported defect 2026-09-17: the
    advisor was estimating swing levels off the pixels; these are the values, not an estimate."""
    bars = charts.signal_bars(sig, "h4")
    if len(bars) < 12:
        return "- **Recent swing structure:** (unavailable — no H4 bars for this signal; do not estimate swing levels off the image, say the table is missing)"
    tbl = OV.swing_table(bars, 5); dg = charts._digits(sig)
    def row(xs): return " · ".join(f"{s['p']:.{dg}f} ({s['bars_ago']} bars ago)" for s in xs) or "(none in the window)"
    idx = {b[0]: i for i, b in enumerate(bars)}; st = charts._epoch(sig.get("signal_time"))
    ago = (len(bars) - 1 - idx[st]) if st in idx else None
    anchor = (f"the signal bar is {ago} bar{'' if ago == 1 else 's'} back from that edge" if ago is not None
              else "the signal bar is outside the drawn bar window")
    return ("- **Recent swing structure (EA values — authoritative over your read of the image):**\n"
            f"    - swing highs, newest first: {row(tbl['hi'])}\n"
            f"    - swing lows, newest first: {row(tbl['lo'])}\n"
            "    - 5-bar fractal (2 left / 2 right) on H4 over a 14-day rolling window — the same set the chart marks. "
            f"Bars ago counts back from the newest bar on the chart (its right edge); {anchor}. The newest entry may still be "
            "confirmed against the unfinished current bar. No sweep column: the EA tracks no per-swing pool status.")

def build_setup_md(sig: dict, cfg: dict) -> str:
    lv = sig.get("levels") or {}; rr = sig.get("rr") or {}; sz = sig.get("sizing") or {}; ex = sig.get("exposure") or {}
    sig_t = C.parse_iso(sig.get("signal_time")); dl = C.parse_iso(sig.get("deadline"))
    pos_lines = [f"{p.get('strategy')} {p.get('direction')} #{p.get('signal_id')} open {C.r_fmt(p.get('open_r'))}" for p in ex.get("open_positions") or []]
    exposure = (f"aggregate risk-to-stop {ex.get('aggregate_risk_to_stop', 0):.0f} (account ccy) across {ex.get('account_positions', 0)} position(s)"
                + (": " + "; ".join(pos_lines) if pos_lines else ""))
    entry_note = " (STOP entry - breakout order)" if lv.get("stop_entry") else ""
    tp2 = f", TP2 {lv.get('tp2')}" if lv.get("tp2") else ""
    r_tp1 = rr.get("tp1"); r_run = rr.get("runner") or rr.get("detector")
    rules = C.symbol_rules(cfg, sig.get("symbol", "")); rules_line = C.symbol_rules_line(cfg, sig.get("symbol", ""))
    klass = rules.get("class") or sig.get("asset_class")          # config wins: the EA's classifier has no crypto branch
    return f"""# LIVE SETUP — {sig.get('strategy')} {sig.get('direction')} — {sig.get('symbol')} #{sig.get('signal_id')}
_Sighted live consult (CLAUDE.live.md). Judge from the charts, the guide and the calendar below._

- **Symbol / class:** {sig.get('symbol')} ({klass})
{('- **Symbol rules (journaled):** ' + rules_line) if rules_line else '- **Symbol rules:** standard session rules apply (no per-symbol entry in config/symbol_rules.json)'}
- **Time:** {sig.get('sigtime_text', '')} — signal bar {sig.get('signal_time')}, presented {sig.get('published_at')} · session: {sig.get('session')}
- **Decision deadline:** {dl.strftime('%a %d %b %H:%M UTC') if dl else '-'} ({sig.get('max_age_bars')} H4 bars of silence = expired) · delays so far: {sig.get('delay_count', 0)}
- **{regime_line(sig)}**
- **{protocol_line(sig)}**
- **Strategy read:** {sig.get('strategy_text', '')}
- **Proposed levels:** entry {lv.get('entry')}{entry_note}, SL {lv.get('sl')}, TP1 {lv.get('tp1')}{tp2} (partial {lv.get('partial_fraction', 0.5):.0%} at TP1)
- **Risk geometry:** SL 1.0R · TP1 {r_tp1}R · TP2 {r_run}R (floor {rr.get('floor')}R; detector already sized to the gate risk and cleared the R:R floor)
{swing_block(sig)}
- **Sizing:** {sz.get('lots_line', '')} · risk multiplier in effect {sz.get('risk_mult_applied', 1.0)} → effective {float(sz.get('risk_pct_effective', 0.01))*100:.2f}%
- **Exposure:** {exposure}
- **Kill switch:** trading {'ENABLED' if sig.get('trading_enabled') else 'DISABLED (approve would be refused)'}
- **Events (next 14 d, {'/'.join(sorted(C.symbol_ccys(sig.get('symbol', '')) - {'All'}))}):**
{events_block(sig, cfg)}

Images: `d1.png` (daily context, regime) · `h4.png` (H4 setup with the detector's overlays and the tri-EMA).
"""

def write_bundle(sig: dict, cfg: dict) -> str:
    live_dir = cfg["advisor"]["live_dir"]; key = sig["signal_key"]
    bdir = os.path.join(live_dir, "bundles", key); os.makedirs(bdir, exist_ok=True)
    h4, d1 = charts.render_signal(sig, os.path.join(cfg["root"], "web", "charts"), fresh=True)
    import shutil
    shutil.copyfile(h4, os.path.join(bdir, "h4.png")); shutil.copyfile(d1, os.path.join(bdir, "d1.png"))
    brief_note = ""
    b = brief_runner.latest_brief(cfg, 2.0)
    mb = os.path.join(bdir, "market-brief.md")
    if b:
        shutil.copyfile(b["path"], mb); brief_note = f"\n- **Context brief attached:** `market-brief.md` (generated {b['t'].strftime('%Y-%m-%d %H:%M UTC')}{'; FLAGGED directional wording: ' + ', '.join(b['flags']) if b['flags'] else ''}) - CLAUDE.live.md §Context brief governs its weight."
    elif os.path.exists(mb): os.remove(mb)
    C.atomic_write_text(os.path.join(bdir, "setup.md"), build_setup_md(sig, cfg) + brief_note + "\n")
    return bdir

# ----------------------------------------------------------------------------- claude
def claude_env(cfg: dict) -> dict:
    # a child claude inherits any CLAUDE_* variables of a parent Claude Code session and hangs on its messaging
    # socket (found 2026-09-15). Start from a scrubbed environment.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE")}
    a = cfg["advisor"]
    if a.get("token_file"): env["CLAUDE_CODE_OAUTH_TOKEN"] = C.read_secret(a["token_file"])
    if a.get("node_path"): env["PATH"] = a["node_path"] + os.pathsep + env.get("PATH", "")
    return env

def run_claude(cfg: dict, prompt: str, *, sid: str, resume: bool, log) -> dict:
    a = cfg["advisor"]; cmd = a["claude_cmd"]
    args = [cmd, "-p", "--output-format", "json", "--model", a["model"], "--effort", a["effort"],
            "--allowedTools", ALLOWED, "--disallowedTools", DISALLOWED, "--max-turns", "40"]
    args += ["--resume", sid] if resume else ["--session-id", sid]
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=a["live_dir"], env=claude_env(cfg), input=prompt, capture_output=True, text=True, timeout=a["timeout_s"], encoding="utf-8", errors="replace")   # prompt via stdin (cmd /c truncates multi-line args)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {a['timeout_s']}s", "elapsed_s": round(time.time() - t0, 1)}
    el = round(time.time() - t0, 1)
    try: j = json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "error": f"non-json output (rc={p.returncode}): {(p.stderr or p.stdout)[:400]}", "elapsed_s": el}
    if j.get("is_error") or p.returncode != 0:
        return {"ok": False, "error": str(j.get("result") or p.stderr)[:600], "elapsed_s": el, "session_id": j.get("session_id")}
    return {"ok": True, "text": j.get("result", ""), "elapsed_s": el, "session_id": j.get("session_id", sid),
            "turns": j.get("num_turns"), "cost_usd": j.get("total_cost_usd")}

VERDICT_RE = re.compile(r"VERDICT:\s*([A-Z]+(?: \(default\))?)[^\n]*?\((quick|deep)\)[^\n]*?confidence:\s*(low|medium|high)", re.I)

LOG_RE = re.compile(r"^\s*LOG:\s*(.+?)\s*$", re.M)

def split_log(text: str) -> tuple[str, str | None]:
    """returns (text without the LOG: line, the LOG: payload or None)."""
    m = LOG_RE.search(text or "")
    if not m: return text, None
    return (text[:m.start()] + text[m.end():]).rstrip(), m.group(1).strip()

def ensure_log_line(cfg: dict, sig: dict, text: str, kind: str, log) -> None:
    """the runner owns verdicts.live.log: prefix ts|symbol|#id, then the model's LOG: fields; synthesize if absent."""
    p = os.path.join(cfg["advisor"]["live_dir"], "verdicts.live.log"); tag = f"#{sig['signal_id']}"
    _, payload = split_log(text)
    bflag = "brief:" + ("yes" if os.path.exists(os.path.join(cfg["advisor"]["live_dir"], "bundles", sig["signal_key"], "market-brief.md")) else "no")
    if payload:
        payload = re.sub(r"\s*\|\s*brief:(yes|no)\s*$", "", payload)
        C.append_line(p, " | ".join([C.now_iso(), sig["symbol"], tag, payload, bflag])); return
    m = VERDICT_RE.search(text or "")
    v = f"{m.group(1).upper()} ({m.group(2).lower()}, {m.group(3).lower()})" if m else "UNPARSED"
    sh = re.search(r"regime\s*([✓✗?])\s*news\s*([✓✗?])\s*correlation\s*([✓✗?])", text or ""); steps = re.search(r"steps:\s*([0-9✓✗? ]+)", text or "")
    q = re.search(r"Quality:\s*([ABC])", text or ""); why = re.search(r"Why:\s*(.+)", text or "")
    line = " | ".join([C.now_iso(), sig["symbol"], tag, f"{sig.get('strategy')} {sig.get('direction')}", v,
                       f"SH:{''.join(sh.groups()) if sh else '???'} steps:{steps.group(1).strip().replace(' ', '') if steps else '-'}",
                       f"Q:{q.group(1) if q else '-'}", "step: (runner-synthesized)", (why.group(1).strip()[:160] if why else "(no Why line)") + (" | reply" if kind == "reply" else " | runner"), bflag])
    C.append_line(p, line); log(f"{sig['signal_key']}: log line synthesized ({kind})")

# ----------------------------------------------------------------------------- records
def verdict_path(cfg: dict, key: str) -> str: return os.path.join(cfg["root"], "advisor", "verdicts", f"{key}.json")
def load_record(cfg: dict, key: str) -> dict | None: return C.load_json(verdict_path(cfg, key))
def save_record(cfg: dict, rec: dict) -> None: C.atomic_write_json(verdict_path(cfg, rec["signal_key"]), rec)

class Runner:
    def __init__(self, cfg: dict):
        self.cfg = cfg; self.log = C.Log("advisor", cfg["logs_dir"]); self.lock = threading.Lock()
        for sub in ("advisor/verdicts", "advisor/replies", "advisor/replies/done", "web/charts", "advisor/briefs", "advisor/briefs/requests"): os.makedirs(os.path.join(cfg["root"], sub), exist_ok=True)
        for sub in ("bundles",): os.makedirs(os.path.join(cfg["advisor"]["live_dir"], sub), exist_ok=True)

    def consult(self, sig: dict) -> dict:
        key = sig["signal_key"]; rec = load_record(self.cfg, key) or {"schema_version": 1, "signal_key": key, "symbol": sig["symbol"], "signal_id": sig["signal_id"], "session_id": session_id(key), "model": self.cfg["advisor"]["model"], "consults": []}
        bdir = write_bundle(sig, self.cfg)
        rel = os.path.relpath(bdir, self.cfg["advisor"]["live_dir"]).replace("\\", "/")
        prompt = (f"#{sig['signal_id']} — LIVE signal {sig['symbol']} {sig.get('strategy')} {sig.get('direction')}. "
                  f"Bundle: {rel}/setup.md, {rel}/h4.png, {rel}/d1.png" + (f", {rel}/market-brief.md (context brief - §Context brief rules apply)" if os.path.exists(os.path.join(bdir, "market-brief.md")) else "") + ". Read them all, then answer with the full scorecard. "
                  f"Do NOT write to verdicts.live.log yourself and do not use shell commands (none are available): the runner appends the log line. "
                  f"End your answer with one final line starting with 'LOG: ' followed by the verdict-log fields after the '#id' field, i.e. "
                  f"'LOG: {sig.get('strategy')} {sig.get('direction')} | <VERDICT> (quick, <confidence>) | SH:<regime><news><corr> steps:<...> | Q:<A|B|C|-> | step: <decisive step> | <one-clause reason>' "
                  f"using the glyphs ✓ ✗ ? exactly as in the role file (e.g. 'SH:✓✓✗ steps:1✓2✗3✗4?5?6✓').")
        self.log(f"{key}: consult start (session {rec['session_id'][:8]})")
        with self.lock:
            r = run_claude(self.cfg, prompt, sid=rec["session_id"], resume=False, log=self.log)
            if not r["ok"] and "already in use" in (r.get("error") or "").lower():
                r = run_claude(self.cfg, prompt, sid=rec["session_id"], resume=True, log=self.log)
        if r.get("ok"): ensure_log_line(self.cfg, sig, r["text"], "verdict", self.log); r["text"] = split_log(r["text"])[0]
        entry = {"ts": C.now_iso(), "kind": "verdict", "prompt": prompt, **r}
        rec["consults"].append(entry); rec["delay_count"] = sig.get("delay_count", 0); rec["status_at_consult"] = sig.get("status")
        save_record(self.cfg, rec)
        self.log(f"{key}: consult {'ok' if r['ok'] else 'FAILED'} in {r.get('elapsed_s')}s" + ("" if r["ok"] else f": {r.get('error')}"))
        return rec

    def reply(self, key: str, text: str, reply_file: str) -> None:
        rec = load_record(self.cfg, key); sig = C.signal(self.cfg, key)
        if not rec or not sig:
            self.log(f"{key}: reply ignored (no verdict record / signal)"); return
        self.log(f"{key}: reply -> session {rec['session_id'][:8]}")
        # fresh pictures for every reply: re-render h4/d1 from the live feed so a question after a delay is answered
        # on the setup as it looks NOW, not as it looked at publish time
        fresh_note = ""
        try:
            bdir = write_bundle(sig, self.cfg); rel = os.path.relpath(bdir, self.cfg["advisor"]["live_dir"]).replace("\\", "/")
            lv = sig.get("levels") or {}
            fresh_note = (f"\n\n(Charts REFRESHED as of {C.now_iso()}: re-read {rel}/h4.png and {rel}/d1.png before answering - they show the current bars; "
                          f"the signal's levels are unchanged: entry {lv.get('entry')} SL {lv.get('sl')} TP1 {lv.get('tp1')} TP2 {lv.get('tp2')}; delays so far {sig.get('delay_count', 0)}; status {sig.get('status')}.)")
        except Exception as e: self.log(f"{key}: bundle refresh failed: {e!r}")
        text = text + fresh_note + "\n\n(Answer in the same session. Do not write files; end with one line 'LOG: reply | <one-clause summary of what changed, or unchanged>'.)"
        with self.lock: r = run_claude(self.cfg, text, sid=rec["session_id"], resume=True, log=self.log)
        rec["consults"].append({"ts": C.now_iso(), "kind": "reply", "prompt": text.split("\n\n(Answer in the same session")[0], **r}); save_record(self.cfg, rec)
        if r["ok"]: ensure_log_line(self.cfg, sig, r["text"], "reply", self.log); r["text"] = split_log(r["text"])[0]; rec["consults"][-1]["text"] = r["text"]; save_record(self.cfg, rec)
        os.replace(reply_file, os.path.join(os.path.dirname(reply_file), "done", os.path.basename(reply_file)))
        self.log(f"{key}: reply {'ok' if r['ok'] else 'FAILED'} in {r.get('elapsed_s')}s")

    def tick(self) -> None:
        brief_runner.poll_requests(self.cfg, self.log)
        for sig in C.list_signals(self.cfg, light=True):
            if sig.get("status") != "open": continue
            rec = load_record(self.cfg, sig["signal_key"])
            if rec and rec.get("consults"):
                last = rec["consults"][-1]
                if last.get("ok") or last.get("kind") == "reply" or (C.parse_iso(last.get("ts")) and (C.now_utc() - C.parse_iso(last["ts"])).total_seconds() < 600): continue
            full = C.signal(self.cfg, sig["signal_key"])
            if full: self.consult(full)
        for rf in sorted(glob.glob(os.path.join(self.cfg["root"], "advisor", "replies", "*.json"))):
            j = C.load_json(rf)
            if not j or not C.safe_key(j.get("signal_key", "")) or not j.get("text"): os.replace(rf, rf + ".bad"); continue
            self.reply(j["signal_key"], j["text"][:4000], rf)

    def loop(self) -> None:
        self.log(f"runner up: root={self.cfg['root']} live_dir={self.cfg['advisor']['live_dir']} model={self.cfg['advisor']['model']}/{self.cfg['advisor']['effort']}")
        while True:
            try: self.tick()
            except Exception as e: self.log(f"tick error: {e!r}")
            time.sleep(5)

def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config"); ap.add_argument("--once", help="consult this signal key once and exit (test)")
    ap.add_argument("--reply", help="with --once: send this reply text after the verdict (test)")
    ap.add_argument("--bundle-only", action="store_true", help="with --once: write the bundle, no consult")
    a = ap.parse_args(); cfg = C.load_config(a.config); r = Runner(cfg)
    if a.once:
        sig = C.signal(cfg, a.once)
        if not sig: sys.exit("no such signal")
        if a.bundle_only: print(write_bundle(sig, cfg)); return
        rec = r.consult(sig); last = rec["consults"][-1]; print(json.dumps({k: v for k, v in last.items() if k != "prompt"}, indent=1)[:3000])
        if a.reply:
            rf = os.path.join(cfg["root"], "advisor", "replies", f"{a.once}-test.json"); C.atomic_write_json(rf, {"signal_key": a.once, "text": a.reply, "ts": C.now_iso()})
            r.reply(a.once, a.reply, rf); print(json.dumps(load_record(cfg, a.once)["consults"][-1], indent=1)[:3000])
        return
    r.loop()

if __name__ == "__main__": main()
