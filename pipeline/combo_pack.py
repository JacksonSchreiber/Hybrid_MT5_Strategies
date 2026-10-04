#!/usr/bin/env python3
"""combo_pack.py - trader 2026-10-03: the combo builder's results (trade-major, 15-byte records) -> the webapp file.

combos.bin: COMBO-major - combo c is one contiguous block of n_trades x 9-byte records at offset c * n_trades * 9, in the trade order
of meta.json (the server hands the browser one block per dial setting):
  R    int16  full-position R x1000 (costs in)           xb   uint16 exit time, H4 bars since the entry bar x10
  kind uint8  0 stop -1R, 1 stop at entry, 2 raised stop (incl. the bloc lock), 3 TP2, 4 cut B, 5 window end, 6 bloc 24h cut, 255 no data
  ev   uint8 x4  H4 bar (rounded, capped 254; 255 none) of the staged add, the bank, the pyramid, the I add
combos_meta.json: dials, the grid layout, the record format, the build id, and per trade its latest exit across every combo
(the base page data builds each price path that far).
    python3 pipeline/combo_pack.py [data/combos/<run>]      (default: the newest run)
"""
from __future__ import annotations
import glob, json, os, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import combo_build as CB              # noqa: E402
from pipeline.progress import Bar                   # noqa: E402

PK = np.dtype([("R", "<i2"), ("xb", "<u2"), ("kind", "u1"), ("ev", "u1", (4,))])      # 9 bytes


def main(run=None):
    run = run or sorted(d for d in glob.glob(os.path.join(CB.OUTDIR, "2*")) if os.path.exists(os.path.join(d, "meta.json")))[-1]
    meta = json.load(open(os.path.join(run, "meta.json"))); nt, nc = len(meta["trades"]), len(meta["combos"])
    mm = np.memmap(os.path.join(run, "results.u8"), dtype=CB.REC, mode="r", shape=(nt, nc))
    out = np.memmap(os.path.join(run, "combos.bin"), dtype=PK, mode="w+", shape=(nc, nt))
    # trader 2026-10-04 (the disk-saturation freeze): CP_STEP combos per block (1024 -> each trade row is read in one ~15 KB run, so
    # the trade-major file is read once even when it does not fit in RAM), CP_MBPS caps the read rate (0 = no cap).
    step = int(os.environ.get("CP_STEP", "256")); mbps = float(os.environ.get("CP_MBPS", "0"))
    maxxb = np.zeros(nt, dtype=np.int64); bar = Bar(nc, "pack", "combos"); t0 = time.time()
    for c0 in range(0, nc, step):
        tb = time.time()
        blk = np.array(mm[:, c0:c0 + step]).T                                  # (combos, trades)
        o = np.empty(blk.shape, dtype=PK)
        o["R"] = np.clip(np.round(blk["R"].astype(np.float64) * 1000), -32767, 32767).astype(np.int16)
        o["xb"] = blk["xb"]; o["kind"] = blk["kind"]
        ev = blk["ev"].astype(np.int64)
        o["ev"] = np.where(ev == CB.NONE16, 255, np.minimum(np.round(ev / 10.0), 254)).astype(np.uint8)
        out[c0:c0 + blk.shape[0]] = o
        ok = blk["kind"] != 255
        maxxb = np.maximum(maxxb, np.where(ok, blk["xb"], 0).max(axis=0))
        bar.update(blk.shape[0])
        if mbps > 0: out.flush(); time.sleep(max(0.0, blk.nbytes / (mbps * 1e6) - (time.time() - tb)))
    out.flush(); bar.done()
    build = os.path.basename(run.rstrip("/"))
    cm = {"build": build, "n_trades": nt, "n_combos": nc, "record": "R int16 x1000 | xb uint16 H4x10 | kind uint8 | ev uint8 x4 (add, bank, pyr, I)",
          "rec_bytes": PK.itemsize, "dials": meta["dials"], "layout": "bloc off first: for stage (STAGE order): gateA in (0) if stage is off else (0, 1); then bank, pyr, raise, cutB, addI (mixed radix, last fastest); "
                    "then bloc on, the same without the raise dimension (the bloc lock replaces it)",
          "trades": meta["trades"], "max_xb": maxxb.tolist(), "mode": meta["mode"]}
    json.dump(cm, open(os.path.join(run, "combos_meta.json"), "w"))
    print(f"{nc:,} combos x {nt:,} trades -> {os.path.join(run, 'combos.bin')} ({os.path.getsize(os.path.join(run, 'combos.bin')) / 1e9:.2f} GB) "
          f"in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__": main(sys.argv[1] if len(sys.argv) > 1 else None)
