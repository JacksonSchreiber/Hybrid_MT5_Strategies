"""queue_shadow.py - coach 2026-10-03 item 6: live grading of the coach-queue rules, one counter per rule against its counterfactual.
Read at n=30 per rule; the trader's ruling (2026-10-03): NOTHING is retired automatically - the monitor tells the trader, the rules stay on.

Per resolved signal (every ticket closed), a line in <root>/web/queue_rules.csv; increments are full-position R in the first tranche's 1R:
  A (add gate; the first row's reject_reason says the add was held):
      actual   = the released add's r_multiple x lots / full (0 if it was never released);
      cf       = the add as it would have gone in at the bar-6 close (feed H4 close of bar signal+6) for the remaining (full - first) lots,
                 exiting with the first tranche: its banked share at the bank level (+1R from the first fill, or TP1 when nearer) if it
                 banked, the rest at its final exit price;  increment = actual - cf.
  B (shorts day-2 cut; an actions-file DAY2_CUT line per closed ticket):
      cf       = each cut ticket's remaining lots replayed on the feed's M15 bars from the cut, from its stop at the cut, to TP2, banking
                 the detector's share at +1R if it had not banked (no pyramid in the replay);  increment = (cut price - cf exit) in R x
                 lots / full, summed over the cut tickets.  Unresolved while the replay is still open.
  I (improving add; a tranche-4 row, entry_mode improving_add):
      increment = the add's r_multiple x lots / full (the counterfactual is no add).
Resolved numbers are cached in <root>/web/queue_rules_cache.json (the feed keeps a limited M15 history).
"""
from __future__ import annotations
import csv, glob, os
from datetime import datetime, timezone

from live import common as C
from live import mt5feed

