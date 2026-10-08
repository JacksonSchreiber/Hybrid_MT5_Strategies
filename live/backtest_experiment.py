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
import argparse, concurrent.futures as cf, json, os, re, sys, threading, time, uuid
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
    summ["kind"] = "e1"; e1_display(summ)
    BA._write(os.path.join(d, "summary.json"), summ)
    return summ


def research_lock(cfg: dict) -> dict:
    """the same reader as webapp.research_lock (the trader's <root>/web/research_lock.json, else symbol_groups.RESEARCH_LOCK)."""
    from live import symbol_groups as SG
    d = SG.RESEARCH_LOCK; known = {x for g in SG.GROUPS for x in g[3]}
    try: j = json.load(open(os.path.join(cfg["root"], "web", "research_lock.json")))
    except (OSError, ValueError): j = None
    if isinstance(j, dict) and isinstance(j.get("locked"), list) and isinstance(j.get("exposed"), list):
        lk = [x for x in j["locked"] if x in known]
        return {"locked": lk, "period_from": d["period_from"], "exposed": [x for x in j["exposed"] if x in known and x not in lk]}
    return {"locked": list(d["locked"]), "period_from": d["period_from"], "exposed": list(d["exposed"])}


def list_summaries(cfg: dict, sid: str) -> list:
    root = BT.web_dir(cfg, "experiments", sid); out = []
    for nm in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, nm, "summary.json")
        try: out.append(json.load(open(p)))
        except (OSError, ValueError): pass
    return out



# ============================================================================================ display (all experiments, raw only)
def _tbl(cols, rows): return {"cols": cols, "rows": rows}


def e1_display(summ: dict) -> None:
    """trader 2026-10-08: compared with the raw signal only (Minimal is shaped - never a yardstick)."""
    summ["table"] = _tbl([{"h": "Cards"}, {"h": "Took (raw)"}, {"h": "Every signal raw", "r": 1}, {"h": "Decision 1: raw on the ones it took", "r": 1},
                          {"h": "Decision 2: its plan on those", "r": 1}, {"h": "Plan minus raw on those", "r": 1}, {"h": "t"}],
                         [[p["cards"], f"{p['taken']} of {p['n']}", p["all_raw"], p["d1_raw_on_taken"], p["d2_plan_on_taken"],
                           p["d2_plan_minus_raw"]["total"], p["d2_plan_minus_raw"]["t"]] for p in summ["parts"]])
    summ["card_table"] = _tbl([{"h": "Card"}, {"h": "Signal"}, {"h": "Raw call"}, {"h": "Raw R", "r": 1}, {"h": "Its plan", "r": 1}, {"h": "TP2?"},
                               {"h": "Plan levers"}, {"h": "Why", "w": 1}],
                              [[c["card"], f"{c['detector']} · {c['direction']}", c["raw"] + (f" ({c['confidence']})" if c["confidence"] is not None else ""),
                                c["R_raw"], c["R_plan"], "yes" if c["hit_tp2"] else ("+1R" if c["reached_1R"] else ""),
                                (c["plan_note"] or c["levers"] or "-") if c["raw"] == "TAKE" else "", c["rationale"] if c["raw"] == "TAKE" and c["rationale"] else c["why"]]
                               for c in summ["cards"]])
    summ["notes"] = ("Decision 1 = take/skip the RAW signal (a TAKE scores raw R, a SKIP 0). Decision 2 = its plan on the trades it took, against raw on "
                     "the same trades (entry <= 100%, every add capped at 1R open risk). Model cost. Written after these cards were revealed - a hint to "
                     "confirm on fresh Test signals.")


