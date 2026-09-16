"""
common.py - shared plumbing for the Phase-3 live services (docs/live-queue-schema.md is the contract).

Everything here is read-only on the EA's directories except: tasks/ (task files), config/trading_enabled.json
(kill switch), and the service-owned dirs under <root>/web, <root>/advisor, <root>/monitor.
"""
from __future__ import annotations
import csv, glob, json, os, sys, time, uuid, urllib.request, urllib.parse, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
VERBS_SIGNAL = ("approve", "skip", "delay")
VERBS_POSITION = ("close", "close50", "sl_be", "ratchet_tp1")
SKIP_REASONS = {1: "Counter-trend", 2: "News / event", 3: "Ugly structure", 4: "Target blocked", 5: "Correlated", 6: "Gut / other"}
SKIP_REASON_CODES = {8: "no response (expired)", 7: "legacy"}
EVENT_LABEL = {"W": "NO-HOLD (election)", "V": "NO ENTRY <6h (big release)", "C": "caution", "H": "holiday/thin"}

# ----------------------------------------------------------------------------- config
_DEFAULT_CONFIG = {
    "root": "",                       # queue root = <Terminal>\Common\Files\live
    "common_files": "",               # <Terminal>\Common\Files (econ_events.csv lives here)
    "symbols": [],                    # [] = discover from heartbeat_*.json
    "web": {"bind": "10.77.0.1", "port": 8080, "base_url": "http://10.77.0.1:8080"},
    "telegram": {"token_file": "", "chat_id_file": ""},
    "advisor": {"claude_cmd": "claude", "model": "sonnet", "effort": "low", "timeout_s": 150,
                "live_dir": "", "node_path": "", "token_file": ""},
    "monitor": {"interval_s": 15, "heartbeat_stale_s": 180, "headroom_warn_pct": [0.02, 0.01],
                "calendar_min_days": 14, "daily_summary_utc": "21:05", "mt5_task": "hybrid-mt5",
                "mt5_process": "terminal64", "web_task": "hybrid-web", "advisor_task": "hybrid-advisor"},
    "logs_dir": "",
    # TradingView symbol per broker-symbol root (the widget runs on the phone's own internet, not through the box)
    "tv_symbols": {"EURUSD": "OANDA:EURUSD", "GBPUSD": "OANDA:GBPUSD", "USDJPY": "OANDA:USDJPY", "AUDUSD": "OANDA:AUDUSD", "USDCAD": "OANDA:USDCAD",
                   "USDCHF": "OANDA:USDCHF", "NZDUSD": "OANDA:NZDUSD", "US100": "OANDA:NAS100USD", "US500": "OANDA:SPX500USD", "US30": "OANDA:US30USD",
                   "XAUUSD": "OANDA:XAUUSD", "XAGUSD": "OANDA:XAGUSD", "USOIL": "OANDA:WTICOUSD", "UKOIL": "OANDA:BCOUSD"},
    "tv_widget": True,
    "mt5": {"terminal_path": "C:\\Program Files\\OANDA MetaTrader 5\\terminal64.exe"},
    "calendar": {"dir": "", "refresh_utc": "02:30", "stale_days": 7, "python": "", "feed_url": ""},
}

def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _deep_merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out

def load_config(path: str | None = None) -> dict:
    p = path or os.environ.get("HYBRID_LIVE_CONFIG") or str(Path(__file__).with_name("live_config.json"))
    cfg = dict(_DEFAULT_CONFIG)
    if os.path.exists(p):
        cfg = _deep_merge(cfg, json.load(open(p, encoding="utf-8")))
    cfg["_path"] = p
    try:
        from live import mt5feed; mt5feed.configure((cfg.get("mt5") or {}).get("terminal_path"))
    except Exception: pass
    if not cfg["root"]:
        raise SystemExit(f"live config {p}: 'root' (queue root) is required")
    if not cfg["common_files"]:
        cfg["common_files"] = str(Path(cfg["root"]).parent)
    if not cfg["logs_dir"]:
        cfg["logs_dir"] = os.path.join(cfg["root"], "logs")
    return cfg

# ----------------------------------------------------------------------------- time
def now_utc() -> datetime: return datetime.now(timezone.utc)
def now_iso() -> str: return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
def parse_iso(s: str | None) -> datetime | None:
    if not s: return None
    try: return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError: return None
def fmt_dt(d: datetime | None, with_day=True) -> str:
    if not d: return "-"
    return d.strftime("%a %d %b %H:%M" if with_day else "%d %b %H:%M") + " UTC"
def rel_time(d: datetime | None, ref: datetime | None = None) -> str:
    if not d: return "-"
    s = int(((ref or now_utc()) - d).total_seconds()); past = s >= 0; s = abs(s)
    txt = f"{s}s" if s < 90 else f"{s//60}m" if s < 5400 else f"{s//3600}h {s%3600//60:02d}m" if s < 172800 else f"{s//86400}d {s%86400//3600}h"
    return (txt + " ago") if past else ("in " + txt)

