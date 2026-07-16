# CLAUDE.md — FTMO Hybrid Trading System

## Project rules

- **The user (Jackson) is the gatekeeper.** Never advance to the next phase
  (0→1→2→3) without their explicit approval. Report progress regularly in
  plain, non-technical terms — they oversee like an investor, not an engineer.
- **1% risk rule** on a $25k FTMO Swing account is a hard constraint in all
  sizing code.
- **Tick-level data only** for backtests — never substitute 1m data.
- All user-approved decisions get logged in `docs/decisions.md`.

## Agent team (model assignments)

| Role | Model |
|---|---|
| Project Manager / Orchestrator (main session) | Fable 5 |
| MQL5 / EA Developer | Opus 4.8 (`model: "opus"`) |
| Quant Strategy Designer | Opus 4.8 (`model: "opus"`) |
| Data Pipeline Engineer | Sonnet 5 (`model: "sonnet"`) |
| QA / Verification | Sonnet 5 (`model: "sonnet"`) |
| VPS / Telegram Infra (Phase 3) | Sonnet 5 (`model: "sonnet"`) |
| Docs / Changelog | Haiku 4.5 (`model: "haiku"`) |

## Environment facts

- This repo lives on WSL-native ext4 (`/home/jack/hybrid_project`). Do NOT put
  bulk data under `/mnt/c` or `/home/jack/trading` (the latter is a symlink to
  OneDrive — sync churn).
- QDM Linux CLI: `/home/jack/QDM/qdmcli` (license REDACTED). Flag syntax is
  build-specific — consult `docs/qdm-cli.md`, don't guess.
- MT5 data folder (Windows):
  `C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB`
  (WSL: `/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB`)
- GitHub remote: `JacksonSchreiber/Hybrid_MT5_Strategies` (credentials in
  `~/.git-credentials`, outside the repo — never commit tokens).
- MT5 is on the Windows host. Custom-symbol suffixes must be ≤4 chars
  (e.g. `EURUSD.dk`). Dukascopy timestamps are UTC — policy: keep UTC.
- Tester interactivity uses `user32.dll MessageBoxW` (blocks the visual
  tester); MQL5-native MessageBox does NOT work in the tester. WebRequest does
  not work in the tester and DLLs don't run on cloud hosts — tester (Ph. 2)
  and production (Ph. 3) are separate mechanisms by design.

## Conventions

- Python in `pipeline/` (3.12, stdlib + httpx/pyarrow as needed); MQL5 in
  `mql5/`. Data and logs are git-ignored.
- Resumable downloads: per-symbol state; never hammer Dukascopy/QDM servers.
