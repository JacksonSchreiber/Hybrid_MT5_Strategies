#!/usr/bin/env python3
r"""dump_h4_dk.py - park the .dk H4 history studies need, so they stop being limited to whichever windows an old
backtest happened to export (data/backtests/emarev_inv covers US100 2021-22 and USOIL 2018-19 only, and has no US500).

Windows-side, READ-ONLY: it attaches to the ALREADY-RUNNING local terminal and aborts if none is running or if the
process count grows - it never launches or closes the trader's MT5.
    python.exe pipeline\dump_h4_dk.py
Writes data/study/<tf>/<SYMBOL>.csv  (time,open,high,low,close, oldest first, the terminal's own bar clock).
"""
import argparse, csv, os, subprocess, sys
from datetime import datetime, timedelta

TERM = r"C:\Program Files\OANDA MetaTrader 5\terminal64.exe"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study")
SYMS = ["EURUSD.dk", "GBPUSD.dk", "USDJPY.dk", "XAUUSD.dk", "US100.dk", "US500.dk", "USOIL.dk", "BTCUSD.dk"]
FROM, TO = datetime(2012, 1, 1), datetime.utcnow() + timedelta(days=2)


def interactive_terminals() -> int:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if "terminal64.exe" in l.lower())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=OUT)     # the repo lives on the WSL side; when this
    ap.add_argument("--tf", default="h4", choices=["h4", "d1"])               # runs from a Windows temp copy, point --out at one too
    a = ap.parse_args(); out_dir = os.path.join(a.out, a.tf)
    before = interactive_terminals()
    if before == 0: sys.exit("no terminal64 running - open MT5 first (this script never launches one)")
    import MetaTrader5 as m
    if not m.initialize(path=TERM): sys.exit(f"initialize failed: {m.last_error()}")
    if interactive_terminals() > before:
        m.shutdown(); sys.exit("a NEW terminal was launched - aborting (read-only attach only)")
    os.makedirs(out_dir, exist_ok=True)
    for s in SYMS:
        m.symbol_select(s, True)
        r = m.copy_rates_range(s, m.TIMEFRAME_H4 if a.tf == "h4" else m.TIMEFRAME_D1, FROM, TO)
        if r is None or len(r) == 0:
            print(f"{s:12} no H4 history ({m.last_error()})"); continue
        p = os.path.join(out_dir, f"{s}.csv")
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["time", "open", "high", "low", "close"])
            for b in r:
                w.writerow([datetime.utcfromtimestamp(int(b["time"])).strftime("%Y.%m.%d %H:%M"),
                            f"{b['open']:.5f}", f"{b['high']:.5f}", f"{b['low']:.5f}", f"{b['close']:.5f}"])
        print(f"{s:12} {len(r):>7} bars  {datetime.utcfromtimestamp(int(r[0]['time'])):%Y-%m-%d} -> "
              f"{datetime.utcfromtimestamp(int(r[-1]['time'])):%Y-%m-%d}")
    m.shutdown()


if __name__ == "__main__": main()
