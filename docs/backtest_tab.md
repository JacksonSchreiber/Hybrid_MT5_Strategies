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
  - sessions (stratified draw, the six-month rule, nothing locked, sizes bloc 100 / majors 35);
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
  - A card holds at most 264 H4 / 44 D1 bars, and the chart pins both edges, so panning cannot reach further back than the window.
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
