# Decision Log

All user-approved decisions, newest first.

| Date | Decision | Detail |
|---|---|---|
| 2026-08-07 | EMArev: reject setups where the reversion is already spent by entry (wrong-side TP1 fix) | **Bug (user-caught in a visual run):** an EMArev SELL showed **TP1 above entry** (entry 1.21194, TP1/mean 1.21275) — a bank target in the loss zone. **Root cause:** `Emit()` hard-sets `tp1 = m_ema_frozen` (the mean frozen at the stretch extreme), but **entry is the *trigger* bar's close**, which fires up to `ENTRY_VALID_BARS` (3) bars later — after price has reverted back toward the mean. If that trigger bar closes past the frozen mean, TP1 lands on the wrong side of entry; the "sell high, bank at the mean" premise is already spent. `Emit()` only checked the *runner's* R:R (which passed), never the scale-out geometry, so it emitted a broken two-target setup (the dialog's own guard then disabled Accept — the safety net held, but the detector shouldn't produce it). **Fix (chosen: reject, not single-target or live-EMA):** a scale-out geometry guard in `Emit()` requires the entry to still be stretched beyond the mean by `TP1_MIN_ATR` (new named constant, **0.25 ATR** — a tick-epsilon would pass an economically-empty `T1 0.02` target) **and** TP2 strictly beyond TP1; else `ResetCtx()` + reject (matches the existing `rr<MIN_RR` path — the reversion won't return within the entry window). Dropping to single-target was rejected because it silently turns a 0.5/0.5 blended-R trade into a different strategy under the same journal name; live-EMA was rejected because a mean that already moved toward price gives an even closer (still-degenerate) TP1. New `m_dbg_geomrej` funnel counter (in the destructor `PrintFormat`) so over-rejection is visible, not buried in `skip`. **Verified headlessly** (EMA-only, EURUSD.dk 2021.01.01–06.30): pre-fix `trigger=7 → EMIT=7`; post-fix `trigger=7 → geomrej=1 → EMIT=6` — exactly the one wrong-side setup removed, the other 6 EMArev signals unchanged and all TP1-side-correct. (Latent display quirk noted, left as-is: `RrText` uses `MathAbs`, so a wrong-side TP1 *would* read as a small positive R — unreachable now that no such setup reaches the dialog.) |
| 2026-08-07 | Mid-trade management panel: Close / Close 50% / SL→BE while a position is open | **New feature (advisor doctrine now includes mid-trade actions — flatten before red-folder news, exit at BE on kill conditions, manual scale-out fallback — none of which were executable in the tester).** A small non-modal panel appears the moment a position fills and auto-hides when flat, showing live state (direction, lots, entry, current SL/TP, **Open R + Banked R** — R is the only decision unit) plus three buttons: **Close** (full exit), **Close 50%** (partial of current volume), **SL→BE** (stop to entry + `InpBEPadPips`). Close/Close-50% **arm→confirm** (one-click mis-clicks in a paused tester are easy); SL→BE is instant (reversible). **Key design:** the panel runs a Win32 window on its **own thread** with its own message loop — the signal dialog is pumped by the EA's blocking `OnTick` loop and so dies when the visual tester pauses, but a pause is *exactly* when the operator decides at bar close, so the panel must stay live while `OnTick` is stopped. The EA only pushes state + drains a latched button action (`TDM_Open/Update/Poll/Close`); all trading stays on the MQL thread. **Correctness guards:** Close-50% sets `tp1_done` (the auto scale-out sizes its partial off the *initial* lots — leaving it armed would close the whole runner); SL→BE moves only the live stop, never the journal `sl` (the risk basis for every R), reads `POSITION_TP` back unchanged (no mid-trade TP moves — one-edit doctrine), and is disabled inside the broker stops-level band; Open R is volume-weighted on the remainder so Open+Banked = close-now total. Every press is logged to a sibling `…_.actions.csv` (bar time, price, lots before→after, banked/open R) that `review_session.py --actions` grades. Panel is interactive/visual-only (dormant in headless `AA_ALL`/`AA_SKIP`). Four new DLL exports → **restart MT5**. **Runtime is UNVERIFIED** — compiles clean (DLL + EA, 0/0), but the threading path can only be exercised in a visual run (the headless verifier can never reach it); confirmed working = a future visual test. Non-goals held: no new order entry, no free SL/TP editing mid-trade. |
| 2026-08-07 | Account-safety: reject degenerate (too-tight) stops before sizing + margin cap backstop | **Critical bug fixed:** a SweepMSS signal whose entry (pullback-to-OB) landed ~on the frozen sweep extreme produced a **1.9-pip stop → the 1%-risk sizer made 13.47 lots** (~$1.5M notional on $25k). The R:R floor screens *reward*, not *risk*, so a tiny stop → huge R:R sailed through. Fixes (layered, detector-agnostic): (1) **min-stop gate** in `HandleSignal` before sizing/overlays/dialog — reject if SL distance < `max(InpMinStopATR·ATR14, InpMinStopSpreads·spread, stops_level)` (default 0.5 ATR); (2) the dialog **also blocks Accept** if an *edit* makes the stop too tight; (3) **margin cap** (`InpMaxMarginPct`, default 50% of free margin) so no upstream failure can place a monster; (4) rejects are **logged + journalled** (`decision="rejected"`) — never silently suppressed; (5) the dialog **shows SL in ATR multiples** (`SL 0.06 ATR` is an instant human flag). Verified headlessly: the exact signal (`entry 1.12593 / sl 1.12574`) is now `rejected`, while 12 normal SMC signals in the same window trade unchanged. (Detector note: the sweep-extreme selection that let entry ≈ extreme could be refined separately; the gate makes it safe regardless.) |
| 2026-08-06 | Scale-out survives edits: TP1+TP2 editable, SL edits never touch the TP side | **Bug fixed:** editing SL collapsed the two-target (TP1/TP2, 50% scale-out) plan to a single TP, silently changing the strategy's expectancy. Now: levels are fully independent — an SL edit only re-sizes lots + recomputes R:R, never mutates TP values or the scale-out structure. Scale-out strategies (`partial_fraction>0`: EMArev, DeepFib) expose **TP1 (bank) + TP2 (runner)** as separate editable fields (SMC keeps one TP). The dialog shows **per-target R1/R2 + the split-weighted blend**; the Accept floor-block tests the **runner's R** (which the detectors guarantee ≥ MIN_RR, so an unedited signal never false-blocks, while an edit that pulls the runner in still blocks). On Accept the plan is preserved (`tp=tp2` placed, `partial_fraction` kept), so TP1-partial + SL-to-breakeven automation keeps running. Journal adds `orig_tp1`/`orig_tp2` (28 cols). Also: **persistent swing markers** (rolling `InpSwingDays`=14 window, redrawn each bar, independent of the signal — EMArev never filled the per-signal arrays so its setups showed none); **coach mode also scrubs the chart corner label's timestamp** (`TD_Coach`); DLL signatures grew (`TD_Open`+tp2, `TD_Poll`+tp2, `TD_SetDisplay`+tp2) → **restart MT5** so the matching DLL loads. Guardrail hints (SL-inside-structure / TP-behind-wall warnings) **deferred** to next. |
| 2026-08-06 | Econ overlay: rolling forward window + look-ahead-safe bias; popup events list with coach-mode redaction | **Bug found & fixed:** the event overlay drew once at the first tick with `tmax = start+4h`, so in the tester (no future bars) it only ever showed events *before* the test start — forward events (incl. next-day central-bank decisions) never rendered, so the skip rule couldn't fire. Now the calendar is cached once and **redrawn each new bar** over a rolling `[now−InpEvtPastDays, now+InpEvtLookaheadHours]` (default 48h ahead). **Look-ahead safety:** upcoming events (still future) render **neutral `[upcoming]`** — the bull/bear bias + actual is revealed only once the event has passed. `IsTopTierEvent` widened (`monetary policy`, `press conference`, `interest rate`, `rate statement`, `bank rate`) so ECB/Fed/BoE statements are caught. **Popup events list:** the approval dialog now lists notable scheduled events for the pair over the next `InpEvtListDays` (14) days — only scheduled info (time/ccy/name), never outcomes, since all are after the decision. **Coach-mode toggle:** a button that blanks the symbol (`██████`) and switches all dates to **relative** (`in 1d 4h`) for screenshots to the AI coach — so it can't reverse-identify the historical instance and grade with hindsight; normal mode shows the real symbol + absolute `DD MMM HH:MM`. Justification: scheduled events are foreknowable to a live trader (legit), but their outcomes are not (gated). |
| 2026-08-06 | Dialog edit model: **independent levels, R:R is an output + floor guard** (supersedes the R:R-lock) | Entry, SL, and TP are now **independent** in the approval dialog — editing one never moves another. SL and TP are structural (a prior extreme / swing per the strategy specs), so dragging the stop must not drag the target. Each edit re-sizes lots to hold 1% risk, **recomputes and displays the live R:R**, and **blocks Accept when R:R falls below the strategy floor** (`InpSmcMinRR`/`InpFibMinRR`/`InpEmaMinRR`; 2.0 for DeepFib). This reverses the earlier "R:R never changes" lock (which incorrectly moved TP when SL was edited). Entry edits keep SL/TP fixed (removes the `InpEntryFollow` rigid/anchored input — behaviour is always structure-anchored); entry away from market still becomes a pending order. |
| 2026-08-01 | Entry now editable via pending orders (supersedes the read-only entry below) | Reverses the same-day deferral using the pending-order approach it was deferred *for*. Untouched entry = market fill as before; entry edited away from market on Accept = a **pending order** (buy/sell limit on the favourable side, stop on the breakout side, auto-selected and shown in the dialog's "Order" row). Level-follow modes: **rigid-shift** (default `InpEntryFollow=EF_RIGID`, SL/TP move with entry, R:R+lots held) and **structure-anchored** (`EF_ANCHORED`, SL/TP pinned, R:R+lots re-derive; flags when R:R drops under the strategy floor). Unfilled pendings auto-cancel after `InpPendingExpiryBars` H4 bars (default 3, journalled `expired`). Fill binds via `OnTradeTransaction` (`DEAL_ENTRY_IN` → posid) so scale-out/BE/exit-R work identically; a resting pending suppresses new signals (one setup/symbol). `risk_px` recomputed at the realised fill. Verified headlessly (forced-pending probe, `mt5_verify.sh --force-pending`): pendings fill, bind, scale out to 3.43R, and expire correctly. Also added: skip-reason capture (1–6 keys + `skip_reason` column), decision-time chart screenshot, `orig_entry/orig_sl/orig_tp` journal columns, and fixed the `tp1_done` `(non-string passed)` bug. |
| 2026-08-01 | ~~Approval dialog is editable (SL/TP), entry read-only~~ **(superseded above same day)** | Operator can retune Stop Loss / Take Profit in the popup; the other level auto-adjusts to hold R:R, lots re-size, and the chart lines update live before Accept/Skip. Entry was initially display-only because these signals fill at market and editing it would mis-size the trade — **superseded** the same day by the pending-order approach (row above). Dialog reworked from a blocking modal to a poll-driven DLL (`TD_Open`/`TD_Poll`/`TD_SetDisplay`/`TD_Close`) so MQL runs between keystrokes. |
 | All recommended defaults: counter-trend setups alerted+flagged (not filtered); fib deep-zone only (61.8–78.6%, stop past 88.6%); strict equal-highs/lows pools; EMA-reversion frequent profile with partial exit at mean + runner |
| 2026-07-16 | Agent team approved | Fable 5 PM; Opus 4.8 EA dev + strategy design; Sonnet 5 pipeline/QA/infra; Haiku 4.5 docs |
| 2026-07-16 | History depth | ~5 years (2020→present), extendable later |
| 2026-07-16 | Tick acquisition | QuantDataManager **Linux** CLI at /home/jack/QDM/qdmcli, license REDACTED (user installed) |
| 2026-07-16 | Project location | WSL-native ext4 (/home/jack/hybrid_project), not OneDrive |
| 2026-07-16 | GitHub remote | JacksonSchreiber/Hybrid_MT5_Strategies (user-provided PAT, stored outside repo) |
| 2026-07-16 | Overall plan approved | 4 phases; user gates every phase transition |

## 2026-09-30 — Coach rulings after the D1 withdrawal and the exit programme

- **D1 extension withdrawn.** `trendcont_d1_ext_min` = 0 (deployed 20:20 UTC). The number stays on the card as an
  observation; the mechanical Quality grade is gone (the advisor's own Quality line returns); the home screen sorts by
  signal time. The opinion counter restarted at the 20:22 UTC redeploy.
- **Study rule (pipeline, standing):** every study that joins a higher-timeframe bar states its bar-index rule in the
  report header (daily features use the last D1 bar dated strictly before the signal's day), and reconciles the
  feature against the EA's published value on at least 10 live cards before any result goes to the coach.
- **Doctrine v2 exit, TrendCont, live:** bank 25% at +1R (or TP1 if nearer), stop to entry at +1R, runner to TP2.
  `live.json trendcont_bank_frac: 0.25`, read at the moment of the bank; EA deployed 20:31 UTC, journaling of the
  share actually banked from 20:37 UTC (`partial_frac`; the lot step rounds 25% of 0.05 lots to 0.01 = 20%, and a
  0.01-lot position cannot split -> 0). Positions already banked at 50% stay as they are. Other detectors stay at 50%.
  Guards: the MT5 tester reproduction (EURUSD, US100, EA exit code, within +-0.02R of the replay) - fail = revert to
  50%; the bank shadow log (`web/shadow_bank.csv`, hourly) - 30 in-tolerance stop-to-entry fills is a revisit point.

## 2026-10-12 — Live custom plans (trader: "i want all the options just like how the practice is ... built it custom")

- **The live signal page is the Practice card.** Chart, levels, the advisor (full read + a verdict line by the plan), the full
  news, FTMO room, correlation, and the lever builder (every Practice lever: banks, stop moves, trail, average cuts with the
  12 h window, cut-unless, adds at a level / bar close / TP1 with early / hold / wait / risk cap, TP2 on/off, Inverse). The
  page preloads today's EA rules for that signal as levers (the +1R bank and stop to entry, the bloc's T+L1.0, the shorts'
  raise), so a plan starts from what would have run. `live_config "live_plans": {"enabled": false}` brings back the old card.
- **Execution:** the EA got generic primitives (approve with `plan` / `entry_pct` / `tp2` / `inverse`; `plan_add`, `set_sl`,
  `close_part`; `close` with a plan reason) and switches its own management OFF for plan rows (bank / BE, pyramid, shorts raise,
  bloc cut / lock, staged add, day-2 rules, min-lot guard, Inverse offers). The plan runner (live/plan_live.py, a thread in the
  monitor) replays the Practice kernel on the live M1 path and sends the primitives. docs/live_plans.md has the contract.
- **Live limits (CLAUDE.md 1% rule, hard):** the entry is at most 100% of the ruled size; every add runs under a 1R cap on the
  open risk (the plan's risk cap raises the stops first; the EA refuses any add over 1R x 1.05).
- **Verification:** kernel copy = Practice kernel on 109,350 runs (4,374 box frames x 25 plans, 0 mismatches,
  pipeline/plan_live_parity.py); the runner against a fake broker on 60 frames x 4 plans x BUY/SELL tracks the kernel to a
  median 0.000R / p90 0.03R (execution timing; pipeline/plan_live_sim.py); EA gate chain PASS (tester parity byte-identical,
  live self-test covering every new verb and the inverse).
- **Coach flags:** a custom plan overrides the doctrine per trade (v2 bank, bloc T+L1.0, shorts raise are only the preloaded
  default); live fills lag Practice by a poll (seconds) and round to the lot step; plan rows' journal R is per leg (weight by
  lots / full_lots, as staged adds).

## 2026-10-12 — A refused approve retries, stays open, and says why (trader: "yes let's do that ... make sure the error actually is descriptive")

- **Trigger:** US500 #6 was approved with a plan at 22:24 UTC. The broker refused the market buy with 10021 "no quotes",
  24 minutes after US indices reopened from their daily break, with 1-4 price updates a minute. The EA had consumed the
  signal (approved, no position), so it could not be approved again.
- **Now:**
  - *Retries:* the EA retries the broker's "try again" answers for about 8 s (live only; the tester is unchanged).
  - *Stays open:* if every attempt fails, the signal stays OPEN and can be approved again (no journal row, nothing decided).
  - *Says why:* the web page, Telegram and the plan log say in plain words what the broker said, what the market looked
    like, what was tried and what to do. The refused plan is preloaded for the next try.
  - *1% rule:* a retry never re-sends at a stop distance more than 1.05x the sized risk.
- **Verification:** EA gate chain PASS (tester parity byte-identical on both builds; the live self-test adds a synthetic
  refusal that fills on the retry, plus a refusal on every attempt that leaves signal #5 open, then re-approved).
- **Coach flag:** a refused-then-re-approved signal journals once, at the fill that happened. The refusal itself lives in the
  audit log (`order_retry`, `order_failed_open`), not in the journal.

## 2026-10-13 — A live plan's R counts from the fill, not the card entry (trader: "it should just be based on the original SL and not change")

- **Trigger:** EURCAD #3 (13:49 UTC) and NZDCHF #6 (02:01 UTC) were approved with plans after the price had already moved
  in the trade's favour (+0.91R and +0.66R past the card entry). The plan runner measured R from the card entry, so the plan's
  "stop to entry when +0.5R" lever was already true at the fill. It put the stop at the fill price, the spread put that
  stop through the price, and the runner closed each trade within seconds (-0.02R, -0.03R). AUDUSD #4's page showed two R
  numbers for the same price (the position's open R +0.72R, counted from the fill; the plan's replay +0.96R, counted with
  the card's 1R).
- **Now:** every new plan measures R like the position does: from the actual fill to the original signal stop (the EA's open
  R and sizing basis; never the moving stop). A late fill's levers start at 0 from where the trade got in. The running AUDUSD
  #4 plan keeps the card's R until it closes (re-basing it mid-trade would re-time its past cut checks).
- **Also (same day, trader's asks):** the journal tab sorts newest first by signal time, and its symbol box matches any part of
  the name in any case ("xauusd" finds XAUUSD.sim); the live charts show the cursor's price as R beside the crosshair price
  label (a signal: from its entry to its stop; a position: from the fill to the original stop).
- **Verification:** pipeline/plan_live_rbase_check.py replays EURCAD #3 on its real numbers (the old frame closes it, the new one
  does nothing) and checks AUDUSD #4's replay R equals the EA's open R; the runner-vs-kernel sim (pipeline/plan_live_sim.py) on
  normal and late fills (--late).

## 2026-10-13 — Plans can be edited on an open trade (trader: "on open trades, i'd like to be able to add stuff/edit/delete stuff to the plan")

- **What:** the position page has "Edit the plan": add, change or remove levers on a running plan (e.g. a new average cut at +1R,
  or the trail tightened from 1R to 0.5R). "Review the change" shows what the plan runner would do at once; "Save the change" applies
  it from that minute.
- **Rules (stated on the page):** a lever left as it is keeps what it already did; a changed lever starts fresh from now; a removed
  lever leaves its effects (a stop never moves back); a time that has passed never fires; a changed trail checks at once. The entry
  size and TP2 are fixed once the trade is open (TP2 would need an EA change); adds stay under the 1R cap (the 1% rule).
- **Verification:** kernel parity with Practice unchanged (0 mismatches) and every plan split into three identical versions returns
  the unsplit result bit for bit; edit scenarios pass; the runner sim matches the previous numbers; the full flow (review, save, the
  runner sending the stop move) ran on a local copy of AUDUSD #4 against the live price feed.
- **Coach flag:** an edited plan is no longer the plan approved at the signal - the plan record keeps every version with its minute
  (`versions`), and the plan log names each change.

## 2026-10-13 — The open-positions reviewer suggests plan edits (trader: "make sure the open trades reviewer advisor know that these edits can be made? it should suggest edits to the plan if necessary")

- **Now:** for a plan trade the reviewer sees the plan (each lever with its id, whether it fired, when its time comes, the R it
  counts in, the runner's recent moves) and the rules of editing; it can answer EDIT PLAN with concrete lever changes. The review
  page offers "Load into the plan editor": the change appears in the editor unsaved, the trader presses Review to see what it does
  and Save to apply it. Nothing is applied by the advisor.
- **Fixed on the way:** plan trades were described to the reviewer as "the EA banks at +1R", although the EA's own rules are off on
  plan rows.

## 2026-10-14 — Custom entry / stop / TP1 / TP2 on live plans, and armed "wait" plans (trader: "add an option to change the SL, entry, TP1/2. keep the signal's default in the box, but allow it to be changed")

- **What:** the signal page's plan card has a Levels box prefilled with the signal's levels; any can be changed before approving. A
  custom entry is a pending order at that price (limit or stop by the market; its own expiry, default 12 h). The EA sizes the trade at
  the custom entry and stop, so the risk stays the ruled 1R (CLAUDE.md 1% rule); the plan's R counts from the fill to the custom stop.
- **Wait:** a plan can wait "armed" for conditions (price touches, M15/H1/H4 closes beyond a price, a time, hours) met in order, with
  cancel rules and a time limit (default 48 h, max 120 h). Nothing is sent until they are met; then the approve goes out by itself. The
  runner keeps the signal open with delays meanwhile; a cancelled wait leaves the signal open for the trader.
- **Engineer's calls (not asked):** the EA's 1.0 R:R floor on a market entry stays (the page warns before approving); custom levels are
  not offered with Inverse (v1); levels and the wait are fixed once approved (edits stay lever-only).
- **Needs the EA update** (`plan_levels` on the signal JSON); signals published by an older EA refuse custom levels with a plain message.

## 2026-10-14 — The live advisor builds its own plan (trader: "the advisor on the live symbols should be able to create its own plan based on the market conditions. dont have it create it off the studies we've run")

- **What:** every live verdict ends with the advisor's plan (levels, wait, levers) built from the chart and today's market, not from
  study results or presets. Its "Changes my mind" points (where the stop or entry should really be, what to see first) are built into the
  plan; a WAIT verdict's plan waits for exactly its criteria and enters by itself once the trader arms it.
- **The trader stays the decider:** the plan shows under the verdict with "Load into the builder"; the trader reviews, changes and
  approves or arms it. The advisor never sends anything.
- **Role change:** the "doctrine - don't re-argue it / don't recommend moving a stop" section is now background (the EA's default
  management when no plan is used). Backup: training/advisor/CLAUDE.live.md.bak-20261014.
- **Also:** a 12 h average R pill on every pending signal and open position (home page and the signal / position pages).
