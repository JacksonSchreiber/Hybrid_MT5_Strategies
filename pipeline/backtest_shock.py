#!/usr/bin/env python3
"""backtest_shock.py — staged backtest driver + report for Strategy 4 (Shock
Continuation), per docs/strategies/shock-continuation-spec.md §3/§4/§5.

Isolation: runs the EA headless via mt5_verify.sh with --strat Shock (InpUseShock
only; SMC/Fib/EMA off). Real Dukascopy ticks (model 4), spread as recorded.

Overfit guard (user decision 2026-08-28): SYMBOL holdout, not date holdout — the
loaded tick windows can't support a 2023-24 date holdout for EUR/GBP. Params are
selected on the FIT group (EURUSD 2016-19 + GBPUSD 2013-18) and validated on the
fully-unseen HOLDOUT group (USDJPY + XAUUSD 2020-26). Flagged in the report as a
deviation from the literal §5 for coach reconciliation.

Stages (staged to save compute):
  default  — run every symbol at default params; report N FIRST. §4 kill condition
             is >=150 trades: if the default run misses it, STOP (no grid).
  grid     — one-at-a-time sensitivity (shock_atr / pull / tp_mult) per symbol.
  report   — (re)build the markdown report from whatever runs are on disk.

Usage:
  python3 backtest_shock.py default
  python3 backtest_shock.py grid
  python3 backtest_shock.py report
"""
from __future__ import annotations

import csv, os, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# FILE_COMMON resolves to the SHARED Terminal/Common/Files (not the terminal-specific
# EE0304.../Common/Files) — the EA writes shock_meta.csv here and mt5_verify reads it here.
COMMON = Path("/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files")
JOURNAL_DIR = COMMON / "journal"
ECON_CSV = COMMON / "econ_events.csv"
RESULTS = REPO / "data" / "backtests" / "shock"          # gitignored (data/)
RESULTS.mkdir(parents=True, exist_ok=True)

RISK_PCT = 0.01
FTMO_DAILY_R = 5.0        # 5 full 1% losers in one UTC day = daily-loss breach

# symbol -> (from, to, symbol_group). FULL available .dk range (coach re-run 2026-08-28:
# EUR/GBP extended from QDM). symbol-holdout = fit {EUR,GBP} / validate {JPY,XAU}; an
# orthogonal 2023-24 DATE holdout is applied per symbol (fit <=2022 / validate 2023-24).
SYMBOLS = {
    "EURUSD.dk": ("2016.08.01", "2025.12.31", "fit"),
    "GBPUSD.dk": ("2013.08.01", "2024.12.31", "fit"),
    "USDJPY.dk": ("2020.01.01", "2025.12.31", "holdout"),
    "XAUUSD.dk": ("2020.01.01", "2025.12.31", "holdout"),
}
DATE_HOLDOUT = (2023, 2024)   # per-symbol date holdout (coach re-run #3)

DEFAULT = {"shock_atr": 1.8, "pull": 0.8, "tp": 0.75}
# one-at-a-time sensitivity cells (default value shared, so 7 unique cells)
GRID = {
    "shock_atr": [1.5, 1.8, 2.2],
    "pull":      [0.5, 0.8, 1.2],
    "tp":        [0.5, 0.75, 1.0],
}


def cell_id(p: dict) -> str:
    return f"a{p['shock_atr']}_p{p['pull']}_t{p['tp']}"


