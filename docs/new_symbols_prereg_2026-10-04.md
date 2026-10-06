# New symbols (the 26 fleet2 downloads): reservation + pre-registration (coach 2026-10-04 item 8)

This was written BEFORE any MT5 AA run, signal or result on these symbols. Status: DRAFT for the coach's approval of the bars.
No result is computed until it is approved, and then only by the trader's command, ONE look.

## Reserved as blind Test material (fixed seed "20261004-reserve-<group>"; never shown in any study, visualizer or info window)
About a third per group:

| Group | Reserved |
|---|---|
| Yen | AUDJPY |
| Swiss franc | GBPCHF, NZDCHF |
| Euro / Sterling crosses | EURAUD, EURCAD |
| EM carry | USDSGD |
| Nordic & Central Europe | EURHUF, USDCZK, USDSEK |

These 9 go on the research lock's locked list before any of the 26 reach the Data visualizers. Their AA journals may be produced, but
they are not opened.

## Study set (17): the live stack against raw, by component
CADJPY, CHFJPY, NZDJPY, AUDCHF, CADCHF, EURCHF, EURNZD, GBPAUD, GBPCAD, GBPNZD, USDZAR, EURNOK, EURPLN, EURSEK, USDHUF, USDNOK, USDPLN.

- **Population:**
  - MT5 AA runs (all four detectors, EA input defaults, real ticks), 2012-01 -> 2026-06 where the data allows. History is
    extended to 2012 from the fleet2 downloads for the symbols MT5 only holds from 2020.
  - Replayed by the SCALED study engine, at model cost (a lower bound), with +1 pip round trip reported alongside.
  - The research lock still applies: 2025-26 stays locked unless the coach rules otherwise for this look.
- **Variants, each vs the RAW signal** (100% at the signal, signal stop, TP2):
  - staged entry alone (25% now, 75% at the bar-6 close);
  - bank alone (detector share at +1R/TP1, stop to entry);
  - pyramid alone (+50% at +1.5R, armed from the entry);
  - short-side stop alone (shorts' stop to +0.25R at +1.5R);
  - the full stack;
  - leave-one-out (the full stack minus each component).
- **Primary measure:** the increment over raw in R/trade, pooled over the 17 symbols, with the paired t and worst drawdown.
- **Proposed bars (for the coach to confirm or change before the look):**
  - a component is SUPPORTED if its alone-increment is > 0 at t >= 1.5 pooled AND positive in at least 3 of the 5 groups;
  - it is REFUTED if <= 0;
  - otherwise it is INCONCLUSIVE.
  - The full stack is SUPPORTED if > 0 at t >= 1.5.
- **Reported, not gated:** per group, per symbol, long/short, 2012-19 vs 2020-24.
- One look, no parameter changes afterwards. Any symbol excluded for data problems is named before the look and reported only as
  a separate addendum.

## Data note (2026-10-05, written after the MT5 AA runs and BEFORE any look; facts only, nothing excluded)
- **The runs:** all 25 AA journals were produced, 2012-01 to 2026-06 where the data allows, in data/study/fleet2_aa/. The reserved
  symbols' journals remain unopened.
- **USDCZK** was not run from 2012. Dukascopy's USDCZK ticks start 2016-01-03, and the stage's export check requires the first year to
  equal the start year. It is being re-run from 2016-01-01.
- **The stage's conversion-price check found gaps in MT5's conversion history.** The tester logged "no prices for symbol" for the
  conversion pair:

  | Pair missing | Crosses affected | Years without prices |
  |---|---|---|
  | EURUSD.dk | EURCHF, EURCAD, EURNZD, EURAUD, EURNOK, EURSEK, EURHUF, EURPLN | simulated 2012 to mid-2016 |
  | GBPUSD.dk | GBPCHF, GBPCAD, GBPNZD, GBPAUD | about 2012 only (2,100-2,900 lines) |
  | USDJPY.dk | CADJPY, CHFJPY | before 2020; their journals contain 2020+ rows only, like the 2020+ AUDJPY/NZDJPY copies |

- **What was checked so far (counts only, no outcomes):** signal counts per year in the affected EUR and GBP journals look normal in
  2012-16, so the signals are generated. The likely effect is on account-currency values such as lot sizing and margin.
  - For example, EURNOK's median lots are 0.6 in 2012-19 against 0.25 in 2020-26. The trend differs from USDNOK's.
  - Whether the R values used by the study engine depend on those values must be confirmed before the look.
- **Options for the coach and trader to rule on before the look:**
  - accept, since the R replay is price-based;
  - start the affected EUR crosses at 2016-07 and the GBP crosses at 2013;
  - or extend EURUSD.dk, GBPUSD.dk and USDJPY.dk in MT5 back to 2012 (a re-import of those three) and re-run the affected symbols.
