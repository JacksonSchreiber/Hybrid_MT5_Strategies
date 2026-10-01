#!/usr/bin/env python3
"""reoffer_count_study.py - coach item 26 D14: spread rejects on the demo archive and the live journals, and how many the
re-offer rule would have published.

Sources (pulled read-only from the box into data/study/item26/): demo archive live/archive/demo-20260929/journal, live
live/journal (account 600078707), audit_<SYM>.log spread_gate lines (the reject moment, true UTC, and the spread then),
web/spread_live.csv (5-minute live spread samples, from 2026-10-01 12:43Z only), M5 bars from the box feed via the web app's
/api/bars (broker clock = UTC+3 in September/October).
Rule replayed (item 26 A2/A3): hold from the reject for up to 3 H4 bars; at every M5 close re-check the spread; publish the first
time spread <= 0.10R of the CURRENT stop distance (entry = market: ask for a buy, bid for a sell; stop = the original), unless
since the reject price has traded through the stop or reached +1R (or TP1 if nearer) - then cancel. Spread at each M5 close is
an ESTIMATE: the live 5-minute samples' median for that symbol and UTC hour when there are >= 3, else the Sept-2026 MT5 hourly
profile (data/study/spread_profile.csv, a LOWER bound - MT5 bar minimum spread). Ask side = bid + that estimate.
Outcome so far (information only, n is tiny): the re-offered entry managed plainly (stop -1R on the new distance / TP2), marked
at the last available M5 close if still open.
"""
from __future__ import annotations
import csv, glob, json, os, re, statistics as st, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
D = os.path.join(ROOT, "data", "study", "item26")
REPORT = os.path.join(ROOT, "data", "study", "reoffer_count_report.txt")
BROKER_OFF = 3 * 3600                     # EEST (UTC+3) - every reject here is late Sept / Oct 2026
MAXR, HOLD_MIN = 0.10, 3 * 240
WEB = "http://10.77.0.1:8080"


def journal(path_glob, src):
    out = []
    for p in sorted(glob.glob(path_glob)):
        if any(x in p for x in (".actions.", ".delays.", ".inv.")): continue
        for r in csv.DictReader(open(p, errors="replace")): r["_src"] = src; out.append(r)
    return out


def audit_rejects():
    m = defaultdict(list)
    for p in glob.glob(os.path.join(D, "audit", "*.log")):
        for ln in open(p, errors="replace"):
            if "|spread_gate|" not in ln: continue
            f = ln.rstrip("\n").split("|")
            mm = re.search(r"= ([0-9.]+)R of the ([0-9.]+) stop", f[-1])
            m[(f[1], f[5])].append((datetime.strptime(f[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                                    float(mm.group(1)) if mm else None))
    return m


def spreads():
    live = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(D, "spread_live.csv"))):
        live[(r["symbol"], int(r["hour_utc"]))].append(float(r["spread"]))
    prof = {(r["symbol"], int(r["hour_utc"])): float(r["spread_med_hour"]) for r in csv.DictReader(open(os.path.join(ROOT, "data", "study", "spread_profile.csv")))
            if r["spread_med_hour"] not in ("", None)}
    def est(sym, h):
        v = live.get((sym, h))
        if v and len(v) >= 3: return st.median(v), "live"
        return prof.get((sym, h)), "profile"
    return est


def bars(sym):
    cp = os.path.join(D, "bars", f"{sym}_m5.json"); os.makedirs(os.path.dirname(cp), exist_ok=True)
    if os.path.exists(cp): return json.load(open(cp))
    with urllib.request.urlopen(f"{WEB}/api/bars/{sym}?tf=m5&n=2000", timeout=30) as f: b = json.loads(f.read())["bars"] or []
    json.dump(b, open(cp, "w")); return b


def main():
    rows = journal(os.path.join(D, "demo", "*.csv"), "demo") + journal(os.path.join(D, "journal", "*.csv"), "live")
    allsig = [r for r in rows if r.get("decision")]
    rej, seen = [], set()
    for r in rows:                                   # the demo archive and the live journal overlap: one row per signal
        if r.get("decision") == "rejected" and (r.get("reject_reason") or "").startswith("spread "):
            k = (r["symbol"], r["signal_time"], r["strategy"], r["entry"], r["sl"])
            if k not in seen: seen.add(k); rej.append(r)
    au = audit_rejects(); est = spreads()
    L = [__doc__.rstrip(), ""]
    for src in ("demo", "live"):
        n_all = sum(1 for r in allsig if r["_src"] == src); n_rej = sum(1 for r in rej if r["_src"] == src)
        L.append(f"{src}: {n_all} journal rows (decisions incl. rejects), {n_rej} spread rejects")
    for pess in (False, True):
        run(rej, au, est, pess, L)
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L[len(__doc__.splitlines()) + 1:]))


