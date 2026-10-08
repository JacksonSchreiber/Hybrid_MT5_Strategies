# Backtest tab - phase 2 (Practice mode), build notes (2026-10-04)

Built on branch `backtest-tab`, not deployed. The comparison is always the raw signal (trader ruling 2026-10-04).

## Pieces
- **Rule engine** (`live/backtest_engine.py`): one kernel in plain Python that numba can compile.
  - The offline precompute njit-compiles it (`pipeline/backtest_rules.py`), and the box runs the same source as plain Python.
    Both give bit-identical results (checked on 292 trades x 6 plans).
  - Box speed: median 1.5 ms per plan evaluation, p95 12 ms, max 69 ms.
  - Config schema `levers/v1`: entry %, TP2 on/off, the 200% cap (+ `trim_to_cap`), and a stack of lever cards: `bank`, `move`,
    `add_reach`, `add_bar`, `add_tp1`, `cut_avg`, `cut_unless`. Any card can be added any number of times.
  - Parity: it reproduces `pipeline/backtest_presets.py` exactly, max |diff| 0.0 on every preset, option and ladder, every trade.
  - Designed difference (`strict_tp2`, on by default): a bank above TP2, or an add at/above TP2, never fires.
    - These could only fire in TP2's own minute; the preset kernel lets them.
    - It changes 1-99 of 4,374 trades per menu entry, by 0.00005-0.00024R a trade on average.
- **Menu v1** (`pipeline/backtest_rules.py` -> `data/backtest/menu_v1.json`): the five presets and the group defaults as lever stacks.
  - Commodity bloc default = Minimal + "close at 1 h if the average R is negative". Trader-approved; the coach's approval of
    the trait is pending.
  - USD majors default = Minimal.
- **Box frames** (`data/backtest/frames_v1/`): the M1 frame per trade, 238 MB in 9 files (index.json + one .bin per symbol).
  - Each frame runs from the entry to the first -1R touch: every leg is closed by then, because no stop is ever set below -1R.
  - Capped at 40 trading days: 458 trades are cut there, and anything still open is closed at the last close.
  - Stored as int32 R x 1e6, delta-coded + zlib.
- **Results** (`data/backtest/results_v1.json.gz`, server-side only): per trade R under raw, the presets, the options and the
  default; banked +1R; hit TP2; and the post-signal H4 path for the reveal.
- **Info window** (`data/backtest/info_v1.json`): per group, Practice-only lines, each tagged `tested` or `observed`. Locked
  trades are excluded.
- **Server** (`live/backtest.py`):
  - sessions (stratified draw, the three-month rule, nothing locked). Sizes changed on 2026-10-06 (coach):
    - Practice is 25 signals for both groups;
    - Test is bloc 100 and majors 35 (the trader kept majors at 35), taken in blocks of 20;
  - blind card serving;
  - the decision is recorded BEFORE the reveal;
  - the per-signal CSV + session summary (`<root>/web/backtest/reports/`);
  - the advisor-recommendation hooks.
