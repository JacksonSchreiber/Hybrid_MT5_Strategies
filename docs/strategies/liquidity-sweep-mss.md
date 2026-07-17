# Strategy 1 — Liquidity Sweep & Market Structure Shift (SMC model)

> Shared vocabulary (fractals, ATR, level pools, buffer, sizing, drawdown,
> one-setup-per-symbol) lives in [README.md](README.md). This spec only adds
> or overrides what is specific to the setup.

## Concept summary (for the account owner)

Big players push price just past an obvious high or low where lots of stop-loss
orders are sitting, grab that liquidity, then reverse. We wait for price to
spike through a known level and snap back inside it (the "sweep"), then wait for
price to break the last small structure in the new direction (the "shift" —
proof the reversal has teeth). We then buy/sell the pullback into the zone the
reversal came from, with a tight stop just beyond the spike. The chart will show
you the swept level, the shift, and the entry zone so you can sanity-check it.

## Definitions (precise, on OHLC)

The **long** case is described (sweep of a **low**, reversal **up**). Short is
the exact mirror. All levels/tolerances use `ATR(14)` on the trigger TF, frozen
at the sweep bar's close.

### 1. Liquidity pool (what gets swept)

A price level below current price that qualifies as one of:

- **Swing pool:** a confirmed fractal **swing low** within the last
  `liquidity_lookback` bars that has **not** been traded below since it formed
  (an intact "obvious low").
- **Equal-lows pool (EQL):** two or more confirmed swing lows whose values lie
  within `eq_tol_atr * ATR` of each other (default 0.10 ATR). The pool level =
  the **lowest** of the cluster (the price stops sit just under it). Equal lows
  are the *strongest* pool type — flag them.
- **Level pool:** **PDL** or **PWL** (prior-day / prior-week low). No fractal
  lag; always available.

The detector records which pool type was swept (shown on the overlay). If
several pools sit within `eq_tol_atr` of each other, treat them as one stacked
pool (stronger).

### 2. Sweep (the stop-run)

On a **closed** bar `s`:

- **Penetration:** `low[s] < pool_level` (wick trades through the level).
- **Reclaim:** `close[s] > pool_level` (bar closes back **above** the level).
- **Magnitude gate:** penetration depth `pool_level - low[s] ≥ sweep_min_atr *
  ATR` (default 0.05) — filters trivial touches — **and** depth
  `≤ sweep_max_atr * ATR` (default 1.2) — a sweep, not a full breakdown.
- **Speed gate:** reclaim must occur within `sweep_max_bars` bars (default 1;
  i.e. a single bar that wicks below and closes back above). Range 1–2. If 2,
  the pool must be reclaimed by the close of bar `s+1` and no bar may close
  below `pool_level`.

**Sweep extreme** = the lowest `low` among the sweep bar(s). This anchors the
stop.

### 3. Market-structure shift / change-of-character (MSS)

After a valid sweep, define the **reference swing high** = the most recent
confirmed fractal **swing high** that formed **at or after** the start of the
down-move into the sweep (i.e. the lower-high that price must overcome to prove
the reversal). MSS is confirmed when, on a **closed** bar:

- `close > reference_swing_high` (default `mss_break = close`; a body close
  beyond, not just a wick). Alternative `mss_break = wick` noted in Design
  decisions.
- The break must occur within `mss_max_bars` bars after the sweep (default 6).
  If exceeded → setup expires.

The bar/leg that produces the MSS is the **displacement leg**; its origin
defines the entry zone.

### 4. Entry zone — Order Block (chosen interpretation)

**Order block (OB)** = the **last down-close (bearish) candle before the
displacement leg** that broke structure. Zone = that candle's **[low, high]**
range. **Proximal edge** = the high (nearest to price after the break);
**distal edge** = the low. Equilibrium = midpoint `(high+low)/2`.

- If a **Fair Value Gap (FVG)** exists inside the displacement leg (a 3-bar
  imbalance: `low[k+1] > high[k-1]` for the up-leg), and it overlaps the OB,
  the **overlap** is the highest-quality zone — narrow the entry zone to the
  overlap. (FVG-only entry is an alternative — see Design decisions.)
