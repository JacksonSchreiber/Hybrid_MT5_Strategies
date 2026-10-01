"""
eligibility.py - per-symbol live-eligibility counter for the dashboard (coach 2026-09-21, universe expansion).

For every symbol: demo DECISIONS and cumulative COSTED R, flagged when a symbol reaches >= 20 decisions with costed
R >= 0 (the evidence a symbol would need before a funded seat - the flag is information, not a promotion).

Decision = a signal the trader was asked about and that resolved: approved (incl. pending fills) or skipped codes 1-8.
Not decisions: code 9 superseded (§11.9 - never a decision), auto=1 election skips (the EA decided), `rejected`
(invalidated / min-stop - nobody decided), TEST rows. Code 8 is counted and shown separately: it is a decision the
trader let lapse, and on a TAKE-class signal a chargeable violation (§11.9).

Costed R is the ACCOUNT's view, not a model: every deal of the position from the broker (profit + swap + commission +
fee, entry deal included) divided by the stop priced in account currency (|entry - SL| x lots x tick value / tick
size). Live fills already carry the spread. Computed once per closed position through the feed and cached in
<root>/web/costed_r.json; a closed row the feed has not priced yet shows as uncosted, never as zero.
"""
from __future__ import annotations
import os
from live import common as C

FLAG_N = 20

def _cache_path(cfg: dict) -> str: return os.path.join(cfg["root"], "web", "costed_r.json")

def _is_decision(r: dict) -> tuple[bool, str]:
    if (r.get("strategy") or "").strip().upper() == "TEST": return False, ""
    d = (r.get("decision") or "").strip(); sk = str(r.get("skip_reason") or "").strip()
    if str(r.get("auto") or "").strip() == "1": return False, ""
    if d in ("approved", "approved_pending"): return True, "approved"
    if d == "expired" and str(r.get("is_pending") or "").strip() == "1": return True, "approved"   # approved as a pending order that never filled
    if d == "skipped":
        if sk == "9": return False, ""
        if sk == "8": return True, "no_response"
        return True, "skipped"
    return False, ""

def refresh(cfg: dict, max_n: int = 25, log=None) -> int:
    """price up to max_n closed approved positions that are not in the cache yet (feed calls; run from the monitor)."""
    from live import mt5feed
    cache = C.load_json(_cache_path(cfg), {}) or {}; done = 0
    for r in C.journal_rows(cfg):
        pid = str(r.get("posid") or "").strip()
        if done >= max_n or not pid or pid == "0" or pid in cache: continue
        if (r.get("decision") or "") not in ("approved", "approved_pending") or not (r.get("exit_time") or "").strip() or (r.get("terminal") or "").strip() == "": continue
        try: entry, sl, lots = float(r["entry"]), float(r["sl"]), float(r["lots"])
        except (KeyError, ValueError): continue
        d = mt5feed.position_costs(int(pid))
        if not d or not d.get("n") or not d.get("tick_size") or not d.get("tick_value"): continue
        risk = abs(entry - sl) / d["tick_size"] * d["tick_value"] * lots
        if risk <= 0: continue
        cache[pid] = {"symbol": r.get("symbol"), "signal_id": r.get("signal_id"), "net": round(d["net"], 2), "risk_ccy": round(risk, 2),
                      "costed_r": round(d["net"] / risk, 4), "price_r": float(r.get("r_multiple") or 0), "swap": d.get("swap"),
                      "commission": d.get("commission"), "fee": d.get("fee"), "ts": C.now_iso()}
        done += 1
    if done:
        C.atomic_write_json(_cache_path(cfg), cache)
        if log: log(f"eligibility: priced {done} closed position(s)")
    return done

def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def _full_scale(r: dict, first: dict | None) -> float:
    """trader 2026-10-01: R in FULL-POSITION units. A staged first tranche stopped in trial lost 1R on 25% of the size =
    -0.25R of the position; a staged add or a pyramid add is likewise a fraction of the full ruled position. Scale the row's
    own costed R (net / its own stop risk) by its own risk over the full position's risk at the first entry."""
    full = _f(r.get("full_lots")) or 0.0
    lots = _f(r.get("lots")) or 0.0
    if full <= 0 or lots <= 0: return 1.0
    base = first or r
    e0, s0, e, s = _f(base.get("entry")), _f(base.get("sl")), _f(r.get("entry")), _f(r.get("sl"))
    if not e0 or not s0 or not e or not s or e == s: return lots / full
    return (abs(e - s) * lots) / (abs(e0 - s0) * full)

def price_r_scale(r: dict) -> float:
    """2026-10-01: the weight for the EA's own r_multiple (journal column), NOT for costed_r. The EA measures every tranche -
    first, staged add, pyramid add - against the FIRST tranche's 1R (risk_px = the anchor risk), per unit of the row's own lots,
    so the full-position share is just lots / full lots. (_full_scale's extra |entry - sl| factor belongs to costed_r, which is
    net / the row's OWN stop risk; applied to r_multiple it over-weighted a pyramid add ~2.5x.)"""
    full, lots = _f(r.get("full_lots")) or 0.0, _f(r.get("lots")) or 0.0
    return lots / full if full > 0 and lots > 0 else 1.0

def table(cfg: dict) -> list[dict]:
    cache = C.load_json(_cache_path(cfg), {}) or {}
    syms = {s: None for s in C.symbols(cfg)}
    agg: dict[str, dict] = {}
    rows = C.journal_rows(cfg)
    firsts = {(r.get("symbol"), r.get("signal_id")): r for r in rows if int(_f(r.get("tranche")) or 0) <= 1 and r.get("strategy") != "Inverse"}
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        if not sym: continue
        a = agg.setdefault(sym, {"symbol": sym, "decisions": 0, "approved": 0, "skipped": 0, "no_response": 0, "closed": 0, "costed_r": 0.0, "uncosted": 0, "open": 0})
        tr = int(_f(r.get("tranche")) or 0)
        pid = str(r.get("posid") or "").strip()
        closed_row = bool((r.get("exit_time") or "").strip() and (r.get("terminal") or "").strip())
        if tr >= 2:                                           # a staged add or a pyramid add: part of its signal, not a decision
            if closed_row and pid in cache:
                a["costed_r"] += cache[pid]["costed_r"] * _full_scale(r, firsts.get((r.get("symbol"), r.get("signal_id"))))
            elif closed_row: a["uncosted"] += 1
            continue
        ok, kind = _is_decision(r)
        if not ok: continue
        a["decisions"] += 1; a[kind] += 1
        if kind == "approved":
            if closed_row:
                a["closed"] += 1
                if pid in cache: a["costed_r"] += cache[pid]["costed_r"] * _full_scale(r, None)
                else: a["uncosted"] += 1
            elif pid and pid != "0": a["open"] += 1
    for s in syms:
        agg.setdefault(s, {"symbol": s, "decisions": 0, "approved": 0, "skipped": 0, "no_response": 0, "closed": 0, "costed_r": 0.0, "uncosted": 0, "open": 0})
    for a in agg.values():
        a["flag"] = a["decisions"] >= FLAG_N and a["costed_r"] >= 0 and a["uncosted"] == 0
    return sorted(agg.values(), key=lambda a: (-a["decisions"], a["symbol"]))
