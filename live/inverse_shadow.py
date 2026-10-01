"""inverse_shadow.py - coach item 21 (2026-10-01): the Inverse's live record and the shadow log of the ones not traded.

Every Inverse card in the journal gets a line in <root>/web/inverse_shadow.csv:
  - filled (armed and fired): the actual R, the fill slippage vs the parent's stop, the H4 bar the parent was stopped in;
  - skipped / expired / cancelled: what it WOULD have done - if its parent was stopped at/through its stop inside H4 bars
    7-18, the Inverse is replayed from the parent's stop on the feed's M15 bars (stop first each bar; bank at its TP1 with the
    parent detector's fraction, stop to entry, runner to its TP2), else "would not have filled".
The monitor reads it for the coach's alerts: fills' mean slippage after 10 (> 0.05R), the early stop at 25 fills (mean
<= -0.15R), and the full read at 50.
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone

from live import common as C
from live import mt5feed

BANKF = {"TrendCont": 0.25, "DeepFib": 0.25}
COLS = ["symbol", "signal_id", "parent_signal_id", "parent_strategy", "direction", "decision", "state", "entry", "sl", "tp1", "tp2",
        "bars_to_stop", "fill_slip_R", "actual_R", "would_fill", "shadow_R", "note"]


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _ts(s):   # journal times are broker clock 'YYYY.MM.DD HH:MM:SS' -> the feed's epoch (also broker clock)
    try: return int(datetime.strptime(s[:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError): return None


def _h4_bar_number(sym, t_signal, t_exit):
    bars = mt5feed.bars(sym, "h4", 400) or []
    ts = [int(b[0]) for b in bars]
    if not ts or t_signal is None or t_exit is None: return None
    import bisect
    i_sig = bisect.bisect_right(ts, t_signal) - 1; i_ex = bisect.bisect_right(ts, t_exit) - 1
    return (i_ex - i_sig) if i_sig >= 0 and i_ex >= 0 else None


def _replay(sym, up, fill, sl, tp1, tp2, bankf, t_from):
    R = abs(fill - sl)
    if R <= 0: return None
    bars = [b for b in (mt5feed.bars(sym, "m15", 2000) or []) if int(b[0]) > t_from]
    if not bars: return None
    sg = 1.0 if up else -1.0; toR = lambda p: (p - fill) * sg / R
    bank_R = toR(tp1) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    stop, banked, locked, size = -1.0, False, 0.0, 1.0
    for b in bars:
        o = toR(b[1]); hi, lo = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo <= stop: return locked + size * (o if o < stop else stop)
        if not banked and hi >= bank_R:
            banked = True; locked += bankf * bank_R; size *= (1 - bankf); stop = 0.0
            if tgt is not None and hi >= tgt: return locked + size * tgt
            continue
        if banked and tgt is not None and hi >= tgt: return locked + size * tgt
    return None                                                           # still open: no shadow number yet


def build(cfg: dict) -> list[dict]:
    rows = C.journal_rows(cfg); out = []
    by = {(r.get("symbol"), r.get("signal_id")): r for r in rows if r.get("strategy") != "Inverse"}
    for r in rows:
        if r.get("strategy") != "Inverse": continue
        par = by.get((r.get("symbol"), r.get("parent_signal_id")))
        x = {k: r.get(k) for k in ("symbol", "signal_id", "parent_signal_id", "direction", "decision", "entry", "sl", "tp1", "tp2",
                                   "bars_to_stop")}
        x.update({"parent_strategy": (par or {}).get("strategy", ""), "state": r.get("entry_mode"), "fill_slip_R": r.get("inv_slip_r"),
                  "actual_R": r.get("r_multiple") if r.get("decision") == "approved" and r.get("exit_time") else "",
                  "would_fill": "", "shadow_R": "", "note": r.get("reject_reason", "")})
        if r.get("decision") in ("skipped", "expired", "cancelled") and par and par.get("exit_time"):
            pe, ps, pex = _f(par.get("entry")), _f(par.get("sl")), _f(par.get("exit_price"))
            up_parent = (par.get("direction") or "").upper().startswith("B")
            R = abs(pe - ps) if pe and ps else None
            stopped = R and pex is not None and ((ps - pex) if up_parent else (pex - ps)) >= -0.02 * R
            bar = _h4_bar_number(r["symbol"], _ts(par.get("signal_time")), _ts(par.get("exit_time")))
            if stopped and bar is not None and 7 <= bar <= 18 and not (par.get("tp1_done") == "1"):
                x["would_fill"] = "yes"; x["bars_to_stop"] = bar
                sh = _replay(r["symbol"], not up_parent, ps, _f(r.get("sl")), _f(r.get("tp1")), _f(r.get("tp2")),
                             BANKF.get(par.get("strategy"), 0.5), _ts(par.get("exit_time")) or 0)
                x["shadow_R"] = "" if sh is None else round(sh, 3)
            else:
                x["would_fill"] = "no"
        out.append(x)
    return out


def write(cfg: dict) -> dict:
    rows = build(cfg)
    p = os.path.join(cfg["root"], "web", "inverse_shadow.csv"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for x in rows: w.writerow(x)
    os.replace(p + ".tmp", p)
    fills = [x for x in rows if x["decision"] == "approved" and _f(x["fill_slip_R"]) is not None]
    closed = [_f(x["actual_R"]) for x in fills if _f(x["actual_R"]) is not None]
    return {"cards": len(rows), "fills": len(fills),
            "slip_first10": (sum(_f(x["fill_slip_R"]) for x in fills[:10]) / 10.0) if len(fills) >= 10 else None,
            "closed": len(closed), "mean_R": (sum(closed) / len(closed)) if closed else None}
