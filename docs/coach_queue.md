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
- **Threshold sweep (added 2026-10-02),** close 100% of shorts whose 48h average is not above the bar:

  | bar | 2012-24 vs base | worst DD | 2025-26 vs base | shorts cut (dev / hold) |
  |---|---|---|---|---|
  | 0R | -0.0105 (t -1.6) | 27.9 | +0.010 | 108 / 7 |
  | +0.25R | -0.0008 | 25.2 | +0.053 (t 3.9) | 193 / 22 |
  | +0.5R | +0.006 | 22.2 | +0.078 (t 4.8) | 252 / 30 |

  - **The gain rises steadily with the bar, in both periods** (no zig-zag). It comes from closing shorts that are only *mildly*
    positive (48h average +0.25..+0.5R), i.e. banking a modest gain early. Cutting deep losers (average < 0) at day 2 costs
    money: they recover often enough.
  - **Longs-only loses at every bar** (0R: -0.017, +0.25R: -0.042, +0.5R: -0.04 in 2012-24).
  - The +0.5R bar was the first one tried; a higher bar (+0.75, +1R) is the natural next probe.
  - Reports: `data/study/day2_avg_cut_{0,0.25}_report.txt`.
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

**d) Grid: checkpoint (days 1-5) x bar (0..+1.5R), close 100% (added 2026-10-02;** `pipeline/avg_cut_grid_study.py`,
`data/study/avg_cut_grid_report.txt`)
- **All trades and longs-only lose in every one of the 35 cells in 2012-24** (-0.006 to -0.12R, mostly t -2 to -4).
- **Shorts-only:**
  - **Day 1** loses everywhere in 2012-24 (-0.008 to -0.022): too early.
  - **Days 2-5 with bars of +0.5R and above** sit at about zero in 2012-24 (-0.004 to +0.006).
  - **Day 2 / +0.5R** is the best 2012-24 cell (+0.006, DD 27.4 -> 22.2).
  - **2025-26 is positive in every shorts-only cell**, +0.01 to +0.09R, t 1.2-5.0.
  - **The shape:** costless over 13 years, a large help in the bad-for-shorts period, and lower drawdown in most cells. That reads
    like a regime hedge more than an edge.
- **Combined with entry 1 (the add gate on all trades + the shorts-only cut):**
  - Day 2 / +0.5R gives 2012-24 **+0.009 (t 0.9), worst DD 21.1R** (base 27.4) and 2025-26 **+0.092 (t 4.9), DD 13.5R**
    (base 20.4). That is the best combined cell.
  - **The two rules are close to additive.** The interaction term is -0.003 to 0 in 2012-24 and -0.002 to +0.004 in 2025-26:
    they neither clash nor amplify, because they touch different trades and different moments.
- **Ask:** if the coach wants one short-side rule pre-registered, "shorts: close at the day-2 close if the 48h average is not above
  +0.5R" is the cell to fix (it was also the first one tried, not picked from the grid). Run it with the add gate as a pair, with
  an EA reproduction.

## 3. Add to proven runners at day 2 (added 2026-10-02)

