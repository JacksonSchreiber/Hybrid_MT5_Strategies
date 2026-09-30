"""correlation.py - coach item 9 (ruled 2026-09-30 late on the item-16 co-movement study): measured correlation on the card.

For the signal and every open position: the 60-day correlation of daily log returns, on CLOSED weekday D1 bars only
(the forming day is dropped; weekend bars are dropped, as live OANDA has none), signed by direction (same direction ->
+corr, opposite -> -corr). Weight: 1.0 when signed corr >= 0.6 or the same symbol in the same direction; 0.5 for
0.3-0.6; 0 below 0.3. Effective correlated risk = this signal's risk + sum(open UNBANKED risk x weight). Past 1.5% it
is a correlation veto (code 5). A position already banked, or whose stop sits at or beyond entry, has no unbanked risk.
Item 16's evidence: two 1R positions with signed corr > 0.6 close on the same side 73.5% of the time (1.73R effective).
"""
from __future__ import annotations
import datetime as dt, math

from live import common as C
from live import mt5feed

WINDOW = 60
CAP = 1.5            # % of the account
W_HIGH, W_MID = 0.6, 0.3


def _closes(symbol: str) -> dict[str, float]:
    bars = mt5feed.bars(symbol, "d1", WINDOW + 15) or []
    out = {}
    for b in bars[:-1]:                                                   # the last bar is today, still forming
        t = b.get("t") if isinstance(b, dict) else b[0]
        c = b.get("c") if isinstance(b, dict) else b[4]
        d = dt.datetime.fromtimestamp(int(t), dt.timezone.utc)
        if d.weekday() >= 5: continue
        out[d.strftime("%Y-%m-%d")] = float(c)
    return out


def corr60(a: dict[str, float], b: dict[str, float]) -> float | None:
    days = sorted(set(a) & set(b))[-(WINDOW + 1):]
    if len(days) < 30: return None
    ra = [math.log(a[days[i]] / a[days[i - 1]]) for i in range(1, len(days))]
    rb = [math.log(b[days[i]] / b[days[i - 1]]) for i in range(1, len(days))]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    va = sum((x - ma) ** 2 for x in ra); vb = sum((y - mb) ** 2 for y in rb)
    if va <= 0 or vb <= 0: return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / math.sqrt(va * vb)


def _sign(d) -> int: return 1 if str(d).upper() in ("BUY", "LONG", "1") else -1


def _unbanked_pct(p: dict) -> float:
    rp = p.get("risk_pct_effective")
    if not rp or p.get("banked"): return 0.0
    try:
        e, s = float(p.get("entry") or 0), float(p.get("sl_live") or p.get("sl") or 0)
        if e and s and (s >= e if _sign(p.get("direction")) > 0 else s <= e): return 0.0   # stop at/through entry: nothing at risk
    except (TypeError, ValueError): pass
    return float(rp) * 100.0


def _feed_risk() -> dict[int, float]:
    """ticket -> risk to the CURRENT stop in % of equity, from the terminal (0 once the stop is at or past entry)."""
    try: return {int(a["ticket"]): max(0.0, float(a["risk_pct"])) for a in (mt5feed.positions() or [])
                 if a.get("risk_pct") is not None and _stop_behind(a)}
    except Exception: return {}


def _stop_behind(a: dict) -> bool:
    """True when the stop still sits on the losing side of entry (a stop at/through entry risks nothing)."""
    po, sl = float(a.get("price_open") or 0), float(a.get("sl") or 0)
    if not sl: return True
    return sl < po if _sign(a.get("direction")) > 0 else sl > po


def compute(sig: dict, cfg: dict) -> dict:
    own = float((sig.get("sizing") or {}).get("risk_pct_effective") or 0) * 100.0
    sym, sd = sig.get("symbol", ""), _sign(sig.get("direction"))
    cache: dict[str, dict] = {}
    get = lambda s: cache.setdefault(s, _closes(s))
    pairs, total, missing = [], own, False
    try: mine = get(sym)
    except Exception: mine = {}
    feed = _feed_risk()
    for p in C.list_positions(cfg):
        tk = int(p.get("posid") or 0)
        risk = feed.get(tk, 0.0) if feed and tk in feed else (_unbanked_pct(p) if not feed else 0.0)
        if p.get("banked"): risk = 0.0
        pd_ = _sign(p.get("direction"))
        same = (p.get("symbol") == sym and pd_ == sd)
        if same: c = 1.0
        else:
            try: c = corr60(mine, get(p["symbol"]))
            except Exception: c = None
        if c is None: missing = True
        signed = None if c is None else (c if pd_ == sd else -c)
        w = 1.0 if same else (0.0 if signed is None else (1.0 if signed >= W_HIGH else (0.5 if signed >= W_MID else 0.0)))
        pairs.append({"symbol": p["symbol"], "direction": p.get("direction"), "signal_id": p.get("signal_id"),
                      "unbanked": risk, "corr": c, "signed": signed, "weight": w, "adds": risk * w, "same": same})
        total += risk * w
    return {"own": own, "pairs": pairs, "total": total, "cap": CAP, "veto": total > CAP + 1e-9, "missing": missing}


def text(r: dict) -> str:
    """the card/advisor lines."""
    lines = [f"- **Correlated risk (measured, 60d D1, server-computed — authoritative):** this signal {r['own']:.2f}% "
             f"+ weighted unbanked open risk = **{r['total']:.2f}%** of the {r['cap']:.1f}% cap"
             + (" → **CORRELATION VETO (code 5)**" if r["veto"] else " → within the cap")]
    for x in sorted(r["pairs"], key=lambda x: -x["adds"]):
        cs = "same symbol, same direction" if x["same"] else ("corr n/a" if x["corr"] is None else f"corr {x['corr']:+.2f} → signed {x['signed']:+.2f}")
        lines.append(f"    - {x['symbol']} {x['direction']} #{x['signal_id']}: {cs} · weight {x['weight']:.1f} × unbanked "
                     f"{x['unbanked']:.2f}% = +{x['adds']:.2f}%")
    if not r["pairs"]: lines.append("    - no open positions")
    if r["missing"]: lines.append("    - (a correlation could not be computed - its position is weighted 0; check the chart)")
    return "\n".join(lines)
