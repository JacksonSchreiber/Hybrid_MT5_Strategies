#!/usr/bin/env python3
"""
Backfill US30 (Dukascopy raw code USA30IDXUSD) directly from Dukascopy's
public tick datafeed, bypassing qdmcli entirely.

WHY: qdmcli reliably fails on USA30IDXUSD's most-recent-days "finishing
tail" - it gets stuck in a persistent NoHttpResponseException retry loop
for the last few days of history and never reaches a graceful exit (last
observed failure: rc=141 SIGPIPE), leaving the internal store at 0 records
every time. Two qdmcli attempts both died this way. Per project rule, an
in-flight qdmcli must NEVER be killed (that's how the data gets lost), so
this backfill runs entirely independently of qdmcli/port 5050 - it only
touches Dukascopy's raw datafeed directly (same technique already proven
in this session's Group-B feasibility probe, which pulled real
USA30IDXUSD ticks - bid ~52808 - straight from datafeed.dukascopy.com).

TWO PHASES:
  1. FETCH: hourly tick .bi5 files, HISTORY_START -> HISTORY_END (all 24
     hours per weekday - Dukascopy CFD/index instruments trade close to
     24x5, same as FX), cached to disk with a sqlite manifest for
     resume/retry safety (mirrors trading-backtest/src/data/download.py's
     own proven Manifest/Pacer/backoff pattern).
  2. DECODE: read all cached .bi5 files in chronological (day, hour) order,
     decode (20-byte big-endian records: ms-offset, ask-raw, bid-raw,
     ask-vol, bid-vol; scale 0.001 - same as the other 3 Group-B symbols),
     and stream-write data/mt5_ready/US30.csv in the exact format
     mt5_import.sh already consumes: `YYYY.MM.DD HH:MM:SS.mmm,bid,ask`, no
     header, ascending time. Streamed per-hour (not held fully in memory -
     the sibling indices had 186M-650M ticks each, too much to hold as one
     in-memory structure comfortably).

Resumable: safe to re-run after an interruption - the fetch phase skips
anything already in the manifest as ok/empty, and the decode phase always
rebuilds the CSV fresh from whatever is cached (fast: pure local I/O).

Usage:
  nohup /home/jack/trading-backtest/.venv/bin/python3 \
      /home/jack/hybrid_project/pipeline/backfill_us30.py \
      > /home/jack/hybrid_project/logs/backfill_us30.log 2>&1 &
"""
import asyncio
import datetime as dt
import lzma
import random
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, "/home/jack/trading-backtest")
import httpx
from src.data.bi5 import looks_like_error_page

SYMBOL = "USA30IDXUSD"
FTMO_BASE = "US30"

# USA30IDXUSD's real Dukascopy history starts 2011.09.19 (confirmed via
# direct-feed probe, matching the sibling indices USATECHIDXUSD/
# USA500IDXUSD) - but the user has scoped the project's effective
# multi-symbol backtest window to 2020+ (26 of 48 other symbols only have
# 2020+ data by choice, so the common window is bounded there regardless),
# so this backfill is intentionally truncated to 2020-01-01 rather than
# fetching the full 2011+ depth. Any 2011-2019 hour-files already cached
# from before this change are harmless leftovers - decode_phase filters
# strictly to [HISTORY_START, HISTORY_END] so they never reach the CSV.
HISTORY_START = dt.date(2020, 1, 1)
HISTORY_END = dt.date(2026, 7, 16)

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
RECORD_FMT = ">iiiff"
RECORD_SIZE = struct.calcsize(RECORD_FMT)
SCALE = 0.001

CACHE_DIR = Path("/home/jack/hybrid_project/data/us30_backfill_cache")
OUT_CSV = Path("/home/jack/hybrid_project/data/mt5_ready/US30.csv")
MANIFEST_DB = CACHE_DIR / "manifest.sqlite"

CONCURRENCY = 8
PACE_S = 0.25
MAX_TRIES = 15

# HARDENING (2026-07-18, after a real hang): Dukascopy sometimes accepts a
# TCP/TLS connection and then never sends a single byte back - a true
# "black hole" socket. The original code passed timeout=90.0 to client.get()
# (which httpx expands to connect=read=write=pool=90s), but that alone was
# observed to hang for 19-23+ minutes with 0% CPU and open sockets - past
# any 90s bound. Root cause not fully pinned down (possibly an edge case in
# httpx/httpcore's read-timeout accounting for a connection that never
# receives ANY bytes), so the fix is layered rather than trusting a single
# mechanism:
#   1. An explicit httpx.Timeout with per-phase bounds at the CLIENT level
#      (not just a per-call float) - the documented, correct way to
#      configure this.
#   2. A hard OUTER asyncio.wait_for() around every attempt. This is the
#      real guarantee: asyncio.wait_for's cancellation is enforced by the
#      event loop itself, not by httpx's internal timeout bookkeeping, so
#      it cannot be defeated by whatever caused the original hang.
REQUEST_TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=30.0)
OUTER_TIMEOUT_S = 45.0   # hard asyncio-level bound per attempt (belt-and-suspenders)
WATCHDOG_IDLE_S = 300    # log a loud warning if nothing completes for this long


