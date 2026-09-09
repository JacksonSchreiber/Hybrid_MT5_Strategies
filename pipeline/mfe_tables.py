#!/usr/bin/env python3
"""mfe_tables.py — Item 2 (coach 2026-09-08): MFE + recovery tables from the study
journals (data/study/mfe/*.study.csv), per strategy and per regime. Pre-registered:
no optimization, no cell selection — just the counts.

MFE table    : % of taken signals whose max-favorable-excursion (mfe_r) reaches
               +0.5/+1/+1.5/+2/+3 R and full TP (terminal==TP) before the -1R stop.
Recovery tbl : of trades that reached +X R then traded back below entry (dipped &
               pre_dip_r>=X), what % subsequently reached +1R (post_dip_r>=1) / hit TP
               vs stopped out.
"""
import csv, glob, sys
from collections import defaultdict

STUDY = sys.argv[1] if len(sys.argv) > 1 else "data/study/mfe/*.study.csv"
MFE_TH = [0.5, 1.0, 1.5, 2.0, 3.0]
REC_X = [0.25, 0.5, 0.75, 1.0]


def load():
    rows = []
    for f in glob.glob(STUDY):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            dec = (r.get("decision") or "").strip()
            if dec not in ("approved", "approved_pending"):
                continue
            if (r.get("r_multiple") or "").strip() == "":     # not closed
                continue
            def fnum(k):
                v = (r.get(k) or "").strip()
                try: return float(v)
                except ValueError: return None
            rows.append({
                "strategy": (r.get("strategy") or "?").strip(),
                "regime": (r.get("regime") or "").strip() or "NONE",
                "mfe_r": fnum("mfe_r") or 0.0,
                "pre": fnum("pre_dip_r") or 0.0,
                "post": fnum("post_dip_r") or 0.0,
                "dipped": (r.get("dipped") or "0").strip() == "1",
                "term": (r.get("terminal") or "").strip(),
            })
    return rows


def pct(a, b):
    return f"{100.0*a/b:4.0f}%" if b else "   -"


def mfe_block(title, rows):
    L = [f"### MFE — {title}", "| group | n | " +
         " | ".join(f"+{t}R" for t in MFE_TH) + " | TP |",
         "|" + "---|" * (len(MFE_TH) + 3)]
    def row(name, rs):
        n = len(rs)
        cells = [pct(sum(1 for x in rs if x["mfe_r"] >= t), n) for t in MFE_TH]
        tp = pct(sum(1 for x in rs if x["term"] == "TP"), n)
        L.append(f"| {name} | {n} | " + " | ".join(cells) + f" | {tp} |")
    row("ALL", rows)
    return L, row


def recovery_block(title, rows):
    L = [f"### Recovery — {title} (of dipped trades that reached +X, then went below entry)",
         "| reached +X | n | →reached +1R | →hit TP | →stopped |",
         "|---|---|---|---|---|"]
    def row(name, rs):
        for x in REC_X:
            sub = [r for r in rs if r["dipped"] and r["pre"] >= x]
            n = len(sub)
            to1 = pct(sum(1 for r in sub if r["post"] >= 1.0), n)
            totp = pct(sum(1 for r in sub if r["term"] == "TP"), n)
            tosl = pct(sum(1 for r in sub if r["term"] == "SL"), n)
            L.append(f"| {name} +{x}R | {n} | {to1} | {totp} | {tosl} |")
    return L, row


def main():
    rows = load()
    print(f"# Item 2 — MFE + Recovery tables\n\n_{len(rows)} taken, closed detector "
          f"signals across the fleet. Model-4 ticks, blind-approve, original levels._\n")
    str016 = sorted({r["strategy"] for r in rows})
    regs = ["TREND_UP", "TREND_DOWN", "CHOP", "NONE"]

    # MFE table
    Lm, addm = mfe_block("overall / by strategy / by regime", rows)
    for s in str016:
        addm(f"strat:{s}", [r for r in rows if r["strategy"] == s])
    for g in regs:
        rs = [r for r in rows if r["regime"] == g]
        if rs: addm(f"regime:{g}", rs)
    print("\n".join(Lm) + "\n")

    # Recovery table
    Lr, addr = recovery_block("overall / by strategy / by regime", rows)
    addr("ALL", rows)
    for s in str016:
        addr(f"strat:{s}", [r for r in rows if r["strategy"] == s])
    for g in regs:
        rs = [r for r in rows if r["regime"] == g]
        if rs: addr(f"reg:{g}", rs)
    print("\n".join(Lr))


if __name__ == "__main__":
    main()
