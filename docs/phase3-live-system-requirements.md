# Phase 3 — Live System: Technical Requirements

**Author:** the coach · **Date:** 2026-09-14 · **Status:** approved for build (real-capital sign-off issued 2026-09-14, see `training/phase3-signoff.md`)

This is a requirements document: it states what the live system must do and the invariants it must never violate. Implementation choices are the engineer's. Where the document says MUST, it is a hard requirement; SHOULD is a strong default the engineer may deviate from with a written reason.

---

## 0. Purpose, scope, non-goals

**Purpose.** Move the exam-proven hybrid system — four frozen detectors, the regime gate, protocol v3, doctrine-v2 exits, hard floors — from the MT5 strategy tester onto a live FTMO account, with the trader approving signals from a phone through a private web app.

**Scope.** One rented Windows Server VPS hosting everything: MT5 + the EA in a new headless live mode, a web application (the trader's only decision surface), a Telegram notifier (notify-only), an advisor auto-consult runner, a watchdog, WireGuard, OpenSSH. Provisioning script, state backup, shadow-period acceptance.

**Non-goals (explicitly out of scope).**
- No changes to detector logic, regime formula, doctrine-v2 exits, protocol v3, event classes, or floors. The live EA is the tester EA minus the popup, plus a queue.
- No Telegram commands. Telegram sends; it never receives anything that causes an action.
- No public API of any kind. No MetaQuotes-hosted VPS. No Wine.
- No new strategies, overlays, or studies as part of this build.

---

## 1. Architecture (single box)

```
 Trader's phone / laptop
        │  WireGuard tunnel (the ONLY thing open to the internet)
        ▼
 ┌────────────────────────── Windows Server 2022 VPS ──────────────────────────┐
 │  WireGuard svc   OpenSSH svc (key-only, WG interface only)                  │
 │                                                                            │
 │  Web app (binds WG interface only, HTTPS + login)  ◄──► Advisor runner      │
 │        │ writes tasks / reads signals, acks, heartbeat                      │
 │        ▼                                                                    │
 │  File queue  (MT5 Common\Files\live\ … atomic JSON files)                   │
 │        ▲                                                                    │
 │        │ polls tasks / writes signals, acks, journals, heartbeat            │
 │  MT5 terminal (auto-logon session, locked screen) ── EA in LIVE mode        │
 │                                                                            │
 │  Telegram notifier (outbound only)     Watchdog (heartbeat, restarts, alerts)│
 └────────────────────────────────────────────────────────────────────────────┘
```

Integration between the web app and the EA is a **local file queue on the same filesystem**. There is no network path between the internet and MT5, and no network path between the web app and MT5.

---

## 2. Security requirements (hard)

### 2.1 Network exposure
- S1. The only inbound port reachable from the internet MUST be WireGuard's UDP port. Windows Firewall default-deny inbound; provider firewall the same. Verified by an external port scan during acceptance (§8).
- S2. RDP MUST be disabled at the service level (not merely firewalled). Provider Windows images ship with 3389 open — closing it is step one of provisioning, before anything else runs.
- S3. OpenSSH MUST bind only to the WireGuard interface address, key-only authentication, password auth disabled, Administrator login via SSH disabled in favor of a dedicated admin account.
- S4. The web app MUST bind only to the WireGuard interface address, serve HTTPS (self-signed or private-CA certificate is acceptable inside the tunnel; the trader pins it on the phone), and require login (single user; strong password; session expiry ≤ 24h; TOTP SHOULD be offered).
- S5. Telegram is outbound-only. The bot token's compromise MUST at worst allow spam to the trader. The bot MUST NOT parse or act on any inbound message. Links it sends point at WireGuard-private addresses and are useless off-tunnel.

### 2.2 The trading boundary
- S6. **No free-form trading endpoint.** The web app can only emit the typed task verbs in §4 against signal ids the EA itself reported, or position ids the EA itself reported. There is no endpoint that accepts a symbol, direction, price, or size.
- S7. **The EA re-validates every task** against its own state and doctrine before acting (§3.4). The web app is untrusted from the EA's point of view.
- S8. **Append-only command audit log**: every task written, by whom (session), when, and the EA's ack/reject with reason. The coach grades live months from this and the journals.
- S9. Secrets (broker password, Telegram token, web session secret, advisor OAuth token) live outside the repo, readable only by the service accounts that need them. Nothing secret in journals, logs, or the web UI.
- S10. Minimal software surface: no browser use on the box, no unnecessary services, Windows Update on a defined maintenance window with a pre-update snapshot (§7).

---

## 3. EA — LIVE mode requirements

### 3.1 Parity with the tester build
- E1. All four detectors (SweepMSS, DeepFib, TrendCont, EMArevQ), the regime tag, decision_class, doctrine-v2 exits (+1R 50% bank + BE, tighten-only ratchet), the +0.5R BE hard block, the election NO-HOLD hard gate, MinRR floors, per-pullback/per-stretch re-arm, FREEZE delay semantics — all byte-identical to the current tester build. `InpLiveMode=true` selects the queue path instead of the popup; nothing else differs.
- E2. The popup DLL is not loaded in live mode. Custom indicators (tri-EMA) load as today.
- E3. Journaling parity: the main journal, `.actions.csv`, `.delays.csv`, `decision_class`, `to_*` true-orig columns, `entry_mode`, and the counterfactual columns MUST be written exactly as in the tester, plus a `live=1` marker and the account id. Journals rotate monthly (`SYMBOL_YYYYMM.csv`), because the coach grades live months like windows.

### 3.2 Signal publication
- E4. On a signal, the EA writes `signals/<signal_id>.json` (§4) containing everything the tester popup showed plus what the chart renderer needs: symbol, strategy, direction, levels (entry/SL/TP1/TP2, true-orig), R:R, lots at 1% (or the symbol's risk multiplier, E9), regime tag + with_trend, decision_class, PROTOCOL badge text (rule-citing wording), the events panel (next 2 weeks, English labels), the last N H4 bars and N D1 bars (N sufficient for the renderer's overlays and EMA200 warm-up), overlay geometry (swings, zones, pullback swing, EMA values), and the **decision deadline** (E6).
- E5. The EA MUST publish a heartbeat file every ≤60s (`heartbeat.json`: timestamp, account equity/balance, open positions, last signal id, EA build) so the watchdog and the web dashboard can tell "alive" from "hung".

### 3.3 Decision window
- E6. A signal is answerable until the **close of the next H4 bar** (i.e., the same one-bar window the delay feature already uses). If no task arrives by then, the EA journals `skipped, code 8 (no-response)` and the signal expires. A `delay` task extends by one bar exactly as in the tester, with the same FREEZE/market-now reopen semantics.
- E7. Missed signals are never auto-taken. The hybrid covenant stands: **the EA cannot enter without a task.**

### 3.4 Task validation inside the EA (untrusted input)
- E8. Every task MUST be checked before acting: signal/position id exists and is still live; verb is legal for the current state (e.g., `ratchet` only after the +1R bank; `sl_be` only at open R ≥ +0.5; no stop move ever loosens); the election gate and event horizons are re-checked at execution time; TAKE/DISCRETION semantics unchanged. Rejected tasks are acked with a reason and logged (S8). Duplicate task ids are ignored idempotently.
- E9. Sizing is computed by the EA only: 1% risk × a per-symbol risk multiplier from a config file (`risk_mult`: indices and oil 1.0; FX 0.5 per the sign-off's allocation ruling; trader-editable; the web app displays it, never sets it per trade).
- E10. **FTMO account guards** run before any order: daily-loss and max-loss headroom checked against configured limits (5% daily / 10% max on the account's rules); if a new position could breach either at its stop, the task is rejected with reason. A global `trading_enabled` kill switch (config file + web toggle) MUST be honored on every tick.

---

## 4. File-queue protocol

- Q1. Root: `Common\Files\live\` with subdirectories `signals/`, `tasks/`, `acks/`, `positions/`, `heartbeat.json`, `audit.log`. The web app writes only to `tasks/` (and reads everything); the EA writes everything else.
- Q2. **Atomic writes** everywhere: write to a temp name, then rename. Readers ignore temp names. No partial reads possible.
- Q3. `task.json` schema (single verb per file): `{task_id (uuid), signal_id | position_id, verb, params, issued_at, issued_by}`. Verbs: `approve` (params: entry_mode pending|market_now when applicable), `skip` (params: reason_code 1–6), `delay`, `ratchet_tp1`, `close`, `close50`, `sl_be`. Nothing else is parsed.
- Q4. `ack.json`: `{task_id, result: accepted|rejected|expired, reason, executed_at, order/position refs}`.
- Q5. Polling: EA polls `tasks/` on its timer (≤5s); the web app polls `signals/`, `acks/`, `positions/`, `heartbeat.json` (≤5s) or uses filesystem watch. Consumed tasks move to `tasks/done/`. Retain 90 days, then archive.
- Q6. Schemas are versioned (`schema_version`) and documented in `docs/live-queue-schema.md`; both sides reject unknown versions.

---

## 5. Web application requirements

- W1. Single-user, mobile-first, fast. Pages: **Dashboard** (heartbeat status, account equity/drawdown headroom vs FTMO limits, open positions with live R, pending signals with countdown to deadline, kill switch), **Signal** (§5.1), **Position** (§5.2), **Journal** (browse/filter the live journals; per-decision detail with chart), **Events** (deployed calendar, next 2 weeks, English labels, per-symbol relevance), **Settings** (risk multipliers display, notification preferences, session management).
- W2. Charts are **rendered server-side** from the bars in the signal file (reuse the existing Python renderer used for D1 and the calibration sheet), with the same overlays and tri-EMA colors as the tester, H4 and D1 side by side. No dependence on MT5's display.

### 5.1 Signal page — everything needed to decide, on one screen
- W3. Header: strategy, direction, symbol, PROTOCOL badge (TAKE/DISCRETION, rule-citing text), regime tag, deadline countdown.
- W4. Charts (W2), levels, R:R, lots, risk multiplier in effect.
- W5. Events panel: next 2 weeks, English labels, with the binding ones (NO ENTRY <6h, NO-HOLD) visually distinct; the longer-horizon watch line.
- W6. **Advisor verdict**, auto-generated on signal arrival (§6): the full scorecard exactly as in training, plus a reply box that continues the same advisor session; replies and answers render inline.
- W7. Actions: Approve (with pending/market-now choice at a delayed reopen), Skip (reason picker 1–6, same wording as the popup), Delay. Buttons disable after the deadline or after an ack; every press shows the EA's ack or rejection reason.

### 5.2 Position page
- W8. Live R, banked R, stop/target levels, time in trade, next scheduled events inside the hold. Actions: `ratchet_tp1`, `close`, `close50`, `sl_be` — each enabled only when doctrine allows (the UI mirrors the EA's rules; the EA remains the authority).

### 5.3 Notifications (Telegram, outbound only)
- W9. Messages on: new signal (with deadline and a deep link), fill, +1R bank, exit with R, task rejection, heartbeat loss, MT5 restart, reboot recovery, drawdown headroom warnings (configurable thresholds), daily summary. All links are WireGuard-private.

---

## 6. Advisor auto-consult (live mode)

- A1. On each new signal the server runs one advisor consult headlessly and attaches the verdict to the signal (target: verdict visible within ~30s of the Telegram ping). Reuse the existing subscription-funded transport (Agent SDK / Claude Code headless with the stored OAuth token; effort low; the advisor's current model choice) — no per-token API billing.
- A2. **Live advisor is sighted.** Blindness exists to prevent hindsight in historical replay; live has no future to leak. The live advisor may see symbol, date, the real calendar, and the events panel. The coach will supply a `CLAUDE.live.md` role file; the tester's blind `CLAUDE.md` remains untouched for training. The two modes MUST never share a session.
- A3. Input bundle: the server-rendered H4/D1 charts, a `setup.md` with the same fields as training plus symbol, date, `class:` (FX major / equity index / energy / metal), and the events panel.
- A4. Output: the standard scorecard format; logged to a live `verdicts.log` whose line format gains `timestamp | symbol | signal_id` fields (the training log's `#N`-only format has been a chronic audit pain).
- A5. Reply box (W6): the trader's message is appended as a follow-up turn in the same session; the answer is logged and rendered. Session lifetime = the signal's decision window plus the position's life.

---

## 7. Operations, recovery, backup

- O1. **Unattended recovery.** Auto-logon of a dedicated trading account with immediate screen lock; MT5 launched by a scheduled task at that logon with a startup `.ini` that attaches the EA to its charts with its `.set`; "save password" enabled so the terminal reconnects without a human; web app, advisor runner, notifier, and watchdog as scheduled tasks/services that start at boot. Target: full trading readiness within 5 minutes of any reboot, with a Telegram "recovered" message.
- O2. **Watchdog**: EA heartbeat stale > 3 min → Telegram alert; MT5 process absent → restart + alert; web app down → restart + alert; disk/clock/time-sync sanity checks. The watchdog itself runs as a service with its own restart policy.
- O3. **Time**: the box keeps UTC, NTP-synced; all timestamps UTC.
- O4. **Windows Update**: a weekly maintenance window outside peak sessions; the pre-update step takes a provider snapshot (calendar reminder — provider snapshots expire); post-update the reboot recovery (O1) is verified by the watchdog and reported.
- O5. **Provisioning script** (idempotent PowerShell): from a fresh Windows Server 2022 image to trading-ready — firewall rules, disable RDP, OpenSSH, WireGuard, MT5 install, Python, app deploy, scheduled tasks, auto-logon, restore of state folders. Documented in `docs/live-runbook.md`.
- O6. **State backup, pulled from the trader's side nightly** over the tunnel (the box cannot reach the backups): `MQL5\` (experts, indicators, includes, sets), `Profiles\`, `config\`, `Common\Files\` (journals, queue archive, captures), the web app data directory, WireGuard configs, scheduled-task XML exports, `verdicts.log`. Restore = provisioning script + state restore.
- O7. **Deploy path** for EA updates: `scp` the compiled `.ex5` over the tunnel; document the hot-reload behavior and the restart-with-ini path for larger changes.

---

## 8. Shadow period — acceptance criteria (the only new gate)

The full loop runs against an **FTMO demo/free-trial account** (real orders to a demo, not simulated internally) for **at least three weeks** before the live account is attached. The coach grades the shadow weeks like windows. Acceptance requires ALL of:

1. EA-alive uptime ≥ 99.5% (from heartbeat), zero signals missed by the plumbing (every detector signal produced a signal file, a Telegram ping, and an advisor verdict; verified against a parallel tester run over the same period where feasible).
2. Task round-trip: every trader action produced an ack within 10s; rejections carried correct reasons; no duplicate executions.
3. Recovery: ≥3 deliberate reboots from SSH with full unattended recovery each time; ≥1 full rebuild-from-script + state restore drill, with the minutes-to-trading-ready number recorded.
4. Security: external port scan shows only the WireGuard UDP port; RDP service disabled; SSH refuses password auth; web app unreachable off-tunnel; secrets absent from repo, logs, and UI.
5. Doctrine parity: journals from the shadow period pass the same audit the coach runs on training windows (protocol, events, floors, ratchet legality) with the EA's validation rejecting at least one deliberately illegal task in a test.
6. Advisor: verdict latency and format meet A1/A4; reply round-trip works.

Only after the coach signs the shadow report does the live FTMO account replace the demo. Symbol allocation on go-live follows the sign-off (indices + oil 1.0×, FX 0.5×).

---

## 9. Deliverables

- The EA live mode (build + `.set`), the queue schema doc, the web app, the notifier, the advisor runner, the watchdog, the provisioning script, the backup/restore scripts, `docs/live-runbook.md` (setup, deploy, recovery, update window, backup, emergency console access), and the shadow-period report.
- Engineer flags any requirement it cannot meet as written, with a proposed alternative, before building around it — same standard as every prior work order.

---

## 10. Rulings addendum (2026-09-14) — engineer's EA-mapping gaps

The engineer's code mapping found five places where §3 assumed behavior the tester build
does not have, plus two hard constraints. Coach rulings, binding for the plan:

**G1. Election / NO-HOLD hard gate — BUILD (new work; the coach's "already frozen" was
wrong: it was ordered in the pair-12 batch and never confirmed delivered).** At signal time
and again at task execution: if any NO-HOLD (class W) row for a binding currency falls
within the **election horizon = 14 calendar days** (configurable), the entry is rejected —
journaled as an automatic skip (code 2, `auto=1`) with the event named in the ack. Elections
are rare; a wide horizon costs little and the German-election charge was 8.7 days out.
Hold-through edge case (snap election announced after entry): no automatic force-close —
the watchdog/web app raises a NO-HOLD alert and the trader closes from the position page.
Human stays in the loop on exits; the machine only refuses entries.

**G2. Decision deadline (E6) vs the trader's standing "unlimited delays, never auto-cancel"
ruling — RECONCILED, not overruled.** In live mode an unanswered signal is treated as an
implicit DELAY at each bar close (FREEZE semantics, original levels, re-arm rules unchanged),
so it persists across bars exactly as the trader ruled. It ends only when (a) a task arrives,
(b) the detector invalidates the setup (existing `rejected` path), or (c) it reaches
**max_age = 3 H4 bars (12h), configurable**, after which it is journaled `skipped, code 8
(no-response)`. Every implicit delay is written to `.delays.csv` with `implicit=1` so the
coach can see the trader's response latency. The trader may raise max_age; the default
bounds staleness without breaking the covenant.

**G3. FTMO guards + account id (E10, E3) — BUILD, greenfield.** Track initial balance,
day-start equity/balance per FTMO's published daily-loss definition (engineer to implement
exactly as FTMO states it, config-driven: daily 5%, max 10%, safety buffer 0.5%), and journal
the account login. Pre-order check: if the new position's full −1R loss would take either
limit below buffer, reject with reason. Kill switch as specified.

**G4. Restart survivability — BUILD, in scope under E1 (parity means the +1R bank, BE,
ratchet flags and R-accounting survive a restart).** Ruling on mechanism: a per-position
`state.json` written atomically on every state change, restored in OnInit and reconciled
against the terminal's actual open positions (the terminal is the source of truth for
existence; the state file for flags and R-accounting). Positions found open with no state
file are adopted conservatively: treated as un-banked, BE not set, ratchet unused, and the
coach alerted. Journal-replay rehydration is acceptable as a fallback, not the primary.

**G5. Atomic writes, JSON queue, OnTimer — BUILD.** OnTimer at 1s drives polling (≤5s) and
the heartbeat (≤60s) independent of ticks. Queue files are JSON with write-to-temp + rename.
**Journals stay CSV** (the coach's grading tooling reads them); apply write-to-temp + rename
to journal writes too so a crash never truncates a journal.

**C1. E2 compile-time switch — APPROVED as `#ifdef LIVE` around the import block**, single
source file, not a forked target (fork = drift). The build script produces both artifacts
from one `.mq5`; the live `.ex5` name carries `-live`.

**C2. E9 vs E1 — APPROVED as proposed:** the per-symbol risk multiplier applies only in
`SizeByRisk` at order time; the detectors' 1% min-lot viability gate stays untouched so the
emitted signal set is identical to the tester's. Journal both `risk_pct_gate` (1%) and
`risk_mult_applied` per trade.

**Small items:** the live task validator MUST call `BEPlaceable` (and every other doctrine
check the popup relied on greyed buttons for — audit them all; the button was never the
enforcement, S7 says the EA is). `StratMinRR` gets an explicit `TrendCont → 1.0` entry (no
behavior change; ratified default made explicit).

Coach error ledger (Phase 3): G1 assumed delivered without confirmation; the +0.5R block
assumed to live in the execution path rather than the UI. Both now specified correctly.

---

## 11. Rulings on the plan-mode flags (2026-09-14)

1. **Per-symbol files — APPROVED.** `heartbeat_<SYM>.json`, `audit_<SYM>.log`,
   `signals/<SYM>-<seq>.json`; watchdog and web app glob them. The dashboard MUST show
   per-instance liveness and the watchdog MUST alert on any single stale instance, not
   just on all-stale.
2. **Auto-skip naming — APPROVED as proposed.** Event named in the signal file
   (`auto_reason`), the audit line, and the journal row (skip 2, `auto=1`); later tasks
   against that id ack `rejected: signal_not_open`; approve-time re-check names it in the ack.
3. **Parked-signal invalidation — APPROVED, option (a), live-only, documented.** A frozen
   setup whose SL has been traded through is structurally dead; journaling it
   `rejected: invalidated` is truer than letting it age into code 8, and approving it would
   have failed geometry validation anyway. Not on TP-through (the trader may still want the
   pending at frozen entry). Tester behavior unchanged; noted for a future parity batch.
4. **`.delays.csv` stays append — APPROVED.** A torn last line is acceptable; readers
   (coach tooling included) MUST tolerate a partial final row. Journal + actions stay
   temp+rename as ruled.
5. **Live journals under `live\journal\` — RULED.** Never mixed with tester files (a
   tester relaunch on the same box would otherwise collide). Tooling gets a `--live` root
   option; monthly rotation as E3.
6. **Live-only extra columns — APPROVED.** Readers are by-name; document the header
   delta in `docs/live-queue-schema.md`.
7. **Cross-symbol stop-risk — STRENGTHENED, not delegated to the dashboard.** An MQL5
   instance can enumerate every open position on the account regardless of its chart
   symbol, with each position's SL. Therefore each EA instance MUST compute aggregate
   risk-to-stop across ALL account positions and include it in the E10 pre-order check:
   reject if (aggregate open risk-to-stop + this position's −1R) would take daily or max
   headroom below the buffer. The dashboard shows the same number; the EA enforces it.
   Correlated-exposure judgment (code 5) remains the trader's.
8. **AutoTrading armed — REQUIRED in the live startup `.ini`**, and the heartbeat MUST
   report `terminal_trade_allowed` and `mql_trade_allowed`; the watchdog alerts (and the
   reboot-recovery test fails) if either is false. A silently disarmed terminal is the
   quietest possible outage and the one this design must never miss.

9. **Multi-park (up to four parked signals per symbol, live-only) + skip code 9
   "superseded" — ACCEPTED, with grading rules.** (a) Code-9 rows are automatic, never
   decisions: excluded from precision, from the forgone ledger, and from every
   violation audit — including when the superseded signal was TAKE-class, because
   approving any signal on that symbol satisfies the participation obligation under
   one-position-per-symbol. (b) Code 8 (no-response) on a **TAKE-class** signal IS a
   chargeable protocol violation — the only legal ways off a TAKE are approve, an
   event/correlation skip, invalidation, or supersession by an approval. Sitting a
   TAKE out until it expires is an illegal skip with a timer. (c) The live signal set
   diverges from the tester's by design (the tester suppresses detections while one
   signal is parked); shadow-period plumbing checks compare against the live
   detectors' own emissions, not a one-park tester run. (d) Superseded signals' blind
   outcomes are computed by the coach's tooling as a "selection among alternatives"
   note (did the trader pick the better of concurrent setups) — reported once n≥10,
   never graded.

### 11.9 Multi-park and code 9 (coach ruling 2026-09-16, relayed by the trader)

- **Code 9 "superseded"** rows are never decisions and never chargeable: excluded from precision, from the forgone
  ledger and from every violation audit - including when the superseded signal was TAKE-class (under one position per
  symbol, approving any signal on that symbol satisfies the participation obligation).
- **Code 8 (no-response) on a TAKE-class signal IS a chargeable violation.** A TAKE can only end four ways: approve, an
  event/correlation skip, invalidation, or supersession by an approval. Letting one expire is an illegal skip with a
  timer on it. The journal row carries `decision_class`; the `expired_code8` audit line carries `class=<…>` and the
  literal `CHARGEABLE_VIOLATION` when the class is TAKE, so the audit is mechanical.
- The live signal set now diverges from the tester's by design (the tester still suppresses detections while a signal
  is parked): §8.1's plumbing check compares against the live detectors' own emissions, not a one-park tester replay.
- Superseded signals' blind outcomes are computed by coach tooling as a "selection among alternatives" note (did the
  trader pick the better of concurrent setups), reported at n≥10, never graded.
- TEST rows and the malformed-ack test from the first live night were purged from the shadow journals on 2026-09-16
  before real shadow records count (audit logs keep the `test_signal` lines, append-only).

**§11.9(b) AMENDED 2026-09-21 (trader ruling):** an unanswered TAKE-class signal that expires
(code 8) is journaled and **not charged**; there is no offline window. The monthly export
reports every expired TAKE-class signal with its blind outcome, the TAKE-expiry rate, and its
hour-of-day split. A TAKE-class SKIP with a reason other than event (2) or correlation (5)
remains chargeable. A `ftmo_daily_headroom` refusal is an EA auto-reject — journaled, tagged,
never a trader violation, including on an approved TAKE-class signal.
