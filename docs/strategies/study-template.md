# Study template — required sections and pre-flight checks

Every study/feasibility run in this program follows this shape. The coach writes the spec; the engineer runs it once and
reports. Added 2026-09-17 after the BTCUSD study found that `.dk` data carries no spread (coach item 3).

## Pre-flight checks (all mandatory, reported in the deliverable)

1. **Zero-spread check.** `.dk` imports fill every market order AT the detector's level, i.e. the tester ran mid-price.
   Run `python3 pipeline/cost_journal.py <journal.csv> --json` and report `zero_spread_source` / `max_fill_minus_level`.
   If true, the spread MUST be charged explicitly: per-symbol discounts in `data/study/spread_drag.md`
   (scaled column), or a fresh live sample from the shadow box for a symbol not in that table.
2. **Other real costs.** Swaps (per the broker's published figures, with the date they were read) and commission, via
   `config/symbol_rules.json` → `<SYMBOL>.costs` and the same costing script. Report every cell costs-off AND costs-on;
   judge the gate on costs-on.
3. **Session reality.** Dump the broker's actual quoting days for the symbol on the shadow box (bar coverage by weekday
   over ≥28 days — see `data/study/broker_sessions.md` for the method) and state whether the research data's sessions
   match. Filter the research window to the broker's real sessions when they differ, so the trader never trains on
   setups they could not have taken.
4. **Data provenance.** Import source and exact range, effective start after the regime warm-up, and any coverage gaps.
5. **Contract spec.** Contract size, min lot and lot step, and how many signals the min-lot gate suppresses at the
   account size being modelled.

## Report sections

Question · what is frozen · data · costs used (with source + date read) · cells (fleet, per detector, per regime tag,
per date half, per decision class; each with n, avg R, win %, bank %, TP %, max drawdown in R, cadence/yr) ·
the pre-registered gate with a PASS/FAIL line per criterion · ten largest winners and losers · rulings applied ·
what the study could NOT model.

## Deliverables

`data/study/<name>/report.md`, the equity curves (costs-on and costs-off), the costed trade list, the import log, and
the analysis script committed under `pipeline/`. Nothing touches the VPS.