def run(rej, au, est, pess, L):
    res = []
    for r in rej:
        sym, sid = r["symbol"], r["signal_id"]
        st_b = datetime.strptime(r["signal_time"][:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
        close_utc = st_b + timedelta(hours=4) - timedelta(seconds=BROKER_OFF)                 # decision at the bar close (true UTC)
        a = next((x for x in sorted(au.get((sym, f"sig:{sid}"), [])) if close_utc - timedelta(minutes=5) <= x[0] <= close_utc + timedelta(hours=72)), None)
        t_rej = a[0] if a else close_utc
        spr0 = a[1] if a else float(re.search(r"spread ([0-9.]+)R", r["reject_reason"]).group(1))
        e, s = float(r["entry"]), float(r["sl"]); up = r["direction"].upper().startswith("B"); R = abs(e - s)
        tp1 = float(r["tp1"]) if r.get("tp1") else None; tp2 = float(r["tp2"]) if r.get("tp2") else (float(r["tp"]) if r.get("tp") else None)
        trig = e + (R if up else -R)
        if tp1 and ((up and tp1 < trig) or (not up and tp1 > trig)): trig = tp1
        b = bars(sym); t0 = int(t_rej.timestamp()) + BROKER_OFF                                   # feed epochs are broker clock
        x = {"src": r["_src"], "sym": sym, "sid": sid, "strat": r["strategy"], "dir": "BUY" if up else "SELL", "month": r["signal_time"][:7],
             "hour": t_rej.hour, "spr0": spr0, "t_rej": t_rej, "result": "no bars", "held": None, "spr1": None, "drift": None, "srcs": "", "out": None}
        post = [k for k in b if k[0] >= t0 - 300]
        if not post or post[0][0] > t0 + 600: res.append(x); continue
        x["result"] = "never normalised"; srcs = set()
        for k in post:
            if k[0] + 300 - t0 > HOLD_MIN * 60: break
            hutc = datetime.fromtimestamp(k[0] - BROKER_OFF, timezone.utc).hour
            sp, srcn = est(sym, hutc); srcs.add(srcn)
            if sp is None: continue
            if pess:                                  # pessimistic: that day's excess over the typical spread persists all window
                sp0 = spr0 * R; base0 = est(sym, t_rej.hour)[0]
                if base0 and sp0 > base0: sp *= sp0 / base0
            hi_b, lo_b = k[2], k[3]
            if up:
                if lo_b <= s: x["result"] = "cancelled: stop traded"; break
                if hi_b >= trig: x["result"] = "cancelled: +1R/TP1 reached"; break
            else:
                if hi_b + sp >= s: x["result"] = "cancelled: stop traded"; break
                if lo_b + sp <= trig: x["result"] = "cancelled: +1R/TP1 reached"; break
            px = k[4] + (sp if up else 0.0); dist = (px - s) if up else (s - px)
            if dist <= 0: x["result"] = "cancelled: stop traded"; break
            if sp / dist <= MAXR:
                x["result"] = "RE-OFFERED"; x["held"] = (k[0] + 300 - t0) / 60.0; x["spr1"] = sp / dist
                x["drift"] = ((px - e) if up else (e - px)) / R
                rr = None                                                                             # plain outcome so far
                for k2 in [q for q in b if q[0] > k[0]]:
                    if up:
                        if k2[3] <= s: rr = -1.0; break
                        if tp2 and k2[2] >= tp2: rr = (tp2 - px) / dist; break
                    else:
                        if k2[2] + sp >= s: rr = -1.0; break
                        if tp2 and k2[3] + sp <= tp2: rr = (px - tp2) / dist; break
                if rr is None: rr = ((b[-1][4] - px) if up else (px - b[-1][4] - sp)) / dist; x["out"] = f"{rr:+.2f} open"
                else: x["out"] = f"{rr:+.2f}"
                break
        x["srcs"] = "/".join(sorted(srcs)); res.append(x)
    L.append("\n" + "=" * 100 + ("\nPESSIMISTIC spread: the reject-hour excess (observed / typical) is applied to every later hour of the window"
             if pess else "\nOPTIMISTIC spread: each hour at its typical level (live samples, else the MT5 profile - a lower bound)"))
    L.append("EVERY SPREAD REJECT (reject time in true UTC):")
    L.append(f"  {'src':4} {'symbol':11} {'sig':>3} {'detector':9} {'dir':4} {'rejected at':17} {'spread':>6} | {'result':27} {'held':>6} {'spread then':>11} {'drift':>6} {'spread est':>10} {'plain R so far':>14}")
    for x in sorted(res, key=lambda x: x["t_rej"]):
        L.append(f"  {x['src']:4} {x['sym']:11} {x['sid']:>3} {x['strat']:9} {x['dir']:4} {x['t_rej'].strftime('%a %d %b %H:%M'):17} {x['spr0']:6.3f} | {x['result']:27} "
                 + (f"{x['held']:5.0f}m {x['spr1']:11.3f} {x['drift']:+6.2f} {x['srcs']:>10} {x['out']:>14}" if x["result"] == "RE-OFFERED" else f"{'':6} {'':11} {'':6} {x['srcs']:>10}"))
    for lab, key in (("month", "month"), ("symbol", "sym"), ("UTC hour of the reject", "hour"), ("detector", "strat")):
        c = Counter(x[key] for x in res); ro = Counter(x[key] for x in res if x["result"] == "RE-OFFERED")
        L.append(f"\nby {lab}: " + "  ".join(f"{k} {c[k]} ({ro.get(k, 0)} re-offered)" for k in sorted(c)))
    L.append(f"\nTOTAL: {len(res)} spread rejects -> " + ", ".join(f"{k} {v}" for k, v in Counter(x['result'] for x in res).most_common()))


if __name__ == "__main__": main()
