# Overnight 2026-10-04 (trader asleep) - what was done

## 1. Commodity bloc: plain T+L1.0, live in the EA
- **Rules,** on AUDUSD, NZDUSD, USDCAD, AUDNZD, AUDCAD and NZDCAD:
  - at the 6th H4 bar close, every ticket is closed unless the bid reached +1.5R (from the card entry);
  - at the +1.5R pyramid trigger (after the bank), every stop moves to +1R, longs and shorts. On these symbols this replaces the shorts-only +0.25R raise.
  - Early promote: dropped.
- **Config:**
  - `provisioning/live.json`: `bloc_tl_symbols = "AUDUSD,NZDUSD,USDCAD,AUDNZD,AUDCAD,NZDCAD"`, `bloc_tl_from = "2026.10.04 00:00"` (broker time).
  - **Rollback, no MT5 restart:** set `bloc_tl_symbols` to `""` and run `provisioning/deploy_live.sh --config`.
- **Not touched:**
  - **Trades signalled before 2026-10-04,** which keep the standard management: open AUDNZD #1 (two tickets) and USDCAD #1.
  - **Late or delayed approvals and re-offers,** the same exclusion as the study (QueueRuleRow).
  - **The two parked bloc signals, NZDCAD #6 and NZDUSD #2,** are outside the EA's bloc rules if approved; manage them by hand.
- **Gates:**
  - build 0 errors / 0 warnings;
  - tester parity identical vs the baseline on both builds;
  - live self-test + validator PASS;
  - `BLOC=1` self-tests exercised both BLOC_LOCK (stop to +1R) and BLOC_CUT (closed at the bar-N close, staged add skipped).
- **Deploy:** `deploy --ea` 07:37Z (markets closed). 47/47 EA instances alive on the new build, the bloc rules on exactly the six charts, 9 parked signals and 21 positions intact, no id collisions.
- **Telegram:** new alerts "BLOC 24h CUT" and "BLOC LOCK".
- **Fix found on the way:** the item-25 shorts stop raise didn't hold the EA's external-edit detector during its own modify, so it could log the move as an EXTERNAL_SL. It now holds it.
- **EA vs study reproduction (AUDUSD 2018-26, real ticks):** RESULT_PENDING

## 2. Group labels on every trade screen
- **Where:** every symbol shows a colour-coded group pill (single source: `live/symbol_groups.py`):
  - dashboard pending signals, positions, instances and recent signals;
  - signal pages, position pages, pending orders and the journal.
- **Strategy box:** each signal and position shows its group's brief strategy. Commodity-bloc ones add your advice: "Slow commodity-bloc trades are dead trades..."
- **Position cards:** bloc positions show "24h CUT in Xh unless +1.5R", "STOP +1R (bloc lock)" and "passed the 24h check".
- **Telegram:** new-signal messages carry the group, plus the advice for bloc signals.

## 3. Advisor
- **Role file** (`CLAUDE.live.md`, on the box): a Commodity bloc section.
  - Overrides: no TAKE default (A-grade bar only), and backs cutting slow or negative trades inside day 1.
  - Context: tight stops, plain T+L1.0, and that the EA automates it from 2026-10-04.
- **Setup card:** every consult's card carries a Group line.
- **Library note:** `library/expected-r-blind-accept.md` (bloc only), with the spread caveat.

## 4. Data visualizers: AUDNZD, AUDCAD, NZDCAD added
- **Combo build:** 20261004_0346, 8,606 trades x 29,484 combos; verify ALL PASS.
- **Upload:** combos.bin 2.28 GB, SHA-verified.
- **Research lock:** the three sit in the research-lock group (hidden while the lock is on).
- **Status:** UPLOAD_PENDING

## 5. Downloads started (download + M1 export only, no MT5 processing)
- **Symbols:** 26 live-universe symbols with no data, in group order:
  - Yen;
  - Swiss franc;
  - Euro / Sterling crosses;
  - EM carry;
  - Nordic & Central Europe.
- **Progress:** `data/qdm_csv/fleet/fleet2_progress.log`.
- **Status:** DOWNLOAD_PENDING

## Open decisions for the trader
- (Checked: there is no live auto-approval. The EA never enters without your task, so TAKE-class bloc signals can't be taken without you.)
- **Early bloc trades:** the bloc trades opened before 2026-10-04 (AUDNZD #1, USDCAD #1) are on standard management. Apply the cut and lock by hand if you want.
