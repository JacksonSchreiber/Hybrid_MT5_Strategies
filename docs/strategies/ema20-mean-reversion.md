# Strategy 3 — 20 EMA Mean Reversion

> Shared vocabulary (fractals, ATR, level pools, buffer, sizing, drawdown,
> one-setup-per-symbol) lives in [README.md](README.md). This spec only adds
> or overrides what is specific to the setup.

## Concept summary (for the account owner)

Price rarely stays far from its 20-period moving average for long; when it
stretches unusually far away, it tends to "snap back" toward it. We measure how
far price is from the 20 EMA in volatility units (ATR), and when it is
abnormally stretched **and** a reversal candle prints back toward the average,
we fade the move for a bounce to the mean. This is the highest-win-rate, lowest-
reward strategy of the three — small, frequent, high-hit-rate trades — and we
deliberately skip it in strong trends where "stretched" just keeps stretching.

## Definitions (precise, on OHLC / indicator data)

The **long** case is described (price stretched **below** the EMA, expecting a
bounce **up**). Short is the exact mirror. ATR = `ATR(14)` on the trigger TF.

### 1. The mean and the stretch

- **EMA:** `EMA(ema_period)`, applied price = **close** (`PRICE_CLOSE`).
  Default `ema_period = 20`.
- **Stretch (signed):** `stretch = (EMA − close) / ATR(14)` for the long side
  (positive when price is below the EMA). It is a **z-like distance in ATR
  units**.
- **Stretch extreme:** the setup keys off the bar with the **most negative
  close-to-EMA distance** in the current excursion (the furthest bar below the
  EMA before the reversal); its **low** anchors the stop.

Equivalent framing: a **Keltner-style band** at `EMA ± stretch_min * ATR`; the
stretch gate is "price closed beyond the band."

### 2. Stretch gate

- `stretch ≥ stretch_min` on the extreme bar. Default `stretch_min = 2.0`
  (range 1.5–3.5). Below this, not stretched enough → no setup.

### 3. Regime filter (avoid strong trends)

The danger of mean reversion is fading a runaway trend. Skip the setup unless
**both**:

- **`ADX(adx_period) < adx_ceiling`** on the trigger TF (default `adx_period =
  14`, `adx_ceiling = 30`, range 20–40). High ADX = strong trend where stretch
  persists.
- **No fresh higher-TF breakout in the stretch direction:** the current bar's
  extreme has **not** made a new `d1_break_lookback`-bar (default 20) **D1**
  low. (For longs we won't buy a dip that is simultaneously a fresh multi-day
  breakdown.) `d1_regime = filter` by default; can be relaxed to `flag`.

Optional confluence flag (not a gate): stretch extreme sits at a level pool
(PDL/PWL) → "**pool confluence**," higher quality.

### 4. Reversal trigger (back toward the EMA)

On a **closed** bar after (or at) the stretch extreme, require a **bullish
reversal**:

- **Bullish engulfing** (body engulfs prior body, `close > open`), **or**
- **Pin / hammer:** `close > open`, close in upper third of range, lower wick ≥
  `pin_wick_ratio * body` (default 1.5), **or**
