r"""
calendar_refresh.py - keep the EA's econ_events.csv fresh from ForexFactory's official rolling feed (coach ruling
2026-09-16: same source, made live). Runs inside the monitor daily and can be run by hand.

Facts that shape it: FF publishes ONLY the current Sun..Sat week (ff_calendar_thisweek.xml; there is no next-week
file), rate-limits repeat pulls, and gives no `actual`/event id in JSON - the XML carries GMT times and a stable
series id in <url>. So each pull is a SNAPSHOT that is accumulated into a forward store keyed by (series_id, utc
date); a later snapshot replaces an earlier one (release times get revised). The deployed file is then a FULL REBUILD:
    history (data/econ/ff_combined.csv, dated before the store's first day) + forward store
    -> pipeline/normalize_econ_tzfix.py (classes from config/event_classes.yaml, political_events.csv merged)
    -> pipeline/test_calendar_coverage.py must pass (history survived) + sanity checks on the fresh rows
    -> atomic replace of <Common>\Files\econ_events.csv. Every EA instance reloads it once a day after 03:00 UTC.
Layout on the box (deploy_live.sh --calendar): <calendar_dir>\pipeline\*.py, \config\*, \history\ff_combined.csv,
\snapshots\, \forward.json, \build\.
"""
from __future__ import annotations
import csv, json, os, re, subprocess, sys, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
HDR8 = ["DateTime", "Currency", "Impact", "Event", "Actual", "Forecast", "Previous", "Detail"]

def fetch_feed(url: str, log) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "hybrid-live/1 (calendar refresh; contact via repo)"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30, context=C._ssl_ctx()) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            log(f"calendar: fetch attempt {attempt+1} failed: {e}")
            time.sleep(20 * (attempt + 1))
    return None

def parse_feed(xml_text: str) -> list[dict]:
    """XML rows -> {id, utc: datetime|None(all-day), date: 'YYYY-MM-DD', ccy, title, impact, forecast, previous}."""
    out = []
    for e in ET.fromstring(xml_text).findall("event"):
        g = lambda k: (e.findtext(k) or "").strip()
        m = re.search(r"/calendar/(\d+)-", g("url")); sid = m.group(1) if m else g("title")
        d = datetime.strptime(g("date"), "%m-%d-%Y").date()
        t = g("time"); utc = None
        mt = re.match(r"^(\d{1,2}):(\d{2})(am|pm)$", t.lower())
        if mt:
            hh = int(mt.group(1)) % 12 + (12 if mt.group(3) == "pm" else 0)
            utc = datetime(d.year, d.month, d.day, hh, int(mt.group(2)), tzinfo=timezone.utc)
        out.append({"id": sid, "utc": utc, "date": d.isoformat(), "ccy": g("country"), "title": g("title"), "impact": g("impact"),
                    "forecast": g("forecast"), "previous": g("previous")})
    return out

