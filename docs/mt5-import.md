# MT5 Custom-Symbol Tick Import (Dukascopy → `.dk`)

How Dukascopy tick CSVs become MT5 custom symbols usable by the Strategy
Tester, and the exact manual steps for the EURUSD smoke test.

## 1. Pipeline overview

```
qdmcli (fleet job)                data/mt5_ready/<BASE>.csv      (WSL, ext4, ~7 GB for EURUSD)
        │                                   │
        │  exportToMT5                      │  pipeline/stage_csv_for_import.sh <BASE>
        ▼                                   ▼
  Dukascopy ticks               MQL5\Files\import\<BASE>.csv     (Windows, MT5 sandbox)
                                            │
                                            │  ImportTicks.mq5  (run manually in MT5)
                                            ▼
                                  custom symbol  <BASE>.dk        (ticks + M1 bars)
                                            │
                                            │  VerifyImport.mq5  (QA)
                                            ▼
                                  Strategy Tester ("Every tick based on real ticks")
```

Components (canonical sources live in the repo; the terminal only ever runs
copies synced into its data folder):

| File | Role |
|---|---|
| `mql5/scripts/ImportTicks.mq5` | Streams the CSV into custom symbol `<BASE>.dk`. |
| `mql5/scripts/VerifyImport.mq5` | Read-only QA probe (tick count, first/last, M1/H4/D1 bars). |
| `pipeline/stage_csv_for_import.sh` | Stage CSV into the sandbox; sync + compile scripts; cleanup. |

### CSV format (input)

`exportToMT5` output — no header, 3 columns, UTC timestamps:

```
2020.01.01 22:01:12.821,1.12132,1.12133
```

`yyyy.MM.dd HH:mm:ss.mmm,bid,ask` — no volume, no last price. EURUSD is
169,181,128 rows / 6.77 GB (2020-01-01 → 2026-07-15).

## 2. Key design decisions

**CustomTicksAdd (not per-batch CustomTicksReplace).** The symbol is created
fresh and the CSV is strictly time-ascending, processed in ~1M-tick batches.
`CustomTicksAdd` documented behaviour is "append to the end", it requires the
symbol be in Market Watch (the script selects it), and requires ascending
`time_msc` (the CSV already is). MetaQuotes' own doc example uses it to load
351M+ historical ticks, so it *is* the documented bulk loader. Per-batch
`CustomTicksReplace` was rejected because it deletes+rewrites a `[from,to]`
interval and would clip ticks sharing an identical millisecond across a batch
boundary (Dukascopy has many same-ms ticks). *Fallback:* if `CustomTicksAdd`
proves painfully slow on a given box (it routes ticks through the Market-Watch
buffer), switch to `CustomTicksReplace` batched on boundaries chosen so they
never split an identical `time_msc`. Not needed unless the smoke test shows a
speed problem.

**Bars are built explicitly, not assumed.** The MQL5 docs are silent on whether
adding ticks auto-generates M1 bars for a custom symbol. So `ImportTicks`
aggregates M1 OHLC bars from the ticks in-stream (bid-based, exactly how MT5
builds bars) and writes them with `CustomRatesUpdate`. H4/D1 (and every other
timeframe) are always derived by the terminal from M1, so M1 is sufficient.
The script *also* probes `iBars` (after forcing timeseries synchronisation via
`SERIES_SYNCHRONIZED`) both before and after the `CustomRatesUpdate` call, so
the Journal records empirically what auto-build did — that is our definitive
answer to "does MT5 auto-build?", logged at run time.

**Timestamps.** UTC throughout, no conversion (project policy). `StringToTime`
parses `yyyy.MM.dd HH:mm:ss`; it does *not* parse `.mmm`, so the milliseconds
are split off manually and folded into `time_msc` (int64 ms since epoch).

**Parsing.** The file is opened `FILE_TXT` (one whole line per read) and each
line is split with our own `StringSplit(',')` — *not* `FILE_CSV`. This is
deliberate: it removes any ambiguity about the space inside the datetime field
(`2020.01.01 22:01:12.821`) being mistaken for a column separator. Malformed
lines are counted and skipped; if they exceed `MaxBadLines` (default 200) the
script aborts loudly — so a systemic parse mismatch fails fast, never
silently.

## 3. EURUSD smoke test — exact manual steps

The CSV is already staged and both scripts are compiled (0 errors). To run the
smoke test:

1. Open MT5 (the OANDA terminal at
   `C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB`).
2. In the **Navigator** (Ctrl+N) → **Scripts**, confirm `ImportTicks` and
   `VerifyImport` are listed. (If not, right-click Navigator → Refresh, or
   re-run `pipeline/stage_csv_for_import.sh --compile` from WSL.)
