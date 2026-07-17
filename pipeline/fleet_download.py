#!/usr/bin/env python3
"""
Fleet download pipeline: fetch + export the 49 FTMO symbols from Dukascopy
via the QDM CLI, fully unattended.

Per symbol, strictly serialized (one qdmcli JVM invocation at a time):
    1. -symbol action=add symbols=<dukascopy_name> datasource=dukascopy datatype=TICK
       (skipped if the symbol is already registered in QDM)
    2. -data action=update symbols=<dukascopy_name>          (full-history download)
    3. -data action=exportToMT5 symbol=<dukascopy_name> timeframe=TICK
           datefrom=2020.01.01 dateto=2026.07.16
           outputdir=data/mt5_ready filename=<ftmo_name>

Deliberately one invocation per step (not `-run file=` batch mode): a prior
pilot found that chaining two `action=export` commands in one batch silently
drops the second export's output with no error, which is unacceptable for an
unattended run. The ~10-15s JVM-start tax per invocation is an acceptable
trade for reliability here.

After each export: verify the output CSV exists, is non-trivially sized, and
its last line's date is >= 2026-07-01. Only then is the symbol appended to
the state file (resumable: symbols already in the state file are skipped on
the next run). Any failure at any step is logged to the failures file and
the fleet moves on to the next symbol - it never aborts the whole run for a
single symbol's failure.

DISK SAFETY (2026-07-17 revision): the host is WSL2 - its ext4 filesystem
lives inside a .vhdx virtual disk file on the Windows C: drive. That vhdx
grows but NEVER shrinks: deleting files inside WSL frees space *within* the
vhdx for future writes, but does NOT return space to the real Windows C:
drive. `df /` inside WSL therefore reports space against the vhdx's current
allocation, which is fiction relative to the real constraint - the actual
guard checks `df` on `/mnt/c` (the real Windows host free space) instead.

To keep the real disk (both the vhdx's growth and, indirectly, the true
C: constraint) under control, once a symbol's exported CSV is validated,
its QDM internal full-history store (/home/jack/QDM/user/data/History/<name>)
is deleted - we only ever export once (2020+), so that internal store is
dead weight afterward. CSVs in data/mt5_ready are NEVER deleted by this
script - a separate MT5-side auto-importer consumes and deletes them.

The internal-store deletion gate is full `validate_export`, not just
"does the file exist": a truncated/partial CSV (e.g. from a crash
mid-export) must never trigger deletion of the only remaining good copy
of the data, since regenerating it means a full multi-hour re-download.

If real Windows C: free space drops below DISK_GUARD_MIN_GB, the fleet
pauses (not aborts) and polls periodically, giving the parallel CSV
auto-importer time to drain data/mt5_ready and free real disk space, up to
DISK_GUARD_MAX_WAIT_SEC before giving up and exiting cleanly for the run
to be resumed later.

Usage:
    cd /home/jack/QDM && nohup python3 /home/jack/hybrid_project/pipeline/fleet_download.py \
        > /home/jack/hybrid_project/logs/fleet_download.log 2>&1 &

(The script itself sets its own cwd to QDM_DIR before invoking qdmcli, so the
`cd` above is just for operator convenience / matches how qdmcli is normally
run from its own directory.)
"""

import csv
import datetime
import io
import os
import re
import shutil
import subprocess
import sys
import time

import yaml

# --- Paths & constants -------------------------------------------------

REPO_DIR = "/home/jack/hybrid_project"
QDM_DIR = "/home/jack/QDM"
QDMCLI = os.path.join(QDM_DIR, "qdmcli")

SYMBOLS_YAML = os.path.join(REPO_DIR, "config/symbols.yaml")
STATE_FILE = os.path.join(REPO_DIR, "pipeline/fleet_state.txt")
FAILURES_FILE = os.path.join(REPO_DIR, "pipeline/fleet_failures.txt")
MT5_READY_DIR = os.path.join(REPO_DIR, "data/mt5_ready")
FLEET_LOG_DIR = os.path.join(REPO_DIR, "logs/fleet")

