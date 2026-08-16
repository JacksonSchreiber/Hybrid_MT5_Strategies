#!/usr/bin/env python3
"""tier0.py — the blind economic-calendar encoder for the advisor's setup bundle.

Runs in plain code (no model, no network, stdlib only) as part of the blind
bundle the OS-capture daemon delivers. Its one job now: turn the real economic
calendar into the BLIND encoding the advisor is allowed to see — never event
names, currencies, or dates, only:

    {"high_impact_ahead": bool, "hours_until": float|None,
     "affects": "base|quote|both|none", "recent_event_bias": "with|against|none"}

High-impact detection reuses the EA's keyword classifier (EventSignificance HIGH
in HybridForwardTest.mq5) — kept in sync here — because econ_events.csv has no
native impact column (datetime_utc,ccy,event,actual,forecast,ccy_bias).

Consumed by pipeline/inbox_bridge.py (`blind_setup_md`). The former Tier-0
skip-evaluation + correlation-cap logic (which read the retired app's
playbook/clusters.json) has been removed along with the assistant app — the
advisor applies those rules itself from the quick-reference guide.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# econ_events.csv lives in MT5 Common\Files (same file the EA overlay reads).
ECON_CSV = Path(
    "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/econ_events.csv"
)

# --- high-impact keyword classifier (port of EventSignificance HIGH, EA) -------
HIGH_IMPACT_KEYS = (
    "federal funds", "fomc", "rate decision", "official bank rate",
    "main refinancing", "cash rate", "interest rate", "rate statement",
    "bank rate", "monetary policy", "press conference", "ecb press",
    "non-farm", "nonfarm", "cpi", "gdp", "pce",
    # Tier B (2026-08-15): remaining red-folder reds. member-country sub-releases
    # are already dropped by _SKIP_COUNTRY above, so these stay US/EZ/UK headline.
    "unemployment rate", "average hourly earnings", "average earnings",
    "retail sales", "claimant count", "pmi",
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


@dataclass
class Signal:
    symbol: str
    direction: int          # +1 / -1
    entry_time: datetime    # UTC, the signal bar / intended entry
    strategy: str = ""


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
