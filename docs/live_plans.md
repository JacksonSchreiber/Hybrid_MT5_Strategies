# Live custom plans (trader 2026-10-12)

The live signal page is the Practice card: the trader builds a lever plan (the backtest tab's "levers/v1" config) and approves.
The EA only executes primitives; the **plan runner** (live/plan_live.py, a thread in the monitor) runs the SAME rule engine as
Practice (live/backtest_engine.py semantics, verified by a parity test) on the live M1 path and issues the primitives as tasks.

## Why this split
- One source of truth for lever semantics: the Practice kernel. The live stepper is checked against `kern` on the box frames.
- The EA stays generic: no lever logic in MQL5, so the gate chain checks only plumbing.
- Every leg always carries a broker-side stop (and TP2 when the plan keeps TP2), so a runner outage never leaves a leg unprotected:
  it only delays banks / adds / cuts / stop moves.

## Task interface (EA, live only)

All tasks use the existing envelope (`schema_version`, `task_id`, `symbol`, `verb`, `params`, `issued_at`, `issued_by`).
The EA's flat parser sees params as `params.<name>`; values are numbers or strings.

### approve (existing verb, new optional params)
| param | values | meaning |
|---|---|---|
| `plan` | `1` | the row is PLAN-MANAGED (see below) |
| `entry_pct` | 1-100 (default 100) | first position = ruled lots x pct/100, floored to the volume step, at least the min lot |
| `tp2` | `1` (default) / `0` | `0`: the order has NO broker TP (the plan runs until a stop, a cut or a manual close) |
| `inverse` | `1` | trade the opposite direction: direction flipped, SL / TP / TP1 / TP2 mirrored round the parked candidate's entry (`2*entry - level`) BEFORE the market validation and sizing |
| `lv_entry` `lv_sl` `lv_tp1` `lv_tp2` | prices (2026-10-14) | the plan's own levels replace the card's BEFORE the market validation and the sizing (`SizeByRisk(entry, sl)`: 1R = the ruled risk at the custom stop). `lv_entry` = a pending order there (limit or stop against the market; `entry_mode` forced to pending). Sides checked (`bad_params:lv_sl_side` / `lv_tp_side`); refused with `inverse` (`bad_params:levels_with_inverse`). `lv_tp1` never turns a one-target card into a split (journal only - the EA's management is off on plan rows) |
| `pend_bars` | 1-60 | the custom-entry pending order's expiry in H4 bars (row field `pend_bars`, persisted; 0 = `InpPendingExpiryBars`) |

The signal JSON of an EA with these params carries `"plan_levels": true`; the page and `plan_live.approve` refuse custom levels on a
signal without it (an older EA would ignore them and size at the card's stop).

Plan rows: `entry_mode` = `plan_` + the usual mode (`plan_market`, `plan_market_now`, `plan_pending_frozen`), with `_inv`
appended for an inverse (`plan_market_inv`); `st_full_lots` = the ruled full lots (100%); `st_tranche` 0. Staging never
applies to a plan row. The positions JSON carries `entry_mode` for every row.

### plan_add (new; target `position_id` = the plan's first leg)
params: `lots`, `sl` (price), `tp` (price, 0 = none), `leg` (int, informational).
Gates (refusal = rejected ack with the reason): the target is a plan row of this symbol; some leg of that signal is still open;
kill switch on; spread gate (`max_spread_r` against the first leg's 1R); lots floored to the step and at least the min lot;
price not through `sl` (BUY bid > sl, SELL ask < sl) and the stops level respected; TP on the right side or 0; FTMO headroom
(`FtmoHeadroomOK`); **1R cap**: the signal's open risk to stop after the add (sum over its open legs of lots x |price in - current SL|
in money, counting only stops on the losing side of the entry, + the new leg's) must be at most the ruled 1R (full lots x |first
fill - original stop|) x 1.05 - else `risk_over_1R` (the CLAUDE.md 1% rule; the plan's risk cap normally makes room first).
On success: a new row (a copy of the first leg's row, like the staged add) with `st_tranche` 5, `entry_mode` `plan_add`, its own
posid / lots / fill; the ack's `refs` describe the NEW row (posid, lots, entry, sl, tp).

### set_sl (new; target `position_id`, any leg of a plan)
params: `sl` (price), `loosen` (`1` allows moving the stop away from the price).
Refused when: not a plan row; the position is closed; the stop is through the price or inside the stops level
(`sl_through_price` - the runner then closes the leg); it loosens without `loosen=1` (`would_loosen`); it is beyond the row's
original signal stop (`beyond_signal_stop`). TP unchanged. The EA's own external-edit detector must not count it as an external edit.

### close_part (new; target `position_id`, any leg of a plan)
params: `lots`. Floored to the step; below the min lot -> `below_min_lot`; leaving less than the min lot -> the whole leg closes.
Bookkeeping like `close50` (closed volume, action log tag `PLAN_BANK`).

### close (existing) with `params.why`
`why` starting with `plan` -> the action log tag is `PLAN_CLOSE` instead of the manual tag (the runner's cuts and exits).

### The EA's own management is OFF for plan rows
The +1R bank / SL to BE (ManageOpenPositions), the pyramid, the shorts stop raise, the bloc bar-N cut and +1R lock, the staged add,
the day-2 rules, the min-lot split guard. Still ON: the broker stop / TP, the weekend flatten (BTCUSD), the kill switch, FTMO checks,
the Inverse signal offer (unchanged).

## Plan record (written by the web app at approve)
`<root>/plans/<signal_key>.json`: the validated plan, the signal levels (mirrored when inverse), direction, card entry and 1R, the
approve task id, then the runner's state: legs (leg index -> posid), executed one-shot actions (banks, adds, exit), pending tasks,
and a human-readable log the position page shows.

## Runner semantics (Practice parity)
- Prices in the POSITION's R (trader 2026-10-13, record key `rbase: "fill"`): R(price) = d x (price - first fill) /
  |first fill - signal SL| - the EA's open R and sizing basis. Exit side = bid for a BUY, ask for a SELL. So "+1R", "entry"
  (0R), the trail distance, cut thresholds and banks all count from where the trade got in, and the -1R floor is the broker's
  original stop. Before 2026-10-13 the replay used the card's R (origin the card entry); a late fill then started the replay at,
  say, +0.91R and a "stop to entry when +0.5R" lever fired at the fill (EURCAD #3, NZDCHF #6 closed within seconds). Records
  without `rbase` (approved before the change) keep the card's R until they finish.
- The path starts at the first leg's fill minute (M1 bars from the terminal, with each bar's own spread); the current minute is
  included for level triggers only (time-based checks wait for the minute to close).
- Times: "H hours" = H4 bar H // 4 after the ENTRY-BAR open (the signal bar's close, same grid as Practice), then H % 4 hours into
  it; "bar N close" the same. A time that passed before the fill never fires. The trail counts every N trading hours from the fill.
- One-shot actions (a bank, an add, a cut / exit) execute once, when the stepper first shows them; stops follow the stepper's
  current level for each leg every few seconds (tighten only, or both ways with the trail's `lower`, never past the signal stop).
- Live differences from Practice: fills at market after a poll (seconds), lot-step rounding (a 25% bank of 0.03 lots is 0.01),
  the 1R cap on adds, entry at most 100%.
- A leg missing from the terminal's position list is closed only when the EA agrees - it moves a closed position's file to
  `positions/closed/` - or after `FEED_MISS_S` (30 min, a chart whose EA died). Until then that poll decides nothing. Right after an
  MT5 restart the terminal lists no positions until the account loads: on 2026-10-15 (the EA update) that empty list marked the running
  NZDUSD #7 and USDNOK #7 plans finished while both trades were open; the two records were set back to `running` by hand (backups
  `*.json.bak-restart-20261015` on the box) and the rule above was added.

## As built (2026-10-12)
- EA reasons may carry a suffix: `bad_params:entry_pct`, `below_min_lot:<asked>`, `risk_over_1R:<x>R`, `spread_too_wide:...`,
  `order_failed:<retcode>`; `set_sl` within half a tick answers `ok_unchanged`; `plan_add` may answer `ok_unconfirmed` (posid not bound).
- Ack `refs.sl` is the row's ORIGINAL stop (the R basis); the live stop / size are `refs.pos_sl` / `refs.pos_volume`.
- Each plan add has its own state file `<id>_t5_<posid>.json`; plan rows never offer an Inverse.
- The trader's own buttons still work on plan legs (Close, Close 50%, SL to BE, ratchet); the runner follows the account (a tighter
  manual stop is kept - except under a trail with `lower`, which sets the trail's level both ways).
- The runner refuses to manage a first leg the EA did not open as a plan row (an EA build without plans): it is left to the EA's rules.
- Page: `live/static/signal_plan.html` + `signal_plan.js` (Alpine; the Practice markup and edits), `webapp.signal_plan_layout`,
  `POST /signal_plan` (validates, writes the plan record + approve task, waits 12 s for the ack), `POST /plan_stop`, the plan panel on
  the signal and position pages. Tests: `pipeline/plan_live_parity.py`, `pipeline/plan_live_sim.py`.

## Editing a running plan (trader 2026-10-13)
"on open trades, i'd like to be able to add stuff/edit/delete stuff to the plan" - the position page shows the plan builder
(`signal_plan.html` in mode `edit`) under a running plan: the current levers with their ids and state pills (done / holding /
time passed). **Review the change** posts `POST /plan_edit preview=1`: a dry run of the runner's next poll with the edit applied now
(`plan_live.preview_edit`, the same `actions()` code) - its plain-words changes, what the runner would send at once (a stop move, a
close, a bank, an add) and warnings (a time already passed). **Save the change** posts `preview=0`: the web app drops
`plans/edits/<key>.json` and waits up to 12 s; the runner (the record's only writer) applies it at the top of its next step
(`apply_edit` -> `build_edit` / `commit_edit`) and acknowledges it in `edit_last`.

- **Versions:** the record keeps `versions` [{plan, ids, from_t, rev, about}], `lever_ids` (aligned with `plan.levers`) and `rev`.
  The replay (`plan_live.replay`) runs version 0 from the fill and each later version from the first minute at or after its
  `from_t` (the minute of the save), handing over the end state (`handoff`: legs, sizes, stops, totals; each lever's flags by id;
  the cuts' history recomputed from the path). `kern_state(..., j0, j1, init)` runs one segment; j0=0 / no init is kern exactly.
- **Rules:** an unchanged lever keeps its id and what it did; a changed lever gets a new id and starts fresh from the edit minute
  (a level touched before does not count); a removed lever leaves its effects (a stop never moves back, except a trail's `lower`);
  a time that passed never fires; "average R since entry" and "+X R reached" look at the whole trade; a new or changed trail runs
  one check at the edit minute, then every N hours from the fill as before.
- **Fixed after entry:** the entry %, TP2 on/off (`set_sl` never touches the TP) and the size cap. Adds stay under the 1R cap.
- **Executed banks** are lever ids in `rec["banks"]` (older records' compile-order indices are converted on load, `norm_banks`).
- **Checks:** `pipeline/plan_live_parity.py` (kernel = kern, plus every plan split into three identical versions = unsplit, bit for
  bit); `pipeline/plan_live_edit_check.py` (scenarios: trail tightened, cuts added in the future / past, triggers touched before the
  edit, a fired bank kept / deleted, id bookkeeping, the runner applying a request, the preview's actions).
- **The positions reviewer (2026-10-13)** knows plan trades: its bundle (`positions_review.build_bundle` -> `plan_live.plan_brief`) says
  the plan runner manages them (the EA's rules are off), which R the plan counts in, the plan clock, and every lever with its id,
  state and timing; its role explains edits and their rules. It answers `edit_plan` with `{changes, remove: [id], change: [{id,
  field}], add: [lever]}`, validated against the running plan (`plan_live.apply_suggestion`); the review page's "Load into the plan
  editor" opens the position page with `?suggest=<review>` - the suggestion loads as an unsaved change (Undo goes back), and the
  trader reviews and saves as with any edit.

## Custom levels, armed plans and the advisor's own plan (trader 2026-10-14)
"add an option to change the SL, entry, TP1/2. keep the signal's default in the box, but allow it to be changed. also add an indicator
showing the average R in the past 12 hours for each symbol" and "the advisor on the live symbols should be able to create its own plan
based on the market conditions ... especially if the advisor's ruling is a 'wait', it should have a plan that executes on its wait criteria".

- **Levels box** (signal page, pre-approve only): entry / stop / TP1 / TP2 prefilled with the signal's, each editable with a reset; it
  shows 1R, each target in R and the size at the same 1% risk (the EA computes the exact lots). The chart's lines move with them. The
  plan carries only the changed levels (`plan.levels`, `entry_h` = the pending order's hours); `plan_live.validate_levels` checks the
  sides; the record's `card` takes them, so the R frame (`rbase`: fill to YOUR stop), TP1 / TP2 levers and the 1R cap all count from
  them. A custom entry's lever hours count from the H4 bar it fills in. Not with Inverse.
- **Wait** (`plan.arm` = `{when: [...], cancel: [...]}`): conditions on the bid (the chart's price) - `touch` above/below a price, a
  `close` (M15 / H1 / H4 bar, bucketed from M1) above/below a price, `after` a UTC time, `hours` after the previous step. `when` steps are
  met IN ORDER, each from the minute the previous one was met; any `cancel` rule ends the wait (nothing sent); a wait always has a time
  limit (none given: 48 h; at most 120 h). The record starts `armed`: no approve task. The runner (`Runner.armed`) checks every poll;
  when the last step is met it re-reads the record (a Disarm from the page wins), re-anchors the lever hours to the current H4 bar and
  sends the approve (`send_approve`, issued_by plan, a Telegram line). While armed it keeps the signal open with an explicit `delay`
  when the next bar close would expire it (implicit_streak >= max_age - 1; one per H4 bar). The signal still dies if the price trades
  through the CARD's stop at a bar close (the EA's parked-signal rule) - the armed plan then ends `cancelled`. Cancelled / ended waits
  leave the signal open for the trader. A market entry after a wait meets the EA's R:R floor (1.0 to TP2 from the fill) - the page says so.
- **Disarm:** `POST /plan_stop` on an armed record -> `stopped`, nothing sent. The home page shows `ARMED · waiting k/n`; the signal page
  shows the armed panel above the builder; a second approve is refused until it is disarmed.
- **12 h average:** `positions_review.avg12_r` = the mean of the last 720 M1 closes (12 trading hours, the 12 h average cut's window)
  against an entry and 1R, signed by the direction - a position from its fill to its original stop (since its entry when younger than
  12 h), a pending signal from its entry to its stop. Pills on the home page (signals and positions), the signal and position pages
  (cached 30 s).
- **The advisor's own plan** (CLAUDE.live.md "Your plan", the consult prompt asks for it): after `Changes my mind:` a fenced `plan`
  block, schema `live-advisor-plan/v1` {rationale, levels, wait, entry, tp2, levers}, built from the chart and today's market - not
  from the studies, presets or the record. Its Changes-my-mind points become levels / wait steps; a WAIT verdict's plan carries its wait
  criteria as `wait`. `verdict_fmt.parse` lifts the block out (`plan_text`); `plan_live.advisor_plan` validates it exactly as an approve
  (levers, level sides, the wait) and the advisor card shows it in words with "Load into the builder" (a window event the builder takes
  as an unsaved plan). The trader still approves or arms it; nothing is sent by the advisor. The role's old "don't recommend moving a
  stop" doctrine is now background ("the EA's default management"). The role also maps the usual Changes-my-mind phrasings to steps
  ("…that holds for a bar" = a second close; "a retest" = the close, then a touch back; "a rejection wick off X" = a touch, then a close
  back on your side; "a close back inside kills it" = a cancel close); what the wait cannot check (spread, session, volume, a gap, news
  content) stays in words. Checked 2026-10-14 against the last 42 live Changes-my-mind lines (most are "an H4 close beyond X before a
  time") and one consult run off-box on the AUDUSD #5 bundle (a SKIP with an armed plan: after the AUD jobs release, an H1 close above
  the lower high; the server accepted it as is).
- **Old EA builds:** levels need the signal's `plan_levels: true` (written by the 2026-10-14 build at every publish / re-present); on a
  signal without it the Levels box is greyed, an approve with levels is refused, and "Load into the builder" leaves the advisor's levels
  out and names them (the wait and levers still load).
- **Deployed:** Python, page and role 2026-10-14 21:17 UTC; the EA (`HybridForwardTest-live.ex5`, md5 db51f46d…) 2026-10-15 03:50 UTC,
  installed with MT5 down (`C:/ProgramData/hybrid/ea_update_20261014.ps1`; the previous build is `C:/ProgramData/hybrid/ea_backup/
  HybridForwardTest-live.ex5.20261012`). Box file backups: `*.bak-20261014-levels`, `advisor/live/advisor/CLAUDE.md.bak-20261014`.
- **Checks:** `pipeline/plan_live_levels_check.py` (levels, conditions, approve params, armed runner on a fake feed: steps in order,
  the keep-alive delay, the trigger, cancels; the advisor block; L10 a custom-entry order that expires ends its plan; L11 a leg the
  position list misses while the EA still lists it open keeps its plan running). EA self-test (from signal 17 on, after the plan and inverse scenarios):
  a stop on the wrong side refused `bad_params:lv_sl_side`; a tighter custom stop + TP2 at 3R lands on the row and the broker and the
  full lots are sized at the custom stop (`plan_lv_check`); a custom entry halfway to the stop with `pend_bars` 1 rests as a
  `plan_pending_frozen` order and fills (cut) or expires after one bar (`plan_lvpend_check`). `live_queue_check.py` validates them.
