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


def as_json() -> list[dict]:
    return [{"k": k, "name": n, "col": c, "syms": s, "how": h} for k, n, c, s, h in GROUPS]
