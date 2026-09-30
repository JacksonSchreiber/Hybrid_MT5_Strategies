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
