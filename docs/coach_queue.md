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

## 3. Add to improving longs at day 2 (added 2026-10-02, trigger revised 2026-10-03)

**Rule (I).** At the close of H4 bar 12 (~48h), a LONG still open whose second-day average R (mean of M1 bid closes from the bar-6
close to the bar-12 close) is above its first-day average (entry to the bar-6 close) gets +50% of the full size at market. The add
shares the position's stop and runs to TP2.

| | adds | 2012-24 vs base | worst DD | 2025-26 vs base |
|---|---|---|---|---|
| I (longs, +50%) | 517 | +0.023 (t 3.2) | 31.9 | +0.013 |
| improving by more than +0.5R | 230 | +0.015 (t 2.8) | 29.5 | +0.015 |

- **Longer streaks are weaker.** Daily averages rising 2 / 3 / 4 / 5 days in a row give +0.023 / +0.014 / +0.009 / +0.003R in
  2012-24 and +0.013 / -0.002 / -0.004 / +0.001 in 2025-26. Two days is the best, and the only length positive in both periods.
- **Longs only.** The same add on shorts is about 0 in 2012-24 and -0.033 (t -3.3) in 2025-26.
- **Alone it raises drawdown** (27.4 -> 31.9R): it adds to many trades. Inside the package (entry 4) that cost mostly disappears.
- **Replaces** the earlier trigger (48h average > +1R: +0.008R, t 2.1, 111 adds), which the trader withdrew on 2026-10-03.
- **Scripts:** `pipeline/trend_of_avg_study.py`, `pipeline/streak_avg_study.py`; reports `data/study/trend_of_avg_report.txt`,
  `streak_avg_report.txt`.
- **Ask:** pre-register I as defined above (longs only), with an EA reproduction.

## 4. The three rules together (added 2026-10-02, revised 2026-10-03)

A = entry 1 (24h average < 0 holds the add until +1R, all trades). B = entry 2 (shorts: close at day 2 if the 48h average is not
above +0.5R). I = entry 3 (longs: day-2 average above day-1 average -> +50%).

| rules | 2012-24 vs base | t | worst DD | total R / DD | 2025-26 vs base | worst DD | total R / DD |
|---|---|---|---|---|---|---|---|
| base | | | 27.4 | 11.6 | | 20.4 | 1.0 |
| A | +0.006 | 1.1 | 25.9 | 12.9 | +0.015 | 15.8 | 1.6 |
| B | +0.006 | 0.7 | 22.2 | 15.1 | +0.078 | 14.2 | 3.3 |
| I | +0.023 | 3.2 | 31.9 | 11.9 | +0.013 | 22.9 | 1.1 |
| A+B | +0.009 | 0.9 | 21.1 | 16.2 | +0.092 | 13.5 | 3.8 |
| A+I | +0.029 | 3.2 | 30.0 | 13.2 | +0.028 | 17.7 | 1.7 |
| B+I | +0.029 | 2.5 | 23.4 | 17.0 | +0.091 | 16.7 | 3.1 |
| **A+B+I** | **+0.032** | **2.6** | **23.5** | **17.3** | **+0.105** | **15.4** | **3.7** |

- **A+B+I in 2012-24:** R per trade +28% (+0.115 -> +0.146), worst drawdown -14% (27.4 -> 23.5R), return per unit of drawdown
  11.6 -> 17.3.
- **A+B+I in 2025-26:** R per trade +0.060 -> +0.165, drawdown 20.4 -> 15.4R.
- **Bid-only agrees:** +0.033 in 2012-24, DD 30.8 -> 26.8.
- **A and B take out most of I's drawdown cost** (31.9 -> 23.5R). The three rules act on different trades and moments: A on weak
  trades at 24h (withholds size), B on stalled shorts at 48h (exits), I on strengthening longs at 48h (adds size).
