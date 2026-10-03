"""rule_tags.py - coach 2026-10-03 item 7: every live signal tagged with the rules that touched it, so any grader's results can be split
later (graders do NOT exclude touched signals - that would select the sample).

Tags (all read from the journal + actions files; no EA field needed):
  STAGED        a staged add (tranche 2) went in          ADD_HELD     coach queue A held the add (reject_reason)
  DAY2_CUT      coach queue B closed it (actions)         IMPR_ADD     coach queue I added (tranche 4)
  PYRAMID       the +1.5R add (tranche 3)                 SHORT_RAISE  the shorts stop raise armed (short_raise=1)
  REOFFER       a re-offered spread reject (reoffer=1)    INVERSE      an Inverse signal
  MANUAL_SIZE   Full now / Promote now                    MANUAL_EXIT  a manual close / 50% close / stop edit in the actions file
Written to <root>/web/rule_tags.csv; tags_for(cfg) gives {(symbol, signal_id): "TAG;TAG"} for the graders' 'rules' column.
"""
from __future__ import annotations
import csv, glob, os

from live import common as C

AUTO = ("AUTO_", "DAY2_CUT", "SHORT_RAISE", "PYRAMID", "STAGED")


def tags_for(cfg: dict, rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else C.journal_rows(cfg)
    acts: dict = {}
    for p in glob.glob(os.path.join(cfg["root"], "journal", "*.actions.csv")):
        sym = os.path.basename(p).split("_")[0]
        try:
            with open(p, encoding="ascii", errors="replace", newline="") as fh:
                for a in csv.DictReader(fh): acts.setdefault((sym, a.get("signal_id")), []).append(a.get("action") or "")
        except OSError: pass
    t: dict = {}
    for r in rows:
        k = (r.get("symbol"), r.get("signal_id")); s = t.setdefault(k, set())
        if r.get("decision") not in ("approved", "approved_pending"):
            if r.get("reoffer") == "1": s.add("REOFFER")
            continue
        tr = r.get("tranche") or ""
        if tr == "2": s.add("STAGED")
        if tr == "3": s.add("PYRAMID")
        if tr == "4": s.add("IMPR_ADD")
        rj = r.get("reject_reason") or ""
        if "add held" in rj or "held (gate)" in rj: s.add("ADD_HELD")
        if r.get("short_raise") == "1": s.add("SHORT_RAISE")
        if r.get("reoffer") == "1": s.add("REOFFER")
        if r.get("strategy") == "Inverse": s.add("INVERSE")
        em = r.get("entry_mode") or ""
        if em == "manual_full" or em.endswith("manual_promote"): s.add("MANUAL_SIZE")
    for k, al in acts.items():
        s = t.setdefault(k, set())
        if "DAY2_CUT" in al: s.add("DAY2_CUT")
        if any(a and not a.startswith(AUTO) for a in al): s.add("MANUAL_EXIT")
    return {k: ";".join(sorted(v)) for k, v in t.items()}


def write(cfg: dict) -> int:
    rows = C.journal_rows(cfg); tg = tags_for(cfg, rows)
    first = {}
    for r in rows: first.setdefault((r.get("symbol"), r.get("signal_id")), r)
    p = os.path.join(cfg["root"], "web", "rule_tags.csv"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["symbol", "signal_id", "strategy", "direction", "signal_time", "decision", "rules"])
        for k in sorted(tg, key=lambda k: (first.get(k) or {}).get("signal_time", "")):
            r = first.get(k) or {}
            w.writerow([k[0], k[1], r.get("strategy", ""), r.get("direction", ""), r.get("signal_time", ""), r.get("decision", ""), tg[k]])
    os.replace(p + ".tmp", p)
    return len(tg)
