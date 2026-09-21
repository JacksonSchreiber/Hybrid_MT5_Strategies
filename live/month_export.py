"""
month_export.py - the monthly export the coach grades live months from (coach 2026-09-21, §11.9(b) amended).

One zip per month (web: /export/<YYYYMM>.zip, dashboard button): every symbol's monthly journal / actions / delays for
that month, the month's verdicts.live.log + consults.log lines, the Opus fallback policy history, the lineup history,
and the EXPIRED-TAKE report:

  take_expiries_<YYYYMM>.csv   every TAKE-class signal that expired unanswered (skipped code 8 - journaled, NOT charged
                               since 2026-09-21) with its BLIND OUTCOME: what the trade would have made had it been taken
                               at presentation and then managed purely mechanically (doctrine v2: 50 % banked at +1R - or
                               at TP1 when that is nearer - stop to BE + 1 pip, runner to TP2/TP; stop-first inside a bar,
                               M15 walk; costs off; entry = the detector's level at presentation, a STOP entry fills only
                               if price reaches it within the 3-bar answer window).
  take_expiry_report_<YYYYMM>.md   the TAKE-expiry rate (code-8 TAKEs / TAKE signals the trader could answer) and its
                               split by UTC hour of presentation, plus the sum of the blind outcomes.

TAKE signals "the trader could answer" = TAKE-class journal rows that are not EA-made (auto=0: excludes election gate 2,
superseded 9 and FTMO auto-reject 10) and not invalidated (`rejected`). TEST rows excluded throughout.
"""
from __future__ import annotations
import csv, glob, io, json, os, zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from live import common as C

M15 = 900

def _pip(price: float, digits: int) -> float:
    pt = 10 ** -digits
    return 10 * pt if digits in (3, 5) else pt

def blind_outcome(bars: list[list], direction: int, entry: float, sl: float, tp1: float, tp2: float, tp: float,
                  partial: float, stop_entry: bool, start_epoch: int, digits: int, answer_window_s: int = 3 * 4 * 3600) -> dict:
    """mechanical doctrine-v2 walk over M15 bars (server epochs, oldest first) from the presentation bar."""
    risk = abs(entry - sl)
    if risk <= 0: return {"result": "no_geometry", "R": None}
    d = 1 if direction > 0 else -1
    r_of = lambda px: d * (px - entry) / risk
    runner_tp = tp2 if (partial > 0 and tp2 > 0) else (tp or tp2 or tp1)
    trig_px = entry + d * risk
    trig_via = "+1R"
    if partial > 0 and tp1 > 0 and (d * (tp1 - trig_px) <= 0): trig_px, trig_via = tp1, "TP1"
    pad = _pip(entry, digits)
    be_px = entry + d * pad
    walk = [b for b in bars if b[0] >= start_epoch]
    if not walk: return {"result": "no_data", "R": None}
    i = 0
    if stop_entry:
        filled = False
        for i, b in enumerate(walk):
            if b[0] - start_epoch > answer_window_s: break
            if (b[2] >= entry if d > 0 else b[3] <= entry): filled = True; break
        if not filled: return {"result": "stop entry never filled", "R": 0.0, "bars": i}
    banked = False; bank_r = 0.0
    for j in range(i, len(walk)):
        b = walk[j]; hi, lo = b[2], b[3]
        adverse = lo if d > 0 else hi; favour = hi if d > 0 else lo
        if not banked:
            if (adverse <= sl if d > 0 else adverse >= sl):
                return {"result": "SL", "R": -1.0, "exit_t": b[0], "bars_m15": j - i}
            if (favour >= trig_px if d > 0 else favour <= trig_px):
                banked = True; bank_r = r_of(trig_px)
                continue                                   # conservative: the runner is judged from the NEXT bar
        else:
            if (adverse <= be_px if d > 0 else adverse >= be_px):
                return {"result": f"banked {trig_via} then BE", "R": round(0.5 * bank_r + 0.5 * r_of(be_px), 3), "exit_t": b[0], "bars_m15": j - i}
            if runner_tp and (favour >= runner_tp if d > 0 else favour <= runner_tp):
                return {"result": f"banked {trig_via} then TP", "R": round(0.5 * bank_r + 0.5 * r_of(runner_tp), 3), "exit_t": b[0], "bars_m15": j - i}
    last = walk[-1][4]
    if banked: return {"result": f"banked {trig_via}, runner open", "R": round(0.5 * bank_r + 0.5 * r_of(last), 3), "open": True}
    return {"result": "open (no exit yet)", "R": round(r_of(last), 3), "open": True}