- **UI** (`live/static/backtest.html` + `backtest.js` + `backtest.css`):
  - Alpine.js + SortableJS + Shoelace, self-hosted in `live/static/vendor/` (MIT).
  - The builder: a sentence-style lever-card stack, a palette (an "+ Add lever" menu at phone width), drag to reorder, and
    presets that load a stack in one undoable step.
  - Undo/Redo, also Ctrl+Z / Ctrl+Shift+Z, with history per card.
  - A size meter vs the 200% cap with plain-words validation (Take is disabled when over the cap without trim).
  - The reveal: your plan vs raw vs default vs every preset, and the post-signal path with an exit marker.
  - **Bar-close wait** (trader 2026-10-06, `add_bar.wait_avg`, optional): at the bar close, wait while the average R is below X
    and add the first minute after the bar that it gets there. There's no deadline, so the add never goes in if the average never
    recovers. It's independent of the early add.
    - The older `hold_avg` only holds the EARLY add. It needs "go in early", and at the bar close the add goes in regardless; the UI
      now says so.
    - Why it was added: a Practice card (25% entry + 75% at bar 6, early at +2R, held below 1R) lost -0.70R. The early level was never
      reached, so the bar-6 add went in at about -0.4R and the stop took both pieces.
    - Unset, it is left out of the normalised plan. Every existing plan and the frozen menu v1 give identical results: old vs new
      engine, 4,374 trades x 34 plans, 0 differences.
    - It is a new lever option, not a menu change, and was added before any Test session.
  - **+/- button** on the fields that can be negative (stop-move level and trigger, the hold / wait / cut averages), because some
    phone number pads have no minus key.
  - **Risk cap on the bar add** (trader 2026-10-07, `add_bar.risk_cap`, R): when the add goes in (any of its fills: early, held,
    bar close, waiting), the open risk is kept at or below the cap.
    - Open risk = size x (price in - stop) summed over the open legs, at the price without the spread, so a full entry at the signal
      stop = 1R. Over the cap, every open leg's stop is raised to one level that brings it back to exactly the cap (a leg already
      above that level keeps its stop; never lowered). If that level would sit at or above the price, the add is skipped.
    - Example: 25% at the signal, the 75% goes in at +0.5R with the trade's stop -> 0.25 x 1 + 0.75 x 1.5 = 1.375R, so every stop
      goes to -0.625R (a stop-out then loses exactly 1R). An add at or below entry is never over 1R, so nothing moves.
    - On by default at 1R: a new bar-add card from the palette, and also the "slow" preset when it is loaded (the trader chose
      that). The frozen "slow" itself is unchanged: its menu results, R_presets and R_loaded are WITHOUT the cap, while the trader's
      plan JSON shows `risk_cap` and R_choice includes it. Advisor plans load exactly as the advisor wrote them.
    - Unset, it is left out of the normalised plan; the engine is identical without it (old vs new, every trade x every menu
      plan + the wait/hold plans, 0 differences). A second new lever option before any Test session; not a menu change.

## Advisor recommendation (`advisor-levers/v1`)
The advisor emits a fenced block:
```levers
{"schema": "advisor-levers/v1", "nearest_preset": "ruthless", "rationale": "one line",
 "plan": {"entry": {"pct": 100}, "tp2": true, "trim_to_cap": false, "levers": [ ...levers/v1 cards... ]}}
```
- **Validation:** schema, `nearest_preset` in the menu, the levers the engine knows, and the 200% cap. Anything else is
  rejected, with the reason logged to `web/backtest/advisor_rejects.log`.
- **Versions:** every version is kept per (session, card). v1 from the blind batch is flagged `blind_v1: true`, and later
  versions are notes like "v2 · updated after your question". Advisor-alone grading uses v1; the combined result uses the latest.
- **Report columns:** `adv_v1_stack`, `adv_nearest_preset`, `R_adv_v1`, `R_adv_nearest`, `R_adv_latest`, `adv_versions`,
  `adv_loaded`, `adv_final_unchanged`.
- **Visibility:** a per-mode flag, `backtest.advisor_slot`: practice `off` (default), test `after_commit`.
  - `after_commit` is the coach's two-step: `POST /bt/commit` stores the trader's first commit server-side and only then
    returns the advisor's plan.
  - Nothing from a session's decisions is ever written where an advisor bundle reads.
- **Dev only:** `POST /bt/dev/advisor` (needs `backtest.dev: true`) injects a recommendation for the current card. The real
  consult wiring comes in the advisor phase.

## Routes
- **Pages:** `GET /backtest` (the nav link only when `backtest.tab_visible` is true).
- **Reads:** `GET /api/bt/init`, `GET /api/bt/card?sid=`, `GET /api/bt/sessions`, `GET /bt/report/<sid>.csv`.
- **Writes:** `POST /bt/start` (group, mode), `/bt/decide` (sid, i, decision, plan JSON), `/bt/commit`, `/bt/adv_loaded`,
  `/bt/dev/advisor`. Always JSON.
- **Static:** `GET /static/backtest.js|.css` and `/static/vendor/**` (path-checked).

## Deploying (parent - nothing deployed yet)
- **Static files:** `deploy_live.sh` copies an explicit static list. Add `live/static/backtest.js`, `backtest.css`,
  `backtest.html`, and `live/static/vendor/` recursively (484 files, 2.7 MB).
