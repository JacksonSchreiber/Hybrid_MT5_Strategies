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
from live import verdict_fmt as VF

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
    lv = sig.get("levels") or {}
    try: e, R = float(lv.get("entry")), abs(float(lv.get("entry")) - float(lv.get("sl")))
    except (TypeError, ValueError): e = R = 0.0
    up = str(sig.get("direction", "")).upper().startswith("B")
    def dist(p): return ((p - e) if up else (e - p)) / R if R > 0 else 0.0     # + = in the trade direction, in R
    def row(xs):
        return " · ".join(f"{x['p']:.{dg}f} ({x['bars_ago']} bars ago"
                          + (f", {dist(x['p']):+.2f}R" if R > 0 else "") + ")" for x in xs) or "(none in the window)"
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
            "confirmed against the unfinished current bar. No sweep column: the EA tracks no per-swing pool status.\n"
            + wall_line(tbl, dist, R, dg, up))

SLAM_ONE, SLAM_PAIR = 2.0, 1.5      # guide thresholds (coach 2026-09-24): one bar >= 2x ATR, or two consecutive >= 1.5x

def slam_block(sig: dict) -> str:
    """Step 2 in numbers. The two panels read the same 1.1-1.8 ATR bars opposite ways because each eyeballed "slam" off
    the picture; the guide now carries thresholds, so the card carries the measurements they apply to. Pullback = from
    the most recent swing the price retraced FROM (a swing high for a BUY) up to the signal bar, ranges as high-low."""
    bars = charts.signal_bars(sig, "h4"); atr = float((sig.get("sizing") or {}).get("atr14") or 0)
    if len(bars) < 12 or atr <= 0:
        return ("- **Step 2 numbers:** (unavailable - " + ("no H4 bars" if len(bars) < 12 else "no ATR on this card")
                + "; estimate against the last 14 bars on the chart and say you estimated)")
    dg = charts._digits(sig)
    idx = {b[0]: i for i, b in enumerate(bars)}
    si = idx.get(charts._epoch(sig.get("signal_time")), len(bars) - 1)
    up = str(sig.get("direction", "")).upper().startswith("B")
    tbl = OV.swing_table(bars, 5); start = None
    for sw in tbl["hi" if up else "lo"]:                      # newest first: the first one at or before the signal bar
        j = len(bars) - 1 - sw["bars_ago"]
        if j < si: start = (j, sw["p"]); break
    if start is None: j0, from_txt = max(0, si - 7), "no swing of the right kind in the window, so the last 8 bars"
    else: j0, from_txt = start[0], f"from the {start[1]:.{dg}f} swing {'high' if up else 'low'} ({si - start[0]} bar{'' if si - start[0] == 1 else 's'} before the signal bar)"
    seg = bars[j0:si + 1]
    if len(seg) < 2: return "- **Step 2 numbers:** (unavailable - the pullback window came out empty)"
    xs = [(b[2] - b[3]) / atr for b in seg]
    pair = max((min(xs[i], xs[i + 1]) for i in range(len(xs) - 1)), default=0.0)
    slam = max(xs) >= SLAM_ONE or pair >= SLAM_PAIR
    why = (f"largest bar {max(xs):.2f}x" + (" >= 2x" if max(xs) >= SLAM_ONE else "")
           + f", best consecutive pair both >= {pair:.2f}x" + (" >= 1.5x" if pair >= SLAM_PAIR else ""))
    return (f"- **Step 2 numbers (H4 ATR(14) = {atr:.{dg}f}):** pullback {from_txt} - bar ranges as multiples of ATR, "
            f"oldest to newest: {' · '.join(f'{x:.2f}' for x in xs)}. {why}. By the guide's thresholds "
            f"(slam = one bar >= {SLAM_ONE:g}x, or two consecutive >= {SLAM_PAIR:g}x): **{'SLAM' if slam else 'no slam'}**. "
            "Ranges are high-low of each finished H4 bar; the last entry is the signal bar itself.")

WALL_R = 0.10        # the guide's "held twice or more" tolerance - measured in R, not in ATR (coach 2026-09-24)

