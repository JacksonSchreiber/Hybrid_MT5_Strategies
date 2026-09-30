#!/usr/bin/env python3
"""d1_gate_shadow.py - coach 2026-09-30 task 2: shadow outcomes of the live/demo rows the D1-extension gate rejected.

INFORMATION ONLY - these rows stay rejected in the record.

Population: every row with decision == "rejected" and reject_reason starting "d1_ext_gate" in
data/study/live_journal/*.csv (current live account) and data/study/demo_journal/*.csv (demo, to 29 Sep), 24-30 Sep 2026.
Bars: data/study/h4/<SYM>.sim.csv (400 H4 bars back from 30 Sep, fetched for 13 symbols). Rows whose symbol has no
bar file are listed, not replayed.

CLOCK: the journals' signal_time is the BROKER clock (OANDA EET/EEST, UTC+3 in late September) and the .sim bar files
carry the broker's epoch written as if UTC - both are broker clock, so signal_time is matched to the bar keyed at the
same time directly. Checked per row below: the row's entry against the close of that bar (a market signal enters at
the signal bar's close; the difference should be a spread), and against the neighbouring bars' closes.

Replay: exit_param_study.replay (via coach_items_common.replay_ex) with the row's entry/sl/tp1/tp2, starting the bar
after the signal bar, under the 50% bank doctrine (bank 50% at min(+1R, TP1), stop to entry there, runner to TP2).
A trade still open at the last bar is marked at that bar's close and labelled OPEN (the last bar, 30 Sep 20:00 broker,
may itself still have been forming when the file was written). R is RAW: data/study/spread_drag.json has no entry for
the exotic/cross symbols here, and the flat 0.007R default would badly understate e.g. USDZAR's spread.
"""
from __future__ import annotations
import csv, glob, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import replay_ex                             # noqa: E402
from pipeline.exit_param_study import replay                                  # noqa: E402
from pipeline.trendcont_step13_study import load                              # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main():
    print(__doc__)
    rows = []
    for src in ("live_journal", "demo_journal"):
        for p in sorted(glob.glob(os.path.join(ROOT, "data", "study", src, "*.csv"))):
            if ".actions" in p or ".delays" in p: continue
            for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
                if r.get("decision") == "rejected" and (r.get("reject_reason") or "").startswith("d1_ext_gate"):
                    if not ("2026.09.24" <= r["signal_time"][:10] <= "2026.09.30"): continue
                    rows.append((src.split("_")[0], r))
    print(f"d1_ext_gate rejections 24-30 Sep: {len(rows)} ({sum(1 for s, _ in rows if s == 'live')} live, "
          f"{sum(1 for s, _ in rows if s == 'demo')} demo)\n")
    hdr = (f"{'src':4} {'symbol':11} {'id':>3} {'signal (broker)':16} {'strat':9} {'dir':4} {'D1 ext':>7} "
           f"{'entry':>10} {'bar close':>10} {'prev/next close':>21} {'bars':>4} {'R (50%)':>8} status")
    print(hdr); print("-" * len(hdr))
    tot, n_closed, n_open, nob = 0.0, 0, 0, []
    for src, r in rows:
        sym = r["symbol"]; bars = load(sym, "h4")
        ext = r["reject_reason"].split("extension")[1].split("ATR")[0].strip() if "extension" in r["reject_reason"] else "?"
        if not bars: nob.append((src, r, ext)); continue
        idx = {b[0][:16]: k for k, b in enumerate(bars)}
        i = idx.get(r["signal_time"][:16])
        if i is None: nob.append((src, r, ext)); continue
        e, s = float(r["entry"]), float(r["sl"])
        tp1 = float(r["tp1"]) if r.get("tp1") else None
        tp2 = float(r["tp2"]) if r.get("tp2") else None
        up = r["direction"].upper().startswith("B")
        res = replay_ex(bars, i + 1, up, e, s, tp1, tp2, "doc", 0.5, 0.0)
        assert abs(res[0] - replay(bars, i + 1, up, e, s, tp1, tp2, "doc", 0.5, 0.0)) < 1e-12
        R, j, closed = res
        nb = len(bars) - (i + 1)
        pn = f"{bars[i - 1][4]:.5g}/{bars[i + 1][4]:.5g}" if i + 1 < len(bars) else f"{bars[i - 1][4]:.5g}/-"
        status = f"closed {bars[j][0][:16]}" if closed else f"OPEN, marked at {bars[j][0][:16]} close"
        print(f"{src:4} {sym:11} {r['signal_id']:>3} {r['signal_time'][:16]:16} {r['strategy']:9} {r['direction']:4} "
              f"{ext:>7} {e:>10.5g} {bars[i][4]:>10.5g} {pn:>21} {nb:>4} {R:>+8.3f} {status}")
        tot += R; n_closed += closed; n_open += not closed
    print(f"\nreplayed {n_closed + n_open} of {len(rows)}: total {tot:+.3f}R raw ({n_closed} closed, {n_open} still open, "
          f"open ones marked to market)")
    print(f"\nno bars ({len(nob)} rows - symbol not among the fetched .sim files):")
    for src, r, ext in nob:
        print(f"  {src:4} {r['symbol']:11} #{r['signal_id']:>2} {r['signal_time'][:16]} {r['strategy']} {r['direction']:4} D1 ext {ext}")
    print("\nThe 13 fetched .sim bar files overlap the rejected symbols only on CADCHF and USDZAR; a fair shadow read of the"
          "\ngate needs bars for the other symbols (the 30 Sep rows would still be too young to score).")


if __name__ == "__main__": main()
