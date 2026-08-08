# Phase 2 Enhancements — Engineering Directions

Requirements-level directions from the advisory review (2026-08-01). The implementing
engineer owns the detailed design; anything ambiguous here is yours to decide and record
in `decisions.md`. Items are ordered by recommended build order, not priority label.
Items B, C, and D are small and should land first — they unblock the coaching workflow
regardless of when A ships.

Scope guard: everything below is Phase-2 (tester harness) work. Nothing here touches
Phase-3 (live/VPS/Telegram). The existing hard safety rule stands unchanged: the dialog
and any order placement fire only under `MQL_TESTER && MQL_VISUAL_MODE && MQL_DLLS_ALLOWED`
(plus the headless `InpAutoApprove` path).

---

## A. Editable entry via pending orders

**Why.** The operator wants to nudge the entry on an alert based on discretion (e.g. wait
for a deeper pullback), with SL/TP following automatically. Entry is currently
display-only per the 2026-08-01 decision-log entry, because these signals fill at market
and an edited entry would mis-size the trade. This work reverses that deferral with the
pending-order approach that entry was deferred *for*. **Update the decision log** when
this ships — the "entry read-only" row must be superseded, not silently contradicted.

**Requirements.**

1. Entry becomes an editable field in the dialog. If untouched, behavior is exactly
   today's: market fill on Accept. If edited, Accept places a **pending order** at the
   edited price instead:
   - order type auto-selected from the edited price's relation to current market and
     trade direction (buy-limit / sell-limit on the favorable side, buy-stop / sell-stop
     on the breakout side). The dialog should display which type will be placed.
2. **Level-follow mode: rigid shift (default).** SL and TP move by the same delta as the
   entry edit. Risk distance, R:R, and lots are unchanged. This is the operator's
   requested behavior and the simpler build.
3. **Level-follow mode: structure-anchored (fast-follow, input toggle).** SL and TP stay
   pinned at the detector's structure levels; editing entry changes the risk distance, so
   lots and R:R recompute live (existing R:R/lots recompute machinery in the poll loop
   should extend naturally). Deeper entry → tighter risk → better R:R, which is the
   trading-correct model. Ship rigid-shift first; do not block on this.
