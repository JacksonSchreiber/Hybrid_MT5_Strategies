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
> - `InpAutoApprove` (**tester-only**): `NONE` = interactive editable dialog
>   (default); `ALL` / `SKIP` = headless auto-approve/skip at the detector's
>   original levels for automated verification via `pipeline/mt5_verify.sh`
>   (no dialog, no DLL).
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
   **persist** after the decision. All overlay objects are created
   **non-selectable** so they can't be dragged by accident. Two context layers
   are added on top of the base overlay:
   - **Native Fibonacci grid (Deep-Fib setups).** A real `OBJ_FIBO` is anchored
     to the detector's impulse leg — anchor 0 = leg origin (`leg_p0`), anchor 1 =
     impulse extreme (`leg_p1`) — with custom levels **0.0 / 0.5 / 0.618 / 0.705
     (OTE) / 0.786 / 0.886 (SL) / 1.0**, golden-pocket levels (0.618–0.786)
     emphasised. The grid's level prices are made to **coincide exactly** with
     the detector's `Level(v) = leg_p1 + v·(leg_p0−leg_p1)` — i.e. the 0.618 line
     sits on the golden-pocket zone box's upper edge and the 0.886 line on the
     SL fib. The harness does **not** assume MT5's level-value convention: it
     creates the object, reads back where each level actually lands with
     `ObjectGetValueByTime`, and if the 0.618 line doesn't match it remaps every
     level value to `1−v` so the labels stay correct for both long and short
     setups. A `FIBCHK …` line is printed to the Experts log with the per-level
     `fib vs det` prices for verification. Because the native grid now carries
     the 0.5/0.618/0.786/0.886 levels with labels, the old individual Fib aux
     H-lines for those were dropped (EMA50 and the 1.618 TP2 extension aux
     remain).
   - **Swing-high / swing-low markers.** The confirmed fractal swings the
     detectors already find (SMC and Fib) are marked with an arrow
     (`OBJ_ARROW_DOWN` above each swing high, `OBJ_ARROW_UP` below each swing
     low) plus an `OBJ_TEXT` reading literally **"swing high"** / **"swing
     low"**, capped at the ~6 most recent of each to stay readable. These are
     additive context on top of the setup-specific labels (swept high/low, MSS
     level, etc.).
2. Pops an **editable Accept/Skip dialog** showing symbol, strategy, direction,
   entry/SL/TP, R:R and the risk-sized lot count — with the **entry/SL/TP text
   colour-matched to the chart lines** (see below). **Entry, Stop Loss and Take
   Profit are all editable and independent** (editing one never moves another);
   moving the entry away from market makes Accept a **pending order** (shown in
   the "Order" row). Lots re-size, R:R recomputes live, and the chart lines move
   before you commit; Accept blocks if R:R falls below the strategy's floor.
3. **Accept** → sizes the position at 1% of equity from the (possibly edited) SL
   distance and places a market fill, or a pending order at the edited entry.
   **Skip** → journals a skip (with a 1–6 reason code).
4. Appends the signal (and, on close, the outcome) to a CSV journal.

### Hard safety rule

The dialog DLL is called **only** when `MQL_TESTER && MQL_VISUAL_MODE` are both
true *and* DLL imports are allowed. Run anywhere else (live chart, non-visual
optimisation) and the EA prints an explanation and stays **inert** — it invokes
no dialog DLL and places no trades. (Native MQL5 `MessageBox()` does nothing in
the tester, which is why a DLL is used at all.)

## The coloured editable dialog (TradeDialog.dll)

`MessageBoxW` cannot colour text and cannot be edited, so the dialog is served
by a tiny Win32 helper DLL, **`mql5/dll/TradeDialog.c`** → **`TradeDialog.dll`**.
It is **poll-driven, not a blocking modal**: `TD_Open` shows the window and
returns immediately, then the EA pumps it (`TD_Poll`) from a `while`-loop inside
`OnTick`. That loop still blocks `OnTick` so the tester stays held on the bar —
but MQL runs between keystrokes, which is what lets it recompute R:R-locked
levels, re-size lots, and reposition the chart H-lines **live** as you edit.
(The chart repaints while `OnTick` is blocked — verified empirically.)

