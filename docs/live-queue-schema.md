# Live file-queue schema (Phase 3, Milestone 1) — `schema_version: 1`

Contract between the EA (`HybridForwardTest-live.ex5`, `InpLiveMode=true`) and everything else (web app,
watchdog, advisor runner, validator). Source of truth for §4 of `docs/phase3-live-system-requirements.md`
with the §10/§11 rulings applied. **Both sides reject any other `schema_version`.** Files are JSON, ASCII-only
(non-ASCII is `\uXXXX`-escaped), written atomically (write `<name>.tmp`, then rename); readers ignore `*.tmp`.
The EA never parses anything but the flat `task.json` shape below (S6) and re-validates every task (S7).

## Directory layout — `Common\Files\<root>\` (`root` = `InpLiveRoot`, default `live`)

| Path | Writer | Purpose |
|---|---|---|
| `signals/<SYMBOL>-<signal_id>.json` | EA | one file per published signal; rewritten on every state change (§11-1 per-symbol key) |
| `tasks/<task_id>.json` | **web app only** | one verb per file; the EA CLAIMS it by moving it to `tasks/done/` before acting |
| `tasks/done/<task_id>.json` | EA | consumed tasks (retain 90 d, then archive) |
| `acks/<task_id>.json` | EA | exactly one ack per claimed task (a re-issued `task_id` is ignored: audit `duplicate`, no second ack) |
| `positions/<SYMBOL>-<posid>.json` → `positions/closed/` | EA | live position view for the web app (Slice 4) |
| `state/<SYMBOL>/<signal_id>.json`, `state/<SYMBOL>/parked.json`, `state/<SYMBOL>/seq.json`, `state/account.json` | EA | restart survivability (G4) — the EA's own truth for flags/R-accounting; the terminal is truth for existence |
| `heartbeat_<SYMBOL>.json` | EA | E5 liveness, ≤60 s (§11-1: per instance; the watchdog alerts on ANY stale instance) |
| `audit_<SYMBOL>.log` | EA | append-only command audit (S8) |
| `config/live.json`, `config/trading_enabled.json`, `config/risk_mult.json`, `config/account.json` | trader / provisioning (EA writes `live.json` defaults if missing) | knobs; **`trading_enabled.json` missing or unparseable = disabled (fail closed)** |
| `journal/<SYMBOL>_YYYYMM.csv`, `.actions.csv`, `.delays.csv` | EA | the graded journals, CSV, monthly (E3, §11-5: never mixed with tester files) |

## `signal.json`

```
schema_version, ea_build, symbol, symbol_root, asset_class (FX major|FX cross|equity index|energy|metal|(unclassified)),
signal_id, signal_key ("<SYMBOL>-<id>"),
status: open | approved | approved_pending | skipped | auto_skipped | expired | rejected,
auto_reason ("" | "election:<ccy> <name> @<iso>" | "events_not_loaded" | "skip:<code>" | "no-response: max_age reached" |
             "invalidated: price through frozen SL while parked" | "order_failed"),
signal_time, decision_bar, published_at, deadline (ISO-8601 UTC), sigtime_text, session,
strategy, strategy_text, direction (BUY|SELL), direction_sign (+1|-1),
levels{entry, sl, tp, tp1, tp2, partial_fraction, stop_entry, two_target},
true_orig{entry, sl, tp1, tp2}                      -- frozen at first presentation (to_* columns)
rr{detector, runner, tp1, floor},
sizing{lots, risk_pct_gate, risk_mult_applied, risk_pct_effective, lots_line, sl_atr, atr14},
regime{tag (TREND_UP|TREND_DOWN|CHOP|""), with_trend ("1"|"0"|""), pretty},
decision_class (TAKE|DISCRETION), protocol_text (rule-citing wording, identical to the popup),
events[{t_utc, anchor_utc, hours_until, ccy, name, cls (V|W|C|H), sig ([HIGH]|[MED]|[LOW]),
        label (NO-HOLD (election) | NO ENTRY <6h (big release) | caution | holiday/thin), binding}], events_count,
election_gate{hit, event, anchor_utc},
exposure{aggregate_risk_to_stop, account_positions, open_positions[{posid, signal_id, strategy, direction, open_r}]},
max_age_bars, delay_count, implicit_streak, entry_modes[ market | pending ], trading_enabled,
overlay{zone{from,to,hi,lo}, zone2{hi,lo}, leg{t0,p0,t1,p1}, aux[{price,label}], swings_hi[{t,p}], swings_lo[{t,p}], ema_h4{e20,e50,e200}},
bars_h4[[t_epoch,o,h,l,c,v] × ≤500 oldest→newest], bars_d1[… ≤500], bars_h4_count, bars_d1_count
```
`deadline` = current bar open + `max(1, max_age_bars − implicit_streak)` H4 bars. `binding` = W within the election
horizon, or V inside 6 h. `sigtime_text`'s `(+N bars)` counts calendar H4 periods exactly as the tester popup does.

