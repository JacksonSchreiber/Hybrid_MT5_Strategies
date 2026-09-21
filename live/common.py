"""
common.py - shared plumbing for the Phase-3 live services (docs/live-queue-schema.md is the contract).

Everything here is read-only on the EA's directories except: tasks/ (task files), config/trading_enabled.json
(kill switch), and the service-owned dirs under <root>/web, <root>/advisor, <root>/monitor.
"""
from __future__ import annotations
import csv, glob, json, os, re, sys, time, uuid, urllib.request, urllib.parse, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
VERBS_SIGNAL = ("approve", "skip", "delay")
VERBS_POSITION = ("close", "close50", "sl_be", "ratchet_tp1")
VERBS_ADMIN = ("test_signal",)          # trader-issued synthetic signal (live only)
VERBS_PENDING = ("cancel_pending",)     # trader cancels a resting pending order (target: signal_id)
SKIP_REASONS = {1: "Counter-trend", 2: "News / event", 3: "Ugly structure", 4: "Target blocked", 5: "Correlated", 6: "Gut / other"}
SKIP_REASON_CODES = {8: "no response (expired; a TAKE is not charged since 2026-09-21)", 7: "legacy", 9: "superseded (another signal approved)", 10: "EA auto-reject: FTMO headroom"}
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
    "feed": {"port": 8081, "url": ""},   # url set on the web/advisor side -> bars via the feedd process
    "brief": {"model": "claude-opus-5", "effort": "medium", "timeout_s": 150},
    "symbol_rules_file": "",          # per-symbol doctrine flags (deployed copy of config/symbol_rules.json)
    "expected_symbols": [],           # the lineup the post-reboot check waits for ([] = whatever heartbeats exist)
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
        from live import mt5feed; mt5feed.configure((cfg.get("mt5") or {}).get("terminal_path")); mt5feed.configure_url((cfg.get("feed") or {}).get("url"))
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


