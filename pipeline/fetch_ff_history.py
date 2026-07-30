#!/usr/bin/env python3
"""Fetch ForexFactory historical economic calendar (2020-01 .. 2026-07) into
data/econ/ff_history.csv. Uses curl_cffi chrome impersonation to bypass
Cloudflare, parses the embedded window.calendarComponentStates JSON.

Resumable: each month's parsed events are cached to data/econ/_cache/.
Re-run to continue after an interruption. Final CSV is (re)built from cache
every run, so once all months are cached the CSV is complete.
"""
import csv, glob, json, os, random, sys, time
from datetime import datetime, timezone

from curl_cffi import requests as creq

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(BASE, "data", "econ", "ff_history.csv")
CACHE = os.path.join(BASE, "data", "econ", "_cache")
LOG = os.path.join(BASE, "logs", "ff_fetch.log")

START = datetime(2020, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=timezone.utc)
IMPACT_MAP = {"high": "High", "medium": "Medium", "low": "Low"}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def extract_days(text):
    i = text.find("window.calendarComponentStates[1] = {")
    if i < 0:
        raise ValueError("no calendarComponentStates marker")
    start = text.find("days: [", i)
    if start < 0:
        raise ValueError("no days array")
    start = text.find("[", start)
    depth = 0
    end = None
    for idx in range(start, len(text)):
        c = text[idx]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end is None:
        raise ValueError("unbalanced days array")
    return json.loads(text[start:end])


def fetch_month(mon, year, retries=4):
    url = f"https://www.forexfactory.com/calendar?month={mon}.{year}"
    for attempt in range(1, retries + 1):
        try:
            r = creq.get(url, impersonate="chrome", timeout=40)
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            if "Just a moment" in r.text:
                raise ValueError("cloudflare challenge")
            return extract_days(r.text)
        except Exception as e:
            wait = min(45, 4 * attempt * attempt) + random.uniform(0, 3)
            log(f"  ! {mon}.{year} attempt {attempt}/{retries} failed: {e}; sleep {wait:.1f}s")
            time.sleep(wait)
    return None


def month_targets():
    targets = []
    for year in range(2020, 2027):
        for mi, mon in enumerate(MONTHS, 1):
            if year == 2026 and mi > 7:
                continue
            targets.append((mon, year, mi))
    return targets


def build_csv():
    rows = {}
    for fp in glob.glob(os.path.join(CACHE, "*.json")):
        days = json.load(open(fp))
        for d in days:
            for e in d.get("events", []):
                imp = IMPACT_MAP.get(e.get("impactName", ""))
                if imp is None:
                    continue
                dl = e.get("dateline")
                if not dl:
                    continue
                dt = datetime.fromtimestamp(int(dl), tz=timezone.utc)
                if dt < START or dt > END:
                    continue
                rows[e.get("id")] = {
                    "datetime_utc": dt.isoformat(),
                    "currency": e.get("currency", ""),
                    "impact": imp,
                    "event": e.get("name", ""),
                    "actual": e.get("actual", ""),
                    "forecast": e.get("forecast", ""),
                    "previous": e.get("previous", ""),
                }
    ordered = sorted(rows.values(), key=lambda r: (r["datetime_utc"], r["currency"], r["event"]))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["datetime_utc", "currency", "impact", "event", "actual", "forecast", "previous"])
        w.writeheader()
        w.writerows(ordered)
    return len(ordered)


def main():
    os.makedirs(CACHE, exist_ok=True)
    targets = month_targets()
    todo = [(m, y) for (m, y, mi) in targets if not os.path.exists(os.path.join(CACHE, f"{y}-{mi:02d}.json"))]
    log(f"RUN: {len(targets)} months total, {len(todo)} to fetch (rest cached)")
    failures = []
    for n, (mon, year) in enumerate(todo, 1):
        mi = MONTHS.index(mon) + 1
        days = fetch_month(mon, year)
        if days is None:
            failures.append(f"{mon}.{year}")
            log(f"[{n}/{len(todo)}] {mon}.{year} FAILED")
            continue
        json.dump(days, open(os.path.join(CACHE, f"{year}-{mi:02d}.json"), "w"))
        log(f"[{n}/{len(todo)}] {mon}.{year} cached: {len(days)} days")
        time.sleep(2.5 + random.uniform(0, 1.5))
    total = build_csv()
    cached = len(glob.glob(os.path.join(CACHE, "*.json")))
    log(f"BUILD: {cached}/{len(targets)} months cached -> {total} rows in CSV")
    if failures:
        log(f"FAILURES this run ({len(failures)}): {', '.join(failures)}")
    if cached == len(targets):
        log("COMPLETE: all months cached")
    else:
        log(f"INCOMPLETE: {len(targets)-cached} months still missing; re-run to resume")


if __name__ == "__main__":
    main()
