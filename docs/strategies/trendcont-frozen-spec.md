# TrendCont — trend-continuation detector (FROZEN 2026-09-09, coach part B)

Pre-registered replacement candidate for EMArev's alert slot. **Frozen before running;
no parameter search on fail.** Study-only until it passes AND 10b closes (input default OFF
→ live path untouched).

## Family (coach)
Machine-tagged D1 TREND → H4 pullback toward the EMA20 → a close resuming the trend
direction. Structural SL beyond the pullback swing; R-based targets; doctrine-v2 exits;
1% risk; one-position lock.

## Frozen definition

**Timeframe:** H4 (working tf). **Indicators:** H4 EMA20(close), H4 ATR(14); D1 EMA200(close),
D1 ADX(14). All indicator handles created once per symbol (in `EnsureHandles`, not per tick).

**Context gate — D1 regime (the frozen tag formula, D1 last-closed bar, shift 1):**
- `trend_dir = +1` iff `close(D1,1) > EMA200(D1,1)` AND `EMA200(D1,1) > EMA200(D1,11)`
  (200-EMA rising over 10 D1 bars) AND `ADX(D1,1) ≥ 20`.
- `trend_dir = −1` iff the mirror (below, falling, ADX ≥ 20).
- else no signal. (Warm-up: the D1 200-EMA needs ~210 D1 bars, so the first ~10 months of
  each symbol's range yield `trend_dir=0` — correct, but the early date-half is thinner.)

**Trigger** — exactly two conditions (the coach's family, nothing added). Signal bar = the
last closed H4 bar (shift 1). For `trend_dir = +1` (long); short mirrors (high↔low, >↔<):
1. **Pullback to the EMA20 within the last 3 H4 bars:** any bar `i ∈ {1,2,3}` has
   `low(i) ≤ EMA20_H4(i)` — the EMA20 was touched or breached from the trend side, each
   bar tested against its own contemporaneous EMA20 value. (K=3 matches the SL swing below.)
2. **Resume close on the signal bar:** `close(1) > EMA20_H4(1)` AND `close(1) > open(1)` —
   a bullish close back on the trend side of the mean.

**Entry:** market fill at the signal-bar close (`DC_Fill(sym, dir, close(1))`).

**Structural SL (beyond the pullback swing, bars 1–3):**
- long: `SL = min(low(1), low(2), low(3)) − 0.10·ATR(14)`
- short: `SL = max(high(1), high(2), high(3)) + 0.10·ATR(14)`
- `risk = |entry − SL|`. Tiny/degenerate stops are rejected by the EA's existing
  `InpMinStopATR = 0.5·ATR` floor (inherited, not re-added here).

**Targets (R-based, doctrine-v2 two-target):** `tp1 = entry ± 2R`, `tp2 = entry ± 4R`,
`partial_fraction = 0.5`, order `tp = tp2`, `rr = 4.0`. **Under doctrine-v2 the bank fires at
`min(+1R, tp1) = +1R` regardless, so `tp1=2R` is inert** — the effective ladder is
{+1R bank 50 % + SL→BE, runner 50 % to 4R}.

**Sizing / concurrency:** 1 % risk (`LotsForRisk`, RISK_PCT=0.01); the EA's one-setup lock;
the EA's degenerate-stop safety gate. Reject if lots < broker min.

**Name / journal token:** `TrendCont`.

## Amendment — per-pullback re-arm (coach live-defect order, 2026-09-09)

Authorized after TrendCont went live in the window-11 era lineup (`SMC,Fib,TrendCont,EMArevQ`;
the "study-only / default OFF" framing above is superseded). **Defect:** the frozen trigger has
no re-arm state, so when a popup is *declined* it opens no position, the EA's one-setup lock never
engages, and the trigger (pullback-in-bars-1–3 ∧ resume-close) re-fires **every H4 bar** it
persists. AA blind-approve runs masked it (approvals open a position → the lock suppresses the
repeats); a live decline surfaced it (window 11 aborted on first attach).

**Rule (added to the trigger; nothing else changes):** one signal per pullback. After **any**
TrendCont emit (any decision) on a symbol, **disarm**; re-arm only after the resume leg AND a fresh
touch, in order:
1. **resume leg** — a signal-bar **close** ≥ `0.75·ATR(14)` beyond the EMA20 in the trend
   direction (`close(1)−EMA20(1) ≥ 0.75·ATR` for a long; mirror for a short). Wick excursions do
   not count — the close must clear the band.
2. **touch** — a later bar returns to the EMA20 (`low(1) ≤ EMA20(1)` long / `high(1) ≥ EMA20(1)`
   short). This is the new pullback.
Both, in that order, on **separate bars** → re-arm (coach tighten 2026-09-10: the intent is a
*completed micro-cycle* — leave, then return — so one bar may **not** serve as both the resume leg
and the touch; the touch re-arms only if the resume leg was already seen on an earlier bar). A D1
trend **flip** to the opposite side also re-arms immediately
(the old pullback is dead); a mere trend **loss** (`trend_dir=0`, e.g. ADX dips below 20) does
**not** — the same H4 pullback persists, so re-acquisition on the same side still requires a fresh
resume leg + touch. Emit only while armed.

**Scope / comparability:** implemented once in `CTrendContinuation::Detect`
(`REARM_ATR=0.75`, state `m_block_dir`/`m_resumed`). The offline study path *is* the EA fleet
(`run_trendcont_fleet.sh` → `mt5_verify.sh --strat TrendCont`; `trendcont_eval.py` reads the EA
journals, no separate Python detector), so the fix applies identically to offline, AA, and live
by construction. Verified before/after (both phases same model, so the re-arm is the only variable):
SKIP-mode raw emits collapse and after ⊆ before (purely subtractive, 0 invented — the detector fix
is sound); the AA taken base is **NOT** materially unchanged — the lock masked every-bar re-fires
but not same-pullback re-entries after a fast stop-out, so the base drops ~16% and its composition
churns. Those re-entries averaged +0.111R (the defect was inflating the number). Reference line of
record: **model-4, tightened launch binary, +0.041R (FAIL, n=2164)** — study-B already failed its
+0.15R bar (+0.060R anchor), and the fix does not change the FAIL verdict, only lowers the honest
number. TrendCont stays in the lineup by trader override (discretionary testbed), not by the bar.
See `pipeline/trendcont_rearm_eval.py`, `pipeline/trendcont_eval.py`, and the re-arm report.

## Test (pre-registered)
Backtest **blind** across the full fleet (all 7 symbols, full ranges as `run_mfe_fleet.sh`,
model 4), **standalone** (only TrendCont enabled → it owns the one-position lock),
doctrine-v2 exits (`InpV2Exit=true`). n = journaled approved rows (the lock suppresses
consecutive triggers; suppressed ones are Prints only — not counted).

**Pass bar (all four):**
1. avg ≥ +0.15R/trade (fleet-wide),
2. n ≥ 150 fleet-wide,
3. both date halves positive — split at the **fleet-wide median `signal_time`**, each half's
   avg R > 0,
4. no single symbol contributes more than half the fleet profit — share = symbol net R ÷
   fleet net R, evaluated only when fleet net R > 0 (guaranteed if test 1 passes); max share
   ≤ 0.50. If fleet net R ≤ 0 the test is moot (the study already fails on avg).

Pass → takes EMArev's alert slot after 10b (trader trains on it in a fresh window first).
Fail → report and stop; **no parameter search**.
