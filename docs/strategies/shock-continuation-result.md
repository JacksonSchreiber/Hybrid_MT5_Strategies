# Shock Continuation (Strategy 4 candidate) — REJECTED

**Ruling:** rejected on the spec's acceptance bars; no salvage subset qualifies.
Detector + harness code kept for reuse. `InpUseShock` stays **off** in the training
path. Nothing here touches Level 7. (Engineer, coach re-run 2026-08-28; final
2026-09-05. Spec: [shock-continuation-spec.md](shock-continuation-spec.md).)

## Verdict

The blanket strategy fails the §4 bars, and the coach's pre-registered salvage rule
is not met by either named subset.

| §4 bar | required | result (full range) |
|---|---|---|
| sample | ≥ 150 trades | **117** |
| win rate | ≥ 40% | **25%** |
| avg R | ≥ +0.25 | **−0.035** (symbol-fit −0.070 / symbol-holdout +0.042) |

Even on the **maximum available tick history** (~32 symbol-years), the setup produces
only 117 trades — it fails on **frequency as well as edge**. Two of four symbols carry
a positive blanket avg R (GBP +0.14, XAU +0.35); the other two are firmly negative
(EUR −0.30 with 20 consecutive losers, JPY −0.25).

## Data range (answering "why were those years excluded")

The first backtest used EUR 2016-19 / GBP 2013-18 because those were the only ranges
**imported into the `.dk` custom symbols** at the time — the QDM store held the full
history, but it had never been materialized as `.dk` ticks in MT5. For this re-run the
full ranges were exported from QDM and imported: **EURUSD.dk 2016-07→2026-06 (276M
ticks), GBPUSD.dk 2013-07→2025-12 (337M ticks)**, verified dense end-to-end. USDJPY/
XAUUSD already carried 2020→2025. (The L7 windows remain covered as subsets.)

## Full-range results (default params, model-1, real spread on Mon-open gaps)

| scope | n | WR | avg R | total R | max CL |
|---|--:|--:|--:|--:|--:|
| EURUSD.dk (2016-25) | 38 | 18% | −0.303 | −11.5 | 20 |
| GBPUSD.dk (2013-24) | 42 | 29% | +0.140 | +5.9 | 8 |
| USDJPY.dk (2020-25) | 19 | 21% | −0.245 | −4.7 | 8 |
| XAUUSD.dk (2020-25) | 18 | 33% | +0.346 | +6.2 | 5 |
| **ALL** | **117** | **25%** | **−0.035** | −4.1 | 25 |
| symbol-FIT (EUR+GBP) | 80 | 24% | −0.070 | −5.6 | 17 |
| symbol-HOLDOUT (JPY+XAU) | 37 | 27% | +0.042 | +1.6 | 8 |
| date pre-2023 | 88 | 25% | +0.002 | +0.2 | 25 |
| **date-HOLDOUT (2023-24)** | 22 | 23% | **−0.207** | −4.5 | 9 |
| date 2025+ | 7 | 29% | +0.041 | +0.3 | 4 |

The strategy is break-even at best pre-2023 and **degrades to −0.207 R in the 2023-24
date holdout** — the opposite of a durable edge.

**Weekend-gap slippage:** 0.00 R this sample. 33% of trades are held across a weekend
(structural exposure), but the −1R stops were honored on the Monday opens in this data —
no gap-through slippage was realized. The exposure remains a live risk on a bad weekend
even though it did not bite here.

## Splits (hypothesis test)

| subset | n | WR | avg R |
|---|--:|--:|--:|
| scheduled shock | 62 | 29% | +0.122 |
| unscheduled shock | 55 | 20% | −0.211 |
| trend-aligned | 108 | 25% | −0.016 |
| counter-trend | 9 | 22% | −0.261 |