def _spans(n: int, b: int) -> list:
    sp = [(0, n)] + ([(0, b), (b, n)] if n > b else []) + [(k * b, min(n, (k + 1) * b)) for k in range(-(-n // b))]
    return [x for k, x in enumerate(sp) if x not in sp[:k]]


def plain_notes(S: dict, group: str) -> list:
    """broad behaviour only (trader 2026-10-08: no rates, no success numbers, no presets): the group's own description line."""
    out = []
    for l in S["info"]["groups"][group]["lines"]:
        t = l["text"]
        if t.lower().startswith("behaviour"): out.append(t.split(":", 1)[1].strip() if ":" in t else t)
    return out or [S["info"]["groups"][group]["name"]]


def _plain_job(S, s, i, token=None) -> dict:
    j = _job(S, s, i); j["plain_notes"] = plain_notes(S, s["group"])
    if token: j["bundle"] = token
    return j


def _sonnet(cfg) -> dict:
    st = dict(BA.settings(cfg)); st["model"] = SONNET; return st


SONNET = "claude-sonnet-5-5"          # trader 2026-10-08: experiments E2 / E3 run on Sonnet 5.5 to save tokens
BLIND_HEAD = """You advise a discretionary trader on ONE historical trade signal, shown BLIND: no symbol, no dates, prices in R from the entry,
time counted back from the signal bar. You cannot look anything up - you have no web access, and you must not try to work out the symbol
or the period: judge it on what is in your folder. You can only read files in your working folder."""


# ============================================================================================ E2: take or skip the raw signal
E2_VERSION = "experiment E2 take-or-skip raw, Sonnet (2026-10-08)"


def role_e2() -> str:
    return f"""# Blind backtest advisor ({E2_VERSION})

{BLIND_HEAD}

Read `card.md` and its two charts `h4.png` (the last ~2 months of H4 bars) and `d1.png` (the last 44 days; the last day is partial, up to
the signal); `h4_bars.csv` has the H4 bars as numbers.

ONE DECISION: TAKE or SKIP this signal exactly as it comes - in at the signal with the full ruled size, out at TP2 or at the stop, nothing
changed. Take it by default. SKIP only the ones that look horrible.

Answer in exactly this shape:

RAW: TAKE or SKIP
CONFIDENCE: a whole number 0-100 (how sure you are of that call)
WHY: one or two plain lines

Keep it short. Do not write files.
"""


def consult_e2(cfg, S, s, i, d, role, consult=None) -> dict:
    job = _plain_job(S, s, i); bd = BA.write_bundle(cfg, job, role); last = None
    prompt = role + "\n\n---\n\nRead the blind card in your working folder and answer exactly in the shape above."
    for att in range(3):
        r = (consult or BA.call_claude)(cfg, prompt, _sonnet(cfg), None, bd)
        if r.get("ok"): break
        last = r.get("error"); time.sleep(20 * (att + 1))
    else:
        rec = {"i": i, "hid": job["hid"], "state": "failed", "error": str(last)[:300], "role": E2_VERSION, "at": BT._now()}
        BA._write(os.path.join(d, f"{job['hid']}.json"), rec); return rec
    t = r.get("text") or ""; m = RAW_RE.search(t); _, conf, why = BA.parse_verdict(t)
    rec = {"i": i, "hid": job["hid"], "state": "done", "role": E2_VERSION, "model": SONNET, "at": BT._now(), "elapsed_s": r.get("elapsed_s"),
           "text": t[:4000], "raw": m.group(1).upper() if m else None, "confidence": conf, "why": why}
    BA._write(os.path.join(d, f"{job['hid']}.json"), rec); return rec


def summary_e2(cfg, sid, name) -> dict:
    S = BT.store(cfg); s = BT.load_session(cfg, sid); d = exp_dir(cfg, sid, name); b = s.get("block") or 20
    rows, failed = [], 0
    for i in range(s["n"]):
        hid = s["ids"][i]
        try: rec = json.load(open(os.path.join(d, f"{hid}.json")))
        except (OSError, ValueError): continue
        if rec.get("state") != "done" or rec.get("raw") not in ("TAKE", "SKIP"):
            failed += rec.get("state") in ("failed", "done"); continue
        R = S["results"][hid]; c = S["cards"][hid]
        rows.append({"i": i, "card": i + 1, "sig": f"{c.get('detector')} · {c.get('direction')}", "raw": rec["raw"], "conf": rec.get("confidence"),
                     "R_raw": R["R"]["raw"], "tp2": bool(R["hit_tp2"]), "r1": bool(R["banked_1R"]), "why": rec.get("why") or ""})
    def part(lo, hi):
        xs = [r for r in rows if lo <= r["i"] < hi]; tk = [r for r in xs if r["raw"] == "TAKE"]; sk = [r for r in xs if r["raw"] == "SKIP"]
        return [f"{lo + 1}-{hi}", f"{len(tk)} of {len(xs)}", round(sum(r["R_raw"] for r in xs), 3), round(sum(r["R_raw"] for r in tk), 3),
                round(sum(r["R_raw"] for r in tk) - sum(r["R_raw"] for r in xs), 3), round(sum(r["R_raw"] for r in sk), 3)]
    sk = [r for r in rows if r["raw"] == "SKIP"]; tk = [r for r in rows if r["raw"] == "TAKE"]
    rate = lambda xs, k: f"{sum(1 for r in xs if r[k])} of {len(xs)}" if xs else "-"
    summ = {"name": name, "kind": "e2", "title": "Experiment E2 - take or skip the raw signal (skip only the horrible ones), Sonnet",
            "version": E2_VERSION, "sid": sid, "at": BT._now(), "answered": len(rows), "total": s["n"], "failed": failed,
            "notes": "A TAKE scores the raw signal's R, a SKIP 0; compared with taking every signal raw. No rates or presets were given - only the "
                     "group's broad behaviour. Model cost. These cards were revealed before - a hint to confirm on fresh Test signals.",
            "lines": [f"Its skips: {rate(sk, 'tp2')} hit TP2, {rate(sk, 'r1')} reached +1R. Its takes: {rate(tk, 'tp2')} hit TP2, {rate(tk, 'r1')} reached +1R."],
            "table": _tbl([{"h": "Cards"}, {"h": "Took"}, {"h": "Every signal raw", "r": 1}, {"h": "Raw on the ones it took", "r": 1},
                           {"h": "Difference (its calls minus all raw)", "r": 1}, {"h": "Raw R of the ones it skipped", "r": 1}],
                          [part(lo, hi) for lo, hi in _spans(s["n"], b)]),
            "card_table": _tbl([{"h": "Card"}, {"h": "Signal"}, {"h": "Call"}, {"h": "Raw R", "r": 1}, {"h": "TP2?"}, {"h": "Why", "w": 1}],
                               [[r["card"], r["sig"], r["raw"] + (f" ({r['conf']})" if r["conf"] is not None else ""), r["R_raw"],
                                 "yes" if r["tp2"] else ("+1R" if r["r1"] else ""), r["why"]] for r in rows])}
    BA._write(os.path.join(d, "summary.json"), summ); return summ


# ============================================================================================ E3: take everything, reviewed while it runs
E3_VERSION = "experiment E3 take-all + reviews 1/2/3/4 h then every 12 h, Sonnet (2026-10-08)"
REVIEW_H = [1, 2, 3, 4] + [4 + 12 * k for k in range(1, 400)]       # trading hours after the entry-bar open (as the engine's cuts) - E3 (first run)
REVIEW_H_B = [4, 8, 12] + [12 + 12 * k for k in range(1, 400)]      # trader 2026-10-08, second run: 4, 8, 12, 24, 36, 48 ... h
SCHEDULES = {"h1234": REVIEW_H, "h4812": REVIEW_H_B}
SCHED_TEXT = {"h1234": "1, 2, 3 and 4 hours after the entry and every 12 hours after that", "h4812": "4, 8 and 12 hours after the entry and every 12 hours after that"}
_FRAMES_LOCK = threading.Lock()
MAX_OPEN = 2.0                                                      # the only limit: the open position never above 200% of the ruled size
TRADE_FENCE = re.compile(r"```trade\s*(\{.*?\})\s*```", re.S)


class Sim:
    """one E3 trade, minute by minute, with the rule engine's fills (live/backtest_engine.py kern): stops first (at the stop or the
    open, whichever is worse), standing closes at their level, standing stop moves (tighten only), standing adds at their level or the
    open (whichever is worse) + model spread, then TP2. Review actions happen at the review minute's close. Values in profit-R."""
    def __init__(self, F: dict, ctx: dict):
        self.F, self.c = F, ctx; self.n = F["n"]; self.be = ctx["be"]; self.tp2 = ctx["tp2"]
        self.legs = []; self.locked = 0.0; self.orders = []; self.tp2_on = True; self.end = None; self.j = 0
        self.cs = 0.0; self.hi = -1e9; self.lo = 1e9; self.peak_open = 0.0; self.gross = 0.0

    def open_size(self): return sum(l["sz"] for l in self.legs if l["alive"])
    def _lvl(self, to): return -1.0 if to <= -1 + 1e-12 else self.be + to          # a stop in R (0 = entry) -> the engine's level
    def _stop(self, v):
        if v == "trade": return self.legs[0]["st"] if self.legs else -1.0
        return self._lvl(float(v))

    def _add(self, sz, px, eo, st):
        sz = min(sz, MAX_OPEN - self.open_size())
        if sz <= 1e-12: return 0.0
        self.legs.append({"sz": sz, "le": px + eo, "lp": px, "st": st, "alive": True}); self.gross += sz
        self.peak_open = max(self.peak_open, self.open_size()); return sz

    def enter(self, pct: float, stop):
        e0 = self.c["e0"]; sz = min(max(pct, 1.0), 200.0) / 100.0
        self.legs.append({"sz": sz, "le": e0, "lp": e0 - self.F["eo"][0], "st": -1.0 if stop in (None, "trade") else self._lvl(float(stop)), "alive": True})
        self.gross = sz; self.peak_open = sz

    def _finish(self, kind, j, px=None):
        for l in self.legs:
            if l["alive"]:
                if px is not None: self.locked += l["sz"] * (px - l["le"])
                l["alive"] = False
        self.end = (kind, j)

    def run_to(self, jend: int) -> None:
        F = self.F; xo, xh, xl, xc, bo, bh, bc, eo = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo"))
        jend = min(jend, self.n - 1)
        while self.end is None and self.j <= jend:
            j = self.j; self.cs += bc[j]; self.hi = max(self.hi, xh[j]); self.lo = min(self.lo, xl[j]); alive = False
            for l in self.legs:
                if l["alive"]:
                    if xl[j] <= l["st"]:
                        r = l["st"] if l["st"] < xo[j] else xo[j]; self.locked += l["sz"] * (r - l["le"]); l["alive"] = False
                    else: alive = True
            if not alive: self.end = ("stopped", j); break
            for o in sorted([o for o in self.orders if o["type"] == "close_at" and not o.get("done")], key=lambda o: o["at"]):
                if xh[j] >= o["at"] and not (self.tp2_on and self.tp2 is not None and o["at"] > self.tp2 + 1e-12):
                    o["done"] = True; f = o["pct"] / 100.0
                    for l in self.legs:
                        if l["alive"]: self.locked += f * l["sz"] * (o["at"] - l["le"]); l["sz"] *= (1.0 - f)
                    if not any(l["alive"] and l["sz"] > 1e-12 for l in self.legs): self._finish("closed by its orders", j); break
            if self.end: break
            for o in self.orders:
                if o["type"] == "stop_at" and not o.get("done") and xh[j] >= o["when"]:
                    o["done"] = True; v = self._lvl(o["to"])
                    for l in self.legs:
                        if l["alive"] and v > l["st"]: l["st"] = v
            for o in self.orders:
                if o["type"] == "add_at" and not o.get("done") and bh[j] >= o["at"] and not (self.tp2_on and self.tp2 is not None and o["at"] >= self.tp2 - 1e-12):
                    o["done"] = True; px = o["at"] if o["at"] > bo[j] else bo[j]
                    self._add(o["pct"] / 100.0, px, eo[j], self._stop(o.get("stop", "trade")))
            if self.tp2_on and self.tp2 is not None and xh[j] >= self.tp2:
                self._finish("TP2", j, self.tp2); break
            self.j += 1
        if self.end is None and self.j >= self.n: self._finish("frame end", self.n - 1, xc[self.n - 1])

    def R(self) -> float: return self.locked + sum(l["sz"] * (self.F["xc"][min(self.j, self.n - 1)] - l["le"]) for l in self.legs if l["alive"])

    # ---- a review's changes, at the close of minute j (validated one by one; a refused item is noted, the rest applies)
    def apply(self, j: int, a: dict) -> list:
        F = self.F; px_exit = F["xc"][j]; bid = F["bc"][j]; notes = []
        if a.get("tp2") is not None: self.tp2_on = bool(a["tp2"])
        cn = a.get("close_now")
        if cn:
            f = max(0.0, min(100.0, float(cn))) / 100.0
            for l in self.legs:
                if l["alive"]: self.locked += f * l["sz"] * (px_exit - l["le"]); l["sz"] *= (1.0 - f)
            if f >= 1.0 - 1e-9 or not any(l["alive"] and l["sz"] > 1e-12 for l in self.legs): self._finish("closed at a review", j); return notes
        sn = a.get("stop_now")
        if sn is not None:
            v = self._lvl(float(sn))
            if v >= px_exit: notes.append(f"stop_now {float(sn):+.2f}R refused: at or above the price")
            else:
                for l in self.legs:
                    if l["alive"]: l["st"] = v
        an = a.get("add_now")
        if an:
            st = self._stop(an.get("stop", "trade"))
            if st >= px_exit: notes.append("add_now refused: its stop is at or above the price")
            elif not self._add(float(an.get("pct", 0)) / 100.0, bid, F["eo"][j], st): notes.append("add_now refused: the position is already at 200%")
        if a.get("orders") is not None:
            keep = []
            for o in a["orders"] or []:
                try:
                    t = o["type"]
                    if t == "close_at" and float(o["at"]) > px_exit and 0 < float(o["pct"]) <= 100: keep.append({"type": t, "at": float(o["at"]), "pct": float(o["pct"])})
                    elif t == "stop_at" and float(o["when"]) > px_exit and float(o["to"]) < float(o["when"]): keep.append({"type": t, "when": float(o["when"]), "to": float(o["to"])})
                    elif t == "add_at" and float(o["at"]) > bid and float(o["pct"]) > 0:
                        keep.append({"type": t, "at": float(o["at"]), "pct": float(o["pct"]), "stop": o.get("stop", "trade")})
                    else: notes.append(f"order refused: {json.dumps(o)[:80]}")
                except (KeyError, TypeError, ValueError): notes.append(f"order refused: {json.dumps(o)[:80]}")
            self.orders = keep
        return notes

    def state_md(self, j: int, H: float, k: int, sell: bool, history: list) -> str:
        F = self.F; e = lambda v: f"{v:+.2f}R"; st = lambda l: -1.0 if l["st"] <= -1 + 1e-12 else l["st"] - self.be
        legs = [l for l in self.legs if l["alive"] and l["sz"] > 1e-12]
        risk = sum(l["sz"] * (l["lp"] - (l["st"] if l["st"] > -1 else -1.0)) for l in legs)
        ords = "; ".join({"close_at": lambda o: f"close {o['pct']:g}% at {e(o['at'])}", "stop_at": lambda o: f"stop to {e(o['to'])} when {e(o['when'])}",
                          "add_at": lambda o: f"add {o['pct']:g}% at {e(o['at'])} (stop {o['stop'] if o['stop'] == 'trade' else e(float(o['stop']))})"}[o["type"]](o)
                         for o in self.orders if not o.get("done")) or "none"
        out = [f"# Trade state - review {k}, {H:g} trading hours after the entry", "",
               f"- Direction {'SELL' if sell else 'BUY'}. EVERY number below is PROFIT-R (positive = in your favour; for a SELL that is the price "
               "falling). The charts are in PRICE-R like card.md.",
               f"- Now: an exit would get {e(F['xc'][j])} (bid {e(F['bc'][j])}). Best since the entry {e(self.hi)}, worst {e(self.lo)}; "
               f"average R since the entry {e(self.cs / (j + 1))}.",
               f"- Levels: TP1 {e(self.c['tp1']) if self.c.get('tp1') is not None else '-'}, TP2 {e(self.tp2) if self.tp2 is not None else '-'} "
               f"(TP2 exit {'ON' if self.tp2_on else 'OFF'}); the signal stop is -1.00R.",
               "- Open pieces: " + (" | ".join(f"{l['sz'] * 100:.0f}% in at {e(l['lp'])}, stop {e(st(l))}" for l in legs) or "none") +
               f". Open size {self.open_size() * 100:.0f}% of the ruled size (max 200%).",
               f"- Banked so far {e(self.locked)}; open R now {e(self.R() - self.locked)}; total if closed now {e(self.R())}; at risk to the stops now {risk:.2f}R.",
               f"- Standing orders: {ords}.", "", "## Your earlier reviews"]
        out += [f"- {h}" for h in history] or ["- (none)"]
        return "\n".join(out) + "\n"


def since_entry(F: dict, ctx: dict, j: int, sell: bool) -> tuple[list, int]:
    """the bars since the entry up to minute j (inclusive), in PRICE-R like the card: M5, M15, H1 or H4 - the smallest with <= 200 bars."""
    tm = ctx["tm"]; span = int(tm[j] - tm[0]) + 1
    size = next((m for m in (5, 15, 60, 240) if span / m <= 200), 240)
    out, cur = [], None
    for q in range(j + 1):
        k = int((tm[q] - tm[0]) // size)
        o, h, c = F["bo"][q], F["bh"][q], F["bc"][q]; l = F["xl"][q] + (F["bh"][q] - F["xh"][q])   # bid low (xl is the exit side: a SELL's is the ask)
        if cur is None or cur[0] != k:
            if cur: out.append(cur)
            cur = [k, o, h, l, c]
        else: cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c
    if cur: out.append(cur)
    bars = []
    for k, o, h, l, c in out:                          # profit-R -> price-R (a SELL flips), synthetic time
        if sell: o, h, l, c = -o, -l, -h, -c
        bars.append([1_000_000 + k * size * 60, o, h, l, c, 0])
    return bars, size


def e3_version(sched: str) -> str:
    return E3_VERSION if sched == "h1234" else "experiment E3b take-all + reviews 4/8/12 h then every 12 h, Sonnet (2026-10-08)"


def role_e3(sched: str = "h1234") -> str:
    return f"""# Blind backtest advisor ({e3_version(sched)})

{BLIND_HEAD}

You MANAGE ONE trade that is being taken. Your folder: `card.md`, `h4.png` and `d1.png` (the setup at the signal, before the entry);
once the trade is running also `since_entry.png` / `since_entry.csv` (the bars since the entry, in PRICE-R like the card, up to now) and
`state.md` (where the trade stands now, in PROFIT-R, and your earlier reviews).

- At the entry you choose the size (1-200% of the ruled size), its stop, the standing orders and the TP2 exit.
- Then you review it {SCHED_TEXT[sched]}, until it ends. At each review you may change
  anything, or nothing.
- The only limit: the open position never goes above 200% of the ruled size. A stop can sit anywhere from the signal stop (-1R) up to just
  below the current price. You are graded on the trade's final R.

Your tools (levels in PROFIT-R: 0 = the entry; for a SELL, +1R is the price falling 1R):
- "close_now": a % of what is open, closed at the current price (100 = close the trade).
- "stop_now": a level for the stop of every open piece (looser or tighter, from -1R up to below the current price).
- "add_now": {{"pct": 50, "stop": 0.0}} - add now at the current price (its stop a level, or "trade" = the first piece's stop).
- "orders": the standing orders until you change them - a list REPLACES all of them ([] = none; leave it out to keep the current ones);
  each fires once:
    {{"type": "close_at", "pct": 50, "at": 2.0}}             close pct% of what is open when +at R is reached
    {{"type": "stop_at", "to": 0.5, "when": 1.5}}            move every stop to `to` when +when R is reached (tighten only)
    {{"type": "add_at", "pct": 50, "at": 2.0, "stop": 1.0}}  add pct% when +at R is reached, its stop at `stop` (or "trade")
- "tp2": true / false - exit everything at TP2, or not.

Answer in exactly this shape (the server parses it):

DECISION: ENTER (at the entry) / HOLD or CHANGE (at a review)
WHY: one plain line

```trade
{{"entry": {{"pct": 100, "stop": -1.0}}, "orders": [ ... ], "tp2": true, "note": "<one line>"}}
```
At the entry the block needs "entry". At a review leave out (or set null) anything you do not change - HOLD can be just {{"note": "..."}}.
Keep it short. Do not write files.
"""


def parse_trade(text: str) -> tuple[str | None, str | None, dict, str]:
    m = re.search(r"DECISION:\s*\**\s*(ENTER|HOLD|CHANGE)\b", text or "", re.I); _, _, why = BA.parse_verdict(text)
    blocks = TRADE_FENCE.findall(text or ""); a, err = {}, ""
    if blocks:
        try: a = json.loads(blocks[-1]); a = a if isinstance(a, dict) else {}
        except ValueError as e: err = f"the trade block is not valid JSON: {e}"
    return (m.group(1).upper() if m else None), why, a, err


def manage_e3(cfg, S, s, i, d, role, consult=None, sched: str = "h1234") -> dict:
    hid = s["ids"][i]
    with _FRAMES_LOCK: F, ctx = S["frames"].get(hid)           # the frame reader shares file handles: one reader at a time
    card = S["cards"][hid]; sell = card.get("direction") == "SELL"
    job = _plain_job(S, s, i); bd = BA.write_bundle(cfg, job, role); st = _sonnet(cfg)
    for f in ("since_entry.png", "since_entry.csv"):
        try: os.remove(os.path.join(bd, f))
        except FileNotFoundError: pass
    def ask(prompt):
        last = None
        for att in range(3):
            r = (consult or BA.call_claude)(cfg, prompt, st, None, bd)
            if r.get("ok"): return r
            last = r.get("error"); time.sleep(15 * (att + 1))
        return {"ok": False, "error": last}
    sim = Sim(F, ctx); hist, reviews = [], []
    open(os.path.join(bd, "state.md"), "w", encoding="utf-8").write("# Trade state - the ENTRY (at the signal bar's close)\n\nNothing has happened yet: choose the entry size, "
                                                                     "its stop, any standing orders and the TP2 exit.\n")
    r = ask(role + "\n\n---\n\nThis is the ENTRY. Read card.md, h4.png, d1.png and state.md, then answer in the shape above with an \"entry\".")
    dec, why, a, err = parse_trade(r.get("text") or "") if r.get("ok") else (None, None, {}, f"consult failed: {r.get('error')}")
    en = a.get("entry") if isinstance(a.get("entry"), dict) else {}
    try: pct = float(en.get("pct", 100)); stp = en.get("stop", -1.0); stp = stp if stp == "trade" else float(stp)
    except (TypeError, ValueError): pct, stp, err = 100.0, -1.0, (err + "; bad entry - 100% at the signal stop").strip("; ")
    if stp != "trade" and stp >= 0 - 1e-9: stp, err = -1.0, (err + "; entry stop at/above the entry - signal stop used").strip("; ")
    sim.enter(pct, stp)
    notes = sim.apply_entry(a) if a else []
    reviews.append({"k": 0, "H": 0, "decision": dec or "ENTER", "why": why, "block": a, "notes": notes + ([err] if err else []), "elapsed_s": r.get("elapsed_s")})
    hist.append(f"entry: {sim.open_size() * 100:.0f}% in, stop {stp if stp == 'trade' else f'{stp:+.2f}R'}" + (f"; {a.get('note')}" if a.get("note") else ""))
    last_j = -1
    for k, H in enumerate(SCHEDULES[sched], 1):
        jr = E.mark(ctx, H)
        if jr >= F["n"] - 1 or jr >= E.BEYOND: break
        if jr <= last_j: continue
        last_j = jr; sim.run_to(jr)
        if sim.end: break
        bars, size = since_entry(F, ctx, jr, sell)
        with open(os.path.join(bd, "since_entry.csv"), "w") as f:
            f.write(f"bars_ago,open,high,low,close   # {size}-minute bars since the entry, PRICE-R, the last one up to now\n")
            for q, b in enumerate(bars): f.write(f"{len(bars) - 1 - q},{b[1]:.3f},{b[2]:.3f},{b[3]:.3f},{b[4]:.3f}\n")
        try:
            from live import charts
            img = charts.render(bars, title=f"Since the entry · {size}-minute bars · price in R · {card.get('direction')}", levels=card.get("levels"),
                                n_show=len(bars), digits=2, hide_dates=True)
            img.save(os.path.join(bd, "since_entry.png"))
        except Exception as e: open(os.path.join(bd, "charts_unavailable.txt"), "w").write(f"since-entry chart failed: {e!r}\n")
        open(os.path.join(bd, "state.md"), "w", encoding="utf-8").write(sim.state_md(jr, H, k, sell, hist))
        r = ask(role + f"\n\n---\n\nReview {k}, {H:g} trading hours after the entry. Read state.md and since_entry.png (card.md and the setup charts "
                       "too, if you need them), then answer in the shape above (HOLD or CHANGE).")
        dec, why, a, err = parse_trade(r.get("text") or "") if r.get("ok") else ("HOLD", None, {}, f"consult failed - held: {r.get('error')}")
        notes = sim.apply(jr, a) if a else []
        changed = any(a.get(x) not in (None, 0, "", False) for x in ("close_now", "stop_now", "add_now")) or a.get("orders") is not None or a.get("tp2") is not None
        reviews.append({"k": k, "H": H, "j": jr, "decision": dec, "why": why, "block": a, "notes": notes + ([err] if err else []), "changed": changed,
                        "elapsed_s": r.get("elapsed_s"), "R_if_closed": round(sim.R(), 4)})
        hist.append(f"review {k} ({H:g} h): {dec or '?'}" + (f" - {a.get('note')}" if a.get("note") else "") + (f" [refused: {'; '.join(notes)}]" if notes else ""))
        if sim.end: break
    if not sim.end: sim.run_to(F["n"] - 1)
    rec = {"i": i, "hid": hid, "state": "done", "role": e3_version(sched), "model": SONNET, "schedule": sched, "at": BT._now(), "R": round(sim.locked, 6),
           "end": list(sim.end) if sim.end else None, "entry_pct": reviews[0]["block"].get("entry", {}).get("pct", 100) if isinstance(reviews[0]["block"].get("entry"), dict) else 100,
           "peak_open": round(sim.peak_open, 4), "gross": round(sim.gross, 4), "reviews": reviews}
    BA._write(os.path.join(d, f"{hid}.json"), rec); return rec


def _apply_entry(self, a: dict) -> list:
    """the entry block's orders / TP2 (validated against the entry price, 0)."""
    notes = []
    if a.get("tp2") is not None: self.tp2_on = bool(a["tp2"])
    keep = []
    for o in a.get("orders") or []:
        try:
            t = o["type"]
            if t == "close_at" and float(o["at"]) > 0 and 0 < float(o["pct"]) <= 100: keep.append({"type": t, "at": float(o["at"]), "pct": float(o["pct"])})
            elif t == "stop_at" and float(o["when"]) > 0 and float(o["to"]) < float(o["when"]): keep.append({"type": t, "when": float(o["when"]), "to": float(o["to"])})
            elif t == "add_at" and float(o["at"]) > 0 and float(o["pct"]) > 0: keep.append({"type": t, "at": float(o["at"]), "pct": float(o["pct"]), "stop": o.get("stop", "trade")})
            else: notes.append(f"order refused: {json.dumps(o)[:80]}")
        except (KeyError, TypeError, ValueError): notes.append(f"order refused: {json.dumps(o)[:80]}")
    self.orders = keep
    return notes


Sim.apply_entry = _apply_entry


def summary_e3(cfg, sid, name) -> dict:
    S = BT.store(cfg); s = BT.load_session(cfg, sid); d = exp_dir(cfg, sid, name); b = s.get("block") or 20
    rows, failed = [], 0
    for i in range(s["n"]):
        hid = s["ids"][i]
        try: rec = json.load(open(os.path.join(d, f"{hid}.json")))
        except (OSError, ValueError): continue
        if rec.get("state") != "done": failed += 1; continue
        c = S["cards"][hid]; R = S["results"][hid]; rv = rec["reviews"]
        rows.append({"i": i, "card": i + 1, "sig": f"{c.get('detector')} · {c.get('direction')}", "entry": rec.get("entry_pct", 100), "R": rec["R"],
                     "R_raw": R["R"]["raw"], "n_rev": len(rv) - 1, "n_chg": sum(1 for x in rv[1:] if x.get("changed")), "end": (rec.get("end") or ["?"])[0],
                     "peak": rec.get("peak_open"), "note": next((x["block"].get("note") for x in reversed(rv) if x.get("block", {}).get("note")), "") or (rv[-1].get("why") or "")})
    def part(lo, hi):
        xs = [r for r in rows if lo <= r["i"] < hi]; dd = [r["R"] - r["R_raw"] for r in xs]
        return [f"{lo + 1}-{hi}", len(xs), round(sum(r["R_raw"] for r in xs), 3), round(sum(r["R"] for r in xs), 3), round(sum(dd), 3), _t(dd),
                round(sum(r["entry"] for r in xs) / len(xs), 0) if xs else None]
    ends = {}
    for r in rows: ends[r["end"]] = ends.get(r["end"], 0) + 1
    nrev = sum(r["n_rev"] for r in rows); nchg = sum(r["n_chg"] for r in rows)
    sched = next((json.load(open(os.path.join(d, f"{s['ids'][i]}.json"))).get("schedule") for i in range(s["n"]) if os.path.exists(os.path.join(d, f"{s['ids'][i]}.json"))), None) or "h1234"
    summ = {"name": name, "kind": "e3", "title": f"Experiment {name.upper()} - every signal taken, managed by the advisor (its own entry plan; reviews {SCHED_TEXT[sched]}), Sonnet",
            "version": E3_VERSION, "sid": sid, "at": BT._now(), "answered": len(rows), "total": s["n"], "failed": failed,
            "notes": "Compared with taking every signal raw (in at the signal, out at TP2 or the stop). The only limit was a 200% open position. "
                     "Model cost. These cards were revealed before - a hint to confirm on fresh Test signals.",
            "lines": [f"Reviews: {nrev} in all, {nchg} changed something. Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(ends.items(), key=lambda x: -x[1])) + "."],
            "table": _tbl([{"h": "Cards"}, {"h": "Trades"}, {"h": "Every signal raw", "r": 1}, {"h": "Managed by the advisor", "r": 1},
                           {"h": "Difference", "r": 1}, {"h": "t"}, {"h": "Average entry size %"}], [part(lo, hi) for lo, hi in _spans(s["n"], b)]),
            "card_table": _tbl([{"h": "Card"}, {"h": "Signal"}, {"h": "Entry %"}, {"h": "Reviews (changed)"}, {"h": "Raw R", "r": 1}, {"h": "Managed R", "r": 1},
                                {"h": "How it ended"}, {"h": "Its last note", "w": 1}],
                               [[r["card"], r["sig"], r["entry"], f"{r['n_rev']} ({r['n_chg']})", r["R_raw"], round(r["R"], 4), r["end"], r["note"]] for r in rows])}
    BA._write(os.path.join(d, "summary.json"), summ); return summ


def run_kind(cfg: dict, sid: str, name: str, kind: str, parallel: int, consult=None, log=print, sched: str = "h1234") -> None:
    if kind == "e1": return run(cfg, sid, name, parallel, consult, log)
    S = BT.store(cfg); s = BT.load_session(cfg, sid); d = exp_dir(cfg, sid, name)
    if kind == "e2": role, fn, summ = role_e2(), consult_e2, summary_e2
    else: role, summ = role_e3(sched), summary_e3; fn = lambda *a: manage_e3(*a, sched=sched)
    todo = []
    for i in range(s["n"]):
        try: ok = json.load(open(os.path.join(d, f"{s['ids'][i]}.json"))).get("state") == "done"
        except (OSError, ValueError): ok = False
        if not ok: todo.append(i)
    log(f"{name} ({kind}): {len(todo)} of {s['n']} cards, {parallel} at a time"); t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(fn, cfg, S, s, i, d, role, consult): i for i in todo}
        for k, f in enumerate(cf.as_completed(futs), 1):
            try:
                r = f.result()
                log(f"  card {futs[f] + 1}: {r.get('state')} {r.get('raw') or ''}{' R %+.2f' % r['R'] if 'R' in r else ''}"
                    f"{' reviews %d' % (len(r['reviews']) - 1) if 'reviews' in r else ''} ({k}/{len(todo)}, {time.time() - t0:.0f}s)")
            except Exception as e: log(f"  card {futs[f] + 1}: ERROR {e!r}")
    summ(cfg, sid, name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default=r"C:\ProgramData\hybrid\live\live_config.json")
    ap.add_argument("--sid", default=""); ap.add_argument("--fresh", default="", help="draw a fresh experiment-only Test session of this group")
    ap.add_argument("--n", type=int, default=0); ap.add_argument("--name", default="e1"); ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--kind", default="e1", choices=("e1", "e2", "e3")); ap.add_argument("--sched", default="h1234", choices=tuple(SCHEDULES))
    ap.add_argument("--summary-only", action="store_true"); a = ap.parse_args()
    cfg = json.load(open(a.config))
    if not re.fullmatch(r"[a-z0-9_]{1,24}", a.name): sys.exit("bad experiment name")
    sid = a.sid
    if a.fresh:
        r = BT.experiment_session(cfg, a.fresh, research_lock(cfg), a.n or None); sid = r["sid"]
        print(f"fresh experiment session {sid}: {r['n']} {a.fresh} Test cards (shortfall {r['shortfall']})", flush=True)
    if not sid: sys.exit("--sid or --fresh")
    if a.summary_only: {"e1": write_summary, "e2": summary_e2, "e3": summary_e3}[a.kind](cfg, sid, a.name)
    else: run_kind(cfg, sid, a.name, a.kind, a.parallel, log=lambda m: print(m, flush=True), sched=a.sched)
