"""
charts.py - server-side H4 / D1 chart renderer from the bars inside a signal JSON (W2). Pure Pillow.

Same palette as the tester's HybridTriEMA overlay and pipeline/inbox_bridge.render_d1:
  EMA20 gold, EMA50 deep-sky-blue, EMA200 violet; up (63,185,80), down (248,81,73); entry green, SL red, TP blue.
H4 additionally draws the overlay geometry the EA exports (zone, zone2, leg, aux levels, swing points) and marks
the signal bar. Charts are sighted (symbol + date in the title) - live has no future to leak (A2).
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont

BG = (13, 17, 23); GRID = (30, 36, 46); TXT = (201, 209, 217); DIM = (110, 118, 129)
UP = (63, 185, 80); DN = (248, 81, 73); BLUE = (88, 166, 255)
EMA_COL = {20: (255, 215, 0), 50: (0, 191, 255), 200: (238, 130, 238)}
ZONE = (255, 215, 0, 56); ZONE2 = (0, 191, 255, 48); LEG = (238, 130, 238); SWING = (201, 209, 217)
H4_BARS = 90; D1_BARS = 130

def _font(size: int):
    for cand in ("DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try: return ImageFont.truetype(cand, size)
        except OSError: continue
    return ImageFont.load_default()

def ema(vals: list[float], n: int) -> list[float]:
    if not vals: return []
    k = 2.0 / (n + 1); out = [vals[0]]
    for v in vals[1:]: out.append(out[-1] + k * (v - out[-1]))
    return out

def _price_range(bars, extra: list[float]) -> tuple[float, float]:
    lo = min(b[3] for b in bars); hi = max(b[2] for b in bars)
    for p in extra:
        if p and p > 0: lo = min(lo, p); hi = max(hi, p)
    pad = (hi - lo) * 0.06 or 1e-6
    return lo - pad, hi + pad

def render(bars: list[list], *, title: str, levels: dict | None, overlay: dict | None = None, n_show: int = H4_BARS,
           signal_t: int | None = None, size=(1000, 520), digits: int = 5, marks: list[tuple[int, str]] | None = None,
           strategy: str = "", imbal: bool = False) -> Image.Image:
    """bars: [[t_epoch,o,h,l,c,v]] oldest->newest (all of them; EMAs are computed on the full series)."""
    W, H = size; pad_l, pad_r, pad_t, pad_b = 12, 92, 30, 28
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img, "RGBA")
    f_small, f_title = _font(12), _font(14)
    if not bars or len(bars) < 5:
        d.text((pad_l, pad_t), title + "  (no bars)", fill=TXT, font=f_title); return img
    closes = [b[4] for b in bars]
    emas = {n: ema(closes, n)[-n_show:] for n in (20, 50, 200)}
    show = bars[-n_show:]; n = len(show); t_index = {b[0]: i for i, b in enumerate(show)}
    lv = levels or {}
    extra = [lv.get(k) for k in ("entry", "sl", "tp1", "tp2", "tp")]
    if overlay:
        for z in ("zone", "zone2"):
            zz = overlay.get(z) or {}
            if zz.get("hi", 0) > 0: extra += [zz["hi"], zz["lo"]]
    lo, hi = _price_range(show, [e for e in extra if e])
    x0, x1, y0, y1 = pad_l, W - pad_r, pad_t, H - pad_b
    step = (x1 - x0) / n; bw = max(2, int(step * 0.62))
    def X(i): return x0 + step * (i + 0.5)
    def Y(p): return y1 - (p - lo) / (hi - lo) * (y1 - y0)
    # grid + right axis
    for k in range(6):
        p = lo + (hi - lo) * k / 5; y = Y(p)
        d.line([(x0, y), (x1, y)], fill=GRID)
        d.text((x1 + 6, y - 7), f"{p:.{digits}f}", fill=DIM, font=f_small)
    # time axis labels (every ~n/6 bars)
    every = max(1, n // 6)
    for i in range(0, n, every):
        t = datetime.fromtimestamp(show[i][0], timezone.utc)
        d.line([(X(i), y1), (X(i), y1 + 4)], fill=DIM)
        d.text((X(i) - 20, y1 + 6), t.strftime("%d %b" if n_show > 100 else "%d %b %H:%M"), fill=DIM, font=f_small)
    # overlay zones (behind candles): zone / zone2 span zone.from..zone.to exactly as the tester rectangles
    z_from = z_to = None
    if overlay:
        zz = overlay.get("zone") or {}
        z_from = t_index.get(_epoch(zz.get("from"))); z_to = t_index.get(_epoch(zz.get("to")))
        for z, col in (("zone", ZONE), ("zone2", ZONE2)):
            zz = overlay.get(z) or {}
            if zz.get("hi", 0) > 0 and zz.get("lo", 0) > 0:
                xa = X(z_from) if z_from is not None else x0; xb = X(z_to) if z_to is not None else x1
                d.rectangle([xa - bw, Y(zz["hi"]), xb + bw, Y(zz["lo"])], fill=col, outline=(col[0], col[1], col[2], 160))
    # imbalances (FVG / gap / volume spike) from the bars, as DrawImbalances: purple boxes to the last bar + midline
    if imbal:
        from live import overlays as OV
        for zb in OV.imbalances(bars if n_show >= len(bars) else bars, 120, 2.0, 15):
            i0 = t_index.get(zb["t0"])
            if i0 is None: continue
            col = (147, 112, 219, 40 if zb["state"] == 0 else 22)
            d.rectangle([X(i0) - bw // 2, Y(zb["hi"]), x1, Y(zb["lo"])], fill=col, outline=(147, 112, 219, 110))
            mid = (zb["hi"] + zb["lo"]) / 2; _dashed(d, X(i0), x1, Y(mid), (147, 112, 219, 140), 3, 5)
        for sw in OV.swings(bars, 14):
            i = t_index.get(sw["t"])
            if i is None: continue
            if sw["kind"] == "hi": d.text((X(i) - 4, Y(sw["p"]) - 15), "v", fill=SWING, font=f_small)
            else: d.text((X(i) - 4, Y(sw["p"]) + 2), "^", fill=SWING, font=f_small)
    # candles
    for i, b in enumerate(show):
        t, o, h, l, c = b[0], b[1], b[2], b[3], b[4]
        col = UP if c >= o else DN; x = X(i)
        d.line([(x, Y(h)), (x, Y(l))], fill=col, width=1)
        top, bot = Y(max(o, c)), Y(min(o, c))
        if bot - top < 1: bot = top + 1
        if c >= o: d.rectangle([x - bw // 2, top, x + bw // 2, bot], fill=col)
        else: d.rectangle([x - bw // 2, top, x + bw // 2, bot], fill=BG, outline=col)
    # EMAs
    for nper, series in emas.items():
        pts = [(X(i), Y(v)) for i, v in enumerate(series[-n:])]
        if len(pts) > 1: d.line(pts, fill=EMA_COL[nper], width=2 if nper == 200 else 1)
    # overlay: leg, aux levels, swings
    leg = (overlay or {}).get("leg") or {}
    if overlay:
        if leg.get("p0", 0) > 0 and leg.get("p1", 0) > 0:
            i0 = t_index.get(_epoch(leg.get("t0"))); i1 = t_index.get(_epoch(leg.get("t1")))
            if i0 is not None and i1 is not None: d.line([(X(i0), Y(leg["p0"])), (X(i1), Y(leg["p1"]))], fill=LEG, width=2)
        lx = X(z_to) + 6 if z_to is not None else x0 + 4
        for a in overlay.get("aux") or []:
            if a.get("price", 0) > 0:
                _dashed(d, x0, x1, Y(a["price"]), (192, 192, 192), 2, 4); d.text((lx, Y(a["price"]) - 13), a.get("label", ""), fill=(192, 192, 192), font=f_small)
        if strategy == "DeepFib" and leg.get("p0", 0) > 0 and leg.get("p1", 0) > 0:
            from live import overlays as OV
            for price, lab, gp in OV.fib_levels(leg):
                col = (255, 215, 0) if gp else (150, 150, 150)
                _dashed(d, x0, x1, Y(price), col, 4 if gp else 2, 4); d.text((x1 - 70, Y(price) - 13), lab, fill=col, font=f_small)
        for key, mark in (("swings_hi", "v"), ("swings_lo", "^")):
            for s in overlay.get(key) or []:
                i = t_index.get(_epoch(s.get("t")))
                if i is not None: d.text((X(i) - 4, Y(s["p"]) - (14 if mark == "v" else 2)), mark, fill=(255, 200, 120), font=f_small)
    # levels
    for key, col, lab in (("entry", UP, "entry"), ("sl", DN, "SL"), ("tp1", BLUE, "TP1"), ("tp2", BLUE, "TP2")):
        p = lv.get(key)
        if p and p > 0:
            _dashed(d, x0, x1, Y(p), col); d.text((x1 - 34, Y(p) - 14), lab, fill=col, font=f_small)
    if signal_t is not None and signal_t in t_index:
        x = X(t_index[signal_t]); d.line([(x, y0), (x, y1)], fill=(255, 255, 255, 60)); d.text((x + 3, y0 + 2), "signal", fill=TXT, font=f_small)
    for t, label in marks or []:
        if t in t_index: x = X(t_index[t]); d.line([(x, y0), (x, y1)], fill=(255, 140, 0, 120)); d.text((x + 3, y0 + 14), label, fill=(255, 140, 0), font=f_small)
    d.text((pad_l, 8), title, fill=TXT, font=f_title)
    lx = W - pad_r - 250
    for nper, col in EMA_COL.items():
        d.text((lx, 8), f"EMA{nper}", fill=col, font=f_small); lx += 64
    return img

def _epoch(iso) -> int | None:
    if not iso: return None
    try: return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError: return None

def _dashed(d, xa, xb, y, col, on=6, off=6):
    x = xa
    while x < xb:
        d.line([(x, y), (min(x + on, xb), y)], fill=col); x += on + off

def signal_bars(sig: dict, tf: str) -> list[list]:
    """bars for a signal: the terminal feed when available (live box), else whatever the EA embedded."""
    try:
        from live import mt5feed
        b = mt5feed.bars(sig["symbol"], tf, 500)
        if b: return b
    except Exception: pass
    return sig.get(f"bars_{tf}") or []

def render_signal(sig: dict, out_dir: str, fresh: bool = False) -> tuple[str, str]:
    """writes <out_dir>/<key>-h4.png and -d1.png (idempotent per delay_count unless fresh). returns the two paths."""
    key = sig["signal_key"]; stamp = f"{key}-d{sig.get('delay_count', 0)}"
    os.makedirs(out_dir, exist_ok=True)
    p_h4 = os.path.join(out_dir, f"{stamp}-h4.png"); p_d1 = os.path.join(out_dir, f"{stamp}-d1.png")
    if not fresh and os.path.exists(p_h4) and os.path.exists(p_d1): return p_h4, p_d1
    digits = _digits(sig)
    head = f"{sig['symbol']}  {sig['strategy']} {sig['direction']}  #{sig['signal_id']}  {sig.get('sigtime_text', '')}"
    sig_t = _epoch(sig.get("signal_time"))
    lv = sig.get("levels") or {}
    render(signal_bars(sig, "h4"), title="H4  " + head, levels=lv, overlay=sig.get("overlay"), n_show=H4_BARS, signal_t=sig_t, digits=digits, strategy=sig.get("strategy", ""), imbal=True).save(p_h4 + ".tmp", "PNG"); os.replace(p_h4 + ".tmp", p_h4)
    render(signal_bars(sig, "d1"), title="D1  " + head + f"   regime {(sig.get('regime') or {}).get('pretty', '')}", levels=lv, overlay=None, n_show=D1_BARS, digits=digits).save(p_d1 + ".tmp", "PNG"); os.replace(p_d1 + ".tmp", p_d1)
    return p_h4, p_d1

def _digits(sig: dict) -> int:
    e = (sig.get("levels") or {}).get("entry") or 1.0
    s = f"{e}"; return max(2, min(5, len(s.split(".")[1]) if "." in s else 2))
