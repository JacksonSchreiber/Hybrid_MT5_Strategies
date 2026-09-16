r"""
brief_runner.py - on-demand Context brief (coach task 2026-09-16): a descriptive macro brief generated on the trader's
request with claude-opus-5 at medium effort, web search enabled, over the subscription transport (same OAuth token
and launcher as the advisor). Fixed template: (a) this week's scheduled events from OUR deployed feed with English
labels (built here, not by the model), (b) central-bank posture per lineup currency, (c) five-session drivers per
lineup symbol, (d) sources. Hard rule: no directional language; a post-generation filter flags violations.
Files (under <root>/advisor/briefs): <UTC>.md, briefs.log, status.json, requests/.
The advisor bundle attaches the newest brief from the past 2 UTC days as market-brief.md (trader change: 2 days).
"""
from __future__ import annotations
import glob, json, os, re, subprocess, sys, threading, time, uuid
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C

LINEUP = ["US100", "US500", "USOIL", "XAUUSD", "EURUSD", "GBPUSD"]
CCYS = ["USD", "EUR", "GBP"]
DIRECTIONAL = re.compile(r"\b(buy|sell|bullish|bearish|go long|go short|long bias|short bias|skip|take the trade|fade|overweight|underweight|"
                         r"buy the dip|sell the rally|upside target|downside target|recommend(?:ed|ation)?|should (?:buy|sell|enter|exit|avoid))\b", re.I)
ROLE = """# Context brief writer (live, sighted)

You write a SHORT descriptive macro brief for a discretionary trader. You research with WebSearch/WebFetch from
PUBLIC sources only (no paywalled sites, never enter credentials). You EXPLAIN; you NEVER VOTE: no buy/sell/skip,
no bullish/bearish, no "should", no targets, no recommendations about any instrument or setup. Describe what
happened, what is scheduled, and what markets are pricing, in plain words. Keep it tight: the whole brief under
~450 words. Cite every claim's source at the end as a list of URLs.
"""

def brief_dir(cfg): return os.path.join(cfg["root"], "advisor", "briefs")

def week_events(cfg: dict) -> str:
    """(a) this week's scheduled events from the deployed feed, Mon 00:00 .. Sun 24:00 UTC, lineup currencies, with labels."""
    rows, cov = C.load_events(cfg); now = C.now_utc()
    mon = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0); sun = mon + timedelta(days=7)
    keep = [r for r in rows if mon <= r["t"] < sun and r["ccy"] in set(CCYS) | {"All"} and r["cls"] in ("V", "W", "C", "H")]
    lines = [f"  - {r['t'].strftime('%a %d %b %H:%M')} UTC  {r['ccy']}  {r['name']}  [{r['cls']}: {r['label']}]" for r in keep[:60]]
    return ("\n".join(lines) if lines else "  - (no classified events for USD/EUR/GBP this week in the deployed feed)") + (f"\n  (feed coverage ends {cov.strftime('%Y-%m-%d')})" if cov else "")

def build_prompt(cfg: dict) -> str:
    now = C.now_utc()
    return f"""Write today's CONTEXT BRIEF ({now.strftime('%A %d %B %Y, %H:%M UTC')}). Use exactly these four sections, in this order, as markdown headings:

## (a) This week's scheduled events
Reproduce this list verbatim (it comes from our deployed calendar; do not add or reorder events, you may add one short plain-English clause per line about what the release is):
{week_events(cfg)}

## (b) Central-bank posture
One line each for USD (Federal Reserve), EUR (ECB), GBP (Bank of England): current policy rate, the last decision and its date, what is currently priced for the next meeting, and the stated stance. Facts only.

## (c) Five-session drivers
One line for each of: US100, US500, USOIL, XAUUSD, EURUSD, GBPUSD. What moved it over the last five trading sessions and why, according to public reporting. Describe; do not characterise the direction as good or bad and do not project it.

## (d) Sources
A bullet list of the public URLs you used (at least one per section).

HARD RULE: no directional language or recommendations about any instrument or setup anywhere in the brief - no buy, sell, skip, bullish, bearish, long, short, target, "should". The brief explains; it never votes. Use WebSearch/WebFetch on public sources only; never a paywalled site. Finish within about 60 seconds of research."""