def _month_rows(cfg: dict, ym: str) -> list[dict]:
    out = []
    for p in glob.glob(os.path.join(cfg["root"], "journal", f"*_{ym}.csv")):
        if any(x in os.path.basename(p) for x in (".actions.", ".delays.")): continue
        with open(p, encoding="ascii", errors="replace", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("strategy") or "").strip().upper() != "TEST": out.append(r)
    return out

def take_report(cfg: dict, ym: str, log=None) -> tuple[list[dict], str]:
    from live import mt5feed
    rows = _month_rows(cfg, ym)
    take = [r for r in rows if (r.get("decision_class") or "").strip() == "TAKE" and str(r.get("auto") or "0").strip() != "1"
            and (r.get("decision") or "") != "rejected"]
    expired = [r for r in take if r.get("decision") == "skipped" and str(r.get("skip_reason")).strip() == "8"]
    hours_all, hours_exp = Counter(), Counter()
    out = []
    for r in take:
        sym = r["symbol"]; sid = r["signal_id"]
        s = C.signal(cfg, f"{sym}-{sid}") or {}
        pub = C.parse_iso(s.get("decision_bar_utc") or s.get("published_at"))
        h = pub.hour if pub else None
        hours_all[h] += 1
        if r not in expired: continue
        hours_exp[h] += 1
        lv = s.get("levels") or {}
        f = lambda k, alt: float(lv.get(k) or r.get(alt) or 0)
        entry, sl, tp1, tp2, tp = f("entry", "orig_entry"), f("sl", "orig_sl"), f("tp1", "orig_tp1"), f("tp2", "orig_tp2"), f("tp", "orig_tp")
        partial = float(lv.get("partial_fraction") or r.get("partial_frac") or 0)
        d = 1 if (r.get("direction") or "").upper() == "BUY" else -1
        start = C.parse_iso(s.get("decision_bar_server"))
        start_ep = int(start.timestamp()) if start else None
        res = {"result": "no presentation bar (signal file missing)", "R": None}
        if start_ep:
            digits = max(0, len(str(lv.get("entry") or r.get("orig_entry")).split(".")[1])) if "." in str(lv.get("entry") or r.get("orig_entry")) else 2
            bars = None
            try: bars = mt5feed.bars_range(sym, "m15", start_ep - M15, int(datetime.now(timezone.utc).timestamp()) + 4 * 3600)
            except Exception as e:
                if log: log(f"export: bars for {sym}: {e!r}")
            res = blind_outcome(bars or [], d, entry, sl, tp1, tp2, tp, partial, bool(lv.get("stop_entry")), start_ep, digits) if bars else {"result": "feed unavailable", "R": None}
        out.append({"symbol": sym, "signal_id": sid, "strategy": r.get("strategy"), "direction": r.get("direction"),
                    "presented_utc": pub.strftime("%Y-%m-%d %H:%M") if pub else "", "hour_utc": h, "regime": r.get("regime"),
                    "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "blind_result": res.get("result"), "blind_R": res.get("R"),
                    "still_open": bool(res.get("open"))})
    n_take, n_exp = len(take), len(expired)
    rs = [x["blind_R"] for x in out if isinstance(x["blind_R"], (int, float))]
    hours = sorted({k for k in hours_all if k is not None})
    md = [f"# Expired TAKE-class signals — {ym[:4]}-{ym[4:]} (§11.9(b) amended 2026-09-21: journaled, NOT charged)", "",
          f"- TAKE-class signals the trader could answer: **{n_take}** (auto=0, not invalidated, TEST excluded)",
          f"- expired unanswered (code 8): **{n_exp}** → TAKE-expiry rate **{(n_exp / n_take * 100) if n_take else 0:.1f}%**",
          f"- blind outcome of the expired ones (mechanical doctrine v2, costs off): **{sum(rs):+.2f}R** over {len(rs)} priced"
          + (f", {sum(1 for x in out if x['still_open'])} still open (marked to the last close)" if any(x["still_open"] for x in out) else ""),
          "", "| hour presented (UTC) | TAKE signals | expired | rate |", "|---|---|---|---|"]
    for h in hours:
        md.append(f"| {h:02d}:00 | {hours_all[h]} | {hours_exp[h]} | {(hours_exp[h] / hours_all[h] * 100) if hours_all[h] else 0:.0f}% |")
    if None in hours_all: md.append(f"| (no signal file) | {hours_all[None]} | {hours_exp[None]} | |")
    md += ["", "| signal | presented (UTC) | regime | blind result | blind R |", "|---|---|---|---|---|"]
    for x in out:
        md.append(f"| {x['symbol']} #{x['signal_id']} {x['strategy']} {x['direction']} | {x['presented_utc']} | {x['regime']} | {x['blind_result']} | "
                  f"{'-' if x['blind_R'] is None else format(x['blind_R'], '+.2f')} |")
    md += ["", "Method: entry at the detector's level at presentation (a STOP entry fills only if price reaches it inside the 3-bar "
           "answer window); 50 % banked at +1R or TP1 if nearer, stop to BE + 1 pip; runner to TP2 (TP for single-target); M15 walk, "
           "stop-first inside a bar, the runner judged from the bar after the bank; costs off. Same exits the EA runs mechanically."]
    return out, "\n".join(md) + "\n"