def log(msg):
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def tick_url(day, hour):
    return f"{BASE_URL}/{SYMBOL}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"


def cache_path(day, hour):
    return CACHE_DIR / f"{day.year}" / f"{day.month:02d}" / f"{day.day:02d}_{hour:02d}.bi5"


class Manifest:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS files(day TEXT, hour INT, status TEXT, "
            "PRIMARY KEY(day, hour))"
        )

    def status_map(self):
        rows = self.con.execute("SELECT day, hour, status FROM files").fetchall()
        return {(d, h): s for d, h, s in rows}

    def record(self, day, hour, status):
        self.con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (day, hour, status))

    def commit(self):
        self.con.commit()


class Pacer:
    """Global minimum interval between request starts (server-friendly)."""

    def __init__(self, interval_s):
        self.interval = interval_s
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


def weekdays(start, end):
    d = start
    while d <= end:
        if d.weekday() != 5:  # skip Saturday only (Sunday carries the early open)
            yield d
        d += dt.timedelta(days=1)


def _backoff_with_jitter(attempt):
    # HYPOTHESIS for the 0-socket/0%-CPU freeze the supervisor now handles
    # externally (2026-07-18): this backoff was NOT jittered - if all
    # CONCURRENCY=8 semaphore-holders happen to fail around the same time
    # (plausible: they're dispatched from the same wave and share one
    # Pacer), they retry in near lockstep, all asyncio.sleep()-ing
    # simultaneously. During a sustained Dukascopy outage that can chain
    # across many retry rounds, producing exactly "no sockets in flight,
    # 0% CPU, no progress" for minutes at a time - not a deadlock, just
    # every worker legitimately asleep in backoff at once. Adding jitter
    # desynchronizes concurrent retries so that pattern can't persist.
    # (Not confirmed as THE root cause - offered as a plausible, low-risk
    # mitigation per the PM's ask to reduce restart churn, not a claim the
    # freeze is fully explained/fixed.)
    return min(2.0 * 1.5 ** attempt, 60) + random.uniform(0, 5)


async def fetch_one(client, sem, pacer, url, dest, max_tries=MAX_TRIES):
    async with sem:
        for attempt in range(max_tries):
            await pacer.wait()
            try:
                # Outer asyncio.wait_for is the real guarantee against a
                # hang (see HARDENING note above) - it forces this attempt
                # to end within OUTER_TIMEOUT_S no matter what httpx does
                # internally. A TimeoutError here just counts as one more
                # retry, same as any other transient failure.
                r = await asyncio.wait_for(
                    client.get(url, timeout=REQUEST_TIMEOUT), timeout=OUTER_TIMEOUT_S
                )
            except (httpx.TransportError, httpx.TimeoutException, asyncio.TimeoutError, TimeoutError):
                await asyncio.sleep(_backoff_with_jitter(attempt))
                continue
            if r.status_code == 404:
                return "empty", 0
            body = r.content
            if r.status_code != 200 or looks_like_error_page(body):
                await asyncio.sleep(_backoff_with_jitter(attempt))
                continue
            if not body:
                return "empty", 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.rename(dest)
            return "ok", len(body)
        return "failed", 0