3. Open **any** chart (any symbol, any timeframe — the script does not use the
   chart's symbol; it reads `SymbolBase`).
4. Open the **Toolbox** (Ctrl+T) → **Experts** / **Journal** tabs so you can
   watch progress.
5. Drag **`ImportTicks`** from Navigator onto the chart. In the inputs dialog:
   - `SymbolBase` = **`EURUSD`**
   - leave the rest at defaults (`CustomSuffix=.dk`, `BatchSize=1000000`,
     `DeleteIfExists=true`, `BuildM1Bars=true`).
   - Click **OK**.
6. Watch the **Experts** log. Expected sequence:
   - `WARNING: origin 'EURUSD.sim' NOT found ... EXPECTED on a non-FTMO
     terminal` — **this is normal on the OANDA terminal.** Specs are set by
     inference (digits read from the CSV prices). P/L-affecting specs
     (contract size, tick value, calc mode) are minimal until this is re-run
     on the real FTMO terminal where `EURUSD.sim` exists and specs are cloned.
   - `... N ticks added (batch B)...` progress every 10 batches (~10M ticks).
   - A bar auto-build probe line (before/after `CustomRatesUpdate`).
   - `=== IMPORT COMPLETE ===` with total ticks (~169.18M), first/last tick,
     and M1/H4/D1 bar counts.

> Do not close the chart or the terminal while it runs. The script is
> synchronous (runs on the chart thread) — the terminal UI may feel sluggish
> during the load; that is expected.

**Signals to read correctly:**
- *Expected, not a failure:* the `EURUSD.sim NOT found` warning (OANDA
  terminal has no `.sim` origins), and a sluggish UI during the load.
- *This IS a failure:* if the script aborts within seconds with
  `malformed lines ... exceeded MaxBadLines`, the CSV rows aren't parsing as
  three comma fields. Capture the first malformed line from the log and report
  it — the fix is a parsing adjustment, not a data problem.

### Expected duration

169M ticks is a large load. Realistic range on a normal desktop:
**~15–45 minutes**, dominated by `CustomTicksAdd` writing into the terminal's
tick database (disk-bound). Do not treat a specific minute figure as a
contract — if it runs much longer than ~1 hour, note it and we switch to the
`CustomTicksReplace` fallback (see §2). CSV parsing itself is fast; the tick
DB writes are the cost.

## 4. Verify the result

Drag **`VerifyImport`** onto any chart, set `TargetSymbol` = **`EURUSD.dk`**,
OK. The Journal should show:

- `bars: M1=~2,432,000  H4=...  D1=...` (M1 close to the M1 CSV's 2.43M rows).
- `first M1 bar` ≈ `2020.01.01 22:01` and `last M1 bar` ≈ `2026.07.15 23:59`.
- `tick count (sum of M1 tick_volume): ~169,181,128` (matches the CSV row
  count — the acceptance check).
- `earliest stored tick: 2020.01.01 22:01:12.821 bid=1.12132 ask=1.12133`.

Tick count = the M1 CSV row count and first/last timestamps lining up with the
CSV are the pass criteria.

## 5. Cleanup

After a successful import the staged 6.77 GB sandbox copy is redundant (the
ticks now live in the terminal's own DB):

```sh
./pipeline/stage_csv_for_import.sh --clean EURUSD
```

## 6. Fanning out to the other 48 symbols

For each `<BASE>` once its `data/mt5_ready/<BASE>.csv` exists:

```sh
./pipeline/stage_csv_for_import.sh <BASE>     # stage
# → in MT5: run ImportTicks with SymbolBase=<BASE>
# → in MT5: run VerifyImport with TargetSymbol=<BASE>.dk
./pipeline/stage_csv_for_import.sh --clean <BASE>   # reclaim disk
```

On the real FTMO terminal, `<BASE>.sim` origins exist, so specs are cloned
automatically and the inference warning does not appear. On any terminal where
the origin is missing, verify the inferred specs before trusting tester money
figures (indices/metals/crypto especially — see `config/symbols.yaml` asset
classes).

## 7. Rebuilding the scripts (developer note)

Sources are the repo copies under `mql5/scripts/`. To edit and recompile:

```sh
# edit mql5/scripts/*.mq5, then:
./pipeline/stage_csv_for_import.sh --compile
```

This syncs the sources into `MQL5\Scripts\` and runs the MetaEditor CLI,
printing the (UTF-16) compile log decoded to UTF-8. Iterate to `0 errors`.
Never edit the copies inside the MT5 data folder directly — they are
overwritten on every sync.