def to_row8(r: dict) -> list[str]:
    # all-day / tentative rows get the 00:00 sentinel the normalizer turns into a sane slot on that UTC date
    dt = (r["utc"] or datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return [dt, r["ccy"], r["impact"], r["title"].replace(",", " "), "", r["forecast"], r["previous"], ""]

class Refresher:
    def __init__(self, cfg: dict, log):
        self.cfg = cfg; self.log = log; cal = cfg.get("calendar") or {}
        self.dir = cal.get("dir") or os.path.join(cfg["root"], "calendar")
        self.feed = cal.get("feed_url") or FEED
        self.python = cal.get("python") or sys.executable
        self.target = os.path.join(cfg["common_files"], "econ_events.csv")
        for sub in ("snapshots", "build"): os.makedirs(os.path.join(self.dir, sub), exist_ok=True)

    def run(self) -> dict:
        res = {"ok": False, "ts": C.now_iso()}
        xml_text = fetch_feed(self.feed, self.log)
        if not xml_text: res["error"] = "feed unavailable"; return res
        rows = parse_feed(xml_text)
        if len(rows) < 20: res["error"] = f"feed too small ({len(rows)} rows)"; return res
        stamp = C.now_utc().strftime("%Y%m%dT%H%M%S")
        with open(os.path.join(self.dir, "snapshots", f"ff_{stamp}.xml"), "w", encoding="utf-8") as f: f.write(xml_text)
        # forward store: (id,date) -> row; later snapshot wins
        fp = os.path.join(self.dir, "forward.json"); store = C.load_json(fp, {}) or {}
        for r in rows:
            store[f"{r['id']}@{r['date']}"] = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}
        # keep the store bounded: drop rows older than 120 days (history covers the past)
        cutoff = (C.now_utc() - timedelta(days=120)).date().isoformat()
        store = {k: v for k, v in store.items() if v["date"] >= cutoff}
        C.atomic_write_json(fp, store)
        fwd = sorted(store.values(), key=lambda v: (v["date"], v.get("utc") or ""))
        first_fwd = min(v["date"] for v in fwd)
        # combined input: history strictly before the store's first day + the store
        hist = os.path.join(self.dir, "history", "ff_combined.csv"); src = os.path.join(self.dir, "build", "ff_input.csv"); kept = 0
        with open(hist, encoding="utf-8", errors="replace", newline="") as fi, open(src, "w", encoding="utf-8", newline="") as fo:
            rd = csv.reader(fi); w = csv.writer(fo); hdr = next(rd); w.writerow(hdr)
            for row in rd:
                if row and row[0][:10] < first_fwd: w.writerow(row); kept += 1
            for v in fwd:
                vv = dict(v); vv["utc"] = datetime.fromisoformat(vv["utc"]) if vv.get("utc") else None
                w.writerow(to_row8(vv))
        out = os.path.join(self.dir, "build", "econ_events.csv"); pipe = os.path.join(self.dir, "pipeline")
        p = subprocess.run([self.python, "normalize_econ_tzfix.py", src, out], cwd=pipe, capture_output=True, text=True, timeout=600)
        if p.returncode != 0: res["error"] = "normalize failed: " + (p.stderr or p.stdout)[-400:]; return res
        res["normalize"] = (p.stdout or "").strip().splitlines()[-1:] 
        t = subprocess.run([self.python, "test_calendar_coverage.py", out], cwd=pipe, capture_output=True, text=True, timeout=600)
        res["coverage_test_rc"] = t.returncode
        if t.returncode != 0: res["error"] = "coverage test FAILED: " + (t.stdout or t.stderr)[-600:]; return res
        # sanity on the fresh rows: last date, class counts, at least one V in the forward window
        cls = {}; last = ""; n = 0; fwd_v = 0
        with open(out, encoding="utf-8", errors="replace", newline="") as f:
            for r in csv.DictReader(f):
                n += 1; cls[r.get("class", "")] = cls.get(r.get("class", ""), 0) + 1; last = max(last, r["datetime_utc"])
                if r["datetime_utc"][:10].replace(".", "-") >= first_fwd and r.get("class") == "V": fwd_v += 1
        res.update({"rows": n, "history_rows_kept": kept, "forward_rows": len(fwd), "classes": cls, "last_utc": last, "forward_V": fwd_v, "feed_rows": len(rows), "feed_first": rows[0]["date"], "feed_last": rows[-1]["date"]})
        if n < kept + len(fwd) * 0.5: res["error"] = "row count collapsed"; return res
        if last.replace(".", "-")[:10] < C.now_utc().date().isoformat(): res["error"] = f"coverage end {last} is in the past"; return res
        # deploy atomically next to the EA
        tmp = self.target + ".tmp"
        with open(out, "rb") as fi, open(tmp, "wb") as fo: fo.write(fi.read())
        os.replace(tmp, self.target); res["ok"] = True; res["deployed"] = self.target
        return res

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); a = ap.parse_args()
    cfg = C.load_config(a.config); log = C.Log("calendar", cfg["logs_dir"])
    r = Refresher(cfg, log).run(); log(json.dumps(r)); print(json.dumps(r, indent=1)); sys.exit(0 if r["ok"] else 1)

if __name__ == "__main__": main()
