#!/usr/bin/env python3
"""tier0.py — the blind economic-calendar encoder for the advisor's setup bundle.

Runs in plain code (no model, no network, stdlib only) as part of the blind bundle
the OS-capture daemon delivers. It reads econ_events.csv's `class` column — baked by
normalize_econ from config/event_classes.yaml (docs/calendar-gap-coverage-spec.md
§3.5), the SINGLE source of truth — so this file no longer keeps its own keyword list
(that was one of the three drifting lists the spec killed). If an OLD feed without a
`class` column is present, a minimal built-in fallback keeps it working (and says so).

Two surfaces:
- scan_calendar()  -> NAMED, classed rows (V within 12h; W within the weekend-hold
                      window). For the acceptance tests and the coach. NOT blind.
- blind_calendar() -> the ONLY thing the advisor sees: booleans / class / affected-leg,
                      never a name, currency, or date. Now carries a SEPARATE
                      weekend_event field (spec §3.4) so a Friday->Monday election is
                      visible even though it is >12h away.
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

VIOLATION_HORIZON_H = 12.0    # a V-class event inside this = rule violation (§1)

# Minimal fallback ONLY for a legacy feed with no `class` column. The real classifier
# is config/event_classes.yaml (baked into the feed); regenerate the feed to use it.
_FALLBACK_V = ("federal funds", "fomc", "rate decision", "official bank rate",
               "main refinancing", "cash rate", "interest rate", "rate statement",
               "bank rate", "monetary policy", "press conference", "ecb press",
               "non-farm", "nonfarm", "cpi", "gdp", "pce", "unemployment rate",
               "average earnings", "average hourly earnings", "claimant count",
               "jackson hole", "fed chair")
_FALLBACK_W = ("election", "referendum", "plebiscite")

# ISO currency codes we recognise as FX legs.
FX_CODES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"}
# Symbols with no FX leg (indices, metals, oil) are USD-driven for the calendar.
USD_PROXY_HINTS = ("US30", "US100", "US500", "NAS", "SPX", "DJ", "XAU", "XAG",
                   "GOLD", "SILVER", "OIL", "WTI", "USOIL")


def _fallback_class(name: str) -> str:
    e = (name or "").lower()
    if any(k in e for k in _FALLBACK_W):
        return "W"
    if any(k in e for k in _FALLBACK_V):
        return "V"
    return ""


def symbol_currencies(symbol: str) -> set[str]:
    core = symbol.split(".")[0].upper()
    if len(core) == 6 and core[:3] in FX_CODES and core[3:] in FX_CODES:
        return {core[:3], core[3:]}
    if any(h in core for h in USD_PROXY_HINTS):
        return {"USD"}
    return {"USD"}


def symbol_leg(symbol: str, ccy: str) -> str:
    core = symbol.split(".")[0].upper()
    if len(core) == 6 and core[:3] == ccy:
        return "base"
    if len(core) == 6 and core[3:] == ccy:
        return "quote"
    return "base"  # index/metal proxy, or an all-ccy event: USD/base is the driver


def _affects_legs(symbol: str, ev) -> set[str]:
    """Which of the symbol's legs an event touches. An all-ccy event (ccy=All, e.g.
    Jackson Hole / a G20 weekend) touches both."""
    if ev["ccy"] == "ALL":
        return {"base", "quote"}
    return {symbol_leg(symbol, ev["ccy"])}


def _event_matches(ev, ccy_set: set[str]) -> bool:
    return ev["ccy"] == "ALL" or ev["ccy"] in ccy_set


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
        has_class = r.fieldnames is not None and "class" in r.fieldnames
        for d in r:
            t = _parse_dt(d.get("datetime_utc") or d.get("datetime"))
            if t is None:
                continue
            name = (d.get("event") or "").strip()
            cls = (d.get("class") or "").strip().upper() if has_class else _fallback_class(name)
            rows.append({
                "t": t,
                "ccy": (d.get("ccy") or "").strip().upper(),
                "name": name,
                "bias": _to_int(d.get("ccy_bias")),
                "cls": cls,
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


SWING_FLOOR_DAYS = 5   # covers a weekday vote (US Tue / UK Thu) landing just past Monday


def _weekend_end(now: datetime) -> datetime:
    """End of the weekend-hold scan for a swing position opened `now` (spec §3.4).

    Base rule: the coming Monday 12:00 UTC — covers any Fri->Mon gap. Floor: at least
    SWING_FLOOR_DAYS out, so a weekday vote whose result lands early the next week (a US
    Tuesday election, a UK Thursday count) is still seen from a Thu/Fri decision — the
    spec's 'include the election week' (§1). The floor is short enough that a mid-week
    decision never reaches the *following* weekend, so ordinary Fridays don't over-flag."""
    days = (7 - now.weekday()) % 7          # Monday=0 .. Sunday=6
    if days == 0:                           # decision already on a Monday -> next Monday
        days = 7
    monday = (now + timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0)
    return max(monday, now + timedelta(days=SWING_FLOOR_DAYS))


