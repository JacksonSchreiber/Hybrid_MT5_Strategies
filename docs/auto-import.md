# Unattended MT5 Auto-Import

Runs the tick import **with no manual drag-the-script step**, so the rolling
disk pipeline (download → export CSV → import → delete CSV + clear QDM store →
reclaim) can run hands-off. Proven end-to-end on AUDUSD (132.9M ticks).

## Why this exists (the disk constraint)

The Windows host has ~250 GB free. WSL's ext4 lives in a vhdx on C: that never
shrinks, so deleting a CSV inside WSL does **not** reclaim C: space. But the
imported form is tiny — AUDUSD is **~607 MB** in `bases\Custom\` vs a **5.0 GB**
CSV. So the pipeline imports each symbol, then deletes the CSV to reclaim
space. Import had to become unattended for that loop to run autonomously.

## Pieces

| File | Role |
|---|---|
| `mql5/include/Hybrid/TickImport.mqh` | The shared tick-load engine (`RunTickImport`), extracted from ImportTicks so the manual script and the auto EA run **identical** code. |
| `mql5/experts/AutoImport.mq5` | EA: reads a job list, imports each symbol via the shared engine, writes a status file, self-closes the terminal. |
| `pipeline/mt5_import.sh` | WSL driver: stages CSVs, writes the job list, launches terminal64 headlessly, waits, parses status. |

`ImportTicks.mq5` (manual) and `AutoImport.mq5` (unattended) both `#include`
the same engine — there is one tick loader, not two.

## Mechanism

1. The driver checks **no `terminal64.exe` is already running** (else it
   refuses — see safety below), stages the CSV(s) into
   `MQL5\Files\import\<BASE>.csv`, and writes the job list `jobs.txt`.
2. It writes a one-shot startup config ini and launches:
   ```
   "C:\Program Files\OANDA MetaTrader 5\terminal64.exe" /config:<...>\autoimport_startup.ini
   ```
   The ini (`[StartUp]` section) auto-attaches AutoImport to a chart:
   ```ini
   [StartUp]
   Expert=AutoImport
   Symbol=EURUSD.dk
   Period=H4
   ```
   **No `/portable`** — a plain launch of this terminal uses the `EE0304…`
   AppData data folder that already holds the `.dk` symbols and staged CSVs;
   `/portable` would wrongly use the install directory.
3. AutoImport runs its batch from the **first `OnTimer`** tick (not `OnInit` —
   a multi-minute blocking `OnInit` would stall the background M1 series-sync
   the importer depends on). For each job it calls `RunTickImport` (same
   `CustomTicksAdd` + `CustomRatesUpdate` logic as the manual importer),
   appends a line to `import_status.txt`, and finally calls **`TerminalClose(0)`**.
4. The driver polls `tasklist.exe` until `terminal64` exits (and/or the
   `# DONE` marker appears in the status file), then parses the status and
   returns pass/fail.

### Host chart symbol

`[StartUp] Symbol` must be a symbol that **already exists** at launch (the
chart has to open for the EA to attach). We use `EURUSD.dk` (imported during
the smoke test). The imported jobs are independent of this host symbol.
Override with `--host-symbol` if `EURUSD.dk` is ever absent (any existing
symbol works, including a broker symbol like `EURUSD`).

## Usage

```sh
./pipeline/mt5_import.sh AUDUSD                    # one symbol
./pipeline/mt5_import.sh AUDUSD NZDUSD USDCAD      # several (sequential, one terminal)
./pipeline/mt5_import.sh --clean AUDUSD            # delete the staged CSV after success (reclaim C:)
./pipeline/mt5_import.sh --host-symbol EURUSD AUDUSD
./pipeline/mt5_import.sh --timeout 1800 AUDUSD     # max wait seconds (default 2400)
./pipeline/mt5_import.sh --sessions EURUSD AUDUSD  # re-patch trading sessions ONLY (no re-import)
```

Each `<SYMBOL>` (import mode) must have `data/mt5_ready/<SYMBOL>.csv`.

### Trading sessions

New imports now define **24x5 FX trading/quote sessions** (7 days for crypto)
so tester orders fill — a custom symbol with no sessions rejects every order
with `10018 market closed`. Sessions (UTC): Sun 20:00-24:00, Mon-Fri
00:00-24:00, Sat none. To re-patch **already-imported** symbols without touching
their ticks, use `--sessions <BASE>...` (writes `SESSIONS <base>` job lines that
AutoImport applies via `TI_SessionsOnly`). Verified: after re-patching
`EURUSD.dk`, an identical 2021-2024 tester run went from many `10018` failures
to **0**, 99% fill rate.

## File contracts (for the pipeline engineer)

