"""shadow_bank.py - coach 2026-09-30 item 8: the bank shadow log.

For every closed live TrendCont, what a 50%, 25% and 0% bank (stop to entry at +1R in all three) would each have
returned - once at the MODELLED levels (bank exactly at the trigger, runner exactly at entry / TP2 / SL) and once at the
ACTUAL fills from the broker's deal history - plus the gap between the two on the runner's exit. All three variants
share one price path (the stop moves to entry at the same +1R in each), so one real trade prices all three.

The coach's revisit point: >= 30 live TrendConts whose stop-to-entry exits filled within 0.05R of modelled.
Rows with a manual action (close, close 50%, SL to BE, ratchet) are logged but flagged: their path is the trader's.
Writes <root>/web/shadow_bank.csv (rewritten each run).
"""
from __future__ import annotations
import csv, glob, os

from live import common as C
from live import mt5feed

AUTO = ("AUTO_1R", "AUTO_TP1", "AUTO_1R_BE_ONLY", "AUTO_TP1_BE_ONLY")
COLS = ["symbol", "signal_id", "posid", "signal_time", "entry", "sl", "tp1", "tp2", "bank_journaled", "bank_share_actual",
        "bank_fired", "bank_model_R", "bank_fill_R", "runner_exit", "runner_model_R", "runner_fill_R", "runner_gap_R",
        "R50_model", "R25_model", "R0_model", "R50_fill", "R25_fill", "R0_fill", "journal_R", "manual"]


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def actions(cfg: dict) -> dict:
    out: dict = {}
    for p in glob.glob(os.path.join(cfg["root"], "journal", "*.actions.csv")):
        sym = os.path.basename(p).split("_")[0]
        try:
            with open(p, encoding="ascii", errors="replace", newline="") as f:
                for r in csv.DictReader(f): out.setdefault((sym, r.get("signal_id")), []).append(r)
        except OSError: pass
    return out


def one(r: dict, acts: list, fills: list) -> dict | None:
    e, s, tp1, tp2 = _f(r.get("entry")), _f(r.get("sl")), _f(r.get("tp1")), _f(r.get("tp2"))
    if not e or not s or e == s or not fills: return None
    R = abs(e - s); up = (r.get("direction") or "").upper().startswith("B") or (s < e)
    toR = lambda px: (px - e) * (1 if up else -1) / R
    ins = [x for x in fills if x[1] == 0]; outs = [x for x in fills if x[1] in (1, 2, 3)]
    if not ins or not outs: return None
    tot = sum(x[2] for x in ins)
    trig = min(1.0, toR(tp1)) if tp1 else 1.0
    fired = any((a.get("action") or "") in AUTO for a in acts)
    manual = any((a.get("action") or "") not in AUTO for a in acts)
    partial = outs[0] if (fired and len(outs) > 1 and outs[0][2] < tot - 1e-9) else None
    rest = [x for x in outs if x is not partial]
    vol = sum(x[2] for x in rest) or 1.0
    runner_px = sum(x[2] * x[3] for x in rest) / vol
    run_fill = toR(runner_px)
    levels = {"SL": -1.0, "BE": 0.0}
    if tp2: levels["TP"] = toR(tp2)
    kind = min(levels, key=lambda k: abs(levels[k] - run_fill))
    if abs(levels[kind] - run_fill) > 0.12: kind = "other"            # a manual / end-of-data exit, not a stop or target
    run_mod = levels.get(kind, run_fill)
    bank_fill = toR(partial[3]) if partial else None
    out = {"symbol": r.get("symbol"), "signal_id": r.get("signal_id"), "posid": r.get("posid"), "signal_time": r.get("signal_time"),
           "entry": e, "sl": s, "tp1": tp1, "tp2": tp2, "bank_journaled": r.get("partial_frac"),
           "bank_share_actual": round(partial[2] / tot, 3) if partial else 0.0, "bank_fired": int(fired),
           "bank_model_R": round(trig, 3) if fired else "", "bank_fill_R": round(bank_fill, 3) if bank_fill is not None else "",
           "runner_exit": kind, "runner_model_R": round(run_mod, 3), "runner_fill_R": round(run_fill, 3),
           "runner_gap_R": round(run_fill - run_mod, 3) if kind != "other" else "", "journal_R": r.get("r_multiple"),
           "manual": int(manual)}
    for f in (0.5, 0.25, 0.0):
        tag = str(int(f * 100))
        out[f"R{tag}_model"] = round((f * trig + (1 - f) * run_mod) if fired else run_mod, 3)
        bf = bank_fill if bank_fill is not None else trig
        out[f"R{tag}_fill"] = round((f * bf + (1 - f) * run_fill) if fired else run_fill, 3)
    return out


def build(cfg: dict) -> list[dict]:
    acts = actions(cfg); out = []
    for r in C.journal_rows(cfg):
        if r.get("strategy") != "TrendCont" or r.get("decision") not in ("approved", "approved_pending"): continue
        if not r.get("exit_time") or int(_f(r.get("posid")) or 0) <= 0: continue
        fills = mt5feed.position_fills(int(_f(r["posid"])))
        x = one(r, acts.get((r.get("symbol"), r.get("signal_id")), []), fills or [])
        if x: out.append(x)
    return out


