r"""
advisor_runner.py - live advisor auto-consult (spec §6, CLAUDE.live.md).

For every signal that reaches status "open" it renders h4/d1 (charts.py) and writes ONE sighted bundle (setup.md with
the swing table and the open-exposure block), then runs TWO independent headless Claude Code consults from it in
parallel (coach 2026-09-21): sonnet-low (fast, 150 s) and opus-high (deep, ~600 s). Each has its own deterministic
session, its own record <root>/advisor/verdicts/<key>.<model>.json, its own notes file notes.live.<model>.md, and a
per-model concurrency cap - neither waits on the other or on anything else. The runner owns verdicts.live.log (model
field after the timestamp) and advisor/consults.log (one line per attempt: the dashboard's daily counts and the
rate-limit alert). Replies from the web app carry a model id and continue that model's session, with fresh charts in
that model's own bundle sub-folder. The advisor never touches the queue (it has no Bash).

Layout the consult runs in (cwd = advisor.live_dir):
   <X>\quick-reference.html          (the role file reads ../quick-reference.html)
   <X>\advisor\CLAUDE.md             (= training/advisor/CLAUDE.live.md, copied by provisioning/deploy_live.sh)
   <X>\advisor\library\  notes.live.<model>.md  verdicts.live.log  .claude\settings.json  bundles\<key>\{setup.md,h4.png,d1.png,<model>\}
"""
from __future__ import annotations
import glob, json, os, re, subprocess, sys, time, uuid, threading
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import charts
from live import overlays as OV
from live import exposure as EXP
from live import brief_runner

SESSION_NS = uuid.UUID("5f0a3c1e-9b7d-4c1a-8f2e-2d3b4a5c6d7e")
DISALLOWED = "Bash,WebFetch,WebSearch,Task,Agent,NotebookEdit"


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
    sig_t = C.parse_iso(sig.get("signal_time_utc") or sig.get("signal_time")); horizon = sig_t + timedelta(days=14) if sig_t else None
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
            "    - 5-bar fractal (2 left / 2 right) on H4 over a 14-day rolling window — the same set the chart marks, but only the "
            "five most recent of each: the chart marks more, so an unlisted marker is not a spurious one. "
            f"Bars ago counts back from the newest bar on the chart (its right edge); {anchor}. The newest entry may still be "
            "confirmed against the unfinished current bar. No sweep column: the EA tracks no per-swing pool status.")

def build_setup_md(sig: dict, cfg: dict) -> str:
    lv = sig.get("levels") or {}; rr = sig.get("rr") or {}; sz = sig.get("sizing") or {}
    sig_t = C.parse_iso(sig.get("signal_time")); dl = C.parse_iso(sig.get("deadline"))
    entry_note = " (STOP entry - breakout order)" if lv.get("stop_entry") else ""
    tp2 = f", TP2 {lv.get('tp2')}" if lv.get("tp2") else ""
    r_tp1 = rr.get("tp1"); r_run = rr.get("runner") or rr.get("detector")
    rules = C.symbol_rules(cfg, sig.get("symbol", "")); rules_line = C.symbol_rules_line(cfg, sig.get("symbol", ""))
    klass = rules.get("class") or sig.get("asset_class")          # config wins: the EA's classifier has no crypto branch
    return f"""# LIVE SETUP — {sig.get('strategy')} {sig.get('direction')} — {sig.get('symbol')} #{sig.get('signal_id')}
_Sighted live consult (CLAUDE.live.md). Judge from the charts, the guide and the calendar below._

- **Symbol / class:** {sig.get('symbol')} ({klass})
{('- **Symbol rules (journaled):** ' + rules_line) if rules_line else '- **Symbol rules:** standard session rules apply (no per-symbol entry in config/symbol_rules.json)'}
- **Time:** {sig.get('sigtime_text', '')} — signal bar {sig.get('signal_time_utc') or sig.get('signal_time')}, presented {sig.get('published_at')} (all UTC) · session: {sig.get('session')}
- **Decision deadline:** {dl.strftime('%a %d %b %H:%M UTC') if dl else '-'} ({sig.get('max_age_bars')} H4 bars of silence = expired) · delays so far: {sig.get('delay_count', 0)}
- **{regime_line(sig)}**
- **{protocol_line(sig)}**
- **Strategy read:** {sig.get('strategy_text', '')}
- **Proposed levels:** entry {lv.get('entry')}{entry_note}, SL {lv.get('sl')}, TP1 {lv.get('tp1')}{tp2} (partial {lv.get('partial_fraction', 0.5):.0%} at TP1)
- **Risk geometry:** SL 1.0R · TP1 {r_tp1}R · TP2 {r_run}R (floor {rr.get('floor')}R; detector already sized to the gate risk and cleared the R:R floor)
{swing_block(sig)}
- **Sizing:** {sz.get('lots_line', '')} · risk multiplier in effect {sz.get('risk_mult_applied', 1.0)} → effective {float(sz.get('risk_pct_effective', 0.01))*100:.2f}%
{EXP.build(sig, cfg)}
- **Kill switch:** trading {'ENABLED' if sig.get('trading_enabled') else 'DISABLED (approve would be refused)'}
- **Events (next 14 d, {'/'.join(sorted(C.symbol_ccys(sig.get('symbol', '')) - {'All'}))}):**
{events_block(sig, cfg)}

Images: `d1.png` (daily context, regime) · `h4.png` (H4 setup with the detector's overlays and the tri-EMA).
"""

