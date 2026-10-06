# Parallel QDM downloads (many installs, one Mullvad exit each)

Set up 2026-10-05 after a single download call blocked the MT5 stage for 10+ hours. Trader: "make a script and document how you did
this so it's easy to do and make arbitrary amounts of concurrent downloads in the future".

## Why
- **QDM runs one instance per install.** It binds `AppWebServerPortQDM` (5050 for the main install, in `internal/AppSettings.txt`).
  A second `qdmcli` in the same folder exits with "Another instance of QuantDataManager is running". QDM's own message suggests
  separate installations.
- **A download call never lets go until it finishes.** One `-data action=update` for a few symbols from 2012 can run for days.
- **Dukascopy rate-limits per IP** (HTTP 429). Four symbols on one IP wrote about one day of data per minute.
- **The fix:** each extra install has its own port and folder, and its Java goes out through a different Mullvad relay's SOCKS5 proxy,
  so each one is throttled separately.

## M1 vs TICK: the big one
- QDM's Dukascopy source starts by reading StrategyQuant's descriptor
  (`http://autoupdate.strategyquant.com/dukascopy/dukascopy_ver3.xml`). It lists a CDN (`https://cdn.strategyquantcdn.com/data/dukascopy/{tick,m1}`)
  with yearly files for most symbols. QDM fetches those first, then gets the rest straight from Dukascopy.
- **TICK** is fetched hour-file by hour-file from `datafeed.dukascopy.com`, with 19 threads at once:
  - Dukascopy answers Mullvad exits with 429, often on the very first https request;
  - QDM's retries then crawl along at under one day of data per minute (2026-10-05).
- **M1** came almost entirely from the CDN. On 2026-10-05 DAX (from 2013-09-30, its first date on Dukascopy, to 2026-10-04) took
  5 min 17 s, with no 429s and 3.9M bars.
- **The CDN's first year differs by symbol.** On 2026-10-05: GBRIDXGBP 2011, USDCNH 2012, DEUIDXEUR 2013, EUSIDXEUR and ESPIDXEUR 2014.
  - Years before that come day by day from Dukascopy and crawl at about 0.5 day per minute: EUSIDXEUR's 2011-09 to 2014-08 takes
    about 1.5 days.
  - The worker probes `https://cdn.strategyquantcdn.com/data/dukascopy/m1/<SYM>/<YEAR>.zip` and logs the first year so a slow
    symbol is expected.
  - That can't be skipped: QDM's `-data action=update` ignores `datefrom`, and `-symbol` has no start-date setting. QDM starts at
    Dukascopy's first date for the instrument.
  - If an early backfill isn't worth it, take that symbol out of the queue.
- **The CDN zips** (`<YEAR>.zip` of per-day `.dat` files) use StrategyQuant's own "sq4" binary format, so they can't be read
  outside QDM.
- **qdm_worker.sh registers symbols as M1 by default.** Set `QDM_DATATYPE=TICK` only when ticks are needed, e.g. a later MT5
  real-tick import. A symbol already registered with the other datatype is deleted and re-added.
- **Coverage is accepted when** the last bar is recent AND either the data starts by 2012 or the last update ended with QDM's
  "Completed" line and no ERROR. Many symbols start later on Dukascopy.

## Pieces
| File | What it does |
|---|---|
| `pipeline/qdm_clone.sh <n> [socks_ip]` | Makes or refreshes `/home/jack/QDM<n>`. It copies the main install without its data (~0.8 GB). |
| `pipeline/qdm_worker.sh <n>` | Takes symbols from the queue one at a time on install n: register (M1 by default), update, coverage check, M1 export, `.done`. |
| `pipeline/qdm_pool.sh [queue]` | Adds installs and workers every 3 minutes while the memory budget allows, each with a fresh, tested Mullvad exit. |
| `data/qdm_csv/fleet/pool_queue.txt` | The symbols to download, separated by spaces or lines. `#` starts a comment. |
| `data/qdm_csv/fleet/pool_progress.log` | One log for the pool and all its workers. |
| `data/qdm_csv/fleet/oos_logs/` | Per-symbol state and logs (see below). |

Per-symbol state in `oos_logs/`:
- **`<SYM>.claim`:** which worker has the symbol.
- **`<SYM>.done`:** finished. These markers are shared with the fleet2/fleet3 downloaders.
- **`<SYM>.failed`:** QDM rejected the code, or the store was still empty after 6 updates. Delete it to retry.
- **Logs:** `<SYM>_qdm<n>_update_<k>.log` and `<SYM>_qdm<n>_export_<k>.log`.

### What qdm_clone.sh changes in a copy
- **`user/settings/settings.xml`:** five absolute `/home/jack/QDM/...` paths are rewritten to the new folder.
- **`internal/AppSettings.txt`:** the port becomes 5049+n.
- **The launcher is renamed `qdm<n>cli`, and the copy has no file called `qdmcli`.** The MT5 stage and driver check whether QDM is busy
  with `pgrep -x qdmcli`. A copy whose process was also named `qdmcli` would block MT5 again.
- **`qdmcli.config` and `qdm<n>cli.config`** get identical content, because it isn't known which one the renamed launcher reads:
  - heap `-Xms256m -Xmx$QDM_HEAP`, 768m by default;
  - `-DsocksProxyHost=<ip> -DsocksProxyPort=1080`.
- **`user/data/data.db`** (the symbol registry) is copied on the first clone only. A refresh keeps the clone's own registry, because
  that records what the clone downloaded.

