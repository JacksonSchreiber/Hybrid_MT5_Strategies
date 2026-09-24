#!/usr/bin/env python3
"""advisor_amended_replay.py - coach 2026-09-24 items 1 + 3: replay the four disputed bundles under the AMENDED
TrendCont steps 2/5, three cells x three repetitions, and report flip rate per cell.

Offline: nothing touches the live verdict records, the journals or the shadow clock. The card is rebuilt from the EA's
OWN embedded bars (bars_h4/bars_d1 in the signal JSON = the bars as of publication), not the live feed, so the replay
sees what the advisor saw; the original h4.png/d1.png are copied across untouched. Each run gets a fresh session id,
which is what makes the CLI load the amended CLAUDE.md.

Runs ON THE BOX, ssh session kept open (a detached Start-Process dies with the session):
    scp pipeline/advisor_amended_replay.py hybridops@10.77.0.1:C:/ProgramData/hybrid/tmp/
    ssh hybridops@10.77.0.1 "& 'C:\\Program Files\\Python312\\python.exe' -X utf8 -u C:\\ProgramData\\hybrid\\tmp\\advisor_amended_replay.py"
"""
import collections, concurrent.futures as cf, json, os, re, shutil, subprocess, sys, time, uuid

sys.path.insert(0, r"C:\ProgramData\hybrid\live")
from live import advisor_runner as AR, charts, common as C                  # noqa: E402

CFG = C.load_config(r"C:\ProgramData\hybrid\live\live_config.json")
LIVE = CFG["advisor"]["live_dir"]
KEYS = ["EURSEK.sim-1", "CADJPY.sim-1", "USOIL.sim-1", "BTCUSD.sim-2"]
CELLS = [("sonnet-low", "sonnet", "low", 300), ("opus-high", "claude-opus-5-5", "low", 300),
         ("opus-high", "claude-opus-5-5", "high", 900)]
REPS = 3
RAW = r"C:\ProgramData\hybrid\tmp\amended_replay_raw.txt"

# Publication-time bars, not today's feed: the live EA writes bars_h4/bars_d1 EMPTY (the box reads the terminal
# directly), so the replay asks the feed for the bars up to the moment the signal was published. Signal timestamps are
# broker clock, which is exactly what bars_range() wants.
from live import mt5feed                                                    # noqa: E402
mt5feed.configure_url("http://127.0.0.1:8081")
STEP = {"h4": 4 * 3600, "d1": 24 * 3600}
_bars_cache: dict = {}

def pub_bars(sig, tf):
    key = (sig["signal_key"], tf)
    if key not in _bars_cache:
        t_to = int(C.parse_iso(sig.get("published_at")).timestamp())
        n = 500 if tf == "h4" else 400
        _bars_cache[key] = mt5feed.bars_range(sig["symbol"], tf, t_to - n * STEP[tf], t_to) or []
    return _bars_cache[key]

charts.signal_bars = pub_bars


def build(key):
    sig = C.signal(CFG, key)
    src = os.path.join(LIVE, "bundles", key); dst = os.path.join(src, "amended")
    os.makedirs(dst, exist_ok=True)
    for f in ("h4.png", "d1.png", "market-brief.md"):
        if os.path.exists(os.path.join(src, f)): shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))
    C.atomic_write_text(os.path.join(dst, "setup.md"), AR.build_setup_md(sig, CFG) + "\n")
    return sig, dst


def run(key, sig, hid, model, effort, to, rep):
    rel = f"bundles/{key}/amended"
    m = {"id": hid, "model": model, "effort": effort, "label": hid}
    prompt = AR.header(m) + (
        f"#{sig['signal_id']} — LIVE signal {sig['symbol']} {sig.get('strategy')} {sig.get('direction')}. "
        f"Bundle: {rel}/setup.md, {rel}/h4.png, {rel}/d1.png"
        + (f", {rel}/market-brief.md (context brief - §Context brief rules apply)" if os.path.exists(os.path.join(LIVE, rel, "market-brief.md")) else "")
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
        txt = j.get("result", "") if not j.get("is_error") else f"ERROR {str(j.get('result'))[:150]}"
    except subprocess.TimeoutExpired: txt = f"TIMEOUT {to}s"
    except Exception as e: txt = f"ERROR {type(e).__name__}: {e}"[:200]
    el = round(time.time() - t0, 1)
    v = AR.verdict_word(txt) or ("ERR" if txt.startswith(("ERROR", "TIMEOUT")) else "?")
    mm = re.search(r"step:\s*([^|\n]{0,70})", txt)
    with open(RAW, "a", encoding="utf-8") as f:
        f.write(f"\n\n===== {key} | {model} | {effort} | rep{rep} | {v} | {el}s\n{txt[:2500]}")
    return {"key": key, "cell": f"{model.split('-')[0]}-{effort}", "rep": rep, "v": v, "s": el,
            "step": (mm.group(1).strip() if mm else "")}


sigs = {}
for k in KEYS:
    sig, dst = build(k); sigs[k] = sig
    card = open(os.path.join(dst, "setup.md"), encoding="utf-8").read()
    line = next((l for l in card.splitlines() if l.startswith("- **Step 2 numbers")), "(no step-2 line!)")
    print(f"{k}: {line[:190]}", flush=True)

jobs = [(k, sigs[k], h, mo, e, to, r) for k in KEYS for (h, mo, e, to) in CELLS for r in range(1, REPS + 1)]
print(f"\n{len(jobs)} runs, 2 at a time\n", flush=True)
rows = []
with cf.ThreadPoolExecutor(max_workers=2) as ex:
    for r in ex.map(lambda a: run(*a), jobs):
        rows.append(r); print(f"  {r['key']:14} {r['cell']:14} rep{r['rep']} -> {r['v']:5} ({r['s']:6.1f}s) {r['step'][:48]}", flush=True)

cells = [f"{mo.split('-')[0]}-{e}" for (_, mo, e, _) in CELLS]
print(f"\n{'signal':14} " + "  ".join(f"{c:>22}" for c in cells))
for k in KEYS:
    out = []
    for c in cells:
        vs = [r["v"] for r in rows if r["key"] == k and r["cell"] == c]
        out.append(f"{'/'.join(vs):>22}")
    print(f"{k:14} " + "  ".join(out))
print("\nflip rate (a cell/signal whose three reps are not unanimous):")
for c in cells:
    n = flips = 0
    for k in KEYS:
        vs = [r["v"] for r in rows if r["key"] == k and r["cell"] == c]
        n += 1; flips += len(set(vs)) > 1
    print(f"  {c:14} {flips}/{n} signals unstable")
json.dump(rows, open(r"C:\ProgramData\hybrid\tmp\amended_replay.json", "w"), indent=1)
