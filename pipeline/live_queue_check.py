#!/usr/bin/env python3
"""live_queue_check.py — validate a Phase-3 live file-queue root (docs/live-queue-schema.md).

    python3 pipeline/live_queue_check.py --root /mnt/c/.../Common/Files/live_selftest --symbol EURUSD.dk [--expect-selftest] [--expect-live-cols]

Checks (stdlib only; exit 1 on any FAIL):
  * every *.json under signals/ acks/ positions/ state/ + heartbeat parses, has schema_version==1 and the
    required keys for its kind; files are ASCII; no *.tmp older than 60 s anywhere
  * tasks/done/*.json  <->  acks/<task_id>.json  1:1 (every claimed task acked; no ack without a task)
  * audit_<SYM>.log: one `executed` line per task_id at most (no duplicate executions); pipes parse
  * ack.result in {accepted,rejected,expired}; ack.reason prefix in the documented enum
  * delays.csv: `implicit` in {0,1}; a torn LAST line is tolerated (§11-4), any other malformed row fails
  * journal: skip_reason 8 rows exist when signals expired; `auto`/`live` columns when --expect-live-cols
  * --expect-selftest: the deliberately illegal tasks were rejected with their named reasons, the
    duplicate task_id produced a `duplicate` audit line and exactly one ack, >=1 approve accepted,
    >=1 skip accepted, >=1 delay accepted, >=1 code-8 expiry, >=1 election auto-skip (if the window has one)
"""
import argparse, csv, json, os, re, sys, time, glob

REQ = {
 "signal": ["schema_version","ea_build","symbol","signal_id","status","signal_time","decision_bar","strategy",
            "direction","levels","true_orig","rr","sizing","regime","decision_class","protocol_text","events",
            "election_gate","exposure","deadline","max_age_bars","delay_count","implicit_streak","entry_modes",
            "overlay","bars_h4","bars_d1","asset_class","sigtime_text","session","trading_enabled"],
 "ack":    ["schema_version","task_id","symbol","verb","result","reason","executed_at","refs"],
 "heartbeat": ["schema_version","symbol","status","ts","account_login","equity","balance","trading_enabled",
               "terminal_trade_allowed","mql_trade_allowed","aggregate_risk_to_stop","open_positions","last_signal_id","ea_build"],
 "parked": ["schema_version","sid","delay_count","implicit_streak","published_bar","cand"],
}
ACK_RESULTS = {"accepted","rejected","expired"}
REASON_ENUM = ["ok","schema_version_unsupported","malformed_json","bad_task_id","bad_params","unknown_verb","symbol_mismatch",
  "unknown_signal","signal_not_open","unknown_position","position_closed","already_filled","not_pending","stale_task","restart_during_execution",
  "trading_disabled","events_not_loaded","election_gate","setup_lock","geom_invalid","rr_below_floor","stop_too_tight",
  "lots_zero","ftmo_daily_headroom","ftmo_max_headroom","be_floor","be_stops_level","be_would_loosen","ratchet_not_banked",
  "ratchet_already_used","ratchet_no_tp1","ratchet_not_past_tp1","ratchet_would_loosen","min_lot_split","order_failed","weekend_flat",
  # live custom plans (docs/live_plans.md, trader 2026-10-12): plan_add / set_sl / close_part
  "ok_unchanged","ok_unconfirmed","not_a_plan","below_min_lot","sl_through_price","beyond_signal_stop","would_loosen","tp_invalid",
  "risk_over_1R","spread_too_wide"]
SIGNAL_STATUS = {"open","approved","approved_pending","skipped","auto_skipped","expired","rejected"}

fails=[]; warns=[]; info=[]
def fail(m): fails.append(m)
def warn(m): warns.append(m)

