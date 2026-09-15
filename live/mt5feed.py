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

def available() -> bool: return _mt5 is not None

def configure(path: str | None) -> None:
    global TERMINAL_PATH
    if path: TERMINAL_PATH = path

def _ensure() -> bool:
    global _inited, _init_fail_at
    if _mt5 is None: return False
    if _inited: return True
    if time.time() - _init_fail_at < 15: return False      # don't hammer a stopped terminal
    ok = _mt5.initialize(path=TERMINAL_PATH)                # attaches to the running terminal (same user)
    if not ok: _init_fail_at = time.time(); return False
    _inited = True; return True

def _reset() -> None:
    global _inited
    try: _mt5.shutdown()
    except Exception: pass
    _inited = False

def bars(symbol: str, tf: str = "h4", n: int = 500, max_age_s: float = 2.0) -> list[list] | None:
    """[[t_epoch,o,h,l,c,v]] oldest->newest, or None when the terminal is unavailable."""
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
    with _lock:
        if not _ensure(): return None
        try: k = _mt5.symbol_info_tick(symbol)
        except Exception: _reset(); return None
        if k is None: return None
        return {"t": int(k.time), "bid": float(k.bid), "ask": float(k.ask)}

def status() -> dict:
    with _lock:
        if not _ensure(): return {"available": available(), "connected": False}
        try:
            ti = _mt5.terminal_info(); ai = _mt5.account_info()
            return {"available": True, "connected": bool(ti and ti.connected), "trade_allowed": bool(ti and ti.trade_allowed),
                    "login": ai.login if ai else None, "server": ai.server if ai else None, "equity": ai.equity if ai else None, "build": ti.build if ti else None}
        except Exception:
            _reset(); return {"available": True, "connected": False}
