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

def table(cfg: dict) -> list[dict]:
    cache = C.load_json(_cache_path(cfg), {}) or {}
    syms = {s: None for s in C.symbols(cfg)}
    agg: dict[str, dict] = {}
    for r in C.journal_rows(cfg):
        sym = (r.get("symbol") or "").strip()
        if not sym: continue
        a = agg.setdefault(sym, {"symbol": sym, "decisions": 0, "approved": 0, "skipped": 0, "no_response": 0, "closed": 0, "costed_r": 0.0, "uncosted": 0, "open": 0})
        ok, kind = _is_decision(r)
        if not ok: continue
        a["decisions"] += 1; a[kind] += 1
        if kind == "approved":
            pid = str(r.get("posid") or "").strip()
            if (r.get("exit_time") or "").strip() and (r.get("terminal") or "").strip():
                a["closed"] += 1
                if pid in cache: a["costed_r"] += cache[pid]["costed_r"]
                else: a["uncosted"] += 1
            elif pid and pid != "0": a["open"] += 1
    for s in syms:
        agg.setdefault(s, {"symbol": s, "decisions": 0, "approved": 0, "skipped": 0, "no_response": 0, "closed": 0, "costed_r": 0.0, "uncosted": 0, "open": 0})
    for a in agg.values():
        a["flag"] = a["decisions"] >= FLAG_N and a["costed_r"] >= 0 and a["uncosted"] == 0
    return sorted(agg.values(), key=lambda a: (-a["decisions"], a["symbol"]))
