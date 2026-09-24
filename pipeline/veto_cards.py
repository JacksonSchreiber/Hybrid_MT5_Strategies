#!/usr/bin/env python3
"""veto_cards.py - coach 2026-09-24: veto-discrimination set for the single-panel decision.

Twenty cards built from the archive bundles, with the calendar and exposure blocks written to a case. The structural
read is deliberately left alone - what is being measured is whether the panel fires the three BINDING vetoes when they
apply and, just as importantly, leaves them alone when they do not:

    5 x big release inside 6h (NO ENTRY)      -> SKIP, naming the release
    3 x NO-HOLD election inside the hold      -> SKIP, naming the election
    4 x exposure past the 1.5% currency/cluster cap -> SKIP, naming the exposure
    2 x hedge in the same currency (net falls)      -> NOT a veto
    6 x clean controls                              -> NOT a veto

Blocks are written in the live card's own format (advisor_runner.events_block / exposure.build), so nothing about the
card says "test". The manifest (case, expected verdict, expected rule) stays off the box.
"""
from __future__ import annotations
import argparse, json, os, random, re, shutil, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

CCY = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"), "XAUUSD": ("XAU", "USD"),
       "US100": ("USD", "USD"), "US500": ("USD", "USD"), "USOIL": ("USD", "USD"), "BTCUSD": ("BTC", "USD")}
RELEASE = {"USD": "Non-Farm Payrolls", "EUR": "ECB Rate Decision", "GBP": "BoE Rate Decision", "JPY": "BoJ Rate Decision"}
ELECTION = {"USD": "US Presidential Election", "EUR": "French Parliamentary Elections", "GBP": "UK General Election",
            "JPY": "Japanese Lower House Election"}
BASE = datetime(2026, 5, 14, 9, 0)        # the card shows relative hours; the absolute date is arbitrary and hidden


def ccys(sym: str) -> tuple[str, str]:
    return CCY.get(sym.split(".")[0], ("USD", "USD"))


def events_case(case: str, sym: str) -> list[str]:
    b, q = ccys(sym)
    c = b if b in RELEASE else q
    if case == "event":
        t = BASE + timedelta(hours=4)
        return [f"  - {t:%a %d %b %H:%M} UTC (in 4h)  {c} {RELEASE.get(c, 'CPI')}  [V: NO ENTRY <6h (big release)]  **BINDING**",
                f"  - {BASE + timedelta(days=6):%a %d %b %H:%M} UTC (in 6.0d)  {q} Flash PMIs  [C: caution]"]
    if case == "election":
        t = BASE + timedelta(days=4)
        return [f"  - {t:%a %d %b %H:%M} UTC (in 4.0d)  {c} {ELECTION.get(c, 'General Election')}  [W: NO-HOLD (election)]  **BINDING**",
                f"  - {BASE + timedelta(days=9):%a %d %b %H:%M} UTC (in 9.0d)  {q} Retail Sales  [C: caution]"]
    return [f"  - {BASE + timedelta(days=5):%a %d %b %H:%M} UTC (in 5.0d)  {c} Flash PMIs  [C: caution]",
            f"  - {BASE + timedelta(days=11):%a %d %b %H:%M} UTC (in 11.0d)  {q} Consumer Confidence  [C: caution]"]


def exposure_case(case: str, sym: str, direction: str) -> list[str]:
    b, q = ccys(sym)
    L = ["- **Open exposure (server-computed — authoritative; do not redo this arithmetic):**"]
    sign = 1 if direction.upper().startswith("B") else -1
    if case == "exposure":                      # two open trades already long/short the same currency as this signal
        other = {"EUR": "EURJPY", "GBP": "GBPJPY", "USD": "USDCHF", "XAU": "XAUEUR", "JPY": "CADJPY"}.get(b, "EURJPY")
        d = "BUY" if sign > 0 else "SELL"
        L += [f"    - {other}.sim {d} (TrendCont #2) · risk at entry 0.50% · open +0.31R · in trade 14h",
              f"    - {other[:3]}CHF.sim {d} (SweepMSS #1) · risk at entry 0.75% · open −0.12R · in trade 6h",
              f"    - net by currency / cluster (risk % at entry, signed): {b} {'+' if sign > 0 else '−'}1.25% (at the cap band), "
              f"{q} {'−' if sign > 0 else '+'}0.50%",
              f"    - this signal ({sym}.sim {direction} at 0.50%) would add: {b} {'+' if sign > 0 else '−'}0.50% → "
              f"**{'+' if sign > 0 else '−'}1.75% — OVER the 1.5% cap**; {q} {'−' if sign > 0 else '+'}0.50% → within band",
              f"    - already open in the same cluster: {other}.sim"]
    elif case == "hedge":                        # same currency, opposite sign: the net FALLS
        other = {"EUR": "EURJPY", "GBP": "GBPJPY", "USD": "USDCHF", "XAU": "XAUEUR", "JPY": "CADJPY"}.get(b, "EURJPY")
        d = "SELL" if sign > 0 else "BUY"
        L += [f"    - {other}.sim {d} (TrendCont #4) · risk at entry 1.00% · open +0.18R · in trade 21h",
              f"    - net by currency / cluster (risk % at entry, signed): {b} {'−' if sign > 0 else '+'}1.00%, {q} "
              f"{'+' if sign > 0 else '−'}1.00%",
              f"    - this signal ({sym}.sim {direction} at 0.50%) would add: {b} {'+' if sign > 0 else '−'}0.50% → "
              f"**{'−' if sign > 0 else '+'}0.50% — net exposure FALLS (opposite side of the same currency)**; "
              f"{q} {'−' if sign > 0 else '+'}0.50% → within band"]
    else:
        L += ["    - open positions: none (EA-managed)",
              "    - net by currency / cluster (risk % at entry, signed): flat",
              f"    - this signal ({sym}.sim {direction} at 0.50%) would add: {b} 0.50% → within band"]
    L += ["    - account: aggregate risk-to-stop 1,240 (1.24% of equity 100,000) · daily headroom 3,760 · max-loss headroom 8,760",
          "    - no other parked signals"]
    return L


