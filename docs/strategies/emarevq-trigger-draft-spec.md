# EMArevQ — quiet-trigger EMArev — DRAFT trigger spec (coach 2026-09-09)

EMArev returns as a NEW detector, **EMArevQ** (quiet-trigger only). This is NOT the retired
alert class — the base is unchanged but the **trigger is redefined** by a whole-climb quiet
filter, and A/B/C are **DRAFT** values calibrated to the trader's eye (item 2) before freezing.
Discretion-class everywhere. Retired EMArev alert path stays off.

## Base conditions — UNCHANGED from CEma20MeanRev (`EmaDetector.mqh`), verbatim
No change to any of these; EMArevQ inherits the exact base object's logic:
- **Stretch**: `(EMA20 − close)/ATR(14) ≥ STRETCH_MIN` (fade up, dir +1) or
  `(close − EMA20)/ATR ≥ STRETCH_MIN` (fade down, dir −1). STRETCH_MIN = 2.0 (H4 EMA20, ATR14).
- **Gates**: ADX(14) < 30 (ADX_CEILING); D1 fresh-breakout hard gate (no break of the prior
  20-D1 extreme).
- **Forming → reversal trigger** within ENTRY_VALID_BARS = 3 bars of the extreme: a `toward`
  move AND (engulfing | pin wick ≥1.5 | close beyond prior bar's extreme). Invalidation on
  cross-back, runaway (>1.0 ATR beyond extreme), or ADX ≥ 30.
- **Levels**: entry = trigger-bar close; SL = stretch extreme ± 0.10 ATR; TP1 = EMA20 (mean),
  TP2 beyond (MIN_RR 1.3); partial 0.5; doctrine-v2 exits apply on top (bank +1R / BE / runner).

## NEW — whole-climb quiet filter (the redefinition; DRAFT A/B/C)
Evaluated over the **entire excursion (the "climb")** = the contiguous H4 bars from the **last
bar that touched EMA20** (the last bar whose [low,high] contains the EMA20 value, scanning back
from the signal bar) up to and including the **signal (reversal-trigger) bar**. Let
`total_stretch` = |stretch extreme − EMA20 at the climb start| (price units). A trigger passes
EMArevQ only if ALL three hold:

- **(A) No violent bar:** no single H4 bar in the climb has range (high−low) > **1.2 × ATR(14)**.
  _Draft A = 1.2._
- **(B) Slow means time:** climb length ≥ **8** H4 bars. _Draft B = 8._
- **(C) No dominant bar:** the largest single bar's contribution to the stretch is < **30%** of
  `total_stretch`. Per-bar contribution = new ground made away from EMA20 that bar =
  `max(0, dist_i − max(dist_0..i−1))`, `dist = |bar extreme − EMA20|`; the positive
  contributions sum to `total_stretch`. _Draft C = 0.30._

Intent: EMArevQ fires only on a **slow, grinding drift** away from the mean — never a violent
spike or a one-bar gap. Consistent with the earlier EMArev-by-trigger study (quiet cells were
the least-bad); the trader wants only the quiet-drift stretches, judged by his eye.

**These A/B/C are DRAFT.** They will be tuned once to the trader's marks (item 2) and then
frozen. Expectation after freeze: EMArevQ fires **rarely** — if it fires often it is
mis-calibrated and the trader is re-consulted. No outcome data is consulted during calibration.

## Class / integration (after freeze)
- Strategy name **EMArevQ**; own magic; popup **PROTOCOL: DISCRETION** (discretion-class in all
  regimes); full parity (journaling / AA / delay / FREEZE / doctrine-v2 exits). Retired EMArev
  alert path stays off.
- Blind-base measurement (NOT a gate): after freeze, offline backtest fleet-wide (v2 exits, same
  estimator as the other studies) → report avg R, n, per-symbol-year cadence, for the coach to
  grade discretionary uplift.

## Calibration procedure — FROZEN before any marks exist (item 2)
Declared now so the tuning is mechanical, not fit to the answers after the fact.

1. **Blind extract** (`pipeline/emarevq_calib_extract.py`): from the mfe EMArev cohort, write
   `data/study/emarevq/triggers_blind.csv` with ONLY `symbol, signal_time, direction, to_entry,
   to_sl` — no outcome column exists in that file. Every step below reads only it. Outcome
   re-enters only in item 3, after the freeze.
2. **H4 + anchor validation:** aggregate QDM M1 → H4 (00/04/08/12/16/20 server), EMA20 + ATR14.
   For each trigger, the H4 bar at `signal_time` must have `|close − to_entry| ≤ 0.10·ATR`
   (H4-clock alignment check; `to_entry` = trigger-bar fill ≈ close). Drop triggers that fail;
   **if >5% drop, STOP — the H4 aggregation is misaligned with MT5 — fix before rendering.**
3. **Metrics** over the climb (last EMA20 touch → signal bar): A = max bar range/ATR(14);
   B = climb length in H4 bars; C = max single-bar contribution / total_stretch, where
   contribution = new ground away from EMA20 and total_stretch = |extreme − EMA20 at start|
   (extreme ≈ to_sl ± DC_Buffer, DC_Buffer = max(0.10·ATR, spread, stops)).
4. **Signed normalized margins:** mA = (1.2 − A)/σA, mB = (B − 8)/σB, mC = (0.30 − C)/σC
   (σ = cross-cohort std). Buckets → ~6 each: **clear-pass** (all m>0, min large), **clear-fail**
   (≥2 axes fail hard), **borderline-fail-A / -B / -C** (exactly one axis fails, small |margin|).
   = 30. Random within bucket, **seed logged**. Thin buckets reported + rebalanced.
5. **Render** 30 blind H4 candlesticks (candles + EMA20 line + one marker on the trigger bar,
   ~40 bars context). NO symbol / date / outcome / metric values / climb bracket — the trader's
   eye decides where the drift starts and whether it is slow. Numbered 1–30 only.
6. **Marks:** trader marks each **slow drift / not / can't tell** (3-state). Can't-tell excluded
   from tuning, count reported.
7. **Tune once:** grid A∈{1.0,1.1,…,1.6}, B∈{5,…,12}, C∈{0.20,0.25,…,0.45}; pick the joint
   (A,B,C) maximizing agreement with his yes/no on the marked sample; ties → the STRICTER set
   (rarer firing). Report agreement + confusion matrix. **FREEZE.**
8. **Rarity check:** fleet firing rate at the tuned (A,B,C) over the full cohort. If it fires
   more than ~1 per symbol-quarter, that is "firing often" → re-consult the trader, do NOT
   re-tune silently. If clear-fail charts are marked "slow drift" at any real rate, the filter
   SHAPE is wrong → back to the coach, not a numeric tune.

## Status
DRAFT. Item-2 calibration is pending the trader's marks (blind ~30-chart sheet). Items 3–4 gate
on the freeze.
