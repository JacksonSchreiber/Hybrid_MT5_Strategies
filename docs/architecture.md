# Architecture

Technical, end-to-end. See [README.md](README.md) for the navigation index and
current status.

## System overview

Three subsystems, built/planned in order:

1. **Data pipeline** (Phase 1, built & running) — pulls Dukascopy tick history
   for 49 FTMO symbols via the QDM CLI and lands it as MT5 `.dk` custom
   symbols, under a hard real-disk constraint.
2. **Interactive forward-test harness** (Phase 2, built) — three MQL5
   detectors run inside the MT5 Strategy Tester against the imported tick
   data; every setup is drawn on the chart and presented to the account owner
   via a colour-coded modal before anything is sized/placed.
3. **Production loop** (Phase 3, **planned, not built**) — an unattended EA on
   a VPS pushes signals to Telegram for remote approval. Nothing in this repo
   implements this yet.

## 1. Data pipeline (Phase 1)

### Data flow

```
config/symbols.yaml (49 FTMO symbols -> Dukascopy names)
         │
         ▼
pipeline/fleet_download.py   (one qdmcli JVM invocation at a time, per symbol)
         │
         │  1. qdmcli -symbol action=add   symbols=<duk> datasource=dukascopy datatype=TICK
         │  2. qdmcli -data action=update  symbols=<duk>            (full-history download)
         │  3. qdmcli -data action=exportToMT5 symbol=<duk> timeframe=TICK
         │              datefrom=<DATEFROM> dateto=<DATETO>
         │              outputdir=data/mt5_ready filename=<ftmo_name>
         │  4. validate_export() -> pipeline/fleet_state.txt (success) or
         │                          pipeline/fleet_failures.txt (failure)
         │  5. delete QDM's internal per-symbol store (/home/jack/QDM/user/data/History/<duk>)
         │     -- only after validate_export() passes -- to keep the vhdx from growing forever
         ▼
data/mt5_ready/<FTMO>.csv   (plain CSV: "yyyy.MM.dd HH:mm:ss.mmm,bid,ask", no header)
         │
         │  pipeline/rolling_import.sh  (polls fleet_state.txt, runs IN PARALLEL with the fleet)
         │      for each symbol done-in-fleet but not-yet-imported:
         │        pipeline/mt5_import.sh --clean <BASE>
         ▼
pipeline/mt5_import.sh
         │  1. refuse if terminal64.exe already running (never disrupt a live terminal)
         │  2. pipeline/stage_csv_for_import.sh <BASE>   (copy CSV into MQL5\Files\import\)
         │  3. write MQL5\Files\import\jobs.txt (one BASE per line)
         │  4. launch terminal64.exe /config:<startup ini>  (auto-attaches AutoImport.mq5)
         ▼
mql5/experts/AutoImport.mq5  (on OnTimer, not OnInit)
         │  for each job: RunTickImport() from mql5/include/Hybrid/TickImport.mqh
         │    - CustomSymbolCreate <BASE>.dk (clone specs from <BASE>.sim if present, else infer)
         │    - stream CSV -> CustomTicksAdd in ~1M-tick batches
         │    - build M1 bars in-stream -> CustomRatesUpdate
         │    - TI_ApplySessions(): define 24x5 FX / 7d crypto trading+quote sessions
         │  writes MQL5\Files\import\import_status.txt, then TerminalClose(0)
         ▼
<BASE>.dk custom symbol inside the MT5 terminal's own tick/bar database
(bases\Custom\<BASE>.dk\... on the Windows host)
         │
         │  rolling_import.sh verifies the "<BASE>,OK,..." line in import_status.txt,
         │  THEN deletes data/mt5_ready/<BASE>.csv (source CSV) and appends
         │  <BASE> to pipeline/import_state.txt
         ▼
disk reclaimed; fleet_download.py and rolling_import.sh both keep polling
until 49/49 downloaded AND 49/49 imported
```