- **Momentum flip:** `close > high[-1]` (closes above the prior bar's high).

`trigger = any_of_above` default. The trigger bar must close **toward** the EMA
(i.e. `close > close[-1]`) and the stretch gate (§2) must have been satisfied on
the extreme bar at or before the trigger.

## Signal state machine (closed-bar transitions only)

| State | Entry condition (on a closed bar) | On entry, the system… |
|-------|-----------------------------------|-----------------------|
| **FORMING** | `stretch ≥ stretch_min` (§2) **and** regime filter passes (§3). | Marks stretch extreme; draws EMA, band, stretch reading. |
| **ARMED** | A **reversal trigger** (§4) closes, computed R:R ≥ `min_rr`, sizing ≥ min lot. | Enters (below); SL/TP attached; draws trigger candle, SL, TP1(EMA)/TP2. **Alerts the human.** |
| **TRIGGERED** | Entry fills (may be intrabar via resting order). | Position live. |
| **INVALIDATED** | Any of: (a) a bar **closes below `stretch_extreme_low − buffer`** before fill (thesis dead); (b) `stretch` keeps growing and price makes a new extreme **more than `max_extend_atr` ATR** beyond the original extreme without a valid trigger (runaway — abort, default 1.0); (c) regime filter flips to strong-trend; (d) entry unfilled within `entry_valid_bars` (default 3). | Cancels resting order, clears overlay, releases symbol lock. |

## Entry / SL / TP

- **Entry (default `entry_mode = trigger_close`):** market at the **open of the
  bar after** the confirmation close (a resting order; fill may be intrabar).
  Alternative `zone_limit`: buy-limit at the trigger bar's midpoint (better
  price, risks non-fill).
- **SL:** `stretch_extreme_low − buffer` (README buffer). Just beyond the
  furthest point of the stretch — where "reverting" is objectively wrong.
- **TP — two-part, because a 1 R target here is negative expectancy:**
  1. **TP1 = the EMA (the mean).** Chosen interpretation (closed-bar faithful):
     **exit TP1 when a bar closes across the EMA** (`close ≥ EMA` for a long),
     managed on bar close. This is the clean, non-repainting primary.
     - *Backtest/limit alternative:* a static limit at the **EMA value frozen at
       the entry bar** — simpler R stat but the mean has moved by the time it's
       hit (Design decisions).
  2. **TP2 (runner) = the further of** `min_rr` R **or the opposite Keltner
     band** (`EMA + stretch_min * ATR`). Default: scale out half at TP1, run the
     rest to TP2, move SL to break-even at TP1.
- **`min_rr` default 1.3** (range 1.0–2.0). **The blended target must clear
  1.3 R** — do **not** run this strategy on a bare 1 R "to the EMA only" target;
  at ~55–62% win rate a 1 R target is roughly break-even after costs.

## Risk

README sizing from `sl_distance = entry − SL`. Stretch setups have a **naturally
wide** SL (stop sits beyond a far extreme), so lots are often small and R:R
modest — this is expected. Verify `lots ≥ SYMBOL_VOLUME_MIN` or skip. Honour
README 3% concurrent and 3%/4% daily caps. Because this strategy fires most
often, it is the one most likely to bump the concurrent-risk cap — that is by
design (the cap protects the FTMO daily limit).

## Parameters

| Name | Default | Sane range | Meaning |
|------|---------|-----------|---------|
| `ema_period` | 20 | 10–34 | Mean EMA (applied price = close). |
| `atr_period` | 14 | 10–20 | ATR for the stretch measure (README ATR). |
| `stretch_min` | 2.0 | 1.5–3.5 | Min close-to-EMA distance in ATR to qualify. |
| `adx_period` | 14 | 10–20 | ADX period for regime filter. |
| `adx_ceiling` | 30 | 20–40 | Skip if ADX above this (too trendy). |
| `d1_regime` | filter | filter/flag | D1 fresh-breakout guard: gate vs shown-flag. |
| `d1_break_lookback` | 20 | 10–40 | D1 lookback for the fresh-breakout guard. |
| `trigger` | any_of_above | any_of_above / engulf_only | Reversal trigger set. |
| `pin_wick_ratio` | 1.5 | 1.0–3.0 | Lower-wick-to-body ratio for a pin. |
| `entry_mode` | trigger_close | trigger_close / zone_limit | Fill method. |
| `entry_valid_bars` | 3 | 2–8 | Bars a resting entry stays valid. |
| `max_extend_atr` | 1.0 | 0.5–2.0 | Extra stretch beyond extreme that aborts. |
| `sl_buffer_atr` | 0.10 | 0.05–0.25 | Stop buffer (README). |
| `tp1_mode` | ema_cross_close | ema_cross_close / ema_static | TP1 management. |
| `min_rr` | 1.3 | 1.0–2.0 | Reject setups below this blended R:R. |