# ---------------------------------------------------------------- run one window
def run_window(symbol: str, frm: str, to: str, p: dict, model: int | None = None,
               timeout: int = 5400) -> Path | None:
    # model 1 (1-min OHLC) is the fast proxy for the trade-COUNT gate — the detector
    # fires on H4 closes identically to real ticks. model 4 (real ticks, real spread/
    # slippage) is reserved for acceptance-quality R via SHOCK_MODEL=4.
    if model is None:
        model = int(os.environ.get("SHOCK_MODEL", "1"))
    """Run one headless backtest; copy its journal + shock_meta sidecar into
    RESULTS/<cell>/<symbol>.{journal,meta}.csv. Returns the journal copy path."""
    tag = cell_id(p)
    outdir = RESULTS / tag
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ,
               SHOCK_ATR=str(p["shock_atr"]), SHOCK_PULL=str(p["pull"]),
               SHOCK_TPMULT=str(p["tp"]), SHOCK_CALGATE="true")
    # clear the sidecar so we know this run's rows are the ones we read
    (JOURNAL_DIR / "shock_meta.csv").unlink(missing_ok=True)
    cmd = [str(HERE / "mt5_verify.sh"), "--mode", "ALL", "--strat", "Shock",
           "--symbol", symbol, "--from", frm, "--to", to,
           "--model", str(model), "--timeout", str(timeout)]
    print(f"  RUN {symbol} {frm}..{to} {tag} ...", flush=True)
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           timeout=timeout + 300)
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT {symbol} {tag}")
        return None
    # mt5_verify's log() writes to STDERR (incl. the "journal:" line), so scan both
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    jpath = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("journal:"):
            jpath = line.split(":", 1)[1].strip()
    if not jpath or not Path(jpath).exists():
        print(f"    no journal ({symbol} {tag}); tail:\n" +
              "\n".join(out.splitlines()[-8:]))
        return None
    jdst = outdir / f"{symbol}.journal.csv"
    jdst.write_text(Path(jpath).read_text(encoding="utf-8", errors="replace"))
    # the detector writes shock_meta.csv via FILE_COMMON; on the Windows->WSL boundary
    # it can lag becoming visible after the terminal exits, so retry briefly before copy.
    meta = JOURNAL_DIR / "shock_meta.csv"
    for _ in range(60):                       # up to 30s for the Win->WSL flush
        if meta.exists():
            break
        time.sleep(0.5)
    if meta.exists():
        (outdir / f"{symbol}.meta.csv").write_text(
            meta.read_text(encoding="utf-8", errors="replace"))
    else:
        print(f"    WARN: no shock_meta.csv for {symbol} (splits will be empty)")
    n = sum(1 for _ in open(jdst)) - 1
    print(f"    -> {n} journal rows")
    return jdst


# ---------------------------------------------------------------- parse + metrics
def _dt(s):
    s = (s or "").strip()
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def load_trades(journal: Path, meta: Path | None):
    """Closed trades only (a fill occurred). Joined with the shock_meta sidecar on
    (signal_time, symbol)."""
    m = {}
    if meta and meta.exists():
        for row in csv.DictReader(open(meta, encoding="utf-8", errors="replace")):
            k = (_dt(row.get("signal_time")), (row.get("symbol") or "").strip())
            m[k] = row
    out = []
    for row in csv.DictReader(open(journal, encoding="utf-8", errors="replace")):
        posid = (row.get("posid") or "0").strip()
        exitp = (row.get("exit_price") or "").strip()
        if posid in ("", "0") or not exitp:
            continue                                  # never filled / still open
        try:
            r_mult = float(row.get("r_multiple") or "nan")
        except ValueError:
            continue
        if r_mult != r_mult:
            continue
        st = _dt(row.get("signal_time"))
        meta_row = m.get((st, (row.get("symbol") or "").strip()), {})
        out.append({
            "signal_time": st, "exit_time": _dt(row.get("exit_time")),
            "symbol": (row.get("symbol") or "").strip(),
            "direction": row.get("direction"), "r": r_mult,
            "scheduled": meta_row.get("scheduled", "?"),
            "regime": meta_row.get("regime", "?"),
            "cal": meta_row.get("cal_flag", "?"),
        })
    return out


def held_over_weekend(t) -> bool:
    a, b = t["signal_time"], t["exit_time"]
    if not a or not b:
        return False
    d = a
    while d <= b:
        if d.weekday() == 5:                          # any Saturday inside the hold
            return True
        d += timedelta(days=1)
    return False


def metrics(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0}
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    # max consecutive losers (chronological)
    ordered = sorted(trades, key=lambda t: t["signal_time"] or datetime.min)
    mc = cur = 0
    for t in ordered:
        cur = cur + 1 if t["r"] <= 0 else 0
        mc = max(mc, cur)
    worst_slip = max(((-1.0 - r) for r in rs if r < -1.0), default=0.0)
    wknd_trades = [t for t in trades if held_over_weekend(t)]
    wknd = len(wknd_trades)
    # weekend-gap slippage in R (coach #4): how far past the -1R stop a weekend-held
    # loser was carried by the Mon-open gap (0 if it stopped at/inside -1R).
    wknd_slips = [max(0.0, -1.0 - t["r"]) for t in wknd_trades if t["r"] < -1.0]
    wknd_slip_worst = max(wknd_slips, default=0.0)
    wknd_slip_total = sum(wknd_slips)
    # FTMO daily-loss breaches: UTC day sum of r (in %) <= -5
    byday = {}
    for t in ordered:
        d = (t["exit_time"] or t["signal_time"])
        if d:
            byday.setdefault(d.date(), 0.0)
            byday[d.date()] += t["r"]
    breaches = sum(1 for v in byday.values() if v <= -FTMO_DAILY_R)
    return {"n": n, "wr": 100.0 * wins / n, "avg_r": sum(rs) / n, "total_r": sum(rs),
            "max_consec_losers": mc, "worst_slip_r": worst_slip,
            "weekend_pct": 100.0 * wknd / n, "ftmo_breaches": breaches,
            "wknd_slip_worst": wknd_slip_worst, "wknd_slip_total": wknd_slip_total}


