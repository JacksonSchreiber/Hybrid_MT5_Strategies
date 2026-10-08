#!/usr/bin/env python3
"""backtest_exp_pool.py - trader 2026-10-08: an EXPERIMENT-ONLY pool of blind cards for symbols outside the backtest tab's two groups,
for the advisor experiments (live/backtest_experiment.py). NOT part of the frozen Practice/Test split, never served by the tab.

SYMBOLS: US100, US500 (US indices), XAUUSD (precious metals), USOIL (standalone), USDJPY (yen) - the lineup symbols with combo
inputs that are not research-locked. The research lock still applies: locked roots never, and no trade in or after the lock's
period_from year (the sealed holdout). The 7 locked symbols and the new-symbol fleet (reserved for the coach + trader's one look) are
not touched.
EACH TRADE: the same blind card as the tab (pipeline/backtest_cards.build_card + check_card + the v3 overlay; events for the indices
and oil read the USD calendar), the same box frame (to the first -1R touch, capped at 40 trading days; backtest_rules' encoding) and
the raw signal's R on the decoded frame, + hit TP2 / reached +1R (server-side only).
OUTPUT data/backtest_pool/: cards.json.gz, results.json.gz, frames/{index.json, <SYM>.bin}, pool.json (hid -> sym, group, year: the
draw's index, server-side only), report.txt. Upload: scp to <box root>/backtest_pool/.
    python3 pipeline/backtest_exp_pool.py            (heavy: run capped - systemd-run --user -p MemoryMax=5G ...)
"""
from __future__ import annotations
import gzip, hashlib, json, os, sys, time
import numpy as np
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_cards as BC                    # noqa: E402
from pipeline import backtest_rules as BR                    # noqa: E402
from pipeline import backtest_presets as BP                  # noqa: E402
from live import backtest_engine as E                        # noqa: E402
from live import symbol_groups as SG                         # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "backtest_pool"); FR = os.path.join(OUT, "frames")
SYMS = ["US100.dk", "US500.dk", "XAUUSD.dk", "USOIL.dk", "USDJPY.dk"]
EV_ROOT = {"US100": "USDUSD", "US500": "USDUSD", "USOIL": "USDUSD"}       # the calendar's currency for non-FX symbols
RAW = {"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": True, "levers": []}


def group_of(root: str) -> tuple[str, str]:
    g = next(g for g in SG.GROUPS if root in g[3]); return g[0], g[1]


def servable(c: dict) -> bool:                                 # live/backtest.servable: a calendar and a regime tag
    return c.get("calendar") == "ok" and bool((c.get("regime") or {}).get("tag"))


