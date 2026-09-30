#!/usr/bin/env python3
r"""dump_m1_dk.py - coach item 19 (2026-09-30): M1 history for the .dk symbols the QDM store does not hold (XAUUSD,
USDJPY), from the trader's ALREADY-RUNNING local terminal. Read-only attach, same guard as dump_h4_dk.py: aborts if no
terminal is running or if the process count grows - it never launches or closes MT5.
    python.exe pipeline\dump_m1_dk.py --out <dir>
Writes <dir>/<ROOT>-M1-No Session.csv in the QDM fleet format (Date,Time,Open,High,Low,Close,Volume; the terminal's
own bar clock, UTC for the .dk imports), so pipeline/build_m5.py builds M5 from it unchanged.
"""
import argparse, csv, os, subprocess, sys
from datetime import datetime, timedelta

TERM = r"C:\Program Files\OANDA MetaTrader 5\terminal64.exe"
SYMS = {"XAUUSD.dk": "XAUUSD", "USDJPY.dk": "USDJPY"}
FROM, TO = datetime(2019, 12, 1), datetime.utcnow() + timedelta(days=2)


def interactive_terminals() -> int:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if "terminal64.exe" in l.lower())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    before = interactive_terminals()
    if before == 0: sys.exit("no terminal64 running - open MT5 first (this script never launches one)")
    import MetaTrader5 as m
    if not m.initialize(path=TERM): sys.exit(f"initialize failed: {m.last_error()}")
    if interactive_terminals() > before:
        m.shutdown(); sys.exit("a NEW terminal was launched - aborting (read-only attach only)")
    os.makedirs(a.out, exist_ok=True)
    for s, root in SYMS.items():
        m.symbol_select(s, True)
        p = os.path.join(a.out, f"{root}-M1-No Session.csv"); n = 0; first = last = None
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["Date", "Time", "Open", "High", "Low", "Close", "Volume"])
            t0 = FROM
            while t0 < TO:
                t1 = min(TO, t0 + timedelta(days=92))
                r = m.copy_rates_range(s, m.TIMEFRAME_M1, t0, t1)
                for b in (r if r is not None else []):
                    d = datetime.utcfromtimestamp(int(b["time"]))
                    if first is None: first = d
                    last = d; n += 1
                    w.writerow([d.strftime("%Y%m%d"), d.strftime("%H:%M:%S"), b["open"], b["high"], b["low"], b["close"], int(b["tick_volume"])])
                t0 = t1
        print(f"{s:12} {n:>9} M1 bars  {first} -> {last}  ({m.last_error()})")
    m.shutdown()


if __name__ == "__main__": main()
