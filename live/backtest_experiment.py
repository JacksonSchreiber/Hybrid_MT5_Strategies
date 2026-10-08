"""backtest_experiment.py - trader 2026-10-08: advisor EXPERIMENTS on the cards of a finished Test session (offline, one-off).

E1 "take-raw + 1R plan": every card of the session gets a fresh blind consult (the same bundle, confinement and CLI flags as the Test
advisor - live/backtest_advisor.py) asking for TWO decisions, recorded and graded separately:
  1. RAW: TAKE or SKIP the signal exactly as it comes (in at the signal, full size, out at TP2 or the stop) - a TAKE scores the raw R,
     a SKIP 0;
  2. PLAN, only after a TAKE: the lever plan that takes home the most R without ever risking more than 1R - graded against the raw
     signal on the same trade. The harness forces it: entry at most 100% (else the plan is graded as raw, flagged) and the plan-wide
     risk_cap 1.0 (the engine raises every stop when an add would push the open risk above 1R). An empty lever list = no plan (raw).
The advisor never sees outcomes; the results stay out of the Test record (advisor/<sid>/, the session file, its grades and CSV).
STATE: <root>/web/backtest/experiments/<sid>/<name>/{<hid>.json per card (written as each consult finishes - a rerun resumes),
summary.json}. The session's end page lists the experiments (backtest.card()).
RUN (on the box, from C:/ProgramData/hybrid/live; NOT during a deploy - a deploy stops every Python process):
  python -X utf8 -m live.backtest_experiment --sid <sid> --name e1 --parallel 8 [--summary-only]
PARALLEL: each card is its own fresh consult (its own claude process + bundle folder); 8 at a time because the box has 8 GB and runs MT5.
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, json, os, re, sys, time, uuid
from live import backtest as BT, backtest_advisor as BA, backtest_engine as E

E1_VERSION = "experiment E1 take-raw + 1R plan (2026-10-08)"
RAW_RE = re.compile(r"RAW:\s*\**\s*(TAKE|SKIP)\b", re.I)


def base_rates(S: dict, group: str) -> dict:
    """the group's PRACTICE partition (never the Test signals): how often the raw signal reaches +1R, hits TP2, stops first."""
    hs = [h for h, t in S["split"]["trades"].items() if t["group"] == group and t["part"] == "practice" and h in S["results"]]
    R = S["results"]; n = max(1, len(hs))
    return {"n": len(hs), "reach_1R": sum(1 for h in hs if R[h]["banked_1R"]) / n, "tp2": sum(1 for h in hs if R[h]["hit_tp2"]) / n,
            "stop_first": sum(1 for h in hs if not R[h]["banked_1R"] and R[h]["R"]["raw"] <= -0.9) / n,
            "raw_avg": sum(R[h]["R"]["raw"] for h in hs) / n, "minimal_avg": sum(R[h]["R"]["minimal"] for h in hs) / n}