def split(trades, key, val):
    return [t for t in trades if t.get(key) == val]


def fmt(m):
    if m.get("n", 0) == 0:
        return "n=0"
    return (f"n={m['n']:4d}  WR={m['wr']:4.0f}%  avgR={m['avg_r']:+.3f}  "
            f"totR={m['total_r']:+6.1f}  maxCL={m['max_consec_losers']:2d}  "
            f"slipR={m['worst_slip_r']:.2f}  wknd={m['weekend_pct']:3.0f}%  "
            f"wkSlipR={m.get('wknd_slip_total',0):.1f}(worst {m.get('wknd_slip_worst',0):.2f})  "
            f"ftmoBreach={m['ftmo_breaches']}")


# ---------------------------------------------------------------- stages
def stage_default():
    print("=== STAGE: default-parameter run (report N first — §4 gate is >=150) ===")
    all_trades = []
    per = {}
    for sym, (frm, to, grp) in SYMBOLS.items():
        j = run_window(sym, frm, to, DEFAULT)
        if not j:
            per[sym] = []
            continue
        t = load_trades(j, j.with_name(f"{sym}.meta.csv"))
        per[sym] = t
        all_trades += t
    print("\n--- default per-symbol ---")
    for sym, t in per.items():
        print(f"  {sym:11} {fmt(metrics(t))}")
    agg = metrics(all_trades)
    print(f"\n  ALL SYMBOLS  {fmt(agg)}")
    print(f"\n  §4 sample gate (>=150 trades): "
          f"{'PASS' if agg.get('n',0) >= 150 else 'FAIL — insufficient sample, do not integrate'}")
    build_report()


def stage_grid():
    print("=== STAGE: sensitivity grid (one-at-a-time) ===")
    cells = []
    for knob, vals in GRID.items():
        for v in vals:
            p = dict(DEFAULT); p[knob] = v
            cells.append(p)
    seen = set(); uniq = []
    for p in cells:
        if cell_id(p) not in seen:
            seen.add(cell_id(p)); uniq.append(p)
    for p in uniq:
        for sym, (frm, to, grp) in SYMBOLS.items():
            run_window(sym, frm, to, p)
    build_report()


# ---------------------------------------------------------------- report
def year_of(t):
    return t["signal_time"].year if t["signal_time"] else 0


def date_group(t):
    return "holdout" if year_of(t) in DATE_HOLDOUT else "fit"


def salvage(per_sym):
    """Coach's fixed salvage rule: a v2 spec is written ONLY if a single pre-named
    subset (trend-aligned OR unscheduled — never a post-hoc combination) shows
    avg R >= +0.25 at n >= 40, the SAME SIGN in the symbol-fit and symbol-holdout
    groups, in at least 3 of the 4 symbols."""
    out = []
    fit_syms = [s for s, (_, _, g) in SYMBOLS.items() if g == "fit"]
    hold_syms = [s for s, (_, _, g) in SYMBOLS.items() if g == "holdout"]
    for label, key, val in [("trend-aligned", "regime", "trend"),
                            ("unscheduled", "scheduled", "unsched")]:
        allt = [t for ts in per_sym.values() for t in split(ts, key, val)]
        m = metrics(allt)
        fit_m = metrics([t for s in fit_syms for t in split(per_sym.get(s, []), key, val)])
        hold_m = metrics([t for s in hold_syms for t in split(per_sym.get(s, []), key, val)])
        persym = {s: metrics(split(ts, key, val)) for s, ts in per_sym.items()}
        pos_syms = sum(1 for mm in persym.values() if mm.get("n", 0) > 0 and mm["avg_r"] > 0)
        same_sign = (fit_m.get("n", 0) > 0 and hold_m.get("n", 0) > 0
                     and (fit_m["avg_r"] > 0) == (hold_m["avg_r"] > 0) and fit_m["avg_r"] > 0)
        passed = (m.get("n", 0) >= 40 and m.get("avg_r", -9) >= 0.25
                  and same_sign and pos_syms >= 3)
        out.append((label, m, fit_m, hold_m, persym, pos_syms, same_sign, passed))
    return out


