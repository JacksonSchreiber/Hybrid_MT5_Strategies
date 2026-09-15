# Breakout replacement-candidate gate — FROZEN SPEC (coach 2026-09-09)

Fully offline (trader's ruling: no MT5 at this stage). Two frozen candidates backtested
in Python over the QDM M1 store, aggregated to H4, with an offline reconstruction of the
doctrine-v2 exit policy. **Frozen before the first run. No parameter search** — if neither
passes as frozen, report and stop; the alert slot stays empty and two live detectors
(SweepMSS, DeepFib) is a complete lineup.

This spec is written and committed BEFORE any candidate is run against any symbol.

---

## 0. Data path (offline)

- **Bars**: QDM M1 export (`qdmcli -data action=export ... timeframe=M1 format="Generic
  bar format (comma delimited)"`) per symbol, full available `.dk` range, aggregated to
  H4 in Python. M1 is bid OHLC.
- **Symbol-set rule (frozen):** the fleet = *every fleet symbol whose full-range M1 is
  obtainable offline from the QDM store or a QDM/Dukascopy download that completes within
  T+3h of the run start*. Symbols in QDM's local store (EURUSD, GBPUSD, USOIL=LIGHTCMDUSD,
  US500=USA500IDXUSD, US100=USATECHIDXUSD) are exported directly. USDJPY and XAUUSD are not
  in the local store; a background `-symbol action=add` + download is attempted once — if it
  does not finish by T+3h, those symbols are **excluded and disclosed** in the report. The
  gate is evaluated ONCE, after the download resolves either way. (Symbol availability is not
  outcome-dependent, so this rule is pre-registration-clean.)
- **H4 aggregation**: group M1 by 4h boundaries anchored to the same server clock as the
  existing MT5 H4 dumps (bars open at 00/04/08/12/16/20). D1 = server-calendar-date
  aggregation of the H4 series.
- **Spread**: QDM M1 is bid. A per-symbol fixed spread constant is added on entry (buy
  fills at bid+spread, sell at bid−spread; SL/TP compared against bid). Constants frozen
  in `SPREAD` below and disclosed. This is the offline proxy for real fill; tick-accuracy
  is deferred to a passer only.

## 1. Regime tag (exact port of the live D1 formula)

`TrendDir(D1)`, computed on D1 bars at or before the signal (no lookahead):
- EMA200(D1); ADX14(D1).
- `+1` (TREND_UP) iff close(D1,1) > EMA200(1) AND EMA200(1) > EMA200(11) AND ADX(1) ≥ 20.
- `-1` (TREND_DOWN) mirror. Else 0 (no trade).
- ~210 D1-bar warmup before the first eligible signal.

## 2. Candidate 1 — DonchianBreak (FROZEN)

- **Direction gate**: regime TREND only; long only in TREND_UP, short only in TREND_DOWN.
- **Trigger**: H4 **close** beyond the 55-bar Donchian channel extreme. Channel = max(high)
  / min(low) over the **55 H4 bars preceding** the signal bar (exclusive of the signal bar).
  Long: close(1) > highest_high(2..56). Short: close(1) < lowest_low(2..56).
- **SL**: 1.5 × ATR(14, H4) from entry (long: entry − 1.5·ATR; short: entry + 1.5·ATR).
- **Declared cut (pre-registered secondary)**: breakout within 3% of the 250-bar extreme vs
  not — `|close − extreme_250| / extreme_250 ≤ 0.03`, extreme_250 = the same-side 250-bar
  extreme. Both cut cells reported against the same four gates. The PRIMARY gate is the full
  (uncut) set; the cut is a secondary, chosen by definition not by result.

## 3. Candidate 2 — SqueezeBreak (FROZEN)

- **Squeeze condition**: BB width(20, 2σ) in the lowest quintile (≤ 20th percentile) of the
  trailing 120 H4 bars. width = (upper − lower)/mid, mid = SMA20(close).
- **Compression range (frozen)**: max(high)/min(low) over the 20 BB bars ending at the last
  squeeze bar (the most recent bar still in-squeeze before the breakout).
