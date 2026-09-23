# Live runbook — Phase 3

Companion to `docs/phase3-live-system-requirements.md` (spec, §10/§11 rulings binding) and
`docs/live-queue-schema.md` (file contracts). This file is operational: how to build, test and run the EA's
LIVE mode, what was proven, and what is still open.

## Milestone 1 — EA LIVE mode + file queue (tester-proven, 2026-09-15)

### What M1 delivers
- One EA source (`mql5/experts/HybridForwardTest.mq5`); two builds. `HybridForwardTest-live.mq5` is a 3-line
  wrapper (`#define LIVE` + `#include`) that drops the popup-DLL imports (C1/E2). The tester build is
  byte-identical in behaviour to the pre-Phase-3 EA (gated on every slice by a journal diff).
- `InpLiveMode=true` (only on the -live build, only with `InpAutoApprove=NONE`): signals are published as
  JSON to `Common\Files\<InpLiveRoot>\signals\`, parked on the DELAY machinery, re-presented at every bar close
  (`implicit=1` delay rows) and expired as skip code 8 after `max_age_bars` (3) with no response. Tasks
  (`approve|skip|delay|close|close50|sl_be|ratchet_tp1`) are polled from `tasks\`, claimed, validated with every
  popup-era gate re-checked, executed and acked. Heartbeat, audit log, per-position files, restart state (G4),
  kill switch (fail-closed), per-symbol risk multiplier (C2), FTMO daily/max headroom with the §11-7
  cross-symbol aggregate, election auto-skip (G1), SL-through invalidation while parked (§11-3).

### Build
```
pipeline/mt5_build_ea.sh          # sync repo → MT5 tree, MetaEditor /compile both builds, 0 errors/0 warnings required
```
Refuses while `terminal64.exe` runs. **Never copy or compile anything under `MQL5\Experts` or `MQL5\Include`
while a tester run is in progress** — the -live wrapper `#include`s the main source, and a change aborts the
running test silently (no `OnDeinit`, a `.part` journal is left behind). Never `taskkill` MT5: a headless run
launched while the trader's terminal is open would disrupt the live level; the scripts refuse instead.

### Gates (all PASS on 2026-09-15)
| gate | command | result |
|---|---|---|
| A. tester parity, normal build | `pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --symbol EURUSD.dk --from 2024.01.01 --to 2024.06.30 --model 4` → `diff` vs `data/baselines/m1_pre_refactor.csv` | identical (md5 `070e2dd631d0`), `.actions.csv` identical |
| B. tester parity, -live binary | same with `--expert HybridForwardTest-live` | identical |
| C. live self-test | `pipeline/live_selftest.sh --selftest --poll 20 --symbol EURUSD.dk --from 2024.01.01 --to 2024.06.30` then `python3 pipeline/live_queue_check.py --root …/Common/Files/live_selftest --symbol EURUSD.dk --expect-selftest --expect-live-cols` | PASS (see plan progress log for the counts) |

Slice-0 probe (`mql5/experts/LiveProbe.mq5`, deleted after M1): in the headless tester `EventSetTimer(1)`
fires, `FileFindFirst/Next` enumerates `FILE_COMMON`, `FileMove(..., FILE_COMMON|FILE_REWRITE)` renames over an
existing file (refuses without the flag, err 5020), nested `FolderCreate` works.

### What the in-EA self-test proves (`InpLiveSelfTest`, tester-only)
| scenario | task(s) | expected |
|---|---|---|
| illegal batch at start | bad schema, unknown verb, unknown signal, unknown position, malformed JSON, stale `issued_at` | each `rejected` with its named reason (`stale_task` ⇒ `expired`), no execution |
| signal ordinal ≡ 1 (mod 4) | `approve market` | accepted → position; `duplicate` audit when the same `task_id` is re-issued (one ack only) |
| ordinal 5 | kill switch OFF then `approve` | `rejected trading_disabled`, no `executed`; switch ON → same signal accepted |
| ordinal 9 | `daily_loss_pct` 0.1 % then `approve` | `rejected ftmo_daily_headroom` + `ftmo_reject` audit; the EA closes the signal itself: journal `skipped,10,auto=1`, audit `EA_AUTO_REJECT code=10` (since 2026-09-21); rules restored |
| ordinal 13 | `risk_mult.json` 0.5 then `approve` | journal `risk_mult_applied=0.500`, risk-to-stop halved |
| ordinal ≡ 2 | `skip 3` | accepted, journal `skipped,3` |
| ordinal ≡ 3 | `delay` then `approve pending` | delay row `implicit=0`, pending order at the frozen entry |
| ordinal ≡ 0 | nothing | 3 × `implicit=1` delay rows then journal `skipped,8` |
| election within 14 d | – | `auto_skipped`, journal `skipped,2,auto=1` |
| every position | early `sl_be` / `ratchet_tp1`, then state-triggered `sl_be` (BE placeable) / `ratchet_tp1` (banked & past TP1), `close50`, `close` | every `gate` audit line agrees with its ack; `agg_check ok` on the first position |
| once, on a later position | `LiveSimulateRestart()` | `restart_check ok rows N->N`, one `adopt_orphan` |