def write(cfg: dict) -> tuple[int, int, int]:
    """-> (rows, stop-to-entry exits without manual action, of those within 0.05R of modelled)"""
    rows = build(cfg)
    p = os.path.join(cfg["root"], "web", "shadow_bank.csv")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for x in rows: w.writerow(x)
    os.replace(tmp, p)
    be = [x for x in rows if x["runner_exit"] == "BE" and not x["manual"]]
    ok = [x for x in be if abs(float(x["runner_gap_R"])) <= 0.05]
    return len(rows), len(be), len(ok)


# ----------------------------------------------------------------------------- coach item 18: staged entry log
STAGED_COLS = ["symbol", "signal_id", "strategy", "signal_time", "full_lots", "first_lots", "add_lots", "add_state", "add_fill",
               "add_time", "R_first_per_lot", "R_add_per_lot", "staged_R", "counterfactual_full_R",
               "pyramid_lots", "pyramid_fill", "pyramid_time", "R_pyramid_per_lot", "counterfactual_noadd_R", "closed"]


def staged(cfg: dict) -> list[dict]:
    """Per staged signal: the realised R in units of the FULL ruled position (sum of lots x R per lot / full lots) and the
    counterfactual 'all 100% at the signal' R. Both tranches ride one price path and bank/move to entry at the first
    tranche's levels, so the full-at-signal position would have earned the first tranche's R per lot (the lot step's
    rounding of the bank aside)."""
    by: dict = {}
    for r in C.journal_rows(cfg):
        if r.get("decision") not in ("approved", "approved_pending"): continue
        t = int(_f(r.get("tranche")) or 0)
        by.setdefault((r.get("symbol"), r.get("signal_id")), {})[t] = r
    out = []
    for (sym, sid), d in sorted(by.items(), key=lambda kv: (kv[1].get(1) or kv[1].get(2) or {}).get("signal_time", "")):
        a, b, py = d.get(1), d.get(2), d.get(3)
        if not a and py: a = d.get(0)                       # not staged (too small to split) but pyramided: the first row is tranche 0
        if not a: continue
        full = _f(a.get("full_lots")) or _f(a.get("lots")) or 0.0
        l1, l2 = _f(a.get("lots")) or 0.0, (_f(b.get("lots")) or 0.0) if b else 0.0
        r1 = _f(a.get("r_multiple")) if a.get("exit_time") else None
        r2 = (_f(b.get("r_multiple")) if b.get("exit_time") else None) if b else None
        l3 = (_f(py.get("lots")) or 0.0) if py else 0.0
        r3 = (_f(py.get("r_multiple")) if py.get("exit_time") else None) if py else None
        closed = r1 is not None and (b is None or r2 is not None) and (py is None or r3 is not None)
        noadd = ((l1 * r1 + (l2 * r2 if b else 0.0)) / full) if (closed and full) else None
        st_R = (noadd + (l3 * r3 / full if py else 0.0)) if noadd is not None else None
        out.append({"symbol": sym, "signal_id": sid, "strategy": a.get("strategy"), "signal_time": a.get("signal_time"),
                    "full_lots": full, "first_lots": l1, "add_lots": l2 or "",
                    "add_state": "added" if b else ("skipped: " + (a.get("reject_reason") or "") if a.get("reject_reason") else "pending"),
                    "add_fill": b.get("entry") if b else "", "add_time": b.get("fill_time") if b else "",
                    "R_first_per_lot": "" if r1 is None else round(r1, 3), "R_add_per_lot": "" if r2 is None else round(r2, 3),
                    "staged_R": "" if st_R is None else round(st_R, 3),
                    "counterfactual_full_R": "" if r1 is None else round(r1, 3),
                    "pyramid_lots": l3 or "", "pyramid_fill": py.get("entry") if py else "", "pyramid_time": py.get("fill_time") if py else "",
                    "R_pyramid_per_lot": "" if r3 is None else round(r3, 3),
                    "counterfactual_noadd_R": "" if noadd is None else round(noadd, 3), "closed": int(closed)})
    return out


def write_staged(cfg: dict) -> tuple[int, float | None, float | None]:
    """-> (closed staged signals, staged R per unit of worst drawdown, counterfactual R per unit of worst drawdown)"""
    rows = staged(cfg)
    p = os.path.join(cfg["root"], "web", "staged_entry.csv"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STAGED_COLS); w.writeheader()
        for x in rows: w.writerow(x)
    os.replace(p + ".tmp", p)
    done = [x for x in rows if x["closed"]]

    def r_per_dd(vals):
        cur = peak = dd = 0.0
        for v in vals: cur += v; peak = max(peak, cur); dd = max(dd, peak - cur)
        return (sum(vals) / dd) if dd > 0 else None
    return len(done), r_per_dd([x["staged_R"] for x in done]), r_per_dd([x["counterfactual_full_R"] for x in done])



def pyramid_stats(cfg: dict) -> tuple[int, float, float]:
    """coach item 20 grading: over closed pyramided signals, mean (R with the add - R without it) and the drawdown ratio
    (with / without), both in full-position units."""
    rows = [x for x in staged(cfg) if x["closed"] and x["pyramid_lots"] not in ("", 0)]
    if not rows: return 0, 0.0, 0.0
    w = [x["staged_R"] for x in rows]; wo = [x["counterfactual_noadd_R"] for x in rows]

    def dd(v):
        cur = peak = m = 0.0
        for r in v: cur += r; peak = max(peak, cur); m = max(m, peak - cur)
        return m
    return len(rows), sum(a - b for a, b in zip(w, wo)) / len(rows), (dd(w) / dd(wo)) if dd(wo) > 0 else 0.0