A live **2-minute stall-detector** (a background bash loop launched
alongside the fleet, not a file in the repo) watches the active `qdmcli
action=update` process; if its progress line hasn't changed in 120s it
prints a `STALL <symbol> ...` line and leaves the decision (kill/wait/skip)
to the PM. This supersedes the older, coarser `pipeline/stall_watchdog.sh`
(20-minute stall / 70-minute hard cap, auto-kills). Both exist in the repo;
see [tools-guide.md](tools-guide.md#stall-detection) for which one is
actually in use and why.

### Why the pipeline is shaped this way: the WSL disk model

WSL2's ext4 filesystem lives inside a `.vhdx` virtual disk file on the
Windows `C:` drive. That `.vhdx` **grows on demand but never shrinks** —
deleting a file inside WSL frees space *within* the vhdx for future writes,
but does **not** return space to the real Windows `C:` drive. `df /` inside
WSL therefore reports free space against the vhdx's current allocation,
which is fiction relative to the actual constraint (real Windows-host `C:`
free space, checked via `df -BG --output=avail /mnt/c`).

Consequences baked into the pipeline:

- **Every disk guard in the codebase checks `/mnt/c`, not `/`.** See
  `free_gb_windows_host()` in `pipeline/fleet_download.py` and the equivalent
  function in `pipeline/rolling_import.sh`.
- **QDM's internal full-history store is deleted per-symbol, right after its
  CSV is validated**, because that store is dead weight once the CSV exists
  and the pipeline never re-downloads. This claws back *vhdx* space (write
  headroom), not real `C:` space.
- **The rolling importer is a net disk *reclaimer*:** it deletes the
  multi-GB source CSV in `data/mt5_ready/` immediately after a verified
  successful import — the imported form (ticks + M1 bars inside the MT5
  terminal's own database) is far smaller than the CSV (e.g. AUDUSD:
  ~607 MB imported vs. a multi-GB CSV; see `docs/auto-import.md`).
- **If real `C:` free space drops below a threshold** (`DISK_GUARD_MIN_GB`,
  60 GB in `fleet_download.py`, 50 GB in `rolling_import.sh`), the affected
  script **pauses and polls** (10-minute interval) rather than aborting,
  giving the other half of the pipeline (importer draining CSVs, or
  downloader slowing new writes) time to recover real disk. `fleet_download.py`
  gives up after 6 hours and exits cleanly (resumable on next run);
  `rolling_import.sh` polls indefinitely.

### Resumability

Both `fleet_download.py` and `rolling_import.sh` are safe to kill and
restart at any time:

- `pipeline/fleet_state.txt` / `pipeline/import_state.txt` are the
  source-of-truth "done" lists (append-only, one line per completed symbol).
  A symbol already in the relevant state file is skipped on restart.
- `pipeline/fleet_failures.txt` / `pipeline/import_failures.txt` record
  failures with a timestamp and reason. `fleet_download.py` **skips**
  symbols already in `fleet_failures.txt` on restart (they're intended to be
  retried deliberately, as a separate batch) — a symbol that later succeeds
  ends up in **both** files; that is normal (the failure line is just stale
  history), not a live problem.
- `rolling_import.sh` does **not** skip past failures the same way: an
  import failure leaves the source CSV in place, so it's retried
  automatically on the importer's next poll pass.

See [tools-guide.md](tools-guide.md#resume-the-pipeline-in-a-new-session)
for the concrete resume runbook.

## 2. Phase-2 interactive forward-test harness

### Data flow

```
MT5 Strategy Tester (visual mode, "Every tick based on real ticks", .dk symbol)
         │  OnTick(): on every NEW H4 bar close
         ▼
HybridForwardTest.mq5 :: OnTick()
         │  calls Detect() on ALL enabled detectors (each advances its own
         │  state machine every bar, closed-bar only), keeps the first
         │  valid emit in priority order SMC(1) > Fib(2) > EMA(3)
         ▼
ISignalDetector::Detect()  (mql5/include/Hybrid/detectors/{Smc,Fib,Ema}Detector.mqh)
         │  returns a filled SignalCandidate (mql5/include/Hybrid/Signal.mqh)
         ▼
HybridForwardTest.mq5 :: HandleSignal()
         │  1. DrawOverlays()   -- zone rectangle, entry/SL/TP H-lines (colour-matched),
         │                         aux levels, optional 2nd zone, impulse-leg trendline
         │  2. AskApproval()    -- TradeDialog.dll modal (or MessageBoxW fallback)
         │                         BLOCKS the tester thread until answered
         │  3. on approve: SizeByRisk() (1% equity / SL distance) -> g_trade.Buy/Sell()
         │     on deny:    journal a "denied" row, no order
         ▼
ManageOpenPositions()  (every tick)
         │  for two-target strategies (Fib, EMA): bank partial_fraction at tp1,
         │  move SL to breakeven, let the remainder run to tp2
         ▼
OnTradeTransaction()
         │  accumulates blended, volume-weighted R across the (up to two) closing deals
         ▼
WriteJournal()  ->  <Terminal>\Common\Files\journal\<SYMBOL>_<start>_<end>.csv
```

Full field-level contracts (every `SignalCandidate` field, every EA input,
the exact journal CSV columns) are in
[api-reference.md](api-reference.md). The operational "how to run a demo /
how to headless-verify" workflow is in
[tester-harness.md](tester-harness.md) and
[tools-guide.md](tools-guide.md).

### Hard safety rule

The DLL modal is invoked **only** when `MQL_TESTER && MQL_VISUAL_MODE &&
MQL_DLLS_ALLOWED` are all true (`HybridForwardTest.mq5 :: OnInit()`).
Anywhere else — a live chart, a non-visual optimization run — the EA prints
an explanation and stays completely inert: no dialog, no trades. This is
enforced in code, not by operator discipline. `InpAutoApprove` (`AA_ALL` /
`AA_SKIP`) is a **tester-only** headless path that also touches no DLL,
gated the same way, used for automated verification
(`pipeline/mt5_verify.sh`).

### Why a DLL and not native MQL5

`MessageBox()` does nothing inside the Strategy Tester. `user32.dll`'s
`MessageBoxW` blocks the tester thread and does work, but cannot colour
text. `TradeDialog.dll` (`mql5/dll/TradeDialog.c`) is a small Win32 helper
that reproduces `MessageBoxW`'s blocking, system-modal behaviour with
colour-coded fields matching the chart overlay palette. See
[api-reference.md](api-reference.md#tradedialogdll) for the exact exported
signature.

## 3. Phase 3 (planned — not built)

Per `CLAUDE.md`/`README.md`, Phase 3 is: an EA running unattended on a VPS
that pushes signal alerts to Telegram, where the account owner approves or
denies remotely. **Nothing in this repo implements any part of Phase 3** —
there is no VPS provisioning code, no Telegram integration, no cloud EA
variant. The Phase-2 harness is deliberately tester-only (it depends on
`MQL_TESTER`, DLL imports, and `WebRequest`, none of which are available or
appropriate on a live cloud host) — CLAUDE.md notes this is by design:
"WebRequest does not work in the tester and DLLs don't run on cloud hosts —
tester (Ph. 2) and production (Ph. 3) are separate mechanisms by design."
Do not extend the Phase-2 harness in place expecting it to become Phase 3;
a live-trading EA needs its own signal-delivery mechanism (e.g. WebRequest
to a Telegram bot API) built from scratch, reusing only the
`ISignalDetector`/`SignalCandidate` detection layer.

## Related docs

- [tools-guide.md](tools-guide.md) — how to actually run every piece above.
- [api-reference.md](api-reference.md) — exact signatures/contracts.
- [strategies/README.md](strategies/README.md) — the detection logic itself (shared vocabulary + per-strategy specs).
