# impl-2 — Deep Fibonacci Retracement In-Trend

Build sheet for `CDeepFibRetrace : public ISignalDetector`. Read
[impl-README.md](impl-README.md) first. Long/uptrend case shown; **short =
mirror** (downtrend, retrace up into the premium zone, bearish trigger, Bid,
`-1`).

Approved decisions baked in: **deep discount zone ONLY, 0.618–0.786**; **SL just
past 88.6%**; **D1 bias = FLAG, not filter** (alert counter-D1 anyway, flag it).

## Parameters (const members, spec defaults)

```mql5
int    FRACTAL_N        = 2;
int    ATR_PERIOD       = 14;
int    EMA_PERIOD       = 50;    // trend EMA, PRICE_CLOSE
int    EMA_SLOPE_BARS   = 5;     // slope >= 0 over last N
double IMPULSE_MIN_ATR  = 2.0;   // min impulse-leg size in ATR
double FIB_LO           = 0.618; // proximal edge of discount band
double FIB_HI           = 0.786; // distal edge (APPROVED: deep only)
double PIN_WICK_RATIO   = 1.5;
// entry_mode = trigger_close -> market at next-bar open (== harness on emit)
int    ENTRY_VALID_BARS = 4;
double SL_FIB           = 0.886; // APPROVED: stop just past 88.6%
double SL_BUFFER_ATR    = 0.10;
double TP_RUNNER_EXT    = 1.618; // advisory runner extension
double MIN_RR           = 2.0;
```

## Persistent state

```mql5
enum FibState { FIB_IDLE, FIB_FORMING };   // emit happens ON the trigger bar

struct FibCtx
  {
   FibState state;
   int      dir;          // +1 up
   double   L0, L100;      // impulse origin (0%) / terminal (100%)
   datetime t0, t100;      // leg endpoints (for trendline overlay)
   double   atr_frozen;    // frozen at trigger bar close
   int      bars_forming;  // since band first tagged
   bool     d1_bias;       // D1 trend agrees (flag)
  };
FibCtx   m_c;
datetime m_last_bar;
bool     m_symbol_locked;
```

`level(f) = L100 − f*(L100 − L0)` for longs. Key levels: `0.5, 0.618, 0.705,
0.786, 0.886, 1.0`.

## Per-closed-bar algorithm (`Detect`)

`ZeroMemory(out)`; `m_last_bar` guard; `EnsureHandles` (ATR + EMA(50));
`CopyRates` (`need = 2*FRACTAL_N + enough to hold the impulse leg + retrace`,
e.g. 80); `CopyBuffer(m_hEMA,0,0,EMA_SLOPE_BARS+2,ema)`. Newest closed = index 1.

### 1. Trend gate (§1) — both must hold on index 1

```mql5
// structure: most-recent confirmed swing high is HH AND swing low is HL
bool struct_up = (lastSwingHigh_is_HH && lastSwingLow_is_HL);  // from fractal engine
// EMA regime: close above EMA50 with non-negative slope
bool ema_up = (r[1].close > ema[1]) && (ema[1] - ema[1+EMA_SLOPE_BARS] >= 0.0);
bool trend_up = struct_up && ema_up;
```

If `!trend_up` → clear any FORMING and `return false` (no longs).

### 2. Impulse leg (§2) — most recent completed swing-low→swing-high

- `L0` = origin confirmed swing low, `L100` = terminal confirmed swing high, the
  **latest completed** low→high move in trend direction (both fractals
  confirmed). Capture `t0,t100`.
- **Size gate:** `(L100 − L0) ≥ IMPULSE_MIN_ATR*ATR` (2.0). Else no valid leg.
- **Freshness:** no confirmed swing high above `L100` since it formed. If a new
  HH forms → **re-anchor** `L0/L100` to the new leg (and drop any stale FORMING).

On a valid fresh leg → `state=FORMING` (draw grid/band), lock symbol.

### 3. Discount-zone test + confirmation trigger (§3–4) → EMIT on the trigger bar

Only when `state=FORMING`. On index 1 (the just-closed candidate trigger bar):

- **Band tag:** `r[1].low ≤ level(FIB_LO)` (0.618) — the bar reached the
  discount band; and the trigger low is **inside or below** the band (not a
  shallow tag): `r[1].low ≤ level(FIB_LO)`. (Distal edge `level(FIB_HI)` = 0.786
  is the reference for "too deep"; see invalidation.)
- **Bullish reversal (either):**
  - **Engulfing:** `r[1].close > r[1].open` AND `r[1].close > r[2].open` AND
    `r[1].open < r[2].close` (body engulfs prior body).
  - **Pin/hammer:** `r[1].close > r[1].open`; close in upper third:
    `r[1].close ≥ r[1].high − (r[1].high−r[1].low)/3`; lower wick ≥ ratio*body:
    `(min(r[1].open,r[1].close) − r[1].low) ≥ PIN_WICK_RATIO*|r[1].close−r[1].open|`.