def build_report():
    lines = ["# Shock Continuation — backtest report (coach re-run 2026-08-28)",
             f"_Generated {datetime.now():%Y-%m-%d %H:%M}. Model-1 gate (1-min OHLC; captures "
             "Mon-open weekend gaps). FULL available .dk range. Strategy isolated._", "",
             "> **Blanket strategy REJECTED** on the spec bars (n<150, WR<40%, avgR<+0.25). "
             "Two holdouts: **symbol** (fit EUR+GBP / validate JPY+XAU) and **date** "
             "(fit <=2022 / validate 2023-24, per symbol). Salvage rule is pre-registered "
             "(see the Salvage section).", ""]
    for tag_dir in sorted(RESULTS.glob("a*")):
        if not tag_dir.is_dir():
            continue
        per_sym = {}
        for sym in SYMBOLS:
            j = tag_dir / f"{sym}.journal.csv"
            per_sym[sym] = load_trades(j, tag_dir / f"{sym}.meta.csv") if j.exists() else []
        allt = [t for ts in per_sym.values() for t in ts]
        if not allt:
            continue
        lines.append(f"## params `{tag_dir.name}`")
        lines.append("| scope | metrics |")
        lines.append("|---|---|")
        for sym, ts in per_sym.items():
            lines.append(f"| {sym} ({SYMBOLS[sym][2]}) | {fmt(metrics(ts))} |")
        fit_t = [t for s in per_sym for t in per_sym[s] if SYMBOLS[s][2] == "fit"]
        hold_t = [t for s in per_sym for t in per_sym[s] if SYMBOLS[s][2] == "holdout"]
        lines.append(f"| **ALL** | {fmt(metrics(allt))} |")
        lines.append(f"| **symbol-FIT (EUR+GBP)** | {fmt(metrics(fit_t))} |")
        lines.append(f"| **symbol-HOLDOUT (JPY+XAU)** | {fmt(metrics(hold_t))} |")
        dpre = [t for t in allt if year_of(t) <= 2022]
        dhold = [t for t in allt if year_of(t) in DATE_HOLDOUT]
        dpost = [t for t in allt if year_of(t) >= 2025]
        lines.append(f"| **date pre-2023** | {fmt(metrics(dpre))} |")
        lines.append(f"| **date-HOLDOUT (2023-24)** | {fmt(metrics(dhold))} |")
        lines.append(f"| **date 2025+** | {fmt(metrics(dpost))} |")
        # hypothesis splits (aggregate)
        lines.append("\n**Splits (aggregate):**")
        for label, key, val in [("scheduled", "scheduled", "sched"),
                                ("unscheduled", "scheduled", "unsched"),
                                ("trend-aligned", "regime", "trend"),
                                ("counter-trend", "regime", "counter")]:
            lines.append(f"- {label}: {fmt(metrics(split(allt, key, val)))}")
        # SALVAGE evaluation
        lines.append("\n**Salvage evaluation** (v2 only if a pre-named subset hits "
                     "avgR>=+0.25, n>=40, same sign fit&holdout, >=3/4 symbols):")
        verdicts = []
        for label, m, fit_m, hold_m, persym, pos_syms, same_sign, passed in salvage(per_sym):
            lines.append(f"- **{label}**: overall {fmt(m)}")
            lines.append(f"    - symbol-fit {fmt(fit_m)}")
            lines.append(f"    - symbol-hold {fmt(hold_m)}")
            lines.append("    - per-symbol avgR: " + " ".join(
                f"{s.split('.')[0]}={persym[s].get('avg_r',0):+.2f}(n{persym[s].get('n',0)})"
                for s in SYMBOLS))
            lines.append(f"    - positive in {pos_syms}/4 symbols; same-sign fit&hold: {same_sign}"
                         f"  →  **{'SALVAGE (write v2)' if passed else 'no salvage'}**")
            verdicts.append((label, passed))
        any_pass = any(p for _, p in verdicts)
        lines.append(f"\n### VERDICT: {'V2 candidate — ' + ', '.join(l for l,p in verdicts if p) if any_pass else 'REJECTED — file it, keep detector+harness code, stop.'}")
        lines.append("")
    rp = RESULTS / "REPORT.md"
    rp.write_text("\n".join(lines) + "\n")
    print(f"\nreport -> {rp}")
    print("\n".join(lines))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "default"
    {"default": stage_default, "grid": stage_grid, "report": build_report}.get(
        stage, stage_default)()