COLS = ["rule", "symbol", "signal_id", "strategy", "direction", "signal_time", "status", "actual_R", "cf_R", "increment_R", "note"]
BANKF = {"TrendCont": 0.25, "DeepFib": 0.25}


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _ts(s):
    try: return int(datetime.strptime((s or "")[:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        try: return int(datetime.strptime((s or "")[:16], "%Y.%m.%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
        except ValueError: return None


def _w(r):
    full, lots = _f(r.get("full_lots")) or 0.0, _f(r.get("lots")) or 0.0
    return lots / full if full > 0 and lots > 0 else 1.0


def _rule_a(sym, rs, first, sg, R):
    add = next((r for r in rs if r.get("tranche") == "2"), None)
    actual = (_f(add.get("r_multiple")) or 0.0) * _w(add) if add else 0.0
    full = _f(first.get("full_lots")) or 0.0; l1 = _f(first.get("lots")) or 0.0
    if full <= l1: return None, "no add size"
    t6 = _ts(first.get("signal_time"))
    if not t6: return None, "no signal time"
    h4 = [b for b in (mt5feed.bars_range(sym, "h4", t6, t6 + 14 * 86400) or []) if int(b[0]) >= t6]   # bar counting (weekends have no bars)
    if len(h4) < 7: return None, "bar 6 not in the feed yet"
    add_px = float(h4[6][4])                                                                  # the close of bar signal+6; e1 = _f(first.get("entry")); s1 = _f(first.get("sl"))
    fin = _f(first.get("exit_price"))
    if None in (e1, s1, fin): return None, "first tranche incomplete"
    pf = _f(first.get("partial_frac")) or 0.0
    bank_px = e1 + sg * abs(e1 - s1)
    tp1 = _f(first.get("tp1"))
    if tp1 and (sg > 0 and tp1 < bank_px or sg < 0 and tp1 > bank_px): bank_px = tp1
    toR = lambda p: (p - add_px) * sg / R
    cf_unit = (pf * toR(bank_px) + (1 - pf) * toR(fin)) if first.get("tp1_done") == "1" else toR(fin)
    return (actual, cf_unit * (full - l1) / full), ""


def _replay(sym, up, e, R, stop_px, tp2, banked, bankf, t_from, px0):
    """remaining ticket from the cut: -> exit price-R per unit (in the anchor's R, from px0) or None while still open"""
    bars = mt5feed.bars_range(sym, "m15", t_from, t_from + 30 * 86400) or []
    bars = [b for b in bars if int(b[0]) >= t_from]
    if not bars: return None
    sg = 1.0 if up else -1.0; toR = lambda p: (p - px0) * sg / R
    r1 = e + sg * R; locked, size, stop = 0.0, 1.0, stop_px
    for b in bars:
        o, hi, lo = float(b[1]), float(b[2]), float(b[3])
        adv, fav = (lo, hi) if up else (hi, lo)
        if (adv <= stop) if up else (adv >= stop): return locked + size * toR(o if ((o < stop) if up else (o > stop)) else stop)
        if not banked and ((fav >= r1) if up else (fav <= r1)):
            banked = True; locked += bankf * toR(r1); size *= 1 - bankf; stop = e
        if tp2 and ((fav >= tp2) if up else (fav <= tp2)): return locked + size * toR(tp2)
    return None


def build(cfg: dict) -> list[dict]:
    cache_p = os.path.join(cfg["root"], "web", "queue_rules_cache.json")
    cache = C.load_json(cache_p, {}) or {}
    rows = C.journal_rows(cfg)
    sigs: dict = {}
    for r in rows:
        if r.get("decision") in ("approved", "approved_pending"): sigs.setdefault((r.get("symbol"), r.get("signal_id"), (r.get("signal_time") or "")[:16]), []).append(r)
    cuts: dict = {}
    for p in glob.glob(os.path.join(cfg["root"], "journal", "*.actions.csv")):
        sym = os.path.basename(p).split("_")[0]
        try:
            for a in csv.DictReader(open(p, encoding="ascii", errors="replace", newline="")):
                if a.get("action") == "DAY2_CUT": cuts.setdefault((sym, a.get("posid")), a)
        except OSError: pass
    out = []
    for (sym, sid, st), rs in sigs.items():
        first = next((r for r in rs if int(_f(r.get("tranche")) or 0) <= 1), rs[0])
        e1, s1 = _f(first.get("entry")), _f(first.get("sl"))
        if not e1 or not s1 or e1 == s1: continue
        up = (first.get("direction") or "").upper().startswith("B"); sg = 1.0 if up else -1.0; R = abs(e1 - s1)
        closed = all((r.get("exit_time") or "").strip() for r in rs)
        base = {"symbol": sym, "signal_id": sid, "strategy": first.get("strategy"), "direction": first.get("direction"), "signal_time": first.get("signal_time")}
        jobs = []
        rj = (first.get("reject_reason") or "")
        if "add held" in rj or "held (gate)" in rj: jobs.append("A")
        if any((sym, r.get("posid")) in cuts for r in rs): jobs.append("B")
        if any(r.get("tranche") == "4" and (r.get("entry_mode") or "") == "improving_add" for r in rs): jobs.append("I")
        for rule in jobs:
            key = f"{rule}|{sym}|{sid}|{st}"; x = dict(base, rule=rule, actual_R="", cf_R="", increment_R="", note="")
            if key in cache: x.update(cache[key]); out.append(x); continue
            if not closed: x["status"] = "open"; out.append(x); continue
            res, note = None, ""
            try:
                if rule == "A":
                    res, note = _rule_a(sym, rs, first, sg, R)
                elif rule == "I":
                    a4 = [r for r in rs if r.get("tranche") == "4"]
                    v = sum((_f(r.get("r_multiple")) or 0.0) * _w(r) for r in a4); res = (v, 0.0)
                else:
                    act = cf = 0.0; pend = False
                    for r in rs:
                        a = cuts.get((sym, r.get("posid")))
                        if not a: continue
                        px, lots_b, slp = _f(a.get("price")), _f(a.get("lots_before")) or 0.0, _f(a.get("sl_after"))
                        full = _f(r.get("full_lots")) or _f(first.get("lots")) or 0.0
                        if not px or not slp or full <= 0: continue
                        tp2 = _f(r.get("tp2")) or _f(r.get("tp"))
                        rem = _replay(sym, up, e1, R, slp, tp2, r.get("tp1_done") == "1", BANKF.get(first.get("strategy"), 0.5), _ts(a.get("bar_time")) or 0, px)
                        if rem is None: pend = True; break
                        act += 0.0; cf += rem * lots_b / full                     # actual: closed at px (0 from px); cf: the replay's exit from px
                    res = None if pend else (act, cf)
                    if pend: note = "counterfactual still open"
            except Exception as e:
                note = f"error: {e!r}"
            if res is None: x["status"] = "pending"; x["note"] = note; out.append(x); continue
            x.update(status="resolved", actual_R=round(res[0], 4), cf_R=round(res[1], 4), increment_R=round(res[0] - res[1], 4), note=note)
            cache[key] = {k: x[k] for k in ("status", "actual_R", "cf_R", "increment_R", "note")}
            out.append(x)
    C.atomic_write_json(cache_p, cache)
    return out


def write(cfg: dict) -> dict:
    rows = build(cfg)
    p = os.path.join(cfg["root"], "web", "queue_rules.csv"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for x in rows: w.writerow(x)
    os.replace(p + ".tmp", p)
    out = {}
    for rule in ("A", "B", "I"):
        v = [float(x["increment_R"]) for x in rows if x["rule"] == rule and x["status"] == "resolved"]
        out[rule] = (len(v), sum(v))
    return out