- **Caveats:**
  - Each rule came from the trader's exploration on this record, so the package inherits the selection bias of all three.
  - Most of the 2025-26 gain is B (a bad period for shorts).
  - All three touch sizing or exits intrabar-adjacent, so an EA real-tick reproduction is required.
- **Script / report:** `pipeline/combo3_study.py`, `data/study/combo3_report.txt`.
- **Ask:** test A+B+I as one pre-registered package (fixed parameters as above), with a fragility check per rule and an EA
  reproduction on two symbols.

---

## Pre-registration for the out-of-sample symbol test (frozen 2026-10-02, BEFORE any new-symbol data is looked at)

New symbols (never used in any study): US30, XAGUSD, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP, USDMXN. Data: Dukascopy
M1 via QDM, 2012 -> 2026. Signals from the MT5 tester (the four live detectors, AA, as for the pinned record). Replay with the same M1
ask-side engine (SCALED; BID shown alongside) and the same live stack as the base.

Rules tested, exactly as below, with no re-tuning on these symbols:
- **A.** At the bar-6 close, a trial average R (mean of M1 bid closes since entry, first tranche's 1R) below 0 holds the 75% add
  until the bid reaches +1R (added at market then). All trades.
- **B.** At the bar-12 close, a SHORT whose average R since entry is not above +0.5R is closed (100%) at market.
- **I.** At the bar-12 close, a LONG whose second-day average R (bar-6 close -> bar-12 close) is above its first-day average (entry
  -> bar-6 close) gets +50% of the full size at market (shares the stop, runs to TP2).
- The package is A+B+I; each rule is also reported alone.
- (Revised 2026-10-03: I replaced the earlier rule C (48h average > +1R). This was still before any new-symbol data was looked at,
  so the pre-registration stands.)

Read-out, decided now:
- The change vs base in R per trade and worst drawdown, for 2012-24 and 2025-26 separately, by symbol and pooled, with longs and
  shorts split.
- **Support** = pooled 2012-24 increment > 0 AND worst DD not higher than base, for the package and for each rule.
- Anything else is reported as it comes out. The new symbols are looked at once; no parameter changes after the first look.

## Coach ruling 2026-10-03 and build status
- Pre-registration stands (one look). Bars on the new symbols: B increment >= 0 (2012-24) and DD not above base; A DD not above base and
  increment >= -0.005R in both periods; I increment > 0 (2012-24) with t >= 1.5 and positive on FX longs alone. I ships only with A and B on.
- EA real-tick reproduction per rule (on vs off, +-0.02R) before live: pipeline/queue_repro_run.sh -> pipeline/queue_repro_check.py.
- Live grading: web/queue_rules.csv (live/queue_shadow.py), one counter per rule vs its counterfactual, read at n=30.
  Trader 2026-10-03: never retired automatically - the monitor tells the trader; the rules stay on.
- Built into the EA 2026-10-03, OFF: live.json staged_add_gate (A, 1 = on), short_day2_cut_avg (B, 0.5), improving_add_frac (I, 0.5).
  Averages match the study: M1 bid closes from the entry-bar open, against the card entry (signal-bar close) in the card's R.
  I: shares the position's stop (original stop before the bank, entry after); spread, lot-floor and FTMO headroom gates at the add;
  total size capped at 200% of full. None of the three acts on Inverse rows (not in the study population).
- FX-only split of the original seven (data/study/combo3_class_report.txt): I on FX longs 2012-24 +0.023R (t 1.2, DD 16.2 -> 21.5),
  2025-26 -0.036R (t -0.9); I's edge sits in index longs (+0.055, t 2.9) and gold longs (+0.058).

## Coach ruling 2026-10-03 (second) - pre-registered before the look
- I: bar unchanged (new symbols: increment > 0 at t >= 1.5 AND a positive FX-long cell). If it fails, I is CLOSED - never narrowed to
  indices/gold after the fact (the original-seven edge is index + gold longs = drift, the coach's concern).
- A: if A's FX-short cell is negative on the new symbols too (original seven: -0.029R, t -2.0), A ships for LONGS ONLY. Other bars unchanged.
- B: unchanged.
- Accepted: I's spec, the average-R definition, the monitor guard alerts, no automatic retirement (counters report to the trader at n=30),
  the double-bank fix (confirmed: no reproduction before item 25 was affected - bank/pyramid/promote/inverse repros ran 30 Sep - 1 Oct
  01:10, before the bug landed 1 Oct 13:58; the pinned AA study journals pre-date staging; only the item-25 repro had it, in both arms -
  its corrected increment comes from data/study/queue_repro/<SYM>_off vs _nosr).
- Graders: touched signals are NOT excluded. Each counterfactual = the full live stack with only the grader's own rule off; every signal
  is tagged with the rules that touched it (live/rule_tags.py -> web/rule_tags.csv + a 'rules' column on every grader file).

- 2026-10-03, decided before any new-symbol result: US30 runs from 2013-01-01 only (Dukascopy throttled the 2011-12 source); the other nine are complete from 2012. Pooled as registered.

## OOS pipeline (prepared, not run)

**NEEDS A COACH RULING BEFORE `--look`.** On the original seven, the population the new symbols will use (TrendCont from the
four-detector AA run, because REC cannot be reproduced on the current build) already fails two things:
- A fails the strict both-periods reading (2025-26 DD 23.6 vs 23.4).
- The package loses support.

The base also drops from +0.115 to +0.078. See deviation 1 below. The coach should confirm two things in writing before the look:
1. the AA-TrendCont population;
2. the both-periods reading of A's bar.

Prepared 2026-10-03 while MT5 was busy with the queue reproduction. No rule result and no base result has been computed on any new
symbol. The study was validated on the original seven only.

**Commands, in order**
1. Already done: `python3 pipeline/oos_build_inputs.py build`, which writes `data/study/oos_inputs.json`. It holds, per symbol, the M1
   checks, ref price, live spread, tick size and class. Re-run it if US30's M1 CSV appears.
2. Optional: `pipeline/oos_mt5_stage.sh --dry-run` prints the plan and touches nothing.
3. Run the MT5 stage once the queue job has finished, MT5 is closed and no qdmcli is running:
   `nohup pipeline/oos_mt5_stage.sh > data/study/oos_aa/stage.out 2>&1 &`
   - The script refuses to start if terminal64 is running.
   - It is resumable: per-step markers live in `data/study/oos_aa/state/`.
   - It runs these steps:
     - **Parity:** one AA run of EURUSD.dk, 2016.08.01-2026.06.29. The journal goes to `oos_aa/parity/` and is diffed against the
       pinned journal on signal fields only.
     - **Per symbol:**
       - export the tick CSV from the QDM store;
       - build H4 from that same CSV, into `data/study/h4/<SYM>.dk.csv`;
       - import with `mt5_import.sh --clean`;
       - run the AA tester (`--mode ALL --strat SMC,Fib,TrendCont,EMArevQ --model 4`, `--from <start> --to 2026.06.30`, every
         tester-study env knob unset, so the EA runs on its input defaults);
       - copy the journal to `data/study/oos_aa/` right after the run.
     - **At the end:** `oos_build_inputs.py build`, `drag` (writes `data/study/oos_spread_drag.json` and `.md`) and `qa`.
   - Time estimates per symbol: export 3-8 min, H4 1-3 min, staging and import 15-45 min, AA run 20-60 min. That is about
     45-110 min per symbol, **about 8-14 h for the nine**, plus about 20 min for parity.
   - Disk: each tick CSV is about 12-25 GB on /home and is copied to C: while it imports. The script checks that C: has the CSV
     size × 1.1 + 30 GB free.
4. US30, when its download finishes (the M1 CSV appears in `data/qdm_csv/fleet`):
   `python3 pipeline/oos_build_inputs.py build && pipeline/oos_mt5_stage.sh US30`
5. Readiness check: `python3 pipeline/oos_queue_study.py`. It computes nothing. It lists whatever is missing, or says ready.
6. **The one look** (trader's command): `python3 pipeline/oos_queue_study.py --look`
   - Output: `data/study/oos_queue_report.txt` (and `oos_queue_trades.pkl`).
   - Use `--look --exclude US30` only if the trader decides not to wait. An excluded symbol can only ever be reported later as a
     separate addendum. It is never re-pooled.
   - The script refuses to overwrite an existing look report. A re-run needs `--relook "<reason>"` and is written to a separately
     labelled file.
   - A re-run must keep the first look's exclusions. `--relook` can never add US30.
   - A symbol excluded from the first look is reported only with `python3 pipeline/oos_queue_study.py --look --addendum US30`.
     That writes `data/study/oos_queue_addendum_US30.txt`, evaluated alone and never pooled.
   - data/ is gitignored, so copy or commit the report text on the day of the look.
   - The ref and spread fields in `oos_inputs.json` are frozen once written; `build` only fills in missing symbols. The report
     prints the sha256 of `spread_profile.csv`, `spreads.json`, `universe_cost_filter.csv`, `oos_inputs.json` and
     `oos_spread_drag.json`.

**What the report contains**
- base, A, B, I and A+B+I, in SCALED and BID, for 2012-24 and 2025-26: R/trade, increment vs base, paired t, worst DD, total R,
  R/DD, and longs/shorts.
- Class × direction × period cells, and symbol × direction × period cells.
- The bars block, with every sub-condition printed next to its verdict.
- Provenance: journal and H4 sha256, ref price, drag, class.

**Readings frozen in `pipeline/oos_queue_study.py`, before the look.** All are on SCALED, pooled.
- **Support:** 2012-24 increment > 0 and 2012-24 worst DD ≤ base.
- **B:** 2012-24 increment ≥ 0 and 2012-24 DD ≤ base. 2025-26 DD is printed but is not part of the bar.
- **A:** increment ≥ -0.005R and DD ≤ base, both in **both** periods.
  - FX-short cell, 2012-24: if the increment is < 0, A ships longs only.
- **I** (alone vs base): 2012-24 increment > 0, t ≥ 1.5, and a 2012-24 FX-long cell > 0. Otherwise I is CLOSED.

**Validation on the original seven**
- Command: `--symbols EURUSD.dk,GBPUSD.dk,US100.dk,US500.dk,USDJPY.dk,USOIL.dk,XAUUSD.dk`, using combo3's own population loader.
- All 3,117 per-trade results are **identical to `combo3_trades.pkl`** (exact float equality, order included).
- `combo3_report.txt`: 20 of 20 rows match, 0 mismatches.
- `combo3_class_report.txt`: 20 of 20 cell lines match.
- Report: `data/study/oos_queue_validate_report.txt`.
- The look code path was also dry-tested in a scratch directory on EURUSD/US100 copies. It reproduced the bridge loader trade for
  trade.

**Deviations from the original seven's method**
1. **TrendCont comes from the four-detector AA run.** The originals' TrendCont came from the standalone record REC
   (`data/study/trendcont`), which was built before the 2026-09-09/10 re-arm fix and includes the defect's re-entries. The current
   build cannot reproduce REC.
   - Bridge on the original seven, with TrendCont from the pinned AA journals instead of REC: `--symbols <seven> --aa-trendcont`,
     report `data/study/oos_queue_bridge_report.txt`. It shows what the population swap alone does, on SCALED, 2012-24:

     | | combo3 population (REC TrendCont) | AA TrendCont |
     |---|---|---|
     | n | 2775 | 2486 |
     | base | +0.1146 | +0.0776 |
     | base DD | 27.4 | 38.4 |
     | A+B+I | +0.0315 (t 2.6), DD 23.5 (support) | +0.0172 (t 1.4), DD 39.8 (**no support**) |
     | B | +0.0059, DD 22.2 | +0.0019, DD 36.1 (pass) |
     | I | +0.0226 (t 3.2) | +0.0142 (t 2.0), FX long +0.029 (pass) |
     | A | pass | **fails the strict reading on 2025-26 DD** (23.6 vs 23.4) |

   - **Coach to note before the look:** under the population the new symbols will use, A and the package are already marginal on
     the original seven.
2. **Ref price** (SCALED spread scaling): `universe_cost_filter.csv` price (the box, 2026-09-21). The originals used the
   `spreads.json` price (the box, 2026-09-17). The spread profile is the same file and the same run for all symbols.
3. **Drag** (BID only): same formula. The median stop and median entry come from the new AA journals only, because there are no
   graded windows on the new symbols.
4. **H4** is rebuilt from the tick CSV, not copied from the terminal. It is bit-identical on EURUSD and GBPUSD (16,110 and 20,147 bars,
   0 mismatches). The importer's digits are checked; if any tick needed rounding, the symbol is stopped.
5. **Windows:**
   - The tester runs from 2012.01.01 to 2026.06.30. MT5 shifts the start for warm-up, as it did for USDJPY/XAUUSD.
   - Signals are cut at 2026-07-01, the latest original end (EURUSD). That leaves ≥3 months of M1 runway.
   - US30 runs from 2013-01-01: its ticks are exported from 2013, and the tester and the study both start there.
6. **Import replaces the existing `.dk` symbols.** The fleet copies of the nine (2020-01 to 2026-07) are deleted and re-created with
   2012 to 2026-10 history. Specs are inferred by TickImport, as before.

**One-look risks**
- The AA journals carry the EA's base outcome (`r_multiple`, `pnl`, `terminal`, `mfe_r`, `rt_*`), and the tester writes an HTML
  report with profit and drawdown. Opening or summing either is a look at base.
- `oos_build_inputs.py` (drag, qa) and `oos_parity_check.py` read signal fields only. The stage log prints only signal counts.
- The guards are procedural. Deleting the report bypasses them, so record the report's timestamp and sha on the day of the look.

**Observation, not acted on:** the QDM M1 series sits below the `.dk` bid and almost never above it (above in 0% of EURUSD H4 closes,
2.6% of US100). The median gap is 1e-5 on EURUSD (below in 78% of closes) and 0.8 pt on US100 (below in 94%). The engine's per-trade offset absorbs the level, and the effect is the same for old and new
symbols. Still, "M1 = bid" in `ask_side_study`'s docstring is not exact.

## THE LOOK - 2026-10-03 15:15 (data/study/oos_queue_report.txt, sha256 7ebccc817db25c33...)
- Pool: AUDUSD, USDCAD, USDCHF, NZDUSD, USDMXN, XAGUSD (3,494 trades). EXCLUDED before the look for a data fault: EURJPY, GBPJPY, EURGBP
  (the tester had no USDJPY/GBPUSD/EURUSD conversion history before 2020/2013/2016, so no or stuck trades in those years) and US30
  (no data). These may only appear later as a separate addendum, never re-pooled. Trader's call: look now rather than fix first.
- Bars: A PASS (FX-short cell +0.0075, so both sides), B PASS, I FAIL (2012-24 -0.0040, t -0.86; FX longs -0.0125) -> I CLOSED.
- 2012-24 SCALED: base -0.0464R/trade, DD 170.2 | A+B +0.0155 (t 1.64), DD 114.4 | A+B+I +0.0115 (t 1.10), DD 134.6.
  2025-26: base -0.1254, DD 54.0 | A+B +0.0099, DD 47.0.
- NOTE for the coach: the BASE is negative on all six pooled symbols (TrendCont from today's fixed detector); every one of the nine
  is in the live 47-symbol demo universe.
