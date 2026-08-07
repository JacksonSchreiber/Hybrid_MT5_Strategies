# FTMO Hybrid Trading System — Technical Wiki

**Start here.** This is the single entry point for the technical documentation.
If you are a fresh engineer or a new Claude session picking this project up
cold, read this page, then follow the links below in whatever order matches
your task.

> Status snapshot dated **2026-07-17**. Pipeline counters below are a
> point-in-time read of the state files at the time this page was written —
> re-check `pipeline/fleet_state.txt` / `pipeline/import_state.txt` (or run
> `pipeline/progress.sh`) for the live count.

## One-paragraph overview

This is a human-in-the-loop trading system for a $25,000 FTMO Swing MT5
account. Three rule-based detectors (Liquidity Sweep + Market Structure
Shift, Deep Fibonacci Retracement, 20 EMA Mean Reversion) watch H4/D1 charts
across 49 FTMO symbols for setups; nothing trades automatically — every
signal is presented to the account owner (Jackson) as a chart overlay plus a
colour-coded, editable Accept/Skip dialog (retune Entry/SL/TP independently,
R:R recomputed live with a strategy floor; chart lines update live), sized at
1% account risk. Phase 1 (get five-plus
years of real Dukascopy tick history into MT5 as `.dk` custom symbols) and
Phase 2 (the interactive MT5 Strategy Tester harness that runs the detectors
and pops the approval dialog) are built. Phase 3 (an unattended cloud EA on a
VPS that pushes signals to Telegram for remote approval) is **planned, not
built**.

## Navigation

| Doc | What it covers |
|---|---|
| [architecture.md](architecture.md) | End-to-end technical architecture: data pipeline, Phase-2 tester harness, planned Phase-3, the WSL disk model and why the pipeline is shaped the way it is. **Read this second.** |
| [api-reference.md](api-reference.md) | Exact interfaces/signatures/contracts read from source: `ISignalDetector`, `SignalCandidate` (every field), `TickImport.mqh` public functions, `TradeDialog.dll` export, the AutoImport `jobs.txt`/`import_status.txt` contract, `HybridForwardTest` EA inputs + journal CSV schema. |
| [tools-guide.md](tools-guide.md) | Operational "how to run it" guide for every tool: QDM CLI, `fleet_download.py`, the import chain, `progress.sh`, the live stall-detector, the Watch Officer brief, `mt5_verify.sh`, manual import scripts, headless MQL5 compilation, and a **resume-the-pipeline-in-a-new-session runbook**. |
| [qdm-cli.md](qdm-cli.md) | Deep reference for the QuantDataManager CLI (flags verified against our build, not guessed). Summarized in tools-guide.md; read this for the full flag set. |
| [pilot-eurusd.md](pilot-eurusd.md) | The original EURUSD dry run that discovered QDM CLI's real behavior (e.g. `datefrom`/`dateto` ignored by `action=update`, honored by `exportToMT5`). Historical record — findings are folded into qdm-cli.md and tools-guide.md. |
| [mt5-import.md](mt5-import.md) | The manual tick-import pipeline design (`CustomTicksAdd` vs `CustomTicksReplace`, M1 bar building, CSV parsing) and the original EURUSD smoke-test steps. |
| [auto-import.md](auto-import.md) | The unattended `AutoImport.mq5` EA + `mt5_import.sh` driver that replaced the manual drag-and-drop import for the 49-symbol fan-out. |
| [tester-harness.md](tester-harness.md) | The Phase-2 interactive visual-tester workflow: overlays, the coloured editable dialog (poll-driven, live SL/TP), the Pause trick, running a demo. Field-level journal/API detail now lives in api-reference.md. |
| [strategies/README.md](strategies/README.md) | Shared strategy vocabulary (fractals, ATR, level pools, sizing, drawdown caps) and the at-a-glance comparison of the three strategies. |
| [strategies/liquidity-sweep-mss.md](strategies/liquidity-sweep-mss.md), [deep-fib-retracement.md](strategies/deep-fib-retracement.md), [ema20-mean-reversion.md](strategies/ema20-mean-reversion.md) | Design specs (the "why") for each strategy, written for both the account owner and the implementer. |
| [strategies/impl-README.md](strategies/impl-README.md) + `impl-*.md` | MQL5-level build sheets the detectors were actually coded from — read these plus the detector source to change strategy logic. |
| [strategies/detectors-implementation.md](strategies/detectors-implementation.md) | What was actually built vs. the blueprints: deviations, verification results, known caveats. |
| [decisions.md](decisions.md) | Chronological log of every user-approved decision. |