**Input — `MQL5\Files\import\jobs.txt`** (written by the driver): one symbol
base per line; blank lines and `#` comments ignored. Consumed (deleted) by
the EA on start.

```
AUDUSD
NZDUSD
```

**Output — `MQL5\Files\import\import_status.txt`** (written by the EA, one line
per job, `FileFlush` after each so a crash keeps completed rows):

```
# SYMBOL,STATUS,ticks,first,last,seconds
AUDUSD,OK,132896012,2020.01.01 22:00:10.013,2026.07.16 23:59:57.120,162.7
# DONE 1 ok, 0 fail, 162.7 s
```

- Columns: `SYMBOL, OK|FAIL, ticks, first(UTC), last(UTC), seconds`.
  `first`/`last` contain a space but no comma (safe to split on `,`).
  On `FAIL`, ticks=0, first/last empty, and the 6th column carries the error.
- A trailing `# DONE <ok> ok, <fail> fail, <s> s` line marks completion.

The driver treats a symbol as success iff a `^<SYMBOL>,OK,` line exists.

## Proof (this run)

| Test | Ticks | Import time | Total wall | Result |
|---|---|---|---|---|
| TESTAA (600-tick synthetic) | 600 | 0.0 s | 8 s | OK, self-closed |
| **AUDUSD (real)** | **132,896,012** | **162.7 s** | **170 s** | **OK, self-closed** |

The terminal launched, imported, wrote status, and closed itself with **zero
manual interaction**. On-disk result: AUDUSD.dk = ~607 MB (450 MB ticks +
157 MB M1 history) vs the 5.0 GB CSV. Rough throughput: ~0.8M ticks/sec, so
budget **~3–5 min per major FX symbol** (startup + import + close); thinner
symbols are faster.

**Bars built (backtest-ready).** The Experts log for the AUDUSD run reported
`M1=100000 H4=10543 D1=2046` with `bad=0`. `D1=2046` is ~5.6 years of daily
bars (2020→2026) and `H4=10543` spans the same range — i.e. the M1 backbone the
tester needs was built across the whole history. `M1=100000` is **not** the true
M1 count: `iBars`/`SERIES_BARS_COUNT` are capped by the terminal's *Max bars in
chart* setting. The real M1 series (~2.4M bars) lives in
`bases\Custom\history\AUDUSD.dk` (157 MB) and the Strategy Tester reads the full
history from disk regardless of the chart cap. So `AUDUSD,OK` = a symbol that
runs "every tick based on real ticks", not merely ticks persisted.

## Safety & caveats

- **Never disrupts a running terminal.** If `terminal64.exe` is already
  running (the user may be using MT5), the driver refuses to launch and exits
  non-zero. Close MT5 first, or schedule imports when it's not in use.
- **Re-trigger guard.** The EA deletes `jobs.txt` as its first action, so if
  the startup config leaves an AutoImport chart in the profile, a later
  *normal* launch finds no `jobs.txt`, does nothing, and does **not** close the
  terminal. (A harmless inert AutoImport chart may remain in the profile; the
  user can remove it.)
- **Algo Trading must be enabled.** EAs only run when the terminal's global
  "Algo Trading" toggle is on (it persists across restarts). If a run produces
  no `import_status.txt` and the terminal sits at the desktop instead of
  closing, enable Algo Trading (Ctrl+E) once, then re-run. AutoImport does not
  trade, but the toggle still gates EA execution. (It was on for the proof
  runs.)
- **Focus stealing.** Launching terminal64 opens its window and may take focus
  for the ~3–5 min run. Expected; run when you're not mid-task on the desktop.
- **Timeout is report-only.** On timeout the driver reports and leaves the
  terminal for the operator to close (it does not force-kill). In practice the
  EA self-closes well within the default 2400 s for a single symbol.
- **Cold-start race.** The driver's wait loop breaks when `terminal64` is no
  longer in `tasklist`. The pre-loop `sleep 8` is the guard that lets the
  process appear before polling — on a cold or antivirus-throttled start where
  the terminal takes >8 s to register, the loop could momentarily see "not
  running" and declare a false FAIL while an import is actually still going. It
  fails to the safe side (the "refuse if already running" guard then blocks a
  clobbering relaunch), but if you see spurious fast failures, raise that
  initial sleep. Proven fine here (startup was <8 s).
- **Antivirus** may scan the freshly-written `.ex5`/DLL or the large CSV copy;
  if launches are slow to start, whitelist the data folder.
- The config ini is written read-only-style and removed after the run; MT5
  does not save changes back to a `/config` file.

## Rebuild

```sh
./pipeline/stage_csv_for_import.sh --compile   # compiles ImportTicks, VerifyImport,
                                               # HybridForwardTest, AutoImport
```