DATEFROM = "2020.01.01"
DATETO = "2026.07.16"
MIN_LAST_DATE = datetime.date(2026, 7, 1)  # last line's date must be >= this
MIN_FILE_BYTES = 100_000  # "non-trivially sized" floor (real files are GB-scale)
MIN_ROWS = 500  # sanity floor on row count

HISTORY_DIR = os.path.join(QDM_DIR, "user", "data", "History")

# Real Windows host free space, NOT WSL's own `df /` (see module docstring -
# the vhdx never shrinks, so WSL's own filesystem free-space number is
# fiction relative to the actual constraint).
DISK_GUARD_MIN_GB = 60
DISK_GUARD_POLL_SEC = 10 * 60  # recheck every 10 min while paused
DISK_GUARD_MAX_WAIT_SEC = 6 * 3600  # give up after 6h of waiting and exit cleanly

ADD_TIMEOUT_SEC = 120  # symbol add/list is a metadata op, should be fast
STEP_TIMEOUT_SEC = 6 * 3600  # generous ceiling for update/export (EURUSD's
# full-history download took ~25 min; this leaves large headroom for CDN
# hiccups without letting a hung invocation block the whole fleet forever)


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def free_gb_windows_host():
    """Real free space on the Windows host's C: drive, via its WSL mount.
    This is the actual constraint (see module docstring) - WSL's own
    `df /` / shutil.disk_usage() reports space inside the vhdx's current
    allocation, which does NOT reflect real remaining disk on the host."""
    result = subprocess.run(
        ["df", "-BG", "--output=avail", "/mnt/c"],
        capture_output=True, text=True,
    )
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    # Expected output: ["Avail", "255G"] - take the last non-empty line,
    # strip the trailing "G" unit.
    val = lines[-1].rstrip("G")
    return float(val)


def wait_for_disk_or_stop():
    """Real Windows-host disk guard. Returns True if it's safe to proceed.
    If free space is below threshold, pauses (does not immediately abort)
    and polls periodically so the parallel MT5 auto-importer has a chance
    to drain data/mt5_ready and free real disk space - only gives up (and
    signals the caller to stop the run cleanly) after DISK_GUARD_MAX_WAIT_SEC
    of no improvement."""
    free = free_gb_windows_host()
    if free >= DISK_GUARD_MIN_GB:
        return True

    log(f"Windows host C: free space is {free:.1f} GB, below the "
        f"{DISK_GUARD_MIN_GB} GB guard threshold. Pausing (not aborting) "
        f"for up to {DISK_GUARD_MAX_WAIT_SEC // 3600}h to give the CSV "
        f"auto-importer a chance to drain data/mt5_ready and free real "
        f"disk space...")
    waited = 0
    while waited < DISK_GUARD_MAX_WAIT_SEC:
        time.sleep(DISK_GUARD_POLL_SEC)
        waited += DISK_GUARD_POLL_SEC
        free = free_gb_windows_host()
        log(f"  ... rechecked after {waited // 60} min paused: "
            f"C: free = {free:.1f} GB")
        if free >= DISK_GUARD_MIN_GB:
            log(f"C: free space recovered to {free:.1f} GB - resuming fleet.")
            return True

    log(f"C: free space still below {DISK_GUARD_MIN_GB} GB after "
        f"{DISK_GUARD_MAX_WAIT_SEC // 3600}h of waiting - stopping this "
        f"run cleanly. Symbols not yet in {STATE_FILE} will resume on the "
        f"next run.")
    return False


def internal_store_dir(duk):
    return os.path.join(HISTORY_DIR, duk)


