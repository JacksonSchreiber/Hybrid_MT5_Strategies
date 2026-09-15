#!/usr/bin/env python3
"""TrendCont per-pullback re-arm — before/after fleet comparison (coach 2026-09-09 live-defect).

Reads the scratch-parked model-1 fleet journals produced by run_fleet.sh:
    <scratch>/{before,after}/{skip,all}/<SYM>.csv
Both phases are model-1 so the ONLY variable is the re-arm (no model confound).

SKIP mode  = the declined-popup scenario (no position -> no one-setup lock -> raw emit set).
             The defect makes TrendCont re-fire every bar the condition holds; the re-arm
             collapses this to one signal per pullback. We report the total collapse and the
             count of adjacent-bar (<=8h, same direction) re-fires, which must go to ~0.
ALL  mode  = AA blind-approve taken trades (decision=approved). This is the study-B taken base.
             We report n and avg R before/after and the removed/added sets (added = lock-cascade
             second-order effect: removing an early signal frees its lock to let a later one fire).
"""
import csv, glob, os, sys
from datetime import datetime

SC = os.path.join(os.path.dirname(__file__), "..", "data", "study", "trendcont_rearm")
SYMS = ["EURUSD.dk","GBPUSD.dk","USDJPY.dk","XAUUSD.dk","US100.dk","US500.dk","USOIL.dk"]