def _atomic_copy(src: str, dst: str) -> None:
    """copy then rename: a consult reading the bundle never sees a half-written PNG while another thread refreshes it."""
    import shutil
    shutil.copyfile(src, dst + ".tmp"); os.replace(dst + ".tmp", dst)

def write_bundle(sig: dict, cfg: dict, sub: str | None = None) -> str:
    """bundles/<key>/ (the shared initial bundle both advisors are launched from) or bundles/<key>/<sub>/ (one model's
    fresh copy for a reply - never overwrites what the other model is reading)."""
    live_dir = cfg["advisor"]["live_dir"]; key = sig["signal_key"]
    bdir = os.path.join(live_dir, "bundles", key, *( [sub] if sub else [] )); os.makedirs(bdir, exist_ok=True)
    h4, d1 = charts.render_signal(sig, os.path.join(cfg["root"], "web", "charts"), fresh=True)
    _atomic_copy(h4, os.path.join(bdir, "h4.png")); _atomic_copy(d1, os.path.join(bdir, "d1.png"))
    brief_note = ""
    b = brief_runner.latest_brief(cfg, 2.0)
    mb = os.path.join(bdir, "market-brief.md")
    if b:
        _atomic_copy(b["path"], mb); brief_note = f"\n- **Context brief attached:** `market-brief.md` (generated {b['t'].strftime('%Y-%m-%d %H:%M UTC')}{'; FLAGGED directional wording: ' + ', '.join(b['flags']) if b['flags'] else ''}) - CLAUDE.live.md §Context brief governs its weight."
    elif os.path.exists(mb): os.remove(mb)
    C.atomic_write_text(os.path.join(bdir, "setup.md"), build_setup_md(sig, cfg) + brief_note + "\n")
    return bdir

# ----------------------------------------------------------------------------- models (coach 2026-09-21: dual advisor)
DEFAULT_MODELS = [
    {"id": "sonnet-low", "model": "sonnet", "effort": "low", "timeout_s": 150, "max_parallel": 3, "label": "Sonnet · low effort · fast"},
    {"id": "opus-high", "model": "opus", "effort": "high", "timeout_s": 600, "max_parallel": 2, "label": "Opus · high effort · deep"},
]
MAX_ATTEMPTS = 2          # initial consult attempts per model per signal (a rate-limit error is never retried automatically)
RETRY_AFTER_S = 600
RATE_RE = re.compile(r"rate.?limit|usage limit|too many requests|\b429\b|overloaded|quota|limit (?:reached|exceeded)", re.I)

def models(cfg: dict) -> list[dict]:
    a = cfg["advisor"]
    if a.get("models"): return a["models"]
    return [{"id": "sonnet-low", "model": a.get("model", "sonnet"), "effort": a.get("effort", "low"), "timeout_s": a.get("timeout_s", 150),
             "max_parallel": 3, "label": "Sonnet · low effort · fast"}]
def model_cfg(cfg: dict, mid: str) -> dict | None: return next((m for m in models(cfg) if m["id"] == mid), None)
def notes_file(mid: str) -> str: return f"notes.live.{mid}.md"
def session_id(key: str, mid: str) -> str: return str(uuid.uuid5(SESSION_NS, f"{key}|{mid}"))

