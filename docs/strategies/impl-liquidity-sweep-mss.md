# impl-1 — Liquidity Sweep & Market Structure Shift (SMC)

Build sheet for `CLiquiditySweepMSS : public ISignalDetector`. Read
[impl-README.md](impl-README.md) first (handle lifecycle, fractal engine, entry =
market-fill rule, struct extension, guards). Long case shown; **short = mirror**
(swap high↔low, above↔below, `>`↔`<`, Ask↔Bid, `+1`↔`-1`).

Approved decisions baked in: **liquidity pools STRICT** (near-perfect equal
highs/lows + clean PDH/PDL/PWH/PWL only); **D1 context = FLAG, not filter**
(alert counter-trend anyway, flag it).

## Parameters (const members, spec defaults)

```mql5
int    FRACTAL_N        = 2;
int    ATR_PERIOD       = 14;
int    LIQ_LOOKBACK     = 20;    // bars back for swing pools
double EQ_TOL_ATR       = 0.10;  // equal-lows clustering (STRICT: see §Pool)
double SWEEP_MIN_ATR    = 0.05;  // min penetration depth
double SWEEP_MAX_ATR    = 1.2;   // max penetration (sweep, not breakdown)
int    SWEEP_MAX_BARS   = 1;     // reclaim within N bars
// mss_break = close (body close beyond ref swing high)
int    MSS_MAX_BARS     = 6;     // bars after sweep to confirm MSS
// entry_mode = proximal_edge (OB high) — realised via pullback-into-OB emit
int    ENTRY_VALID_BARS = 8;     // bars the internal ARMED state waits for pullback
double SL_BUFFER_ATR    = 0.10;
double MIN_RR           = 2.0;
double TP_R             = 3.0;   // fallback fixed target
double TP1_R            = 2.0;   // advisory partial level
```

## Persistent state (survives between bars)

```mql5
enum SmcState { SMC_IDLE, SMC_FORMING, SMC_ARMED };

struct SmcCtx
  {
   SmcState state;
   int      dir;              // +1 long (swept a low), -1 short
   // sweep
   double   pool_level;       // swept level
   int      pool_type;        // 0 Swing,1 EQL,2 PDL/PDH,3 PWL/PWH
   double   sweep_extreme;    // lowest low of sweep bar(s) (long)
   datetime sweep_time;
   double   atr_frozen;       // ATR at sweep bar close
   int      bars_since_sweep; // increments each new bar
   // mss / OB
   double   ref_swing;        // reference swing high to break
   double   ob_hi, ob_lo;     // order block range
   datetime ob_t0, ob_t1;
   double   fvg_hi, fvg_lo;   // FVG overlap (0 = none)
   double   mss_level;        // = ref_swing, for overlay
   datetime mss_time;
   int      bars_since_arm;   // waiting for pullback into OB
   bool     d1_confluence;
  };
SmcCtx   m_c;
datetime m_last_bar;
bool     m_symbol_locked;     // one internal setup at a time (self-mute)
```

## Per-closed-bar algorithm (`Detect`)

`ZeroMemory(out)` first. Guard `m_last_bar==iTime(sym,tf,1) → return false`;
update it. `EnsureHandles`; copy ATR + `r[]` (as-series, `need = LIQ_LOOKBACK +
MSS_MAX_BARS + 2*FRACTAL_N + 6`). Then run the state machine on **index 1** as
the newest closed bar.

### State machine (explicit edges; all on closed bars)

**SMC_IDLE → SMC_FORMING — a valid sweep just closed at index 1.**

Build the candidate pool set (STRICT), find one that qualifies as swept:

1. **Pools (STRICT):**
   - **Swing low pool:** a confirmed fractal swing low within `LIQ_LOOKBACK`
     bars (centre `c` with `IsSwingLow`) **not traded below since it formed**
     (no `low[j] < swing_low` for `1 ≤ j < c`).
   - **EQL pool (strongest, flag):** ≥ 2 confirmed swing lows within
     `EQ_TOL_ATR*ATR` of each other. **STRICT interpretation (approved):** treat
     as EQL only when the cluster spread `≤ EQ_TOL_ATR*ATR` **and** both are
     recent (within `LIQ_LOOKBACK`) and clean (each is a real fractal, not a
     plateau). Pool level = the **lowest** of the cluster.
   - **Level pool:** `PDL = iLow(sym,PERIOD_D1,1)`, `PWL =
     iLow(sym,PERIOD_W1,1)`. Only these clean day/week levels — **no session
     extremes** (STRICT).
   - Stack pools within `EQ_TOL_ATR*ATR` into one (stronger); record best type.

