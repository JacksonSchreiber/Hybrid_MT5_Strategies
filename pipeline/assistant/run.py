"""run.py — launcher + self-test for the blind advisory service.

    python3 -m pipeline.assistant.run --selftest            # subscription (default)
    python3 -m pipeline.assistant.run --selftest --transport api

The self-test proves the Tier 0 → Tier 1 path end-to-end on a SYNTHETIC blind
chart (no real L0/L1 screenshots exist yet): it runs the evaluation twice and
checks that `cache_read_input_tokens` is non-zero on the repeat call — the
definition-of-done cache verification — then prints the verdicts and the JSONL
log location.
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
from datetime import datetime

from pipeline.assistant.config import (Config, load_config, setup_auth,
                                       MODEL_ALIAS, MODEL_API_ID)
from pipeline.assistant.transport import make_transport
from pipeline.assistant import app
from pipeline import tier0


# ---- synthetic blind charts (stand-ins until real item-C screenshots exist) --
def _chart(kind: str) -> str:
    from PIL import Image, ImageDraw
    W, H = 520, 320
    im = Image.new("RGB", (W, H), (13, 17, 23))
    d = ImageDraw.Draw(im)
    import random
    r = random.Random(hash(kind) & 0xffff)
    n, x0, cw, step = 30, 24, 5, 16
    if kind == "d1":          # uptrend then a reluctant pullback into a zone
        y = 250
        seq = [-9] * 16 + [6] * 10 + [-3] * 4
    else:                     # h4: drift down into a zone, then a hammer up
        y = 120
        seq = [7] * 10 + [4] * 8 + [-2] * 6 + [-12, -10]
    for i in range(n):
        o = y
        y = max(30, min(H - 40, y + seq[i % len(seq)] + r.randint(-3, 3)))
        c = y
        x = x0 + i * step
        col = (63, 185, 80) if c < o else (248, 81, 73)
        hi = min(o, c) - r.randint(2, 8)
        lo = max(o, c) + r.randint(2, 8)
        d.line([(x, hi), (x, lo)], fill=col)
        d.rectangle([x - cw // 2, min(o, c), x + cw // 2, max(o, c)], fill=col)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def _selftest(cfg: Config, transport, runs: int):
    d1, h4 = _chart("d1"), _chart("h4")
    # a clean weekday DeepFib BUY that PASSES Tier 0 (verified in tier0 self-test)
    signal = tier0.Signal(symbol="EURUSD.dk", direction=1,
                          entry_time=datetime(2021, 1, 20, 8, 0), strategy="DeepFib")
    meta = {
        "strategy": "DeepFib", "direction": 1,
        "session": "London", "day_of_week": "Wednesday",
        "entry": 1.2100, "sl": 1.2050, "tp1": 1.2200, "tp2": 1.2280,
        "sl_r": 1.0, "tp1_r": 2.0, "tp2_r": 3.6,
        "cluster_exposure": "none reported",
    }
    audit = {"signal_id": "selftest-1", "symbol": "EURUSD.dk",
             "signal_time": "2021-01-20 08:00 UTC"}

    print(f"\n=== self-test: Tier 0 → Tier 1 (x{runs}) via transport={cfg.transport} ===")
    results = []
    for k in range(1, runs + 1):
        out = app.evaluate(signal=signal, audit=audit, images_b64=[d1, h4],
                           meta=meta, cfg=cfg, transport=transport)
        u = out.get("_usage", {})
        te = out.get("_transport_error")
        print(f"\n[run {k}] verdict={out.get('verdict')} "
              f"confidence={out.get('confidence')} tier={out.get('tier')}"
              + (f"  ESCALATE" if out.get("escalate") else ""))
        print(f"        why: {str(out.get('why'))[:200]}")
        if out.get("regime"):
            print(f"        regime={out.get('regime')} "
                  f"patterns={out.get('detected_patterns')}")
        print(f"        usage: cache_read={u.get('cache_read')} "
              f"cache_creation={u.get('cache_creation')} "
              f"in={u.get('input')} out={u.get('output')}"
              + (f"  [transport_error: {te}]" if te else ""))
        results.append(out)

    # definition-of-done cache check
    if runs >= 2:
        cr = results[-1].get("_usage", {}).get("cache_read") or 0
        ok = cr > 0
        print(f"\ncache verification (repeat call): cache_read={cr} → "
              f"{'PASS (prompt cache hit)' if ok else 'no cache read — check prefix stability / TTL'}")
    print(f"\nJSONL log: {cfg.log_path}")


def main():
    ap = argparse.ArgumentParser(description="Blind advisory service launcher.")
    ap.add_argument("--selftest", action="store_true",
                    help="run the synthetic Tier 0 → Tier 1 end-to-end check")
    ap.add_argument("--transport", choices=("agent_sdk", "api"), default=None,
                    help="override ASSISTANT_TRANSPORT (default agent_sdk = subscription)")
    ap.add_argument("--runs", type=int, default=2, help="self-test repeat count")
    a = ap.parse_args()

    cfg = load_config()
    if a.transport:
        cfg.transport = a.transport

    # §8b: prepare auth (unset ANTHROPIC_API_KEY on subscription; log the path)
    setup_auth(cfg.transport)

    transport = make_transport(cfg.transport, MODEL_ALIAS, MODEL_API_ID)

    if a.selftest:
        _selftest(cfg, transport, max(1, a.runs))
    else:
        print("nothing to do — pass --selftest (or wire a real signal source).",
              file=sys.stderr)


if __name__ == "__main__":
    main()