| Field | Dialog colour | Editable? | Matches chart line |
|---|---|---|---|
| Entry | green `RGB(0,160,0)` | **yes** (away from market = pending) | entry H-line |
| Stop Loss | red `RGB(204,0,0)` | **yes** (white box) | SL H-line |
| Take Profit | blue `RGB(0,0,204)` | **yes** (white box) | TP H-line |
| Direction | BUY green / SELL red | — | — |
| Symbol / Strategy / Lots / R:R | neutral grey-black | — | — |

The chart overlay colours in the EA were set to the **same** RGB values, so the
dialog and the chart lines are pixel-matched. Buttons: **Accept** and **Skip**;
**Skip is the default** (a reflexive Enter skips rather than trades), and Accept
is disabled whenever the current SL/TP geometry is invalid. Keyboard: **Enter** =
Accept (only when valid), **Esc** = Skip; typing routes to the focused SL/TP box.
`TD_Poll` returns `0` (pending) / `1` (Accept) / `2` (Skip). A re-entrancy guard
makes a second concurrent `TD_Open` return failure immediately.

**Editing levels — independent, scale-out preserved.** Each field edits on its
own: SL and TP are structural, so **editing the stop never moves the target (or
the scale-out structure), and vice-versa**; editing entry leaves the TPs put.
Scale-out strategies (EMArev, DeepFib) show **TP1 (bank) and TP2 (runner) as
separate editable fields** — the plan is preserved through edits; SMC shows one
TP. The R:R row shows **per-target R1/R2 + the split-weighted blend** (single-TP
shows `1 : R`). Every edit re-sizes lots to hold 1% risk and recomputes the live
R:R. Accept **blocks below the strategy floor** (2.0 SMC/Fib, 1.3 EMA), tested on
the **runner's R** so an *unedited* signal never false-blocks while an edit that
pulls the runner in does (`< MIN x.x` shows in the R:R). The chart shows a solid
TP1 line + a dashed TP2 runner line that move as you edit.

**Persistent swing markers.** "swing high"/"swing low" markers are now a
signal-independent overlay over a rolling `InpSwingDays` (14-day) window, redrawn
each bar — so they're always present (EMArev used to show none, since it never
populated the per-signal swing arrays).

**Entry → pending order.** Leave the entry at market and Accept fills at market
as before. Move it away and Accept places a **pending order** at that price —
buy/sell **limit** on the favourable side, **stop** on the breakout side,
auto-selected and shown in the dialog's **"Order"** row. Moving the entry deeper
(toward the stop) shrinks risk and *improves* R:R against the fixed target.
Unfilled pendings auto-cancel after `InpPendingExpiryBars` H4 bars (default 3)
and journal as `expired`; a fill binds via `OnTradeTransaction` so
scale-out/breakeven/exit-R all run normally. A resting pending suppresses new
signals (one setup/symbol).

**Skip with a reason:** press a digit **1–6** while the dialog is up (when you're
*not* typing in a number box) to skip and record *why* — `1` counter-trend, `2`
news/event, `3` ugly structure, `4` target obstructed, `5` correlated exposure,
`6` gut/other. The bare **Skip** button / Esc records `6`. The code lands in the
journal's `skip_reason` column, which powers the coaching report's skip-precision
analysis. (Approve rows and headless skips are `0`.)

**Decision screenshot:** when the dialog opens (overlays already drawn), the EA
saves a PNG of the chart to `MQL5\Files\journal\shots\<stem>_<signal_id>.png`
(`InpShotOnDecision`, default on; `InpShotW`×`InpShotH`, default 1600×900) — what
the operator saw at decision time, for a vision-capable coach. Note this is under
the terminal's `MQL5\Files\` (the only place `ChartScreenShot` can write), *not*
the `Common\Files` journal folder where the CSVs live.

