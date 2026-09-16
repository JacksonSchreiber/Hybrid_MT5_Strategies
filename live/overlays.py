"""
overlays.py - the tester's display overlays recomputed from bars (mirrors DrawImbalances / DrawSwingMarkers /
DrawFibo in HybridForwardTest.mq5) so the web chart and the advisor PNG show what the tester chart showed.
bars: [[t,o,h,l,c,v]] oldest->newest; the LAST bar is treated as the forming bar and excluded from scans
(the EA scans closed bars: CopyRates(...,1,...)).
"""
from __future__ import annotations

FIB_LV = [0.0, 0.5, 0.618, 0.705, 0.786, 0.886, 1.0]
FIB_LT = ["0.0", "50.0", "61.8", "70.5 OTE", "78.6", "88.6 SL", "100.0"]

def fib_levels(leg: dict) -> list[tuple[float, str, bool]]:
    """(price, label, golden_pocket) for the DeepFib leg p0->p1 (levels measured back from p1)."""
    p0, p1 = leg.get("p0") or 0, leg.get("p1") or 0
    if p0 <= 0 or p1 <= 0: return []
    return [(p1 + lv * (p0 - p1), lt, 0.618 <= lv <= 0.786) for lv, lt in zip(FIB_LV, FIB_LT)]

def _imb_state(bars, iformed, lo, hi, d):
    inverted = False
    for j in range(iformed + 1, len(bars)):
        b = bars[j]; enters = b[2] >= lo and b[3] <= hi
        if not inverted:
            through = b[4] < lo if d > 0 else b[4] > hi
            if through: inverted = True; continue
            if enters: return 2
        elif enters: return 2
    return 1 if inverted else 0

def imbalances(bars: list[list], lookback: int = 120, vol_mult: float = 2.0, max_draw: int = 15) -> list[dict]:
    """[{t0, t1, lo, hi, dir, state}] newest first, used-up zones skipped, capped at max_draw."""
    r = bars[:-1] if len(bars) > 1 else bars       # closed bars only
    cnt = len(r)
    if cnt < 25: return []
    zones = []
    for i in range(max(2, cnt - lookback), cnt):
        if r[i - 2][2] < r[i][3]: zones.append((r[i - 2][2], r[i][3], +1, i))
        if r[i - 2][3] > r[i][2]: zones.append((r[i][2], r[i - 2][3], -1, i))
        if r[i][3] > r[i - 1][2]: zones.append((r[i - 1][2], r[i][3], +1, i))
        if r[i][2] < r[i - 1][3]: zones.append((r[i][2], r[i - 1][3], -1, i))
        if i >= 21:
            avg = sum(x[5] for x in r[i - 20:i]) / 20.0
            if avg > 0 and r[i][5] > vol_mult * avg: zones.append((r[i][3], r[i][2], +1 if r[i][4] >= r[i][1] else -1, i))
    out = []
    for lo, hi, d, i in reversed(zones):
        if len(out) >= max_draw: break
        st = _imb_state(r, i, lo, hi, d)
        if st == 2: continue
        out.append({"t0": r[i][0], "t1": r[cnt - 1][0], "lo": lo, "hi": hi, "dir": d, "state": st})
    return out

def swings(bars: list[list], days: int = 14, n: int = 2, bars_per_day: int = 6) -> list[dict]:
    """fractal swing highs/lows over the rolling window: [{t, p, kind: 'hi'|'lo'}]."""
    r = bars[:-1] if len(bars) > 1 else bars
    nb = min(days * bars_per_day + 8, len(r))
    if nb < 12: return []
    w = r[-nb:]; out = []
    for i in range(n, len(w) - n):
        sh = all(w[i][2] > w[i - k][2] and w[i][2] > w[i + k][2] for k in range(1, n + 1))
        sl = all(w[i][3] < w[i - k][3] and w[i][3] < w[i + k][3] for k in range(1, n + 1))
        if sh: out.append({"t": w[i][0], "p": w[i][2], "kind": "hi"})
        if sl: out.append({"t": w[i][0], "p": w[i][3], "kind": "lo"})
        if len(out) >= 120: break
    return out
