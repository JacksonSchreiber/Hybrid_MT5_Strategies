#!/usr/bin/env python3
"""inbox_bridge.py — auto-deliver blind setup screenshots to the advisor inbox.

When a signal fires in a VISUAL tester run (or live), the EA screenshots the H4
chart WITH overlays to MQL5\\Files\\journal\\shots\\<sym>_<stamp>_<id>.png
(engineer item C; needs InpShotOnDecision=true, and InpBlindLabels=true so the
on-chart label carries no date). This bridge turns each raw shot into a blind,
ready-to-judge bundle in the advisor's inbox — no pasting, no manual capture:

  MQL5\\Files\\journal\\shots\\<sym>_<stamp>_<id>.png   (raw H4 + overlays)
        │  + Common\\Files\\journal\\<sym>_<stamp>*.csv  (the numbers, by id)
        │  + data/qdm_csv/<BASE>-M1*.csv                 (for the D1 render)
        ▼
  training/advisor/inbox/
        h4.png     blind-cropped H4 (title bar + time-axis bands removed; overlays kept)
        d1.png     blind D1 candlestick rendered from bars ≤ signal time (lookahead-safe)
        setup.md   blind numbers: strategy/direction, R-distances, session/day, Tier-0 calendar
  training/advisor/inbox/_archive/<stamp>_<id>/  same three, kept per signal (L0/L1 dataset)

Modes:
  --backfill   process every existing shot (build the dataset from a finished replay).
               USE THIS FOR TESTER RUNS: the Strategy Tester commits sandbox file
               writes only when the test thread ends, so shots appear all at once
               after you close the tester — --watch sees nothing mid-run.
  --watch      poll the shots dir; process new shots as they appear (LIVE trading,
               where MQL5\\Files writes are immediate — not the tester).
  (default)    process only the newest shot → inbox

Why the D1 is rendered, not screenshotted: MT5 can't open a 2nd chart inside the
Strategy Tester (ChartOpen is disabled there), and the EA runs on H4. The render
is blind by construction (no symbol, no axis dates). Live trading can instead
capture the real D1 chart. If the symbol has no M1 data, the D1 is skipped and
setup.md says so — the H4 + numbers still deliver.

stdlib + Pillow, reusing pipeline.tier0 and pipeline.export_d1_stats.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipeline import tier0                       # noqa: E402
from pipeline import export_d1_stats as d1s       # noqa: E402

# --- paths (terminal-specific; override with flags if the terminal id differs) --
TERMINAL = Path("/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal")
TERMINAL_ID = "EE0304F13905552AE0B5EAEFB04866EB"
TESTER_ROOT = TERMINAL.parent / "Tester"          # sibling of Terminal/
JOURNAL_DIR = TERMINAL / "Common" / "Files" / "journal"   # FILE_COMMON → shared, live


def _default_shots_dir() -> Path:
    """Where ChartScreenShot actually writes.

    In the STRATEGY TESTER, MQL5\\Files is sandboxed under the tester *agent*
    (MetaQuotes\\Tester\\<id>\\Agent-<ip>-<port>\\MQL5\\Files), NOT the terminal —
    and the port suffix isn't stable across machines, so glob Agent-* and take the
    newest. Fall back to the terminal path for LIVE trading (no agent sandbox).
    NOTE: the tester commits sandbox file writes when the test thread finishes, so
    PNGs appear only after the run ends — use --backfill after closing the tester,
    not --watch (which is for live trading)."""
    shots = sorted(TESTER_ROOT.glob(f"{TERMINAL_ID}/Agent-*/MQL5/Files/journal/shots"),
                   key=lambda p: p.stat().st_mtime)
    if shots:
        return shots[-1]
    agents = sorted(TESTER_ROOT.glob(f"{TERMINAL_ID}/Agent-*"),
                    key=lambda p: p.stat().st_mtime)
    if agents:
        return agents[-1] / "MQL5" / "Files" / "journal" / "shots"
    return TERMINAL / TERMINAL_ID / "MQL5" / "Files" / "journal" / "shots"


SHOTS_DIR = _default_shots_dir()
INBOX = Path("/mnt/c/Users/jacks/OneDrive/Trading/hybrid_project/training/advisor/inbox")
ARCHIVE = INBOX / "_archive"

# blind-crop bands (pixels) for the standard 1600x900 decision shot: the top band
# covers MT5's top-left "SYMBOL,PERIOD" label; the bottom band covers the date axis.
CROP_TOP = 30
CROP_BOTTOM = 28

D1_RENDER_BARS = 130          # ~6 months of D1 context


# --------------------------------------------------------------------------- #
def parse_shot_name(png: Path):
    """<sym>_<stamp>_<id>.png → (symbol, stamp, id). Symbol may contain a dot
    (EURUSD.dk) but no underscore; stamp is YYYYMMDD; id is an int."""
    stem = png.stem                              # sym_stamp_id
    try:
        sym, stamp, sid = stem.rsplit("_", 2)
        return sym, stamp, int(sid)
    except ValueError:
        return None


def find_journal_row(symbol: str, stamp: str, sig_id: int):
    """Latest journal CSV for this run (final or .part), row with signal_id==id."""
    cands = sorted(JOURNAL_DIR.glob(f"{symbol}_{stamp}*.csv"),
                   key=lambda p: (".part" in p.name, -p.stat().st_mtime))
    for path in cands:
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("signal_id") or "").strip() == str(sig_id):
                        return r
        except OSError:
            continue
    return None


def _f(row, *keys):
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    return None


def _session(hour: int) -> str:
    if 7 <= hour < 13:
        return "London"
    if 13 <= hour < 20:
        return "New York"
    if 20 <= hour or hour < 2:
        return "overnight"
    return "Asia"


def blind_setup_md(row) -> tuple[str, datetime | None]:
    """Build the blind setup.md (no symbol, no date) + return the signal datetime
    (used for the D1 render). Numbers come from the journal row."""
    strat = (row.get("strategy") or "?").strip()
    direction = 1 if (row.get("direction") or "").strip().upper() == "BUY" else -1
    dstr = "BUY" if direction > 0 else "SELL"
    entry = _f(row, "orig_entry", "entry")
    sl = _f(row, "orig_sl", "sl")
    tp1 = _f(row, "orig_tp1", "tp1", "orig_tp", "tp")
    tp2 = _f(row, "orig_tp2", "tp2")
    risk = abs(entry - sl) if (entry is not None and sl is not None and entry != sl) else None

    def rmult(x):
        if x is None or risk in (None, 0):
            return "?"
        return f"{abs(x - entry) / risk:.1f}R"

    st = (row.get("signal_time") or "").strip()
    sig_dt = None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            sig_dt = datetime.strptime(st, fmt)
            break
        except ValueError:
            pass
    dow = sig_dt.strftime("%A") if sig_dt else "?"
    sess = _session(sig_dt.hour) if sig_dt else "?"

    # blind calendar via Tier 0 (no names/dates)
    cal = {"high_impact_ahead": None, "hours_until": None, "affects": "?",
           "recent_event_bias": "?"}
    if sig_dt:
        sig = tier0.Signal(symbol=(row.get("symbol") or "").strip(),
                           direction=direction, entry_time=sig_dt, strategy=strat)
        try:
            cal = tier0.blind_calendar(sig, tier0._load_events(), horizon_h=12.0)
        except Exception:
            pass

    lines = [
        f"# BLIND SETUP — {strat} {dstr}",
        "_No symbol, no date. For the blind advisor. Judge from the charts + the library only._",
        "",
        f"- **Session / day-of-week:** {sess} / {dow}",
        f"- **Proposed levels (chart-visible prices):** entry {entry}, SL {sl}, "
        f"TP1 {tp1}, TP2 {tp2}",
        f"- **Risk geometry:** SL {rmult(sl)} · TP1 {rmult(tp1)} · TP2 {rmult(tp2)} "
        "(detector already sized to 1% and cleared the R:R floor)",
        f"- **Calendar (blind):** high_impact_ahead={cal['high_impact_ahead']}, "
        f"hours_until={cal['hours_until']}, affects={cal['affects']}, "
        f"recent_event_bias={cal['recent_event_bias']}",
        "",
        "Images: `d1.png` (daily context) · `h4.png` (H4 setup, with overlays).",
    ]
    return "\n".join(lines) + "\n", sig_dt


# --- image work --------------------------------------------------------------
def crop_blind(src: Path, dst: Path, top=CROP_TOP, bottom=CROP_BOTTOM):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    w, h = im.size
    im.crop((0, top, w, max(top + 1, h - bottom))).save(dst, "PNG")


def render_d1(symbol: str, asof: datetime, dst: Path, d1_csv: Path | None = None) -> bool:
    """Blind D1 candlestick truncated at asof. Returns False if no data.

    Prefers `d1_csv` — the EA-dumped daily series written at signal-fire, which
    exists for EVERY symbol and whose final (partial) bar ends AT the signal (no
    post-decision price leak). Falls back to aggregating the on-disk M1 CSV (only
    EURUSD survives on disk; used for offline backfill). Both share the same
    columns, so the per-day aggregator is a no-op on the already-daily EA file."""
    from PIL import Image, ImageDraw
    src = d1_csv if (d1_csv and d1_csv.exists()) else d1s._find_m1(symbol)
    if not src:
        return False
    bars = d1s._aggregate_d1(src, asof)
    if len(bars) < 20:
        return False
    seg = bars[-D1_RENDER_BARS:]
    closes = [b[4] for b in seg]
    ema = d1s._ema(closes, 20)
    hi = max(b[2] for b in seg)
    lo = min(b[3] for b in seg)
    rng = (hi - lo) or 1e-9
    W, H, pad = 900, 460, 24
    im = Image.new("RGB", (W, H), (13, 17, 23))
    d = ImageDraw.Draw(im)
    n = len(seg)
    cw = max(2, (W - 2 * pad) // n - 2)
    def yv(p):
        return int(pad + (hi - p) / rng * (H - 2 * pad))
    for i, b in enumerate(seg):
        _dt, o, h, l, c, _v = b
        x = pad + i * ((W - 2 * pad) // n) + cw // 2
        col = (63, 185, 80) if c >= o else (248, 81, 73)
        d.line([(x, yv(h)), (x, yv(l))], fill=col)
        top_y, bot_y = yv(max(o, c)), yv(min(o, c))
        d.rectangle([x - cw // 2, top_y, x + cw // 2, max(top_y + 1, bot_y)],
                    fill=col if c >= o else (13, 17, 23), outline=col)
    # EMA20 (amber) for regime context
    pts = [(pad + i * ((W - 2 * pad) // n) + cw // 2, yv(ema[i])) for i in range(n)]
    if len(pts) > 1:
        d.line(pts, fill=(210, 153, 34), width=2)
    im.save(dst, "PNG")
    return True


# --- per-shot processing -----------------------------------------------------
def process(png: Path, *, to_inbox: bool, verbose=True) -> bool:
    parsed = parse_shot_name(png)
    if not parsed:
        return False
    symbol, stamp, sig_id = parsed
    row = find_journal_row(symbol, stamp, sig_id)
    if not row:
        if verbose:
            print(f"  {png.name}: no journal row for id {sig_id} — skipped")
        return False

    md, sig_dt = blind_setup_md(row)
    arch = ARCHIVE / f"{stamp}_{sig_id}"
    arch.mkdir(parents=True, exist_ok=True)
    crop_blind(png, arch / "h4.png")
    d1_ok = render_d1(symbol, sig_dt, arch / "d1.png") if sig_dt else False
    if not d1_ok:
        md += "\n_(D1 render unavailable for this symbol — use the H4 + numbers.)_\n"
    (arch / "setup.md").write_text(md, encoding="utf-8")

    if to_inbox:
        INBOX.mkdir(parents=True, exist_ok=True)
        crop_blind(png, INBOX / "h4.png")
        if d1_ok:
            render_d1(symbol, sig_dt, INBOX / "d1.png")
        (INBOX / "setup.md").write_text(md, encoding="utf-8")
    if verbose:
        print(f"  {png.name} → {arch.name}/  (d1={'yes' if d1_ok else 'no'}"
              f"{', → inbox' if to_inbox else ''})")
    return True


def newest_shot():
    shots = sorted(SHOTS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
    return shots[-1] if shots else None


def main():
    global SHOTS_DIR
    ap = argparse.ArgumentParser(description="Deliver blind setup shots to the advisor inbox.")
    ap.add_argument("--backfill", action="store_true", help="process all existing shots")
    ap.add_argument("--watch", action="store_true", help="poll for new shots and process them")
    ap.add_argument("--interval", type=float, default=3.0, help="--watch poll seconds")
    ap.add_argument("--shots-dir", default=str(SHOTS_DIR))
    a = ap.parse_args()

    SHOTS_DIR = Path(a.shots_dir)
    if not SHOTS_DIR.exists():
        if a.watch:
            # The EA only FolderCreate's journal\shots on the FIRST screenshot, so
            # when the bridge is booted at launch the dir isn't there yet. In watch
            # mode, create it and wait for shots rather than dying on the race.
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        else:
            sys.exit(f"shots dir not found: {SHOTS_DIR}")

    if a.backfill:
        pngs = sorted(SHOTS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
        print(f"backfill: {len(pngs)} shots")
        last = None
        for p in pngs:
            if process(p, to_inbox=False):
                last = p
        if last:                                   # inbox = the most recent one
            process(last, to_inbox=True)
        print(f"done. archive: {ARCHIVE}  inbox: {INBOX}")
    elif a.watch:
        print(f"watching {SHOTS_DIR} (every {a.interval}s) — Ctrl-C to stop")
        seen = {p.name for p in SHOTS_DIR.glob("*.png")}
        while True:
            try:
                time.sleep(a.interval)
                for p in sorted(SHOTS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime):
                    if p.name not in seen:
                        seen.add(p.name)
                        process(p, to_inbox=True)
            except KeyboardInterrupt:
                print("\nstopped."); break
    else:
        p = newest_shot()
        if not p:
            sys.exit(f"no shots in {SHOTS_DIR} — run a visual test with "
                     "InpShotOnDecision=true (and InpBlindLabels=true).")
        process(p, to_inbox=True)
        print(f"inbox updated: {INBOX}")


if __name__ == "__main__":
    main()
