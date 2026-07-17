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

The one thing that DOES abort the whole run is free disk space dropping
below FREE_DISK_MIN_GB, checked before every symbol (downloads and exports
both consume disk, so this is checked continuously, not just at startup).

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

FREE_DISK_MIN_GB = 50

ADD_TIMEOUT_SEC = 120  # symbol add/list is a metadata op, should be fast
STEP_TIMEOUT_SEC = 6 * 3600  # generous ceiling for update/export (EURUSD's
# full-history download took ~25 min; this leaves large headroom for CDN
# hiccups without letting a hung invocation block the whole fleet forever)


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def free_gb(path):
    return shutil.disk_usage(path).free / (1024**3)


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
    return True


def main():
    os.makedirs(FLEET_LOG_DIR, exist_ok=True)
    os.makedirs(MT5_READY_DIR, exist_ok=True)

    symbols = load_symbols()
    done = load_done()
    log(f"Fleet download starting. {len(symbols)} symbols in config, "
        f"{len(done)} already marked done in {STATE_FILE}.")

    existing_symbols = get_existing_symbols()
    log(f"QDM currently has {len(existing_symbols)} symbol(s) registered: "
        f"{sorted(existing_symbols)}")

    processed, succeeded, failed = 0, 0, 0
    for ftmo_name, duk in symbols:
        if ftmo_name in done:
            log(f"{ftmo_name}: already in state file, skipping")
            continue

        free = free_gb(REPO_DIR)
        if free < FREE_DISK_MIN_GB:
            log(f"ABORTING FLEET RUN: free disk {free:.1f} GB < "
                f"{FREE_DISK_MIN_GB} GB threshold. Symbols not yet in "
                f"{STATE_FILE} will be picked up on the next run.")
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
