# Strategy 4 (candidate) — Shock Continuation

**Status:** research spec for the engineer — backtest only. NOT to be wired into the
interactive tester or the training program until the acceptance bars below are met.
The three live detectors stay frozen through Levels 6–7 so baseline comparisons hold.
(Coach-authored 2026-08-28; engineer should move this to `docs/strategies/` in the repo.)

> Shared vocabulary (fractals, ATR, level pools, buffer, sizing, drawdown,
> one-setup-per-symbol) lives in `docs/strategies/README.md`. This spec only adds or
> overrides what is specific to the setup.

## Why this exists (coach evidence, 2026-08-28)

From the EMArev cohort across 16 blind baselines (2013–2024, four symbols):

| D1 shape into the EMArev signal | n | fade WR | fade total R |
|---|---|---|---|
| Shock (a D1 bar ≥ 1.8 ATR in the last 3) | 28 | 25% | **−10.92R** |
| Freight (5+ same-direction closes, ≥ 2 ATR) | 5 | 40% | +1.65R |
| Slow / absorbed | 60 | 40% | +3.95R |

71 of 72 EMArev losers were full stop-outs, i.e. price kept running with the shock by
at least a stop-distance. Fading fresh daily-scale shocks loses; the hypothesis is that
**joining them after a shallow pullback** is positive-expectancy. This is a post-hoc
slice with coach-chosen thresholds (n=28) — the spec's job is to test it properly on
ticks, not to assume it. The inverse of a fade is NOT computable from the fade's R
column (different stop/target paths); it needs a path-level backtest.

## Concept (for the account owner)

A big scheduled or unscheduled event prints a very large daily candle. That candle is
the *announcement*; the days after it are the *repositioning* by everyone who could not
react in the first hour. We do not touch the release itself (spreads, slippage, FTMO
Swing rules, and no human edge in the first 15 minutes). We wait for the first H4
pullback that fails to reclaim the pre-shock territory, then join the shock's direction
on resumption, stop beyond the pullback extreme, target a measured move.

## Definitions (long case = bullish shock; short = mirror)

All on closed bars. ATR = ATR(14) Wilder on the timeframe named.

### 1. Shock bar (D1)
A closed D1 bar `s` with `range[s] ≥ shock_atr × ATR_D1` (default **1.8**, range
1.5–2.5) **or** `|close − open| ≥ shock_body_atr × ATR_D1` (default **1.5**), closing
in the top `shock_close_pct` of its range (default **35%**, i.e. close ≥ low + 0.65 ×
range). Direction = sign(close − open). Record `shock_high`, `shock_low`,
`shock_mid = (high+low)/2`, and `pre_level = open[s]` (the pre-shock reference).

### 2. Validity window
The setup is live from the close of `s` for `valid_bars_h4` H4 bars (default **18**,
= 3 trading days). No entry inside the first `cooldown_h4` H4 bars after the shock bar's
close (default **1**) — lets the immediate post-release chop print.

### 3. Pullback (H4)
A retrace against the shock direction of at least `pull_min_atr × ATR_H4` (default
**0.8**) from the post-shock high, that **holds**: no H4 close below `shock_mid`
(default `hold_level = mid`; alternative `pre_level` is stricter). Pullback extreme =
lowest low of the pullback bars.

### 4. Resumption trigger
A closed H4 bar that closes above the prior H4 bar's high after the pullback extreme
(simple resumption close), **or** a bullish engulfing / hammer whose close is above
the pullback's midpoint — parameter `trigger = resume_close | reversal_candle`
(default `resume_close`).

### 5. Entry / SL / TP
- **Entry:** buy-stop at `trigger_bar_high + buffer` (README buffer), valid
  `entry_valid_bars` H4 bars (default **6**); cancel if unfilled.
- **SL:** `pullback_extreme − buffer`.
- **TP:** measured move — `entry + tp_mult × (shock_high − shock_low)` (default
  **0.75**); alternative `opposing_pool` (nearest D1 swing / PWH ≥ min_rr away).
  `min_rr` default **2.0**; below it, do not arm.
- Optional scale-out `tp1_r` = 1.5R with BE per the README doctrine; backtest the
  single-target variant for clean stats.

### 6. Invalidation
(a) H4 close below `hold_level` before fill; (b) validity window expires; (c) a second
shock bar in the opposite direction; (d) higher-priority setup arms (README priority
becomes 1 > 2 > 4 > 3 — decide at integration).

## Calendar interaction (must be modelled, not assumed)
- Class W events inside the projected hold → do not arm (unchanged doctrine).
- The shock bar itself will usually BE a V/U event; that is the point. Forward V ≤ 6h
  from entry → do not arm (amended doctrine); 6–12h → flag caution.
- Log for every trade: whether the shock was scheduled (V/C in feed) or unscheduled (U).

## Backtest protocol (acceptance bars)
1. **Data:** real Dukascopy ticks via the existing `.dk` symbols; model 4; spread as
   recorded (do NOT fix spread — release-window widening is part of the edge test).
   Symbols: EURUSD, GBPUSD, USDJPY, XAUUSD. Years: all available (2013+ where loaded).
2. **Runs:** (a) default parameters; (b) sensitivity on `shock_atr` ∈ {1.5, 1.8, 2.2},
   `pull_min_atr` ∈ {0.5, 0.8, 1.2}, `tp_mult` ∈ {0.5, 0.75, 1.0}; (c) split by
   scheduled vs unscheduled shock; (d) split by D1 regime (trend-aligned vs
   counter-trend shock via the count-the-closes rule) — the hypothesis predicts
   trend-aligned shocks dominate.
3. **Report per run:** n, WR, avg R, total R, max consecutive losers, worst gap-through-
   stop slippage in R, share of trades holding across a weekend, FTMO daily-loss
   breaches at 1% risk.
4. **Acceptance to proceed to a blind AA baseline + guide tab:** default-parameter run
   ≥ 150 trades across symbols, **avg R ≥ +0.25**, WR ≥ 40%, no parameter cell in the
   sensitivity grid with avg R < −0.15 (fragility check), and slippage-adjusted results
   within 20% of raw. Anything less: report, do not integrate.
5. **Overfit guard:** hold out 2023–2024 entirely from any parameter selection; report
   holdout separately. Parameters are chosen on 2013–2022 only.

## Explicitly out of scope
Entering on the release candle; anything inside the first H4 bar after the shock; any
change to the three live detectors; any live or tester-interactive use before
acceptance.

## Questions for the account owner (answer at integration, not now)
1. Measured-move target vs opposing-pool target — which reads better on the overlay?
2. Should the shock-bar direction require D1 trend agreement (filter) or just flag it?
3. Priority against Deep Fib when both arm on the same news leg (they will overlap).