2. **Sweep test on bar `s=1`** against a pool below current price:
   - Penetration: `r[1].low < pool_level`.
   - Reclaim: `r[1].close > pool_level`.
   - Depth gate: `SWEEP_MIN_ATR*ATR ≤ (pool_level − r[1].low) ≤
     SWEEP_MAX_ATR*ATR`.
   - Speed gate (`SWEEP_MAX_BARS=1`): the wick-below-and-close-above is a single
     bar `s=1`. (If raised to 2: allow reclaim by close of the 2nd bar with no
     intervening close below `pool_level`.)

   On pass: `state=FORMING`; `dir=+1`; freeze `atr_frozen=ATR`,
   `sweep_extreme=r[1].low` (lowest low over the sweep bars), `pool_level`,
   `pool_type`, `sweep_time=r[1].time`, `bars_since_sweep=0`, lock symbol. Set the
   **reference swing high** = most recent confirmed fractal swing high that
   formed **at/after** the start of the down-move into the sweep (scan
   `IsSwingHigh` from index 2 outward, newest first, within the down-leg).
   Draw-only overlay data captured now.

**SMC_FORMING → SMC_ARMED — MSS confirmed within `MSS_MAX_BARS`.**

Each new bar: `bars_since_sweep++`. On index 1:
- **MSS (body close):** `r[1].close > ref_swing` (strict). This is the
  displacement leg.
- **Timeout:** `bars_since_sweep > MSS_MAX_BARS` **without** MSS →
  INVALIDATE (see below).
- **Sweep-thesis death:** `r[1].close < sweep_extreme − buffer` → INVALIDATE.

On MSS pass, identify the **order block**:
- **OB = last down-close (bearish) candle before the displacement leg.** Scan
  back from the MSS bar toward the sweep: first index `k (k≥1)` with
  `r[k].close < r[k].open`. `ob_hi=r[k].high`, `ob_lo=r[k].low`,
  `ob_t0=r[k].time`; proximal edge (long) = `ob_hi`, distal = `ob_lo`,
  equilibrium = `(ob_hi+ob_lo)/2`.
- **FVG refinement (optional):** if a 3-bar bullish imbalance exists in the
  displacement leg (`r[k-2].low > r[k].high` in as-series, i.e. gap between the
  bar before and after the middle), and it overlaps `[ob_lo,ob_hi]`, narrow the
  entry zone to the overlap: `fvg_lo=max(ob_lo,gap_lo)`,
  `fvg_hi=min(ob_hi,gap_hi)`. Proximal edge then = `fvg_hi`.
- **Compute prospective entry/SL/TP** (below) at the OB proximal edge; if
  `rr < MIN_RR` or lots `< vol_min` → **do not arm**, INVALIDATE.
- Else `state=ARMED`, `bars_since_arm=0`, store OB + MSS geometry, set
  `d1_confluence` (below).

**SMC_ARMED → EMIT (return true) — price has pulled back into the OB.**

Because the harness fills at **market on approval** (no resting limit), do not
emit at the ARM bar (price is above the OB after displacement). Instead, on each
subsequent closed bar:
- **Pullback reached:** `r[1].low ≤ ob_proximal` (price returned to the zone).
  → **emit now**: entry = live `Ask`, recompute SL/TP/rr against this Ask,
  re-check `rr ≥ MIN_RR` and lots; if good, `return true` with the full
  candidate; then reset to IDLE + unlock **after** the harness handles it (set a
  one-shot: emit, then on the next Detect clear state).
- **Pre-fill invalidation** (`SMC_ARMED`, still pre-emit):
  - `r[1].close < sweep_extreme − buffer` → INVALIDATE.
  - `bars_since_arm > ENTRY_VALID_BARS` (no pullback) → INVALIDATE.
- `bars_since_arm++` each bar.

> Rationale: emitting only when market has re-entered the OB makes the market
> fill ≈ the `proximal_edge` limit the spec intended, so `|entry−sl|` sizing =
> realised 1% risk. (impl-README §0.3.)

**Any state → INVALIDATE:** `state=IDLE`, clear `m_c`, `m_symbol_locked=false`,
`return false`. (There is no resting order to cancel — pre-emit only.)

**Self-mute:** while `state≠IDLE` or a live position exists on this symbol, do
not start a new FORMING. (Cross-strategy lock + priority 1>2>3 is orchestrator
scope — impl-README PM item 3.)

