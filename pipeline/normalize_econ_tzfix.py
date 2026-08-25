#!/usr/bin/env python3
"""Like normalize_econ.py, but repairs the ehsanrs2 archive's DATE-ONLY rows AND
bakes the `class` column (V/W/C/H) from config/event_classes.yaml — the single
source of truth (docs/calendar-gap-coverage-spec.md §3.5). tier0.py and the EA read
that column instead of matching keywords, so the three lists can no longer drift.

Date handling (unchanged): many historical rows store the event at LOCAL 00:00 (or
23:59) — a "no intraday time" sentinel. Converting that Tehran-midnight to UTC shifts
the event to the wrong day, so:
- rows with a real local time -> convert local->UTC normally (accurate)
- rows at local 00:00 / 23:59  -> keep the LOCAL date, place at a sane UTC slot
  (18:30 for FOMC/rate events, else 12:30). Day-accurate; hour approximate.

Also merges config/political_events.csv (§3.7): the hand-curated political / fiscal
weekend layer, placed at each row's date_utc_start with class=W. tier0 expands W rows
into the weekend-hold window.

Usage: normalize_econ_tzfix.py <source.csv> <out.csv>
"""
import csv, re, sys
from collections import Counter
from datetime import datetime, timezone, time
from pathlib import Path

from event_classes import classify   # single classifier (reads config/event_classes.yaml)

INVERSE = ("unemployment rate","jobless claim","unemployment claim",
           "continuing claim","initial jobless","misery index")
PM_KEYS = ("fomc","federal funds","rate decision","rate statement","press conference",
           "official bank rate","main refinancing","cash rate","monetary policy")

POLITICAL_CSV = Path(__file__).resolve().parent.parent / "config" / "political_events.csv"

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

def load_political():
    """Return list of (datetime_utc_str, ccy, event, actual, forecast, bias, cls)."""
    out=[]
    if not POLITICAL_CSV.exists(): return out
    with open(POLITICAL_CSV,newline="",encoding="utf-8") as fh:
        for raw in fh:
            if raw.lstrip().startswith("#") or raw.startswith("date_utc_start"):
                continue
            parts=[p.strip() for p in raw.rstrip("\n").split(",")]
            if len(parts)<5 or not parts[0]: continue
            start,end,ccy,event,cls=parts[0],parts[1],parts[2],parts[3],parts[4]
            try: d=datetime.strptime(start,"%Y.%m.%d %H:%M")
            except ValueError: continue
            out.append((d.strftime("%Y.%m.%d %H:%M"),ccy,event,"","",0,cls or "W"))
    return out

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
            cls,_=classify(ev,ccy)                 # bake the class letter (V/W/C/H or "")
            rows.append((u.strftime("%Y.%m.%d %H:%M"),ccy,ev,(x.get("Actual") or "").strip(),
                         (x.get("Forecast") or "").strip(),
                         bias(ev,x.get("Actual"),x.get("Forecast")),cls))
    rows.extend(load_political())                  # §3.7 political / fiscal layer
    # sort by datetime string (fixed-width YYYY.MM.DD HH:MM sorts chronologically)
    rows.sort(key=lambda t:t[0])
    seen,outrows=set(),[]
    for dt_s,ccy,ev,act,fc,b,cls in rows:
        k=(dt_s,ccy,ev)
        if k in seen: continue
        seen.add(k)
        outrows.append((dt_s,ccy,ev,act,fc,str(b),cls))
    with open(out,"w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh)
        w.writerow(["datetime_utc","ccy","event","actual","forecast","ccy_bias","class"])
        w.writerows(outrows)
    yrs=Counter(r[0][:4] for r in outrows)
    cls_ct=Counter(r[6] or "-" for r in outrows)
    print(f"wrote {len(outrows)} events -> {out}  (real-time: {realtime}, date-only reconstructed: {dateonly}, political merged: {len(load_political())})")
    print("per year:",dict(sorted(yrs.items())))
    print("per class:",dict(sorted(cls_ct.items())))

if __name__=="__main__": main()