def role_e1(br: dict) -> str:
    lev = BA._LEVERS_V3[BA._LEVERS_V3.index("- Use only these lever types"):]
    return f"""# Blind backtest advisor ({E1_VERSION})

You advise a discretionary trader on ONE historical trade signal at a time, shown BLIND: no symbol, no dates, prices in R from the
entry, time counted back from the signal bar. You cannot look anything up - you have no web access, and you must not try to work out
the symbol or the period: judge the setup on the card.

Your working folder holds exactly one card: read `card.md` and its two charts `h4.png` (the last ~2 months of H4 bars) and `d1.png`
(the last 44 days; the last day is partial, up to the signal). `h4_bars.csv` has the H4 bars as numbers if you need precision. You
can only read files in this folder.

TWO SEPARATE DECISIONS, graded separately:
1. RAW: would you TAKE or SKIP this signal exactly as it comes - in at the signal with the full ruled size, out at TP2 or at the stop,
   nothing else? A TAKE scores the raw signal's R; a SKIP scores 0.
2. PLAN, only if you said TAKE: the management plan that takes home the most R from this trade without ever risking more than 1R. It
   is graded on its R against the raw signal's R on the same trade. An empty lever list ("no plan") is a valid answer when you think
   the raw signal is the best way to hold it.
   - Never more than 1R at risk: the entry is at most 100% of the ruled size, and the engine raises every open stop when an add would
     push the open risk above 1R (the add is skipped if it cannot fit).
   - Think about every way this trade can play out, not only the best one. Facts from this group's Practice signals (not advice):
     {br['reach_1R']:.0%} reach +1R, {br['tp2']:.0%} hit TP2, {br['stop_first']:.0%} hit the stop before +1R; the raw signal averages
     {br['raw_avg']:+.3f}R and Minimal {br['minimal_avg']:+.3f}R per signal.
   - Base each change on something you can point to on this card (swings / zone / EMAs against the stop, TP1, TP2; the trend; when
     the next event is) and name it in the rationale. The presets in card.md (their exact levers listed) are starting points.

Answer in exactly this shape (the server parses it):

RAW: TAKE or SKIP
CONFIDENCE: a whole number 0-100 (that the raw signal makes money)
WHY: one or two plain lines

and, only after RAW: TAKE, the plan:

```levers
{{"schema": "advisor-levers/v1", "nearest_preset": "<raw|minimal|ruthless|slow|banker|pyramid>", "rationale": "<ONE line, under 300 characters: what on this card set the plan>",
 "plan": {{"entry": {{"pct": 100}}, "tp2": true, "levers": [ ... ]}}}}
```

Rules for the plan:
{lev}- The entry is at most 100% (the 1R limit); nearest_preset = the preset your plan is closest to ("raw" for no plan) - a label only.
Keep it short. Do not write files.
"""


def _job(S: dict, s: dict, i: int) -> dict:
    """the same blind job the Test batch writes (backtest_advisor.queue_session) - the card only, a fresh random bundle folder."""
    g = s["group"]; info = S["info"]["groups"][g]; menu = S["menu"]; dfl = menu["defaults"][g]; hid = s["ids"][i]
    c = dict(S["cards"][hid]); c.pop("hid", None)
    return {"i": i, "hid": hid, "bundle": uuid.uuid4().hex[:16], "group_name": info["name"], "card": c, "info_lines": info["lines"],
            "preload": {"label": dfl["label"], "about": dfl["about"], "cfg": dfl["cfg"]},
            "presets": [{"key": p["key"], "label": p["label"], "about": p["about"], "cfg": p["cfg"]} for p in menu["presets"]],
            "menu_version": menu["version"]}


def parse_e1(text: str) -> dict:
    m = RAW_RE.search(text or ""); _, conf, why = BA.parse_verdict(text)
    out = {"raw": m.group(1).upper() if m else None, "confidence": conf, "why": why, "plan": None, "plan_note": "", "nearest_preset": None, "rationale": ""}
    if out["raw"] != "TAKE": return out
    if not BT.FENCE.search(text or ""):
        out.update(plan=E.validate({"schema": E.SCHEMA, "levers": [], "risk_cap": 1.0})[0], plan_note="no levers block - graded as raw"); return out
    try:
        p = BT.parse_recommendation(text, menu_keys=BT.PRESET_KEYS + ("raw",))
        if p["plan"]["entry"]["pct"] > 100 + 1e-9: raise ValueError(f"entry {p['plan']['entry']['pct']:g}% is over 100% (the 1R limit)")
        out.update(plan=E.validate(dict(p["plan"], risk_cap=1.0))[0], nearest_preset=p["nearest_preset"], rationale=p["rationale"],
                   plan_note="no plan (raw)" if not p["plan"]["levers"] else "")
    except Exception as e:
        out.update(plan=E.validate({"schema": E.SCHEMA, "levers": [], "risk_cap": 1.0})[0], plan_note=f"plan rejected ({str(e)[:160]}) - graded as raw")
    return out


def consult_one(cfg: dict, S: dict, s: dict, i: int, d: str, role: str, consult=None) -> dict:
    job = _job(S, s, i); bd = BA.write_bundle(cfg, job, role); st = BA.settings(cfg); last = None
    prompt = role + "\n\n---\n\nAdvise on the blind card in your working folder (read card.md, h4.png and d1.png). Answer exactly in the shape given above."
    for att in range(3):                                # rate limits / a slow start: retry twice
        r = (consult or BA.call_claude)(cfg, prompt, st, None, bd)
        if r.get("ok"): break
        last = r.get("error"); time.sleep(20 * (att + 1))
    else:
        rec = {"i": i, "hid": job["hid"], "state": "failed", "error": str(last)[:300], "role": E1_VERSION, "at": BT._now()}
        BA._write(os.path.join(d, f"{job['hid']}.json"), rec); return rec
    rec = {"i": i, "hid": job["hid"], "state": "done", "role": E1_VERSION, "at": BT._now(), "elapsed_s": r.get("elapsed_s"),
           "text": (r.get("text") or "")[:6000], **parse_e1(r.get("text") or "")}
    BA._write(os.path.join(d, f"{job['hid']}.json"), rec); return rec


