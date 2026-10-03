"""rpaths_live.py - trader 2026-10-03: live trades for the Data visualizers tab (the R-path explorer).

Every CLOSED live signal (TrendCont / SweepMSS / DeepFib / EMArevQ; every approved ticket of the signal has an exit) becomes one
trade in the same format as the historical file (live/static/rpaths.json, pipeline/rpaths_build.py), with src = "live":
  path = the price in the trade's original 1R (first tranche's entry -> its original stop), signed with the trade, bid side;
  rt   = the close of every H4 bar after entry (feed bars), last point = the final exit level; capped at 180 bars;
  pg   = 41 points from entry to the final exit (feed M5 closes), last = the final exit level;
  o    = "tp2" (final exit within 0.05R of TP2), "be" (final exit above -0.5R: back to entry / the +0.25R shorts stop / a manual exit
         near entry) or "sl" (stopped below -0.5R);  f6 = best R in the first 6 H4 bars;  xb = exit time in H4 bars.
Written to <root>/web/rpaths_live.json by the monitor (hourly); the webapp merges it with the historical file.
Journal times are the broker clock ('YYYY.MM.DD HH:MM:SS'), the same clock as the feed's bar epochs.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone

from live import common as C
from live import mt5feed

DETS = ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")
CAP = 180; NPG = 41


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _ts(s):
    try: return int(datetime.strptime(s[:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError): return None


def _trade(sym: str, rs: list[dict]) -> dict | None:
    first = next((r for r in rs if int(_f(r.get("tranche")) or 0) <= 1), rs[0])
    if first.get("strategy") not in DETS: return None
    e0, s0, tp2 = _f(first.get("entry")), _f(first.get("sl")), _f(first.get("tp2")) or _f(first.get("tp"))
    if not e0 or not s0 or e0 == s0: return None
    up = (first.get("direction") or "").upper().startswith("B"); R = abs(e0 - s0); sg = 1.0 if up else -1.0
    toR = lambda p: (p - e0) * sg / R
    t_sig = _ts(first.get("signal_time"))
    t_in = _ts(first.get("fill_time")) or ((t_sig + 4 * 3600) if t_sig else None)     # entry at the open of the bar after the signal bar
    exits = [(_ts(r.get("exit_time")), _f(r.get("exit_price"))) for r in rs if r.get("decision") in ("approved", "approved_pending")]
    if not t_in or not exits or any(t is None or p is None for t, p in exits): return None
    t_out, px_out = max(exits)
    h4 = mt5feed.bars_range(sym, "h4", t_in - 4 * 3600, t_out + 4 * 3600) or []
    m5 = mt5feed.bars_range(sym, "m5", t_in, t_out) or []
    if not h4 or not m5: return None
    x_out = toR(px_out)
    after = [b for b in h4 if int(b[0]) >= t_in - 4 * 3600 + 1]                       # bars from the entry bar on
    rt = []
    for b in after:
        if int(b[0]) + 4 * 3600 > t_out: break                                         # this bar closes after the exit
        rt.append(round(toR(float(b[4])) * 100))
        if len(rt) >= CAP: break
    if len(rt) < CAP: rt.append(round(x_out * 100))
    closes = [float(b[4]) for b in m5]
    idx = [round(i * (len(closes) - 1) / (NPG - 1)) for i in range(NPG)]
    pg = [round(toR(closes[i]) * 100) for i in idx[:-1]] + [round(x_out * 100)]
    first6 = after[:6]
    f6 = max((toR(float(b[2])) if up else toR(float(b[3]))) for b in first6) if first6 else 0.0
    tp2R = toR(tp2) if tp2 else None
    o = "tp2" if (tp2R is not None and x_out >= tp2R - 0.05) else ("be" if x_out > -0.5 else "sl")
    return {"s": sym.split(".")[0], "d": first.get("strategy"), "l": 1 if up else 0, "y": int(first.get("signal_time", "0000")[:4] or 0),
            "o": o, "tp": round(tp2R, 2) if tp2R is not None else None, "f6": round(f6, 2), "xb": round((t_out - t_in) / 14400.0, 2),
            "src": "live", "rt": rt, "pg": pg, "key": f"{sym}-{first.get('signal_id')}"}


def build(cfg: dict) -> list[dict]:
    sigs: dict = {}
    for r in C.journal_rows(cfg):
        if r.get("decision") not in ("approved", "approved_pending"): continue
        sigs.setdefault((r.get("symbol"), r.get("signal_id"), (r.get("signal_time") or "")[:16]), []).append(r)
    out = []
    for (sym, sid, _), rs in sigs.items():
        if not all((r.get("exit_time") or "").strip() and (r.get("terminal") or "").strip() for r in rs): continue   # still open
        try:
            t = _trade(sym, rs)
        except Exception:
            t = None
        if t: out.append(t)
    return out


def write(cfg: dict) -> int:
    trades = build(cfg)
    p = os.path.join(cfg["root"], "web", "rpaths_live.json"); os.makedirs(os.path.dirname(p), exist_ok=True)
    C.atomic_write_json(p, {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "trades": trades})
    return len(trades)
