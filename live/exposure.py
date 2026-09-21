"""
exposure.py - the Open exposure block on the LIVE setup card (coach 2026-09-21).

Server-computed and deterministic, same principle as the swing table: the advisor never does this arithmetic. The
block lists every open position (EA positions from the queue's positions/ files; any other account position from the
terminal feed, flagged not-EA-managed), decomposes each into signed currency legs / asset clusters weighted at the
position's risk % at entry, nets them per currency and per cluster, states what THIS signal would add, carries the
account aggregate risk-to-stop and FTMO headroom (the §11-7 numbers the EA enforces), and lists every other parked
signal - approving several could stack. CLAUDE.live.md §Open exposure uses it for the correlation check.

Decomposition (coach ruling): FX pair = base leg signed by direction, quote leg opposite (long EURUSD = long EUR /
short USD). US100/US500/US30 -> "US equity index" + USD leg; USOIL -> "energy" + USD; XAUUSD/XAGUSD -> "metals" + USD;
BTCUSD -> "crypto" + USD. A USD-quoted asset bought is USD sold, so the USD leg carries the opposite sign.
"""
from __future__ import annotations
import time
from live import common as C

CLUSTERS = {"US100": "US equity index", "US500": "US equity index", "US30": "US equity index",
            "USOIL": "energy", "XAUUSD": "metals", "XAGUSD": "metals", "BTCUSD": "crypto"}

def root(symbol: str) -> str: return (symbol or "").split(".")[0].upper()

def legs(symbol: str, direction: str, risk_pct: float) -> list[tuple[str, float]]:
    """[(bucket, signed risk %)] - + = long that currency/cluster, - = short."""
    r = root(symbol); s = 1.0 if str(direction).upper() in ("BUY", "LONG", "1") else -1.0
    if r in CLUSTERS: return [(CLUSTERS[r], s * risk_pct), ("USD", -s * risk_pct)]
    if len(r) == 6 and r.isalpha(): return [(r[:3], s * risk_pct), (r[3:], -s * risk_pct)]
    return [(r, s * risk_pct)]

def is_cluster(bucket: str) -> bool: return bucket in set(CLUSTERS.values())

