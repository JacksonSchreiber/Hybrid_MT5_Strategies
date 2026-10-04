#!/usr/bin/env python3
"""backtest_render_check.py - coach 2026-10-04 item 10: side-by-side of recent LIVE cards - the H4 image the advisor saw (bundle
h4.png, rendered by live/charts.py from the terminal's bars) against the SAME signal built through the blind-card path
(pipeline/backtest_cards.build_card) and drawn by the interactive chart (live/static/hybrid_chart.js, blind mode), EMAs included.
Inputs (copied read-only from the box): data/backtest/render_check/src/<key>-h4.png, <key>.json (the signal), <sym>-h4-bars.json
(GET /api/bars, 500 H4 bars, broker clock). Output: data/backtest/render_check/<key>_side_by_side.png + a numbers file comparing the
blind card with the raw bars (last close and the EMAs in R at the signal bar).
    python3 pipeline/backtest_render_check.py
"""
from __future__ import annotations
import datetime as dt, glob, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import backtest_cards as BC                    # noqa: E402
from live import symbol_groups as SG                         # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest", "render_check")


def main():
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageDraw
    lines = []
    keys = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(D, "src", "*.json")) if "-bars" not in p)
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--allow-file-access-from-files"])
        for key in keys:
            sig = json.load(open(os.path.join(D, "src", f"{key}.json"))); sym = sig["symbol"]
            bars = json.load(open(os.path.join(D, "src", f"{sym}-h4-bars.json")))["bars"]
            st = int(dt.datetime.strptime(sig["signal_time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())
            isig = next((i for i, x in enumerate(bars) if x[0] == st), None)
            if isig is None or isig + 1 >= len(bars): lines.append(f"{key}: signal bar not in the fetched bars - skipped"); continue
            lv = sig["levels"]; up = sig["direction"] == "BUY"; g = SG.group_of(sym) or {"name": "?"}
            card, prov = BC.build_card(bars, isig, lv["entry"], lv["sl"], lv.get("tp1"), lv.get("tp2"), up, sig["strategy"], g["name"],
                                       SG.root(sym), __import__("hashlib").sha256(key.encode()).hexdigest()[:16])
            BC.check_card(card, prov)
            page = os.path.join(D, f"{key}_blind.html"); BC.dev_page(card, page)
            pg = b.new_page(viewport={"width": 1100, "height": 700}); errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("file://" + os.path.abspath(page)); pg.wait_for_timeout(1500)
            shot = os.path.join(D, f"{key}_blind_h4.png"); pg.locator("#c").screenshot(path=shot); pg.close()
            a = Image.open(os.path.join(D, "src", f"{key}-h4.png")).convert("RGB"); c = Image.open(shot).convert("RGB")
            h = max(a.height, c.height); out = Image.new("RGB", (a.width + c.width + 20, h + 30), (13, 17, 23))
            out.paste(a, (0, 30)); out.paste(c, (a.width + 20, 30)); dr = ImageDraw.Draw(out)
            dr.text((8, 8), f"{key}: LEFT = bundle h4.png the advisor saw (live/charts.py)", fill=(230, 237, 243))
            dr.text((a.width + 28, 8), "RIGHT = blind-card path (backtest_cards + hybrid_chart.js blind mode; prices in R from entry)", fill=(230, 237, 243))
            sb = os.path.join(D, f"{key}_side_by_side.png"); out.save(sb)
            # numbers: blind card vs the raw bars at the signal bar
            R = abs(lv["entry"] - lv["sl"]); raw_close = (bars[isig][4] - lv["entry"]) / R
            import pipeline.backtest_cards as M
            e200 = M.ema([x[4] for x in bars[:isig + 1]], 200)[-1]
            lines.append(f"{key} ({sig['strategy']} {sig['direction']} @ {sig['signal_time']}): card last close {card['h4']['bars'][-1][4]:+.3f}R vs raw {raw_close:+.3f}R; "
                         f"EMA200 card {card['h4']['ema']['200'][-1]:+.3f}R vs raw {(e200 - lv['entry']) / R:+.3f}R; bars in card {len(card['h4']['bars'])}; "
                         f"JS errors {errs[:2]} -> {os.path.basename(sb)}")
        b.close()
    open(os.path.join(D, "render_check.txt"), "w").write("\n".join(lines) + "\n"); print("\n".join(lines))


if __name__ == "__main__": main()
