#!/usr/bin/env python3
"""Like normalize_econ.py, but repairs the ehsanrs2 archive's DATE-ONLY rows.

Many historical rows store the event at LOCAL 00:00 (or 23:59) - a "no intraday
time" sentinel. Converting that Tehran-midnight to UTC shifts the event to the
PREVIOUS day 20:30, which is the wrong day AND wrong time for the majors
(NFP/CPI/Unemployment). So:

- rows with a real local time  -> convert local->UTC normally (these are accurate)
- rows at local 00:00 / 23:59  -> treat the LOCAL date as the event date and place
  it at a sensible UTC release slot on that date: afternoon (18:30 UTC) for
  FOMC/rate-decision events, else 12:30 UTC (the dominant ~08:30-ET data slot).
  Day-accurate; hour approximate for the date-only subset (flagged to the user).
"""
import csv, re, sys
from collections import Counter
from datetime import datetime, timezone, time

INVERSE = ("unemployment rate","jobless claim","unemployment claim",
           "continuing claim","initial jobless","misery index")
PM_KEYS = ("fomc","federal funds","rate decision","rate statement","press conference",
           "official bank rate","main refinancing","cash rate","monetary policy")

def to_num(s):
    if s is None: return None
    s = s.strip().replace(",","").replace("%","")
    if s=="" or s in ("-",): return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([KkMmBbTt]?)$", s)
    if not m: return None
    v=float(m.group(1)); mult={"k":1e3,"m":1e6,"b":1e9,"t":1e12}.get(m.group(2).lower(),1.0)
    return v*mult

def bias(event,actual,forecast):
    a,f=to_num(actual),to_num(forecast)
    if a is None or f is None: return 0
    if abs(a-f)<1e-12: return 0
    pol=-1 if any(k in event.lower() for k in INVERSE) else 1
    return (1 if a>f else -1)*pol

def main():
    src,out=sys.argv[1],sys.argv[2]
    rows=[]; dateonly=0; realtime=0
    with open(src,newline="",encoding="utf-8",errors="replace") as fh:
        for x in csv.DictReader(fh):
            dt=(x.get("DateTime") or "").strip()
            if not dt: continue
            try: d=datetime.fromisoformat(dt)
            except Exception: continue
            ev=(x.get("Event") or "").strip().replace(","," ")
            ccy=(x.get("Currency") or "").strip()
            if not ccy or not ev: continue
            hm=(d.hour,d.minute)
            if hm==(0,0) or hm==(23,59):           # date-only sentinel
                slot=time(18,30) if any(k in ev.lower() for k in PM_KEYS) else time(12,30)
                u=datetime.combine(d.date(),slot,tzinfo=timezone.utc)
                dateonly+=1
            else:
                u=d.astimezone(timezone.utc); realtime+=1
            rows.append((u,ccy,ev,(x.get("Actual") or "").strip(),
                         (x.get("Forecast") or "").strip(),
                         bias(ev,x.get("Actual"),x.get("Forecast"))))
    rows.sort(key=lambda t:t[0])
    seen,outrows=set(),[]
    for u,ccy,ev,act,fc,b in rows:
        k=(u,ccy,ev)
        if k in seen: continue
        seen.add(k)
        outrows.append((u.strftime("%Y.%m.%d %H:%M"),ccy,ev,act,fc,str(b)))
    with open(out,"w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh); w.writerow(["datetime_utc","ccy","event","actual","forecast","ccy_bias"])
        w.writerows(outrows)
    yrs=Counter(r[0][:4] for r in outrows)
    print(f"wrote {len(outrows)} events -> {out}  (real-time rows: {realtime}, date-only reconstructed: {dateonly})")
    print("per year:",dict(sorted(yrs.items())))

if __name__=="__main__": main()
