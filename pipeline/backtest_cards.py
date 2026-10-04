#!/usr/bin/env python3
"""backtest_cards.py - coach 2026-10-04 (backtest tab, phase 1): the BLIND card data for every trade in the frozen split.

A card holds only what was knowable at the signal close, with nothing that names the symbol or the date:
  h4      the last 264 H4 bars (~2 months) ending with the signal bar (whose close is the signal close), + EMA 20/50/200 computed
          on the FULL history up to the signal bar and cut to the window (right from the first visible bar)
  d1      the last 44 days: completed days strictly before the signal bar's day + today's PARTIAL day built only from H4 bars up to
          the signal bar; EMA 20/50/200 the same way (on the daily history built from H4)
  levels  entry / SL / TP1 / TP2;  swings  the EA's swing table (live/overlays.swing_table) on the window
  regime  the frozen D1 tag (pipeline/trader_selection_study.regime_series) on COMPLETED days only, + with/counter trend
  events  scheduled econ events (data: MT5 Common Files econ_events.csv, 2013-01..2026-07, UTC, classes V/W/C/H) for the
          symbol's base / quote currency and "All", in the next 14 days, as relative hours, a generic kind and the class - no
          actual / forecast values, no currency or country names (base / quote / all instead)
BLIND: every price is (p - entry) / R (R = |entry - SL|; unsigned, so a short's SL sits at +1R); times are synthetic and
contiguous (weekends removed) and labelled relative to the signal. Detector overlays (zones / fib leg) are NOT in the historical
record - a gap; the chart's own imbalance zones and swing markers are drawn from the bars.
check_card() runs on EVERY card: nothing after the signal close (every real timestamp behind the card <= the signal bar), the D1
partial day only from H4 bars up to the signal bar, the window <= 264 H4 / 44 D1 bars, no identifying fields, no event values.
    python3 pipeline/backtest_cards.py              -> data/backtest/cards_v1.json.gz (+ build report)
    python3 pipeline/backtest_cards.py --dev N      also write N local dev pages (data/backtest/dev/<hid>.html)
"""
from __future__ import annotations
import csv, datetime as dt, gzip, json, os, re, sys, time
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import overlays as OV                              # noqa: E402
from live import symbol_groups as SG                         # noqa: E402
from pipeline import backtest_split as BS                    # noqa: E402
from pipeline.trader_selection_study import regime_series    # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "backtest", "cards_v1.json.gz"); REPORT = os.path.join(ROOT, "data", "backtest", "cards_v1_report.txt")
DEV = os.path.join(ROOT, "data", "backtest", "dev")
ECON = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/econ_events.csv"
H4N, D1N, EV_DAYS = 264, 44, 14
T0 = 946857600                                              # synthetic epoch for bar 0 (no meaning; never shown)
KIND = [(r"rate|monetary policy|policy statement|cash rate|bank rate|official", "rate decision"), (r"cpi|inflation|pce|price index", "inflation"),
        (r"employment|payroll|unemployment|jobless|claimant|jobs|labor|labour", "jobs"), (r"gdp|growth", "GDP"), (r"pmi|ism", "PMI"),
        (r"retail", "retail sales"), (r"speak|testif|testimony|press conference|chair|governor|president", "speech"),
        (r"election|referendum|vote|ballot|parliament", "political"), (r"trade balance|current account", "trade"),
        (r"confidence|sentiment", "confidence"), (r"holiday|bank holiday", "holiday")]


