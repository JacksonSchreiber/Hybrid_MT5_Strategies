# Studies checked - not forwarded to the coach

Record of ideas the trader tested that were not good enough for the coach queue (docs/coach_queue.md). One line each: the rule, the
result (M1 ask-corrected, all four detectors, pinned record, 2012-24 | 2025-26 vs the live stack), and the script/report. Kept so
nothing is re-run by accident and the coach can see what was tried.

## 2026-10-01 / 02

- **Trial-period actions at +1.5R** (pyramid 66-100%, sell the trial 25%, never promote; also at +1.75..+2.5R).
  - Bigger pyramid: +0.005R per extra 16% of size, with drawdown up.
  - Sell / never promote: lose at every level.
  - `trial_pyr_study.py`, `trial_levels_study.py`
- **Fast-starter stop raise** (+1.5..+3R milestone in the trial -> stop to +0.25..+2R): every cell negative. `trial_raise_study.py`
- **Underwater cut** (below 0 / -0.25 / -0.5R after 1-5 days, full or partial): every cell negative (partial cuts are a cheap
  drawdown dial only). `time_cut_study.py`, `partial_cut_study.py`
- **Renewed trial for underwater trades at bar 6:** about 0 R, drawdown worse. `trial_renew_study.py`
- **Promote +2R-in-24h fast starters to 100-200%:** +0.003R per extra 25%, with drawdown up (R/DD worse). `early_promote_2r_study.py`
- **Day-2 average cut on ALL trades** (bars 0 / +0.25 / +0.5R, sell 25-100%): -0.007 to -0.042R; longs pay. The shorts-only
  version IS in the queue (entry 2). `day2_avg_cut_study.py`
- **Average-cut grid, days 1-5 x bars 0..+1.5R:** all trades and longs-only negative in all 35 cells; shorts-only summarised in
  queue entry 2d. `avg_cut_grid_study.py`
- **Day 3, average below +0.5R:** sell 25-100% or stop raise to -0.5..+0.25R.
  - All trades: -0.005 to -0.027R.
  - Longs: negative everywhere.
  - Shorts: sell about +0.001 | +0.007 to +0.038; stop raise -0.002 to -0.005 | +0.014 to +0.027.
  - Not forwarded (trader: not good enough). `day3_weak_study.py`, `data/study/day3_weak_report.txt`
- **Day 5, average below +0.25 / +0.5 / +0.75 / +1R:** sell 25-100% or stop raise to -0.5..+0.25R.
  - 2012-24: negative in EVERY cell, for all trades (-0.003 to -0.029R), longs and shorts (shorts -0.000 to -0.006).
  - 2025-26: mostly positive (shorts +0.003 to +0.041; the stop raise to entry +0.016 to +0.022 on all trades).
  - Too late to act: by day 5 the stalled trades left are the ones that recover often enough. Not forwarded.
  - `day3_weak_study.py 30 <bar>`, `data/study/weak_bar30_{0.25,0.5,0.75,1}_report.txt`
- **Gate decision-time grid (12-72h):** withdrawn by the trader, because the add is fixed at 24h by design. `gate_grid_study.py`
- **Day 4, stop to +0.25R** (all open trades, closed at market if already below; or only if price > +1R; or only if the average
  since entry > +1R).
  - 2012-24, all trades: -0.035 (t -2.6) / -0.015 / -0.011.
  - 2025-26: -0.015 / -0.033 / -0.007.
  - Longs pay all of it. Shorts are about 0, as most are already at +0.25R from the live shorts rule. Many trades are helped,
    but the few hurt are TP2 misses worth several R.
  - Not forwarded. `day4_raise_study.py`, `data/study/day4_raise_report.txt`
- **Late breakout: average since entry < 0 but price > +1R -> stop to +0.25R** (checked at days 1-5).
  - It almost never happens on a trade that is still open: 0-2 trades changed per checkpoint in 2012-24, 0 in 2025-26.
  - Effect +0.0000..+0.0003R. No signal either way. `late_breakout_raise_study.py`, `data/study/late_breakout_raise_report.txt`
- **Same, with price > +0.25 / +0.5 / +0.75R** (average < 0 -> stop +0.25R, days 1-5).
  - Rare: 1-35 trades changed per cell in 2012-24, 0-3 in 2025-26.
  - Mostly small negatives in 2012-24: worst -0.008R at +0.25R / day 3, where the hurt trades average -1.06R each because the
    tight stop cuts recoveries. Day 1 / price > +0.5R is +0.001 on 9 trades.
  - Longs pay; shorts are about 0. Nothing usable.
  - `late_breakout_raise_study.py <price>`, `data/study/late_breakout_raise_px{0.25,0.5,0.75}_report.txt`
