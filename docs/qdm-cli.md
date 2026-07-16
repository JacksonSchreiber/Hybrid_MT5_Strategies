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
./qdmcli -data action=exportToMT5 symbol=EURUSD timeframe=Tick
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

## Open questions (answer during EURUSD pilot)

- Symbol naming after add: is the stored name `EURUSD_TICK` / `EURUSD`? (`-symbol action=list` after add)
- What exactly does `exportToMT5 timeframe=Tick` emit (file format, destination)?
  If it produces an MT5-ready tick file, our MQL5 import script may only need
  to create the custom symbol and load it.
- `datefrom` support on `action=update` (limit download range to 2020+).
