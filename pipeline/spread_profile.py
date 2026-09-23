#!/usr/bin/env python3
"""spread_profile.py - what the spread costs, in R, at each H4 bar close (trader/coach 2026-09-23).

Runs ON THE BOX (it talks to the local feed daemon, which owns the MT5 connection):
    scp pipeline/spread_profile.py data/study/universe_cost_filter.csv hybridops@10.77.0.1:C:/ProgramData/hybrid/tmp/
    ssh hybridops@10.77.0.1 "& 'C:\\Program Files\\Python312\\python.exe' C:\\ProgramData\\hybrid\\tmp\\spread_profile.py"

The spread is paid the instant a trade opens, so it only matters at the moments a signal can be published: the six H4
closes (01/05/09/13/17/21 UTC - the broker clock is UTC+3, bars open 00/04/08/12/16/20 there). Cost in R uses one
`est_stop` per symbol from data/study/universe_cost_filter.csv, so the numbers are indicative: a signal whose real stop
is wider than est_stop pays proportionally less.
"""
import argparse, csv, os, sys, time, statistics as st

sys.path.insert(0, r"C:\ProgramData\hybrid\live")
from live import mt5feed                                    # noqa: E402

BARS = [1, 5, 9, 13, 17, 21]                                # H4 closes, UTC
SRV_OFFSET_H = 3                                            # broker clock = UTC+3 (EEST); feed epochs are server time


def est_stops(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try: out[r["symbol"]] = float(r["est_stop"])
            except (KeyError, ValueError): pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8081")
    ap.add_argument("--stops", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe_cost_filter.csv"))
    ap.add_argument("--bars", type=int, default=20000, help="M1 bars per symbol (20000 ~= 20 trading days)")
    ap.add_argument("--hourly", default="", help="comma-separated symbols to also print a 24-hour curve for")
    ap.add_argument("symbols", nargs="*", help="default: every symbol in the stops csv")
    a = ap.parse_args()

    mt5feed.configure_url(a.url)
    stops = est_stops(a.stops)
    syms = a.symbols or sorted(stops)
    hourly = [s for s in a.hourly.split(",") if s]
    data = {}

    print(f"median spread cost in R in the first 5 min of each H4 bar close (UTC), n = M1 samples")
    print(f"{'symbol':12} " + "  ".join(f"{h:02d}:00 (n)" for h in BARS))
    for s in syms:
        d = mt5feed.spreads(s, a.bars)
        if not d or not d.get("rows"):
            print(f"{s:12} NO DATA"); continue
        data[s] = d
        pt, stop = d["point"], stops.get(s)
        by = {h: [] for h in BARS}
        for t, sp in d["rows"]:
            u = time.gmtime(t - SRV_OFFSET_H * 3600)
            if u.tm_min < 5 and u.tm_hour in by: by[u.tm_hour].append(sp * pt)
        cells = []
        for h in BARS:
            v = by[h]
            cells.append(f"{st.median(v)/stop:5.3f} ({len(v):3d})" if (v and stop) else f"{'  -  ':>5} ({len(v):3d})")
        print(f"{s:12} " + "  ".join(cells))

    for s in hourly:                                        # the rollover window is only visible hour by hour
        d = data.get(s) or mt5feed.spreads(s, a.bars)
        if not d or not d.get("rows"): continue
        pt, stop = d["point"], stops.get(s)
        by = {h: [] for h in range(24)}
        for t, sp in d["rows"]: by[time.gmtime(t - SRV_OFFSET_H * 3600).tm_hour].append(sp * pt)
        span = (max(r[0] for r in d["rows"]) - min(r[0] for r in d["rows"])) / 86400.0
        print(f"\n{s}  ({len(d['rows'])} M1 bars over {span:.1f} days)")
        print("  hour " + " ".join(f"{h:>5d}" for h in range(24)))
        print("  bars " + " ".join(f"{len(by[h]):>5d}" for h in range(24)))
        print("  R    " + " ".join((f"{st.median(by[h])/stop:5.2f}" if (by[h] and stop) else "    -") for h in range(24)))


if __name__ == "__main__":
    main()
