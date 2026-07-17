# Detector Implementation (Phase 2 core)

What was built for task #14: the three real strategy detectors and their
wiring into the harness, verified headless on `EURUSD.dk`.

## What was built

| File | Contents |
|---|---|
| `mql5/include/Hybrid/Signal.mqh` | `SignalCandidate` extended (tp1/tp2/partial_fraction, d1_context, comment, aux_*, zone2, leg_*). Interface unchanged. |
| `mql5/include/Hybrid/detectors/DetectorCommon.mqh` | Shared primitives: `LotsForRisk` (the 1% math, used by harness + detectors), `DC_Buffer`, `DC_Fill`, STRICT fractals, `DC_CollectSwings`, reversal-candle helpers, and **`ResetCandidate`**. |
| `.../detectors/SmcDetector.mqh` | `CLiquiditySweepMSS` — sweep → MSS → order block → ARMED → emit-on-pullback. |
| `.../detectors/FibDetector.mqh` | `CDeepFibRetrace` — in-trend deep 0.618–0.786 retracement + reversal trigger. |
| `.../detectors/EmaDetector.mqh` | `CEma20MeanRev` — 2-ATR stretch from EMA20 + reversal trigger. |
| `mql5/experts/HybridForwardTest.mq5` | Priority arbitration, two-target scale-out execution + blended journal, auto-approve, extended overlays. |
| `pipeline/mt5_verify.sh` | Headless Strategy-Tester driver (reuses the auto-import launch pattern with a `[Tester]` ini + `.set`). |

