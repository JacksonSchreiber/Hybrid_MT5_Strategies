# Engineer directions — EMArev INVERSE as a live tester option (trader-ruled, 2026-09-05)

**Authority:** trader override, on record in `training/coach-log.md`. The coach's
objection is logged; this document implements the trader's ruling faithfully.
**Scope:** interactive tester only. Ships whenever ready — L7 sessions may run with
or without it; windows played before it ships simply have no inverse cohort.

## What the trader wants

When an **EMArev** alert fires, the approve dialog offers a third choice beyond
Approve(fade)/Skip: **INVERSE** — open a real tester position in the *stretch*
direction (with the move, against the fade). Once open, the trader can manage it:
**close early, keep it open longer, or close 50%** — the existing manual-action
pathway (actions.csv already logs CLOSE50 / SL_BE etc.) should cover this; add plain
CLOSE if missing.

## Trade template (from the backtested E1·X2 geometry — the trader manages from there)

- Entry: market at decision time, stretch direction. Size: standard 1% fixed-capital.
- SL: EMA20 ± README buffer, capped at 2.5×ATR_H4 from entry (floor 1.0×ATR_H4).
- No TP by default. **Auto-close at 12 H4 bars** (the 2-day time exit) *unless* the
  trader extends — an EXTEND action cancels the auto-close (that is the "keep open
  longer" option). CLOSE / CLOSE50 available any time.
- Calendar: the Option-A gates apply at entry (no arm on fwd-V ≤ 6h; W-in-hold → the
  dialog should warn but the trader may proceed — it is ungraded; log the warning).

## Integrity requirements (non-negotiable — this is what makes "ignore it for the exam" true)

1. **Separate journal.** Inverse trades write to `<journal>.inv.csv` (same schema,
   strategy=`EMArevINV`), never to the graded journal. Manual actions keyed by posid
   as today.
2. **Separate magic number.**
3. **No interference with the graded stream:** an open inverse must NOT hold the
   one-setup-per-symbol lock (subsequent detector signals fire normally) and must
   NOT consume the 3% concurrent-risk alert-suppression cap. Max **one inverse open
   at a time** (hard).
4. **Sizing of graded trades unaffected** (already true — fixed-capital sizing).
5. **Capture daemon:** fire the blind bundle for inverse decisions too if cheap;
   otherwise skip — advisor consults on inverse are the trader's option, not required.
6. `InpUseEmaRevInv`-style flag naming is already taken by the backtest detector —
   name this one `InpOfferInverse`, default **false**; `start_level.sh` sets it true
   only when the trader launches with `--inverse` (new flag, default off).

## Grading contract (coach side, for the record)

- Inverse cohort is **excluded from all graded bars** (alpha, precision, violations,
  FTMO bars) — reported separately: n, W/L, R, management actions vs the unmanaged
  E1·X2 counterfactual (the frozen `backtest_emarev_inv.py` computes the unmanaged
  outcome per signal — keep it runnable per window).
- The coach's separate FTMO *awareness* note will sum both cohorts (real account
  survival counts everything), but it does not gate the exam.
- Discrimination bars (unchanged from the eye-journal protocol, now fed by real
  selections): TAKE subset avg R ≥ +0.25 at n ≥ 20 and above the −0.07R pool;
  management adds value when managed R beats unmanaged over n ≥ 15 actions.

## Also in this ruling

L7 keeps its bars and still gates the Phase-3 sign-off, but is no longer framed as
terminal: the trader has opened the door to L8+ windows (unused inventory: US500
2022, US30 backfill, the .sim bardump symbols, plus any future QDM imports). No L8
windows are ruled yet.
