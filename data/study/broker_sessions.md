# Broker session reality check (coach item 2, 2026-09-17)

**Question:** does the broker quote BTCUSD at weekends? The research data (`BTCUSD.dk`, Dukascopy) does — 76 weekend
signals and 197 weekend-spanning trades in the 2018–2026 baseline. The broker side was unestablished.

**Method (repeatable for any symbol):** on the shadow box, pull every M1 bar of the last 28 days from the terminal and
count bars per weekday in the broker's clock (server = UTC+3). A symbol that quotes at weekends has Saturday/Sunday bars.
Script: `pipeline/broker_sessions.py`. Run 2026-09-17 23:5x UTC, account 1600190425 on OANDA-Demo-1.

| symbol | Mon | Tue | Wed | Thu | Fri | Sat | Sun | weekend bars |
|---|---|---|---|---|---|---|---|---|
| BTCUSD.sim | 5736 | 5736 | 5736 | 5736 | 5736 | 0 | 0 | **0** |
| EURUSD.sim | 5722 | 5726 | 5733 | 5735 | 5736 | 0 | 0 | 0 |
| GBPUSD.sim | 5733 | 5735 | 5732 | 5736 | 5736 | 0 | 0 | 0 |
| US100.sim | 5272 | 5512 | 5512 | 5512 | 5512 | 0 | 0 | 0 |
| US500.sim | 5238 | 5470 | 5458 | 5489 | 5446 | 0 | 0 | 0 |
| USOIL.sim | 5358 | 5484 | 5500 | 5469 | 5482 | 0 | 0 | 0 |
| XAUUSD.sim | 5346 | 5496 | 5496 | 5496 | 5496 | 0 | 0 | 0 |

BTCUSD.sim's own specification (read 2026-09-16): sessions Mon–Fri 00:05–23:59, no Saturday/Sunday row; swaps in
percent p.a. (long −30.31 / short −19.69), `swap_rollover3days = 7` (no triple-swap day), contract 1 BTC, min lot 0.01.

**FTMO's published position** (checked 2026-09-17): crypto is advertised as tradable 24/7, *but* "weekend trading hours
on cryptocurrencies may vary on each platform due to weekly scheduled maintenance times… check the trading hours of the
symbol in the platform you are using", and FTMO Traders must close positions before the weekend market close unless on a
Swing account. So "24/7" is a platform-dependent claim, not a guarantee for this feed.

**Conclusion → the coach's branch (b).** On the platform the shadow account actually trades, BTCUSD is Mon–Fri exactly
like the FX and index symbols: 0 weekend bars in 28 days. §10.1's weekend-flat rule stands, and any future BTC research
window must be filtered to the broker's real sessions (the 2019 window used for window 14 contained zero weekend entries
and zero weekend exits, so its grade is unaffected; one trade spanned a weekend).

**Caveat worth stating to the coach:** this is the OANDA demo (`OANDA-Demo-1`), which is the shadow venue, not an FTMO
server. FTMO's own platform may quote crypto at weekends. Re-run this check on the FTMO account the day it exists,
before BTC takes its 0.5× seat — the branch could flip to (a) there.
