#!/usr/bin/env python3
"""archive_discrimination.py - coach 2026-09-24 item 2: can the advisors tell record winners from record losers?

Runs the 20 blind archive bundles (pipeline/archive_bundles.py) past three cells - Sonnet-low, Opus-low, Opus-high -
and reports each cell's TAKE rate on winners vs losers. The coach's bars: SKIPs >= 18 of 20 = a prior, not a read;
TAKE rate on winners exceeding the losers' by >= 30 points = a read; in between, the live ledger decides.

Offline: no live records, no journals, no shadow clock. Runs ON THE BOX, ssh session kept open.
"""
import collections, concurrent.futures as cf, json, os, re, subprocess, sys, time, uuid

sys.path.insert(0, r"C:\ProgramData\hybrid\live")
from live import advisor_runner as AR, common as C                          # noqa: E402

CFG = C.load_config(r"C:\ProgramData\hybrid\live\live_config.json")
LIVE = CFG["advisor"]["live_dir"]
KEYS = [f"arc-{n:02d}" for n in range(1, 21)]
CELLS = [("sonnet-low", "sonnet", "low", 300), ("opus-high", "claude-opus-5-5", "low", 300),
         ("opus-high", "claude-opus-5-5", "high", 900)]
RAW = r"C:\ProgramData\hybrid\tmp\arc_discrim_raw.txt"


def run(key, hid, model, effort, to):
    rel = f"bundles/{key}"
    m = {"id": hid, "model": model, "effort": effort, "label": hid}
    prompt = AR.header(m) + (
        f"ARCHIVE card {key}. Bundle: {rel}/setup.md, {rel}/h4.png, {rel}/d1.png. Read them all, then answer with the "
        "full scorecard exactly as you would for a live card. The date, the calendar, the open exposure and everything "
        "after the signal bar are withheld by design (see the card) - that is not a failed bundle, so do not treat it "
        "as one: score the structural steps and mark news and correlation not applicable. "
        "End your answer with one final line starting with 'LOG: ' followed by "
        "'TrendCont <dir> | <VERDICT> (quick, <confidence>) | SH:<regime><news><corr> steps:<...> | Q:<A|B|C|-> | "
        "step: <decisive step> | <one-clause reason>' using the glyphs ✓ ✗ ? exactly as in the role file.")
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
        txt = j.get("result", "") if not j.get("is_error") else f"ERROR {str(j.get('result'))[:150]}"
    except subprocess.TimeoutExpired: txt = f"TIMEOUT {to}s"
    except Exception as e: txt = f"ERROR {type(e).__name__}: {e}"[:200]
    el = round(time.time() - t0, 1)
    v = AR.verdict_word(txt) or ("ERR" if txt.startswith(("ERROR", "TIMEOUT")) else "?")
    mm = re.search(r"step:\s*([^|\n]{0,60})", txt)
    with open(RAW, "a", encoding="utf-8") as f:
        f.write(f"\n\n===== {key} | {model} | {effort} | {v} | {el}s\n{txt[:2000]}")
    return {"key": key, "cell": f"{model.split('-')[0]}-{effort}", "v": v, "s": el, "step": mm.group(1).strip() if mm else ""}


jobs = [(k, h, mo, e, to) for k in KEYS for (h, mo, e, to) in CELLS]
print(f"{len(jobs)} runs, 2 at a time\n", flush=True)
rows = []
with cf.ThreadPoolExecutor(max_workers=2) as ex:
    for r in ex.map(lambda a: run(*a), jobs):
        rows.append(r); print(f"  {r['key']} {r['cell']:14} -> {r['v']:6} ({r['s']:6.1f}s) {r['step'][:44]}", flush=True)
json.dump(rows, open(r"C:\ProgramData\hybrid\tmp\arc_discrim.json", "w"), indent=1)
print("\nverdict counts per cell (grading happens off-box, where the manifest is):")
for c in sorted({r["cell"] for r in rows}):
    cnt = collections.Counter(r["v"] for r in rows if r["cell"] == c)
    print(f"  {c:14} " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())))
print("\nper card: " + " ".join(f"{r['key']}:{r['cell'][:6]}={r['v']}" for r in sorted(rows, key=lambda x: (x['key'], x['cell']))))
