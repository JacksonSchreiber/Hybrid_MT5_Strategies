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
| ordinal 9 | `daily_loss_pct` 0.1 % then `approve` | `rejected ftmo_daily_headroom` + `ftmo_reject` audit; rules restored → `skip 6` accepted |
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
