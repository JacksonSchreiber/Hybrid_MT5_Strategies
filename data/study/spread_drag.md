# Spread drag per lineup symbol (coach item 3, 2026-09-17)

**Why:** every `.dk` import in the program's history carries NO spread - each fill equals the detector's level, so every tester
window and every AA baseline ran at mid-price. This table is the honest discount to apply to those figures.

**Method:** live spread sampled on the shadow box (OANDA-Demo-1, 12 samples over 30 s per symbol, 12 ticks each, 2026-09-17 ~23:58 UTC);
median stop distance = median |entry - SL| over every APPROVED row in the graded journals and parked AA baselines
(5514 rows over 156 files). Drag in R = spread / stop, charged once per round trip
(enter at ask, exit at bid). Two columns because spreads scale with price: today's spread over a window traded at a much
lower price level overstates the cost, so the scaled column re-prices the spread at that symbol's median graded price
(spread % of price x median graded price / median stop). **The scaled column is the discount to use.**

| symbol | live spread (today) | spread % of price | median stop (graded) | drag at today's spread | **drag scaled to the window's price level** | approved rows |
|---|---|---|---|---|---|---|
| BTCUSD | 3 | 0.0039% | 632.725 | 0.005R | **0.002R** | 484 |
| EURUSD | 0.00002 | 0.0017% | 0.00325 | 0.006R | **0.006R** | 1277 |
| GBPUSD | 0.00004 | 0.0030% | 0.00514 | 0.008R | **0.008R** | 870 |
| US100 | 2.3 | 0.0078% | 74.6 | 0.031R | **0.008R** | 722 |
| US500 | 0.5 | 0.0065% | 18.1 | 0.028R | **0.010R** | 949 |
| USOIL | 0.071 | 0.0702% | 0.893 | 0.080R | **0.049R** | 786 |
| XAUUSD | 0.38 | 0.0087% | 11.6785 | 0.033R | **0.015R** | 426 |

Reading it: FX and the indices are small (0.006-0.010R scaled). XAUUSD ~0.015R. **USOIL is the outlier at 0.049R
scaled** (0.080R at today's price level) - on a blind base of a tenth of an R that is a material discount, and the
WTI windows (9b, 12b) should be read with it. BTCUSD's spread drag is trivial in R terms because its stops are wide
(0.002R); its cost problem is swaps, not spread (see `data/study/btcusd/report.md`).

Caveats: OANDA demo spreads, sampled once in a quiet window - real spreads widen around releases and at the rollover,
and the historical scaling assumes spread stayed a constant fraction of price. Commission and swaps are separate and
are not in this table (see `pipeline/cost_journal.py`).