- **Modules:** `live/backtest.py` and `live/backtest_engine.py` are covered by the existing `live/*.py` copy.
- **Box Python needs numpy:** no other live module uses it. Run `pip install numpy`, or port the evaluator off numpy.
- **Data upload (not web-served):** `<root>/backtest/` (or set `backtest.data_dir`) needs `split_v1.json`, `cards_v1.json.gz`,
  `results_v1.json.gz`, `menu_v1.json`, `info_v1.json` and `frames_v1/` (about 270 MB in total).
- **Config:** `"backtest": {"tab_visible": false, "dev": false, "advisor_slot": {"practice": "off", "test": "after_commit"}}`.

## Kernel limits
- **Within one minute:** an M1 bar has no intrabar order. Stops are checked first; an add and TP2, or a bank, in the same
  minute both count.
- **Fills:**
  - banks and TP2 fill exactly at the level;
  - level adds fill at the level or the minute's open (whichever is worse) + model spread;
  - stops fill at the stop or the open, whichever is worse;
  - bar and held adds fill at the minute close.
- **Costs:** model spread only (no commission, swap or slippage).
- **Frame end:** the box frames stop at the first -1R touch or 40 trading days.
- **Average R:** the EA's AvgRSince definition. The 1 h check uses the window from the entry-bar open; no M1 bar in that hour
  means no check.

## Review fixes + coach rulings (2026-10-04, after the adversarial review)
- **Events (cards_v2):** a blind card shows only the NEAREST high-impact event of class V and of class W, as a timing bucket (within 1 day /
  1-3 days / 3-7 days / 7-14 days).
  - v1's side / kind / exact hours let a naive Bayes name the bloc symbol 76% of the time (baseline 18%); class x bucket counts still
    gave 24-29%.
  - v2: bloc 21% (baseline 18%), majors at baseline, year at chance.
  - Cards without the calendar or a regime tag (144; they revealed 2012) are never served, in Practice or Test.
- **Sessions:** one symbol at most once per 91 days (coach: the longest history a card shows + a month; was 182). Any two signals of
  the group at least 15 days apart (>= the 60-bar reveal - the crosses share legs). A thin stratum fills from the neighbouring year
  (same direction + detector, nearest year first).
  - Planned Test sessions drawn back to back: bloc 2 x 100 = 200, majors 3 x 35 = 105.
  - A card holds at most 264 H4 / 44 D1 bars, and the chart pins the left edge, so panning cannot reach further back than the window.
  - To the right of the signal the chart pans into empty space (trader 2026-10-06), so the price-line labels need not cover the signal bar. A card opens with about 140 px of that space. Nothing is drawn there, because the server sends nothing after the signal.
- **Engine:**
  - A plan whose banks take out everything ends the trade that minute (kind 4 "banked out").
  - An add's stop "trade" = the ORIGINAL leg's current stop (moves on "all" or "original" legs count).
  - At most 20 levers per list (adds / take profit / stop moves / cuts) and 60 in all; every malformed plan is a plain-words 400.
  - Parity with the preset kernel is unchanged (0.0 on all 16 menu entries).
