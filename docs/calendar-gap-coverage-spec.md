# Calendar gap-coverage spec — what must be on the event feed, and where to get it

Issued by the coach, 2026-08-25, after the L1 re-retry. Owner: engineer.
Trigger: the trader's best decision of the window (J27, skipped a Friday short into the
2017 French election weekend; Sunday gap = −3.28R straight through the stop) was made
because it was late Friday — the trader did not know the election was there. The feed
had the row. The classifier never surfaced it. This spec closes that gap and adds the
event classes that history says move FX by whole-R amounts overnight.

## 0. Evidence from our own data (Dukascopy .dk, bid)

Largest weekend gaps, EURUSD 2017-01 → 2026-07 (Sunday open vs Friday close, pips).
"Known date" = the trigger was on a public calendar before Friday's close.

| # | Sunday | Gap | Cause | Known date? |
|---|---|---|---|---|
| 1 | 2017-04-23 | +193 | French presidential election, round 1 | YES — election |
| 2 | 2022-02-27 | −148 | Russia cut from SWIFT; Ukraine war weekend | no — geopolitical |
| 3 | 2025-02-02 | −128 | Tariffs on CA/MX/CN signed Sat Feb 1 | YES — announced deadline |
| 4 | 2026-03-08 | −73 | Iran war (began Sat 2026-02-28), weekend escalation | no — geopolitical (verify) |
| 5 | 2025-04-06 | −68 | "Liberation Day" tariffs; China retaliation Fri + weekend | YES — announced date |
| 6 | 2020-03-08 | +67 | Saudi–Russia oil price war + COVID weekend | no |
| 7 | 2022-09-11 | +61 | Kharkiv counteroffensive + hawkish ECB weekend | no |
| 8 | 2024-11-24 | +61 | Bessent named Treasury Secretary Fri night | no — transition period |
| 9 | 2025-06-22 | −59 | US strikes on Iran, Sat Jun 21 | no — geopolitical |
| 10 | 2025-05-11 | −56 | US–China Geneva trade talks (scheduled weekend meeting) | YES — summit |
| 11 | 2018-01-21 | +55 | US government shutdown from Sat Jan 20 | YES — funding deadline |
| 12 | 2022-11-06 | −52 | NFP Friday + China-reopening rumours over weekend | partial |
| 13 | 2017-09-24 | −48 | German federal election | YES — election |
| 14 | 2026-04-12 | −48 | Russia–Ukraine truce expiry Apr 13 | YES — deadline (verify) |
| 15 | 2022-04-10 | +46 | French presidential election, round 1 | YES — election |
| 16 | 2024-11-03 | +45 | US election eve; Iowa poll Saturday | YES — election week |
| 17 | 2025-04-13 | −45 | Electronics tariff exemption, Fri night Apr 11 | no — policy |
| 18 | 2023-03-12 | +42 | SVB failure Fri; weekend rescue | no — bank failure |
| 19 | 2017-03-26 | +40 | AHCA healthcare bill pulled, Fri Mar 24 | YES — scheduled vote |
| 20 | 2026-05-24 | +40 | (unattributed — verify) | ? |

Ten of the twenty largest EURUSD weekend gaps had a date that was public before Friday.
Those ten are the calendar's job. The other ten are why the late-Friday skip and the
weekend-hold rules stay regardless of how good the calendar gets.

GBPUSD 2017-01 → 2019-12, top weekend gaps:

| Sunday | Gap | Cause | Known date? |
|---|---|---|---|
| 2017-01-15 | −183 | Weekend leak of May's "hard Brexit" speech (speech scheduled Tue Jan 17) | YES — scheduled speech |
| 2018-10-14 | −66 | Raab–Barnier talks collapse Sun, before the Oct 17–18 EU summit | YES — summit week |
| 2018-11-11 | −59 | Jo Johnson resigns Fri; weekend cabinet revolt | no |
| 2018-11-04 | +59 | Sunday Times: financial-services Brexit deal | no |
| 2017-08-27 | +49 | Jackson Hole (Fri Aug 25) | YES — symposium |
| 2019-03-03 | +45 | Weekend reports of backstop progress before the Mar 12 vote | YES — vote week |
| 2019-03-10 | −44 | Weekend talks fail before the Mar 12 vote | YES — vote week |
| 2018-09-02 | −43 | Chequers plan fallout | no |
| 2017-01-29 | +42 | Trump travel-ban weekend | no |
| 2019-10-20 | −33 | Letwin amendment — Parliament sat on a SATURDAY | YES — announced sitting |

