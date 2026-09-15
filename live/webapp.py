r"""
webapp.py - the trader's phone-first web app (spec §5). stdlib ThreadingHTTPServer, bound to the WireGuard address.

Routes: /  dashboard | /signal/<key> | /position/<key> | /journal | /events | /settings | /health
        /chart/<key>/<h4|d1>.png | POST /task (typed verbs against ids the EA reported - S6) | POST /reply | POST /kill
Every task written and every ack received is appended to <root>/web/web_audit.log (S8). No login: the tunnel is
the boundary (trader ruling 2026-09-15); HTTP inside WireGuard.
"""
from __future__ import annotations
import html, json, os, sys, urllib.parse, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import charts

CFG: dict = {}
LOG = None
E = html.escape

CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--txt:#c9d1d9;--dim:#8b949e;--up:#3fb950;--dn:#f85149;--blue:#58a6ff;--gold:#ffd700;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:16px/1.4 -apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--blue);text-decoration:none}nav{display:flex;gap:4px;padding:8px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2;overflow-x:auto}
nav a{padding:6px 10px;border-radius:6px;white-space:nowrap}nav a.on{background:var(--card)}main{padding:10px;max-width:980px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.k{color:var(--dim);font-size:13px}.v{font-variant-numeric:tabular-nums}.big{font-size:22px;font-weight:600}h1{font-size:20px;margin:6px 0}h2{font-size:16px;margin:8px 0 4px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600;background:#21262d}.ok{color:var(--up)}.bad{color:var(--dn)}.warn{color:var(--warn)}.take{background:#1f3a24;color:var(--up)}.disc{background:#3a2f1f;color:var(--warn)}
.btn{display:inline-block;padding:12px 16px;border-radius:8px;border:1px solid var(--line);background:#21262d;color:var(--txt);font-size:16px;font-weight:600;cursor:pointer}
.btn.go{background:#238636;border-color:#2ea043;color:#fff}.btn.no{background:#8b2f2f;border-color:#a33;color:#fff}.btn.wait{background:#3d3520;color:#fff}.btn:disabled{opacity:.4}.btn.on{background:#30363d;border-color:#8b949e}
form.inline{display:inline}img.chart{width:100%;height:auto;border-radius:6px;border:1px solid var(--line);margin:6px 0}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--dim);font-weight:500}
pre{white-space:pre-wrap;word-break:break-word;font:14px/1.4 ui-monospace,Menlo,Consolas,monospace;background:#0b0f14;padding:10px;border-radius:6px;border:1px solid var(--line);margin:6px 0}
textarea,select,input{width:100%;background:#0b0f14;color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:10px;font-size:16px}
.ev.bind{color:var(--dn);font-weight:600}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}@media(max-width:640px){.grid2{grid-template-columns:1fr}}
.flash{padding:10px;border-radius:6px;margin:8px 0}.flash.ok{background:#12301a;border:1px solid #2ea043}.flash.bad{background:#3a1d1d;border:1px solid #a33}
.cd{font-weight:600}
"""
JS = """
function tick(){document.querySelectorAll('[data-deadline]').forEach(function(el){var d=new Date(el.dataset.deadline);var s=Math.floor((d-Date.now())/1000);
 if(s<=0){el.textContent='EXPIRED';el.className='cd bad';return;}var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;el.textContent=(h>0?h+'h ':'')+m+'m '+x+'s';el.className='cd '+(s<3600?'warn':'ok');});}
setInterval(tick,1000);tick();
var meta=document.querySelector('meta[name=autorefresh]');if(meta&&!document.querySelector('textarea:focus')){setTimeout(function(){if(!document.querySelector('textarea:focus'))location.reload();},parseInt(meta.content)*1000);}
"""

def page(title: str, body: str, active: str = "", refresh: int | None = None) -> str:
    tabs = [("/", "Home", "home"), ("/journal", "Journal", "journal"), ("/events", "Events", "events"), ("/settings", "Settings", "settings")]
    nav = "".join(f'<a href="{h}" class="{"on" if a == active else ""}">{t}</a>' for h, t, a in tabs)
    m = f'<meta name="autorefresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(title)}</title>{m}<style>{CSS}</style></head><body><nav>{nav}</nav><main>{body}</main><script>{JS}</script></body></html>')

def flash(msg: str | None, ok: bool = True) -> str:
    return f'<div class="flash {"ok" if ok else "bad"}">{E(msg)}</div>' if msg else ""

def status_pill(s: str) -> str:
    cls = {"open": "warn", "approved": "ok", "approved_pending": "ok", "skipped": "", "auto_skipped": "", "expired": "bad", "rejected": "bad"}.get(s, "")
    return f'<span class="pill {cls}">{E(s)}</span>'

def cls_pill(dc: str) -> str:
    return f'<span class="pill {"take" if dc == "TAKE" else "disc"}">{E(dc or "?")}</span>'

def ftmo_block(hb: dict) -> str:
    f = hb.get("ftmo") or {}
    if not f or not f.get("initial_balance"): return '<div class="k">FTMO limits: not yet captured (account not synced)</div>'
    eq = hb.get("equity", 0); hd = f.get("headroom_daily", 0); hm = f.get("headroom_max", 0)
    def pct(x): return f"{x / f['initial_balance'] * 100:.2f}%" if f.get("initial_balance") else "-"
    return (f'<div class="grid2"><div><div class="k">Daily headroom</div><div class="big v {"bad" if hd < 0.01 * f["initial_balance"] else "ok"}">{hd:,.0f} <span class="k">({pct(hd)})</span></div><div class="k">floor {f.get("daily_floor", 0):,.0f} · day {f.get("day_key", "")}</div></div>'
            f'<div><div class="k">Max-loss headroom</div><div class="big v {"bad" if hm < 0.01 * f["initial_balance"] else "ok"}">{hm:,.0f} <span class="k">({pct(hm)})</span></div><div class="k">floor {f.get("max_floor", 0):,.0f} · initial {f["initial_balance"]:,.0f}</div></div></div>'
            f'<div class="k">equity {eq:,.2f} · aggregate risk-to-stop {hb.get("aggregate_risk_to_stop", 0):,.0f} · buffer {f.get("buffer", 0):,.0f}</div>')

# ----------------------------------------------------------------------------- pages
def dashboard(q: dict) -> str:
    out = [flash(q.get("msg"), q.get("ok", "1") == "1")]
    syms = C.symbols(CFG)
    ks = C.kill_switch(CFG)
    from live import mt5feed
    fs = mt5feed.status()
    if fs.get("available"):
        out.append(f'<div class="k">terminal feed: {"connected" if fs.get("connected") else "<span class=bad>NOT CONNECTED</span>"} · {E(str(fs.get("server") or ""))} · build {fs.get("build")}</div>')
    out.append(f'<div class="card"><div class="row"><div><div class="k">Kill switch</div><div class="big {"ok" if ks else "bad"}">{"TRADING ENABLED" if ks else ("DISABLED" if ks is False else "DISABLED (no config file)")}</div></div>'
               f'<form method="post" action="/kill" class="inline" style="margin-left:auto"><input type="hidden" name="enable" value="{0 if ks else 1}"><button class="btn {"no" if ks else "go"}" onclick="return confirm(\'{"Disable" if ks else "Enable"} trading?\')">{"Disable" if ks else "Enable"}</button></form></div></div>')
    for sym in syms:
        hb = C.heartbeat(CFG, sym); age = C.heartbeat_age_s(hb)
        alive = hb and hb.get("status") == "running" and age is not None and age < CFG["monitor"]["heartbeat_stale_s"]
        flags = "" if not hb else ("" if hb.get("terminal_trade_allowed") and hb.get("mql_trade_allowed") else ' <span class="pill bad">AutoTrading OFF</span>')
        out.append(f'<div class="card"><div class="row"><span class="big">{E(sym)}</span><span class="pill {"ok" if alive else "bad"}">{"EA alive" if alive else "EA STALE / STOPPED"}</span>{flags}<span class="k">beat {C.rel_time(C.parse_iso(hb.get("ts")) if hb else None)}</span></div>'
                   + (ftmo_block(hb) if hb else "") + (f'<div class="k">alerts: {E(", ".join(hb.get("alerts") or []))}</div>' if hb and hb.get("alerts") else "") + '</div>')
    # open signals
    sigs = C.list_signals(CFG); opn = [s for s in sigs if s.get("status") == "open"]
    out.append("<h2>Pending signals</h2>")
    if not opn: out.append('<div class="card k">none</div>')
    for s in opn:
        out.append(f'<a href="/signal/{E(s["signal_key"])}"><div class="card"><div class="row"><span class="big">{E(s["symbol"])} {E(s["strategy"])} {E(s["direction"])}</span>{cls_pill(s.get("decision_class"))}<span class="k">#{s["signal_id"]}</span></div>'
                   f'<div class="row"><span class="k">deadline</span><span class="cd" data-deadline="{E(s.get("deadline", ""))}"></span><span class="k">delays {s.get("delay_count", 0)}</span><span class="k">{E((s.get("regime") or {}).get("pretty", ""))}</span></div></div></a>')
    # positions
    pos = C.list_positions(CFG)
    out.append("<h2>Open positions</h2>")
    if not pos: out.append('<div class="card k">none</div>')
    for p in pos:
        out.append(f'<a href="/position/{E(p["symbol"])}-{p["posid"]}"><div class="card"><div class="row"><span class="big">{E(p["symbol"])} {E(p["strategy"])} {E(p["direction"])}</span><span class="big v {"ok" if p.get("open_r", 0) >= 0 else "bad"}">{C.r_fmt(p.get("open_r"))}</span>'
                   f'<span class="k">banked {C.r_fmt(p.get("banked_r"))} · {p.get("lots_live")} lots · {p.get("bars_open")} bars</span></div></div></a>')
    # recent decided signals
    out.append("<h2>Recent signals</h2><div class='card'><table><tr><th>#</th><th>signal</th><th>status</th><th>time</th></tr>")
    for s in [x for x in sigs if x.get("status") != "open"][:12]:
        out.append(f'<tr><td><a href="/signal/{E(s["signal_key"])}">{s["signal_id"]}</a></td><td>{E(s["symbol"])} {E(s["strategy"])} {E(s["direction"])}</td><td>{status_pill(s.get("status", ""))}</td><td class="k">{E((s.get("published_at") or "")[:16].replace("T", " "))}</td></tr>')
    out.append("</table></div>")
    return page("Hybrid live", "".join(out), "home", refresh=30)

def signal_page(key: str, q: dict) -> str:
    s = C.signal(CFG, key)
    if not s: return page("Signal", '<div class="card bad">no such signal</div>')
    lv = s.get("levels") or {}; rr = s.get("rr") or {}; sz = s.get("sizing") or {}; rg = s.get("regime") or {}
    out = [flash(q.get("msg"), q.get("ok", "1") == "1")]
    is_open = s.get("status") == "open"
    out.append(f'<h1>{E(s["symbol"])} · {E(s["strategy"])} {E(s["direction"])} <span class="k">#{s["signal_id"]}</span></h1>'
               f'<div class="row">{cls_pill(s.get("decision_class"))}{status_pill(s.get("status", ""))}<span class="pill">{E(rg.get("pretty", ""))}</span>'
               + (f'<span class="k">deadline</span><span class="cd" data-deadline="{E(s.get("deadline", ""))}"></span>' if is_open else "") + f'<span class="k">{E(s.get("sigtime_text", ""))} · {E(s.get("session", ""))}</span></div>'
               f'<div class="k">{E(s.get("protocol_text", ""))}</div><div class="k">{E(s.get("strategy_text", ""))}</div>')
    if s.get("auto_reason"): out.append(f'<div class="flash bad">auto: {E(s["auto_reason"])}</div>')
    d = s.get("delay_count", 0)
    out.append(chart_block(s, lv))
    out.append(f'<details><summary class="k">static EA charts (what the advisor saw)</summary><img class="chart" src="/chart/{E(key)}/h4.png?d={d}" alt="H4"><img class="chart" src="/chart/{E(key)}/d1.png?d={d}" alt="D1"></details>')
    out.append(f'<div class="card"><div class="grid2"><div><span class="k">entry</span> <b class="v">{lv.get("entry")}</b>{" (STOP)" if lv.get("stop_entry") else ""}</div><div><span class="k">SL</span> <b class="v bad">{lv.get("sl")}</b></div>'
               f'<div><span class="k">TP1</span> <b class="v">{lv.get("tp1")}</b> <span class="k">{rr.get("tp1")}R</span></div><div><span class="k">TP2</span> <b class="v">{lv.get("tp2") or "-"}</b> <span class="k">{rr.get("runner")}R</span></div>'
               f'<div><span class="k">lots</span> <b class="v">{sz.get("lots")}</b> <span class="k">{E(sz.get("lots_line", ""))}</span></div><div><span class="k">risk mult</span> <b class="v">{sz.get("risk_mult_applied")}</b> <span class="k">→ {float(sz.get("risk_pct_effective", 0) or 0) * 100:.2f}%</span></div></div>'
               f'<div class="k">delays {d} · implicit streak {s.get("implicit_streak", 0)} · presented {E(s.get("published_at", ""))} · kill switch {"on" if s.get("trading_enabled") else "OFF"}</div></div>')
    # events
    evs = s.get("events") or []
    out.append('<div class="card"><h2>Events (next 14 d)</h2>')
    for e in evs[:20]:
        out.append(f'<div class="ev {"bind" if e.get("binding") else ""}">{E(C.fmt_dt(C.parse_iso(e.get("t_utc"))))} · {E(e.get("ccy", ""))} {E(e.get("name", ""))} <span class="k">[{E(e.get("cls", ""))} {E(e.get("label", ""))}]</span>{" BINDING" if e.get("binding") else ""}</div>')
    if not evs: out.append('<div class="k">no notable events inside the window</div>')
    _, cov = C.load_events(CFG); st = C.parse_iso(s.get("signal_time"))
    if cov and st and cov < st + timedelta(days=14): out.append(f'<div class="warn">calendar coverage ends {cov.strftime("%Y-%m-%d")} — later events unknown</div>')
    eg = s.get("election_gate") or {}
    if eg.get("hit"): out.append(f'<div class="bad">election gate: {E(eg.get("event", ""))}</div>')
    out.append("</div>")
    # advisor
    rec = C.load_json(os.path.join(CFG["root"], "advisor", "verdicts", f"{key}.json"))
    out.append('<div class="card"><h2>Advisor</h2>')
    if not rec or not rec.get("consults"): out.append('<div class="k">verdict pending…</div>')
    else:
        for c in rec["consults"]:
            if c.get("kind") == "reply": out.append(f'<div class="k">you · {E(c.get("ts", ""))}</div><pre>{E(c.get("prompt", ""))}</pre>')
            out.append(f'<div class="k">advisor · {E(c.get("ts", ""))} · {c.get("elapsed_s")}s</div><pre>{E(c.get("text") or ("ERROR: " + str(c.get("error"))))}</pre>')
    out.append(f'<form method="post" action="/reply"><input type="hidden" name="key" value="{E(key)}"><textarea name="text" rows="2" placeholder="reply to the advisor (same session)"></textarea><div style="margin-top:6px"><button class="btn">Send reply</button></div></form></div>')
    # actions
    if is_open:
        modes = s.get("entry_modes") or ["market"]
        out.append(f'<div class="card"><h2>Decide</h2><form method="post" action="/task"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="approve">'
                   + (f'<select name="entry_mode">' + "".join(f'<option value="{m}">{ {"market": "market now", "pending": "pending at the frozen entry"}.get(m, m)}</option>' for m in modes) + "</select>" if len(modes) > 1 else '<input type="hidden" name="entry_mode" value="market">')
                   + '<div style="margin-top:8px"><button class="btn go" style="width:100%">APPROVE</button></div></form>'
                   f'<form method="post" action="/task" style="margin-top:10px"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="skip"><select name="reason_code">'
                   + "".join(f'<option value="{k}">{k}  {E(v)}</option>' for k, v in C.SKIP_REASONS.items()) + '</select><div style="margin-top:8px"><button class="btn no" style="width:100%">SKIP</button></div></form>'
                   f'<form method="post" action="/task" style="margin-top:10px"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="delay"><button class="btn wait" style="width:100%">DELAY one bar</button></form></div>')
    # acks for this signal
    acks = [a for a in _acks() if a.get("signal_id") == s["signal_id"] and a.get("symbol") == s["symbol"]]
    if acks:
        out.append('<div class="card"><h2>Task history</h2><table>')
        for a in sorted(acks, key=lambda a: a.get("executed_at", "")):
            out.append(f'<tr><td class="k">{E(a.get("executed_at", ""))}</td><td>{E(a.get("verb", ""))}</td><td class="{"ok" if a.get("result") == "accepted" else "bad"}">{E(a.get("result", ""))}</td><td>{E(a.get("reason", ""))}</td></tr>')
        out.append("</table></div>")
    return page(f"#{s['signal_id']} {s['strategy']} {s['direction']}", "".join(out), "home", refresh=60 if is_open else None)

def chart_block(sig: dict, levels: dict | None = None, marks: list | None = None) -> str:
    """interactive Lightweight-Charts block fed from the signal's own bars; everything drawn on load."""
    ov = sig.get("overlay") or {}
    from live import mt5feed
    live_feed = mt5feed.available()
    data = {"symbol": sig["symbol"], "live": live_feed, "bars_h4": sig.get("bars_h4") or [], "bars_d1": sig.get("bars_d1") or [],
            "levels": levels or sig.get("levels") or {},
            "overlay": {k: ov.get(k) for k in ("zone", "zone2", "leg", "aux", "swings_hi", "swings_lo")},
            "signal_t": charts._epoch(sig.get("signal_time")), "digits": charts._digits(sig), "marks": marks or []}
    tvs = C.tv_symbol(CFG, sig["symbol"]); link = f"https://www.tradingview.com/chart/?symbol={urllib.parse.quote(tvs)}&interval=240"
    return (f'<div class="card" style="padding:6px"><div class="hc"><div class="row hc-bar" style="padding:2px 4px 6px"><button class="btn on" data-tf="h4" style="padding:6px 12px">H4</button><button class="btn" data-tf="d1" style="padding:6px 12px">D1</button>'
            f'<span class="k">EMA <span style="color:#ffd700">20</span> <span style="color:#00bfff">50</span> <span style="color:#ee82ee">200</span> · zone, leg, swings from the detector</span>'
            f'<span class="k hc-status" style="margin-left:auto"></span><a href="{link}" target="_blank">TradingView ↗</a></div><div class="hc-box" style="width:100%"></div></div>'
            f'<script src="/static/lw.js"></script><script src="/static/hybrid_chart.js"></script><script>HybridChart.mount(document.currentScript.previousElementSibling.previousElementSibling.previousElementSibling, {json.dumps(data, separators=(",", ":"))});</script></div>')

def tv_block(symbol: str, interval: int = 240, levels: dict | None = None) -> str:
    """TradingView Advanced Chart widget (loads from tradingview.com on the viewer's device) + deep link to the TV app."""
    tvs = C.tv_symbol(CFG, symbol); link = f"https://www.tradingview.com/chart/?symbol={urllib.parse.quote(tvs)}&interval={interval}"
    if not CFG.get("tv_widget", True): return f'<div class="k"><a href="{link}" target="_blank">Open {E(tvs)} in TradingView ↗</a></div>'
    conf = json.dumps({"autosize": True, "symbol": tvs, "interval": str(interval), "timezone": "Etc/UTC", "theme": "dark", "style": "1", "locale": "en",
                       "hide_side_toolbar": False, "allow_symbol_change": False, "save_image": False, "withdateranges": True, "details": False, "calendar": False,
                       "studies": ["STD;EMA"], "support_host": "https://www.tradingview.com"})
    lv = levels or {}; csv = ",".join(str(lv[k]) for k in ("entry", "sl", "tp1", "tp2") if lv.get(k))
    copy = (f'<button class="btn" type="button" onclick="navigator.clipboard.writeText(\'{E(csv)}\').then(function(){{this.textContent=\'copied\'}}.bind(this))">Copy levels</button>'
            f'<span class="k">→ paste into the Hybrid levels indicator (entry,sl,tp1,tp2)</span>') if csv else ""
    return (f'<div class="card" style="padding:0;overflow:hidden"><div style="height:520px"><div class="tradingview-widget-container" style="height:100%;width:100%">'
            f'<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>'
            f'<script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>{conf}</script></div></div>'
            f'<div class="row" style="padding:8px"><a href="{link}" target="_blank">Open {E(tvs)} in the TradingView app ↗</a>{copy}</div></div>')

def _acks() -> list[dict]:
    import glob
    return [a for a in (C.load_json(p) for p in glob.glob(os.path.join(CFG["root"], "acks", "*.json"))) if a]

def position_page(key: str, q: dict) -> str:
    p = C.position(CFG, key)
    if not p: return page("Position", '<div class="card bad">no such position</div>')
    out = [flash(q.get("msg"), q.get("ok", "1") == "1")]
    closed = p.get("_closed")
    out.append(f'<h1>{E(p["symbol"])} · {E(p["strategy"])} {E(p["direction"])} <span class="k">pos {p["posid"]} · signal #{p.get("signal_id")}</span></h1>'
               f'<div class="card"><div class="grid2"><div><div class="k">open R</div><div class="big v {"ok" if p.get("open_r", 0) >= 0 else "bad"}">{C.r_fmt(p.get("open_r"))}</div></div><div><div class="k">banked R</div><div class="big v">{C.r_fmt(p.get("banked_r"))}</div></div>'
               f'<div><span class="k">entry</span> <b class="v">{p.get("entry")}</b></div><div><span class="k">SL live</span> <b class="v bad">{p.get("sl_live")}</b> <span class="k">(risk basis {p.get("sl_risk_basis")})</span></div>'
               f'<div><span class="k">TP1</span> <b class="v">{p.get("tp1") or "-"}</b></div><div><span class="k">TP live</span> <b class="v">{p.get("tp_live") or "-"}</b></div>'
               f'<div><span class="k">lots</span> <b class="v">{p.get("lots_live")}</b> <span class="k">of {p.get("lots_init")}</span></div><div><span class="k">bars open</span> <b class="v">{p.get("bars_open")}</b></div></div>'
               f'<div class="k">banked {p.get("banked")} · tp1_done {p.get("tp1_done")} · ratcheted {p.get("ratcheted")} · close-now {C.r_fmt(p.get("closenow_r"))} · updated {E(p.get("ts", ""))}{" · CLOSED" if closed else ""}</div></div>')
    src = C.signal(CFG, f'{p["symbol"]}-{p.get("signal_id")}') if p.get("signal_id") else None
    plv = {"entry": p.get("entry"), "sl": p.get("sl_live"), "tp1": p.get("tp1"), "tp2": p.get("tp_live")}
    if src: out.append(chart_block(src, plv, marks=[{"t": int(p["opened_at"]) - int(p["opened_at"]) % 14400, "label": "entry"}] if p.get("opened_at") else None))
    else: out.append(tv_block(p["symbol"], 240, plv))
    if p.get("signal_id"): out.append(f'<div class="k"><a href="/signal/{E(p["symbol"])}-{p["signal_id"]}">→ signal #{p["signal_id"]} (EA charts, verdict)</a></div>')
    # events inside hold (next 5 days for the symbol)
    evs, _ = C.load_events(CFG); ccys = C.symbol_ccys(p["symbol"]); now = C.now_utc()
    soon = [e for e in evs if e["ccy"] in ccys and now <= e["t"] <= now + timedelta(days=5) and e["cls"] in ("V", "W")][:8]
    if soon:
        out.append('<div class="card"><h2>Events inside the hold (5 d)</h2>' + "".join(f'<div class="ev {"bind" if e["cls"] == "W" else ""}">{E(C.fmt_dt(e["t"]))} · {E(e["ccy"])} {E(e["name"])} <span class="k">[{e["cls"]} {E(e["label"])}]</span></div>' for e in soon) + "</div>")
    if not closed:
        def b(verb, label, enabled, cls=""):
            return (f'<form method="post" action="/task" class="inline"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="{verb}">'
                    f'<button class="btn {cls}" {"" if enabled else "disabled"} onclick="return confirm(\'{label}?\')">{label}</button></form>')
        out.append('<div class="card"><h2>Manage</h2><div class="row">'
                   + b("sl_be", "SL → BE", bool(p.get("be_placeable"))) + b("ratchet_tp1", "SL → TP1", bool(p.get("ratchet_placeable")))
                   + b("close50", "Close 50%", True) + b("close", "Close all", True, "no") + '</div>'
                   f'<div class="k">BE {"placeable" if p.get("be_placeable") else "not yet (+0.5R floor / stops level)"} · ratchet {"placeable" if p.get("ratchet_placeable") else "not yet (needs bank + past TP1)"} — the EA re-checks every rule before acting</div></div>')
    acks = [a for a in _acks() if a.get("position_id") == p["posid"] and a.get("symbol") == p["symbol"]]
    if acks:
        out.append('<div class="card"><h2>Task history</h2><table>')
        for a in sorted(acks, key=lambda a: a.get("executed_at", "")):
            out.append(f'<tr><td class="k">{E(a.get("executed_at", ""))}</td><td>{E(a.get("verb", ""))}</td><td class="{"ok" if a.get("result") == "accepted" else "bad"}">{E(a.get("result", ""))}</td><td>{E(a.get("reason", ""))}</td></tr>')
        out.append("</table></div>")
    return page(f"pos {p['posid']}", "".join(out), "home", refresh=None if closed else 30)

def journal_page(q: dict) -> str:
    sym = q.get("symbol") or None; rows = C.journal_rows(CFG, sym)
    dec = q.get("decision") or ""
    if dec: rows = [r for r in rows if r.get("decision") == dec]
    rows = list(reversed(rows))[:200]
    opts = "".join(f'<option value="{E(x)}" {"selected" if x == dec else ""}>{E(x or "all decisions")}</option>' for x in ["", "approved", "approved_pending", "skipped", "rejected"])
    out = [f'<h1>Journal</h1><form method="get" class="row"><select name="decision" style="width:auto">{opts}</select><input name="symbol" value="{E(sym or "")}" placeholder="symbol" style="width:160px"><button class="btn">Filter</button></form><div class="card"><table><tr><th>#</th><th>time</th><th>signal</th><th>decision</th><th>R</th><th>exit</th></tr>']
    for r in rows:
        key = f'{r.get("symbol")}-{r.get("signal_id")}'
        rtxt = r.get("r_multiple") or ""; skip = f' ({r.get("skip_reason")}{" auto" if r.get("auto") == "1" else ""})' if r.get("decision") == "skipped" else ""
        out.append(f'<tr><td><a href="/signal/{E(key)}">{E(r.get("signal_id", ""))}</a></td><td class="k">{E((r.get("signal_time") or "")[:16])}</td><td>{E(r.get("strategy", ""))} {E(r.get("direction", ""))} <span class="k">{E(r.get("decision_class", ""))}</span></td>'
                   f'<td>{E(r.get("decision", ""))}{E(skip)}</td><td class="v {"ok" if rtxt and float(rtxt) >= 0 else "bad" if rtxt else ""}">{E(rtxt)}</td><td class="k">{E((r.get("exit_time") or "")[:16])}</td></tr>')
    out.append("</table></div>")
    tot = [float(r["r_multiple"]) for r in rows if r.get("r_multiple")]
    if tot: out.append(f'<div class="k">closed trades in view: {len(tot)} · sum {sum(tot):+.2f}R · avg {sum(tot)/len(tot):+.3f}R</div>')
    return page("Journal", "".join(out), "journal")

def events_page(q: dict) -> str:
    evs, cov = C.load_events(CFG); now = C.now_utc(); syms = C.symbols(CFG)
    ccys = set().union(*(C.symbol_ccys(s) for s in syms)) if syms else {"EUR", "USD", "All"}
    win = [e for e in evs if now - timedelta(hours=6) <= e["t"] <= now + timedelta(days=14) and e["ccy"] in ccys]
    out = [f'<h1>Events</h1><div class="k">next 14 days · relevant to {E(", ".join(syms) or "(no symbols)")} · coverage ends {cov.strftime("%Y-%m-%d") if cov else "?"}</div>']
    if cov and cov < now + timedelta(days=14): out.append(f'<div class="flash bad">Calendar coverage ends {cov.strftime("%Y-%m-%d")}. Events after that are unknown, and the EA\'s election gate cannot see them. Refresh econ_events.csv.</div>')
    day = None
    for e in win:
        d = e["t"].strftime("%a %d %b")
        if d != day: out.append(f"<h2>{E(d)}</h2>"); day = d
        cls = e["cls"]; mark = "bind" if cls in ("V", "W") else ""
        out.append(f'<div class="ev {mark}">{e["t"].strftime("%H:%M")} · {E(e["ccy"])} {E(e["name"])} <span class="k">[{E(cls or "-")}{(" " + E(e["label"])) if e["label"] else ""}]</span></div>')
    if not win: out.append('<div class="card k">no events in the window</div>')
    return page("Events", "".join(out), "events")

def settings_page(q: dict) -> str:
    rm = C.risk_mult(CFG); syms = C.symbols(CFG)
    out = [flash(q.get("msg"), q.get("ok", "1") == "1"), '<h1>Settings</h1><div class="card"><h2>Risk multipliers (config/risk_mult.json)</h2><table><tr><th>symbol</th><th>mult</th></tr>']
    for s in syms: out.append(f'<tr><td>{E(s)}</td><td class="v">{rm.get(s.split(".")[0], 1.0)}</td></tr>')
    out.append('</table><div class="k">edit on the box (sizing only, C2); shown here so the number in effect is never a surprise</div></div>')
    cfgv = C.load_json(os.path.join(CFG["root"], "config", "live.json"), {}) or {}
    out.append(f'<div class="card"><h2>EA knobs (config/live.json)</h2><pre>{E(json.dumps(cfgv, indent=1))}</pre></div>')
    out.append(f'<div class="card"><h2>Service</h2><div class="k">queue root {E(CFG["root"])}<br>web {E(CFG["web"]["base_url"])} · no login (WireGuard is the boundary) · sessions: n/a</div></div>')
    for s in syms:
        out.append(f'<div class="card"><h2>Audit tail {E(s)}</h2><pre>{E(chr(10).join(C.audit_tail(CFG, s, 25)))}</pre></div>')
    return page("Settings", "".join(out), "settings")

# ----------------------------------------------------------------------------- actions
def do_task(form: dict) -> tuple[str, bool, str]:
    key = form.get("key", ""); verb = form.get("verb", "")
    if verb in C.VERBS_SIGNAL:
        s = C.signal(CFG, key)
        if not s: return "/", False, "unknown signal"
        if s.get("status") != "open": return f"/signal/{key}", False, f"signal is {s.get('status')}, not open"
        params = {}
        if verb == "approve":
            em = form.get("entry_mode", "market")
            if em not in (s.get("entry_modes") or ["market"]): return f"/signal/{key}", False, "entry mode not offered"
            params = {"entry_mode": em}
        elif verb == "skip":
            try: rc = int(form.get("reason_code", "0"))
            except ValueError: rc = 0
            if rc not in C.SKIP_REASONS: return f"/signal/{key}", False, "bad reason"
            params = {"reason_code": rc}
        tid = C.write_task(CFG, s["symbol"], verb, params, signal_id=s["signal_id"])
        back = f"/signal/{key}"
    elif verb in C.VERBS_POSITION:
        p = C.position(CFG, key)
        if not p or p.get("_closed"): return "/", False, "unknown or closed position"
        tid = C.write_task(CFG, p["symbol"], verb, {}, position_id=p["posid"])
        back = f"/position/{key}"
    else:
        return "/", False, "unknown verb"
    a = C.wait_ack(CFG, tid, 12.0)
    if not a: return back, False, f"{verb}: task written, no ack within 12 s (EA busy or stopped?) — check again shortly"
    ok = a.get("result") == "accepted"
    refs = a.get("refs") or {}
    return back, ok, f"{verb}: {a.get('result')} — {a.get('reason')}" + (f" · posid {refs.get('posid')} ticket {refs.get('order_ticket')}" if ok and verb == "approve" else "")

class H(BaseHTTPRequestHandler):
    server_version = "hybrid-live/1"
    def log_message(self, fmt, *args):
        if LOG and "/chart/" not in (args[0] if args else ""): LOG(f"{self.client_address[0]} {fmt % args}")
    def _send(self, body: str | bytes, ctype="text/html; charset=utf-8", code=200):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def _redirect(self, url: str, msg: str, ok: bool):
        self.send_response(303); self.send_header("Location", f"{url}?{urllib.parse.urlencode({'msg': msg, 'ok': '1' if ok else '0'})}"); self.end_headers()
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}; parts = [p for p in u.path.split("/") if p]
        try:
            if not parts: return self._send(dashboard(q))
            if parts[0] == "static" and len(parts) == 2 and parts[1] in ("lw.js", "hybrid_chart.js"):
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", parts[1]), "rb") as f: return self._send(f.read(), "application/javascript")
            if parts[0] == "api" and len(parts) == 3 and parts[1] == "bars":
                from live import mt5feed
                sym = parts[2]; tf = q.get("tf", "h4"); n = max(1, min(2000, int(q.get("n", "500") or 500)))
                if not C.safe_key(sym) or tf not in mt5feed.TF: return self._send("bad request", "text/plain", 400)
                b = mt5feed.bars(sym, tf, n); k = mt5feed.tick(sym)
                return self._send(json.dumps({"symbol": sym, "tf": tf, "bars": b, "tick": k, "ts": C.now_iso(), "live": b is not None}, separators=(",", ":")), "application/json")
            if parts[0] == "health": return self._send(json.dumps({"ok": True, "ts": C.now_iso()}), "application/json")
            if parts[0] == "signal" and len(parts) == 2: return self._send(signal_page(parts[1], q))
            if parts[0] == "position" and len(parts) == 2: return self._send(position_page(parts[1], q))
            if parts[0] == "journal": return self._send(journal_page(q))
            if parts[0] == "events": return self._send(events_page(q))
            if parts[0] == "settings": return self._send(settings_page(q))
            if parts[0] == "chart" and len(parts) == 3 and parts[2] in ("h4.png", "d1.png"):
                s = C.signal(CFG, parts[1])
                if not s: return self._send("no signal", "text/plain", 404)
                h4, d1 = charts.render_signal(s, os.path.join(CFG["root"], "web", "charts"))
                with open(h4 if parts[2] == "h4.png" else d1, "rb") as f: return self._send(f.read(), "image/png")
            return self._send("not found", "text/plain", 404)
        except Exception as e:
            if LOG: LOG(f"GET {self.path} error {e!r}")
            return self._send(page("error", f'<div class="card bad">{E(repr(e))}</div>'), code=500)
    def do_POST(self):
        u = urllib.parse.urlsplit(self.path); n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
        try:
            if u.path == "/task":
                back, ok, msg = do_task(form); return self._redirect(back, msg, ok)
            if u.path == "/reply":
                key = form.get("key", ""); text = (form.get("text") or "").strip()
                if not C.safe_key(key) or not C.signal(CFG, key) or not text: return self._redirect("/", "bad reply", False)
                C.atomic_write_json(os.path.join(CFG["root"], "advisor", "replies", f"{key}-{C.now_utc().strftime('%Y%m%dT%H%M%S')}.json"), {"signal_key": key, "text": text[:4000], "ts": C.now_iso()})
                return self._redirect(f"/signal/{key}", "reply queued — the answer appears here in ~10-30 s", True)
            if u.path == "/kill":
                en = form.get("enable") == "1"; C.set_kill_switch(CFG, en)
                C.append_line(os.path.join(CFG["root"], "web", "web_audit.log"), f"{C.now_iso()}|kill_switch|{'enabled' if en else 'disabled'}|by=web")
                return self._redirect("/", f"trading {'ENABLED' if en else 'DISABLED'} (EA picks it up within one poll)", True)
            return self._send("not found", "text/plain", 404)
        except Exception as e:
            if LOG: LOG(f"POST {self.path} error {e!r}")
            return self._redirect("/", f"error: {e!r}", False)

def main():
    global CFG, LOG
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); ap.add_argument("--bind"); ap.add_argument("--port", type=int); a = ap.parse_args()
    CFG = C.load_config(a.config); LOG = C.Log("web", CFG["logs_dir"])
    bind = a.bind or CFG["web"]["bind"]; port = a.port or CFG["web"]["port"]
    for sub in ("web/charts", "advisor/replies", "tasks"): os.makedirs(os.path.join(CFG["root"], sub), exist_ok=True)
    srv = ThreadingHTTPServer((bind, port), H); srv.daemon_threads = True
    LOG(f"serving http://{bind}:{port}  root={CFG['root']}")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__": main()
