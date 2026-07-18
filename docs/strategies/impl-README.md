# Implementation Blueprints — shared contract & conventions

These four `impl-*.md` files are **MQL5-level build sheets**. The MQL5 dev codes
each detector directly from them with no further design decisions. They target
the exact `ISignalDetector` / `SignalCandidate` contract in
`mql5/include/Hybrid/Signal.mqh` and the harness behaviour in
`mql5/experts/HybridForwardTest.mq5`. Read this file first; each strategy doc
only adds what is specific to it.

- Strategy 1 — [impl-liquidity-sweep-mss.md](impl-liquidity-sweep-mss.md)
- Strategy 2 — [impl-deep-fib-retracement.md](impl-deep-fib-retracement.md)
- Strategy 3 — [impl-ema20-mean-reversion.md](impl-ema20-mean-reversion.md)

Design specs (the "why"): `README.md`, `liquidity-sweep-mss.md`,
`deep-fib-retracement.md`, `ema20-mean-reversion.md`.

---

## 0. How the harness actually calls a detector (read carefully)

The blueprints are constrained by what `HybridForwardTest.mq5` really does. Do
not design against the spec's idealised lifecycle without reading this first.

1. **One call per new closed bar.** `OnTick` fires `Detect()` exactly once when a
   new H4 bar opens (`iTime(sym,tf,0) != g_last_bar`). At that instant **index 0
   is the just-opened (empty) bar and index 1 is the bar that just closed.** All
   detection runs on index ≥ 1. Detectors still keep their own `m_last_bar`
   guard (defence in depth) so a setup transition never runs twice on one bar.

2. **`Detect()` returning `valid=true` == "alert the human NOW and, on approve,
   fill at MARKET".** The harness has **no resting-order / pending-order stage**.
   On approval it calls `g_trade.Buy/Sell(lots,_Symbol,0.0,sl,tp,...)` — the
   `0.0` price means **market**. There is no ARMED-order-waiting-to-fill and
   nothing to cancel after emit. Therefore:
   - The spec state machines (`FORMING → ARMED → TRIGGERED / INVALIDATED`) run
     **entirely inside the detector, before emit.** The detector emits (`return
     true`) at the moment the spec calls **ARMED**, i.e. the setup is ready to
     enter right now.
   - The `INVALIDATED` edges apply **only to the pre-emit internal state** (a
     setup that never became a live position). Once emitted and approved it is a
     live position managed by its SL/TP — there is no order to cancel.

3. **Sizing is driven by `|entry − sl|`** (`SizeByRisk`), but the **fill is
   market at approval**. So the **1% risk constraint (CLAUDE.md, hard) only
   holds if `cand.entry` is the price the harness will actually fill at.**
   **Unifying rule for all three detectors:**
   > Set `cand.entry` = the **expected market fill at the instant Detect fires**
   > — `SymbolInfoDouble(sym,SYMBOL_ASK)` for a long, `SYMBOL_BID` for a short
   > (fall back to `close[1]` if 0, as the dummy does). Compute `sl`, `tp`, `rr`
   > **and the `min_rr` gate** against that **same** entry. Never report a spec
   > "limit" price in `cand.entry` while the harness fills at market — that
   > desyncs sizing from realised risk.

   Consequence for entry semantics:
   - **Fib & EMA** specs already say "market at next-bar open after the
     confirmation close" — that is exactly what the harness does when Detect
     fires on the new bar. Clean map.
   - **SMC** default is a *limit pullback into the order block*. There is no
     resting limit here, so the SMC detector holds an internal ARMED state and
     only emits on a later closed bar **whose price has pulled back into the OB**
     (current market ≈ OB proximal edge). This makes the market fill ≈ the
     intended limit price and keeps risk exact. See impl-1.