- **Trigger**: first H4 close beyond the compression range after the squeeze.
- **Direction gate**: regime-aligned only (long break in TREND_UP, short break in
  TREND_DOWN; a break against the regime is discarded).
- **Breakout expiry (frozen)**: the breakout must occur within 6 H4 bars of the squeeze
  ending; else no signal.
- **SL**: far side of the compression range (long: min_low of range; short: max_high).

## 4. Doctrine-v2 exits (both candidates, exact policy)

- **Ladder (frozen)**: tp1 = 2R (inert marker), **tp2 = 4R = the order TP / runner target**,
  partial_fraction = 0.5. NOTE: neither candidate's brief states a runner TP; per the
  coach's "reasonable freedom, freeze before running", the runner target is frozen at **+4R**
  by the TrendCont precedent (accepted without comment in study B), identical for both
  candidates so they stay comparable to each other and to study B. **Flagged spec gap.**
- **v2 policy**: trigR = min(1, tp1R) = **+1R** (two-target). At first touch of +1R: bank
  50% (realise +0.5R on the closed half), move SL to break-even + pad. padR = 1.0 pip /
  risk (`InpBEPadPips=1.0`). Runner (remaining 50%) exits at tp2 (+4R → total +2.5R) or at
  BE (+padR → total ≈ +0.5R) or, pre-bank, at the structural SL (−1R).
- **v2_r** = 0.5·bankR + 0.5·runnerR (bank at exactly +1R unless the same M1 bar gaps past;
  runner in {+4R, +padR}). Pre-bank SL → v2_r = −1R.

## 5. Intrabar fill / stop resolution (M1, conservative)

- **Entry**: open of the first M1 bar at/after the signal H4 bar's close, ± spread.
- **Pre-bank**, if one M1 bar's [low,high] spans both SL and +1R → **SL first** (pessimistic).
- **Post-bank**, if one M1 bar spans both BE and +4R → **BE first** (pessimistic).
- One position at a time (one-position lock): a new signal while a position is open is
  dropped (matches the live one-setup lock).
- Indicators computed once on the H4/D1 series; M1 is walked only within each trade's
  lifetime (entry→exit), never for indicator computation.

## 6. Pass bar — the four study-B gates (ALL required)

1. avg ≥ **+0.15R** / trade (net, across the evaluated fleet).
2. **n ≥ 150** fleet-wide.
3. both date halves positive (split at the fleet-wide median signal time).
4. no single symbol contributes **> 50%** of net R.

Passes → takes the alert slot after a tick-accurate MT5 re-port + one short visual sanity
window (that step is NOT part of this gate). Fails → report and stop, no parameter search.

## 7. Frozen constants

```
DONCHIAN_N      = 55        # channel lookback (bars preceding signal)
DONCHIAN_250    = 250       # declared-cut extreme lookback
DONCHIAN_CUTPCT = 0.03
DON_SL_ATR      = 1.5
ATR_PERIOD      = 14
BB_PERIOD       = 20
BB_SIGMA        = 2.0
SQUEEZE_TRAIL   = 120       # width-percentile lookback
SQUEEZE_PCTL    = 20        # lowest quintile
SQUEEZE_EXPIRY  = 6         # H4 bars after squeeze end
EMA_D1          = 200
ADX_D1          = 14
D1_SLOPE_BACK   = 10
ADX_MIN         = 20
TP1_R = 2.0 ; TP2_R = 4.0 ; PARTIAL = 0.5 ; TRIG_R = 1.0
BE_PAD_PIPS     = 1.0
# SPREAD (price units, per symbol) — frozen offline fill proxy, disclosed:
#   FX 5-digit: 0.00010 ; JPY 3-digit: 0.010 ; XAU: 0.30 ; USOIL: 0.03
#   US500: 0.5 ; US100: 1.0   (indices, points)
```
_Frozen 2026-09-09 before any run. Backtester: `pipeline/breakout_backtest.py`;
report: `data/study/breakouts_report.md`._
