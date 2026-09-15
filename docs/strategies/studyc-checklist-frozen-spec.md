# Study C — checklist-criterion calibration — FROZEN SPEC (coach 2026-09-09)

Offline, on-disk data only (no MT5, no qdmcli). For every TAKEN signal in the fleet set,
compute machine-computable checklist criteria, measure each criterion's marginal effect on
**doctrine-v2** outcomes per regime, fit a simple additive points score (fit pre-2022,
validate 2022+ untouched), and report whether any threshold achieves **≥72% skip precision
in TREND** and what it costs in forgone R. Deliverable: a per-regime scoring rule simple
enough to print on the popup, OR the honest finding that no computable subset clears
break-even in TREND. **No hand-tuning past the validation split. If it fails validation, it
fails.** Frozen before any fitting.

## Data
- Signals: `data/study/mfe/*.study.csv` — 1527 taken (approved) fleet signals (SweepMSS 711,
  EMArev 635, DeepFib 181). Closed signals only (drop any open-at-end).
- Calendar: `Common/Files/econ_events.csv` (V/W/C/H class, currency, time).
- Bars (Tier 2 only): `data/backtests/emarev_inv/*.h4.csv` (EURUSD 2016+, GBPUSD 2013+,
  USDJPY 2020+, XAUUSD 2020+; US100/US500/USOIL absent or partial → NOT in Tier 2).
- Parse with `csv.DictReader` (NOT awk — the `terminal` field has a row-shape quirk).

## Outcome — doctrine-v2 (exact sign; hybrid magnitude)
The stored `r_multiple` is the OLD full-position exit; doctrine-v2 must be derived from the
stored touch-order + mfe fields (as in `pipeline/v2emarev_cells.py`).
- **Sign is EXACT for every closed signal** (this is what the ≥72% *precision* gate needs):
  - trigR = min(1, tp1R) if two-target else 1.0; padR = 1.0 pip / risk.
  - If mfe_r ≥ trigR → the trade BANKED; worst case runner returns to BE at +padR, so
    v2_r ≥ 0.5·trigR + 0.5·padR > 0 → **WIN (sign +)**, exactly.
  - If mfe_r < trigR → v2 never banked; v2_r = old_r exactly → sign = sign(old_r).
- **Magnitude (forgone R only)**: hybrid v2_r from v2emarev_cells.py (old_r for no-bank;
  observer/analytic for bank-fires), fleet-wide, with its known optimistic skew disclosed.
- **Estimator validation**: on the 620 EMArev cohort where observer truth exists, confirm
  sign(hybrid) == sign(observer) for 100% of closed rows before trusting it on SweepMSS/DeepFib.

## Criteria — Tier 1 (fleet-wide, journal + calendar) → the SCORE is built ONLY from these
1. **thin_session**: signal H4 bar opens at 00:00 or 04:00 server time (overnight/thin).
2. **calendar_hot**: a V- or W-class event in the signal's symbol currency within ±4h of
   signal_time (port `CalLabeled`).
3. **strategy**: SweepMSS / DeepFib / EMArev (categorical).
4. **with_trend**: stored with_trend flag (1 = with regime, 0 = counter).
5. **wide_stop**: within-symbol z-score of stop width |orig_entry−orig_sl|/orig_entry in the
   top tercile (a structurally wide, low-R-density stop).
6. **near_target**: target distance to tp1 in R = |orig_tp1−orig_entry|/risk in the LOW
   tercile (little room to the first target).
7. **is_pending**: stored is_pending flag.

DROPPED: **delay-vs-immediate** — the fleet set is blind-approve (AA), decision_ms≈0 with
zero variance; not measurable here. Reported as dropped with this reason (no proxy substituted).

## Criteria — Tier 2 (sub-fleet appendix; marginals only, NOT in the score)
8. **trend_age_d1**: consecutive D1 bars the current regime tag has held at signal time
   (D1 aggregated from the H4 dumps; port TrendDir). Reported on the 4-symbol bar sub-fleet.
9. **dist_to_obstruction**: nearest prior H4 swing between entry and tp1, in R. Same sub-fleet.

## Method (frozen)
- Stratify by regime tag. **TREND = TREND_UP ∪ TREND_DOWN pooled** for the ≥72% test (per
  coach wording); the split is also reported. Exclude NONE/blank regime (~68).
- **Marginal effect**: for each Tier-1 criterion, per regime, report doctrine-v2 loss-rate
  and avg v2_r with the criterion present vs absent, with n.
- **Additive score (frozen before fitting)**: +1 point per Tier-1 criterion whose FIT-HALF
  marginal loss-rate is adverse (criterion-present loss-rate exceeds criterion-absent by
  ≥ 8 percentage points) in TREND. Score = count of adverse criteria a signal trips. Higher
  score = worse expected doctrine-v2 outcome = skip candidate. (The 8pp threshold and the
  +1 weighting are frozen NOW, before looking at any marginal.)
- **Fit / validate**: fit half = signal_time < 2022-01-01; validate = 2022+ (untouched).
  First, COUNT TREND signals in each half and report — if 2022+ TREND is thin, say so.
- **Skip rule**: on the fit half, find the lowest score threshold S* such that skipping all
  TREND signals with score ≥ S* achieves ≥72% precision (fraction of skipped that are
  doctrine-v2 losers). Then evaluate that SAME S* ONCE on 2022+: report skip precision, n
  skipped, and forgone R (sum of v2_r of the skipped WINNERS). ≥72% on validate → a rule;
  <72% → fails, no second look.
- Report the skipped-set n prominently (no floor imposed by coach, but a tiny cell must not
  read as a result).

## Deliverable
`data/study/studyc_report.md`: per-criterion marginals (per regime), the frozen score, the
fit-half threshold, the single validation-half evaluation, and either the printable
per-regime rule ("Score N/7 — TREND bar is X") or the honest null finding.

_Frozen 2026-09-09 before fitting. Code: `pipeline/studyc_calibration.py`; outputs under
`data/study/studyc/`._