def wall_line(tbl: dict, dist, R: float, dg: int, up: bool) -> str:
    """Step 5's arithmetic, done FOR the advisor and in the unit the rule is written in. The rule is "a level the table
    shows held twice or more inside the first 1R", tolerance 0.1R - and an advisor that converts that gap into ATR
    lands on the wrong side of it (Opus did exactly that on EURSEK #1: it called a 0.109R pair a "0.11 ATR double top"
    and vetoed on it). So the card states which levels are in the road, which pairs are within 0.1R, and how wide the
    closest pair is."""
    if R <= 0: return "    - (no entry/SL on this card, so the step-5 distances could not be computed)"
    road = sorted(((x["p"], dist(x["p"])) for x in tbl["hi" if up else "lo"] if 0 < dist(x["p"]) <= 1.0), key=lambda z: z[1])
    if not road:
        return "    - **Step 5 arithmetic:** no swing of the blocking kind inside the first 1R — the road to the +1R bank is clear."
    pairs = [(road[i], road[i + 1], road[i + 1][1] - road[i][1]) for i in range(len(road) - 1)]
    walls = [(x, y, g) for x, y, g in pairs if g <= WALL_R]
    txt = " · ".join(f"{p:.{dg}f} at {d:+.2f}R" for p, d in road)
    if walls:
        w = ", ".join(f"{x[0]:.{dg}f}/{y[0]:.{dg}f} ({g:.3f}R apart)" for x, y, g in walls)
        verdict = f"**a level held twice within {WALL_R:.2f}R → step 5 ✗**: {w}"
    else:
        close = min((g for _, _, g in pairs), default=None)
        verdict = (f"no level is held twice within {WALL_R:.2f}R"
                   + (f" (closest pair {close:.3f}R apart)" if close is not None else " (only one level in the road)")
                   + " → step 5 is `?` at worst, quality capped at B, not a veto")
    return f"    - **Step 5 arithmetic (in R, the unit the rule is written in):** in the road to +1R: {txt}. {verdict}."

def d1_ext_line(sig: dict) -> str:
    """D1's distance from its own EMA20, in D1 ATR(14), signed by the trade's direction (coach 2026-09-24). The
    driver study found this is the only measured feature that explains the trader's selection edge, and the blind
    record backs it: above +0.5 ATR the graded TrendCont population returns +0.18R against -0.08R below it."""
    ext = charts.d1_extension(sig)
    if ext is None: return "- **D1 extension:** (unavailable — not enough D1 history on this card)"
    where = ("stretched well away from the mean" if ext > 1.5 else
             ("extended with the trade" if ext >= 0.5 else ("near its mean" if ext > -0.5 else "extended AGAINST this trade")))
    band = "inside the studied band (>= +0.5 ATR)" if ext >= 0.5 else "below the studied band (< +0.5 ATR)"
    return (f"- **D1 extension:** last D1 close sits **{ext:+.2f} ATR** from its own EMA20, signed in the trade's "
            f"direction — {where}, {band}.")


def quality_line(sig: dict) -> str:
    """The Quality grade, computed (coach 2026-09-24): D1 extension >= 1.5 ATR = A, 0.5-1.5 = B, and one grade off for
    a spread of 0.05R or worse, or for the broker-midnight bar (00:00 broker = the 21:00 UTC rollover close, where the
    exotics' spread runs 0.5-1R). Printed so the panel restates the grade instead of inventing one."""
    ext = sig.get("sizing", {}).get("d1_ext")
    if ext is None: ext = charts.d1_extension(sig)          # older cards / EA without the field
    if ext is None: return "- **Quality (mechanical):** not computable — no D1 history on this card."
    ext = float(ext)
    base = "A" if ext >= 1.5 else ("B" if ext >= 0.5 else "C")
    pens = []
    spr = float((sig.get("sizing") or {}).get("spread_r") or 0)
    if spr >= 0.05: pens.append(f"spread {spr:.3f}R (>= 0.05R)")
    bar = str(sig.get("decision_bar") or "")                 # broker clock, as the EA writes it
    if bar[11:13] == "00": pens.append("broker-midnight bar (21:00 UTC rollover)")
    grade = {"A": "B", "B": "C", "C": "C"}[base] if pens else base
    why = f"D1 extension {ext:+.2f} ATR → {base}" + (f", minus one for {' and '.join(pens)}" if pens else ", no penalty")
    return f"- **Quality (mechanical):** **{grade}** — {why}. State this grade; do not invent a different one."


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
{slam_block(sig)}
{d1_ext_line(sig)}
{quality_line(sig)}
- **Sizing:** {sz.get('lots_line', '')} · risk multiplier in effect {sz.get('risk_mult_applied', 1.0)} → effective {float(sz.get('risk_pct_effective', 0.01))*100:.2f}%
- **Entry cost (spread) right now:** {sz.get('spread')} = **{float(sz.get('spread_r') or 0):.3f}R** of the stop{f" (gate: refused above {float(sz.get('max_spread_r') or 0):.2f}R)" if sz.get('max_spread_r') else ""} — you pay this the moment the trade opens
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

MIN_DRAWN = {"h4": 20, "d1": 20}     # fewer bars than this on the image and the advisor is grading a blank chart