# ----------------------------------------------------------------------------- broker clock (found 2026-09-21)
# The EA writes signal times (signal_time, decision_bar, published_at, deadline) on the BROKER clock but labels them
# "Z", and builds sigtime_text / session / the events list against that clock. In the tester (.dk data on UTC) that was
# invisible; on OANDA the server runs EET/EEST, so live cards were 2-3 h off: deadlines too generous, sessions skewed,
# event hours short - and events in the first 2-3 h after the bar DROPPED (EA filter `t_utc < bar_server`). Every
# reader goes through tz_fix_signal(): display fields become true UTC, the raw values stay under *_server (charts match
# them against the feed's server-epoch bars), and the events list is rebuilt from the UTC calendar with the EA's filter.
def _last_sunday(y: int, m: int) -> datetime:
    d = datetime(y, m + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if m < 12 else datetime(y, 12, 31, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)

def server_offset_h(t_utc: datetime | None = None, cfg: dict | None = None) -> int:
    """OANDA MT5 server clock = EET/EEST: UTC+3 from the last Sunday of March 01:00 UTC to the last Sunday of October
    01:00 UTC, UTC+2 otherwise. cfg['server_offset_h'] (fixed int) overrides for another broker."""
    if cfg and cfg.get("server_offset_h") is not None: return int(cfg["server_offset_h"])
    t = t_utc or now_utc()
    return 3 if (_last_sunday(t.year, 3) + timedelta(hours=1)) <= t < (_last_sunday(t.year, 10) + timedelta(hours=1)) else 2

def _srv_to_utc(v, cfg) -> datetime | None:
    t = parse_iso(v)
    if not t: return None
    return t - timedelta(hours=server_offset_h(t - timedelta(hours=3), cfg))

def session_name(h: int) -> str:     # mirrors the EA's SessionName, fed the UTC hour
    return "Asia" if h < 7 else ("London" if h < 13 else ("New York" if h < 21 else "late/off-hours"))

_TOP_SKIP = ("german", "french", "spanish", "italian", "chinese", "japanese", "swiss", "canadian", "australian", "new zealand")
_TOP_KEYS = ("federal funds", "fomc", "rate decision", "official bank rate", "main refinancing", "cash rate", "cpi", "non-farm",
             "nonfarm", "gdp", "pce", "pmi", "unemployment rate", "ecb press", "monetary policy", "press conference", "interest rate",
             "rate statement", "bank rate", "average hourly earnings", "average earnings", "retail sales", "claimant count")
def _top_tier(cls: str, name: str) -> bool:   # the EA's g_ev_top
    if cls in ("V", "W", "C"): return True
    if cls: return False
    e = name.lower()
    return not any(x in e for x in _TOP_SKIP) and any(k in e for k in _TOP_KEYS)

def events_for(cfg: dict, symbol: str, from_utc: datetime, days: int = 14, cap: int = 15, election_days: int = 14) -> list[dict]:
    """the EA's BuildEventJson filter, on the UTC clock: [from, from+days], notable, base/quote/All, W anchored to its UTC
    midnight, binding = W inside the election horizon or V inside 6 h."""
    rows, _ = load_events(cfg); ccys = symbol_ccys(symbol); out = []
    for r in rows:
        t = r["t"]
        if t < from_utc or t > from_utc + timedelta(days=days) or not _top_tier(r["cls"], r["name"]) or r["ccy"] not in ccys: continue
        rt = t.replace(hour=0, minute=0, second=0) if r["cls"] == "W" else t
        hrs = max(0.0, (rt - from_utc).total_seconds() / 3600.0)
        out.append({"t_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "anchor_utc": rt.strftime("%Y-%m-%dT%H:%M:%SZ"), "hours_until": round(hrs, 1),
                    "ccy": r["ccy"], "name": r["name"], "cls": r["cls"], "sig": "[HIGH]" if r["cls"] in ("V", "W") else ("[MED]" if r["cls"] == "C" else "[LOW]"),
                    "label": EVENT_LABEL.get(r["cls"], ""), "binding": (r["cls"] == "W" and hrs <= election_days * 24.0) or (r["cls"] == "V" and hrs < 6.0)})
        if len(out) >= cap: break
    return out

def tz_fix_signal(s: dict | None, cfg: dict, events: bool = True) -> dict | None:
    if not s or s.get("_tz_fixed"): return s
    for k in ("signal_time", "decision_bar", "published_at", "deadline"):
        if s.get(k):
            s[k + "_server"] = s[k]; u = _srv_to_utc(s[k], cfg)
            if u: s[k + "_utc"] = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    for k in ("published_at", "deadline"):                         # display/sort fields -> true UTC
        if s.get(k + "_utc"): s[k] = s[k + "_utc"]
    db = parse_iso(s.get("decision_bar_utc"))
    if db:
        suffix = ""
        if "(+" in (s.get("sigtime_text") or ""): suffix = "  " + s["sigtime_text"][s["sigtime_text"].index("(+"):]
        s["sigtime_text"] = f"{db.strftime('%A')}  {db.strftime('%H:%M')} UTC{suffix}"
        s["session"] = session_name(db.hour)
        if events and s.get("symbol"):
            ev = events_for(cfg, s["symbol"], db)
            s["events_ea"] = s.get("events"); s["events"] = ev; s["events_count"] = len(ev)
    s["_tz_fixed"] = True
    return s

def list_signals(cfg: dict, symbol: str | None = None, light: bool = True) -> list[dict]:
    """all signal files (newest first). light=True drops the bar arrays to keep the dashboard cheap."""
    pat = f"{symbol}-*.json" if symbol else "*.json"
    out = []
    for p in glob.glob(os.path.join(cfg["root"], "signals", pat)):
        s = load_json(p)
        if not s or "signal_id" not in s: continue
        if light:
            s = {k: v for k, v in s.items() if k not in ("bars_h4", "bars_d1")}
        s["_path"] = p; out.append(tz_fix_signal(s, cfg, events=not light))
    out.sort(key=lambda s: (parse_iso(s.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), s.get("signal_id", 0)), reverse=True)
    return out

def signal(cfg: dict, key: str) -> dict | None:
    if not safe_key(key): return None
    s = load_json(os.path.join(cfg["root"], "signals", f"{key}.json"))
    if s: s["_path"] = os.path.join(cfg["root"], "signals", f"{key}.json")
    return tz_fix_signal(s, cfg)

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

# --- MT5 trade-server return codes -------------------------------------------------------------
# The EA can only report what the server gave it ("order_failed:10018"). The trader should not have
# to look the number up, so every place that SHOWS an ack reason runs it through reason_text().
RETCODE = {
    10004: "requote", 10006: "rejected by dealer", 10007: "cancelled by trader", 10008: "order placed",
    10009: "done", 10010: "done partially", 10011: "request processing error", 10012: "request timed out",
    10013: "invalid request", 10014: "invalid volume", 10015: "invalid price", 10016: "invalid stops",
    10017: "trading disabled", 10018: "market closed", 10019: "not enough money", 10020: "prices changed",
    10021: "no quotes to process", 10022: "invalid order expiration", 10023: "order state changed",
    10024: "too many requests", 10025: "no changes in the request", 10026: "autotrading disabled by the server",
    10027: "autotrading disabled by the terminal", 10028: "request locked by dealer",
    10029: "order or position frozen", 10030: "unsupported filling type", 10031: "no connection to the trade server",
    10032: "real accounts only", 10033: "pending-order limit reached", 10034: "volume limit reached",
    10035: "invalid or prohibited order type", 10036: "position already closed",
    10038: "close volume exceeds the position", 10039: "a close order already exists",
    10040: "position limit reached", 10041: "request rejected, order cancelled", 10042: "long positions only",
    10043: "short positions only", 10044: "close-only", 10045: "FIFO close required",
    10046: "opposite positions prohibited",
}

def reason_text(reason) -> str:
    """'order_failed:10018' -> 'order_failed:10018 (market closed)'. Anything else is passed through
    unchanged, so a reason the EA spells out in words (be_floor, stale_task, ...) reads as before."""
    r = str(reason or "")
    m = re.search(r"order_failed:(\d{4,6})\b", r)
    if not m: return r
    t = RETCODE.get(int(m.group(1)))
    return r if not t else r[:m.end()] + f" ({t})" + r[m.end():]

def row_state(cfg: dict, symbol: str, signal_id) -> dict | None:
    """the EA's own state for a journal row (state/<SYM>/<id>.json): decision, placed_time, order_ticket, posid..."""
    try: sid = int(signal_id)
    except (TypeError, ValueError): return None
    return load_json(os.path.join(cfg["root"], "state", symbol, f"{sid}.json")) if safe_key(symbol) else None

PENDING_EXPIRY_BARS = 3   # EA input InpPendingExpiryBars (H4 bars after the placement bar)
def pending_cancel_at(placed_server_epoch: int, bars: int = PENDING_EXPIRY_BARS, server_offset_h: int = 3) -> datetime:
    """UTC time the EA cancels an unfilled pending order. placed_time is BROKER-clock epoch (TimeCurrent); H4 bars align to
    the broker clock (OANDA UTC+3), so count N bar opens after the placement bar in broker time, skipping weekends, then convert."""
    t = datetime.fromtimestamp(placed_server_epoch - placed_server_epoch % 14400, timezone.utc); n = 0
    while n < bars:
        t += timedelta(hours=4)
        if t.weekday() < 5: n += 1
    return t - timedelta(hours=server_offset_h)

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
    if verb not in VERBS_SIGNAL + VERBS_POSITION + VERBS_ADMIN + VERBS_PENDING: raise ValueError("unknown verb")
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
_EV_CACHE: dict = {}
def load_events(cfg: dict) -> tuple[list[dict], datetime | None]:
    """rows: {t: datetime, ccy, name, cls, sig}; returns (rows sorted, coverage_end). Cached per file mtime."""
    p = os.path.join(cfg["common_files"], "econ_events.csv"); rows = []
    try: mt = os.path.getmtime(p)
    except OSError: mt = None
    if mt is not None and _EV_CACHE.get("p") == p and _EV_CACHE.get("mt") == mt: return _EV_CACHE["v"]
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
    v = (rows, (rows[-1]["t"] if rows else None))
    if mt is not None: _EV_CACHE.update(p=p, mt=mt, v=v)
    return v

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

# ----------------------------------------------------------------------------- per-symbol doctrine flags
_RULES_CACHE: dict = {}
def symbol_rules(cfg: dict, symbol: str) -> dict:
    """config/symbol_rules.json as deployed beside live_config.json (class, session flags, costs). {} when absent."""
    p = (cfg.get("symbol_rules_file") or os.path.join(os.path.dirname(cfg.get("_path", "") or "."), "symbol_rules.json"))
    mt = 0.0
    try: mt = os.path.getmtime(p)
    except OSError: return {}
    if _RULES_CACHE.get("_path") != p or _RULES_CACHE.get("_mtime") != mt:
        _RULES_CACHE.clear(); _RULES_CACHE.update({"_path": p, "_mtime": mt, "_data": load_json(p, {}) or {}})
    return (_RULES_CACHE["_data"] or {}).get((symbol or "").split(".")[0].upper(), {})

def symbol_rules_line(cfg: dict, symbol: str) -> str:
    r = symbol_rules(cfg, symbol)
    if not r: return ""
    return (f"class: {r.get('class')} · late-Friday rule {r.get('late_friday_rule')} · weekend-hold flag {r.get('weekend_hold_flag')} · "
            f"overnight-timing steps {r.get('overnight_timing_steps')} · event rows: {r.get('event_rows')} (config/symbol_rules.json)")

def tv_symbol(cfg: dict, symbol: str) -> str:
    root = symbol.split(".")[0].upper()
    return (cfg.get("tv_symbols") or {}).get(root, f"OANDA:{root}")

def r_fmt(x) -> str:
    try: return f"{float(x):+.2f}R"
    except (TypeError, ValueError): return "-"