# ----------------------------------------------------------------------------- claude
def claude_env(cfg: dict) -> dict:
    # a child claude inherits any CLAUDE_* variables of a parent Claude Code session and hangs on its messaging
    # socket (found 2026-09-15). Start from a scrubbed environment.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE")}
    a = cfg["advisor"]
    if a.get("token_file"): env["CLAUDE_CODE_OAUTH_TOKEN"] = C.read_secret(a["token_file"])
    if a.get("node_path"): env["PATH"] = a["node_path"] + os.pathsep + env.get("PATH", "")
    return env

def run_claude(cfg: dict, m: dict, prompt: str, *, sid: str, resume: bool, log) -> dict:
    a = cfg["advisor"]; cmd = a["claude_cmd"]; nf = notes_file(m["id"])
    others = [notes_file(x["id"]) for x in models(cfg) if x["id"] != m["id"]] + ["notes.live.md"]
    allowed = f"Read,Glob,Grep,Write({nf}),Edit({nf})"                      # only THIS model's notes file (no concurrent writers)
    denied = DISALLOWED + "".join(f",Write({o}),Edit({o})" for o in others)
    args = [cmd, "-p", "--output-format", "json", "--model", m["model"], "--effort", m["effort"],
            "--allowedTools", allowed, "--disallowedTools", denied, "--max-turns", "40"]
    args += ["--resume", sid] if resume else ["--session-id", sid]
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    t0 = time.time(); to = int(m.get("timeout_s") or 150)
    try:
        p = subprocess.run(args, cwd=a["live_dir"], env=claude_env(cfg), input=prompt, capture_output=True, text=True, timeout=to, encoding="utf-8", errors="replace")   # prompt via stdin (cmd /c truncates multi-line args)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {to}s", "elapsed_s": round(time.time() - t0, 1), "timeout": True}
    el = round(time.time() - t0, 1)
    try: j = json.loads(p.stdout)
    except ValueError:
        err = f"non-json output (rc={p.returncode}): {(p.stderr or p.stdout)[:400]}"
        return {"ok": False, "error": err, "elapsed_s": el, "rate_limited": bool(RATE_RE.search(err))}
    if j.get("is_error") or p.returncode != 0:
        err = str(j.get("result") or p.stderr)[:600]
        return {"ok": False, "error": err, "elapsed_s": el, "session_id": j.get("session_id"), "rate_limited": bool(RATE_RE.search(err))}
    return {"ok": True, "text": j.get("result", ""), "elapsed_s": el, "session_id": j.get("session_id", sid),
            "turns": j.get("num_turns"), "cost_usd": j.get("total_cost_usd")}

VERDICT_RE = re.compile(r"VERDICT:\s*([A-Z]+(?: \(default\))?)[^\n]*?\((quick|deep)\)[^\n]*?confidence:\s*(low|medium|high)", re.I)
VERDICT_WORD_RE = re.compile(r"VERDICT:\s*\**\s*([A-Z]+)", re.I)

def verdict_word(text: str | None) -> str | None:
    """TAKE / SKIP / WAIT ... from a scorecard - the web app's AGREE / DISAGREE badge compares these."""
    m = VERDICT_WORD_RE.search(text or "")
    return m.group(1).upper() if m else None

LOG_RE = re.compile(r"^\s*LOG:\s*(.+?)\s*$", re.M)
_log_lock = threading.Lock()     # verdicts.live.log + consults.log: runner-owned, serialized appends (two advisors, no concurrent writers)

def split_log(text: str) -> tuple[str, str | None]:
    """returns (text without the LOG: line, the LOG: payload or None)."""
    m = LOG_RE.search(text or "")
    if not m: return text, None
    return (text[:m.start()] + text[m.end():]).rstrip(), m.group(1).strip()