def render_health(sig: dict) -> tuple[bool, dict]:
    cnt = {tf: charts.drawn_bars(sig, tf) for tf in ("h4", "d1")}
    return all(cnt[tf] >= MIN_DRAWN[tf] for tf in cnt), cnt

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
    ok, cnt = render_health(sig)
    if not ok:                        # coach 2026-09-24 item 5: ONE re-render, then fail the bundle - never consult a blank chart
        time.sleep(3.0)
        h4, d1 = charts.render_signal(sig, os.path.join(cfg["root"], "web", "charts"), fresh=True)
        _atomic_copy(h4, os.path.join(bdir, "h4.png")); _atomic_copy(d1, os.path.join(bdir, "d1.png"))
        ok, cnt = render_health(sig)
    banner = "" if ok else (f"# BUNDLE FAILED - DO NOT GRADE THIS CARD\n\nThe charts rendered with h4={cnt['h4']} and "
                            f"d1={cnt['d1']} bars drawn (minimum {MIN_DRAWN['h4']}/{MIN_DRAWN['d1']}) after a re-render, so the "
                            "images are blank or near-blank. No consult is run on this bundle; the trader decides unaided.\n\n")
    C.atomic_write_text(os.path.join(bdir, "setup.md"), banner + build_setup_md(sig, cfg) + brief_note + "\n")
    C.atomic_write_json(os.path.join(bdir, "bundle_health.json"), {"ok": ok, "drawn": cnt, "at": C.now_iso()})
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
def pub_of(sig: dict) -> str: return str(sig.get("published_at_server") or sig.get("published_at") or "")
def session_id(key: str, mid: str, pub: str = "") -> str: return str(uuid.uuid5(SESSION_NS, f"{key}|{mid}" + (f"|{pub}" if pub else "")))
def rec_matches(rec: dict | None, sig: dict) -> bool:
    """a record belongs to THIS publication of the key (2026-09-21: a restart re-used EURUSD id 3 for a new setup, which then
    inherited the old setup's verdicts). Records written before the field existed are assumed to match."""
    rp = (rec or {}).get("published_at_server"); sp = pub_of(sig)
    return not rp or not sp or rp == sp

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
    """The runner owns verdicts.live.log and builds every field from the parsed block (CLAUDE.live.md §Verdict log,
    coach 2026-09-24). The model is no longer asked for a LOG: line: one source of truth, and a model drifting from
    the output format shows up as empty fields rather than as a plausible-looking line it wrote itself.

    <ts> | <model> | <symbol> | #<id> | <strat> <dir> | <VERDICT> (conf) | mech:<PASS|VETO:rule> d1ext:<±n.nn>
        | opinion:<W>(conf) | SH:<r><n><c> steps:<...> | Q:<A|B|-> | sources:<n> | <one clause> | <SUMMARY>
    """
    p_ = os.path.join(cfg["advisor"]["live_dir"], "verdicts.live.log")
    pv = VF.parse(text); strat = pv.get("strategy") or sig.get("strategy")
    g = {"ok": "\u2713", "no": "\u2717", "maybe": "?", "na": "-"}
    sh = "".join(g.get(v, "?") for _, v in (pv.get("start") or [])) or "???"
    steps = "".join(f"{n}{g.get(v, '?')}" for n, v in (pv.get("steps") or [])) or "-"
    mech = ("VETO:" + (pv.get("mech_rule") or "?") if pv.get("mech") == "VETO" else (pv.get("mech") or "-"))
    d1 = f"{pv['d1_ext']:+.2f}" if pv.get("d1_ext") is not None else "-"
    opin = (pv.get("opinion") or "-") + (f"({pv['opinion_conf']})" if pv.get("opinion_conf") else "")
    nsrc = len(pv.get("sources") or [])
    clause = VF.plainify((pv.get("why") or "").split(". ")[0], strat)[:150] or "(no Why line)"
    summ = VF.plainify(pv.get("summary"), strat) or "-"
    if pv.get("synthesised"): summ = "(synth) " + summ
    flags = []
    if pv.get("missing"): flags.append("unparsed:" + ",".join(pv["missing"][:4]))
    if pv.get("opinion") and not nsrc: flags.append("UNSOURCED")
    if kind == "reply": flags.append("reply")
    line = " | ".join([C.now_iso(), mid, sig["symbol"], f"#{sig['signal_id']}",
                       f"{sig.get('strategy')} {sig.get('direction')}",
                       f"{pv.get('verdict') or '-'}" + (f" ({pv['confidence']})" if pv.get("confidence") else ""),
                       f"mech:{mech} d1ext:{d1}", f"opinion:{opin}", f"SH:{sh} steps:{steps}",
                       f"Q:{pv.get('quality') or '-'}", f"sources:{nsrc}", clause, summ]
                      + ([" ".join(flags)] if flags else []))
    with _log_lock: C.append_line(p_, line)
    if flags: log(f"{sig['signal_key']}/{mid}: {' '.join(flags)}")

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
            "session_id": session_id(key, m["id"], pub_of(sig)), "published_at_server": pub_of(sig), "status": "idle", "consults": []}

