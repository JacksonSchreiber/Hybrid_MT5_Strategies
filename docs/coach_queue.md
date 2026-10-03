# Coach queue - studies the trader wants the coach to see

Running list kept by the engineer. Each entry gives the rule, the numbers (M1 ask-corrected engine, SCALED, all four detectors,
7 symbols, pinned record; 2012-24 development / 2025-26 holdout), the caveats, and the proposed next step. Nothing here is live.
Base everywhere = the live stack: staged 25% / 75% at the bar-6 close, bank 25% (TrendCont, DeepFib) or 50% (SweepMSS, EMArevQ)
at +1R or TP1 -> stop to entry, pyramid +50% at +1.5R, shorts' stop to +0.25R at +1.5R. Base 2012-24 +0.1146R/trade, worst DD
27.4R; 2025-26 +0.0596R, DD 20.4R.

---

## 1. Hold the staged add until a weak trade proves itself (added 2026-10-02)

**Rule.** At the bar-6 close (~24h, the live add), compute the trade's average R over the trial (mean of every M1 bid close since
entry). If it is below 0, do not add yet: add the 75% at market the first time price reaches +1R (the bank fires there too, so the
add enters with the stop moving to entry).

| | 2012-24 vs base | worst DD | 2025-26 vs base | worst DD |
|---|---|---|---|---|
| hold until +1R | +0.006 (t 1.05) | 27.4 -> 25.9 | +0.015 (t 1.03) | 20.4 -> 15.8 |
| hold until 0R / +0.5R | +0.002 / +0.002 | 26.3 / 30.8 | +0.004 / +0.003 | 16.0 / 16.1 |
| never add | -0.012 | 34.2 (worse) | +0.022 | 14.3 |

- **Population:** 1,380 of 2,775 trades are gated (their trial average is below 0).
- **+1R variant, 2012-24:** helped 333 / hurt 201; both sides gain (+0.009R per gated long, +0.016R per gated short).
- **+1R variant, 2025-26:** longs gain, shorts are flat (-0.007).
- **Why it might be real:** new size goes on only once the trade has shown it can move. The add then sits on a stop at entry,
  not at -1R.
- **Caveats:** t is about 1 in each period. Four variants were tested and the best picked by looking. It is a sizing-timing
  rule, so a real-tick EA reproduction is needed before any number counts. The EA already has the parts (staged add, a
  level-triggered promote).
- **Script / report:** `pipeline/trial_avg_gate_study.py`, `data/study/trial_avg_gate_report.txt`.
- **Ask:** pre-register "+1R" as the single variant, with a bar, then run the EA reproduction.

## 2. Shorts: take profit fast (the pattern behind several results; added 2026-10-02)

The trader's read: on shorts, the edge is in taking what the market gives quickly. Long-side runners pay; short-side runners
mostly don't. The evidence, collected from separate studies:

**a) Day-2 average cut, SHORTS ONLY.** At the close of H4 bar 12 (~48h), if a short's 48-hour average R is not above +0.5R,
close it at market (longs untouched).

| sell | 2012-24 vs base | worst DD | 2025-26 vs base | worst DD |
|---|---|---|---|---|
| 100% | +0.006 (t 0.67) | 27.4 -> **22.2** | **+0.078 (t 4.8)** | 20.4 -> 14.2 |
| 75% | +0.004 | 22.5 | +0.044 | 16.5 |
| 50% | +0.002 | 24.1 | +0.029 | 17.8 |
| 25% | +0.001 | 25.8 | +0.015 | 19.1 |

- **Population:** 252 shorts cut in 2012-24 (helped 193 / hurt 59) and 30 in 2025-26 (27 / 3).
- **The same rule on ALL trades loses** in 2012-24 (-0.039R, t -2.3): cutting weak longs costs -0.28R each.
- **Script:** `pipeline/day2_avg_cut_study.py`. The shorts-only cut was computed from the same engine on 2026-10-02.

**b) Supporting results (all point the same way):**
- **Shorts stop raise to +0.25R at +1.5R:** shipped by trader override. The EA real-tick check gave +0.021R (EURUSD) / +0.031R
  (US100) per short. The long mirror lost everywhere.
- **TP2 rate after +1.5R:**
  - 2012-24: shorts 40% vs longs 49%.
  - 2025-26: shorts 19% vs longs 56%.
  - Index and oil shorts are the weakest (36-39%).
- **Underwater cut after 2 days:** neutral on shorts in 2012-24, positive on shorts in 2025-26; it loses on longs.
- **Half-size shorts:** account drawdown 33.3R -> 23.4R for -0.002R/trade (2012-24, M5). This is the parked drawdown lead.
- **Early promotion of fast starters:** shorts gained far less than longs (+0.08R vs +0.23R per promoted trade at 200%).

**c) Counter-evidence and caveats:**
- **Lower TP2 targets on shorts zig-zagged** (+3.5R / +3.0R / +2.5R / +2.0R: no monotone pattern = noise), so "take profit
  fast" is not simply "lower the target".
- **The long/short gap is partly drift.** Index shorts fought the 2012-24 uptrend, and that gap reversed in 2025-26 (see the
  ask-side / direction study). One-detector splits are provisional.
- **2025-26 is a bad period for shorts generally.** Any rule that reduces short exposure looks good there. The 2012-24 gains are
  small (t < 1); the robust part is the drawdown cut.
- **Every short-side rule here was found by slicing after looking.**

**Ask:** design ONE pre-registered short-side study family with the coach (candidates: the day-2 average cut; a time- or
progress-based short exit; half-size shorts). Fix the variant, a sealed holdout split by symbol, and a fragility bar (drop the
top helped, double the hurt) before running it. Then an EA reproduction for whichever survives.
