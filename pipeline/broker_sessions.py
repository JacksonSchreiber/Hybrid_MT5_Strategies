#!/usr/bin/env python3
"""broker_sessions.py - does the broker quote this symbol at weekends? Counts M1 bars per weekday (broker clock) over
the last N days, straight from the running terminal. Run ON the box: python broker_sessions.py [--days 28] [SYMBOLS...]"""
import argparse, json
from datetime import datetime, timedelta, timezone
import MetaTrader5 as m
ap = argparse.ArgumentParser(); ap.add_argument("symbols", nargs="*"); ap.add_argument("--days", type=int, default=28)
ap.add_argument("--path", default=r"C:\Program Files\OANDA MetaTrader 5\terminal64.exe"); ap.add_argument("--offset-h", type=int, default=3)
a = ap.parse_args()
if not m.initialize(path=a.path): raise SystemExit(f"initialize failed: {m.last_error()}")
syms = a.symbols or [s.name for s in m.symbols_get() if s.visible]
now = datetime.now(timezone.utc) + timedelta(hours=a.offset_h); frm = now - timedelta(days=a.days)
out = {}
for s in syms:
    m.symbol_select(s, True)
    r = m.copy_rates_range(s, m.TIMEFRAME_M1, frm, now)
    cnt = {d: 0 for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}
    for b in (r if r is not None else []):
        cnt[datetime.fromtimestamp(int(b["time"]), timezone.utc).strftime("%a")] += 1
    out[s] = {"bars": 0 if r is None else len(r), "by_weekday": cnt, "weekend_bars": cnt["Sat"] + cnt["Sun"],
              "quotes_weekends": (cnt["Sat"] + cnt["Sun"]) > 0}
    print(f"{s:12s} bars={out[s]['bars']:6d} " + " ".join(f"{d}={cnt[d]:5d}" for d in cnt) + f"  weekend={out[s]['weekend_bars']}")
print(json.dumps(out, indent=1))
