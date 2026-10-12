"""symbol_groups.py - trader 2026-10-03: the 49 account symbols in behaviour groups (by what drives price, not by results), the
single source for the wiki table and the Data visualizers group toggles. PROPOSED - to be confirmed by behaviour measures (trend
persistence, volatility, cost vs range, co-movement) before any per-group tuning.
Each group: (key, name, colour, symbols (roots, no suffix), how it behaves)."""

GROUPS = [
    ("idx", "US indices", "#3987e5", ["US100", "US500", "US30"],
     "Long upward drift, gaps at the open, dips get bought"),
    ("met", "Precious metals", "#e3b341", ["XAUUSD", "XAGUSD"],
     "Long macro and safe-haven trends, high volatility"),
    ("maj", "USD majors", "#58a6ff", ["EURUSD", "GBPUSD"],
     "Deepest liquidity, macro trends in phases"),
    ("jpy", "Yen", "#f778ba", ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "NZDJPY", "CHFJPY"],
     "Carry and risk-on/off: smooth trends, sharp unwinds"),
    ("com", "Commodity bloc", "#3fb950", ["AUDUSD", "NZDUSD", "USDCAD", "AUDNZD", "AUDCAD", "NZDCAD"],
     "Commodity and China linked, tends to range and revert"),
    ("chf", "Swiss franc", "#d29922", ["USDCHF", "EURCHF", "GBPCHF", "AUDCHF", "NZDCHF", "CADCHF"],
     "Safe haven, central-bank managed history, quiet ranges with jumps"),
    ("eur", "Euro / Sterling crosses", "#a371f7", ["EURGBP", "EURAUD", "EURCAD", "EURNZD", "GBPAUD", "GBPCAD", "GBPNZD"],
     "Slow, range-bound, higher costs"),
    ("nor", "Nordic & Central Europe", "#79c0ff", ["EURSEK", "EURNOK", "USDSEK", "USDNOK", "USDHUF", "USDPLN", "EURHUF", "EURPLN", "USDCZK"],
     "Thin, expensive, central-bank anchored"),
    ("emk", "EM carry", "#ff7b72", ["USDMXN", "USDZAR", "USDSGD"],
     "Carry drift with violent risk-off spikes"),
    ("one", "Standalone", "#8b949e", ["USOIL", "BTCUSD"],
     "Oil: inventory and OPEC events. Bitcoin: Mon-Fri only on this broker"),
]


# trader 2026-10-04: every trade screen labels the symbol with its group, and shows the group's brief strategy (+ advice where
# the trader gave one). Only the commodity bloc has its own management so far; every other group runs the standard stack.
# trader 2026-10-09: staged entry and the +1.5R pyramid add are off - every position is 100% at entry for its whole life.
STANDARD = "Standard management: 100% at the signal (no staged entry, no +1.5R add); bank at +1R."
STRATEGY = {
    "com": "Commodity-bloc T+L1.0: 100% at the signal. At the 6th H4 bar close (~24h of trading) the trade is closed unless "
           "+1.5R was reached, and at +1.5R the stop moves to +1R. A-grade setups only.",
}
ADVICE = {
    "com": "Slow commodity-bloc trades are dead trades. Winners win fast or not at all - ruthlessly cut negative or slow trades "
           "within the first day. Tight stops; risky group - take it only if the setup looks very good.",
}


def as_json() -> list[dict]:
    return [{"k": k, "name": n, "col": c, "syms": s, "how": h, "strategy": STRATEGY.get(k, STANDARD), "advice": ADVICE.get(k, "")}
            for k, n, c, s, h in GROUPS]


def root(symbol: str) -> str:
    """'AUDUSD.sim' / 'AUDUSD.dk' / 'audusd' -> 'AUDUSD' (broker / data suffix stripped)."""
    return (symbol or "").split(".")[0].upper()


_BY_ROOT = {s: (k, n, c) for k, n, c, ss, _ in GROUPS for s in ss}


def group_of(symbol: str) -> dict | None:
    """the symbol's group: {k, name, col, strategy, advice}, or None for a symbol outside every group (never an error)."""
    g = _BY_ROOT.get(root(symbol))
    if not g: return None
    k, n, c = g
    return {"k": k, "name": n, "col": c, "strategy": STRATEGY.get(k, STANDARD), "advice": ADVICE.get(k, "")}


# trader 2026-10-03: the research lock (Data visualizers). When the lock is on the page hides every trade of a LOCKED symbol, and every
# 2025-26 trade of any symbol not in EXPOSED. AUDUSD/NZDUSD/USDCAD are exposed (the trader has studied them, incl. 2025-26). The three
# commodity-bloc crosses (AUDNZD, AUDCAD, NZDCAD) were locked until their one holdout look (spent 2026-10-04) and are now exposed too.
RESEARCH_LOCK = {
    "locked": ["USDCHF", "EURJPY", "GBPJPY", "EURGBP", "USDMXN", "XAGUSD", "US30"],
    "period_from": 2025,
    "exposed": ["AUDUSD", "NZDUSD", "USDCAD", "AUDNZD", "AUDCAD", "NZDCAD"],   # the bloc crosses' one holdout look was spent 2026-10-04
}
