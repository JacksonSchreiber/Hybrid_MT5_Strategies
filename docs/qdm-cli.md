# QDM CLI Reference (verified on our build)

- Binary: `/home/jack/QDM/qdmcli` (Linux native, Java; run from `/home/jack/QDM/`)
- Build: **125.2692**, QuantDataManagerPro (Full license REDACTED, verified 2026-07-16)
- Note: startup prints heavy DEBUG logging — filter with `grep -v DEBUG`.
- Each invocation is a full JVM start (~10–15 s overhead) → prefer batching via
  `-run file=commands.txt` (one line per command) for multi-step jobs.

## License

```sh
./qdmcli -license action=info
./qdmcli -license action=update code=REDACTED   # done, verified OK
```

## Symbols (defines what to download)

```sh
./qdmcli -symbol action=list
./qdmcli -symbol action=add symbols=EURUSD,GBPUSD datasource=dukascopy datatype=TICK
./qdmcli -symbol action=delete symbols=EURUSD
./qdmcli -symbol action=clear symbols=EURUSD          # clears downloaded data
```

Key args: `datatype=[M1,TICK]`, `datasource=[dukascopy,file,darwinex,crypto,yahoo]`,
`instrument=` (optional), `postfix=`.

## Instruments (contract specs)

```sh
./qdmcli -instrument action=list
./qdmcli -instrument action=edit instrument=EURUSD datatype=forex defaultspread=2 ...
```

Args: `pointvalue`, `ticksize`, `tickstep`, `defaultspread`, `commissions`, `swap`,
`datatype=[stock,futures,forex,cfds,etf,index,crypto]`.

## Data (download / export)

```sh
# Download/update everything (or one symbol) from its datasource:
./qdmcli -data action=update
./qdmcli -data action=update symbols=EURUSD_TICK

# Export to CSV:
./qdmcli -data action=export symbols=EURUSD_TICK timeframe=TICK \
  datefrom=2020.01.01 dateto=2026.07.16 outputdir=/home/jack/hybrid_project/data/qdm_csv \
  format="Generic tick format (comma delimited)"

# NATIVE MT5 EXPORT (huge - may replace our own import script):
# NOTE: timeframe is case-sensitive — must be TICK or M1 (uppercase). "Tick" errors:
#   Error: 'Timeframe' must be one of ['TICK,M1'].
./qdmcli -data action=exportToMT5 symbol=EURUSD timeframe=TICK \
  datefrom=2020.01.01 dateto=2026.07.16 outputdir=/path filename=EURUSD
./qdmcli -data action=exportToMT5 symbol=EURUSD timeframe=M1 \
  spreadType=points spreadValue=2 datefrom=2020.01.01 dateto=2026.07.16 \
  outputdir=/path filename=EURUSD
```

`spreadType=[points,pips,real]` — use `real` with tick data (Dukascopy has true bid/ask).
Timezone handling: `-data action=timezones` lists zones; `timezone=` arg on import/export.

Export formats include: `Generic tick format (comma delimited)`,
`Generic bar format (comma delimited)`, `MetaTrader4 tick/bar format`, etc.
(default is MetaTrader4 bar format — always pass `format=` explicitly).

## Batch mode

```sh
./qdmcli -run file=/home/jack/hybrid_project/pipeline/commands.txt
```

One command per line; ideal for the 49-symbol pipeline (single JVM start).

**Caveat found during the EURUSD pilot:** queuing two `-data action=export`
commands back-to-back in the same batch file is unreliable — in testing,
only the first one produced output; the second (`timeframe=M1` right after
a `timeframe=TICK` export) silently produced no file and no error/progress
output. Re-running the same M1 export as its own standalone command (or as
the first line of a fresh batch) worked fine. Until this is root-caused,
verify every expected output file actually exists after a batch that
chains multiple exports — don't trust `Exit app - ok` alone.

## Open questions — ANSWERED during EURUSD pilot (2026-07-16)

- **Stored symbol name after `-symbol action=add`**: the stored name is the
  plain symbol, e.g. `EURUSD` — **not** `EURUSD_TICK`. Confirmed via
  `-symbol action=list` immediately after add:
  `EURUSD,EURUSD,TICK,,,,0,0,Dukascopy,Forex`. The `Instrument` column is also
  the bare name `EURUSD` (QDM appends `_dukascopy` only internally in the
  `-instrument action=list` inventory, e.g. `EURUSD_dukascopy` — you never
  pass that suffixed form to `-symbol`/`-data` commands).
- **`datefrom`/`dateto` on `action=update` are NOT honored.** Tested with
  `./qdmcli -data action=update symbols=EURUSD datefrom=2026.07.14
  dateto=2026.07.15` — the log showed it starting from `Downloading
  year:2005` regardless, i.e. `action=update` always pulls full available
  history from the source; there is no way to cap the download range at
  update time. `datefrom`/`dateto` only take effect on `action=export` /
  `action=exportToMT5` (confirmed working there — see `docs/pilot-eurusd.md`).
  Plan pipeline storage/disk budgeting around full-history downloads for
  every symbol.
- **`exportToMT5` output — format, naming, and date scoping (confirmed):**
  - `timeframe=` is case-sensitive: must be `TICK` or `M1` (uppercase).
    `timeframe=Tick` fails with `Error: 'Timeframe' must be one of
    ['TICK,M1'].`
  - File is written to `outputdir` as `<filename>.csv` (defaults to
    `<symbol>.csv` if `filename=` is omitted) — plain CSV, not a binary
    MT4/MT5 history format. It is **not** directly loadable by MT5 as-is;
    our MQL5/Python import step still needs to read this CSV and populate
    the custom symbol (e.g. via `CustomTicksAdd`).
  - Tick format is 3 columns, **no header row**:
    `datetime,bid,ask` — e.g. `2020.01.01 22:01:12.821,1.12132,1.12133`.
    This differs from `action=export`'s "Generic tick format" (4 columns
    *with* header: `DateTime,Bid,Ask,Volume`, and a different datetime
    format — `20200101 22:01:12.821` vs `2020.01.01 22:01:12.821`).
  - **`datefrom`/`dateto` ARE honored on `exportToMT5`** (unlike
    `action=update`, which ignores them — see above). Verified directly:
    omitting them exported the full available history (2003.05.05 →
    2026.07.15, 512,807,256 rows, 20.5 GB for EURUSD tick); passing
    `datefrom=2020.01.01 dateto=2026.07.16` produced exactly the same
    row count as the equivalently-scoped `action=export` CSV
    (169,181,128 rows, 6.77 GB) minus the header row. **Always pass
    `datefrom`/`dateto` explicitly on `exportToMT5`** — the unscoped
    default is enormous and mostly outside our 2020+ analysis window.
  - Full details, sample lines, and timings: `docs/pilot-eurusd.md`.
