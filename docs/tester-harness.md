# Phase 2 Interactive Tester Harness

`mql5/experts/HybridForwardTest.mq5` lets the account owner live through the
**approve / deny decision workflow** in the MT5 visual Strategy Tester.

> **Update (task #14):** the harness now drives the **three real detectors**
> (SMC sweep+MSS, Deep-Fib, EMA20 mean-reversion) with priority arbitration and
> real two-target scale-out — not the Monday dummy. See
> [strategies/detectors-implementation.md](strategies/detectors-implementation.md).
> New EA inputs:
> - `InpUseSMC` / `InpUseFib` / `InpUseEMA` — enable each strategy (all true).
>   Priority when several fire on one bar: **SMC > Fib > EMA**.
> - Per-strategy params: `InpSmcMinRR`, `InpSmcTpR`, `InpFibImpulseATR`,
>   `InpFibMinRR`, `InpEmaStretch`, `InpEmaAdxCeil`, `InpEmaMinRR`.
> - `InpAutoApprove` (**tester-only**): `NONE` = interactive modal (default);
>   `ALL` / `SKIP` = headless auto-approve/skip for automated verification via
>   `pipeline/mt5_verify.sh` (no modal, no DLL).
> - Removed: `InpLookback`, `InpRR` (dummy-only).
>
> The sections below describe the interactive visual-tester workflow, which is
> unchanged; just replace "dummy Monday signal" with "a detector's setup".

## What it does

On each signal (dummy: **the first H4 bar of every Monday, alternating
buy/sell**) it:

1. Draws the decision context on the chart — entry (green), SL (red) and TP
   (blue) lines, a setup-zone rectangle (last 5 H4 bars' range) and a text
   label with strategy name + timestamp. Objects are named `HFT_<id>_*` and
   **persist** after the decision.
2. Pops a **system-modal Yes/No dialog** showing symbol, strategy, direction,
   entry/SL/TP, R:R and the risk-sized lot count — with the **entry/SL/TP text
   colour-matched to the chart lines** (see below).
3. **Yes** → sizes the position at 1% of equity from the SL distance and
   places the order. **No** → journals a skip.
4. Appends the signal (and, on close, the outcome) to a CSV journal.

### Hard safety rule

The dialog DLL is called **only** when `MQL_TESTER && MQL_VISUAL_MODE` are both
true *and* DLL imports are allowed. Run anywhere else (live chart, non-visual
optimisation) and the EA prints an explanation and stays **inert** — it invokes
no dialog DLL and places no trades. (Native MQL5 `MessageBox()` does nothing in
the tester, which is why a DLL is used at all.)

## The coloured dialog (TradeDialog.dll)

`MessageBoxW` cannot colour text, so the coloured dialog is served by a tiny
Win32 helper DLL, **`mql5/dll/TradeDialog.c`** → **`TradeDialog.dll`**. It
creates a centred, top-most, system-modal window that **blocks the tester
thread until answered** (exactly like `MessageBoxW`) and colours each field:

| Field | Dialog colour | Matches chart line |
|---|---|---|
| Entry | green `RGB(0,160,0)` | entry H-line |
| Stop Loss | red `RGB(204,0,0)` | SL H-line |
| Take Profit | blue `RGB(0,0,204)` | TP H-line |
| Direction | BUY green / SELL red | — |
| Symbol / Strategy / Lots / R:R | neutral grey-black | — |

The chart overlay colours in the EA were set to the **same** RGB values, so the
dialog and the chart lines are pixel-matched. Buttons: **Yes (approve)** and
**No (skip)**; **No is the default** (a reflexive Enter skips rather than
trades). Keyboard: **Y** = approve, **N**/**Esc** = skip, **Enter** = the
focused button. Returns `1` (approve) / `0` (skip). A re-entrancy guard makes a
second concurrent call return "skip" immediately.

Exported symbol (undecorated, x64 — matches the MQL5 `#import`): **`ShowTradeDialog`**.

### Build & deploy the DLL

```sh
./mql5/dll/build.sh            # zig cross-compile + verify exports + deploy
./mql5/dll/build.sh --no-deploy # build + verify only
```

The DLL **must live in the terminal's `MQL5\Libraries\`** folder (where MT5
loads `#import` DLLs from — *not* `MQL5\Files\`); `build.sh` copies it there.
The built `.dll` is **deployed, not committed** (git-ignored); rebuild from the
`.c` any time.

### Fallback toggle & the early-binding caveat

`input InpUseColoredDialog` (default **true**) picks the back-end at runtime:
`true` = coloured `TradeDialog.dll`, `false` = plain `MessageBoxW`. **Important:**
MT5 *early-binds* `#import` DLLs at EA load, so **both** `TradeDialog.dll` and
`user32.dll` must be resolvable for the EA to load at all — the toggle is a
graceful-degradation switch (flip to `false` if the coloured dialog ever
misbehaves and you want the known-good monochrome box), **not** a rescue for a
missing DLL. If `TradeDialog.dll` is absent from `MQL5\Libraries\`, the EA fails
to load with a clear journal error; fix it by running `build.sh` (re-deploy),
not by toggling the input.

## Prerequisites

- `EURUSD.dk` custom symbol already imported (see `docs/mt5-import.md`).
- **`TradeDialog.dll` built and deployed** to `MQL5\Libraries\` via
  `./mql5/dll/build.sh` (required for the EA to load with the default
  `InpUseColoredDialog=true`).
- The EA compiled (0 errors) — it appears in Navigator → Expert Advisors as
  **HybridForwardTest**. Rebuild any time with
  `./pipeline/stage_csv_for_import.sh --compile`.

## Enabling the DLL modal (required — two separate switches)

Applies to both `TradeDialog.dll` and `user32.dll` — "Allow DLL imports" gates
all `#import` DLLs.

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
strategies are wired in (task #14) by implementing `ISignalDetector`; the
harness builds them in `OnInit` per the `InpUse*` flags — the overlays, modal,
sizing, execution and journal are strategy-agnostic.

```
ISignalDetector (interface)
   ├── CLiquiditySweepMSS   (priority 1)  detectors/SmcDetector.mqh
   ├── CDeepFibRetrace      (priority 2)  detectors/FibDetector.mqh
   ├── CEma20MeanRev        (priority 3)  detectors/EmaDetector.mqh
   └── CDummyDetector       (reference; not used by default) Signal.mqh
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
