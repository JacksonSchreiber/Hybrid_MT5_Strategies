#!/usr/bin/env python3
"""oos_h4_from_ticks.py - the .dk H4 bars a study needs, rebuilt from the SAME tick CSV the MT5 import reads (OOS symbol test,
2026-10-03). No MT5 needed.

Why this reproduces the .dk H4: the importer (mql5/include/Hybrid/TickImport.mqh) builds the custom symbol's M1 bars from the
tick CSV's BID column (open = first bid of the minute, high / low = max / min bid, close = last bid), UTC, and MT5 aggregates those
M1 bars into H4 on the 00/04/../20 UTC grid. This script does the same aggregation straight from the ticks, so its output should be
bit-identical to what pipeline/dump_h4_dk.py copies out of the terminal (time,open,high,low,close, oldest first, %.5f).
Validated on EURUSD (data/mt5_ready/EURUSD.csv, the CSV EURUSD.dk was imported from) against data/study/h4/EURUSD.dk.csv:
see `--check`. (The QDM M1 CSVs in data/qdm_csv/fleet are NOT the same price series: their H4 aggregate matches the .dk grid bar
for bar but sits ~0.5-1 spread away in price, which is why the engine re-anchors each trade with a per-trade offset.)

    python3 pipeline/oos_h4_from_ticks.py --csv data/mt5_ready/AUDUSD.csv --sym AUDUSD.dk            # -> data/study/h4/AUDUSD.dk.csv
    python3 pipeline/oos_h4_from_ticks.py --csv data/mt5_ready/EURUSD.csv --sym EURUSD.dk --check    # compare, write nothing
Tick CSV format (QDM exportToMT5 timeframe=TICK): no header, 'yyyy.MM.dd HH:mm:ss.mmm,bid,ask', UTC.
Refuses to overwrite an existing H4 file (the original seven's files are study inputs); pass --force for a new-symbol re-run.
"""
from __future__ import annotations
import argparse, os, sys, time
import numpy as np
import pyarrow as pa, pyarrow.csv as pacsv, pyarrow.compute as pc

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
H4DIR = os.path.join(ROOT, "data", "study", "h4")


def build(csv_path: str, digits: int | None = None):
    """-> (keys [4h buckets since epoch], open, high, low, close, ticks, ticks with more decimals than `digits`) from the bid column,
    streamed. digits: round every bid to the symbol's digits first (only matters if the CSV carries more decimals than the symbol)."""
    ro = pacsv.ReadOptions(column_names=["t", "bid", "ask"], block_size=64 << 20)
    co = pacsv.ConvertOptions(column_types={"t": pa.string(), "bid": pa.float64(), "ask": pa.float64()}, include_columns=["t", "bid"])
    rd = pacsv.open_csv(csv_path, read_options=ro, convert_options=co)
    K, O, H, L, C = [], [], [], [], []
    n = 0; prev_last = None; extra = 0
    for batch in rd:
        if batch.num_rows == 0: continue
        hrs = pc.strptime(pc.utf8_slice_codeunits(batch.column("t"), 0, 13), format="%Y.%m.%d %H", unit="s")
        key = (hrs.cast(pa.int64()).to_numpy(zero_copy_only=False) // 3600) // 4
        bid = batch.column("bid").to_numpy(zero_copy_only=False)
        if digits is not None:
            r = np.round(bid, digits); extra += int(np.count_nonzero(np.abs(r - bid) > 10.0 ** -(digits + 3))); bid = r
        if prev_last is not None and key[0] < prev_last: sys.exit(f"ticks out of order at row {n}")
        if np.any(np.diff(key) < 0): sys.exit(f"ticks out of order inside the block starting at row {n}")
        cut = np.flatnonzero(np.diff(key)) + 1
        st = np.concatenate(([0], cut)); en = np.concatenate((cut, [len(key)])) - 1
        K.append(key[st]); O.append(bid[st]); C.append(bid[en])
        H.append(np.maximum.reduceat(bid, st)); L.append(np.minimum.reduceat(bid, st))
        n += len(key); prev_last = key[-1]
    k, o, h, l, c = (np.concatenate(a) for a in (K, O, H, L, C))
    # a bucket split across two blocks: merge consecutive equal keys
    cut = np.flatnonzero(np.diff(k)) + 1
    st = np.concatenate(([0], cut)); en = np.concatenate((cut, [len(k)])) - 1
    return k[st], o[st], np.maximum.reduceat(h, st), np.minimum.reduceat(l, st), c[en], n, extra


def fmt_time(keys):
    return np.datetime_as_string((keys * 4 * 3600).astype("datetime64[s]"), unit="m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True); ap.add_argument("--sym", required=True, help="e.g. AUDUSD.dk")
    ap.add_argument("--out", default=H4DIR); ap.add_argument("--check", action="store_true", help="compare with the existing file only")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--digits", type=int, default=None, help="the symbol's digits as the importer infers them; bids are rounded to it and any tick that\n"
                    "needed rounding is counted (non-zero = the H4 equivalence is unverified for this symbol)")
    a = ap.parse_args()
    t0 = time.time()
    k, o, h, l, c, n, extra = build(a.csv, a.digits)
    times = [s.replace("-", ".").replace("T", " ") for s in fmt_time(k)]
    print(f"{a.sym}: {n:,} ticks -> {len(k):,} H4 bars {times[0]} .. {times[-1]} ({time.time() - t0:.0f}s)"
          + ("" if a.digits is None else f"; digits {a.digits}, ticks with more decimals: {extra:,}" + ("  ** UNVERIFIED **" if extra else "")))
    dst = os.path.join(a.out, f"{a.sym}.csv")
    if a.check:
        import csv
        ref = {r["time"]: (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in csv.DictReader(open(dst))}
        mine = {t: tuple(round(float(x), 5) for x in v) for t, v in zip(times, zip(o, h, l, c))}
        common = sorted(set(ref) & set(mine))
        only_r = sorted(set(ref) - set(mine)); only_m = sorted(set(mine) - set(ref))
        diff = [t for t in common if any(abs(x - y) > 1e-9 for x, y in zip(ref[t], mine[t]))]
        print(f"CHECK vs {dst}: common {len(common):,}  only-in-file {len(only_r)} {only_r[:3]}  only-rebuilt {len(only_m)} {only_m[:3]}  "
              f"price mismatches {len(diff)} {[(t, ref[t], mine[t]) for t in diff[:3]]}")
        return
    if os.path.exists(dst) and not a.force: sys.exit(f"{dst} exists - refusing to overwrite (use --force for a new-symbol re-run)")
    os.makedirs(a.out, exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "w", newline="") as f:
        f.write("time,open,high,low,close\n")
        for t, a1, b1, c1, d1 in zip(times, o, h, l, c): f.write(f"{t},{a1:.5f},{b1:.5f},{c1:.5f},{d1:.5f}\n")
    os.replace(tmp, dst)
    print(f"wrote {dst}")


if __name__ == "__main__": main()
