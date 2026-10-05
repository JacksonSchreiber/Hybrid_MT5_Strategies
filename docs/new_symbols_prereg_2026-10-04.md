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
