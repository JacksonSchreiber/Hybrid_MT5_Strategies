#!/usr/bin/env python3
"""spread_profile.py - what the spread costs, in R, hour by hour and at each H4 bar close (coach 2026-09-23).

Runs ON THE BOX (it talks to the local feed daemon, which owns the MT5 connection):
    scp pipeline/spread_profile.py data/study/universe_cost_filter.csv hybridops@10.77.0.1:C:/ProgramData/hybrid/tmp/
    ssh hybridops@10.77.0.1 "& 'C:\\Program Files\\Python312\\python.exe' C:\\ProgramData\\hybrid\\tmp\\spread_profile.py --csv C:\\ProgramData\\hybrid\\tmp\\spread_profile.csv"
    scp hybridops@10.77.0.1:C:/ProgramData/hybrid/tmp/spread_profile.csv data/study/

The spread is paid the instant a trade opens, so what matters is its size at the moments a signal can be published:
the six H4 closes (01/05/09/13/17/21 UTC - the broker clock is UTC+3, so bars open 00/04/08/12/16/20 there). The CSV
carries all 24 hours because the study-template spread discount is a single daily median per symbol and the rollover
hour is ~10x that even for the majors (coach ruling 2026-09-23: the next study that leans on the discount gets a
per-bar version).

Cost in R uses one `est_stop` per symbol from data/study/universe_cost_filter.csv, so the numbers are indicative: a
signal whose real stop is wider than est_stop pays proportionally less (EURNOK #1's stop was 1.9x its est_stop).
"""
import argparse, csv, os, sys, time, statistics as st

sys.path.insert(0, r"C:\ProgramData\hybrid\live")
from live import mt5feed                                    # noqa: E402

BARS = [1, 5, 9, 13, 17, 21]                                # H4 closes, UTC
SRV_OFFSET_H = 3                                            # broker clock = UTC+3 (EEST); feed epochs are server time
FIRST_MIN = 5                                               # a signal publishes within minutes of the close


def est_stops(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try: out[r["symbol"]] = float(r["est_stop"])
            except (KeyError, ValueError): pass
    return out


def profile(sym, rows, point, stop):
    """-> {hour: (n_hour, med_hour, n_first5, med_first5)} in price terms."""
    hour, first5 = {h: [] for h in range(24)}, {h: [] for h in range(24)}
    for t, sp in rows:
        u = time.gmtime(t - SRV_OFFSET_H * 3600)
        hour[u.tm_hour].append(sp * point)
        if u.tm_min < FIRST_MIN: first5[u.tm_hour].append(sp * point)
    return {h: (len(hour[h]), st.median(hour[h]) if hour[h] else None,
                len(first5[h]), st.median(first5[h]) if first5[h] else None) for h in range(24)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8081")
    ap.add_argument("--stops", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe_cost_filter.csv"))
    ap.add_argument("--bars", type=int, default=20000, help="M1 bars per symbol (20000 ~= 20 trading days)")
    ap.add_argument("--csv", default="", help="write the full 24-hour profile here")
    ap.add_argument("--hourly", default="", help="comma-separated symbols to also print a 24-hour curve for")
    ap.add_argument("symbols", nargs="*", help="default: every symbol in the stops csv")
    a = ap.parse_args()

    mt5feed.configure_url(a.url)
    stops = est_stops(a.stops)
    syms = a.symbols or sorted(stops)
    asof = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prof, span = {}, {}

    print(f"median spread cost in R in the first {FIRST_MIN} min of each H4 bar close (UTC), n = M1 samples")
    print(f"{'symbol':12} " + "  ".join(f"{h:02d}:00 (n)" for h in BARS))
    for s in syms:
        d = mt5feed.spreads(s, a.bars)
        if not d or not d.get("rows"):
            print(f"{s:12} NO DATA"); continue
        stop = stops.get(s)
        prof[s] = profile(s, d["rows"], d["point"], stop)
        span[s] = (max(r[0] for r in d["rows"]) - min(r[0] for r in d["rows"])) / 86400.0
        cells = []
        for h in BARS:
            n5, m5 = prof[s][h][2], prof[s][h][3]
            cells.append(f"{m5/stop:5.3f} ({n5:3d})" if (m5 is not None and stop) else f"{'  -  ':>5} ({n5:3d})")
        print(f"{s:12} " + "  ".join(cells))

    for s in [x for x in a.hourly.split(",") if x and x in prof]:  # the rollover window is only visible hour by hour
        stop = stops.get(s)
        print(f"\n{s}  ({span[s]:.1f} days)")
        print("  hour " + " ".join(f"{h:>5d}" for h in range(24)))
        print("  bars " + " ".join(f"{prof[s][h][0]:>5d}" for h in range(24)))
        print("  R    " + " ".join((f"{prof[s][h][1]/stop:5.2f}" if (prof[s][h][1] is not None and stop) else "    -") for h in range(24)))

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "hour_utc", "is_h4_close", "n_bars_hour", "spread_med_hour", "cost_r_hour",
                        "n_bars_first5", "spread_med_first5", "cost_r_first5", "est_stop", "sampled_days", "asof_utc"])
            for s in sorted(prof):
                stop = stops.get(s)
                for h in range(24):
                    n, mh, n5, m5 = prof[s][h]
                    w.writerow([s, h, int(h in BARS), n,
                                "" if mh is None else f"{mh:.6f}", "" if (mh is None or not stop) else f"{mh/stop:.4f}",
                                n5, "" if m5 is None else f"{m5:.6f}", "" if (m5 is None or not stop) else f"{m5/stop:.4f}",
                                "" if not stop else f"{stop:.6f}", f"{span[s]:.1f}", asof])
        print(f"\nwrote {a.csv}")


if __name__ == "__main__":
    main()