The spec predicted **trend-aligned shocks dominate**. They don't: 108 of 117 trades are
trend-aligned (that's just where the setups are), and the trend-aligned subset is
−0.016 R — break-even, not the predicted edge. The only mildly positive cut is
*scheduled* shocks (+0.122), which is not one of the pre-named salvage subsets and is
still well short of +0.25.

## Salvage evaluation (pre-registered rule)

A v2 spec was authorized only if a **single pre-named subset** (trend-aligned *or*
unscheduled — never a post-hoc combination) showed **avg R ≥ +0.25 at n ≥ 40, the same
sign in symbol-fit and symbol-holdout, and positive in ≥ 3 of 4 symbols.**

| subset | overall avg R (n) | per-symbol avg R | positive syms | same-sign fit/hold | pass? |
|---|---|---|--:|:--:|:--:|
| **trend-aligned** | −0.016 (108) | EUR −0.30, GBP +0.26, JPY −0.31, XAU +0.31 | 2/4 | no | **NO** |
| **unscheduled** | −0.211 (55) | EUR −0.24, GBP −0.71, JPY −0.58, XAU +0.56 | 1/4 | no | **NO** |

Neither subset clears any of the four conditions. **No salvage.**

## Disposition

- **File the rejection** (this document). Do not integrate; do not write a v2 spec.
- **Keep the code** — `mql5/include/Hybrid/detectors/ShockDetector.mqh` and
  `pipeline/backtest_shock.py` have reuse value (the pending-STOP entry path, the
  self-contained calendar gate reading the `class` column, the symbol/date-holdout +
  weekend-slippage backtest harness).
- **`InpUseShock` stays `false`** everywhere in the training path (EA default false;
  `start_level.sh` writes it false). The three live detectors are untouched. **Level 7
  is unaffected.**

### Notes for the record
- Two engineering bugs found and fixed during the re-run: (1) the full-range EUR/GBP
  import (they were 2025-only after the L7 coverage task); (2) the split sidecar wrote
  to the shared `Terminal/Common/Files/` (FILE_COMMON) while the driver read the
  terminal-specific folder — fixed in `backtest_shock.py`.
- The symbol-holdout substitution for §5 was accepted by the coach; the 2023-24 date
  holdout is added wherever data exists and is reported above.

---

# Appendix — EMArev-Inverse ("ride the stretch") also REJECTED (2026-09-05)

The trader's ONE pre-named follow-up ([emarev-inverse-spec.md](emarev-inverse-spec.md)):
when EMArev fires on an explosive-event stretch, trade **with** the stretch instead of
fading it. Different population and entry from shock-continuation (it triggers on the
live EMArev signal stream and enters at/near the signal). Per the spec, if this fails
its bars the post-shock-continuation claim is **dead twice — no v3**; the prospective
eye journal (`training/shock-eye-journal.md`) is the only path after.

**It fails.** Method: the frozen EMArev detector was wrapped unmodified and its signal
stream logged inverted (305 signals across the full ranges; **38 gated** by fwd-V-≤6h /
W-in-hold); the six pre-named cells (entry E1 market / E2 confirm-stop × exit X1 fixed-TP
/ X2 12-bar time / X3 1.5-ATR trail), all sharing the EMA-stop, were simulated in Python
on model-1 H4 bars (conservative stop-first intrabar).

**SHOCK subset (the hypothesis) — avg R / n, gate applied, and acceptance:**

| cell | overall avg R (n) | sym-fit | sym-hold | date 23-24 | pos syms | pass? |
|---|---|--:|--:|--:|--:|:--:|
| E1·X1 | −0.009 (74) | −0.02 | +0.01 | −0.21 | 2/4 | ❌ |
| E1·X2 | −0.072 (74) | −0.26 | +0.22 | −0.07 | 2/4 | ❌ |
| E1·X3 | −0.294 (74) | −0.36 | −0.19 | −0.36 | 0/4 | ❌ |
| **E2·X1** | **+0.107 (45)** | +0.17 | +0.02 | −0.02 | **3/4** | ❌ |
| E2·X2 | −0.052 (45) | −0.18 | +0.12 | −0.12 | 1/4 | ❌ |
| E2·X3 | −0.042 (45) | −0.02 | −0.08 | −0.11 | 2/4 | ❌ |

The acceptance bar was n ≥ 60, avg R ≥ +0.25, WR ≥ 30%, same-sign fit/holdout, 2023-24
≥ 0, ≥3/4 symbols positive, and a cross-entry robustness floor. **No cell clears it.**
The best is E2·X1 (WR 80%, positive in 3/4 symbols) but avg R is only +0.107 — under
half the bar — with n=45 (< 60) and a negative 2023-24 date holdout (−0.02). The ALL
control is the same story (best cell +0.034). X3 (trail) is the worst exit by far
(−0.29 R on the SHOCK subset), i.e. there is no ride to trail.

*Data note:* the EA's EMArev-inverse econ reader had an off-by-one (read `ccy_bias` as
the class column), which zeroed the calendar flags in the raw log; the gate and the
scheduled subset were re-derived in Python from `econ_events.csv` (fix also applied to
`EmaRevInvDetector.mqh`). The gate failed *open* in the raw log, so it could only have
added marginal signals — and applying it correctly (38 gated) left the rejection intact.

**Intrabar robustness:** only ~1% of X1 exits (4/305 E1, 2/188 E2) had an H4 bar
containing both the stop and the TP — the EMA-stop sits ~2–2.5 ATR out while the TP is
1 ATR beyond the extreme, so they rarely collide. The conservative stop-first rule is
therefore not outcome-determining, and an M1 re-run cannot lift E2·X1 by the ~0.14 R
needed. Full 6-cell table + subsets in `data/backtests/emarev_inv/REPORT.md`.

**Disposition:** the post-shock-continuation continuation claim is **dead twice**. No
v3; the prospective eye journal is the remaining path. Keep the harness + detector
(`EmaRevInvDetector.mqh`, `backtest_emarev_inv.py`). `InpUseEmaRevInv` stays **false**
in the training path (EA default + `start_level.sh`). **Level 7 is untouched.**
