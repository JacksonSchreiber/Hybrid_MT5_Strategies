"""frontend.py — the advisory service's display + real-chart validation.

Two things the spec's step 5 (front end) and step 3 (validate against graded
outcomes) need, on REAL data instead of a synthetic chart:

  --replay <AA_ALL_journal.csv> [--limit N]
      For each signal in an AA_ALL baseline (every setup taken at the detector's
      levels, with its realised R), render the H4 + D1 charts from the symbol's
      M1 data truncated at the signal bar (lookahead-safe), run the full
      Tier 0 → Tier 1 → Tier 2 decision, print a verdict card, and — since the
      baseline carries the realised R — tally the assistant's judgment:
        good SKIP = skipped a baseline loser · bad SKIP = skipped a winner
        good TAKE/ADJUST = acted on a winner · bad TAKE = acted on a loser
      This is the "check its SKIPs against the coach's graded outcomes" step.

Each Tier-1 call is a real subscription call (tens of seconds cold), so use
--limit for a quick sample; a full level is an overnight run (fits §8b).

Reuses the assistant pipeline + pipeline.export_d1_stats for M1→bar aggregation.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import sys
from datetime import datetime

from pipeline import tier0
from pipeline import export_d1_stats as d1s
from pipeline.assistant.config import (Config, load_config, setup_auth,
                                       MODEL_ALIAS, MODEL_API_ID)
from pipeline.assistant.transport import make_transport
from pipeline.assistant import app


def _session(hour: int) -> str:
    if 7 <= hour < 13:
        return "London"
    if 13 <= hour < 20:
        return "New York"
    if hour >= 20 or hour < 2:
        return "overnight"
    return "Asia"


# --- M1 → H4 aggregation (D1 reuses export_d1_stats) -------------------------
def _aggregate_h4(m1_path, asof: datetime, keep=140):
    asof_key = asof.strftime("%Y%m%d%H%M")
    buckets, order = {}, []
    with open(m1_path, "r", encoding="utf-8", errors="replace") as fh:
        fh.readline()
        for line in fh:
            p = line.split(",")
            if len(p) < 6:
                continue
            d, t = p[0].strip(), p[1].strip()
            if (d + t[:2] + t[3:5]) > asof_key:
                break
            try:
                o, h, l, c = float(p[2]), float(p[3]), float(p[4]), float(p[5])
            except ValueError:
                continue
            key = d + f"{(int(t[:2]) // 4) * 4:02d}"     # YYYYMMDDHH (H4 start)
            b = buckets.get(key)
            if b is None:
                buckets[key] = [o, h, l, c, 0.0]
                order.append(key)
            else:
                b[1] = max(b[1], h); b[2] = min(b[2], l); b[3] = c
    return [(k, *buckets[k]) for k in order[-keep:]]


def _render_b64(bars, ema_period=20, W=900, H=460):
    from PIL import Image, ImageDraw
    if len(bars) < 2:
        return None
    closes = [b[4] for b in bars]
    ema = d1s._ema(closes, ema_period)
    hi = max(b[2] for b in bars); lo = min(b[3] for b in bars); rng = (hi - lo) or 1e-9
    im = Image.new("RGB", (W, H), (13, 17, 23)); d = ImageDraw.Draw(im)
    n = len(bars); pad = 22; step = (W - 2 * pad) // n; cw = max(2, step - 2)
    def yv(p): return int(pad + (hi - p) / rng * (H - 2 * pad))
    for i, b in enumerate(bars):
        _k, o, h, l, c, _v = b
        x = pad + i * step + cw // 2
        col = (63, 185, 80) if c >= o else (248, 81, 73)
        d.line([(x, yv(h)), (x, yv(l))], fill=col)
        ty, by = yv(max(o, c)), yv(min(o, c))
        d.rectangle([x - cw // 2, ty, x + cw // 2, max(ty + 1, by)],
                    fill=col if c >= o else (13, 17, 23), outline=col)
    pts = [(pad + i * step + cw // 2, yv(ema[i])) for i in range(n)]
    if len(pts) > 1:
        d.line(pts, fill=(210, 153, 34), width=2)
    buf = io.BytesIO(); im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _f(row, *keys):
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    return None


def _meta_and_signal(row):
    strat = (row.get("strategy") or "?").strip()
    direction = 1 if (row.get("direction") or "").strip().upper() == "BUY" else -1
    st = (row.get("signal_time") or "").strip()
    sig_dt = None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            sig_dt = datetime.strptime(st, fmt); break
        except ValueError:
            pass
    entry = _f(row, "orig_entry", "entry"); sl = _f(row, "orig_sl", "sl")
    tp1 = _f(row, "orig_tp1", "tp1", "orig_tp", "tp"); tp2 = _f(row, "orig_tp2", "tp2")
    risk = abs(entry - sl) if (entry and sl and entry != sl) else None
    rr = lambda x: round(abs(x - entry) / risk, 1) if (x and risk) else None
    meta = {
        "strategy": strat, "direction": direction,
        "session": _session(sig_dt.hour) if sig_dt else "?",
        "day_of_week": sig_dt.strftime("%A") if sig_dt else "?",
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "sl_r": 1.0, "tp1_r": rr(tp1), "tp2_r": rr(tp2),
        "cluster_exposure": "none reported",
    }
    sym = (row.get("symbol") or "").strip()
    signal = tier0.Signal(symbol=sym, direction=direction,
                          entry_time=sig_dt or datetime(2000, 1, 1), strategy=strat)
    return meta, signal, sig_dt, sym


def replay(journal_path, limit, cfg: Config, transport):
    with open(journal_path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = [r for r in csv.DictReader(fh)]
    graded = [r for r in rows if (r.get("r_multiple") or "").strip() not in ("", None)]
    if limit:
        graded = graded[:limit]
    print(f"replay: {len(graded)} graded signals from {journal_path} "
          f"(transport={cfg.transport})\n")

    tally = {"good_skip": 0, "bad_skip": 0, "good_act": 0, "bad_act": 0, "t0": 0}
    for r in graded:
        meta, signal, sig_dt, sym = _meta_and_signal(r)
        if sig_dt is None:
            continue
        r_real = float(r["r_multiple"])
        m1 = d1s._find_m1(sym)
        if not m1:
            print(f"  #{r.get('signal_id')} {sym}: no M1 for render — skipped")
            continue
        h4 = _render_b64(_aggregate_h4(m1, sig_dt))
        d1 = _render_b64(d1s._aggregate_d1(m1, sig_dt)[-140:])
        imgs = [x for x in (d1, h4) if x]
        audit = {"signal_id": r.get("signal_id"), "symbol": sym,
                 "signal_time": r.get("signal_time"), "baseline_r": r_real}
        out = app.evaluate(signal=signal, audit=audit, images_b64=imgs,
                           meta=meta, cfg=cfg, transport=transport)
        v = out.get("verdict"); tier = out.get("tier")
        # score vs the graded baseline R
        acted = v in ("TAKE", "ADJUST")
        if tier == 0:
            tally["t0"] += 1
        if v == "SKIP":
            tally["good_skip" if r_real < 0 else "bad_skip"] += 1
        elif acted:
            tally["good_act" if r_real > 0 else "bad_act"] += 1
        mark = ("✓" if (v == "SKIP" and r_real < 0) or (acted and r_real > 0)
                else "✗")
        print(f"  {mark} #{r.get('signal_id'):>2} {meta['strategy']:<8} "
              f"{'BUY ' if meta['direction']>0 else 'SELL'}  "
              f"verdict={v}/{out.get('confidence')} (tier {tier})  "
              f"baseline={r_real:+.2f}R")
        print(f"       why: {str(out.get('why'))[:150]}")

    print("\n=== validation tally (vs baseline R) ===")
    print(f"  good SKIP (skipped a loser):  {tally['good_skip']}")
    print(f"  bad  SKIP (skipped a winner): {tally['bad_skip']}")
    print(f"  good ACT  (took a winner):    {tally['good_act']}")
    print(f"  bad  ACT  (took a loser):     {tally['bad_act']}")
    n = sum(v for k, v in tally.items() if k != "t0")
    good = tally["good_skip"] + tally["good_act"]
    if n:
        print(f"  agreement with graded outcome: {good}/{n} = {100*good/n:.0f}%")
    print(f"\nJSONL log: {cfg.log_path}")


def main():
    ap = argparse.ArgumentParser(description="Advisory front-end / real-chart validation.")
    ap.add_argument("--replay", required=True, help="AA_ALL baseline journal CSV")
    ap.add_argument("--limit", type=int, default=0, help="cap signals (each is a real call)")
    ap.add_argument("--transport", choices=("agent_sdk", "api"), default=None)
    a = ap.parse_args()
    cfg = load_config()
    if a.transport:
        cfg.transport = a.transport
    setup_auth(cfg.transport)
    transport = make_transport(cfg.transport, MODEL_ALIAS, MODEL_API_ID)
    replay(a.replay, a.limit, cfg, transport)


if __name__ == "__main__":
    main()