All detection runs on **closed bars** (as-series index ≥ 1), no repaint. Handles
are created lazily and cached per symbol. D1 context is read via `PERIOD_D1`
handles/`iHigh/iLow` — a **flag**, never a hard filter (PM decision 1), except
the EMA D1 fresh-breakout guard which is a filter (that strategy's own default).

## Priority arbitration & one-setup-per-symbol (PM decision 4)

Every new H4 bar the harness calls **all** enabled detectors (so each advances
its state machine) and keeps the **highest-priority** valid emit — order
**SMC (1) > Fib (2) > EMA (3)**. While a position is open on the symbol, new
emits are suppressed (one active setup per symbol). Portfolio/daily caps are
deferred to the Phase-3 orchestrator (out of scope here).

## Two-target scale-out (PM decision 2)

Fib and EMA place the full 1%-risk position with TP = **TP2 (runner)**; the
harness banks `partial_fraction` (0.5) at **TP1** en route, moves the stop to
**breakeven**, and lets the remainder run to TP2. Managed every tick (a bar can
blow through TP1). **SMC is single-TP** (partial_fraction = 0) at opposing
liquidity. The journal's `r_multiple` is the **blended, volume-weighted R**
accumulated across the (up to two) closing deals:
`R = Σ (deal_volume / initial_lots) · (moved / risk_px)`, with
`risk_px = |entry − original SL|` (the BE move never changes the risk basis).

Verified example (EMA): bank half at TP1 (≈+1.6R contribution), runner to TP2 →
blended R 3.20; runner stopped at BE → blended R 0.84; full stop → −1.00R.

## Headless auto-verify input

`InpAutoApprove` (**tester-only**, hard-gated to `MQL_TESTER`):
- `AA_NONE` (default) — interactive modal (visual tester + DLLs required).
- `AA_ALL` — auto-approve every signal, no modal → exercises the full
  order/scale-out/journal lifecycle.
- `AA_SKIP` — auto-deny every signal, no modal → every bar all detectors are
  evaluated (no position ever suppresses detection), so this gives the cleanest
  per-strategy signal counts.

The modal DLLs are touched **only** when `AA_NONE`, preserving the
"DLL only in the visual tester" guarantee.

Re-run: `./pipeline/mt5_verify.sh --mode ALL|SKIP --strat SMC,Fib,EMA --from D --to D`.

## Verification results (EURUSD.dk, H4, Model=1)

Detectors evaluate correctly (a detection **funnel** is printed per detector at
deinit — a deliberate QA readout, e.g. `SMC funnel: bars=… sweep=… mss=… arm=…
EMIT=…`). Per-strategy counts:

| Mode | Window | SMC | Fib | EMA | Notes |
|---|---|---|---|---|---|
| AA_SKIP (presented) | 2021–2024 | 57 | 17 | 47 | arbitration winners per bar |
| AA_ALL (presented) | 2021–2024 | 49 | 14 | 39 | fewer — positions suppress detection |
| AA_ALL (closed) | 2021–2024 | 42 | 12 | 29 | SMC single-TP; Fib/EMA two-target |

> **"Presented" undercounts lower-priority strategies on coincident bars** —
> only the arbitration winner is journalled each bar, so on a bar where SMC and
> EMA both fire, EMA is not counted.

The 2021–2024 AA_ALL run completed clean ("Test passed", no critical error),
final balance 31,712 from 25,000 (dummy, untuned parameters). Blended R values
are sane (losers −1.00, scale-out winners fractional-to-multi-R). SMC rows carry
`partial_frac=0` (single TP); Fib/EMA rows carry `0.50`.

## Deviations from the blueprints (with reasons)

1. **Dropped the `BarsCalculated()` pre-guard** (impl-README §1 mandated it).
   In the Strategy Tester, indicators calculate **on demand when `CopyBuffer`
   is called**; gating on `BarsCalculated` *before* `CopyBuffer` deadlocks
   (it stays 0 forever, detector never runs). We rely on `CopyBuffer`/`CopyRates`
   return counts instead. *(This was a real bug found in verification.)*
2. **`ResetCandidate` instead of `ZeroMemory(out)`** — MQL5 does not reliably
   zero-initialise a locally-declared string-bearing struct, so `aux_count`
   held stack garbage and `DC_AddAux` indexed out of range (intermittent crash
   → OnTester critical error). Explicit, string-safe reset fixes it.
3. **SMC `partial_fraction = 0` (single TP)** — PM decision 2 overrides the
   blueprint's advisory 0.5.
4. **EQL pools not distinctly classified** — the SMC pool set is confirmed swing
   lows/highs + PDL/PWL/PDH/PWH; `pool_type=1` (EQL cluster) is detected as
   swing pools, not separately labelled.
5. **SMC `ref_swing` simplified** to the nearest opposing confirmed fractal
   (rather than "the swing formed at/after the down-move into the sweep").
6. **SMC "pool not traded below since formed" and the FVG/`zone2` refinement**
   are not implemented (order block only).
7. **Fib D1 bias = D1 EMA50 regime only** (no D1 fractal structure) — sufficient
   for the flag.

These are Phase-2 simplifications; none change the risk model (SL-based 1%
sizing) or the emit contract. They can be tightened in tuning.

## Caveats needing the user's eye / a follow-up

- **Market-closed order failures.** Several approved signals fail with
  `10018 market closed` at H4/day boundaries because the `.dk` symbol's
  *inferred* specs set no trading sessions. This affects the live visual demo
  and likely wants a **session-spec fix in the importer** (`Hybrid\TickImport`),
  not here. Signals still fire and journal; only the fill is refused.
- **Read the R column, not PnL/lots.** Blended R is price-based (`moved/risk_px`)
  and trustworthy. PnL and lot sizes depend on the inferred `tick_value`
  (≈correct for EURUSD) and scale inversely with SL distance, so tight-SL
  setups show large lots/PnL — representative, not authoritative.
- **Model=1 (1-min OHLC)** was used for verify speed; production uses real
  ticks, under which the intrabar TP1-crossing sequence differs slightly.
- **Overlays and the coloured-modal text are compile-verified only.** The
  aux-level / zone2 / impulse-leg rendering and the folded comment/D1/scale-out
  lines in the dialog need a **visual interactive run** to eyeball.