- **Server:**
  - /bt/* is always JSON (any exception -> 400; body > 64 KB -> 413); no filesystem paths in errors.
  - Skip is never blocked: an invalid plan on a skip is stored as null with the reason.
  - The first commit cannot be overwritten; /bt/adv_loaded only for the current card; a malformed advisor block lands in advisor_rejects.log.
  - The card payload carries no id before the decision.
- **Comparator (coach):**
  - The grading comparator (R_default) is Minimal for both groups.
  - The bloc's 1h cut is the trader's own plan, not an approved trait: it stays the preloaded plan on bloc cards, labelled "Your simple
    rules", and its info line is tagged "observed".
  - No live-stack column (pending).
- **Report:**
  - New columns: n_levers, loaded_preset (the preset the plan was loaded from when editing began = the "nearest preset" for grading),
    R_loaded_preset, and R_*_1pip (after a +1 pip round trip = gross units x 1 pip / stop pips).
  - Stop pips sit in the server-side results file only.
  - The summary carries model cost and the +1 pip figures.
  - The CSV's first line states the 40-trading-day frame cap.
- **Page:**
  - One keydown listener; Ctrl+Z inside a field is the field's own.
  - A blank or non-numeric entry snaps back to the plan's value.
  - SELL cards label every R in profit terms (SL -1.00R), with a legend.
  - The group select is set once its options are upgraded.
  - The sessions table scrolls inside its card at phone width.

## Test mode (phase 3, 2026-10-05; build only, nothing deployed)
- **Menu v1 is FROZEN** (`data/backtest/menu_v1.json` "frozen": at / by / banker "L1 thirds"). Protective banker = 25% at +1R / +2R /
  +3R, with the stop to entry / +1R / +2R. A Test session refuses to start without a frozen menu. A change is a new menu version and a
  new Test round (rounds are not pooled).
- **Sessions:**
  - drawn from the group's Test partition (bloc 100, majors 35), with the same sampler rules (91-day per symbol, 15-day group gap,
    neighbour-year fill);
  - taken in **blocks of 20** (coach 2026-10-06). After cards 20, 40, ... the session stops at a checkpoint (`/bt/next_block`
    starts the next block; leaving there and resuming later is fine):
    - at a checkpoint the trader sees the AGGREGATE grade only (trader 2026-10-08, overriding the coach's "nothing at a checkpoint"):
      trader alone / advisor alone / together for the block just finished, the session so far, and a by-block table (summary
      `grading.by_block`). No per-card result, chart or CSV row. Coach flag: knowing the running score can change how the later
      blocks are played (e.g. leaning on the advisor if it is ahead);
    - until the next block is started, the server refuses commits, decisions and advisor views for its first card;
    - bloc sessions are 5 blocks, majors sessions are 20 + 15;
  - every drawn id goes into `<root>/web/backtest/test_ledger.json` AT ONCE and is never drawn again ("one pass per signal ever");
  - a session cannot be restarted, only resumed.
- **Nothing per card is revealed until the last card** (only the block totals at checkpoints): no per-card result, no tally, the session list shows "hidden", and the CSV holds the
  header only. Then the full report and the grade.
- **Two steps on every Test card:**
  - `POST /bt/commit` records the first call (immutable). Only then the advisor's verdict, its v1 stack ("Advisor recommended", with
    its nearest preset) and follow-up questions (`POST /bt/ask`) appear.
  - The final goes through `POST /bt/decide`, which refuses a Test card without a commit.
  - `GET /api/bt/advisor?sid=&i=` is polled while the advisor works or answers.
  - If the advisor isn't ready, the page says so; the trader can wait or proceed, and the decision records `adv_available` = false.
- **The advisor batch** (`live/backtest_advisor.py`, run from `advisor_runner.tick` - no new service):
  - On Test start, queue files for the first 10 cards go in `<root>/web/backtest/advq/`, topped up to 10 ahead of the trader as
    cards are decided (review 2). Each holds the BLIND card only: no symbol, date, outcome, results, combo data or commit.
  - Initial consults run in session order, follow-ups first, `max_parallel` at a time (default 2). A failure gets one retry after
    60 s, then the card is marked failed. A crash is recovered from the leftover `.running` files.
  - **Role v3 (trader 2026-10-08): a plan made for each signal.** Under v2 the advisor put the same ruthless-like plan on 18 of
    its first 19 Test cards. v3 tells it to read the room on the card (swings / zone / EMAs against the stop, TP1, TP2; with the
    trend or not; the event bucket) and set each lever from it, to treat the presets as starting points (card.md now lists their
    exact levers), and to name the card feature behind each change in the rationale; `nearest_preset` is only a label.
    - The role never changes inside a block: a Test session started before v3 (no `adv_role` stamp) keeps v2 for its first block
      (`role_for`); each consult's role is in its status file and the CSV's `adv_role` column; `by_block` lists the roles per block.
    - The parent work-dir CLAUDE.md is now a pointer only; the role is in the card's folder and on stdin.
  - Work dir: `<advisor live_dir>/../backtest/`, holding a pointer CLAUDE.md, a deny-only `.claude/settings.json` (not load-bearing: `--restricted` ignores settings
    and the consult runs in a subfolder) and one
    folder per card, `bundles/<random token>/{CLAUDE.md, card.md, h4.png, d1.png, h4_bars.csv}`. The consult RUNS INSIDE that
    folder, and the prompt carries no session id, card number or path. The charts are rendered with `live/charts.py` from the
    blind bars in R, with the card's precomputed EMAs, the detector overlay and no dates; card.md lists the overlay in price-R and
    bars ago.
  - Tools: Read only, confined to the card's folder (`--restricted --tools Read --allowedTools "Read(./**)"`, every other tool
    in `--disallowedTools`, `--strict-mcp-config`, `--setting-sources project`). No web: a search on a blind card could reveal the
    date, the symbol or the outcome.
  - The answer: VERDICT / CONFIDENCE / WHY plus a levers block. v1 is stored as blind_v1 (rejected stacks are logged with the
    reason; the verdict still counts). Follow-ups resume the same consult session; a new levers block becomes v2, v3 ... and counts
    toward the combined result only.
  - Status: `<root>/web/backtest/advisor/<sid>/<hid>.status.json`.
  - Config: `backtest.advisor = {model, effort, timeout_s, max_parallel, max_turns}`. The defaults are the advisor's first model,
    medium effort, 600 s, 2 parallel and 30 turns.
- **The grade** (the session summary and the end page):
  - Three actors: the trader alone (the first commit), the advisor alone (its blind v1 verdict + v1 stack; a rejected v1 stack
    is graded as Minimal and the card stays in; cards without a v1 verdict are left out) and both together (the final).
  - Each is reported against Minimal (every signal taken with Minimal) and against raw, at model cost and +1 pip.
  - Selection = the take rate on signals that reached +1R minus the take rate on signals that went to the -1R stop without reaching
    +1R, with a two-proportion z.
  - Settings = the plan's R minus Minimal on the signals the actor took, with a paired t.
  - The advisor columns are recomputed at every report write and at the end, so a v1 that arrived after the trader's final still
    counts.
- **New CSV columns:** R_first, R_first_1pip, adv_verdict, adv_confidence, R_adv_alone, R_adv_alone_1pip, adv_state,
  adv_available_at_final, R_minimal, R_minimal_1pip, reached_1R, went_to_stop.
- **Dev:** with `backtest.dev` true, `/bt/start` takes `n` (a smaller session, for testing).

### Deploy notes (phase 3 additions)
- **Code:** the new module `live/backtest_advisor.py` is covered by the `live/*.py` copy. `live/advisor_runner.py` gains the
  three-line poll hook. `live/charts.py` gains an optional `emas_in=` (backward compatible).
- **Static:** `backtest.html`, `.js` and `.css` changed. Icons: only vendored ones are used (shield-check for the locked first call).
- **Data:** upload the updated `data/backtest/menu_v1.json` (frozen). The rest is unchanged from phase 2: cards_v2 (or v3 from the
  overlay port), results_v1, info_v1, split_v1, salt.txt and frames_v1.
- **Config:** `backtest.advisor_slot.test = "after_commit"` (the default), optionally a `backtest.advisor` block.
- **Box:** the claude CLI must accept `--resume` (follow-ups). The advisor runner creates the work dir on its first poll.
- **Box (review 2):** the consult flags are `--restricted --tools Read --allowedTools "Read(./**)" --disallowedTools ...
  --strict-mcp-config --setting-sources project`. Locally (CLI 2.1.289, against a fake API) Read inside the bundle worked. An
  absolute path outside it, `../`, a traversal path, Glob, WebFetch and Bash were all refused; only Read was offered to the model.
  **After deploy,** run one real consult on the box (CLI 2.1.281) and check that a read of `../../` is refused.
- **The ledger starts empty on the box.** The local test ledger lives only under the scratchpad root.

## Review 2 fixes (2026-10-05; build only, nothing deployed)
- **Locks:** the store loads before the session lock is taken (`card`, `report_csv`); the lock is re-entrant. The webapp and the
  advisor runner are separate processes, so per-card state uses lock files (`<status>.lock`, stale after 30 s).
- **Advisor order:** `/bt/ask` is refused until the card's advisor view is done. `blind_v1` can only come from the blind batch.
  Follow-ups: at most 3 waiting, 5 per card, 50 per session. One consult per card at a time; the thread merges under the card lock
  and is numbered.
- **Cost:** about 10 initial consults ahead of the trader instead of the whole session at once. The Test start screen shows a cost
  note.
- **Commit rules:** a Take first call needs a valid plan. A Skip with an over-cap plan still commits: the plan is stored as null
  with `plan_invalid`.
- **Final buttons:** locked while the advisor works, until "Proceed without it".
- **Presets:** "Advisor v1 (its blind first plan)" stays loadable after a follow-up replaces the recommendation.
- **Speed:** plan evaluations are cached (key: store, card, plan). Decide stays around 0.01-0.04 s at any session length.
- **Overlays:** computed from bars truncated at the signal bar (`Bars.upto`). `check_card` asserts this; a deliberately leaky
  build fails. The rebuilt cards are identical to cards_v3.
- **Charts:** blind charts keep zero and negative R levels (the range, levels, zones and fibs). Fib labels have no " SL". A
  one-point zone is drawn back to the previous bar.
- **Practice/Test reveal guard:**
  - a Test draw skips Test signals in the 14 days after a SERVED Practice card of the same symbol;
  - while a Test session is open, Practice skips cards from 14 days before to 60 days after its undecided Test signals (same
    symbol).
- **Dev:** `n` must be a whole number of at least 1.

### Coach note - Practice reveals vs the Test partition (numbers 2026-10-05)
The split is random per trade, so Practice and Test signals interleave in time. **Measured after the guard,** with 3 served Practice
sessions and then the planned Test sessions:
- **Same symbol, reveal direction:** 0 drawn Test signals (was up to 68%).
- **Any group symbol in the 14 days before a Test signal:** bloc 59-62%, majors 17-22% (two runs). The crosses share legs.
- **History direction** (a later served Practice card's 60-day chart shows a Test signal's aftermath, same symbol): bloc 54%,
  majors 73%. The charts are in R with no dates, so using this needs shape-matching across cards.
- **Guard (b) check:** with a Test session open, a fresh Practice draw had 0 same-symbol cards near its undecided signals.

**Test pool left under wider guards.** Eligible pool: bloc 988, majors 263. The Practice sessions are a random draw,
so a rerun moves these counts by a few percent.

| Rule | Bloc after 1 / 3 / 6 Practice sessions | Majors after 1 / 3 / 6 |
|---|---|---|
| Same symbol, 0..+14 days (shipped) | 944 / 870 / 759 | 243 / 212 / 185 |
| Same symbol, -60..+14 | 727 / 439 / 202 | 165 / 83 / 48 |
| Any group symbol, 0..+14 | 698 / 381 / 142 | 220 / 180 / 137 |
| Any group symbol, -60..+14 | 95 / 6 / 0 | 107 / 45 / 31 |

A random split cannot close the group-wide or history exposure without emptying the Test pool. **The structural answer is a
time-blocked split.** Practice drops 14 days before and 60 days after each Test block. Session counts are full sessions back to back
under the 91-day / 15-day rules:

| Design | Bloc | Majors |
|---|---|---|
| Current random split | Test 1040. Sessions of 100: 7 / of 70: 9 / of 50: 12 | Test 272. Of 35: 4 / of 25: 7 / of 20: 9 |
| Test = Sep-Dec every year | Test 1110, buffer 724. Largest session ~69. Of 70: 6 / of 50: 15 | Test 274, buffer 185. Largest 24. Of 20: 7 / of 15: 11 |
| Test = even years | Test 1746, buffer 326. Of 100: 8 / of 70: 17 / of 50: 25. Practice draws 86 of 100 | Test 455, buffer 79. Largest 31. Of 25: 8 / of 20: 14. Practice draws 24 of 35 |

- **Sep-Dec** keeps Practice whole but caps the bloc at ~70 and the majors at ~20 per session, and tilts Test to one season.
- **Alternate years** keeps bloc sessions of 100 but thins Practice, which is the majors' binding constraint.
- **Not re-cut.** The split stays split_v1 until the coach rules.

Scripts: `pipeline/backtest_split_guard_check.py` (the exposure left), `pipeline/backtest_split_guard_cost.py` (the pool table),
`pipeline/backtest_split_blocks.py` (the block designs).
