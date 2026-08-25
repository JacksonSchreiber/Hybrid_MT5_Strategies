#!/usr/bin/env python3
"""event_classes.py — load config/event_classes.yaml and classify one event name.

This is the SINGLE classifier. It runs at NORMALIZE time only: normalize_econ bakes
the resulting class letter into econ_events.csv's `class` column, and the runtime
consumers (tier0.py, the EA) read that column instead of matching keywords. That is
the whole point of the root-cause fix in docs/calendar-gap-coverage-spec.md §3.5 —
one table, no drift. PyYAML is fine here (normalize time); tier0 stays stdlib-only.

    classify("French Presidential Election", "EUR") -> ("W", False)   # political, kept
    classify("German CPI", "EUR")                   -> ("",  False)   # member sub-release, dropped
    classify("Jackson Hole Symposium", "All")       -> ("V", True)    # all-ccy
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_YAML = Path(__file__).resolve().parent.parent / "config" / "event_classes.yaml"


@lru_cache(maxsize=1)
def _table(path: str = ""):
    p = Path(path) if path else _YAML
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    rules = [
        (re.compile(r["re"], re.IGNORECASE), r["class"],
         bool(r.get("political", False)), bool(r.get("all_ccy", False)))
        for r in doc.get("rules", [])
    ]
    skip = tuple(s.lower() for s in doc.get("country_skip", []))
    return rules, skip


def classify(event_name: str, ccy: str | None = None, *, yaml_path: str = ""):
    """Return (class_letter, all_ccy_bool). class_letter is '' when unclassified or
    dropped by the country-skip filter. First matching rule wins."""
    name = (event_name or "").strip()
    if not name:
        return "", False
    low = name.lower()
    rules, skip = _table(yaml_path)
    for rx, cls, political, all_ccy in rules:
        if rx.search(low):
            # country-skip applies AFTER the class lookup, and never to political rows
            if not political and any(w in low for w in skip):
                return "", False
            return cls, all_ccy
    return "", False


if __name__ == "__main__":  # quick manual check: `python3 event_classes.py`
    for n, c in [("French Presidential Election", "EUR"),
                 ("German CPI", "EUR"),
                 ("Fed Chairman Bernanke Testifies", "USD"),
                 ("Jackson Hole Symposium", "All"),
                 ("FOMC Minutes", "USD"),
                 ("Core PCE Price Index m/m", "USD"),
                 ("Flash Manufacturing PMI", "EUR"),
                 ("Retail Sales m/m", "USD"),
                 ("Bank Holiday", "GBP")]:
        print(f"{classify(n, c)!s:14}  {n}")
