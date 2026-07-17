# Strategy 2 — Deep Fibonacci Retracement In-Trend (Discount Matrix)

> Shared vocabulary (fractals, ATR, level pools, buffer, sizing, drawdown,
> one-setup-per-symbol) lives in [README.md](README.md). This spec only adds
> or overrides what is specific to the setup.

## Concept summary (for the account owner)

When a market is clearly trending up, pullbacks are chances to join the trend at
a "discount." We measure the most recent strong up-leg, wait for price to fall
back into the deep part of that leg (the 61.8–78.6% retrace — the discount
zone), and require a bullish reversal candle there to prove buyers stepped in.
We enter with a stop just beyond the leg's origin and aim back to the prior high
and beyond, so the reward is several times the risk. The chart shows you the
trend, the fib zone, and the confirmation candle.

## Definitions (precise, on OHLC / indicator data)

The **long / uptrend** case is described. Short is the exact mirror (downtrend,
retrace up into the discount→"premium" zone, bearish trigger). ATR = `ATR(14)`
on the trigger TF, frozen at the trigger bar's close.

### 1. Established trend (objective gate)

Uptrend is confirmed when **both** hold on closed bars:

- **Structure:** most recent confirmed fractal **swing high is a HH** and most
  recent confirmed **swing low is a HL** (README market-structure labels).
- **EMA regime:** `close > EMA(ema_trend_period)` with a **non-negative slope**
  over the last `ema_slope_bars` bars. Default `ema_trend_period = 50`,
  `ema_slope_bars = 5`.

Both must be true → `trend = up`. Either fails → no long setups.

### 2. Impulse leg (what the fib is drawn on)

- The **impulse leg** = the most recent **completed** swing-low→swing-high move
  in the trend direction (both endpoints confirmed fractals). Endpoints:
  `L0` = origin swing low (0%), `L100` = terminal swing high (100%).
- **Size gate:** `L100 − L0 ≥ impulse_min_atr * ATR` (default 2.0; range
  1.0–4.0). Rejects noise legs.
- **Freshness:** the impulse must be the leg *currently being retraced* — no
  confirmed swing high above `L100` has formed since (that would start a new
  leg). If a higher high forms, re-anchor to the new leg.

### 3. Discount zone (the deep-fib band)

Fib levels measured on the impulse leg: `level(f) = L100 − f * (L100 − L0)`.

- **Discount band = [ fib 0.618 , fib 0.786 ]** — i.e. between
  `level(0.618)` (upper/proximal) and `level(0.786)` (lower/distal).
- **Anchor rule:** always low→high for longs (0% at `L0`, 100% at `L100`);
  never re-anchor mid-setup unless a new leg forms (§2 freshness).
- Key reference levels also computed: `0.886` (deep invalidation) and `0.5`
  (equilibrium — above the zone, not an entry).

### 4. Confirmation trigger

On a **closed** bar whose **low tags the discount band** (`low ≤ level(0.618)`
and the bar interacts with the band), require a **bullish reversal**:

- **Bullish engulfing:** `close > open`, and the body engulfs the prior bar's
  body (`close > open[-1]`, `open < close[-1]`); **or**
- **Rejection / pin:** `close > open`, close in the **upper third** of the
  bar's range, and lower wick ≥ `pin_wick_ratio * body` (default 1.5).

The trigger bar's low must be **inside or below** the band (not a shallow tag
that never reached discount). `trigger = engulf_or_pin` default. Lower-TF
structure-shift trigger is an alternative (Design decisions).

## Signal state machine (closed-bar transitions only)

| State | Entry condition (on a closed bar) | On entry, the system… |
|-------|-----------------------------------|-----------------------|
| **FORMING** | `trend = up` (§1) and a fresh impulse leg (§2) exists; price has begun retracing toward the band. | Draws trend labels, impulse leg, fib grid + discount band. |
| **ARMED** | A **confirmation trigger** (§4) closes inside/at the discount band, computed R:R ≥ `min_rr`, sizing ≥ min lot. | Places entry order (below), SL/TP attached. Draws trigger candle, SL, TPs. **Alerts the human.** |
| **TRIGGERED** | Entry order fills (see Entry — may be intrabar via a resting order). | Position live. |
| **INVALIDATED** | Any of: (a) a bar **closes below `level(sl_fib)` − buffer** (retrace too deep → trend thesis dead); (b) a new HH forms **above `L100`** with no valid trigger (missed / trend continued without us) → re-anchor to new leg; (c) `trend` gate breaks (structure turns or `close < EMA`); (d) entry not filled within `entry_valid_bars` (default 4). | Cancels resting order, clears overlay, releases symbol lock. |

## Entry / SL / TP

- **Entry (default `entry_mode = trigger_close`):** buy-**stop** is *not* used;
  enter at the **market on the open of the bar after** the confirmation close.
  For MQL5 reproducibility this is a resting buy-stop at `high` of the trigger
  bar **or** a market order at next-bar open — default **market at next open**
  (cleanest for a closed-bar-confirmed reversal).
  - Alternative `entry_mode = zone_limit`: buy-limit at `level(0.705)`
    (mid-discount) — better price, risks non-fill.
- **SL:** `level(sl_fib) − buffer`, default `sl_fib = 0.886` (just beyond the
  deep-fib; a break there says the pullback is really a reversal). Alternative
  `sl_fib = 1.0` (beyond `L0`, the impulse origin) — wider, safer, smaller lot.
  SL is also never tighter than `trigger_low − buffer`.