**Upcoming-events list + Coach mode.** The dialog lists the **notable scheduled
events** for the pair's currencies over the next `InpEvtListDays` (14) days — the
economic-calendar context you'd want *before* deciding (ECB/Fed decisions, CPI,
NFP, GDP, PMIs). Each line carries a **significance tag** — `[HIGH]` / `[MED]` /
`[LOW]` — a keyword-derived rating (`EventSignificance`; there's no impact field in
`econ_events.csv`): rate decisions + central-bank statements/pressers + NFP/CPI/GDP
read `[HIGH]`, second-tier prints (PMI, unemployment, retail sales…) read `[MED]`.
Because the list itself is curated to top-tier events, tags in practice read HIGH
or MED (a genuinely low-impact print isn't listed). It's scheduled info only (time
+ currency + name + rating); no outcome is ever shown, because every listed event
is after the decision. The **Coach mode**
button redacts a screenshot for the AI coach: it blanks the **symbol** (`██████`),
switches all dates to **relative** (`in 1d 4h`), and **date-scrubs the on-chart
signal label** (the corner label's `@ <timestamp>` is dropped) — so neither the
dialog nor the chart behind it can reverse-identify the historical instance for
hindsight grading. Normal (un-toggled) shows the real symbol + absolute
`DD MMM HH:MM` + the full chart label.

**Chart event overlay (look-ahead-safe).** The dashed vertical event lines on the
chart now roll **forward** with the replay: the calendar is cached once and
redrawn each new bar over `[now − InpEvtPastDays, now + InpEvtLookaheadHours]`
(default 48h ahead). An event still in the future renders **neutral `[upcoming]`**
— its bull/bear colour + surprise is revealed only once replay passes it, so no
outcome is leaked ahead of time. (Previously the overlay drew once at the first
tick and, in the tester, only ever showed events *before* the test start — so
forward events never appeared.)

Exported symbols (undecorated, x64 — match the MQL5 `#import`): the signal dialog
**`TD_Open`**, **`TD_Poll`**, **`TD_SetDisplay`**, **`TD_SkipReason`**,
**`TD_SetOrderType`**, **`TD_SetEvents`**, **`TD_Coach`**, **`TD_Close`**; the
management panel **`TDM_Open`**, **`TDM_Update`**, **`TDM_Poll`**, **`TDM_Close`**
(plus a legacy no-op `ShowTradeDialog` stub).
Full contract in [api-reference.md](api-reference.md#tradedialogdll).

## The mid-trade management panel

While a position is open, a small **non-modal** panel appears (top-right of the
screen) and auto-hides the moment you go flat. It lets you act on the advisor's
mid-trade doctrine — flatten before a red-folder event, exit at break-even on a
kill condition, or scale out manually — none of which the Accept/Skip dialog can
do. It shows live state (direction, lots, entry, current SL/TP, and **Open R +
Banked R** — R being the only unit decisions are made in) and three buttons:

- **Close** — full market exit. *(news-eve flatten, kill-condition exit)*
- **Close 50%** — partial exit of current volume. *(manual scale-out fallback)*
- **SL → BE** — move the stop to entry + `InpBEPadPips` pips. *(pre-news lockdown,
  rejection-at-resistance hold)*

Close and Close-50% **arm on the first click** (the caption changes to
"Confirm …") and fire on the second — a paused tester makes mis-clicks easy.
SL→BE is instant (it's reversible), and is **greyed out** when break-even would
sit inside the broker's stops-level band (too close to price to place). The panel
minimises to the taskbar (its `[X]` minimises rather than closes, so you can't
lose it mid-trade).

**Why it's a separate window on its own thread:** the Accept/Skip dialog is pumped
by the EA's blocking `OnTick` loop, so it freezes the instant you pause the visual
tester. But pausing at bar close is *exactly* when you'd manage a trade — so the
panel runs its own Win32 message loop on a dedicated thread and stays live and
clickable while the tester is paused. Your click is executed on the next tick
(when you resume); the advisor's rules are evaluated on bar close anyway.

Every button press is logged to a sibling **`…_.actions.csv`** (bar time, price,
lots before→after, banked/open R) so the coach can grade the intervention —
`pipeline/review_session.py --actions <…>.actions.csv`. The panel is
interactive/visual-only (dormant in headless `AA_ALL`/`AA_SKIP`); toggle it with
`InpManagePanel`. Its four `TDM_*` exports are new, so **restart MT5** after a
rebuild (as with any DLL signature change).

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
4. When a signal fires the chart draws the lines + zone + label and the
   editable dialog appears. Read the context; optionally **edit Entry / Stop
   Loss / Take Profit** (each is independent — lots re-size, R:R recomputes, the
   chart lines move live; entry away from market becomes a pending order shown in
   the "Order" row; Accept blocks if R:R drops below the strategy floor). Then
   **Accept** to trade, or click **Skip** and pick a reason (or press **1–6**).
5. Repeat for each signal. When the run ends, check the Journal/Experts tab
   for the summary and the journal-file path.

### The Pause (VK_PAUSE) trick

While the dialog is up the tester is **held on the bar** (the EA is looping in
`OnTick`), so you already have unlimited time to study the setup and edit SL/TP.
The tester resumes replaying as soon as you click **Accept/Skip**. If you want
the chart to **stay frozen after you decide** (to inspect the overlays post-fill),
press the keyboard **Pause** key before answering; the tester stays paused after
your click. Press Pause again (or the tester's play button) to resume. Without
this, the chart keeps moving right after your click.

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

The full, verified-against-source **25-column schema** lives in
[api-reference.md § Journal CSV schema](api-reference.md#journal-csv-schema) —
trust that page. In brief, each row is one presented signal:

- **context** — `signal_id`, `signal_time`, `symbol`, `strategy`, `direction`
- **levels** — `orig_entry/orig_sl/orig_tp` (detector's proposal) vs
  `entry/sl/tp` (what was actually placed, so operator edits are analysable),
  plus `tp1`/`tp2`/`partial_frac`/`lots`
- **decision** — `approved` / `skipped` / `approved_pending` / `expired`,
  `skip_reason` (1–6, see below), `edited` (operator changed a level — the
  authoritative flag, not inferred from prices), `is_pending`, `decision_ms`,
  `posid`, `tp1_done`
- **outcome** — `exit_time`, `exit_price`, `pnl`, `r_multiple` (blended,
  volume-weighted R, filled when the position fully closes)

`r_multiple` = realised move ÷ initial risk (fill→SL) distance, blended across
up to two closing deals. These journals are the input to
[`review_session.py`](tools-guide.md) (the AI-coaching report card).

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
harness builds them in `OnInit` per the `InpUse*` flags — the overlays, dialog,
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

**Confirmed empirically (probe `mql5/experts/LineAnimProbe.mq5`, since removed):**
- **The chart repaints while `OnTick` is blocked.** A probe that moved an
  `OBJ_HLINE` + `ChartRedraw(0)` in a loop inside one held tick showed the line
  sliding smoothly in the visual tester. This is what makes the live-updating
  editable dialog possible: the EA holds the bar in a `while`-loop and MQL
  repositions the entry/SL/TP lines between keystrokes.

**Needs the live demo to confirm (report back after the first run):**
- **Keystrokes land in the SL/TP edit boxes.** The editable dialog is pumped by
  `TD_Poll` (`PeekMessage`/`IsDialogMessageW` burst) rather than a blocking
  `GetMessage` loop. Chart repaint under a blocked `OnTick` is proven; keyboard
  delivery to a child `EDIT` control through the burst pump is not. **Check: can
  you type into the Stop Loss / Take Profit boxes and does the number change?**
  If not, the fix is a `GetMessage`-based first-drain in `TD_Poll`.
- That editing SL / TP / entry moves only that line, re-sizes lots + recomputes
  R:R live, and that Accept greys out on bad geometry or R:R below the floor.
- Exact placement of the tester's per-run "Allow DLL imports" control varies by
  MT5 build — confirm where it is on this terminal.
- That the dialog genuinely halts tick replay until answered (expected, but
  worth eyeballing on the first signal).