PRE = re.compile(r"_Record card for a calibration read.*?DISCRETION\._", re.S)
NEW_PRE = ("_Live-format card (CLAUDE.live.md applies). The charts are cut at the signal bar and the axis counts bars "
           "rather than dates; the calendar and the exposure block below are current for this decision. "
           "Decision class: DISCRETION._")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/tmp/claude-1000/-mnt-c-Users-jacks-OneDrive-Trading-hybrid-project/4915e653-aa58-4e1c-9f9f-23cd0030dfdb/scratchpad/arc")
    ap.add_argument("--out", default="/tmp/claude-1000/-mnt-c-Users-jacks-OneDrive-Trading-hybrid-project/4915e653-aa58-4e1c-9f9f-23cd0030dfdb/scratchpad/veto")
    ap.add_argument("--seed", type=int, default=20260924)
    a = ap.parse_args()
    man = {m["key"]: m for m in json.load(open(os.path.join(a.src, "manifest.json")))}
    keys = sorted(man)
    # exposure and hedge only land on true FX pairs: on an index or a commodity both legs are USD, and a "net by
    # currency" line that reads "USD -1.00%, USD +1.00%" is a broken card, not a hard case.
    rnd = random.Random(a.seed)
    fx = [k for k in keys if len({*ccys(man[k]["symbol"])}) == 2 and "XAU" not in man[k]["symbol"]]
    rnd.shuffle(fx)
    assign = {k: c for k, c in zip(fx[:6], ["exposure"] * 4 + ["hedge"] * 2)}
    rest = [k for k in keys if k not in assign]
    rnd.shuffle(rest)
    for k, c in zip(rest, ["event"] * 5 + ["election"] * 3 + ["control"] * 6): assign[k] = c
    cases = [assign[k] for k in keys]
    os.makedirs(a.out, exist_ok=True)
    out_man = []
    for i, (k, case) in enumerate(zip(keys, cases), 1):
        sym, direction = man[k]["symbol"].split(".")[0], man[k]["direction"]
        nk = f"vet-{i:02d}"
        d = os.path.join(a.out, nk); os.makedirs(d, exist_ok=True)
        for f in ("h4.png", "d1.png"): shutil.copyfile(os.path.join(a.src, k, f), os.path.join(d, f))
        card = open(os.path.join(a.src, k, "setup.md"), encoding="utf-8").read()
        card = PRE.sub(NEW_PRE, card).replace("# ARCHIVE SETUP", "# LIVE SETUP")
        blocks = ("\n" + "\n".join(exposure_case(case, sym, direction))
                  + f"\n- **Events (next 14 d, {'/'.join(sorted(set(ccys(sym))))}):**\n"
                  + "\n".join(events_case(case, sym)) + "\n")
        card = card.replace("\nImages:", blocks + "\nImages:")
        open(os.path.join(d, "setup.md"), "w", encoding="utf-8").write(card)
        out_man.append({"key": nk, "src": k, "symbol": sym, "direction": direction, "case": case,
                        "expect_veto": case in ("event", "election", "exposure"),
                        "rule": {"event": "release", "election": "election", "exposure": "exposure"}.get(case, "")})
        print(f"  {nk} <- {k} {sym:8} {direction:5} {case}")
    json.dump(out_man, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print(f"\n{len(out_man)} cards in {a.out} (manifest stays HERE)")


if __name__ == "__main__": main()
