r"""
webapp.py - the trader's phone-first web app (spec §5). stdlib ThreadingHTTPServer, bound to the WireGuard address.

Routes: /  dashboard | /signal/<key> | /position/<key> | /journal | /events | /settings | /health
        /chart/<key>/<h4|d1>.png | POST /task (typed verbs against ids the EA reported - S6) | POST /reply | POST /kill
Every task written and every ack received is appended to <root>/web/web_audit.log (S8). No login: the tunnel is
the boundary (trader ruling 2026-09-15); HTTP inside WireGuard.
"""
from __future__ import annotations
import hashlib, html, json, os, re, sys, urllib.parse, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import charts
from live import advisor_runner as AR
from live import eligibility as ELIG

CFG: dict = {}
LOG = None
STATIC_V = str(int(os.path.getmtime(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "hybrid_chart.js")))) if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "hybrid_chart.js")) else "0"
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
.ev.bind{color:var(--dn);font-weight:600}.ev.amber{color:var(--warn);font-weight:600}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}@media(max-width:640px){.grid2{grid-template-columns:1fr}}
.flash{padding:10px;border-radius:6px;margin:8px 0}.flash.ok{background:#12301a;border:1px solid #2ea043}.flash.bad{background:#3a1d1d;border:1px solid #a33}
.advs{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:760px){.advs{grid-template-columns:1fr}}
.adv{border:1px solid var(--line);border-left:5px solid var(--line);border-radius:8px;padding:10px;background:#11161d;min-width:0}
.adv.fam-sonnet{border-left-color:#58a6ff}.adv.fam-opus{border-left-color:#bc8cff}.advs.dis .adv{border-top-color:#a33;border-right-color:#a33;border-bottom-color:#a33;box-shadow:0 0 0 1px #a33}
.advtag{display:inline-block;padding:3px 9px;border-radius:10px;font-size:13px;font-weight:700}.advtag.fam-sonnet{background:#132a45;color:#79c0ff}.advtag.fam-opus{background:#2d1f47;color:#d2a8ff}
.badge{display:inline-block;padding:4px 12px;border-radius:12px;font-weight:800;font-size:14px;letter-spacing:.05em}.badge.agree{background:#12301a;color:#3fb950;border:1px solid #2ea043}.badge.disagree{background:#3a1d1d;color:#ff7b72;border:1px solid #f85149}.badge.wait{background:#21262d;color:var(--dim)}
.vw{font-size:18px;font-weight:800}
.cd{font-weight:600}.brief h2{color:var(--txt);text-transform:none;letter-spacing:0;font-size:17px;margin-top:14px}.brief p,.brief li{font-size:15px;line-height:1.5}.brief a{word-break:break-all}
"""
JS = """
function tick(){document.querySelectorAll('[data-deadline]').forEach(function(el){var d=new Date(el.dataset.deadline);var s=Math.floor((d-Date.now())/1000);
 if(s<=0){el.textContent='EXPIRED';el.className='cd bad';return;}var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;el.textContent=(h>0?h+'h ':'')+m+'m '+x+'s';el.className='cd '+(s<3600?'warn':'ok');});}
function utc(){var d=new Date();var p=function(n){return (n<10?'0':'')+n};var el=document.getElementById('utc');if(el)el.textContent=p(d.getUTCHours())+':'+p(d.getUTCMinutes())+':'+p(d.getUTCSeconds())+' UTC';}
function els(){document.querySelectorAll('[data-started]').forEach(function(el){var s=Math.max(0,Math.floor((Date.now()-new Date(el.dataset.started))/1000));el.textContent=(s>=60?Math.floor(s/60)+'m ':'')+(s%60)+'s';});}
setInterval(function(){tick();utc();els();},1000);tick();utc();els();
(function(){var box=document.getElementById('advisor');if(!box)return;setInterval(function(){
 var typed=[].some.call(box.querySelectorAll('textarea'),function(t){return t.value.trim()!==''||t===document.activeElement;});if(typed)return;
 fetch('/api/advisor/'+encodeURIComponent(box.dataset.key)).then(function(r){return r.json();}).then(function(j){if(j&&j.v&&j.v!==box.dataset.v){box.innerHTML=j.html;box.dataset.v=j.v;els();}}).catch(function(){});},4000);})();
var meta=document.querySelector('meta[name=autorefresh]');if(meta&&!document.querySelector('textarea:focus')){setTimeout(function(){if(!document.querySelector('textarea:focus'))location.reload();},parseInt(meta.content)*1000);}
"""

def page(title: str, body: str, active: str = "", refresh: int | None = None) -> str:
    tabs = [("/", "Home", "home"), ("/context", "Context", "context"), ("/journal", "Journal", "journal"), ("/events", "Events", "events"), ("/settings", "Settings", "settings")]
    nav = "".join(f'<a href="{h}" class="{"on" if a == active else ""}">{t}</a>' for h, t, a in tabs) + '<span id="utc" class="k" style="margin-left:auto;align-self:center;white-space:nowrap;font-variant-numeric:tabular-nums"></span>'
    m = f'<meta name="autorefresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(title)}</title>{m}<style>{CSS}</style></head><body><nav>{nav}</nav><main>{body}</main><script>{JS}</script></body></html>')

def flash(msg: str | None, ok: bool = True) -> str:
    return f'<div class="flash {"ok" if ok else "bad"}">{E(msg)}</div>' if msg else ""

def status_pill(s: str) -> str:
    cls = {"open": "warn", "approved": "ok", "approved_pending": "ok", "skipped": "", "auto_skipped": "", "expired": "bad", "rejected": "bad", "cancelled": ""}.get(s, "")
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


# ----------------------------------------------------------------------------- advisors (coach 2026-09-21: two per signal)
def _fam(mid: str) -> str: return "opus" if "opus" in mid else ("sonnet" if "sonnet" in mid else "other")

def _last_word(rec: dict | None) -> str | None:
    for c in reversed((rec or {}).get("consults") or []):
        if c.get("ok"):
            w = AR.verdict_word(c.get("text"))
            if w: return w
    return None

def advisor_section(key: str) -> tuple[str, str]:
    """both advisor panels + the AGREE / DISAGREE badge. Returns (html, version) - the page polls /api/advisor/<key>
    and swaps the html in the moment a verdict lands."""
    ms = AR.models(CFG)
    recs = {m["id"]: C.load_json(os.path.join(CFG["root"], "advisor", "verdicts", f"{key}.{m['id']}.json")) for m in ms}
    words = {mid: _last_word(r) for mid, r in recs.items()}
    have = [w for w in words.values() if w]
    if len(ms) > 1 and len(have) == len(ms):
        agree = len(set(have)) == 1
        badge = (f'<span class="badge agree">AGREE · {E(have[0])}</span>' if agree else
                 '<span class="badge disagree">DISAGREE · ' + " vs ".join(E(f"{words[m['id']]} ({m['id'].split('-')[0].title()})") for m in ms) + '</span>')
    else:
        agree = True
        badge = f'<span class="badge wait">{len(have)} of {len(ms)} verdicts in</span>'
    panels = []
    for m in ms:
        mid = m["id"]; rec = recs[mid] or {}; st = rec.get("status") or "idle"; lab = m.get("label", mid)
        other = next((x for x in ms if x["id"] != mid and words.get(x["id"])), None)
        stands = f" {other.get('label', other['id']).split(' ·')[0]}'s verdict stands." if other else ""
        cs = rec.get("consults") or []; last = cs[-1] if cs else {}
        if st == "queued": line = f'<span class="warn">queued — waiting for a free {E(m["model"])} slot</span> <span class="k" data-started="{E(rec.get("queued_at", ""))}"></span>'
        elif st == "running": line = f'<span class="warn">running{" (reply)" if rec.get("running_kind") == "reply" else ""}…</span> <b class="v" data-started="{E(rec.get("started_at", ""))}"></b> <span class="k">of {m.get("timeout_s")}s max</span>'
        elif st == "rate_limited": line = f'<span class="bad"><b>RATE-LIMITED</b> — {E(lab.split(" ·")[0])} did not answer (subscription limit).{E(stands)}</span>'
        elif st == "error" and last.get("timeout"): line = f'<span class="bad"><b>Timed out</b> after {m.get("timeout_s")}s — no verdict.{E(stands)}</span>'
        elif st == "error" and last.get("interrupted"): line = '<span class="warn">interrupted by an advisor restart — it will run again shortly</span>'
        elif st == "error": line = f'<span class="bad"><b>Failed</b>: {E(str(last.get("error"))[:200])}.{E(stands)}</span>'
        elif st == "skipped": line = f'<span class="k">not run — {E(rec.get("note", "signal decided first"))}</span>'
        elif st == "ok": line = f'<span class="ok">answered</span> <span class="k">in {last.get("elapsed_s")}s</span>'
        else: line = '<span class="k">waiting to start…</span>'
        body = []
        for c in cs:
            if c.get("kind") == "reply": body.append(f'<div class="k">you · {E(c.get("ts", ""))}</div><pre>{E(c.get("prompt", ""))}</pre>')
            if c.get("ok"): body.append(f'<div class="k">{E(mid)} · {E(c.get("ts", ""))} · {c.get("elapsed_s")}s</div><pre>{E(c.get("text") or "")}</pre>')
            elif c.get("kind") == "verdict" and not c.get("interrupted"): body.append(f'<div class="k bad">{E(mid)} · {E(c.get("ts", ""))} · no answer: {E(str(c.get("error"))[:160])}</div>')
        w = words.get(mid)
        answered = any(c.get("ok") for c in cs)
        can_reply = answered and st not in ("running", "queued")
        can_rerun = (not answered) and st in ("error", "rate_limited", "skipped")
        panels.append(f'<div class="adv fam-{_fam(mid)}"><div class="row"><span class="advtag fam-{_fam(mid)}">{E(lab)}</span>'
                      + (f'<span class="vw">{E(w)}</span>' if w else "") + f'</div><div style="margin:6px 0">{line}</div>' + "".join(body)
                      + (f'<form method="post" action="/reply"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="model" value="{E(mid)}">'
                         f'<textarea name="text" rows="2" placeholder="reply to {E(lab.split(" ·")[0])} (its own session)"></textarea><div style="margin-top:6px"><button class="btn">Reply to {E(lab.split(" ·")[0])}</button></div></form>' if can_reply else "")
                      + (f'<form method="post" action="/advisor_retry" class="inline"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="model" value="{E(mid)}">'
                         f'<button class="btn">Run {E(lab.split(" ·")[0])} again</button></form>' if can_rerun else "")
                      + '</div>')
    legacy = C.load_json(os.path.join(CFG["root"], "advisor", "verdicts", f"{key}.json"))
    leg = ""
    if legacy and legacy.get("consults"):
        leg = '<details><summary class="k">earlier single-advisor verdict (before 2026-09-21)</summary>' + "".join(
            f'<div class="k">advisor · {E(c.get("ts", ""))}</div><pre>{E(c.get("text") or ("ERROR: " + str(c.get("error"))))}</pre>' for c in legacy["consults"]) + '</details>'
    html_ = f'<div class="row" style="margin-bottom:8px">{badge}</div><div class="advs{"" if agree else " dis"}">{"".join(panels)}</div>{leg}'
    v = hashlib.sha1(json.dumps({k: [(r or {}).get("status"), len((r or {}).get("consults") or []), ((r or {}).get("consults") or [{}])[-1].get("ts")] for k, r in recs.items()}, sort_keys=True).encode()).hexdigest()[:12]
    return html_, v

def advisor_load_card() -> str:
    """today's consult count per model (UTC day) from the runner's consults.log - Opus-high is real subscription load."""
    p = os.path.join(CFG["root"], "advisor", "consults.log"); day = C.now_utc().strftime("%Y-%m-%d")
    cnt: dict[str, dict] = {m["id"]: {"n": 0, "ok": 0, "err": 0, "rl": 0, "to": 0, "sec": 0.0} for m in AR.models(CFG)}
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                x = ln.rstrip("\n").split("|")
                if len(x) < 7 or not x[0].startswith(day): continue
                c = cnt.setdefault(x[1], {"n": 0, "ok": 0, "err": 0, "rl": 0, "to": 0, "sec": 0.0})
                c["n"] += 1; c["ok" if x[4] == "ok" else "err"] += 1; c["rl"] += x[6] == "rate_limited"; c["to"] += x[6] == "timeout"
                try: c["sec"] += float(x[5])
                except ValueError: pass
    except OSError: pass
    cells = []
    for mid, c in cnt.items():
        m = AR.model_cfg(CFG, mid) or {"label": mid}
        warn = f' <span class="pill bad">{c["rl"]} rate-limited</span>' if c["rl"] else ""
        cells.append(f'<div><span class="advtag fam-{_fam(mid)}">{E(m.get("label", mid))}</span><div class="big v">{c["n"]}</div>'
                     f'<div class="k">{c["ok"]} ok · {c["err"]} failed{f" ({c['to']} timeout)" if c["to"] else ""} · avg {(c["sec"] / c["n"]) if c["n"] else 0:.0f}s</div>{warn}</div>')
    return f'<div class="card"><div class="k">Advisor consults today (UTC {day})</div><div class="grid2">{"".join(cells)}</div></div>'

def shadow_line() -> str:
    h = C.load_json(os.path.join(CFG["root"], "config", "lineup_history.json"), None)
    if not h or not h.get("events"): return ""
    ev = h["events"]; start = C.parse_iso(ev[0].get("date") + "T00:00:00Z"); last = C.parse_iso(ev[-1].get("date") + "T00:00:00Z")
    now = C.now_utc(); d_all = (now - start).days if start else 0; d_last = (now - last).days if last else 0
    tail = f" · current configuration ({E(ev[-1].get('event', ''))}, {len(ev[-1].get('lineup') or [])} symbols) since {E(ev[-1]['date'])}: {d_last} day{'s' if d_last != 1 else ''}" if len(ev) > 1 else ""
    return f'<div class="k">shadow day {d_all} (since {E(ev[0]["date"])}){tail}</div>'

def eligibility_card() -> str:
    rows = ELIG.table(CFG)
    out = [f'<div class="card"><div class="k">Live eligibility (demo) · decisions = approved + skips 1–8 (code 9, auto and TEST excluded) · costed R from broker deals · flag at ≥ {ELIG.FLAG_N} decisions with R ≥ 0</div>'
           '<table><tr><th>symbol</th><th>dec</th><th>appr</th><th>skip</th><th>no-resp</th><th>closed</th><th>costed R</th><th></th></tr>']
    for a in rows:
        rr = a["costed_r"]; unc = f' <span class="k">+{a["uncosted"]} unpriced</span>' if a["uncosted"] else ""
        flag = '<span class="pill ok">≥20 · R≥0</span>' if a["flag"] else (f'<span class="k">{a["decisions"]}/{ELIG.FLAG_N}</span>' if a["decisions"] < ELIG.FLAG_N else '<span class="pill bad">R&lt;0</span>')
        out.append(f'<tr><td>{E(a["symbol"])}</td><td class="v">{a["decisions"]}</td><td class="v">{a["approved"]}</td><td class="v">{a["skipped"]}</td><td class="v">{a["no_response"]}</td>'
                   f'<td class="v">{a["closed"]}{f" +{a['open']} open" if a["open"] else ""}</td><td class="v {"ok" if rr >= 0 else "bad"}">{rr:+.2f}{unc}</td><td>{flag}</td></tr>')
    out.append("</table></div>")
    return "".join(out)

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
    acct = next((hb for hb in (C.heartbeat(CFG, x) for x in syms) if hb and (hb.get("ftmo") or {}).get("initial_balance")), None)
    if acct: out.append(f'<div class="card"><div class="k">Account · from {E(acct.get("symbol", ""))} beat {C.rel_time(C.parse_iso(acct.get("ts")))}</div>' + ftmo_block(acct) + '</div>')
    out.append(shadow_line())
    # backup + telegram test (trader rulings 2026-09-16: manual 30-day zip instead of a nightly pull)
    from live import backup
    ds = backup.days_since(CFG); lb = backup.last(CFG)
    if ds is None: bcls, btxt = "bad", "never backed up"
    else: bcls = "ok" if ds < 15 else ("warn" if ds < 25 else "bad"); btxt = f"{ds:.0f} day{'s' if ds >= 1.5 else ''} since last backup"
    out.append(f'<div class="card"><div class="row"><div><div class="k">Backup</div><div class="big {bcls}">{E(btxt)}</div><div class="k">{E(lb["name"]) if lb else "zip of the last 30 days: journals, queue, state, verdicts, calendar, MT5 presets"}</div></div>'
               f'<a class="btn" href="/backup.zip" style="margin-left:auto" onclick="setTimeout(function(){{location.reload()}},4000)">Download backup (30 d)</a>'
               f'<form method="post" action="/telegram_test" class="inline"><button class="btn">Telegram test</button></form></div></div>')
    # open signals
    sigs = C.list_signals(CFG); opn = [s for s in sigs if s.get("status") == "open"]
    out.append("<h2>Pending signals</h2>")
    if not opn: out.append('<div class="card k">none</div>')
    for s in opn:
        out.append(f'<a href="/signal/{E(s["signal_key"])}"><div class="card"><div class="row"><span class="big">{E(s["symbol"])} {E(s["strategy"])} {E(s["direction"])}</span>{cls_pill(s.get("decision_class"))}<span class="k">#{s["signal_id"]}</span></div>'
                   f'<div class="row"><span class="k">deadline</span><span class="cd" data-deadline="{E(s.get("deadline", ""))}"></span><span class="k">delays {s.get("delay_count", 0)}</span><span class="k">{E((s.get("regime") or {}).get("pretty", ""))}</span></div></div></a>')
    # pending orders (approved with "pending at the original entry"; resting on the broker until price comes back)
    out.append("<h2>Pending orders</h2>")
    pend = pending_orders()
    if pend is None: out.append('<div class="card k">terminal feed unavailable - pending orders cannot be listed right now</div>')
    elif not pend: out.append('<div class="card k">none</div>')
    for o in pend or []:
        out.append(pending_card(o, link=True))
    # positions
    pos = C.list_positions(CFG)
    out.append("<h2>Open positions</h2>")
    if not pos: out.append('<div class="card k">none</div>')
    for p in pos:
        out.append(f'<a href="/position/{E(p["symbol"])}-{p["posid"]}"><div class="card"><div class="row"><span class="big">{E(p["symbol"])} {E(p["strategy"])} {E(p["direction"])}</span><span class="big v {"ok" if p.get("open_r", 0) >= 0 else "bad"}">{C.r_fmt(p.get("open_r"))}</span>'
                   f'<span class="k">banked {C.r_fmt(p.get("banked_r"))} · {p.get("lots_live")} lots · {p.get("bars_open")} bars</span></div></div></a>')
    # instances: one compact table (the expanded universe runs ~40 charts - a card each would bury everything below)
    live_n = 0; rows_i = []
    for sym in syms:
        hb = C.heartbeat(CFG, sym); age = C.heartbeat_age_s(hb)
        alive = hb and hb.get("status") == "running" and age is not None and age < CFG["monitor"]["heartbeat_stale_s"]; live_n += bool(alive)
        flags = "" if not hb else ("" if hb.get("terminal_trade_allowed") and hb.get("mql_trade_allowed") else ' <span class="pill bad">AutoTrading OFF</span>')
        wf = ' <span class="pill">weekend-flat</span>' if hb and hb.get("weekend_flat") else ""
        tvs = C.tv_symbol(CFG, sym); tvl = f"https://www.tradingview.com/chart/?symbol={urllib.parse.quote(tvs)}&interval=240"
        rows_i.append(f'<tr><td>{E(sym)}{wf}</td><td><span class="pill {"ok" if alive else "bad"}">{"alive" if alive else "STALE"}</span>{flags}</td>'
                      f'<td class="k">{C.rel_time(C.parse_iso(hb.get("ts")) if hb else None)}</td><td><a href="{tvl}" target="_blank">TV ↗</a></td></tr>'
                      + (f'<tr><td colspan="4" class="k">alerts: {E(", ".join(hb.get("alerts") or []))}</td></tr>' if hb and hb.get("alerts") else ""))
    out.append(f'<h2>Instances · {live_n}/{len(syms)} alive</h2><div class="card"><table><tr><th>symbol</th><th>EA</th><th>beat</th><th></th></tr>{"".join(rows_i)}</table></div>')
    out.append('<h2>Advisors</h2>' + advisor_load_card())
    out.append('<h2>Live eligibility</h2>' + eligibility_card())
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
        out.append(f'<div class="ev {ev_cls(e.get("hours_until"), e.get("cls"), e.get("binding"))}">{E(C.fmt_dt(C.parse_iso(e.get("t_utc"))))} · {E(e.get("ccy", ""))} {E(e.get("name", ""))} <span class="k">[{E(e.get("cls", ""))} {E(e.get("label", ""))}]</span>{" BINDING" if e.get("binding") else ""}</div>')
    if not evs: out.append('<div class="k">no notable events inside the window</div>')
    _, cov = C.load_events(CFG); st = C.parse_iso(s.get("signal_time"))
    if cov and st and cov < st + timedelta(days=14): out.append(f'<div class="warn">calendar coverage ends {cov.strftime("%Y-%m-%d")} — later events unknown</div>')
    eg = s.get("election_gate") or {}
    if eg.get("hit"): out.append(f'<div class="bad">election gate: {E(eg.get("event", ""))}</div>')
    out.append("</div>")
    # advisors: two independent consults, each panel fills in the moment its verdict lands
    html_adv, v = advisor_section(key)
    out.append(f'<div class="card"><h2>Advisors</h2><div id="advisor" data-key="{E(key)}" data-v="{v}">{html_adv}</div></div>')
    # actions
    if is_open:
        modes = s.get("entry_modes") or ["market"]
        out.append(f'<div class="card"><h2>Decide</h2><form method="post" action="/task"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="approve">'
                   + f'<select name="entry_mode"><option value="market">Enter NOW at market (SL/TP as shown)</option><option value="pending">Pending order at the original entry {lv.get("entry")} (waits for price to come back)</option></select>'
                   + '<div style="margin-top:8px"><button class="btn go" style="width:100%">APPROVE</button></div></form>'
                   f'<form method="post" action="/task" style="margin-top:10px"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="skip"><select name="reason_code">'
                   + "".join(f'<option value="{k}">{k}  {E(v)}</option>' for k, v in C.SKIP_REASONS.items()) + '</select><div style="margin-top:8px"><button class="btn no" style="width:100%">SKIP</button></div></form>'
                   f'<form method="post" action="/task" style="margin-top:10px"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="delay"><button class="btn wait" style="width:100%">DELAY one bar</button></form></div>')
    if s.get("status") == "rejected" and str(s.get("auto_reason", "")).startswith("invalidated: price through SL") and "pending" in str(s.get("auto_reason", "")):
        out.insert(1, f'<div class="flash bad">pending order INVALIDATED - {E(s["auto_reason"])}; the EA deleted it before it filled</div>')
    if s.get("status") == "cancelled":
        out.insert(1, '<div class="flash ok">pending order CANCELLED by you before it filled</div>')
    if s.get("status") == "approved_pending":
        pend = pending_orders()
        mine = [o for o in (pend or []) if o.get("signal_key") == key]
        if mine: out.insert(1, '<h2>Pending order</h2>' + pending_card(mine[0]))
        elif pend is not None:
            pos = [p for p in C.list_positions(CFG) if p.get("symbol") == s["symbol"] and str(p.get("signal_id")) == str(s["signal_id"])]
            row = C.row_state(CFG, s["symbol"], s["signal_id"]) or {}
            if pos: out.insert(1, f'<div class="flash ok">pending order has FILLED - <a href="/position/{E(s["symbol"])}-{pos[0]["posid"]}">open position</a></div>')
            elif row.get("decision") == "expired": out.insert(1, f'<div class="flash bad">pending order EXPIRED unfilled (EA cancelled it after {C.PENDING_EXPIRY_BARS} H4 bars)</div>')
            else: out.insert(1, '<div class="flash bad">no resting order found for this signal (filled, cancelled or expired) - check Open positions</div>')
    # acks for this signal
    acks = [a for a in _acks() if a.get("signal_id") == s["signal_id"] and a.get("symbol") == s["symbol"]]
    if acks:
        out.append('<div class="card"><h2>Task history</h2><table>')
        for a in sorted(acks, key=lambda a: a.get("executed_at", "")):
            out.append(f'<tr><td class="k">{E(a.get("executed_at", ""))}</td><td>{E(a.get("verb", ""))}</td><td class="{"ok" if a.get("result") == "accepted" else "bad"}">{E(a.get("result", ""))}</td><td>{E(a.get("reason", ""))}</td></tr>')
        out.append("</table></div>")
    return page(f"#{s['signal_id']} {s['strategy']} {s['direction']}", "".join(out), "home", refresh=60 if is_open else (20 if s.get("status") == "approved_pending" else None))

def chart_block(sig: dict, levels: dict | None = None, marks: list | None = None) -> str:
    """interactive Lightweight-Charts block fed from the signal's own bars; everything drawn on load."""
    ov = sig.get("overlay") or {}
    from live import mt5feed
    live_feed = mt5feed.available()
    data = {"symbol": sig["symbol"], "live": live_feed, "bars_h4": sig.get("bars_h4") or [], "bars_d1": sig.get("bars_d1") or [],
            "levels": levels or sig.get("levels") or {},
            "overlay": {k: ov.get(k) for k in ("zone", "zone2", "leg", "aux", "swings_hi", "swings_lo")},
            "signal_t": charts._epoch(sig.get("signal_time")), "digits": charts._digits(sig), "marks": marks or [], "strategy": sig.get("strategy", "")}
    tvs = C.tv_symbol(CFG, sig["symbol"]); link = f"https://www.tradingview.com/chart/?symbol={urllib.parse.quote(tvs)}&interval=240"
    cid = "hc_" + hashlib.md5(sig["signal_key"].encode()).hexdigest()[:8]
    return (f'<div class="card" style="padding:6px"><div class="hc" id="{cid}"><div class="row hc-bar" style="padding:2px 4px 6px"><button class="btn on" data-tf="h4" style="padding:6px 12px">H4</button><button class="btn" data-tf="d1" style="padding:6px 12px">D1</button>'
            f'<span class="k">EMA <span style="color:#ffd700">20</span> <span style="color:#00bfff">50</span> <span style="color:#ee82ee">200</span> · zone · <span style="color:#9370db">imbalances</span> · swings · fib (DeepFib)</span>'
            f'<span class="k hc-status" style="margin-left:auto"></span><a href="{link}" target="_blank">TradingView ↗</a></div><div class="hc-box" style="width:100%"></div></div>'
            f'<script src="/static/lw.js?v={STATIC_V}"></script><script src="/static/hybrid_chart.js?v={STATIC_V}"></script><script>window.addEventListener("DOMContentLoaded", function () {{ HybridChart.mount("{cid}", {json.dumps(data, separators=(",", ":"))}); }});</script></div>')

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

def ev_cls(hours_until, cls: str | None, binding: bool | None) -> str:
    """the popup's rule (TradeDialog.c): red bold inside 6 h, amber bold 6-12 h; W/binding red; H grey."""
    try: h = float(hours_until)
    except (TypeError, ValueError): h = None
    if binding or cls == "W": return "bind"
    if h is not None and 0 <= h < 6 and cls in ("V", "C"): return "bind"
    if h is not None and 6 <= h < 12 and cls in ("V", "C"): return "amber"
    if cls == "H": return "k"
    return ""

def pending_orders() -> list[dict] | None:
    """our resting orders (magic + 'Signal #N' comment), joined to their signal key."""
    from live import mt5feed
    raw = mt5feed.orders()
    if raw is None: return None
    out = []
    for o in raw:
        m = re.match(r"Signal #(\d+)\b", o.get("comment") or "")
        o["signal_key"] = f'{o["symbol"]}-{m.group(1)}' if m else None
        out.append(o)
    return out

def cancel_txt(o: dict) -> str:
    try:
        sid = (o.get("signal_key") or "").split("-")[-1]
        row = C.row_state(CFG, o["symbol"], sid) or {}
        placed = C.parse_iso(row.get("placed_time")) if isinstance(row.get("placed_time"), str) else (datetime.fromtimestamp(int(row["placed_time"]), timezone.utc) if row.get("placed_time") else None)
        base = int(placed.timestamp()) if placed else int(o.get("setup") or 0)
        at = C.pending_cancel_at(base, C.PENDING_EXPIRY_BARS, int(o.get("server_offset_h", 3)))
        return f'~{C.fmt_dt(at)} ({C.rel_time(at)}, {C.PENDING_EXPIRY_BARS} H4 bars after placement)'
    except Exception:
        return f"after {C.PENDING_EXPIRY_BARS} H4 bars"

def pending_card(o: dict, link: bool = False) -> str:
    s = C.signal(CFG, o["signal_key"]) if o.get("signal_key") else None
    title = f'{E(o["symbol"])} {E(s["strategy"]) if s else ""} {E(o["type"])}'
    dist = o.get("distance"); dr = o.get("distance_r")
    near = dr is not None and dr <= 0.25
    body = (f'<div class="card"><div class="row"><span class="big">{title}</span><span class="pill warn">resting</span>'
            f'<span class="k">#{E(o["signal_key"].split("-")[-1]) if o.get("signal_key") else "?"} · ticket {o["ticket"]}</span></div>'
            f'<div class="grid2"><div><span class="k">order price</span> <b class="v">{o["price"]}</b></div><div><span class="k">market ({"ask" if o["buy"] else "bid"})</span> <b class="v">{o.get("market")}</b></div>'
            f'<div><span class="k">distance to fill</span> <b class="v {"ok" if near else ""}">{dist:.2f}</b> <span class="k">= {dr:.2f}R of the stop</span></div>' if dist is not None and dr is not None else
            f'<div class="card"><div class="row"><span class="big">{title}</span><span class="pill warn">resting</span></div><div class="grid2"><div><span class="k">order price</span> <b class="v">{o["price"]}</b></div><div><span class="k">market</span> <b class="v">-</b></div><div><span class="k">distance</span> <b>-</b></div>')
    body += (f'<div><span class="k">SL / TP</span> <b class="v bad">{o["sl"]}</b> / <b class="v">{o["tp"]}</b></div>'
             f'<div><span class="k">lots</span> <b class="v">{o["volume"]}</b></div><div><span class="k">EA cancels if unfilled</span> <b class="v">{cancel_txt(o)}</b></div></div>'
             f'<div class="k">fills when the {"ask" if o["buy"] else "bid"} reaches {o["price"]}; it then becomes an open position with the +1R bank and BE rules</div>')
    cancel = (f'<form method="post" action="/task" style="margin-top:8px"><input type="hidden" name="key" value="{E(o["signal_key"])}"><input type="hidden" name="verb" value="cancel_pending">'
              f'<button class="btn no" onclick="return confirm(\'Cancel the resting order for {E(o["signal_key"])}?\')">Cancel order</button></form>') if o.get("signal_key") else ""
    inner = body[len('<div class="card">'):] if body.startswith('<div class="card">') else body
    if link and o.get("signal_key"):
        return f'<div class="card"><a href="/signal/{E(o["signal_key"])}" style="color:inherit">' + inner + '</a>' + cancel + '</div>'
    return '<div class="card">' + inner + cancel + '</div>'

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
        out.append('<div class="card"><h2>Events inside the hold (5 d)</h2>' + "".join(f'<div class="ev {ev_cls((e["t"] - now).total_seconds() / 3600, e["cls"], e["cls"] == "W")}">{E(C.fmt_dt(e["t"]))} · {E(e["ccy"])} {E(e["name"])} <span class="k">[{e["cls"]} {E(e["label"])}]</span></div>' for e in soon) + "</div>")
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
        rtxt = r.get("r_multiple") or ""; skip = f' ({r.get("skip_reason")}{" auto" if r.get("auto") == "1" else ""}{" superseded" if r.get("skip_reason") == "9" else ""})' if r.get("decision") == "skipped" else ""
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
        cls = e["cls"]; mark = ev_cls((e["t"] - now).total_seconds() / 3600, cls, cls == "W")
        out.append(f'<div class="ev {mark}">{e["t"].strftime("%H:%M")} · {E(e["ccy"])} {E(e["name"])} <span class="k">[{E(cls or "-")}{(" " + E(e["label"])) if e["label"] else ""}]</span></div>')
    if not win: out.append('<div class="card k">no events in the window</div>')
    return page("Events", "".join(out), "events")

def md_html(md: str) -> str:
    """tiny markdown -> html for the brief (headings, bullets, paragraphs, links); everything escaped first."""
    out = []; in_ul = False
    for line in md.splitlines():
        t = E(line.rstrip())
        t = re.sub(r"(https?://[^\s)]+)", r'<a href="\1" target="_blank">\1</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        if t.startswith("## "): 
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h2>{t[3:]}</h2>")
        elif t.startswith("# "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h1>{t[2:]}</h1>")
        elif t.lstrip().startswith(("- ", "* ")):
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{t.lstrip()[2:]}</li>")
        elif t.strip() == "":
            if in_ul: out.append("</ul>"); in_ul = False
        else: out.append(f"<p>{t}</p>")
    if in_ul: out.append("</ul>")
    return "\n".join(out)

def context_page(q: dict) -> str:
    from live import brief_runner
    st = C.load_json(os.path.join(brief_runner.brief_dir(CFG), "status.json"), {}) or {}
    briefs = brief_runner.list_briefs(CFG); sel = q.get("b") or (briefs[0]["name"] if briefs else None)
    out = [flash(q.get("msg"), q.get("ok", "1") == "1"), '<h1>Context brief</h1>']
    gen = st.get("state") == "generating"
    out.append(f'<div class="card"><div class="row"><form method="post" action="/brief" class="inline"><button class="btn go" {"disabled" if gen else ""}>{"Generating… (up to ~90 s)" if gen else "Generate brief"}</button></form>'
               f'<span class="k">claude-opus-5 · medium effort · public web sources · descriptive only, never a vote{" · started " + E(st.get("started", "")) if gen else ""}</span></div>'
               + (f'<div class="bad" style="margin-top:6px">last generation failed: {E(str((st.get("last") or {}).get("error")))}</div>' if st.get("state") == "error" else "") + '</div>')
    if sel:
        r = brief_runner.read_brief(CFG, sel)
        if r:
            head, body = r; meta = next((b for b in briefs if b["name"] == sel), None)
            t = meta["t"].strftime("%a %d %b %Y %H:%M UTC") if meta else sel
            fl = meta["flags"] if meta else []
            out.append(f'<div class="card"><div class="row"><span class="big">Brief · {E(t)}</span><span class="k">{E(head.strip("<!-> \n"))}</span></div>'
                       + (f'<div class="flash bad">FLAG: directional wording detected ({E(", ".join(fl))}). The brief must explain, never vote - read those lines with that in mind.</div>' if fl else '<div class="k ok">filter: no directional language found</div>')
                       + f'<div class="brief">{md_html(body)}</div></div>')
    out.append('<div class="card"><h2>Previous briefs</h2>' + ("".join(f'<div><a href="/context?b={E(b["name"])}">{E(b["t"].strftime("%a %d %b %Y %H:%M UTC"))}</a>{" <span class=bad>FLAG</span>" if b["flags"] else ""}</div>' for b in briefs[:30]) or '<div class="k">none yet</div>') + '</div>')
    return page("Context brief", "".join(out), "context", refresh=15 if gen else None)

def settings_page(q: dict) -> str:
    rm = C.risk_mult(CFG); syms = C.symbols(CFG)
    out = [flash(q.get("msg"), q.get("ok", "1") == "1"), '<h1>Settings</h1><div class="card"><h2>Risk multipliers (config/risk_mult.json)</h2><table><tr><th>symbol</th><th>mult</th></tr>']
    for s in syms: out.append(f'<tr><td>{E(s)}</td><td class="v">{rm.get(s.split(".")[0], 1.0)}</td></tr>')
    out.append('</table><div class="k">edit on the box (sizing only, C2); shown here so the number in effect is never a surprise</div></div>')
    cfgv = C.load_json(os.path.join(CFG["root"], "config", "live.json"), {}) or {}
    out.append(f'<div class="card"><h2>EA knobs (config/live.json)</h2><pre>{E(json.dumps(cfgv, indent=1))}</pre></div>')
    out.append('<div class="card"><h2>Test signal (demo only)</h2><div class="k">Publishes a synthetic TEST signal at the current price with an ATR-sized stop (TP1 = 2R, TP2 = 4R), parked like a real one. Approve it from its page to place a real demo order, then use the position page. Journal rows carry strategy TEST. Refused while that symbol has a parked signal or an open position.</div>'
               '<form method="post" action="/task" class="row" style="margin-top:8px"><input type="hidden" name="verb" value="test_signal"><select name="symbol" style="width:auto">' + "".join(f'<option value="{E(x)}">{E(x)}</option>' for x in syms) + '</select>'
               '<select name="direction" style="width:auto"><option>BUY</option><option>SELL</option></select><select name="sl_atr" style="width:auto"><option value="1.0">SL 1.0 ATR</option><option value="0.6">SL 0.6 ATR (can fail approve if price moves)</option><option value="1.5">SL 1.5 ATR</option></select><button class="btn">Publish test signal</button></form></div>')
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
    elif verb in C.VERBS_PENDING:
        s = C.signal(CFG, key)
        if not s: return "/", False, "unknown signal"
        if s.get("status") != "approved_pending": return f"/signal/{key}", False, f"signal is {s.get('status')}, no resting order"
        tid = C.write_task(CFG, s["symbol"], verb, {}, signal_id=s["signal_id"])
        a = C.wait_ack(CFG, tid, 12.0)
        if not a: return f"/signal/{key}", False, "cancel: task written, no ack within 12 s - check again shortly"
        return f"/signal/{key}", a.get("result") == "accepted", f"cancel pending order: {a.get('result')} - {C.reason_text(a.get('reason'))}"
    elif verb in C.VERBS_ADMIN:
        sym = form.get("symbol", "")
        if sym not in C.symbols(CFG): return "/settings", False, "unknown symbol"
        d = form.get("direction", "").upper()
        if d not in ("BUY", "SELL"): return "/settings", False, "bad direction"
        try: sl_atr = max(0.6, min(3.0, float(form.get("sl_atr", "1.0"))))   # the EA rejects stops under 0.5 ATR (doctrine min-stop)
        except ValueError: sl_atr = 1.0
        tid = C.write_task(CFG, sym, "test_signal", {"direction": d, "sl_atr": sl_atr})
        a = C.wait_ack(CFG, tid, 12.0)
        if not a: return "/settings", False, "test_signal: task written, no ack within 12 s"
        sid = (a.get("refs") or {}).get("signal_id")
        if a.get("result") == "accepted" and sid: return f"/signal/{sym}-{sid}", True, f"TEST signal #{sid} published on {sym} - approve or skip it here"
        return "/settings", False, f"test_signal: {a.get('result')} - {C.reason_text(a.get('reason'))}"
    else:
        return "/", False, "unknown verb"
    a = C.wait_ack(CFG, tid, 12.0)
    if not a: return back, False, f"{verb}: task written, no ack within 12 s (EA busy or stopped?) — check again shortly"
    ok = a.get("result") == "accepted"
    refs = a.get("refs") or {}
    return back, ok, f"{verb}: {a.get('result')} — {C.reason_text(a.get('reason'))}" + (f" · posid {refs.get('posid')} ticket {refs.get('order_ticket')}" if ok and verb == "approve" else "")

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
            if parts[0] == "api" and len(parts) == 3 and parts[1] == "advisor":
                if not C.safe_key(parts[2]): return self._send("bad request", "text/plain", 400)
                h, v = advisor_section(parts[2]); return self._send(json.dumps({"html": h, "v": v}), "application/json")
            if parts[0] == "backup.zip":
                from live import backup
                data, name, man = backup.build(CFG, 30); backup.record(CFG, man, name)
                C.append_line(os.path.join(CFG["root"], "web", "web_audit.log"), f"{C.now_iso()}|backup|{name}|{len(data)}B|{man['files']} files|by=web")
                self.send_response(200); self.send_header("Content-Type", "application/zip"); self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if parts[0] == "health": return self._send(json.dumps({"ok": True, "ts": C.now_iso()}), "application/json")
            if parts[0] == "signal" and len(parts) == 2: return self._send(signal_page(parts[1], q))
            if parts[0] == "position" and len(parts) == 2: return self._send(position_page(parts[1], q))
            if parts[0] == "context": return self._send(context_page(q))
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
                key = form.get("key", ""); text = (form.get("text") or "").strip(); mid = form.get("model") or AR.models(CFG)[0]["id"]
                m = AR.model_cfg(CFG, mid)
                if not C.safe_key(key) or not C.signal(CFG, key) or not text or not m: return self._redirect("/", "bad reply", False)
                C.atomic_write_json(os.path.join(CFG["root"], "advisor", "replies", f"{key}-{mid}-{C.now_utc().strftime('%Y%m%dT%H%M%S')}.json"), {"signal_key": key, "model": mid, "text": text[:4000], "ts": C.now_iso()})
                return self._redirect(f"/signal/{key}", f"reply queued for {m.get('label', mid)} — the answer appears in its panel", True)
            if u.path == "/advisor_retry":
                key = form.get("key", ""); mid = form.get("model", ""); m = AR.model_cfg(CFG, mid)
                if not C.safe_key(key) or not C.signal(CFG, key) or not m: return self._redirect("/", "bad request", False)
                C.atomic_write_json(os.path.join(CFG["root"], "advisor", "retry", f"{key}.{mid}.json"), {"signal_key": key, "model": mid, "ts": C.now_iso()})
                return self._redirect(f"/signal/{key}", f"{m.get('label', mid)} will run again — its panel shows progress", True)
            if u.path == "/brief":
                from live import brief_runner
                brief_runner.request(CFG, "web"); C.append_line(os.path.join(CFG["root"], "web", "web_audit.log"), f"{C.now_iso()}|brief_requested|by=web")
                return self._redirect("/context", "brief requested - the page refreshes itself while it generates", True)
            if u.path == "/telegram_test":
                ok = C.telegram_send(CFG, f"Test from the web app ({C.now_iso()}). If you read this, alerts from the box reach you.", LOG)
                return self._redirect("/", "Telegram test sent" if ok else "Telegram send FAILED - check token/chat id/network on the box", ok)
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
    for sub in ("web/charts", "advisor/replies", "advisor/retry", "tasks"): os.makedirs(os.path.join(CFG["root"], sub), exist_ok=True)
    srv = ThreadingHTTPServer((bind, port), H); srv.daemon_threads = True
    LOG(f"serving http://{bind}:{port}  root={CFG['root']}")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__": main()