def ensure_log_line(cfg: dict, sig: dict, mid: str, text: str, kind: str, log) -> None:
    """the runner owns verdicts.live.log: ts | model | symbol | #id, then the model's LOG: fields; synthesize if absent.
    Format: CLAUDE.live.md §Verdict log (model field added 2026-09-21)."""
    p = os.path.join(cfg["advisor"]["live_dir"], "verdicts.live.log"); tag = f"#{sig['signal_id']}"
    _, payload = split_log(text)
    bflag = "brief:" + ("yes" if os.path.exists(os.path.join(cfg["advisor"]["live_dir"], "bundles", sig["signal_key"], "market-brief.md")) else "no")
    if payload:
        payload = re.sub(r"\s*\|\s*brief:(yes|no)\s*$", "", payload)
        with _log_lock: C.append_line(p, " | ".join([C.now_iso(), mid, sig["symbol"], tag, payload, bflag]))
        return
    m = VERDICT_RE.search(text or "")
    v = f"{m.group(1).upper()} ({m.group(2).lower()}, {m.group(3).lower()})" if m else "UNPARSED"
    sh = re.search(r"regime\s*([✓✗?])\s*news\s*([✓✗?])\s*correlation\s*([✓✗?])", text or ""); steps = re.search(r"steps:\s*([0-9✓✗? ]+)", text or "")
    q = re.search(r"Quality:\s*([ABC])", text or ""); why = re.search(r"Why:\s*(.+)", text or "")
    line = " | ".join([C.now_iso(), mid, sig["symbol"], tag, f"{sig.get('strategy')} {sig.get('direction')}", v,
                       f"SH:{''.join(sh.groups()) if sh else '???'} steps:{steps.group(1).strip().replace(' ', '') if steps else '-'}",
                       f"Q:{q.group(1) if q else '-'}", "step: (runner-synthesized)", (why.group(1).strip()[:160] if why else "(no Why line)") + (" | reply" if kind == "reply" else " | runner"), bflag])
    with _log_lock: C.append_line(p, line)
    log(f"{sig['signal_key']}/{mid}: log line synthesized ({kind})")

def consult_log(cfg: dict, key: str, mid: str, kind: str, r: dict) -> None:
    """one line per consult attempt - the dashboard's per-model daily counts and the rate-limit alert read this."""
    err = (str(r.get("error") or "").replace("|", "/").replace("\n", " "))[:160]
    with _log_lock:
        C.append_line(os.path.join(cfg["root"], "advisor", "consults.log"),
                      "|".join([C.now_iso(), mid, key, kind, "ok" if r.get("ok") else "error", str(r.get("elapsed_s")),
                                "rate_limited" if r.get("rate_limited") else ("timeout" if r.get("timeout") else ""), err]))

# ----------------------------------------------------------------------------- records (one file per model: no shared writer)
def verdict_path(cfg: dict, key: str, mid: str) -> str: return os.path.join(cfg["root"], "advisor", "verdicts", f"{key}.{mid}.json")
def load_record(cfg: dict, key: str, mid: str) -> dict | None: return C.load_json(verdict_path(cfg, key, mid))
def save_record(cfg: dict, rec: dict) -> None: C.atomic_write_json(verdict_path(cfg, rec["signal_key"], rec["model_id"]), rec)
def new_record(cfg: dict, sig: dict, m: dict) -> dict:
    key = sig["signal_key"]
    return {"schema_version": 2, "signal_key": key, "symbol": sig["symbol"], "signal_id": sig["signal_id"], "model_id": m["id"],
            "model": m["model"], "effort": m["effort"], "label": m.get("label", m["id"]), "timeout_s": m.get("timeout_s"),
            "session_id": session_id(key, m["id"]), "status": "idle", "consults": []}

def header(m: dict) -> str:
    return (f"[ADVISOR: {m['id']} · {m.get('label', m['id'])}] You are the {m.get('label', m['id'])} advisor of the two that run on every "
            f"signal (CLAUDE.live.md §Two advisors). Your notes file is `{notes_file(m['id'])}` - read it at session start in place of "
            f"notes.live.md and write only to it. Your verdict-log model field is `{m['id']}` (the runner writes the log). "
            f"You never see the other advisor's verdict.\n\n")

