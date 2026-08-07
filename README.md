# FTMO Hybrid Trading System

Human-in-the-loop trading system for a $25k FTMO Swing account traded via MT5.
Algorithms detect setups on 1D/4H charts across ~49 FTMO symbols; **every trade
is manually approved** before execution.

**Technical documentation lives in the wiki: [docs/README.md](docs/README.md)**
— architecture, API reference (exact MQL5/Python signatures and contracts),
and an operational tools guide for every script. This file stays a short
project overview.

## Strategies (each targeting ~40–50% win rate, 1% risk per trade)

1. **Liquidity Sweep & Market Structure Shift** (SMC model)
2. **Deep Fibonacci Retracement In-Trend** (Discount Matrix)
3. **20 EMA Mean Reversion**

## Phases

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Dev environment, agent team, QDM CLI working | Done |
| 1 | Dukascopy tick data for all 49 symbols, imported to MT5 custom symbols | Pipeline built and running (see [docs/README.md](docs/README.md) for the live count) |
| 2 | Interactive forward test: MT5 visual tester pauses on each alert for a manual Accept/Skip via an editable dialog (retune Entry/SL/TP independently, live R:R + floor, chart lines update live) | Built (3 detectors + arbitration + two-target scale-out) |
| 3 | Production: cloud EA → VPS → Telegram approval loop | Planned, not built |

Details, current counts, and every contract: [docs/README.md](docs/README.md).

## Layout

```
pipeline/    Python: QDM CLI orchestration, validation
mql5/        MQL5: import scripts, forward-test EA (experts/, scripts/)
config/      symbols.yaml (FTMO → Dukascopy mapping), settings
docs/        decisions log, QDM CLI reference
data/        qdm_csv/ (CLI exports), mt5_ready/ (import-ready) — git-ignored
logs/        pipeline logs — git-ignored
```

## Key paths

- QDM Linux CLI: `/home/jack/QDM/qdmcli`
- FTMO symbol list: `/mnt/c/Users/jacks/OneDrive/Trading/ftmo_symbols.txt`
- MT5: installed on the Windows host (WSL sees it via /mnt/c)