def _net(entries: list[tuple[str, float]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for b, v in entries:
        d = out.setdefault(b, {"net": 0.0, "long": 0.0, "short": 0.0})
        d["net"] += v; d["long" if v > 0 else "short"] += abs(v)
    return out

def _bucket_txt(b: str, d: dict) -> str:
    n = d["net"]; side = "long" if n > 1e-9 else ("short" if n < -1e-9 else "flat")
    hedge = f" (hedged: +{d['long']:.2f} / −{d['short']:.2f})" if d["long"] > 0 and d["short"] > 0 else ""
    return f"{b} {side} {abs(n):.2f}%{hedge}"

def net_line(entries: list[tuple[str, float]]) -> str:
    nets = _net(entries)
    if not nets: return "(no open exposure)"
    order = sorted(nets.items(), key=lambda kv: (is_cluster(kv[0]), -abs(kv[1]["net"]), kv[0]))   # currencies, then clusters
    return " · ".join(_bucket_txt(b, d) for b, d in order)

def _hours_in_trade(p: dict, offset_h: float | None) -> str:
    bars = p.get("bars_open")
    if offset_h is not None and p.get("opened_at"):
        h = (time.time() + offset_h * 3600 - float(p["opened_at"])) / 3600.0
        return f"{h:.0f}h ({bars} H4 bar{'' if bars == 1 else 's'})" if bars is not None else f"{h:.0f}h"
    return f"~{int(bars) * 4}h ({bars} H4 bar{'' if bars == 1 else 's'})" if bars is not None else "?"

def _server_offset_h(symbol: str) -> float | None:
    """broker clock minus UTC, from a live quote (OANDA is +3 in summer, +2 in winter - never hard-code it)."""
    try:
        from live import mt5feed
        k = mt5feed.tick(symbol)
        if k and k.get("t"): return round((k["t"] - time.time()) / 3600.0)
    except Exception: pass
    return None

def _account_positions() -> list[dict] | None:
    try:
        from live import mt5feed
        return mt5feed.positions()
    except Exception: return None

def newest_heartbeat(cfg: dict) -> dict | None:
    best = None
    for sym in C.symbols(cfg):
        hb = C.heartbeat(cfg, sym)
        if hb and (best is None or (C.parse_iso(hb.get("ts")) or C.now_utc()) > (C.parse_iso(best.get("ts")) or C.now_utc())): best = hb
    return best

def build(sig: dict, cfg: dict) -> str:
    """the markdown block for setup.md."""
    lines = ["- **Open exposure (server-computed — authoritative; do not redo this arithmetic):**"]
    pos = C.list_positions(cfg)
    entries: list[tuple[str, float]] = []
    ea_ids = set()
    offset = _server_offset_h(pos[0]["symbol"]) if pos else None
    if not pos: lines.append("    - open positions: none (EA-managed)")
    for p in pos:
        ea_ids.add(int(p.get("posid") or 0))
        rp = p.get("risk_pct_effective")
        rtxt = f"{float(rp) * 100:.2f}%" if rp else "?"
        lines.append(f"    - {p['symbol']} {p.get('direction')} ({p.get('strategy')} #{p.get('signal_id')}) · risk at entry {rtxt} · open {C.r_fmt(p.get('open_r'))}"
                     f"{' · banked' if p.get('banked') else ''} · in trade {_hours_in_trade(p, offset)}")
        if rp: entries += legs(p["symbol"], p.get("direction", ""), float(rp) * 100)
        else: lines.append(f"      (risk % at entry unknown for {p['symbol']} #{p.get('signal_id')} — left out of the currency net below)")
    acct = _account_positions()
    if acct is None:
        lines.append("    - account cross-check: terminal feed unavailable — positions not opened by the EA cannot be listed")
    else:
        for a in acct:
            if int(a.get("ticket") or 0) in ea_ids: continue
            rp = a.get("risk_pct")
            lines.append(f"    - {a['symbol']} {a.get('direction')} · NOT EA-MANAGED (ticket {a.get('ticket')}) · risk to current stop "
                         f"{(f'{rp:.2f}%' if rp is not None else 'unlimited (no SL)')} · {a.get('volume')} lots")
            if rp: entries += legs(a["symbol"], a.get("direction", ""), rp)
    lines.append(f"    - net by currency / cluster (risk % at entry, signed): {net_line(entries)}")
    # what this signal would add
    sz = sig.get("sizing") or {}; rp_new = float(sz.get("risk_pct_effective") or 0) * 100
    if rp_new > 0:
        add = legs(sig.get("symbol", ""), sig.get("direction", ""), rp_new)
        after = _net(entries + add)
        parts = [f"{b} {'+' if v > 0 else '−'}{abs(v):.2f}% → {_bucket_txt(b, after[b])}" for b, v in add]
        lines.append(f"    - this signal ({sig.get('symbol')} {sig.get('direction')} at {rp_new:.2f}%) would add: " + "; ".join(parts))
        same = sorted({root(p["symbol"]) for p in pos if root(p["symbol"]) in CLUSTERS and CLUSTERS[root(p["symbol"])] == CLUSTERS.get(root(sig.get("symbol", "")))})
        if same: lines.append(f"    - already open in the same cluster ({CLUSTERS[root(sig.get('symbol', ''))]}): {', '.join(same)}")
    # account numbers the EA enforces (§11-7)
    hb = newest_heartbeat(cfg)
    if hb:
        f = hb.get("ftmo") or {}; eq = float(hb.get("equity") or 0); agg = float(hb.get("aggregate_risk_to_stop") or 0)
        head = (f" · daily headroom {f.get('headroom_daily', 0):,.0f} · max-loss headroom {f.get('headroom_max', 0):,.0f}"
                if f.get("initial_balance") else " · FTMO limits not yet captured")
        lines.append(f"    - account: aggregate risk-to-stop {agg:,.0f} ({(agg / eq * 100) if eq else 0:.2f}% of equity {eq:,.0f}){head}"
                     f" (heartbeat {hb.get('symbol')} {C.rel_time(C.parse_iso(hb.get('ts')))})")
    else:
        lines.append("    - account: no heartbeat — aggregate risk-to-stop and headroom unknown")
    # other parked signals
    others = [s for s in C.list_signals(cfg) if s.get("status") == "open" and s.get("signal_key") != sig.get("signal_key")]
    if others:
        lines.append("    - other signals parked awaiting a decision (approving several would stack): "
                     + "; ".join(f"{s['symbol']} {s.get('direction')} {s.get('strategy')} [{s.get('decision_class')}]"
                                 f" at {float((s.get('sizing') or {}).get('risk_pct_effective') or 0) * 100:.2f}%" for s in others))
    else:
        lines.append("    - other signals parked awaiting a decision: none")
    return "\n".join(lines)
