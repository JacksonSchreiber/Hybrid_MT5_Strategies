# BTCUSD.dk 2019 AA baseline (window 14) — read before grading
- Generated 2026-09-16, headless AA_ALL, real ticks, H4, $25k, lineup SMC,Fib,TrendCont,EMArevQ, current doctrine (v2 exits).
- **Zero-spread caveat:** the BTCUSD.dk import carries NO spread (every fill equals the detector level). Spread, swaps and
  commission are therefore applied post hoc by `pipeline/cost_journal.py` (OANDA BTCUSD.sim figures as read 2026-09-16:
  swaps -30.31% long / -19.69% short p.a. on notional, Mon-Fri, no triple day; commission 0.0325%/side; spread $3.09).
  The window is graded on the COSTED figures (`*_AA_ALL.costed.csv`), for both the baseline and the trader's journal.
- Per-symbol flag state (config/symbol_rules.json, journaled): class crypto; late-Friday rule off; weekend-hold flag off;
  overnight-timing steps advisory; USD V/W event rows bind as for an index. The EA has no session gates (nothing forked).