def claude_env(cfg: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE")}
    a = cfg["advisor"]
    if a.get("token_file"): env["CLAUDE_CODE_OAUTH_TOKEN"] = C.read_secret(a["token_file"])
    if a.get("node_path"): env["PATH"] = a["node_path"] + os.pathsep + env.get("PATH", "")
    return env

def generate(cfg: dict, log, requested_by: str = "web") -> dict:
    bcfg = cfg.get("brief") or {}
    model = bcfg.get("model") or "claude-opus-5"; effort = bcfg.get("effort") or "medium"; timeout_s = int(bcfg.get("timeout_s") or 150)
    bd = brief_dir(cfg); os.makedirs(bd, exist_ok=True)
    work = os.path.join(cfg["advisor"]["live_dir"], "..", "brief"); work = os.path.abspath(work); os.makedirs(os.path.join(work, ".claude"), exist_ok=True)
    C.atomic_write_text(os.path.join(work, "CLAUDE.md"), ROLE)
    C.atomic_write_text(os.path.join(work, ".claude", "settings.json"), json.dumps({"effortLevel": effort, "permissions": {"allow": ["WebSearch", "WebFetch"], "deny": ["Bash", "Write", "Edit", "Read", "Glob", "Grep", "Task", "Agent", "NotebookEdit"]}}))
    sid = str(uuid.uuid4()); prompt = build_prompt(cfg); t0 = time.time(); ts = C.now_utc().strftime("%Y%m%dT%H%M%SZ")
    C.atomic_write_json(os.path.join(bd, "status.json"), {"state": "generating", "started": C.now_iso(), "model": model, "by": requested_by})
    cmd = cfg["advisor"]["claude_cmd"]
    args = [cmd, "-p", "--output-format", "json", "--model", model, "--effort", effort, "--session-id", sid,
            "--allowedTools", "WebSearch,WebFetch", "--disallowedTools", "Bash,Write,Edit,Read,Glob,Grep,Task,Agent,NotebookEdit", "--max-turns", "25"]
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    res = {"ts": ts, "session_id": sid, "model": model, "effort": effort, "by": requested_by}
    try:
        # the prompt goes through STDIN: a multi-line argument through `cmd /c` on Windows gets truncated at the first newline
        p = subprocess.run(args, cwd=work, env=claude_env(cfg), input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
        j = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
        text = j.get("result", "") if not j.get("is_error") else ""
        if not text: res["error"] = (str(j.get("result")) or p.stderr or p.stdout)[:500]
        res["cost_usd"] = j.get("total_cost_usd"); res["turns"] = j.get("num_turns")
    except subprocess.TimeoutExpired:
        text = ""; res["error"] = f"timeout after {timeout_s}s"
    res["elapsed_s"] = round(time.time() - t0, 1)
    if text:
        flags = sorted({m.group(0).lower() for m in DIRECTIONAL.finditer(text)})
        srcs = re.findall(r"https?://[^\s)>\]]+", text)
        res.update({"flags": flags, "sources": len(set(srcs)), "words": len(text.split())})
        head = (f"<!-- generated {res['ts']} | model {model} effort {effort} | session {sid} | elapsed {res['elapsed_s']}s | "
                f"sources {res['sources']} | flags {','.join(flags) if flags else 'none'} -->\n")
        C.atomic_write_text(os.path.join(bd, f"{ts}.md"), head + text.strip() + "\n"); res["file"] = f"{ts}.md"
    C.append_line(os.path.join(bd, "briefs.log"), " | ".join([res["ts"], sid, model + "/" + effort, f"{res['elapsed_s']}s",
                  f"sources={res.get('sources', 0)}", f"flags={','.join(res.get('flags') or []) or 'none'}", f"by={requested_by}", ("ERROR " + res["error"]) if res.get("error") else "ok"]))
    C.atomic_write_json(os.path.join(bd, "status.json"), {"state": "error" if res.get("error") else "idle", "last": res, "finished": C.now_iso()})
    log(f"brief: {'ERROR ' + res['error'] if res.get('error') else 'ok'} in {res['elapsed_s']}s flags={res.get('flags')}")
    return res

def list_briefs(cfg: dict) -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(brief_dir(cfg), "*.md")), reverse=True):
        name = os.path.basename(p)[:-3]
        try: t = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError: continue
        with open(p, encoding="utf-8", errors="replace") as f: head = f.readline()
        m = re.search(r"flags ([^ ]+) -->", head); fl = [] if not m or m.group(1) == "none" else m.group(1).split(",")
        out.append({"name": name, "t": t, "path": p, "flags": fl, "head": head.strip()})
    return out

def latest_brief(cfg: dict, max_age_days: float = 2.0) -> dict | None:
    for b in list_briefs(cfg):
        if C.now_utc() - b["t"] <= timedelta(days=max_age_days): return b
        break
    return None

def read_brief(cfg: dict, name: str) -> tuple[str, str] | None:
    if not C.safe_key(name): return None
    p = os.path.join(brief_dir(cfg), f"{name}.md")
    if not os.path.exists(p): return None
    with open(p, encoding="utf-8", errors="replace") as f: head = f.readline(); body = f.read()
    return head, body

# --- request queue (the web app asks, the advisor runner generates in a thread) ---
def request(cfg: dict, by: str = "web") -> None:
    C.atomic_write_json(os.path.join(brief_dir(cfg), "requests", f"{C.now_utc().strftime('%Y%m%dT%H%M%S')}.json"), {"by": by, "ts": C.now_iso()})

_worker: threading.Thread | None = None
def poll_requests(cfg: dict, log) -> None:
    global _worker
    reqs = sorted(glob.glob(os.path.join(brief_dir(cfg), "requests", "*.json")))
    if not reqs: return
    if _worker and _worker.is_alive(): return
    for r in reqs: 
        try: os.remove(r)
        except OSError: pass
    _worker = threading.Thread(target=generate, args=(cfg, log, "web"), daemon=True); _worker.start()

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); a = ap.parse_args()
    cfg = C.load_config(a.config); log = C.Log("brief", cfg["logs_dir"])
    r = generate(cfg, log, "cli"); print(json.dumps(r, indent=1)); sys.exit(0 if not r.get("error") else 1)

if __name__ == "__main__": main()