def exp_dir(cfg: dict, sid: str, name: str) -> str: return BT.web_dir(cfg, "experiments", sid, name)


def run(cfg: dict, sid: str, name: str, parallel: int = 8, consult=None, log=print) -> None:
    S = BT.store(cfg); s = BT.load_session(cfg, sid); d = exp_dir(cfg, sid, name); br = base_rates(S, s["group"]); role = role_e1(br)
    todo = []
    for i in range(s["n"]):
        p = os.path.join(d, f"{s['ids'][i]}.json")
        try: done = json.load(open(p)).get("state") == "done"
        except (OSError, ValueError): done = False
        if not done: todo.append(i)
    log(f"{name}: {len(todo)} of {s['n']} cards to consult, {parallel} at a time")
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(consult_one, cfg, S, s, i, d, role, consult): i for i in todo}
        for k, f in enumerate(cf.as_completed(futs), 1):
            try: r = f.result(); log(f"  card {futs[f] + 1}: {r.get('state')} {r.get('raw') or ''} ({k}/{len(todo)}, {time.time() - t0:.0f}s)")
            except Exception as e: log(f"  card {futs[f] + 1}: ERROR {e!r}")
    write_summary(cfg, sid, name, br)


def _t(xs):
    if len(xs) < 2: return None
    m = sum(xs) / len(xs); sd = (sum((v - m) ** 2 for v in xs) / (len(xs) - 1)) ** 0.5
    return round(m / (sd / len(xs) ** 0.5), 2) if sd > 0 else None