### Mullvad exits
- **Where Mullvad runs:** inside WSL (`/usr/bin/mullvad`, interface `wg0-mullvad`, LAN sharing on).
- **Every relay has a SOCKS5 proxy** at `<relay>-wg-socks5-<nnn>.relays.mullvad.net:1080`, which resolves to 10.124.x.x through the
  tunnel. Going through a relay's proxy shows that relay's IP, whichever relay the tunnel itself is on.
- **How the pool picks relays:**
  - European relays first (Dukascopy is in Geneva), one per city in turn;
  - it skips any proxy another install already uses;
  - it tests each candidate by fetching QDM's Dukascopy descriptor through it:
    `curl --socks5 <ip>:1080 http://autoupdate.strategyquant.com/dukascopy/dukascopy_ver3.xml`. The response must be XML.
    - On 2026-10-05, de-ber-001 and de-dus-101 got a Cloudflare HTML page instead. QDM then fails every symbol at once with "Error
      while downloading and parsing descriptor XML".
    - Some relays don't answer at all, for example nl-ams-001.
  - When a worker hits that descriptor error, it keeps its claim and exits. The pool then gives that install another exit and
    restarts it.
- **Manual check:** `curl -s --socks5 10.124.0.53:1080 https://am.i.mullvad.net/ip`
- **Switching the tunnel's own relay** (used by the main install) also works:
  `mullvad relay set location se && mullvad reconnect`. The box tunnel (10.77.0.1) survives the switch.

### Safety rails
- **Memory:** every worker unit counts at the biggest worker's RSS, including workers waiting on the disk guard, which don't use RAM
  yet. The pool adds one only while `(workers + 1) x RSS <= MemAvailable + the clones' current RSS - RESERVE_MB - MARGIN_MB`.
  - Counting MemAvailable alone, as the first version did, let workers pile up behind the disk guard and then start their JVMs
    all at once.
  - RESERVE_MB is 4000 by default. It applies only while the MT5 AA stage or driver is running, and covers that stage's biggest step:
    - the main install's export (`-Xmx2g`, about 2.4 GB);
    - the H4 build from the tick CSV (`oos_h4_from_ticks.py`), which peaked near 3.8 GB for USDNOK's 464M ticks.
  - On 2026-10-05 a 2400 MB reserve let available RAM fall to 230 MB during that H4 build, and two workers had to be stopped.
  - Otherwise the reserve is IDLE_RESERVE_MB, 600 by default, so the pool grows once MT5 is done.
  - A worker stopped this way keeps its claim. The pool restarts it first, but only within the same budget.
  - Each worker unit is capped at `MemoryMax=1600M` with `MemorySwapMax=0`, plus `Nice=19` and the idle IO class.
  - WSL has about 7.6 GB.
- **Disk:**
  - An M1 worker waits while C: has less than 25 GB free.
  - A TICK worker waits under 53 GB while the MT5 AA stage is running, otherwise under 45 GB.
  - Downloads land in the WSL disk file on C:. Space freed inside WSL is reused before that file grows.
- **Never kill qdmcli:** an update writes its coverage to data.db only when it exits cleanly. Kill workers by unit or PID, never with
  `pkill -f`.
- **The main install `/home/jack/QDM`** stays free for the MT5 stage's exports. Don't point the pool at it.

## How to run a new batch
```bash
# 1. list the symbols (QDM/Dukascopy codes)
printf '%s\n' DEUIDXEUR GBRIDXGBP XPTCMDUSD > /home/jack/hybrid_project/data/qdm_csv/fleet/pool_queue.txt
# 2. start the pool (a light user unit; it starts capped qdm-worker-<n> units itself)
systemd-run --user --unit=qdm-pool --collect -p Nice=10 bash /home/jack/hybrid_project/pipeline/qdm_pool.sh
# 3. watch
tail -f /home/jack/hybrid_project/data/qdm_csv/fleet/pool_progress.log
systemctl --user list-units 'qdm-*'
```
- **Fixed number of workers:** set `MAX_N`, e.g. `MAX_N=6` means installs 2–6.
- **Other knobs:** `RESERVE_MB`, `IDLE_RESERVE_MB`, `MARGIN_MB`, `WORKER_MEMMAX`, `QDM_HEAP` (passed through to qdm_clone.sh by the environment) and
  `STEP_S`.
- **Re-running is safe:**
  - finished symbols are skipped;
  - a worker that died resumes its own claim in its own install, where its partial download is;
  - existing installs are reused, and their proxy is replaced only if it stops answering.
- **Making one install by hand:** `bash pipeline/qdm_clone.sh 7 10.124.0.53`, then
  `systemd-run --user --unit=qdm-worker-7 --collect -p MemoryMax=1600M -p MemorySwapMax=0 bash pipeline/qdm_worker.sh 7`

## Output
- **The M1 CSV** goes to `data/qdm_csv/fleet/<SYM>-M1-No Session.csv`, the same as fleet2/fleet3.
- **The tick store** stays in `/home/jack/QDM<n>/user/data/History/<SYM>/` of whichever install downloaded it.
  - To export ticks for MT5 later, use that install's launcher, e.g. `cd /home/jack/QDM3 && ./qdm3cli -data action=exportToMT5 ...`.
  - Or move the symbol's History folder and re-register it.
- **The DONE line** in the log counts weekdays with no M1 bars. Failed hour-downloads can leave holes inside the range that the
  first/last coverage check doesn't see.

## Cleanup
- **Delete a finished install:** stop its unit, then `rm -rf /home/jack/QDM<n>`. Its tick store goes with it; the M1 CSVs stay.
- **Main install's `internal/tmp`:** it held 83 GB on 2026-10-05. Clear it only while the main install is idle.