The validator (`pipeline/live_queue_check.py --expect-selftest --expect-live-cols`) asserts all of the above plus schema keys,
done↔acks 1:1, unique `executed` per task, no `.tmp`/`.part` leftovers, monthly 51-column journals, delays torn-line tolerance.

### Tester-time caveats
- `OnTimer` fires once per **simulated** second, so a 6-month self-test is ~500k timer ticks; the poll/heartbeat
  intervals are raised (`--poll 20 --beat 600`) and the kill switch / risk_mult files are read at poll cadence.
- The in-EA self-test (`InpLiveSelfTest`, tester-only) is the driver; an external Python driver only makes sense
  in the visual tester or on the VPS.
- The tester has ~319 D1 bars of history for the `bars_d1` block (500 requested); graceful.

### Running LIVE-in-tester by hand (visual, external driver)
`Expert=HybridForwardTest-live`, `InpLiveMode=true`, `InpLiveSelfTest=false`, `InpAutoApprove=0`, root
`live`, then `python3 pipeline/live_queue_driver.py --root <Common>/Files/live --symbol <SYM> --watch` and
approve/skip/delay by hand; acks/audit tail in the driver.

### Open items carried to the VPS milestones
- `common.ini` on the current terminal has `[Experts] Enabled=0`: the live startup `.ini` must arm AutoTrading;
  the heartbeat's `terminal_trade_allowed`/`mql_trade_allowed` (§11-8) are what the watchdog alerts on.
- Forward economic calendar: the baked `events` feed ends at the data cut; live needs a forward feed or the
  election gate fails closed (`events_not_loaded` → every fresh signal auto-skips code 2). Provisioning item.
- FTMO wording checklist (config only): `day_ref` balance|equity|max and `daily_base` initial|day_start in
  `config\account.json`; set `initial_balance` explicitly at go-live.
