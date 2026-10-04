# Locked-symbol look: does the live stack generalise? (trader 2026-10-04)

Written BEFORE the look. One look only; afterwards both symbols are spent (move them to "exposed" in the research lock).

- **Symbols:** XAGUSD and USDCHF, all years 2012-26 (both locked until now, never studied). Picked as the closest relatives of the
  live stack's design symbols: XAUUSD for silver, the USD majors for USDCHF.
- **Still locked:** EURJPY, GBPJPY, EURGBP, USDMXN, US30.
- **Data:** combo build 20261004_1402 (M1 replay, model cost). The question is the improvement over raw, not the level.
- **Primary: live stack without the bloc rules vs the raw signal, pooled across both symbols.**
  - The stack **generalises** if the improvement is > 0 with t >= 1.5.
  - It is **fitted** to its design symbols if the improvement is <= 0.
  - Anything in between is **inconclusive**.
  - Reported per symbol, long/short and 2012-24 / 2025-26 as well.
- **Secondary:** live stack + bloc rules (24h cut / +1R lock) vs raw. Expected to be negative here, as on the other trending groups.

## RESULT (look taken 2026-10-04; both symbols are now spent)
- **Primary: live stack vs raw, pooled -0.017R/trade (t -0.39). Verdict: FITTED** (improvement <= 0). It doesn't generalise
  beyond its design symbols.
  - XAGUSD -0.004 (t -0.07), USDCHF -0.030 (t -0.48).
  - It cuts the drawdown (141 -> 93R) but not the losses: the raw signal itself is negative on both (-0.044).
- **Secondary: live + bloc rules vs raw +0.023 (t 0.44).** Not negative as expected; within noise. Drawdown 141 -> 28R.
- **Other splits:**
  - long -0.023 / short -0.011 (live vs raw);
  - 2012-24 +0.015, 2025-26 -0.334 (n 107; raw +0.241 there).
- Silver did not behave like gold (raw -0.023 vs gold's +0.19 in-sample).
- Data: `data/study/locked_look_2026-10-04.txt`
