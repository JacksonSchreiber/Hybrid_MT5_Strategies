# Window 14 — BTCUSD.dk 2019 — AA baseline report (coach 2026-09-16; trader-initiated discretion window)

**Baseline:** `journal/baselines/BTCUSD.dk_20190101_20191231_AA_ALL.csv` (+ `.actions.csv`, `.costed.csv`, README sidecar). Headless AA_ALL, real ticks, H4, $25k, current doctrine, four-detector lineup. Generated 2026-09-16.
**Grading basis:** COSTED figures (`costed_r`), same script on the baseline and on the trader's journal.

## Signal counts per detector (journal rows)
| detector | signals |
|---|---|
| DeepFib | 4 |
| EMArevQ | 4 |
| SweepMSS | 8 |
| TrendCont | 24 |
| **total** | **40** |

## AA baseline totals
| | costs-off | **costed** |
|---|---|---|
| closed trades | 37 | 37 |
| total R | +2.64 | **-2.00** |
| avg R | +0.071 | **-0.054** |
| TrendCont (n=23) | -3.69 | **-6.62** |
| SweepMSS (n=7) | +6.33 | **+5.60** |
| EMArevQ (n=3) | -0.40 | **-0.67** |
| DeepFib (n=4) | +0.40 | **-0.30** |

## Costing parameters (config/symbol_rules.json → BTCUSD.costs)
- Source: OANDA-Demo-1 BTCUSD.sim specification read 2026-09-16 (trader screenshot)
- Swaps: -30.31% long / -19.69% short p.a. on notional, Mon-Fri x1, no triple day; charged per rollover night on the entry price, lot fraction 0.5 after the +1R bank
- Commission: 0.0325% of notional per side (in + out)
- Spread: $3.09 per round trip (live BTCUSD.sim sample 2026-09-16); the .dk import itself carries no spread
- Contract 1 BTC, min lot 0.01, step 0.01; costs computed in R = f(price/stop, nights), independent of tester lot size

## Flag state (journaled)
class crypto · late-Friday rule off · weekend-hold flag off · overnight-timing steps advisory · USD V/W event rows bind as for an index. The EA has no session gates, so nothing was forked in MQL5; the flags govern the advisor bundle (setup.md `class: crypto` + rules line) and the checklist.

## How to run the window
`bash pipeline/start_level.sh 14` (baseline already parked). After the window: `python3 pipeline/cost_journal.py <trader journal>.csv --json` → costed journal + totals for the coach.