def _window_start(ev_t: datetime) -> datetime:
    """Weekend-hold window start for a W row: midnight UTC of the event's date. The
    stored time is a placeholder (12:30 for FF vote rows, 00:00 for political rows), so
    hours-until is measured to the window start, not the placeholder clock (§3.3/§3.6)."""
    return ev_t.replace(hour=0, minute=0, second=0, microsecond=0)


def scan_calendar(signal: Signal, events, *, swing: bool = True):
    """NAMED, classed scan — for acceptance tests and the coach, NOT for the advisor.

    Returns violations (V within VIOLATION_HORIZON_H) and weekend events (W within the
    Fri->Mon window when `swing` or the decision is Fri/Sat)."""
    ccy = symbol_currencies(signal.symbol)
    now = signal.entry_time
    v_end = now + timedelta(hours=VIOLATION_HORIZON_H)
    extend = swing or now.weekday() in (4, 5)     # swing holds, or a Fri/Sat decision
    w_end = _weekend_end(now) if extend else v_end

    violations, weekend = [], []
    for ev in events:
        if not _event_matches(ev, ccy):
            continue
        if ev["cls"] == "V" and now <= ev["t"] <= v_end:
            violations.append({
                "name": ev["name"], "ccy": ev["ccy"], "cls": "V",
                "hours_until": round((ev["t"] - now).total_seconds() / 3600.0, 2),
                "legs": _affects_legs(signal.symbol, ev),
            })
        elif ev["cls"] == "W" and now <= ev["t"] <= w_end:
            ws = _window_start(ev["t"])
            weekend.append({
                "name": ev["name"], "ccy": ev["ccy"], "cls": "W",
                "hours_until": round(max(0.0, (ws - now).total_seconds() / 3600.0), 2),
                "legs": _affects_legs(signal.symbol, ev),
            })

    violations.sort(key=lambda x: x["hours_until"])
    weekend.sort(key=lambda x: x["hours_until"])
    return {"violations": violations, "weekend": weekend,
            "recent_bias": _recent_bias(signal, events, ccy)}


def _recent_bias(signal: Signal, events, ccy, past_h: float = 24.0):
    now = signal.entry_time
    past_start = now - timedelta(hours=past_h)
    for ev in reversed(events):
        if ev["t"] > now:
            continue
        if ev["t"] < past_start:
            break
        if ev["cls"] != "V" or not _event_matches(ev, ccy):
            continue
        leg = symbol_leg(signal.symbol, ev["ccy"])
        ccy_effect = ev["bias"] * (1 if leg == "base" else -1)
        if ccy_effect != 0:
            return "with" if (ccy_effect > 0) == (signal.direction > 0) else "against"
        return "none"
    return "none"


def _aff(legs: set[str]) -> str:
    if not legs:
        return "none"
    if legs == {"base"}:
        return "base"
    if legs == {"quote"}:
        return "quote"
    return "both"


def blind_calendar(signal: Signal, events, horizon_h: float = VIOLATION_HORIZON_H,
                   past_h: float = 24.0, *, swing: bool = True):
    """The ONLY calendar info the models receive. No names/currencies/dates — booleans,
    class letters, and affected-leg only. `weekend_event` is separate from
    `high_impact_ahead` so a >12h Fri->Mon election still reaches the advisor (§3.4)."""
    s = scan_calendar(signal, events, swing=swing)

    v_legs = set().union(*[v["legs"] for v in s["violations"]]) if s["violations"] else set()
    w_legs = set().union(*[w["legs"] for w in s["weekend"]]) if s["weekend"] else set()

    return {
        # violation horizon (unchanged contract)
        "high_impact_ahead": bool(s["violations"]),
        "hours_until": s["violations"][0]["hours_until"] if s["violations"] else None,
        "affects": _aff(v_legs),
        "recent_event_bias": s["recent_bias"],
        # weekend-hold horizon (new, §3.4) — class only, never the event name
        "weekend_event": bool(s["weekend"]),
        "weekend_class": "W" if s["weekend"] else None,
        "weekend_hours_until": s["weekend"][0]["hours_until"] if s["weekend"] else None,
        "weekend_affects": _aff(w_legs),
    }