## `task.json` (the ONLY thing the EA parses from the web app — S6)

```
{ "schema_version": 1, "task_id": "<[A-Za-z0-9_-]{8,64}>", "symbol": "<SYMBOL>",
  "signal_id": <int>  |  "position_id": <int>,
  "verb": approve | skip | delay | close | close50 | sl_be | ratchet_tp1,
  "params": { "entry_mode": market | pending }       -- approve (pending only at a delay reopen; market_now = market)
            { "reason_code": 1..6 }                    -- skip
            {}                                         -- everything else
  "issued_at": "<ISO-8601 UTC>", "issued_by": "<session>" }
```
Flat JSON only: one nested `params` object, no arrays, ≤64 KB. Tasks for another symbol are left untouched.
Processed in `issued_at` order (file name as tie-break). A task older than `task_max_age_hours` (24) is acked `expired: stale_task`.

## `ack.json`

```
{ schema_version, ea_build, task_id, symbol, verb, signal_id|position_id, result: accepted|rejected|expired, reason,
  executed_at, refs{signal_id, posid, order_ticket, lots, decision, entry, sl, tp, pos_sl, pos_volume} }
```
**Reason enum** (a `:` suffix may carry detail): `ok, schema_version_unsupported, malformed_json, bad_task_id, bad_params,
unknown_verb, symbol_mismatch, unknown_signal, signal_not_open, unknown_position, position_closed, stale_task,
restart_during_execution, trading_disabled, events_not_loaded, election_gate, setup_lock, geom_invalid, rr_below_floor,
stop_too_tight, lots_zero, ftmo_daily_headroom, ftmo_max_headroom, be_floor, be_stops_level, be_would_loosen,
ratchet_not_banked, ratchet_already_used, ratchet_no_tp1, ratchet_not_past_tp1, ratchet_would_loosen, min_lot_split, order_failed`.

### Verb × state legality (the EA is the authority; the UI mirrors it)

| verb | requires | re-checked at execution |
|---|---|---|
| approve | signal parked (`status: open`) | kill switch, AutoTrading armed, calendar loaded, election gate (G1), one-setup lock, entry_mode legal, geometry / MinRR floor / min stop (market: `ValidateMarketEntry`; pending: `ValidGeom` + market not through SL), lots > 0, FTMO headroom incl. aggregate risk-to-stop (G3/§11-7) |
| skip | signal parked; `reason_code` 1–6 | — |
| delay | signal parked | resets the code-8 counter (explicit delay = a response) |
| close | row with that `posid`, position open | — (always allowed, incl. kill switch off) |
| close50 | position open | min-lot split possible |
| sl_be | position open | **`BEPlaceable`**: open R ≥ +0.5 (`be_floor`), stops-level band, and tighten-only |
| ratchet_tp1 | position open | **`RatchetPlaceable`**: banked, TP1 exists, once per trade, price past TP1, tighten-only |

Kill switch OFF: `approve` is refused (`trading_disabled`); skip/delay and every risk-reducing position verb still work.

## Signal lifecycle (G1/G2/§11-3)

1. Detector emits → `HandleSignal` runs exactly as in the tester up to the popup → **LivePresent**.
2. Fresh signal: election gate (class-W row for base/quote/All within `election_horizon_days`, midnight-anchored; calendar
   not loaded ⇒ gate hit) → `auto_skipped`, journal `skipped` code 2 with `auto=1`. Else publish `status: open` and PARK.
