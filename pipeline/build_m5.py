#!/usr/bin/env python3
"""build_m5.py - M5 bars from the QDM M1 exports (data/qdm_csv/fleet, UTC, bid) -> data/study/m5/<SYM>.dk.csv
(time,open,high,low,close; same clock as data/study/h4). Prices are the M1 file's own (bid); studies correct the
small constant offset to the .dk store per trade."""
import csv, os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = {"EURUSD.dk": "EURUSD", "GBPUSD.dk": "GBPUSD", "USOIL.dk": "LIGHTCMDUSD", "US500.dk": "USA500IDXUSD",
       "US100.dk": "USATECHIDXUSD", "XAUUSD.dk": "XAUUSD", "USDJPY.dk": "USDJPY"}
for sym, q in SRC.items():
    p = os.path.join(ROOT, "data", "qdm_csv", "fleet", f"{q}-M1-No Session.csv")
    if not os.path.exists(p): print(f"{sym}: no M1 file ({q}), skipped"); continue
    out = os.path.join(ROOT, "data", "study", "m5", f"{sym}.csv"); n = 0
    with open(p) as fh, open(out, "w", newline="") as fo:
        rd = csv.reader(fh); next(rd); w = csv.writer(fo); w.writerow(["time", "open", "high", "low", "close"])
        cur = None
        for d, t, o, h, l, c, *_ in rd:
            key = f"{d[:4]}.{d[4:6]}.{d[6:]} {t[:2]}:{int(t[3:5]) // 5 * 5:02d}"
            if cur and cur[0] == key:
                cur[2] = max(cur[2], float(h)); cur[3] = min(cur[3], float(l)); cur[4] = c
            else:
                if cur: w.writerow(cur); n += 1
                cur = [key, o, float(h), float(l), c]
        if cur: w.writerow(cur); n += 1
    print(f"{sym}: {n} M5 bars -> {out}")