def load_json(path, kind):
    try:
        raw=open(path,"rb").read()
        if any(b>0x7E for b in raw): fail(f"{os.path.basename(path)}: non-ASCII byte (files must be ASCII/\\u-escaped)")
        d=json.loads(raw.decode("ascii","replace"))
    except Exception as e:
        fail(f"{os.path.basename(path)}: JSON parse error: {e}"); return None
    if d.get("schema_version")!=1: fail(f"{os.path.basename(path)}: schema_version {d.get('schema_version')!r} != 1")
    for k in REQ[kind]:
        if k not in d: fail(f"{os.path.basename(path)}: missing key {k!r}")
    return d

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",required=True); ap.add_argument("--symbol",required=True)
    ap.add_argument("--expect-selftest",action="store_true"); ap.add_argument("--expect-live-cols",action="store_true")
    a=ap.parse_args(); R=a.root; S=a.symbol
    if not os.path.isdir(R): print(f"FAIL: root {R} missing"); return 1
    # --- tmp leftovers ---
    now=time.time()
    for p in glob.glob(os.path.join(R,"**","*.tmp"),recursive=True):
        if now-os.path.getmtime(p)>60: fail(f"stale tmp: {os.path.relpath(p,R)}")
    # --- signals ---
    sigs={}
    for p in sorted(glob.glob(os.path.join(R,"signals","*.json"))):
        d=load_json(p,"signal")
        if not d: continue
        if d["status"] not in SIGNAL_STATUS: fail(f"{os.path.basename(p)}: bad status {d['status']!r}")
        if d["symbol"]!=S: fail(f"{os.path.basename(p)}: symbol {d['symbol']}!={S}")
        if not (isinstance(d["bars_h4"],list) and len(d["bars_h4"])>=200): fail(f"{os.path.basename(p)}: bars_h4 too short ({len(d.get('bars_h4',[]))})")
        for ev in d["events"]:
            for k in ("t_utc","hours_until","ccy","name","cls","label","binding"):
                if k not in ev: fail(f"{os.path.basename(p)}: event missing {k}")
        sigs[d["signal_id"]]=d
    st={}
    for d in sigs.values(): st[d["status"]]=st.get(d["status"],0)+1
    info.append(f"signals: {len(sigs)} {st}")
    # --- tasks/done <-> acks ---
    done={}
    for p in glob.glob(os.path.join(R,"tasks","done","*.json")):
        try: d=json.load(open(p))
        except Exception: d={"task_id":"malformed-"+os.path.basename(p)[:-5]}
        done[d.get("task_id","?")]=d
    acks={}
    for p in glob.glob(os.path.join(R,"acks","*.json")):
        d=load_json(p,"ack")
        if not d: continue
        acks[d["task_id"]]=d
        if d["result"] not in ACK_RESULTS: fail(f"ack {d['task_id']}: bad result {d['result']!r}")
        if not any(d["reason"]==r or d["reason"].startswith(r+":") for r in REASON_ENUM): fail(f"ack {d['task_id']}: reason {d['reason']!r} not in enum")
        if os.path.basename(p)!=d["task_id"]+".json": fail(f"ack file name != task_id: {os.path.basename(p)}")
    pending=glob.glob(os.path.join(R,"tasks","*.json"))
    if pending: warn(f"{len(pending)} task(s) still pending in tasks/ (unconsumed at stop)")
    for tid in done:
        if tid not in acks and not tid.startswith("malformed-"): fail(f"claimed task without ack: {tid}")
    for tid in acks:
        if tid not in done and not tid.startswith("malformed-"): fail(f"ack without a claimed task: {tid}")
    info.append(f"tasks done: {len(done)}  acks: {len(acks)}  results: { {r:sum(1 for x in acks.values() if x['result']==r) for r in ACK_RESULTS} }")
    # --- audit ---
    exec_seen={}; dup_lines=0; audit_rows=0
    ap_=os.path.join(R,f"audit_{S}.log")
    if os.path.exists(ap_):
        for line in open(ap_,encoding="ascii",errors="replace"):
            line=line.rstrip("\r\n")
            if not line: continue
            f=line.split("|")
            if len(f)!=9: fail(f"audit line has {len(f)} fields: {line[:80]}"); continue
            audit_rows+=1
            ev,tid=f[2],f[3]
            if ev=="executed":
                exec_seen[tid]=exec_seen.get(tid,0)+1
            if ev=="duplicate": dup_lines+=1
        for tid,n in exec_seen.items():
            if n>1: fail(f"task {tid} executed {n} times")
    else: fail("audit log missing")
    info.append(f"audit rows: {audit_rows}  executed(unique): {len(exec_seen)}  duplicate lines: {dup_lines}")
    # --- heartbeat ---
    hb=os.path.join(R,f"heartbeat_{S}.json")
    if os.path.exists(hb): load_json(hb,"heartbeat")
    else: fail("heartbeat missing")
    # --- state ---
    for p in glob.glob(os.path.join(R,"state",S,"parked.json")): load_json(p,"parked")
    # --- journals ---
    jn=sorted(p for p in glob.glob(os.path.join(R,"journal",f"{S}_*.csv")) if not any(x in p for x in (".actions.",".delays.",".inv.",".part.")))
    if a.expect_live_cols:
        # E3: monthly rotation - every live journal is <SYM>_YYYYMM.csv, and the header carries the 5 live columns
        for p in jn:
            if not re.fullmatch(rf"{re.escape(S)}_\d{{6}}\.csv", os.path.basename(p)): fail(f"journal not monthly-named: {os.path.basename(p)}")
            with open(p,newline="",encoding="ascii",errors="replace") as f: hdr=f.readline().rstrip("\r\n").split(",")
            if hdr[-24:-19]!=["live","account_id","risk_pct_gate","risk_mult_applied","auto"]: fail(f"journal {os.path.basename(p)} header lacks live columns: {hdr[-24:-19]}")
            if hdr[-19:]!=["reject_reason","entry_mode","tranche","full_lots","fill_time","parent_signal_id","bars_to_stop","inv_slip_r",
                           "short_raise","short_raise_time","short_raise_px","short_raise_stop","short_raise_from","short_raise_vol",
                           "reoffer","held_minutes","spread_r_at_signal","spread_r_at_offer","drift_r"]:
                fail(f"journal {os.path.basename(p)} ends {hdr[-19:]!r}, expected reject_reason + staged + Inverse + short-raise + re-offer columns")
            if len(hdr)!=70: fail(f"journal {os.path.basename(p)} has {len(hdr)} columns, expected 70 (46 tester + 5 live + reject_reason + 4 staged + 3 Inverse + 6 short raise + 5 re-offer)")
        if not jn: fail("no monthly live journal found")
        for p in glob.glob(os.path.join(R,"journal","*.part.csv")): fail(f"live journal dir has a .part file (tester path leaked): {os.path.basename(p)}")
    skip8=skip2auto=0; all_rows=[]
    for p in jn:
        rows=list(csv.DictReader(open(p,encoding="ascii",errors="replace"))); all_rows+=rows; f8=f2=0
        for r in rows:
            if r.get("decision")=="skipped" and r.get("skip_reason")=="8": skip8+=1; f8+=1
            if r.get("decision")=="skipped" and r.get("skip_reason")=="2" and r.get("auto","0")=="1": skip2auto+=1; f2+=1
            if a.expect_live_cols:
                if r.get("live")!="1": fail(f"journal row sig {r.get('signal_id')} live!=1")
                if not r.get("account_id"): fail(f"journal row sig {r.get('signal_id')} account_id empty")
                # coach 2026-09-23: a rejected row is the input to the held-setup counterfactual, so it must carry
                # the full plan (levels + the size it would have traded) and say why it was refused.
                if r.get("decision")=="rejected":
                    miss=[c for c in ("entry","sl","tp1","lots") if not (r.get(c) or "").strip()]
                    if miss: fail(f"journal row sig {r.get('signal_id')} rejected but {'/'.join(miss)} empty")
                    if (r.get("lots") or "0")=="0.00": fail(f"journal row sig {r.get('signal_id')} rejected with lots 0.00")
                    if not (r.get("reject_reason") or "").strip(): fail(f"journal row sig {r.get('signal_id')} rejected with no reject_reason")
        info.append(f"journal {os.path.basename(p)}: {len(rows)} rows, skip8={f8}, skip2(auto)={f2}")
    info.append(f"journal totals: {len(all_rows)} rows, skip8={skip8}, skip2(auto)={skip2auto}")
    if st.get("expired",0) and skip8==0: fail("signals expired but no skip_reason=8 journal row")
    if a.expect_live_cols and st.get("auto_skipped",0) and not any(r.get("auto","0")=="1" and r.get("skip_reason") in ("2","10") for r in all_rows): fail("signals auto_skipped but no journal row with skip_reason 2|10 & auto=1")
    # --- fill_now (trader ruling 2026-09-30): an ACCEPTED fill must have produced a position on the same row ---
    if a.expect_selftest:
        fn = [p_ for p_ in glob.glob(os.path.join(R, "acks", "st-*-fill-now.json"))]
        if not fn: fail("selftest: no fill_now task was issued (the stage never found a resting pending order)")
        for p_ in fn:
            ak = json.load(open(p_)); sid = str(ak.get("signal_id") or re.search(r"st-(\d+)-fill", p_).group(1))
            if ak.get("result") == "accepted":
                row = next((r for r in all_rows if r.get("signal_id") == sid), None)
                if not row: fail(f"selftest: fill_now accepted for #{sid} but no journal row")
                elif not row.get("posid") or row.get("posid") == "0": fail(f"selftest: fill_now accepted for #{sid} but the row has no position")
                else: info.append(f"fill_now #{sid}: accepted -> position {row.get('posid')} at {row.get('entry')} ({row.get('lots')} lots)")
            else: info.append(f"fill_now #{sid}: {ak.get('result')} - {ak.get('reason')} (a refusal is legal; it must name a reason)")
            if ak.get("result") != "accepted" and not ak.get("reason"): fail(f"selftest: fill_now #{sid} refused without a reason")

    # --- delays (torn last line tolerated) ---
    for p in glob.glob(os.path.join(R,"journal",f"{S}_*.delays.csv")):
        lines=open(p,encoding="ascii",errors="replace").read().split("\n")
        hdr=lines[0].split(","); n_impl=0; body=[l for l in lines[1:] if l.strip()]
        for i,l in enumerate(body):
            f=l.split(",")
            if len(f)!=len(hdr):
                if i==len(body)-1: warn(f"delays: torn last line tolerated ({len(f)} fields)"); continue
                fail(f"delays: malformed row {i+2}: {l[:60]}"); continue
            if "implicit" in hdr:
                v=f[hdr.index("implicit")]
                if v not in ("0","1"): fail(f"delays: implicit={v!r} row {i+2}")
                if v=="1": n_impl+=1
        info.append(f"delays {os.path.basename(p)}: {len(body)} rows, implicit=1: {n_impl}")
    # --- self-test expectations ---
    if a.expect_selftest:
        exp={"st-bad-schema":"schema_version_unsupported","st-bad-verb":"unknown_verb","st-unknown-sig":"unknown_signal",
             "st-unknown-pos":"unknown_position","st-stale-task1":"stale_task"}
        for tid,reason in exp.items():
            ak=acks.get(tid)
            if not ak: fail(f"selftest: no ack for {tid}")
            elif ak["result"]=="accepted" or not ak["reason"].startswith(reason): fail(f"selftest: {tid} -> {ak['result']}/{ak['reason']} (expected {reason})")
        mal=[t for t in acks if t.startswith("malformed-")]
        if not mal: fail("selftest: malformed task produced no ack")
        if dup_lines<1: fail("selftest: duplicate task_id produced no `duplicate` audit line")
        if len([1 for t in acks if t=="st-1-approve"])!=1: fail("selftest: st-1-approve should have exactly one ack")
        def cnt(pred): return sum(1 for x in acks.values() if pred(x))
        if cnt(lambda x:x["verb"]=="approve" and x["result"]=="accepted")<1: fail("selftest: no accepted approve")
        if cnt(lambda x:x["verb"]=="skip" and x["result"]=="accepted")<1: fail("selftest: no accepted skip")
        if cnt(lambda x:x["verb"]=="delay" and x["result"]=="accepted")<1: fail("selftest: no accepted delay")
        if skip8<1: fail("selftest: no code-8 expiry")
        # §11-7 aggregate headroom: ordinal 9's approve runs under a 0.1% daily limit and must be refused
        fk=acks.get("st-9-approve-ftmo")
        if fk is None: warn("selftest: FTMO scenario not exercised (fewer than 9 signals published?)")
        elif fk["result"]!="rejected" or not fk["reason"].startswith("ftmo_daily_headroom"): fail(f"selftest: st-9-approve-ftmo -> {fk['result']}/{fk['reason']} (expected rejected/ftmo_daily_headroom)")
        else:
            if not any("|ftmo_reject|" in l for l in open(ap_,encoding="ascii",errors="replace")): fail("selftest: FTMO rejection has no ftmo_reject audit line")
            # §11.9(b) amended 2026-09-21: the refusal is an EA AUTO-REJECT - signal 9 closes as skipped code 10, auto=1, tagged
            if not any("|auto_skip|" in l and "EA_AUTO_REJECT code=10" in l and "|sig:9|" in l for l in open(ap_,encoding="ascii",errors="replace")):
                fail("selftest: FTMO refusal has no EA_AUTO_REJECT auto_skip audit line for sig 9")
            r9=[r for r in all_rows if r.get("signal_id")=="9"]
            if not r9 or r9[-1].get("decision")!="skipped" or r9[-1].get("skip_reason")!="10" or r9[-1].get("auto","0")!="1":
                fail(f"selftest: signal 9 after the FTMO refusal is not journaled skipped/10/auto=1 ({[(r.get('decision'), r.get('skip_reason'), r.get('auto')) for r in r9]})")
        gates={"sl_be":0,"ratchet_tp1":0,"close50":0,"close":0}
        for x in acks.values():
            if x["verb"] in gates: gates[x["verb"]]+=1
        info.append(f"selftest position verbs acked: {gates}")
        for vb in gates:
            if gates[vb]<1: warn(f"selftest: no {vb} task acked (no position lived long enough?)")
        # gate == ack: every `gate` audit line (X=true|false) must agree with that task's ack result
        gate_lines=[l.rstrip("\r\n").split("|") for l in open(ap_,encoding="ascii",errors="replace") if "|gate|" in l]
        for f in gate_lines:
            if len(f)!=9: continue
            tid,verdict=f[3],f[6]
            ak=acks.get(tid)
            if not ak: fail(f"selftest: gate line for {tid} but no ack"); continue
            want="accepted" if verdict.endswith("=true") else "rejected"
            if ak["result"]!=want: fail(f"selftest: {tid} gate {verdict} but ack {ak['result']}/{ak['reason']}")
        # E10 kill switch: OFF -> trading_disabled (and never executed); ON -> the same signal approved
        kk=acks.get("st-5-approve-killed"); ko=acks.get("st-5-approve")
        if kk is None: warn("selftest: kill-switch scenario not exercised (signal 5 not published?)")
        else:
            if kk["result"]!="rejected" or not kk["reason"].startswith("trading_disabled"): fail(f"selftest: st-5-approve-killed -> {kk['result']}/{kk['reason']} (expected rejected/trading_disabled)")
            if "st-5-approve-killed" in exec_seen: fail("selftest: st-5-approve-killed was EXECUTED despite the kill switch")
            if ko is None: fail("selftest: kill switch restored but st-5-approve never acked")
            elif ko["reason"].startswith("trading_disabled"): fail("selftest: st-5-approve refused trading_disabled after the switch was restored")
        # C2 risk multiplier: journal risk_mult_applied=0.500 on the halved approve
        hk=acks.get("st-13-approve-half")
        if hk is None: warn("selftest: risk_mult scenario not exercised (signal 13 not published?)")
        elif hk["result"]=="accepted":
            r13=[r for r in all_rows if r.get("signal_id")=="13"]
            if not r13: fail("selftest: st-13-approve-half accepted but no journal row for signal 13")
            elif r13[0].get("risk_mult_applied")!="0.500": fail(f"selftest: signal 13 risk_mult_applied={r13[0].get('risk_mult_applied')} (expected 0.500)")
            else:
                others=[float(r["lots"]) for r in all_rows if r.get("decision")=="approved" and r.get("risk_mult_applied")=="1.000" and r.get("signal_id")!="13"]
                if others and float(r13[0]["lots"])>=min(others): warn(f"selftest: halved lots {r13[0]['lots']} not below the smallest full-risk lots {min(others)} (different stop distances?)")
        else: warn(f"selftest: st-13-approve-half not accepted ({hk['reason']}) - risk_mult column not verified")
        # §11-7 aggregate term + restart drill across months
        aggl=[l for l in open(ap_,encoding="ascii",errors="replace") if "|agg_check|" in l]
        if not aggl: warn("selftest: agg_check not exercised (no position opened?)")
        for l in aggl:
            if "|ok|" not in l: fail(f"selftest: aggregate risk check failed: {l.strip()[:140]}")
        rcl=[l for l in open(ap_,encoding="ascii",errors="replace") if "|restart_check|" in l]
        if not rcl: fail("selftest: no restart_check audit line")
        else:
            m=re.search(r"rows (\d+)->(\d+)",rcl[-1])
            if "|ok|" not in rcl[-1]: fail(f"selftest: restart drill: {rcl[-1].strip()[:140]}")
            elif not m or int(m.group(1))<8 or m.group(1)!=m.group(2): fail(f"selftest: restart drill covered too few rows or lost rows: {rcl[-1].strip()[:140]}")
        for vb in ("sl_be","ratchet_tp1"):
            if cnt(lambda x,vb=vb:x["verb"]==vb and x["result"]=="accepted")<1: warn(f"selftest: {vb} accept path not exercised in this window")
        info.append(f"selftest gate lines checked: {len(gate_lines)}; sl_be accepted: {cnt(lambda x:x['verb']=='sl_be' and x['result']=='accepted')}  ratchet_tp1 accepted: {cnt(lambda x:x['verb']=='ratchet_tp1' and x['result']=='accepted')}")
    # --- live custom plans (docs/live_plans.md): the plan scenario + the inverse plan scenario ---
    if a.expect_selftest:
        def ackr(tid): return acks.get(tid)
        pa=[x for t,x in acks.items() if re.fullmatch(r"st-\d+-approve-plan",t)]
        if not pa: fail("selftest: no plan approve was issued (no free signal from #17 on?)")
        acc=[x for x in pa if x["result"]=="accepted"]
        if pa and not acc: fail(f"selftest: no plan approve accepted ({[(x['task_id'],x['reason']) for x in pa]})")
        for x in acc:
            sid=str(x["signal_id"]); row=next((r for r in all_rows if r.get("signal_id")==sid and (r.get("entry_mode") or "").startswith("plan_") and r.get("entry_mode")!="plan_add"),None)
            if not row: fail(f"selftest: plan approve #{sid} accepted but no journal row with a plan_ entry_mode"); continue
            try: lots,full=float(row["lots"]),float(row.get("full_lots") or 0)
            except ValueError: lots,full=0.0,0.0
            if not full or abs(lots-max(0.01,int(full*0.5/0.01+1e-9)*0.01))>0.0051: fail(f"selftest: plan #{sid} lots {row.get('lots')} vs full {row.get('full_lots')} (expected 50%)")
            info.append(f"plan #{sid}: {row.get('entry_mode')} lots {row.get('lots')} of {row.get('full_lots')} tp {row.get('tp')}")
            if (row.get("tp") or "0") not in ("0","0.0","") and float(row.get("tp") or 0)!=0.0: fail(f"selftest: plan #{sid} asked tp2=0 but the row has tp {row.get('tp')}")
            adds=[r for r in all_rows if r.get("signal_id")==sid and r.get("entry_mode")=="plan_add"]
            ak=ackr("st-plan-add")
            if ak is None: warn("selftest: plan_add not exercised (the plan leg closed first?)")
            elif ak["result"]!="accepted": fail(f"selftest: st-plan-add -> {ak['result']}/{ak['reason']} (expected accepted)")
            elif not adds or adds[0].get("tranche")!="5": fail(f"selftest: plan_add accepted but no tranche-5 plan_add journal row for #{sid}")
            elif str(ak["refs"].get("posid"))!=adds[0].get("posid"): fail(f"selftest: plan_add ack refs posid {ak['refs'].get('posid')} != the new row's {adds[0].get('posid')}")
            for tid,want in (("st-plan-add-over","rejected/risk_over_1R"),("st-plan-sl-tight","accepted/ok"),("st-plan-sl-loosen-refused","rejected/would_loosen"),
                             ("st-plan-sl-loosen-ok","accepted/ok"),("st-plan-sl-beyond","rejected/beyond_signal_stop"),("st-plan-bank","accepted/ok"),("st-plan-cut","accepted/ok")):
                ak=ackr(tid); res,rsn=want.split("/")
                if ak is None: warn(f"selftest: {tid} not exercised (the plan leg closed first?)"); continue
                if ak["result"]!=res or not (ak["reason"]==rsn or ak["reason"].startswith(rsn)): fail(f"selftest: {tid} -> {ak['result']}/{ak['reason']} (expected {want})")
            acts=[l for p_ in glob.glob(os.path.join(R,"journal",f"{S}_*.actions.csv")) for l in open(p_,encoding="ascii",errors="replace")]
            if ackr("st-plan-cut") and ackr("st-plan-cut")["result"]=="accepted" and not any(",PLAN_CLOSE," in l for l in acts): fail("selftest: plan cut accepted but no PLAN_CLOSE action row")
            if ackr("st-plan-bank") and ackr("st-plan-bank")["result"]=="accepted" and not any(",PLAN_BANK," in l for l in acts): fail("selftest: close_part accepted but no PLAN_BANK action row")
        ia=[x for t,x in acks.items() if re.fullmatch(r"st-\d+-approve-plan-inv",t)]
        if not ia: warn("selftest: inverse plan not exercised (no free signal after the plan scenario)")
        for x in ia:
            if x["result"]!="accepted": info.append(f"inverse plan #{x['signal_id']}: {x['result']} - {x['reason']}"); continue
            sid=str(x["signal_id"]); row=next((r for r in all_rows if r.get("signal_id")==sid and (r.get("entry_mode") or "").startswith("plan_")),None)
            sig=sigs.get(int(sid)) or {}
            if not row or not row.get("entry_mode","").endswith("_inv"): fail(f"selftest: inverse plan #{sid} accepted but its journal row is not *_inv ({row and row.get('entry_mode')})")
            elif sig and row.get("direction")==sig.get("direction"): fail(f"selftest: inverse plan #{sid} journaled in the signal's own direction {row.get('direction')}")
            chk=[l for l in open(ap_,encoding="ascii",errors="replace") if "|plan_inv_check|" in l and f"sig:{sid} " in l]
            if not chk: warn(f"selftest: inverse plan #{sid} mirror check not reached (closed first?)")
            elif "|ok|" not in chk[-1]: fail(f"selftest: inverse plan mirror check: {chk[-1].strip()[:200]}")
            else: info.append(f"inverse plan #{sid}: mirror check ok ({row.get('direction')} vs signal {sig.get('direction')})")
        # a plan row is never an Inverse parent (coordinator 2026-10-12): no `inverse ... published` line for a plan signal
        plan_sids={str(x["signal_id"]) for x in pa+ia if x["result"]=="accepted"}
        for l in open(ap_,encoding="ascii",errors="replace"):
            f=l.rstrip("\r\n").split("|")
            if len(f)==9 and f[2]=="inverse" and f[6]=="published" and f[5].startswith("sig:") and f[5][4:] in plan_sids:
                fail(f"selftest: an Inverse was published for plan signal {f[5]}")
        pv={v_:sum(1 for x in acks.values() if x["verb"]==v_) for v_ in ("plan_add","set_sl","close_part")}
        info.append(f"selftest plan verbs acked: {pv}")
    # --- order retries + a refused approve keeps its signal open (trader 2026-10-12) ---
    if a.expect_selftest:
        al=[l.rstrip("\r\n") for l in open(ap_,encoding="ascii",errors="replace")]
        a1=acks.get("st-1-approve")
        if a1 is None: fail("selftest: no ack for st-1-approve (the order-retry scenario)")
        elif a1["result"]!="accepted" or int((a1.get("refs") or {}).get("attempts") or 1)!=2:
            fail(f"selftest: st-1-approve (one synthetic broker refusal) -> {a1['result']}/{a1['reason']} attempts={(a1.get('refs') or {}).get('attempts')} (expected accepted on attempt 2)")
        elif not any("|order_retry|" in l and "|sig:1|" in l and "filled on attempt 2" in l for l in al): fail("selftest: no `order_retry ... filled on attempt 2` audit line for sig 1")
        else: info.append("order retry: st-1-approve refused once (synthetic 10021), filled on attempt 2")
        ar=acks.get("st-5-approve-refused")
        if ar is None: warn("selftest: broker-refusal scenario not exercised (signal 5 not published?)")
        else:
            rf=ar.get("refs") or {}
            if ar["result"]!="rejected" or ar["reason"]!="order_failed:10021": fail(f"selftest: st-5-approve-refused -> {ar['result']}/{ar['reason']} (expected rejected/order_failed:10021)")
            if rf.get("signal_open")!=1 or rf.get("attempts")!=4: fail(f"selftest: st-5-approve-refused refs signal_open={rf.get('signal_open')} attempts={rf.get('attempts')} (expected 1 / 4)")
            n_or=sum(1 for l in al if "|order_retry|" in l and "|sig:5|" in l and "|failed|" in l)
            if n_or!=4: fail(f"selftest: {n_or} failed order_retry audit lines for sig 5 (expected 4)")
            ofl=[l for l in al if "|ofail_check|" in l]
            if not ofl: fail("selftest: no ofail_check audit line (the open-after-refusal assertions never ran)")
            elif "|ok|" not in ofl[-1]: fail(f"selftest: refused approve did not leave the signal open: {ofl[-1][:200]}")
            ko=acks.get("st-5-approve")
            if not ko or ko["result"]!="accepted": fail(f"selftest: the re-approve after the refusal was not accepted ({ko and (ko['result'], ko['reason'])})")
            r5=[r for r in all_rows if r.get("signal_id")=="5"]
            if len(r5)!=1 or (r5[0].get("posid") or "0")=="0": fail(f"selftest: signal 5 should have exactly one journal row, with a position ({[(r.get('decision'),r.get('posid')) for r in r5]})")
            else: info.append(f"broker refusal: #5 refused x4 -> still open -> re-approved, position {r5[0].get('posid')}")
    # --- report ---
    print("== live_queue_check ==")
    for m in info: print("  ",m)
    for m in warns: print("  WARN:",m)
    for m in fails: print("  FAIL:",m)
    print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)})")
    return 0 if not fails else 1

if __name__=="__main__": sys.exit(main())
