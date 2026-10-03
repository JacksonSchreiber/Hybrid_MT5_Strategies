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
