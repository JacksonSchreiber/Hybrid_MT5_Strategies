# API Reference

Exact interfaces, signatures, and contracts, read from source. Every claim
below cites the file it came from — if this doc and the code ever disagree,
**trust the code** and file a correction here.

Contents: [ISignalDetector](#isignaldetector--adding-a-detector) ·
[SignalCandidate](#signalcandidate-struct) ·
[TickImport.mqh](#tickimportmqh-public-functions) ·
[TradeDialog.dll](#tradedialogdll) ·
[AutoImport contract](#autoimport-contract) ·
[HybridForwardTest inputs](#hybridforwardtest-ea-inputs) ·
[Journal CSV](#journal-csv-schema) ·
[Priority arbitration](#priority-arbitration-rule)

---

## ISignalDetector + adding a detector

Source: `mql5/include/Hybrid/Signal.mqh`.

```mql5
interface ISignalDetector
  {
   bool   Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out);
   string Name(void);
  };
```

That is the **entire** interface — two methods, no `OnInit` hook, no
lifecycle callbacks. The harness (`HybridForwardTest.mq5`) talks to
detectors only through this interface and the `SignalCandidate` struct; a
new detector requires **no harness changes** beyond registering it (below).

### Contract

- **`Detect()` is called once per new closed bar**, not once per tick. The
  harness (`HybridForwardTest.mq5 :: OnTick()`) gates this: it compares
  `iTime(_Symbol,g_tf,0)` against a stored `g_last_bar` and only calls all
  detectors' `Detect()` when a fresh H4 bar has opened. At that instant,
  under MQL5's as-series convention, **index 0 is the just-opened (still
  forming) bar and index 1 is the bar that just closed.**
- **Closed-bar only.** No detector may use index 0 (the forming bar) to
  decide a state transition. Every real detector additionally keeps its own
  `m_last_bar` guard as defence in depth so a transition never runs twice on
  one bar (see `datetime m_last_bar` + the `if(b1<=0 || b1==m_last_bar)
  return false;` guard at the top of every `Detect()` in
  `mql5/include/Hybrid/detectors/*.mqh`).
- **Symbol-keyed, lazily-created indicator handles.** There is no `OnInit`
  hook on the interface, and the detector object is constructed (`new
  C...()`) before any symbol context is known. Every real detector therefore
  keeps handle members (e.g. `int m_hATR`) plus `bool m_init` / `string
  m_sym`, and calls a private `EnsureHandles(symbol, tf)` at the top of
  `Detect()` that only (re)creates handles when `!m_init || sym != m_sym`.
  Handles are never released per-call; MT5 frees them when the EA unloads.
- **`Detect()` returning `true` means "alert the human now, and if approved,
  fill at MARKET."** The harness has no resting/pending-order stage — on
  approval it calls `g_trade.Buy/Sell(lots,_Symbol,0.0,sl,tp,...)` (price
  `0.0` = market). Consequently every detector must set `cand.entry` to the
  **expected market fill at the instant `Detect` returns true** —
  `SymbolInfoDouble(sym,SYMBOL_ASK)` for a long, `SYMBOL_BID` for a short
  (the shared helper `DC_Fill(sym,dir,fallback)` in `DetectorCommon.mqh`
  does exactly this, falling back to `close[1]` if the tick price is 0) —
  and compute `sl`/`tp`/`rr` against that same price. A detector whose spec
  calls for a resting limit order (e.g. SMC's order-block pullback) must
  hold its own internal "armed, waiting for price to reach the intended
  entry" state and only call `Detect()` → `return true` once price has
  actually reached it, so the market fill ≈ the intended limit price.
- **One order, one live TP.** The harness places a single order with one
  `tp`. `tp1`/`tp2`/`partial_fraction` describe an advisory scale-out plan
  that `HandleSignal`/`ManageOpenPositions` **does** execute (bank
  `partial_fraction` at `tp1`, move SL to breakeven, run the rest to `tp2`)
  — see [Priority arbitration](#priority-arbitration-rule) and
  `mql5/experts/HybridForwardTest.mq5 :: ManageOpenPositions()`. The single
  field `cand.tp` must be set to whichever target the detector wants the
  order's native TP to be (the runner/`tp2` for two-target strategies; see
  each detector's `Emit()`/tail of `Detect()`).
- **Every field must be reset.** MQL5 does not reliably zero-initialize a
  locally-declared struct with string members (`aux_count` can hold stack
  garbage). Every detector must call `ResetCandidate(out)` (from
  `mql5/include/Hybrid/detectors/DetectorCommon.mqh`) at the very top of
  `Detect()`, before any early `return false`.
- **One setup per symbol, self-muted internally.** Each detector tracks its
  own `m_state` (`IDLE`/`FORMING`/`ARMED` or similar) and must not start a
  new `FORMING` while mid-setup. Cross-strategy muting (don't let a
  lower-priority strategy fire while a position from any strategy is open)
  is the harness's job (`HasOpenPosition()` in `HybridForwardTest.mq5`), not
  the detector's.

### Concrete steps to add a new detector

Using an existing detector as the template (`CEma20MeanRev` in
`mql5/include/Hybrid/detectors/EmaDetector.mqh` is the simplest of the
three — start there):

1. Create `mql5/include/Hybrid/detectors/YourDetector.mqh`, guarded with an
   `#ifndef`/`#define` header, `#include <Hybrid\detectors\DetectorCommon.mqh>`.
2. Declare `class CYourDetector : public ISignalDetector` with private
   parameter members (constants or ctor args, following the pattern in any
   `impl-*.md` file if you're implementing a documented spec), handle
   members (`int m_hATR;` etc.), `bool m_init; string m_sym; datetime
   m_last_bar;`, and whatever persistent forming-state struct/fields your
   strategy needs.
3. Implement `string Name(void)` returning the strategy's short name (this
   string is shown in the dialog, the journal `strategy` column, and used by
   `pipeline/mt5_verify.sh` for per-strategy counts — keep it short and
   unique, e.g. `"SweepMSS"`, `"DeepFib"`, `"EMArev"`).
4. Implement `bool Detect(const string symbol,ENUM_TIMEFRAMES tf,
   SignalCandidate &out)`:
   - `ResetCandidate(out);` first.
   - New-bar guard: `datetime b1=iTime(symbol,tf,1); if(b1<=0 ||
     b1==m_last_bar) return false; m_last_bar=b1;`.
   - `EnsureHandles(symbol,tf)` (private helper you write, following the
     lazy-create-once pattern above).
   - `CopyRates`/`CopyBuffer` your indicators, as-series
     (`ArraySetAsSeries(arr,true)`), always checking the returned count
     against what you requested before trusting the data.
   - Run your state machine on index 1 (last closed bar). On a valid
     trigger, compute `entry` (via `DC_Fill`), `sl`, `tp`/`tp1`/`tp2`, `rr`,
     size via `LotsForRisk(sym,entry,sl,riskpct)` and **skip (do not emit)**
     if the result is below `DC_VolMin(sym)` (never round up — that breaks
     the 1% rule; the harness's own `SizeByRisk` clamps up as a last resort,
     but detectors must pre-empt it).
   - Fill every field of `out` you use (see the field table below), set
     `out.valid=true; out.strategy=Name();`, and `return true`.
5. Register it in `mql5/experts/HybridForwardTest.mq5`:
   - Add an `input bool InpUseYourStrat = true;` and any tunable params as
     new `input` variables (follow the `InpSmc*`/`InpFib*`/`InpEma*`
     pattern).
   - `#include <Hybrid\detectors\YourDetector.mqh>` at the top.
   - In `OnInit()`, extend `ISignalDetector *g_detectors[3]` to `[4]` (or
     however many total) and append `if(InpUseYourStrat)
     g_detectors[g_ndet++]=new CYourDetector(...);` — **array order is
     priority order** (see [priority arbitration](#priority-arbitration-rule)),
     so insert it wherever it should rank against SMC/Fib/EMA.
   - No other harness change is required — `DrawOverlays`, `AskApproval`,
     `SizeByRisk`, `ManageOpenPositions`, and `WriteJournal` are all
     strategy-agnostic and already read the full `SignalCandidate` struct.
6. Recompile: `./pipeline/stage_csv_for_import.sh --compile` (see
   [tools-guide.md](tools-guide.md#compiling-mql5-headlessly)).

---

## SignalCandidate struct

Source: `mql5/include/Hybrid/Signal.mqh` (base fields + Phase-2 extension,
in one struct — the extension was added in task #14, documented separately
in `docs/strategies/detectors-implementation.md`). A freshly declared
`SignalCandidate` is not reliably auto-zeroed by MQL5 (string members can
carry stack garbage) — every detector calls `ResetCandidate()` from
`DetectorCommon.mqh` before filling it.

### Base fields

| Field | Type | Meaning | Who sets it | How the harness uses it |
|---|---|---|---|---|
| `valid` | `bool` | `false` = no signal this bar | Detector | Harness only acts if `true` |
| `strategy` | `string` | Detector name, e.g. `"SweepMSS"` | Detector (`Name()`) | Shown in dialog, journal `strategy` column, matched by `mt5_verify.sh` |
| `direction` | `int` | `+1` = buy, `-1` = sell | Detector | Drives `Buy`/`Sell`, overlay colours (BUY green / SELL red) |
| `entry` | `double` | Proposed entry price (the expected market fill — see contract above) | Detector | Basis for `SizeByRisk`. **Independently editable**: left at market → market fill; moved away → a pending order at that price (SL/TP stay put, R:R + lots recompute) |
| `sl` | `double` | Stop-loss price | Detector | Order SL; risk basis for sizing. **Independently editable** — changes only SL, re-sizes lots, recomputes R:R (TP unmoved) |
| `tp` | `double` | Take-profit price (single live target — the runner for two-target strategies) | Detector | Order TP unless `two_target` (then `HandleSignal` uses `tp2`). **Independently editable** — changes only TP, recomputes R:R (SL unmoved) |
| `rr` | `double` | Reward:risk ratio | Detector | A live **output** of the levels, shown in the dialog + journal; recomputed into `cand.rr` on Accept. Accept is blocked while it is below the strategy floor |
| `zone_from` | `datetime` | Setup-zone left edge (bar time) | Detector | `OBJ_RECTANGLE` left edge in `DrawOverlays` |
| `zone_to` | `datetime` | Setup-zone right edge (bar time) | Detector | `OBJ_RECTANGLE` right edge; also the x-anchor for aux labels and the corner label |
| `zone_hi` | `double` | Setup-zone top price | Detector | Rectangle top |
| `zone_lo` | `double` | Setup-zone bottom price | Detector | Rectangle bottom |

### Phase-2 extension fields

| Field | Type | Meaning | Who sets it | How the harness uses it |
|---|---|---|---|---|
| `tp1` | `double` | First/partial target price (`0` = unused) | Detector | If `>0` and `partial_fraction>0`: banked via `PositionClosePartial` in `ManageOpenPositions` when price reaches it. **Zeroed if the operator edits SL/TP** (the plan is anchored to the original levels → collapses to single target) |
| `tp2` | `double` | Runner target price (`0` = unused) | Detector | If two-target, this becomes the **order's actual TP** (`order_tp` in `HandleSignal`) and the remainder's TP after the partial. **Zeroed on an edit** (single target at the edited `tp`) |
| `partial_fraction` | `double` | Fraction of lots to bank at `tp1`, e.g. `0.5` (`0` = single target) | Detector | `HandleSignal` computes `two_target = (partial_fraction>0.0 && tp1>0.0 && tp2>0.0)`; if the split would leave either leg below `SYMBOL_VOLUME_MIN`, the harness silently falls back to single-target and logs a note. **Zeroed on an edit** |
| `d1_context` | `bool` | `true` = D1-timeframe aligned/confluent | Detector | Shown in dialog (`[D1 aligned]` suffix) and overlay label — **a flag, never a hard filter** (PM decision 1) — except EMA's D1 fresh-breakout check, which *is* a hard filter internally before emit, so `d1_context` there means "regime clean" |
| `comment` | `string` | Human-readable one-liner, e.g. `"stretch 2.3 ATR, ADX 22 +pool"` | Detector | Appended to the dialog's strategy line and the chart corner label |
| `aux_count` | `int` | Number of aux overlay levels used (≤ 8) | Detector (via `DC_AddAux`) | Loop bound in `DrawOverlays` |
| `aux_price[8]` | `double[8]` | Horizontal price levels (fib grid, EMA, band, swept pool, ref swing, MSS, etc.) | Detector | One `OBJ_HLINE` (dashed grey) per used slot |
| `aux_label[8]` | `string[8]` | Label per aux level | Detector | One `OBJ_TEXT` per used slot |
| `zone2_hi`, `zone2_lo` | `double` | Optional 2nd rectangle (FVG overlap / band); `0` = unused | Detector | Second, darker-filled `OBJ_RECTANGLE` if both `>0` |
| `leg_t0`, `leg_t1` | `datetime` | Impulse-leg / structure trendline endpoints (time); `0` = unused | Detector (Fib only, currently) | `OBJ_TREND` line if both `>0` |
| `leg_p0`, `leg_p1` | `double` | Impulse-leg / structure trendline endpoints (price) | Detector | Paired with `leg_t0`/`leg_t1` |

`DC_AddAux(SignalCandidate &c, double price, string label)` (in
`DetectorCommon.mqh`) is the safe way to append an aux level — it bounds
`aux_count` to 8 and no-ops past that.

---

## TickImport.mqh public functions

Source: `mql5/include/Hybrid/TickImport.mqh`. Shared by `ImportTicks.mq5`
(manual) and `AutoImport.mq5` (unattended) — **do not fork the loader**;
change it here and both callers pick it up.

### `RunTickImport` — the entry point

```mql5
bool RunTickImport(const string base, TickImportResult &res,
                   const string group="Dukascopy", const string originSuffix=".sim",
                   const string customSuffix=".dk", const int batchSize=1000000,
                   const int progressEveryN=20, const long maxBadLines=200,
                   const bool deleteIfExists=true, const bool buildM1=true)
```

Imports `MQL5\Files\import\<base>.csv` (no header, 3 columns:
`yyyy.MM.dd HH:mm:ss.mmm,bid,ask`, UTC) into custom symbol
`<base><customSuffix>`. Returns `res.ok`.

- Creates the custom symbol via `CustomSymbolCreate`, cloning specs from
  `<base><originSuffix>` if that origin exists (e.g. `EURUSD.sim` on the real
  FTMO terminal), otherwise inferring digits/contract size/tick value from
  the CSV and the base name (see `TI_SetInferredSpecs`).
- Streams the CSV line-by-line (`FILE_TXT`, own `StringSplit(',')` — not
  `FILE_CSV`, deliberately, to avoid ambiguity with the space inside the
  datetime field), buffering up to `batchSize` ticks before calling
  `CustomTicksAdd` (append-only bulk loader; the CSV is strictly
  time-ascending so this is safe — see `docs/mt5-import.md` for why
  `CustomTicksReplace` was rejected).
- If `buildM1=true`, aggregates M1 OHLC bars from the tick stream (bid-based)
  in parallel and writes them once via `CustomRatesUpdate` at the end.
- Calls `TI_ApplySessions()` on the newly created symbol (always — this is
  not optional / not gated by an argument) so tester orders can fill.
- Malformed lines (wrong column count, unparseable datetime, non-positive
  bid/ask) are counted; if the count exceeds `maxBadLines`, the whole import
  aborts with `res.err="malformed lines exceeded tolerance"`.
- On success, populates `res.ticks`, `res.bad`, `res.first`/`res.last`
  (`"yyyy.MM.dd HH:mm:ss.mmm"` UTC strings), `res.m1`/`res.h4`/`res.d1` (bar
  counts via `TI_SyncedBars`), `res.seconds`, and sets `res.ok=true`.

```mql5
struct TickImportResult
  {
   bool     ok;
   long     ticks;
   long     bad;
   string   first;     // "yyyy.MM.dd HH:mm:ss.mmm" (UTC)
   string   last;
   long     m1;
   long     h4;
   long     d1;
   double   seconds;
   string   err;       // populated on failure
  };
```

### Session-setting functions

```mql5
void TI_ApplySessions(const string sym, const string base);
bool TI_SessionsOnly(const string base, const string customSuffix=".dk");
```

- **`TI_ApplySessions(sym, base)`** — defines trading + quote sessions on an
  already-created custom symbol so the Strategy Tester will fill orders.
  Custom symbols created with no cloned origin have **no sessions by
  default**, which makes every order fail with `10018 market closed`. Sets,
  per UTC weekday: Sunday `20:00–24:00`, Monday–Friday `00:00–24:00`,
  Saturday none, for FX/metals/indices/oil; all 7 days full for crypto
  (detected via `StringFind(base,"BTC")>=0 || StringFind(base,"ETH")>=0`).
  **Source note:** the code's own header comment above this function says
  "Sun 22:00-24:00", but the actual constant is `sun_from=20*3600` (20:00) —
  the 20:00 figure above is what the code does; the comment is stale.
  Also forces `SYMBOL_TRADE_MODE_FULL`, `SYMBOL_TRADE_EXECUTION_MARKET`, and
  `SYMBOL_FILLING_FOK+SYMBOL_FILLING_IOC` so `CTrade` can execute. Called
  unconditionally at the end of `TI_SetupSymbol` (i.e. every fresh import
  gets sessions automatically) — **not** a separate opt-in step.
- **`TI_SessionsOnly(base, customSuffix)`** — re-patches sessions on an
  **already-imported** symbol without touching its ticks. Returns `false` if
  the symbol doesn't exist. This is what a `SESSIONS <base>` job line in
  `jobs.txt` invokes (see [AutoImport contract](#autoimport-contract)) and
  what `pipeline/mt5_import.sh --sessions <BASE>...` drives from WSL — use
  it to re-apply the session fix to symbols imported before this feature
  existed, without re-importing 100M+ ticks.

### Other public helpers (used internally, occasionally useful standalone)

| Function | Signature | Purpose |
|---|---|---|
| `TI_DecimalsOf` | `int TI_DecimalsOf(const string s)` | Counts decimal digits after the first `.` in a numeric string (used to infer `SYMBOL_DIGITS`) |
| `TI_ParseDateTimeMsc` | `bool TI_ParseDateTimeMsc(const string dt, datetime &sec_out, long &msc_out)` | Parses `"yyyy.MM.dd HH:mm:ss.mmm"` into seconds + millisecond-since-epoch (`StringToTime` doesn't handle the `.mmm` part, so it's split manually) |
| `TI_MscToStr` | `string TI_MscToStr(long msc)` | Inverse of the above, for logging/printing |
| `TI_SyncedBars` | `long TI_SyncedBars(const string sym, ENUM_TIMEFRAMES tf, int max_wait_ms=15000)` | Waits (polling `SERIES_SYNCHRONIZED`, up to `max_wait_ms`) for a timeseries to sync, then returns `MathMax(SERIES_BARS_COUNT, iBars(...))` |
| `TI_SetInferredSpecs` | `bool TI_SetInferredSpecs(const string csvpath)` | Infers digits/contract size/tick value/currency legs from the base name + first CSV line, for symbols with no `.sim` origin to clone |
| `TI_SetupSymbol` | `bool TI_SetupSymbol(const string csvpath)` | Creates/recreates the custom symbol (clone-or-infer), selects it, applies sessions |
| `TI_FlushTicks` | `bool TI_FlushTicks(const MqlTick &ticks[], int count)` | Wraps `CustomTicksAdd` with error handling |

---

## TradeDialog.dll

Source: `mql5/dll/TradeDialog.c`, built by `mql5/dll/build.sh`.

The dialog is **editable and poll-driven**: Entry, Stop Loss and Take Profit
are all edit boxes and **independent** — editing one never moves another (SL/TP
are structural). Each edit re-sizes lots, recomputes the live **R:R**, and
**moves the real chart lines live** before Accept/Skip; Accept is blocked while
R:R is below the strategy floor. Editing the entry away from market turns Accept
into a **pending order** whose type is shown in the "Order" row (see
[decisions.md](decisions.md) and the [EA inputs](#hybridforwardtest-inputs)
`InpPendingExpiryBars`). Skipping opens a reason picker (1–6).

Why poll-driven and not a single blocking modal: live chart updates need MQL
code to run *between* keystrokes so it can recompute levels and reposition the
H-lines. A one-call modal that runs its own message loop (the old design)
never returns to MQL until the user answers, so the chart is frozen the whole
time. Instead the window is opened once and then **pumped** from a `while`
loop inside `OnTick` — that loop still blocks `OnTick`, so the visual tester
stays held on the bar (the pause is preserved), but MQL runs each turn. (The
chart *does* repaint while `OnTick` is blocked — verified empirically in the
visual tester.)

### Exported symbols and C signatures

Eight exports, all **undecorated names** (verified by `build.sh`'s PE
export-table parser): `TD_Open`, `TD_Poll`, `TD_SetDisplay`, `TD_SkipReason`,
`TD_SetOrderType`, `TD_SetEvents`, `TD_Close`, and a legacy `ShowTradeDialog` stub.

```c
// Create the window (Entry / SL / TP all editable) and return immediately.
// Returns 1 = shown OK, 0 = failure (caller must fail-closed to skip).
int  TD_Open(const wchar_t *title,   const wchar_t *symbol,
             const wchar_t *strategy, const wchar_t *direction,   // "BUY"/"SELL"
             const wchar_t *entry,   const wchar_t *sl,
             const wchar_t *tp,      const wchar_t *lots, const wchar_t *rr);

// Drain pending window messages (bounded PeekMessage pump), read the current
// Entry/SL/TP box text into the out-params, set *dirty=1 if the user changed a
// box since the last poll. Returns 0 = pending, 1 = Accept, 2 = Skip/closed.
// Digit keys 1-6 (when focus is NOT in a number box) skip with that reason.
int  TD_Poll(double *entry, double *sl, double *tp, int *dirty);

// Push RECOMPUTED, normalised strings back into the boxes (skipping whichever
// box currently has keyboard focus, so it never fights the cursor) + the
// lots/RR statics. ok=0 disables the Accept button (invalid geometry).
void TD_SetDisplay(const wchar_t *entry, const wchar_t *sl, const wchar_t *tp,
                   const wchar_t *lots,  const wchar_t *rr, int ok);

// Skip-reason code (1-6) from the last skip; 6 = bare Skip/Esc/close. Valid
// after TD_Poll returns 2. (0 only if never skipped.)
int  TD_SkipReason(void);

// Set the "Order" row text: "MARKET" or e.g. "BUY LIMIT @ 1.08200". The EA
// computes this from the edited entry vs current market and pushes it live.
void TD_SetOrderType(const wchar_t *s);

// Fill the upcoming-events list with BOTH date forms; the dialog shows one per
// the Coach-mode toggle (absolute normally, relative when redacted).
void TD_SetEvents(const wchar_t *abs_dates, const wchar_t *rel_dates);

// Destroy the window and free per-dialog GDI objects.
void TD_Close(void);

// Legacy single-call modal export — retained as a no-op stub (returns 0) for
// binary compatibility only; the EA drives the poll API above.
int  ShowTradeDialog(const wchar_t *title, ... );   // 9 wchar_t* args, returns 0
```

All numeric math (R:R lock, lot sizing, tick/stops normalisation) lives in
**MQL**, where the broker-spec functions are; the DLL is display + text input
+ message pump only — it never computes a price or a lot size.

### MQL5 `#import`

`mql5/experts/HybridForwardTest.mq5`:

```mql5
#import "TradeDialog.dll"
int  TD_Open(string title,string symbol,string strategy,string direction,
             string entry,string sl,string tp,string lots,string rr);
int  TD_Poll(double &entry,double &sl,double &tp,int &dirty);
void TD_SetDisplay(string entry,string sl,string tp,string lots,string rr,int ok);
void TD_Close(void);
#import
```

MQL5 `string` is UTF-16 internally and marshals directly to `LPCWSTR`
(`wchar_t*`); `double &`/`int &` marshal as output pointers. **`#import` DLLs
are early-bound at EA load** — both `TradeDialog.dll` and `user32.dll` must be
resolvable or the EA fails to load entirely, regardless of the
`InpUseColoredDialog` input (that input only picks which path is *called*, not
which DLL is *loaded*). The DLL must live in the terminal's `MQL5\Libraries\`
folder (not `MQL5\Files\`) — `build.sh` deploys it there. If the DLL is
missing/unresolved the colour path fails-closed to **Skip** with a printed
error (it does not silently reject setups).

### EA-side interactive loop (`InteractiveDialog` in `HybridForwardTest.mq5`)

**Edit model: levels are INDEPENDENT; R:R is a live output + floor guard, not a
lock** (SL and TP are structural per the strategy specs — editing one must not
move the other). `floor = StratMinRR(strategy)` (`InpSmcMinRR`/`InpFibMinRR`/
`InpEmaMinRR`).

1. `TD_Open(...)`, then loop `TD_Poll`:
   - **SL edited** → only SL moves (Entry, TP fixed). **TP edited** → only TP
     moves. **Entry edited** → only Entry moves (SL/TP fixed; away from market
     ⇒ a pending order). No cross-recompute.
   - Normalise to tick size + digits (`NormPrice`), validate geometry
     (`ValidGeom`: SL<Entry<TP for BUY, inverted for SELL, each leg ≥ a tick
     and ≥ broker stops-level).
   - If geometry valid: re-size lots (`SizeByRisk` on the new stop distance),
     compute `rr = RRatio(entry,sl,tp)`, move the `HFT_<id>_entry/sl/tp` H-lines
     (`ObjectSetDouble(...,OBJPROP_PRICE,...)`), `ChartRedraw(0)`, and push back
     via `TD_SetDisplay(..., ok = rr≥floor)` — so **Accept is blocked (and the
     R:R shows `< MIN x.x`) whenever R:R is below the strategy floor**. If
     geometry is invalid (levels crossed): keep last-good, `ok=0` once.
3. On **Accept**, commit the (possibly edited) levels into `cand`. If anything
   was edited, the two-target TP1/TP2 partial plan (anchored to the *original*
   levels) is **collapsed to a single target at the edited TP**, and lots are
   re-sized on the final risk distance so the placed order + journal match what
   was approved. On **Skip**, `cand` is left unchanged.

### The MessageBoxW fallback

```mql5
#import "user32.dll"
int MessageBoxW(long hWnd,string lpText,string lpCaption,uint uType);
#import
#define MB_YESNO        0x00000004
#define MB_ICONQUESTION 0x00000020
#define MB_SYSTEMMODAL  0x00001000
#define IDYES           6
#define IDNO            7
```

Used instead of the colour path when `InpUseColoredDialog=false`
(`AskApproval()` in `HybridForwardTest.mq5`); `hWnd=0` + `MB_SYSTEMMODAL`
blocks the tester thread. **No colour and no editing** — plain read-only
Yes/No text body built with `StringFormat`. This path exists only as a
degraded fallback; the editable feature is colour-path only.

### Behaviour, colours, return codes

- `TD_Open` creates a centred, top-most (`WS_EX_TOPMOST`) popup
  (`WS_POPUP|WS_CAPTION|WS_SYSMENU`) and **returns immediately**. It does *not*
  run its own message loop; the EA pumps it via `TD_Poll` (bounded
  `PeekMessage`/`IsDialogMessageW` drain, ≤256 msgs/call) from a `while` loop
  in `OnTick`. That MQL loop is what holds the tester on the bar.
- **Editable fields:** Entry, Stop Loss and Take Profit are all `EDIT` controls
  (white background, `WS_BORDER`), coloured green/red/blue to match their chart
  lines. Below R:R, a read-only **"Order" row** shows what Accept will place
  (`MARKET` or e.g. `BUY LIMIT @ 1.08200`), set live by the EA via
  `TD_SetOrderType`.
- **Dirty tracking:** an `EN_CHANGE` from an edit box sets a `dirty` flag read
  by `TD_Poll`. Writes the EA makes via `TD_SetDisplay` are wrapped in a
  `suppress` guard so they don't self-trigger dirty, and `TD_SetDisplay` skips
  any box that currently holds keyboard focus so it never stomps the cursor.
- **Skip reasons:** digit keys `1`–`6` (only when focus is *not* in a number
  box — there they're digits) skip immediately with that reason code; the bare
  Skip button / Esc / close give `6` (other). Read via `TD_SkipReason`.
- **Re-entrancy guard:** `InterlockedCompareExchange` on a static `g_inuse`
  flag set in `TD_Open`, cleared in `TD_Close` — a second concurrent `TD_Open`
  returns `0` without showing a window.
- **Colour palette** (via the `RGB()` macro, correct COLORREF byte order):

  | Field | Colour | Matches |
  |---|---|---|
  | Entry (edit) | `RGB(0,160,0)` green | chart entry H-line |
  | Stop Loss (edit) | `RGB(204,0,0)` red | chart SL H-line |
  | Take Profit (edit) | `RGB(0,0,204)` blue | chart TP H-line |
  | Direction | BUY green / SELL red (same greens/reds as above) | — |
  | Symbol, Strategy, Lots, R:R | `RGB(20,20,20)` near-black (`COL_VALUE`) | neutral |
  | Field captions | `RGB(90,90,90)` grey (`COL_LABEL`) | — |
  | Edit-box background | `RGB(255,255,255)` white (`COL_EDITBG`) | — |
  | Dialog background | `RGB(248,248,248)` (`COL_BG`) | — |

- **Buttons:** "Accept" (`ID_YES=100`) and "Skip" (`ID_NO=101`). **Skip is the
  default-focused button** (`SetFocus(g.hNo)`), so a reflexive Enter skips
  rather than trades. Accept is disabled (`EnableWindow`) whenever the EA
  reports invalid geometry via `TD_SetDisplay(...,ok=0)`.
- **Keyboard:** `Enter` → Accept (only when currently valid), `Esc` → Skip.
  `[X]`/Alt+F4 (`WM_CLOSE`) → Skip. Typing routes to the focused SL/TP box via
  `IsDialogMessageW`.
- **`TD_Poll` return codes:** `0` = pending (keep looping), `1` = Accept,
  `2` = Skip (covers Skip-click, Esc, `[X]`, and the not-open case — Skip is
  always the fail-safe outcome).

### Build & deploy

```sh
./mql5/dll/build.sh              # zig cross-compile (x86_64-windows-gnu) + verify exports + deploy
./mql5/dll/build.sh --no-deploy   # build + verify only, skip copying to MQL5\Libraries\
```

Compiler: `/home/jack/tools/zig-x86_64-linux-0.16.0/zig cc -target
x86_64-windows-gnu -shared -O2`, linking `user32`/`gdi32`/`kernel32`. The
built `.dll`/`.lib`/`.pdb` are git-ignored (deployed, not committed) —
rebuild from `TradeDialog.c` any time.

---

## AutoImport contract

Source: `mql5/experts/AutoImport.mq5`, driven from WSL by
`pipeline/mt5_import.sh`.

### `jobs.txt` (input, written by the driver, consumed/deleted by the EA)

Path: `MQL5\Files\import\jobs.txt`. One entry per line; blank lines and `#`
comments are ignored. Two line forms:

```
AUDUSD
NZDUSD
SESSIONS EURUSD
```

- A bare `<BASE>` line → full tick import via `RunTickImport(job)` (all
  defaults: `.dk` suffix, `Dukascopy` group, `.sim` origin).
- A `SESSIONS <BASE>` line (prefix match on `"SESSIONS "`) → **re-patch
  sessions only**, via `TI_SessionsOnly(base)` — no CSV read, no ticks
  touched. `pipeline/mt5_import.sh --sessions <BASE>...` writes only this
  form.

**The EA deletes `jobs.txt` as its very first action** (before doing any
work) — this is the re-trigger safety: if a later *normal* (non-import)
launch of the terminal happens to load an AutoImport chart from the saved
profile, it finds no `jobs.txt`, does nothing, and — critically — does
**not** call `TerminalClose`, so it can never surprise-close a terminal the
user is actively using.

### `import_status.txt` (output, written by the EA, one line per job)

Path: `MQL5\Files\import\import_status.txt`. Opened in `FILE_WRITE` mode
(truncated) at the start of the batch; `FileFlush`-ed after every line so a
crash mid-batch keeps all completed rows.

```
# SYMBOL,STATUS,ticks,first,last,seconds
AUDUSD,OK,132896012,2020.01.01 22:00:10.013,2026.07.16 23:59:57.120,162.7
EURUSD,OK,0,,,sessions
BADSYM,FAIL,0,,,CSV not staged: MQL5\Files\import\BADSYM.csv
# DONE 1 ok, 0 fail, 162.7 s
```

- Header comment line first, then one data line per job, in job order.
- Columns: `SYMBOL, STATUS, ticks, first(UTC), last(UTC), seconds_or_error`.
  - `STATUS` is `OK` or `FAIL` for both a normal import job and a `SESSIONS`
    job — the code emits `StringFormat("%s,%s,0,,,sessions\n",base,(ok?"OK":"FAIL"))`
    for a sessions job, so `ticks`/`first`/`last` are always empty/`0` and
    the literal word `sessions` fills the 6th column (`EURUSD,OK,0,,,sessions`
    above is a verbatim example of that line, not illustrative).
  - On a normal import `FAIL`, `ticks=0`, `first`/`last` are empty, and the 6th
    column carries the error string (e.g. from `res.err`).
  - `first`/`last` contain an internal space (`"yyyy.MM.dd HH:mm:ss.mmm"`)
    but no comma, so splitting the line on `,` is safe.
- Trailing `# DONE <ok> ok, <fail> fail, <seconds> s` line marks batch
  completion — `pipeline/mt5_import.sh` polls for this marker (or terminal
  exit) to know the run finished.
- A caller (e.g. `pipeline/rolling_import.sh`) should treat a symbol as
  successfully imported iff a line matching `^<SYMBOL>,OK,` exists — this is
  exactly what `rolling_import.sh :: import_one()` does via `grep`.

### `terminal64 /config` startup-ini invocation

`pipeline/mt5_import.sh` writes a one-shot startup config ini and launches
the terminal **without `/portable`** (a plain launch uses the real
`EE0304…` AppData data folder that already holds the `.dk` symbols and
staged CSVs — `/portable` would wrongly point at the install directory):

```ini
; Auto-generated by pipeline/mt5_import.sh - one-shot unattended import.
[StartUp]
Expert=AutoImport
Symbol=EURUSD.dk
Period=H4
```

```sh
"/mnt/c/Program Files/OANDA MetaTrader 5/terminal64.exe" "/config:C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB\autoimport_startup.ini"
```

`[StartUp] Symbol` must be a symbol that **already exists** at launch time
(a chart has to open for the EA to attach to it) — `EURUSD.dk` is used
because it was the first symbol imported; override with `--host-symbol` if
it's ever absent. The imported jobs themselves are unrelated to this host
symbol. The EA does its work from the **first `OnTimer` tick** (not
`OnInit` — see the comment block at the top of `AutoImport.mq5`: a
multi-minute blocking `OnInit` would stall the background M1 series-sync
`RunTickImport` depends on), then calls `TerminalClose(0)` when
`AutoShutdown` (default `true`) is set and the batch is done.

---

## HybridForwardTest EA inputs

Source: `mql5/experts/HybridForwardTest.mq5`, top-of-file `input`
declarations.

| Input | Type | Default | Meaning |
|---|---|---|---|
| `InpRiskPct` | `double` | `0.01` | Risk per trade as a fraction of equity (1%) |
| `InpMagic` | `long` | `990217` | Magic number for orders/positions this EA owns |
| `InpDeviation` | `int` | `50` | Max slippage, in points |
| `InpCleanupOnDeinit` | `bool` | `false` | If `true`, deletes all `InpObjPrefix`-prefixed chart objects on EA removal |
| `InpObjPrefix` | `string` | `"HFT_"` | Chart-object name prefix (objects are named `HFT_<signal_id>_*`) |
| `InpUseColoredDialog` | `bool` | `true` | `true` = `TradeDialog.dll` (coloured); `false` = `MessageBoxW` fallback (plain) |
| `InpAutoApprove` | `enum ENUM_AUTO_APPROVE` (`AA_NONE=0`/`AA_ALL=1`/`AA_SKIP=2`) | `AA_NONE` | **Tester-only.** `AA_NONE` = interactive editable dialog (needs visual mode + DLLs). `AA_ALL` = auto-approve every signal at the detector's original levels, no dialog — exercises the full order/scale-out/journal lifecycle. `AA_SKIP` = auto-deny every signal, no dialog — cleanest per-strategy signal counts (no position ever suppresses detection) |
| `InpUseSMC` | `bool` | `true` | Enable Strategy 1 (Liquidity Sweep + MSS), priority 1 |
| `InpUseFib` | `bool` | `true` | Enable Strategy 2 (Deep Fib Retracement), priority 2 |
| `InpUseEMA` | `bool` | `true` | Enable Strategy 3 (20 EMA Mean Reversion), priority 3 |
| `InpSmcMinRR` | `double` | `2.0` | SMC: minimum reward:risk to arm |
| `InpSmcTpR` | `double` | `3.0` | SMC: fallback fixed R target when no opposing liquidity clears `min_rr` |
| `InpFibImpulseATR` | `double` | `2.0` | Fib: minimum impulse-leg size, in ATR |
| `InpFibMinRR` | `double` | `2.0` | Fib: minimum reward:risk to arm |
| `InpEmaStretch` | `double` | `2.0` | EMA: minimum close-to-EMA stretch, in ATR, to qualify |
| `InpEmaAdxCeil` | `double` | `30.0` | EMA: skip if ADX ≥ this (too trendy) |
| `InpEmaMinRR` | `double` | `1.3` | EMA: minimum blended reward:risk to arm |
| `InpPendingExpiryBars` | `int` | `3` | Unfilled pending order (edited entry) auto-cancels after this many H4 bars, journalled `expired` |
| `InpShotOnDecision` | `bool` | `true` | Save a chart screenshot to `MQL5\Files\journal\shots\<stem>_<id>.png` when the dialog opens |
| `InpShotW` / `InpShotH` | `int` | `1600` / `900` | Screenshot dimensions (px) |
| `InpForcePendingTest` | `bool` | `false` | **Test-only.** Under `AA_ALL`, place a forced STOP pending instead of a market order — verifies the `OnTradeTransaction` pending-fill binding headlessly (`mt5_verify.sh --force-pending`) |

> Display/overlay inputs (`InpShow*`, `InpFontSize`, `InpLineWidth`, event/
> imbalance/declutter knobs) are documented in
> [tester-harness.md](tester-harness.md).

## Priority arbitration rule

Source: `HybridForwardTest.mq5 :: OnTick()`.

```mql5
SignalCandidate best; bool have=false;
for(int i=0;i<g_ndet;i++)
  {
   SignalCandidate c;
   bool v=g_detectors[i].Detect(_Symbol,g_tf,c);
   if(v && c.valid && !have) { best=c; have=true; }
  }
```

- **Every enabled detector's `Detect()` is called on every new H4 bar**,
  regardless of arbitration outcome — this is required so each detector's
  internal state machine keeps advancing even on bars where it doesn't win.
- `g_detectors[]` is populated in `OnInit()` in a **fixed priority order**:
  index 0 = SMC (if `InpUseSMC`), then Fib (if `InpUseFib`), then EMA (if
  `InpUseEMA`) — i.e. **SMC > Fib > EMA**. The loop keeps the **first**
  (`!have`) valid emit it sees in that order, so on a bar where two
  detectors both emit, the higher-priority one wins and the other's signal
  is discarded for that bar (it is never journalled or shown).
- **One active setup per symbol, across all strategies:** if
  `HasOpenPosition()` (a position open under `InpMagic` on `_Symbol`)
  returns `true`, a new winning signal is suppressed entirely (logged, not
  journalled) rather than presented — `HybridForwardTest.mq5` enforces this
  centrally; individual detectors additionally self-mute their own internal
  state but cannot see each other's.
- Portfolio-level guards (3% concurrent-risk cap, 3%/4% daily new-risk caps
  described in `docs/strategies/README.md`) are **not implemented** in the
  Phase-2 harness — they are explicitly deferred to a future Phase-3
  orchestrator (`docs/strategies/impl-README.md`, PM item 3).

## Journal CSV schema

Source: `HybridForwardTest.mq5 :: WriteJournal()`. Written with
`FILE_COMMON` so it lands in a stable, findable location regardless of
which sandboxed tester agent is running:

```
<Terminal>\Common\Files\journal\<SYMBOL>_<start>_<end>.csv
```

e.g.
`C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\Common\Files\journal\EURUSD.dk_20240101_20240331.csv`.
During the run it is written to a `..._<start>.part.csv` path
(`g_journal_part`); at `OnDeinit` it's rewritten once more to the final
`<start>_<end>.csv` name and the `.part` file is deleted. The whole file is
rewritten and `FileFlush`-ed on every signal and every trade-close event
(`OnTradeTransaction`), so a tester crash never loses more than the
in-flight write.

**Header line (exact, current schema — 26 columns):**

```
signal_id,signal_time,symbol,strategy,direction,orig_entry,orig_sl,orig_tp,entry,sl,tp,tp1,tp2,partial_frac,lots,decision,skip_reason,edited,is_pending,decision_ms,posid,tp1_done,exit_time,exit_price,pnl,r_multiple
```

> This is the current, authoritative schema — trust this page. The
> `orig_*`, `skip_reason`, `is_pending` columns and the `approved_pending`/
> `expired` decision values were added with the editable-entry / coaching
> work (2026-08-01, `decisions.md`); the same change fixed `tp1_done`, which
> previously wrote the literal `(non-string passed)`. The five-column
> `signal_time+strategy` join is what `pipeline/review_session.py` keys on.

| Column | Type/format | Meaning |
|---|---|---|
| `signal_id` | int | Running counter (`g_sig_seq`), 1-based |
| `signal_time` | `TimeToString(...,TIME_DATE\|TIME_SECONDS)` | Signal bar time (UTC) — `cand.zone_to` at emit |
| `symbol` | string | `_Symbol` (the `.dk` custom symbol) |
| `strategy` | string | `cand.strategy` (`SweepMSS`/`DeepFib`/`EMArev`, or a custom detector's `Name()`) |
| `direction` | `"BUY"`/`"SELL"` | From `cand.direction` |
| `orig_entry` | price string | The **detector's proposed** entry, snapshotted before any operator edit (orig-vs-final analysis) |
| `orig_sl` | price string | Detector's proposed SL |
| `orig_tp` | price string | Detector's proposed TP (the single/display target) |
| `entry` | price string | **Realised fill price** if filled (overwritten from the deal — market or pending), else the placed/proposed entry |
| `sl` | price string | **Original** SL — the risk basis; never overwritten by the breakeven move |
| `tp` | price string | The TP actually placed on the order — `tp2` (the runner) for a two-target signal, else `cand.tp` |
| `tp1` | price string or empty | Partial target price (empty if `0`) |
| `tp2` | price string or empty | Runner target price (empty if `0`) |
| `partial_frac` | `%.2f` | Fraction banked at `tp1` (`0.00` if single-target, incl. min-lot fallback and any operator-edited setup) |
| `lots` | `%.2f` | Initial order lots (`SizeByRisk` on the final risk distance) |
| `decision` | `"approved"` / `"skipped"` / `"approved_pending"` / `"expired"` | Operator (or auto-approve) decision. `approved_pending` = edited-entry pending order (whether it later filled or not — check `posid`); `expired` = pending cancelled unfilled after `InpPendingExpiryBars` H4 bars |
| `skip_reason` | int `0`–`6` | Skip-reason code (`0` for non-skips and headless skips): 1 counter-trend, 2 news/event, 3 ugly structure, 4 target obstructed, 5 correlated exposure, 6 gut/other |
| `edited` | `0`/`1` | `1` if the operator changed any level vs the detector's proposal. **Authoritative** — computed from the committed levels *before* `entry` is overwritten by the fill, so downstream analysis must not infer edits from `orig_entry` vs `entry` (the fill ≈ never equals the proposal) |
| `is_pending` | `0`/`1` | `1` if the order originated as a pending (entry edited away from market), even after it fills. Doubles as the "realised entry differs from a market fill" signal for edit-delta analysis |
| `decision_ms` | long | Wall-clock ms taken to decide (`0` for `InpAutoApprove` modes) |
| `posid` | long | `DEAL_POSITION_ID` of the opening deal (`0` if skipped, expired-unfilled, or the order failed) |
| `tp1_done` | `0`/`1` | Whether the partial has been banked + SL moved to BE (always `1` for a single-target signal, since there's nothing to bank) |
| `exit_time` | date string or empty | Set once the position is fully closed |
| `exit_price` | price string or empty | Last closing deal's price |
| `pnl` | `%.2f` or empty | Accumulated profit + swap + commission across all closing deals |
| `r_multiple` | `%.2f` or empty | **Blended, volume-weighted R**: `Σ (deal_volume / initial_lots) × (moved / risk_px)` across the (up to two) closing deals, where `risk_px = |fill − original sl|` (recomputed at the realised fill, incl. pending fills; the breakeven move never changes the risk basis) |

A position is considered fully closed (`closed=true`, `exit_*`/`pnl`/
`r_multiple` finalized) once `closed_vol >= lots - step*0.5` in
`OnTradeTransaction`, i.e. accumulated closing volume matches the original
lots within half a volume step.
