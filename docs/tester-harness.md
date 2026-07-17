# Phase 2 Interactive Tester Harness

`mql5/experts/HybridForwardTest.mq5` lets the account owner live through the
**approve / deny decision workflow** in the MT5 visual Strategy Tester before
any real strategy is wired in. It runs a *dummy* signal, but every other part
(modal, overlays, 1%-risk sizing, order execution, journal) is the real
Phase-2 machinery.

## What it does

On each signal (dummy: **the first H4 bar of every Monday, alternating
buy/sell**) it:

1. Draws the decision context on the chart — entry (green), SL (red) and TP
   (blue) lines, a setup-zone rectangle (last 5 H4 bars' range) and a text
   label with strategy name + timestamp. Objects are named `HFT_<id>_*` and
   **persist** after the decision.
2. Pops a **system-modal Yes/No box** (`user32.dll MessageBoxW`, flags
   `MB_YESNO|MB_ICONQUESTION|MB_SYSTEMMODAL`) showing symbol, strategy,
   direction, entry/SL/TP, R:R and the risk-sized lot count.
3. **Yes** → sizes the position at 1% of equity from the SL distance and
   places the order. **No** → journals a skip.
4. Appends the signal (and, on close, the outcome) to a CSV journal.

### Hard safety rule

The DLL is called **only** when `MQL_TESTER && MQL_VISUAL_MODE` are both true
*and* DLL imports are allowed. Run anywhere else (live chart, non-visual
optimisation) and the EA prints an explanation and stays **inert** — it never
touches `user32.dll` and places no trades. (Native MQL5 `MessageBox()` does
nothing in the tester, which is why the DLL is used at all.)

## Prerequisites

- `EURUSD.dk` custom symbol already imported (see `docs/mt5-import.md`).
- The EA compiled (0 errors) — it appears in Navigator → Expert Advisors as
  **HybridForwardTest**. Rebuild any time with
  `./pipeline/stage_csv_for_import.sh --compile`.

## Enabling the DLL modal (required — two separate switches)

1. **Terminal:** Tools → Options → Expert Advisors → tick **"Allow DLL
   imports"**. Click OK.
2. **Tester (per run):** in the Strategy Tester **Settings** tab, after
   choosing the EA, there is an EA-options row — make sure **"Allow DLL
   imports"** is ticked there too. Some builds expose it as a dialog when you
   press the EA's *inputs/settings* button. Both the terminal switch and the
   tester switch must be on, or the EA reports DLLs disabled and stays inert.

## Running the demo

1. **View → Strategy Tester** (Ctrl+R).
2. **Settings** tab:
   - Expert: **HybridForwardTest**
   - Symbol: **EURUSD.dk**
   - Modelling: **Every tick based on real ticks**
   - Visual mode: **on** (tick the *Visual mode* box)
   - Date: use a custom range, suggested **2024-01-01 → 2024-03-31** (about a
     dozen Mondays → a dozen decisions; long enough to feel the workflow,
     short enough to finish quickly).
   - Deposit: 25000 USD (matches the FTMO account) is a sensible default.
3. Press **Start**. The visual chart opens and replays ticks.
4. When a signal fires the chart draws the lines + zone + label and a Yes/No
   dialog appears. Read the context, click **Yes** to trade or **No** to skip.
5. Repeat for each signal. When the run ends, check the Journal/Experts tab
   for the summary and the journal-file path.

### The Pause (VK_PAUSE) trick

The visual tester resumes replaying as soon as you answer the modal. If you
want to **freeze the chart to study the setup** before/after deciding, press
the keyboard **Pause** key while the dialog is up (or before answering). The
tester stays paused after you answer, so you can inspect the overlays; press
Pause again (or the tester's play button) to resume. Without this, the chart
keeps moving right after your click.

## The journal

Written with `FILE_COMMON`, so during testing it lands in a **stable, findable
location** (not the hidden per-agent tester sandbox):

```
<Terminal>\Common\Files\journal\EURUSD.dk_<start>_<end>.csv
```

Typically:
`C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\Common\Files\journal\EURUSD.dk_20240101_20240331.csv`
(WSL: `/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal/`).

During the run it is `..._<start>.part.csv`; at the end it is rewritten to the
`<start>_<end>.csv` name and the `.part` file is removed. The whole file is
rewritten and `FileFlush`-ed on every signal and every trade close, so a tester
crash never loses more than the in-flight write.

Columns (one row per signal):

| Column | Meaning |
|---|---|
| `signal_id` | running counter |
| `signal_time` | signal bar time (UTC) |
| `symbol`, `strategy`, `direction` | context |
| `entry`, `sl`, `tp`, `lots` | proposed trade + risk-sized volume |
| `decision` | `approved` / `denied` |
| `decision_ms` | wall-clock ms the human took to answer |
| `posid` | position id (0 if denied/failed) |
| `exit_time`, `exit_price`, `pnl`, `r_multiple` | filled when the position closes |

`r_multiple` = realised move ÷ initial risk (entry→SL) distance.

## Risk sizing

`lots = (equity × 1%) ÷ (SL_distance ÷ tick_size × tick_value)`, then
normalised to the symbol's volume step/min/max. All specs are read from the
symbol, so it works on `EURUSD.dk`. Note: on a custom symbol built with
*inferred* specs (no `.sim` origin to clone), `tick_value` may be a placeholder
— the lot math is only as accurate as those specs, so treat demo lot sizes as
representative until the harness runs on the real FTMO terminal.

## Architecture (Phase 2 growth)

The harness talks only to the `ISignalDetector` interface and the
`SignalCandidate` struct (`mql5/include/Hybrid/Signal.mqh`). The three real
strategies plug in later by implementing `ISignalDetector` and swapping the
`new CDummyDetector(...)` line in `OnInit` — the overlays, modal, sizing,
execution and journal need no changes.

```
ISignalDetector (interface)
   └── CDummyDetector      (this phase: Monday-H4 alternating dummy)
   └── CStrategyA / B / C  (Phase 2: real detectors, drop-in)
```

## Verified from documentation vs needs the live demo

**Settled from docs / project notes (no live run needed):**
- Native `MessageBox()` does not work in the tester; `user32.dll MessageBoxW`
  is required and blocks the tester thread (project constraint, CLAUDE.md).
- MQL5 `string` is UTF-16, so it marshals to `MessageBoxW`'s `LPCWSTR`
  correctly; `hWnd=0` + `MB_SYSTEMMODAL` is the standard working pattern.
- `OnTradeTransaction` fires in the tester; matching a `DEAL_ENTRY_OUT` deal's
  `DEAL_POSITION_ID` to the stored `posid` is the correct outcome-capture path.
- The DLL guard (`MQL_TESTER && MQL_VISUAL_MODE && MQL_DLLS_ALLOWED`) means the
  DLL is never called outside the visual tester.

**Needs the live demo to confirm (report back after the first run):**
- **Overlays visible *while the modal is up*.** The whole point of drawing
  before the modal is that the user sees context while the tester is frozen.
  Objects created in `OnTick` normally repaint only after the handler returns,
  and `MessageBoxW` blocks inside the handler — a system-modal runs its own
  Windows message pump that *should* service the chart's paint, but this can
  only be confirmed visually. **Check: are the entry/SL/TP lines and the zone
  box visible behind the dialog?** If they appear only after you answer, that
  is a known tester-repaint limitation — note it and we adjust.
- Exact placement of the tester's per-run "Allow DLL imports" control varies by
  MT5 build — confirm where it is on this terminal.
- That the modal genuinely halts tick replay until answered (expected, but
  worth eyeballing on the first signal).