- **Entry price:** default `entry_mode = proximal_edge` → limit at the OB high.
  Alternative `equilibrium` (limit at midpoint) gives more R but lower fill
  rate.

## Signal state machine (closed-bar transitions only)

| State | Entry condition (on a closed bar) | On entry, the system… |
|-------|-----------------------------------|-----------------------|
| **FORMING** | A valid **sweep** (§2) has just closed against a qualifying pool (§1). | Marks sweep extreme; starts MSS watch; draws swept level + sweep candle. |
| **ARMED** | Within `mss_max_bars`, a bar **closes** confirming **MSS** (§3), the OB (§4) is identified, computed R:R ≥ `min_rr`, and sizing yields ≥ min lot. | Places a **buy-limit** at the entry price (§4) with SL/TP (below). Draws OB zone, MSS level, SL, TP. **Alerts the human.** |
| **TRIGGERED** | The resting buy-limit **fills** (may be intrabar — not repainting). | Position live; overlay marks fill. |
| **INVALIDATED** | Any of: (a) a bar **closes** below the **sweep extreme − buffer** before the limit fills (thesis dead); (b) MSS not confirmed within `mss_max_bars`; (c) limit not filled within `entry_valid_bars` (default 8) → cancel; (d) opposing setup arms with higher priority. | Cancels the resting order, clears overlay, releases the symbol lock. |

Notes:
- FORMING→ARMED can only use **confirmed** swings (fractal lag). The reference
  swing high (§3) must be confirmed before its break counts.
- Only one setup per symbol (README). While ARMED/TRIGGERED, detector is muted
  on that symbol.

## Entry / SL / TP

- **Entry:** buy-limit at OB proximal edge (default) — README lifecycle: fill
  may be intrabar via the resting order.
- **SL:** `sweep_extreme − buffer` (README buffer). This is *below the stop-run
  low*, where the reversal thesis is objectively wrong.
- **TP (in priority order):**
  1. **Opposing liquidity:** the nearest **swing high / PDH / PWH** *above*
     entry that price was drawn toward, provided it sits ≥ `min_rr` R away.
  2. If no opposing pool within reach, **fixed `tp_r` R** (default 3.0).
  - Optional scale-out: `tp1_r` (default 2.0 R) partial + runner to opposing
    liquidity; move SL to break-even at `tp1_r`. (Human may override on
    approval; backtest uses the single-target rule for clean stats — see
    Design decisions.)
- **`min_rr` default 2.0.** If TP1 < 2.0 R, **skip** (do not arm).

## Risk

Standard README sizing from `sl_distance = entry − SL`. Because both are fixed
at ARMED, risk is exactly 1% at fill. If `sweep_extreme − buffer` makes the stop
so wide that `lots < SYMBOL_VOLUME_MIN`, skip. Respect the README concurrent-
risk (3%) and daily (3%/4%) caps.

## Parameters

| Name | Default | Sane range | Meaning |
|------|---------|-----------|---------|
| `fractal_n` | 2 | 1–3 | Swing fractal half-width (README). |
| `liquidity_lookback` | 20 | 10–40 | Bars back to search for swing pools. |
| `eq_tol_atr` | 0.10 | 0.05–0.25 | Equal-highs/lows clustering tolerance. |
| `sweep_min_atr` | 0.05 | 0.02–0.20 | Min penetration depth (filters touches). |
| `sweep_max_atr` | 1.2 | 0.6–2.0 | Max penetration (sweep vs breakdown). |
| `sweep_max_bars` | 1 | 1–2 | Bars allowed to reclaim the pool. |
| `mss_break` | close | close/wick | Body-close vs wick break for MSS. |
| `mss_max_bars` | 6 | 3–12 | Bars after sweep to confirm MSS. |
| `entry_mode` | proximal_edge | proximal_edge / equilibrium | OB entry price. |
| `entry_valid_bars` | 8 | 4–20 | Bars a resting limit stays valid. |
| `sl_buffer_atr` | 0.10 | 0.05–0.25 | Stop buffer (README). |
| `min_rr` | 2.0 | 1.5–3.0 | Reject setups below this R:R. |
| `tp_r` | 3.0 | 2.0–5.0 | Fallback fixed target when no opposing pool. |
| `tp1_r` | 2.0 | 1.5–3.0 | Optional partial-scale level. |
| `d1_context` | flag | flag/filter | D1 alignment as shown-flag vs hard filter. |

