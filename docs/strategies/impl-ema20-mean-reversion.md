# impl-3 — 20 EMA Mean Reversion

Build sheet for `CEma20MeanRev : public ISignalDetector`. Read
[impl-README.md](impl-README.md) first. Long case shown (price stretched **below**
the EMA, bounce **up**); **short = mirror** (stretched above, bounce down, Bid,
`-1`).

Approved decision baked in: **frequent profile** — `stretch_min = 2.0` ATR
trigger, **partial exit at the mean + runner to the opposite band**.

## Parameters (const members, spec defaults)

```mql5
int    EMA_PERIOD       = 20;    // PRICE_CLOSE
int    ATR_PERIOD       = 14;
double STRETCH_MIN      = 2.0;   // APPROVED frequent profile
int    ADX_PERIOD       = 14;
double ADX_CEILING      = 30.0;  // skip if ADX >= this (too trendy)
// d1_regime = filter (APPROVED default): fresh D1 breakout is a GATE
int    D1_BREAK_LOOKBACK= 20;    // D1 bars for the fresh-breakout guard
double PIN_WICK_RATIO   = 1.5;
// entry_mode = trigger_close -> market next open (== harness on emit)
int    ENTRY_VALID_BARS = 3;
double MAX_EXTEND_ATR   = 1.0;   // extra stretch beyond extreme that aborts
double SL_BUFFER_ATR    = 0.10;
double MIN_RR           = 1.3;   // blended target must clear this
```

> **Consistency rule (critical):** the single live `tp` MUST be the **runner
> (TP2)**, NOT the mean. The mean (EMA) is ~1R away — below `MIN_RR=1.3`. If you
> set `tp`=EMA the `rr < MIN_RR` gate rejects **every** setup and the detector
> never fires. `tp` = TP2 = further of `MIN_RR*risk` or the opposite Keltner
> band; TP1=EMA is advisory (`tp1`). (impl-README §0.4.)

## Persistent state

```mql5
enum EmaState { EMA_IDLE, EMA_FORMING };

struct EmaCtx
  {
   EmaState state;
   int      dir;              // +1 long
   double   ema_frozen;       // EMA at extreme bar (for band + TP)
   double   atr_frozen;       // ATR at extreme bar close
   double   stretch;          // signed stretch on extreme bar (ATR units)
   double   extreme_low;      // furthest low below EMA (SL anchor)
   datetime extreme_time;
   int      bars_forming;
   bool     pool_confluence;  // extreme at PDL/PWL (flag)
  };
EmaCtx   m_c;
datetime m_last_bar;
bool     m_symbol_locked;
```

## Per-closed-bar algorithm (`Detect`)

`ZeroMemory(out)`; `m_last_bar` guard; `EnsureHandles` (ATR, EMA(20), ADX(14));
`CopyRates` (`need ~ 40`); copy EMA, ADX, ATR buffers as-series. Newest closed =
index 1.

```mql5
double stretch = (ema[1] - r[1].close) / ATR;   // long: positive when below EMA
```

### 1. FORMING — stretch gate (§2) + regime filter (§3), on index 1

- **Stretch gate:** `stretch ≥ STRETCH_MIN` (2.0).
- **Regime — ADX:** `adx[1] < ADX_CEILING` (30). (`iADX` buffer 0 = main.)
- **Regime — D1 fresh-breakout guard (FILTER, approved):** the extreme has **not**
  made a new `D1_BREAK_LOOKBACK`-bar D1 low.
  ```mql5
  double d1low=DBL_MAX; MqlRates d1[]; ArraySetAsSeries(d1,true);
  if(CopyRates(sym,PERIOD_D1,1,D1_BREAK_LOOKBACK,d1)<D1_BREAK_LOOKBACK) return false;
  for(i=0;i<D1_BREAK_LOOKBACK;i++) d1low=MathMin(d1low,d1[i].low);
  bool fresh_breakdown = (r[1].low < d1low);
  if(fresh_breakdown) skip;   // don't buy a dip that is a fresh multi-day breakdown
  ```
- All pass → `state=FORMING`; freeze `ema_frozen=ema[1]`, `atr_frozen=ATR`,
  `stretch`, `extreme_low=r[1].low`, `extreme_time`, `bars_forming=0`; lock
  symbol. **Track the extreme:** each subsequent bar, if `r[1].low < extreme_low`
  update `extreme_low`/`extreme_time`/`ema_frozen`/stretch (still forming).
- **Pool confluence flag (not a gate):** `extreme_low` within
  `0.10*atr_frozen` of `PDL`/`PWL` → `pool_confluence=true`.

### 2. Reversal trigger (§4) → EMIT on the trigger bar

While `state=FORMING`, on index 1 require a **bullish reversal** (any of), and
the bar must close **toward** the EMA (`r[1].close > r[2].close`):

- **Engulfing:** `r[1].close > r[1].open` AND `r[1].close > r[2].open` AND
  `r[1].open < r[2].close`.
- **Pin/hammer:** `r[1].close > r[1].open`; close upper third
  (`r[1].close ≥ r[1].high − (r[1].high−r[1].low)/3`); lower wick ≥
  `PIN_WICK_RATIO*|body|`.
- **Momentum flip:** `r[1].close > r[2].high`.

(`trigger = any_of_above`.) The stretch gate must have held on the extreme bar
at/before this trigger. On pass → compute entry/SL/TP; if `rr ≥ MIN_RR` and lots
≥ vol_min → **emit** (`return true`), reset to IDLE + unlock next Detect.