def tmin(s) -> int:
    """'YYYY.MM.DD HH:MM' (or an epoch in seconds) -> minutes since the epoch, UTC."""
    if isinstance(s, (int, float)): return int(s) // 60
    return int(dt.datetime.strptime(s[:16], "%Y.%m.%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() // 60)


def ema(vals: list[float], n: int) -> list[float]:
    k = 2 / (n + 1); out = []; p = vals[0]
    for i, v in enumerate(vals): p = v if i == 0 else p + k * (v - p); out.append(p)
    return out


def day_of(t) -> str:
    return str(t)[:10] if not isinstance(t, (int, float)) else dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y.%m.%d")


def prep_symbol(bars: list) -> dict:
    """per-symbol work done once (each value at index i depends only on data up to i, so slicing it at the signal is the same as
    computing it on the history up to the signal): H4 EMAs, the COMPLETED daily bars (with the first H4 index of each day), their
    EMAs and the frozen regime tag per day."""
    closes = [b[4] for b in bars]
    days: list = []; first: list = []; cur = None
    for i, b in enumerate(bars):
        d = day_of(b[0])
        if d != cur: days.append([d, b[1], b[2], b[3], b[4]]); first.append(i); cur = d
        else: days[-1][2] = max(days[-1][2], b[2]); days[-1][3] = min(days[-1][3], b[3]); days[-1][4] = b[4]
    dc = [d[4] for d in days]
    return {"ema_h4": {n: ema(closes, n) for n in (20, 50, 200)}, "days": days, "first": first, "day_idx": {d[0]: k for k, d in enumerate(days)},
            "ema_d1": {n: ema(dc, n) for n in (20, 50, 200)}, "regime": regime_series(days)}


_EV = None
def events_feed():
    global _EV
    if _EV is None:
        _EV = []
        for r in csv.DictReader(open(ECON, newline="")):
            if not r.get("class"): continue
            _EV.append((tmin(r["datetime_utc"]), r["ccy"].strip(), r["event"], r["class"]))
        _EV.sort()
    return _EV


def kind_of(name: str) -> str:
    n = name.lower()
    for pat, k in KIND:
        if re.search(pat, n): return k
    return "other"


def events_for(root: str, sig_close_min: int) -> tuple[list, str]:
    ev = events_feed()
    if not ev: return [], "unavailable"
    if sig_close_min < ev[0][0] or sig_close_min + EV_DAYS * 1440 > ev[-1][0]: return [], "unavailable"     # no dates: they would leak the period
    base, quote = root[:3], root[3:6]; out = []
    import bisect
    i = bisect.bisect_left(ev, (sig_close_min, "", "", ""))
    while i < len(ev) and ev[i][0] <= sig_close_min + EV_DAYS * 1440:
        t, c, name, cls = ev[i]; i += 1
        side = "base" if c == base else ("quote" if c == quote else ("all" if c.lower() == "all" else None))
        if side: out.append({"in_h": round((t - sig_close_min) / 60, 1), "side": side, "kind": kind_of(name), "cls": cls})
    return out[:20], "ok"


def build_card(bars: list, isig: int, e: float, sl: float, tp1, tp2, up: bool, det: str, group: str, root: str, hid: str,
               blind: bool = True, ctx: dict | None = None) -> tuple[dict, dict]:
    """bars = the symbol's FULL H4 history [[time, o, h, l, c, (v)]], isig = index of the signal bar, ctx = prep_symbol(bars).
    -> (card, provenance)."""
    ctx = ctx or prep_symbol(bars)
    R = abs(e - sl)
    nz = (lambda p: round((p - e) / R, 3)) if blind else (lambda p: p)
    lo = max(0, isig - H4N + 1); win = bars[lo:isig + 1]
    eh = {n: ctx["ema_h4"][n][lo:isig + 1] for n in (20, 50, 200)}
    sig_day = day_of(bars[isig][0]); kd = ctx["day_idx"][sig_day]          # the signal bar's day: completed days are 0..kd-1
    part_idx = list(range(ctx["first"][kd], isig + 1))                      # today's PARTIAL day: only H4 bars up to the signal bar
    part = [sig_day, bars[part_idx[0]][1], max(bars[i][2] for i in part_idx), min(bars[i][3] for i in part_idx), bars[isig][4]]
    days = ctx["days"][:kd] + [part]; ipart = kd
    dlo = max(0, ipart - D1N + 1); dwin = days[dlo:ipart + 1]
    ed = {}
    for n in (20, 50, 200):                                                 # completed days' EMA, then one step with today's partial close
        prev = ctx["ema_d1"][n][kd - 1] if kd else part[4]; k = 2 / (n + 1)
        ed[n] = ctx["ema_d1"][n][dlo:kd] + [prev + k * (part[4] - prev) if kd else part[4]]
    tag = ctx["regime"][kd - 1] if kd else ""                                # the last COMPLETED day
    wt = (1 if (tag == "TREND_UP") == up else 0) if tag in ("TREND_UP", "TREND_DOWN") else None
    sw = OV.swing_table([list(b[:5]) for b in win], 5)
    sig_close = tmin(bars[isig + 1][0]) if isig + 1 < len(bars) else tmin(bars[isig][0]) + 240
    evs, cal = events_for(root, sig_close)
    if blind:
        h4b = [[T0 + j * 14400, nz(b[1]), nz(b[2]), nz(b[3]), nz(b[4]), 0] for j, b in enumerate(win)]
        d1b = [[T0 + j * 86400, nz(d[1]), nz(d[2]), nz(d[3]), nz(d[4]), 0] for j, d in enumerate(dwin)]
    else:   # the render check only: real prices and real times (epoch seconds)
        h4b = [[tmin(b[0]) * 60, b[1], b[2], b[3], b[4], b[5] if len(b) > 5 else 0] for b in win]
        d1b = [[tmin(d[0] + " 00:00") * 60, d[1], d[2], d[3], d[4], 0] for d in dwin]
    card = {"v": 1, "hid": hid, "group": group, "detector": det, "direction": "BUY" if up else "SELL",
            "regime": {"tag": tag, "with_trend": wt},
            "levels": {"entry": nz(e), "sl": nz(sl), "tp1": nz(tp1) if tp1 else None, "tp2": nz(tp2) if tp2 else None},
            "h4": {"bars": h4b, "ema": {str(n): [round(nz(v), 3) if blind else v for v in eh[n]] for n in eh}, "sig": len(h4b) - 1},
            "d1": {"bars": d1b, "ema": {str(n): [round(nz(v), 3) if blind else v for v in ed[n]] for n in ed}, "sig": len(d1b) - 1},
            "swings": {k: [{"p": nz(s["p"]), "bars_ago": s["bars_ago"]} for s in sw[k]] for k in ("hi", "lo")},
            "events": evs, "calendar": cal}
    prov = {"sig_bar_min": tmin(bars[isig][0]), "sig_close_min": sig_close, "h4_last_min": tmin(win[-1][0]),
            "ema_last_index": isig, "d1_part_h4_max_min": max(tmin(bars[i][0]) for i in part_idx) if part_idx else None,
            "d1_days": [d[0] for d in dwin], "regime_days_max": ctx["days"][kd - 1][0] if kd else None, "sig_day": sig_day}
    return card, prov


IDENT = re.compile(r"(?:19|20)\d\d[.\-/]\d\d|AUD|NZD|CAD|USD|EUR|GBP|JPY|CHF|XAU|XAG|\.dk|\.sim")
def check_card(card: dict, prov: dict) -> None:
    """raises AssertionError when the card could show anything after the signal close or identify the symbol / date."""
    assert prov["h4_last_min"] == prov["sig_bar_min"], "H4 window does not end at the signal bar"
    assert prov["h4_last_min"] < prov["sig_close_min"], "H4 window reaches the signal close or later"
    assert prov["ema_last_index"] is not None
    assert prov["d1_part_h4_max_min"] is None or prov["d1_part_h4_max_min"] <= prov["sig_bar_min"], "D1 partial day uses a bar after the signal"
    assert prov["d1_days"][-1] == prov["sig_day"] and all(d < prov["sig_day"] for d in prov["d1_days"][:-1]), "D1 window is not completed days + today"
    assert prov["regime_days_max"] is None or prov["regime_days_max"] < prov["sig_day"], "regime uses the signal day"
    assert len(card["h4"]["bars"]) <= H4N and len(card["d1"]["bars"]) <= D1N, "window longer than ~2 months"
    for n in ("20", "50", "200"):
        assert len(card["h4"]["ema"][n]) == len(card["h4"]["bars"]) and len(card["d1"]["ema"][n]) == len(card["d1"]["bars"])
    for ev in card["events"]:
        assert set(ev) == {"in_h", "side", "kind", "cls"} and ev["in_h"] >= 0, "event carries values / is in the past"
    s = json.dumps({k: v for k, v in card.items() if k != "group"})          # the group label is allowed (coach item 3)
    assert not IDENT.search(s), f"identifying text in the card: {IDENT.search(s).group(0)}"
    assert not any(k in card for k in ("sym", "symbol", "t", "date", "time", "key")), "identifying field"


def dev_page(card: dict, path: str) -> None:
    rel = os.path.relpath(os.path.join(ROOT, "live", "static"), os.path.dirname(path))
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind card {card['hid']}</title><style>body{{background:#0d1117;color:#c9d1d9;font:14px system-ui;margin:16px}} .hc-bar button{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 10px;margin-right:6px}} .hc-bar button.on{{background:#1f6feb}} .k{{color:#8b949e}} table{{border-collapse:collapse}} td{{padding:2px 8px}}</style></head><body>
<h3>{card['group']} · {card['detector']} {card['direction']} · regime {card['regime']['tag'] or 'n/a'}</h3>
<div id="c"><div class="hc-bar"><button data-tf="h4" class="on">H4</button><button data-tf="d1">D1</button><span class="hc-status k"></span></div><div class="hc-box"></div></div>
<div class="k">events (next 14 days): {', '.join(f"in {e['in_h']:g}h {e['side']} {e['kind']} [{e['cls']}]" for e in card['events']) or card['calendar']}</div>
<script src="{rel}/lw.js"></script><script src="{rel}/hybrid_chart.js"></script>
<script>var C={json.dumps(card, separators=(",", ":"))};
HybridChart.mount("c", {{blind: true, live: false, bars_h4: C.h4.bars, bars_d1: C.d1.bars, ema_h4: C.h4.ema, ema_d1: C.d1.ema, sig_h4: C.h4.sig, sig_d1: C.d1.sig,
  levels: C.levels, strategy: C.detector, overlay: null}});</script></body></html>"""
    open(path, "w").write(html)


def main():
    from pipeline import combo_build as CB
    t0 = time.time(); split = BS.load_split()
    popk = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    gname = {g: gi["name"] for g, gi in split["groups"].items()}
    cards = {}; fails = []; cal_gap = 0; ntr = 0; ctxs: dict = {}
    for h, t in sorted(split["trades"].items(), key=lambda kv: kv[1]["key"][0]):
        x = popk[tuple(t["key"])]; ntr += 1
        if x["i0"] < 400: fails.append((h, "too little history")); continue
        if x["sym"] not in ctxs: ctxs.clear(); ctxs[x["sym"]] = prep_symbol(x["bars"])
        card, prov = build_card(x["bars"], x["i0"] - 1, x["e"], x["s"], x["tp1"], x["tp2"], x["up"], x["strat"], gname[t["group"]],
                                x["sym"].split(".")[0], h, ctx=ctxs[x["sym"]])
        try: check_card(card, prov)
        except AssertionError as ex: fails.append((h, str(ex))); continue
        cal_gap += card["calendar"] != "ok"; cards[h] = card
    if any(f[1] != "too little history" for f in fails):
        raise SystemExit(f"CHECK FAILED on {sum(1 for f in fails if f[1] != 'too little history')} cards, e.g. {[f for f in fails if f[1] != 'too little history'][:3]}")
    doc = {"v": 1, "built": time.strftime("%Y-%m-%d %H:%M"), "split_sha": split["sha256"], "n": len(cards), "cards": cards}
    raw = json.dumps(doc, separators=(",", ":")).encode()
    with gzip.open(OUT, "wb", compresslevel=9) as f: f.write(raw)
    L = [f"cards v1 built {doc['built']} from split {split['sha256'][:16]}..: {len(cards)} of {ntr} trades carded, "
         f"{len(fails)} skipped (too little history before the signal), check_card PASS on every carded trade",
         f"calendar unavailable on {cal_gap} cards (signal before 2013 or within 14 days of the feed's end)",
         f"size {len(raw) / 1e6:.1f} MB raw, {os.path.getsize(OUT) / 1e6:.1f} MB gzip; {time.time() - t0:.0f}s"]
    if "--dev" in sys.argv:
        os.makedirs(DEV, exist_ok=True); k = int(sys.argv[sys.argv.index("--dev") + 1])
        for h in list(cards)[:k]: dev_page(cards[h], os.path.join(DEV, f"{h}.html"))
        L.append(f"dev pages: {k} in {DEV}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