- **Average < +0.25R and price > +0.5 / +0.75 / +1R -> stop to breakeven or +0.25R** (days 1-5).
  - Days 2-5 mostly small negatives in 2012-24 (longs pay; shorts slightly positive in every cell, +0.000..+0.002).
  - Price > +1R with stop to breakeven never changes anything: those trades are banked and already at breakeven.
  - The one consistent cell is **day 1 (the bar-6 close), price > +0.5R, stop to +0.25R**: 2012-24 +0.0048 (t 1.4), 48 trades
    changed (44 helped / 4 hurt), drawdown 27.4 -> 28.3; 2025-26 +0.013 on 5 trades. Shorts t 2.8 within it. Small, and picked
    from a 30-cell grid. Not forwarded unless the trader asks.
  - `late_breakout_raise_study.py <price> 0.25 <stop>`, `data/study/late_breakout_px*_avg0.25_sl*_report.txt`
- **Day 7, average between +1R and +1.5R -> stop to +0.25 / +0.5 / +0.75 / +1R.**
  - 2012-24: -0.003 / -0.012 / -0.011 / -0.015R (t to -2.3); 2025-26: -0.018 to -0.028R (4 trades).
  - Only 20-50 trades are affected. The hurt trades (-0.4 to -0.8R each) are runners that dip and then reach TP2.
  - Not forwarded. `day7_band_raise_study.py`, `data/study/day7_band_raise_report.txt`
- **Pyramid gated on the average** (add the +1.5R pyramid only if the average since entry > 0 / +0.25 / +0.5 / +0.75 / +1R;
  reference: no pyramid).
  - 2012-24: every gate loses, -0.001 / -0.011 / -0.021 / -0.034 / -0.041R. No pyramid at all costs -0.043R (t -4.1), so the
    pyramid is worth about +0.04R a trade.
  - Even the late breakouts (low average) pay off from the pyramid: skipped trades lose about -0.12R each.
  - Shorts: the pyramid is worth +0.009 in 2012-24 but -0.034 in 2025-26 (the usual short-side split).
  - Not forwarded. `pyramid_gate_study.py`, `data/study/pyramid_gate_report.txt`
- **Fading shorts cut at day 2** (second-day average below the first by more than 0 / 0.25 / 0.5R; also B restricted to fading
  shorts).
  - All weaker than the existing B (48h average <= +0.5R): fading-only -0.001..+0.002R in 2012-24; "B and fading" +0.004 vs B
    +0.006.
  - B stays the short-side rule. `trend_of_avg_study.py`
- **Bigger bank for weak shorts** (bank 50% / 75% instead of the detector share when a short reaches +1R with its average below 0
  / +0.25 / +0.5R, or all shorts).
  - 2012-24: slightly negative in every cell (-0.001..-0.003R), drawdown a little lower (to 24.7R).
  - 2025-26: positive (+0.002..+0.021, t 2-4), the usual bad-period-for-shorts pattern.
  - Not forwarded. `short_bank_avg_study.py`, `data/study/short_bank_avg_report.txt`
- **Worsening streaks -> sell** (daily averages falling 2..5 days in a row; sell 25-100%; longs and shorts separately).
  - Longs, 2-day streak: clearly negative (-0.030R at 100%, t -3.1; -0.007 at 25%). 3+ day streaks are rare (2-39 trades), about 0.
  - Shorts: about 0 at every length (-0.002..+0.001).
  - Not forwarded. Improving streaks -> add are summarised in queue entry 3. `streak_avg_study.py`, `data/study/streak_avg_report.txt`
- **Rule C: day-2 add +50% when the 48h average > +1R (longs)** - withdrawn from the coach queue by the trader on 2026-10-03 and
  replaced by I (improving longs).
  - Alone: +0.008R (t 2.1), drawdown 26.6R.
  - In the package, A+B+C gave +0.017 / DD 22.4R against A+B+I's +0.032 / 23.5R.
  - `day2_avg_add_study.py`, `combo3_study.py`
