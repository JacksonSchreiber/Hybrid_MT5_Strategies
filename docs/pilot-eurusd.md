# EURUSD Tick-Data Pilot (2026-07-16)

End-to-end dry run of the QDM CLI pipeline for one symbol (EURUSD) before
fanning out to all 49 FTMO symbols. Goal: prove the add → download → export
→ (MT5 export) → validate loop works, and pin down the CLI's actual
behavior where the docs were previously uncertain.

All commands run from `/home/jack/QDM`; full logs (with DEBUG/TRACE noise)
are under `/home/jack/hybrid_project/logs/pilot_eurusd_*.log`. Only one
qdmcli process ran at a time throughout, per the serialization constraint.

## 1. Add the symbol

```sh
./qdmcli -symbol action=add symbols=EURUSD datasource=dukascopy datatype=TICK
```

Log: `logs/pilot_eurusd_add.log` → `Symbol 'EURUSD' added.`

`-symbol action=list` immediately after:

```
Symbol,Instrument,Timeframe,Timezone,Date from,Date to,Total days,Total records,Source,Data type
EURUSD,EURUSD,TICK,,,,0,0,Dukascopy,Forex
```

**Finding:** stored symbol name is the plain `EURUSD`, not `EURUSD_TICK`.
(QDM's internal per-symbol data file on disk is named `EURUSD_TICK.dat` —
see below — but that internal filename is never what you pass back into
`-symbol`/`-data` commands.)

## 2. Download

Command used (deliberately testing whether a date range narrows the
download):

```sh
./qdmcli -data action=update symbols=EURUSD datefrom=2026.07.14 dateto=2026.07.15
```

Log: `logs/pilot_eurusd_test_datefrom.log` (this is the download log; also
referred to as `pilot_eurusd_download.log` in the task brief — same file).

**Finding: `datefrom`/`dateto` are ignored by `action=update`.** Despite
requesting a 1-day range, the log immediately showed
`EURUSD, Downloading year:2005` and proceeded through every year Dukascopy
has data for, back to **2003-05-05** (Dukascopy's earliest EURUSD ticks).
`action=update` always pulls full available history from the source; there
is no server-side way to cap it at update time.

Result:
- Completed in **24 min 56 sec** (QDM-reported), ~29 min wall time including
  JVM startup and connection setup.
- `Exit app - ok`, no errors.
- Internal store: `/home/jack/QDM/user/data/History/EURUSD/EURUSD_TICK.dat`
  = **5.8 GB** (mtime 19:49:30), covering 2003-05-05 → 2026-07-15.

## 3. Export to CSV

Batch file `pipeline/commands_pilot_eurusd_export.txt` (one JVM start):

```
-symbol action=list
-data action=export symbols=EURUSD timeframe=TICK datefrom=2020.01.01 dateto=2026.07.16 outputdir=/home/jack/hybrid_project/data/qdm_csv format="Generic tick format (comma delimited)"
-data action=export symbols=EURUSD timeframe=M1 datefrom=2020.01.01 dateto=2026.07.16 outputdir=/home/jack/hybrid_project/data/qdm_csv format="Generic bar format (comma delimited)"
-data action=exportToMT5 symbol=EURUSD timeframe=Tick outputdir=/home/jack/hybrid_project/data/mt5_ready
```

**Findings from this run:**
- The tick CSV export succeeded.
- The M1 CSV export **silently produced no file** — no error, no progress
  lines, nothing. When 2-3 `-data` commands are queued back-to-back in one
  `-run` batch, only the export that QDM picks up first prints progress and
  actually writes output; a second `action=export` queued immediately after
  appears to get dropped rather than run sequentially. **Lesson: don't queue
  multiple `-data action=export` commands in the same batch file — verify
  each one's output file exists before assuming a batch succeeded, or run
  exports one at a time.**
- `exportToMT5 timeframe=Tick` failed immediately:
  `Error: 'Timeframe' must be one of ['TICK,M1'].` — **`timeframe=` is
  case-sensitive; must be `TICK` or `M1` (uppercase).**

Fixed up with a second batch, `pipeline/commands_pilot_eurusd_export2.txt`,
run as its own commands (M1 export re-run + exportToMT5 with correct case).
The M1 export ran fine standalone; exportToMT5 also succeeded but — because
no `datefrom`/`dateto` was passed this time — exported the **full** history
(2003-2026), which is investigated in §4.

### Tick CSV — `data/qdm_csv/EURUSD-TICK-No Session.csv`

- Format: `Generic tick format (comma delimited)`, **with header row**.
- Size: **7.2 GB** (7,705,320,637 bytes)
- Rows: **169,181,129** (incl. header → 169,181,128 data rows)
- Columns: `DateTime,Bid,Ask,Volume`
- First data line: `20200101 22:01:12.821,1.12106,1.12160,750000`
- Last data line: `20260715 23:59:54.533,1.14698,1.14701,450000`
- Date range covered: 2020-01-01 → 2026-07-15 (matches the requested
  `datefrom=2020.01.01 dateto=2026.07.16` exactly — no truncation).

### M1 bar CSV — `data/qdm_csv/EURUSD-M1-No Session.csv`

- Format: `Generic bar format (comma delimited)`, with header row.
- Size: **138 MB**
- Rows: **2,432,442** (incl. header)
- Columns: `Date,Time,Open,High,Low,Close,Volume`
- First data line: `20200101,22:01:00,1.12106,1.12135,1.12106,1.12135,20250000`
- Last data line: `20260715,23:59:00,1.14696,1.14698,1.14691,1.14698,69300000`

## 4. `exportToMT5` — behavior, format, date scoping

Three variants were tested to fully characterize this command:

| Run | Args | Rows | Size | Date range in output |
|---|---|---|---|---|
| 1 (batch 1) | `timeframe=Tick` (wrong case) | — | — | **errored**, no file |
| 2 (batch 2) | `timeframe=TICK`, no datefrom/dateto | 512,807,256 | 20.5 GB | 2003.05.05 → 2026.07.15 (full history) |
| 3 (standalone) | `timeframe=TICK datefrom=2020.01.01 dateto=2026.07.16 filename=EURUSD_scoped` | 169,181,128 | 6.77 GB | 2020.01.01 → 2026.07.15 |

**Findings:**
- `outputdir`/`filename` control destination: file is written as
  `<filename>.csv` in `outputdir` (defaults to `<symbol>.csv` if `filename=`
  is omitted).
- Output is a **plain CSV, not a binary MT4/MT5 history file** — it is
  **not directly loadable by MT5**; an import step (our MQL5/Python script,
  e.g. via `CustomTicksAdd`) is still required. "exportToMT5" names the
  *target format*, not a native terminal-readable artifact.
- Format is 3 columns, **no header row**: `datetime,bid,ask` — no volume
  column, unlike `action=export`'s tick format. Datetime uses dots
  (`2020.01.01 22:01:12.821`) vs `action=export`'s space/no-dots
  (`20200101 22:01:12.821`).
- Row count of the scoped run (169,181,128) matches the scoped
  `action=export` tick CSV's data-row count (169,181,129 − 1 header =
  169,181,128) **exactly** — confirms both draw from the same underlying
  tick store and the date filter is applied identically.