- **TP (priority):**
  1. **TP1 = prior extreme** = `L100` (the swing high the leg topped at) — the
     obvious liquidity above.
  2. **TP2 (runner) = extension** `level(-0.272)`/`level(-0.618)` i.e. 1.272 /
     1.618 extensions of the leg beyond `L100`. Default runner = **1.618**.
  - Because entry sits at ~0.618–0.786 and SL just beyond 0.886, distance up to
    `L100` is large → R:R is typically **2.5–5**. If TP1 < `min_rr` R, skip.
- **`min_rr` default 2.0.**

## Risk

README sizing from `sl_distance = entry − SL`. Deep-fib entries give small SL
distance relative to the target, so lots are healthy and R:R high — but verify
`lots ≥ SYMBOL_VOLUME_MIN`; if the trigger candle is huge, the stop may be wide.
Honour README 3% concurrent and 3%/4% daily caps.

## Parameters

| Name | Default | Sane range | Meaning |
|------|---------|-----------|---------|
| `fractal_n` | 2 | 1–3 | Swing fractal half-width (README). |
| `ema_trend_period` | 50 | 20–100 | Trend EMA (applied price = close). |
| `ema_slope_bars` | 5 | 3–10 | Bars over which EMA slope must be ≥ 0. |
| `impulse_min_atr` | 2.0 | 1.0–4.0 | Min impulse-leg size in ATR. |
| `fib_lo` | 0.618 | 0.5–0.65 | Proximal edge of discount band. |
| `fib_hi` | 0.786 | 0.75–0.886 | Distal edge of discount band. |
| `trigger` | engulf_or_pin | engulf_or_pin / ltf_shift | Confirmation type. |
| `pin_wick_ratio` | 1.5 | 1.0–3.0 | Lower-wick-to-body ratio for a pin. |
| `entry_mode` | trigger_close | trigger_close / zone_limit | Fill method. |
| `entry_valid_bars` | 4 | 2–10 | Bars a resting entry stays valid. |
| `sl_fib` | 0.886 | 0.786–1.0 | Fib level beyond which stop sits. |
| `sl_buffer_atr` | 0.10 | 0.05–0.25 | Stop buffer (README). |
| `tp_runner_ext` | 1.618 | 1.272–2.618 | Extension for the runner target. |
| `min_rr` | 2.0 | 1.5–3.0 | Reject setups below this R:R. |
| `d1_bias` | flag | flag/filter | D1 trend agreement: shown-flag vs hard filter. |

## Timeframe interplay

- **Default:** identify trend, impulse and fib on **H4**; read **D1** trend
  (§1 applied to D1). If D1 trend agrees, flag "**D1 bias aligned**" (higher
  quality). `d1_bias = flag` shows it; `= filter` requires it.
- **Single-TF variant:** run entirely on **D1** (D1 swings/impulse/fib). Rare,
  large, high-conviction pullback alerts.

## Overlay drawing requirements

1. **Trend labels** — the last HH/HL sequence marked; EMA(`ema_trend_period`)
   plotted; "D1 bias aligned" flag if applicable.
2. **Impulse leg** — line from `L0` to `L100` with both endpoints marked.
3. **Fib grid** — 0 / 0.5 / 0.618 / 0.705 / 0.786 / 0.886 / 1.0 levels; the
   **discount band [0.618, 0.786] shaded**.
4. **Confirmation candle** — highlighted, labelled (engulf / pin).
5. **Entry** arrow, **SL** line (labelled $ risk + lots), **TP1 = L100** and
   **TP2 = extension** lines (labelled R multiples).
6. **R:R readout** and **direction** in a corner label.

## Known failure modes (human skip-checklist)

- **Trend already exhausted.** A very extended trend (many legs, tiny recent
  pullbacks, price far above EMA) can top exactly at your entry. Prefer early-
  to-mid-trend pullbacks.
- **Impulse leg was actually the reversal.** If the "impulse" was a climactic
  spike (news/gap), the retrace can just keep going. Check the leg looks
  organic.
- **V-reversal, no touch.** If price rockets back up before tagging 0.618, there
  is no discount entry — do not chase; wait for the next leg.
- **Deep-fib break with momentum.** If the confirmation candle is immediately
  overrun and a bar closes below 0.886, the pullback was a reversal — the SL
  handles it, but don't re-enter blindly.
- **Counter to D1.** An H4 uptrend pullback inside a D1 downtrend is lower
  probability — the "D1 bias" flag is the tell.
- **Overlapping/ambiguous swings.** If fractals are messy (choppy range), the
  impulse leg is ill-defined — skip; this model needs a clean leg.

## Design decisions

- **Trend = structure (HH/HL) AND EMA regime** — two independent confirmations
  reduce false "trends." *Alternative:* EMA-only or structure-only (looser,
  noisier).
- **Discount band = 0.618–0.786** per the "deep fib / discount matrix" brief.
  *Alternative:* the tighter 0.618–0.705 "golden pocket," or including 0.886.
- **SL at 0.886** (not full 1.0) as the default — keeps R:R high while giving
  the reversal room; 1.0 offered as the conservative variant.
- **Entry at next-bar market after a confirmed close** — faithful to closed-bar
  confirmation; `zone_limit` offered for better price at the cost of fills.
- **Runner default 1.618** — a standard, liquid extension target; TP1 at the
  prior high banks the high-probability portion.

## Questions for the account owner

1. How deep a pullback do you want to buy — only the **very deep 61.8–78.6%**
   discounts (fewer, better-priced entries), or should we also alert on the
   shallower "golden pocket" (61.8–70.5%) that fills more often?
2. On the stop: place it **just past the deep-fib (88.6%)** for a bigger reward-
   to-risk, or **all the way beyond the leg's start (100%)** for more breathing
   room and fewer stop-outs (but smaller size)?
3. Should we only alert when the **daily chart agrees** with the trend
   (fewer, cleaner), or also on H4-only trends you can judge yourself?
