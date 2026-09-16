# BTCUSD feasibility study — report (one look, pre-registered)

**Run:** 2026-09-16 · frozen four-detector system, doctrine-v2 exits, regime gate, 1% viability gate · headless AA tester, real ticks (Dukascopy), H4 · `AA_BTCUSD.dk_20180101_20260716.csv`
**Verdict against the §7 gate (costs-on): FAIL**

## Data
- Import: `BTCUSD.dk` custom symbol, Dukascopy ticks 2017-05 → **2026-07-16** (last imported tick; rolling importer, see `import_log.txt`). Tester range requested 2018-01-01 → 2026-09-16; effective end 2026-07-16.
- Effective start: **2018-01-02** - the import holds history from 2017-05, so the ~210-D1 regime warm-up was complete before 2018-01-01 and every fill carries a regime tag (the spec's 2018-08 assumption was conservative; the halves are therefore 2018-01 → 2021-12 and 2022-01 → 2026-07).
- Spread: **the import carries no spread** - every BUY fill equals the detector's bid-level (max |fill − level| = $0.00 over 254 BUY fills), i.e. the tester ran mid-price for spread purposes, exactly the flattering case the spec warns about. Corrected by charging the spread explicitly: live BTCUSD.sim spread sampled on the shadow box 2026-09-16 (11 samples over 30 s: mean $3.09, min $3.00, max $4.00 at $75,662). The BTCUSD.dk import carries NO spread (every fill equals the detector level), so the spread is applied here as an explicit cost. That is 0.0041% of price; per trade it costs spread$/stop$ in R (fleet mean below). Sensitivity: a $30 spread (10×, closer to 2018–2020 CFD conditions) would cost ~1.5% of R per trade at a $2,000 stop.

## Costs used (per OANDA-Demo-1 'BTCUSD.sim' MT5 Specification window, read 2026-09-16 (trader screenshot))
| item | value |
|---|---|
| contract size | 1.0 BTC per lot · digits 2 · min 0.01 · step 0.01 · max 4.0 |
| swap type | in percentage terms, using current price (annual %, /360 per night) |
| swap long / short | **-30.31% / -19.69% p.a.**, charged at Mon–Fri rollovers ×1, none on Sat/Sun, no triple day |
| commission | **0.0325% of notional per side** (in + out) |
| spread | **$3.09 per round trip** (live sample, see Data) |
| sessions | Mon-Fri 00:05-23:59 (no weekend quotes on this broker) |
| applied as | swap_R = Σ charged nights (rate/100/360 × entry price × lot fraction) / stop$; lot fraction 1.0 before the +1R bank, 0.5 after; comm_R = 2 × 0.0325% × price / stop$; spread_R = $3.09 / stop$. All in R, so the tester's own lot size does not matter. |
| min-lot at $25k | lots = floor(($250 / stop$) / 0.01) × 0.01; **0 of 426 fills suppressed** (< 0.01 lot); mean realised risk after rounding = 97.8% of the 1% target |

Average cost drag (fleet): **-0.087R per trade** (mean 2.2 charged nights per trade).

## Cells (costs-off / **costs-on**)
| cell | n | avg R off | **avg R on** | win% on | bank% (+1R) | TP% | maxDD off / **on** (R) | cadence/yr | drag R/trade |
|---|---|---|---|---|---|---|---|---|---|
| FLEET (scope: from effective start, min-lot passed) | 426 | +0.068 | **-0.019** | 52% | 53% | 16% | 27.2 / **32.0** | 50.4 | -0.087 |
| ALL fills incl. warm-up | 426 | +0.068 | **-0.019** | 52% | 53% | 16% | 27.2 / **32.0** | 50.4 | -0.087 |
| half 1: 2018-01 → 2021-12 | 175 | +0.083 | **-0.001** | 54% | 55% | 15% | 10.5 / **15.5** | 43.9 | -0.084 |
| half 2: 2022-01 → 2026-07 | 251 | +0.057 | **-0.031** | 51% | 51% | 16% | 27.2 / **32.0** | 56.4 | -0.088 |
| detector DeepFib | 24 | +0.352 | **+0.245** | 54% | 58% | 21% | 4.3 / **5.0** | 3.0 | -0.107 |
| detector EMArevQ | 27 | +0.040 | **-0.043** | 56% | 56% | 30% | 4.2 / **5.2** | 3.3 | -0.082 |
| detector SweepMSS | 75 | +0.247 | **+0.164** | 59% | 59% | 23% | 11.1 / **12.7** | 9.0 | -0.084 |
| detector TrendCont | 300 | +0.003 | **-0.083** | 50% | 50% | 13% | 16.6 / **32.5** | 35.5 | -0.086 |
| regime TREND_UP | 244 | +0.051 | **-0.034** | 50% | 50% | 16% | 16.6 / **29.8** | 31.2 | -0.085 |
| regime TREND_DOWN | 150 | +0.138 | **+0.052** | 57% | 59% | 15% | 9.0 / **12.0** | 18.3 | -0.086 |
| regime CHOP | 32 | -0.131 | **-0.235** | 44% | 44% | 16% | 5.5 / **7.9** | 4.1 | -0.104 |
| class TAKE | 73 | +0.429 | **+0.345** | 63% | 64% | 26% | 9.6 / **10.9** | 8.8 | -0.084 |
| class DISCRETION | 353 | -0.007 | **-0.094** | 50% | 50% | 14% | 22.1 / **39.6** | 41.8 | -0.087 |
| direction BUY | 256 | +0.075 | **-0.015** | 52% | 52% | 17% | 16.1 / **19.8** | 30.8 | -0.090 |
| direction SELL | 170 | +0.058 | **-0.025** | 53% | 54% | 15% | 16.9 / **21.6** | 20.5 | -0.082 |

## Gate (§7, costs-on, scope = from effective start, min-lot passed)
| criterion | result | value |
|---|---|---|
| 1. fleet avg R (costs-on) >= 0.00 | FAIL | -0.019R |
| 2. both halves >= 0.00 | FAIL | H1 -0.001R, H2 -0.031R |
| 3. cadence 15-60 signals/year | PASS | 50.4/yr |
| 4. no detector < -0.10R on n>=30 | PASS | none |
| 5. blind max drawdown <= 15R | FAIL | 32.0R (costs-off 27.2R) |

**FAIL.** Closed on the first strike per §7 - no re-cuts, no parameter search.

## Ten largest winners / losers (costs-on, scope)
| winners | R on | | losers | R on |
|---|---|---|---|---|
| #77 2020-02-11 DeepFib BUY | +3.98 | | #34 2019-01-03 TrendCont SELL | -3.31 |
| #285 2023-10-19 DeepFib BUY | +3.85 | | #42 2019-02-21 DeepFib BUY | -2.26 |
| #157 2021-07-14 DeepFib SELL | +2.75 | | #24 2018-07-13 TrendCont SELL | -1.30 |
| #131 2021-01-29 TrendCont BUY | +2.73 | | #38 2019-01-30 TrendCont SELL | -1.29 |
| #409 2025-11-12 TrendCont SELL | +2.65 | | #273 2023-08-14 DeepFib SELL | -1.26 |
| #70 2019-11-10 DeepFib SELL | +2.59 | | #388 2025-06-30 TrendCont BUY | -1.25 |
| #181 2022-01-04 TrendCont SELL | +2.54 | | #36 2019-01-24 TrendCont SELL | -1.24 |
| #213 2022-07-19 DeepFib BUY | +2.52 | | #416 2025-12-22 TrendCont SELL | -1.20 |
| #134 2021-03-08 TrendCont BUY | +2.51 | | #269 2023-07-22 TrendCont BUY | -1.19 |
| #410 2025-11-20 TrendCont SELL | +2.51 | | #264 2023-06-25 TrendCont BUY | -1.19 |

## Rulings applied (§5)
- USD V/W calendar rows bound as for indices (the frozen EA gates; no crypto-specific events exist in the feed - **unmodeled**: ETF rulings, halvings, exchange failures).
- Late-Friday, weekend-hold and overnight-timing checklist steps: not EA gates, so nothing to suspend in the run; they are advisor/checklist items and were simply not applied. **Broker reality check:** this broker quotes BTCUSD **Mon–Fri only** (no weekend sessions), so "24/7" does not hold on the shadow account; weekend gaps exist in the Dukascopy ticks only where Dukascopy itself paused.
- Sizing 1% per the frozen gate; the 1 BTC contract honoured in the min-lot check above. Live, if ever: risk multiplier 0.5.

## What the study could not model
Crypto-specific event risk (ETF decisions, halvings, exchange failures) is absent from the feed; the tester spread is Dukascopy's, not this broker's floating spread; swap is charged on the entry price rather than each night's close (a second-order simplification); 'end' terminals (23 fills still open at the data end) are marked to the last price; Dukascopy coverage gaps, if any, show as missing bars in the import log.
