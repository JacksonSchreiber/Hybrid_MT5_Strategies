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