Largest single-day ranges are the same story: GBPUSD 2019-12-12 UK election 464 pips;
2017-01-17 May's Brexit speech 377 pips (a scheduled speech); 2019-01-02 holiday flash
crash 332 pips; EURUSD 2024-11-06 US election 252 pips; 2022-06-16 SNB surprise hike 221.

## 1. What must be on the calendar (by class)

Classes are the rule the trader applies. Every row in the feed must carry one.

**V — violation-class (approve inside ~12h = rule violation).** Already in the feed;
classifier coverage is the issue for some.
- Rate decisions + statements + press conferences: Fed/FOMC, ECB, BoE, BoJ, SNB, RBA,
  RBNZ, BoC (for the symbol's two currencies only).
- CPI (headline + core), NFP / jobs report, unemployment rate, average earnings /
  claimant count, GDP (advance), Core PCE.
- Fed Chair semiannual testimony (Humphrey-Hawkins, Feb + Jul) — ruled V on 2026-08-17.
  Feed has "Fed Chair … Testifies" rows (760 in the archive); no key matches them.
- Jackson Hole symposium (Fri/Sat late Aug) — 98 archive rows; no key matches.
- FOMC minutes, ECB accounts — keep caution-class (C), not V.

**W — weekend-hold class (any position that will be open Fri close → Mon open with one of
these inside the window = automatic skip / close-before-Friday).** This is the missing
layer. Feed has SOME of these rows but the classifier and the horizon both miss them.
- National elections, every round (French R1 and R2, German Bundestag, Italian, Spanish,
  Dutch, Greek; UK general; US presidential AND midterms; Japanese upper/lower house;
  Australian, NZ, Canadian federal; Swiss federal votes). Most European/Japanese elections
  are Sundays; Australian/NZ are Saturdays; UK Thursdays with overnight counts; US
  Tuesdays with overnight counts. Include the election week for Thursday/Tuesday votes.
- Referendums and plebiscites: Brexit 2016-06-23, Scotland 2014, Italy 2016-12-04,
  Greece 2015-07-05, Turkey 2017, Swiss quarterly votes when they touch the SNB/CHF.
- Parliamentary confidence / leadership / treaty votes with a published date (Brexit
  "meaningful votes", Greek bailout votes, no-confidence motions, party-leadership ballots).
- Government funding / shutdown deadlines and debt-ceiling X-dates (US); UK Budget and
  Autumn Statement; the 2022-09-23 mini-budget was a Friday → GBP −4.5% at Asia open.
- Announced trade / tariff deadlines and negotiation weekends (2025-02-01, 2025-04-02,
  2025-05-10/11 Geneva, truce expiries).
- Scheduled summits that can end in a communiqué: G7, G20, EU Council, NATO, OPEC/OPEC+
  (CAD/NOK), US–China leader meetings.
- Sovereign rating review dates. By EU regulation S&P/Moody's/Fitch/DBRS/Scope publish
  a calendar each December; the dates are FRIDAYS after the close by design → Monday
  gaps (US downgrade 2011-08-05 Fri; Moody's US 2025-05-16 Fri; Fitch US 2023-08-01).
- Scheduled central-bank set-pieces outside decisions: Jackson Hole, ECB Sintra forum,
  BoE Mansion House, BoJ Outlook Report; major scheduled leader speeches (May 2017-01-17).
- Announced weekend parliamentary sittings (Letwin Saturday 2019-10-19).

**C — caution-class (demand A-grade setup).** Retail sales, ISM, flash PMIs, FOMC
minutes, ECB accounts, PPI, JOLTS, consumer confidence, Fed/ECB member speeches.

**H — holiday-liquidity class.** Bank holidays for either currency (already in the
archive as Non-Economic, 2,141 rows), Dec 24 → Jan 2 (2019-01-02 flash crash), Japanese
Golden Week (yen intervention 2024-04-29 on a Tokyo holiday), Good Friday / Easter Monday,
US Thanksgiving Thu/Fri, Lunar New Year for AUD/NZD/JPY. Rule: no new entries; expect
spreads; stops are not guaranteed.

**U — unscheduled (cannot be calendared; this is what the structural rules are for).**
Wars and strikes (2022-02-24, 2024-04-13, 2025-06-21, 2026-02-28), sanctions, bank
failures (SVB, Credit Suisse), pandemic, resignations (Truss, Abe), snap-election calls
(May 2017-04-18, 390 pips), peg breaks (SNB 2015-01-15; CNY 2015-08-11), FX intervention
(BoJ 2022-09-22, 2022-10-21 Fri NY, 2024-04-29), coups (Turkey 2016-07-15 Fri night),
natural disasters (Japan 2011-03-11 Fri). Do NOT try to backfill these into the feed as
if they were predictable — that would teach the classifier hindsight. Keep a separate
`unscheduled_shocks.csv` for the coach's post-hoc analysis only, never for the blind
advisor's setup.md.

## 2. Sources to download

| Layer | Source | Coverage | How |
|---|---|---|---|
| Economic releases, rate decisions, testimony, Jackson Hole, bank holidays, major elections | ForexFactory archive — Hugging Face `Ehsanrs2/Forex_Factory_Calendar` (already in repo at `data/econ/ff_cache_full.csv`, 83k rows) | 2007-01-01 → 2025-04-07 | Already have it. Extend to today with `ehsanrs2/forexfactory-scraper` (the one normalize_econ.py was built for). Impact column: elections for majors are tagged High since ~2012, earlier ones Medium/Low — classify by class, not by FF impact. |
| National elections + referendums, every country, every round | Wikipedia `https://en.wikipedia.org/wiki/<YEAR>_national_electoral_calendar` | 2001 → 2027, one page per year, organised by month | MediaWiki API `action=parse&page=<YEAR>_national_electoral_calendar&prop=wikitext`. Verified 2017: FR Apr 23 / May 7 / Jun 11+18, DE Sep 24, UK Jun 8, JP Oct 22, NZ Sep 23, NL Mar 15, plus Swiss/Turkish referendums. Filter to countries of the 8 majors' currencies + EUR members FR/DE/IT/ES/NL/GR. Best free source; use it as the primary political layer. |
| Structured election records (optional, richer) | IFES ElectionGuide `https://www.electionguide.org` — free registration; CSV per election, JSON API | 1998 → present | Use if the Wikipedia scrape needs cross-checking; not required. Wikidata SPARQL was tested and rejected — returns thousands of local/mayoral elections with placeholder Jan-1 dates. |
| Sovereign rating review dates | MNI yearly PDF `MNI_Sovereign_Rating_Review_Calendar_<YEAR>.pdf` (2025, 2026 published); Moody's "European Union Sovereign Release Calendar"; S&P "calendar of EMEA sovereign rating publication dates" | forward year, published each Dec; historical PDFs back to ~2015 via S&P/Moody's press pages | Parse PDF → rows for US, UK, FR, DE, IT, ES, JP, CA, AU, NZ, CH. All dates are Fridays. |
| Central-bank meeting dates (cross-check only — FF already carries them) | Fed `federalreserve.gov/monetarypolicy/fomchistorical<YEAR>.htm` (1936→) and `fomccalendars.htm`; ECB/BoE/BoJ/SNB/RBA/RBNZ/BoC official schedule pages; `centralbank.watch` .ics for the forward year | all | Only to validate the FF layer; do not build a parallel decision feed. |
| Fed Chair semiannual testimony | `federalreserve.gov/monetarypolicy/mpr_default.htm` (Monetary Policy Report dates) | 1990s→ | Cross-check; FF rows already exist. |
| US shutdowns, debt-ceiling X-dates, UK fiscal events, G7/G20/EU Council/OPEC dates | Wikipedia list pages ("Government shutdowns in the United States", "List of G7 summits", "List of G20 summits", OPEC conference list); consilium.europa.eu meeting calendar; opec.org press releases | all | Small enough to hand-curate into `political_events.csv` once, then maintain per quarter. |
| Unscheduled shocks (coach-only file) | hand-curated from the gap tables above + the daily-range list; extend with `data/gaps/` recompute after each new tick download | 2017→ | Never feeds the blind advisor. |

Recompute the gap tables anytime: daily OHLC from the tick/M1 files, gap = first tick of
Sunday vs last tick of Friday, rank by |gap|. Script lives in this session's scratchpad;
one awk pass over each tick file (~2 min for 3 GB).

## 3. Pipeline defects found while checking coverage (fix all, in this order)

1. **Elections are in the feed but no key matches them.** `econ_events.csv` carries 223
   election/referendum/vote rows (e.g. `2017.04.23 … EUR,French Presidential Election`).
   `pipeline/tier0.py` `HIGH_IMPACT_KEYS` (line 34) has no `election`, `referendum`,
   `vote`, `testif`, or `jackson hole`. Same for the EA's `EventSignificance hi[]` (~1596)
   and `IsTopTierEvent keys[]` (~1577).
2. **`_SKIP_COUNTRY` (tier0.py line 45; EA `skip[]`) would suppress them even after the
   key is added.** "French Presidential Election", "German Federal Elections", "Italian
   Parliamentary Election", "Spanish Parliamentary Election" all start with a skipped
   country word. Political rows must be exempted from the country skip.
3. **Election timestamps are placeholders.** FF stores them as end-of-day local
   (23:59:59 Tehran → the feed shows 12:30 UTC on the vote day). Treat class-W rows as
   an all-day window: [Sat 00:00 UTC of that weekend → Mon 06:00 UTC] for Sunday/Saturday
   votes; [vote day 00:00 → +1 day 12:00 UTC] for weekday votes. Results land overnight.
4. **The 12h horizon cannot see Sunday from Friday.** `blind_calendar(horizon_h=12)`
   called on Friday 12:00 UTC ends Saturday 00:00. Rule: when the decision time is Friday,
   or the strategy is a swing hold (all three are), extend the scan to Monday 12:00 UTC.
   Emit `weekend_event` as its own field in `setup.md`, separate from `high_impact_ahead`.
5. **Three keyword lists drift (documented 2026-08-15).** Root-cause fix: have
   `normalize_econ.py` write a `class` column (V/C/W/H) from ONE table
   (`config/event_classes.yaml`, regex → class, with the country-skip applied *after* the
   class lookup), and have tier0 and the EA read the class column instead of matching
   keywords. One source of truth; adding an event type becomes a one-line YAML change
   with no recompile.
6. Testimony key must not over-flag: BoE governors testify to committees monthly. Use
   regex `fed chair .*testif` (and `boe gov .*testif` only when paired with an inflation
   report hearing) — or simpler, classify FF's "Fed Chair … Testifies" as V, all other
   testimony as C.
7. Separate file for the political layer: `political_events.csv`
   (`date_utc_start,date_utc_end,ccy,event,class,source,round`) merged into
   `econ_events.csv` by the same normalizer; keeps the FF layer regenerable.
8. Already filed 2026-08-17: `mt5_verify.sh --mode ALL` writes the AA journal to the same
   path as the trader's journal (overwrote the L1 re-retry journal; restored). Distinct
   filename for AA-mode journals.

## 4. Acceptance tests (run tier0 blind_calendar at these decision times)

| Decision time (UTC) | Symbol | Must return |
|---|---|---|
| 2017-04-21 Fri 12:00 | EURUSD | weekend_event: French Presidential Election (W) |
| 2017-05-05 Fri 12:00 | EURUSD | weekend_event: French Presidential Election R2 (W) |
| 2017-09-22 Fri 12:00 | EURUSD | weekend_event: German Federal Elections (W) |
| 2016-12-02 Fri 12:00 | EURUSD | weekend_event: Italian constitutional referendum (W) |
| 2017-07-12 Wed 12:00 | EURUSD | high_impact_ahead: Fed Chair Yellen Testifies 14:00 (V) |
| 2019-12-11 Wed 12:00 | GBPUSD | high_impact_ahead: UK Parliamentary Elections (W, weekday vote → next-day window) |
| 2017-08-24 Thu 12:00 | EURUSD | high_impact_ahead: Jackson Hole Symposium (V) |
| 2019-10-18 Fri 12:00 | GBPUSD | weekend_event: Parliament Saturday sitting / Brexit vote (W) |
| 2017-06-14 Wed 12:00 | EURUSD | no election flag (negative control; FOMC that day is V) |
| 2017-04-21 Fri 12:00 | USDJPY | NO French election flag (currency filter: EUR only) |

Then re-run the classifier over every Friday in the top-20 gap table above and report
how many of the ten "known date" weekends it now flags. Target: 10/10.

## 5. What the calendar will still not do

Half of the largest gaps were unscheduled. No feed fixes that. The late-Friday skip, the
weekend-hold rule, and the count-the-closes regime rule are the only protection against
class U — which is why J27 was not luck: it was the unknown-unknowns rule doing its job.
The fix here is for the known unknowns, so the next election is a decision, not a dodge.