> `trigger_close` entry = market next-bar open = harness-on-emit. Entry = live
> Ask.

### Invalidation (pre-emit only; FORMING)

- **Thesis dead:** a bar **closes** below `extreme_low − buffer` → IDLE + unlock.
- **Runaway:** price makes a new low `> MAX_EXTEND_ATR*atr_frozen` beyond the
  original extreme with no valid trigger → abort → IDLE.
- **Regime flip:** `adx[1] ≥ ADX_CEILING` → IDLE.
- **`bars_forming > ENTRY_VALID_BARS`** without a trigger → IDLE.
- `bars_forming++` each bar.

## Entry / SL / TP (against live Ask on emit)

```
buffer = max(SL_BUFFER_ATR*atr_frozen, spread_now, stops_level*point)
entry  = Ask
sl     = extreme_low - buffer
risk   = entry - sl                                  // naturally wide -> small lots (expected)
// TP1 = the mean (EMA). Managed on CLOSE-ACROSS at run time, so as a static
// price for sizing/overlay use the frozen EMA:
tp1    = ema_frozen;                                  // advisory partial marker
// TP2 = runner = further of MIN_RR R or opposite Keltner band:
double band_up = ema_frozen + STRETCH_MIN*atr_frozen; // opposite band
tp2    = MathMax(entry + MIN_RR*risk, band_up);
tp     = tp2;                                          // SINGLE LIVE TARGET (see consistency rule)
rr     = (tp - entry)/risk;
partial_fraction = 0.5;                               // bank half at TP1 (advisory)
if(rr < MIN_RR) skip;
if(risk<=0 || SizeByRisk(entry,sl) < vol_min) skip;
```

**`tp1_mode = ema_cross_close`** (spec default) means the *live* TP1 exit is "a
bar closes across the EMA", which the single-order harness cannot manage — so for
the harness path TP1 is advisory and `tp`=TP2 governs. If the dev extends
`HandleSignal` for real partials, TP1 becomes a managed close-across exit on the
half position (impl-README §3).

## D1 regime (FILTER — approved) + pool confluence (flag)

Unlike strats 1 & 2, decision 1 (flag counter-trend) does **not** override this
strategy's own `d1_regime = filter` default: a **fresh D1 breakdown is a hard
gate** (skip) because fading a genuine multi-day break is the classic trap. Set
`out.d1_context = !fresh_breakdown` (true = regime clean). `pool_confluence` →
`comment` "pool confluence".

## SignalCandidate population

| field | value |
|-------|-------|
| `strategy` | `"EMArev"` |
| `direction` | `+1`/`-1` |
| `entry`,`sl`,`tp`,`rr` | as above (`tp`=TP2 runner) |
| `tp1`,`tp2`,`partial_fraction` | EMA / runner / 0.5 (advisory partial) |
| `zone_from`,`zone_to` | `extreme_time` → `iTime(sym,tf,1)` |
| `zone_hi`,`zone_lo` | Keltner band region: `ema_frozen` / `ema_frozen − STRETCH_MIN*atr_frozen` (shaded) |
| `aux_price[..]`,`aux_label[..]` | EMA20 ("mean"), lower band, upper band, `extreme_low` ("stretch anchor") |
| `d1_context` | `!fresh_breakdown` (regime clean) |
| `comment` | `StringFormat("stretch %.1f ATR, ADX %.0f%s", stretch, adx[1], pool_confluence?" +pool":"")` |

## Overlay objects (extended DrawOverlays)

1. EMA20 (aux) + Keltner band `EMA ± STRETCH_MIN*ATR` shaded (base zone + band
   aux levels).
2. Stretch reading label ("stretch = X.X ATR", ADX value, pool flag) via
   `comment`, anchored at `extreme_time`.
3. Stretch extreme marked (`extreme_low` aux = SL anchor).
4. Trigger candle highlighted, labelled engulf/pin/momentum-flip.
5. Entry arrow, SL ($risk+lots), **TP1 = EMA** ("on close across", advisory aux),
   **TP2 = band/R** = harness `tp`, R multiples.
6. Corner label: direction + R:R.

## Edge-case skips (spec failure modes → conditions)

- **Strong trend trap:** the `ADX_CEILING` + D1 fresh-break gate handle the
  numeric case; the human still skips a "freight train" on the chart.
- **Fresh breakdown:** hard-gated by the D1 guard (above).
- **News spike / weekend gap:** the stretch may be artificial; the `MAX_EXTEND`
  runaway abort and ADX gate limit exposure — otherwise the human judges (gaps on
  `.dk` custom symbols are common).
- **Target already at the EMA on entry:** if the EMA has fallen to meet price,
  `tp` (TP2 runner) still uses `max(MIN_RR*risk, band)` so a trivially-close mean
  doesn't collapse R below the gate — but if even the band is < `MIN_RR*risk`,
  `rr < MIN_RR` → skip (as intended).
- **Serial re-triggers:** `m_symbol_locked` + `ENTRY_VALID_BARS` limit re-arming
  after a stop-out.
- Standard guards (ATR=0, history, min-lot, `.dk` UTC) per impl-README §5.
- **This is the highest-frequency detector** — expected to bump the (orchestrator)
  concurrent-risk cap most often; that cap is out of scope here (impl-README PM 3).