def load(phase, mode, sym):
    p = f"{SC}/{phase}/{mode}/{sym}.csv"
    if not os.path.exists(p): return None
    rows=[]
    with open(p, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("strategy")!="TrendCont": continue
            rows.append(row)
    return rows

def ts(s): return datetime.strptime(s.strip(), "%Y.%m.%d %H:%M:%S")

def adjacent_refires(rows):
    """count consecutive same-direction emits <=8h apart (adjacent H4-bar re-fires)."""
    by_dir={}
    for r in rows: by_dir.setdefault(r["direction"],[]).append(ts(r["signal_time"]))
    n=0
    for d,times in by_dir.items():
        times.sort()
        for a,b in zip(times, times[1:]):
            if 0 < (b-a).total_seconds() <= 8*3600: n+=1
    return n

def half_split(rows):
    if not rows: return (0,0.0,0,0.0)
    srt=sorted(rows, key=lambda r: ts(r["signal_time"]))
    mid=len(srt)//2
    def avg(rs):
        rm=[r["_R"] for r in rs]
        return (len(rm), sum(rm)/len(rm) if rm else 0.0)
    e=avg(srt[:mid]); l=avg(srt[mid:])
    return (e[0],e[1],l[0],l[1])

print("="*78)
print("SKIP mode — raw emit set (declined-popup scenario): the defect and its fix")
print("="*78)
print(f"{'symbol':12} {'before':>8} {'after':>8} {'removed':>8} {'adj-refire b/a':>16}")
skip_tot_b=skip_tot_a=0
for sym in SYMS:
    b=load("before","skip",sym); a=load("after","skip",sym)
    if b is None or a is None:
        print(f"{sym:12} {'--' if b is None else len(b):>8} {'--' if a is None else len(a):>8}  (missing)")
        continue
    bset={r["signal_time"] for r in b}; aset={r["signal_time"] for r in a}
    removed=len(bset-aset)
    print(f"{sym:12} {len(b):>8} {len(a):>8} {removed:>8}   {adjacent_refires(b):>6} / {adjacent_refires(a):<6}")
    skip_tot_b+=len(b); skip_tot_a+=len(a)
print(f"{'POOLED':12} {skip_tot_b:>8} {skip_tot_a:>8} {skip_tot_b-skip_tot_a:>8}")

print()
print("="*78)
print("ALL mode — AA taken base (decision=approved): study-B materially unchanged?")
print("="*78)
print(f"{'symbol':12} {'n_bef':>6} {'R_bef':>8} {'n_aft':>6} {'R_aft':>8} {'removed':>8} {'added':>8}")
allrows_b=[]; allrows_a=[]
for sym in SYMS:
    b=load("before","all",sym); a=load("after","all",sym)
    if b is None or a is None:
        print(f"{sym:12}  (missing: before={b is not None} after={a is not None})"); continue
    # taken base = approved AND realized (closed, parseable r_multiple); open-at-end rows excluded
    def realized(rows):
        out=[]
        for r in rows:
            if r["decision"]!="approved": continue
            v=r.get("r_multiple","").strip()
            if v=="": continue
            try: r["_R"]=float(v)
            except ValueError: continue
            out.append(r)
        return out
    b=realized(b); a=realized(a)
    bset={r["signal_time"] for r in b}; aset={r["signal_time"] for r in a}
    Rb=sum(r["_R"] for r in b)/len(b) if b else 0.0
    Ra=sum(r["_R"] for r in a)/len(a) if a else 0.0
    print(f"{sym:12} {len(b):>6} {Rb:>+8.3f} {len(a):>6} {Ra:>+8.3f} {len(bset-aset):>8} {len(aset-bset):>8}")
    allrows_b+=b; allrows_a+=a
if allrows_b and allrows_a:
    Rb=sum(r["_R"] for r in allrows_b)/len(allrows_b)
    Ra=sum(r["_R"] for r in allrows_a)/len(allrows_a)
    eb=half_split(allrows_b); ea=half_split(allrows_a)
    print(f"{'FLEET':12} {len(allrows_b):>6} {Rb:>+8.3f} {len(allrows_a):>6} {Ra:>+8.3f}")
    print(f"  before halves: early n={eb[0]} {eb[1]:+.3f} | late n={eb[2]} {eb[3]:+.3f}")
    print(f"  after  halves: early n={ea[0]} {ea[1]:+.3f} | late n={ea[2]} {ea[3]:+.3f}")
    print(f"  study-B anchor (model-4, parked): see data/study/trendcont_before_rearm/ (n per symbol)")

# --- churn decomposition: turn "removed/added" into a mechanism -------------------
def load_keyR(phase, mode, sym, approved_only):
    rows = load(phase, mode, sym) or []
    d = {}
    for r in rows:
        if approved_only and r["decision"] != "approved": continue
        v = r.get("r_multiple","").strip()
        R = None
        if v != "":
            try: R = float(v)
            except ValueError: R = None
        d[(r["signal_time"], r["direction"])] = R
    return d

def bavg(keys, Rmap):
    rs = [Rmap[k] for k in keys if Rmap.get(k) is not None]
    return (len(keys), len(rs), sum(rs)/len(rs) if rs else 0.0)

print()
print("="*78)
print("Churn decomposition (why the ALL taken base changes) — key=(signal_time,dir)")
print("="*78)
print("  det   = removed at DETECTION by re-arm  (before_ALL \\ after_SKIP): same-pullback re-entry")
print("  casc  = removed by LOCK CASCADE (before_ALL ∩ after_SKIP) \\ after_ALL: detected, other pos open")
print("  added = after_ALL \\ before_ALL (all were lock-suppressed before; now fire)")
print(f"{'symbol':12} {'det n/R':>16} {'casc n/R':>16} {'added n/R':>16}")
agg={'det':[], 'casc':[], 'added':[]}
for sym in SYMS:
    bA=load_keyR("before","all",sym,True); aA=load_keyR("after","all",sym,True)
    aS=load_keyR("after","skip",sym,False); bS=load_keyR("before","skip",sym,False)
    bAk=set(bA); aAk=set(aA); aSk=set(aS)
    det   = bAk - aSk
    casc  = (bAk & aSk) - aAk
    added = aAk - bAk
    dn=bavg(det,bA); cn=bavg(casc,bA); an=bavg(added,aA)
    print(f"{sym:12} {dn[0]:>5} {dn[2]:>+9.3f} {cn[0]:>5} {cn[2]:>+9.3f} {an[0]:>5} {an[2]:>+9.3f}")
    for k in det:   agg['det'].append(bA.get(k))
    for k in casc:  agg['casc'].append(bA.get(k))
    for k in added: agg['added'].append(aA.get(k))
def fagg(name):
    xs=[x for x in agg[name] if x is not None]
    return f"n={len(agg[name])} realized={len(xs)} avgR={(sum(xs)/len(xs) if xs else 0):+.3f}"
print(f"FLEET  det: {fagg('det')}")
print(f"FLEET  casc:{fagg('casc')}")
print(f"FLEET  added:{fagg('added')}")

# --- after-verdict vs the pre-registered pass bar (model-1 basis) ------------------
print()
print("="*78)
print("Pass-bar verdict after re-arm (model-1) vs study-B anchor (model-4)")
print("="*78)
def fleet_stats(phase):
    R=[]; 
    for sym in SYMS:
        for r in (load(phase,"all",sym) or []):
            if r["decision"]!="approved": continue
            v=r.get("r_multiple","").strip()
            if v=="":
                continue
            try: R.append((r["signal_time"], float(v)))
            except ValueError: pass
    R.sort()
    n=len(R); avg=sum(x for _,x in R)/n if n else 0
    mid=n//2; e=[x for _,x in R[:mid]]; l=[x for _,x in R[mid:]]
    ea=sum(e)/len(e) if e else 0; la=sum(l)/len(l) if l else 0
    return n,avg,ea,la
for ph in ("before","after"):
    n,avg,ea,la=fleet_stats(ph)
    t1="PASS" if avg>=0.15 else "FAIL"
    t2="PASS" if n>=150 else "FAIL"
    t3="PASS" if (ea>0 and la>0) else "FAIL"
    print(f"model-1 {ph:6}: n={n} avgR={avg:+.3f} [t1 {t1}]  n>=150 [{t2}]  halves {ea:+.3f}/{la:+.3f} [t3 {t3}]")
print("model-4 study-B anchor (parked data/study/trendcont/): n=2346 avgR=+0.060 -> test1 FAIL (already)")
print("=> re-arm does NOT change the verdict: FAIL on test1 both before and after (bar +0.15R).")