3. While parked, detectors are paused (one setup per symbol, as in the tester). Each bar close with no task = implicit
   FREEZE delay (`.delays.csv` row with `implicit=1`); an explicit `delay` task logs `implicit=0` and resets the streak.
4. Bar close with price through the frozen SL ⇒ `rejected` (`invalidated`), journal `rejected` (§11-3, live only).
5. `implicit_streak ≥ max_age_bars` ⇒ `expired`, journal `skipped` code 8 (no-response).
6. `approve` ⇒ `CommitDecision` (the tester's commit code) ⇒ `approved` / `approved_pending`; `skip` ⇒ `skipped`.

## `heartbeat_<SYMBOL>.json`

```
{ schema_version, ea_build, symbol, status: running|stopped, ts, account_login, equity, balance, margin_free,
  trading_enabled, terminal_trade_allowed, mql_trade_allowed (§11-8: both MUST be present every beat),
  aggregate_risk_to_stop (§11-7), risk_mult_applied,
  ftmo{initial_balance, day_key, day_start_balance, day_start_equity, daily_floor, max_floor, buffer, headroom_daily, headroom_max} (G3: the numbers the EA enforces),
  open_positions[{posid, signal_id, strategy, direction, open_r, banked_r, lots, banked, ratcheted}],
  parked_signal_id, last_signal_id, in_tester, alerts[ unprotected_position:<n> | terminal_trade_not_allowed | mql_trade_not_allowed | … ] }
```

## `audit_<SYMBOL>.log` — one line per event, append-only

`<ts>|<symbol>|<event>|<task_id>|<verb>|<target>|<result>|<reason>|<detail>` — events: `restart, stop, kill_switch, config,
published, republished, auto_skip, invalidated, expired_code8, claimed, duplicate, gate, executed, rejected, acked,
adopt_orphan, month_rollover, ftmo_reject`. A torn last line is possible; readers tolerate it.

## Config files

- `live.json` `{schema_version, election_horizon_days (14), max_age_bars (3), task_max_age_hours (24)}` — written with the
  input defaults if missing.
- `trading_enabled.json` `{"trading_enabled": true|false}` — read every second; **missing/unparseable ⇒ false**.
- `risk_mult.json` `{"EURUSD": 0.5, "US100": 1.0, …}` keyed by symbol root (broker suffix stripped); missing symbol ⇒ 1.0 (+ one audit warning).
  Applied ONLY at order sizing (C2); the detectors' 1 % viability gate is untouched, so the emitted signal set equals the tester's.
- `account.json` (G3/§11-7) `{schema_version, initial_balance (0 ⇒ captured from ACCOUNT_BALANCE on the first live run and
  persisted in state/account.json, never re-captured; >0 ⇒ config wins), daily_loss_pct 0.05, max_loss_pct 0.10, buffer_pct 0.005,
  day_reset_mode "server_midnight", day_reset_hour 0, day_ref balance|equity|max, daily_base initial|day_start}`. Re-read every
  poll (trader-editable). Pre-order check: `equity − aggregate_risk_to_stop − this_trade_risk` must stay above
  `day_ref − daily_loss_pct × base + buffer` (else `ftmo_daily_headroom`) and above `initial × (1 − max_loss_pct) + buffer`
  (else `ftmo_max_headroom`); every rejection writes an `ftmo_reject` audit line carrying `agg` and `this`. The same numbers
  are published in the heartbeat `ftmo{}` block. **Go-live checklist:** confirm FTMO's wording for the day-start reference
  (balance vs equity) and the daily base (initial vs day-start) and set `day_ref`/`daily_base` accordingly — config only.
- `state/account.json` (EA) `{schema_version, login, initial_balance, day_key, day_start_balance, day_start_equity, set_at}` —
  `day_key` = server date after `day_reset_hour`; a mid-day restart never re-snapshots.

## Journals (CSV, monthly, append-only columns)

Live journals live under `<root>\journal\` and NEVER in the tester's `journal\` (§11-5). Files are **monthly**
(`<SYMBOL>_YYYYMM.csv`, `.actions.csv`, `.delays.csv`); a journal row is keyed by its **signal month**, an action by its
**bar month**, a delay by the month of the bar it was logged on. The journal and actions files are rewritten atomically
(temp + rename) on every state change for every month that has rows in memory (a position opened in May and closed in
June stays in May's file); `.delays.csv` is append-only (§11-4: a torn last line is legal, readers must tolerate it).
There is no `.part` file and no finaliser rename in live mode.

Columns are the tester's 46 plus, **appended in this order, live mode only** (§11-6):

| column | meaning |
|---|---|
| `live` | `1` (row produced by the live queue path) |
| `account_id` | `AccountInfoInteger(ACCOUNT_LOGIN)` |
| `risk_pct_gate` | the detectors' viability-gate risk (`InpRiskPct`, 0.0100) |
| `risk_mult_applied` | `risk_mult.json` multiplier at sizing time (C2; sizing only, detectors untouched) |
| `auto` | `1` only on `skipped` rows with `skip_reason=2` written by the election gate (G1) |

`.delays.csv` appends `implicit` (`1` = bar-close FREEZE re-present, `0` = explicit `delay` task). Every grading reader
uses `csv.DictReader` by header name, so the tester's 46-column files and the live 51-column files read identically;
tooling gets a `--live` root option in M2.

## State files (G4, Slice 4)

`state/<SYMBOL>/parked.json` = the parked signal (candidate + park counters + frozen regime/class/true-orig), removed when resolved.
`state/<SYMBOL>/<signal_id>.json` = the full journal row + its manual actions, rewritten on every state change; restored in `OnInit`
and reconciled against the terminal (terminal = truth for existence, file = truth for flags/R). Orphan positions with our magic and
no state file are adopted conservatively (`strategy=ADOPTED`, un-banked, no BE, no ratchet) with a heartbeat alert.

## Advisor verdict log line (`verdicts.live.log`, runner-written)

```
<UTC ts> | <symbol> | #<signal_id> | <strategy> <direction> | <VERDICT> (quick, <confidence>) | SH:<regime><news><corr> steps:<…> | Q:<A|B|C|-> | step: <decisive step> | <one-clause reason> | brief:yes|no
```
`brief:yes` when `market-brief.md` (the newest Context brief from the past 2 UTC days, trader ruling 2026-09-16) was
in that consult's bundle. Reply lines: `<ts> | <symbol> | #<id> | reply | <one clause> | brief:yes|no`.
Briefs: `<root>\advisor\briefs\<UTC>.md` (header comment: model, session, elapsed, sources, flags) + `briefs.log`
(`ts | session | model/effort | elapsed | sources=n | flags=… | by=… | ok|ERROR …`).

## Parking slots (trader ruling 2026-09-16)

`config\live.json` `max_parks` (default 1 = the graded one-setup rule; the live box runs 4): while fewer than
`max_parks` signals are parked on a symbol, the detectors keep running at bar close and each new setup is published,
pinged and consulted on its own (`signals/<SYM>-<id>.json`, `state/<SYM>/parked_<id>.json`). Each parked signal keeps
its own delay count, implicit streak and deadline. Approving one closes every other parked signal on that symbol as
`skipped` with **skip_reason 9 "superseded"** (`auto=1`, `auto_reason: "superseded: #N approved"`); one position per
symbol is unchanged. Skip codes: 1-6 trader reasons, 7 legacy, 8 no response, 9 superseded.

## Resting pending orders (trader rulings 2026-09-17)

An approval with `entry_mode: pending` places a limit/stop order at the original entry (`decision: approved_pending`,
`order_ticket` set, no `positions/` file until it fills). While it rests: the web app lists it under **Pending orders**
(live distance to fill, EA cancel time) from the terminal feed (`feedd /orders`). It ends one of three ways:
1. **fills** → the fill binds to the row, a `positions/` file appears, Telegram "FILLED";
2. **expires** unfilled at the 3rd H4 bar after placement (`InpPendingExpiryBars`, broker-clock bars; OANDA = UTC+3) →
   `decision: expired`, Telegram "PENDING ORDER EXPIRED";
3. **invalidated** when price trades through its SL before filling (ask ≥ SL for a sell, bid ≤ SL for a buy; checked
   every tick, live only) → order deleted, `decision: rejected`, `terminal: invalidated`, signal `status: rejected` with
   `auto_reason: "invalidated: price through SL … while the pending order rested"`, audit `invalidated … pending_sl_through`,
   Telegram "PENDING ORDER INVALIDATED". Same rule as a parked signal (§11-3).