def write_summary(cfg: dict, sid: str, name: str, br: dict | None = None) -> dict:
    S = BT.store(cfg); s = BT.load_session(cfg, sid); d = exp_dir(cfg, sid, name); b = s.get("block") or 20
    br = br or base_rates(S, s["group"])
    sel_rows, plan_rows, cards, failed = [], [], [], 0
    for i in range(s["n"]):
        hid = s["ids"][i]
        try: rec = json.load(open(os.path.join(d, f"{hid}.json")))
        except (OSError, ValueError): rec = {"state": "missing"}
        if rec.get("state") != "done" or rec.get("raw") not in ("TAKE", "SKIP"):
            if rec.get("state") in ("failed", "done"): failed += 1          # failed, or answered without a RAW line (not yet run: neither)
            continue
        R = S["results"][hid]; sp = R.get("stop_pips"); gr = R.get("gross") or {}
        cost = (lambda u: BT.PIP_COST * u / sp) if sp else (lambda u: 0.0)
        take = rec["raw"] == "TAKE"; raw = R["R"]["raw"]; raw1 = raw - cost(gr.get("raw", 1.0))
        ev = BT._evaluate(S, hid, rec["plan"]) if take else None
        pR = ev["R"] if ev else 0.0; pR1 = (ev["R"] - cost(ev["gross"])) if ev else 0.0
        base = {"adv_graded": True, "adv_taken": take, "R_minimal": R["R"]["minimal"], "R_minimal_1pip": R["R"]["minimal"] - cost(gr.get("minimal", 1.0)),
                "R_raw": raw, "R_raw_1pip": raw1, "reached_1R": bool(R["banked_1R"]), "went_to_stop": bool((not R["banked_1R"]) and raw <= -0.9), "blk": i // b, "i": i}
        sel_rows.append({**base, "R_adv_alone": raw if take else 0.0, "R_adv_alone_1pip": raw1 if take else 0.0, "R_adv_plan": raw if take else None})
        plan_rows.append({**base, "R_adv_alone": pR, "R_adv_alone_1pip": pR1, "R_adv_plan": pR if take else None})
        c = S["cards"][hid]; L = (rec.get("plan") or {}).get("levers") or []
        cards.append({"card": i + 1, "detector": c.get("detector"), "direction": c.get("direction"), "raw": rec["raw"], "confidence": rec.get("confidence"),
                      "R_raw": raw, "R_plan": round(pR, 4) if take else None, "R_minimal": R["R"]["minimal"], "hit_tp2": bool(R["hit_tp2"]),
                      "reached_1R": bool(R["banked_1R"]), "nearest_preset": rec.get("nearest_preset"), "plan_note": rec.get("plan_note", ""),
                      "levers": ", ".join(sorted({l["type"] for l in L})) if take else "", "why": rec.get("why") or "", "rationale": rec.get("rationale") or ""})

    def part(lo, hi):
        sr = [r for r in sel_rows if lo <= r["i"] < hi]; pr = [r for r in plan_rows if lo <= r["i"] < hi]
        tk = [(p, q) for p, q in zip(sr, pr) if p["adv_taken"]]
        dpr = [q["R_adv_alone"] - p["R_adv_alone"] for p, q in tk]; dpm = [q["R_adv_alone"] - q["R_minimal"] for p, q in tk]
        return {"cards": f"{lo + 1}-{hi}", "n": len(sr), "taken": len(tk),
                "all_raw": round(sum(r["R_raw"] for r in sr), 3), "all_minimal": round(sum(r["R_minimal"] for r in sr), 3),
                "d1_raw_on_taken": round(sum(p["R_adv_alone"] for p, _ in tk), 3),
                "d2_plan_on_taken": round(sum(q["R_adv_alone"] for _, q in tk), 3),
                "d2_minimal_on_taken": round(sum(q["R_minimal"] for _, q in tk), 3),
                "d2_plan_minus_raw": {"total": round(sum(dpr), 3), "mean": round(sum(dpr) / len(dpr), 4) if dpr else None, "t": _t(dpr)},
                "d2_plan_minus_minimal": {"total": round(sum(dpm), 3), "mean": round(sum(dpm) / len(dpm), 4) if dpm else None, "t": _t(dpm)}}
    n = s["n"]; spans = [(0, n)] + ([(0, b), (b, n)] if n > b else []) + [(k * b, min(n, (k + 1) * b)) for k in range(-(-n // b))]
    parts = [part(lo, hi) for k, (lo, hi) in enumerate(spans) if (lo, hi) not in spans[:k]]
    summ = {"name": name, "title": "Experiment E1 - take or skip the raw signal, then a plan that never risks more than 1R", "version": E1_VERSION,
            "sid": sid, "at": BT._now(), "answered": len(cards), "total": n, "failed": failed, "base_rates_practice": br,
            "decision1": BT._grade(sel_rows, "advisor"), "combined": BT._grade(plan_rows, "advisor"), "parts": parts,
            "no_plan": sum(1 for c in cards if c["plan_note"] == "no plan (raw)"), "plan_flagged": sum(1 for c in cards if "graded as raw" in c["plan_note"]),
            "notes": "Decision 1 = take/skip the RAW signal (a TAKE scores raw R, a SKIP 0). Decision 2 = its plan on the trades it took, against raw "
                     "on the same trades (entry <= 100%, every add capped at 1R open risk by the engine). Model cost. The advisor is blind, but this "
                     "role was written after these cards were revealed - a good result is a hint to confirm on fresh Test signals.",
            "cards": cards}
    BA._write(os.path.join(d, "summary.json"), summ)
    return summ


def list_summaries(cfg: dict, sid: str) -> list:
    root = BT.web_dir(cfg, "experiments", sid); out = []
    for nm in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, nm, "summary.json")
        try: out.append(json.load(open(p)))
        except (OSError, ValueError): pass
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default=r"C:\ProgramData\hybrid\live\live_config.json")
    ap.add_argument("--sid", required=True); ap.add_argument("--name", default="e1"); ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--summary-only", action="store_true"); a = ap.parse_args()
    cfg = json.load(open(a.config))
    if not re.fullmatch(r"[a-z0-9_]{1,24}", a.name): sys.exit("bad experiment name")
    if a.summary_only: write_summary(cfg, a.sid, a.name)
    else: run(cfg, a.sid, a.name, a.parallel, log=lambda m: print(m, flush=True))
