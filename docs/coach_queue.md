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
- **Timing/bar grid (added 2026-10-02;** `pipeline/gate_grid_study.py`, `data/study/gate_grid_report.txt`). Decision at
  12/24/36/48/72h; average below -0.25/0/+0.25/+0.5R holds the add until +1R; change in R/trade vs base (2012-24 | 2025-26):

  | decision | avg < -0.25R | avg < 0R | avg < +0.25R | avg < +0.5R |
  |---|---|---|---|---|
  | 12h | +0.000 \| +0.011 | **+0.009 (t 1.4) \| -0.001** | +0.009 \| -0.007 | +0.009 \| +0.002 |
  | 24h | -0.000 \| +0.002 | **+0.006 (t 1.1) \| +0.015** | -0.003 \| -0.011 | -0.003 \| +0.000 |
  | 36h | -0.010 \| -0.019 | -0.009 \| -0.012 | -0.016 \| -0.025 | -0.012 \| -0.017 |
  | 48h | -0.005 \| -0.030 | -0.009 \| -0.024 | -0.013 \| -0.023 | -0.014 \| -0.022 |
  | 72h | -0.024 \| -0.072 | -0.026 \| -0.066 | -0.025 \| -0.051 | -0.028 \| -0.042 |

  - **A bar at 0R is the sweet spot.** Gating at +0.25R or +0.5R holds back too many eventual winners, and drawdown rises to
    31-36R.
  - **The decision must come early (12-24h).** From 36h on every cell loses. The "add delayed, no gate" reference shows why:
    simply adding later costs -0.005 to -0.024R, and the gate cannot buy that back.
  - **24h / avg < 0 stays the most robust cell:** it is the only one positive in both periods with lower drawdown in both
    (25.9 / 15.8R). 12h / avg < 0 is slightly better in 2012-24 (+0.009) but flat in 2025-26.
  - Longs and shorts both gain at 24h/0 in 2012-24 (+0.003 / +0.003 per account trade).
- **Ask:** pre-register "24h, average < 0, hold until +1R" as the single variant, with a bar, then run the EA reproduction.

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
