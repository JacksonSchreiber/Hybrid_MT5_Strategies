r"""
webapp.py - the trader's phone-first web app (spec §5). stdlib ThreadingHTTPServer, bound to the WireGuard address.

Routes: /  dashboard | /signal/<key> | /position/<key> | /journal | /events | /settings | /health
        /chart/<key>/<h4|d1>.png | POST /task (typed verbs against ids the EA reported - S6) | POST /reply | POST /kill
Every task written and every ack received is appended to <root>/web/web_audit.log (S8). No login: the tunnel is
the boundary (trader ruling 2026-09-15); HTTP inside WireGuard.
"""
from __future__ import annotations
import csv, hashlib, html, json, os, re, sys, time, urllib.parse, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import charts
from live import advisor_runner as AR
from live import verdict_fmt as VF
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
.vp{display:inline-block;padding:3px 12px;border-radius:12px;font-weight:800;font-size:14px;letter-spacing:.04em}
.vp.take{background:#12301a;color:#3fb950;border:1px solid #2ea043}.vp.takedef{background:#0d1f13;color:#3fb950;border:1px dashed #2ea043}
.vp.skip{background:#3a1d1d;color:#ff7b72;border:1px solid #f85149}.vp.adjust{background:#3a2f1f;color:#e3b341;border:1px solid #9e6a03}
.vp.wait,.vp.none{background:#21262d;color:var(--dim);border:1px solid var(--line)}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin:6px 0}
.chip{display:inline-block;padding:2px 8px;border-radius:9px;font-size:12px;font-weight:700;background:#21262d;color:var(--dim);border:1px solid transparent}
.chip.ok{color:#3fb950}.chip.no{color:#ff7b72}.chip.maybe{color:#e3b341}.chip.na{color:var(--dim)}
.chip.dec{border-color:#f85149;box-shadow:0 0 0 1px #f85149;background:#2a1717}
.chip.diff{border-color:#58a6ff}
.vline{margin:6px 0}.vline b{color:var(--fg)}
.advrow{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-top:6px}
.advrow .sm{color:var(--dim);font-size:13px;flex:1 1 220px;min-width:0}
.cd{font-weight:600}.brief h2{color:var(--txt);text-transform:none;letter-spacing:0;font-size:17px;margin-top:14px}.brief p,.brief li{font-size:15px;line-height:1.5}.brief a{word-break:break-all}
"""
JS = """
function tick(){document.querySelectorAll('[data-deadline]').forEach(function(el){var d=new Date(el.dataset.deadline);var s=Math.floor((d-Date.now())/1000);
 if(s<=0){el.textContent='EXPIRED';el.className='cd bad';return;}var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;el.textContent=(h>0?h+'h ':'')+m+'m '+x+'s';el.className='cd '+(s<3600?'warn':'ok');});}
function utc(){var d=new Date();var p=function(n){return (n<10?'0':'')+n};var el=document.getElementById('utc');if(el)el.textContent=p(d.getUTCHours())+':'+p(d.getUTCMinutes())+':'+p(d.getUTCSeconds())+' UTC';}
function els(){document.querySelectorAll('[data-started]').forEach(function(el){var s=Math.max(0,Math.floor((Date.now()-new Date(el.dataset.started))/1000));el.textContent=(s>=60?Math.floor(s/60)+'m ':'')+(s%60)+'s';});}
setInterval(function(){tick();utc();els();},1000);tick();utc();els();
(function(){var pb=document.getElementById('poslive');if(!pb||pb.dataset.closed==='1')return;var st=function(id,v){var e=document.getElementById(id);if(e&&v!==undefined&&v!==null)e.textContent=v;};
 var t=setInterval(function(){fetch('/api/position/'+encodeURIComponent(pb.dataset.key)).then(function(r){return r.json();}).then(function(p){if(!p||p.error)return;
  st('p-open_r',p.open_r);var o=document.getElementById('p-open_r');if(o)o.className='big v '+(p.open_r_ok?'ok':'bad');
  st('p-banked_r',p.banked_r);st('p-sl_live',p.sl_live);st('p-tp_live',p.tp_live);st('p-lots_live',p.lots_live);st('p-bars_open',p.bars_open);st('p-flags',p.flags);
  var be=document.querySelector('button[data-verb=sl_be]'),ra=document.querySelector('button[data-verb=ratchet_tp1]');if(be)be.disabled=!p.be_placeable;if(ra)ra.disabled=!p.ratchet_placeable;
  if(p.closed){clearInterval(t);document.querySelectorAll('button[data-verb]').forEach(function(b){b.disabled=true;});
   var n=document.createElement('div');n.className='flash ok';n.innerHTML='Position CLOSED - <a href="">reload for the final numbers</a>';pb.parentNode.insertBefore(n,pb);}
 }).catch(function(){});},5000);})();
(function(){var w=document.getElementById('sigwatch');if(!w)return;var shown=false;var t=setInterval(function(){fetch('/api/signal/'+encodeURIComponent(w.dataset.key)).then(function(r){return r.json();}).then(function(s){if(!s||s.error)return;
  var cd=document.querySelector('[data-deadline]');if(cd&&s.deadline)cd.dataset.deadline=s.deadline;
  if(!shown&&(s.status!==w.dataset.status||String(s.delay_count)!==w.dataset.delays)){shown=true;var n=document.createElement('div');n.className='flash '+(s.status==='open'?'ok':'bad');n.style.position='sticky';n.style.top='52px';n.style.zIndex='3';
   n.innerHTML='Signal updated: now <b>'+s.status+'</b>'+(s.status==='open'?' (re-presented, delay '+s.delay_count+')':'')+(s.auto_reason?' - '+s.auto_reason:'')+' - <a href="">tap to reload</a>';var m=document.querySelector('main');m.insertBefore(n,m.firstChild);}
 }).catch(function(){});},10000);})();
(function(){var box=document.getElementById('advisor');if(!box)return;setInterval(function(){
 var typed=[].some.call(box.querySelectorAll('textarea'),function(t){return t.value.trim()!==''||t===document.activeElement;});if(typed)return;
 fetch('/api/advisor/'+encodeURIComponent(box.dataset.key)).then(function(r){return r.json();}).then(function(j){if(j&&j.v&&j.v!==box.dataset.v){box.innerHTML=j.html;box.dataset.v=j.v;els();}}).catch(function(){});},4000);})();
var meta=document.querySelector('meta[name=autorefresh]');if(meta&&!document.querySelector('textarea:focus')){setTimeout(function(){if(!document.querySelector('textarea:focus'))location.reload();},parseInt(meta.content)*1000);}
"""

def page(title: str, body: str, active: str = "", refresh: int | None = None) -> str:
    tabs = [("/", "Home", "home"), ("/context", "Context", "context"), ("/journal", "Journal", "journal"), ("/equity", "Equity", "equity"), ("/events", "Events", "events"), ("/settings", "Settings", "settings")]
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
    # the risk rung the EA sizes at (LadderRiskPct): max-loss headroom after open risk < 1,000 -> 0.10%; 1,000-1,500 -> 0.25%;
    # above -> the ruled full size. headroom_max in the heartbeat is exactly that number (equity - aggregate risk - max floor).
    if hm < 1000: rung, nxt = "0.10%", f"steps up to 0.25% at 1,000 of headroom ({1000 - hm:,.0f} to go)"
    elif hm <= 1500: rung, nxt = "0.25%", f"steps up to full size above 1,500 ({1500 - hm:,.0f} to go) · down to 0.10% below 1,000"
    else: rung, nxt = "full ruled size", "steps down to 0.25% at 1,500 of headroom"
    pl = eq - f["initial_balance"]
    top = (f'<div class="row" style="align-items:baseline"><div><div class="k">Equity</div>'
           f'<div style="font-size:2.2em;font-weight:700" class="{"ok" if pl >= 0 else "bad"}">{eq:,.2f}</div>'
           f'<div class="k">{pl:+,.2f} ({pl / f["initial_balance"] * 100:+.2f}%) vs the {f["initial_balance"]:,.0f} start</div></div>'
           f'<div style="margin-left:auto;text-align:right"><div class="k">Risk level</div><div class="big">{rung} per trade</div>'
           f'<div class="k">{nxt}</div></div></div>')
    return (top + f'<div class="grid2"><div><div class="k">Daily headroom</div><div class="big v {"bad" if hd < 0.01 * f["initial_balance"] else "ok"}">{hd:,.0f} <span class="k">({pct(hd)})</span></div><div class="k">floor {f.get("daily_floor", 0):,.0f} · day {f.get("day_key", "")}</div></div>'
            f'<div><div class="k">Max-loss headroom</div><div class="big v {"bad" if hm < 0.01 * f["initial_balance"] else "ok"}">{hm:,.0f} <span class="k">({pct(hm)})</span></div><div class="k">floor {f.get("max_floor", 0):,.0f} · initial {f["initial_balance"]:,.0f}</div></div></div>'
            f'<div class="k">aggregate risk-to-stop {hb.get("aggregate_risk_to_stop", 0):,.0f} · buffer {f.get("buffer", 0):,.0f}</div>')



def ftmo_room_block(s: dict) -> str:
    """'room for N more at <this signal's true risk %>' - the EA's own pre-order check (equity - aggregate risk-to-stop - this trade must
    stay above the daily AND max floors + buffer), from the freshest heartbeat. Approving past it = EA auto-reject, code 10."""
    hbs = [h for h in (C.heartbeat(CFG, x) for x in C.symbols(CFG)) if h and (h.get("ftmo") or {}).get("initial_balance")]
    if not hbs: return '<div class="card k">FTMO room: unknown (no heartbeat with the account limits yet)</div>'
    hb = max(hbs, key=lambda h: C.parse_iso(h.get("ts")) or C.now_utc()); f = hb["ftmo"]; eq = float(hb.get("equity") or 0)
    spare = min(float(f.get("headroom_daily", 0)), float(f.get("headroom_max", 0))) - float(f.get("buffer", 0))
    # count at the risk this signal actually sizes at (the ladder rung x multiplier), not fixed 0.5%/1.0% tiers
    this = float((s.get("sizing") or {}).get("risk_pct_effective") or 0)
    per = this if this > 0 else 0.001
    n = max(0, int(spare // (per * eq))) if eq > 0 else 0
    fits = eq > 0 and spare >= this * eq
    cls = "ok" if (fits and n >= 2) else ("warn" if fits else "bad")
    note = ("" if fits else " — <b>this signal does NOT fit: approving it would be auto-rejected by the EA (code 10)</b>")
    return (f'<div class="card"><div class="k">FTMO room before this approval (aggregate risk-to-stop {float(hb.get("aggregate_risk_to_stop") or 0):,.0f}, '
            f'spare {spare:,.0f} above the tighter floor + buffer)</div><div class="big {cls}">room for {n} more at {per * 100:.2f}%</div>'
            f'<div class="k">this signal sizes at {this * 100:.2f}%{note}</div></div>')

def inverse_card(s: dict) -> str:
    """coach item 21: the parent's entry, stop, current R and bars open; the Inverse's levels; the fill rule."""
    p = s.get("parent") or {}; lv = s.get("levels") or {}
    st_ = s.get("status")
    state = {"open": "awaiting your decision", "approved_pending": "ARMED - waiting for the parent's stop (cancels if the parent banks, closes otherwise, or passes bar 18)",
             "approved": "FILLED", "cancelled": "cancelled", "expired": "expired (no response) - nothing placed",
             "skipped": "skipped"}.get(st_, st_)
    return (f'<div class="card"><h2>{E(str(p.get("strategy")))} Inverse</h2>'
            f'<div class="k">parent #{E(str(p.get("signal_id")))} {E(str(p.get("direction", "")))}: entry {p.get("entry")} · stop {p.get("stop")} · '
            f'open {C.r_fmt(p.get("open_r"))} after {p.get("bars_open")} H4 bars · unbanked</div>'
            f'<div class="big">{E(str(s.get("direction")))} stop-entry at {lv.get("entry")} · SL {lv.get("sl")} · bank {lv.get("tp1")} · TP2 {lv.get("tp2")}</div>'
            f'<div class="k"><b>fills only if the parent is stopped before H4 bar 19</b> (EA-held; kill switch, NO-ENTRY, spread, lot floor, '
            f'headroom and a 0.10R slippage guard run at the fill). Full size, no staging. State: {E(str(state))}</div></div>')


def promote_button(p: dict) -> str:
    """coach item 22: a waiting staged first tranche can be promoted to full at market during H4 bars 1-5."""
    if int(p.get("tranche") or 0) != 1 or int(p.get("staged_state") or 0) != 1: return ""
    k = f'{p["symbol"]}-{p["posid"]}'
    return (f'<form method="post" action="/task" style="margin-top:8px"><input type="hidden" name="key" value="{E(k)}">'
            f'<input type="hidden" name="verb" value="promote"><button class="btn go" style="width:100%">PROMOTE NOW - add the rest at market</button>'
            f'<div class="k">H4 bars 1-5 only; kill switch, spread, lot floor and FTMO headroom apply; after a promotion the bar-6 add does not fire</div></form>')


def staged_block(p: dict) -> str:
    """coach item 18: the two tranches of a staged entry - the first at the signal, the add at the close of H4 bar N."""
    tr = int(p.get("tranche") or 0)
    try:                                                    # coach item 20: the +1.5R pyramid level on every first-entry card
        e, slb = float(p.get("entry")), float(p.get("sl_risk_basis"))
        lvl = e + 1.5 * (e - slb)
        pyr = f'<div class="k">pyramid: +50% of the full size if price reaches +1.5R after the bank = <b>{lvl:.{max(2, len(str(p.get("entry")).split(".")[-1]))}f}</b> (stop at entry, target TP2)</div>'
    except (TypeError, ValueError): pyr = ""
    sr = short_raise_line(p)
    if tr == 3:
        return (f'<div class="card"><div class="k">Pyramid add (+1.5R after the bank)</div><div class="big">{p.get("lots_init")} lots added at {p.get("entry")}</div>'
                f'<div class="k">stop at the first entry, target TP2; the runner\'s stop is unchanged</div>{sr}</div>')
    if not tr: return f'<div class="card">{pyr}{sr}</div>' if (pyr or sr) else ""
    full = p.get("full_lots"); stt = int(p.get("staged_state") or 0)
    if tr == 1:
        due = C._srv_to_utc(p.get("add_due"), CFG) if p.get("add_due") else None
        state = {1: f'add of {max(0.0, float(full or 0) - float(p.get("lots_init") or 0)):.2f} lots due at the close of the bar ending '
                    f'{due.strftime("%a %d %b %H:%M UTC") if due else "?"} (if still open; weekends push it out)',
                 2: "add placed - see the other position of this signal",
                 3: f'add skipped - {p.get("staged_note") or ""}'}.get(stt, "")
        return (f'<div class="card"><div class="k">Staged entry · tranche 1 of 2</div><div class="big">{p.get("lots_init")} of {full} lots at the signal</div>'
                f'<div class="k">{E(state)}</div>{pyr}{sr}{promote_button(p)}</div>')
    return (f'<div class="card"><div class="k">Staged entry · tranche 2 of 2 (the add)</div><div class="big">{p.get("lots_init")} lots added at {p.get("entry")}</div>'
            f'<div class="k">banks and moves to entry at the FIRST tranche\'s +1R and entry, as one position</div>{sr}</div>')


def short_raise_line(p: dict) -> str:
    """coach item 25 (trader override 2026-10-01): on a SELL (not an Inverse), every ticket's stop moves to +L R at +1.5R."""
    if (p.get("direction") or "").upper() != "SELL" or p.get("strategy") == "Inverse": return ""
    st_ = int(p.get("short_raise") or 0)
    if st_ == 1:
        try: when = C._srv_to_utc(datetime.fromtimestamp(int(p.get("short_raise_time")), timezone.utc).isoformat(), CFG) if int(p.get("short_raise_time") or 0) > 0 else None   # EA epoch = server clock
        except (TypeError, ValueError, OverflowError): when = None
        return (f'<div class="k"><span class="pill ok">STOP RAISED</span> shorts rule: +1.5R reached, the stop on every ticket of this signal is now '
                f'<b>{p.get("short_raise_stop")}</b> (+0.25R)' + (f' since {when.strftime("%a %d %b %H:%M UTC")}' if when else "") + '</div>')
    if st_ == 2:
        return '<div class="k"><span class="pill bad">STOP RAISE REFUSED</span> +1.5R reached but the EA could not move the stop - still at entry (audit log)</div>'
    L = _f_live("short_raise_at_pyramid_r")
    if not L: return ""
    try:
        e, slb = float(p.get("entry")), float(p.get("sl_risk_basis"))
        dg = max(2, len(str(p.get("entry")).split(".")[-1]))
        return (f'<div class="k">shorts rule: if price reaches +1.5R = <b>{e + 1.5 * (e - slb):.{dg}f}</b> after the bank, the stop on every ticket '
                f'moves to +{L:g}R = <b>{e + L * (e - slb):.{dg}f}</b></div>')
    except (TypeError, ValueError): return ""


def _f_live(key: str):
    """a numeric key of the EA's live.json (<root>/config/live.json); None when absent or 0."""
    try:
        v = float((C.load_json(os.path.join(CFG["root"], "config", "live.json"), {}) or {}).get(key) or 0)
        return v if v > 0 else None
    except (TypeError, ValueError): return None


def corr_block(s: dict) -> str:
    """item 9 (coach 2026-09-30 late): measured 60d correlation with every open position, weighted 1.0 / 0.5 / 0, and the
    effective correlated risk against the 1.5% cap (over it = a correlation veto, code 5)."""
    try:
        from live import correlation as CORR
        r = CORR.compute(s, CFG)
    except Exception as e:
        return f'<div class="card k">correlated risk: unavailable ({E(repr(e))})</div>'
    cls = "bad" if r["veto"] else ("warn" if r["total"] > 0.75 * r["cap"] else "ok")
    rows = "".join(
        f'<div class="k">{E(x["symbol"])} {E(str(x["direction"]))} #{E(str(x["signal_id"]))}: '
        + ("same symbol, same direction" if x["same"] else ("corr n/a" if x["corr"] is None else f'corr {x["corr"]:+.2f} → signed {x["signed"]:+.2f}'))
        + f' · weight {x["weight"]:.1f} × unbanked {x["unbanked"]:.2f}% = +{x["adds"]:.2f}%</div>'
        for x in sorted(r["pairs"], key=lambda x: -x["adds"]))
    return (f'<div class="card"><div class="k">Correlated risk (60-day daily correlation; weight 1.0 at ≥0.6 or same trade, 0.5 at 0.3–0.6)</div>'
            f'<div class="big {cls}">{r["total"]:.2f}% of {r["cap"]:.1f}%{" — correlation veto (code 5)" if r["veto"] else ""}</div>'
            f'<div class="k">this signal {r["own"]:.2f}% + weighted unbanked open risk</div>{rows or "<div class=k>no open positions</div>"}</div>')

# ----------------------------------------------------------------------------- advisors (coach 2026-09-21: two per signal)
def _fam(mid: str) -> str: return "opus" if "opus" in mid else ("sonnet" if "sonnet" in mid else "other")

def _last_word(rec: dict | None) -> str | None:
    for c in reversed((rec or {}).get("consults") or []):
        if c.get("ok"):
            w = AR.verdict_word(c.get("text"))
            if w: return w
    return None

PILL = {"TAKE": "take", "TAKE (default)": "takedef", "SKIP": "skip", "ADJUST": "adjust", "WAIT": "wait"}

def vpill(v: str | None) -> str:
    return f'<span class="vp {PILL.get(v or "", "none")}">{E(v or "no verdict")}</span>'


def verdict_card(p: dict, other: dict | None = None) -> str:
    """The verdict block as a card (coach 2026-09-24). Whatever parsed is rendered; the raw text always stays one
    click away under "more", so a model drifting from the format costs detail, never the verdict."""
    dec = VF.decisive_step(p)
    oth = dict(other.get("steps") or []) if other else {}
    conf = f'<span class="k">confidence {E(p["confidence"])}</span>' if p.get("confidence") else ""
    # layer 1 beside layer 2 (coach 2026-09-24): the mechanical read is a chip row next to the verdict pill, so a
    # mechanical VETO sitting beside a TAKE opinion - or the reverse - is visible without reading a word.
    mech = ""
    if p.get("mech"):
        cls = "no" if p["mech"] == "VETO" else "ok"
        lbl = "VETO" + (f' · {p["mech_rule"]}' if p.get("mech_rule") else "") if p["mech"] == "VETO" else "mechanical PASS"
        bits = [f'<span class="chip {cls}">{E(lbl)}</span>']
        if p.get("d1_ext") is not None:
            bits.append(f'<span class="chip">D1 {p["d1_ext"]:+.2f} ATR (obs)</span>')
        for m in re.findall(r"(spread [0-9.]+R)|(signal bar \d{2}:\d{2} UTC)|(\b\d{2}:\d{2} bar[^·]*)", p.get("mech_detail") or ""):
            t = next((x for x in m if x), "").strip(" ·")
            if t: bits.append(f'<span class="chip">{E(t)}</span>')
        mech = '<div class="chips">' + "".join(bits) + "</div>"
    flags = ""
    if p.get("missing"): flags += f'<span class="pill warn">format drift: no {E(", ".join(p["missing"][:3]))}</span>'
    if p.get("unsourced") and p.get("opinion"): flags += '<span class="pill warn">unsourced opinion</span>'
    head = f'<div class="advrow">{vpill(p.get("verdict"))}{conf}{flags}</div>{mech}'
    strat = p.get("strategy")
    sm = f'<div class="vline"><b>{E(VF.plainify(p["summary"], strat))}</b></div>' if p.get("summary") else ""
    start = "".join(f'<span class="chip {g}">{E(n)} {"✓" if g == "ok" else ("✗" if g == "no" else "?")}</span>'
                    for n, g in p.get("start") or [])
    def chip(n, g):
        nm = VF.step_name(strat, n)
        lbl = f'{n}{"✓" if g == "ok" else ("✗" if g == "no" else "?")}' + (f' {E(nm)}' if n == dec and nm else "")
        return (f'<span class="chip {g}{" dec" if n == dec else ""}{" diff" if oth and oth.get(n) and oth.get(n) != g else ""}"'
                + (f' title="{E(nm)}"' if nm else "") + f'>{lbl}</span>')
    steps = "".join(chip(n, g) for n, g in p.get("steps") or [])
    q = (f'<div class="vline"><span class="k">quality</span> <b>{E(p["quality"])}</b>'
         + (f' <span class="k">{E(p["quality_why"])}</span>' if p.get("quality_why") else "") + "</div>") if p.get("quality") else ""
    op = ""
    if p.get("opinion") or p.get("opinion_text"):
        dis = p.get("opinion") and p.get("verdict") and p["opinion"] != p["verdict"]
        op = (f'<div class="vline" style="{"border-left:3px solid #58a6ff;padding-left:8px" if dis else ""}">'
              f'<span class="k">opinion</span> {vpill(p.get("opinion"))}'
              + (f'<span class="k">{E(p["opinion_conf"])}</span>' if p.get("opinion_conf") else "")
              + (f'<span class="k"> · differs from the verdict above</span>' if dis else "")
              + f'<div style="margin-top:4px">{E(VF.plainify(p.get("opinion_text"), strat))}</div></div>')
    src = (f'<div class="vline"><span class="k">sources</span> {E("; ".join(p["sources"]))}</div>' if p.get("sources")
           else ('<div class="vline"><span class="k bad">no sources cited</span></div>' if p.get("opinion") else ""))
    why = f'<div class="vline"><span class="k">why</span> {E(VF.plainify(p["why"], strat))}</div>' if p.get("why") else ""
    chg = f'<div class="vline"><span class="k">changes my mind</span> {E(p["changes"])}</div>' if p.get("changes") else ""
    more = ((f'<div class="k">notes</div><pre>{E(p["notes"])}</pre>' if p.get("notes") else "")
            + f'<div class="k">raw reply</div><pre>{E(p.get("raw") or "")}</pre>')
    return (head + sm
            + (f'<div class="chips">{start}</div>' if start else "")
            + (f'<div class="chips">{steps}<span class="k">{E(p.get("strategy") or "")} steps</span></div>' if steps else "")
            + q + op + src + why + chg + f'<details><summary class="k">more</summary>{more}</details>')


def _parsed_last(rec: dict | None) -> dict:
    for c in reversed((rec or {}).get("consults") or []):
        if c.get("ok") and c.get("kind") == "verdict": return VF.parse(c.get("text"))
    return VF.parse(None)


def advisor_rows(key: str, sig: dict) -> str:
    """Home screen: one row per advisor - label, verdict pill, confidence, the SUMMARY sentence - filled in as each
    consult returns, so the trader can triage from the list without opening anything."""
    out = []
    for m in AR.models(CFG):
        mid = m["id"]; lab = m.get("label", mid).split(" ·")[0]
        rec = C.load_json(os.path.join(CFG["root"], "advisor", "verdicts", f"{key}.{mid}.json"))
        if rec is not None and not AR.rec_matches(rec, sig): rec = None
        st = (rec or {}).get("status") or "idle"
        tag = f'<span class="advtag fam-{_fam(mid)}">{E(lab)}</span>'
        if st == "ok" or ((rec or {}).get("consults") and any(c.get("ok") for c in rec["consults"])):
            p = _parsed_last(rec)
            body = (f'{vpill(p.get("verdict"))}'
                    + (f'<span class="k">{E(p["confidence"])}</span>' if p.get("confidence") else "")
                    + (f'<span class="chip">size {E(p["size"])}{(" " + E(p["size_cond"])) if p.get("size") == "PROMOTE-IF" and p.get("size_cond") else ""}'
                       f'{(" (" + E(p["size_conf"]) + ")") if p.get("size_conf") else ""}</span>' if p.get("size") else "")   # coach item 22
                    + f'<span class="sm">{E(VF.plainify(p.get("summary"), p.get("strategy") or sig.get("strategy")))}</span>')
        elif st == "failed": body = '<span class="vp skip">BUNDLE FAILED</span><span class="sm">no consult was run on this card</span>'
        elif (rec or {}).get("policy_skip"): body = f'<span class="vp none">not run</span><span class="sm">{E((rec or {}).get("note", ""))}</span>'
        elif st in ("rate_limited", "error"): body = f'<span class="vp none">{"rate-limited" if st == "rate_limited" else "failed"}</span><span class="sm">{E(str((rec or {}).get("note") or ""))}</span>'
        elif st in ("running", "queued"): body = f'<span class="vp none">consulting…</span><span class="sm">{E(st)}</span>'
        else: body = '<span class="vp none">consulting…</span><span class="sm">waiting to start</span>'
        out.append(f'<div class="advrow">{tag}{body}</div>')
    return "".join(out)


# Measured run-to-run stability (coach 2026-09-24): three repeats of four identical cards. Sonnet returned
# SKIP / ADJUST / TAKE on one of them - at the coach's 1-in-5 bar - so a DISAGREE badge has to say that the fast
# panel's verdict is not reproducible, otherwise the trader reads a coin flip as a second opinion.
FLIP_RATE = {"sonnet-low": (1, 4, "SKIP/ADJUST/TAKE on three identical runs"), "opus-high": (0, 4, "")}

def stability_note(ms: list, words: dict) -> str:
    bits = []
    for m in ms:
        f = FLIP_RATE.get(m["id"])
        if not f or not f[0] or not words.get(m["id"]): continue
        bits.append(f'{m["id"].split("-")[0].title()} flipped on {f[0]} of {f[1]} repeat runs of the same card ({f[2]})')
    return f'<div class="k" style="margin-top:4px">⚠ {E(" · ".join(bits))}</div>' if bits else ""


def advisor_section(key: str) -> tuple[str, str]:
    """both advisor panels + the AGREE / DISAGREE badge. Returns (html, version) - the page polls /api/advisor/<key>
    and swaps the html in the moment a verdict lands."""
    ms = AR.models(CFG)
    sig = C.signal(CFG, key) or {}
    recs = {m["id"]: C.load_json(os.path.join(CFG["root"], "advisor", "verdicts", f"{key}.{m['id']}.json")) for m in ms}
    recs = {k: (r if AR.rec_matches(r, sig) else None) for k, r in recs.items()}    # never show another publication's verdicts
    words = {mid: _last_word(r) for mid, r in recs.items()}
    have = [w for w in words.values() if w]
    unpaired = [m for m in ms if (recs[m["id"]] or {}).get("policy_skip")]
    if unpaired and len(have) == len(ms) - len(unpaired) and have:
        agree = True
        badge = f'<span class="badge wait">{E(have[0])} · {E(ms[0]["label"].split(" ·")[0])} only - {E((recs[unpaired[0]["id"]] or {}).get("note", "fallback"))}</span>'
    elif len(ms) > 1 and len(have) == len(ms):
        agree = len(set(have)) == 1
        badge = (f'<span class="badge agree">AGREE · {E(have[0])}</span>' if agree else
                 '<span class="badge disagree">DISAGREE · ' + " vs ".join(E(f"{words[m['id']]} ({m['id'].split('-')[0].title()})") for m in ms) + '</span>'
                 + stability_note(ms, words))
    elif len(ms) == 1 and have:
        # single panel (coach 2026-09-24, after the veto-discrimination pass): the verdict itself is the badge -
        # there is nothing left to agree or disagree with.
        agree = True
        badge = f'<span class="badge agree">{E(have[0])}</span>'
    else:
        agree = True
        badge = f'<span class="badge wait">{len(have)} of {len(ms)} verdict{"" if len(ms) == 1 else "s"} in</span>'
    parsed = {m["id"]: _parsed_last(recs[m["id"]]) for m in ms}     # both, so each card can mark where they differ
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
        first_steps = dict(parsed.get(mid, {}).get("steps") or [])
        for c in cs:
            if c.get("kind") == "reply": body.append(f'<div class="k">you · {E(c.get("ts", ""))}</div><pre>{E(c.get("prompt", ""))}</pre>')
            if c.get("ok"):
                pv = VF.parse(c.get("text"))
                oth = parsed.get(next((x["id"] for x in ms if x["id"] != mid), ""), None)
                body.append(f'<div class="k">{E(mid)} · {E(c.get("ts", ""))} · {c.get("elapsed_s")}s'
                            + (" · follow-up" if c.get("kind") == "reply" else "") + "</div>"
                            + verdict_card(pv, oth if c.get("kind") != "reply" else {"steps": list(first_steps.items())}))
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
    pol = AR.opus_policy(CFG); st = pol.get("step", "none")
    pl = ('<div class="k">Opus fallback: <b class="ok">none</b> - Opus runs on every signal (step A / B flip automatically on the coach\'s trigger)</div>' if st == "none" else
          f'<div class="k">Opus fallback: <b class="warn">step {E(st)}</b> since {E(str(pol.get("since")))} - {E(str(pol.get("reason", "")))}</div>')
    return f'<div class="card"><div class="k">Advisor consults today (UTC {day})</div><div class="grid2">{"".join(cells)}</div>{pl}</div>'

def shadow_line() -> str:
    h = C.load_json(os.path.join(CFG["root"], "config", "lineup_history.json"), None)
    if not h or not h.get("events"): return ""
    ev = h["events"]; start = C.parse_iso(ev[0].get("date") + "T00:00:00Z"); last = C.parse_iso(ev[-1].get("date") + "T00:00:00Z")
    now = C.now_utc(); d_all = (now - start).days if start else 0; d_last = (now - last).days if last else 0
    tail = f" · current configuration ({E(ev[-1].get('event', ''))}, {len(ev[-1].get('lineup') or [])} symbols) since {E(ev[-1]['date'])}: {d_last} day{'s' if d_last != 1 else ''}" if len(ev) > 1 else ""
    return f'<div class="k">shadow day {d_all} (since {E(ev[0]["date"])}){tail}</div>'

def eligibility_card() -> str:
    rows = ELIG.table(CFG)
    out = [f'<div class="card"><div class="k">Live eligibility (demo) · decisions = approved + skips 1–8 (code 9, auto and TEST excluded) · costed R from broker deals, in FULL-POSITION R (a trial stop-out at 25% size counts about −0.25R; adds count toward their signal, not as decisions) · flag at ≥ {ELIG.FLAG_N} decisions with R ≥ 0</div>'
           '<table><tr><th>symbol</th><th>dec</th><th>appr</th><th>skip</th><th>no-resp</th><th>closed</th><th>costed R</th><th></th></tr>']
    active = [a for a in rows if a["decisions"] or a["open"]]; idle = [a for a in rows if not (a["decisions"] or a["open"])]
    for a in active:
        rr = a["costed_r"]; unc = f' <span class="k">+{a["uncosted"]} unpriced</span>' if a["uncosted"] else ""
        flag = '<span class="pill ok">≥20 · R≥0</span>' if a["flag"] else (f'<span class="k">{a["decisions"]}/{ELIG.FLAG_N}</span>' if a["decisions"] < ELIG.FLAG_N else '<span class="pill bad">R&lt;0</span>')
        out.append(f'<tr><td>{E(a["symbol"])}</td><td class="v">{a["decisions"]}</td><td class="v">{a["approved"]}</td><td class="v">{a["skipped"]}</td><td class="v">{a["no_response"]}</td>'
                   f'<td class="v">{a["closed"]}{f" +{a['open']} open" if a["open"] else ""}</td><td class="v {"ok" if rr >= 0 else "bad"}">{rr:+.2f}{unc}</td><td>{flag}</td></tr>')
    out.append("</table>")
    if idle: out.append(f'<div class="k" style="margin-top:6px">no demo decisions yet ({len(idle)}): {E(", ".join(a["symbol"].split(".")[0] for a in idle))}</div>')
    out.append("</div>")
    return "".join(out)




def tc_watch_line() -> str:
    """The coach's watch item counts down in the open, so it cannot quietly expire (monitor flags at 100)."""
    st = C.load_json(os.path.join(CFG["root"], "advisor", "trendcont_watch.json")) or {}
    n = int(st.get("count") or 0)
    if not n: return ""
    done = " — due for re-check" if st.get("flagged") else ""
    return f'<div class="k">coach watch item: {n}/100 TrendCont decisions since the 2026-09-24 ruling{done}</div>'


def sysres_line() -> str:
    """box CPU / RAM / disk (trader 2026-09-23): one line, amber then red as each approaches its limit."""
    from live import sysres
    r = sysres.read(CFG)
    def cls(v, warn, bad): return "bad" if (v is not None and v >= bad) else ("warn" if (v is not None and v >= warn) else "ok")
    cpu = r["cpu_pct"]; mu, mt = r["mem_used_gb"], r["mem_total_gb"]; du, dt = r["disk_used_gb"], r["disk_total_gb"]
    mp = (mu / mt * 100) if (mu is not None and mt) else None
    dp = (du / dt * 100) if (du is not None and dt) else None
    parts = [f'CPU <b class="{cls(cpu, 70, 85)}">{cpu:.0f}%</b>' if cpu is not None else "CPU -",
             f'RAM <b class="{cls(mp, 80, 92)}">{mu:.1f}</b> / {mt:.1f} GB' if mp is not None else "RAM -",
             f'disk <b class="{cls(dp, 80, 90)}">{du:.0f}</b> / {dt:.0f} GB ({dt - du:.0f} free)' if dp is not None else "disk -"]
    return f'<div class="k">box: {" · ".join(parts)}</div>'

def be_ok(p: dict) -> bool:
    """SL->BE offered only if it would TIGHTEN: a stop already past entry (ratchet, or moved by hand in MT5) makes BE a loosen."""
    try:
        sl, e = float(p.get("sl_live") or 0), float(p.get("entry") or 0)
        past = sl > 0 and ((sl >= e) if (p.get("direction") or "").upper() == "BUY" else (sl <= e))
    except (TypeError, ValueError): past = False
    return bool(p.get("be_placeable")) and not past

def pos_flags(p: dict, closed: bool) -> str:
    xe = p.get("last_external_edit") or ""
    return (f"banked {p.get('banked')} · tp1_done {p.get('tp1_done')} · ratcheted {p.get('ratcheted')} · close-now {C.r_fmt(p.get('closenow_r'))}"
            + (f" · edited in MT5 ×{p.get('external_edits')}: {xe.split(' ', 1)[-1]}" if xe else "")
            + f" · updated {p.get('ts', '')}{' · CLOSED' if closed else ''}")

def position_live(key: str) -> dict:
    """the fields the position page updates in place every 5 s (formatted server-side, so the page and the poll agree)."""
    p = C.position(CFG, key)
    if not p: return {"error": "no such position"}
    closed = bool(p.get("_closed"))
    return {"open_r": C.r_fmt(p.get("open_r")), "open_r_ok": (p.get("open_r") or 0) >= 0, "banked_r": C.r_fmt(p.get("banked_r")),
            "sl_live": p.get("sl_live"), "tp_live": p.get("tp_live") or "-", "lots_live": p.get("lots_live"), "bars_open": p.get("bars_open"),
            "flags": pos_flags(p, closed), "closed": closed, "be_placeable": be_ok(p) and not closed,
            "ratchet_placeable": bool(p.get("ratchet_placeable")) and not closed}

def signal_live(key: str) -> dict:
    s = C.signal(CFG, key)
    if not s: return {"error": "no such signal"}
    return {"status": s.get("status", ""), "delay_count": s.get("delay_count", 0), "deadline": s.get("deadline", ""), "auto_reason": s.get("auto_reason", "")}

# ----------------------------------------------------------------------------- pages
def dashboard(q: dict) -> str:
    out = [flash(q.get("msg"), q.get("ok", "1") == "1")]
    syms = C.symbols(CFG)
    ks = C.kill_switch(CFG)
    from live import mt5feed
    fs = mt5feed.status()
    if fs.get("available"):
        out.append(f'<div class="k">terminal feed: {"connected" if fs.get("connected") else "<span class=bad>NOT CONNECTED</span>"} · {E(str(fs.get("server") or ""))} · build {fs.get("build")}</div>')
    out.append(sysres_line())
    out.append(f'<div class="card"><div class="row"><div><div class="k">Kill switch</div><div class="big {"ok" if ks else "bad"}">{"TRADING ENABLED" if ks else ("DISABLED" if ks is False else "DISABLED (no config file)")}</div></div>'
               f'<form method="post" action="/kill" class="inline" style="margin-left:auto"><input type="hidden" name="enable" value="{0 if ks else 1}"><button class="btn {"no" if ks else "go"}" onclick="return confirm(\'{"Disable" if ks else "Enable"} trading?\')">{"Disable" if ks else "Enable"}</button></form></div></div>')
    acct = next((hb for hb in (C.heartbeat(CFG, x) for x in syms) if hb and (hb.get("ftmo") or {}).get("initial_balance")), None)
    if acct: out.append(f'<div class="card"><div class="k">Account · from {E(acct.get("symbol", ""))} beat {C.rel_time(C.parse_iso(acct.get("ts")))}</div>' + ftmo_block(acct) + '</div>')
    # (trader 2026-10-01: the shadow-day / watch-counter / opinion-counter lines are gone from the top - the monitor still
    #  tracks them and sends the coach's alerts by Telegram)
    # backup + telegram test (trader rulings 2026-09-16: manual 30-day zip instead of a nightly pull)
    from live import backup
    ds = backup.days_since(CFG); lb = backup.last(CFG)
    if ds is None: bcls, btxt = "bad", "never backed up"
    else: bcls = "ok" if ds < 15 else ("warn" if ds < 25 else "bad"); btxt = f"{ds:.0f} day{'s' if ds >= 1.5 else ''} since last backup"
    out.append(f'<div class="card"><div class="row"><div><div class="k">Backup</div><div class="big {bcls}">{E(btxt)}</div><div class="k">{E(lb["name"]) if lb else "zip of the last 30 days: journals, queue, state, verdicts, calendar, MT5 presets"}</div></div>'
               f'<a class="btn" href="/backup.zip" style="margin-left:auto" onclick="setTimeout(function(){{location.reload()}},4000)">Download backup (30 d)</a>'
               f'<form method="post" action="/telegram_test" class="inline"><button class="btn">Telegram test</button></form></div>'
               f'<div class="row" style="margin-top:8px"><span class="k">Monthly export for the coach (journals + expired-TAKE blind outcomes):</span>'
               f'<a class="btn" style="padding:6px 10px;font-size:13px" href="/export/{(C.now_utc().replace(day=1) - timedelta(days=1)).strftime("%Y%m")}.zip">last month</a>'
               f'<a class="btn" style="padding:6px 10px;font-size:13px" href="/export/{C.now_utc().strftime("%Y%m")}.zip">this month so far</a>'
               f'<a class="btn" style="padding:6px 10px;font-size:13px" href="/shadow_bank.csv">bank shadow log</a>'
               f'<a class="btn" style="padding:6px 10px;font-size:13px" href="/staged_entry.csv">staged entry log</a>'
               f'<a class="btn" style="padding:6px 10px;font-size:13px" href="/inverse_shadow.csv">inverse log</a></div></div>')
    # open signals
    sigs = C.list_signals(CFG); opn = [s for s in sigs if s.get("status") == "open"]
    # coach 2026-09-30: sorted by signal time (oldest first - nearest deadline); the D1-extension sort was withdrawn with
    # its look-ahead study. The D1 number stays on each card as an observation.
    ext = {}
    for s_ in opn:
        try: ext[s_["signal_key"]] = charts.d1_ext_of(s_)
        except Exception: ext[s_["signal_key"]] = None
    opn.sort(key=lambda x: str(x.get("signal_time") or ""))
    out.append("<h2>Pending signals</h2>")
    if not opn: out.append('<div class="card k">none</div>')
    for s in opn:
        out.append(f'<a href="/signal/{E(s["signal_key"])}"><div class="card"><div class="row"><span class="big">{E(s["symbol"])} {E(s["strategy"])} {E(s["direction"])}</span>{cls_pill(s.get("decision_class"))}<span class="k">#{s["signal_id"]}</span></div>'
                   f'<div class="row"><span class="k">deadline</span><span class="cd" data-deadline="{E(s.get("deadline", ""))}"></span><span class="k">delays {s.get("delay_count", 0)}</span><span class="k">{E((s.get("regime") or {}).get("pretty", ""))}</span>'
                   + (f'<span class="k">D1 {ext[s["signal_key"]]:+.2f} ATR (obs)</span>' if ext.get(s["signal_key"]) is not None else '<span class="k">D1 ext n/a</span>')
                   + '</div>'
                   + advisor_rows(s["signal_key"], s) + '</div></a>')
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
    # a staged add (tranche 2) or a pyramid add (tranche 3) sits indented under the position it was added to
    def _tr(p): return int(p.get("tranche") or 0)
    parents = [p for p in pos if _tr(p) < 2]
    children = {}
    for p in pos:
        if _tr(p) >= 2: children.setdefault((p["symbol"], str(p.get("signal_id"))), []).append(p)
    ordered = []
    for p in parents:
        ordered.append((p, False))
        for c in sorted(children.pop((p["symbol"], str(p.get("signal_id"))), []), key=_tr): ordered.append((c, True))
    for rest in children.values(): ordered += [(c, False) for c in rest]   # parent already closed: show it on its own
    for p, child in ordered:
        what = {2: "staged add", 3: "pyramid add (+1.5R)"}.get(_tr(p), "")
        title = (f'↳ {E(what)} {E(p["direction"])}' if child else f'{E(p["symbol"])} {E(p["strategy"])} {E(p["direction"])}'
                 + (f' <span class="k">({E(what)})</span>' if what else ""))
        style = ' style="margin-left:28px;border-left:3px solid var(--line, #888)"' if child else ""
        if _tr(p) == 1 and int(p.get("staged_state") or 0) == 1:
            # trial phase: the staged first tranche, waiting for the bar-6 add (or a Promote now)
            due = C._srv_to_utc(p.get("add_due"), CFG) if p.get("add_due") else None
            left = ""
            if due:
                mins = int((due - C.now_utc()).total_seconds() // 60)
                left = f" · add in {mins // 60}h{mins % 60:02d}" if mins > 0 else " · add due now"
            title += f' <span class="pill warn">TRIAL {p.get("lots_init")}/{p.get("full_lots")} lots{left}</span>'
        if int(p.get("short_raise") or 0) == 1 and not child: title += ' <span class="pill ok">STOP +0.25R</span>'
        out.append(f'<a href="/position/{E(p["symbol"])}-{p["posid"]}"><div class="card"{style}><div class="row"><span class="big">{title}</span><span class="big v {"ok" if p.get("open_r", 0) >= 0 else "bad"}">{C.r_fmt(p.get("open_r"))}</span>'
                   f'<span class="k">banked {C.r_fmt(p.get("banked_r"))} · {p.get("lots_live")} lots · {p.get("bars_open")} bars</span></div></div></a>')
    # instances: one compact table (the expanded universe runs ~40 charts - a card each would bury everything below)
    live_n = 0; rows_i = []
    for sym in syms:
        hb = C.heartbeat(CFG, sym); age = C.heartbeat_age_s(hb)
        alive = hb and hb.get("status") == "running" and age is not None and age < CFG["monitor"]["heartbeat_stale_s"]; live_n += bool(alive)
        flags = "" if not hb else ("" if hb.get("terminal_trade_allowed") and hb.get("mql_trade_allowed") else ' <span class="pill bad">AutoTrading OFF</span>')
        wf = ' <span class="pill">weekend-flat</span>' if hb and hb.get("weekend_flat") else ""
        # coach 2026-09-23: a blank regime is a QUIET outage - it drops TrendCont and the whole TAKE class
        rg = (hb or {}).get("regime"); d1 = (hb or {}).get("d1_bars")
        rgc = (f'<span class="pill {"ok" if rg else "bad"}">{E(rg) if rg else "REGIME BLANK"}</span>'
               + (f' <span class="k">D1 {d1}</span>' if d1 is not None and (not rg or d1 < 211) else "")) if hb else ""
        tvs = C.tv_symbol(CFG, sym); tvl = f"https://www.tradingview.com/chart/?symbol={urllib.parse.quote(tvs)}&interval=240"
        rows_i.append(f'<tr><td>{E(sym)}{wf}</td><td><span class="pill {"ok" if alive else "bad"}">{"alive" if alive else "STALE"}</span>{flags} {rgc}</td>'
                      f'<td class="k">{C.rel_time(C.parse_iso(hb.get("ts")) if hb else None)}</td><td><a href="{tvl}" target="_blank">TV ↗</a></td></tr>'
                      + (f'<tr><td colspan="4" class="k">alerts: {E(", ".join(hb.get("alerts") or []))}</td></tr>' if hb and hb.get("alerts") else ""))
    tbl = f'<table><tr><th>symbol</th><th>EA · regime</th><th>beat</th><th></th></tr>{"".join(rows_i)}</table>'
    blank = [x for x in syms if (C.heartbeat(CFG, x) or {}).get("regime_ready") is False]
    all_ok = live_n == len(syms) and len(syms) > 0 and not blank
    out.append(f'<h2>Instances · <span class="{"ok" if all_ok else "bad"}">{live_n}/{len(syms)} alive</span></h2><div class="card"><details{"" if all_ok else " open"}><summary class="k">'
               f'{"all EA instances alive, every regime tagged - tap for the list and TradingView links" if all_ok else ("REGIME BLANK on " + str(len(blank)) + " instance(s): " + E(", ".join(blank)) + " - TrendCont and the TAKE class are OFF there until D1 history fills" if blank and live_n == len(syms) else "SOME INSTANCES STALE - list")}</summary>{tbl}</details></div>')
    out.append('<h2>Advisors</h2>' + advisor_load_card())
    out.append('<h2>Live eligibility</h2>' + eligibility_card())
    # recent decided signals
    out.append("<h2>Recent signals</h2><div class='card'><table><tr><th>#</th><th>signal</th><th>status</th><th>time</th></tr>")
    for s in [x for x in sigs if x.get("status") != "open"][:12]:
        out.append(f'<tr><td><a href="/signal/{E(s["signal_key"])}">{s["signal_id"]}</a></td><td>{E(s["symbol"])} {E(s["strategy"])} {E(s["direction"])}</td><td>{status_pill(s.get("status", ""))}</td><td class="k">{E((s.get("published_at") or "")[:16].replace("T", " "))}</td></tr>')
    out.append("</table></div>")
    return page("Hybrid live", "".join(out), "home", refresh=30)

def spread_cls(sz: dict) -> str:
    """Colour the live spread against the gate itself (live.json max_spread_r), so one number drives both: red = an
    approve would be refused `spread_too_wide` right now, amber = over half the gate."""
    r = float(sz.get("spread_r") or 0)
    mx = float(sz.get("max_spread_r") or 0) or 0.10
    return "bad" if r > mx else ("warn" if r > mx / 2 else "ok")


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
               f'<div><span class="k">lots</span> <b class="v">{sz.get("lots")}</b> <span class="k">{E(C.lots_line(sz))}</span></div><div><span class="k">risk mult</span> <b class="v">{sz.get("risk_mult_applied")}</b> <span class="k">→ {float(sz.get("risk_pct_effective", 0) or 0) * 100:.2f}%</span></div>'
               f'<div><span class="k">spread now</span> <b class="v {spread_cls(sz)}">{float(sz.get("spread_r") or 0):.3f}R</b> <span class="k">({sz.get("spread")} — paid the moment it opens)</span></div></div>'
               f'<div class="k">delays {d} · implicit streak {s.get("implicit_streak", 0)} · presented {E(s.get("published_at", ""))} · kill switch {"on" if s.get("trading_enabled") else "OFF"}</div></div>')
    # events
    evs = s.get("events") or []
    out.append('<div class="card"><h2>Events (next 14 d)</h2>')
    for e in evs[:20]:
        out.append(f'<div class="ev {ev_cls(e.get("hours_until"), e.get("cls"), e.get("binding"))}">{E(C.fmt_dt(C.parse_iso(e.get("t_utc"))))} · {E(e.get("ccy", ""))} {E(e.get("name", ""))} <span class="k">[{E(e.get("cls", ""))} {E(e.get("label", ""))}]</span>{" BINDING" if e.get("binding") else ""}</div>')
    if not evs: out.append('<div class="k">no notable events inside the window</div>')
    _, cov = C.load_events(CFG); st = C.parse_iso(s.get("signal_time_utc") or s.get("signal_time"))
    if cov and st and cov < st + timedelta(days=14): out.append(f'<div class="warn">calendar coverage ends {cov.strftime("%Y-%m-%d")} — later events unknown</div>')
    eg = s.get("election_gate") or {}
    if eg.get("hit"): out.append(f'<div class="bad">election gate: {E(eg.get("event", ""))}</div>')
    out.append("</div>")
    # advisors: two independent consults, each panel fills in the moment its verdict lands
    html_adv, v = advisor_section(key)
    out.append(f'<div class="card"><h2>Advisors</h2><div id="advisor" data-key="{E(key)}" data-v="{v}">{html_adv}</div></div>')
    # FTMO room (coach 2026-09-21): what the account can still take before an approve is auto-rejected (code 10)
    if is_open:
        out.append(ftmo_room_block(s))
        out.append(corr_block(s))
    # actions
    if s.get("strategy") == "Inverse":
        out.insert(2, inverse_card(s))
    if is_open and s.get("strategy") == "Inverse":
        # coach item 21: approving ARMS the EA-held stop-entry; no size/entry choice on an Inverse card
        out.append(f'<div class="card"><h2>Decide</h2><form method="post" action="/task"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="approve">'
                   f'<input type="hidden" name="entry_mode" value="market"><div class="k">Approving ARMS the Inverse: nothing is placed unless the parent is stopped before H4 bar 19.</div>'
                   '<div style="margin-top:8px"><button class="btn go" style="width:100%">ARM INVERSE</button></div></form>'
                   f'<form method="post" action="/task" style="margin-top:10px"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="skip"><select name="reason_code">'
                   + "".join(f'<option value="{k}">{k}  {E(v)}</option>' for k, v in C.SKIP_REASONS.items()) + '</select><div style="margin-top:8px"><button class="btn no" style="width:100%">SKIP</button></div></form></div>')
    elif is_open:
        modes = s.get("entry_modes") or ["market"]
        out.append(f'<div class="card"><h2>Decide</h2><form method="post" action="/task"><input type="hidden" name="key" value="{E(key)}"><input type="hidden" name="verb" value="approve">'
                   + f'<select name="entry_mode"><option value="market">Enter NOW at market (SL/TP as shown)</option><option value="pending">Pending order at the original entry {lv.get("entry")} (waits for price to come back)</option></select>'
                   + (('<div style="margin-top:8px"><span class="k">Size (market entries):</span> <select name="size">'
                       '<option value="staged" selected>Staged - 25% now, the rest at the bar-6 close (default)</option>'
                       '<option value="full">Full now - the whole ruled size at once</option></select></div>')
                      if float((sz.get("lots") or 0)) >= 0.02 else '<input type="hidden" name="size" value="staged">')
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
    out.append(f'<div id="sigwatch" data-key="{E(key)}" data-status="{E(s.get("status", ""))}" data-delays="{s.get("delay_count", 0)}"></div>')
    return page(f"#{s['signal_id']} {s['strategy']} {s['direction']}", "".join(out), "home")   # no full-page refresh: /api/signal state poll + banner

def chart_block(sig: dict, levels: dict | None = None, marks: list | None = None) -> str:
    """interactive Lightweight-Charts block fed from the signal's own bars; everything drawn on load."""
    ov = C.clean_overlay(sig.get("overlay"), (sig.get("levels") or {}).get("entry"))   # never let garbage geometry rescale the chart
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
    # Enter now at the market instead of waiting for the price to come back (trader ruling 2026-09-30). Shown with what
    # the move does to the trade: price nearer the stop shortens it, which RAISES R:R and cuts the loss if it fails -
    # that is often the better entry, not a concession, so the numbers are on the button rather than a warning.
    fill = ""
    if o.get("signal_key"):
        mkt, sl, tp, px = o.get("market"), o.get("sl"), o.get("tp"), o.get("price")
        note = ""
        try:
            if mkt and sl and px:
                now_stop, plan_stop = abs(float(mkt) - float(sl)), abs(float(px) - float(sl))
                pct = (now_stop / plan_stop * 100) if plan_stop else 0
                rr_now = (abs(float(tp) - float(mkt)) / now_stop) if (tp and now_stop) else None
                rr_pl = (abs(float(tp) - float(px)) / plan_stop) if (tp and plan_stop) else None
                better = pct < 100
                dg = len(str(px).split(".")[1]) if "." in str(px) else 0      # the order's own precision (5 for FX, 2 for BTC)
                note = (f'<div class="k">at the market now: stop {now_stop:.{dg}f} ({pct:.0f}% of planned)'
                        + (f' · R:R {rr_pl:.2f} → <b class="{"ok" if better else "warn"}">{rr_now:.2f}</b>' if rr_now and rr_pl else "")
                        + (' · nearer the stop, so a smaller loss if it fails' if better else ' · further from the stop, so a bigger one') + '</div>')
        except (TypeError, ValueError): pass
        fill = (note + f'<form method="post" action="/task" style="margin-top:6px"><input type="hidden" name="key" value="{E(o["signal_key"])}"><input type="hidden" name="verb" value="fill_now">'
                f'<button class="btn go" onclick="return confirm(\'Enter {E(o["symbol"])} at the market now instead of waiting for {o["price"]}?\')">Enter at market now</button></form>')
    cancel = (f'<form method="post" action="/task" style="margin-top:8px"><input type="hidden" name="key" value="{E(o["signal_key"])}"><input type="hidden" name="verb" value="cancel_pending">'
              f'<button class="btn no" onclick="return confirm(\'Cancel the resting order for {E(o["signal_key"])}?\')">Cancel order</button></form>') if o.get("signal_key") else ""
    cancel = fill + cancel
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
               f'<div class="card" id="poslive" data-key="{E(key)}" data-closed="{1 if closed else 0}"><div class="grid2"><div><div class="k">open R</div><div id="p-open_r" class="big v {"ok" if p.get("open_r", 0) >= 0 else "bad"}">{C.r_fmt(p.get("open_r"))}</div></div><div><div class="k">banked R</div><div id="p-banked_r" class="big v">{C.r_fmt(p.get("banked_r"))}</div></div>'
               f'<div><span class="k">entry</span> <b class="v">{p.get("entry")}</b></div><div><span class="k">SL live</span> <b id="p-sl_live" class="v bad">{p.get("sl_live")}</b> <span class="k">(risk basis {p.get("sl_risk_basis")})</span></div>'
               f'<div><span class="k">TP1</span> <b class="v">{p.get("tp1") or "-"}</b></div><div><span class="k">TP live</span> <b id="p-tp_live" class="v">{p.get("tp_live") or "-"}</b></div>'
               f'<div><span class="k">lots</span> <b id="p-lots_live" class="v">{p.get("lots_live")}</b> <span class="k">of {p.get("lots_init")}</span></div><div><span class="k">bars open</span> <b id="p-bars_open" class="v">{p.get("bars_open")}</b></div></div>'
               f'<div class="k" id="p-flags">{E(pos_flags(p, closed))}</div></div>')
    out.append(staged_block(p))
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
                    f'<button class="btn {cls}" data-verb="{verb}" {"" if enabled else "disabled"} onclick="return confirm(\'{label}?\')">{label}</button></form>')
        out.append('<div class="card"><h2>Manage</h2><div class="row">'
                   + b("sl_be", "SL → BE", be_ok(p)) + b("ratchet_tp1", "SL → TP1", bool(p.get("ratchet_placeable")))
                   + b("close50", "Close 50%", True) + b("close", "Close all", True, "no") + '</div>'
                   f'<div class="k">BE {"placeable" if p.get("be_placeable") else "not yet (+0.5R floor / stops level)"} · ratchet {"placeable" if p.get("ratchet_placeable") else "not yet (needs bank + past TP1)"} — the EA re-checks every rule before acting</div></div>')
    acks = [a for a in _acks() if a.get("position_id") == p["posid"] and a.get("symbol") == p["symbol"]]
    if acks:
        out.append('<div class="card"><h2>Task history</h2><table>')
        for a in sorted(acks, key=lambda a: a.get("executed_at", "")):
            out.append(f'<tr><td class="k">{E(a.get("executed_at", ""))}</td><td>{E(a.get("verb", ""))}</td><td class="{"ok" if a.get("result") == "accepted" else "bad"}">{E(a.get("result", ""))}</td><td>{E(a.get("reason", ""))}</td></tr>')
        out.append("</table></div>")
    return page(f"pos {p['posid']}", "".join(out), "home")        # no full-page refresh: the live fields poll /api/position (the chart keeps its view)

# ----------------------------------------------------------------------------- equity history (trader 2026-09-30)
RANGES = {"7d": 7, "30d": 30, "90d": 90, "all": None}

def _equity_series(days: int | None) -> tuple[list, list, dict]:
    """(balance points, equity samples, account) in TRUE UTC epochs. Balance comes from the broker's own deal history,
    rebuilt BACKWARDS from today's balance so it is anchored even if the window misses the first deposit; deal times
    are broker clock and are converted. Equity comes from the monitor's 5-minute samples."""
    from live import mt5feed
    now = time.time()
    t0 = now - days * 86400 if days else datetime(2015, 1, 1, tzinfo=timezone.utc).timestamp()
    h = mt5feed.balance_history(int(t0), int(now) + 86400) or {}
    deals = h.get("deals") or []
    off = lambda t: t - C.server_offset_h(datetime.fromtimestamp(t, timezone.utc), CFG) * 3600
    bal_now = h.get("balance")
    bal: list[tuple[float, float]] = []
    if bal_now is not None:
        b = float(bal_now); bal.append((now, b))
        start_t = t0
        for t, d, kind, sym in reversed(deals):
            tu = off(t)
            if tu < t0: break
            bal.append((tu, b))
            if kind == "funding":           # the curve begins AT the funding - before it the account did not exist, and
                start_t = None; break       # plotting its zero drags the axis to 0 and flattens the real curve to a sliver
            b -= d; bal.append((tu, b))
        if start_t is not None: bal.append((max(t0, off(deals[0][0]) - 3600) if deals else t0, b))
        bal.reverse()
    eq: list[tuple[float, float, float | None, float | None]] = []
    p = os.path.join(CFG["root"], "web", "equity.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                t = C.parse_iso(r.get("ts"))
                try: e = float(r["equity"])
                except (TypeError, ValueError, KeyError): continue
                if not t or t.timestamp() < t0: continue
                fd = float(r["daily_floor"]) if r.get("daily_floor") else None
                fm = float(r["max_floor"]) if r.get("max_floor") else None
                eq.append((t.timestamp(), e, fd, fm))
    return bal, eq, {"balance": bal_now, "equity": h.get("equity")}


def equity_json(rng: str) -> dict:
    """The equity page's series for the interactive chart. Lightweight Charts rejects duplicate or out-of-order times,
    and the balance rebuild emits two points per deal (before/after) for its step shape - so collapse each timestamp to
    the value AFTER it; the chart's step line type draws the same staircase from that."""
    bal, eq, _ = _equity_series(RANGES.get(rng, 30))
    def uniq(pts):
        out = {}
        for t, v in pts: out[int(t)] = round(float(v), 2)
        return [[t, out[t]] for t in sorted(out)]
    hb = next((h for h in (C.heartbeat(CFG, x) for x in C.symbols(CFG)) if h and (h.get("ftmo") or {}).get("initial_balance")), None) or {}
    f = hb.get("ftmo") or {}
    return {"balance": uniq(bal), "equity": uniq([(t, e) for t, e, _, _ in eq]),
            "daily_floor": uniq([(t, fd) for t, _, fd, _ in eq if fd]),
            "max_floor": f.get("max_floor"), "initial": f.get("initial_balance")}


def _svg_chart(bal, eq, floor_max, initial) -> str:
    W, H, L, R, T, B = 960, 380, 64, 16, 14, 30
    pts = [v for _, v in bal] + [e for _, e, _, _ in eq] + [x for x in (floor_max, initial) if x]
    ts = [t for t, _ in bal] + [t for t, *_ in eq]
    if len(ts) < 2 or not pts: return '<div class="card k">not enough history yet - equity is sampled every 5 minutes from today</div>'
    lo, hi = min(pts), max(pts); pad = (hi - lo) * 0.06 or 50; lo -= pad; hi += pad
    t0, t1 = min(ts), max(ts)
    if t1 <= t0: t1 = t0 + 1
    X = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    g = []
    for i in range(6):                                            # horizontal grid + y labels
        v = lo + (hi - lo) * i / 5; y = Y(v)
        g.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" stroke="#30363d" stroke-width="1"/>'
                 f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#8b949e">{v:,.0f}</text>')
    for i in range(6):                                            # x labels
        t = t0 + (t1 - t0) * i / 5
        g.append(f'<text x="{X(t):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="#8b949e">'
                 f'{datetime.fromtimestamp(t, timezone.utc):%d %b}</text>')
    def hline(v, color, label, dash):
        if v is None or not (lo <= v <= hi): return ""
        y = Y(v)
        return (f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" stroke="{color}" stroke-width="1.5" stroke-dasharray="{dash}"/>'
                f'<text x="{W - R - 4}" y="{y - 5:.1f}" text-anchor="end" font-size="11" fill="{color}">{E(label)}</text>')
    lines = [hline(initial, "#8b949e", f"start {initial:,.0f}" if initial else "", "2 4"),
             hline(floor_max, "#f85149", f"FTMO max-loss floor {floor_max:,.0f}" if floor_max else "", "6 4")]
    if len(bal) >= 2:                                             # balance as a step line - it only moves on a closed deal
        d = f"M{X(bal[0][0]):.1f},{Y(bal[0][1]):.1f}" + "".join(f" L{X(t):.1f},{Y(v):.1f}" for t, v in bal[1:])
        lines.append(f'<path d="{d}" fill="none" stroke="#58a6ff" stroke-width="2"/>')
    dfl = [(t, fd) for t, _, fd, _ in eq if fd]
    if len(dfl) >= 2:
        d = f"M{X(dfl[0][0]):.1f},{Y(dfl[0][1]):.1f}" + "".join(f" L{X(t):.1f},{Y(v):.1f}" for t, v in dfl[1:])
        lines.append(f'<path d="{d}" fill="none" stroke="#d29922" stroke-width="1.2" stroke-dasharray="3 3"/>')
    if len(eq) >= 2:
        d = f"M{X(eq[0][0]):.1f},{Y(eq[0][1]):.1f}" + "".join(f" L{X(t):.1f},{Y(e):.1f}" for t, e, _, _ in eq[1:])
        lines.append(f'<path d="{d}" fill="none" stroke="#3fb950" stroke-width="1.6"/>')
    return (f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto;background:#0b0f14;border:1px solid #30363d;border-radius:8px">'
            + "".join(g) + "".join(lines) + '</svg>')


EQUITY_JS = r'''
(function () {
  var box = document.getElementById('eqchart');
  if (!box || !window.LightweightCharts) return;
  var fmt = function (v) { return v == null ? '-' : v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); };
  fetch('/api/equity?range=' + encodeURIComponent(box.dataset.range)).then(function (r) { return r.json(); }).then(function (d) {
    var chart = LightweightCharts.createChart(box, {
      height: 420, layout: { background: { color: '#0d1117' }, textColor: '#c9d1d9' },
      grid: { vertLines: { color: '#1e242e' }, horzLines: { color: '#1e242e' } }, crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#30363d', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false, rightOffset: 4 },
      localization: { priceFormatter: fmt }, handleScale: true, handleScroll: true });
    // keep the FTMO max-loss floor inside the autoscale, so zooming in on recent bars never scrolls the kill line off
    var keepFloor = function (orig) {
      var r = orig(); if (!r || d.max_floor == null) return r;
      return { priceRange: { minValue: Math.min(r.priceRange.minValue, d.max_floor), maxValue: Math.max(r.priceRange.maxValue, d.max_floor) } };
    };
    var toPts = function (a) { return a.map(function (p) { return { time: p[0], value: p[1] }; }); };
    var bal = chart.addLineSeries({ color: '#58a6ff', lineWidth: 2, lineType: 1, title: 'balance', priceLineVisible: false, autoscaleInfoProvider: keepFloor });
    bal.setData(toPts(d.balance));
    var eq = null, df = null;
    if (d.equity.length) { eq = chart.addLineSeries({ color: '#3fb950', lineWidth: 2, title: 'equity', priceLineVisible: false }); eq.setData(toPts(d.equity)); }
    if (d.daily_floor.length) { df = chart.addLineSeries({ color: '#d29922', lineWidth: 1, lineStyle: 2, lineType: 1, title: 'daily floor', priceLineVisible: false, lastValueVisible: false }); df.setData(toPts(d.daily_floor)); }
    if (d.max_floor != null) bal.createPriceLine({ price: d.max_floor, color: '#f85149', lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: 'FTMO floor' });
    if (d.initial != null) bal.createPriceLine({ price: d.initial, color: '#8b949e', lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: 'start' });
    chart.timeScale().fitContent();
    var read = document.getElementById('eqread'), idle = read.textContent;
    chart.subscribeCrosshairMove(function (p) {
      if (!p || !p.time || !p.seriesData) { read.textContent = idle; return; }
      var t = new Date(p.time * 1000), parts = [t.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'];
      var b = p.seriesData.get(bal); if (b) parts.push('balance ' + fmt(b.value));
      if (eq) { var e = p.seriesData.get(eq); if (e) parts.push('equity ' + fmt(e.value)); }
      if (d.max_floor != null) { var v = (eq && p.seriesData.get(eq)) || b; if (v) parts.push('room ' + fmt(v.value - d.max_floor)); }
      read.textContent = parts.join(' · ');
    });
    document.getElementById('eqfit').onclick = function () { chart.timeScale().fitContent(); };
    if (window.ResizeObserver) new ResizeObserver(function () { chart.applyOptions({ width: box.clientWidth }); }).observe(box);
  }).catch(function () { box.innerHTML = '<div class="k" style="padding:12px">could not load the equity series - reload to retry</div>'; });
})();
'''


def equity_page(q: dict) -> str:
    rng = q.get("range") if q.get("range") in RANGES else "30d"
    bal, eq, acct = _equity_series(RANGES[rng])
    hb = next((h for h in (C.heartbeat(CFG, s) for s in C.symbols(CFG)) if h and (h.get("ftmo") or {}).get("initial_balance")), None) or {}
    f = hb.get("ftmo") or {}
    fmax, init = f.get("max_floor"), f.get("initial_balance")
    cur = float(acct.get("equity") or hb.get("equity") or 0)
    series = [v for _, v in bal] + [e for _, e, _, _ in eq]
    start = (bal[0][1] if bal else (eq[0][1] if eq else cur))
    peak, dd, run = start, 0.0, start
    for v in [v for _, v in sorted([(t, v) for t, v in bal] + [(t, e) for t, e, _, _ in eq])]:
        run = v; peak = max(peak, v); dd = max(dd, peak - v)
    chg = cur - start
    sel = " ".join(f'<a href="/equity?range={k}" class="pill{" take" if k == rng else ""}">{k}</a>' for k in RANGES)
    tiles = (f'<div class="grid2">'
             f'<div class="card"><div class="k">equity now</div><div class="big">{cur:,.2f}</div>'
             f'<div class="k {"ok" if chg >= 0 else "bad"}">{chg:+,.2f} ({(chg / start * 100) if start else 0:+.2f}%) over {rng}</div></div>'
             f'<div class="card"><div class="k">room to the max-loss floor</div><div class="big {"bad" if fmax and cur - fmax < 500 else ""}">'
             f'{(cur - fmax) if fmax else 0:,.2f}</div><div class="k">floor {fmax:,.0f}</div></div>' if fmax else
             f'<div class="grid2"><div class="card"><div class="k">equity now</div><div class="big">{cur:,.2f}</div></div>')
    tiles += (f'<div class="card"><div class="k">peak in range</div><div class="big">{peak:,.2f}</div></div>'
              f'<div class="card"><div class="k">largest drop from a peak</div><div class="big bad">{dd:,.2f}</div>'
              f'<div class="k">{(dd / peak * 100) if peak else 0:.2f}% of the peak</div></div></div>')
    legend = ('<div class="row k" style="margin:6px 0"><span style="color:#58a6ff">━ balance (closed trades)</span>'
              '<span style="color:#3fb950">━ equity (sampled every 5 min)</span>'
              '<span style="color:#d29922">┅ daily floor</span><span style="color:#f85149">┅ FTMO max-loss floor</span></div>')
    note = ('<div class="k">Balance comes from the broker\'s own deal history, so it covers the account from the start, '
            'including the hand-traded period. Equity is sampled from today onward.</div>')
    chart = (f'<div class="row" style="margin:4px 0"><button class="btn" id="eqfit" type="button" style="padding:6px 12px;font-size:14px">Fit all</button>'
             f'<span class="k" id="eqread">drag to scroll · wheel or pinch to zoom · tap or hover for values</span></div>'
             f'<div id="eqchart" data-range="{E(rng)}" style="height:420px;border:1px solid #30363d;border-radius:8px;overflow:hidden"></div>'
             f'<noscript>{_svg_chart(bal, eq, fmax, init)}</noscript>'
             f'<script src="/static/lw.js?v={STATIC_V}"></script><script>{EQUITY_JS}</script>')
    return page("Equity", f'<h1>Equity</h1><div class="row">{sel}</div>{tiles}{legend}{chart}{note}', "equity")


def journal_page(q: dict) -> str:
    sym = q.get("symbol") or None; rows = C.journal_rows(CFG, sym)
    dec = q.get("decision") or ""
    if dec: rows = [r for r in rows if r.get("decision") == dec]
    rows = list(reversed(rows))[:200]
    opts = "".join(f'<option value="{E(x)}" {"selected" if x == dec else ""}>{E(x or "all decisions")}</option>' for x in ["", "approved", "approved_pending", "skipped", "rejected"])
    out = [f'<h1>Journal</h1><form method="get" class="row"><select name="decision" style="width:auto">{opts}</select><input name="symbol" value="{E(sym or "")}" placeholder="symbol" style="width:160px"><button class="btn">Filter</button></form><div class="card"><table><tr><th>#</th><th>time</th><th>signal</th><th>decision</th><th>R</th><th>exit</th></tr>']
    for r in rows:
        key = f'{r.get("symbol")}-{r.get("signal_id")}'
        rtxt = r.get("r_multiple") or ""; skip = f' ({r.get("skip_reason")}{" auto" if r.get("auto") == "1" else ""}{" superseded" if r.get("skip_reason") == "9" else (" FTMO auto-reject" if r.get("skip_reason") == "10" else "")})' if r.get("decision") == "skipped" else ""
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
            sz = form.get("size", "staged")                          # coach item 22: Staged (default) / Full now
            if sz in ("staged", "full"): params["size"] = sz
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
        what = "enter at market" if verb == "fill_now" else "cancel pending order"
        if not a: return f"/signal/{key}", False, f"{what}: task written, no ack within 12 s - check again shortly"
        return f"/signal/{key}", a.get("result") == "accepted", f"{what}: {a.get('result')} - {C.reason_text(a.get('reason'))}"
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
            if parts[0] == "api" and len(parts) == 3 and parts[1] in ("position", "signal"):
                if not C.safe_key(parts[2]): return self._send("bad request", "text/plain", 400)
                return self._send(json.dumps(position_live(parts[2]) if parts[1] == "position" else signal_live(parts[2])), "application/json")
            if parts[0] == "api" and len(parts) == 2 and parts[1] == "equity":
                return self._send(json.dumps(equity_json(q.get("range", "30d")), separators=(",", ":")), "application/json")
            if parts[0] == "api" and len(parts) == 3 and parts[1] == "advisor":
                if not C.safe_key(parts[2]): return self._send("bad request", "text/plain", 400)
                h, v = advisor_section(parts[2]); return self._send(json.dumps({"html": h, "v": v}), "application/json")
            if parts[0] == "export" and len(parts) == 2 and re.fullmatch(r"\d{6}\.zip", parts[1]):
                from live import month_export
                data, name = month_export.build_zip(CFG, parts[1][:6], LOG)
                C.append_line(os.path.join(CFG["root"], "web", "web_audit.log"), f"{C.now_iso()}|export|{name}|{len(data)}B|by=web")
                self.send_response(200); self.send_header("Content-Type", "application/zip"); self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if parts[0] == "backup.zip":
                from live import backup
                data, name, man = backup.build(CFG, 30); backup.record(CFG, man, name)
                C.append_line(os.path.join(CFG["root"], "web", "web_audit.log"), f"{C.now_iso()}|backup|{name}|{len(data)}B|{man['files']} files|by=web")
                self.send_response(200); self.send_header("Content-Type", "application/zip"); self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if parts[0] in ("shadow_bank.csv", "staged_entry.csv", "inverse_shadow.csv", "manual_sizing.csv"):   # coach items 8, 18, 21, 22
                try:
                    with open(os.path.join(CFG["root"], "web", parts[0]), "rb") as f: data = f.read()
                except OSError: data = b"not built yet - the monitor writes it hourly\n"
                self.send_response(200); self.send_header("Content-Type", "text/csv"); self.send_header("Content-Disposition", f'attachment; filename="{parts[0]}"')
                self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if parts[0] == "health": return self._send(json.dumps({"ok": True, "ts": C.now_iso()}), "application/json")
            if parts[0] == "signal" and len(parts) == 2: return self._send(signal_page(parts[1], q))
            if parts[0] == "position" and len(parts) == 2: return self._send(position_page(parts[1], q))
            if parts[0] == "context": return self._send(context_page(q))
            if parts[0] == "journal": return self._send(journal_page(q))
            if parts[0] == "equity": return self._send(equity_page(q))
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