## Timeframe interplay

- **Default:** detect and trigger on **H4**; use **D1** only for the regime
  guard (§3) and pool confluence. This is the fastest of the three.
- **Single-TF variant:** run entirely on **D1** — a D1 close 2+ ATR from the D1
  20 EMA is a strong, rare mean-reversion alert; larger moves, wider stops.

## Overlay drawing requirements

1. **EMA(`ema_period`)** plotted; **Keltner band** at `EMA ± stretch_min*ATR`
   shaded.
2. **Stretch reading** — label at the extreme bar: "stretch = X.X ATR" and the
   ADX value; "pool confluence" flag if applicable.
3. **Stretch extreme** marked (the anchor for SL).
4. **Trigger candle** highlighted, labelled (engulf / pin / momentum-flip).
5. **Entry** arrow, **SL** line (labelled $ risk + lots), **TP1 = EMA** (note
   "on close across"), **TP2 = band / R** line.
6. **R:R readout** and **direction** in a corner label.

## Known failure modes (human skip-checklist)

- **Strong trend (the classic trap).** In a powerful trend, price rides far from
  the EMA for a long time; "stretched" is normal, not extreme. The ADX ceiling
  and D1 guard exist for this — if the chart *looks* like a freight train, skip
  even if the numbers pass.
- **Fresh breakout / breakdown.** A stretch created by a genuine break of a big
  level often keeps going. Check the stretch extreme isn't a fresh multi-day
  break (the D1 guard flags it).
- **News spike.** A stretch caused by a scheduled release can extend further on
  follow-through orders; be cautious around red-folder events.
- **EMA is flat but price gapped.** Weekend/holiday gaps create artificial
  stretch that may not revert cleanly on custom symbols.
- **Target already at the EMA on entry.** If the reversal is so slow that the
  EMA has fallen to meet price, TP1 is trivially close — R:R collapses below
  `min_rr` → the gate skips it (as intended).
- **Serial re-triggers.** After a stop-out, resist immediately re-arming the
  same stretch; the one-setup-per-symbol lock plus `entry_valid_bars` limit this.

## Design decisions

- **Stretch = close-to-EMA in ATR units** (equivalently a Keltner band). *Not
  chosen:* Bollinger %B (uses std-dev, more sensitive to the window), or raw
  points (not volatility-normalised, un-portable across symbols).
- **TP1 = "bar closes across the EMA"** (closed-bar faithful, no repaint) rather
  than a static EMA snapshot; the snapshot is offered as `ema_static` for a
  cleaner R statistic when needed.
- **Two-part target with `min_rr` ≥ 1.3** deliberately, because a bare "revert
  to the mean = ~1 R" target is negative expectancy at this win rate. The runner
  to the opposite band is what makes the strategy pay.
- **Regime filter = ADX ceiling + D1 fresh-break guard.** *Alternative:* a pure
  higher-TF-alignment rule (only fade *with* the D1 trend), which is stricter
  and cuts frequency hard.
- **Highest expected win rate of the three (~55–62%), lowest R:R** — this is
  the intended profile; it is not meant to hit the system-level "40–50% WR with
  favourable R:R" on its own (README blend note).

## Questions for the account owner

1. This strategy trades **often** with **small wins and a high hit-rate** — is
   that the profile you want it to contribute, or would you rather it fire
   **rarely** with only the most extreme stretches (raise `stretch_min`)?
2. How hard should the "don't fight a strong trend" filter be — a **hard block**
   (you never see those alerts) or a **warning flag** (you still see them and
   decide on the chart)? (`d1_regime` filter-vs-flag.)
3. For taking profit, do you prefer we **bank half at the average and let the
   rest run** to the far band, or take the **whole position off at the average**
   for the simplest, highest hit-rate exit?
