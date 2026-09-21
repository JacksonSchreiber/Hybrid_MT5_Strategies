# BTCUSD Feasibility Study — pre-registered spec

**Author:** the coach · **Date:** 2026-09-16 · **Status:** approved to run **after shadow week 1 is stable** (offline only; touches nothing live) · **Type:** one-look feasibility gate, same standard as every prior study

## 1. Question

Can the frozen four-detector system, under doctrine-v2 exits and the regime gate, trade BTCUSD (the only crypto instrument on FTMO × OANDA) with a **non-negative blind base after real costs**? This is a feasibility gate, not an edge claim: a pass earns BTCUSD a *training window*, not a live seat.

## 2. What is frozen (no changes of any kind)

- Detectors: SweepMSS, DeepFib, TrendCont, EMArevQ — byte-identical, including re-arm rules and the 1% min-lot viability gate.
- Regime tag formula (D1 200-EMA / slope / ADX ≥ 20), decision classes, doctrine-v2 exits (+1R 50% bank + BE, tighten-only), MinRR floors.
- No parameter search. No threshold scans. One run, one report.

## 3. Data

- **Source:** Dukascopy BTCUSD tick history (available from ~2017-05) imported as `BTCUSD.dk`, same pipeline as the other `.dk` symbols. (Standing preference is MT5 on-disk data first; the broker's BTCUSD history is too shallow for a decade-scale test, so Dukascopy is the justified exception here — state the import range in the report.)
- **Range:** 2018-01-01 → present. Warm-up: the regime tag needs ~210 D1 bars, so signals begin ~2018-08; report the effective start.
- **Date halves for the stability check:** 2018-08 → 2021-12 and 2022-01 → present.
- **Bid/ask:** keep Dukascopy's real spread — BTC CFD spreads are wide and a mid-price test would flatter every result.

## 4. Costs — the part every prior study could ignore and this one cannot

- **Swaps:** pull the current published BTCUSD long and short financing from the FTMO × OANDA symbol specification (per lot per night, including any triple-day convention). Apply per night held to every simulated position. Report the figure used.
- **Spread:** from the tick data (above). Report the average spread in ATR units at entry.
- **Commission:** per the symbol spec, if any.
- Report every cell **twice**: costs-off and costs-on. The gate is judged on costs-on; the pair shows the drag.

## 5. Session / doctrine rulings for a 24/7 instrument (coach, pre-stated)

- **Calendar:** USD V/W rows bind exactly as for indices (BTC reacts to FOMC/CPI); W-class NO-HOLD applies. Crypto-specific events (ETF rulings, halvings, exchange failures) are not in the feed and are **not modeled** — noted as a limitation.
- **Late-Friday rule, weekend-hold flag, overnight-timing checklist steps:** **suspended** for this instrument in the study (there is no close). Weekend bars are ordinary bars.
- **Sizing:** 1% risk per the frozen gate. Contract spec (1 lot = 1 BTC on FTMO) must be honored in the min-lot viability check — report how many signals the min-lot gate suppresses at the $25k account size, because at BTC prices with tight stops the lot granularity can bite.
- If ever live: risk multiplier 0.5 (new-instrument rule), never higher without a graded window at full size.

## 6. Cells to report

For costs-on (and costs-off alongside): fleet total; per detector; per regime tag (TREND_UP / TREND_DOWN / CHOP); per date half; TAKE-class vs DISCRETION-class. For each: n, avg R, win rate, bank-rate (% reaching +1R), TP-rate, max drawdown of the blind equity curve in R, cadence per year. Plus: the 10 largest winners and losers with dates (for the coach's eye), and the min-lot suppression count (§5).

## 7. Gate (fixed now; single look)

BTCUSD advances to a **training window** only if ALL hold on **costs-on** figures:
1. Fleet avg R ≥ **0.00** (non-negative blind base after costs).
2. Both date halves ≥ 0.00.
3. Cadence between **15 and 60 signals/year** after the min-lot gate (fewer = untrainable; more = a different instrument than the one the doctrine was built for).
4. No single detector below **−0.10R** avg on n ≥ 30 (a detector that is structurally broken on BTC gets disabled for the instrument rather than dragging the others; the coach rules which).
5. Blind max drawdown ≤ **15R** (the FTMO 10% max-loss equals 10R at 1% sizing; a blind curve that breaches it in history is disqualified regardless of average).

Pass → the coach rules a BTCUSD training window (same bars as every window: +2R absolute, zero violations), traded in the local tester while live continues. Fail → **closed, no re-cuts, no parameter search** — the same third-strike discipline as EMArev applies from the first strike here, because this is a new instrument, not a new idea.

## 8. Deliverables

`data/study/btcusd/report.md` (cells, costs used, limitations, the ten-and-ten list), the equity curves (costs-on/off), the import log, and one paragraph on anything the study could not model (crypto-specific events, exchange-hour anomalies, Dukascopy coverage gaps). No EA changes, no config changes, nothing touches the VPS.

## 9. Timing

Not before shadow week 1 has been stable for seven days (heartbeats clean, no plumbing incidents). The live build's attention comes first; this is the first post-shadow research item, by coach ruling.

---

## 10. BTCUSD discretion window — instrument rules (coach, 2026-09-17, pre-registered)

The mechanical study is closed. This section governs the **trader's discretion window
(window 14, BTCUSD.dk 2019)** only. Two trader-initiated instrument rules, ruled before
the window runs, applied identically to the **AA baseline and the trader's run** so the
comparison stays honest. Label the window correctly in the report: it tests *the system
with BTC-specific risk parameters plus trader discretion*, not the frozen system.

### 10.1 Flat by Friday close — MANDATORY, no discretion

**Rationale (stronger than the trader stated):** this broker's BTCUSD trades Mon–Fri
while the underlying trades 24/7 on global venues. The CFD therefore reopens Monday
having skipped ~48h of real price discovery — a structural gap, not a liquidity
accident. A stop does not protect through a gap. This is the same logic that makes
elections NO-HOLD, and it applies here every single week.

- **No new BTCUSD entries after Friday 12:00 UTC** (engineer: adjust to the broker's
  actual session end; the intent is "no entry that cannot mature before close").
  Journaled as an automatic skip, `auto=1`, reason "weekend-flat rule".
- **Any open BTCUSD position is force-closed at the last H4 bar close before the broker's
  Friday close**, at market, unconditionally — banked or not, in profit or not. Journaled
  as action `WEEKEND_FLAT` with the R at close.
- The trader may close earlier at discretion; the trader may never extend past it.
- This rule is **BTCUSD-only**. It does not touch the six lineup symbols.

**The cost this imposes, which MUST be measured:** the system's winners run 5–15 days;
under this rule the maximum hold is Monday open → Friday close. The engineer reports the
AA baseline **twice — with and without the weekend-flat rule** — so the coach can see
what the protection costs in R and in runner truncation. If the constrained baseline is
drastically worse, BTCUSD is structurally unsuited to a swing system on this broker and
the window is cancelled before the trader spends time on it. That finding is worth the
run on its own.

### 10.2 Stop width — measure first, then a principled floor

The trader's read is that BTC's noise between bars overruns structural stops. That is
testable rather than assumed, and the fix must not be fitted to outcomes.

1. **Measure:** from existing journals and the feasibility-study data, report the
   distribution (median, quartiles) of **stop distance in ATR(14) units at signal time**
   for each of the six lineup symbols and for BTCUSD, per detector.
2. **Rule, pre-registered:** if BTCUSD's median stop distance in ATR units falls **below
   the lineup's median**, apply an **ATR floor** for BTCUSD only — the stop is widened
   (never tightened) to the *lineup median in ATR units* when structure sits closer.
   The floor is set to the lineup's own median deliberately: it makes BTC's stops as
   noise-tolerant as the instruments the doctrine was validated on, and it is not chosen
   by looking at BTC's results. **One value, no search, no second pass.** If BTC's median
   is already at or above the lineup's, no floor is applied and the trader's hypothesis is
   recorded as not supported.
3. **Targets need no separate rule.** TP1/TP2 are R-multiples of the stop — widen the
   stop and the targets widen with it. R:R and the MinRR floor are unaffected.
4. **Sizing is unchanged:** 1% risk. A wider stop means fewer lots, not more risk.
5. **The tradeoff to report:** a wider stop puts +1R further away, so the mechanical bank
   rate falls. Report bank-rate and TP-rate with and without the floor on the AA baseline.

### 10.3 Scope discipline

These two rules are the **only** instrument-specific adjustments permitted for this
window. No further parameter changes before, during, or after it — a window that fails
is closed, not re-tuned (the EMArev standard, applied from the first strike as §7 states).

### 10.4 §10.2 measurement result and the widen-vs-reject ruling (coach, 2026-09-17)

**Measurement (data/study/stop_width_atr.md):** BTCUSD median stop 1.242 ATR vs the lineup
median **1.385 ATR** (pooled 1.397 — the two readings agree). BTC is below the lineup
median, so **the floor applies**, exactly as pre-registered. Floor value frozen at
**1.385 ATR**, BTCUSD only, chosen without reference to BTC outcomes. BTC's tight tail is
concentrated in Deep Fib (0.86 ATR); TrendCont is already at 1.32.

**Ruling: WIDEN (option a), not reject.**
- It is the literal wording of §10.2 and the trader's stated hypothesis ("the SL needs to be
  further away because BTC is so volatile"). Rejecting tight setups tests a different
  question — *are tight-stop setups bad?* — and would leave his hypothesis untested.
- **Widening does not divorce the stop from structure; it buffers it.** The rule is
  `SL = max(structural stop, 1.385 ATR from entry)`, so the structural invalidation level
  always sits *inside* the stop. This is the opposite of the retired SLIDE defect, which
  moved a stop off structure to preserve an R distance; here structure is preserved and a
  noise margin is added beyond it.
- Rejecting would drop roughly half of BTC's setups — including the Deep Fib family, which
  is TAKE-class and was part of the only profitable half of the instrument. Removing the
  positive half to fix a volatility complaint is the wrong surgery.
- **It is not free, and that is the point:** a wider stop means fewer units, and +1R and the
  R-multiple targets sit further away in price, so the bank rate and TP rate must fall.
  Whether the reduced stop-out rate outweighs that is precisely what the window measures.

**Implementation requirements.** New EA input, **default off** so every existing baseline
stays byte-identical (the engineer's instinct, ratified); symbol-scoped to BTCUSD; applied
identically to the AA baseline and the trader's run; widen-only (never tightens a stop that
is already ≥ 1.385 ATR); **applies to all four detectors, not per-detector** — a per-detector
floor would be fitting. Journal the pre-floor stop, the post-floor stop and a `floor_applied`
flag on every row. **Report on the baseline: how many setups were widened, the mean widening
in ATR, and bank-rate / TP-rate with and without the floor** (§10.2 point 5). Run the gate
chain as usual.

### 10.5 Scope of the ATR floor — TEST ONLY (trader ruling, 2026-09-17)

The 1.385-ATR floor is a **window-15 experiment**. It must not reach the live system:
input default off; enabled only for window 15's tester run and its AA baseline; **never
set in any live config**, and the live box's config must not carry the key at all. The
shadow period continues untouched. (This is separate from §10.1's weekend-flat rule, which
is intended to ship with the instrument if the broker is Mon–Fri.)

**Consequence to settle AFTER the window, flagged now:** if window 15 passes *with* the
floor on, then live BTCUSD without the floor is not the instrument that was tested — the
0.5× seat would have to carry the floor too, or the pass doesn't transfer. The coach will
rule that when the result exists; it is not a reason to change anything now. Window 14
(no floor) and window 15 (floor) also give a rough two-point read on whether the floor
helps, confounded by different years — informative, not decisive.

### 10.6 Floor result on the 2023 baseline — FALSIFIED; floor DROPPED for window 15 (coach, 2026-09-17)

The floor caught exactly what it was aimed at (35 of 70 setups widened, mean +0.525 ATR,
widened set averaging 0.860 ATR before the floor). Its effect:

| | floor 1.385 | no floor |
|---|---|---|
| bank rate | 31.4% | **38.6%** |
| TP rate | 12.9% | 10.0% |
| stop-out rate | 40.0% | **38.6%** |
| costed total R | −9.02 | **−6.40** |

**The hypothesis is refuted on its own mechanism.** The claim was that BTC's noise overruns
structural stops, which predicts *fewer stop-outs* when stops widen. Stop-outs did not fall —
they rose slightly — while the bank rate fell 7.2 points, which is geometric and certain
(a wider stop puts +1R further away). The floor therefore carries a certain cost and an
unobserved benefit: ≈ −2.6R over the year on the blind base. Caveat acknowledged: the two
runs are not the same trades (70 rows vs 73 — geometry cascades the signal set), so this is
a rough comparison; but the bank-rate mechanism is robust to that and the stop-out result
points the wrong way regardless.

**Why stop-outs didn't fall is itself the finding, and it is coherent with the library note:**
BTC's adverse excursions are not noise-sized, they are cascade-sized. Forced liquidation flow
does not politely stop at 1.385 ATR — it runs. Extra stop distance buys nothing against a
cascade while costing target distance on every trade.

**Ruling: the ATR floor is DROPPED for window 15.** The window runs on the no-floor baseline
(73 rows, −6.40R costed), identical geometry to window 14, which also makes the two windows
comparable. The floor input stays built and default-off. The trader may override — his
standing right — but the override now has a measured price and the coach's position is on
record. §10.1 weekend-flat is unaffected and stays in force (verified working: 50 boundary
closes, no weekend decisions, nothing carried into a Monday gap).

### 10.7 The five Sunday-stamped signals — accepted as a stated limitation

Five of 70 setups (7%) are read from a Sunday 20:00 candle that would not exist at a
Mon–Fri broker. Rebuilding a weekday-only custom symbol would force minute-bar modelling
instead of real ticks. **Ruling: accept the engineer's recommendation.** Tick fidelity has
been the foundation of every graded window in this program; trading it away to fix 7% of
signals is the worse bargain. **Requirement:** flag those five rows in the journal so the
coach can run a sensitivity check at grading and report the window result with and without
any trade originating from them. If the window's verdict turns on one of the five, the coach
will say so explicitly rather than let it pass silently.