**Rule.** At the close of H4 bar 12 (~48h), if the trade is still open and its average R since entry is above +1R, add X% of the
full size at market (it shares the position's stop and runs to TP2).

| add | 2012-24 vs base | worst DD | 2025-26 vs base | worst DD |
|---|---|---|---|---|
| +25% | +0.005 (t 2.2) | 26.7 | +0.000 | 21.9 |
| **+50%** | **+0.010 (t 2.2)** | **26.1** | +0.000 | 23.4 |
| +75% | +0.015 | 27.7 | +0.000 | 24.9 |
| +100% | +0.020 | 30.0 | +0.000 | 26.4 |

- **Population:** 165 adds in 2012-24 (111 long / 54 short); 23 in 2025-26.
- **The gain is mostly LONGS** (t 2.1); shorts add +0.001-0.003, about nothing.
- **Price constraint at the moment of the add:** requiring price below +2R (or +1..+2R, or 0..+1R) roughly halves the gain. The
  value is in adding to trades that are averaging above +1R AND still running above +2R, i.e. the strongest runners.
- **Drawdown:** +50% is the best cell (27.4 -> 26.1R). Above that, drawdown climbs.
- **Caveats:**
  - 2025-26 is flat on only 23 adds, so there is no holdout support.
  - It is a size increase, so its risk shows in the FTMO headroom, not just in R.
  - The same family as the bigger-trial-pyramid and promote-fast-starters results: more size on winners earns more. This
    version is the most drawdown-efficient seen so far.
- **Checkpoint sweep, days 1-5** (+50%, average > +1R, any price; change vs base, 2012-24 | 2025-26):

  | checkpoint | all trades | worst DD (2012-24) | longs only | shorts only |
  |---|---|---|---|---|
  | day 1 | +0.005 \| -0.001 | 28.0 | +0.005 \| -0.002 | +0.000 \| +0.002 |
  | **day 2** | **+0.010 (t 2.2) \| +0.000** | **26.1** | **+0.008 (t 2.1) \| +0.006** | +0.002 \| -0.006 |
  | day 3 | +0.005 \| +0.008 | 29.2 | +0.004 \| +0.008 | +0.001 \| -0.001 |
  | day 4 | +0.006 \| +0.004 | 30.5 | +0.006 \| +0.008 | -0.001 \| -0.004 |
  | day 5 | +0.007 \| +0.004 | 32.3 | +0.009 (t 2.4) \| +0.001 | -0.002 \| +0.003 |

  - **Positive at every checkpoint in 2012-24, and mostly positive in 2025-26 for longs**, so day 2 is not a lucky pick of the
    timing. Day 2 is the best balance: the strongest t, and the only checkpoint where drawdown falls (later checkpoints add on
    trades that are about to finish, and drawdown rises to 29-32R).
  - **Shorts are about zero at every checkpoint.** This is a longs rule.
- **Script / report:** `pipeline/day2_avg_add_study.py` (argv = checkpoint in H4 bars), `data/study/day2_avg_add_report.txt` and
  `avg_add_bar{6,18,24,30}_report.txt`.
- **Ask:** pre-register "+50% at day 2 when the 48h average > +1R" (longs only, or both sides) and run an EA reproduction.

## 4. The three rules together (added 2026-10-02)

A = entry 1 (24h average < 0 holds the add until +1R, all trades). B = entry 2 (shorts: close at day 2 if the 48h average is not
above +0.5R). C = entry 3 (day 2: 48h average > +1R adds +50%, longs only; C* = both sides).

| rules | 2012-24 vs base | worst DD | total R / DD | 2025-26 vs base | worst DD | total R / DD |
|---|---|---|---|---|---|---|
| base | | 27.4 | 11.6 | | 20.4 | 1.0 |
| A | +0.006 | 25.9 | 12.9 | +0.015 | 15.8 | 1.6 |
| B | +0.006 | 22.2 | 15.1 | +0.078 | 14.2 | 3.3 |
| C | +0.008 (t 2.1) | 26.6 | 12.8 | +0.006 | 22.5 | 1.0 |
| A+B | +0.009 | 21.1 | 16.2 | +0.092 | 13.5 | 3.8 |
| A+C | +0.014 (t 2.1) | 25.5 | 14.0 | +0.021 | 15.8 | 1.7 |
| B+C | +0.014 | 22.9 | 15.6 | +0.084 | 16.3 | 3.0 |
| **A+B+C** | **+0.017 (t 1.6)** | **22.4** | **16.3** | **+0.098 (t 4.5)** | **13.5** | **4.0** |
| A+B+C* | +0.019 | 23.5 | 15.7 | +0.092 | 13.5 | 3.8 |

- **A+B+C is the best package in both periods:**
  - R per trade +15% (2012-24: +0.115 -> +0.132);
  - worst drawdown down 18% (27.4 -> 22.4R);
  - return per unit of drawdown 11.6 -> 16.3.
  - In 2025-26: +0.060 -> +0.158 per trade, with drawdown 20.4 -> 13.5R.
  - Bid-only gives the same picture (+0.019, DD 30.8 -> 23.2; 2025-26 +0.097).
- **The rules are nearly additive.**
  - 2012-24: the sum of the three separate gains is +0.020 vs +0.017 together; 2025-26: +0.099 vs +0.098.
  - They act on different trades and moments: A on weak trades at 24h (withholds size), B on stalled shorts at 48h (exits), C on
    strong longs at 48h (adds size).
- **C on longs only is better than on both sides** (shorts' adds are about zero and cost drawdown), consistent with the
  long-runner / short-take-profit picture.
- **Caveats:**
  - Each rule came from the trader's exploration on this record, so the package inherits the selection bias of all three.
  - The 2012-24 t is 1.6.
  - Most of the 2025-26 gain is B (a bad period for shorts).
  - All three touch sizing or exits intrabar-adjacent, so an EA real-tick reproduction is required.
- **Script / report:** `pipeline/combo3_study.py`, `data/study/combo3_report.txt`.
- **Ask:** if the coach takes these forward, test A+B+C as one pre-registered package (fixed parameters as above), with a
  fragility check per rule and an EA reproduction on two symbols.

---

## Pre-registration for the out-of-sample symbol test (frozen 2026-10-02, BEFORE any new-symbol data is looked at)

New symbols (never used in any study): US30, XAGUSD, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP, USDMXN. Data: Dukascopy
M1 via QDM, 2012 -> 2026. Signals from the MT5 tester (the four live detectors, AA, as for the pinned record). Replay with the same M1
ask-side engine (SCALED; BID shown alongside) and the same live stack as the base.

Rules tested, exactly as below, with no re-tuning on these symbols:
- **A.** At the bar-6 close, a trial average R (mean of M1 bid closes since entry, first tranche's 1R) below 0 holds the 75% add
  until the bid reaches +1R (added at market then). All trades.
- **B.** At the bar-12 close, a SHORT whose average R since entry is not above +0.5R is closed (100%) at market.
- **C.** At the bar-12 close, a LONG whose average R since entry is above +1R gets +50% of the full size at market (shares the stop,
  runs to TP2).
- The package is A+B+C; each rule is also reported alone.

Read-out, decided now:
- The change vs base in R per trade and worst drawdown, for 2012-24 and 2025-26 separately, by symbol and pooled, with longs and
  shorts split.
- **Support** = pooled 2012-24 increment > 0 AND worst DD not higher than base, for the package and for each rule.
- Anything else is reported as it comes out. The new symbols are looked at once; no parameter changes after the first look.