- **Commodity-bloc draft v5.2** (trader 2026-10-04; 100% entry, 1h/3.5h average checks, stop to entry +0.5R longs / +0.3R shorts, 16h
  cut at average < +1.5R, +1R lock at +1.5R, +100% at +2R, TP2).
  - In-sample (AUDUSD/NZDUSD/USDCAD): +0.066R/trade, DD 12.5 at model cost; each extra pip of spread ~ -0.034R.
  - Holdout one look (AUDCAD/AUDNZD/NZDCAD): -0.078R/trade (t -4.6), FAIL. `cbloc_v52.py`, `cbloc_holdout_look.py`,
    docs/cbloc_holdout_report_2026-10-04.txt

- **Commodity-bloc time stop raise** (trader 2026-10-04; AUDUSD/NZDUSD/USDCAD; stop to entry at the bar-3 close, +0.25R at bar 6,
  +0.5R at bar 18, tighten only; closed at that bar close if the price is already through the new stop).
  - **On the raw signal it helps a lot:** -0.107 -> -0.017R/trade (t 2.6), DD 221 -> 82. Most of that is closing trades that are
    below entry at 12h.
  - **On the live bloc setup (T+L1.0) it adds nothing:** +0.018 -> +0.016R/trade (t -0.4), DD 15 -> 11. The "skip if already
    through" version is +0.019 (t 0.3). The 24h cut and the +1R lock already do the job.
  - **Raw, levels entry / +0.5R / +0.8R** (same bars): -0.012R/trade (t 2.65 vs raw), DD 89. That is barely different from the first
    levels (-0.017, DD 82), and still below the live bloc setup (+0.018). `--levels 0,0.5,0.8 --raw`
  - **Raw, entry at 4/6/8/12h then +0.5R at 24h (no 3-day step):** -0.021 / -0.029 / -0.025 / -0.017R/trade, DD 65 / 70 / 67 / 92.
    An earlier entry stop cuts the drawdown but not the average; the hours are within noise of each other. `--hours H,24 --levels 0,0.5 --raw`
  - **Raw, entry at 0.5/1/2h then +0.5R at 24h:** -0.009 / -0.006 / -0.014R/trade, DD 20 / 24 / 42. About half the trades are
    closed at 30 min.
  - **Raw, 4h or 8h entry stop + an average check at 0.5/1/2h (close if the average since entry < 0R):**
    - 4h: -0.017 / -0.007 / -0.023;
    - 8h: -0.006 / **+0.004** (DD 31) / -0.006.
    - Read: every early-exit variant lands at about -0.02..+0.004; these are differences within noise after about 15 variants on the
      same data, all still below the live bloc setup (+0.018, DD 15), and at model cost.
  - **+ stop to -0.5R at 1..7h on top of "8h entry + 1h average check":** -0.002 / -0.002 / +0.000 / -0.001 / -0.003 / -0.002 /
    +0.001R/trade, DD 31-38, against +0.004 / DD 31 without it. Fewer full -1R stops, but dips to -0.5R that would have recovered (fewer
    TP2s) cost the same. Shorts slightly better, longs worse (as in the bloc drafts). `--hours T,8,24 --levels -0.5,0,0.5 --avgcheck 1`
  - **+ bank at TP1 on "8h entry + 1h average check"** (TP1 is about +2R for TrendCont/SweepMSS; it is reached on 161 trades):
    - stop unchanged, 25 / 50 / 75%: +0.005 / +0.007 / +0.008R/trade, DD 26 / 22 / 22 (no bank: +0.004, DD 31);
    - stop to entry at the bank: about the same.
    - Small and monotonic: banking more at TP1 helps a hair and trims the drawdown. Still below the live bloc setup. `--bank F [--bank-be]`
  - **The 1h average check's threshold, 0 / -0.05 / -0.1 / -0.15 / -0.2 / -0.25 / -0.3R:**
    - +0.004 / +0.003 / -0.001 / -0.014 / -0.017 / -0.023 / -0.027R/trade, DD 31 / 31 / 41 / 50 / 59 / 66 / 69;
    - with a 75% bank at TP1: +0.008 / **+0.012 (DD 20)** / +0.007 / -0.005 / -0.004 / -0.011 / -0.013.
    - A lower threshold keeps more TP2s (76 -> 100) but lets many more trades run to -1R (127 -> 295). 0 to -0.05 is the flat top;
      the -0.05 + bank peak is probably noise after about 30 variants on the same data. `--avgthr X`
  - **The same thresholds with the average check at 2h and 4h:** every setting is below the 1h check.
    - 2h at 0 / -0.05R: -0.006 / -0.006R/trade (DD 35 / 31); 4h at 0R: -0.008 (DD 38).
    - With the 75% bank, at best +0.003 (2h at -0.05R).
    - A later check lets more trades reach -1R first (127 at 1h -> 149 at 2h -> 207 at 4h, at a 0R threshold) and saves only a
      few TP2s.
  - **Out-of-sample check of the best set on the crosses** (AUDCAD/AUDNZD/NZDCAD; never used to build this family; 1h average check
    < 0R, entry at 8h, +0.5R at 24h):
    - raw -0.099 -> rule -0.046R/trade (t 1.3), DD 83;
    - with a TP1 bank of 25% -0.048, of 75% -0.053 (the bank effect does not carry over);
    - long -0.037 / short -0.056: the USD "shorts carry it" does not carry over either.
    - Worse than the live T+L1.0 on the same crosses (-0.024). The 8h close and the entry stop cost R on the crosses (-6 and -38R
      vs raw), where on the USD pairs they gained. Not forwarded. `--syms AUDCAD.dk,AUDNZD.dk,NZDCAD.dk`
  - **Middle-ground tries, judged on the worse of USD / crosses** (raw, 100% at the signal, 1h average check):
    - **+ 24h cut unless +1.5R + +1R lock at +1.5R (no 8h step):** at 0R USD **+0.035** (DD 21) / crosses -0.037 (DD 75); at
      -0.05R +0.052 / -0.048. The best worse-group so far; pooled about 0.
    - **+ -0.5R at 8h + +0.5R at 24h:** at 0R +0.005 / -0.060; at -0.05R +0.002 / -0.070.
    - On the crosses longs carry the loss (-0.062 vs shorts -0.013). (The "lock / cut vs raw" figures, -6 / -13R on the crosses vs
      +71 / +23R on USD, group trades by how they exited, which sorts them by outcome. The coach ruled 2026-10-04 that they are not
      evidence of context dependence; don't cite them.) `--hours none --avgcheck 1 --bloc`
  - Fixed 2026-10-04: checks inside the entry bar now start at the first M1 minute. Sunday-open bars have no data for 60-120 min, so
    about 2% of trades were skipping every step. The 4h/8h/12h runs were unaffected. `--avgcheck H`
  - `cbloc_timeraise_study.py`, `data/study/cbloc_timeraise_report.txt`
- **Similar-trades (nearest neighbour) test** (trader 2026-10-04, for the per-signal settings / path chart proposal).
  - **Setup:** 8,599 trades. Neighbours are the 50 nearest in the same group + direction + detector, matched on 12 signal-time
    features, and taken from other years (and, separately, from earlier years only).
  - **Outcomes: not predicted.**
    - raw R skill vs the plain group average -0.016; live-setup R -0.013; reached TP2 -0.009 (all < 0);
    - no group > +0.004; larger k only approaches 0;
    - no single feature has positive skill.
  - **Letting the neighbours pick the preset: no gain** (-0.007 vs the group's best preset -0.002, t -0.4).
  - **Move size: predicted.** Best R in the first 24h: skill +0.08, quintiles +0.60 -> +1.55R. That is mostly mechanical (stop size
    vs ATR alone gives +0.16); without stop/TP features the context still gives +0.03. The 12-bar path +0.08.
  - Read: neighbours tell you how far a trade typically travels (in R), not whether it wins or which management to use.
    `neighbor_path_test.py`, `data/study/neighbor_path_report.txt`
- **Shape matching (analogs) on the last month of bars** (trader 2026-10-04).
  - **Setup:** 8,556 trades; the last 120 H4 bars or 22 completed days before the signal (closes, + highs/lows, or z-normalised pure
    shape); the 50 / 10 / 1 closest by Euclidean distance; same group or any symbol; neighbours from other years.
  - **Result: no version beats the plain group average for any target.**
    - raw R skill -0.016 to -0.021; TP2 -0.015 to -0.020;
    - even the first-day move size and the 12-bar path are below the average (-0.01 / -0.03);
    - the 10 / 1 closest are far worse (-0.10 / -1.0: single outcomes are noise).
  - Preset picked by the matches: -0.001 to -0.028 vs the group's best preset (no gain). A faint top-vs-bottom fifth spread on
    live-setup R (+0.12..+0.16, t 2.4-3.2) recurs, but it does not survive as a prediction (skill < 0). `shape_match_test.py`