class Runner:
    def __init__(self, cfg: dict):
        self.cfg = cfg; self.log = C.Log("advisor", cfg["logs_dir"])
        self.mu = threading.Lock(); self.inflight: set[tuple[str, str]] = set()
        self.sem = {m["id"]: threading.BoundedSemaphore(max(1, int(m.get("max_parallel", 2)))) for m in models(cfg)}
        for sub in ("advisor/verdicts", "advisor/replies", "advisor/replies/done", "advisor/retry", "web/charts", "advisor/briefs", "advisor/briefs/requests"): os.makedirs(os.path.join(cfg["root"], sub), exist_ok=True)
        live_dir = cfg["advisor"]["live_dir"]; os.makedirs(os.path.join(live_dir, "bundles"), exist_ok=True)
        legacy = os.path.join(live_dir, "notes.live.md")
        for m in models(cfg):                     # each model starts from the shared history, then keeps its own
            nf = os.path.join(live_dir, notes_file(m["id"]))
            if not os.path.exists(nf):
                import shutil
                if os.path.exists(legacy): shutil.copyfile(legacy, nf)
                else: open(nf, "a").close()
        # a consult that was running when the runner stopped is dead: record it so the panel does not spin forever
        for p in glob.glob(os.path.join(cfg["root"], "advisor", "verdicts", "*.*.json")):
            rec = C.load_json(p)
            if rec and rec.get("status") in ("running", "queued"):
                rec["consults"].append({"ts": C.now_iso(), "kind": rec.get("running_kind", "verdict"), "ok": False, "interrupted": True,
                                        "error": "interrupted: the advisor runner restarted during this consult"})
                rec["status"] = "error"; rec.pop("started_at", None); rec.pop("queued_at", None); save_record(cfg, rec)

    # ---------------------------------------------------------------- threads
    def _spawn(self, key: str, slot: str, fn, *args) -> bool:
        with self.mu:
            if (key, slot) in self.inflight: return False
            self.inflight.add((key, slot))
        def run():
            try: fn(*args)
            except Exception as e: self.log(f"{key}/{slot}: thread error {e!r}")
            finally:
                with self.mu: self.inflight.discard((key, slot))
        threading.Thread(target=run, daemon=True, name=f"adv-{key}-{slot}").start()
        return True

    def busy(self, key: str, slot: str) -> bool:
        with self.mu: return (key, slot) in self.inflight

    def needs_consult(self, key: str, mid: str) -> bool:
        rec = load_record(self.cfg, key, mid)
        if not rec or not rec.get("consults"): return True
        cs = rec["consults"]
        if any(c.get("kind") == "reply" for c in cs): return False
        vs = [c for c in cs if c.get("kind") == "verdict"]
        if any(c.get("ok") for c in vs): return False
        if not vs: return True
        last = vs[-1]
        if last.get("interrupted"): return True
        if last.get("rate_limited"): return False
        if len([c for c in vs if not c.get("interrupted")]) >= MAX_ATTEMPTS: return False
        t = C.parse_iso(last.get("ts"))
        return bool(t and (C.now_utc() - t).total_seconds() >= RETRY_AFTER_S)

    def _launch(self, sig: dict, todo: list[dict], force: bool = False) -> None:
        """render the shared bundle ONCE per presentation, then start every model's consult in its own thread."""
        key = sig["signal_key"]; bdir = os.path.join(self.cfg["advisor"]["live_dir"], "bundles", key)
        mark = os.path.join(bdir, ".rendered"); stamp = str(sig.get("delay_count", 0))
        try: have = open(mark).read().strip() == stamp and os.path.exists(os.path.join(bdir, "setup.md"))
        except OSError: have = False
        if not have:
            write_bundle(sig, self.cfg); C.atomic_write_text(mark, stamp)
        for m in todo: self._spawn(key, m["id"], self.consult, sig, m, force)

    def consult(self, sig: dict, m: dict, force: bool = False) -> None:
        key = sig["signal_key"]; mid = m["id"]
        rec = load_record(self.cfg, key, mid) or new_record(self.cfg, sig, m)
        rec.update(status="queued", queued_at=C.now_iso(), running_kind="verdict"); save_record(self.cfg, rec)
        with self.sem[mid]:                                   # per-model cap: waiting here never blocks the other model
            cur = C.signal(self.cfg, key)
            if not cur or (cur.get("status") != "open" and not force):
                rec["status"] = "skipped"; rec["note"] = f"signal decided ({cur.get('status') if cur else 'gone'}) before a slot was free"
                rec.pop("queued_at", None); save_record(self.cfg, rec); self.log(f"{key}/{mid}: skipped, signal no longer open"); return
            rec.update(status="running", started_at=C.now_iso()); rec.pop("queued_at", None); save_record(self.cfg, rec)
            rel = f"bundles/{key}"; bdir = os.path.join(self.cfg["advisor"]["live_dir"], "bundles", key)
            prompt = header(m) + (f"#{sig['signal_id']} — LIVE signal {sig['symbol']} {sig.get('strategy')} {sig.get('direction')}. "
                      f"Bundle: {rel}/setup.md, {rel}/h4.png, {rel}/d1.png" + (f", {rel}/market-brief.md (context brief - §Context brief rules apply)" if os.path.exists(os.path.join(bdir, "market-brief.md")) else "") + ". Read them all, then answer with the full scorecard. "
                      f"Do NOT write to verdicts.live.log yourself and do not use shell commands (none are available): the runner appends the log line. "
                      f"End your answer with one final line starting with 'LOG: ' followed by the verdict-log fields after the '#id' field, i.e. "
                      f"'LOG: {sig.get('strategy')} {sig.get('direction')} | <VERDICT> (quick, <confidence>) | SH:<regime><news><corr> steps:<...> | Q:<A|B|C|-> | step: <decisive step> | <one-clause reason>' "
                      f"using the glyphs ✓ ✗ ? exactly as in the role file (e.g. 'SH:✓✓✗ steps:1✓2✗3✗4?5?6✓').")
            self.log(f"{key}/{mid}: consult start (session {rec['session_id'][:8]}, timeout {m.get('timeout_s')}s)")
            r = run_claude(self.cfg, m, prompt, sid=rec["session_id"], resume=False, log=self.log)
            if not r["ok"] and "already in use" in (r.get("error") or "").lower():
                r = run_claude(self.cfg, m, prompt, sid=rec["session_id"], resume=True, log=self.log)
        if r.get("ok"): ensure_log_line(self.cfg, sig, mid, r["text"], "verdict", self.log); r["text"] = split_log(r["text"])[0]
        consult_log(self.cfg, key, mid, "verdict", r)
        rec = load_record(self.cfg, key, mid) or rec
        rec["consults"].append({"ts": C.now_iso(), "kind": "verdict", "prompt": prompt, **r})
        rec["status"] = "ok" if r.get("ok") else ("rate_limited" if r.get("rate_limited") else "error")
        rec["delay_count"] = sig.get("delay_count", 0); rec["status_at_consult"] = sig.get("status"); rec.pop("started_at", None)
        save_record(self.cfg, rec)
        self.log(f"{key}/{mid}: consult {'ok' if r['ok'] else 'FAILED'} in {r.get('elapsed_s')}s" + ("" if r["ok"] else f": {r.get('error')}"))

    def reply(self, key: str, mid: str, text: str, reply_file: str) -> None:
        m = model_cfg(self.cfg, mid); rec = load_record(self.cfg, key, mid); sig = C.signal(self.cfg, key)
        if not m or not rec or not sig or not rec.get("consults"):
            self.log(f"{key}/{mid}: reply ignored (no verdict record / signal / model)")
            os.replace(reply_file, reply_file + ".bad"); return
        with self.sem[mid]:
            rec.update(status="running", started_at=C.now_iso(), running_kind="reply"); save_record(self.cfg, rec)
            self.log(f"{key}/{mid}: reply -> session {rec['session_id'][:8]}")
            # fresh pictures for every reply, into THIS model's own folder: a question after a delay is answered on the setup
            # as it looks NOW, and the other advisor keeps reading the bundle it was launched from
            fresh_note = ""
            try:
                bdir = write_bundle(sig, self.cfg, sub=mid); rel = f"bundles/{key}/{mid}"
                lv = sig.get("levels") or {}
                fresh_note = (f"\n\n(Charts REFRESHED as of {C.now_iso()}: re-read {rel}/setup.md, {rel}/h4.png and {rel}/d1.png before answering - they show the current bars; "
                              f"the signal's levels are unchanged: entry {lv.get('entry')} SL {lv.get('sl')} TP1 {lv.get('tp1')} TP2 {lv.get('tp2')}; delays so far {sig.get('delay_count', 0)}; status {sig.get('status')}.)")
            except Exception as e: self.log(f"{key}/{mid}: bundle refresh failed: {e!r}")
            full = text + fresh_note + "\n\n(Answer in the same session. Do not write files other than your own notes; end with one line 'LOG: reply | <one-clause summary of what changed, or unchanged>'.)"
            r = run_claude(self.cfg, m, full, sid=rec["session_id"], resume=True, log=self.log)
        if r["ok"]: ensure_log_line(self.cfg, sig, mid, r["text"], "reply", self.log); r["text"] = split_log(r["text"])[0]
        consult_log(self.cfg, key, mid, "reply", r)
        rec = load_record(self.cfg, key, mid) or rec
        rec["consults"].append({"ts": C.now_iso(), "kind": "reply", "prompt": text, **r})
        rec["status"] = "ok" if r.get("ok") else ("rate_limited" if r.get("rate_limited") else "error"); rec.pop("started_at", None)
        save_record(self.cfg, rec)
        os.replace(reply_file, os.path.join(os.path.dirname(reply_file), "done", os.path.basename(reply_file)))
        self.log(f"{key}/{mid}: reply {'ok' if r['ok'] else 'FAILED'} in {r.get('elapsed_s')}s")

    def tick(self) -> None:
        brief_runner.poll_requests(self.cfg, self.log)
        ms = models(self.cfg)
        for sig in C.list_signals(self.cfg, light=True):
            if sig.get("status") != "open": continue
            key = sig["signal_key"]
            if self.busy(key, "bundle"): continue
            todo = [m for m in ms if not self.busy(key, m["id"]) and self.needs_consult(key, m["id"])]
            if not todo: continue
            full = C.signal(self.cfg, key)
            if full: self._spawn(key, "bundle", self._launch, full, todo)
        # "Run again" from the web app (e.g. after a rate limit cleared): one fresh initial consult for that model
        for rq in sorted(glob.glob(os.path.join(self.cfg["root"], "advisor", "retry", "*.json"))):
            j = C.load_json(rq) or {}; key = j.get("signal_key", ""); mid = j.get("model", "")
            m = model_cfg(self.cfg, mid); full = C.signal(self.cfg, key) if C.safe_key(key) else None
            if not m or not full: os.replace(rq, rq + ".bad"); continue
            if self.busy(key, mid) or self.busy(key, "bundle"): continue
            os.remove(rq); self.log(f"{key}/{mid}: re-run requested from the web app")
            self._spawn(key, "bundle", self._launch, full, [m], True)
        for rf in sorted(glob.glob(os.path.join(self.cfg["root"], "advisor", "replies", "*.json"))):
            j = C.load_json(rf)
            if not j or not C.safe_key(j.get("signal_key", "")) or not j.get("text"): os.replace(rf, rf + ".bad"); continue
            mid = j.get("model") or ms[0]["id"]
            if not model_cfg(self.cfg, mid): os.replace(rf, rf + ".bad"); continue
            if self.busy(j["signal_key"], mid): continue           # that model is still answering: the reply waits its turn
            self._spawn(j["signal_key"], mid, self.reply, j["signal_key"], mid, j["text"][:4000], rf)

    def loop(self) -> None:
        self.log(f"runner up: root={self.cfg['root']} live_dir={self.cfg['advisor']['live_dir']} models="
                 + ", ".join(f"{m['id']}({m['model']}/{m['effort']}, {m.get('timeout_s')}s, x{m.get('max_parallel', 2)})" for m in models(self.cfg)))
        while True:
            try: self.tick()
            except Exception as e: self.log(f"tick error: {e!r}")
            time.sleep(5)

def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config"); ap.add_argument("--once", help="consult this signal key once and exit (test)")
    ap.add_argument("--model", help="with --once: only this model id (default: every model, in parallel)")
    ap.add_argument("--bundle-only", action="store_true", help="with --once: write the bundle, no consult")
    a = ap.parse_args(); cfg = C.load_config(a.config)
    if a.once:
        sig = C.signal(cfg, a.once)
        if not sig: sys.exit("no such signal")
        if a.bundle_only: print(write_bundle(sig, cfg)); return
        r = Runner(cfg); ms = [m for m in models(cfg) if not a.model or m["id"] == a.model]
        write_bundle(sig, cfg)
        ts = [threading.Thread(target=r.consult, args=(dict(sig, status="open"), m)) for m in ms]
        for t in ts: t.start()
        for t in ts: t.join()
        for m in ms:
            rec = load_record(cfg, a.once, m["id"]); last = (rec or {}).get("consults", [{}])[-1]
            print(json.dumps({"model": m["id"], **{k: v for k, v in last.items() if k != "prompt"}}, indent=1)[:2500])
        return
    Runner(cfg).loop()

if __name__ == "__main__": main()
