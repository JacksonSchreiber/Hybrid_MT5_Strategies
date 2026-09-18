# §10.2 — stop-width measurement (ATR units), per symbol and detector

**Run:** 2026-09-18 · `pipeline/stop_width_atr.py`, read-only attach to the running local terminal (never launches one;
aborts if the process count grows). Stop = the DETECTOR's |orig_entry − orig_sl| (before any trader edit), ATR = Wilder
ATR(14) on .dk H4 as known at the open of the signal bar — the same series the EA sizes from. Population: every approved
row in the graded journals and parked AA baselines.

| symbol | n | median stop (ATR) | p25 | p75 | per-detector medians |
|---|---|---|---|---|---|
| BTCUSD.dk **(BTC)** | 484 | **1.242** | 0.893 | 1.929 | DeepFib 0.86 (n=33) · EMArevQ 1.23 (n=30) · SweepMSS 1.39 (n=88) · TrendCont 1.32 (n=333) |
| EURUSD.dk | 1037 | **1.279** | 0.926 | 1.871 | DeepFib 0.66 (n=57) · EMArev 1.05 (n=207) · EMArevQ 1.45 (n=24) · ShockCont 1.49 (n=28) · SweepMSS 1.47 (n=234) · TrendCont 1.44 (n=487) |
| GBPUSD.dk | 870 | **1.373** | 0.974 | 1.960 | DeepFib 0.77 (n=56) · EMArev 1.08 (n=93) · EMArevQ 1.31 (n=50) · ShockCont 1.20 (n=27) · SweepMSS 1.76 (n=240) · TrendCont 1.50 (n=404) |
| US100.dk | 722 | **1.521** | 0.986 | 2.238 | DeepFib 0.68 (n=30) · EMArev 0.94 (n=119) · EMArevQ 1.77 (n=37) · SweepMSS 1.62 (n=92) · TrendCont 1.71 (n=444) |
| US500.dk | 949 | **1.492** | 0.983 | 2.102 | DeepFib 0.82 (n=37) · EMArev 1.14 (n=237) · EMArevQ 1.41 (n=34) · SweepMSS 1.65 (n=192) · TrendCont 1.72 (n=449) |
| USDJPY.dk *(retired from the lineup)* | 293 | **1.294** | 0.942 | 1.872 | DeepFib 0.86 (n=10) · EMArev 1.14 (n=77) · EMArevQ 1.11 (n=6) · SweepMSS 1.55 (n=62) · TrendCont 1.46 (n=138) |
| USOIL.dk | 786 | **1.397** | 0.974 | 2.051 | DeepFib 0.75 (n=33) · EMArev 1.03 (n=111) · EMArevQ 1.51 (n=45) · SweepMSS 1.51 (n=134) · TrendCont 1.56 (n=463) |
| XAUUSD.dk | 426 | **1.201** | 0.775 | 1.717 | DeepFib 0.69 (n=39) · EMArev 0.96 (n=128) · EMArevQ 1.17 (n=9) · SweepMSS 1.65 (n=113) · TrendCont 1.41 (n=137) |

## The §10.2 comparison

- **BTCUSD median stop = 1.242 ATR.**
- **Lineup median** (US100, US500, USOIL, XAUUSD, EURUSD, GBPUSD): **1.385 ATR** as the median of
  the six symbol medians; **1.397 ATR** pooled n-weighted. The two agree to within 0.012 ATR.
- **BTC sits BELOW the lineup median**, so per §10.2 the lineup-median ATR floor applies to BTCUSD:
  **floor = 1.385 ATR** (one value, no search).

Per detector, BTC's tight stops are concentrated in DeepFib
(0.86 ATR median); its widest are
SweepMSS
(1.39 ATR).

## Open mechanism question for the coach (blocks window 15's baseline)

"Widen-only" has two possible implementations in this codebase, and they are not equivalent:

1. **Widen (literal reading).** At signal creation, if |entry − SL| < floor × ATR, push the SL out to floor × ATR and keep
   entry/TP prices. The trade is still taken, sized smaller (bigger stop = fewer lots), but its R:R falls, so some signals
   then fail `StratMinRR` and drop out anyway. Needs a new EA input (default 0 = off, so every existing baseline stays
   byte-identical) plus the full gate chain, and the same input value set for the baseline and the trader's run.
2. **Filter (what the EA does today).** `InpMinStopATR` already rejects any signal whose stop is below N × ATR — a
   rejection, not a widening. Setting it to the floor for the BTC window needs no code change at all, but it removes
   roughly the bottom half of BTC's signals rather than trading them with a wider stop.

I will build (1) on a word from the coach, since it matches "widen-only"; (2) is available immediately if the intent was
a floor as an admission test. Either way the value is one number, applied identically to the baseline and the window.
