# Tools Guide

Operational "how to run it" reference, written for a fresh session with zero
prior context. For contracts/signatures see [api-reference.md](api-reference.md);
for why things are shaped this way see [architecture.md](architecture.md).

Contents: [QDM CLI](#qdm-cli) · [fleet_download.py](#fleet_downloadpy) ·
[The import chain](#the-import-chain) · [progress.sh](#progresssh) ·
[Stall detection](#stall-detection) · [Watch Officer](#watch-officer) ·
[mt5_verify.sh](#mt5_verifysh) · [Manual import scripts](#manual-import-scripts-importticksmq5--verifyimportmq5) ·
[Compiling MQL5 headlessly](#compiling-mql5-headlessly) ·
[Resume the pipeline in a new session](#resume-the-pipeline-in-a-new-session)

---

## QDM CLI

Full reference: [qdm-cli.md](qdm-cli.md) (flag-by-flag, verified against our
build — read it before guessing a flag). Essentials:

- Binary: `/home/jack/QDM/qdmcli` — a Linux-native Java binary, **must be run
  from `/home/jack/QDM/`** (relative paths inside it assume this cwd).
- Build **125.2692**, QuantDataManagerPro, full license REDACTED.
- Each invocation is a full JVM start (~10–15s overhead). Startup prints
  heavy DEBUG logging — pipe through `grep -v DEBUG` when reading interactively.
- **Serialize invocations** — never run two `qdmcli` processes at once
  (project-wide constraint; the download pipeline enforces this by only ever
  running one at a time).

```sh
cd /home/jack/QDM
./qdmcli -symbol action=list
./qdmcli -symbol action=add symbols=EURUSD datasource=dukascopy datatype=TICK
./qdmcli -data action=update symbols=EURUSD              # full-history download; datefrom/dateto IGNORED here
./qdmcli -data action=exportToMT5 symbol=EURUSD timeframe=TICK \
  datefrom=2020.01.01 dateto=2026.07.16 \
  outputdir=/home/jack/hybrid_project/data/mt5_ready filename=EURUSD   # datefrom/dateto DO apply here
```

Two behaviors that will bite you if you don't know them going in (both
confirmed empirically in [pilot-eurusd.md](pilot-eurusd.md)):

1. **`-data action=update` ignores `datefrom`/`dateto`** — it always pulls
   full available history from Dukascopy. There is no server-side way to cap
   a download's range.
2. **`timeframe=` on `exportToMT5` is case-sensitive** (`TICK`/`M1` only —
   `Tick` errors) and **`datefrom`/`dateto` DO apply** there — always pass
   them explicitly, or you get the entire available history (can be 20+ GB
   for one major FX pair).
3. Don't queue two `-data action=export` commands in one `-run file=` batch
   — only the first one reliably produces output. Run exports one at a time,
   or verify the output file exists after every batch.

---

## fleet_download.py

**Purpose:** unattended, resumable downloader for all 49 FTMO symbols —
add → full-history download → scoped `exportToMT5` CSV → validate → delete
QDM's internal store. Source: `pipeline/fleet_download.py`.

**Config:** `config/symbols.yaml` (the 49 symbols, FTMO name → Dukascopy
name). Constants at the top of the script: `DATEFROM = "2003.01.01"` (full
available history — see the note below), `DATETO = "2026.07.16"`,
`DISK_GUARD_MIN_GB = 60`, `STEP_TIMEOUT_SEC = 6*3600`.

> **History depth note:** as of 2026-07-17 the script exports **full
> available history** (`DATEFROM=2003.01.01`) for every symbol it processes
> from that date forward. The 27 symbols that finished *before* this change
> were exported at **2020+** and already had their internal stores deleted
> — they are not retroactively re-exported. If you need pre-2020 data for an
> already-finished symbol, you must re-run `-data action=update` +
> `exportToMT5` for it manually (the internal store is gone, so this is a
> full re-download). Older docs (`README.md`, `docs/pilot-eurusd.md`,
> `docs/mt5-import.md`) describe the original 2020+-only policy — this is
> current, correct behavior superseding those.

**Run it:**

```sh
cd /home/jack/QDM && nohup python3 /home/jack/hybrid_project/pipeline/fleet_download.py \
    > /home/jack/hybrid_project/logs/fleet_download.log 2>&1 &
```

(The script sets its own cwd internally before invoking `qdmcli`; the `cd`
above is just for operator convenience.)

**State/log files** (all under `/home/jack/hybrid_project/`):

| File | Contents |
|---|---|
| `pipeline/fleet_state.txt` | Append-only: one line per successfully completed symbol (`<name>,<rows> rows (<bytes> bytes),<iso timestamp>`) |
| `pipeline/fleet_failures.txt` | Append-only: one line per failure (`<name>,<iso timestamp>,<reason>`) |
| `logs/fleet_download.log` | Orchestrator's own stdout/stderr — may go quiet after a restart if redirection wasn't re-established; **not** the source of truth for progress (see per-symbol logs below) |
| `logs/fleet/<SYM>_add.log`, `_update.log`, `_export.log` | Raw `qdmcli` output for each step of each symbol — this is where real progress lines live |

**Disk guard:** checks real Windows-host `C:` free space via `df -BG
--output=avail /mnt/c` (never WSL's own `df /` — see
[architecture.md](architecture.md#why-the-pipeline-is-shaped-this-way-the-wsl-disk-model)).
Below 60 GB it pauses and polls every 10 minutes, giving the rolling
importer time to drain `data/mt5_ready/`; gives up after 6 hours and exits
cleanly (fully resumable).

**Restart/resume behavior:** on start, it re-reads `fleet_state.txt` (done)
and `fleet_failures.txt` (previously failed) and **skips both** — a symbol
already done is never re-processed; a symbol that previously failed is
**not** retried automatically on a plain restart (this is deliberate — flaky
exotics don't get hammered every restart). To force-retry a specific failed
symbol, remove its line(s) from `pipeline/fleet_failures.txt` before
restarting. It also does a one-time `startup_cleanup()` pass that re-checks
every already-`done` symbol's CSV and deletes its internal store if it
hasn't been cleaned up yet (safe/idempotent).

**Gotchas:**
- It is safe to `kill` and restart at any time — nothing is transactional
  across steps, but a symbol only gets appended to `fleet_state.txt` after
  its export is fully validated, so a half-finished symbol on kill just
  redoes from scratch on restart (add is skipped if already registered in
  QDM; update/export are cheap to re-run relative to losing data).
- A symbol that failed once and later succeeded appears in **both**
  `fleet_failures.txt` and `fleet_state.txt` — that's normal, not a live
  problem (the Watch Officer brief explicitly calls this out).
- `US100`/`US500`/`US30`/`USOIL` have a known dotted-name export issue (see
  Watch Officer brief) — expected, not a bug to chase.

---

## The import chain

Three scripts, layered:

```
rolling_import.sh  (orchestrator: polls fleet_state.txt, drains the backlog)
   └─ mt5_import.sh <BASE> [--clean]   (per-symbol: stage → launch terminal → wait → verify)
         └─ stage_csv_for_import.sh <BASE>   (copy CSV into the MT5 sandbox)
```

### rolling_import.sh

**Purpose:** drains `data/mt5_ready/` CSVs into MT5 **as fleet_download.py
produces them**, running in parallel with it. Imports one symbol at a time
(never stages more than one big CSV concurrently — staging copies the file,
temporarily doubling its disk footprint). Source: `pipeline/rolling_import.sh`.

**Run it:**

```sh
nohup /home/jack/hybrid_project/pipeline/rolling_import.sh \
    > /home/jack/hybrid_project/logs/rolling_import.log 2>&1 &
```

**Loop logic:** for every symbol present in `pipeline/fleet_state.txt` but
absent from `pipeline/import_state.txt`: run `mt5_import.sh --clean <BASE>`,
then **independently re-verify** success by grepping the real
`import_status.txt` for a `<BASE>,OK,` line with a plausible tick count
(≥1000) and a plausible last-date (starts with `202`) — never trust
`mt5_import.sh`'s exit code alone. Only on that independent verification
does it delete the **source** CSV (`data/mt5_ready/<BASE>.csv` — distinct
from the *staged* copy that `mt5_import.sh --clean` already cleaned) and
append the symbol to `import_state.txt`. On any failure: logs to
`pipeline/import_failures.txt`, leaves the source CSV in place (so the next
poll pass retries it automatically), and continues.

**Termination condition:** keeps polling (60s idle sleep) until the backlog
is empty **and** `fleet_download.py` is no longer running (checked via
`pgrep -f fleet_download.py`) — i.e. it exits only once downloads are fully
done and every downloaded symbol has been imported.

**State/log files:** `pipeline/import_state.txt` (done), `pipeline/
import_failures.txt` (failures), `logs/rolling_import.log` (orchestrator
log), `logs/rolling_import/<SYM>.log` (per-symbol `mt5_import.sh` output).

**Disk guard:** same `/mnt/c` real-disk check as `fleet_download.py`, 50 GB
threshold, 10-minute poll — belt-and-suspenders, since this script is a net
disk *reclaimer* (it deletes a multi-GB CSV per success), so tripping it
should be rare.

**Resume:** safe to kill/restart any time — it recomputes the backlog from
the state files on every loop iteration, so nothing is lost. An in-flight
`mt5_import.sh` run that gets interrupted (e.g. by killing this script) may
leave `terminal64.exe` open; check for that before restarting the importer
(see [resume runbook](#resume-the-pipeline-in-a-new-session)).

### mt5_import.sh

**Purpose:** the actual unattended-import driver — stages CSV(s), writes
`jobs.txt`, launches `terminal64.exe` headlessly via a `/config` startup
ini that auto-attaches `AutoImport.mq5`, waits for it to finish and
self-close, parses `import_status.txt`. Source: `pipeline/mt5_import.sh`.
Full contract of the files it writes/reads: [api-reference.md](api-reference.md#autoimport-contract).

```sh
./pipeline/mt5_import.sh AUDUSD                       # one symbol
./pipeline/mt5_import.sh AUDUSD NZDUSD USDCAD          # several, sequential, one terminal launch
./pipeline/mt5_import.sh --clean AUDUSD                # delete the STAGED CSV after success (not the source)
./pipeline/mt5_import.sh --host-symbol EURUSD AUDUSD   # override the chart-host symbol (default EURUSD.dk)
./pipeline/mt5_import.sh --timeout 1800 AUDUSD         # max wait seconds (default 2400)
./pipeline/mt5_import.sh --sessions EURUSD AUDUSD      # re-patch trading sessions ONLY, no re-import
```

Each plain `<SYMBOL>` (non `--sessions`) run requires
`data/mt5_ready/<SYMBOL>.csv` to already exist.

**Safety:** refuses to launch (exits non-zero) if `terminal64.exe` is
already running (`tasklist.exe` check) — never disrupts a terminal the user
may be using. On timeout it reports and **leaves the terminal for the
operator to close** (does not force-kill).

**Gotchas:**
- **Algo Trading must be enabled** in the terminal (Ctrl+E, persists across
  restarts) — `AutoImport.mq5` doesn't trade, but the global toggle still
  gates whether *any* EA runs at all. If a run produces no
  `import_status.txt` and the terminal just sits open, this is almost
  certainly why.
- **Focus stealing** — launching `terminal64` opens its window and can take
  focus for the run's duration (~3–5 min per major-FX symbol).
- **Cold-start race** — the driver's wait loop starts after a fixed `sleep
  8`; on an unusually slow cold start it could momentarily misjudge state.
  Proven fine in practice (see `docs/auto-import.md`).

### stage_csv_for_import.sh

**Purpose:** low-level bridge between the WSL repo and the Windows MT5
sandbox. Source: `pipeline/stage_csv_for_import.sh`.

```sh
./pipeline/stage_csv_for_import.sh EURUSD          # copy data/mt5_ready/EURUSD.csv -> MQL5\Files\import\
./pipeline/stage_csv_for_import.sh --clean EURUSD  # remove the staged copy (not the source)
./pipeline/stage_csv_for_import.sh --sync-scripts  # copy .mq5/.mqh sources into MQL5\Scripts\Experts\Include\
./pipeline/stage_csv_for_import.sh --compile       # sync + compile everything via MetaEditor CLI
```

Checks free space on the MT5 data folder's drive before staging (`+10%`
headroom over the file size) and dies loudly rather than half-copying.

---

## progress.sh

**Purpose:** one-shot, human-readable snapshot of the whole pipeline's
state — what's downloading right now, what's importing right now, disk
free, and progress bars toward 49/49. Source: `pipeline/progress.sh`. Purely
read-only (greps log files and state files, checks `pgrep`) — safe to run
at any time, does not touch the pipeline.

```sh
./pipeline/progress.sh
```

Sample output shape:

```
FTMO pipeline  20:41:07   disk 223G free   downloads:running  imports:running
  IN MT5 (goal)  [###################.......] 71%   35/49
  DOWNLOADED     [###################.......] 71%   35/49
  now downloading: EURPLN   [####............] 40%  writing 2024.03.12   on-symbol 12m
  now importing  : USDMXN: import verified OK - ...  [MT5 OPEN (importing)]
```

**The `watch` one-liner** (not a separate script — just wrap it):

```sh
watch -n 30 ./pipeline/progress.sh
```

(`progress.sh` itself is a single snapshot; `watch -n <seconds>` re-runs it
on an interval — pick any interval that suits you, 15–60s is reasonable.)

---

## Stall detection

Two mechanisms exist in this repo; know which one is actually live.

### The live 2-minute stall-detector (current, in use)

Not a checked-in script — a **background shell loop**, typically launched
via the agent's Bash tool with `run_in_background: true` (or the `Monitor`
tool) alongside `fleet_download.py`. Polls every 30s; watches the active
`qdmcli action=update` process's progress line in
`logs/fleet/<SYM>_update.log`. If the progress line hasn't changed in
**120 seconds**, it prints a line like:

```
STALL EURPLN — no progress 120s, on-symbol 12m, stuck at '2024.03.12' [Dukascopy retry loop] — PM decision needed
```

and then **does nothing further** — no kill, no restart. **The PM (the
orchestrating Claude session) decides remediate-or-skip** on each alert;
this is a deliberate policy choice (see `pipeline/watch_officer.md`, which
explicitly defers to this detector rather than duplicating stall detection
itself). This mechanism supersedes `stall_watchdog.sh` for *live* pipeline
runs — if you're starting a fresh pipeline run in a new session, prefer
launching a loop like this over relying on `stall_watchdog.sh`.

The core loop shape (reconstructed from the live process, since it is not a
checked-in file):

```sh
prev=""; prevchg=$(date +%s); alerted=""
while pgrep -f fleet_download.py >/dev/null 2>&1; do
  info=$(pgrep -af qdmcli | grep -v pgrep | grep 'action=update' | head -1)
  if [ -n "$info" ]; then
    pid=$(awk '{print $1}' <<<"$info")
    sym=$(grep -oE 'symbols=[^ ]+' <<<"$info" | cut -d= -f2)
    prog=$(grep -aE 'Writing |Downloading year:' "logs/fleet/${sym}_update.log" | tail -1)
    now=$(date +%s); key="$sym|$prog"
    if [ "$key" != "$prev" ]; then prev="$key"; prevchg=$now; [ "$alerted" = "$sym" ] && alerted=""; fi
    stalled=$(( now - prevchg ))
    if (( stalled >= 120 )) && [ "$alerted" != "$sym" ]; then
      echo "STALL $sym — no progress ${stalled}s ... — PM decision needed"
      alerted="$sym"
    fi
  fi
  sleep 30
done
```

### stall_watchdog.sh (older, coarser — superseded for live use)

**Purpose:** a self-contained, **auto-acting** kill-switch — no PM decision
involved. Source: `pipeline/stall_watchdog.sh`.

```sh
nohup /home/jack/hybrid_project/pipeline/stall_watchdog.sh \
    > /home/jack/hybrid_project/logs/stall_watchdog.log 2>&1 &
```

Watches the same progress lines, but with a **20-minute stall threshold**
and a **70-minute hard cap per symbol** — on either trip, it `kill -9`s the
`qdmcli` process directly. The fleet's own fail-and-continue logic then logs
the failure and moves to the next symbol. Runs only while `fleet_download.py`
is alive; exits when it isn't. Logs to `logs/stall_watchdog.log`.

**Why both exist:** `stall_watchdog.sh` was the original safety net (task
history: commit `3f8ca85`); the 2-minute PM-decides pattern was adopted
right after (`6f57fa0`, "Switch stall handling to 2-min flag-and-PM-decide")
because a 20-minute silent auto-kill was too slow/opaque for an
interactively-supervised run. `stall_watchdog.sh` is still in the repo and
still works as a coarse, fully-autonomous fallback (e.g. for a genuinely
unattended overnight run with nobody watching); the 2-minute loop is what
you should reach for whenever a PM/agent is actively supervising the run.

---

## Watch Officer

**Purpose:** a periodic, **read-only** health-check pass over the pipeline,
reporting to the PM. Brief: `pipeline/watch_officer.md` — read it in full
before acting as (or reasoning about) the Watch Officer; it is the
authoritative checklist (workers alive, disk, new failures, qdmcli
liveness, import health) and the list of known-benign conditions **not** to
escalate.

**Scheduling mechanism — gap, documented honestly:** there is **no unix
crontab** on this host (`crontab -l` → "no crontab for jack") and nothing
in the repo pins an interval or invocation command for the Watch Officer.
It is a Claude Code agent brief, not a cron job — in practice it is
launched periodically as a background/scheduled agent (e.g. via the `loop`
or `schedule` skill/tool) that reads `pipeline/watch_officer.md` and reports
back. **The exact scheduling config (interval, launch command) is not
captured anywhere in this repo** — if you need it, ask the user how the
current Watch Officer cadence was set up, or set up a fresh one with the
`schedule`/`loop` capability, pointing it at `pipeline/watch_officer.md`.

**Hard rules** (from the brief, restated because they matter): read-only,
diagnose-don't-fix, never run `qdmcli` or launch/kill MT5, never restart
anything. It escalates in a structured format (`OK — ...` one-liner if
healthy, or `ESCALATE` + issue/evidence/severity/suggested-fix if not) and
explicitly defers stall detection to the [2-minute detector](#stall-detection)
above — it should not re-alert on a normal slow download.

---

## mt5_verify.sh

**Purpose:** headless Strategy-Tester verification of
`HybridForwardTest.mq5` — runs a tester pass in `AA_ALL`/`AA_SKIP` mode (no
modal, no DLL) and summarizes the resulting journal. Source:
`pipeline/mt5_verify.sh`.

```sh
./pipeline/mt5_verify.sh --mode ALL  --strat EMA                    # exercise the two-target lifecycle for one strategy
./pipeline/mt5_verify.sh --mode SKIP --strat SMC,Fib,EMA             # per-strategy signal counts (no suppression)
./pipeline/mt5_verify.sh --mode SKIP --from 2022.01.01 --to 2024.12.31 --strat SMC
```

| Flag | Default | Meaning |
|---|---|---|
| `--mode` | `ALL` | `ALL` → `InpAutoApprove=AA_ALL` (`1`); `SKIP` → `AA_SKIP` (`2`) |
| `--strat` | `SMC,Fib,EMA` | Comma list; sets `InpUseSMC`/`InpUseFib`/`InpUseEMA` accordingly |
| `--symbol` | `EURUSD.dk` | Tester symbol |
| `--from` / `--to` | `2023.01.01` / `2024.12.31` | Tester date range |
| `--model` | `1` | `0`=every-tick, `1`=1-min OHLC (fast, default), `4`=real ticks (production-representative, slower) |
| `--timeout` | `1800` | Max seconds to wait |

Mechanics: writes an EA `.set` file to `MQL5\Profiles\Tester\hft_verify.set`
and a `[Tester]` startup ini (`ShutdownTerminal=1`), launches `terminal64
/config:...`, waits for it to exit, then locates the newly-created journal
under `<Terminal>\Common\Files\journal\` (diffing directory listings
before/after) and greps per-strategy row counts (`SweepMSS`/`DeepFib`/
`EMArev`) plus the total row count. Same "refuse if terminal64 already
running" safety as `mt5_import.sh`.

---

## Manual import scripts (ImportTicks.mq5 / VerifyImport.mq5)

These are the original, drag-onto-a-chart manual path — still the right
tool for a one-off symbol or for debugging (no headless terminal launch,
you watch the Experts/Journal log live). Full mechanism and the original
EURUSD smoke test: [mt5-import.md](mt5-import.md). Quick version:

1. Ensure the CSV is staged: `./pipeline/stage_csv_for_import.sh <BASE>`.
2. In MT5, open **Navigator (Ctrl+N) → Scripts**, drag **ImportTicks** onto
   any chart. Set `SymbolBase=<BASE>` (defaults for everything else are
   fine — `CustomSuffix=.dk`, `BatchSize=1000000`, `DeleteIfExists=true`,
   `BuildM1Bars=true`).
3. Watch **Toolbox (Ctrl+T) → Experts** for progress; expect `... N ticks
   added (batch B)...` every ~10M ticks, then `=== IMPORT COMPLETE ===`.
4. Drag **VerifyImport** onto any chart, set `TargetSymbol=<BASE>.dk`. Read
   the Journal: `bars: M1=... H4=... D1=...`, first/last M1 bar time, and
   `tick count (sum of M1 tick_volume)` should match the CSV's row count.
5. `./pipeline/stage_csv_for_import.sh --clean <BASE>` to reclaim the staged
   copy once verified.

Both scripts `#include <Hybrid\TickImport.mqh>` — the same engine
`AutoImport.mq5` uses (see [api-reference.md](api-reference.md#tickimportmqh-public-functions)).
Never fork the loader; fix it once, both callers benefit.

---

## Compiling MQL5 headlessly

There is no interactive MetaEditor step in this pipeline — everything
compiles via the MetaEditor CLI, driven from WSL:

```sh
./pipeline/stage_csv_for_import.sh --compile
```

This syncs every `.mq5`/`.mqh` source from the repo (`mql5/scripts/`,
`mql5/experts/`, `mql5/include/`, preserving subdirectories like `Hybrid\`)
into the terminal's `MQL5\Scripts\`, `MQL5\Experts\`, `MQL5\Include\`, then
runs:

```sh
"/mnt/c/Program Files/OANDA MetaTrader 5/MetaEditor64.exe" /compile:"C:\...\MQL5\<Subdir>\<Name>.mq5" /log
```

for `ImportTicks.mq5`, `VerifyImport.mq5`, and (if present)
`HybridForwardTest.mq5`/`AutoImport.mq5`. `MetaEditor64`'s `/compile` exit
code is the error count, which the script tolerates (`|| true`) so it can
always print the log; it then decodes the compile log (written UTF-16LE by
MetaEditor) to UTF-8 via `iconv` and greps for `0 error` to report
pass/fail per file.

**Never edit the copies inside the MT5 data folder directly** — they are
silently overwritten by the next `--sync-scripts`/`--compile`. Always edit
`mql5/**/*.mq5`/`*.mqh` in the repo and recompile.

To build the DLL (separate toolchain, not MetaEditor): see
[api-reference.md#build--deploy](api-reference.md#build--deploy) —
`./mql5/dll/build.sh`.

---

## Resume the pipeline in a new session

If you're picking this project up in a fresh Claude session (or after a
host reboot) and need to know "is the data pipeline still running, and if
not, how do I restart it":

**1. Check what's actually running:**

```sh
pgrep -af fleet_download.py       # the downloader
pgrep -af rolling_import.sh       # the importer
pgrep -af stall_watchdog.sh       # (optional) the coarse auto-kill watchdog
tasklist.exe /FI "IMAGENAME eq terminal64.exe"   # is an MT5 terminal open right now?
```

If both `fleet_download.py` and `rolling_import.sh` show up, the pipeline
is live — just check progress (`./pipeline/progress.sh`) and, if you're the
PM, make sure a stall-detector loop (see [above](#stall-detection)) is also
running so downloads that hang get flagged.

**2. If nothing is running, check where it left off:**

```sh
wc -l pipeline/fleet_state.txt pipeline/import_state.txt   # X/49, Y/49
tail -20 pipeline/fleet_failures.txt pipeline/import_failures.txt
df -BG --output=avail /mnt/c                                # real disk headroom
```

**3. Relaunch whichever worker(s) are missing:**

```sh
# downloader (only if < 49/49 downloaded)
cd /home/jack/QDM && nohup python3 /home/jack/hybrid_project/pipeline/fleet_download.py \
    > /home/jack/hybrid_project/logs/fleet_download.log 2>&1 &

# importer (only if < 49/49 imported, or downloader is about to run)
nohup /home/jack/hybrid_project/pipeline/rolling_import.sh \
    > /home/jack/hybrid_project/logs/rolling_import.log 2>&1 &

# a stall-detector loop for the downloader (recommended whenever fleet_download.py is launched
# under active supervision — see the reconstructed loop body above)
```

Both workers are idempotent-safe to relaunch: they resume purely from the
state files (`fleet_state.txt`/`import_state.txt`/`fleet_failures.txt`/
`import_failures.txt`), never from in-memory state, so a kill+relaunch never
double-processes a completed symbol and never loses a completed one.

**4. If `terminal64.exe` was left open** (e.g. the importer or `mt5_verify.sh`
was killed mid-run): decide whether it's mid-import (check
`MQL5\Files\import\import_status.txt` for a recent `# DONE` or a fresh
mtime) before closing it — `mt5_import.sh`/`mt5_verify.sh` both refuse to
launch a second terminal while one is running, so you must resolve this
before relaunching either.

**5. Sanity-check disk** before doing anything disk-heavy: `df -BG
--output=avail /mnt/c` — if it's already below ~60 GB, both workers' own
guards will pause automatically, but it's worth knowing going in.

**Do not** run `qdmcli` directly while `fleet_download.py` is running (the
one-JVM-at-a-time constraint is real and unenforced by the OS — you would
get overlapping/corrupt behavior), and do not launch a second `terminal64`
while `mt5_import.sh`/`mt5_verify.sh`/the rolling importer has one open —
both scripts check for this and refuse, but a manual launch outside them
would not be checked.
