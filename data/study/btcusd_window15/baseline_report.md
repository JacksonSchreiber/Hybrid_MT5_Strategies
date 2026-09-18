# Window 15 — BTCUSD.dk 2023 — AA baseline report (coach 2026-09-18)

**Baseline (official, graded against):** `journal/baselines/BTCUSD.dk_20230101_20231231_AA_ALL.csv` (+ `.actions.csv`,
`.costed.csv`, README sidecar). Headless AA_ALL, real ticks, H4, $25k, four-detector lineup, **§10.2 ATR floor 1.385 ON**
(BTCUSD only, widen-only) and **§10.1 weekend-flat ON**. A second run with the floor OFF (weekend-flat still on) is parked
beside it as `*.nofloor.csv` for the with/without comparison the coach asked for.
**Grading basis:** costed R (`pipeline/cost_journal.py`), same script and parameters as window 14.

## §10.2 floor — what it did
| | value |
|---|---|
| setups widened | **35 of 70** (50%) |
| mean widening | **0.525 ATR** (median 0.521) |
| mean pre-floor stop of the widened setups | 0.860 ATR (they were the tight tail) |
| widened by detector | DeepFib 6 · EMArevQ 3 · SweepMSS 6 · TrendCont 20 |

## Floor ON vs OFF (both weekend-flat, blind AA baseline)
| | floor 1.385 | no floor |
|---|---|---|
| journal rows / approved | 70 / 70 | 73 / 70 |
| **bank rate (+1R)** | **31.4%** | **38.6%** |
| **TP rate** | **12.9%** | **10.0%** |
| SL rate | 40.0% | 38.6% |
| BE rate | 18.6% | 27.1% |
| blind total R (costs-off) | -2.89 | +0.65 |
| **blind total R (costed)** | **-9.02** | **-6.40** |
| blind avg R (costed) | -0.1289 | -0.0914 |

The coach predicted bank and TP rates must fall with a wider stop. Bank rate did (38.6% → 31.4%);
TP rate rose slightly (10.0% → 12.9%) because the survivors run further in R terms before the
first target is reached. The stop-out rate barely moved (38.6% → 40.0%), so on this year's blind
base the floor cost more in reduced banking than it saved in avoided stop-outs: **costed -6.40R → -9.02R**.
Caveat: geometry changes cascade the signal set (the one-setup lock shifts), so the two runs are not the same trades —
70 vs 73 rows. This is the blind base only; the window measures whether the trader's selection beats it.

## Signal counts per detector (floor run)
DeepFib 6 · EMArevQ 6 · SweepMSS 12 · TrendCont 46

## §10.1 weekend-flat — fidelity check
- 50 weekend-boundary closes in the tester log ("Weekend-flat: closing signal #N").
- Every weekend-stamped exit is at **Sat 00:00** — the weekend boundary, i.e. the Friday close in continuous data. No exit
  sits inside weekend price, and no position is carried into a Monday gap.
- No decision is taken on a weekend bar.
- **Residual, for the coach:** 5 of 70 setups have a *signal candle* stamped Sunday 20:00 — decided on the
  Monday 00:00 bar (legal under the rule) but derived from a candle a Mon-Fri broker would not have printed:
  2023.06.25 20:00:00, 2023.07.02 20:00:00, 2023.09.03 20:00:00, 2023.10.08 20:00:00, 2023.11.19 20:00:00. Removing that residue needs a weekday-only price series, not a decision-time rule (see below).

## Costing parameters
Identical to window 14: OANDA BTCUSD.sim spec read 2026-09-16 — swaps −30.31%/−19.69% p.a. Mon–Fri, commission 0.0325%/side,
spread $3.09 charged per round trip; costs computed in R. `zero_spread_source` on both journals: True.