4. **One order, one TP.** The harness places a single order with one `tp`. The
   struct is extended (§3) with advisory `tp1/tp2/partial_fraction`, but **true
   scale-out (partial at TP1 + runner to TP2 + move-to-BE) requires a
   `HandleSignal` extension the dev must add** — flagged for the PM below. Until
   then `cand.tp` is the single live target and must be chosen as the **nearest
   target that clears `min_rr`** so the gate can pass (see each doc's TP rule).

   > **Status update (this is now built):** the PM item below was confirmed
   > and the dev extended the harness. `HybridForwardTest.mq5 ::
   > ManageOpenPositions()` now does exactly this — banks `partial_fraction`
   > at `tp1` via `PositionClosePartial`, moves SL to breakeven, and lets the
   > remainder run to `tp2` (the order's actual TP for a two-target signal).
   > See [api-reference.md](../api-reference.md#signalcandidate-struct) and
   > [detectors-implementation.md](detectors-implementation.md). This
   > paragraph and PM item 2 below are left as the original build sheet for
   > history; treat them as superseded by the code.

---

## 1. Shared indicator-handle lifecycle (lazy, symbol-keyed)

`ISignalDetector` exposes **only** `Detect()` and `Name()` — there is **no
OnInit hook**, and the symbol is unknown until `Detect()` is first called
(`g_detector=new C...()` runs before any symbol context). So **create handles
lazily on the first Detect, cache them, reuse.**

```mql5
// members
int  m_hATR;      // iATR handle (trigger TF)
int  m_hEMA;      // iMA  handle (trigger TF) — Fib/EMA only
int  m_hADX;      // iADX handle (trigger TF) — EMA only
int  m_hEMA_D1;   // D1 EMA — Fib D1 bias only
int  m_hADX_D1;   // (unused; D1 breakout uses CopyRates, not iADX)
bool m_init;
string m_sym;

bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
  {
   if(m_init && sym==m_sym) return true;
   // symbol changed or first call: (re)create
   m_sym=sym;
   m_hATR = iATR(sym,tf,ATR_PERIOD);                       // 14
   m_hEMA = iMA (sym,tf,EMA_PERIOD,0,MODE_EMA,PRICE_CLOSE);// per strategy
   m_hADX = iADX(sym,tf,ADX_PERIOD);                       // EMA only
   if(m_hATR==INVALID_HANDLE) return false;
   m_init=true;
   return true;
  }
```

- **`iATR(sym,tf,14)`** — Wilder ATR(14) (README). Buffer 0. Read the value at
  **shift 1** (last closed bar) and **freeze it** into the setup.
- **`iMA(sym,tf,period,0,MODE_EMA,PRICE_CLOSE)`** — EMA on close.
- **`iADX(sym,tf,period)`** — buffer 0 = ADX main, 1 = +DI, 2 = −DI. Use main.
- **Readiness guard** before every `CopyBuffer`: check the handle ≠
  `INVALID_HANDLE` **and** `BarsCalculated(handle) > required_shift`; if not
  ready `return false` (no signal this bar). Never trust a `CopyBuffer` that
  returns < requested count.
- **Do not release handles per call.** They live for the detector's lifetime;
  MT5 frees them at EA unload.

### Copying data (as-series, closed bars only)

```mql5
double atr[]; ArraySetAsSeries(atr,true);
if(CopyBuffer(m_hATR,0,0,3,atr)<3) return false;
double ATR = atr[1];                 // last CLOSED bar's ATR, frozen
if(ATR<=0.0) return false;           // ATR=0 guard (avoids /0 everywhere)

MqlRates r[]; ArraySetAsSeries(r,true);
int need = LOOKBACK + 2*FRACTAL_N + 4;
if(CopyRates(sym,tf,0,need,r) < need) return false;   // insufficient history
```

With `ArraySetAsSeries(...,true)`: **index 0 = current forming bar, index 1 =
last closed, higher index = older.** Every rule below is written in this
convention. Detection touches index ≥ 1 only.

### Reading D1 / W1 while running on an H4 chart

Multi-TF is just a different `tf` argument — no chart switch needed:

- **D1 EMA / ADX:** separate handles `iMA(sym,PERIOD_D1,...)`,
  `iADX(sym,PERIOD_D1,...)`; `CopyBuffer` shift 1 = last **closed** D1 bar.
- **PDH/PDL:** prior completed D1 bar = `iHigh/iLow(sym,PERIOD_D1,1)` (index 0 is
  today's forming D1 bar). No fractal lag — fixed the instant the day closes.
- **PWH/PWL:** prior completed calendar week = `iHigh/iLow(sym,PERIOD_W1,1)`.
  Prefer `PERIOD_W1` index 1 over aggregating D1 bars — cleaner and exact.
- **D1 N-bar breakout (EMA strat §3):** `CopyRates(sym,PERIOD_D1,1,N,d1)` and
  test the extreme against those N closed D1 bars.

---

## 2. Shared fractal / swing-structure engine

All three detectors need confirmed fractal swings. Build one helper; each
detector calls it. **`FRACTAL_N = 2` (5-bar: 2 left, 2 right), strict
inequality** (README).

### Confirmation & indexing

A swing-high **centre** sits at bar index `c`. Its right neighbours are the more
recent bars `c-1 … c-FRACTAL_N`; its left neighbours the older `c+1 …
c+FRACTAL_N`. For all right neighbours to be **closed**, need `c-FRACTAL_N ≥ 1`,
so the **earliest-confirmable centre index is `c = 1 + FRACTAL_N` (= 3 for
N=2).** That is the fractal confirmation lag: swings are known `FRACTAL_N` closed
bars late — correct and non-repainting.

```mql5
// r[] is as-series MqlRates. Returns true if index c is a confirmed swing high.
bool IsSwingHigh(const MqlRates &r[],int c,int n)
  {
   double h=r[c].high;
   for(int k=1;k<=n;k++)
      if(!(h>r[c-k].high) || !(h>r[c+k].high))   // STRICT both sides
         return false;
   return true;
  }
bool IsSwingLow(const MqlRates &r[],int c,int n)
  {
   double l=r[c].low;
   for(int k=1;k<=n;k++)
      if(!(l<r[c-k].low) || !(l<r[c+k].low))
         return false;
   return true;
  }
```

- Ties (`>=`) never form a fractal (plateaus may instead be an equal-highs pool,
  strat 1).
- Guard the scan range: only call for `n ≤ c ≤ ArraySize(r)-1-n`.

### Structure labels (HH/HL/LH/LL) and trend

Walk `c` from low index (recent) to high (old); collect confirmed swing highs and
lows into two small ring buffers (see structs below). Then:

- **swing high is HH** if it exceeds the previous confirmed swing high, else LH.
- **swing low is HL** if it exceeds the previous confirmed swing low, else LL.
- **Uptrend** = most-recent confirmed swing high is HH **and** most-recent
  confirmed swing low is HL. **Downtrend** = LH + LL. Else **range/undefined**.

```mql5
struct SwingPt { datetime t; double px; int idx; bool isHigh; };  // idx at detection time
```

Persist the **last few** confirmed swings per side (4–6 is plenty) so structure
labels and "reference swing" lookups are O(1). Recompute the confirmed-swing
list each bar from the fresh `CopyRates` window (simplest, no cross-bar index
drift) rather than storing raw indices that shift as bars roll off.

---

## 3. Signal.mqh extension (the dev adds these fields ONCE)

The current `SignalCandidate` cannot express partial targets, the D1 flag, or the
strategies' richer overlays. Add the following block to `struct SignalCandidate`
in `mql5/include/Hybrid/Signal.mqh`. All new fields are **optional/back-compat**
(default 0/false/empty ⇒ harness behaves exactly as today). **Do not remove or
retype existing fields** — the harness reads `entry/sl/tp/rr/zone_*` directly.

```mql5
// --- scale-out plan (advisory until HandleSignal is extended) ---
double   tp1;              // first / partial target price (0 = unused)
double   tp2;              // runner target price          (0 = unused)
double   partial_fraction;// fraction to bank at tp1, e.g. 0.5 (0 = single target)

// --- context flag (APPROVED decision 1: FLAG, never a hard filter) ---
bool     d1_context;      // true = D1 aligned / confluent (shown, not gated)

// --- human-readable one-liner for modal + label ---
string   comment;         // e.g. "EQL pool + D1 confluence" / "stretch 2.3 ATR, ADX 22"

// --- extra overlay geometry (rendered by an extended DrawOverlays) ---
int      aux_count;                 // number of aux levels used (<= 8)
double   aux_price[8];              // horizontal levels: fib grid / EMA / band / swept pool / ref swing / MSS
string   aux_label[8];             // label per aux level
double   zone2_hi, zone2_lo;       // optional 2nd rectangle (FVG overlap / band); 0 = unused
datetime leg_t0, leg_t1;           // impulse-leg / structure trendline endpoints (Fib); 0 = unused
double   leg_p0, leg_p1;
```

**Zeroing rule:** every detector must set all new fields (0/false/"" when
unused) so a struct reused across bars never leaks a stale overlay. Simplest:
`SignalCandidate out; ZeroMemory(out);` at the top of `Detect`, then fill.

### Harness changes the dev must make to render/execute the extension

These are **code tasks for the MQL5 dev**, described here so the blueprint is
complete (this doc does not edit code):

- **`DrawOverlays`:** after the existing zone + entry/sl/tp lines, loop
  `aux_count` → draw an `OBJ_HLINE` + `OBJ_TEXT` per `aux_price/aux_label`;
  draw `zone2` as a second `OBJ_RECTANGLE` (darker fill) when non-zero; draw the
  impulse leg as `OBJ_TREND` from `(leg_t0,leg_p0)` to `(leg_t1,leg_p1)` when
  non-zero. Also surface `d1_context` / `comment` in the corner label.
- **`AskApproval` / modal:** append `comment` and, if `partial_fraction>0`, a
  "TP1 x / TP2 y, bank z%" line so the human sees the scale-out plan.
- **Scale-out execution (optional, for parity with the spec):** if the dev wants
  real partials, split the approved order — place `partial_fraction` of the lots
  with `tp=tp1`, the remainder with `tp=tp2`, and move the remainder to BE when
  TP1 fills. Until then `cand.tp` (the single target) governs.

---

## 4. Shared overlay conventions

The harness already draws: setup-zone rectangle (`zone_hi/lo/from/to`), entry
(green), SL (red), TP (blue) H-lines, and a text label. Each detector **populates
those base fields** plus `aux_*/zone2/leg_*` for its strategy-specific marks.
Colour palette is fixed by the harness (entry `RGB(0,160,0)`, SL `RGB(204,0,0)`,
TP `RGB(0,0,204)`); aux levels use `clrDimGray` dashed unless a doc says
otherwise. Object names stay under the harness `InpObjPrefix` (`HFT_<id>_...`)
so cleanup works.

---

## 5. Edge cases / guards (apply to all three)

- **ATR = 0 or CopyBuffer short** → `return false`. Never divide by ATR without
  the `ATR>0` check (all "in ATR" gates and the EMA stretch depend on it).
- **Insufficient history** → `CopyRates`/`CopyBuffer` returning < requested ⇒
  `return false`. Request `LOOKBACK + 2*FRACTAL_N + slack`.
- **`SYMBOL_POINT`/tick specs zero** on odd custom symbols → fall back to
  `_Point`; if `SYMBOL_TRADE_TICK_SIZE`/`TICK_VALUE` ≤ 0 the harness's
  `SizeByRisk` already returns 0 lots and refuses the trade — the detector should
  still emit (human sees it) but note the risk in `comment`.
- **`.dk` custom symbols:** suffix ≤ 4 chars (satisfied); timestamps are **UTC**
  (project policy) — session/day/week boundaries use UTC as-is, no offset.
- **min-lot skip:** compute the same `SizeByRisk` math the harness uses; if it
  yields `< SYMBOL_VOLUME_MIN`, **do not emit** (spec: skip, never round up —
  rounding up breaks the 1% rule). The harness *clamps up* with a warning, so the
  **detector must pre-empt** by skipping when `risk_usd/loss_per_lot <
  volume_min`.
- **Buffer** (README): `buffer = max(sl_buffer_atr*ATR, spread_now,
  min_stop_points*point)` where `spread_now = (Ask−Bid)` and `min_stop_points =
  SymbolInfoInteger(sym,SYMBOL_TRADE_STOPS_LEVEL)`. Use for every "beyond X" SL.
- **`min_rr` gate** uses the frozen `entry/sl/tp`; if `rr < min_rr`, skip.
- **New-bar re-entrancy:** keep `m_last_bar=iTime(sym,tf,1)`; if unchanged since
  last emit-eligible evaluation, don't re-run transitions.

---

## PM: needs confirmation

1. **Market-on-approval vs the spec's resting pending orders.** The harness fills
   at **market on human approval**, not via a resting limit/stop that fills
   intrabar. The blueprints reconcile this by emitting at the spec's ARMED moment
   with `cand.entry` = expected market fill (Ask/Bid) so 1% risk stays exact, and
   for SMC by waiting to emit until price has pulled back into the OB.
   **Default chosen:** market-on-approval, entry = live Ask/Bid. **Confirm** this
   is acceptable, or the harness needs a pending-order execution path (bigger
   change) to honour limit entries literally.
2. **Partial TP / runner not executed by the harness.** `HandleSignal` places one
   order with one `tp`. `tp1/tp2/partial_fraction` are carried as **advisory**
   (shown to the human). **Default chosen:** single live target = the nearest
   target clearing `min_rr` (per strategy), scale-out shown but not auto-managed.
   **Confirm** whether the dev should extend `HandleSignal` to place real
   partials + move-to-BE.
3. **Portfolio guards are out of the harness's scope.** One-setup-per-symbol
   *across all three strategies*, the 3% concurrent-risk cap, the 3%/4% daily
   caps, and cross-strategy priority (1>2>3) are README **orchestrator** rules.
   A single-symbol detector can self-mute its own symbol but cannot see the
   others' state. **Default chosen:** each detector enforces only its own
   per-symbol lock (one active internal setup at a time); the portfolio caps are
   deferred to the Phase-3 orchestrator. **Confirm** no per-detector enforcement
   is expected in Phase 2.
4. **Spread/tick data in the tester.** Entry = Ask/Bid needs live spread; in the
   visual tester on `.dk` tick data this is present, but if a symbol is loaded
   with modelled spread the buffer's `spread_now` term may be flat. Non-blocking;
   noted so QA checks the tester spread model.