async def fetch_phase():
    manifest = Manifest(MANIFEST_DB)
    have = manifest.status_map()
    jobs = []
    for day in weekdays(HISTORY_START, HISTORY_END):
        for hour in range(24):
            key = (day.isoformat(), hour)
            if have.get(key) in ("ok", "empty"):
                continue
            jobs.append((day, hour))

    log(f"Fetch phase: {len(jobs)} hour-slots to fetch ({len(have)} already cached).")
    if not jobs:
        log("Nothing to fetch - all cached already.")
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    pacer = Pacer(PACE_S)
    limits = httpx.Limits(max_connections=CONCURRENCY + 4)
    counts = {"ok": 0, "empty": 0, "failed": 0}
    completed = 0
    last_commit_at = 0
    last_activity = asyncio.get_event_loop().time()
    COMMIT_EVERY = 40    # commit the manifest often - bounds rework on interruption
    LOG_EVERY = 40        # fine-grained progress, not gated on a large batch's straggler
    t_start = asyncio.get_event_loop().time()

    async def run(day, hour):
        nonlocal completed, last_commit_at, last_activity
        dest = cache_path(day, hour)
        status, _nbytes = await fetch_one(client, sem, pacer, tick_url(day, hour), dest)
        manifest.record(day.isoformat(), hour, status)
        counts[status] += 1
        completed += 1
        last_activity = asyncio.get_event_loop().time()
        if completed - last_commit_at >= COMMIT_EVERY:
            manifest.commit()
            last_commit_at = completed
        if completed % LOG_EVERY == 0:
            elapsed = asyncio.get_event_loop().time() - t_start
            rate = completed / elapsed if elapsed > 0 else 0
            eta_s = (len(jobs) - completed) / rate if rate > 0 else -1
            log(f"  progress: {completed}/{len(jobs)} "
                f"(ok={counts['ok']} empty={counts['empty']} failed={counts['failed']}) "
                f"rate={rate:.1f}/s eta={eta_s/3600:.1f}h")

    async def watchdog():
        # Visibility net: with the hardened per-attempt timeout above, a
        # true hang shouldn't be possible anymore (asyncio.wait_for's
        # cancellation is enforced by the event loop, independent of
        # httpx). This just gives a loud, unmistakable log line if
        # something unexpected still stalls progress, rather than silence.
        while True:
            await asyncio.sleep(60)
            idle = asyncio.get_event_loop().time() - last_activity
            if idle >= WATCHDOG_IDLE_S:
                log(f"  WATCHDOG WARNING: no completed fetch in {idle:.0f}s "
                    f"(expected: the {OUTER_TIMEOUT_S}s per-attempt hard timeout "
                    f"should prevent this - if you see this repeating, something "
                    f"is still wrong).")

    async with httpx.AsyncClient(http2=False, limits=limits, timeout=REQUEST_TIMEOUT,
                                  headers={"User-Agent": "Mozilla/5.0"}) as client:
        wd = asyncio.create_task(watchdog())
        try:
            # bound overall in-flight tasks (not just HTTP concurrency) so we
            # don't schedule all N coroutines at once - process in reasonably
            # sized waves while still logging/committing far more often than
            # each wave.
            WAVE = 200
            for i in range(0, len(jobs), WAVE):
                chunk = jobs[i:i + WAVE]
                await asyncio.gather(*(run(d, h) for d, h in chunk))
        finally:
            wd.cancel()
    manifest.commit()
    log(f"Fetch phase complete: {counts}")


def decode_phase():
    log("Decode phase: streaming all cached .bi5 files in chronological order...")
    manifest = Manifest(MANIFEST_DB)
    have = manifest.status_map()
    # Filter strictly to [HISTORY_START, HISTORY_END]: the cache may contain
    # leftover 2011-2019 entries from before the 2020-01-01 truncation (left
    # in place deliberately, see HISTORY_START comment) - those must NOT
    # leak into the output CSV.
    all_ok = [
        (dt.date.fromisoformat(d), h) for (d, h), s in have.items() if s == "ok"
    ]
    n_out_of_range = sum(1 for day, _h in all_ok if not (HISTORY_START <= day <= HISTORY_END))
    ok_keys = sorted(
        (day, h) for day, h in all_ok if HISTORY_START <= day <= HISTORY_END
    )
    log(f"  {len(ok_keys)} hour-files with real data to decode "
        f"({n_out_of_range} cached out-of-range entries excluded).")

    n_ticks = 0
    n_skipped_files = 0
    last_ts_ms = None
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w") as out:
        for idx, (day, hour) in enumerate(ok_keys):
            path = cache_path(day, hour)
            if not path.exists():
                n_skipped_files += 1
                continue
            try:
                raw = lzma.decompress(path.read_bytes())
            except lzma.LZMAError:
                n_skipped_files += 1
                continue
            if len(raw) % RECORD_SIZE:
                n_skipped_files += 1
                continue
            n = len(raw) // RECORD_SIZE
            if n == 0:
                continue
            hour_start = dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc)
            hour_start_ms = int(hour_start.timestamp() * 1000)

            lines = []
            for i in range(n):
                ms, ask_raw, bid_raw, _av, _bv = struct.unpack_from(RECORD_FMT, raw, i * RECORD_SIZE)
                ts_ms = hour_start_ms + ms
                if last_ts_ms is not None and ts_ms < last_ts_ms:
                    continue  # drop any out-of-order straggler, keep strictly ascending
                last_ts_ms = ts_ms
                ts = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc)
                bid = bid_raw * SCALE
                ask = ask_raw * SCALE
                lines.append(
                    f"{ts.strftime('%Y.%m.%d %H:%M:%S')}.{ts.microsecond // 1000:03d},"
                    f"{bid:.3f},{ask:.3f}"
                )
            if lines:
                out.write("\n".join(lines) + "\n")
                n_ticks += len(lines)

            if idx % 2000 == 0 and idx > 0:
                log(f"  decode progress: {idx}/{len(ok_keys)} files, {n_ticks} ticks so far")

    log(f"Decode phase complete: {n_ticks} ticks written to {OUT_CSV} "
        f"({n_skipped_files} corrupt/missing cache files skipped).")
    return n_ticks


async def main():
    log(f"US30 backfill starting: {SYMBOL} -> {FTMO_BASE}.csv, "
        f"{HISTORY_START.isoformat()} -> {HISTORY_END.isoformat()}")
    await fetch_phase()
    n = decode_phase()
    log(f"US30 backfill DONE: {n} ticks written to {OUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