def dir_size_bytes(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def cleanup_internal_store(ftmo_name, duk, csv_path=None):
    """Delete the QDM internal full-history store for `duk`, but ONLY if
    the symbol's exported CSV in mt5_ready passes FULL validation (exists,
    non-trivial size, last line >= MIN_LAST_DATE, row-count floor) - not
    just an existence check. A truncated/partial CSV (e.g. a crash mid-
    export) must never cause deletion of the only remaining good copy of
    the data: regenerating it means a full multi-hour re-download.

    This also correctly (and intentionally) refuses to delete a store
    whose CSV is simply missing altogether - e.g. because it was already
    consumed by the parallel MT5 auto-importer, or lost some other way -
    since we can't re-verify it was ever complete once it's gone. Returns
    bytes freed (0 if skipped)."""
    if csv_path is None:
        csv_path = os.path.join(MT5_READY_DIR, f"{ftmo_name}.csv")

    ok, result = validate_export(ftmo_name, csv_path)
    if not ok:
        log(f"{ftmo_name}: SKIPPING internal-store cleanup - CSV did not "
            f"pass validation ({result}). Internal store preserved at "
            f"{internal_store_dir(duk)}.")
        return 0

    store_dir = internal_store_dir(duk)
    if not os.path.isdir(store_dir):
        log(f"{ftmo_name}: internal store {store_dir} already absent, nothing to clean.")
        return 0

    size = dir_size_bytes(store_dir)
    shutil.rmtree(store_dir)
    log(f"{ftmo_name}: deleted internal store {store_dir} "
        f"({size / 1e9:.2f} GB freed *inside the vhdx* - this creates "
        f"write headroom for future CSV exports, it does NOT free real "
        f"Windows C: disk space).")
    return size


def startup_cleanup(done, symbols):
    """One-time pass at startup: for every symbol already marked done in
    the state file, delete its internal store if (and only if) its CSV in
    mt5_ready still passes full validation. See cleanup_internal_store for
    why a missing/invalid CSV must NOT trigger deletion."""
    total_freed = 0
    n_deleted = 0
    n_skipped = 0
    for ftmo_name, duk in symbols:
        if ftmo_name not in done:
            continue
        freed = cleanup_internal_store(ftmo_name, duk)
        if freed:
            n_deleted += 1
            total_freed += freed
        else:
            n_skipped += 1
    log(f"Startup internal-store cleanup complete: {n_deleted} store(s) "
        f"deleted, {n_skipped} skipped (already absent or CSV not valid), "
        f"{total_freed / 1e9:.2f} GB freed inside the vhdx (does NOT "
        f"change Windows C: free space - only future write headroom).")
    return total_freed


def load_symbols():
    with open(SYMBOLS_YAML) as f:
        data = yaml.safe_load(f)
    out = []
    for s in data["symbols"]:
        duk = s.get("dukascopy_name")
        if not duk:
            log(f"WARNING: {s.get('ftmo_name')} has no dukascopy_name in "
                f"symbols.yaml - skipping permanently (not a failure, not "
                f"retryable, just unmapped).")
            continue
        out.append((s["ftmo_name"], duk))
    return out


def load_done():
    done = set()
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                done.add(line.split(",")[0])
    return done


def append_state(name, rows, size_bytes, ts):
    with open(STATE_FILE, "a") as f:
        f.write(f"{name},{rows} rows ({size_bytes} bytes),{ts}\n")


def append_failure(name, reason):
    ts = datetime.datetime.now().isoformat()
    with open(FAILURES_FILE, "a") as f:
        f.write(f"{name},{ts},{reason}\n")


def run_qdm(args, logfile_path, timeout):
    """Run a single qdmcli invocation; full raw output goes to logfile_path
    (never to our own stdout - that log is heavy DEBUG noise per-invocation
    and would make the orchestrator log unreadable across 49 symbols).
    Returns the process return code, or -1 on timeout (process is killed)."""
    os.makedirs(os.path.dirname(logfile_path), exist_ok=True)
    with open(logfile_path, "w") as lf:
        try:
            proc = subprocess.run(
                [QDMCLI] + args,
                cwd=QDM_DIR,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            return proc.returncode
        except subprocess.TimeoutExpired:
            lf.write(f"\n[fleet_download.py] TIMEOUT after {timeout}s - process killed\n")
            return -1


def get_existing_symbols():
    """Query -symbol action=list once at startup so we don't re-issue
    action=add for a symbol that's already registered (e.g. resuming after
    a crash between add and export)."""
    logfile = os.path.join(FLEET_LOG_DIR, "_symbol_list_startup.log")
    rc = run_qdm(["-symbol", "action=list"], logfile, ADD_TIMEOUT_SEC)
    existing = set()
    if rc != 0:
        log(f"WARNING: -symbol action=list failed (rc={rc}); assuming no "
            f"symbols pre-registered. See {logfile}")
        return existing
    with open(logfile) as f:
        text = f.read()
    # Data rows are real CSV (the Timezone column can itself contain a
    # comma, e.g. `"(UTC) Coordinated Universal Time, DST: No"`, quoted per
    # RFC 4180) - naive str.split(",") mis-detects these rows. Use the csv
    # module and only look at lines starting from the header we recognize.
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Symbol,Instrument"))
    except StopIteration:
        log(f"WARNING: could not find symbol-list header in {logfile}; "
            f"assuming no symbols pre-registered.")
        return existing
    # Data rows run until the next non-CSV-looking status line (e.g. "Data listed.")
    data_lines = []
    for l in lines[start + 1:]:
        if not l.strip() or l.strip() in ("Data listed.",) or l.startswith("---"):
            break
        data_lines.append(l)
    for row in csv.reader(io.StringIO("\n".join(data_lines))):
        if row and row[0]:
            existing.add(row[0])
    return existing


def tail_last_line(path):
    """Read the last non-empty line of a (possibly multi-GB) file without
    scanning the whole thing, by seeking backward from the end."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        block = 4096
        data = b""
        pos = end
        while pos > 0 and data.count(b"\n") < 2:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            block *= 2
        lines = [l for l in data.splitlines() if l.strip()]
        if not lines:
            return None
        return lines[-1].decode(errors="replace")


def count_lines(path):
    result = subprocess.run(["wc", "-l", path], capture_output=True, text=True)
    try:
        return int(result.stdout.split()[0])
    except Exception:
        return -1


def validate_export(ftmo_name, out_csv):
    """Returns (ok: bool, rows_or_reason)."""
    if not os.path.exists(out_csv):
        return False, f"output file missing: {out_csv}"

    size = os.path.getsize(out_csv)
    if size < MIN_FILE_BYTES:
        return False, f"output file too small ({size} bytes < {MIN_FILE_BYTES})"

    last_line = tail_last_line(out_csv)
    if not last_line:
        return False, "could not read a non-empty last line"

    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", last_line)
    if not m:
        return False, f"last line has unexpected format: {last_line[:100]!r}"

    last_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if last_date < MIN_LAST_DATE:
        return False, f"last line date {last_date} is before {MIN_LAST_DATE}"

    rows = count_lines(out_csv)
    if rows < MIN_ROWS:
        return False, f"row count too low ({rows} < {MIN_ROWS})"

    return True, (rows, size)


def process_symbol(ftmo_name, duk, existing_symbols):
    log(f"=== {ftmo_name} (dukascopy={duk}): starting ===")

    # 1. add (skip if already registered)
    if duk not in existing_symbols:
        logfile = os.path.join(FLEET_LOG_DIR, f"{ftmo_name}_add.log")
        rc = run_qdm(
            ["-symbol", "action=add", f"symbols={duk}",
             "datasource=dukascopy", "datatype=TICK"],
            logfile, ADD_TIMEOUT_SEC,
        )
        if rc != 0:
            append_failure(ftmo_name, f"symbol add failed rc={rc}; see {logfile}")
            log(f"{ftmo_name}: symbol add FAILED (rc={rc}) - see {logfile}")
            return False
        existing_symbols.add(duk)
        log(f"{ftmo_name}: symbol registered")
    else:
        log(f"{ftmo_name}: already registered in QDM, skipping add")

    # 2. update (full-history download - datefrom/dateto are NOT honored
    #    here per the EURUSD pilot finding, so we don't pass them)
    logfile = os.path.join(FLEET_LOG_DIR, f"{ftmo_name}_update.log")
    t0 = time.time()
    rc = run_qdm(["-data", "action=update", f"symbols={duk}"], logfile, STEP_TIMEOUT_SEC)
    dt = time.time() - t0
    if rc != 0:
        append_failure(ftmo_name, f"update failed rc={rc} after {dt:.0f}s; see {logfile}")
        log(f"{ftmo_name}: download FAILED (rc={rc}) after {dt:.0f}s - see {logfile}")
        return False
    log(f"{ftmo_name}: download completed in {dt:.0f}s")

    # 3. exportToMT5, scoped to 2020+, filed under the FTMO base name
    out_csv = os.path.join(MT5_READY_DIR, f"{ftmo_name}.csv")
    logfile = os.path.join(FLEET_LOG_DIR, f"{ftmo_name}_export.log")
    t0 = time.time()
    rc = run_qdm(
        ["-data", "action=exportToMT5",
         f"symbol={duk}", "timeframe=TICK",
         f"datefrom={DATEFROM}", f"dateto={DATETO}",
         f"outputdir={MT5_READY_DIR}", f"filename={ftmo_name}"],
        logfile, STEP_TIMEOUT_SEC,
    )
    dt = time.time() - t0
    if rc != 0:
        append_failure(ftmo_name, f"exportToMT5 failed rc={rc} after {dt:.0f}s; see {logfile}")
        log(f"{ftmo_name}: export FAILED (rc={rc}) after {dt:.0f}s - see {logfile}")
        return False
    log(f"{ftmo_name}: export completed in {dt:.0f}s")

    # 4. validate
    ok, result = validate_export(ftmo_name, out_csv)
    if not ok:
        append_failure(ftmo_name, f"validation failed: {result}")
        log(f"{ftmo_name}: VALIDATION FAILED - {result}")
        return False

    rows, size = result
    ts = datetime.datetime.now().isoformat()
    append_state(ftmo_name, rows, size, ts)
    log(f"{ftmo_name}: SUCCESS - {rows} rows, {size} bytes -> {out_csv}")

    # 5. reclaim disk: the internal full-history store is dead weight now
    #    that we've exported the scoped CSV we actually need (we never
    #    re-download). Runs only after export+validation succeeded, and
    #    only between symbols (never mid-qdmcli), since this is sequential.
    cleanup_internal_store(ftmo_name, duk, out_csv)
    return True


def main():
    os.makedirs(FLEET_LOG_DIR, exist_ok=True)
    os.makedirs(MT5_READY_DIR, exist_ok=True)

    symbols = load_symbols()
    done = load_done()
    log(f"Fleet download starting. {len(symbols)} symbols in config, "
        f"{len(done)} already marked done in {STATE_FILE}.")

    startup_cleanup(done, symbols)

    existing_symbols = get_existing_symbols()
    log(f"QDM currently has {len(existing_symbols)} symbol(s) registered: "
        f"{sorted(existing_symbols)}")

    processed, succeeded, failed = 0, 0, 0
    for ftmo_name, duk in symbols:
        if ftmo_name in done:
            log(f"{ftmo_name}: already in state file, skipping")
            continue

        if not wait_for_disk_or_stop():
            sys.exit(0)

        processed += 1
        try:
            ok = process_symbol(ftmo_name, duk, existing_symbols)
        except Exception as e:
            append_failure(ftmo_name, f"unexpected exception: {e!r}")
            log(f"{ftmo_name}: unexpected EXCEPTION: {e!r} - continuing with next symbol")
            ok = False
        if ok:
            succeeded += 1
        else:
            failed += 1

    log(f"Fleet download run complete. Processed {processed} symbol(s) "
        f"this run: {succeeded} succeeded, {failed} failed. "
        f"({len(done) + succeeded}/{len(symbols)} total done overall.)")


if __name__ == "__main__":
    main()
