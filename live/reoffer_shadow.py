"""reoffer_shadow.py - coach item 26 (2026-10-01): re-offered spread rejects, graded live.

Every journal signal with reoffer=1 gets a line in <root>/web/reoffer.csv:
  taken    (approved): the signal's full-position R - each ticket's r_multiple (already in the first tranche's 1R) x its lots /
           the full lots (eligibility.price_r_scale), once every ticket is closed;
  skipped / expired re-offers: the SHADOW - what the card would have made, replayed on the feed's M15 bars from the re-offer
           (signal time + held minutes) at the card's entry, stop and targets, bank fraction of the detector, stop to entry,
           runner to TP2 (inverse_shadow._replay);
  closed without a card (spread never normalised / cancelled by rule 3): counted, not graded.
Coach guard: re-offered takes vs on-time takes (approved, reoffer != 1, not Inverse) at n = 20 re-offered takes; below -0.10R
mean -> switch reoffer_spread_rejects off and tell the coach.
"""
from __future__ import annotations
import csv, os

from live import common as C
from live import inverse_shadow as IS

COLS = ["symbol", "signal_id", "strategy", "direction", "decision", "held_minutes", "spread_r_at_signal", "spread_r_at_offer", "drift_r",
        "entry", "sl", "tp1", "tp2", "status", "actual_R", "shadow_R", "note"]
BANKF = {"TrendCont": 0.25, "DeepFib": 0.25}


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def build(cfg: dict) -> tuple[list[dict], list[float]]:
    from live import eligibility as ELIG
    rows = C.journal_rows(cfg)
    sigs: dict = {}
    for r in rows: sigs.setdefault((r.get("symbol"), r.get("signal_id")), []).append(r)
    out, ontime = [], []
    for (sym, sid), rs in sigs.items():
        first = next((r for r in rs if int(_f(r.get("tranche")) or 0) <= 1), rs[0])
        dec = first.get("decision") or ""
        taken = dec in ("approved", "approved_pending")
        closed = taken and all((r.get("exit_time") or "").strip() for r in rs if r.get("decision") in ("approved", "approved_pending"))
        tot = sum((_f(r.get("r_multiple")) or 0.0) * ELIG.price_r_scale(r) for r in rs if r.get("decision") in ("approved", "approved_pending"))
        if (first.get("reoffer") or "") != "1":
            if taken and closed and first.get("strategy") not in ("Inverse", "ADOPTED"): ontime.append(tot)
            continue
        x = {k: first.get(k, "") for k in ("symbol", "signal_id", "strategy", "direction", "held_minutes", "spread_r_at_signal",
                                         "spread_r_at_offer", "drift_r", "entry", "sl", "tp1", "tp2")}
        x.update({"decision": dec, "actual_R": "", "shadow_R": "", "note": ""})
        if dec == "rejected":
            x["status"] = "no_card"; x["note"] = first.get("reject_reason", "")
        elif taken:
            x["status"] = "taken_closed" if closed else "taken_open"
            if closed: x["actual_R"] = round(tot, 4)
        else:
            x["status"] = "skipped"
            up = (first.get("direction") or "").upper().startswith("B")
            t0 = IS._ts(first.get("signal_time"))
            t_from = (t0 or 0) + int(_f(first.get("held_minutes")) or 0) * 60
            sh = IS._replay(sym, up, _f(first.get("entry")), _f(first.get("sl")), _f(first.get("tp1")), _f(first.get("tp2")) or _f(first.get("tp")),
                            BANKF.get(first.get("strategy"), 0.5), t_from) if t0 and _f(first.get("entry")) and _f(first.get("sl")) else None
            x["shadow_R"] = "" if sh is None else round(sh, 4)
        out.append(x)
    return out, ontime


def write(cfg: dict) -> dict:
    rows, ontime = build(cfg)
    p = os.path.join(cfg["root"], "web", "reoffer.csv"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for x in rows: w.writerow(x)
    os.replace(p + ".tmp", p)
    tk = [float(x["actual_R"]) for x in rows if x["status"] == "taken_closed"]
    sk = [float(x["shadow_R"]) for x in rows if x["status"] == "skipped" and x["shadow_R"] != ""]
    return {"taken": len(tk), "taken_mean": (sum(tk) / len(tk)) if tk else None, "skipped": len(sk), "skipped_mean": (sum(sk) / len(sk)) if sk else None,
            "ontime": len(ontime), "ontime_mean": (sum(ontime) / len(ontime)) if ontime else None,
            "no_card": sum(1 for x in rows if x["status"] == "no_card")}