- **`datefrom`/`dateto` ARE honored on `exportToMT5`**, unlike
  `action=update`. Omitting them exports the *entire* available history
  (huge — 20.5 GB for one major pair), so **always pass them explicitly**.

Final validated file (run 3, renamed to the deliverable path after deleting
the redundant 20.5 GB unscoped file to save disk):

`data/mt5_ready/EURUSD.csv`
```
2020.01.01 22:01:12.821,1.12132,1.12133
2020.01.01 22:01:17.176,1.12139,1.12140
2020.01.01 22:01:18.545,1.12138,1.12139
...
2026.07.15 23:59:45.558,1.14698,1.14699
2026.07.15 23:59:51.446,1.14698,1.14699
2026.07.15 23:59:54.533,1.14699,1.14700
```
6.77 GB, 169,181,128 rows.

## 5. Sanity checks

- Tick counts are in the **hundreds of millions** for EURUSD full history
  (512.8M, 2003-2026) and **169.2M** for the 2020+ window — comfortably
  above the "tens of millions" expectation for 5.5 years of a major FX
  pair's ticks.
- First/last timestamps line up exactly with the requested `datefrom`/
  `dateto` on every scoped export (2020-01-01 → 2026-07-15; `dateto` is
  exclusive of 2026-07-16 itself, i.e. covers through end-of-day
  2026-07-15).
- No errors in any completed job's log; every successful run ends with
  `Exit app - ok`. One caveat found along the way: the `NN%` progress
  figures printed during `action=export`/`exportToMT5` do **not** scale
  linearly with the date range being written (e.g. a scoped 2020-2026
  export showed only "33%" progress by the time it reached the final
  date, then jumped straight to "Completed, 100%"). **Don't trust the
  percentage as a proxy for how much date range is left — verify
  completion via `Exit app - ok` plus first/last CSV timestamps, not the
  percentage.**

## 6. Timings summary

| Step | Duration | Notes |
|---|---|---|
| Symbol add | <1 s | instant |
| Full-history download (`action=update`, EURUSD, TICK) | 24 min 56 s | unavoidably full history (2003-2026); ~29 min wall incl. JVM/connection overhead |
| Tick CSV export (2020-2026, 169M rows, 7.2 GB) | ~2 min | pure local disk I/O, no network |
| M1 CSV export (2.4M rows, 138 MB) | <1 min | |
| exportToMT5, unscoped (512.8M rows, 20.5 GB) | ~4-5 min | superseded/deleted |
| exportToMT5, scoped (169.2M rows, 6.77 GB) | ~1.5-2 min | final deliverable |
| **Total pilot wall time** | **~44 min** | dominated by the one-time full-history download |

