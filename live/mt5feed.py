"""
mt5feed.py - live bars and quotes straight from the server's MetaTrader terminal via the official `MetaTrader5`
Python package (local IPC to the running terminal; attaches, never launches). The EA publishes only trigger info;
charts and the advisor bundle pull bars from here. On a machine without the package (dev box) every call returns
None and callers fall back to the bars embedded in the signal JSON, if any.
"""
from __future__ import annotations
import threading, time
try:
    import MetaTrader5 as _mt5
except ImportError:      # not Windows / not installed
    _mt5 = None

_lock = threading.Lock()
_inited = False
_init_fail_at = 0.0
_cache: dict[tuple, tuple[float, list]] = {}
TF = {"h4": "TIMEFRAME_H4", "d1": "TIMEFRAME_D1", "h1": "TIMEFRAME_H1", "m15": "TIMEFRAME_M15"}
TERMINAL_PATH = r"C:\Program Files\OANDA MetaTrader 5\terminal64.exe"

FEED_URL = ""          # when set (web app / advisor), bars come from the feed daemon over localhost instead of in-process
def configure_url(url: str | None) -> None:
    global FEED_URL; FEED_URL = url or ""

def _remote(path: str, timeout: float = 4.0):
    import json as _j, urllib.request as _u
    try:
        with _u.urlopen(FEED_URL.rstrip("/") + path, timeout=timeout) as r: return _j.load(r)
    except Exception: return None

def available() -> bool: return bool(FEED_URL) or _mt5 is not None

def configure(path: str | None) -> None:
    global TERMINAL_PATH
    if path: TERMINAL_PATH = path

def _ensure() -> bool:
    global _inited, _init_fail_at
    if _mt5 is None: return False
    if _inited: return True
    if time.time() - _init_fail_at < 15: return False      # don't hammer a stopped terminal
    if not console_terminal_running():                      # NEVER let initialize() launch its own headless terminal
        _init_fail_at = time.time(); return False
    ok = _mt5.initialize(path=TERMINAL_PATH)                # attaches to the running terminal (same user)
    if not ok: _init_fail_at = time.time(); return False
    _inited = True; return True

def console_terminal_running() -> bool:
    """true when terminal64.exe runs in an interactive session (session != 0). A session-0 copy is a stray
    launched by the MetaTrader5 package and must not be attached to."""
    import os as _o, subprocess as _s
    if _o.name != "nt": return True
    try:
        out = _s.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            f = [x.strip('"') for x in line.split('","')]
            if len(f) >= 4 and f[0].lower() == "terminal64.exe" and f[3] not in ("0", "Services"): return True
    except Exception: return True
    return False

def _reset() -> None:
    global _inited
    try: _mt5.shutdown()
    except Exception: pass
    _inited = False

def bars(symbol: str, tf: str = "h4", n: int = 500, max_age_s: float = 2.0) -> list[list] | None:
    """[[t_epoch,o,h,l,c,v]] oldest->newest, or None when the terminal is unavailable."""
    if FEED_URL:
        r = _remote(f"/bars?symbol={symbol}&tf={tf}&n={n}"); return (r or {}).get("bars")
    key = (symbol, tf, n)
    with _lock:
        c = _cache.get(key)
        if c and time.time() - c[0] < max_age_s: return c[1]
        if not _ensure(): return None
        try:
            r = _mt5.copy_rates_from_pos(symbol, getattr(_mt5, TF[tf]), 0, n)
        except Exception:
            _reset(); return None
        if r is None:
            if _mt5.last_error()[0] != 1: _reset()
            return None
        out = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), int(x[5])] for x in r]
        _cache[key] = (time.time(), out)
        return out

def tick(symbol: str) -> dict | None:
    if FEED_URL:
        r = _remote(f"/tick?symbol={symbol}", 3.0); return (r or {}).get("tick")
    with _lock:
        if not _ensure(): return None
        try: k = _mt5.symbol_info_tick(symbol)
        except Exception: _reset(); return None
        if k is None: return None
        return {"t": int(k.time), "bid": float(k.bid), "ask": float(k.ask)}

ORDER_TYPES = {2: "BUY LIMIT", 3: "SELL LIMIT", 4: "BUY STOP", 5: "SELL STOP", 6: "BUY STOP LIMIT", 7: "SELL STOP LIMIT"}

def orders(symbol: str | None = None) -> list[dict] | None:
    """resting pending orders (all symbols or one), with the live quote and distance to the order price."""
    if FEED_URL:
        r = _remote("/orders" + (f"?symbol={symbol}" if symbol else ""), 4.0); return (r or {}).get("orders")
    with _lock:
        if not _ensure(): return None
        try:
            raw = _mt5.orders_get(symbol=symbol) if symbol else _mt5.orders_get()
        except Exception:
            _reset(); return None
        out = []
        for o in raw or []:
            t = _mt5.symbol_info_tick(o.symbol)
            buy = o.type in (2, 4, 6); px = (t.ask if buy else t.bid) if t else 0.0
            stop = abs(o.price_open - o.sl) if o.sl else 0.0
            out.append({"ticket": int(o.ticket), "symbol": o.symbol, "type": ORDER_TYPES.get(o.type, str(o.type)), "buy": buy,
                        "volume": float(o.volume_current), "price": float(o.price_open), "sl": float(o.sl), "tp": float(o.tp),
                        "magic": int(o.magic), "comment": o.comment, "setup": int(o.time_setup), "expiration": int(o.time_expiration),
                        "bid": float(t.bid) if t else None, "ask": float(t.ask) if t else None, "market": float(px),
                        "distance": abs(px - o.price_open) if px else None, "distance_r": (abs(px - o.price_open) / stop) if px and stop else None})
        return out

def status() -> dict:
    if FEED_URL:
        return _remote("/status", 3.0) or {"available": True, "connected": False, "daemon": "unreachable"}
    with _lock:
        if not _ensure(): return {"available": available(), "connected": False}
        try:
            ti = _mt5.terminal_info(); ai = _mt5.account_info()
            return {"available": True, "connected": bool(ti and ti.connected), "trade_allowed": bool(ti and ti.trade_allowed),
                    "login": ai.login if ai else None, "server": ai.server if ai else None, "equity": ai.equity if ai else None, "build": ti.build if ti else None}
        except Exception:
            _reset(); return {"available": True, "connected": False}
