#!/usr/bin/env python3
"""Item 3 (coach 2026-09-08): BE-floor counterfactual. For each BE instance, the final
R had the stop NEVER moved to BE -- a clean forward H4 bar-walk from entry with the
ORIGINAL stop held to the single target, conservative intrabar (stop-first)."""
import csv, sys
from datetime import datetime
CJ="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
H4="data/backtests/emarev_inv"
INST=[("USOIL.dk",f"{CJ}/USOIL.dk_20190101_20191230.csv",15,f"{H4}/USOIL.dk.h4.csv"),
      ("USOIL.dk",f"{CJ}/USOIL.dk_20190101_20191230.csv",20,f"{H4}/USOIL.dk.h4.csv"),
      ("USOIL.dk",f"{CJ}/USOIL.dk_20190101_20191230.csv",25,f"{H4}/USOIL.dk.h4.csv"),
      ("XAUUSD.dk",f"{CJ}/XAUUSD.dk_20210103_20211230.csv",16,f"{H4}/XAUUSD.dk.h4.csv")]
def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S","%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s.strip(),f)
        except: pass
    return None
def load_h4(p):
    b=[]
    for r in csv.DictReader(open(p,encoding="utf-8",errors="replace")):
        t=dt(r["time"])
        if t: b.append((t,float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"])))
    b.sort(); return b
def sigrow(j,sid):
    for r in csv.DictReader(open(j,encoding="utf-8",errors="replace")):
        if r["signal_id"]==str(sid): return r
    return None
print("# Item 3 — BE-floor counterfactual (stop never moved)\n")
print("| inst | dir | actual R | counterfactual R (orig stop held) | Δ | outcome |")
print("|---|---|---|---|---|---|")
for sym,j,sid,h4 in INST:
    r=sigrow(j,sid); bars=load_h4(h4)
    d=1 if r["direction"]=="BUY" else -1
    entry=float(r["entry"]); sl=float(r["orig_sl"]); tp=float(r["tp1"] or r["tp"])
    et=dt(r["signal_time"]); risk=abs(entry-sl); actual=r["r_multiple"]
    fwd=[x for x in bars if x[0]>et]
    cf=None; reason="open_end"
    for (t,o,hi,lo,c) in fwd:
        stop=(lo<=sl) if d>0 else (hi>=sl)
        tph=(hi>=tp) if d>0 else (lo<=tp)
        if stop and tph:  # both in bar -> conservative stop-first
            cf=-1.0; reason="SL(stop-first)"; break
        if stop: cf=-1.0; reason="SL"; break
        if tph: cf=(abs(tp-entry)/risk); reason="TP"; break
    if cf is None:
        cf=((fwd[-1][4]-entry)/risk if d>0 else (entry-fwd[-1][4])/risk) if fwd else 0.0; reason="mark-to-end"
    a=float(actual); dlt=cf-a
    print(f"| {sym} #{sid} | {'BUY' if d>0 else 'SELL'} | {a:+.2f} | {cf:+.2f} ({reason}) | {dlt:+.2f} | {'BE cut a winner' if dlt>0.3 else 'BE saved a loss' if dlt<-0.3 else 'wash'} |")
print("\n_5 ratification-set BE instances await the coach's signal list._")