## 7. Disk footprint observed (this pilot, EURUSD only)

| Artifact | Size |
|---|---|
| Internal store `EURUSD_TICK.dat` (full history, 2003-2026) | 5.8 GB |
| `qdm_csv/EURUSD-TICK-No Session.csv` (2020+, generic tick format) | 7.2 GB |
| `qdm_csv/EURUSD-M1-No Session.csv` (2020+, generic bar format) | 138 MB |
| `mt5_ready/EURUSD.csv` (2020+, exportToMT5 format) | 6.77 GB |
| **Total for one symbol, all artifacts kept simultaneously** | **~20 GB** |

Host disk at time of pilot: 51 GB used, 906 GB free (per PM check after the
pilot completed).

## 8. Recommendation for the 49-symbol fan-out

EURUSD is the single most tick-dense instrument Dukascopy offers (deepest,
most liquid FX pair in the world), so its ~20 GB all-artifacts-kept
footprint is close to a **worst-case per-symbol number**, not an average.
Other majors (GBPUSD, USDJPY, USDCHF, AUDUSD, etc.) will likely be the same
order of magnitude; crosses, exotics (USDHUF, EURCZK, USDZAR, …),
commodities, indices, and BTCUSD are expected to be substantially smaller
(lower tick frequency / shorter available history / less liquidity).

Even so, naively running all 49 symbols through add → full-history download
→ export (all formats) → keep-everything would risk **hundreds of GB**
if several majors approach EURUSD's footprint simultaneously kept on disk
— worth avoiding given 906 GB free is not an unlimited budget once other
project data/logs/MT5 terminal data are accounted for.

**Recommended pattern: rolling per-symbol processing, not keep-everything.**

For each symbol, serialized (one qdmcli invocation at a time, as required):
1. `-symbol action=add` (instant).
2. `-data action=update` — full-history download (unavoidable; ranges from
   ~25 min for a major down to plausibly a few minutes for thin exotics/
   indices with far fewer ticks).
3. Export **only** the artifacts actually needed downstream, **always with
   explicit `datefrom=2020.01.01 dateto=<today>`** (never call
   `exportToMT5` unscoped) — pick one of: `action=export` tick CSV,
   `action=export` M1 CSV, or `action=exportToMT5`, not all three unless a
   given symbol genuinely needs all three.
4. Consume/import the export immediately (MT5 custom-symbol load or
   downstream backtest ingestion).
5. Delete the CSV export(s) from `data/qdm_csv` / `data/mt5_ready` once
   consumed. Decide per-symbol whether to keep the internal `.dat` store
   (re-exporting later is fast/free; but re-downloading a cleared symbol
   means paying the full-history download cost again) or `action=clear` it
   to reclaim disk — for 49 symbols kept, the internal-store-only cost is
   still bounded (majors ~a few GB each, most others much less), so
   keeping internal stores and only churning the CSV exports is the safer
   default.

**Time budget:** with downloads fully serialized (only one qdmcli JVM at a
time, no way around this), 49 symbols × up to ~25-30 min each in the worst
case is a ceiling of ~20-24 hours; realistically most of the 49 (all the
non-major crosses/exotics/commodities/index/crypto symbols) will download
much faster than EURUSD, so the real total is likely well under that
ceiling — but budget for an overnight/unattended batch run rather than an
interactive one, and use `-run file=` per symbol (or small symbol groups)
to minimize JVM-restart overhead. Batch multiple *sequential, single-export*
command files rather than multiple `action=export` calls in one file (see
§3 finding: only the first export in a batch reliably produces output —
always verify each output file lands before trusting a batch ran).

## Files referenced

- `logs/pilot_eurusd_add.log` — symbol add
- `logs/pilot_symbol_list_after_add.log` — stored-name confirmation
- `logs/pilot_eurusd_test_datefrom.log` — download (== `pilot_eurusd_download.log` in the task brief)
- `logs/pilot_eurusd_export.log` — batch 1 (tick CSV succeeded; M1 silently skipped; exportToMT5 case error)
- `logs/pilot_eurusd_export2.log` — batch 2 (M1 CSV succeeded; exportToMT5 unscoped succeeded, later deleted)
- `logs/pilot_eurusd_exportToMT5_scoped_test.log` — exportToMT5 scoped, final deliverable
- `pipeline/commands_pilot_eurusd_export.txt`, `pipeline/commands_pilot_eurusd_export2.txt` — batch command files used
