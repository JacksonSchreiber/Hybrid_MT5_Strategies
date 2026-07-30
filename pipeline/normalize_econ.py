#!/usr/bin/env python3
"""Normalize a ForexFactory-derived high-impact calendar CSV into the file the
HybridForwardTest EA reads (Common\\Files\\econ_events.csv).

Input  (ehsanrs2 archive format): DateTime,Currency,Impact,Event,Actual,Forecast,Previous,Detail
Output (EA format):               datetime_utc,ccy,event,actual,forecast,ccy_bias

- datetime_utc: 'YYYY.MM.DD HH:MM' in UTC (MT5 StringToTime parses this).
- ccy_bias: surprise direction FOR THE EVENT'S CURRENCY, precomputed here where
  the numeric parsing is easy:  +1 bullish / -1 bearish / 0 none-or-unknown.
  The EA flips this for the traded symbol's base/quote to get the ticker bias.
  Polarity: higher-actual-than-forecast is bullish for the currency for most
  indicators; INVERSE for "bad-when-high" ones (unemployment / jobless claims).

Usage: normalize_econ.py <source.csv> <out.csv>
"""
import csv, re, sys
from collections import Counter
from datetime import datetime, timezone

INVERSE = ("unemployment rate", "jobless claim", "unemployment claim",
           "continuing claim", "initial jobless", "misery index")

def to_num(s):
    if s is None: return None
    s = s.strip().replace(",", "").replace("%", "")
    if s == "" or s in ("-",): return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([KkMmBbTt]?)$", s)
    if not m: return None
    v = float(m.group(1))
    mult = {"k":1e3,"m":1e6,"b":1e9,"t":1e12}.get(m.group(2).lower(), 1.0)
    return v * mult

def bias(event, actual, forecast):
    a, f = to_num(actual), to_num(forecast)
    if a is None or f is None:            # no surprise computable (e.g. FOMC Statement)
        return 0
    if abs(a - f) < 1e-12:                # came in exactly as expected
        return 0
    pol = -1 if any(k in event.lower() for k in INVERSE) else 1
    return (1 if a > f else -1) * pol

def main():
    src, out = sys.argv[1], sys.argv[2]
    rows = []
    with open(src, newline="", encoding="utf-8", errors="replace") as fh:
        for x in csv.DictReader(fh):
            dt = (x.get("DateTime") or "").strip()
            if not dt:
                continue
            try:
                d = datetime.fromisoformat(dt).astimezone(timezone.utc)
            except Exception:
                continue
            ev  = (x.get("Event") or "").strip().replace(",", " ")
            ccy = (x.get("Currency") or "").strip()
            act = (x.get("Actual") or "").strip()
            fc  = (x.get("Forecast") or "").strip()
            if not ccy or not ev:
                continue
            rows.append((d, ccy, ev, act, fc, bias(ev, act, fc)))
    rows.sort(key=lambda t: t[0])
    seen, outrows = set(), []
    for d, ccy, ev, act, fc, b in rows:
        k = (d, ccy, ev)
        if k in seen:
            continue
        seen.add(k)
        outrows.append((d.strftime("%Y.%m.%d %H:%M"), ccy, ev, act, fc, str(b)))
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["datetime_utc", "ccy", "event", "actual", "forecast", "ccy_bias"])
        w.writerows(outrows)
    yrs = Counter(r[0][:4] for r in outrows)
    print(f"wrote {len(outrows)} events -> {out}")
    print("per year:", dict(sorted(yrs.items())))
    print("sample:", outrows[len(outrows)//2] if outrows else "none")

if __name__ == "__main__":
    main()