## Entry / SL / TP (computed at EMIT against live Ask)

```
buffer = max(SL_BUFFER_ATR*atr_frozen, spread_now, stops_level*point)
entry  = Ask                                   // market fill at approval
sl     = sweep_extreme - buffer
risk   = entry - sl                            // must be > 0
// TP priority:
tp_pool = nearest swing high / PDH / PWH above entry
if(tp_pool exists && (tp_pool-entry) >= MIN_RR*risk) tp = tp_pool;
else                                                 tp = entry + TP_R*risk;
rr  = (tp-entry)/risk;
tp1 = entry + TP1_R*risk;                       // advisory partial (2R)
tp2 = tp;                                        // runner = the chosen target
partial_fraction = 0.5;                          // advisory
if(rr < MIN_RR) skip;
if(risk<=0 || SizeByRisk(entry,sl) < vol_min) skip;
```

`tp` (single live target) = **opposing liquidity if it clears `MIN_RR`, else
fixed `TP_R`=3R** (impl-README §0.4: nearest target that passes the gate).

## D1 context (FLAG, not filter — approved)

Set `out.d1_context = true` when the H4 sweep sits **at** a D1 liquidity pool:
`pool_level` within `EQ_TOL_ATR*atr_frozen` of a D1 confirmed swing low, or of
`PDL`/`PWL`, **or** the D1 structure is not a clean opposing downtrend. This only
**flags** "D1 confluence" — **never suppresses** the alert (counter-trend setups
still fire; the human judges). Put the tell in `comment`, e.g.
`"EQL pool + D1 confluence"` or `"Swing pool — counter D1 downtrend"`.

## SignalCandidate population

| field | value |
|-------|-------|
| `valid` | true on emit |
| `strategy` | `Name()` → `"SweepMSS"` |
| `direction` | `+1` / `-1` |
| `entry`,`sl`,`tp`,`rr` | as above (against live Ask/Bid) |
| `tp1`,`tp2`,`partial_fraction` | advisory 2R / target / 0.5 |
| `zone_from`,`zone_to` | `ob_t0` → `iTime(sym,tf,1)` |
| `zone_hi`,`zone_lo` | OB `ob_hi` / `ob_lo` (the entry zone) |
| `zone2_hi`,`zone2_lo` | FVG overlap `fvg_hi`/`fvg_lo` (0 if none) |
| `aux_price[0..]`,`aux_label[..]` | `pool_level` ("Swept <type>"), `ref_swing`/`mss_level` ("MSS ✓"), `sweep_extreme` ("sweep low"), OB equilibrium (dashed) |
| `d1_context` | D1-confluence flag |
| `comment` | pool type + confluence + "counter-D1" note |

`Name()` returns `"SweepMSS"` (matches modal/journal `strategy`).

## Overlay objects (rendered by extended DrawOverlays)

1. Swept pool level — HLINE at `pool_level`, label pool type (`Swing/EQL/PDL/PWL`)
   + "D1 confluence" if `d1_context`.
2. Sweep candle marker — aux level at `sweep_extreme` ("sweep low").
3. Reference swing high + MSS — HLINE at `ref_swing`, label "MSS ✓" at
   `mss_time`.
4. Order-block zone — base rectangle `[ob_lo,ob_hi]`; equilibrium dashed aux;
   FVG overlap = darker `zone2` rectangle if present.
5. Entry arrow / SL / TP1 / TP — the harness's entry/sl/tp H-lines (+ `tp1`
   advisory aux), labelled `$risk`+lots (from `SizeByRisk`) and R multiples.
6. Corner label — direction + R:R (`comment`).

## Edge-case skips (spec failure modes → code conditions)

- **No displacement on MSS:** require the MSS bar body `|close−open| ≥
  0.5*atr_frozen` (impulsive), else the OB is unreliable → skip arming.
- **Deep sweep = real break:** already gated by `≤ SWEEP_MAX_ATR*ATR`; also skip
  if `close` is barely back inside (`close − pool_level < SWEEP_MIN_ATR*atr`).
- **OB already mitigated:** if between MSS and the pullback a bar closed through
  `ob_lo`, the imbalance is spent → INVALIDATE.
- **Thin/late equal lows:** EQL both must be within `LIQ_LOOKBACK` and be real
  fractals (STRICT) — old/illiquid clusters don't qualify.
- Standard guards (ATR=0, history, min-lot, `.dk` UTC) per impl-README §5.