def main():
    t0 = time.time(); os.makedirs(FR, exist_ok=True)
    lock = SG.RESEARCH_LOCK; salt = open(os.path.join(ROOT, "data", "backtest", "salt.txt")).read().strip()
    pop = [x for x in CB.population() if x["sym"] in SYMS]
    cards, res, idx, pool, rep = {}, {}, {}, {}, []
    for sym in SYMS:
        root = sym.split(".")[0]; gk, gn = group_of(root)
        if root in lock["locked"]: rep.append(f"{root}: research-locked - skipped"); continue
        xs = [x for x in pop if x["sym"] == sym]
        keep = [x for x in xs if int(x["t"][:4]) < lock["period_from"]]
        if not keep: rep.append(f"{root}: no trades before {lock['period_from']}"); continue
        ctx = BC.prep_symbol(keep[0]["bars"]); S = CB.sym_obj(sym); fn = f"{root}.bin"; fh = open(os.path.join(FR, fn), "wb"); pos = 0
        why = {"history": 0, "overlay": 0, "check": 0, "not servable": 0, "no M1": 0}; n_ok = 0
        for x in keep:
            if x["i0"] < 400: why["history"] += 1; continue
            hid = hashlib.sha256(f"{salt}|{x['sym']}|{x['t']}|{x['strat']}".encode()).hexdigest()[:16]
            card, prov = BC.build_card(x["bars"], x["i0"] - 1, x["e"], x["s"], x["tp1"], x["tp2"], x["up"], x["strat"], gn,
                                       EV_ROOT.get(root, root), hid, ctx=ctx, tol=A.TICK[sym] / 2)
            if card["overlay"] is None: why["overlay"] += 1; continue
            try: BC.check_card(card, prov)
            except AssertionError: why["check"] += 1; continue
            if not servable(card): why["not servable"] += 1; continue
            p = CB.prep(S, x)
            if p is None: why["no M1"] += 1; continue
            F = dict(p["F"]); fctx, bctx, _ = BR.full_ctx(S, x, p)
            F["bl"] = F["xl"] if p["up"] else F["xl"] + F["spR"]
            hit = np.nonzero(F["xl"] <= -1.0)[0]; jstop = int(hit[0]) if hit.size else p["n"] - 1
            nk = min(jstop + 1, BR.CAP_MIN, p["n"])
            blob = E.encode_frame(F["bo"][:nk], F["bh"][:nk], F["bl"][:nk], F["bc"][:nk], F["spR"][:nk], fctx["tm"][:nk])
            tn = int(fctx["tm"][nk]) if nk < p["n"] else fctx["t_next"]
            meta = {"g": gk, "f": fn, "off_b": pos, "len_b": len(blob), "n": nk, "n_full": p["n"], "up": bool(p["up"]), "tp2": p["tp2"],
                    "tp1": bctx["tp1"], "off": fctx["off"], "t_next": tn}
            fh.write(blob); pos += len(blob)
            Fd, cd = E.decode_frame(blob, meta); raw = E.evaluate(RAW, Fd, cd)
            ps = BP.path_stats(F, bctx); js = int(hit[0]) if hit.size else p["n"]
            r1 = bool((F["xh"][:js + 1] >= 1.0).any())
            cards[hid] = card; idx[hid] = meta; pool[hid] = {"sym": root, "group": gk, "year": int(x["t"][:4])}
            res[hid] = {"R": {"raw": round(raw["R"], 6)}, "hit_tp2": bool(ps["tp2_raw"]), "banked_1R": r1,
                        "stop_pips": abs(x["e"] - x["s"]) / (10 * A.TICK.get(sym, 1e-5)), "gross": {"raw": raw["gross"]}}
            n_ok += 1
        fh.close(); rep.append(f"{root} ({gn}): {n_ok} cards of {len(keep)} trades before {lock['period_from']} ({len(xs)} in all); left out {why}")
        print(rep[-1], f"({time.time() - t0:.0f}s)", flush=True)
    with gzip.open(os.path.join(OUT, "cards.json.gz"), "wt") as f: json.dump({"v": BC.CARD_V, "built": time.strftime("%Y-%m-%d %H:%M"), "cards": cards}, f, separators=(",", ":"))
    with gzip.open(os.path.join(OUT, "results.json.gz"), "wt") as f: json.dump({"trades": res}, f, separators=(",", ":"))
    json.dump({"version": 1, "built": time.strftime("%Y-%m-%d %H:%M"), "q": E.Q, "trades": idx}, open(os.path.join(FR, "index.json"), "w"), separators=(",", ":"))
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "lock": lock, "trades": pool}, open(os.path.join(OUT, "pool.json"), "w"), separators=(",", ":"))
    # the same check on the decoded frames as the tab: plain-Python kernel == numba on a sample
    fr = E.Frames(FR); mism = 0
    for h in sorted(idx)[::25]:
        Fd, cd = fr.get(h); P = E.compile_cfg(E.validate(RAW)[0], cd, True)
        if tuple(E.run(E.kern, Fd, cd, P)) != tuple(E.run(BR.NB, Fd, cd, P)): mism += 1
    rep.append(f"{len(cards)} cards in all; plain-Python == numba on {len(sorted(idx)[::25])} sampled frames: {mism} mismatches; {time.time() - t0:.0f}s")
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(rep) + "\n"); print("\n".join(rep[-1:]))


if __name__ == "__main__": main()