## Current system status

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Dev environment, agent team, QDM CLI working | **Done** |
| 1 | Tick history for all 49 FTMO symbols, imported to MT5 `.dk` custom symbols | **Built and running.** `fleet_download.py` + `rolling_import.sh` are live background workers draining the 49-symbol fleet in parallel (download → export → import → disk reclaim). As of 2026-07-17: **35/49 downloaded, 35/49 imported** (see `pipeline/fleet_state.txt`, `pipeline/import_state.txt` — these counts move; re-run `pipeline/progress.sh` for current numbers). History depth: symbols finished before 2026-07-17 were exported 2020+; from 2026-07-17 on the fleet exports **full available history** (`DATEFROM=2003.01.01` in `pipeline/fleet_download.py`, `config/settings.yaml`) — older docs that say "5 years / 2020+" describe the original, now-superseded policy. |
| 2 | Interactive forward test: MT5 visual tester pauses on each alert for a manual Accept/Skip via an editable dialog | **Built.** `mql5/experts/HybridForwardTest.mq5` runs all three real detectors with priority arbitration and two-target scale-out; verified headless via `pipeline/mt5_verify.sh` on `EURUSD.dk`. The approval dialog is editable (retune Entry/SL/TP independently with live R:R + a strategy floor and chart lines live; editing entry away from market places a pending order), with skip-reason capture and a decision-time screenshot; `pipeline/review_session.py` grades a session vs the take-everything baseline. Pending-order fill/scale-out/expiry are verified headlessly; needs a **live interactive visual-tester run** to confirm keystrokes land in the edit boxes (chart-repaint-while-held is already proven) — see `docs/tester-harness.md`. |
| 3 | Production: cloud EA → VPS → Telegram approval loop | **Planned, not built.** Nothing in this repo implements Phase 3 — do not treat any current code as Phase-3-ready. |

## Key environment facts

| Fact | Value |
|---|---|
| Repo (WSL-native ext4 — **canonical**, do all work here) | `/home/jack/hybrid_project` |
| Repo mirror paths seen from other shells (do **not** treat as canonical; may be OneDrive-synced or symlinked) | `/mnt/c/Users/jacks/OneDrive/Trading/hybrid_project`, `/home/jack/trading/hybrid_project` |
| QDM Linux CLI | `/home/jack/QDM/qdmcli` (license REDACTED, build 125.2692) — run from `/home/jack/QDM/` |
| MT5 data folder (Windows, OANDA terminal) | `C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB` |
| MT5 data folder (WSL path) | `/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB` |
| Custom-symbol suffix | `.dk` (must stay ≤4 chars — MT5 tester requirement) |
| Timestamps | UTC throughout, no conversion (project policy) |
| GitHub remote | `JacksonSchreiber/Hybrid_MT5_Strategies` (credentials in `~/.git-credentials`, outside the repo) |
| WSL disk model | The WSL ext4 filesystem lives inside a `.vhdx` on the Windows `C:` drive that **grows but never shrinks** — deleting files inside WSL does not free real `C:` space. See [architecture.md](architecture.md) for how the pipeline works around this. |
| Account modelled | FTMO Swing, $25,000, 1% risk/trade ($250), max daily loss 5%, max total loss 10% |

## Conventions

- Python in `pipeline/` (3.12, stdlib + `httpx`/`pyarrow`/`yaml` as needed); MQL5 in `mql5/`.
- `data/` and `logs/` are git-ignored (bulk/derived data, never committed).
- Resumable downloads: per-symbol state files; never hammer Dukascopy/QDM servers (one qdmcli invocation at a time).
- Docs-only changes: this wiki lives under `docs/`; `CLAUDE.md` and the root `README.md` stay short and point here.