# ----------------------------------------------------------------------------- Opus fallback (coach 2026-09-21, pre-approved)
# Trigger (evaluated by the monitor): rate-limit errors on >= 2 days in a week, or Opus unavailable on > 20 % of a day's
# signals. Step A: no Opus on TAKE-class signals (TAKE-default unless a veto fires - a deep read adds least). Step B (only
# if still straining): Opus on the 8 tested symbols only. The active step and its start live in <root>/advisor/opus_policy.json;
# the coach grades the Sonnet/Opus comparison on the paired subset only.
TESTED_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "US100", "US500", "USOIL", "XAUUSD", "BTCUSD"]

def opus_policy(cfg: dict) -> dict:
    return C.load_json(os.path.join(cfg["root"], "advisor", "opus_policy.json"), None) or {"step": "none", "since": None, "history": []}

def policy_skip(cfg: dict, sig: dict, m: dict) -> str | None:
    """why this model does NOT run on this signal under the active fallback step (None = it runs)."""
    fb = cfg["advisor"].get("opus_fallback") or {}
    if m["id"] != fb.get("model_id", "opus-high"): return None
    pol = opus_policy(cfg); step = pol.get("step", "none")
    if step in ("A", "B") and (sig.get("decision_class") or "") == "TAKE":
        return f"Opus fallback step {step} (since {pol.get('since')}): TAKE-class signals get Sonnet only"
    tested = fb.get("tested_symbols") or TESTED_SYMBOLS
    if step == "B" and (sig.get("symbol") or "").split(".")[0].upper() not in tested:
        return f"Opus fallback step B (since {pol.get('since')}): Opus runs on the {len(tested)} tested symbols only"
    return None

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

    def archive(self, key: str, mid: str, rec: dict) -> None:
        src = verdict_path(self.cfg, key, mid); dst = src[:-5] + f".superseded-{C.now_utc().strftime('%Y%m%dT%H%M%S')}.json"
        try: os.replace(src, dst); self.log(f"{key}/{mid}: record belonged to an earlier publication of this id -> {os.path.basename(dst)}")
        except OSError: pass

    def needs_consult(self, key: str, mid: str, sig: dict | None = None) -> bool:
        rec = load_record(self.cfg, key, mid)
        if rec and sig is not None and not rec_matches(rec, sig): return True
        if rec and rec.get("policy_skip"): return False
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
        mark = os.path.join(bdir, ".rendered"); stamp = f"{pub_of(sig)}|{sig.get('delay_count', 0)}"
        try: have = open(mark).read().strip() == stamp and os.path.exists(os.path.join(bdir, "setup.md"))
        except OSError: have = False
        if not have:
            write_bundle(sig, self.cfg); C.atomic_write_text(mark, stamp)
        health = C.load_json(os.path.join(bdir, "bundle_health.json")) or {"ok": True}
        if not health.get("ok"):      # item 5: a failed render is reported, never graded
            why = f"BUNDLE FAILED: {health.get('drawn')} bars drawn after a re-render - no consult run"
            self.log(f"{key}: {why}")
            for m in todo:
                rec = load_record(self.cfg, key, m["id"]) or new_record(self.cfg, sig, m)
                rec.update(status="failed", note=why)
                rec.setdefault("consults", []).append({"ts": C.now_iso(), "kind": "bundle", "ok": False, "error": why})
                save_record(self.cfg, rec)
            return
        for m in todo: self._spawn(key, m["id"], self.consult, sig, m, force)

    def consult(self, sig: dict, m: dict, force: bool = False) -> None:
        key = sig["signal_key"]; mid = m["id"]
        rec = load_record(self.cfg, key, mid)
        if rec and not rec_matches(rec, sig): self.archive(key, mid, rec); rec = None
        rec = rec or new_record(self.cfg, sig, m)
        why = None if force else policy_skip(self.cfg, sig, m)
        if why:                                              # recorded, so the panel says so and the unpaired set is explicit
            rec.update(status="skipped", note=why, policy_skip=True); rec.pop("queued_at", None); save_record(self.cfg, rec)
            self.log(f"{key}/{mid}: not run - {why}"); return
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
                      f"Start from library/INDEX.md and the distilled notes it points at for this setup, then read the bundle. "
                      f"Answer with the block exactly as CLAUDE.live.md specifies - SUMMARY, VERDICT, Mechanical, the two Checks lines, "
                      f"OPINION, Sources, Why, Changes my mind - using the glyphs ✓ ✗ ? as in the role file. Do NOT write a LOG: line and do "
                      f"not use shell commands (none are available): the runner writes the verdict log from your block.")
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
            todo = [m for m in ms if not self.busy(key, m["id"]) and self.needs_consult(key, m["id"], sig)]
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
