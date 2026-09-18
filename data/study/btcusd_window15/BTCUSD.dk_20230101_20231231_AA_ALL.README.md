# BTCUSD.dk 2023 AA baseline (window 15) — read before grading
- Generated 2026-09-18. Headless AA_ALL, real ticks, H4, $25k, lineup SMC,Fib,TrendCont,EMArevQ, current doctrine.
- **§10.2 ATR floor 1.385 ON** (BTCUSD only, widen-only, all detectors; TEST ONLY per §10.5 - never in a live config) and
  **§10.1 weekend-flat ON**. The trader's run uses the identical two inputs via `pipeline/start_level.sh 15`.
- Columns `stop_pre_floor,stop_post_floor,floor_applied` appear only because the floor is on.
- `*.nofloor.csv` beside it = same window, floor OFF, weekend-flat ON (the with/without comparison).
- **Zero-spread caveat:** the .dk import carries no spread; spread, swaps and commission are applied post hoc by
  `pipeline/cost_journal.py`. The window is graded on costed R.
- Full report: `data/study/btcusd_window15/baseline_report.md`.
