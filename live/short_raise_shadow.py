"""short_raise_shadow.py - coach item 25 (trader override 2026-10-01): the shorts-only stop raise, scored live.

On a SELL signal the EA moves every ticket's stop to +short_raise_at_pyramid_r R when +1.5R trades (journal short_raise=1,
with this ticket's pre-raise stop short_raise_from and its volume short_raise_vol). Each ARMED short gets a line in
<root>/web/short_raise.csv:
  actual_R                     the signal's full-position R (each ticket's r_multiple - already in the first tranche's 1R -
                               x its lots / the full lots: eligibility.price_r_scale);
  counterfactual_entry_stop_R  what the same signal would have made with the stop left where it was (at entry). The two
                               paths are identical until a ticket is stopped at the raised stop; only those tickets differ.
                               Each one is replayed on the feed's M5 bid bars from its exit, with the median live spread
                               (web/spread_live.csv) added for the ask: stop = short_raise_from (checked first each bar; a bar
                               opening through it fills at the ask open), target = the ticket's TP2 at the ask. A ticket still
                               unresolved stays 'pending' and the signal is not counted yet.
  diff = actual - counterfactual, in full-position R. Coach guard: early read at 15 resolved signals, judged at 30 - if the
  sum of diff is below zero at 30 the rule is retired (live.json short_raise_at_pyramid_r -> 0).
"""
from __future__ import annotations
import csv, os, statistics as st
from datetime import datetime, timezone

from live import common as C
from live import mt5feed

COLS = ["symbol", "signal_id", "strategy", "signal_time", "raise_time", "raise_stop", "status", "tickets", "tickets_stopped_at_raise",
        "actual_R", "counterfactual_entry_stop_R", "diff_R", "note"]


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _ts(s):   # journal times are broker clock 'YYYY.MM.DD HH:MM:SS' -> the feed's epoch (also broker clock)
    try: return int(datetime.strptime(s[:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError): return None


def _spread(cfg: dict) -> dict:
    p = os.path.join(cfg["root"], "web", "spread_live.csv"); d: dict = {}
    try:
        for r in csv.DictReader(open(p, newline="")):
            v = _f(r.get("spread"))
            if v is not None and v >= 0: d.setdefault(r.get("symbol"), []).append(v)
    except OSError: pass
    return {k: st.median(v) for k, v in d.items() if v}


def _replay_short(sym: str, t_from: int, stop: float, tp: float | None, spr: float):
    """SELL ticket from t_from with its stop at `stop` (ask), target tp (ask). -> exit price or None (unresolved)."""
    bars = mt5feed.bars_range(sym, "m5", t_from, int(datetime.now(timezone.utc).timestamp()) + 86400 * 3) or []
    for b in bars:
        if int(b[0]) < t_from: continue
        o, h, l = b[1] + spr, b[2] + spr, b[3] + spr
        if h >= stop: return max(stop, o)
        if tp and l <= tp: return min(tp, o) if o <= tp else tp
    return None


def build(cfg: dict) -> list[dict]:
    from live import eligibility as ELIG
    rows = [r for r in C.journal_rows(cfg) if r.get("decision") in ("approved", "approved_pending")]
    sigs: dict = {}
    for r in rows: sigs.setdefault((r.get("symbol"), r.get("signal_id")), []).append(r)
    spread = _spread(cfg); out = []
    for (sym, sid), rs in sorted(sigs.items(), key=lambda kv: kv[1][0].get("signal_time") or ""):
        armed = [r for r in rs if (r.get("short_raise") or "") == "1"]
        if not armed: continue
        first = next((r for r in rs if int(_f(r.get("tranche")) or 0) <= 1), rs[0])
        x = {"symbol": sym, "signal_id": sid, "strategy": first.get("strategy"), "signal_time": first.get("signal_time"),
             "raise_time": armed[0].get("short_raise_time"), "raise_stop": armed[0].get("short_raise_stop"), "tickets": len(rs),
             "tickets_stopped_at_raise": 0, "actual_R": "", "counterfactual_entry_stop_R": "", "diff_R": "", "note": ""}
        if any(not r.get("exit_time") for r in rs):
            x["status"] = "open"; out.append(x); continue
        e0, s0, full = _f(first.get("entry")), _f(first.get("sl")), _f(first.get("full_lots")) or _f(first.get("lots"))
        R0 = abs(e0 - s0) if e0 and s0 else None
        if not R0 or not full:
            x["status"] = "error"; x["note"] = "no first-tranche risk / size"; out.append(x); continue
        actual = 0.0
        for r in rs:
            rr = _f(r.get("r_multiple"))
            if rr is not None: actual += rr * ELIG.price_r_scale(r)        # r_multiple is already in the first tranche's 1R
        diff, pending, n_hit, notes = 0.0, False, 0, []
        for r in armed:
            stop, frm, vol, ex = _f(r.get("short_raise_stop")), _f(r.get("short_raise_from")), _f(r.get("short_raise_vol")), _f(r.get("exit_price"))
            if None in (stop, frm, vol, ex) or vol <= 0: continue
            if abs(ex - stop) > 0.10 * R0: continue                                 # this ticket did not leave at the raised stop
            n_hit += 1
            cf = _replay_short(sym, _ts(r.get("exit_time")) or 0, frm, _f(r.get("tp2")) or _f(r.get("tp")), spread.get(sym, 0.0))
            if cf is None: pending = True; notes.append(f"posid {r.get('posid')} unresolved"); continue
            diff += (cf - ex) * vol / (R0 * full)                                   # SELL: actual - counterfactual, full-position R
        x["tickets_stopped_at_raise"] = n_hit; x["actual_R"] = round(actual, 4)
        if pending: x["status"] = "pending_cf"; x["note"] = "; ".join(notes)
        else:
            x["status"] = "resolved"; x["diff_R"] = round(diff, 4); x["counterfactual_entry_stop_R"] = round(actual - diff, 4)
            if not n_hit: x["note"] = "no ticket left at the raised stop - same as the counterfactual"
        out.append(x)
    return out


def write(cfg: dict) -> tuple[int, float | None]:
    """-> (resolved armed shorts, sum of actual - counterfactual over them)"""
    rows = build(cfg)
    p = os.path.join(cfg["root"], "web", "short_raise.csv"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for x in rows: w.writerow(x)
    os.replace(p + ".tmp", p)
    res = [x for x in rows if x["status"] == "resolved"]
    return len(res), (sum(float(x["diff_R"]) for x in res) if res else None)