## Timeframe interplay

- **Default:** detect and trigger on **H4**; read **D1** for context. D1
  context = is the H4 sweep happening **at** a D1 liquidity pool
  (D1 swing / PDL-of-week / PWL)? If yes, flag "**D1 confluence**" on the
  overlay (higher quality). `d1_context = flag` (default) shows it; `= filter`
  requires it.
- **Single-TF variant:** run the entire model on **D1** (D1 fractals, D1 pools,
  D1 MSS). Fewer, larger, higher-conviction alerts; wider stops → smaller lots.

## Overlay drawing requirements (what the human must see)

1. **Swept pool level** — horizontal line at `pool_level`, labelled with pool
   type (`Swing`, `EQL`, `PDL`, `PWL`) and "D1 confluence" if applicable.
2. **Sweep candle(s)** — highlighted, with a marker at the **sweep extreme**.
3. **Reference swing high** + the **MSS break** — line at the reference high,
   arrow/label "MSS ✓" at the breaking bar.
4. **Order block zone** — shaded rectangle [distal, proximal], equilibrium
   dashed; FVG overlap shaded darker if present.
5. **Entry** (arrow at limit price), **SL** (line at sweep extreme − buffer,
   labelled with $ risk and lots), **TP1/TP** (lines, labelled R multiple).
6. **R:R readout** and **direction** (LONG/SHORT) in a corner label.

## Known failure modes (human skip-checklist)

- **No displacement on the MSS.** If the break of structure is a slow, weak
  drift rather than an impulsive leg, the OB is unreliable — skip.
- **Swept into a higher-TF trend.** Fading a strong D1 trend (e.g. sweeping a
  low inside a clean D1 downtrend) is counter-trend; only take if D1 is
  ranging or the sweep is at a major D1 pool. The overlay's D1 flag is the tell.
- **Deep sweep = real break.** If penetration is near `sweep_max_atr` and the
  reclaim is barely back inside, it may be a genuine breakout, not a sweep —
  skip.
- **News candle sweep.** A sweep printed by a scheduled high-impact release can
  reverse violently again; if a red-folder event is within the setup window,
  be cautious.
- **OB already mitigated.** If price already traded back through the OB before
  arming, the imbalance is gone — lower probability.
- **Thin/late equal lows.** "Equal lows" formed months apart or from illiquid
  bars are weak pools; prefer recent, clean EQL.

## Design decisions

- **Order block = last opposite-close candle before the displacement leg**,
  zone = full candle range. *Alternatives not chosen:* body-only OB (open→close),
  FVG-only entry, or "breaker block". FVG is used only as a *refinement* of the
  OB when they overlap.
- **MSS on body close** (not wick) — fewer false shifts. Wick-break is a
  parameter for the more aggressive variant.
- **Single fixed target for backtest stats**; the optional partial/BE runner is
  a live discretionary overlay, kept out of the core stat to avoid ambiguous
  path-dependent results.
- **Session-extreme pools are secondary** to PDH/PDL/PWH/PWL because H4-in-UTC
  cannot align to exact session opens (README caveat).

## Questions for the account owner

1. When price sweeps a level that sits **against a strong daily trend**, should
   the system still alert you (you decide on the chart), or should it stay
   silent unless the daily trend agrees? (This is the `d1_context` flag-vs-
   filter choice.)
2. Do you want the alert to propose **one target** (simple, one clean number to
   judge), or a **partial-take-profit plan** (bank some at 2R, let the rest run
   to the opposite liquidity)?
3. For "equal lows/highs," how strict should "equal" be — only near-perfect
   double bottoms/tops, or a looser cluster? (Tighter = fewer but cleaner
   alerts; looser = more alerts, more noise.)
