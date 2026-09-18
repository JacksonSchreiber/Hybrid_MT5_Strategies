# BTCUSD.dk 2023 AA baseline (window 15) — read before grading
- Regenerated 2026-09-18. Headless AA_ALL, real ticks, H4, $25k, lineup SMC,Fib,TrendCont,EMArevQ, current doctrine.
- **§10.2 ATR floor: OFF** (dropped per §10.6 after it was refuted on this baseline; the input exists and defaults to 0).
- **§10.1 weekend-flat: ON** — verified (weekend-boundary closes at Sat 00:00 = the Friday close; no weekend decisions).
- **§10.7:** last column `weekend_candle` = 1 when the signal candle is a Sat/Sun bar (decision taken on the Monday bar).
  5 such rows; they account for -2.64R of the costed base.
- Costed total **-6.40R** (avg -0.0914); the window is graded on costed R.
- The dropped floor-ON run is kept beside this file as `*.floorON_dropped.csv` for the record.
- Full report: `data/study/btcusd_window15/baseline_report.md`.
