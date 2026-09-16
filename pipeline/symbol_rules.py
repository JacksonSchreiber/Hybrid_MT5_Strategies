"""symbol_rules.py - per-symbol doctrine flags from config/symbol_rules.json (stdlib only; see the _doc key)."""
import json
from pathlib import Path
_P = Path(__file__).resolve().parent.parent / "config" / "symbol_rules.json"
def rules(symbol: str) -> dict:
    root = (symbol or "").split(".")[0].upper()
    try: d = json.loads(_P.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}
    return d.get(root, {})
def rules_line(symbol: str) -> str:
    r = rules(symbol)
    if not r: return ""
    return (f"class: {r.get('class')} · late-Friday rule {r.get('late_friday_rule')} · weekend-hold flag {r.get('weekend_hold_flag')} · "
            f"overnight-timing steps {r.get('overnight_timing_steps')} · event rows: {r.get('event_rows')} (config/symbol_rules.json)")
