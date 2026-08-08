#!/usr/bin/env python3
"""export_d1_stats.py — cutoff-truncated D1 market stats for HISTORICAL market
briefs (docs/assistant-app-implementation.md §4b; consumed by the `market-brief`
skill's historical mode, .claude/skills/market-brief/SKILL.md).

    ./pipeline/export_d1_stats.py --symbol EURUSD --asof 2022-06-15

Prints a JSON object of D1 stats computed **only from bars at or before the
cutoff** — no lookahead. Everything the market-brief template needs is expressed
in **redaction-ready units** (range-position percentiles/thirds, ATR-as-percent,
volatility percentiles, relative durations) so the brief can be filled
mechanically without ever emitting an absolute price or date. Macro backdrop and
instrument quirks are deliberately NOT here — they can't be computed from price
and can't be safely recalled without lookahead (per the skill's hard rule).

Lookahead safety: the D1 series is truncated at the cutoff before any statistic
is computed. Trend/volatility/range use only that truncated series. Event density
"ahead" uses the economic calendar's SCHEDULED times only (foreknowable to a live
trader) — never outcomes.

Data source: QuantDataManager M1 export (data/qdm_csv/<BASE>-M1*.csv) aggregated
to D1. If the symbol has no data file, the script exits non-zero with a clear
message — the skill then refuses to produce a historical brief (its rule), rather
than falling back to memory or the web.

stdlib only (+ pipeline.tier0 for the event-density helpers).
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QDM_DIR = REPO / "data" / "qdm_csv"

# the market-brief skill invokes this as a script (./pipeline/export_d1_stats.py),
# so put the repo root on sys.path for the `pipeline` package import below.
sys.path.insert(0, str(REPO))

# reuse the harness's high-impact classifier + currency mapping + calendar loader
from pipeline import tier0  # noqa: E402


# --------------------------------------------------------------------------- #
def _find_m1(symbol: str) -> Path | None:
    base = symbol.split(".")[0].upper()
    hits = sorted(glob.glob(str(QDM_DIR / f"{base}-M1*.csv")))
    return Path(hits[0]) if hits else None


def _aggregate_d1(m1_path: Path, asof: datetime):
    """Stream the M1 CSV → ordered list of daily bars with date <= asof.
    Format: Date(YYYYMMDD),Time,Open,High,Low,Close,Volume."""
    asof_key = asof.strftime("%Y%m%d")
    days: dict[str, list] = {}     # date -> [o,h,l,c,v]
    order: list[str] = []
    with open(m1_path, "r", encoding="utf-8", errors="replace") as fh:
        fh.readline()  # header
        for line in fh:
            p = line.split(",")
            if len(p) < 6:
                continue
            d = p[0].strip()
            if d > asof_key:        # chronological ascending → past cutoff, stop
                break
            try:
                o, h, l, c = float(p[2]), float(p[3]), float(p[4]), float(p[5])
                v = float(p[6]) if len(p) > 6 and p[6].strip() else 0.0
            except ValueError:
                continue
            b = days.get(d)
            if b is None:
                days[d] = [o, h, l, c, v]
                order.append(d)
            else:
                if h > b[1]:
                    b[1] = h
                if l < b[2]:
                    b[2] = l
                b[3] = c
                b[4] += v
    return [(d, *days[d]) for d in order]     # (yyyymmdd, o,h,l,c,v)


# --- indicators (plain, on the truncated D1 close series) ------------------- #
def _ema(vals, period):
    k = 2.0 / (period + 1)
    e = vals[0]
    out = [e]
    for x in vals[1:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def _atr(bars, period=14):
    """Wilder ATR series aligned to bars (index 0..len-1); first `period` are None."""
    trs = []
    for i, b in enumerate(bars):
        _, o, h, l, c, _v = b
        if i == 0:
            trs.append(h - l)
        else:
            pc = bars[i - 1][4]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = [None] * len(bars)
    if len(bars) >= period:
        first = sum(trs[:period]) / period
        atr[period - 1] = first
        prev = first
        for i in range(period, len(bars)):
            prev = (prev * (period - 1) + trs[i]) / period
            atr[i] = prev
    return atr


def _percentile(sorted_vals, x):
    """Percent of values <= x (0..100)."""
    if not sorted_vals:
        return None
    import bisect
    return round(100.0 * bisect.bisect_right(sorted_vals, x) / len(sorted_vals), 1)


def compute_stats(bars, symbol: str, asof: datetime, events) -> dict:
    closes = [b[4] for b in bars]
    n = len(bars)
    ema20 = _ema(closes, 20)
    atr = _atr(bars, 14)
    last_close = closes[-1]
    last_atr = next((atr[i] for i in range(n - 1, -1, -1) if atr[i] is not None), None)

    # --- trend: direction + how long price has held one side of the D1 EMA20 ---
    side = 1 if last_close >= ema20[-1] else -1
    held = 0
    for i in range(n - 1, -1, -1):
        if (closes[i] >= ema20[i]) == (side > 0):
            held += 1
        else:
            break
    # EMA20 slope over ~20 bars, normalised to ATR/day (scale-free)
    look = min(20, n - 1)
    slope = (ema20[-1] - ema20[-1 - look]) / look if look > 0 else 0.0
    slope_atr = round(slope / last_atr, 3) if last_atr else None
    if abs(slope_atr or 0) < 0.05:
        character, direction = "range", "sideways"
    else:
        direction = "up" if side > 0 else "down"
        # a very recent flip (short hold) after a long prior run reads as transition
        character = "transition" if held <= 5 else "trend"

    # --- range position within the trailing ~2 years -------------------------
    win = min(504, n)
    seg = bars[-win:]
    hi = max(b[2] for b in seg)   # highest high
    lo = min(b[3] for b in seg)   # lowest low
    pos_pct = round(100.0 * (last_close - lo) / (hi - lo), 1) if hi > lo else 50.0
    third = "lower" if pos_pct < 33.3 else ("upper" if pos_pct > 66.6 else "middle")

    # --- volatility regime ---------------------------------------------------
    yr = min(252, n)
    atr_hist = sorted(a for a in atr[-yr:] if a is not None)
    atr_pctile = _percentile(atr_hist, last_atr) if (atr_hist and last_atr) else None
    back = 20
    atr_back = atr[-1 - back] if n > back and atr[-1 - back] is not None else None
    if last_atr and atr_back:
        ratio = last_atr / atr_back
        vol_state = "expanding" if ratio > 1.15 else ("contracting" if ratio < 0.85 else "stable")
    else:
        ratio, vol_state = None, "unknown"
    atr_pct_price = round(100.0 * last_atr / last_close, 3) if last_atr else None

    # --- event density AHEAD (scheduled only; foreknowable) ------------------
    ccy = tier0.symbol_currencies(symbol)
    def count_ahead(days):
        end = asof + timedelta(days=days)
        return sum(1 for e in events
                   if tier0.is_high_impact(e["name"]) and e["ccy"] in ccy
                   and asof < e["t"] <= end)
    nearest, affects = None, set()
    horizon_end = asof + timedelta(days=14)
    for e in events:
        if not tier0.is_high_impact(e["name"]) or e["ccy"] not in ccy:
            continue
        if asof < e["t"] <= horizon_end:
            affects.add(tier0.symbol_leg(symbol, e["ccy"]))
            dh = (e["t"] - asof).total_seconds() / 3600.0
            if nearest is None or dh < nearest:
                nearest = dh
    aff = ("none" if not affects else "both" if len(affects) > 1
           else next(iter(affects)))

    return {
        "meta": {
            "symbol": symbol, "asof": asof.strftime("%Y-%m-%d"),
            "d1_bars_used": n, "d1_from": _fmt(bars[0][0]), "d1_to": _fmt(bars[-1][0]),
            "source": f"{_find_m1(symbol).name} (M1→D1)",
            "lookahead_safe": True,
        },
        "trend": {
            "direction": direction, "character": character,
            "closes_held_beyond_ema20": held,
            "age_months": round(held / 21.0, 1),
            "ema20_slope_atr_per_day": slope_atr,
        },
        "range_position": {
            "window_months": round(win / 21.0, 1),
            "percentile": pos_pct, "third": third,
        },
        "volatility": {
            "realized_vol_percentile_1y": atr_pctile,
            "state": vol_state,
            "vs_20d_ago_ratio": round(ratio, 2) if ratio else None,
            "atr_as_pct_of_price": atr_pct_price,
        },
        "event_density": {
            "high_impact_next_7d": count_ahead(7),
            "high_impact_next_14d": count_ahead(14),
            "hours_until_nearest": round(nearest, 1) if nearest is not None else None,
            "affects": aff,
        },
    }


def _fmt(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def main():
    ap = argparse.ArgumentParser(description="Cutoff-truncated D1 stats for historical market briefs.")
    ap.add_argument("--symbol", required=True, help="e.g. EURUSD (or EURUSD.dk)")
    ap.add_argument("--asof", required=True, help="cutoff date YYYY-MM-DD (inclusive)")
    ap.add_argument("--min-bars", type=int, default=60,
                    help="minimum D1 bars required to compute trend (else refuse)")
    a = ap.parse_args()

    try:
        asof = datetime.strptime(a.asof, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"ERROR: --asof must be YYYY-MM-DD, got {a.asof!r}")

    m1 = _find_m1(a.symbol)
    if not m1:
        sys.exit(f"ERROR: no M1 data for {a.symbol!r} in {QDM_DIR} "
                 f"(looked for {a.symbol.split('.')[0].upper()}-M1*.csv). "
                 "Historical brief cannot be computed — refuse it.")

    bars = _aggregate_d1(m1, asof)
    if len(bars) < a.min_bars:
        sys.exit(f"ERROR: only {len(bars)} D1 bars at/before {a.asof} "
                 f"(need ≥{a.min_bars}). Not enough history to compute stats "
                 "without lookahead — refuse the brief.")

    events = tier0._load_events()
    stats = compute_stats(bars, a.symbol, asof, events)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