- Freeze `atr_frozen=ATR`. Compute entry/SL/TP (below). If `rr ≥ MIN_RR` and
  lots ≥ vol_min → **emit** (`return true`). Reset to IDLE + unlock on next
  Detect (one-shot).

> Entry mode = `trigger_close` = **market at the open of the next bar** = exactly
> what the harness does when Detect fires on the new bar and the human approves.
> Clean map; entry = live Ask. (impl-README §0.3.)

### Invalidation (pre-emit only; FORMING)

- **Too deep:** a bar **closes** below `level(SL_FIB) − buffer` (0.886) → trend
  thesis dead → IDLE + unlock.
- **New HH above `L100`** with no valid trigger → re-anchor to the new leg
  (§2), don't emit.
- **Trend gate breaks** (structure turns or `close < EMA50`) → IDLE.
- **`bars_forming > ENTRY_VALID_BARS`** without a trigger → IDLE.
- `bars_forming++` each bar in FORMING.

## Entry / SL / TP (against live Ask on emit)

```
buffer = max(SL_BUFFER_ATR*atr_frozen, spread_now, stops_level*point)
entry  = Ask
sl     = min( level(SL_FIB) - buffer , trigger_low - buffer )   // never tighter than trigger low
risk   = entry - sl                              // must be > 0
tp     = L100                                    // TP1 = prior extreme (the leg top)
rr     = (tp - entry)/risk;
tp1    = L100;                                    // = tp (primary)
tp2    = L100 + (TP_RUNNER_EXT-1.0)*(L100-L0);    // 1.618 extension, advisory runner
partial_fraction = 0.5;                           // advisory
if(rr < MIN_RR) skip;
if(risk<=0 || SizeByRisk(entry,sl) < vol_min) skip;
```

**Single live `tp` = TP1 = `L100`** (impl-README §0.4: entry near 0.618–0.786 and
SL just past 0.886 makes `L100` already a high-R, gate-clearing target). `tp2`
(1.618 ext) carried advisory. Note `level(-0.618)` in the spec = the 1.618
extension above `L100`, i.e. `L100 + 0.618*(L100−L0)`.

## D1 bias (FLAG, not filter — approved)

Apply the §1 trend gate to `PERIOD_D1` (separate EMA(50) D1 handle + D1 fractal
structure). If D1 trend agrees → `out.d1_context=true`, `comment` includes "D1
bias aligned". If D1 disagrees → still emit (flag only): `comment` = "counter-D1
pullback". **Never suppress.**

## SignalCandidate population

| field | value |
|-------|-------|
| `strategy` | `"DeepFib"` |
| `direction` | `+1`/`-1` |
| `entry`,`sl`,`tp`,`rr` | as above |
| `tp1`,`tp2`,`partial_fraction` | `L100` / 1.618-ext / 0.5 (advisory) |
| `zone_from`,`zone_to` | `t100` → `iTime(sym,tf,1)` (retrace span) |
| `zone_hi`,`zone_lo` | discount band `level(0.618)` / `level(0.786)` (shaded) |
| `leg_t0,leg_p0,leg_t1,leg_p1` | impulse leg `t0,L0 → t100,L100` (trendline) |
| `aux_price[..]`,`aux_label[..]` | fib grid 0/0.5/0.618/0.705/0.786/0.886/1.0 with labels; EMA50 value |
| `d1_context` | D1-bias flag |
| `comment` | "engulf/pin @ discount" + D1 bias note |

## Overlay objects (extended DrawOverlays)

1. Trend labels (last HH/HL) + EMA50 (aux level) + "D1 bias aligned" flag.
2. Impulse leg trendline `L0→L100` (leg_* fields), both endpoints marked.
3. Fib grid (aux levels) with the **discount band [0.618,0.786] shaded** = base
   `zone_hi/zone_lo` rectangle.
4. Confirmation candle highlighted, labelled engulf/pin (via `comment`).
5. Entry arrow, SL line ($risk+lots), TP1=`L100` (harness tp), TP2=ext (advisory
   aux), R multiples.
6. Corner label: direction + R:R.

## Edge-case skips (spec failure modes → conditions)

- **Trend exhausted:** optional guard — skip if price is already `> 3*ATR` above
  EMA50 at the trigger (extended); prefer early/mid-trend. (Comment-flag if not
  hard-skipping.)
- **Climactic impulse:** the `IMPULSE_MIN_ATR` gate + the "organic leg" check —
  reject a leg whose single terminal bar body is `> 0.8*(L100−L0)` (a spike/gap).
- **V-reversal, no touch:** handled — no emit unless `low ≤ level(0.618)`.
- **Deep-fib break with momentum:** the 0.886 close invalidation catches it.
- **Messy/overlapping swings:** if the fractal engine yields no clean single
  low→high leg (multiple unconfirmed or interleaved swings) → no valid impulse →
  skip.
- **Counter-D1:** flag only (approved), never skip.
- Standard guards (ATR=0, history, min-lot, `.dk` UTC) per impl-README §5.