def build_zip(cfg: dict, ym: str, log=None) -> tuple[bytes, str]:
    rows, md = take_report(cfg, ym, log)
    exp_dir = os.path.join(cfg["root"], "export", ym); os.makedirs(exp_dir, exist_ok=True)
    cbuf = io.StringIO()
    cols = ["symbol", "signal_id", "strategy", "direction", "presented_utc", "hour_utc", "regime", "entry", "sl", "tp1", "tp2", "blind_result", "blind_R", "still_open"]
    w = csv.DictWriter(cbuf, fieldnames=cols); w.writeheader(); w.writerows(rows)
    C.atomic_write_text(os.path.join(exp_dir, f"take_expiries_{ym}.csv"), cbuf.getvalue())
    C.atomic_write_text(os.path.join(exp_dir, f"take_expiry_report_{ym}.md"), md)
    buf = io.BytesIO(); pfx = f"{ym[:4]}-{ym[4:]}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(glob.glob(os.path.join(cfg["root"], "journal", f"*_{ym}*.csv"))): z.write(p, f"journal/{os.path.basename(p)}")
        z.writestr(f"take_expiries_{ym}.csv", cbuf.getvalue()); z.writestr(f"take_expiry_report_{ym}.md", md)
        for name, path in (("lineup_history.json", os.path.join(cfg["root"], "config", "lineup_history.json")),
                           ("opus_policy.json", os.path.join(cfg["root"], "advisor", "opus_policy.json"))):
            if os.path.exists(path): z.write(path, name)
        for name, path in (("verdicts.live.log", os.path.join(cfg["advisor"]["live_dir"], "verdicts.live.log")),
                           ("consults.log", os.path.join(cfg["root"], "advisor", "consults.log"))):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    z.writestr(f"{name.split('.')[0]}_{ym}.log", "".join(l for l in f if l[:7] == pfx))
            except OSError: pass
    return buf.getvalue(), f"hybrid-export_{ym}.zip"
