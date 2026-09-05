# EMArev-Inverse ("ride the stretch") — one-shot pre-registered backtest

**Status:** research spec for the engineer — backtest only, isolated, detectors frozen
in the training path. This is the trader's ONE pre-named follow-up to the rejected
shock-continuation strategy (`shock-continuation-result.md`). If it fails its bars,
the post-shock-continuation claim is dead twice; no v3. The prospective eye journal
(`training/shock-eye-journal.md`) remains the only path after that.

## The claim (trader's, 2026-09-05, named before any new numbers)

When EMArev fires because an **explosive event bar** created the stretch, price does
not revert to the average — it continues in the stretch direction. Proposal: at the
EMArev signal, trade WITH the stretch instead of fading it, on the shock subset.

## Why this is not the rejected strategy

Different trigger population and different entry: the shock strategy waited for a D1
shock + H4 pullback + resumption stop (117 setups in 32 symbol-years). This variant
triggers wherever EMArev triggers (~112 signals in ~12 graded window-years alone;
several hundred expected on full tick ranges) and enters at/near the signal. Prior
evidence FOR: EMArev fades on D1-shock stretches ran 25% WR, −10.92R (n=28), with
71/72 of all EMArev fade losses being full stop-outs (price ran ≥1 stop-distance with
the stretch). Prior evidence AGAINST: the pullback-entry version of continuation
failed cleanly. Both are on record; this run settles it.

## Trade construction (structure-chosen, NOT to be parameter-mined)

Long case = EMArev fired SELL on a bullish stretch; we BUY. Short = mirror.

- **Trigger:** every EMArev ARMED signal (reuse the live detector's signal stream so
  the population is exactly what the trader sees).
- **Two entry variants, both pre-named, no others:**
  - **E1 market:** enter at the EMArev signal price, immediately.
  - **E2 confirm:** buy-stop at the stretch extreme + buffer (continuation confirmed
    by a new extreme), valid 6 H4 bars, cancel if unfilled.
- **Stop (the anti-noise design, chosen structurally):** just beyond the **EMA20
  itself** ± README buffer, capped at 2.5×ATR_H4 from entry. Rationale: "continuation"
  is falsified precisely when price fully reverts through the mean — the mean is the
  thesis boundary. This survives the partial mean-reversion chop that kills tight
  stops. If EMA-distance < 1.0 ATR at entry, use 1.0 ATR instead (degenerate case).
- **Exits — three pre-named variants, judged separately (amended 2026-09-05 before
  any run, on the trader's correct geometry critique: stop-at-EMA risks ~2–2.5 ATR,
  so a fixed 2R target would demand 4–5 ATR of continuation while the evidence shows
  ~0.5–1 ATR of typical follow-through; the exit must match the claim "keeps going,
  but not that much"):**
  - **X1 modest TP:** fixed take-profit at 1.0×ATR_H4 beyond the stretch extreme
    (the measured continuation scale; high-WR / low-R profile).
  - **X2 time exit:** no TP; close at market after 12 H4 bars (~2 trading days).
    The cleanest test of "there is a drift" — captures whatever exists without
    demanding a magnitude.
  - **X3 ATR trail:** no TP; trail the stop 1.5×ATR_H4 behind the favorable extreme
    once the trade is ≥ +0.25R; ride until the trail or the EMA-stop is hit.
  All three share the EMA-stop above. No other exits; no tuning of these numbers.
- **Sizing:** README 1% exact. **Calendar:** W-in-hold → no arm; forward V ≤6h → no
  arm (Option-A doctrine), and log event class of the stretch-maker.

## Subsets (all pre-named now)

1. **SHOCK (the trader's claim):** a D1 bar ≥1.8×ATR_D1 range (or body ≥1.5×) within
   the last 3 D1 bars at signal time. This is the hypothesis subset.
2. ALL (control).
3. Scheduled vs unscheduled stretch-maker (informational).

## Protocol

Real ticks, model as available (model-1 acceptable per the shock re-run precedent),
recorded spread. Full available ranges: EURUSD 2016-07→2026-06, GBPUSD 2013-07→2025-12,
USDJPY 2020→2025, XAUUSD 2020→2025. Symbol holdout (EUR+GBP fit / JPY+XAU holdout) AND
the 2023-24 date holdout. Report per cell: n, WR, avg R, total R, max consecutive
losers, weekend-hold share, realized gap slippage in R, FTMO daily breaches at 1%.

## Acceptance (fixed before the run)

A v2 integration spec is written ONLY if the **SHOCK subset** shows, for at least one
(entry × exit) cell of E1/E2 × X1/X2/X3 (named in the report, judged separately — not
blended; and because six cells invite cell-luck, the passing cell must ALSO show
avg R ≥ 0.0 for the same exit under the other entry — a robustness floor, fixed now):
- n ≥ 60 overall, avg R ≥ **+0.25**, WR ≥ 30% (trend-trade profile; the R floor is
  the real bar),
- same sign in symbol-fit and symbol-holdout,
- 2023-24 date holdout ≥ 0.0 avg R,
- positive avg R in ≥ 3 of 4 symbols.

Anything less → file the rejection appendix on `shock-continuation-result.md`, keep
the harness, stop. No parameter iteration beyond what is written here: the stop rule,
targets, and thresholds above are frozen; a "nearly passed" cell does not buy a re-run.

## Out of scope

Any training-path or live use; any change to the three detectors; any additional
entry/stop/target variants; Level 7 (runs on the frozen system regardless).
