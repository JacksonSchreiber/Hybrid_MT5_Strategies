#!/usr/bin/env python3
"""tier0.py — mechanical prefilter for the advisory app (Build step 1, the
non-blind, no-model part; docs/assistant-app-implementation.md §2).

Runs in plain code BEFORE any model call, on data the backend has natively.
Tier 0 is NOT blind — only the Tier-1/Tier-2 models are. Two jobs:

  1. Auto-SKIP with a NAMED rule when a hard, deterministic condition fails:
       - a high-impact event for either currency of the pair falls within the
         horizon (default 12h)               [guide: Start Here step 2]
       - entry is late Friday (weekend gap on an H4 hold)
       - the symbol already has a live setup  (one-setup-per-symbol)
       - a same-direction position in the same correlation family would push
         open risk past the caps (3% concurrent / 2% per idea)  [clusters.json]

  2. Produce the BLIND calendar encoding passed onward to the models — never
     event names, currencies, or dates, only:
       {"high_impact_ahead": bool, "hours_until": float|None,
        "affects": "base|quote|both|none", "recent_event_bias": "with|against|none"}

High-impact detection reuses the EA's keyword classifier (EventSignificance HIGH
set in HybridForwardTest.mq5) — kept in sync here — because econ_events.csv has
no native impact column (datetime_utc,ccy,event,actual,forecast,ccy_bias).

stdlib only. No network, no API key. `python3 pipeline/tier0.py` self-tests
against the real clusters.json + econ_events.csv.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbook"
CLUSTERS_JSON = PLAYBOOK_DIR / "clusters.json"
# econ_events.csv lives in MT5 Common\Files (same file the EA overlay reads).
ECON_CSV = Path(
    "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/econ_events.csv"
)

# --- high-impact keyword classifier (port of EventSignificance HIGH, EA) -------
HIGH_IMPACT_KEYS = (
    "federal funds", "fomc", "rate decision", "official bank rate",
    "main refinancing", "cash rate", "interest rate", "rate statement",
    "bank rate", "monetary policy", "press conference", "ecb press",
    "non-farm", "nonfarm", "cpi", "gdp",
)
# member-country sub-releases the EA drops (only headline US/EZ/UK prints matter)
_SKIP_COUNTRY = ("german", "french", "spanish", "italian", "chinese",
                 "japanese", "swiss", "canadian", "australian", "new zealand")

# ISO currency codes we recognise as FX legs.
FX_CODES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"}
# Symbols with no FX leg (indices, metals, oil) are USD-driven for the calendar:
# "USD events are your events" (guide, Reading the event lines).
USD_PROXY_HINTS = ("US30", "US100", "US500", "NAS", "SPX", "DJ", "XAU", "XAG",
                   "GOLD", "SILVER", "OIL", "WTI", "USOIL")


def is_high_impact(event_name: str) -> bool:
    e = (event_name or "").lower()
    if any(k in e for k in _SKIP_COUNTRY):
        return False
    return any(k in e for k in HIGH_IMPACT_KEYS)


def symbol_currencies(symbol: str) -> set[str]:
    """Currencies whose calendar events matter for this symbol. FX pair → its two
    legs; index/metal/oil → {USD}."""
    core = symbol.split(".")[0].upper()  # strip .dk / .a etc.
    if len(core) == 6 and core[:3] in FX_CODES and core[3:] in FX_CODES:
        return {core[:3], core[3:]}
    if any(h in core for h in USD_PROXY_HINTS):
        return {"USD"}
    # unknown → be safe, treat as USD-driven
    return {"USD"}


def symbol_leg(symbol: str, ccy: str) -> str:
    core = symbol.split(".")[0].upper()
    if len(core) == 6 and core[:3] == ccy:
        return "base"
    if len(core) == 6 and core[3:] == ccy:
        return "quote"
    return "base"  # index/metal proxy: USD treated as the driving leg


# --- correlation families (derived from currency involvement; clusters.json is
# --- the human reference, its "members" prose isn't a machine symbol list) -----
def families_of(symbol: str) -> set[str]:
    ccy = symbol_currencies(symbol)
    core = symbol.split(".")[0].upper()
    fams = set()
    if "USD" in ccy or symbol_currencies(symbol) == {"USD"}:
        fams.add("USD block")
    if "EUR" in ccy:
        fams.add("EUR crosses")
    if "JPY" in ccy:
        fams.add("JPY crosses")
    if ccy & {"AUD", "NZD"} or any(h in core for h in ("US30", "US100", "US500", "NAS", "SPX")):
        fams.add("Risk-on block")
    if any(h in core for h in ("XAU", "XAG", "GOLD", "SILVER")):
        fams.add("Metals")
    return fams


@dataclass
class OpenPosition:
    symbol: str
    direction: int          # +1 long, -1 short
    risk_pct: float         # open risk as % of account (e.g. 1.0)


@dataclass
class Signal:
    symbol: str
    direction: int          # +1 / -1
    entry_time: datetime    # UTC, the signal bar / intended entry
    strategy: str = ""
    risk_pct: float = 1.0
    open_positions: list[OpenPosition] = field(default_factory=list)


def _load_events(path: Path = ECON_CSV):
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        r = csv.DictReader(fh)
        for d in r:
            t = _parse_dt(d.get("datetime_utc") or d.get("datetime"))
            if t is None:
                continue
            rows.append({
                "t": t,
                "ccy": (d.get("ccy") or "").strip().upper(),
                "name": (d.get("event") or "").strip(),
                "bias": _to_int(d.get("ccy_bias")),
            })
    rows.sort(key=lambda x: x["t"])
    return rows


def _parse_dt(s):
    s = (s or "").strip()
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _to_int(s):
    try:
        return int(float((s or "").strip()))
    except (ValueError, TypeError):
        return 0


def blind_calendar(signal: Signal, events, horizon_h: float, past_h: float = 24.0):
    """The ONLY calendar info the models receive. No names/currencies/dates."""
    ccy = symbol_currencies(signal.symbol)
    now = signal.entry_time
    ahead_end = now + timedelta(hours=horizon_h)
    past_start = now - timedelta(hours=past_h)

    affects = set()
    nearest = None
    for ev in events:
        if not is_high_impact(ev["name"]) or ev["ccy"] not in ccy:
            continue
        if now <= ev["t"] <= ahead_end:
            affects.add(symbol_leg(signal.symbol, ev["ccy"]))
            dt = (ev["t"] - now).total_seconds() / 3600.0
            if nearest is None or dt < nearest:
                nearest = dt

    # most recent past high-impact event's directional bias vs the trade
    recent_bias = "none"
    for ev in reversed(events):
        if ev["t"] > now:
            continue
        if ev["t"] < past_start:
            break
        if not is_high_impact(ev["name"]) or ev["ccy"] not in ccy:
            continue
        # ccy_bias is the surprise sign for the event ccy; flip when it's the quote
        leg = symbol_leg(signal.symbol, ev["ccy"])
        ccy_effect = ev["bias"] * (1 if leg == "base" else -1)
        if ccy_effect != 0:
            recent_bias = "with" if (ccy_effect > 0) == (signal.direction > 0) else "against"
        break

    if not affects:
        aff = "none"
    elif affects == {"base"}:
        aff = "base"
    elif affects == {"quote"}:
        aff = "quote"
    else:
        aff = "both"

    return {
        "high_impact_ahead": bool(affects),
        "hours_until": round(nearest, 2) if nearest is not None else None,
        "affects": aff,
        "recent_event_bias": recent_bias,
    }


def hard_fail(signal: Signal, clusters, horizon_h: float):
    """Return a NAMED skip reason (str) if a hard rule fails, else None."""
    caps = clusters["risk_rules"]

    # late Friday (H4 swing hold over the weekend gap)
    wd = signal.entry_time.weekday()  # Mon=0 .. Sun=6
    if wd == 4 and signal.entry_time.hour >= 16:   # Fri from 16:00 UTC
        return ("late-Friday entry (H4 hold over the weekend gap) — "
                f"{signal.entry_time:%a %H:%M} UTC")
    if wd == 5 or wd == 6:
        return "weekend entry"

    # one setup per symbol
    for p in signal.open_positions:
        if p.symbol == signal.symbol:
            return f"one-setup-per-symbol — a position on {signal.symbol} is already open"

    # correlated same-direction over-exposure
    fams = families_of(signal.symbol)
    per_idea_cap = caps["per_idea_cap_pct"]
    concurrent_cap = caps["system_open_risk_cap_pct"]
    for fam in fams:
        same = signal.risk_pct
        for p in signal.open_positions:
            if fam in families_of(p.symbol) and p.direction == signal.direction:
                same += p.risk_pct
        if same > per_idea_cap + 1e-9:
            return (f"correlated over-exposure — family '{fam}' same-direction "
                    f"risk would be {same:.1f}% (> {per_idea_cap:.0f}% per-idea cap)")
    total_open = signal.risk_pct + sum(p.risk_pct for p in signal.open_positions)
    if total_open > concurrent_cap + 1e-9:
        return (f"open-risk cap — total open+new risk {total_open:.1f}% "
                f"(> {concurrent_cap:.0f}% concurrent cap)")
    return None


@dataclass
class Tier0Result:
    skip: bool
    reason: str | None
    calendar_blind: dict


def evaluate(signal: Signal, events=None, clusters=None,
             horizon_h: float | None = None) -> Tier0Result:
    if clusters is None:
        clusters = json.loads(CLUSTERS_JSON.read_text(encoding="utf-8"))
    if events is None:
        events = _load_events()
    if horizon_h is None:
        horizon_h = float(clusters["risk_rules"]["news_horizon_hours"])

    cal = blind_calendar(signal, events, horizon_h)

    # news within the horizon is itself a hard skip per the guide (Start Here 2)
    reason = None
    if cal["high_impact_ahead"]:
        reason = (f"high-impact event ~{cal['hours_until']:.0f}h ahead "
                  f"(affects {cal['affects']}) — within the {horizon_h:.0f}h horizon")
    if reason is None:
        reason = hard_fail(signal, clusters, horizon_h)
    return Tier0Result(skip=reason is not None, reason=reason, calendar_blind=cal)


# ============================================================================
def _selftest():
    clusters = json.loads(CLUSTERS_JSON.read_text(encoding="utf-8"))
    events = _load_events()
    print(f"clusters.json: {len(clusters['correlated_families'])} families, "
          f"caps {clusters['risk_rules']}")
    print(f"econ_events.csv: {len(events)} events "
          f"({events[0]['t']:%Y-%m-%d} .. {events[-1]['t']:%Y-%m-%d})"
          if events else "econ_events.csv: NOT FOUND")
    hi = [e for e in events if is_high_impact(e["name"])]
    print(f"  high-impact events: {len(hi)}  e.g. "
          f"{sorted({e['name'] for e in hi})[:4]}")

    cases = [
        ("news-ahead skip",
         Signal("EURUSD.dk", -1, datetime(2021, 1, 8, 8, 0), "SweepMSS")),
        ("clean weekday",
         Signal("EURUSD.dk", -1, datetime(2021, 1, 20, 8, 0), "DeepFib")),
        ("late-Friday skip",
         Signal("EURUSD.dk", 1, datetime(2021, 1, 22, 18, 0), "EMArev")),
        ("one-setup skip",
         Signal("EURUSD.dk", 1, datetime(2021, 1, 20, 8, 0), "DeepFib",
                open_positions=[OpenPosition("EURUSD.dk", 1, 1.0)])),
        ("correlated over-exposure",
         Signal("EURUSD.dk", 1, datetime(2021, 1, 20, 8, 0), "DeepFib",
                risk_pct=1.0,
                open_positions=[OpenPosition("EURGBP.dk", 1, 1.0),
                                OpenPosition("EURJPY.dk", 1, 1.0)])),
    ]
    print("\n--- cases ---")
    for label, sig in cases:
        r = evaluate(sig, events, clusters)
        verdict = f"SKIP: {r.reason}" if r.skip else "PASS → to models"
        print(f"[{label}] {sig.symbol} {'BUY' if sig.direction>0 else 'SELL'} "
              f"{sig.entry_time:%a %Y-%m-%d %H:%M}\n    {verdict}\n    "
              f"blind_calendar={r.calendar_blind}")


if __name__ == "__main__":
    _selftest()
