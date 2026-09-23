"""
sysres.py - box CPU / RAM / disk for the dashboard (trader 2026-09-23). stdlib only: the Windows numbers come from
kernel32 via ctypes (no psutil, no subprocess per page load), with a /proc fallback so the local test config works.
CPU is a delta between calls, cached for `MIN_S` so several page loads in a row do not re-sample.
"""
from __future__ import annotations
import ctypes, os, time

MIN_S = 2.0
_last: dict = {"t": 0.0, "idle": 0.0, "busy": 0.0, "cpu": None}

def _win_cpu() -> float | None:
    """% of the box's CPU in use, from GetSystemTimes deltas."""
    class FT(ctypes.Structure): _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
    idle, kern, user = FT(), FT(), FT()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)): return None
    v = lambda f: (f.hi << 32) | f.lo
    i, k, u = v(idle), v(kern), v(user)
    busy = (k + u) - i                                   # kernel time INCLUDES idle
    prev_i, prev_b = _last["idle"], _last["busy"]
    _last["idle"], _last["busy"] = i, busy
    di, db = i - prev_i, busy - prev_b
    if prev_i == 0 or di + db <= 0: return _last["cpu"]
    return max(0.0, min(100.0, db / (di + db) * 100.0))

def _linux_cpu() -> float | None:
    try:
        with open("/proc/stat") as f: p = [float(x) for x in f.readline().split()[1:]]
    except OSError: return None
    idle = p[3] + (p[4] if len(p) > 4 else 0); busy = sum(p) - idle
    prev_i, prev_b = _last["idle"], _last["busy"]
    _last["idle"], _last["busy"] = idle, busy
    di, db = idle - prev_i, busy - prev_b
    if prev_i == 0 or di + db <= 0: return _last["cpu"]
    return max(0.0, min(100.0, db / (di + db) * 100.0))

def _win_mem() -> tuple[float, float] | None:
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32), ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)): return None
    G = 1 << 30
    return (m.ullTotalPhys - m.ullAvailPhys) / G, m.ullTotalPhys / G

def _linux_mem() -> tuple[float, float] | None:
    try:
        d = {}
        with open("/proc/meminfo") as f:
            for ln in f:
                k, _, v = ln.partition(":")
                d[k] = float(v.split()[0]) * 1024
    except OSError: return None
    G = 1 << 30
    return (d["MemTotal"] - d.get("MemAvailable", d["MemFree"])) / G, d["MemTotal"] / G

def _disk(path: str) -> tuple[float, float] | None:
    try:
        u = __import__("shutil").disk_usage(path); G = 1 << 30
        return u.used / G, u.total / G
    except OSError: return None

def read(cfg: dict | None = None) -> dict:
    """{cpu_pct, mem_used_gb, mem_total_gb, disk_used_gb, disk_total_gb} - any value may be None."""
    now = time.time(); win = os.name == "nt"
    if now - _last["t"] >= MIN_S:
        sample = _win_cpu if win else _linux_cpu
        if _last["idle"] == 0.0: sample(); time.sleep(0.15)      # first call has no delta: take a short baseline
        _last["cpu"] = sample(); _last["t"] = now
    mem = (_win_mem() if win else _linux_mem()) or (None, None)
    root = (cfg or {}).get("root") or ("C:\\" if win else "/")
    dsk = _disk(root[:3] if win else root) or _disk("C:\\" if win else "/") or (None, None)
    return {"cpu_pct": _last["cpu"], "mem_used_gb": mem[0], "mem_total_gb": mem[1], "disk_used_gb": dsk[0], "disk_total_gb": dsk[1]}