- `tasks\done` retention (90 d) and `signals\` pruning: web-app milestone.
- MFE/rt trackers are not persisted across a restart (journal-only fields restart from 0 for that position).

## Milestone 2 — web app, monitor, advisor runner (2026-09-15)

Code: `live/` (stdlib + Pillow): `common.py` (config, queue readers, typed task writer with web audit, events, Telegram),
`charts.py` (H4/D1 renderer from the bars in the signal JSON, tester palette, overlay geometry), `webapp.py`,
`monitor.py`, `advisor_runner.py`. Local test config: `live/live_config.local.json` (git-ignored) pointing at the
`live_selftest` root; `python3 live/webapp.py --config … --port 8099`, `python3 live/monitor.py --config … --dry --once`,
`python3 live/advisor_runner.py --config … --once <key> [--reply "…"]`.

Verified locally 2026-09-15: all pages 200 on the 27 self-test signals; task refusal before any file write for a
non-open signal / unknown verb; reply queue; kill switch write + audit; one real consult (fresh session, full
scorecard in the CLAUDE.live.md format, model wrote its own log line) in 45 s and a `--resume` reply in 8 s.
Latency note: the role file's mandatory quick-reference read makes a fresh consult ~40-45 s (spec target ~30 s);
tuning candidate for M3 (pre-warmed session per symbol, or a trimmed reference).

Advisor session model: session id = uuid5(signal_key); consult = `claude -p … --session-id`, reply = `--resume`; the
runner scrubs inherited `CLAUDE_*` env vars (a nested child hangs otherwise). `verdicts.live.log` on the VPS is the
canonical copy (M3 nightly pull syncs it to the training tree for the coach).

## Shadow period — started 2026-09-16 (trader ruling: trading stays enabled after the live tests)

Demo `OANDA-Demo-1`, lineup US100/US500/USOIL/XAUUSD ×1.0, EURUSD/GBPUSD ×0.5, kill switch ON from 2026-09-16 ~04:00 UTC.
Live tests done the same night on the demo: test signal → approve (market) → fill → close 50 % → close; skip; pending
entry path. Journal rows with `strategy=TEST` (and their `signal_id`) are trader-issued tests: **exclude from grading**.
Live-only defects found and fixed during the tests (all committed): async fill binding, shared-read file opens with
six instances, task claim race, position-verb confirmation timing, MetaTrader5 package launching a stray terminal
(feed now isolated in `hybrid-feed`). Acceptance criteria: spec §8 (≥3 weeks; the coach grades the weeks).

## Context brief (coach task 2026-09-16)

Web **Context** page (own page, never on the signal page): "Generate brief" → the advisor runner generates it in a
thread (`live/brief_runner.py`): claude-opus-5, medium effort, WebSearch/WebFetch on public sources, subscription
transport (same token/launcher as the advisor), prompt via stdin. Fixed template: (a) this week's events from the
deployed feed with labels (built from the file, the model may only annotate), (b) CB posture USD/EUR/GBP, (c) five-session
drivers per lineup symbol, (d) sources. Hard no-directional rule + post-filter (`DIRECTIONAL` regex) shown as a FLAG on
the page and in `briefs.log`. Files: `<root>\advisor\briefs\<UTC>.md`, `briefs.log`, `status.json`. The advisor
bundle attaches the newest brief from the past **2 UTC days** (trader change to the coach's same-day rule) as
`market-brief.md`; the runner writes `brief:yes|no` at the end of every verdict-log line. No Telegram push; the
daily summary notes whether a brief is available. First generation on the box: 64 s, 9 sources, 746 words, no flags.

## Advisor material on the box — additive copy, `--prune` for retirements (coach 2026-09-17)

`provisioning/deploy_live.sh` copies the live role file, the quick reference and `library/` (recursively, including
`full-texts/`). The copy ADDS and OVERWRITES but never deletes, so a note retired locally would linger on the box and keep
being read by the advisor. Two ways to handle it:
- `provisioning/deploy_live.sh --prune` — full deploy, then delete every file under `advisor/library` that no longer
  exists locally (compared by relative path through a manifest; role file and quick reference are single files and are
  always overwritten). Verified 2026-09-18 by planting two stale files, one nested: both removed, the 30 real files kept.
- Or delete the stale file by hand on the box under `C:\ProgramData\hybrid\advisor\live\advisor\library\`.

## Swing table on the setup card (coach 2026-09-17, trader-reported defect)

The advisor was estimating swing highs/lows off the rendered chart and the trader kept correcting it. Both setup cards
now carry a **Recent swing structure** block: the five most recent swing highs and five swing lows as price + **bars
ago** (relative H4 counts, never times — blind-safe in the tester bundle by construction). The role files make the
table authoritative over the advisor's pixel read; the chart stays authoritative for move character, zone freshness,
trigger-candle shape and structural integrity.

The set is the EA's **persistent** swing markers (`DrawSwingMarkers`: 5-bar fractal, 2 left/2 right, 14-day rolling H4
window) — not the per-signal `DC_FillSwings` arrays, which only SMC and DeepFib fill and which the chart no longer
labels. That is the only set that exists for all four detectors and it is the one the picture marks, so the table and
the picture cannot disagree.

- **Live bundle** (`live/advisor_runner.py: swing_block`): computed by `live/overlays.py: swing_table` from
  `charts.signal_bars(sig, "h4")` — the same call and the same bars the H4 PNG is drawn from. Note the box runs
  `InpSignalBars=0`, so those bars come from the terminal feed (`mt5feed`), not the signal JSON. No bars → the block
  says "unavailable" rather than disappearing.
- **Tester bundle** (`pipeline/inbox_bridge.py: swing_block`, used by `os_shot_daemon.py`): reads the EA sidecar
  `Common\Files\journal\swings\<sym>_<stamp>_<id>.csv` (`kind,price,bars_ago`), written by `WriteSwingSidecar` beside
  `WriteD1Series` at signal-fire. Sidecar absent (older EA build) → no block, never an estimate.
- `overlays.swings()` and `hybrid_chart.js: swings()` were made faithful to `DrawSwingMarkers`: the scan includes the
  forming bar as a right-hand neighbour (the EA copies from index 0, not 1) and runs newest-centre-first. On 500 real
  H4 bars the only difference from the old version is one window-edge bar at 90 ago, outside the EA's centre range.
- **No swept column.** The EA tracks no per-swing pool status (SMC's `m_pool_level`/`m_pool_type` is the single pool of
  the current setup), so the column is omitted per the coach's "don't invent it". A mechanical "exceeded since printing"
  flag is computable from the same bars if the coach rules on the definition.
- **EA change is STAGED, not merged:** branch `swing-sidecar` (`CollectDisplaySwings` + `WriteSwingSidecar`), written
  while window 15 was running, so it was never compiled or copied into the build path. Merge sequence once
  `terminal64` is free: `git merge swing-sidecar` → `pipeline/gate_chain.sh` (parity both builds + live self-test) →
  commit. Until then the tester card simply carries no table; the live card has one today.
  **Then check for a stale daemon before the next window:** `pgrep -af os_shot_daemon.py` — a `--watch` process that
  predates the merge is running the old `inbox_bridge` from memory, so the new EA would write sidecars nothing reads
  and the tester card would lose its table silently, with no error anywhere. Kill it; `start_level.sh` starts its own.

## Demo universe expansion, dual advisor, open exposure (coach 2026-09-21)

**Universe.** `pipeline/universe_cost_filter.py` ran the spread-drag method over all 49 symbols in `ftmo_symbols.txt`
(`data/study/universe_cost_filter.md` + `.csv`): 47 attach (drag ≤ 0.10R/trade), USDCNH excluded by ruling, EURCZK by cost
(0.113R). The spread was sampled 16:07 UTC (London/NY overlap - the tightest hour); the exotics nearest the cap (EURPLN 0.078,
USDCZK 0.060, EURHUF 0.056, USDPLN 0.054) are the ones to re-sample at a thin-hour bar open. Multipliers: US100 US500 USOIL
XAUUSD ×1.0, everything else ×0.5. Attached 2026-09-21 16:14 UTC in two measured stages (6 → 18 → 47); capacity numbers in
`docs/vps-provisioning.md`. The shadow clock did not restart; `config\lineup_history.json` records both configurations and
the dashboard shows the day counts.

**BTCUSD prerequisites.** §10.1 on the live path (EA, config-scoped by `live.json weekend_flat_symbols`): from the Fri 20:00
broker bar (the last H4 bar before the 23:59 close) until the week reopens - positions closed (`WEEKEND_FLAT`), pendings
cancelled, no new entries, approve refused `weekend_flat`. Verified on the box by the heartbeat (`weekend_flat:true,
weekend_cutoff:"Fri 20:00 broker"`); the first real trigger is Friday. ATR floor off. Live card: class + symbol-rules line
from `config/symbol_rules.json` (shipped 2026-09-18).

**Dual advisor.** `advisor.models` in the live config: `sonnet-low` (sonnet, low, 150 s, 3 parallel) and `opus-high`
(`claude-opus-5-5` since 2026-09-23 - trader ruling; needs Claude Code CLI >= 2.1.281 on the box, 2.1.273 rejects the id
with `[claude-code:unrecognized_model]`; its effort default is `medium`, we pass `--effort high` explicitly), high, 600 s,
2 parallel). One bundle per presentation, two independent sessions launched in parallel; per-model records,
notes files and replies; runner-owned verdict log with a model field. Rate limits are never auto-retried - the panel says so,
Telegram alerts, and "Run <model> again" re-runs one model. Dashboard: consults today per model. To drop Opus (subscription
load), delete its entry from `advisor.models` and redeploy - nothing else depends on the pair.

**Open exposure** (`live/exposure.py`, live card only) and **live eligibility** (`live/eligibility.py`, dashboard; the monitor
prices each closed position once from the broker's deals into `web\costed_r.json`).

## Rulings on the expansion flags (coach 2026-09-21)

- **Code 8 on TAKE: journaled, not charged** (§11.9(b) amended). Telegram and the REVISIT ping say so; the monthly export
  (dashboard → "Monthly export": last month / this month so far) lists every expired TAKE with its blind outcome
  (mechanical doctrine-v2 walk on M15 from the presentation bar, costs off), the TAKE-expiry rate and its hour split.
- **FTMO headroom refusal = EA auto-reject, code 10, auto=1** (see the schema doc). The signal page shows the room first:
  "room for N more at 0.5 % · M at 1.0 %" from the EA's own check, with a warning when this signal would not fit.
- **Opus fallback** flips itself (monitor → `advisor/opus_policy.json`, Telegram, dashboard line). Reset: set `"step": "none"`
  in that file. Step A/B skips are recorded per signal, so the coach's paired subset is exact.
- Swing table: live since 2026-09-17; tester card from the 2026-09-21 build (EA sidecar = Python mirror on 16/16 signals).
  No swept column - the EA tracks no per-swing pool status.

## D1 warm-up / blank regime (coach 2026-09-23)

A newly attached symbol has no D1 history until something asks for it. `ComputeRegime` (EMA200 + ADX on D1, 11 buffer
values ≈ 211 bars) and TrendCont's `TrendDir` both fail SILENTLY without it: blank regime tag, `TC[trend=0]` on every call,
and every SweepMSS/DeepFib signal downgraded from TAKE to DISCRETION. Seen on the 41 symbols attached 2026-09-21 (NZDJPY #1
carried a blank tag; its EA logged `Regime: D1 EMA200 not ready`). Fixed: `LiveWarmD1()` forces `CopyRates(PERIOD_D1, 400)`
at init and at heartbeat cadence until `Bars(PERIOD_D1) >= 211`, audited `d1_warmup` / `d1_ready`; the heartbeat carries
`d1_bars`, `regime`, `regime_ready`; the dashboard's Instances block shows the tag per symbol and opens itself when any is
blank; the monitor sends one aggregated Telegram line. After the 2026-09-23 deploy: 47/47 `d1_ready` (FX 350 bars,
indices/metals/BTC 403-523), 0 blank, tags 25 TREND_UP / 19 TREND_DOWN / 3 CHOP.

## Spread gate + spread in R on every signal (trader 2026-09-23)

The spread is paid the instant a trade opens, so every signal now states it in R (`sizing.spread_r` = spread / stop):
the signal page shows "spread now 0.031R" beside the lots (amber > 0.05R, red > 0.10R) and the advisor card carries an
"Entry cost (spread) right now" line. `live.json max_spread_r` (0.15, 0 = off) is a hard gate: a signal whose spread
costs more than that is never shown - journaled as a `rejected` row with the reason, audit `spread_gate`; and an approve
that arrives after the spread has blown out is refused `spread_too_wide` instead of the old, misleading
`stop_too_tight` (which now also prints the distance, the minimum and the spread in R).

Why: a EURNOK approve was refused `stop_too_tight` at 21:16 UTC with a 2.87-ATR stop - the real cause was the rollover
spread at 0.0418 (24x its midday 0.0017), and `MinStopDist` requires the stop to be >= 2x spread. Measured 21:18 UTC,
spread cost per trade: USDZAR 1.20R, EURPLN 1.16R, EURNOK 1.04R, USDNOK 0.93R, EURSEK 0.85R, USDCZK 0.84R, EURHUF 0.83R,
USDPLN 0.72R, USDHUF 0.69R, USDMXN 0.66R, USDSEK 0.58R - against EURUSD 0.079R, US100 0.008R, XAUUSD 0.006R. The cost
filter that attached those eleven sampled 16:07 UTC (the tightest hour), so they passed the coach's 0.10R cap on a
best-case reading. OPEN for the coach: detach the eleven, and re-run the filter sampling at each H4 bar open over 24 h.

## Standing items (checked when the named condition occurs)

- **FTMO server session check.** Re-run `pipeline/broker_sessions.py` against the FTMO account the day it exists and
  update `data/study/broker_sessions.md`. If BTCUSD quotes weekends there, §10.1's weekend-flat rule comes off rather
  than persisting by inertia (coach, 2026-09-17). The shadow venue (OANDA demo) is Mon-Fri; FTMO says crypto hours vary
  by platform.