4. **Guardrails** (reject the edit in the dialog, don't clamp silently):
   - edited entry may not cross SL, nor come within the existing
     `max(sl_buffer, min_stop_points)` floor of it;
   - edited entry may not invert the setup (e.g. a long entry moved above TP);
   - broker-side `SYMBOL_TRADE_STOPS_LEVEL` distance checks for the chosen pending type.
5. **Expiry.** Unfilled pending orders auto-cancel after `InpPendingExpiryBars` H4 bars
   (input, default 3). Journal the expiry (see schema below).
6. **Fill binding.** Bind the pending order's fill to the setup via `OnTradeTransaction`
   so the existing lifecycle (two-target scale-out, TP1 partial close, breakeven move,
   journal exit rows) works identically for pending-originated positions. This is the
   previously deferred work and the bulk of the effort. Note `OnTradeTransaction` does
   fire in the strategy tester; verify behavior under "every tick based on real ticks"
   with a scripted headless run before trusting it interactively.
7. **Journal schema additions** (see also D — coordinate as one schema change, bump any
   schema doc in `api-reference.md` and fix the stale table in `tester-harness.md`):
   - `orig_entry`, `orig_sl`, `orig_tp` — the detector's proposed levels, always written,
     so operator adjustments (entry, SL, or TP) are analyzable as orig-vs-final deltas;
   - `decision` gains values: `approved_pending` (edited entry, order placed) and
     `expired` (pending cancelled unfilled), alongside existing `approved`/`skipped`.
8. Two-target interaction: current behavior collapses an edited-TP plan to a single
   target. Keep whatever rule you have, but journal enough (`tp1`, `tp2`, `partial_frac`
   already exist) that the coach can tell a collapsed plan from a scale-out plan.

**Acceptance.** A visual-tester session where: an untouched signal fills at market as
today; an edited-entry signal places the correct pending type, chart lines sit at the
edited levels, the fill binds and scales out normally, and an unfilled one expires after
3 H4 bars — each producing a correct journal row. Headless `AA_ALL`/`AA_SKIP` runs are
byte-identical in behavior to today (auto modes never touch the entry-edit path).

## B. Skip-reason capture (small; highest coaching value — build first)

On Skip, capture a one-keystroke reason. Suggested codes (final wording yours; keep ≤6):

| Key | Reason |
|---|---|
| 1 | counter-trend / D1 context against |
| 2 | news or event risk in trade horizon |
| 3 | ugly structure / choppy level |
| 4 | target obstructed / pool too close |
| 5 | correlated exposure already open |
| 6 | gut / other |

Implementation freedom: extra buttons in `TradeDialog.c`, or digit keys handled in the
poll loop while the dialog is up — whichever is less invasive. New journal column
`skip_reason` (code int; 0 for approve rows and headless skips). Without this, the AI
coach can only critique outcomes (pure hindsight bias); with it, it critiques reasoning.

## C. Decision-time chart screenshot (small)

At the moment the dialog opens (after overlays are drawn), call `ChartScreenShot()` to
`MQL5\Files\journal\shots\<journal_stem>_<signal_id>.png` (same Common\Files journal
area; create the subdir). Suggested 1600×900. Lets a vision-capable coach see exactly
what the operator saw at decision time. Also fire it in headless runs only if free —
interactive is the requirement.

## D. Bug: `tp1_done` journal column

Every journal row writes the literal string `(non-string passed)` in `tp1_done` — a
`StringFormat`/`FileWrite` type mismatch in `WriteJournal()` (`HybridForwardTest.mq5`,
around lines 204/329/1078). Fix to a real `0/1`. Scale-out analysis (did TP1 bank before
the runner stopped out?) is impossible until this lands. Coordinate the schema doc
update with A.7.

## E. Session review script — `pipeline/review_session.py`

Python, stdlib-only preferred (csv/argparse), consistent with existing pipeline style.

```
./pipeline/review_session.py --user <user_journal.csv> --baseline <aa_all_journal.csv> [--out report.md]
```

- `--baseline` is an `AA_ALL` headless journal for the *same symbol and window*,
  generated via `./pipeline/mt5_verify.sh --mode ALL ...`. Join rows on
  `signal_time` + `strategy` (fall back to nearest-bar tolerance if ids differ between
  runs; document the join rule).
- Output: a markdown report card containing —
  - **Discretion alpha**: total R (user) minus total R (baseline), overall and
    per-strategy. Counts of approved/skipped/edited.
  - **Skip precision**: of the user's skips, % whose baseline outcome was a loss
    (good skips). List every *bad skip* (skipped winner) and *bad approve* (approved
    loser) with signal_time, strategy, skip_reason, baseline r_multiple.
  - **Edit delta**: for rows with orig_* ≠ final levels, realized R vs. the R the
    original levels would have produced (approximate from baseline row when entry
    unedited; mark as N/A when not computable — don't fabricate).
  - **Decision latency**: median / p90 `decision_ms`, and outcome split for
    fastest-quartile decisions (impulse-click detector).
  - **FTMO breach check**: walking the user journal's closed PnL by calendar day,
    flag any day breaching 5% daily loss ($1,250 on the 25k account) and any equity
    trough breaching 10% max drawdown ($2,500). Hypothetical — tester equity, but the
    habit signal is what matters. Read R, not raw PnL, everywhere else (custom-symbol
    tick_value caveat per `detectors-implementation.md`).
- Small sample honesty: print n for every percentage; suppress per-strategy stats
  under n=10 with a "insufficient sample" note rather than printing noise.

This report is the primary input to the operator's AI coaching loop, so keep the
format stable once shipped.

## F2. Blindness leak: calendar date in overlay signal labels (small — found 2026-08-07)

The training workflow includes a "blind advisor" that judges cropped screenshots with no
symbol/date knowledge. The detector's signal label prints the calendar date onto the chart
itself (e.g. `@ 2023.03.27` on a DeepFib label), which leaks timing straight through any
crop. Add an input (`InpBlindLabels`, default false) that suppresses dates in all
overlay-drawn label text (signal labels, and check econ-event line labels too) — bar-relative
or time-of-day-only text is fine. Logged in `training/advisor/notes.md` contamination log.

## F. Deferred (do not build now)

In-EA shadow tracking of skipped signals (virtual fills recording what a skip would have
done). Not needed: the AA_ALL baseline join in E yields the same information with zero
EA code. Revisit only if baseline/interactive runs ever diverge on signal generation.

---

## Documentation obligations

- `decisions.md`: supersede the 2026-08-01 "entry read-only" entry when A ships.
- `api-reference.md`: journal schema (new columns A.7 + B, fixed D) — and resolve the
  already-flagged stale schema table in `tester-harness.md` while you're there.
- `tester-harness.md`: entry-edit workflow, skip-reason keys, screenshot location.
- `tools-guide.md`: `review_session.py` usage.
