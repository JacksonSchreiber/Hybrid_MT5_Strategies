"""advisor_effort_swap.py - 2x2 replay: does the SKIP follow the MODEL or the EFFORT? (trader 2026-09-24)

Replays four signals the two advisors disagreed on, through all four model x effort cells, from the SAME bundle files
the live consult read. Identical prompt and header to the live run; the only differences are --effort and that writes
are denied (the verdict lives in the response text, not the notes file). Nothing touches the live verdict records.

Runs ON THE BOX, and keep the ssh session open - a detached Start-Process dies with the session:
    scp pipeline/advisor_effort_swap.py hybridops@10.77.0.1:C:/ProgramData/hybrid/tmp/
    ssh hybridops@10.77.0.1 "& 'C:\\Program Files\\Python312\\python.exe' -X utf8 -u C:\\ProgramData\\hybrid\\tmp\\advisor_effort_swap.py"

2026-09-24 result (4 signals the panels disagreed on): Opus SKIP in all 8 cells - including low effort at 13-17 s -
while Sonnet took 3 of 4 at low effort and flipped one (CADJPY) at high. Sonnet also flipped USOIL #1 against its own
live verdict on the identical bundle, so its TAKE is not run-to-run stable; Opus reproduced all four live SKIPs.
Transcripts: data/study/advisor_effort_swap_2026-09-24.txt.
"""
import concurrent.futures as cf, json, os, re, subprocess, sys, time, uuid

sys.path.insert(0, r"C:\ProgramData\hybrid\live")
from live import advisor_runner as AR, common as C                      # noqa: E402

CFG = C.load_config(r"C:\ProgramData\hybrid\live\live_config.json") if hasattr(C, "load_config") else json.load(open(r"C:\ProgramData\hybrid\live\live_config.json"))
KEYS = ["EURSEK.sim-1", "CADJPY.sim-1", "USOIL.sim-1", "BTCUSD.sim-2"]
CELLS = [("sonnet-low", "sonnet", "low", 300), ("sonnet-low", "sonnet", "high", 900),
         ("opus-high", "claude-opus-5-5", "low", 300), ("opus-high", "claude-opus-5-5", "high", 900)]
LIVE = os.path.join(CFG["advisor"]["live_dir"])


def run(key, hid, model, effort, to):
    sig = C.signal(CFG, key)
    if not sig: return (key, model, effort, "NO SIGNAL", 0, "")
    m = {"id": hid, "model": model, "effort": effort, "label": hid}
    rel = f"bundles/{key}"
    bdir = os.path.join(LIVE, "bundles", key)
    prompt = AR.header(m) + (
        f"#{sig['signal_id']} — LIVE signal {sig['symbol']} {sig.get('strategy')} {sig.get('direction')}. "
        f"Bundle: {rel}/setup.md, {rel}/h4.png, {rel}/d1.png"
        + (f", {rel}/market-brief.md (context brief - §Context brief rules apply)" if os.path.exists(os.path.join(bdir, "market-brief.md")) else "")
        + ". Read them all, then answer with the full scorecard. "
        f"Do NOT write to verdicts.live.log yourself and do not use shell commands (none are available): the runner appends the log line. "
        f"End your answer with one final line starting with 'LOG: ' followed by the verdict-log fields after the '#id' field, i.e. "
        f"'LOG: {sig.get('strategy')} {sig.get('direction')} | <VERDICT> (quick, <confidence>) | SH:<regime><news><corr> steps:<...> | Q:<A|B|C|-> | step: <decisive step> | <one-clause reason>' "
        f"using the glyphs ✓ ✗ ? exactly as in the role file (e.g. 'SH:✓✓✗ steps:1✓2✗3✗4?5?6✓').")
    cmd = CFG["advisor"]["claude_cmd"]
    args = [cmd, "-p", "--output-format", "json", "--model", model, "--effort", effort,
            "--allowedTools", "Read,Glob,Grep", "--disallowedTools", AR.DISALLOWED + ",Write,Edit",
            "--max-turns", "40", "--session-id", str(uuid.uuid4())]
    if os.name == "nt" and cmd.lower().endswith((".cmd", ".bat")): args = ["cmd", "/c"] + args
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=LIVE, env=AR.claude_env(CFG), input=prompt, capture_output=True,
                           text=True, timeout=to, encoding="utf-8", errors="replace")
        j = json.loads(p.stdout)
        txt = j.get("result", "") if not j.get("is_error") else f"ERROR {str(j.get('result'))[:120]}"
    except subprocess.TimeoutExpired: txt = f"TIMEOUT {to}s"
    except Exception as e: txt = f"ERROR {type(e).__name__}: {e}"[:160]
    el = round(time.time() - t0, 1)
    v = AR.verdict_word(txt) or ("ERR" if txt.startswith(("ERROR", "TIMEOUT")) else "?")
    step = ""
    mm = re.search(r"step:\s*([^|\n]{0,60})", txt)
    if mm: step = mm.group(1).strip()
    with open(r"C:\ProgramData\hybrid\tmp\effort_swap_raw.txt", "a", encoding="utf-8") as f:
        f.write(f"\n\n===== {key} | {model} | {effort} | {v} | {el}s\n{txt[:2500]}")
    return (key, model, effort, v, el, step)


jobs = [(k, h, mo, e, to) for k in KEYS for (h, mo, e, to) in CELLS]
print(f"{len(jobs)} runs, 2 at a time\n")
rows = []
with cf.ThreadPoolExecutor(max_workers=2) as ex:
    for r in ex.map(lambda a: run(*a), jobs):
        rows.append(r); print(f"  {r[0]:14} {r[1]:16} {r[2]:5} -> {r[3]:5} ({r[4]:6.1f}s) {r[5][:50]}", flush=True)

print(f"\n{'signal':14} {'sonnet low':>11} {'sonnet high':>12} {'opus low':>10} {'opus high':>10}")
for k in KEYS:
    d = {(r[1], r[2]): r[3] for r in rows if r[0] == k}
    print(f"{k:14} {d.get(('sonnet','low'),'-'):>11} {d.get(('sonnet','high'),'-'):>12} "
          f"{d.get(('claude-opus-5-5','low'),'-'):>10} {d.get(('claude-opus-5-5','high'),'-'):>10}")