# ----------------------------------------------------------------------------- files
def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f: return json.load(f)
    except (OSError, ValueError): return default

def atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f: f.write(text)
    os.replace(tmp, path)

def atomic_write_json(path: str, obj) -> None:
    atomic_write_text(path, json.dumps(obj, separators=(",", ":"), ensure_ascii=True))

def append_line(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f: f.write(line.rstrip("\n") + "\n")

def read_secret(path: str) -> str:
    with open(path, encoding="ascii", errors="ignore") as f: return f.read().strip()

class Log:
    """tiny stdout+file logger (services run under a scheduled task; stdout goes to the task log)."""
    def __init__(self, name: str, logs_dir: str):
        self.name = name; self.path = os.path.join(logs_dir, f"{name}.log"); os.makedirs(logs_dir, exist_ok=True)
        self._lock = threading.Lock()
    def __call__(self, msg: str) -> None:
        line = f"{now_iso()} {self.name}: {msg}"
        with self._lock:
            print(line, flush=True)
            try:
                with open(self.path, "a", encoding="utf-8") as f: f.write(line + "\n")
            except OSError: pass

# ----------------------------------------------------------------------------- queue readers
def symbols(cfg: dict) -> list[str]:
    if cfg["symbols"]: return list(cfg["symbols"])
    return sorted(os.path.basename(p)[len("heartbeat_"):-len(".json")] for p in glob.glob(os.path.join(cfg["root"], "heartbeat_*.json")))

def heartbeat(cfg: dict, symbol: str) -> dict | None:
    p = os.path.join(cfg["root"], f"heartbeat_{symbol}.json"); hb = load_json(p)
    if hb is not None:
        try: hb["_mtime"] = datetime.fromtimestamp(os.path.getmtime(p), timezone.utc)
        except OSError: hb["_mtime"] = None
    return hb

def heartbeat_age_s(hb: dict | None) -> float | None:
    if not hb: return None
    t = parse_iso(hb.get("ts")) or hb.get("_mtime")
    return (now_utc() - t).total_seconds() if t else None

def list_signals(cfg: dict, symbol: str | None = None, light: bool = True) -> list[dict]:
    """all signal files (newest first). light=True drops the bar arrays to keep the dashboard cheap."""
    pat = f"{symbol}-*.json" if symbol else "*.json"
    out = []
    for p in glob.glob(os.path.join(cfg["root"], "signals", pat)):
        s = load_json(p)
        if not s or "signal_id" not in s: continue
        if light:
            s = {k: v for k, v in s.items() if k not in ("bars_h4", "bars_d1")}
        s["_path"] = p; out.append(s)
    out.sort(key=lambda s: (parse_iso(s.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), s.get("signal_id", 0)), reverse=True)
    return out

def signal(cfg: dict, key: str) -> dict | None:
    if not safe_key(key): return None
    s = load_json(os.path.join(cfg["root"], "signals", f"{key}.json"))
    if s: s["_path"] = os.path.join(cfg["root"], "signals", f"{key}.json")
    return s

def list_positions(cfg: dict, closed: bool = False) -> list[dict]:
    d = os.path.join(cfg["root"], "positions", "closed" if closed else "")
    out = []
    for p in glob.glob(os.path.join(d, "*.json")):
        x = load_json(p)
        if x and "posid" in x: x["_path"] = p; out.append(x)
    out.sort(key=lambda x: x.get("posid", 0), reverse=True)
    return out

def position(cfg: dict, key: str) -> dict | None:
    if not safe_key(key): return None
    for sub in ("", "closed"):
        x = load_json(os.path.join(cfg["root"], "positions", sub, f"{key}.json"))
        if x: x["_closed"] = (sub == "closed"); return x
    return None

def ack(cfg: dict, task_id: str) -> dict | None:
    return load_json(os.path.join(cfg["root"], "acks", f"{task_id}.json")) if safe_key(task_id) else None

def kill_switch(cfg: dict) -> bool | None:
    j = load_json(os.path.join(cfg["root"], "config", "trading_enabled.json"))
    return None if j is None else bool(j.get("trading_enabled", False))

def set_kill_switch(cfg: dict, enabled: bool) -> None:
    atomic_write_json(os.path.join(cfg["root"], "config", "trading_enabled.json"), {"trading_enabled": bool(enabled), "set_at": now_iso(), "set_by": "web"})

def risk_mult(cfg: dict) -> dict:
    return load_json(os.path.join(cfg["root"], "config", "risk_mult.json"), {}) or {}

def journal_rows(cfg: dict, symbol: str | None = None, months: int | None = None) -> list[dict]:
    pat = f"{symbol}_*.csv" if symbol else "*_*.csv"
    files = sorted(p for p in glob.glob(os.path.join(cfg["root"], "journal", pat))
                   if not any(x in os.path.basename(p) for x in (".actions.", ".delays.", ".inv.", ".part.")))
    if months: files = files[-months:]
    rows = []
    for p in files:
        try:
            with open(p, encoding="ascii", errors="replace", newline="") as f:
                for r in csv.DictReader(f): r["_file"] = os.path.basename(p); rows.append(r)
        except OSError: pass
    return rows

def audit_tail(cfg: dict, symbol: str, n: int = 40) -> list[str]:
    p = os.path.join(cfg["root"], f"audit_{symbol}.log")
    try:
        with open(p, encoding="ascii", errors="replace") as f: lines = f.read().splitlines()
        return lines[-n:]
    except OSError: return []

def safe_key(s: str) -> bool:
    return bool(s) and len(s) <= 64 and all(c.isalnum() or c in "._-" for c in s)

# ----------------------------------------------------------------------------- tasks (S6: typed verbs only, ids the EA reported)
def write_task(cfg: dict, symbol: str, verb: str, params: dict, signal_id: int | None = None, position_id: int | None = None, issued_by: str = "web") -> str:
    if verb not in VERBS_SIGNAL + VERBS_POSITION: raise ValueError("unknown verb")
    tid = uuid.uuid4().hex
    t = {"schema_version": SCHEMA_VERSION, "task_id": tid, "symbol": symbol, "verb": verb, "params": params,
         "issued_at": now_iso(), "issued_by": issued_by}
    if signal_id is not None: t["signal_id"] = int(signal_id)
    if position_id is not None: t["position_id"] = int(position_id)
    atomic_write_json(os.path.join(cfg["root"], "tasks", f"{tid}.json"), t)
    append_line(os.path.join(cfg["root"], "web", "web_audit.log"), f"{now_iso()}|task|{tid}|{symbol}|{verb}|{json.dumps(params, separators=(',', ':'))}|sig={signal_id}|pos={position_id}|by={issued_by}")
    return tid

def wait_ack(cfg: dict, task_id: str, timeout_s: float = 12.0) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        a = ack(cfg, task_id)
        if a:
            append_line(os.path.join(cfg["root"], "web", "web_audit.log"), f"{now_iso()}|ack|{task_id}|{a.get('symbol')}|{a.get('verb')}|{a.get('result')}|{a.get('reason')}|refs={json.dumps(a.get('refs', {}), separators=(',', ':'))}")
            return a
        time.sleep(0.5)
    return None

# ----------------------------------------------------------------------------- events (econ_events.csv, same class letters the EA uses)
def load_events(cfg: dict) -> tuple[list[dict], datetime | None]:
    """rows: {t: datetime, ccy, name, cls, sig}; returns (rows sorted, coverage_end)."""
    p = os.path.join(cfg["common_files"], "econ_events.csv"); rows = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\r\n").split(",")
                if len(parts) < 2 or not parts[0][:4].isdigit(): continue
                try: t = datetime.strptime(parts[0].strip(), "%Y.%m.%d %H:%M").replace(tzinfo=timezone.utc)
                except ValueError: continue
                cls = (parts[6].strip() if len(parts) > 6 else "")
                rows.append({"t": t, "ccy": parts[1].strip(), "name": parts[2].strip() if len(parts) > 2 else "", "cls": cls,
                             "sig": parts[5].strip() if len(parts) > 5 else "", "label": EVENT_LABEL.get(cls, "")})
    except OSError: pass
    rows.sort(key=lambda r: r["t"])
    return rows, (rows[-1]["t"] if rows else None)

def symbol_ccys(symbol: str) -> set[str]:
    root = symbol.split(".")[0].upper()
    if len(root) == 6 and root.isalpha(): return {root[:3], root[3:], "All"}
    return {"USD", "All"}   # indices/energy/metals: USD-quoted

# ----------------------------------------------------------------------------- telegram (outbound only, S5)
def telegram_send(cfg: dict, text: str, log=None) -> bool:
    try:
        tok = read_secret(cfg["telegram"]["token_file"]); cid = read_secret(cfg["telegram"]["chat_id_file"])
    except OSError as e:
        if log: log(f"telegram: secrets unreadable: {e}")
        return False
    data = urllib.parse.urlencode({"chat_id": cid, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=15, context=_ssl_ctx()) as r:
            ok = json.load(r).get("ok", False)
    except Exception as e:  # network errors must never kill a service loop
        if log: log(f"telegram: send failed: {e}")
        return False
    return bool(ok)

_SSL = None
def _ssl_ctx():
    """a fresh Windows box has not cached every public root yet, so Python's default store can fail where the OS
    would lazily fetch it. Prefer certifi's bundle when installed; fall back to the system store."""
    global _SSL
    if _SSL is None:
        import ssl
        try:
            import certifi; _SSL = ssl.create_default_context(cafile=certifi.where())
        except ImportError: _SSL = ssl.create_default_context()
    return _SSL

def tv_symbol(cfg: dict, symbol: str) -> str:
    root = symbol.split(".")[0].upper()
    return (cfg.get("tv_symbols") or {}).get(root, f"OANDA:{root}")

def r_fmt(x) -> str:
    try: return f"{float(x):+.2f}R"
    except (TypeError, ValueError): return "-"
