//+------------------------------------------------------------------+
//|                                          HybridForwardTest.mq5    |
//|        FTMO Hybrid Trading System - Phase 2 interactive harness   |
//|                                                                  |
//|  Visual-tester approve/deny harness now driving the THREE real    |
//|  detectors (SMC sweep+MSS, Deep-Fib, EMA20 mean-reversion) behind |
//|  the ISignalDetector plug-in seam, with:                         |
//|    * priority arbitration  SMC(1) > Fib(2) > EMA(3), one active   |
//|      setup per symbol;                                            |
//|    * REAL two-target scale-out for Fib/EMA (bank a partial at TP1,|
//|      move SL to breakeven, run the remainder to TP2) with a       |
//|      blended, volume-weighted R recorded in the journal;         |
//|    * the coloured TradeDialog / MessageBoxW fallback modal, gated |
//|      to the visual tester with DLLs;                              |
//|    * InpAutoApprove for HEADLESS automated verification (auto     |
//|      approve/skip, no modal) - tester-only.                      |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "2.00"
#property description "Phase 2 harness: 3 real detectors + arbitration + two-target scale-out."
//--- ensure the Strategy Tester bundles the display-only 3-EMA overlay used via iCustom (item 8).
//--- Belt-and-braces: iCustom with a string literal auto-registers it, but this makes it explicit.
#property tester_indicator "HybridTriEMA.ex5"

#include <Trade\Trade.mqh>
#include <Hybrid\Signal.mqh>
#include <Hybrid\detectors\DetectorCommon.mqh>
#include <Hybrid\detectors\SmcDetector.mqh>
#include <Hybrid\detectors\FibDetector.mqh>
#include <Hybrid\detectors\EmaDetector.mqh>
#include <Hybrid\detectors\ShockDetector.mqh>   // Strategy 4 CANDIDATE, backtest-only (default OFF)
#include <Hybrid\detectors\EmaRevInvDetector.mqh> // EMArev-Inverse SIGNAL LOGGER, backtest-only (default OFF)
#include <Hybrid\detectors\TrendContDetector.mqh>  // Trend-continuation detector #3 (LIVE from window 11; frozen study-B spec, same object)
#include <Hybrid\detectors\EmaRevQDetector.mqh>    // EMArevQ detector #4: base EMArev + slow-drift gate (coach 2026-09-09; quiet-trigger only)

//--- coloured EDITABLE dialog (poll-driven) + plain fallback (early-bound)
//--- TD_Open shows the window (Entry/SL/TP as edit boxes) and returns at once;
//--- the EA then loops TD_Poll (pumps window msgs, reads the boxes) and pushes
//--- R:R-locked recomputed values back via TD_SetDisplay, moving the real chart
//--- lines live, until the user clicks Accept(1)/Skip(2). See TradeDialog.c.
#ifndef LIVE
#import "TradeDialog.dll"
int  TD_Open(string title,string symbol,string strategy,string direction,
             string sigtime,string regime,string protocol,string entry,string sl,string tp,string tp2,string lots,string rr);
int  TD_Poll(double &entry,double &sl,double &tp,double &tp2,int &dirty);
void TD_SetDisplay(string entry,string sl,string tp,string tp2,string lots,string rr,int ok);
void TD_Close(void);
int  TD_SkipReason(void);   // 1-6 skip-reason code from the last skip (0 none)
void TD_SetOrderType(string s); // set the "Order" row (MARKET / BUY LIMIT @ x)
void TD_SetEvents(string abs_dates,string rel_dates); // upcoming-events list (both forms)
int  TD_Coach(void);        // 1 = coach mode on (scrub chart label to match)
int  TD_TakeShotRequested(void); // 1 (once) if operator clicked "Retake H4 screenshot"
void TD_OfferInverse(int on);    // show the INVERSE button (EMArev + gate ok) for this dialog
void TD_OfferMarketNow(int on);  // show the "enter now at market" button (a delay reopen, item 3)
//--- mid-trade MANAGEMENT PANEL (separate, non-modal window on its OWN thread,
//--- so it stays live + clickable while the visual tester is PAUSED - which is
//--- exactly when the operator decides at bar close). The EA only pushes state
//--- and drains a latched button action; all trading stays on the MQL thread.
int  TDM_Open(string title);                       // spawn panel; 1=up
void TDM_Update(string state_text,double lots,int be_enabled,int ratchet_enabled); // push live state
int  TDM_Poll(void);        // 0 none, 1 close, 2 close50, 3 SL->BE, 4 EXTEND, 5 SL->TP1 (one-shot)
void TDM_Close(void);       // tear down + join the panel thread
void TDM_ShowExtend(int on);// show EXTEND button (only while managing an inverse position)
#import
#import "user32.dll"
int MessageBoxW(long hWnd,string lpText,string lpCaption,uint uType);
#import
#else
//--- LIVE build (Phase 3 §10 C1 / E2): the popup DLL and user32 are NOT imported. These inline
//--- stubs keep every call site compiling; they are unreachable in live mode (AskApproval and
//--- ManagePanelTick never run — the file queue replaces the popup). Return values = "skip/none".
int  TD_Open(string title,string symbol,string strategy,string direction,
             string sigtime,string regime,string protocol,string entry,string sl,string tp,string tp2,string lots,string rr){ return 0; }
int  TD_Poll(double &entry,double &sl,double &tp,double &tp2,int &dirty){ return 2; }
void TD_SetDisplay(string entry,string sl,string tp,string tp2,string lots,string rr,int ok){}
void TD_Close(){}
int  TD_SkipReason(){ return 6; }
void TD_SetOrderType(string s){}
void TD_SetEvents(string abs_dates,string rel_dates){}
int  TD_Coach(){ return 0; }
int  TD_TakeShotRequested(){ return 0; }
void TD_OfferInverse(int on){}
void TD_OfferMarketNow(int on){}
int  TDM_Open(string title){ return 0; }
void TDM_Update(string state_text,double lots,int be_enabled,int ratchet_enabled){}
int  TDM_Poll(){ return 0; }
void TDM_Close(){}
void TDM_ShowExtend(int on){}
int  MessageBoxW(long hWnd,string lpText,string lpCaption,uint uType){ return 7; }   // IDNO
#endif
#define MB_YESNO        0x00000004
#define MB_ICONQUESTION 0x00000020
#define MB_SYSTEMMODAL  0x00001000
#define IDYES           6
#define IDNO            7

//--- auto-approve mode for headless verification (tester only)
enum ENUM_AUTO_APPROVE { AA_NONE=0, AA_ALL=1, AA_SKIP=2 };

//--- inputs
input double InpRiskPct     = 0.01;     // risk per trade (fraction of equity)
//--- account-safety guards (reject degenerate signals; cap monster positions)
input double InpMinStopATR  = 0.5;      // reject signal if SL distance < this * ATR(14)
input double InpMinStopSpreads = 2.0;   // ...also require SL distance >= this * current spread
input double InpMaxMarginPct = 0.50;    // hard cap: one position may use <= this fraction of free margin
input long   InpMagic       = 990217;   // magic number (graded stream)
//--- EMArev INVERSE live option (interactive tester only; ungraded, isolated cohort).
//--- default OFF; start_level.sh sets InpOfferInverse=true only with --inverse.
input bool   InpOfferInverse  = false;   // offer INVERSE on EMArev alerts (a 3rd dialog choice; default OFF, opt-in)
enum ENUM_DELAY_MODE { DM_FREEZE=0, DM_SLIDE=1 };
input ENUM_DELAY_MODE InpDelayMode = DM_FREEZE;  // delay reopen: FREEZE detector levels (pending at orig entry) vs SLIDE to market
//--- STUDY-ONLY (headless): when false, the EA trades the PRE-v2 exit (2026-09-08
//--- scale-at-tp1) so a re-run reproduces the exact frozen signal set, while a shadow
//--- observer reconstructs the doctrine-v2 R per signal + an impulse feature into a
//--- separate sidecar (journal\v2study_*.csv). Live default TRUE = current v2 exit,
//--- live journal + schema untouched. Set false only via V2_EXIT=false in mt5_verify.
input bool   InpV2Exit = true;           // true: live v2 exit; false: study re-run (old exit + v2 observer sidecar)
input long   InpInverseMagic  = 990218;  // SEPARATE magic for inverse trades (isolates them)
input bool   InpTestInverse   = false;   // TEST-ONLY: under AA_ALL, auto-take INVERSE on EMArev (headless lifecycle check)
input int    InpTestDelay     = 0;       // TEST-ONLY: under AA_ALL, delay each signal N bars then approve (headless delay check)
input int    InpDeviation   = 50;       // max slippage (points)
input bool   InpCleanupOnDeinit = false;// delete overlay objects on EA removal
input string InpObjPrefix   = "HFT_";   // chart-object name prefix
input bool   InpUseColoredDialog = true;// true: coloured TradeDialog.dll; false: MessageBoxW
input ENUM_AUTO_APPROVE InpAutoApprove = AA_NONE; // NONE=interactive; ALL/SKIP=headless (tester only)
//--- PHASE 3 LIVE MODE (docs/phase3-live-system-requirements.md §3/§4/§10/§11). Requires the
//--- -live build (HybridForwardTest-live.mq5: #define LIVE, popup DLL not imported). Replaces the
//--- popup with the local file queue under Common\Files\<InpLiveRoot>\ ; detectors/exits/journal
//--- logic are untouched (E1). Never combine with InpAutoApprove (never auto-trade live).
input bool   InpLiveMode         = false;  // LIVE: file-queue decision path instead of the popup (-live build only)
input bool   InpLiveSelfTest     = false;  // LIVE self-test (TESTER ONLY): in-EA scripted task driver + assertions
input string InpLiveRoot         = "live"; // queue root under Common\Files (self-test uses live_selftest)
input int    InpTaskPollSec      = 5;      // poll tasks/ at least this often (Q5: <=5s)
input int    InpHeartbeatSec     = 60;     // heartbeat at least this often (E5: <=60s)
input int    InpLiveMaxAgeBars   = 3;      // G2: unanswered signal ages out after N H4 bars -> skip code 8 (live.json overrides)
input int    InpElectionHorizonDays = 14;  // G1: NO-HOLD election gate horizon in calendar days (live.json overrides)
//--- §10.2 stop-width floor (coach 2026-09-18, TEST ONLY per §10.5 - never set in any live config).
//--- Widen-only: SL = max(structural stop, InpStopFloorATR x ATR(14) from entry). 0 = off.
input double InpStopFloorATR       = 0.0;   // §10.2 ATR floor on the DETECTOR stop (0=off; window 15 uses 1.385)
input string InpStopFloorSymbol    = "BTCUSD"; // symbol root the floor applies to (scoped: no other symbol is touched)
//--- §10.1 weekend-flat (broker quotes Mon-Fri): no signals on Sat/Sun bars, and any open position is closed at the
//--- first weekend bar (== the Friday close in continuous crypto data). 0 = off (every FX/index window is unaffected).
input bool   InpWeekendFlat        = false; // §10.1: skip weekend bars and be flat over the weekend
input int    InpSignalBars         = 500; // bars embedded in each signal JSON (H4 + D1). 0 = none: the web app/advisor pull bars from the terminal (MetaTrader5 API)
//--- strategy selection
input bool   InpUseSMC      = true;     // Strategy 1: liquidity sweep + MSS (priority 1)
input bool   InpUseFib      = true;     // Strategy 2: deep fib retracement (priority 2)
input bool   InpUseEMA      = false;    // Strategy 3: base EMA20 mean reversion — ALERT OFF (coach 2026-09-09): EMArev returns as EMArevQ (quiet-trigger only); base detector retired from the live lineup
input bool   InpUseEMArevQ  = true;     // Strategy 4: EMArevQ = base EMArev + whole-climb slow-drift gate (B>=12 & D>=0.56). Live quiet-only detector (priority 3). PROTOCOL: DISCRETION, shared graded magic.
//--- SMC params
input double InpSmcMinRR    = 2.0;
input double InpSmcTpR      = 3.0;
//--- Fib params
input double InpFibImpulseATR = 2.0;
input double InpFibMinRR    = 2.0;
//--- EMA params
input double InpEmaStretch  = 1.5;      // EMArevQ stretch threshold (coach stretch-scan 2026-09-09: 1.5xATR chosen, validated +0.158R 2022+). Was 2.0.
input double InpEmaAdxCeil  = 30.0;
input double InpEmaMinRR    = 1.3;
//--- Strategy 4 CANDIDATE: Shock Continuation. BACKTEST-ONLY, default OFF so the
//--- three live detectors + interactive tester stay frozen. Enable only headless.
input bool   InpUseShock    = false;    // Strategy 4 (candidate): shock continuation
input double InpShockAtr    = 1.8;      // shock bar: range >= this * ATR_D1  (grid 1.5/1.8/2.2)
input double InpShockPullAtr= 0.8;      // pullback depth >= this * ATR_H4     (grid 0.5/0.8/1.2)
input double InpShockTpMult = 0.75;     // TP = entry + this * shock range     (grid 0.5/0.75/1.0)
input double InpShockMinRR  = 2.0;      // do not arm below this R:R
input int    InpShockTrig   = 0;        // 0 = resume_close, 1 = reversal_candle
input bool   InpShockCalGate= true;     // forward V <=6h -> do not arm (else log only)
//--- EMArev-Inverse ("ride the stretch") SIGNAL LOGGER. BACKTEST-ONLY, default OFF.
//--- Wraps the frozen EMArev; logs inverted-continuation setups (no trades); the 6
//--- entry x exit cells are simulated in Python. Uses the live EMArev config below.
input bool   InpUseEmaRevInv = false;   // EMArev-Inverse candidate: log EMArev signals inverted
input bool   InpUseTrendCont = true;    // Trend-continuation detector #3 (LIVE from window 11; frozen study-B object, unchanged)
//--- imbalance highlights (display-only; NEVER affect trade/entry logic)
input bool   InpShowImbal   = true;           // draw FVG / price-gap / tick-volume imbalances
input color  InpImbColor    = clrMediumPurple;// one shared colour for all three imbalance types
input int    InpImbLookback = 120;            // bars scanned for imbalances
input double InpImbVolMult  = 2.0;            // tick-volume spike threshold vs 20-bar average
input int    InpImbMaxDraw  = 15;             // cap drawn zones (most recent) to avoid clutter
//--- economic-event lines (display-only; high-impact, from Common\Files\econ_events.csv)
input bool   InpShowEvents  = true;           // draw high-impact economic-event lines
input color  InpEvtBull     = clrLimeGreen;   // event that came out BULLISH for this ticker
input color  InpEvtBear     = clrOrangeRed;   // event that came out BEARISH for this ticker
input color  InpEvtNeutral  = clrGray;        // as-expected / no actual (neutral)
input int    InpEvtMaxDraw  = 600;            // safety cap on event lines drawn
input int    InpEvtLookaheadHours = 48;       // overlay: draw upcoming events this far AHEAD (rolling)
input int    InpEvtPastDays  = 7;             // overlay: also keep released events this many days back
input int    InpEvtListDays  = 14;            // popup: list scheduled events this many days after a signal
input int    InpEvtListMax   = 15;            // popup: cap the events list to this many rows
//--- readability / decluttering
input int    InpMaxVisibleSignals = 1;        // recent setups whose overlays stay on chart (older auto-clear)
input bool   InpEventTopTierOnly  = true;     // events: only top-tier movers (rate/CPI/NFP/GDP/PMI)
input bool   InpShowSwings  = true;           // draw swing high/low markers (persistent, rolling window)
input int    InpSwingDays   = 14;             // swing markers: rolling lookback window (days)
input bool   InpShowAux     = true;           // draw aux level lines + labels
input bool   InpShowFib     = true;           // draw the native Fib grid
input bool   InpFibRay      = true;           // extend fib levels right across the chart
input int    InpFontSize    = 10;             // overlay label font size (bigger = more readable)
input bool   InpBlindLabels = false;          // F2: overlay signal labels carry NO date (time-of-day only) — blind-advisor screenshots
input int    InpLineWidth   = 2;              // overlay line width (thicker = more readable)
//--- coaching / decision-capture (Phase-2 enhancements)
input bool   InpShotOnDecision = true;        // save a chart screenshot when the dialog opens
input int    InpShotW        = 1600;          // screenshot width (px)
input int    InpShotH        = 900;           // screenshot height (px)
//--- editable-entry pending orders
input int    InpPendingExpiryBars = 3;        // unfilled pending order auto-cancels after N H4 bars
input bool   InpForcePendingTest  = false;    // TEST-ONLY: under AA_ALL, place a forced STOP pending (verify fill-binding headlessly)
input int    InpForcePendingPts   = 0;        // TEST-ONLY: stop offset in points (0 = auto: 3x stops-level)
//--- mid-trade management panel (interactive/visual only)
input bool   InpManagePanel  = true;          // show the mid-trade management panel while a position is open
input double InpBEPadPips     = 1.0;           // SL->break-even padding (pips in the profit direction; covers spread)

//--- globals
CTrade         g_trade;
ISignalDetector *g_detectors[8];           // priority order; active set = SMC,Fib,EMArevQ,TrendCont (base EMA/Shock/EmaRevInv default off). Sized 8 to hold every registrable detector without overflow.
int            g_ndet       = 0;
ENUM_TIMEFRAMES g_tf        = PERIOD_H4;
bool            g_events_drawn = false;      // econ-event lines drawn once (first tick) [legacy, unused]
//--- econ-event cache (parsed once from Common\Files\econ_events.csv, filtered to
//--- THIS symbol's base/quote ccy). Redrawn each new bar so upcoming events appear
//--- as replay advances; bias/actual is revealed ONLY once an event has passed.
bool            g_ev_loaded = false;
datetime        g_ev_t[];                    // event time (UTC)
string          g_ev_ccy[];                  // event currency (base or quote of _Symbol)
string          g_ev_name[];                 // event name
int             g_ev_cb[];                   // ccy_bias (surprise sign for the event's ccy)
bool            g_ev_top[];                  // passes the top-tier (notable) filter
string          g_ev_cls[];                  // class letter V/W/C/H (baked by normalize_econ)
int             g_sig_ids[];                 // ids of setups with overlays on chart (for pruning)
bool           g_active     = false;
bool           g_started    = false;
datetime       g_last_bar   = 0;
datetime       g_start_time = 0;
datetime       g_last_time  = 0;
int            g_sig_seq    = 0;
string         g_journal_part= "";

//--- journal / managed-position row (one per presented signal)
struct JournalRow
  {
   int      id;
   datetime time;
   string   symbol;
   string   strategy;
   int      direction;
   double   orig_entry;     // detector's PROPOSED entry (before any operator edit)
   double   orig_sl;        // detector's proposed SL
   double   orig_tp;        // detector's proposed TP (the single/display target)
   double   orig_tp1;       // detector's proposed TP1 (scale-out bank target; 0 if none)
   double   orig_tp2;       // detector's proposed TP2 (scale-out runner; 0 if none)
   double   entry;          // realised fill price (for R math)
   double   sl;             // ORIGINAL sl (risk basis; never overwritten by BE move)
   double   tp;             // TP actually placed on the order (tp2 for two-target)
   double   tp1;            // partial target
   double   tp2;            // runner target
   double   partial_frac;   // fraction banked at tp1 (0 = single target)
   double   lots;           // initial lots
   double   risk_px;        // |entry - original sl|
   bool     tp1_done;       // partial taken + SL moved to BE
   bool     banked;         // v2 mechanical rule fired (50% off + SL->BE at first +1R/tp1)
   bool     ratcheted;      // manual SL->TP1 ratchet used (item 2; one per trade, runner phase)
   double   closed_vol;     // accumulated closed volume
   string   decision;       // approved / skipped / approved_pending / expired
   int      skip_reason;    // 1-6 skip-reason code (0 = not a skip / headless)
   bool     edited;         // operator changed any level vs the detector's proposal
   bool     is_pending;     // true = order placed as a pending (edited entry), awaiting fill
   long     order_ticket;   // pending-order ticket (0 until placed; for expiry/fill binding)
   datetime placed_time;    // when the pending order was placed (expiry bar-count basis)
   long     decision_ms;
   long     posid;
   bool     closed;
   datetime exit_time;
   double   exit_price;
   double   pnl;
   double   r_multiple;     // blended, volume-weighted
   string   regime;         // FROZEN D1 regime at signal time: TREND_UP/TREND_DOWN/CHOP/'' (warm-up)
   string   with_trend;     // '1' if signal dir matches trend, '0' if against, '' in CHOP/blank
   string   decision_class; // protocol class mechanically derived from regime+strategy: TAKE / DISCRETION
   double   to_entry;       // TRUE-ORIG: detector's FIRST-presentation levels, immutable (delay audit)
   double   to_sl;
   double   to_tp1;
   double   to_tp2;
   double   mfe_r;          // max favorable excursion in R (Item 2 MFE table)
   double   pre_dip_r;      // max R reached BEFORE the first recross below entry
   double   post_dip_r;     // max R reached AFTER a recross below entry (recovery); 0 if never dipped
   int      dipped;         // 1 = went >=+0.25R then traded back below entry
   string   terminal;       // single token: TP / BE / SL / end (assignment - no string accumulation)
   //--- STUDY-ONLY (InpV2Exit==false); all numeric (no struct-string accumulation bug):
   double   imp_atr;        // trigger impulse: max H4 bar range over prior 3 bars / ATR(14)
   int      imp_nbig;       // # of the prior 3 bars whose range >= 1.5*ATR(14)
   int      cal_lab;        // 1 = a symbol-relevant HIGH-impact (V/W) event within +/-4h of the signal
   double   v2_r;           // shadow observer: reconstructed doctrine-v2 R
   double   v2_bank;        // v2 banked-leg R contribution (0.5 * fire-tick favR)
   int      v2_runner;      // 1 tp2, 0 BE, 2 SL-pre-bank, 3 open-at-end, -1 unset
   //--- TP1-RATCHET STUDY (coach pair-12 item 1, 2026-09-10). Path flags captured LIVE while
   //--- the position is open (v2-live IS policy A; its window entry->tp2/BE contains B1/B2's
   //--- tighter exits). Python reconstructs A/B1/B2 runner R from (banked,bank_r,tp1R,tp2R,
   //--- touched1,redip1) + the AUTHORITATIVE terminal (TP/BE/SL). tp1R>0 only for scale-out
   //--- detectors; single-target detectors have no TP1 -> B1/B2 N/A for them.
   double   rt_tp1R, rt_tp2R;   // runner's TP1 / TP2 in R (0 if no such level)
   int      rt_touched1;        // 1 = favR reached TP1(2R) level while open
   int      rt_reached2;        // 1 = favR reached TP2 level while open (cross-check vs terminal)
   int      rt_redip1;          // 1 = after touching TP1 and before reaching TP2, favR fell back <=TP1
   double   rt_bankr;           // R the live +1R bank actually fired at (OpenR); -99 = never banked
   //--- PHASE 3 LIVE columns (E3/C2/G1): appended to the live journal only; never written in the tester
   bool     live;               // 1 = row produced by the LIVE queue path
   long     account_id;         // AccountInfoInteger(ACCOUNT_LOGIN)
   double   risk_pct_gate;      // the detectors' viability gate risk (InpRiskPct, 1%)
   double   risk_mult_applied;  // per-symbol multiplier applied at sizing (C2)
   int      auto_skip;          // 1 = automatic skip (G1 election gate) - only with skip_reason 2
   string   entry_mode;         // market | market_now | pending_frozen | skip | auto_skip | expired
   //--- §10.2 floor (only written when InpStopFloorATR>0, so default runs stay byte-identical)
   double   stop_pre_floor;     // |entry-SL| as the detector drew it
   double   stop_post_floor;    // |entry-SL| after the floor
   int      floor_applied;      // 1 = the floor widened this stop
  };
JournalRow g_rows[];

//--- mid-trade management panel state
bool   g_can_panel = false;   // interactive + visual + DLLs + InpManagePanel
bool   g_panel_open= false;   // the panel window is currently up

//--- manual mid-trade interventions (Close / Close50 / SL->BE), logged to a
//--- sibling .actions.csv so the coach can grade them (proposed plan vs the
//--- operator's actual intervention). One row per button press.
struct ManualAction
  {
   int      id;            // owning signal id
   long     posid;         // position id acted on
   datetime bar_time;      // H4 bar time of the action
   string   action;        // CLOSE / CLOSE50 / SL_BE
   double   price;         // market price at the action (exit-side)
   double   lots_before;   // position volume before
   double   lots_after;    // position volume after
   double   sl_after;      // SL after the action (unchanged unless SL_BE)
   double   banked_r;      // realised R so far (row.r_multiple)
   double   open_r;        // unrealised R on the remaining volume at action time
  };
ManualAction g_actions[];
string       g_actions_part = "";

//--- EMArev INVERSE cohort: fully isolated from the graded stream (separate journal,
//--- separate magic, own state; never enters g_rows[]/HasOpenPosition's InpMagic filter,
//--- so it holds no one-setup lock). Max ONE inverse open at a time (hard).
JournalRow   g_inv_rows[];               // parallel journal -> <journal>.inv.csv (EMArevINV)
ManualAction g_inv_actions[];            // parallel actions -> <journal>.inv.actions.csv
string       g_inv_journal_part = "";
string       g_inv_actions_part = "";
bool         g_inv_open = false;         // an inverse position is currently open
int          g_inv_open_idx = -1;        // index into g_inv_rows of the open inverse (-1 none)
long         g_inv_posid = 0;            // its position id
datetime     g_inv_entry_bar = 0;        // H4 bar time at entry (12-bar auto-close clock)
bool         g_inv_extended = false;     // trader hit EXTEND -> auto-close cancelled

//--- DELAY-decision state: the operator can defer a signal one bar at a time and keep
//--- deferring; the SAME candidate (same id, same entry/SL/TP) is re-presented next bar.
SignalCandidate g_delayed;               // the deferred candidate awaiting re-presentation
bool         g_delay_pending  = false;   // a signal is waiting to be re-asked next bar
int          g_delayed_id     = 0;       // its stable signal id (unchanged across delays)
bool         g_delay_replaying= false;   // current HandleSignal call is a delay re-present
int          g_delay_count    = 0;       // how many times the CURRENT signal has been delayed (audit)
int          g_h_ema200_d1  = INVALID_HANDLE;  // D1 200-EMA handle (regime tag)
int          g_h_adx_d1      = INVALID_HANDLE; // D1 ADX(14) handle (regime tag)
int          g_h_e20         = INVALID_HANDLE; // display-only 3-EMA overlay handles (item 8)
int          g_h_e50         = INVALID_HANDLE;
int          g_h_e200        = INVALID_HANDLE;
#define TRIEMA_BARS 400                         // how many bars back to draw the EMA lines

//--- Draw ONE EMA as connected trend segments (chart objects, which the visual tester renders
//--- reliably and does NOT strip on a template reapply — unlike a ChartIndicatorAdd'd indicator).
void DrawEmaLine(string tag,int handle,color col,int width)
  {
   if(handle==INVALID_HANDLE) return;
   double buf[]; datetime tm[];
   ArraySetAsSeries(buf,true); ArraySetAsSeries(tm,true);
   int n=CopyBuffer(handle,0,0,TRIEMA_BARS,buf);
   if(n<2) return;
   if(CopyTime(_Symbol,PERIOD_CURRENT,0,n,tm)<2) return;
   for(int i=1;i<n;i++)
     {
      if(buf[i]==EMPTY_VALUE || buf[i-1]==EMPTY_VALUE) continue;
      string nm=StringFormat("HFT_EMA_%s_%d",tag,i);
      if(ObjectFind(0,nm)<0)
        {
         ObjectCreate(0,nm,OBJ_TREND,0,tm[i],buf[i],tm[i-1],buf[i-1]);
         ObjectSetInteger(0,nm,OBJPROP_RAY_RIGHT,false);
         ObjectSetInteger(0,nm,OBJPROP_RAY_LEFT,false);
         ObjectSetInteger(0,nm,OBJPROP_BACK,false);   // foreground: colored EMAs on TOP of template lines
         ObjectSetInteger(0,nm,OBJPROP_SELECTABLE,false);
         ObjectSetInteger(0,nm,OBJPROP_HIDDEN,true);
         ObjectSetInteger(0,nm,OBJPROP_COLOR,col);
         ObjectSetInteger(0,nm,OBJPROP_WIDTH,width);
        }
      else
        { ObjectMove(0,nm,0,tm[i],buf[i]); ObjectMove(0,nm,1,tm[i-1],buf[i-1]); }
     }
  }
//--- redraw the 3-EMA overlay; throttled to once per new bar (900 segments/tick would crawl).
void EnsureTriEMA()
  {
   if(g_h_e20==INVALID_HANDLE) return;
   static datetime last=0;
   datetime cur=iTime(_Symbol,PERIOD_CURRENT,0);
   if(cur==last) return;
   last=cur;
   DrawEmaLine("A",g_h_e20, clrGold,        1);   // EMA20  gold
   DrawEmaLine("B",g_h_e50, clrDeepSkyBlue, 1);   // EMA50  DeepSkyBlue
   DrawEmaLine("C",g_h_e200,clrViolet,      2);   // EMA200 violet
   ChartRedraw(0);
  }
string       g_sig_regime    = "";       // regime FROZEN at the signal's first presentation
string       g_sig_with_trend= "";       // with_trend FROZEN likewise (recompute-safe across delays)
string       g_sig_class     = "";       // protocol class TAKE/DISCRETION, FROZEN with the tag (popup+journal+setup.md)
double       g_to_entry=0, g_to_sl=0, g_to_tp1=0, g_to_tp2=0;  // TRUE-ORIG frozen at first presentation

//+------------------------------------------------------------------+
string DirStr(int d) { return (d>0 ? "BUY" : "SELL"); }
string StampCompact(datetime t)
  { MqlDateTime dt; TimeToStruct(t,dt); return StringFormat("%04d%02d%02d",dt.year,dt.mon,dt.day); }

//+==================================================================+
//| PHASE 3 - LIVE MODE (Milestone 1): file queue, timer, heartbeat,  |
//| JSON. docs/phase3-live-system-requirements.md §3/§4/§10/§11.      |
//| Everything in this section is INERT unless InpLiveMode (and the   |
//| -live build). Nothing here touches detectors, exits or the tester |
//| journal path (E1: byte-identical tester behaviour).               |
//+==================================================================+
#define LIVE_SCHEMA_VERSION 1
#define EA_BUILD            "phase3-m1"

//--- live runtime state
bool     g_trading_enabled  = false;   // kill switch; fail CLOSED when the file is missing/unparseable
string   g_ev_day="";                 // live: day key of the last calendar (re)load
double   g_floor_pre=0.0, g_floor_post=0.0; int g_floor_applied=0;   // §10.2 floor, per signal
double   g_risk_mult        = 1.0;     // per-symbol risk multiplier (C2) - applied at order sizing ONLY
int      g_cfg_max_age_bars = 3;       // G2 (live.json overrides the input)
int      g_cfg_election_days= 14;      // G1 (live.json overrides the input)
int      g_cfg_task_max_age_h=24;      // stale-task cutoff (hours)
datetime g_live_last_poll   = 0;
datetime g_live_last_beat   = 0;
long     g_account_login    = 0;
bool     g_live_inited      = false;
bool     g_live_parked      = false;   // a published signal is awaiting a task (Slice 2)
bool     g_rm_warned        = false;
int      g_live_auto         = 0;       // 1 while committing an automatic skip (G1) -> journal auto=1 (Slice 5)
//--- a PUBLISHED signal awaiting a task (G2). Lives across ticks alongside g_delayed (the cand).
struct LivePark
  {
   int      sid;
   double   orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2;
   string   caption;
   datetime published_bar;      // bar the signal was FIRST published on
   datetime published_at;       // TimeCurrent() at first publish (decision latency basis)
   int      implicit_streak;    // consecutive bar closes with no task (code 8 at max_age)
   bool     explicit_this_bar;  // an explicit delay task arrived this bar (resets the streak)
   double   lots;
  };
LivePark g_park;
//--- PARKING SLOTS (trader ruling 2026-09-16): several signals may be parked per symbol; detectors keep running while
//--- slots remain. The single-park code paths are unchanged - a slot's context is LOADED into the globals
//--- (g_park, g_delayed, g_delay_count, frozen regime/class/true-orig) before any operation on that signal and
//--- STORED back after. max_parks=1 (default; tester/self-test) reproduces the one-park behaviour exactly.
#define MAX_PARKS 6
struct ParkSlot { bool active; LivePark park; SignalCandidate cand; int delay_count; string regime; string with_trend; string cls; double to_entry; double to_sl; double to_tp1; double to_tp2; };
ParkSlot g_slots[MAX_PARKS];
int      g_cfg_max_parks=1;
int  ParkCount(){ int n=0; for(int i=0;i<MAX_PARKS;i++) if(g_slots[i].active) n++; return n; }
int  ParkIndexBySid(int sid){ for(int i=0;i<MAX_PARKS;i++) if(g_slots[i].active && g_slots[i].park.sid==sid) return i; return -1; }
void LiveRecomputeParked(){ g_live_parked=(ParkCount()>0); }
void ParkLoad(int i)
  {
   g_park=g_slots[i].park; g_delayed=g_slots[i].cand; g_delayed_id=g_slots[i].park.sid; g_delay_count=g_slots[i].delay_count;
   g_sig_regime=g_slots[i].regime; g_sig_with_trend=g_slots[i].with_trend; g_sig_class=g_slots[i].cls;
   g_to_entry=g_slots[i].to_entry; g_to_sl=g_slots[i].to_sl; g_to_tp1=g_slots[i].to_tp1; g_to_tp2=g_slots[i].to_tp2;
   g_live_parked=true;
  }
void ParkStoreCurrent()
  {
   int i=ParkIndexBySid(g_park.sid);
   if(i<0){ for(int j=0;j<MAX_PARKS;j++) if(!g_slots[j].active){ i=j; break; } }
   if(i<0) return;
   g_slots[i].active=true; g_slots[i].park=g_park; g_slots[i].cand=g_delayed; g_slots[i].delay_count=g_delay_count;
   g_slots[i].regime=g_sig_regime; g_slots[i].with_trend=g_sig_with_trend; g_slots[i].cls=g_sig_class;
   g_slots[i].to_entry=g_to_entry; g_slots[i].to_sl=g_to_sl; g_slots[i].to_tp1=g_to_tp1; g_slots[i].to_tp2=g_to_tp2;
  }
void ParkClear(int sid){ int i=ParkIndexBySid(sid); if(i>=0) g_slots[i].active=false; }

string LivePath(string sub){ return InpLiveRoot+"\\"+sub; }
string SymbolRoot(){ int p=StringFind(_Symbol,"."); return (p>0 ? StringSubstr(_Symbol,0,p) : _Symbol); }
//--- live wall clock. TimeCurrent() is the last TICK time: it freezes over weekends and on a dead feed, which would
//--- silence the heartbeat and the task poll. The tester keeps sim time (self-test / parity unchanged).
datetime LiveNow(){ return ((bool)MQLInfoInteger(MQL_TESTER) ? TimeCurrent() : TimeGMT()); }
string IsoTime(datetime t)
  { MqlDateTime d; TimeToStruct(t,d);
    return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",d.year,d.mon,d.day,d.hour,d.min,d.sec); }

//--- JSON: minimal escaping writer. Files are ASCII-only (anything outside 0x20-0x7E is \uXXXX),
//--- so FILE_ANSI round-trips them exactly on both sides of the queue.
string JsonEsc(string s)
  {
   string o=""; int n=StringLen(s);
   for(int i=0;i<n;i++)
     {
      ushort c=StringGetCharacter(s,i);
      if(c=='"')       o+="\\\"";
      else if(c=='\\') o+="\\\\";
      else if(c=='\n') o+="\\n";
      else if(c=='\r') o+="\\r";
      else if(c=='\t') o+="\\t";
      else if(c<0x20 || c>0x7E) o+=StringFormat("\\u%04x",c);
      else o+=ShortToString(c);
     }
   return o;
  }
class CJsonW
  {
private:
   string m_s; int m_depth; bool m_first[32];
   void Sep(){ if(m_depth>0){ if(!m_first[m_depth]) m_s+=","; m_first[m_depth]=false; } }
public:
   CJsonW(){ m_s=""; m_depth=0; for(int i=0;i<32;i++) m_first[i]=true; }
   void BeginObj(){ Sep(); m_s+="{"; m_depth++; m_first[m_depth]=true; }
   void EndObj()  { m_s+="}"; m_depth--; }
   void BeginArr(){ Sep(); m_s+="["; m_depth++; m_first[m_depth]=true; }
   void EndArr()  { m_s+="]"; m_depth--; }
   void Key(string k){ Sep(); m_s+="\""+JsonEsc(k)+"\":"; m_first[m_depth]=true; }
   void Str(string v){ Sep(); m_s+="\""+JsonEsc(v)+"\""; }
   void Num(double v,int digits){ Sep(); m_s+=DoubleToString(v,digits); }
   void Int(long v){ Sep(); m_s+=(string)v; }
   void Bool(bool b){ Sep(); m_s+=(b?"true":"false"); }
   void Null(){ Sep(); m_s+="null"; }
   void TimeIso(datetime t){ Str(IsoTime(t)); }
   void KStr(string k,string v){ Key(k); Str(v); }
   void KNum(string k,double v,int digits){ Key(k); Num(v,digits); }
   void KInt(string k,long v){ Key(k); Int(v); }
   void KBool(string k,bool b){ Key(k); Bool(b); }
   void KTime(string k,datetime t){ Key(k); TimeIso(t); }
   void KNull(string k){ Key(k); Null(); }
   string Text(){ return m_s; }
  };

//--- JSON: minimal FLAT parser for task/config files (Q3/Q6). One object, optionally ONE nested
//--- object level (flattened as "params.x"); strings with escapes, numbers, true/false/null.
//--- Arrays and deeper nesting are rejected - nothing else is parsed (S6).
int JSkipWs(string s,int i)
  { int n=StringLen(s); while(i<n){ ushort c=StringGetCharacter(s,i); if(c==' '||c=='\t'||c=='\n'||c=='\r') i++; else break; } return i; }
bool JParseStr(string s,int &i,string &out,string &err)
  {
   int n=StringLen(s);
   if(i>=n || StringGetCharacter(s,i)!='"'){ err="expected string"; return false; }
   i++; out="";
   while(i<n)
     {
      ushort c=StringGetCharacter(s,i);
      if(c=='"'){ i++; return true; }
      if(c=='\\')
        {
         if(i+1>=n){ err="bad escape"; return false; }
         ushort e=StringGetCharacter(s,i+1);
         if(e=='"') out+="\""; else if(e=='\\') out+="\\"; else if(e=='/') out+="/";
         else if(e=='n') out+="\n"; else if(e=='r') out+="\r"; else if(e=='t') out+="\t";
         else if(e=='b' || e=='f') { }
         else if(e=='u')
           {
            if(i+5>=n){ err="bad \\u"; return false; }
            int cp=0;
            for(int k=0;k<4;k++)
              { ushort hc=StringGetCharacter(s,i+2+k);
                int v=(hc>='0'&&hc<='9')?hc-'0':(hc>='a'&&hc<='f')?hc-'a'+10:(hc>='A'&&hc<='F')?hc-'A'+10:-1;
                if(v<0){ err="bad \\u"; return false; } cp=cp*16+v; }
            out+=ShortToString((ushort)cp); i+=6; continue;
           }
         else { err="bad escape"; return false; }
         i+=2; continue;
        }
      out+=ShortToString(c); i++;
     }
   err="unterminated string"; return false;
  }
bool JParseScalar(string s,int &i,string &out,string &err)
  {
   int n=StringLen(s), st=i;
   while(i<n){ ushort c=StringGetCharacter(s,i); if(c==','||c=='}'||c==']'||c==' '||c=='\t'||c=='\n'||c=='\r') break; i++; }
   out=StringSubstr(s,st,i-st);
   if(out==""){ err="empty value"; return false; }
   return true;
  }
bool JParseObj(string s,int &i,string prefix,int depth,string &keys[],string &vals[],string &err)
  {
   int n=StringLen(s);
   i=JSkipWs(s,i);
   if(i>=n || StringGetCharacter(s,i)!='{'){ err="expected {"; return false; }
   i++;
   while(true)
     {
      i=JSkipWs(s,i); if(i>=n){ err="unterminated object"; return false; }
      ushort c=StringGetCharacter(s,i);
      if(c=='}'){ i++; return true; }
      if(c==','){ i++; continue; }
      string k; if(!JParseStr(s,i,k,err)) return false;
      i=JSkipWs(s,i); if(i>=n || StringGetCharacter(s,i)!=':'){ err="expected :"; return false; }
      i++; i=JSkipWs(s,i); if(i>=n){ err="missing value"; return false; }
      c=StringGetCharacter(s,i);
      string full=(prefix=="" ? k : prefix+"."+k);
      if(c=='{'){ if(depth>=1){ err="nesting too deep"; return false; }
                  if(!JParseObj(s,i,full,depth+1,keys,vals,err)) return false; continue; }
      if(c=='['){ err="arrays not supported"; return false; }
      string v;
      if(c=='"'){ if(!JParseStr(s,i,v,err)) return false; }
      else      { if(!JParseScalar(s,i,v,err)) return false; }
      int m=ArraySize(keys); ArrayResize(keys,m+1); ArrayResize(vals,m+1); keys[m]=full; vals[m]=v;
     }
   err="unreachable"; return false;   // satisfies 'all control paths return' (loop exits via return)
  }
bool JsonFlatParse(string text,string &keys[],string &vals[],string &err)
  { ArrayResize(keys,0); ArrayResize(vals,0); err=""; if(StringLen(text)>65536){ err="file too large"; return false; }
    int i=0; return JParseObj(text,i,"",0,keys,vals,err); }
string JGet(string &keys[],string &vals[],string key,string def="")
  { for(int i=0;i<ArraySize(keys);i++) if(keys[i]==key) return vals[i]; return def; }

//--- atomic file I/O (Q2): write <name>.tmp then FileMove-rename over the destination.
//--- Readers ignore *.tmp. Proven in the headless tester by mql5/experts/LiveProbe.mq5 (Slice 0).
bool AtomicWriteText(string rel,string body)
  {
   string tmp=rel+".tmp";
   int h=FileOpen(tmp,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("AtomicWrite: open failed ",tmp," err=",GetLastError()); return false; }
   FileWriteString(h,body); FileFlush(h); FileClose(h);
   if(!FileMove(tmp,FILE_COMMON,rel,FILE_COMMON|FILE_REWRITE))
     { Print("AtomicWrite: move failed ",rel," err=",GetLastError()); FileDelete(tmp,FILE_COMMON); return false; }
   return true;
  }
string ReadTextFile(string rel)
  {
   int h=FileOpen(rel,FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);   // six instances read the same config/task files
   if(h==INVALID_HANDLE) return "";
   string s=""; while(!FileIsEnding(h)) s+=FileReadString(h)+"\n"; FileClose(h); return s;
  }
void AppendLine(string rel,string line)
  {
   int h=FileOpen(rel,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(h==INVALID_HANDLE) return;
   FileSeek(h,0,SEEK_END); FileWriteString(h,line+"\r\n"); FileFlush(h); FileClose(h);
  }
//--- append-only command audit log (S8): ts|symbol|event|task_id|verb|target|result|reason|detail
void AuditLine(string ev,string task_id,string verb,string target,string result,string reason,string detail)
  {
   AppendLine(LivePath("audit_"+_Symbol+".log"),
              IsoTime(LiveNow())+"|"+_Symbol+"|"+ev+"|"+task_id+"|"+verb+"|"+target+"|"+result+"|"+reason+"|"+detail);
  }

void LiveEnsureDirs()
  {
   FolderCreate(InpLiveRoot,FILE_COMMON);
   string d[]={"signals","tasks","tasks\\done","acks","positions","positions\\closed","state","config","journal"};
   for(int i=0;i<ArraySize(d);i++) FolderCreate(LivePath(d[i]),FILE_COMMON);
   FolderCreate(LivePath("state\\"+_Symbol),FILE_COMMON);
  }

//--- config: trading_enabled.json (kill switch, E10) - read EVERY second; missing/bad => disabled
void LiveReadKillSwitch()
  {
   string t=ReadTextFile(LivePath("config\\trading_enabled.json"));
   string k[],v[],err; bool en=false;
   if(t!="" && JsonFlatParse(t,k,v,err)) en=(JGet(k,v,"trading_enabled","false")=="true");
   if(en!=g_trading_enabled || !g_live_inited)
     { AuditLine("kill_switch","","","",(en?"enabled":"disabled"),(t==""?"file_missing":(err!=""?"parse_error":"")),""); g_trading_enabled=en; }
  }
//--- config: risk_mult.json {"EURUSD":0.5,"US100":1.0,...} keyed by symbol ROOT (C2). Sizing only.
void LiveLoadRiskMult()
  {
   string t=ReadTextFile(LivePath("config\\risk_mult.json"));
   string k[],v[],err;
   if(t=="" || !JsonFlatParse(t,k,v,err))
     { if(!g_rm_warned){ AuditLine("config","","","","warn","risk_mult_unavailable",(t==""?"file_missing":err)+" -> 1.0"); g_rm_warned=true; }
       g_risk_mult=1.0; return; }
   string m=JGet(k,v,SymbolRoot(),"");
   if(m==""){ if(!g_rm_warned){ AuditLine("config","","","","warn","risk_mult_missing",SymbolRoot()+" -> 1.0"); g_rm_warned=true; } g_risk_mult=1.0; return; }
   double nm=StringToDouble(m); if(nm<=0.0) nm=1.0;
   g_risk_mult=nm;
  }
//--- config: live.json (G1/G2 knobs); written with defaults from the inputs when missing
void LiveLoadConfig()
  {
   string p=LivePath("config\\live.json");
   string t=ReadTextFile(p); string k[],v[],err;
   if(t=="" || !JsonFlatParse(t,k,v,err))
     {
      g_cfg_election_days=InpElectionHorizonDays; g_cfg_max_age_bars=InpLiveMaxAgeBars; g_cfg_task_max_age_h=24;
      AtomicWriteText(p,StringFormat("{\"schema_version\":%d,\"election_horizon_days\":%d,\"max_age_bars\":%d,\"task_max_age_hours\":%d}",
                                     LIVE_SCHEMA_VERSION,g_cfg_election_days,g_cfg_max_age_bars,g_cfg_task_max_age_h));
     }
   else
     {
      g_cfg_election_days =(int)StringToInteger(JGet(k,v,"election_horizon_days",(string)InpElectionHorizonDays));
      g_cfg_max_age_bars  =(int)StringToInteger(JGet(k,v,"max_age_bars",(string)InpLiveMaxAgeBars));
      g_cfg_task_max_age_h=(int)StringToInteger(JGet(k,v,"task_max_age_hours","24"));
      g_cfg_max_parks=(int)MathMax(1,MathMin(MAX_PARKS,StringToInteger(JGet(k,v,"max_parks","1"))));   // parking slots per symbol (trader ruling 2026-09-16)
      if(g_cfg_election_days<0) g_cfg_election_days=0;
      if(g_cfg_max_age_bars<1)  g_cfg_max_age_bars=1;
     }
   LiveLoadRiskMult();
  }

//--- §11-7: aggregate risk-to-stop across EVERY open position on the account (any symbol), in
//--- account currency. An unprotected position (SL=0) counts its full distance to zero (BUY) or
//--- its open price as a conservative proxy (SELL) - flagged in the heartbeat alerts.
double AggregateRiskToStop(int &unprotected)
  {
   double agg=0.0; unprotected=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong tk=PositionGetTicket(i); if(tk==0) continue;
      string sym=PositionGetString(POSITION_SYMBOL);
      double vol=PositionGetDouble(POSITION_VOLUME), po=PositionGetDouble(POSITION_PRICE_OPEN), sl=PositionGetDouble(POSITION_SL);
      double ts=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE), tv=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
      if(ts<=0.0 || tv<=0.0) continue;
      double dist;
      if(sl>0.0) dist=MathAbs(po-sl); else { dist=po; unprotected++; }
      agg+=(dist/ts)*tv*vol;
     }
   return agg;
  }

//--- E5 heartbeat: heartbeat_<SYMBOL>.json (per-instance, §11-1). Includes §11-8 trade-allowed flags.
void WriteHeartbeat(string status="running")
  {
   if(g_account_login==0) g_account_login=AccountInfoInteger(ACCOUNT_LOGIN);
   int unprot=0; double agg=AggregateRiskToStop(unprot);
   CJsonW j; j.BeginObj();
   j.KInt("schema_version",LIVE_SCHEMA_VERSION); j.KStr("ea_build",EA_BUILD);
   j.KStr("symbol",_Symbol); j.KStr("status",status); j.KTime("ts",LiveNow());
   j.KInt("account_login",g_account_login);
   j.KNum("equity",AccountInfoDouble(ACCOUNT_EQUITY),2); j.KNum("balance",AccountInfoDouble(ACCOUNT_BALANCE),2);
   j.KNum("margin_free",AccountInfoDouble(ACCOUNT_MARGIN_FREE),2);
   j.KBool("trading_enabled",g_trading_enabled);
   j.KBool("terminal_trade_allowed",(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED));
   j.KBool("mql_trade_allowed",(bool)MQLInfoInteger(MQL_TRADE_ALLOWED));
   j.KNum("aggregate_risk_to_stop",agg,2);
   j.KNum("risk_mult_applied",g_risk_mult,3);
   j.KInt("events_count",ArraySize(g_ev_t)); if(ArraySize(g_ev_t)>0) j.KTime("events_last_utc",g_ev_t[ArraySize(g_ev_t)-1]); else j.KNull("events_last_utc");
   j.Key("ftmo"); j.BeginObj();
     j.KNum("initial_balance",g_ftmo_initial,2); j.KStr("day_key",g_ftmo_day_key); j.KNum("day_start_balance",g_ftmo_day_bal,2); j.KNum("day_start_equity",g_ftmo_day_eq,2);
     j.KNum("daily_floor",FtmoDailyFloor(),2); j.KNum("max_floor",FtmoMaxFloor(),2); j.KNum("buffer",g_ftmo_buf_pct*g_ftmo_initial,2);
     j.KNum("headroom_daily",AccountInfoDouble(ACCOUNT_EQUITY)-agg-FtmoDailyFloor(),2); j.KNum("headroom_max",AccountInfoDouble(ACCOUNT_EQUITY)-agg-FtmoMaxFloor(),2);
   j.EndObj();
   j.Key("open_positions"); j.BeginArr();
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].posid>0 && !g_rows[i].closed && PositionSelectByTicket((ulong)g_rows[i].posid))
        { j.BeginObj(); j.KInt("posid",g_rows[i].posid); j.KInt("signal_id",g_rows[i].id); j.KStr("strategy",g_rows[i].strategy);
          j.KInt("direction",g_rows[i].direction); j.KNum("open_r",OpenR(i),3); j.KNum("banked_r",g_rows[i].r_multiple,3);
          j.KNum("lots",PositionGetDouble(POSITION_VOLUME),2); j.KBool("banked",g_rows[i].banked); j.KBool("ratcheted",g_rows[i].ratcheted);
          j.EndObj(); }
   j.EndArr();
   j.KInt("parked_signal_id",(g_live_parked ? g_delayed_id : 0));
   j.KInt("last_signal_id",g_sig_seq);
   j.KBool("in_tester",(bool)MQLInfoInteger(MQL_TESTER));
   j.Key("alerts"); j.BeginArr();
   if(unprot>0) j.Str(StringFormat("unprotected_position:%d",unprot));
   for(int a=0;a<ArraySize(g_live_alerts);a++) j.Str(g_live_alerts[a]);
   if(!(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) j.Str("terminal_trade_not_allowed");
   if(!(bool)MQLInfoInteger(MQL_TRADE_ALLOWED)) j.Str("mql_trade_not_allowed");
   j.EndArr();
   j.EndObj();
   AtomicWriteText(LivePath("heartbeat_"+_Symbol+".json"),j.Text());
  }

//--- LIVE init: runs from OnInit (never waits for tick 1). Mirrors the tester's first-tick block
//--- but points the journals at live\journal\ (§11-5: never mixed with tester files; monthly
//--- rotation lands in Slice 5) and never applies the AA_ prefix.
void LiveInit()
  {
   if(g_live_inited) return;
   g_account_login=AccountInfoInteger(ACCOUNT_LOGIN);
   LiveEnsureDirs();
   g_started=true;
   g_start_time=TimeCurrent(); g_last_time=g_start_time;
   g_journal_part    =LivePath(StringFormat("journal\\%s_%s.part.csv",_Symbol,StampCompact(g_start_time)));
   g_actions_part    =LivePath(StringFormat("journal\\%s_%s.actions.part.csv",_Symbol,StampCompact(g_start_time)));
   g_inv_journal_part=LivePath(StringFormat("journal\\%s_%s.inv.part.csv",_Symbol,StampCompact(g_start_time)));
   g_inv_actions_part=LivePath(StringFormat("journal\\%s_%s.inv.actions.part.csv",_Symbol,StampCompact(g_start_time)));
   if(!g_ev_loaded){ LoadEconEvents(); if(InpShowEvents) DrawEconEvents(); }
   LiveLoadConfig();
   LiveFtmoLoad();
   if(InpLiveSelfTest) AtomicWriteText(LivePath("config\\trading_enabled.json"),"{\"trading_enabled\":true}");   // self-test owns its switch
   LiveReadKillSwitch();
   LiveAckOrphanedDone();
   LiveRestoreState();          // G4: rows/actions/parked from state\, reconciled vs the terminal, orphans adopted
   WriteJournal(g_journal_part);
   if(InpShowSwings) DrawSwingMarkers();
   g_live_inited=true;
   AuditLine("restart","","","","ok","",StringFormat("build=%s tester=%d selftest=%d login=%I64d",EA_BUILD,(int)MQLInfoInteger(MQL_TESTER),(int)InpLiveSelfTest,g_account_login));
   WriteHeartbeat();
  }

//--- G5: the 1s timer drives polling and the heartbeat independent of ticks. Throttles use
//--- TimeCurrent() deltas (simulated seconds in the tester, wall-clock live).
void OnTimer()
  {
   if(!g_active || !InpLiveMode) return;
   if(!g_live_inited) LiveInit();
   datetime now=LiveNow();
   //--- the kill-switch FILE is re-read at the poll cadence (<=5s, same as tasks); its VALUE is honoured
   //--- on every decision (E10). Reading it every second was 15M file reads per tester-year for nothing.
   if(now-g_live_last_poll>=InpTaskPollSec)
     {
      g_live_last_poll=now;
      LiveReadKillSwitch();
      LiveLoadRiskMult();
      LiveFtmoDayReset();      // no file I/O unless the FTMO day key changed
      LiveProcessTasks();
     }
   //--- positions/ view: refreshed on every state change (journal hook) and at the heartbeat cadence
   if(now-g_live_last_beat>=InpHeartbeatSec)
     {
      g_live_last_beat=now; LiveFtmoLoad();
      //--- calendar: econ_events.csv is refreshed daily by the monitor (ForexFactory feed). Reload once a day
      //--- after 03:00 UTC (the refresh runs 02:30) so every instance sees the new coverage without a restart.
      if(!(bool)MQLInfoInteger(MQL_TESTER))
        {
         if(ArraySize(g_ev_t)==0){ LoadEconEvents(); AuditLine("events_reload","","","",(ArraySize(g_ev_t)>0?"ok":"empty"),"retry",StringFormat("rows=%d",ArraySize(g_ev_t))); }   // a failed/racing open retries every heartbeat
         string dk=TimeToString(now,TIME_DATE);
         if(g_ev_day=="") g_ev_day=dk;
         else if(dk!=g_ev_day && (now%86400)>=3*3600)
           { g_ev_day=dk; LoadEconEvents(); AuditLine("events_reload","","","","ok","",StringFormat("rows=%d last=%s",ArraySize(g_ev_t),(ArraySize(g_ev_t)>0?IsoTime(g_ev_t[ArraySize(g_ev_t)-1]):"-"))); }
        }
      LiveWritePositions(); WriteHeartbeat();
     }
   if(InpLiveSelfTest) SelfTestTick();
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   bool in_tester = (bool)MQLInfoInteger(MQL_TESTER);
   bool in_visual = (bool)MQLInfoInteger(MQL_VISUAL_MODE);
   bool dll_ok    = (bool)MQLInfoInteger(MQL_DLLS_ALLOWED);
   bool auto_mode = (InpAutoApprove!=AA_NONE);

#ifdef LIVE
   const bool live_build=true;
#else
   const bool live_build=false;
#endif
   //--- MODE SELECTION (Phase 3 M1 truth table; docs/phase3-live-system-requirements.md §3/§10/§11).
   //--- Tester-era rules are unchanged; LIVE mode adds the file-queue path and must NEVER combine
   //--- with auto-approve (the hybrid covenant: the EA cannot enter without a trader task, E7).
   if(InpLiveMode)
     {
      if(!live_build)
        { Print("INERT: InpLiveMode requires the -live build (this build statically imports the popup DLL)."); g_active=false; return(INIT_SUCCEEDED); }
      if(auto_mode)
        { Print("INERT: never auto-trade live - InpLiveMode with InpAutoApprove=ALL/SKIP is refused."); g_active=false; return(INIT_SUCCEEDED); }
      if(InpLiveSelfTest && !in_tester)
        { Print("INERT: InpLiveSelfTest is tester-only."); g_active=false; return(INIT_SUCCEEDED); }
      //--- §11-8: a live chart with AutoTrading disarmed stays ACTIVE so the heartbeat can REPORT
      //--- terminal_trade_allowed / mql_trade_allowed = false (the quietest outage must be visible);
      //--- order placement is refused at execution (trading_disabled) until it is armed.
      if(!in_tester && (!(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !(bool)MQLInfoInteger(MQL_TRADE_ALLOWED)))
         Print("WARNING: live chart but AutoTrading NOT allowed (terminal=",(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED),
               " mql=",(bool)MQLInfoInteger(MQL_TRADE_ALLOWED),") - heartbeat will report it; approvals refused until armed.");
     }
   else
     {
      //--- HARD RULE: auto-approve (no modal) is TESTER-ONLY; the interactive
      //--- modal path additionally needs visual mode + DLLs. Never auto-trade live.
      if(!in_tester)
        {
         Print("HybridForwardTest runs only in the Strategy Tester - staying INERT.");
         g_active=false; return(INIT_SUCCEEDED);
        }
      if(live_build && !auto_mode)
        { Print("INERT: the -live build has no popup dialog - set InpLiveMode=true (queue) or InpAutoApprove=ALL/SKIP (headless)."); g_active=false; return(INIT_SUCCEEDED); }
      if(!auto_mode && (!in_visual || !dll_ok))
        {
         Print("Interactive mode needs visual tester + DLLs (or set InpAutoApprove=ALL/SKIP for ",
               "headless). MQL_VISUAL_MODE=",in_visual," MQL_DLLS_ALLOWED=",dll_ok," - staying INERT.");
         g_active=false; return(INIT_SUCCEEDED);
        }
     }
   g_active=true;

   //--- build detector list in PRIORITY order (SMC > Fib > EMA)
   g_ndet=0;
   if(InpUseSMC) g_detectors[g_ndet++]=new CLiquiditySweepMSS(InpSmcMinRR,InpSmcTpR,InpRiskPct);
   if(InpUseFib) g_detectors[g_ndet++]=new CDeepFibRetrace(InpFibImpulseATR,InpFibMinRR,InpRiskPct);
   if(InpUseEMArevQ) g_detectors[g_ndet++]=new CEma20MeanRevQ(InpEmaStretch,InpEmaAdxCeil,InpEmaMinRR,InpRiskPct);  // priority 3 (above base EMArev if both on)
   if(InpUseEMA) g_detectors[g_ndet++]=new CEma20MeanRev(InpEmaStretch,InpEmaAdxCeil,InpEmaMinRR,InpRiskPct);
   if(InpUseShock) g_detectors[g_ndet++]=new CShockContinuation(InpShockAtr,InpShockPullAtr,InpShockTpMult,InpShockMinRR,InpRiskPct,InpShockTrig,InpShockCalGate);
   if(InpUseEmaRevInv) g_detectors[g_ndet++]=new CEmaRevInverse(InpEmaStretch,InpEmaAdxCeil,InpEmaMinRR,InpRiskPct);
   if(InpUseTrendCont) g_detectors[g_ndet++]=new CTrendContinuation(InpRiskPct);
   if(g_ndet==0) Print("WARNING: no detectors enabled.");

   //--- D1 regime indicators (coach Phase-2.5 gate): 200-EMA + ADX(14) on the daily.
   g_h_ema200_d1=iMA(_Symbol,PERIOD_D1,200,0,MODE_EMA,PRICE_CLOSE);
   g_h_adx_d1  =iADX(_Symbol,PERIOD_D1,14);
   if(g_h_ema200_d1==INVALID_HANDLE || g_h_adx_d1==INVALID_HANDLE)
      Print("WARNING: D1 regime indicator handle failed - regime column will be blank.");

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviation);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   g_last_bar=iTime(_Symbol,g_tf,0);
   g_started =false;

   string mode=(InpLiveMode ? (InpLiveSelfTest ? "LIVE-SELFTEST(queue,tester)" : (in_tester ? "LIVE-IN-TESTER(queue)" : "LIVE(queue)"))
              : (auto_mode? (InpAutoApprove==AA_ALL?"AUTO-APPROVE(headless)":"AUTO-SKIP(headless)")
                          : "INTERACTIVE"));
   Print("HybridForwardTest ACTIVE [",mode,"] on ",_Symbol," detectors=",g_ndet,
         " risk=",DoubleToString(InpRiskPct*100.0,1),"%");
   //--- build tag: if the popup misbehaves, confirm THIS line appears (fresh EA)
   //--- and that MT5 was restarted so the matching TradeDialog.dll is loaded.
   //--- the mid-trade panel needs the same footing as the interactive dialog
   //--- (real window + human): interactive mode, visual tester, DLLs allowed.
   //--- Headless AA_ALL/AA_SKIP runs never create a window.
   g_can_panel = (g_active && !auto_mode && in_visual && dll_ok && InpManagePanel && !InpLiveMode);   // live: no panel DLL

   Print("HFT build 2026-08-07a: mid-trade management panel (Close / Close50 / SL->BE) +"
         " day-of-week signal time (needs matching TradeDialog.dll - restart MT5 after a rebuild).");

   //--- Item 8 (coach 2026-09-10): DISPLAY-ONLY three-EMA overlay (EMA20 gold / EMA50
   //--- DeepSkyBlue / EMA200 violet) on the chart window. Added to chart 0 so it appears
   //--- identically on the live chart (ChartScreenShot) and in the tester-window capture
   //--- (os_shot_daemon PrintWindow). No signal/logic role. Skipped in headless AA (no chart);
   //--- the D1 counterpart lives in inbox_bridge.render_d1 (same colours).
   if(!auto_mode || in_visual || InpLiveMode)   // live: the signal JSON needs the EMA values (E4)
     {
      g_h_e20 =iMA(_Symbol,PERIOD_CURRENT, 20,0,MODE_EMA,PRICE_CLOSE);
      g_h_e50 =iMA(_Symbol,PERIOD_CURRENT, 50,0,MODE_EMA,PRICE_CLOSE);
      g_h_e200=iMA(_Symbol,PERIOD_CURRENT,200,0,MODE_EMA,PRICE_CLOSE);
      if(g_h_e20==INVALID_HANDLE || g_h_e50==INVALID_HANDLE || g_h_e200==INVALID_HANDLE)
         Print("WARNING: 3-EMA overlay iMA handle failed err=",GetLastError()," - H4 EMAs not shown.");
      else
         Print("HybridTriEMA overlay = object-drawn EMA20/50/200 on chart 0.");
     }
   //--- PHASE 3 LIVE: a 1s timer drives task polling (<=5s) + heartbeat (<=60s) independent of
   //--- ticks (G5 - an idle symbol can go minutes without a tick). LiveInit runs NOW (not on
   //--- tick 1) so the timer never fires before the queue dirs / config / events / state exist.
   if(InpLiveMode)
     {
      if(!EventSetTimer(1)) Print("WARNING: EventSetTimer(1) failed err=",GetLastError()," - live polling/heartbeat will not run.");
      LiveInit();
     }
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Item 2 (coach 2026-09-08): per-tick MFE + ordered path, journal-only |
//| (no behaviour change). mfe_r = max favorable excursion in R; path    |
//| records up-crossings of 0.25/0.5/0.75/1/1.5/2/3 R, 'B' each recross   |
//| below entry, and a terminal (TP/BE/SL/end) at close. Model-4 ticks =  |
//| true touch order; the MFE/recovery tables compute exactly from path.  |
//+------------------------------------------------------------------+
const double PATH_TH[7]={0.25,0.5,0.75,1.0,1.5,2.0,3.0};   // reference thresholds (analysis-side)
//--- Item 2 MFE/recovery tracker. One graded position open at a time (one-setup lock),
//--- tracked in GLOBAL DOUBLES (which persist reliably; struct-array string accumulation
//--- does not in the tester). Snapshot an immutable entry/risk basis at position-open so a
//--- scale-out partial / BE move can't reset it. Journal-only, no behaviour change.
long   g_mfe_posid=0; int g_mfe_rowidx=-1, g_mfe_dir=0; bool g_mfe_dipped=false;
double g_mfe_entry=0, g_mfe_risk=0, g_mfe_max=0, g_mfe_pre=0, g_mfe_post=0;
//--- STUDY-ONLY shadow-v2 observer state (globals; snapshot immutable levels at open).
//--- 0 pre-bank, 1 runner-open, 2 done. trigR=min(1,tp1_R) if two-target else 1.0;
//--- padR = BE-pad in R (mirrors BEPrice, so the runner's BE stop matches the live rule).
int    g_v2_state=2; double g_v2_trigR=1.0, g_v2_tp2R=0.0, g_v2_padR=0.0, g_v2_bank=0.0, g_v2_lastfav=0.0;
//--- TP1-ratchet study path trackers (per open position; one-setup lock => one at a time).
double g_rt_tp1R=0.0, g_rt_tp2R=0.0; bool g_rt_touched1=false, g_rt_reached2=false, g_rt_redip1=false;

//--- STUDY-ONLY trigger-impulse feature: max H4 bar range over the 3 bars preceding the
//--- signal, in ATR(14) units (shift-1 ATR); imp_nbig counts those >= 1.5*ATR.
void ComputeImpulse(int n)
  {
   g_rows[n].imp_atr=0.0; g_rows[n].imp_nbig=0;
   double atr=SignalATR(); if(atr<=0.0) return;
   double mx=0.0; int nb=0;
   for(int s=1;s<=3;s++)
     { double rg=iHigh(_Symbol,g_tf,s)-iLow(_Symbol,g_tf,s);
       if(rg>mx) mx=rg;
       if(rg>=1.5*atr) nb++; }
   g_rows[n].imp_atr=mx/atr; g_rows[n].imp_nbig=nb;
  }

//--- STUDY-ONLY calendar label: 1 if a symbol-relevant HIGH-impact (class V/W) event
//--- falls within +/-4h (+/-1 H4 bar) of the signal. Uses the EA's already-loaded,
//--- server-time-aligned, class-baked econ cache (g_ev_*) scoped to base/quote/All.
int CalLabeled(datetime sigtime)
  {
   string base,quote; SymbolCcy(base,quote);
   for(int i=0;i<ArraySize(g_ev_t);i++)
     {
      if(g_ev_cls[i]!="V" && g_ev_cls[i]!="W") continue;
      if(g_ev_ccy[i]!=base && g_ev_ccy[i]!=quote && g_ev_ccy[i]!="All") continue;
      long d=(long)g_ev_t[i]-(long)sigtime; if(d<0) d=-d;
      if(d<=4*3600) return 1;
     }
   return 0;
  }
void MfeTerminal(int idx,string term)
  {
   if(idx!=g_mfe_rowidx || g_mfe_posid==0) return;
   g_rows[idx].mfe_r=g_mfe_max; g_rows[idx].pre_dip_r=g_mfe_pre;
   g_rows[idx].post_dip_r=g_mfe_post; g_rows[idx].dipped=(g_mfe_dipped?1:0);
   g_rows[idx].terminal=term;
   //--- STUDY-ONLY: real position closed with v2 still open (only the test-boundary
   //--- force-close reaches here unresolved) -> mark to the last favR.
   if(!InpV2Exit && g_v2_state<2)
     {
      if(g_v2_state==1)   // banked, runner open -> mark the runner half
        { g_rows[idx].v2_bank=g_v2_bank; g_rows[idx].v2_r=g_v2_bank+0.5*g_v2_lastfav; }
      else                // never banked -> whole position marked
        g_rows[idx].v2_r=g_v2_lastfav;
      g_rows[idx].v2_runner=3;
      g_v2_state=2;
     }
   g_mfe_posid=0; g_mfe_rowidx=-1;
  }
void TrackAllMfePath()
  {
   int oi=-1;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].posid>0 && !g_rows[i].closed){ oi=i; break; }
   if(oi<0) return;
   if(g_rows[oi].posid!=g_mfe_posid)
     { g_mfe_posid=g_rows[oi].posid; g_mfe_rowidx=oi; g_mfe_dir=g_rows[oi].direction;
       g_mfe_entry=g_rows[oi].entry; g_mfe_risk=g_rows[oi].risk_px;
       g_mfe_max=0; g_mfe_pre=0; g_mfe_post=0; g_mfe_dipped=false;
       //--- TP1-RATCHET STUDY (all modes): snapshot the runner's TP1/TP2 in R at open and reset
       //--- the path flags. tp1R>0 only when the detector has a real TP1 (scale-out); else the
       //--- runner has no TP1 to ratchet to and B1/B2 are N/A (rt_tp1R=0 marks that in Python).
       if(g_mfe_risk>0.0)
         {
          int dd=g_mfe_dir;
          g_rt_tp1R=(g_rows[oi].tp1>0.0 ? dd*(g_rows[oi].tp1-g_mfe_entry)/g_mfe_risk : 0.0);
          g_rt_tp2R=(g_rows[oi].tp2>0.0 ? dd*(g_rows[oi].tp2-g_mfe_entry)/g_mfe_risk
                                        : dd*(g_rows[oi].tp -g_mfe_entry)/g_mfe_risk);
          g_rt_touched1=false; g_rt_reached2=false; g_rt_redip1=false;
          g_rows[oi].rt_tp1R=g_rt_tp1R; g_rows[oi].rt_tp2R=g_rt_tp2R;
          g_rows[oi].rt_touched1=0; g_rows[oi].rt_reached2=0; g_rows[oi].rt_redip1=0;
          g_rows[oi].rt_bankr=-99.0;   // sentinel: overwritten with OpenR when the +1R bank fires
         }
       //--- STUDY-ONLY: snapshot the v2 observer's immutable thresholds at open.
       if(!InpV2Exit && g_mfe_risk>0.0)
         {
          int d=g_mfe_dir;
          double tp1R=(g_rows[oi].partial_frac>0.0 && g_rows[oi].tp1>0.0
                        ? d*(g_rows[oi].tp1-g_mfe_entry)/g_mfe_risk : 1.0);
          g_v2_trigR=(g_rows[oi].partial_frac>0.0 && g_rows[oi].tp1>0.0 ? MathMin(1.0,tp1R) : 1.0);
          g_v2_tp2R =(g_rows[oi].tp2>0.0 ? d*(g_rows[oi].tp2-g_mfe_entry)/g_mfe_risk
                                         : d*(g_rows[oi].tp -g_mfe_entry)/g_mfe_risk);
          g_v2_padR =InpBEPadPips*PipSize()/g_mfe_risk;
          g_v2_bank=0.0; g_v2_lastfav=0.0; g_v2_state=0;
          g_rows[oi].v2_r=0.0; g_rows[oi].v2_bank=0.0; g_rows[oi].v2_runner=-1;
         }
     }
   if(g_mfe_risk<=0.0) return;
   double exitpx=(g_mfe_dir>0? SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   double favR=((g_mfe_dir>0? exitpx-g_mfe_entry : g_mfe_entry-exitpx))/g_mfe_risk;
   if(favR>g_mfe_max) g_mfe_max=favR;
   if(!g_mfe_dipped)
     { if(favR>g_mfe_pre) g_mfe_pre=favR;
       if(favR<=0.0 && g_mfe_pre>=0.25) g_mfe_dipped=true; }
   else
     { if(favR>g_mfe_post) g_mfe_post=favR; }
   g_rows[oi].mfe_r=g_mfe_max; g_rows[oi].pre_dip_r=g_mfe_pre;
   g_rows[oi].post_dip_r=g_mfe_post; g_rows[oi].dipped=(g_mfe_dipped?1:0);
   //--- TP1-RATCHET STUDY (all modes): update the runner's TP1/TP2 path flags from the LIVE
   //--- favR. redip = fell back to/below TP1 AFTER touching it and BEFORE reaching TP2 (the
   //--- prior-tick was1 guard stops the arming tick itself from counting as a redip). This is
   //--- the ONE ordering fact the mfe_r max cannot carry.
   if(g_rows[oi].tp1>0.0)
     {
      bool was1=g_rt_touched1;
      if(favR>=g_rt_tp1R) g_rt_touched1=true;
      if(g_rt_tp2R>0.0 && favR>=g_rt_tp2R) g_rt_reached2=true;
      if(was1 && !g_rt_reached2 && favR<=g_rt_tp1R) g_rt_redip1=true;
      g_rows[oi].rt_touched1=(g_rt_touched1?1:0);
      g_rows[oi].rt_reached2=(g_rt_reached2?1:0);
      g_rows[oi].rt_redip1=(g_rt_redip1?1:0);
     }
   //--- STUDY-ONLY: shadow doctrine-v2 exit. Bank 50% at the first touch of trigR
   //--- (fire-tick favR, mirroring the live +1.0x overshoot); runner (50%) to tp2 with
   //--- a BE stop at padR. v2's stop is never looser than the real (old-exit) stop, so
   //--- the observer always resolves within the real position's lifetime.
   if(!InpV2Exit && g_v2_state<2)
     {
      g_v2_lastfav=favR;
      if(g_v2_state==0)
        {
         if(favR<=-1.0)
           { g_rows[oi].v2_bank=0.0; g_rows[oi].v2_r=-1.0; g_rows[oi].v2_runner=2; g_v2_state=2; }
         else if(favR>=g_v2_trigR)
           { g_v2_bank=0.5*favR; g_v2_state=1;
             if(favR>=g_v2_tp2R)   // bank + tp2 same tick
               { g_rows[oi].v2_bank=g_v2_bank; g_rows[oi].v2_r=g_v2_bank+0.5*g_v2_tp2R;
                 g_rows[oi].v2_runner=1; g_v2_state=2; } }
        }
      else if(g_v2_state==1)
        {
         if(favR>=g_v2_tp2R)
           { g_rows[oi].v2_bank=g_v2_bank; g_rows[oi].v2_r=g_v2_bank+0.5*g_v2_tp2R;
             g_rows[oi].v2_runner=1; g_v2_state=2; }
         else if(favR<=g_v2_padR)   // runner stopped at break-even (pad ~0)
           { g_rows[oi].v2_bank=g_v2_bank; g_rows[oi].v2_r=g_v2_bank;
             g_rows[oi].v2_runner=0; g_v2_state=2; }
        }
     }
  }

void OnTick()
  {
   if(!g_active) return;
   EnsureTriEMA();   // item 8: keep the 3-EMA overlay on-chart (tester strips it on template reapply)

   //--- econ-event lines: parse the calendar once, then redraw on each new bar
   //--- (below) so the forward window rolls with the replay. ALWAYS load (the
   //--- approval popup's upcoming-events list needs g_ev_loaded, and the popup
   //--- goes to the operator, not the blind advisor) — InpShowEvents gates only
   //--- the on-CHART lines, which would leak event names into the advisor shot.
   if(!g_ev_loaded) { LoadEconEvents(); if(InpShowEvents) DrawEconEvents(); }
   LiveInvalidatePendingsThroughSL();   // live only: resting pending order whose SL traded before fill -> deleted + invalidated

   if(!g_started && !InpLiveMode)   // live: LiveInit() already did this in OnInit (live\journal paths)
     {
      g_started=true;
      g_start_time=TimeCurrent(); g_last_time=g_start_time;
      // §3.8: headless AA-mode journals get an "AA_" prefix so mt5_verify --mode ALL
      // can never overwrite the trader's own interactive journal for the same symbol.
      string jpfx=(InpAutoApprove!=AA_NONE ? "AA_" : "");
      g_journal_part=StringFormat("journal\\%s%s_%s.part.csv",jpfx,_Symbol,StampCompact(g_start_time));
      g_actions_part=StringFormat("journal\\%s%s_%s.actions.part.csv",jpfx,_Symbol,StampCompact(g_start_time));
      //--- isolated inverse-cohort journals (never mixed with the graded stream)
      g_inv_journal_part=StringFormat("journal\\%s%s_%s.inv.part.csv",jpfx,_Symbol,StampCompact(g_start_time));
      g_inv_actions_part=StringFormat("journal\\%s%s_%s.inv.actions.part.csv",jpfx,_Symbol,StampCompact(g_start_time));
      WriteJournal(g_journal_part);
      if(InpShowSwings) DrawSwingMarkers();   // first draw (new-bar gate skips tick 1)
     }
   g_last_time=TimeCurrent();

   //--- two-target management runs EVERY tick (a bar can blow through TP1)
   ManageOpenPositions();
   TrackAllMfePath();          // Item 2: per-tick MFE + path (journal-only)
   //--- age out unfilled pending orders (edited-entry setups) every tick
   ExpireStalePendings();

   //--- mid-trade management panel: poll/execute/refresh EVERY tick and tear it
   //--- down the moment we go flat. MUST run above the new-bar gate: a position
   //--- that closes mid-bar frees HandleSignal on the same tick, so the panel
   //--- has to be gone before a fresh signal can open the approval dialog.
   if(g_can_panel) ManagePanelTick();

   //--- new-bar gate: detection only when a fresh H4 bar has closed
   datetime bar0=iTime(_Symbol,g_tf,0);
   if(bar0==g_last_bar) return;
   g_last_bar=bar0;

   //--- INVERSE time exit: auto-close at 12 H4 bars unless the trader hit EXTEND.
   if(g_inv_open && !g_inv_extended)
     {
      int ibars=(int)((bar0-g_inv_entry_bar)/PeriodSeconds(g_tf));
      if(ibars>=12) CloseInverse("AUTO12");
     }

   //--- §10.1 weekend-flat (broker quotes Mon-Fri; the .dk import is 24/7). At the first weekend bar: close any open
   //--- position (that bar's open == the Friday close in continuous crypto data), then take no signals until Monday.
   if(InpWeekendFlat)
     {
      MqlDateTime wd; TimeToStruct(bar0,wd);
      bool weekend=(wd.day_of_week==0 || wd.day_of_week==6);
      if(weekend)
        {
         for(int i=ArraySize(g_rows)-1;i>=0;i--)
            if(g_rows[i].posid>0 && !g_rows[i].closed && PositionSelectByTicket((ulong)g_rows[i].posid))
              { Print("Weekend-flat: closing signal #",g_rows[i].id," at the weekend boundary (§10.1)."); ManualClose(i); }
         for(int i=ArraySize(g_rows)-1;i>=0;i--)
            if(g_rows[i].is_pending && g_rows[i].posid==0 && !g_rows[i].closed && g_rows[i].order_ticket>0)
              { g_trade.OrderDelete((ulong)g_rows[i].order_ticket); g_rows[i].decision="expired"; g_rows[i].closed=true;
                Print("Weekend-flat: cancelled unfilled pending #",g_rows[i].id," (§10.1)."); WriteJournal(g_journal_part); }
         return;                      // no detectors, no delay replay: the weekend does not exist for this instrument
        }
     }
   //--- roll the econ-event overlay forward with the replay (reveals upcoming
   //--- events within the lookahead; bias stays hidden until each one fires)
   if(InpShowEvents) DrawEconEvents();
   //--- persistent swing markers over the rolling window (signal-independent)
   if(InpShowSwings) DrawSwingMarkers();

   //--- a DELAYED signal takes priority on the new bar: re-present the SAME candidate
   //--- (same id, same entry/SL/TP) so the operator can watch how price developed.
   if(InpLiveMode && ParkCount()>0)
     {
      for(int si=0;si<MAX_PARKS;si++)
        {
         if(!g_slots[si].active) continue;
         int psid=g_slots[si].park.sid; ParkLoad(si);
         g_delay_pending=false; g_delay_replaying=true;
         HandleSignal(g_delayed);                       // §11-3 invalidation / implicit delay / code 8, per signal
         if(g_live_parked && g_park.sid==psid) ParkStoreCurrent(); else ParkClear(psid);
        }
      LiveRecomputeParked();
      if(ParkCount()>=g_cfg_max_parks) return;        // all slots taken: detectors wait (max_parks=1 = the old rule)
     }
   else if(g_delay_pending)
     {
      g_delay_pending=false; g_delay_replaying=true;
      //--- re-snap the chart HARD RIGHT to the just-closed (current) bar before the
      //--- dialog re-opens, so both the operator's view and the advisor's capture show
      //--- how price developed during the delay - not where the chart sat last bar.
      ChartSetInteger(0,CHART_AUTOSCROLL,true);
      ChartNavigate(0,CHART_END,0);
      ChartRedraw(0);
      HandleSignal(g_delayed);
      return;   // detectors wait while a delayed signal is still being decided
     }

   //--- call ALL detectors every bar so each advances its state machine;
   //--- keep the highest-priority valid emit (array is in priority order).
   SignalCandidate best; bool have=false;
   for(int i=0;i<g_ndet;i++)
     {
      SignalCandidate c;
      bool v=g_detectors[i].Detect(_Symbol,g_tf,c);
      if(v && c.valid && !have) { best=c; have=true; }
     }
   if(!have) return;

   //--- ONE active setup per symbol: suppress a new emit while a position is live
   //--- OR while a pending order (edited-entry setup) is still resting/unfilled.
   if(HasActiveOrderOrPosition())
     {
      Print("Signal from ",best.strategy," suppressed - a position or pending order is already active (one setup/symbol).");
      return;
     }
   HandleSignal(best);
   if(InpLiveMode && g_live_parked) ParkStoreCurrent();
  }

//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong t=PositionGetTicket(i);
      if(t==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol &&
         PositionGetInteger(POSITION_MAGIC)==InpMagic)
         return true;
     }
   return false;
  }

//--- a resting, unfilled pending order counts as an active setup too
bool HasActiveOrderOrPosition()
  {
   if(HasOpenPosition()) return true;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].is_pending && g_rows[i].order_ticket>0
         && g_rows[i].posid<=0 && !g_rows[i].closed)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
//| Cancel unfilled pending orders older than InpPendingExpiryBars H4 |
//| bars and journal them as "expired". A fill (posid>0) or a prior    |
//| close short-circuits. Proactive OrderDelete => we own the outcome. |
//+------------------------------------------------------------------+
//--- LIVE (trader ruling 2026-09-17): a resting pending order is invalidated when price trades through its SL before it
//--- fills - the same rule as a parked signal (§11-3). Checked every tick. Delete first; mark only when the broker confirms
//--- (if the order already filled in the meantime, the fill binding owns the outcome).
void LiveInvalidatePendingsThroughSL()
  {
   if(!InpLiveMode || (bool)MQLInfoInteger(MQL_TESTER)) return;
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if(!g_rows[i].is_pending || g_rows[i].posid>0 || g_rows[i].closed || g_rows[i].order_ticket<=0 || g_rows[i].sl<=0.0) continue;
      double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID), ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      bool through=(g_rows[i].direction>0 ? (bid>0.0 && bid<=g_rows[i].sl) : (ask>0.0 && ask>=g_rows[i].sl));
      string how="tick";
      //--- history since placement (once a minute): catches a breach that happened during a restart/deploy and reversed
      static datetime last_scan=0;
      if(!through && TimeCurrent()-last_scan>=60 && g_rows[i].placed_time>0)
        {
         datetime from=(datetime)(g_rows[i].placed_time+60-(g_rows[i].placed_time%60));   // first FULL minute after placement
         double hh[],ll[]; double spr=MathMax(0.0,ask-bid);
         int nh=CopyHigh(_Symbol,PERIOD_M1,from,TimeCurrent(),hh), nl=CopyLow(_Symbol,PERIOD_M1,from,TimeCurrent(),ll);
         if(g_rows[i].direction<0){ for(int k=0;k<nh;k++) if(hh[k]+spr>=g_rows[i].sl){ through=true; how="m1_history"; break; } }   // bars are bid: ask ~ bid high + spread
         else                     { for(int k=0;k<nl;k++) if(ll[k]<=g_rows[i].sl){ through=true; how="m1_history"; break; } }
        }
      if(!through) continue;
      if(!OrderSelect((ulong)g_rows[i].order_ticket)) continue;             // already filled or gone: binding/expiry decide
      bool del=g_trade.OrderDelete((ulong)g_rows[i].order_ticket);
      if(!del && !LiveDone()){ AuditLine("invalidate_failed","","",StringFormat("sig:%d",g_rows[i].id),"error",StringFormat("order_delete:%d",g_trade.ResultRetcode()),""); continue; }
      g_rows[i].decision="rejected"; g_rows[i].terminal="invalidated"; g_rows[i].closed=true; g_rows[i].is_pending=false;
      string sp=LivePath(StringFormat("signals\\%s-%d.json",_Symbol,g_rows[i].id)); string js=ReadTextFile(sp);
      if(js!="")
        {
         StringReplace(js,"\"status\":\"approved_pending\"","\"status\":\"rejected\"");
         StringReplace(js,"\"auto_reason\":\"\"",StringFormat("\"auto_reason\":\"invalidated: price through SL %s while the pending order rested\"",DoubleToString(g_rows[i].sl,_Digits)));
         AtomicWriteText(sp,js);
        }
      AuditLine("invalidated","","",StringFormat("sig:%d",g_rows[i].id),"rejected","pending_sl_through",StringFormat("via=%s ticket=%I64d sl=%s bid=%s ask=%s",how,g_rows[i].order_ticket,DoubleToString(g_rows[i].sl,_Digits),DoubleToString(bid,_Digits),DoubleToString(ask,_Digits)));
      Print("Signal #",g_rows[i].id," pending order INVALIDATED (",how,"): price through SL ",DoubleToString(g_rows[i].sl,_Digits)," before fill - order deleted");
      WriteJournal(g_journal_part);
     }
   LivePendingScanStamp();
  }
void LivePendingScanStamp(){ }   // (placeholder kept trivial: the per-row static throttles the history scan to once a minute)

void ExpireStalePendings()
  {
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if(!g_rows[i].is_pending || g_rows[i].posid>0 || g_rows[i].closed) continue;
      if(g_rows[i].order_ticket<=0 || g_rows[i].placed_time<=0) continue;
      int age=iBarShift(_Symbol,g_tf,g_rows[i].placed_time,false);   // H4 bars since placed
      if(age<InpPendingExpiryBars) continue;
      bool del=g_trade.OrderDelete((ulong)g_rows[i].order_ticket);
      g_rows[i].decision="expired";
      g_rows[i].closed=true;
      Print("Signal #",g_rows[i].id," pending EXPIRED after ",age," H4 bars (unfilled)",
            (del?"":"  [OrderDelete rc="+IntegerToString(g_trade.ResultRetcode())+"]"));
      WriteJournal(g_journal_part);
     }
  }

//+------------------------------------------------------------------+
//| If the two-target split would leave either leg below the broker's |
//| min volume, fall back to a single target. Runs at fill time (works |
//| for both market fills and later pending fills).                    |
//+------------------------------------------------------------------+
void MinLotSplitGuard(int n)
  {
   if(g_rows[n].partial_frac<=0.0) return;
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);  if(vmin<=0)vmin=0.01;
   double pv=MathFloor((g_rows[n].partial_frac*g_rows[n].lots)/step)*step;
   if(pv<vmin || (g_rows[n].lots-pv)<vmin)
     { g_rows[n].tp1_done=true; g_rows[n].partial_frac=0.0;
       Print("Signal #",g_rows[n].id," min-lot: cannot scale out - single target."); }
  }

string OrderTypeName(ENUM_ORDER_TYPE t)
  {
   switch(t)
     {
      case ORDER_TYPE_BUY_LIMIT:  return "BUY LIMIT";
      case ORDER_TYPE_BUY_STOP:   return "BUY STOP";
      case ORDER_TYPE_SELL_LIMIT: return "SELL LIMIT";
      case ORDER_TYPE_SELL_STOP:  return "SELL STOP";
      default:                    return "MARKET";
     }
  }

//--- ATR(14) on the working timeframe (last CLOSED bar); 0 if unavailable
double SignalATR()
  {
   int h=iATR(_Symbol,g_tf,14);
   if(h==INVALID_HANDLE) return 0.0;
   double a[]; ArraySetAsSeries(a,true);
   if(CopyBuffer(h,0,0,2,a)<2) return 0.0;
   return a[1];
  }

//--- minimum acceptable stop distance: an account-safety floor on RISK (the R:R
//--- floor screens reward, not risk). A stop tighter than this is rejected so a
//--- degenerate signal can't feed the 1%-risk sizer a near-zero distance and
//--- produce a monster position.
double MinStopDist(double atr)
  {
   double spread=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID);
   if(spread<0.0) spread=0.0;
   double stops=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   double m=InpMinStopATR*atr;
   double sp=InpMinStopSpreads*spread;
   if(sp>m)    m=sp;
   if(stops>m) m=stops;
   return m;
  }

//--- log a rejected (never-shown) signal to the journal so the coach can see it
//--- and the underlying bug is never silently hidden.
void JournalReject(int id,SignalCandidate &cand,string why)
  {
   int n=ArraySize(g_rows); ArrayResize(g_rows,n+1);
   g_rows[n].id=id; g_rows[n].time=cand.zone_to; g_rows[n].symbol=_Symbol;
   g_rows[n].strategy=cand.strategy; g_rows[n].direction=cand.direction;
   g_rows[n].orig_entry=cand.entry; g_rows[n].orig_sl=cand.sl; g_rows[n].orig_tp=cand.tp;
   g_rows[n].orig_tp1=cand.tp1; g_rows[n].orig_tp2=cand.tp2;
   g_rows[n].regime=g_sig_regime; g_rows[n].with_trend=g_sig_with_trend;
   g_rows[n].entry=cand.entry; g_rows[n].sl=cand.sl; g_rows[n].tp=cand.tp;
   g_rows[n].tp1=cand.tp1; g_rows[n].tp2=cand.tp2; g_rows[n].partial_frac=0.0;
   g_rows[n].lots=0.0; g_rows[n].risk_px=MathAbs(cand.entry-cand.sl);
   g_rows[n].decision="rejected"; g_rows[n].skip_reason=0; g_rows[n].edited=false;
   g_rows[n].is_pending=false; g_rows[n].order_ticket=0; g_rows[n].placed_time=0;
   g_rows[n].tp1_done=true; g_rows[n].decision_ms=0; g_rows[n].posid=0;
   g_rows[n].closed=true; g_rows[n].exit_time=0; g_rows[n].exit_price=0.0;
   g_rows[n].pnl=0.0; g_rows[n].r_multiple=0.0;
   g_rows[n].regime=g_sig_regime; g_rows[n].with_trend=g_sig_with_trend; g_rows[n].decision_class=g_sig_class;
   g_rows[n].to_entry=g_to_entry; g_rows[n].to_sl=g_to_sl; g_rows[n].to_tp1=g_to_tp1; g_rows[n].to_tp2=g_to_tp2;
   g_rows[n].mfe_r=0.0; g_rows[n].pre_dip_r=0.0; g_rows[n].post_dip_r=0.0; g_rows[n].dipped=0; g_rows[n].terminal="";
   g_rows[n].imp_atr=0.0; g_rows[n].imp_nbig=0; g_rows[n].cal_lab=0; g_rows[n].v2_r=0.0; g_rows[n].v2_bank=0.0; g_rows[n].v2_runner=-1;
   //--- ArrayResize does NOT zero new struct elements: the reject path must init the runner/ratchet
   //--- study fields too, else rejected rows carry memory garbage in rt_* (non-deterministic journal
   //--- bytes; found by the Phase-3 M1 parity gate 2026-09-15). Behaviour-neutral: no reader uses
   //--- these columns on a rejected row.
   g_rows[n].banked=false; g_rows[n].closed_vol=0.0; g_rows[n].ratcheted=false; g_rows[n].rt_bankr=-99.0;
   g_rows[n].rt_tp1R=0.0; g_rows[n].rt_tp2R=0.0; g_rows[n].rt_touched1=0; g_rows[n].rt_reached2=0; g_rows[n].rt_redip1=0;
   g_rows[n].live=InpLiveMode; g_rows[n].account_id=g_account_login; g_rows[n].risk_pct_gate=InpRiskPct; g_rows[n].risk_mult_applied=g_risk_mult; g_rows[n].auto_skip=0; g_rows[n].entry_mode="rejected";
   g_rows[n].stop_pre_floor=g_floor_pre; g_rows[n].stop_post_floor=g_floor_post; g_rows[n].floor_applied=g_floor_applied;
   Print("Signal #",id," ",cand.strategy," ",DirStr(cand.direction)," REJECTED: ",why);
   WriteJournal(g_journal_part);
  }

//+------------------------------------------------------------------+
//| Size -> overlays -> dialog -> execute (market or pending) -> log   |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| FROZEN regime tag (coach Phase-2.5, 2026-09) - DO NOT ALTER.       |
//| On D1 at the LAST CLOSED bar (shift 1):                            |
//|   TREND_UP   if close > 200-EMA AND 200-EMA > its value 10 D1 bars |
//|              ago AND ADX(14) >= 20                                 |
//|   TREND_DOWN mirrored ; else CHOP.                                 |
//| with_trend = signal direction matches the trend direction (blank   |
//| in CHOP). Warm-up / unready buffers -> BLANK (never a fake CHOP).  |
//| Same formula everywhere; frozen once shipped.                      |
//+------------------------------------------------------------------+
void ComputeRegime(int dir,string &regime,string &wt)
  {
   regime=""; wt="";
   if(g_h_ema200_d1==INVALID_HANDLE || g_h_adx_d1==INVALID_HANDLE){ Print("Regime: no D1 handle - blank"); return; }
   double ema[],adx[]; ArraySetAsSeries(ema,true); ArraySetAsSeries(adx,true);
   if(CopyBuffer(g_h_ema200_d1,0,1,11,ema)<11){ Print("Regime: D1 EMA200 not ready (need ~210 D1 bars) - blank"); return; }
   if(CopyBuffer(g_h_adx_d1,0,1,1,adx)<1){ Print("Regime: D1 ADX not ready - blank"); return; }
   double ema1=ema[0], ema11=ema[10];          // shift 1 (last closed) and shift 11 (10 bars earlier)
   double c1=iClose(_Symbol,PERIOD_D1,1);
   double a1=adx[0];
   if(ema1==EMPTY_VALUE || ema11==EMPTY_VALUE || a1==EMPTY_VALUE || c1<=0.0){ Print("Regime: D1 buffers empty - blank"); return; }
   if(c1>ema1 && ema1>ema11 && a1>=20.0)      regime="TREND_UP";
   else if(c1<ema1 && ema1<ema11 && a1>=20.0) regime="TREND_DOWN";
   else                                        regime="CHOP";
   if(regime=="CHOP") wt="";
   else wt=(((regime=="TREND_UP") && dir>0) || ((regime=="TREND_DOWN") && dir<0)) ? "1" : "0";
  }

//+------------------------------------------------------------------+
//| Protocol decision-class (coach 2026-09-09). MECHANICAL, no drift: |
//|   TAKE       = SweepMSS or DeepFib in a TREND regime tag.         |
//|   DISCRETION = everything else — every TrendCont signal (all      |
//|                regimes), EMArev, and ANY signal under CHOP or a    |
//|                blank/warm-up tag. Blank/warm-up is DISCRETION by   |
//|                construction (fails both TREND tests), never TAKE.  |
//| The trader reads his obligation off the popup; he never derives   |
//| it from the tag himself.                                          |
//+------------------------------------------------------------------+
string ClassifyProtocol(string strat,string regime)
  {
   bool is_trend =(regime=="TREND_UP" || regime=="TREND_DOWN");
   bool take_strat=(strat=="SweepMSS" || strat=="DeepFib");
   return (take_strat && is_trend) ? "TAKE" : "DISCRETION";
  }

//--- Market-entry re-validation (item 3, shared by the popup's Market-now click and the live
//--- approve task): entry at the current market price must keep valid geometry, clear the
//--- strategy's MinRR floor to the runner target, and respect the min stop distance. Same
//--- computations and order as the original inline block; `why` names the first failure.
bool ValidateMarketEntry(SignalCandidate &cand,double &en,double &rrn,string &why)
  {
   double mkn=(cand.direction>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID));
   en=NormPrice(mkn);
   double otpn=(cand.partial_fraction>0.0 && cand.tp1>0.0 && cand.tp2>0.0 ? cand.tp2 : cand.tp);
   rrn=(cand.direction>0 ? (otpn-en)/(en-cand.sl) : (en-otpn)/(cand.sl-en));
   double minstopn=MinStopDist(SignalATR());
   bool geom_ok=(cand.direction>0 ? (cand.sl<en && en<otpn) : (otpn<en && en<cand.sl));
   why="";
   if(!geom_ok || rrn<StratMinRR(cand.strategy) || MathAbs(en-cand.sl)<minstopn)
     {
      why=(!geom_ok ? "price past a level" :
           (MathAbs(en-cand.sl)<minstopn ? "stop too tight" :
            StringFormat("R:R %.1f < min %.1f",rrn,StratMinRR(cand.strategy))));
      return false;
     }
   return true;
  }


//+==================================================================+
//| PHASE 3 LIVE - Slice 2: election gate, signal publication, park/ |
//| replay (implicit delay, code 8, SL-through invalidation).         |
//+==================================================================+
//--- G1: NO-HOLD (class W) row for a binding currency inside the election horizon. Symbol-scoped
//--- via SymbolCcy (base/quote/"All"); W rows re-anchored to that day's midnight exactly as
//--- BuildEventBlocks does. Fail CLOSED when the calendar is not loaded.
bool ElectionGateHit(datetime now,string &ev_name,datetime &ev_t)
  {
   ev_name=""; ev_t=0;
   if(!g_ev_loaded || ArraySize(g_ev_t)==0){ ev_name="events_not_loaded"; return true; }
   if(g_cfg_election_days<=0) return false;
   string base,quote; SymbolCcy(base,quote);
   datetime tmax=now+(datetime)((long)g_cfg_election_days*86400);
   for(int i=0;i<ArraySize(g_ev_t);i++)
     {
      if(g_ev_cls[i]!="W") continue;
      if(g_ev_ccy[i]!=base && g_ev_ccy[i]!=quote && g_ev_ccy[i]!="All") continue;
      MqlDateTime mt; TimeToStruct(g_ev_t[i],mt); mt.hour=0; mt.min=0; mt.sec=0;
      datetime rt=StructToTime(mt);
      if(rt>now && rt<=tmax){ ev_name=g_ev_ccy[i]+" "+g_ev_name[i]; ev_t=rt; return true; }
     }
   return false;
  }
//--- English event labels (the guide's four classes; letters are retired from displays)
string EventLabel(string cls)
  {
   if(cls=="W") return "NO-HOLD (election)";
   if(cls=="V") return "NO ENTRY <6h (big release)";
   if(cls=="C") return "caution";
   if(cls=="H") return "holiday/thin";
   return "";
  }
//--- coarse instrument class for the advisor's library reads (mirrors pipeline/inbox_bridge.asset_class)
string AssetClass()
  {
   string r=SymbolRoot(); StringToUpper(r);
   if(StringFind(r,"OIL")>=0 || r=="WTI" || r=="BRENT" || r=="XTIUSD" || r=="XBRUSD" || r=="XNGUSD") return "energy";
   if(StringFind(r,"XAU")==0 || StringFind(r,"XAG")==0 || r=="GOLD" || r=="SILVER") return "metal";
   string idx[]={"US30","US100","US500","US2000","NAS100","USTEC","SPX500","NDX","SPX","GER40","DE40","UK100","JP225","EU50","AUS200","HK50"};
   for(int i=0;i<ArraySize(idx);i++) if(r==idx[i]) return "equity index";
   string majors[]={"EUR","GBP","USD","JPY","CHF","AUD","CAD","NZD"};
   if(StringLen(r)==6)
     {
      string a=StringSubstr(r,0,3), b=StringSubstr(r,3,3); bool ma=false, mb=false;
      for(int i=0;i<ArraySize(majors);i++){ if(a==majors[i]) ma=true; if(b==majors[i]) mb=true; }
      if(ma && mb) return "FX major";
      if(ma || mb) return "FX cross";
     }
   return "(unclassified)";
  }
//--- the popup's day-of-week line, reproduced for the signal file
string SigTimeString(datetime dnow,datetime zone_to)
  {
   string dows[]={"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
   MqlDateTime ndt; TimeToStruct(dnow,ndt);
   string s=StringFormat("%s  %s UTC",dows[ndt.day_of_week],TimeToString(dnow,TIME_MINUTES));
   int delays=(int)((dnow-zone_to)/PeriodSeconds(g_tf))-1;
   if(delays>0) s+=StringFormat("  (+%d bar%s)",delays,(delays==1?"":"s"));
   return s;
  }
string SessionName(datetime t)
  { MqlDateTime d; TimeToStruct(t,d); int h=d.hour;
    return (h<7 ? "Asia" : (h<13 ? "London" : (h<21 ? "New York" : "late/off-hours"))); }
//--- events panel as JSON objects: the SAME filter as BuildEventBlocks (forward window, notable,
//--- per-symbol base/quote/All), plus the English label and a `binding` flag
//--- (W inside the election horizon, or V inside 6h).
void BuildEventJson(datetime sig,CJsonW &j)
  {
   string base,quote; SymbolCcy(base,quote);
   datetime tmax=sig+(datetime)((long)InpEvtListDays*86400);
   int cnt=0;
   j.Key("events"); j.BeginArr();
   for(int i=0;i<ArraySize(g_ev_t);i++)
     {
      datetime t=g_ev_t[i];
      if(t<sig || t>tmax) continue;
      if(!g_ev_top[i]) continue;
      if(g_ev_ccy[i]!=base && g_ev_ccy[i]!=quote && g_ev_ccy[i]!="All") continue;
      datetime rt=t;
      if(g_ev_cls[i]=="W"){ MqlDateTime mt; TimeToStruct(t,mt); mt.hour=0; mt.min=0; mt.sec=0; rt=StructToTime(mt); }
      double hrs=((double)((long)rt-(long)sig))/3600.0; if(hrs<0.0) hrs=0.0;
      bool binding=((g_ev_cls[i]=="W" && hrs<=g_cfg_election_days*24.0) || (g_ev_cls[i]=="V" && hrs<6.0));
      j.BeginObj();
      j.KTime("t_utc",t); j.KTime("anchor_utc",rt); j.KNum("hours_until",hrs,1);
      j.KStr("ccy",g_ev_ccy[i]); j.KStr("name",g_ev_name[i]); j.KStr("cls",g_ev_cls[i]);
      j.KStr("sig",SigTag(ClassSig(g_ev_cls[i],g_ev_name[i]))); j.KStr("label",EventLabel(g_ev_cls[i])); j.KBool("binding",binding);
      j.EndObj();
      if(++cnt>=InpEvtListMax) break;
     }
   j.EndArr();
   j.KInt("events_count",cnt);
  }
//--- E4: signals/<SYMBOL>-<id>.json - everything the popup showed plus what a server-side
//--- renderer and the live advisor bundle need (symbol, date, class:, events, exposure, deadline,
//--- last 500 H4 + 500 D1 bars, overlay geometry, EMA20/50/200). Rewritten on every state change.
void WriteSignalJson(string status,string auto_reason)
  {
   SignalCandidate c=g_delayed;
   int id=g_park.sid;
   datetime bar0=iTime(_Symbol,g_tf,0);
   bool two_target=(c.partial_fraction>0.0 && c.tp1>0.0 && c.tp2>0.0);
   double risk=MathAbs(c.entry-c.sl);
   double rr_runner=(risk>0.0 ? MathAbs((two_target?c.tp2:c.tp)-c.entry)/risk : 0.0);
   double rr_tp1   =(risk>0.0 && c.tp1>0.0 ? MathAbs(c.tp1-c.entry)/risk : 0.0);
   double atr=SignalATR();
   datetime deadline=bar0+(datetime)((long)MathMax(1,g_cfg_max_age_bars-g_park.implicit_streak)*PeriodSeconds(g_tf));
   string evn=""; datetime evt=0; bool egate=ElectionGateHit(bar0,evn,evt);

   CJsonW j; j.BeginObj();
   j.KInt("schema_version",LIVE_SCHEMA_VERSION); j.KStr("ea_build",EA_BUILD);
   j.KStr("symbol",_Symbol); j.KStr("symbol_root",SymbolRoot()); j.KStr("asset_class",AssetClass());
   j.KInt("signal_id",id); j.KStr("signal_key",StringFormat("%s-%d",_Symbol,id));
   j.KStr("status",status); j.KStr("auto_reason",auto_reason);
   j.KTime("signal_time",c.zone_to); j.KTime("decision_bar",bar0); j.KTime("published_at",g_park.published_at);
   j.KStr("sigtime_text",SigTimeString(bar0,c.zone_to)); j.KStr("session",SessionName(bar0));
   j.KStr("strategy",c.strategy); j.KStr("strategy_text",c.strategy+(c.d1_context?"  [D1 aligned]":"")+(c.comment!=""?" - "+c.comment:""));
   j.KStr("direction",DirStr(c.direction)); j.KInt("direction_sign",c.direction);
   j.Key("levels"); j.BeginObj();
     j.KNum("entry",c.entry,_Digits); j.KNum("sl",c.sl,_Digits); j.KNum("tp",c.tp,_Digits);
     j.KNum("tp1",c.tp1,_Digits); j.KNum("tp2",c.tp2,_Digits); j.KNum("partial_fraction",c.partial_fraction,2);
     j.KBool("stop_entry",c.stop_entry); j.KBool("two_target",two_target);
   j.EndObj();
   j.Key("true_orig"); j.BeginObj();
     j.KNum("entry",g_to_entry,_Digits); j.KNum("sl",g_to_sl,_Digits); j.KNum("tp1",g_to_tp1,_Digits); j.KNum("tp2",g_to_tp2,_Digits);
   j.EndObj();
   j.Key("rr"); j.BeginObj();
     j.KNum("detector",c.rr,2); j.KNum("runner",rr_runner,2); j.KNum("tp1",rr_tp1,2); j.KNum("floor",StratMinRR(c.strategy),2);
   j.EndObj();
   j.Key("sizing"); j.BeginObj();
     j.KNum("lots",g_park.lots,2); j.KNum("risk_pct_gate",InpRiskPct,4); j.KNum("risk_mult_applied",g_risk_mult,3);
     j.KNum("risk_pct_effective",InpRiskPct*g_risk_mult,4); j.KStr("lots_line",LotsLine(g_park.lots,c.entry,c.sl,atr));
     j.KNum("sl_atr",(atr>0.0? risk/atr : 0.0),2); j.KNum("atr14",atr,_Digits);
   j.EndObj();
   j.Key("regime"); j.BeginObj();
     j.KStr("tag",g_sig_regime); j.KStr("with_trend",g_sig_with_trend); j.KStr("pretty",RegimePretty());
   j.EndObj();
   j.KStr("decision_class",g_sig_class);
   j.KStr("protocol_text",(g_sig_class=="TAKE"
      ? "PROTOCOL: TAKE - TREND regime, gate-class strategy (both directions)"
      : "PROTOCOL: DISCRETION - non-gate strategy or CHOP/blank; trader's read"));
   BuildEventJson(bar0,j);
   j.Key("election_gate"); j.BeginObj(); j.KBool("hit",egate); j.KStr("event",evn); if(evt>0) j.KTime("anchor_utc",evt); else j.KNull("anchor_utc"); j.EndObj();
   //--- exposure: this symbol's open graded position(s) + parked state (cross-symbol view = heartbeat aggregate)
   int unprot=0; double agg=AggregateRiskToStop(unprot);
   j.Key("exposure"); j.BeginObj();
     j.KNum("aggregate_risk_to_stop",agg,2); j.KInt("account_positions",PositionsTotal());
     j.Key("open_positions"); j.BeginArr();
     for(int i=0;i<ArraySize(g_rows);i++)
        if(g_rows[i].posid>0 && !g_rows[i].closed && PositionSelectByTicket((ulong)g_rows[i].posid))
          { j.BeginObj(); j.KInt("posid",g_rows[i].posid); j.KInt("signal_id",g_rows[i].id); j.KStr("strategy",g_rows[i].strategy);
            j.KStr("direction",DirStr(g_rows[i].direction)); j.KNum("open_r",OpenR(i),3); j.EndObj(); }
     j.EndArr();
   j.EndObj();
   j.KTime("deadline",deadline); j.KInt("max_age_bars",g_cfg_max_age_bars);
   j.KInt("delay_count",g_delay_count); j.KInt("implicit_streak",g_park.implicit_streak);
   j.Key("entry_modes"); j.BeginArr(); j.Str("market"); j.Str("pending"); j.EndArr();   // live: market now, or a pending order at the original entry (waits for price to return)
   j.KBool("trading_enabled",g_trading_enabled);
   //--- overlay geometry (what DrawOverlays draws) + EMA values at the last closed bar
   j.Key("overlay"); j.BeginObj();
     j.Key("zone"); j.BeginObj(); j.KTime("from",c.zone_from); j.KTime("to",c.zone_to); j.KNum("hi",c.zone_hi,_Digits); j.KNum("lo",c.zone_lo,_Digits); j.EndObj();
     j.Key("zone2"); j.BeginObj(); j.KNum("hi",c.zone2_hi,_Digits); j.KNum("lo",c.zone2_lo,_Digits); j.EndObj();
     j.Key("leg"); j.BeginObj(); j.KTime("t0",c.leg_t0); j.KNum("p0",c.leg_p0,_Digits); j.KTime("t1",c.leg_t1); j.KNum("p1",c.leg_p1,_Digits); j.EndObj();
     j.Key("aux"); j.BeginArr();
     for(int i=0;i<c.aux_count && i<8;i++){ j.BeginObj(); j.KNum("price",c.aux_price[i],_Digits); j.KStr("label",c.aux_label[i]); j.EndObj(); }
     j.EndArr();
     j.Key("swings_hi"); j.BeginArr();
     for(int i=0;i<c.n_swing_hi && i<10;i++){ j.BeginObj(); j.KTime("t",c.swing_hi_t[i]); j.KNum("p",c.swing_hi_p[i],_Digits); j.EndObj(); }
     j.EndArr();
     j.Key("swings_lo"); j.BeginArr();
     for(int i=0;i<c.n_swing_lo && i<10;i++){ j.BeginObj(); j.KTime("t",c.swing_lo_t[i]); j.KNum("p",c.swing_lo_p[i],_Digits); j.EndObj(); }
     j.EndArr();
     double e20[1],e50[1],e200[1]; bool eok=(CopyBuffer(g_h_e20,0,1,1,e20)==1 && CopyBuffer(g_h_e50,0,1,1,e50)==1 && CopyBuffer(g_h_e200,0,1,1,e200)==1);
     j.Key("ema_h4"); j.BeginObj();
     if(eok){ j.KNum("e20",e20[0],_Digits); j.KNum("e50",e50[0],_Digits); j.KNum("e200",e200[0],_Digits); } else { j.KNull("e20"); j.KNull("e50"); j.KNull("e200"); }
     j.EndObj();
   j.EndObj();
   //--- bars: last 500 H4 + 500 D1, oldest -> newest, [t,o,h,l,c,v]
   MqlRates r[]; ArraySetAsSeries(r,false);
   int nb=MathMax(0,MathMin(InpSignalBars,2000));
   int nh=(nb>0 ? CopyRates(_Symbol,PERIOD_H4,0,nb,r) : 0);
   j.Key("bars_h4"); j.BeginArr();
   for(int i=0;i<nh;i++){ j.BeginArr(); j.Int((long)r[i].time); j.Num(r[i].open,_Digits); j.Num(r[i].high,_Digits); j.Num(r[i].low,_Digits); j.Num(r[i].close,_Digits); j.Int(r[i].tick_volume); j.EndArr(); }
   j.EndArr();
   int nd=(nb>0 ? CopyRates(_Symbol,PERIOD_D1,0,nb,r) : 0);
   j.Key("bars_d1"); j.BeginArr();
   for(int i=0;i<nd;i++){ j.BeginArr(); j.Int((long)r[i].time); j.Num(r[i].open,_Digits); j.Num(r[i].high,_Digits); j.Num(r[i].low,_Digits); j.Num(r[i].close,_Digits); j.Int(r[i].tick_volume); j.EndArr(); }
   j.EndArr();
   j.KInt("bars_h4_count",nh); j.KInt("bars_d1_count",nd);
   j.EndObj();
   AtomicWriteText(LivePath(StringFormat("signals\\%s-%d.json",_Symbol,id)),j.Text());
  }
//--- parked-signal state file (restored by Slice 4)
void LiveSaveParked()
  {
   SignalCandidate c=g_delayed;
   CJsonW j; j.BeginObj();
   j.KInt("schema_version",LIVE_SCHEMA_VERSION); j.KInt("sid",g_park.sid); j.KInt("delay_count",g_delay_count);
   j.KInt("implicit_streak",g_park.implicit_streak); j.KBool("explicit_this_bar",g_park.explicit_this_bar);
   j.KTime("published_bar",g_park.published_bar); j.KTime("published_at",g_park.published_at); j.KNum("lots",g_park.lots,2);
   j.KNum("orig_entry",g_park.orig_entry,_Digits); j.KNum("orig_sl",g_park.orig_sl,_Digits); j.KNum("orig_tp",g_park.orig_tp,_Digits);
   j.KNum("orig_tp1",g_park.orig_tp1,_Digits); j.KNum("orig_tp2",g_park.orig_tp2,_Digits); j.KStr("caption",g_park.caption);
   j.KStr("regime",g_sig_regime); j.KStr("with_trend",g_sig_with_trend); j.KStr("decision_class",g_sig_class);
   j.KNum("to_entry",g_to_entry,_Digits); j.KNum("to_sl",g_to_sl,_Digits); j.KNum("to_tp1",g_to_tp1,_Digits); j.KNum("to_tp2",g_to_tp2,_Digits);
   j.Key("cand"); j.BeginObj();
     j.KStr("strategy",c.strategy); j.KInt("direction",c.direction); j.KNum("entry",c.entry,_Digits); j.KNum("sl",c.sl,_Digits);
     j.KNum("tp",c.tp,_Digits); j.KNum("tp1",c.tp1,_Digits); j.KNum("tp2",c.tp2,_Digits); j.KNum("rr",c.rr,3);
     j.KNum("partial_fraction",c.partial_fraction,3); j.KTime("zone_from",c.zone_from); j.KTime("zone_to",c.zone_to);
     j.KNum("zone_hi",c.zone_hi,_Digits); j.KNum("zone_lo",c.zone_lo,_Digits); j.KBool("stop_entry",c.stop_entry);
     j.KBool("d1_context",c.d1_context); j.KStr("comment",c.comment);
   j.EndObj();
   j.EndObj();
   AtomicWriteText(LivePath(StringFormat("state\\%s\\parked_%d.json",_Symbol,g_park.sid)),j.Text());
   ParkStoreCurrent();
  }
void LiveUnpark()
  {
   g_delay_pending=false;
   string f=LivePath(StringFormat("state\\%s\\parked_%d.json",_Symbol,g_park.sid));
   if(FileIsExist(f,FILE_COMMON)) FileDelete(f,FILE_COMMON);
   string legacy=LivePath("state\\"+_Symbol+"\\parked.json");
   if(FileIsExist(legacy,FILE_COMMON)) FileDelete(legacy,FILE_COMMON);
   ParkClear(g_park.sid); LiveRecomputeParked();
  }
//--- the live decision path (replaces the popup). Fresh signal: election gate, else publish+park.
//--- Bar-close replay (via the OnTick delay hook): §11-3 SL-through invalidation, else an implicit
//--- FREEZE delay (implicit=1) until a task arrives or max_age -> skipped code 8.
void LivePresent(int id,SignalCandidate &cand,double lots,bool is_replay,
                 double orig_entry,double orig_sl,double orig_tp,double orig_tp1,double orig_tp2)
  {
   string caption=StringFormat("Signal #%d  -  %s  %s",id,cand.strategy,DirStr(cand.direction));
   datetime bar0=iTime(_Symbol,g_tf,0);
   if(!is_replay)
     {
      g_park.sid=id; g_park.orig_entry=orig_entry; g_park.orig_sl=orig_sl; g_park.orig_tp=orig_tp;
      g_park.orig_tp1=orig_tp1; g_park.orig_tp2=orig_tp2; g_park.caption=caption; g_park.lots=lots;
      g_park.published_bar=bar0; g_park.published_at=TimeCurrent(); g_park.implicit_streak=0; g_park.explicit_this_bar=false;
      g_delayed=cand; g_delayed_id=id;
      //--- G1 election / NO-HOLD hard gate (entry refusal only; fail closed on no calendar)
      string evn=""; datetime evt=0;
      if(ElectionGateHit(bar0,evn,evt))
        {
         string reason=(evn=="events_not_loaded" ? "events_not_loaded" : "election:"+evn+(evt>0?" @"+IsoTime(evt):""));
         WriteSignalJson("auto_skipped",reason);
         AuditLine("auto_skip","","skip",StringFormat("sig:%d",id),"skipped","election_gate",reason);
         Print("Signal #",id," ",cand.strategy," AUTO-SKIPPED (code 2, election gate): ",reason);
         g_live_auto=1;
         CommitDecision(id,cand,caption,orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,false,2,0,false,false,"auto_skip");
         g_live_auto=0;
         LiveUnpark();
         return;
        }
      g_delay_pending=true; g_live_parked=true;
      WriteSignalJson("open","");
      LiveSaveParked();
      AuditLine("published","","",StringFormat("sig:%d",id),"open","",StringFormat("%s %s deadline=%s",cand.strategy,DirStr(cand.direction),
                IsoTime(bar0+(datetime)((long)g_cfg_max_age_bars*PeriodSeconds(g_tf)))));
      Print("Signal #",id," ",cand.strategy," ",DirStr(cand.direction)," PUBLISHED to queue - awaiting task (max_age ",g_cfg_max_age_bars," bars).");
      return;
     }
   //--- bar-close replay of a parked signal
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID), ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   bool through=(cand.direction>0 ? (bid<=cand.sl) : (ask>=cand.sl));
   if(through)
     {
      WriteSignalJson("rejected","invalidated: price through frozen SL while parked");
      AuditLine("invalidated","","",StringFormat("sig:%d",id),"rejected","sl_through","");
      JournalReject(id,cand,"invalidated: price through frozen SL while parked (live, §11-3)");
      LiveUnpark();
      return;
     }
   if(g_park.explicit_this_bar){ g_park.explicit_this_bar=false; g_park.implicit_streak=0; }
   else
     {
      g_delay_count++; g_park.implicit_streak++;
      WriteDelayLog(id,g_delay_count,cand,"delay",1);
      AuditLine("republished","","",StringFormat("sig:%d",id),"open","implicit_delay",StringFormat("streak=%d/%d",g_park.implicit_streak,g_cfg_max_age_bars));
     }
   if(g_park.implicit_streak>=g_cfg_max_age_bars)
     {
      WriteSignalJson("expired","no-response: max_age reached");
      AuditLine("expired_code8","","",StringFormat("sig:%d",id),"skipped","no_response",StringFormat("class=%s bars=%d%s",g_sig_class,g_park.implicit_streak,(g_sig_class=="TAKE"?" CHARGEABLE_VIOLATION":"")));   // §11.9: a TAKE left to expire is an illegal skip
      Print("Signal #",id," ",cand.strategy," EXPIRED (code 8, no response in ",g_cfg_max_age_bars," bars).");
      CommitDecision(id,cand,caption,orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,false,8,0,false,false,"expired");
      LiveUnpark();
      return;
     }
   g_delayed=cand; g_delay_pending=true; g_live_parked=true;   // re-park (the hook cleared it)
   WriteSignalJson("open","");
   LiveSaveParked();
  }



//+==================================================================+
//| PHASE 3 LIVE - Slice 4: restart survivability (G4) + positions/  |
//+==================================================================+
string g_live_alerts[];            // persistent heartbeat alerts (adopt_orphan, restore mismatch, ...)
void LiveAlert(string a){ int n=ArraySize(g_live_alerts); for(int i=0;i<n;i++) if(g_live_alerts[i]==a) return; ArrayResize(g_live_alerts,n+1); g_live_alerts[n]=a; }
string RowStatePath(int id){ return LivePath(StringFormat("state\\%s\\%d.json",_Symbol,id)); }

//--- one row -> flat JSON (the parser supports one nested level: actions live under "actions")
void LiveSaveRowState(int i)
  {
   JournalRow r=g_rows[i];
   CJsonW j; j.BeginObj();
   j.KInt("schema_version",LIVE_SCHEMA_VERSION); j.KStr("symbol",_Symbol);
   j.KInt("id",r.id); j.KInt("time",(long)r.time); j.KStr("strategy",r.strategy); j.KInt("direction",r.direction);
   j.KNum("orig_entry",r.orig_entry,_Digits); j.KNum("orig_sl",r.orig_sl,_Digits); j.KNum("orig_tp",r.orig_tp,_Digits);
   j.KNum("orig_tp1",r.orig_tp1,_Digits); j.KNum("orig_tp2",r.orig_tp2,_Digits);
   j.KNum("entry",r.entry,_Digits); j.KNum("sl",r.sl,_Digits); j.KNum("tp",r.tp,_Digits); j.KNum("tp1",r.tp1,_Digits); j.KNum("tp2",r.tp2,_Digits);
   j.KNum("partial_frac",r.partial_frac,4); j.KNum("lots",r.lots,2); j.KNum("risk_px",r.risk_px,_Digits);
   j.KBool("tp1_done",r.tp1_done); j.KBool("banked",r.banked); j.KBool("ratcheted",r.ratcheted); j.KNum("closed_vol",r.closed_vol,2);
   j.KStr("decision",r.decision); j.KInt("skip_reason",r.skip_reason); j.KBool("edited",r.edited); j.KBool("is_pending",r.is_pending);
   j.KInt("order_ticket",r.order_ticket); j.KInt("placed_time",(long)r.placed_time); j.KInt("decision_ms",r.decision_ms);
   j.KInt("posid",r.posid); j.KBool("closed",r.closed); j.KInt("exit_time",(long)r.exit_time); j.KNum("exit_price",r.exit_price,_Digits);
   j.KNum("pnl",r.pnl,2); j.KNum("r_multiple",r.r_multiple,6);
   j.KStr("regime",r.regime); j.KStr("with_trend",r.with_trend); j.KStr("decision_class",r.decision_class);
   j.KNum("to_entry",r.to_entry,_Digits); j.KNum("to_sl",r.to_sl,_Digits); j.KNum("to_tp1",r.to_tp1,_Digits); j.KNum("to_tp2",r.to_tp2,_Digits);
   j.KNum("mfe_r",r.mfe_r,4); j.KNum("pre_dip_r",r.pre_dip_r,4); j.KNum("post_dip_r",r.post_dip_r,4); j.KInt("dipped",r.dipped); j.KStr("terminal",r.terminal);
   j.KNum("imp_atr",r.imp_atr,4); j.KInt("imp_nbig",r.imp_nbig); j.KInt("cal_lab",r.cal_lab);
   j.KNum("v2_r",r.v2_r,4); j.KNum("v2_bank",r.v2_bank,4); j.KInt("v2_runner",r.v2_runner);
   j.KNum("rt_tp1R",r.rt_tp1R,4); j.KNum("rt_tp2R",r.rt_tp2R,4); j.KInt("rt_touched1",r.rt_touched1); j.KInt("rt_reached2",r.rt_reached2);
   j.KInt("rt_redip1",r.rt_redip1); j.KNum("rt_bankr",r.rt_bankr,4);
   j.KBool("live",r.live); j.KInt("account_id",r.account_id); j.KNum("risk_pct_gate",r.risk_pct_gate,4); j.KNum("risk_mult_applied",r.risk_mult_applied,3);
   j.KInt("auto_skip",r.auto_skip); j.KStr("entry_mode",r.entry_mode);
   j.Key("actions"); j.BeginObj();
   int na=0;
   for(int a=0;a<ArraySize(g_actions);a++)
     {
      if(g_actions[a].id!=r.id) continue;
      string px=StringFormat("%d_",na);
      j.KInt(px+"posid",g_actions[a].posid); j.KInt(px+"bar_time",(long)g_actions[a].bar_time); j.KStr(px+"action",g_actions[a].action);
      j.KNum(px+"price",g_actions[a].price,_Digits); j.KNum(px+"lots_before",g_actions[a].lots_before,2); j.KNum(px+"lots_after",g_actions[a].lots_after,2);
      j.KNum(px+"sl_after",g_actions[a].sl_after,_Digits); j.KNum(px+"banked_r",g_actions[a].banked_r,4); j.KNum(px+"open_r",g_actions[a].open_r,4);
      na++;
     }
   j.KInt("n",na);
   j.EndObj();
   j.EndObj();
   AtomicWriteText(RowStatePath(r.id),j.Text());
  }
//--- write every open row + any row whose state file does not exist yet (closed rows freeze after one write)
void LiveSaveAllState()
  {
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if(g_rows[i].symbol!=_Symbol) continue;
      if(!g_rows[i].closed || !FileIsExist(RowStatePath(g_rows[i].id),FILE_COMMON)) LiveSaveRowState(i);
      else if(g_rows[i].closed)
        {   // closed row (filled OR a pending that expired / was invalidated / cancelled unfilled): final state on disk once, then freeze
         string k[],v[],e; string t=ReadTextFile(RowStatePath(g_rows[i].id));
         if(JsonFlatParse(t,k,v,e) && JGet(k,v,"closed","false")!="true") LiveSaveRowState(i);
        }
     }
   AtomicWriteText(LivePath("state\\"+_Symbol+"\\seq.json"),StringFormat("{\"schema_version\":%d,\"sig_seq\":%d}",LIVE_SCHEMA_VERSION,g_sig_seq));
   LiveWritePositions();
  }
//--- positions/<SYM>-<posid>.json for the web app; moved to positions\closed\ on full close
void LiveWritePositions()
  {
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if(g_rows[i].posid<=0 || g_rows[i].symbol!=_Symbol) continue;
      string f=LivePath(StringFormat("positions\\%s-%I64d.json",_Symbol,g_rows[i].posid));
      bool open=(!g_rows[i].closed && PositionSelectByTicket((ulong)g_rows[i].posid));
      if(!open)
        {
         if(FileIsExist(f,FILE_COMMON)) FileMove(f,FILE_COMMON,LivePath(StringFormat("positions\\closed\\%s-%I64d.json",_Symbol,g_rows[i].posid)),FILE_COMMON|FILE_REWRITE);
         continue;
        }
      double oR=OpenR(i);
      CJsonW j; j.BeginObj();
      j.KInt("schema_version",LIVE_SCHEMA_VERSION); j.KStr("symbol",_Symbol); j.KInt("posid",g_rows[i].posid); j.KInt("signal_id",g_rows[i].id);
      j.KStr("strategy",g_rows[i].strategy); j.KStr("direction",DirStr(g_rows[i].direction)); j.KStr("decision_class",g_rows[i].decision_class);
      j.KNum("entry",g_rows[i].entry,_Digits); j.KNum("sl_risk_basis",g_rows[i].sl,_Digits);
      j.KNum("sl_live",PositionGetDouble(POSITION_SL),_Digits); j.KNum("tp_live",PositionGetDouble(POSITION_TP),_Digits);
      j.KNum("tp1",g_rows[i].tp1,_Digits); j.KNum("tp2",g_rows[i].tp2,_Digits);
      j.KNum("lots_init",g_rows[i].lots,2); j.KNum("lots_live",PositionGetDouble(POSITION_VOLUME),2);
      j.KNum("open_r",oR,3); j.KNum("banked_r",g_rows[i].r_multiple,3); j.KNum("closenow_r",oR+g_rows[i].r_multiple,3);
      j.KBool("banked",g_rows[i].banked); j.KBool("tp1_done",g_rows[i].tp1_done); j.KBool("ratcheted",g_rows[i].ratcheted);
      j.KBool("be_placeable",BEPlaceable(i)); j.KBool("ratchet_placeable",RatchetPlaceable(i));
      j.KInt("opened_at",(long)PositionGetInteger(POSITION_TIME)); j.KInt("bars_open",(int)((TimeCurrent()-(datetime)PositionGetInteger(POSITION_TIME))/PeriodSeconds(g_tf)));
      j.KTime("ts",LiveNow());
      j.EndObj();
      AtomicWriteText(f,j.Text());
     }
  }
//--- restore one row from its state file into g_rows[] (+ its actions into g_actions[])
bool LiveLoadRowState(string rel)
  {
   string k[],v[],e; string t=ReadTextFile(rel);
   if(!JsonFlatParse(t,k,v,e)) { Print("state: parse failed ",rel," ",e); return false; }
   if(JGet(k,v,"symbol","")!=_Symbol) return false;
   int n=ArraySize(g_rows); ArrayResize(g_rows,n+1);
   JournalRow r;
   r.id=(int)StringToInteger(JGet(k,v,"id","0")); r.time=(datetime)StringToInteger(JGet(k,v,"time","0")); r.symbol=_Symbol;
   r.strategy=JGet(k,v,"strategy",""); r.direction=(int)StringToInteger(JGet(k,v,"direction","0"));
   r.orig_entry=StringToDouble(JGet(k,v,"orig_entry")); r.orig_sl=StringToDouble(JGet(k,v,"orig_sl")); r.orig_tp=StringToDouble(JGet(k,v,"orig_tp"));
   r.orig_tp1=StringToDouble(JGet(k,v,"orig_tp1")); r.orig_tp2=StringToDouble(JGet(k,v,"orig_tp2"));
   r.entry=StringToDouble(JGet(k,v,"entry")); r.sl=StringToDouble(JGet(k,v,"sl")); r.tp=StringToDouble(JGet(k,v,"tp"));
   r.tp1=StringToDouble(JGet(k,v,"tp1")); r.tp2=StringToDouble(JGet(k,v,"tp2")); r.partial_frac=StringToDouble(JGet(k,v,"partial_frac"));
   r.lots=StringToDouble(JGet(k,v,"lots")); r.risk_px=StringToDouble(JGet(k,v,"risk_px"));
   r.tp1_done=(JGet(k,v,"tp1_done")=="true"); r.banked=(JGet(k,v,"banked")=="true"); r.ratcheted=(JGet(k,v,"ratcheted")=="true");
   r.closed_vol=StringToDouble(JGet(k,v,"closed_vol")); r.decision=JGet(k,v,"decision",""); r.skip_reason=(int)StringToInteger(JGet(k,v,"skip_reason","0"));
   r.edited=(JGet(k,v,"edited")=="true"); r.is_pending=(JGet(k,v,"is_pending")=="true"); r.order_ticket=StringToInteger(JGet(k,v,"order_ticket","0"));
   r.placed_time=(datetime)StringToInteger(JGet(k,v,"placed_time","0")); r.decision_ms=StringToInteger(JGet(k,v,"decision_ms","0"));
   r.posid=StringToInteger(JGet(k,v,"posid","0")); r.closed=(JGet(k,v,"closed")=="true");
   r.exit_time=(datetime)StringToInteger(JGet(k,v,"exit_time","0")); r.exit_price=StringToDouble(JGet(k,v,"exit_price"));
   r.pnl=StringToDouble(JGet(k,v,"pnl")); r.r_multiple=StringToDouble(JGet(k,v,"r_multiple"));
   r.regime=JGet(k,v,"regime",""); r.with_trend=JGet(k,v,"with_trend",""); r.decision_class=JGet(k,v,"decision_class","");
   r.to_entry=StringToDouble(JGet(k,v,"to_entry")); r.to_sl=StringToDouble(JGet(k,v,"to_sl")); r.to_tp1=StringToDouble(JGet(k,v,"to_tp1")); r.to_tp2=StringToDouble(JGet(k,v,"to_tp2"));
   r.mfe_r=StringToDouble(JGet(k,v,"mfe_r")); r.pre_dip_r=StringToDouble(JGet(k,v,"pre_dip_r")); r.post_dip_r=StringToDouble(JGet(k,v,"post_dip_r"));
   r.dipped=(int)StringToInteger(JGet(k,v,"dipped","0")); r.terminal=JGet(k,v,"terminal","");
   r.imp_atr=StringToDouble(JGet(k,v,"imp_atr")); r.imp_nbig=(int)StringToInteger(JGet(k,v,"imp_nbig","0")); r.cal_lab=(int)StringToInteger(JGet(k,v,"cal_lab","0"));
   r.v2_r=StringToDouble(JGet(k,v,"v2_r")); r.v2_bank=StringToDouble(JGet(k,v,"v2_bank")); r.v2_runner=(int)StringToInteger(JGet(k,v,"v2_runner","-1"));
   r.rt_tp1R=StringToDouble(JGet(k,v,"rt_tp1R")); r.rt_tp2R=StringToDouble(JGet(k,v,"rt_tp2R")); r.rt_touched1=(int)StringToInteger(JGet(k,v,"rt_touched1","0"));
   r.rt_reached2=(int)StringToInteger(JGet(k,v,"rt_reached2","0")); r.rt_redip1=(int)StringToInteger(JGet(k,v,"rt_redip1","0")); r.rt_bankr=StringToDouble(JGet(k,v,"rt_bankr","-99"));
   r.live=(JGet(k,v,"live")=="true"); r.account_id=StringToInteger(JGet(k,v,"account_id","0")); r.risk_pct_gate=StringToDouble(JGet(k,v,"risk_pct_gate","0.01"));
   r.risk_mult_applied=StringToDouble(JGet(k,v,"risk_mult_applied","1")); r.auto_skip=(int)StringToInteger(JGet(k,v,"auto_skip","0")); r.entry_mode=JGet(k,v,"entry_mode","");
   g_rows[n]=r;
   int na=(int)StringToInteger(JGet(k,v,"actions.n","0"));
   for(int a=0;a<na;a++)
     {
      string px=StringFormat("actions.%d_",a);
      int m=ArraySize(g_actions); ArrayResize(g_actions,m+1);
      g_actions[m].id=r.id; g_actions[m].posid=StringToInteger(JGet(k,v,px+"posid","0")); g_actions[m].bar_time=(datetime)StringToInteger(JGet(k,v,px+"bar_time","0"));
      g_actions[m].action=JGet(k,v,px+"action",""); g_actions[m].price=StringToDouble(JGet(k,v,px+"price")); g_actions[m].lots_before=StringToDouble(JGet(k,v,px+"lots_before"));
      g_actions[m].lots_after=StringToDouble(JGet(k,v,px+"lots_after")); g_actions[m].sl_after=StringToDouble(JGet(k,v,px+"sl_after"));
      g_actions[m].banked_r=StringToDouble(JGet(k,v,px+"banked_r")); g_actions[m].open_r=StringToDouble(JGet(k,v,px+"open_r"));
     }
   return true;
  }
//--- terminal = truth for EXISTENCE; file = truth for flags/R. Replays history OUT deals through the
//--- same ApplyExitDeal math for rows that finished while the EA was down; binds/expires pendings.
void LiveReconcile()
  {
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if(g_rows[i].symbol!=_Symbol || g_rows[i].closed) continue;
      //--- pending never bound: filled while down? cancelled/expired?
      if(g_rows[i].is_pending && g_rows[i].posid<=0 && g_rows[i].order_ticket>0)
        {
         if(OrderSelect((ulong)g_rows[i].order_ticket)) continue;   // still resting
         if(HistoryOrderSelect((ulong)g_rows[i].order_ticket))
           {
            long st=HistoryOrderGetInteger((ulong)g_rows[i].order_ticket,ORDER_STATE);
            if(st==ORDER_STATE_FILLED)
              {
               long pid=(long)HistoryOrderGetInteger((ulong)g_rows[i].order_ticket,ORDER_POSITION_ID);
               g_rows[i].posid=pid;
               if(HistorySelectByPosition(pid))
                  for(int d=0;d<HistoryDealsTotal();d++)
                    { ulong dk=HistoryDealGetTicket(d);
                      if(HistoryDealGetInteger(dk,DEAL_ENTRY)==DEAL_ENTRY_IN){ double fill=HistoryDealGetDouble(dk,DEAL_PRICE); if(fill>0.0){ g_rows[i].entry=fill; g_rows[i].risk_px=MathAbs(fill-g_rows[i].sl); } break; } }
               AuditLine("restore","","",StringFormat("sig:%d",g_rows[i].id),"pending_filled_while_down","",StringFormat("posid=%I64d",pid));
              }
            else { g_rows[i].decision="expired"; g_rows[i].closed=true; AuditLine("restore","","",StringFormat("sig:%d",g_rows[i].id),"pending_gone","",StringFormat("state=%d",st)); continue; }
           }
         else continue;
        }
      if(g_rows[i].posid<=0) continue;
      if(PositionSelectByTicket((ulong)g_rows[i].posid)) continue;   // still open: flags from the file stand
      //--- position gone: replay ALL its OUT deals from history (reset the accumulators first)
      if(!HistorySelectByPosition(g_rows[i].posid)){ AuditLine("restore","","",StringFormat("sig:%d",g_rows[i].id),"warn","no_history",StringFormat("posid=%I64d",g_rows[i].posid)); LiveAlert(StringFormat("restore_no_history:%d",g_rows[i].id)); continue; }
      g_rows[i].pnl=0.0; g_rows[i].r_multiple=0.0; g_rows[i].closed_vol=0.0;
      int nd=HistoryDealsTotal(); int applied=0;
      for(int d=0;d<nd;d++)
        {
         ulong dk=HistoryDealGetTicket(d);
         if(HistoryDealGetInteger(dk,DEAL_POSITION_ID)!=g_rows[i].posid) continue;
         if(HistoryDealGetInteger(dk,DEAL_ENTRY)!=DEAL_ENTRY_OUT) continue;
         ApplyExitDeal(i,dk); applied++;
        }
      AuditLine("restore","","",StringFormat("sig:%d",g_rows[i].id),(g_rows[i].closed?"closed_while_down":"partial_while_down"),"",StringFormat("deals=%d R=%.2f",applied,g_rows[i].r_multiple));
     }
  }
//--- positions with our magic + symbol and NO row: adopt conservatively (G4) and alert
void AdoptOrphans()
  {
   for(int p=PositionsTotal()-1;p>=0;p--)
     {
      ulong tk=PositionGetTicket(p); if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      long pid=(long)PositionGetInteger(POSITION_IDENTIFIER);
      if(RowIdxByPosid(pid)>=0) continue;
      //--- an approved row whose fill never bound (live async fill) owns the position that carries its caption
      string cm=PositionGetString(POSITION_COMMENT); int bound_row=-1;
      for(int i=0;i<ArraySize(g_rows) && bound_row<0;i++)
         if(g_rows[i].posid==0 && !g_rows[i].closed && g_rows[i].decision=="approved" && StringFind(cm,StringFormat("Signal #%d ",g_rows[i].id))==0) bound_row=i;
      if(bound_row>=0)
        {
         g_rows[bound_row].posid=pid; g_rows[bound_row].entry=PositionGetDouble(POSITION_PRICE_OPEN); g_rows[bound_row].risk_px=MathAbs(g_rows[bound_row].entry-g_rows[bound_row].sl);
         g_rows[bound_row].lots=PositionGetDouble(POSITION_VOLUME);
         AuditLine("fill_bound","","",StringFormat("pos:%I64d",pid),"ok","on_restart",StringFormat("sig=%d",g_rows[bound_row].id));
         continue;
        }
      int n=ArraySize(g_rows); ArrayResize(g_rows,n+1);
      JournalRow r;
      g_sig_seq++; r.id=g_sig_seq; r.time=(datetime)PositionGetInteger(POSITION_TIME); r.symbol=_Symbol; r.strategy="ADOPTED";
      r.direction=(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY ? 1 : -1);
      r.entry=PositionGetDouble(POSITION_PRICE_OPEN); r.sl=PositionGetDouble(POSITION_SL); r.tp=PositionGetDouble(POSITION_TP);
      r.orig_entry=r.entry; r.orig_sl=r.sl; r.orig_tp=r.tp; r.orig_tp1=0; r.orig_tp2=0; r.tp1=0; r.tp2=0; r.partial_frac=0.0;
      r.lots=PositionGetDouble(POSITION_VOLUME); r.risk_px=MathAbs(r.entry-r.sl);
      r.tp1_done=true; r.banked=false; r.ratcheted=false; r.closed_vol=0.0; r.decision="approved"; r.skip_reason=0; r.edited=false;
      r.is_pending=false; r.order_ticket=0; r.placed_time=0; r.decision_ms=0; r.posid=pid; r.closed=false;
      r.exit_time=0; r.exit_price=0.0; r.pnl=0.0; r.r_multiple=0.0; r.regime=""; r.with_trend=""; r.decision_class="";
      r.to_entry=r.entry; r.to_sl=r.sl; r.to_tp1=0; r.to_tp2=0; r.mfe_r=0; r.pre_dip_r=0; r.post_dip_r=0; r.dipped=0; r.terminal="";
      r.imp_atr=0; r.imp_nbig=0; r.cal_lab=0; r.v2_r=0; r.v2_bank=0; r.v2_runner=-1; r.rt_tp1R=0; r.rt_tp2R=0; r.rt_touched1=0; r.rt_reached2=0; r.rt_redip1=0; r.rt_bankr=-99.0;
      r.live=true; r.account_id=g_account_login; r.risk_pct_gate=InpRiskPct; r.risk_mult_applied=g_risk_mult; r.auto_skip=0; r.entry_mode="adopted";
      r.stop_pre_floor=0; r.stop_post_floor=0; r.floor_applied=0;
      g_rows[n]=r;
      AuditLine("adopt_orphan","","",StringFormat("pos:%I64d",pid),"adopted","",StringFormat("sig=%d %s lots=%.2f sl=%s",r.id,DirStr(r.direction),r.lots,DoubleToString(r.sl,_Digits)));
      LiveAlert(StringFormat("adopt_orphan:%I64d",pid));
      Print("LIVE: ADOPTED orphan position ",pid," as signal #",r.id," (un-banked, no BE, no ratchet) - coach alerted.");
     }
  }
//--- OnInit restore: seq -> rows/actions -> parked -> reconcile -> adopt. Runs BEFORE the first journal write.
void LiveRestoreState()
  {
   string sk[],sv[],se; string st=ReadTextFile(LivePath("state\\"+_Symbol+"\\seq.json"));
   if(JsonFlatParse(st,sk,sv,se)){ int q=(int)StringToInteger(JGet(sk,sv,"sig_seq","0")); if(q>g_sig_seq) g_sig_seq=q; }
   string fname; int nrows=0;
   long hf=FileFindFirst(LivePath("state\\"+_Symbol+"\\*.json"),fname,FILE_COMMON);
   if(hf!=INVALID_HANDLE)
     {
      string names[]; int nn=0;
      do { if(StringFind(fname,"parked")!=0 && fname!="seq.json" && StringFind(fname,".tmp")<0){ ArrayResize(names,nn+1); names[nn++]=fname; } } while(FileFindNext(hf,fname));
      FileFindClose(hf);
      //--- numeric order by id so g_rows[] keeps its historical order
      for(int i=0;i<nn;i++) for(int j=i+1;j<nn;j++) if(StringToInteger(names[j])<StringToInteger(names[i])){ string t=names[i]; names[i]=names[j]; names[j]=t; }
      for(int i=0;i<nn;i++) if(LiveLoadRowState(LivePath("state\\"+_Symbol+"\\"+names[i]))) nrows++;
     }
   //--- parked signals: one file per slot (parked_<sid>.json), plus the legacy single parked.json
   string pfiles[]; int npf=0; string pfn;
   long hpf=FileFindFirst(LivePath("state\\"+_Symbol+"\\parked*.json"),pfn,FILE_COMMON);
   if(hpf!=INVALID_HANDLE){ do { if(StringFind(pfn,".tmp")<0){ ArrayResize(pfiles,npf+1); pfiles[npf++]=pfn; } } while(FileFindNext(hpf,pfn)); FileFindClose(hpf); }
   bool parked=false;
   for(int pf=0;pf<npf && ParkCount()<MAX_PARKS;pf++)
     {
      string pk[],pv[],pe; string pt=ReadTextFile(LivePath("state\\"+_Symbol+"\\"+pfiles[pf]));
      if(pt=="" || !JsonFlatParse(pt,pk,pv,pe)) continue;
      SignalCandidate c; c.valid=true;
      c.strategy=JGet(pk,pv,"cand.strategy",""); c.direction=(int)StringToInteger(JGet(pk,pv,"cand.direction","0"));
      c.entry=StringToDouble(JGet(pk,pv,"cand.entry")); c.sl=StringToDouble(JGet(pk,pv,"cand.sl")); c.tp=StringToDouble(JGet(pk,pv,"cand.tp"));
      c.tp1=StringToDouble(JGet(pk,pv,"cand.tp1")); c.tp2=StringToDouble(JGet(pk,pv,"cand.tp2")); c.rr=StringToDouble(JGet(pk,pv,"cand.rr"));
      c.partial_fraction=StringToDouble(JGet(pk,pv,"cand.partial_fraction")); c.zone_from=IsoToTime(JGet(pk,pv,"cand.zone_from","")); c.zone_to=IsoToTime(JGet(pk,pv,"cand.zone_to",""));
      c.zone_hi=StringToDouble(JGet(pk,pv,"cand.zone_hi")); c.zone_lo=StringToDouble(JGet(pk,pv,"cand.zone_lo"));
      c.stop_entry=(JGet(pk,pv,"cand.stop_entry")=="true"); c.d1_context=(JGet(pk,pv,"cand.d1_context")=="true"); c.comment=JGet(pk,pv,"cand.comment","");
      g_delayed=c; g_delayed_id=(int)StringToInteger(JGet(pk,pv,"sid","0")); g_delay_count=(int)StringToInteger(JGet(pk,pv,"delay_count","0"));
      g_park.sid=g_delayed_id; g_park.implicit_streak=(int)StringToInteger(JGet(pk,pv,"implicit_streak","0")); g_park.explicit_this_bar=(JGet(pk,pv,"explicit_this_bar")=="true");
      g_park.published_bar=IsoToTime(JGet(pk,pv,"published_bar","")); g_park.published_at=IsoToTime(JGet(pk,pv,"published_at","")); g_park.lots=StringToDouble(JGet(pk,pv,"lots"));
      g_park.orig_entry=StringToDouble(JGet(pk,pv,"orig_entry")); g_park.orig_sl=StringToDouble(JGet(pk,pv,"orig_sl")); g_park.orig_tp=StringToDouble(JGet(pk,pv,"orig_tp"));
      g_park.orig_tp1=StringToDouble(JGet(pk,pv,"orig_tp1")); g_park.orig_tp2=StringToDouble(JGet(pk,pv,"orig_tp2")); g_park.caption=JGet(pk,pv,"caption","");
      g_sig_regime=JGet(pk,pv,"regime",""); g_sig_with_trend=JGet(pk,pv,"with_trend",""); g_sig_class=JGet(pk,pv,"decision_class","");
      g_to_entry=StringToDouble(JGet(pk,pv,"to_entry")); g_to_sl=StringToDouble(JGet(pk,pv,"to_sl")); g_to_tp1=StringToDouble(JGet(pk,pv,"to_tp1")); g_to_tp2=StringToDouble(JGet(pk,pv,"to_tp2"));
      g_delay_pending=true; g_live_parked=true; parked=true;
      ParkStoreCurrent();
      if(pfiles[pf]=="parked.json"){ LiveSaveParked(); FileDelete(LivePath("state\\"+_Symbol+"\\parked.json"),FILE_COMMON); }   // migrate the legacy file
     }
   LiveRecomputeParked();
   LiveReconcile();
   AdoptOrphans();
   AuditLine("restore","","","","ok","",StringFormat("rows=%d actions=%d parked=%d sig_seq=%d",nrows,ArraySize(g_actions),(int)parked,g_sig_seq));
   if(nrows>0 || parked) Print("LIVE: restored ",nrows," row(s), ",ArraySize(g_actions)," action(s), parked=",parked," sig_seq=",g_sig_seq);
  }
//--- self-test: snapshot -> wipe in-memory state -> restore from disk -> compare; also drops one open
//--- row's state file first so orphan adoption is exercised. Audits `restart_check ok|mismatch`.
void LiveSimulateRestart()
  {
   int n0=ArraySize(g_rows); int na0=ArraySize(g_actions);
   string sig=""; for(int i=0;i<n0;i++) sig+=StringFormat("%d:%s:%I64d:%d%d%d:%.4f;",g_rows[i].id,g_rows[i].decision,g_rows[i].posid,(int)g_rows[i].banked,(int)g_rows[i].tp1_done,(int)g_rows[i].ratcheted,g_rows[i].r_multiple);
   LiveSaveAllState();
   int drop=-1; for(int i=0;i<n0;i++) if(g_rows[i].posid>0 && !g_rows[i].closed && PositionSelectByTicket((ulong)g_rows[i].posid)){ drop=i; break; }
   int dropped_id=(drop>=0 ? g_rows[drop].id : 0); long dropped_pid=(drop>=0 ? g_rows[drop].posid : 0);
   if(drop>=0) FileDelete(RowStatePath(dropped_id),FILE_COMMON);
   int seq0=g_sig_seq; bool parked0=g_live_parked;
   ArrayResize(g_rows,0); ArrayResize(g_actions,0); g_live_parked=false; g_delay_pending=false;
   LiveRestoreState();
   string sig1=""; for(int i=0;i<ArraySize(g_rows);i++) sig1+=StringFormat("%d:%s:%I64d:%d%d%d:%.4f;",g_rows[i].id,g_rows[i].decision,g_rows[i].posid,(int)g_rows[i].banked,(int)g_rows[i].tp1_done,(int)g_rows[i].ratcheted,g_rows[i].r_multiple);
   //--- expected: every non-dropped row identical; the dropped open position re-appears as ADOPTED
   bool adopted=false; for(int i=0;i<ArraySize(g_rows);i++) if(g_rows[i].strategy=="ADOPTED" && g_rows[i].posid==dropped_pid) adopted=true;
   int expect_rows=n0; bool ok=(ArraySize(g_rows)==expect_rows) && (parked0==g_live_parked) && (drop<0 || adopted) && (g_sig_seq==seq0+(drop>=0?1:0));
   string detail=StringFormat("rows %d->%d actions %d->%d dropped=%d adopted=%d parked %d->%d seq %d->%d",n0,ArraySize(g_rows),na0,ArraySize(g_actions),dropped_id,(int)adopted,(int)parked0,(int)g_live_parked,seq0,g_sig_seq);
   if(ok && drop<0 && sig1!=sig) ok=false;
   AuditLine("restart_check","","","",(ok?"ok":"mismatch"),"",detail);
   Print("LIVE self-test restart_check ",(ok?"OK":"MISMATCH")," - ",detail);
   LiveSaveAllState();
  }

//+==================================================================+
//| PHASE 3 LIVE - Slice 3: task queue consumer (S6/S7/S8, Q3/Q4/Q5) |
//| claim -> validate -> execute -> ack, append-only audit.           |
//+==================================================================+
datetime IsoToTime(string iso)   // "YYYY-MM-DDTHH:MM:SSZ" -> datetime (0 on failure)
  {
   if(StringLen(iso)<19) return 0;
   string t=StringSubstr(iso,0,19); StringReplace(t,"-","."); StringReplace(t,"T"," ");
   return StringToTime(t);
  }
bool TaskIdOk(string id)
  {
   int n=StringLen(id); if(n<8 || n>64) return false;
   for(int i=0;i<n;i++){ ushort c=StringGetCharacter(id,i);
     if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-')) return false; }
   return true;
  }
int RowIdxByPosid(long posid)
  { for(int i=0;i<ArraySize(g_rows);i++) if(g_rows[i].posid==posid && !g_rows[i].closed) return i; return -1; }
int RowIdxBySid(int sid)
  { for(int i=ArraySize(g_rows)-1;i>=0;i--) if(g_rows[i].id==sid) return i; return -1; }
//--- FTMO guards (G3/§11-7). config\account.json = the rules; state\account.json = what we observed.
double g_ftmo_initial=0.0, g_ftmo_daily_pct=0.05, g_ftmo_max_pct=0.10, g_ftmo_buf_pct=0.005;
int    g_ftmo_reset_hour=0; string g_ftmo_day_ref="balance", g_ftmo_daily_base="initial";
string g_ftmo_day_key=""; double g_ftmo_day_bal=0.0, g_ftmo_day_eq=0.0;
string FtmoDayKey(datetime t){ MqlDateTime d; TimeToStruct(t-(datetime)(g_ftmo_reset_hour*3600),d); return StringFormat("%04d-%02d-%02d",d.year,d.mon,d.day); }
void LiveFtmoSaveState()
  { AtomicWriteText(LivePath("state\\account.json"),StringFormat("{\"schema_version\":%d,\"login\":%I64d,\"initial_balance\":%.2f,\"day_key\":\"%s\",\"day_start_balance\":%.2f,\"day_start_equity\":%.2f,\"set_at\":\"%s\"}",
      LIVE_SCHEMA_VERSION,g_account_login,g_ftmo_initial,g_ftmo_day_key,g_ftmo_day_bal,g_ftmo_day_eq,IsoTime(LiveNow()))); }
void LiveFtmoLoad()
  {
   string p=LivePath("config\\account.json"); string t=ReadTextFile(p); string k[],v[],e;
   if(t=="" || !JsonFlatParse(t,k,v,e))
     { AtomicWriteText(p,"{\"schema_version\":1,\"initial_balance\":0,\"daily_loss_pct\":0.05,\"max_loss_pct\":0.10,\"buffer_pct\":0.005,\"day_reset_mode\":\"server_midnight\",\"day_reset_hour\":0,\"day_ref\":\"balance\",\"daily_base\":\"initial\"}");
       t=ReadTextFile(p); JsonFlatParse(t,k,v,e); }
   g_ftmo_daily_pct=StringToDouble(JGet(k,v,"daily_loss_pct","0.05")); g_ftmo_max_pct=StringToDouble(JGet(k,v,"max_loss_pct","0.10"));
   g_ftmo_buf_pct=StringToDouble(JGet(k,v,"buffer_pct","0.005")); g_ftmo_reset_hour=(int)StringToInteger(JGet(k,v,"day_reset_hour","0"));
   g_ftmo_day_ref=JGet(k,v,"day_ref","balance"); g_ftmo_daily_base=JGet(k,v,"daily_base","initial");
   double cfg_init=StringToDouble(JGet(k,v,"initial_balance","0"));
   //--- observed state (initial balance captured ONCE on the first live run and never re-captured)
   string st=ReadTextFile(LivePath("state\\account.json")); string sk[],sv[],se;
   if(st!="" && JsonFlatParse(st,sk,sv,se))
     { g_ftmo_initial=StringToDouble(JGet(sk,sv,"initial_balance","0")); g_ftmo_day_key=JGet(sk,sv,"day_key","");
       g_ftmo_day_bal=StringToDouble(JGet(sk,sv,"day_start_balance","0")); g_ftmo_day_eq=StringToDouble(JGet(sk,sv,"day_start_equity","0")); }
   if(cfg_init>0.0) g_ftmo_initial=cfg_init;                                   // config wins when set
   if(g_ftmo_initial<=0.0 && AccountInfoDouble(ACCOUNT_BALANCE)>0.0){ g_ftmo_initial=AccountInfoDouble(ACCOUNT_BALANCE); AuditLine("config","","","","info","ftmo_initial_captured",DoubleToString(g_ftmo_initial,2)); }
   LiveFtmoDayReset();
  }
//--- snapshot day-start balance/equity at the FTMO day boundary (persisted: a mid-day restart never re-snapshots)
void LiveFtmoDayReset()
  {
   string key=FtmoDayKey(LiveNow());
   if(key==g_ftmo_day_key && g_ftmo_day_bal>0.0) return;
   if(AccountInfoDouble(ACCOUNT_BALANCE)<=0.0) return;                     // account not synced yet: try again next poll
   g_ftmo_day_key=key; g_ftmo_day_bal=AccountInfoDouble(ACCOUNT_BALANCE); g_ftmo_day_eq=AccountInfoDouble(ACCOUNT_EQUITY);
   LiveFtmoSaveState();
   AuditLine("ftmo_day","","","",key,"",StringFormat("day_start_balance=%.2f day_start_equity=%.2f",g_ftmo_day_bal,g_ftmo_day_eq));
  }
double FtmoDayRef(){ return (g_ftmo_day_ref=="equity" ? g_ftmo_day_eq : (g_ftmo_day_ref=="max" ? MathMax(g_ftmo_day_bal,g_ftmo_day_eq) : g_ftmo_day_bal)); }
double FtmoDailyFloor(){ double base=(g_ftmo_daily_base=="day_start" ? FtmoDayRef() : g_ftmo_initial); return FtmoDayRef()-g_ftmo_daily_pct*base; }
double FtmoMaxFloor()  { return g_ftmo_initial*(1.0-g_ftmo_max_pct); }
//--- pre-order: equity - aggregate risk-to-stop (all symbols, §11-7) - this position's full -1R must stay above
//--- both floors plus the safety buffer, else reject with the named reason (and audit it).
bool FtmoHeadroomOK(double lots,double entry,double sl,string &why,string task_id="")
  {
   why="";
   double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE), tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double this_risk=(ts>0.0 && tv>0.0 ? (MathAbs(entry-sl)/ts)*tv*lots : 0.0);
   int unprot=0; double agg=AggregateRiskToStop(unprot);
   double eq=AccountInfoDouble(ACCOUNT_EQUITY), buf=g_ftmo_buf_pct*g_ftmo_initial;
   double after=eq-agg-this_risk;
   if(after<FtmoDailyFloor()+buf) why=StringFormat("ftmo_daily_headroom:after=%.2f floor=%.2f buf=%.2f agg=%.2f this=%.2f",after,FtmoDailyFloor(),buf,agg,this_risk);
   else if(after<FtmoMaxFloor()+buf) why=StringFormat("ftmo_max_headroom:after=%.2f floor=%.2f buf=%.2f agg=%.2f this=%.2f",after,FtmoMaxFloor(),buf,agg,this_risk);
   if(why!=""){ AuditLine("ftmo_reject",task_id,"approve","","rejected",why,""); return false; }
   return true;
  }

void WriteAck(string task_id,string verb,string target_key,long target_id,string result,string reason,int row_idx)
  {
   CJsonW j; j.BeginObj();
   j.KInt("schema_version",LIVE_SCHEMA_VERSION); j.KStr("ea_build",EA_BUILD);
   j.KStr("task_id",task_id); j.KStr("symbol",_Symbol); j.KStr("verb",verb);
   j.KInt(target_key,target_id); j.KStr("result",result); j.KStr("reason",reason); j.KTime("executed_at",LiveNow());
   j.Key("refs"); j.BeginObj();
   if(row_idx<0 && verb=="test_signal" && g_live_parked) j.KInt("signal_id",g_park.sid);   // the synthetic signal just published
   if(row_idx>=0)
     { j.KInt("signal_id",g_rows[row_idx].id); j.KInt("posid",g_rows[row_idx].posid); j.KInt("order_ticket",g_rows[row_idx].order_ticket);
       j.KNum("lots",g_rows[row_idx].lots,2); j.KStr("decision",g_rows[row_idx].decision); j.KNum("entry",g_rows[row_idx].entry,_Digits);
       j.KNum("sl",g_rows[row_idx].sl,_Digits); j.KNum("tp",g_rows[row_idx].tp,_Digits);
       if(PositionSelectByTicket((ulong)g_rows[row_idx].posid)){ j.KNum("pos_sl",PositionGetDouble(POSITION_SL),_Digits); j.KNum("pos_volume",PositionGetDouble(POSITION_VOLUME),2); } }
   j.EndObj();
   j.EndObj();
   AtomicWriteText(LivePath("acks\\"+task_id+".json"),j.Text());
  }

//--- Execute one claimed task. Returns result ("accepted"/"rejected"/"expired"), sets reason + row.
//--- live servers confirm asynchronously: the retcode says DONE before PositionSelect reflects it.
//--- Poll the position up to ~2 s (tester: one pass), and treat a DONE/PLACED retcode as success regardless.
bool LiveDone(){ uint rc=g_trade.ResultRetcode(); return (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED || rc==TRADE_RETCODE_DONE_PARTIAL); }
bool LiveWaitClosed(long posid){ for(int i=0;i<10;i++){ if(!PositionSelectByTicket((ulong)posid)) return true; if((bool)MQLInfoInteger(MQL_TESTER)) break; Sleep(200); } return !PositionSelectByTicket((ulong)posid); }
bool LiveWaitVolBelow(long posid,double vol0){ for(int i=0;i<10;i++){ if(PositionSelectByTicket((ulong)posid) && PositionGetDouble(POSITION_VOLUME)<vol0-1e-9) return true; if(!PositionSelectByTicket((ulong)posid)) return true; if((bool)MQLInfoInteger(MQL_TESTER)) break; Sleep(200); } return false; }
bool LiveWaitSL(long posid,double sl){ for(int i=0;i<10;i++){ if(PositionSelectByTicket((ulong)posid) && MathAbs(PositionGetDouble(POSITION_SL)-sl)<=_Point*1.5) return true; if((bool)MQLInfoInteger(MQL_TESTER)) break; Sleep(200); } return false; }

string LiveExecuteTask(string &k[],string &v[],string task_id,string verb,string &reason,int &row_idx)
  {
   reason=""; row_idx=-1;
   datetime now=LiveNow();
   //--- signal verbs --------------------------------------------------------
   if(verb=="approve" || verb=="skip" || verb=="delay")
     {
      int sid=(int)StringToInteger(JGet(k,v,"signal_id","0"));
      if(sid<=0){ reason="bad_params"; return "rejected"; }
      int slot=ParkIndexBySid(sid);
      if(slot<0){ reason=(RowIdxBySid(sid)>=0 ? "signal_not_open" : "unknown_signal"); return "rejected"; }
      ParkLoad(slot);
      SignalCandidate cand=g_delayed; string caption=g_park.caption;
      if(verb=="delay")
        {
         g_delay_count++; WriteDelayLog(sid,g_delay_count,cand,"delay",0);
         g_park.explicit_this_bar=true; g_park.implicit_streak=0;
         g_delayed=cand; LiveSaveParked(); WriteSignalJson("open","");
         ParkStoreCurrent(); reason="ok"; return "accepted";
        }
      if(verb=="skip")
        {
         int rc=(int)StringToInteger(JGet(k,v,"params.reason_code","0"));
         if(rc<1 || rc>6){ reason="bad_params"; return "rejected"; }
         WriteSignalJson("skipped",StringFormat("skip:%d",rc));
         CommitDecision(sid,cand,caption,g_park.orig_entry,g_park.orig_sl,g_park.orig_tp,g_park.orig_tp1,g_park.orig_tp2,
                        false,rc,(long)(now-g_park.published_at)*1000,false,false,"skip");
         LiveUnpark(); row_idx=RowIdxBySid(sid); reason="ok"; return "accepted";
        }
      //--- approve: every doctrine gate the popup relied on, re-checked HERE (S7)
      if(!g_trading_enabled){ reason="trading_disabled"; return "rejected"; }
      if(!(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !(bool)MQLInfoInteger(MQL_TRADE_ALLOWED)){ reason="trading_disabled"; return "rejected"; }
      if(!g_ev_loaded || ArraySize(g_ev_t)==0){ reason="events_not_loaded"; return "rejected"; }
      string evn=""; datetime evt=0;
      if(ElectionGateHit(now,evn,evt)){ reason="election_gate:"+evn; return "rejected"; }
      if(HasActiveOrderOrPosition()){ reason="setup_lock"; return "rejected"; }
      string em=JGet(k,v,"params.entry_mode",(g_delay_count>0 ? "pending" : "market"));
      if(em=="market_now") em="market";
      string entry_mode;
      if(em=="market")
        {
         double en=0.0, rrn=0.0, mkt=0.0; string why="";
         if(!ValidateMarketEntry(cand,en,rrn,why))
           { reason=(StringFind(why,"price past")>=0 ? "geom_invalid" : (StringFind(why,"stop too")>=0 ? "stop_too_tight" : "rr_below_floor"))+":"+why; return "rejected"; }
         cand.entry=en; entry_mode=(g_delay_count>0 ? "market_now" : "market");
        }
      else if(em=="pending")
        {
         // live: a pending order at the ORIGINAL entry is always offered (price slips between the trigger and the tap)
         bool two=(cand.partial_fraction>0.0 && cand.tp1>0.0 && cand.tp2>0.0);
         double otp=(two? cand.tp2 : cand.tp);
         if(!ValidGeom(cand.direction,cand.entry,cand.sl,otp)){ reason="geom_invalid"; return "rejected"; }
         double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID), ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
         if(cand.direction>0 ? bid<=cand.sl : ask>=cand.sl){ reason="geom_invalid:price_through_sl"; return "rejected"; }
         entry_mode="pending_frozen";
        }
      else { reason="bad_params:entry_mode"; return "rejected"; }
      double lots=SizeByRisk(cand.entry,cand.sl);
      if(lots<=0.0){ reason="lots_zero"; return "rejected"; }
      string fwhy="";
      if(!FtmoHeadroomOK(lots,cand.entry,cand.sl,fwhy,task_id)){ reason=fwhy; return "rejected"; }
      //--- commit exactly as the tester would after an Accept click
      CommitDecision(sid,cand,caption,g_park.orig_entry,g_park.orig_sl,g_park.orig_tp,g_park.orig_tp1,g_park.orig_tp2,
                     true,0,(long)(now-g_park.published_at)*1000,false,false,entry_mode);
      row_idx=RowIdxBySid(sid);
      if(row_idx>=0 && g_rows[row_idx].decision=="approved" && g_rows[row_idx].posid==0 && g_rows[row_idx].order_ticket==0)
        { WriteSignalJson("approved","order_failed"); LiveUnpark(); reason=StringFormat("order_failed:%d",g_trade.ResultRetcode()); return "rejected"; }
      WriteSignalJson((row_idx>=0 && g_rows[row_idx].decision=="approved_pending") ? "approved_pending" : "approved","");
      LiveUnpark();
      //--- one position per symbol: every OTHER parked signal is closed as skipped code 9 "superseded" (auto)
      for(int oi=0;oi<MAX_PARKS;oi++)
        {
         if(!g_slots[oi].active) continue;
         int osid=g_slots[oi].park.sid; ParkLoad(oi);
         WriteSignalJson("skipped",StringFormat("superseded: #%d approved",sid));
         AuditLine("auto_skip","","skip",StringFormat("sig:%d",osid),"skipped","superseded",StringFormat("by #%d",sid));
         g_live_auto=1;
         CommitDecision(osid,g_delayed,g_park.caption,g_park.orig_entry,g_park.orig_sl,g_park.orig_tp,g_park.orig_tp1,g_park.orig_tp2,false,9,0,false,false,"auto_skip");
         g_live_auto=0;
         LiveUnpark();
        }
      reason="ok"; return "accepted";
     }
   //--- trader cancels a RESTING pending order (trader ruling 2026-09-17). Target = signal_id of the approved_pending row.
   if(verb=="cancel_pending")
     {
      int sid=(int)StringToInteger(JGet(k,v,"signal_id","0"));
      int idx=RowIdxBySid(sid);
      if(sid<=0 || idx<0){ reason="unknown_signal"; return "rejected"; }
      if(!g_rows[idx].is_pending || g_rows[idx].posid>0 || g_rows[idx].closed || g_rows[idx].order_ticket<=0){ reason=(g_rows[idx].posid>0 ? "already_filled" : "not_pending"); row_idx=idx; return "rejected"; }
      if(!OrderSelect((ulong)g_rows[idx].order_ticket)){ reason="order_not_found"; row_idx=idx; return "rejected"; }
      bool del=g_trade.OrderDelete((ulong)g_rows[idx].order_ticket);
      if(!del && !LiveDone()){ reason=StringFormat("order_failed:%d",g_trade.ResultRetcode()); row_idx=idx; return "rejected"; }
      g_rows[idx].decision="cancelled"; g_rows[idx].terminal="cancelled"; g_rows[idx].closed=true; g_rows[idx].is_pending=false;
      string sp=LivePath(StringFormat("signals\\%s-%d.json",_Symbol,sid)); string js=ReadTextFile(sp);
      if(js!=""){ StringReplace(js,"\"status\":\"approved_pending\"","\"status\":\"cancelled\""); AtomicWriteText(sp,js); }
      WriteJournal(g_journal_part);
      row_idx=idx; reason="ok"; return "accepted";
     }
   //--- trader-issued TEST signal (live only, never in the tester): a synthetic setup at the current price with an
   //--- ATR-sized stop, published and parked exactly like a detector signal so the whole chain (ping, verdict, approve,
   //--- fill, position verbs) is exercised on the demo. Journal/signal strategy = "TEST" - graders filter it out.
   if(verb=="test_signal")
     {
      if((bool)MQLInfoInteger(MQL_TESTER)){ reason="bad_params"; return "rejected"; }
      if(ParkCount()>=g_cfg_max_parks || HasActiveOrderOrPosition()){ reason="setup_lock"; return "rejected"; }
      string dir=JGet(k,v,"params.direction",""); StringToUpper(dir);
      if(dir!="BUY" && dir!="SELL"){ reason="bad_params"; return "rejected"; }
      double sl_atr=StringToDouble(JGet(k,v,"params.sl_atr","0.5")); if(sl_atr<0.1 || sl_atr>3.0) sl_atr=0.5;
      double a[]; ArraySetAsSeries(a,true); int hA=iATR(_Symbol,g_tf,14); double atr=0.0;
      if(hA!=INVALID_HANDLE && CopyBuffer(hA,0,1,1,a)>0) atr=a[0];
      if(atr<=0.0){ reason="bad_params"; return "rejected"; }
      SignalCandidate c; ResetCandidate(c);
      c.valid=true; c.strategy="TEST"; c.direction=(dir=="BUY"?1:-1);
      double mk=(c.direction>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK) : SymbolInfoDouble(_Symbol,SYMBOL_BID));
      c.entry=NormPrice(mk); double R=sl_atr*atr;
      c.sl=NormPrice(c.entry-c.direction*R); c.tp1=NormPrice(c.entry+c.direction*2.0*R); c.tp2=NormPrice(c.entry+c.direction*4.0*R); c.tp=c.tp2;
      c.partial_fraction=0.5; c.rr=4.0; c.comment="TEST signal issued by the trader (sl "+DoubleToString(sl_atr,2)+" ATR)";
      c.zone_from=iTime(_Symbol,g_tf,3); c.zone_to=iTime(_Symbol,g_tf,0); c.zone_hi=NormPrice(c.entry+0.25*atr); c.zone_lo=NormPrice(c.entry-0.25*atr);
      AuditLine("test_signal",task_id,verb,dir,"issued","",StringFormat("entry=%s sl=%s tp1=%s tp2=%s atr=%s",DoubleToString(c.entry,_Digits),DoubleToString(c.sl,_Digits),DoubleToString(c.tp1,_Digits),DoubleToString(c.tp2,_Digits),DoubleToString(atr,_Digits)));
      HandleSignal(c);
      if(!g_live_parked){ reason="order_failed:not_published"; return "rejected"; }   // e.g. election auto-skip took it
      ParkStoreCurrent();
      row_idx=-1; reason="ok"; return "accepted";
     }
   //--- position verbs --------------------------------------------------------
   if(verb=="close" || verb=="close50" || verb=="sl_be" || verb=="ratchet_tp1")
     {
      long posid=StringToInteger(JGet(k,v,"position_id","0"));
      if(posid<=0){ reason="bad_params"; return "rejected"; }
      int idx=RowIdxByPosid(posid); row_idx=idx;
      if(idx<0){ reason="unknown_position"; return "rejected"; }
      if(!PositionSelectByTicket((ulong)posid)){ reason="position_closed"; return "rejected"; }
      double vol0=PositionGetDouble(POSITION_VOLUME), sl0=PositionGetDouble(POSITION_SL);
      if(verb=="close")
        {
         ManualClose(idx);
         if(!LiveWaitClosed(posid) && !LiveDone()){ reason=StringFormat("order_failed:%d",g_trade.ResultRetcode()); return "rejected"; }
         reason=(PositionSelectByTicket((ulong)posid) ? "ok_unconfirmed" : "ok"); return "accepted";
        }
      if(verb=="close50")
        {
         double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
         double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);  if(vmin<=0)vmin=0.01;
         double pv=MathFloor((vol0*0.5)/step)*step;
         if(pv<vmin || (vol0-pv)<vmin){ reason="min_lot_split"; return "rejected"; }
         ManualClose50(idx);
         bool ok=LiveWaitVolBelow(posid,vol0);
         if(!ok && !LiveDone()){ reason=StringFormat("order_failed:%d",g_trade.ResultRetcode()); return "rejected"; }
         reason=(ok ? "ok" : "ok_unconfirmed"); return "accepted";
        }
      if(verb=="sl_be")
        {
         //--- the +0.5R floor and the stops-level band were only ever a greyed button: enforce here (§10 small item)
         double oR=OpenR(idx);
         if(oR<BE_FLOOR_R){ reason=StringFormat("be_floor:open_r=%.2f<%.2f",oR,BE_FLOOR_R); AuditLine("gate",task_id,verb,StringFormat("pos:%I64d",posid),"BEPlaceable=false","be_floor",""); return "rejected"; }
         if(!BEPlaceable(idx)){ reason="be_stops_level"; AuditLine("gate",task_id,verb,StringFormat("pos:%I64d",posid),"BEPlaceable=false","be_stops_level",""); return "rejected"; }
         double be=NormPrice(BEPrice(idx));
         if(sl0>0.0 && (g_rows[idx].direction>0 ? be<=sl0 : be>=sl0)){ reason="be_would_loosen"; return "rejected"; }
         AuditLine("gate",task_id,verb,StringFormat("pos:%I64d",posid),"BEPlaceable=true","","");
         ManualBE(idx);
         bool ok=LiveWaitSL(posid,be);
         if(!ok && !LiveDone()){ reason=StringFormat("order_failed:%d",g_trade.ResultRetcode()); return "rejected"; }
         reason=(ok ? "ok" : "ok_unconfirmed"); return "accepted";
        }
      if(verb=="ratchet_tp1")
        {
         bool ok_gate=RatchetPlaceable(idx);
         AuditLine("gate",task_id,verb,StringFormat("pos:%I64d",posid),(ok_gate?"RatchetPlaceable=true":"RatchetPlaceable=false"),"","");
         if(!ok_gate)
           {
            if(g_rows[idx].ratcheted) reason="ratchet_already_used";
            else if(!g_rows[idx].banked) reason="ratchet_not_banked";
            else if(g_rows[idx].tp1<=0.0) reason="ratchet_no_tp1";
            else if(sl0>0.0 && (g_rows[idx].direction>0 ? g_rows[idx].tp1<=sl0 : g_rows[idx].tp1>=sl0)) reason="ratchet_would_loosen";
            else reason="ratchet_not_past_tp1";
            return "rejected";
           }
         ManualRatchetTP1(idx);
         bool ok=LiveWaitSL(posid,NormPrice(g_rows[idx].tp1));
         if(!ok && !LiveDone()){ reason=StringFormat("order_failed:%d",g_trade.ResultRetcode()); return "rejected"; }
         reason=(ok ? "ok" : "ok_unconfirmed"); return "accepted";
        }
     }
   reason="unknown_verb"; return "rejected";
  }

//--- Q5: scan tasks\, CLAIM (move to done) before anything else, dedupe on an existing ack,
//--- validate the envelope, execute, ack, audit. Files for other symbols are left untouched.
void LiveProcessTasks()
  {
   string names[]; int nn=0; string fname;
   long hf=FileFindFirst(LivePath("tasks\\*.json"),fname,FILE_COMMON);
   if(hf==INVALID_HANDLE) return;
   do { if(StringFind(fname,".tmp")<0){ ArrayResize(names,nn+1); names[nn++]=fname; } } while(FileFindNext(hf,fname));
   FileFindClose(hf);
   if(nn==0) return;
   //--- process in ISSUE order (issued_at, then name as a deterministic tie-break) - a burst of
   //--- tasks written seconds apart (e.g. sl_be, close50, close) must execute in the order issued,
   //--- never alphabetically (found by the self-test: "close" sorted before "close50").
   string keys[]; ArrayResize(keys,nn);
   for(int i=0;i<nn;i++)
     {
      string k0[],v0[],e0; string t0=ReadTextFile(LivePath("tasks\\"+names[i]));
      string iso=(JsonFlatParse(t0,k0,v0,e0) ? JGet(k0,v0,"issued_at","") : "");
      if(StringLen(iso)<19) iso="9999-99-99T99:99:99Z";   // unparseable -> last
      keys[i]=iso+"|"+names[i];
     }
   ArraySort(keys);
   for(int i=0;i<nn;i++) names[i]=StringSubstr(keys[i],StringFind(keys[i],"|")+1);
   for(int i=0;i<nn;i++)
     {
      string rel=LivePath("tasks\\"+names[i]);
      string text=ReadTextFile(rel);
      string k[],v[],err;
      bool parsed=JsonFlatParse(text,k,v,err);
      string tsym=JGet(k,v,"symbol",""), task_id=JGet(k,v,"task_id",""), verb=JGet(k,v,"verb","");
      if(parsed && tsym!="" && tsym!=_Symbol) continue;                       // another instance's task
      if(!parsed && text=="") continue;                                        // still being written, or just claimed by another instance
      if(parsed && tsym=="") continue;                                          // no symbol: not ours to judge
      string done=LivePath("tasks\\done\\"+names[i]);
      if(!FileMove(rel,FILE_COMMON,done,FILE_COMMON|FILE_REWRITE)){ Print("task claim failed ",names[i]," err=",GetLastError()); continue; }
      if(!parsed || !TaskIdOk(task_id))
        { string tid=(TaskIdOk(task_id)? task_id : "malformed-"+StringSubstr(names[i],0,MathMin(40,StringLen(names[i])-5)));
          AuditLine("rejected",tid,verb,"","rejected",(parsed?"bad_task_id":"malformed_json"),err);
          WriteAck(tid,verb,"signal_id",0,"rejected",(parsed?"bad_task_id":"malformed_json:"+err),-1); continue; }
      if(FileIsExist(LivePath("acks\\"+task_id+".json"),FILE_COMMON))
        { AuditLine("duplicate",task_id,verb,"","ignored","already_acked",""); continue; }
      AuditLine("claimed",task_id,verb,JGet(k,v,"signal_id",JGet(k,v,"position_id","")),"","",names[i]);
      string result,reason; int row_idx=-1;
      string target_key=(verb=="close"||verb=="close50"||verb=="sl_be"||verb=="ratchet_tp1") ? "position_id" : "signal_id";
      long target_id=StringToInteger(JGet(k,v,target_key,"0"));
      if(JGet(k,v,"schema_version","")!=(string)LIVE_SCHEMA_VERSION){ result="rejected"; reason="schema_version_unsupported"; }
      else if(tsym!=_Symbol){ result="rejected"; reason="symbol_mismatch"; }
      else if(verb!="approve"&&verb!="skip"&&verb!="delay"&&verb!="close"&&verb!="close50"&&verb!="sl_be"&&verb!="ratchet_tp1"&&verb!="test_signal"&&verb!="cancel_pending"){ result="rejected"; reason="unknown_verb"; }
      else
        {
         datetime issued=IsoToTime(JGet(k,v,"issued_at",""));
         if(issued>0 && LiveNow()-issued>(long)g_cfg_task_max_age_h*3600){ result="expired"; reason="stale_task"; }
         else result=LiveExecuteTask(k,v,task_id,verb,reason,row_idx);
        }
      AuditLine((result=="accepted"?"executed":"rejected"),task_id,verb,StringFormat("%s:%I64d",target_key,target_id),result,reason,
                (row_idx>=0? StringFormat("posid=%I64d ticket=%I64d decision=%s",g_rows[row_idx].posid,g_rows[row_idx].order_ticket,g_rows[row_idx].decision):""));
      WriteAck(task_id,verb,target_key,target_id,result,reason,row_idx);
      AuditLine("acked",task_id,verb,StringFormat("%s:%I64d",target_key,target_id),result,reason,"");
     }
  }
//--- restart hygiene: a task claimed (in done\) but never acked was interrupted mid-execution
void LiveAckOrphanedDone()
  {
   string fname; long hf=FileFindFirst(LivePath("tasks\\done\\*.json"),fname,FILE_COMMON);
   if(hf==INVALID_HANDLE) return;
   do {
      if(StringFind(fname,".tmp")>=0) continue;
      string k[],v[],err; string text=ReadTextFile(LivePath("tasks\\done\\"+fname));
      if(!JsonFlatParse(text,k,v,err)) continue;
      string tid=JGet(k,v,"task_id",""); if(!TaskIdOk(tid)) continue;
      if(JGet(k,v,"symbol","")!=_Symbol) continue;
      if(FileIsExist(LivePath("acks\\"+tid+".json"),FILE_COMMON)) continue;
      AuditLine("rejected",tid,JGet(k,v,"verb",""),"","expired","restart_during_execution",fname);
      WriteAck(tid,JGet(k,v,"verb",""),"signal_id",StringToInteger(JGet(k,v,"signal_id","0")),"expired","restart_during_execution",-1);
   } while(FileFindNext(hf,fname));
   FileFindClose(hf);
  }

//+==================================================================+
//| SELF-TEST driver (InpLiveSelfTest, TESTER ONLY): scripted tasks   |
//| against our own queue so the whole loop runs at tester speed.     |
//| Minimal set now (Slice 3); extended in Slice 6.                   |
//+==================================================================+
int      st_sid_done   = 0;      // last signal ordinal a decision task was written for
int      st_sid_delay  = 0;      // ordinal that received a delay (awaits pending approve)
bool     st_startup    = false;  // illegal-task batch written once
long     st_pos_posid  = 0;      // position under management
int      st_pos_stage  = 0;
datetime st_pos_t0     = 0;
datetime st_pos_last   = 0;      // last position-verb task time (stages are spaced >= 2 polls apart)
bool     st_dup_done   = false;
bool     st_restart_done = false; bool st_ftmo_restored=false; bool st_kill_restored=false; bool st_mult_restored=false; bool st_agg_checked=false;
void SelfTestWriteTask(string task_id,string target_key,long target_id,string verb,string params_json)
  {
   string body=StringFormat("{\"schema_version\":%d,\"task_id\":\"%s\",\"symbol\":\"%s\",\"%s\":%I64d,\"verb\":\"%s\",\"params\":%s,\"issued_at\":\"%s\",\"issued_by\":\"selftest\"}",
                            LIVE_SCHEMA_VERSION,task_id,_Symbol,target_key,target_id,verb,params_json,IsoTime(TimeCurrent()));
   AtomicWriteText(LivePath("tasks\\"+task_id+".json"),body);
  }
void SelfTestTick()
  {
   datetime now=TimeCurrent();
   //--- startup: the deliberately illegal batch (each must be rejected with its named reason)
   if(!st_startup && now-g_start_time>=60)
     {
      st_startup=true;
      AtomicWriteText(LivePath("tasks\\st-bad-schema.json"),StringFormat("{\"schema_version\":99,\"task_id\":\"st-bad-schema\",\"symbol\":\"%s\",\"signal_id\":1,\"verb\":\"approve\",\"params\":{},\"issued_at\":\"%s\",\"issued_by\":\"selftest\"}",_Symbol,IsoTime(now)));
      SelfTestWriteTask("st-bad-verb","signal_id",1,"nuke","{}");
      SelfTestWriteTask("st-unknown-sig","signal_id",9999,"approve","{\"entry_mode\":\"market\"}");
      SelfTestWriteTask("st-unknown-pos","position_id",424242,"close","{}");
      AtomicWriteText(LivePath("tasks\\st-malformed.json"),"{ this is not json");
      AtomicWriteText(LivePath("tasks\\st-stale-task1.json"),StringFormat("{\"schema_version\":1,\"task_id\":\"st-stale-task1\",\"symbol\":\"%s\",\"signal_id\":1,\"verb\":\"skip\",\"params\":{\"reason_code\":1},\"issued_at\":\"2000-01-01T00:00:00Z\",\"issued_by\":\"selftest\"}",_Symbol));
     }
   //--- decision tasks by signal ordinal, 30 sim-seconds after publish
   if(g_live_parked && g_park.sid!=st_sid_done && now-g_park.published_at>=30)
     {
      int kk=g_park.sid, m=kk%4;
      if(m==1)
        {
         if(kk==9)   // §11-7 scenario: with a 0.1% daily limit even a 1% trade breaches -> ftmo_daily_headroom
           { AtomicWriteText(LivePath("config\\account.json"),"{\"schema_version\":1,\"initial_balance\":0,\"daily_loss_pct\":0.001,\"max_loss_pct\":0.10,\"buffer_pct\":0.0,\"day_reset_mode\":\"server_midnight\",\"day_reset_hour\":0,\"day_ref\":\"balance\",\"daily_base\":\"initial\"}");
             LiveFtmoLoad();
             SelfTestWriteTask(StringFormat("st-%d-approve-ftmo",kk),"signal_id",kk,"approve","{\"entry_mode\":\"market\"}"); st_sid_done=kk; }
         else if(kk==5)   // E10 kill switch: OFF -> approve must be refused `trading_disabled`; restored ON by the cleanup below
           { AtomicWriteText(LivePath("config\\trading_enabled.json"),"{\"trading_enabled\":false}");
             SelfTestWriteTask(StringFormat("st-%d-approve-killed",kk),"signal_id",kk,"approve","{\"entry_mode\":\"market\"}"); st_sid_done=kk; }
         else if(kk==13)  // C2 risk multiplier: 0.5 for this symbol -> journal risk_mult_applied=0.500, lots halved
           { AtomicWriteText(LivePath("config\\risk_mult.json"),StringFormat("{\"%s\":0.5}",SymbolRoot()));
             SelfTestWriteTask(StringFormat("st-%d-approve-half",kk),"signal_id",kk,"approve","{\"entry_mode\":\"market\"}"); st_sid_done=kk; }
         else { SelfTestWriteTask(StringFormat("st-%d-approve",kk),"signal_id",kk,"approve","{\"entry_mode\":\"market\"}"); st_sid_done=kk; }
        }
      else if(m==2) { SelfTestWriteTask(StringFormat("st-%d-skip",kk),"signal_id",kk,"skip","{\"reason_code\":3}"); st_sid_done=kk; }
      else if(m==3) { if(g_delay_count==0 && st_sid_delay!=kk){ SelfTestWriteTask(StringFormat("st-%d-delay",kk),"signal_id",kk,"delay","{}"); st_sid_delay=kk; }
                      else if(g_delay_count>0){ SelfTestWriteTask(StringFormat("st-%d-approve-pending",kk),"signal_id",kk,"approve","{\"entry_mode\":\"pending\"}"); st_sid_done=kk; } }
      else          { st_sid_done=kk; }   // m==0: no task -> must expire code 8
     }
   //--- FTMO scenario cleanup: once the tightened approve is acked, restore the default rules and skip that signal
   if(!st_ftmo_restored && g_live_parked && g_park.sid==9 && FileIsExist(LivePath("acks\\st-9-approve-ftmo.json"),FILE_COMMON))
     { st_ftmo_restored=true; AtomicWriteText(LivePath("config\\account.json"),"{\"schema_version\":1,\"initial_balance\":0,\"daily_loss_pct\":0.05,\"max_loss_pct\":0.10,\"buffer_pct\":0.005,\"day_reset_mode\":\"server_midnight\",\"day_reset_hour\":0,\"day_ref\":\"balance\",\"daily_base\":\"initial\"}");
       LiveFtmoLoad(); SelfTestWriteTask("st-9-skip","signal_id",9,"skip","{\"reason_code\":6}"); }
   //--- kill-switch cleanup: once the refused approve is acked, switch ON and approve for real (must be accepted)
   if(!st_kill_restored && g_live_parked && g_park.sid==5 && FileIsExist(LivePath("acks\\st-5-approve-killed.json"),FILE_COMMON))
     { st_kill_restored=true; AtomicWriteText(LivePath("config\\trading_enabled.json"),"{\"trading_enabled\":true}");
       SelfTestWriteTask("st-5-approve","signal_id",5,"approve","{\"entry_mode\":\"market\"}"); }
   //--- risk_mult cleanup: after the halved approve is acked, restore 1.0 (the multiplier is sizing-only, C2)
   if(!st_mult_restored && FileIsExist(LivePath("acks\\st-13-approve-half.json"),FILE_COMMON))
     { st_mult_restored=true; AtomicWriteText(LivePath("config\\risk_mult.json"),StringFormat("{\"%s\":1.0}",SymbolRoot())); }
   //--- duplicate task_id: re-issue the FIRST approve after it was acked (expect audit `duplicate`, no 2nd ack/exec)
   if(!st_dup_done && st_sid_done>=1 && FileIsExist(LivePath("acks\\st-1-approve.json"),FILE_COMMON))
     { st_dup_done=true; SelfTestWriteTask("st-1-approve","signal_id",1,"approve","{\"entry_mode\":\"market\"}"); }
   //--- position management on the first open graded position
   int idx=ActiveRowIdx();
   if(idx>=0 && g_rows[idx].posid!=st_pos_posid){ st_pos_posid=g_rows[idx].posid; st_pos_stage=0; st_pos_t0=now; st_pos_last=now; }
   if(idx>=0 && st_pos_posid==g_rows[idx].posid && now-st_pos_last>=2*InpTaskPollSec)   // one stage per >=2 polls
     {
      long pid=g_rows[idx].posid; int secs=(int)(now-st_pos_t0); int bars=secs/PeriodSeconds(g_tf);
      bool wrote=true;
      if(st_pos_stage==0 && secs>=60)
        { if(!st_agg_checked && PositionSelectByTicket((ulong)pid))
            {   // §11-7: the aggregate term must see this position (it is the only one on the account here)
             st_agg_checked=true; int unp=0; double agg=AggregateRiskToStop(unp);
             double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE), tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
             double expct=(ts>0.0 ? MathAbs(PositionGetDouble(POSITION_PRICE_OPEN)-PositionGetDouble(POSITION_SL))/ts*tv*PositionGetDouble(POSITION_VOLUME) : 0.0);
             bool okagg=(agg>0.0 && expct>0.0 && MathAbs(agg-expct)/expct<0.05);
             AuditLine("agg_check","","",StringFormat("pos:%I64d",pid),(okagg?"ok":"zero"),"",StringFormat("agg=%.2f expected=%.2f unprotected=%d",agg,expct,unp));
            }
          SelfTestWriteTask(StringFormat("st-pos%I64d-slbe-early",pid),"position_id",pid,"sl_be","{}");
          SelfTestWriteTask(StringFormat("st-pos%I64d-ratchet-early",pid),"position_id",pid,"ratchet_tp1","{}"); st_pos_stage=1; }
      else if(st_pos_stage==1 && !st_restart_done && bars>=1 && ArraySize(g_rows)>=8)   // drill on a later position so rows span months
        { LiveSimulateRestart(); st_restart_done=true; idx=ActiveRowIdx(); if(idx<0) return; }
      //--- state-triggered ACCEPT paths (the early tasks above prove the gates refuse; these prove they admit)
      else if(st_pos_stage==1 && BEPlaceable(idx) && !g_rows[idx].banked && !g_rows[idx].ratcheted)   // pre-bank: SL still at the original stop, so BE is a genuine tighten
        { SelfTestWriteTask(StringFormat("st-pos%I64d-slbe-ok",pid),"position_id",pid,"sl_be","{}"); st_pos_stage=3; }
      else if(st_pos_stage<=3 && !g_rows[idx].ratcheted && RatchetPlaceable(idx))
        { SelfTestWriteTask(StringFormat("st-pos%I64d-ratchet-ok",pid),"position_id",pid,"ratchet_tp1","{}"); st_pos_stage=(st_pos_stage<3?2:3); }
      else if(st_pos_stage==1 && g_rows[idx].banked)
        { SelfTestWriteTask(StringFormat("st-pos%I64d-ratchet",pid),"position_id",pid,"ratchet_tp1","{}"); st_pos_stage=2; }
      else if(st_pos_stage<=2 && bars>=4)
        { SelfTestWriteTask(StringFormat("st-pos%I64d-slbe",pid),"position_id",pid,"sl_be","{}"); st_pos_stage=3; }
      else if(st_pos_stage==3 && bars>=6)
        { SelfTestWriteTask(StringFormat("st-pos%I64d-close50",pid),"position_id",pid,"close50","{}"); st_pos_stage=4; }
      else if(st_pos_stage==4 && bars>=8)
        { SelfTestWriteTask(StringFormat("st-pos%I64d-close",pid),"position_id",pid,"close","{}"); st_pos_stage=5; }
      else wrote=false;
      if(wrote) st_pos_last=now;
     }
  }

void HandleSignal(SignalCandidate &cand)
  {
   //--- §10.2 ATR floor (widen-only, symbol-scoped, all detectors - a per-detector floor would be fitting).
   //--- Structure is preserved: the structural invalidation level always sits INSIDE the widened stop.
   g_floor_pre=MathAbs(cand.entry-cand.sl); g_floor_post=g_floor_pre; g_floor_applied=0;
   if(InpStopFloorATR>0.0 && SymbolRoot()==InpStopFloorSymbol)
     {
      double fatr=SignalATR();
      if(fatr>0.0)
        {
         double need=InpStopFloorATR*fatr;
         if(g_floor_pre<need && cand.direction!=0)
           {
            cand.sl=NormPrice(cand.entry-cand.direction*need);
            g_floor_post=MathAbs(cand.entry-cand.sl); g_floor_applied=1;
           }
        }
     }
   int id;
   bool is_replay=g_delay_replaying;                                   // this call is a delay re-present
   if(g_delay_replaying){ id=g_delayed_id; g_delay_replaying=false; }  // re-present: keep id
   else                 { g_sig_seq++; id=g_sig_seq; }

   if(!is_replay) g_delay_count=0;   // fresh signal: reset the per-signal delay clock
   if(!is_replay) ComputeRegime(cand.direction,g_sig_regime,g_sig_with_trend);   // FREEZE at signal time
   if(!is_replay) g_sig_class=ClassifyProtocol(cand.strategy,g_sig_regime);       // protocol class, FROZEN with the tag (same on every delay re-present)
   if(!is_replay){ g_to_entry=NormPrice(cand.entry); g_to_sl=NormPrice(cand.sl);
                   g_to_tp1=NormPrice(cand.tp1); g_to_tp2=NormPrice(cand.tp2); }  // (1a) TRUE-ORIG freeze
   //--- (b) DELAY MODE (coach 2026-09-08): SLIDE re-anchors the whole plan to market (below);
   //--- FREEZE (default) keeps the detector's original levels and enters via a PENDING order at
   //--- the original entry, so the SL stays anchored to the structure it was drawn from.
   //--- SLIDE path: on a delayed re-present, SLIDE THE
   //--- WHOLE PLAN to the current market. Entry re-anchors to the market price and SL / TP /
   //--- TP1 / TP2 all shift by the SAME delta, so the risk & reward DISTANCES - and the R:R -
   //--- are preserved and every level updates together (no floating ratio). The parallel
   //--- shift keeps the stop distance intact, so it can never degenerate; delays stay
   //--- UNLIMITED and never auto-cancel. Skipped for pending/STOP setups (entry is a breakout
   //--- LEVEL, not the market). g_delayed is updated so the next delay slides from here.
   if(is_replay && !cand.stop_entry && InpDelayMode==DM_SLIDE && !InpLiveMode)   // live = FREEZE always
     {
      double mk=(cand.direction>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK)
                                  : SymbolInfoDouble(_Symbol,SYMBOL_BID));
      if(mk>0.0)
        {
         double ne=NormPrice(mk), d=ne-cand.entry;
         cand.entry=ne;
         cand.sl = NormPrice(cand.sl + d);
         if(cand.tp >0.0) cand.tp  = NormPrice(cand.tp  + d);
         if(cand.tp1>0.0) cand.tp1 = NormPrice(cand.tp1 + d);
         if(cand.tp2>0.0) cand.tp2 = NormPrice(cand.tp2 + d);
         g_delayed=cand;                 // persist the slid plan for the next re-present
        }
     }

   //--- ACCOUNT-SAFETY GATE (before sizing/overlays/dialog): a stop tighter than
   //--- the min distance is a degenerate signal - reject it, log it, journal it.
   //--- This is what catches the "1.9-pip stop -> 13-lot monster" class of bug.
   double atr_now=SignalATR();
   if(!is_replay)   // the setup passed this gate at first presentation; a delay re-present must
     {              // never auto-cancel through it (current ATR could push minstop past a valid stop)
      double stopdist=MathAbs(cand.entry-cand.sl);
      double minstop=MinStopDist(atr_now);
      if(stopdist<minstop || stopdist<=0.0)
        {
         double atrx=(atr_now>0.0? stopdist/atr_now : 0.0);
         JournalReject(id,cand,StringFormat("SL distance %s (%.2f ATR) < min %s - degenerate stop",
                       DoubleToString(stopdist,_Digits),atrx,DoubleToString(minstop,_Digits)));
         return;
        }
     }

   double lots=SizeByRisk(cand.entry,cand.sl);

   //--- INVERSE is ALWAYS an option on an EMArev trigger (trader ruling): offer it
   //--- whenever the flag is on and none is already open (max 1). The old fwd-V<=6h
   //--- HARD gate is gone - V-class events are so common (rate decisions/CPI/NFP/GDP
   //--- across every ccy) that it suppressed the button almost always. Imminent-V risk
   //--- is still surfaced: the popup's red event band shows a <6h event, and PlaceInverse
   //--- logs a V_WARN. The inverse cohort is ungraded, so this never touches the exam.
   //--- INVERSE dialog buttons DISABLED regardless of flags (coach audit 2026-09-08): the
   //--- live inverse changed position lifetimes and cascaded the graded signal SET vs the AA
   //--- baseline. The E1.X2 inverse is now a HEADLESS counterfactual (pipeline/backtest_emarev_inv.py),
   //--- never a live button. InpOfferInverse / InpTestInverse are inert.
   bool offer_inv=false;

   DrawOverlays(id,cand);
   //--- when INVERSE is on the table, overlay its ride entry/stop/take too (distinct
   //--- color + "INVERSE" labels) so the geometry is visible before the operator picks.
   if(offer_inv) DrawInverseOverlays(id,cand);
   PruneOverlays(id);
   //--- scroll the chart HARD RIGHT to the latest bar before the dialog opens, so
   //--- the advisor's decision-time screenshot always shows the most recent bars
   //--- (incl. the trigger). Autoscroll can lag in the visual tester and leave the
   //--- last few bars off the right edge; CHART_END forces the current bar into view.
   ChartSetInteger(0,CHART_AUTOSCROLL,true);
   ChartNavigate(0,CHART_END,0);
   ChartRedraw(0);

   //--- snapshot the detector's PROPOSED levels BEFORE the dialog can edit them
   //--- snapshot the detector's proposal ON THE TICK GRID (NormPrice), matching how the
   //--- committed levels are stored, so an off-grid emit can't later read as an 'edit'.
   double orig_entry=NormPrice(cand.entry), orig_sl=NormPrice(cand.sl), orig_tp=NormPrice(cand.tp);
   double orig_tp1=NormPrice(cand.tp1), orig_tp2=NormPrice(cand.tp2);

   //--- real-time advisor delivery: write the blind numbers NOW (before the
   //--- dialog) so the OS-capture daemon can deliver the setup while the popup is
   //--- up, not only after the decision writes the main journal row. The D1
   //--- daily-context series is dumped FIRST so that whenever the pending sidecar
   //--- exists (what the daemon waits on) the matching D1 file is already there.
   WriteD1Series(id);
   WritePendingSetup(id,cand,orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2);

   //--- PHASE 3 LIVE (Slice 2): no popup. Publish the signal to the file queue and PARK it on the
   //--- delay machinery; a task (Slice 3) or the bar-close replay (implicit delay / code 8) resumes
   //--- it through CommitDecision. Everything above this line ran exactly as in the tester.
   if(InpLiveMode){ LivePresent(id,cand,lots,is_replay,orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2); return; }

   string caption=StringFormat("Signal #%d  -  %s  %s",id,cand.strategy,DirStr(cand.direction));
   long decision_ms=0; int skip_reason=0; bool entry_edited=false; bool want_inv=false; bool want_delay=false;
   bool want_market=false;
   //--- default at a reopen = pending@frozen; overwritten to "market_now" on a placed market entry.
   string entry_mode=(g_delay_count>0 ? "pending_frozen" : "market");
   bool approved=false;
   string mkt_warn="";      // set when a market-now click is declined; shown in the re-opened popup
   //--- Approval LOOP: a sub-floor MARKET-NOW re-opens the SAME dialog immediately (same bar) with
   //--- the reason shown, instead of silently deferring a bar (which read as a Delay). It still
   //--- never PLACES a sub-floor market trade (coach: re-check the MinRR floor at click time).
   while(true)
     {
      want_inv=false; want_delay=false; want_market=false;
      string capw=(mkt_warn=="" ? caption : caption+"   [!] "+mkt_warn);
      approved=AskApproval(id,cand,lots,capw,decision_ms,skip_reason,entry_edited,offer_inv,want_inv,want_delay,want_market);
      //--- DELAY: defer this signal one bar (repeatable). No journal, no order - the same
      //--- candidate (unchanged levels) is re-presented next bar by the OnTick hook above.
      if(want_delay)
        {
         g_delayed=cand; g_delayed_id=id; g_delay_pending=true;
         g_delay_count++;                                   // (a) auditable: count + log this delay
         WriteDelayLog(id,g_delay_count,cand,"delay");
         Print("Signal #",id," DELAYED (#",g_delay_count,") - re-asking next bar; SL/TP frozen; at reopen choose pending@frozen or market-now.");
         return;   // KEEP the inverse preview: the same signal is re-presented next bar
        }
      //--- MARKET-NOW (item 3): at a delay reopen the trader chose to enter at market. Structural
      //--- SL/TP are kept (NEVER slid); entry -> market; lots resize to 1% off the real entry-SL
      //--- distance. The MinRR floor is RE-CHECKED at click time: if the market entry fails the
      //--- floor (or the stop is too tight) it is DECLINED and the dialog re-opens right away with
      //--- the reason -- never a silent sub-floor fill, and no longer a silent one-bar defer.
      if(want_market)
        {
         double en=0.0, rrn=0.0; string why="";
         if(!ValidateMarketEntry(cand,en,rrn,why))   // geom / MinRR floor / min-stop, re-checked at click (shared with the live approve validator)
           {
            Print("Signal #",id," MARKET-NOW declined (",why,") @ ",DoubleToString(en,_Digits),
                  " - re-asking; choose Pending @ frozen or Skip.");
            WriteDelayLog(id,g_delay_count,cand,"market_refused");   // audit (same-bar re-ask)
            mkt_warn="Market entry declined: "+why;
            continue;                                                // re-open the SAME dialog now
           }
         //--- surface the lots change: the popup showed the frozen-entry size, but market-now
         //--- resizes to 1% off the (wider/narrower) real entry-SL distance.
         Print("Signal #",id," MARKET-NOW @ ",DoubleToString(en,_Digits),
               " (frozen entry ",DoubleToString(orig_entry,_Digits),") - lots ",
               DoubleToString(lots,2)," -> ",DoubleToString(SizeByRisk(en,cand.sl),2),
               " (R:R ",DoubleToString(rrn,2),").");
         cand.entry=en; entry_mode="market_now"; approved=true;   // enter at market; structural SL/TP intact
        }
      break;   // Accept / Skip / Inverse / placed market-now -> leave the loop
     }
   CommitDecision(id,cand,caption,orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,
                  approved,skip_reason,decision_ms,entry_edited,want_inv,entry_mode);
  }

//+------------------------------------------------------------------+
//| COMMIT half of HandleSignal, extracted VERBATIM (Phase 3 M1 Slice 2)  |
//| so the live task consumer can resume a PARKED signal on a later     |
//| timer tick with exactly the code the tester runs: edit-detect ->    |
//| row append -> order placement -> journal. Called by the interactive |
//| path right after its approval loop (same args, same order), and by  |
//| LiveExecuteTask for approve/skip/code-8/auto-skip.                  |
//+------------------------------------------------------------------+
void CommitDecision(int id,SignalCandidate &cand,string caption,
                    double orig_entry,double orig_sl,double orig_tp,double orig_tp1,double orig_tp2,
                    bool approved,int skip_reason,long decision_ms,bool entry_edited,bool want_inv,
                    string entry_mode)
  {
   if(g_delay_count>0 && approved) WriteDelayLog(id,g_delay_count,cand,entry_mode);   // journal the final entry mode
   //--- decision resolved to a FADE or SKIP (not delay, not inverse): drop the orange
   //--- INVERSE preview lines so they never linger on a chart where a graded fade is
   //--- now open (would read as live orders). Kept when INVERSE was chosen - there the
   //--- lines show the active ride's levels. Harmless no-op if never drawn.
   if(!want_inv)
     { string pp=StringFormat("%s%d_",InpObjPrefix,id);
       string iobj[]={"ientry","isl","itake","ientryL","islL","itakeL"};
       for(int q=0;q<ArraySize(iobj);q++) ObjectDelete(0,pp+iobj[q]); }
   //--- the dialog may have retuned Entry/SL/TP (R:R held) - re-size on the
   //--- final risk distance so the placed order + journal use edited levels.
   double lots=SizeByRisk(cand.entry,cand.sl);
   //--- authoritative "operator edited a level" flag, taken from the COMMITTED
   //--- cand vs the detector's proposal BEFORE any fill overwrites cand.entry.
   //--- (Can't infer this in review from orig_entry vs entry: entry is later
   //--- overwritten by the realised fill, which ~never equals the proposal.)
   double etol=0.5*SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(etol<=0.0) etol=0.5*_Point;
   //--- for two-target strategies the order TP is the RUNNER (tp2); we bank
   //--- partial_fraction at tp1 en route and move SL to BE.
   bool two_target=(cand.partial_fraction>0.0 && cand.tp1>0.0 && cand.tp2>0.0);
   //--- PHANTOM edited=1 fix: diff only the OPERATOR-EDITABLE fields. cand.tp is an
   //--- internal field the approve-commit reassigns to the runner for scale-out (so it
   //--- != the detector's orig tp even when nothing was edited) - exclude it for two-
   //--- target setups; only entry/SL/tp1/tp2 are user-editable there.
   //--- market-now (item 3) legitimately moves cand.entry to the market price; that is a
   //--- protocol entry MODE, not an operator edit, so it must NOT set edited=1 (the coach's
   //--- grading reads edited as "operator changed a level"). Exclude the entry term on that path;
   //--- SL/TP edits (if any) still count. entry_mode="market_now" is the audit trail for it.
   bool mkt_now=(entry_mode=="market_now");
   bool any_edited=((!mkt_now && MathAbs(cand.entry-orig_entry)>etol)
                    || MathAbs(cand.sl-orig_sl)>etol
                    || (two_target
                        ? (MathAbs(cand.tp1-orig_tp1)>etol || MathAbs(cand.tp2-orig_tp2)>etol)
                        : MathAbs(cand.tp-orig_tp)>etol));
   double order_tp=(two_target? cand.tp2 : cand.tp);

   int n=ArraySize(g_rows); ArrayResize(g_rows,n+1);
   g_rows[n].id=id; g_rows[n].time=cand.zone_to; g_rows[n].symbol=_Symbol;
   g_rows[n].strategy=cand.strategy; g_rows[n].direction=cand.direction;
   g_rows[n].orig_entry=orig_entry; g_rows[n].orig_sl=orig_sl; g_rows[n].orig_tp=orig_tp;
   g_rows[n].orig_tp1=orig_tp1; g_rows[n].orig_tp2=orig_tp2;
   g_rows[n].entry=cand.entry; g_rows[n].sl=cand.sl; g_rows[n].tp=order_tp;
   g_rows[n].tp1=cand.tp1; g_rows[n].tp2=cand.tp2; g_rows[n].partial_frac=(two_target?cand.partial_fraction:0.0);
   g_rows[n].lots=lots; g_rows[n].risk_px=MathAbs(cand.entry-cand.sl);
   g_rows[n].tp1_done=(!two_target); g_rows[n].banked=false; g_rows[n].closed_vol=0.0;
   //--- ArrayResize does NOT zero new struct elements: init the item-1/2 fields explicitly so a
   //--- fresh row never inherits a stale ratcheted=true (button permanently greyed) or rt_ garbage.
   g_rows[n].ratcheted=false; g_rows[n].rt_bankr=-99.0;
   g_rows[n].rt_tp1R=0.0; g_rows[n].rt_tp2R=0.0;
   g_rows[n].rt_touched1=0; g_rows[n].rt_reached2=0; g_rows[n].rt_redip1=0;
   g_rows[n].decision_ms=decision_ms; g_rows[n].skip_reason=0; g_rows[n].edited=any_edited;
   g_rows[n].is_pending=false; g_rows[n].order_ticket=0; g_rows[n].placed_time=0;
   g_rows[n].posid=0; g_rows[n].closed=false;
   g_rows[n].regime=g_sig_regime; g_rows[n].with_trend=g_sig_with_trend; g_rows[n].decision_class=g_sig_class;
   g_rows[n].to_entry=g_to_entry; g_rows[n].to_sl=g_to_sl; g_rows[n].to_tp1=g_to_tp1; g_rows[n].to_tp2=g_to_tp2;
   g_rows[n].mfe_r=0.0; g_rows[n].pre_dip_r=0.0; g_rows[n].post_dip_r=0.0; g_rows[n].dipped=0; g_rows[n].terminal="";
   g_rows[n].imp_atr=0.0; g_rows[n].imp_nbig=0; g_rows[n].cal_lab=0; g_rows[n].v2_r=0.0; g_rows[n].v2_bank=0.0; g_rows[n].v2_runner=-1;
   if(!InpV2Exit){ ComputeImpulse(n); g_rows[n].cal_lab=CalLabeled(g_rows[n].time); }   // STUDY-ONLY features (no live footprint)
   g_rows[n].exit_time=0; g_rows[n].exit_price=0.0; g_rows[n].pnl=0.0; g_rows[n].r_multiple=0.0;
   //--- PHASE 3 LIVE columns (never written in the tester; see WriteJournal)
   g_rows[n].live=InpLiveMode; g_rows[n].account_id=g_account_login; g_rows[n].risk_pct_gate=InpRiskPct;
   g_rows[n].risk_mult_applied=g_risk_mult; g_rows[n].auto_skip=(g_live_auto?1:0); g_rows[n].entry_mode=entry_mode;
   g_rows[n].stop_pre_floor=g_floor_pre; g_rows[n].stop_post_floor=g_floor_post; g_rows[n].floor_applied=g_floor_applied;

   if(want_inv)
     {
      //--- trader chose INVERSE: the graded stream records a plain SKIP (reason 7 =
      //--- took-inverse); the inverse trade lives ONLY in the isolated .inv journal.
      //--- No graded position -> no one-setup lock, no graded R. Schema byte-identical.
      g_rows[n].decision="skip"; g_rows[n].skip_reason=7;
      WriteJournal(g_journal_part);
      PlaceInverse(id,cand);
      return;
     }

   if(approved)
     {
      g_rows[n].decision="approved";
      if(InpLiveMode && !g_trading_enabled)
        {   // belt-and-braces (the task validator already refuses): never place while the kill switch is off
         Print("Signal #",id," LIVE: kill switch OFF - order suppressed at placement.");
         AuditLine("kill_switch","","approve",StringFormat("sig:%d",id),"suppressed","trading_disabled","");
        }
      else if(lots<=0.0)
        {
         Print("Signal #",id," approved but lots<=0 - NOT placing (check ",_Symbol," specs).");
        }
      else
        {
         //--- decide market vs pending. Entry edited far enough from the market
         //--- => pending (limit on the favourable side, stop on the breakout
         //--- side). Untouched (or edited back to ~market) => market fill as today.
         double mkt=(cand.direction>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK)
                                     : SymbolInfoDouble(_Symbol,SYMBOL_BID));
         double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(ts<=0.0) ts=_Point;
         double gate=MathMax(ts,(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point);
         double pend_price=cand.entry;
         //--- a detector can request a breakout STOP entry (cand.stop_entry) so the
         //--- setup only fills on resumption and cancels if unfilled (ShockCont).
         //--- FREEZE-mode delayed signal: enter as a PENDING at the frozen original entry so the
         //--- structural SL is kept (trap: entry_edited/stop_entry are both false for it).
         bool freeze_pend=(InpDelayMode==DM_FREEZE && g_delay_count>0) || (InpLiveMode && entry_mode=="pending_frozen");
         bool want_pending=((entry_edited || cand.stop_entry || freeze_pend) && MathAbs(cand.entry-mkt)>gate);
         //--- TEST-ONLY headless probe (AA_ALL): force a pending so the
         //--- OnTradeTransaction fill-binding + expiry paths can be verified.
         //--- InpForcePendingPts > 0 => STOP just past market (fills fast);
         //--- InpForcePendingPts < 0 => far LIMIT on the favourable side
         //--- (won't fill -> exercises the expiry path); 0 => auto STOP.
         if(InpForcePendingTest && InpAutoApprove==AA_ALL)
           {
            double off=(InpForcePendingPts!=0? MathAbs(InpForcePendingPts)*_Point : gate*3.0);
            want_pending=true;
            if(InpForcePendingPts<0)   // far limit -> likely expires unfilled
               pend_price=(cand.direction>0? mkt-off : mkt+off);
            else                       // stop just past market -> fills quickly
               pend_price=(cand.direction>0? mkt+off : mkt-off);
            //--- rigid-shift SL/TP with the forced entry so geometry stays valid
            //--- (mirrors real EF_RIGID); keep the row's risk basis consistent.
            double d=pend_price-cand.entry;
            cand.sl+=d; order_tp+=d;
            g_rows[n].sl=cand.sl; g_rows[n].tp=order_tp;
            g_rows[n].risk_px=MathAbs(pend_price-cand.sl);
           }

         if(!want_pending)
           {
            //--- MARKET fill (unchanged behaviour)
            bool ok=(cand.direction>0)
                    ? g_trade.Buy(lots,_Symbol,0.0,cand.sl,order_tp,caption)
                    : g_trade.Sell(lots,_Symbol,0.0,cand.sl,order_tp,caption);
            if(ok)
              {
               ulong deal=g_trade.ResultDeal();
               bool bound=false;
               for(int tries=0;tries<10 && !bound;tries++)
                 {
                  if(deal>0 && HistoryDealSelect(deal))
                    {
                     g_rows[n].posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
                     double fill=HistoryDealGetDouble(deal,DEAL_PRICE);
                     if(fill>0.0) { g_rows[n].entry=fill; g_rows[n].risk_px=MathAbs(fill-cand.sl); }
                     bound=(g_rows[n].posid>0);
                    }
                  if(!bound && InpLiveMode && !(bool)MQLInfoInteger(MQL_TESTER)) Sleep(200);   // live: the deal lands a moment after the retcode
                  else break;
                 }
               if(!bound && InpLiveMode)
                 {   // fallback: the position that carries this signal's caption (comment) under our magic
                  for(int p=PositionsTotal()-1;p>=0 && !bound;p--)
                    {
                     ulong tk=PositionGetTicket(p); if(tk==0) continue;
                     if(PositionGetString(POSITION_SYMBOL)!=_Symbol || PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
                     if(StringFind(PositionGetString(POSITION_COMMENT),StringFormat("Signal #%d ",id))!=0) continue;
                     g_rows[n].posid=(long)PositionGetInteger(POSITION_IDENTIFIER);
                     double fill=PositionGetDouble(POSITION_PRICE_OPEN);
                     if(fill>0.0) { g_rows[n].entry=fill; g_rows[n].risk_px=MathAbs(fill-cand.sl); }
                     bound=true; AuditLine("fill_bound","","",StringFormat("sig:%d",id),"ok","by_comment",StringFormat("posid=%I64d",g_rows[n].posid));
                    }
                 }
               if(two_target) MinLotSplitGuard(n);
               Print("Signal #",id," APPROVED -> ",cand.strategy," ",DirStr(cand.direction)," ",
                     DoubleToString(lots,2)," lots posid=",g_rows[n].posid,
                     (two_target?"  [scale-out TP1/TP2]":"  [single TP]"));
              }
            else
               Print("Signal #",id," order FAILED: ",g_trade.ResultRetcode()," ",
                     g_trade.ResultRetcodeDescription());
           }
         else
           {
            //--- PENDING order at the edited entry (fill binds later via OnTradeTransaction)
            pend_price=NormPrice(pend_price);
            ENUM_ORDER_TYPE ot;
            if(cand.direction>0) ot=(pend_price<mkt? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_BUY_STOP);
            else                 ot=(pend_price>mkt? ORDER_TYPE_SELL_LIMIT: ORDER_TYPE_SELL_STOP);
            bool ok=false;
            switch(ot)
              {
               case ORDER_TYPE_BUY_LIMIT:  ok=g_trade.BuyLimit (lots,pend_price,_Symbol,cand.sl,order_tp,ORDER_TIME_GTC,0,caption); break;
               case ORDER_TYPE_BUY_STOP:   ok=g_trade.BuyStop  (lots,pend_price,_Symbol,cand.sl,order_tp,ORDER_TIME_GTC,0,caption); break;
               case ORDER_TYPE_SELL_LIMIT: ok=g_trade.SellLimit(lots,pend_price,_Symbol,cand.sl,order_tp,ORDER_TIME_GTC,0,caption); break;
               case ORDER_TYPE_SELL_STOP:  ok=g_trade.SellStop (lots,pend_price,_Symbol,cand.sl,order_tp,ORDER_TIME_GTC,0,caption); break;
              }
            if(ok)
              {
               g_rows[n].is_pending=true;
               g_rows[n].order_ticket=(long)g_trade.ResultOrder();
               g_rows[n].placed_time=TimeCurrent();
               g_rows[n].decision="approved_pending";
               g_rows[n].entry=pend_price;               // journal the pending price until fill
               g_rows[n].risk_px=MathAbs(pend_price-cand.sl);
               ObjectSetDouble(0,StringFormat("%s%d_entry",InpObjPrefix,id),OBJPROP_PRICE,pend_price);
               ChartRedraw(0);
               Print("Signal #",id," PENDING ",OrderTypeName(ot)," @ ",DoubleToString(pend_price,_Digits),
                     " ticket=",g_rows[n].order_ticket," (expires in ",InpPendingExpiryBars," H4 bars)");
              }
            else
               Print("Signal #",id," pending ",OrderTypeName(ot)," FAILED: ",g_trade.ResultRetcode(),
                     " ",g_trade.ResultRetcodeDescription());
           }
        }
     }
   else
     {
      g_rows[n].decision="skipped";
      g_rows[n].skip_reason=skip_reason;
      Print("Signal #",id," ",cand.strategy," SKIPPED (reason ",skip_reason,
            ", decided in ",decision_ms," ms)");
     }

   WriteJournal(g_journal_part);
  }

//+------------------------------------------------------------------+
//| Per-tick MECHANICAL EXIT (Doctrine v2, coach 2026-09-09): at the      |
//| first touch of +1R open profit -- OR tp1 if it sits nearer than +1R -- |
//| close 50% and move the stop to break-even. Applies to ALL THREE       |
//| strategies (SweepMSS single-target included), fires ONCE per position  |
//| (g_rows[].banked), journaled as an auto action. The remaining 50% runs |
//| to whatever TP the order already carries (tp2 for scale-out setups,    |
//| the single tp otherwise) -- "whichever level is hit first governs."    |
//+------------------------------------------------------------------+
void ManageOpenPositions()
  {
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN); if(vmin<=0)vmin=0.01;
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);

   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if((g_rows[i].decision!="approved" && g_rows[i].decision!="approved_pending")
         || g_rows[i].posid<=0) continue;

      //--- STUDY-ONLY (InpV2Exit==false): reproduce the PRE-v2 exit (2026-09-08
      //--- scale-at-tp1, UNPADDED POSITION_PRICE_OPEN BE) EXACTLY, so the frozen signal
      //--- set + one-setup-lock timing are byte-identical while the shadow observer
      //--- (TrackAllMfePath) reconstructs the doctrine-v2 R alongside. Do NOT "improve".
      if(!InpV2Exit)
        {
         if(g_rows[i].closed || g_rows[i].tp1_done) continue;
         if(g_rows[i].partial_frac<=0.0 || g_rows[i].tp1<=0.0) continue;
         if(!PositionSelectByTicket((ulong)g_rows[i].posid)) continue;
         int dir0=g_rows[i].direction;
         bool reached0=(dir0>0 ? bid>=g_rows[i].tp1 : ask<=g_rows[i].tp1);
         if(!reached0) continue;
         double lots0=g_rows[i].lots;
         double pv0=MathFloor((g_rows[i].partial_frac*lots0)/step)*step;
         double be0=PositionGetDouble(POSITION_PRICE_OPEN);   // unpadded (defines real close -> lock)
         if(pv0>=vmin && (lots0-pv0)>=vmin)
           {
            if(g_trade.PositionClosePartial((ulong)g_rows[i].posid,pv0))
               Print("Signal #",g_rows[i].id," [study old-exit] TP1 -> banked ",
                     DoubleToString(pv0,2)," lots, SL->BE, runner TP2");
            g_trade.PositionModify((ulong)g_rows[i].posid,be0,g_rows[i].tp2);
           }
         g_rows[i].tp1_done=true;
         continue;
        }

      if(g_rows[i].closed || g_rows[i].banked) continue;      // one-time; NOT gated on tp1_done
      if(g_rows[i].risk_px<=0.0) continue;
      if(!PositionSelectByTicket((ulong)g_rows[i].posid)) continue;   // already gone

      int dir=g_rows[i].direction;
      //--- the +1R price, and (for scale-out setups) tp1 if it is the nearer level.
      double r1  =(dir>0 ? g_rows[i].entry+g_rows[i].risk_px : g_rows[i].entry-g_rows[i].risk_px);
      double trig=r1; bool via_tp1=false;
      if(g_rows[i].partial_frac>0.0 && g_rows[i].tp1>0.0)
        {
         bool tp1_nearer=(dir>0 ? g_rows[i].tp1<=r1 : g_rows[i].tp1>=r1);
         if(tp1_nearer){ trig=g_rows[i].tp1; via_tp1=true; }
        }
      bool reached=(dir>0 ? bid>=trig : ask<=trig);
      if(!reached) continue;

      double lots=PositionGetDouble(POSITION_VOLUME);       // live remaining volume
      double be  =NormPrice(BEPrice(i));                    // padded BE (same basis as manual)
      double rtp =PositionGetDouble(POSITION_TP);           // preserve the runner's TP
      double px  =(dir>0 ? bid : ask);
      double orr =OpenR(i);                                 // R at trigger (full vol, ~+1.0R)
      double pv  =MathFloor((0.5*lots)/step)*step;          // mechanical 50%
      string tag =(via_tp1 ? "AUTO_TP1" : "AUTO_1R");
      if(pv>=vmin && (lots-pv)>=vmin)
        {
         if(g_trade.PositionClosePartial((ulong)g_rows[i].posid,pv))
            Print("Signal #",g_rows[i].id," ",tag," -> banked ",DoubleToString(pv,2),
                  " lots (50%), SL -> BE, runner to TP");
         else
            Print("Signal #",g_rows[i].id," ",tag," partial close FAILED: ",g_trade.ResultRetcode());
         g_trade.PositionModify((ulong)g_rows[i].posid,be,rtp);
         LogManualAction(i,tag,px,lots,lots-pv,be,orr);
        }
      else
        {
         //--- volume too small to split: honour the doctrine's stop half (SL->BE),
         //--- skip the partial, and label it so the ledger shows the degenerate case.
         if(g_trade.PositionModify((ulong)g_rows[i].posid,be,rtp))
            Print("Signal #",g_rows[i].id," ",tag," (min-lot) -> SL only to BE, no partial");
         LogManualAction(i,tag+"_BE_ONLY",be,lots,lots,be,orr);
        }
      g_rows[i].banked=true; g_rows[i].tp1_done=true;
      g_rows[i].rt_bankr=orr;   // TP1-ratchet study: the R the +1R bank actually fired at (per-unit)
     }
  }

//+------------------------------------------------------------------+
//| MID-TRADE MANAGEMENT PANEL                                        |
//+------------------------------------------------------------------+
//--- index of the row holding the currently OPEN position (posid bound, not
//--- closed, still selectable), or -1. Both the state refresh and the action
//--- paths need the ROW, which HasOpenPosition() doesn't give.
int ActiveRowIdx()
  {
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      if(g_rows[i].posid<=0 || g_rows[i].closed) continue;
      if(g_rows[i].symbol!=_Symbol) continue;
      if(PositionSelectByTicket((ulong)g_rows[i].posid)) return i;
     }
   return -1;
  }

//--- pip size (a "pip" = 10 points on 3/5-digit FX quotes, else 1 point)
double PipSize() { return ((_Digits==3 || _Digits==5) ? 10.0*_Point : _Point); }

//--- BE stop price for a row's open position: the ACTUAL fill (row.entry) +
//--- pad in the PROFIT direction (pad covers the spread so a hit is ~flat).
double BEPrice(int idx)
  {
   double pad=InpBEPadPips*PipSize();
   return (g_rows[idx].direction>0 ? g_rows[idx].entry+pad : g_rows[idx].entry-pad);
  }

//--- +0.5R BE floor is a LIVE HARDWARE GATE (coach 2026-09-10, on trader's word): the SL->BE
//--- button is disabled while Open R < +0.5R. Five lifetime stop-touch charges + the -2.00R trial
//--- delta moved the guard out of the grading report into the button. Uses the SAME OpenR metric
//--- the grader logs (actions.csv open_r), so the live gate enforces exactly what
//--- be_counterfactual measures (FLOOR=0.5). The reversal-bar condition stays a grading judgment;
//--- the SL->TP1 runner button is untouched.
const double BE_FLOOR_R=0.5;

//--- is SL->BE currently placeable? Gated on (a) the +0.5R Open-R floor above, and (b) the
//--- broker's stops-level band (PositionModify rejects an SL inside it, so the click would
//--- silently do nothing).
bool BEPlaceable(int idx)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return false;
   if(OpenR(idx)<BE_FLOOR_R) return false;               // +0.5R live floor (coach 2026-09-10)
   double be=BEPrice(idx);
   double stops=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   if(g_rows[idx].direction>0) return (bid-be)>=stops;   // BUY: SL must sit below bid
   return (be-ask)>=stops;                               // SELL: SL must sit above ask
  }

//--- unrealised R on the CURRENT (remaining) volume, volume-weighted exactly
//--- like OnTradeTransaction accumulates banked R (wfrac = vol / initial-lots),
//--- so Open R + Banked R = close-now total and neither double-counts.
double OpenR(int idx)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return 0.0;
   if(g_rows[idx].risk_px<=0.0 || g_rows[idx].lots<=0.0) return 0.0;
   double vol=PositionGetDouble(POSITION_VOLUME);
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double exitpx=(g_rows[idx].direction>0? bid : ask);   // exit-side price
   double moved =(g_rows[idx].direction>0? exitpx-g_rows[idx].entry : g_rows[idx].entry-exitpx);
   return (vol/g_rows[idx].lots)*(moved/g_rows[idx].risk_px);
  }

//--- persist the manual-actions log (sibling to the journal CSV).
string ActionRowLine(ManualAction &a)
  {
   return StringFormat("%d,%d,%s,%s,%s,%s,%s,%s,%.2f,%.2f",
         a.id,a.posid,TimeToString(a.bar_time,TIME_DATE|TIME_SECONDS),a.action,
         DoubleToString(a.price,_Digits),DoubleToString(a.lots_before,2),
         DoubleToString(a.lots_after,2),DoubleToString(a.sl_after,_Digits),
         a.banked_r,a.open_r);
  }
void WriteLiveActions()
  {
   string months[]; int nm=0;
   for(int i=0;i<ArraySize(g_actions);i++)
     { string m=StampMonth(g_actions[i].bar_time); bool have=false; for(int q=0;q<nm;q++) if(months[q]==m) have=true;
       if(!have){ ArrayResize(months,nm+1); months[nm++]=m; } }
   for(int q=0;q<nm;q++)
     {
      string body="signal_id,posid,bar_time,action,price,lots_before,lots_after,sl_after,banked_r,open_r\n";
      for(int i=0;i<ArraySize(g_actions);i++) if(StampMonth(g_actions[i].bar_time)==months[q]) body+=ActionRowLine(g_actions[i])+"\n";
      AtomicWriteText(LivePath(StringFormat("journal\\%s_%s.actions.csv",_Symbol,months[q])),body);
     }
  }
void WriteActions(string path)
  {
   if(InpLiveMode){ WriteLiveActions(); LiveSaveAllState(); return; }
   if(path=="") return;
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("WARNING: cannot open actions log '",path,"' err=",GetLastError()); return; }
   FileWriteString(h,"signal_id,posid,bar_time,action,price,lots_before,lots_after,sl_after,banked_r,open_r\n");
   for(int i=0;i<ArraySize(g_actions);i++) FileWriteString(h,ActionRowLine(g_actions[i])+"\n");
   FileFlush(h); FileClose(h);
  }

//--- STUDY-ONLY sidecar (InpV2Exit==false only): shadow doctrine-v2 reconstruction +
//--- trigger-impulse feature, per signal. Never written on the live path.
void WriteV2Study(string path)
  {
   if(path=="") return;
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("WARNING: cannot open v2 study '",path,"' err=",GetLastError()); return; }
   FileWriteString(h,"signal_id,signal_time,symbol,strategy,direction,decision,closed,"
                     "tp1r,tp2r,trigr,mfe_r,pre_dip_r,post_dip_r,dipped,"
                     "v2_bank,v2_runner,v2_r,imp_atr,imp_nbig,cal_lab,old_r\n");
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      JournalRow r=g_rows[i];
      double risk=r.risk_px; int d=r.direction;
      double tp1r =(r.tp1>0.0 && risk>0.0 ? d*(r.tp1-r.entry)/risk : 0.0);
      double tp2r =(risk>0.0 ? d*((r.tp2>0.0? r.tp2 : r.tp)-r.entry)/risk : 0.0);
      double trigr=((r.partial_frac>0.0 && r.tp1>0.0) ? MathMin(1.0,tp1r) : 1.0);
      FileWriteString(h,StringFormat(
         "%d,%s,%s,%s,%s,%s,%d,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%d,%.4f,%d,%.4f,%.3f,%d,%d,%s\n",
         r.id,TimeToString(r.time,TIME_DATE|TIME_SECONDS),r.symbol,r.strategy,DirStr(r.direction),
         r.decision,(r.closed?1:0),
         tp1r,tp2r,trigr,r.mfe_r,r.pre_dip_r,r.post_dip_r,r.dipped,
         r.v2_bank,r.v2_runner,r.v2_r,r.imp_atr,r.imp_nbig,r.cal_lab,
         (r.closed?DoubleToString(r.r_multiple,3):"")));
     }
   FileFlush(h); FileClose(h);
  }

//==================================================================================
//  EMArev INVERSE cohort — fully isolated from the graded stream (own journal, own
//  magic, own state). None of this ever writes g_rows[] or the graded journal.
//==================================================================================
//--- calendar gates (reuse the EA's loaded econ cache g_ev_*; class strings V/W/C).
bool InverseFwdVClear(datetime sigtime)   // false => a symbol-relevant forward V within 6h
  {
   //--- SCOPE to the traded symbol's currencies (base/quote/"All"), like DrawEconEvents.
   //--- V-class is very common (rate decisions, CPI, NFP, GDP, PCE... across EVERY ccy);
   //--- an unrelated JPY CPI must NOT flag an XAUUSD/EURUSD setup. No longer a hard gate
   //--- on the offer (see offer_inv) - used only as an imminent-V *warning* at placement.
   string base,quote; SymbolCcy(base,quote);
   for(int i=0;i<ArraySize(g_ev_t);i++)
     {
      if(g_ev_cls[i]!="V") continue;
      if(g_ev_ccy[i]!=base && g_ev_ccy[i]!=quote && g_ev_ccy[i]!="All") continue;
      if(g_ev_t[i]>sigtime && g_ev_t[i]<=sigtime+6*3600) return false;
     }
   return true;
  }
bool InverseWInHold(datetime sigtime)     // a W-class event in the next ~3 days
  {
   for(int i=0;i<ArraySize(g_ev_t);i++)
      if(g_ev_cls[i]=="W" && g_ev_t[i]>sigtime && g_ev_t[i]<=sigtime+3*86400) return true;
   return false;
  }
//--- isolated journal writer (same 28-col schema, over g_inv_rows[]).
void WriteInvJournal(string path)
  {
   if(path=="") return;
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("WARNING: cannot open inv journal '",path,"' err=",GetLastError()); return; }
   FileWriteString(h,
      "signal_id,signal_time,symbol,strategy,direction,"
      "orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,entry,sl,tp,tp1,tp2,partial_frac,lots,"
      "decision,skip_reason,edited,is_pending,decision_ms,posid,tp1_done,"
      "exit_time,exit_price,pnl,r_multiple\n");
   for(int i=0;i<ArraySize(g_inv_rows);i++)
     {
      JournalRow r=g_inv_rows[i];
      FileWriteString(h,StringFormat(
         "%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%.2f,%s,%s,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s\n",
         r.id,TimeToString(r.time,TIME_DATE|TIME_SECONDS),r.symbol,r.strategy,DirStr(r.direction),
         DoubleToString(r.orig_entry,_Digits),DoubleToString(r.orig_sl,_Digits),DoubleToString(r.orig_tp,_Digits),
         "","",DoubleToString(r.entry,_Digits),DoubleToString(r.sl,_Digits),DoubleToString(r.tp,_Digits),"","",
         r.partial_frac,DoubleToString(r.lots,2),
         r.decision,r.skip_reason,(r.edited?1:0),(r.is_pending?1:0),r.decision_ms,r.posid,(r.tp1_done?1:0),
         (r.closed?TimeToString(r.exit_time,TIME_DATE|TIME_SECONDS):""),
         (r.closed?DoubleToString(r.exit_price,_Digits):""),
         (r.closed?DoubleToString(r.pnl,2):""),
         (r.closed?DoubleToString(r.r_multiple,2):"")));
     }
   FileFlush(h); FileClose(h);
  }
void WriteInvActions(string path)
  {
   if(path=="") return;
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE) return;
   FileWriteString(h,"signal_id,posid,bar_time,action,price,lots_before,lots_after,sl_after,banked_r,open_r\n");
   for(int i=0;i<ArraySize(g_inv_actions);i++)
     {
      ManualAction a=g_inv_actions[i];
      FileWriteString(h,StringFormat("%d,%d,%s,%s,%s,%s,%s,%s,%.2f,%.2f\n",
         a.id,a.posid,TimeToString(a.bar_time,TIME_DATE|TIME_SECONDS),a.action,
         DoubleToString(a.price,_Digits),DoubleToString(a.lots_before,2),
         DoubleToString(a.lots_after,2),DoubleToString(a.sl_after,_Digits),a.banked_r,a.open_r));
     }
   FileFlush(h); FileClose(h);
  }
double InvOpenR()   // unrealised R on the open inverse
  {
   if(!g_inv_open || g_inv_open_idx<0) return 0.0;
   int k=g_inv_open_idx;
   if(!PositionSelectByTicket((ulong)g_inv_posid)) return 0.0;
   double px=(g_inv_rows[k].direction>0? SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   double moved=(g_inv_rows[k].direction>0? px-g_inv_rows[k].entry : g_inv_rows[k].entry-px);
   double vol=PositionGetDouble(POSITION_VOLUME);
   double wf=(g_inv_rows[k].lots>0? vol/g_inv_rows[k].lots:1.0);
   return (g_inv_rows[k].risk_px>0? wf*moved/g_inv_rows[k].risk_px : 0.0);
  }
void LogInvAction(string what,double price,double lb,double la,double sl)
  {
   if(g_inv_open_idx<0) return;
   int n=ArraySize(g_inv_actions); ArrayResize(g_inv_actions,n+1);
   g_inv_actions[n].id=g_inv_rows[g_inv_open_idx].id; g_inv_actions[n].posid=g_inv_posid;
   g_inv_actions[n].bar_time=iTime(_Symbol,g_tf,0); g_inv_actions[n].action=what;
   g_inv_actions[n].price=price; g_inv_actions[n].lots_before=lb; g_inv_actions[n].lots_after=la;
   g_inv_actions[n].sl_after=sl; g_inv_actions[n].banked_r=g_inv_rows[g_inv_open_idx].r_multiple;
   g_inv_actions[n].open_r=InvOpenR();
   WriteInvActions(g_inv_actions_part);
  }
//--- open a market inverse (E1 entry, EMA-stop, no TP; auto-close 12 H4 bars).
void PlaceInverse(int id,SignalCandidate &cand)
  {
   if(g_inv_open){ Print("Inverse: one already open - refused (max 1)."); return; }
   int inv_dir=-cand.direction;
   double ema=cand.tp1;                                  // frozen EMA20 (mean) from EMArev
   double atr=(InpEmaStretch>0.0? (cand.zone_hi-cand.zone_lo)/InpEmaStretch : 0.0);
   if(atr<=0.0){ double a[]; ArraySetAsSeries(a,true); int hA=iATR(_Symbol,g_tf,14);
                 if(hA!=INVALID_HANDLE && CopyBuffer(hA,0,1,1,a)>0) atr=a[0]; }
   if(atr<=0.0){ Print("Inverse #",id," no ATR - refused."); return; }
   double spread=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID); if(spread<0)spread=0;
   double buf=MathMax(0.10*atr,spread);
   double entry=(inv_dir>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID));
   double ema_dist=MathAbs(entry-ema);
   double risk=(ema_dist<1.0*atr ? 1.0*atr : MathMin(ema_dist+buf,2.5*atr));   // cap 2.5, floor 1.0
   double sl=(inv_dir>0? entry-risk : entry+risk);
   double lots=SizeByRisk(entry,sl);
   if(lots<=0.0){ Print("Inverse #",id," lots<=0 - not placing."); return; }
   //--- W-in-hold measured from the ACTUAL entry bar (current), not the trigger:
   //--- a delayed INVERSE enters now and its projected hold runs from here.
   bool w_warn=InverseWInHold(iTime(_Symbol,g_tf,0));
   //--- fwd-V no longer BLOCKS the offer (trader ruling); it warns instead. A symbol-
   //--- relevant V-class event within 6h of entry = the trader is riding into a print.
   bool v_warn=!InverseFwdVClear(iTime(_Symbol,g_tf,0));
   g_trade.SetExpertMagicNumber(InpInverseMagic);        // ISOLATE: separate magic
   string cap=StringFormat("EMArevINV #%d",id);
   bool ok=(inv_dir>0? g_trade.Buy(lots,_Symbol,0.0,sl,0.0,cap) : g_trade.Sell(lots,_Symbol,0.0,sl,0.0,cap));
   g_trade.SetExpertMagicNumber(InpMagic);               // restore graded magic immediately
   if(!ok){ Print("Inverse #",id," order FAILED: ",g_trade.ResultRetcode()," ",g_trade.ResultRetcodeDescription()); return; }
   long posid=0; double fill=entry; ulong deal=g_trade.ResultDeal();
   if(deal>0 && HistoryDealSelect(deal)){ posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
       double f=HistoryDealGetDouble(deal,DEAL_PRICE); if(f>0) fill=f; }
   int n=ArraySize(g_inv_rows); ArrayResize(g_inv_rows,n+1);
   g_inv_rows[n].id=id; g_inv_rows[n].time=cand.zone_to; g_inv_rows[n].symbol=_Symbol;
   g_inv_rows[n].strategy="EMArevINV"; g_inv_rows[n].direction=inv_dir;
   g_inv_rows[n].orig_entry=entry; g_inv_rows[n].orig_sl=sl; g_inv_rows[n].orig_tp=0;
   g_inv_rows[n].orig_tp1=0; g_inv_rows[n].orig_tp2=0;
   g_inv_rows[n].entry=fill; g_inv_rows[n].sl=sl; g_inv_rows[n].tp=0;
   g_inv_rows[n].tp1=0; g_inv_rows[n].tp2=0; g_inv_rows[n].partial_frac=0;
   g_inv_rows[n].lots=lots; g_inv_rows[n].risk_px=MathAbs(fill-sl);
   g_inv_rows[n].tp1_done=true; g_inv_rows[n].closed_vol=0;
   g_inv_rows[n].decision="approved_inverse"; g_inv_rows[n].decision_ms=0; g_inv_rows[n].skip_reason=0;
   g_inv_rows[n].edited=false; g_inv_rows[n].is_pending=false; g_inv_rows[n].order_ticket=0;
   g_inv_rows[n].placed_time=TimeCurrent(); g_inv_rows[n].posid=posid; g_inv_rows[n].closed=false;
   g_inv_rows[n].exit_time=0; g_inv_rows[n].exit_price=0; g_inv_rows[n].pnl=0; g_inv_rows[n].r_multiple=0;
   g_inv_open=true; g_inv_open_idx=n; g_inv_posid=posid;
   g_inv_entry_bar=iTime(_Symbol,g_tf,0); g_inv_extended=false;
   WriteInvJournal(g_inv_journal_part);
   Print("Signal #",id," INVERSE ",DirStr(inv_dir)," ",DoubleToString(lots,2)," lots @ ",
         DoubleToString(fill,_Digits)," SL ",DoubleToString(sl,_Digits)," (EMA-stop; auto-close 12 H4 bars)",
         (w_warn?"  [WARN: W event in projected hold - ungraded, trader proceeded]":""),
         (v_warn?"  [WARN: V event <6h - riding into a print, trader proceeded]":""));
   if(w_warn) LogInvAction("W_WARN",fill,lots,lots,sl);
   if(v_warn) LogInvAction("V_WARN",fill,lots,lots,sl);
  }
//--- inverse management (mirror the graded manual ops on g_inv_posid; full-close
//--- finalization + g_inv_open clearing happen in OnTradeTransaction).
void CloseInverse(string reason)
  {
   if(!g_inv_open || !PositionSelectByTicket((ulong)g_inv_posid)){ return; }
   double vol=PositionGetDouble(POSITION_VOLUME), sl=PositionGetDouble(POSITION_SL);
   double px=(g_inv_rows[g_inv_open_idx].direction>0? SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   LogInvAction(reason,px,vol,0.0,sl);
   if(g_trade.PositionClose((ulong)g_inv_posid))
      Print("INVERSE #",g_inv_rows[g_inv_open_idx].id," ",reason," ",DoubleToString(vol,2)," lots @ ",DoubleToString(px,_Digits));
  }
void Close50Inverse()
  {
   if(!g_inv_open || !PositionSelectByTicket((ulong)g_inv_posid)) return;
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);  if(vmin<=0)vmin=0.01;
   double vol=PositionGetDouble(POSITION_VOLUME), sl=PositionGetDouble(POSITION_SL);
   double pv=MathFloor((vol*0.5)/step)*step;
   if(pv<vmin || (vol-pv)<vmin){ Print("INVERSE 50%: too small to split."); return; }
   double px=(g_inv_rows[g_inv_open_idx].direction>0? SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   LogInvAction("CLOSE50",px,vol,vol-pv,sl);
   if(g_trade.PositionClosePartial((ulong)g_inv_posid,pv))
      Print("INVERSE #",g_inv_rows[g_inv_open_idx].id," CLOSE50 banked ",DoubleToString(pv,2)," lots");
  }
void BEInverse()
  {
   if(!g_inv_open || !PositionSelectByTicket((ulong)g_inv_posid)) return;
   double be=NormPrice(g_inv_rows[g_inv_open_idx].entry);   // BE = entry (no pad; simple)
   double vol=PositionGetDouble(POSITION_VOLUME);
   if(g_trade.PositionModify((ulong)g_inv_posid,be,0.0))
     { LogInvAction("SL_BE",be,vol,vol,be); Print("INVERSE SL->BE @ ",DoubleToString(be,_Digits)); }
  }
void ExtendInverse()
  {
   if(!g_inv_open) return;
   g_inv_extended=true;                                    // cancels the 12-bar auto-close
   LogInvAction("EXTEND",g_inv_rows[g_inv_open_idx].entry,0,0,0);
   Print("INVERSE #",g_inv_rows[g_inv_open_idx].id," EXTEND: 12-bar auto-close cancelled.");
  }
string InvPanelStateText()
  {
   if(!g_inv_open || !PositionSelectByTicket((ulong)g_inv_posid)) return "inverse closed";
   int k=g_inv_open_idx;
   double vol=PositionGetDouble(POSITION_VOLUME), psl=PositionGetDouble(POSITION_SL);
   int bars=(int)((iTime(_Symbol,g_tf,0)-g_inv_entry_bar)/PeriodSeconds(g_tf));
   string s=StringFormat("INVERSE  %s   #%d\r\n",DirStr(g_inv_rows[k].direction),g_inv_rows[k].id);
   s+=StringFormat("Lots: %s  Entry: %s\r\n",DoubleToString(vol,2),DoubleToString(g_inv_rows[k].entry,_Digits));
   s+=StringFormat("EMA-stop: %s\r\n",DoubleToString(psl,_Digits));
   s+=StringFormat("Open R: %+.2f    Banked R: %+.2f\r\n",InvOpenR(),g_inv_rows[k].r_multiple);
   s+=(g_inv_extended? "Auto-close: CANCELLED (extended)\r\n"
                     : StringFormat("Auto-close in %d H4 bars\r\n",12-bars));
   return s;
  }

//--- append one manual-intervention row + rewrite the actions CSV.
void LogManualAction(int idx,string what,double price,double lots_before,
                     double lots_after,double sl_after,double open_r_override=-999.0)
  {
   int n=ArraySize(g_actions); ArrayResize(g_actions,n+1);
   g_actions[n].id=g_rows[idx].id; g_actions[n].posid=g_rows[idx].posid;
   g_actions[n].bar_time=iTime(_Symbol,g_tf,0); g_actions[n].action=what;
   g_actions[n].price=price; g_actions[n].lots_before=lots_before;
   g_actions[n].lots_after=lots_after; g_actions[n].sl_after=sl_after;
   //--- open_r_override lets a caller record the R AT the moment of decision (the
   //--- mechanical +1R bank logs it BEFORE the partial close so the value is the
   //--- full-position ~+1.0R, matching the manual SL_BE basis; default recomputes).
   g_actions[n].banked_r=g_rows[idx].r_multiple;
   g_actions[n].open_r=(open_r_override>-998.0 ? open_r_override : OpenR(idx));
   WriteActions(g_actions_part);
  }

//--- manual FULL close (news-eve flatten / kill-condition). R accounting is
//--- handled by OnTradeTransaction's DEAL_ENTRY_OUT path (grades it correctly).
void ManualClose(int idx)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return;
   double vol=PositionGetDouble(POSITION_VOLUME);
   double sl =PositionGetDouble(POSITION_SL);
   double px =(g_rows[idx].direction>0? SymbolInfoDouble(_Symbol,SYMBOL_BID)
                                      : SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   LogManualAction(idx,"CLOSE",px,vol,0.0,sl);
   if(g_trade.PositionClose((ulong)g_rows[idx].posid))
      Print("Signal #",g_rows[idx].id," MANUAL CLOSE ",DoubleToString(vol,2)," lots @ ",DoubleToString(px,_Digits));
   else
      Print("Signal #",g_rows[idx].id," manual close FAILED: ",g_trade.ResultRetcode());
  }

//--- manual CLOSE 50% of CURRENT volume (scale-out fallback). Disables the auto
//--- TP1 scale-out: ManageOpenPositions sizes its partial off the INITIAL lots,
//--- so leaving it armed would close the whole runner. SL is left alone - that's
//--- the separate BE button's job (one-edit doctrine: BE is the only stop move).
void ManualClose50(int idx)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return;
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);  if(vmin<=0)vmin=0.01;
   double vol=PositionGetDouble(POSITION_VOLUME);
   double sl =PositionGetDouble(POSITION_SL);
   double pv=MathFloor((vol*0.5)/step)*step;
   if(pv<vmin || (vol-pv)<vmin)
     { Print("Signal #",g_rows[idx].id," manual 50%: volume too small to split - ignored."); return; }
   double px=(g_rows[idx].direction>0? SymbolInfoDouble(_Symbol,SYMBOL_BID)
                                     : SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   LogManualAction(idx,"CLOSE50",px,vol,vol-pv,sl);
   //--- mark BOTH: banked suppresses the v2 mechanical +1R rule (its gate), tp1_done
   //--- keeps the legacy readers (terminal classifier, panel) consistent. A manual
   //--- 50% is the discretionary substitute for the auto bank -> no double-close.
   g_rows[idx].banked=true; g_rows[idx].tp1_done=true;
   if(g_trade.PositionClosePartial((ulong)g_rows[idx].posid,pv))
      Print("Signal #",g_rows[idx].id," MANUAL CLOSE 50% -> banked ",DoubleToString(pv,2),
            " lots (auto scale-out disabled)");
   else
      Print("Signal #",g_rows[idx].id," manual 50% FAILED: ",g_trade.ResultRetcode());
  }

//--- manual SL->break-even (+ pad). NEVER writes g_rows[].sl - that is the risk
//--- basis for every R number; only the position's live stop moves. TP is read
//--- back + passed unchanged (no mid-trade TP moves). banked/tp1_done are left
//--- alone: a manual BE does not consume the v2 mechanical +1R bank (which still
//--- takes its 50% + BE at +1R if the trade gets there).
void ManualBE(int idx)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return;
   double be=NormPrice(BEPrice(idx));
   double tp=PositionGetDouble(POSITION_TP);
   double vol=PositionGetDouble(POSITION_VOLUME);
   if(g_trade.PositionModify((ulong)g_rows[idx].posid,be,tp))
     {
      LogManualAction(idx,"SL_BE",be,vol,vol,be);
      Print("Signal #",g_rows[idx].id," MANUAL SL->BE @ ",DoubleToString(be,_Digits));
     }
   else
      Print("Signal #",g_rows[idx].id," SL->BE FAILED: ",g_trade.ResultRetcode()," (too close to market?)");
  }
//--- MANUAL SL->TP1 RATCHET (coach pair-12 item 2, 2026-09-10). Discretionary lock of the runner
//--- stop at TP1 (the +2R structural level). RUNNER PHASE ONLY (banked), ONE per trade, TP1 MAX
//--- (the stop goes to tp1 exactly, never past). The held-BE counterfactual (what the runner would
//--- have done with the stop left at BE) is computed post-hoc by ratchet_counterfactual.py from the
//--- journalled RATCHET_TP1 action, exactly as be_counterfactual.py does for SL_BE.
bool RatchetPlaceable(int idx)
  {
   if(g_rows[idx].ratcheted) return false;            // one per trade
   if(!g_rows[idx].banked)    return false;            // runner phase only (+1R bank fired)
   double tp1=g_rows[idx].tp1;
   if(tp1<=0.0) return false;                          // no TP1 level (single-target detector)
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return false;
   //--- MUST TIGHTEN, never loosen. Guard against any state where the current stop already sits
   //--- at/beyond tp1 (a very tight tp1 vs a padded BE, or a future stop-management step): moving
   //--- the stop to tp1 there would ratchet it the WRONG way. Refuse unless tp1 improves the stop.
   double cur_sl=PositionGetDouble(POSITION_SL);
   if(cur_sl>0.0 && (g_rows[idx].direction>0 ? tp1<=cur_sl : tp1>=cur_sl)) return false;
   double stops=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID), ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   //--- TP1 must sit a valid stop-distance below (long) / above (short) the market: i.e. price has
   //--- run past TP1 so locking the stop there is a real ratchet, not an invalid above-market stop.
   if(g_rows[idx].direction>0) return (bid-tp1)>=stops;
   return (tp1-ask)>=stops;
  }
void ManualRatchetTP1(int idx)
  {
   if(!RatchetPlaceable(idx)) { Print("Signal #",g_rows[idx].id," SL->TP1 not available (runner phase, once, price past TP1)."); return; }
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return;
   double tp1=NormPrice(g_rows[idx].tp1);
   double tp =PositionGetDouble(POSITION_TP);
   double vol=PositionGetDouble(POSITION_VOLUME);
   double px =(g_rows[idx].direction>0? SymbolInfoDouble(_Symbol,SYMBOL_BID)
                                      : SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   if(g_trade.PositionModify((ulong)g_rows[idx].posid,tp1,tp))
     {
      g_rows[idx].ratcheted=true;
      LogManualAction(idx,"RATCHET_TP1",px,vol,vol,tp1,OpenR(idx));   // held-BE counterfactual is post-hoc
      Print("Signal #",g_rows[idx].id," MANUAL SL->TP1 ratchet @ ",DoubleToString(tp1,_Digits));
     }
   else
      Print("Signal #",g_rows[idx].id," SL->TP1 FAILED: ",g_trade.ResultRetcode());
  }

//--- the panel's live state block (multiline; \r\n for the Win32 EDIT).
string PanelStateText(int idx,bool be_ok)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return "position closed";
   double vol=PositionGetDouble(POSITION_VOLUME);
   double psl=PositionGetDouble(POSITION_SL);
   double ptp=PositionGetDouble(POSITION_TP);
   double oR =OpenR(idx);
   double bR =g_rows[idx].r_multiple;
   string s="";
   //--- live day-of-week + time (tester "now"): the trader wanted the current weekday on the
   //--- management panel while a position is open, mirroring the signal popup's day line.
   MqlDateTime nowdt; TimeToStruct(TimeCurrent(),nowdt);
   string dows[]={"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
   s+=StringFormat("%s  %s UTC\r\n",dows[nowdt.day_of_week],TimeToString(TimeCurrent(),TIME_MINUTES));
   s+=StringFormat("%s  %s   #%d\r\n",g_rows[idx].strategy,DirStr(g_rows[idx].direction),g_rows[idx].id);
   s+=StringFormat("Lots: %s  (init %s)\r\n",DoubleToString(vol,2),DoubleToString(g_rows[idx].lots,2));
   s+=StringFormat("Entry: %s\r\n",DoubleToString(g_rows[idx].entry,_Digits));
   s+=StringFormat("SL: %s    TP: %s\r\n",DoubleToString(psl,_Digits),DoubleToString(ptp,_Digits));
   s+=StringFormat("Open R: %+.2f    Banked R: %+.2f\r\n",oR,bR);
   s+=StringFormat("Close-now total: %+.2f R\r\n",oR+bR);
   s+=(be_ok ? "Ready."
             : (oR<BE_FLOOR_R
                ? StringFormat("SL->BE locked until Open R >= +%.2f (now %+.2f)",BE_FLOOR_R,oR)
                : "SL->BE unavailable (too close to market)"));
   return s;
  }

//+------------------------------------------------------------------+
//| Per-tick panel driver: open when a position appears, drain+execute |
//| a confirmed button action, refresh state, tear down when flat.     |
//+------------------------------------------------------------------+
void ManagePanelTick()
  {
   int idx=ActiveRowIdx();
   //--- graded WINS: the inverse gets the panel only when NO graded position is open,
   //--- so this feature never changes graded-trade management (integrity §3).
   bool manage_inv=(idx<0 && g_inv_open);
   if(idx<0 && !manage_inv)
     {
      if(g_panel_open){ TDM_Close(); g_panel_open=false; }
      return;
     }
   static bool warned=false;
   if(!g_panel_open)
     {
      if(TDM_Open(manage_inv? "Manage INVERSE" : "Manage position")==1) { g_panel_open=true; warned=false; }
      else
        {
         if(!warned){ Print("Management panel not up yet (retrying) - if this persists the DLL window failed to create."); warned=true; }
         return;
        }
     }
   TDM_ShowExtend(manage_inv?1:0);

   //--- drain a CONFIRMED button press and execute it on THIS (MQL) thread
   int act=TDM_Poll();
   if(manage_inv)
     {
      if(act==1)      CloseInverse("MANUAL");
      else if(act==2) Close50Inverse();
      else if(act==3) BEInverse();
      else if(act==4) ExtendInverse();
      if(!g_inv_open){ TDM_Close(); g_panel_open=false; return; }   // inverse fully closed this tick
     }
   else
     {
      if(act==1)      ManualClose(idx);
      else if(act==2) ManualClose50(idx);
      else if(act==3) ManualBE(idx);
      else if(act==5) ManualRatchetTP1(idx);   // item 2: discretionary SL->TP1 ratchet
      idx=ActiveRowIdx();
      if(idx<0){ TDM_Close(); g_panel_open=false; return; }
     }

   //--- throttled state refresh (once per tester second, or right after an action)
   static datetime last_push=0;
   datetime now=TimeCurrent();
   if(now!=last_push || act!=0)
     {
      if(manage_inv)
        {
         double vol=(PositionSelectByTicket((ulong)g_inv_posid)? PositionGetDouble(POSITION_VOLUME):0.0);
         TDM_Update(InvPanelStateText(),vol,1,0);   // BE always offered for the inverse; no ratchet
        }
      else
        {
         bool be_ok=BEPlaceable(idx);
         bool rt_ok=RatchetPlaceable(idx);
         double vol=(PositionSelectByTicket((ulong)g_rows[idx].posid)? PositionGetDouble(POSITION_VOLUME):0.0);
         TDM_Update(PanelStateText(idx,be_ok),vol,be_ok?1:0,rt_ok?1:0);
        }
      last_push=now;
     }
  }

//+------------------------------------------------------------------+
//| Snap a price to the symbol tick grid + round to digits.           |
//+------------------------------------------------------------------+
double NormPrice(double p)
  {
   double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(ts<=0.0) ts=_Point;
   if(ts>0.0) p=MathRound(p/ts)*ts;
   return NormalizeDouble(p,_Digits);
  }

//+------------------------------------------------------------------+
//| Geometry validity for a dir-signed setup: SL<entry<TP for BUY     |
//| (inverted for SELL), each leg at least a tick (and broker stops   |
//| level) apart. Entry~current market, so stops-level is a proxy.    |
//+------------------------------------------------------------------+
bool ValidGeom(int dir,double e,double s,double t)
  {
   double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(ts<=0.0) ts=_Point;
   double minstop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   double gate=MathMax(ts,minstop);
   if(e<=0.0||s<=0.0||t<=0.0) return false;
   if(dir>0) { if(!(s<e && e<t)) return false; if(e-s<gate || t-e<gate) return false; }
   else      { if(!(t<e && e<s)) return false; if(s-e<gate || e-t<gate) return false; }
   return true;
  }

//+------------------------------------------------------------------+
//| Reward:risk from a dir-signed setup (0 if risk is degenerate).     |
//| R:R is a live OUTPUT of the levels, not a locked invariant - SL and |
//| TP are structural, so editing one never moves the other.           |
//+------------------------------------------------------------------+
double RRatio(double e,double s,double t)
  {
   double risk=MathAbs(e-s);
   return (risk>0.0 ? MathAbs(t-e)/risk : 0.0);
  }

//--- the strategy's minimum acceptable R:R (for the "below floor" warning
//--- when anchored-entry editing drives R:R under what the detector requires)
double StratMinRR(string strat)
  {
   if(strat=="SweepMSS") return InpSmcMinRR;
   if(strat=="DeepFib")  return InpFibMinRR;
   if(strat=="EMArev")   return InpEmaMinRR;
   if(strat=="EMArevQ")  return InpEmaMinRR;   // same mean-reversion R:R floor as base EMArev
   if(strat=="TrendCont") return 1.0;          // ratified default made explicit (§10 small item)
   return 1.0;
  }

//--- "Order" row label from the (edited) entry vs current market: within a tick/
//--- stops-level of market => MARKET; otherwise the auto-selected pending type.
string OrderNote(int dir,double entry)
  {
   double mkt=(dir>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID));
   double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(ts<=0.0) ts=_Point;
   double gate=MathMax(ts,(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point);
   if(MathAbs(entry-mkt)<=gate) return "MARKET";
   ENUM_ORDER_TYPE ot;
   if(dir>0) ot=(entry<mkt? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_BUY_STOP);
   else      ot=(entry>mkt? ORDER_TYPE_SELL_LIMIT: ORDER_TYPE_SELL_STOP);
   return OrderTypeName(ot)+" @ "+DoubleToString(entry,_Digits);
  }

//+------------------------------------------------------------------+
//| Decision-time chart screenshot -> MQL5\Files\journal\shots\.      |
//| Captures what the operator sees the instant the dialog opens      |
//| (chart + overlays; the Win32 dialog is a separate window and is   |
//| NOT in the shot). ChartScreenShot can only write under MQL5\Files |
//| (not Common\Files), so shots live beside the terminal, separate   |
//| from the Common\Files journal CSVs - keyed by the same stem+id.   |
//+------------------------------------------------------------------+
void DecisionScreenshot(int id)
  {
   if(!InpShotOnDecision) return;
   FolderCreate("journal\\shots");   // MQL5\Files\journal\shots (idempotent)
   string f=StringFormat("journal\\shots\\%s_%s_%d.png",_Symbol,StampCompact(g_start_time),id);
   if(!ChartScreenShot(0,f,InpShotW,InpShotH,ALIGN_RIGHT))
      Print("Signal #",id," screenshot failed err=",GetLastError());
  }

//+------------------------------------------------------------------+
//| Real-time advisor sidecar: the setup's blind numbers for signal  |
//| `id`, written to a FILE_COMMON CSV the INSTANT the signal fires   |
//| (before the approval dialog). The OS-capture daemon reads this to |
//| deliver the blind setup to the advisor WHILE the popup is up — the |
//| main journal row isn't written until the operator decides, so     |
//| without this the bundle can't complete until after the click.     |
//| orig_* are the detector's PROPOSED levels (pre-edit) — exactly    |
//| what the blind advisor should judge. Gated by InpShotOnDecision    |
//| (the "this is an advisor-capture run" switch). common_flag on     |
//| FolderCreate/FileOpen -> Common\Files so it's visible live (the    |
//| tester sandbox is not).                                           |
//+------------------------------------------------------------------+
//--- Dump the symbol's recent DAILY bars (up to the signal instant) so the blind
//--- advisor gets a correct daily-context render for ANY symbol - not just the
//--- one EURUSD M1 CSV that still lives on disk. Truncation is automatic:
//--- CopyRates(...,0,N) in the tester returns bars only up to the current modelled
//--- time, so the last (partial) bar ends AT the signal - no post-decision leak.
//--- Rows are written OLDEST->NEWEST (ArraySetAsSeries false) because the Python
//--- Drop a re-capture request the OS-capture daemon watches for: the operator
//--- clicked "Retake H4 screenshot" on the dialog (TD_TakeShotRequested), so we
//--- write journal\recapture.req (FILE_COMMON) with the current symbol+id. The
//--- daemon re-grabs the tester window and overwrites the advisor's h4.png.
void WriteRecaptureReq(int id)
  {
   int h=FileOpen("journal\\recapture.req",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("Signal #",id," recapture.req write failed err=",GetLastError()); return; }
   FileWriteString(h,StringFormat("%s %d\r\n",_Symbol,id));
   FileClose(h);
   Print("Signal #",id," manual H4 re-capture requested");
  }

//--- aggregator assumes chronological-ascending input; newest-first would silently
//--- render a time-mirrored chart. Called BEFORE WritePendingSetup so that
//--- "pending sidecar exists" always implies "D1 sidecar exists" (the daemon
//--- completes a bundle the moment the pending row appears).
void WriteD1Series(int id)
  {
   if(!InpShotOnDecision) return;
   MqlRates r[];
   ArraySetAsSeries(r,false);                       // index 0 = OLDEST -> ascending
   //--- 500 D1 bars (was 200): render_d1 shows the last ~130 but computes EMA20/50/200 over the
   //--- FULL dump, so EMA200 has ~370 bars of warmup before the visible window (seed residue
   //--- ~2.5%) and reads the same as a live, fully-warmed D1 chart. CopyRates returns fewer near
   //--- a symbol's data start; render_d1 degrades gracefully (EMA just less warm). (item 8)
   int n=CopyRates(_Symbol,PERIOD_D1,0,500,r);
   if(n<=0){ Print("Signal #",id," D1 series copy failed err=",GetLastError()); return; }
   FolderCreate("journal\\d1",FILE_COMMON);
   string f=StringFormat("journal\\d1\\%s_%s_%d.csv",_Symbol,StampCompact(g_start_time),id);
   int h=FileOpen(f,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE)
     { Print("Signal #",id," D1 sidecar open failed err=",GetLastError()); return; }
   FileWriteString(h,"Date,Time,Open,High,Low,Close,Volume\r\n");
   MqlDateTime dt;
   for(int i=0;i<n;i++)
     {
      TimeToStruct(r[i].time,dt);
      FileWriteString(h,StringFormat("%04d%02d%02d,%02d%02d%02d,%s,%s,%s,%s,%d\r\n",
         dt.year,dt.mon,dt.day,dt.hour,dt.min,dt.sec,
         DoubleToString(r[i].open,_Digits),DoubleToString(r[i].high,_Digits),
         DoubleToString(r[i].low,_Digits),DoubleToString(r[i].close,_Digits),
         (int)r[i].tick_volume));
     }
   FileClose(h);
  }

//--- (a) DELAY audit log: every delay the trader takes on a signal is appended here
//--- (signal_id, seq, bar, levels-as-shown), so a journal is never silently missing a
//--- deferral. Sidecar keyed by run stamp; delay-specific schema (NOT the graded 28-col
//--- journal). One row per delay; unlimited delays per signal (trader ruling 2026-09).
void WriteDelayLog(int id,int seq,SignalCandidate &cand,string entry_mode="delay",int implicit=0)
  {
   string f;
   if(InpLiveMode) f=LivePath(StringFormat("journal\\%s_%s.delays.csv",_Symbol,StampMonth(iTime(_Symbol,g_tf,0))));   // §11-5/E3: live = monthly, never mixed with tester files
   else { FolderCreate("journal",FILE_COMMON); f=StringFormat("journal\\%s_%s.delays.csv",_Symbol,StampCompact(g_start_time)); }
   bool exists=FileIsExist(f,FILE_COMMON);
   int h=FileOpen(f,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("Signal #",id," delay-log open failed err=",GetLastError()); return; }
   FileSeek(h,0,SEEK_END);
   //--- entry_mode (item 3): "delay" per re-present; "pending_frozen" / "market_now" at the final
   //--- entry after a delay; "market_refused" when a market-now click failed the MinRR floor.
   //--- live journals append an `implicit` column (G2: 1 = the EA delayed on the trader's behalf at
   //--- bar close, 0 = an explicit delay task). Tester header/rows are byte-identical (no column).
   if(!exists) FileWriteString(h,"signal_id,delay_seq,bar_time,symbol,strategy,direction,entry,sl,tp,tp1,tp2,entry_mode"+(InpLiveMode?",implicit":"")+"\r\n");
   FileWriteString(h,StringFormat("%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s",
      id,seq,TimeToString(iTime(_Symbol,g_tf,0),TIME_DATE|TIME_MINUTES),_Symbol,cand.strategy,
      (cand.direction>0?"BUY":"SELL"),
      DoubleToString(cand.entry,_Digits),DoubleToString(cand.sl,_Digits),DoubleToString(cand.tp,_Digits),
      DoubleToString(cand.tp1,_Digits),DoubleToString(cand.tp2,_Digits),entry_mode)
      +(InpLiveMode?StringFormat(",%d",implicit):"")+"\r\n");
   FileClose(h);
  }

void WritePendingSetup(int id,SignalCandidate &cand,
                       double oe,double osl,double ot,double ot1,double ot2)
  {
   if(!InpShotOnDecision) return;
   FolderCreate("journal\\pending",FILE_COMMON);
   string f=StringFormat("journal\\pending\\%s_%s_%d.csv",_Symbol,StampCompact(g_start_time),id);
   int h=FileOpen(f,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE)
     { Print("Signal #",id," pending sidecar open failed err=",GetLastError()); return; }
   FileWriteString(h,"signal_id,signal_time,symbol,strategy,direction,"
                     "orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,regime,with_trend,decision_class\r\n");
   //--- signal_time = the DECISION bar (current), NOT the trigger. The blind advisor
   //--- bundle (setup.md time-of-day + hours_until countdown) is derived from this,
   //--- and it must match the popup's "Time" field and the chart it screenshots -
   //--- all anchored to iTime(_Symbol,g_tf,0). On a delayed re-present this is re-
   //--- written with the advanced bar, keeping the advisor in step with the trader.
   //--- (The GRADED journal keeps cand.zone_to as the signal time - grading refs the
   //--- trigger; this sidecar is real-time advisor delivery only, never graded.)
   FileWriteString(h,StringFormat("%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\r\n",
      id,TimeToString(iTime(_Symbol,g_tf,0),TIME_DATE|TIME_MINUTES),_Symbol,cand.strategy,
      (cand.direction>0?"BUY":"SELL"),
      DoubleToString(oe,_Digits),DoubleToString(osl,_Digits),DoubleToString(ot,_Digits),
      DoubleToString(ot1,_Digits),DoubleToString(ot2,_Digits),g_sig_regime,g_sig_with_trend,g_sig_class));
   FileClose(h);
  }

//--- R:R readout. Single-target => "1 : R". Scale-out => per-target R1/R2 plus
//--- the split-weighted blend (display only, "if both fill"). Returns `blended`
//--- (info / journal) and `runnerR` = the RUNNER's R, which is what the floor
//--- GATE uses: the detectors floor MIN_RR on the target they place (tp2 for
//--- EMA, tp1==tp for Fib), so gating on the runner never false-blocks an
//--- unedited signal, while an edit that pulls the runner in still blocks.
string RrText(bool scaleout,double e,double s,double tp1,double tp2,double frac,
              double &blended,double &runnerR)
  {
   double risk=MathAbs(e-s);
   if(!scaleout)
     { double R=(risk>0.0?MathAbs(tp1-e)/risk:0.0); blended=R; runnerR=R;
       return StringFormat("1 : %s",DoubleToString(R,2)); }
   double R1=(risk>0.0?MathAbs(tp1-e)/risk:0.0);
   double R2=(risk>0.0?MathAbs(tp2-e)/risk:0.0);
   blended=frac*R1+(1.0-frac)*R2; runnerR=R2;
   return StringFormat("T1 %s / T2 %s / blend %s",
                       DoubleToString(R1,2),DoubleToString(R2,2),DoubleToString(blended,2));
  }

//--- lots display for the dialog, WITH the stop distance in ATR multiples so a
//--- human glance catches a degenerate stop ("SL 0.06 ATR" is an instant flag).
string LotsLine(double lots,double e,double s,double atr)
  {
   double x=(atr>0.0? MathAbs(e-s)/atr : 0.0);
   return StringFormat("%s  (%.1f%% risk, SL %.2f ATR)",DoubleToString(lots,2),InpRiskPct*100.0,x);
  }

//--- scale-out geometry: TP1 and TP2 each valid vs entry/SL, and TP2 strictly
//--- beyond TP1 in the trade direction (runner further than the bank target).
bool ValidScale(int dir,double e,double s,double tp1,double tp2,double ts)
  {
   if(!ValidGeom(dir,e,s,tp1) || !ValidGeom(dir,e,s,tp2)) return false;
   if(dir>0) { if(tp2<=tp1+0.5*ts) return false; }
   else      { if(tp2>=tp1-0.5*ts) return false; }
   return true;
  }

//+------------------------------------------------------------------+
//| Interactive EDITABLE approval (poll-driven TradeDialog.dll).      |
//| Entry/SL/TP(s) are INDEPENDENT editable fields - editing one       |
//| never moves another (SL & TP are structural). Scale-out strategies |
//| (partial_fraction>0) split TP into TP1(bank)+TP2(runner), each      |
//| editable; the plan is PRESERVED under edits. Each edit re-sizes     |
//| lots, recomputes the live (blended) R:R, moves the chart lines, and |
//| BLOCKS Accept below the strategy floor. Coach mode also scrubs the  |
//| chart corner label's timestamp. Entry away from market => pending.  |
//+------------------------------------------------------------------+
//--- display string for the FROZEN regime tag (popup + setup.md use the same text).
string RegimePretty()
  {
   if(g_sig_regime=="")     return "--";                            // blank / warming up
   if(g_sig_regime=="CHOP") return "CHOP";
   return g_sig_regime+"  -  "+(g_sig_with_trend=="1"?"WITH-TREND":"AGAINST-TREND");
  }

bool InteractiveDialog(int id,SignalCandidate &cand,string caption,string plan,
                       int &skip_reason,bool &entry_edited,bool offer_inv,bool &want_inv,bool &want_delay,
                       bool &want_market)
  {
   skip_reason=0; entry_edited=false; want_inv=false; want_delay=false; want_market=false;
   int    dir=cand.direction;
   bool   scaleout=(cand.partial_fraction>0.0 && cand.tp1>0.0 && cand.tp2>0.0);
   double frac=cand.partial_fraction;
   double floor=StratMinRR(cand.strategy);   // strategy's minimum R:R (Accept blocked below it)
   double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(ts<=0.0) ts=_Point;
   double tol=0.5*ts;
   double atr=SignalATR();   // for the "SL x.x ATR" readout in the lots line
   double minstop=MinStopDist(atr);   // also block Accept if an EDIT makes the stop too tight

   //--- committed levels. For scale-out, the "TP" field is TP1 and c2 is TP2.
   double ce=NormPrice(cand.entry), cs=NormPrice(cand.sl);
   double c1=NormPrice(scaleout? cand.tp1 : cand.tp);
   double c2=scaleout? NormPrice(cand.tp2) : 0.0;
   double e0=ce;                                 // for the entry_edited (pending) test
   string tp2str=(scaleout? DoubleToString(c2,_Digits) : "");

   double blended=0.0, runnerR=0.0;
   string rrs=RrText(scaleout,ce,cs,c1,c2,frac,blended,runnerR);
   string stratArg=cand.strategy+(cand.d1_context?"  [D1 aligned]":"");
   if(cand.comment!="") stratArg+=" - "+cand.comment;
   string p=StringFormat("%s%d_",InpObjPrefix,id);

   double lots0=SizeByRisk(ce,cs);
   int ok0=(( scaleout? ValidScale(dir,ce,cs,c1,c2,ts) : ValidGeom(dir,ce,cs,c1) )
            && runnerR>=floor && MathAbs(ce-cs)>=minstop)?1:0;
   //--- DECISION-bar time: the bar the operator is deciding ON (current bar), so it
   //--- REFRESHES on every delayed re-present - the trader watches the clock advance
   //--- as bars play out. Day-of-week + HH:MM only (no date -> coach-safe), UTC (.dk
   //--- symbols are Dukascopy UTC data). Once the signal has actually been delayed
   //--- (>1 bar past the trigger; the 1-bar presentation lag is normal), append the
   //--- original signal fire time so the setup's age stays visible. The DLL's Friday
   //--- weekend-risk flag keys off this string -> now reflects the real decision bar.
   string dows[]={"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
   datetime dnow=iTime(_Symbol,g_tf,0);
   MqlDateTime ndt; TimeToStruct(dnow,ndt);
   string sigtime=StringFormat("%s  %s UTC",dows[ndt.day_of_week],TimeToString(dnow,TIME_MINUTES));
   //--- compact delay counter (dbars-1 backs out the normal 1-bar presentation lag),
   //--- kept short so it fits the fixed-width value field without clipping the time.
   int delays=(int)((dnow-cand.zone_to)/PeriodSeconds(g_tf))-1;
   if(delays>0) sigtime+=StringFormat("  (+%d bar%s)",delays,(delays==1?"":"s"));
   if(TD_Open(caption,_Symbol,stratArg,DirStr(dir),sigtime,RegimePretty(),g_sig_class,
              DoubleToString(ce,_Digits),DoubleToString(cs,_Digits),DoubleToString(c1,_Digits),tp2str,
              LotsLine(lots0,ce,cs,atr),rrs)!=1)
     {
      Print("Signal #",id," dialog failed to open - fail-closed to SKIP.");
      return false;
     }
   TD_OfferInverse(offer_inv?1:0);   // 3rd choice, only for EMArev past the fwd-V gate
   //--- item 3: at a delay REOPEN (g_delay_count>0), offer "enter NOW at market" beside Accept
   //--- (=pending @ frozen). Not for breakout STOP setups (entry is a level, not the market).
   TD_OfferMarketNow((g_delay_count>0 && !cand.stop_entry)?1:0);
   //--- decision-time chart snapshot (overlays already drawn; dialog not in shot)
   DecisionScreenshot(id);
   //--- popup upcoming-events list (both date forms for the coach-mode toggle).
   //--- Anchor to the CURRENT decision bar, NOT cand.zone_to: on a delayed re-present
   //--- the trigger is in the past, so the forward window + "in Xd Yh" countdowns
   //--- (which drive the DLL's red<6h / amber 6-12h bands) must roll forward with the
   //--- market - staying in lockstep with the fresh chart overlay the advisor sees.
   string ev_abs="",ev_rel="";
   if(g_ev_loaded) BuildEventBlocks(iTime(_Symbol,g_tf,0),ev_abs,ev_rel);
   else            { ev_abs="(events not loaded)"; ev_rel=ev_abs; }
   TD_SetEvents(ev_abs,ev_rel);
   TD_SetDisplay(DoubleToString(ce,_Digits),DoubleToString(cs,_Digits),DoubleToString(c1,_Digits),tp2str,
                 LotsLine(lots0,ce,cs,atr),rrs,ok0);

   //--- corner-label full vs coach-scrubbed (drop the " @ <timestamp>...")
   string lblFull=ObjectGetString(0,p+"label",OBJPROP_TEXT);
   string lblScrub=lblFull; int atp=StringFind(lblScrub," @ ");
   if(atp>=0) lblScrub=StringSubstr(lblScrub,0,atp);
   int coach=0;

   bool edited=false, shown_invalid=false;
   int  r=0;
   while(true)
     {
      double e=ce, s=cs, t1=c1, t2=c2; int dirty=0;
      r=TD_Poll(e,s,t1,t2,dirty);
      if(r!=0) { if(r==2) skip_reason=TD_SkipReason(); break; }
      //--- coach mode: scrub/unscrub the CHART corner label to match the dialog
      //--- (only if we actually captured a label - never blank it)
      int cnow=TD_Coach();
      if(cnow!=coach && lblFull!="")
        { coach=cnow; ObjectSetString(0,p+"label",OBJPROP_TEXT,coach?lblScrub:lblFull); ChartRedraw(0); }
      //--- operator asked to re-take the H4 screenshot: signal the OS-capture daemon
      if(TD_TakeShotRequested()) WriteRecaptureReq(id);
      //--- keep the D1-tab (and any other symbol chart) entry/SL/TP in sync with edits,
      //--- and catch a chart opened mid-decision (idempotent; redraws only on change).
      MirrorLevels(e,s,t1,(scaleout? t2 : 0.0));
      if(!dirty) { UiSpin(12); continue; }

      //--- which of the up-to-4 fields changed (tolerance kills sub-tick jitter)
      int changed=0;
      if(MathAbs(e-ce)>tol)                 changed=1;   // entry
      else if(MathAbs(s-cs)>tol)            changed=2;   // SL
      else if(MathAbs(t1-c1)>tol)           changed=3;   // TP1 / single TP
      else if(scaleout && MathAbs(t2-c2)>tol) changed=4; // TP2
      if(changed==0) { UiSpin(12); continue; }

      //--- INDEPENDENT: an edit changes ONLY its own field. Editing the SL never
      //--- mutates the TP side (values OR the TP1/TP2 scale-out structure).
      e =(changed==1? NormPrice(e ): ce);
      s =(changed==2? NormPrice(s ): cs);
      t1=(changed==3? NormPrice(t1): c1);
      t2=(changed==4? NormPrice(t2): c2);

      bool valid=(scaleout? ValidScale(dir,e,s,t1,t2,ts) : ValidGeom(dir,e,s,t1));
      if(valid)
        {
         ce=e; cs=s; c1=t1; c2=t2; edited=true; shown_invalid=false;
         entry_edited=(MathAbs(ce-e0)>tol);
         rrs=RrText(scaleout,ce,cs,c1,c2,frac,blended,runnerR);
         bool okr=(runnerR>=floor && MathAbs(ce-cs)>=minstop);   // also gate on min stop distance
         double lots=SizeByRisk(ce,cs);
         ObjectSetDouble(0,p+"entry",OBJPROP_PRICE,ce);
         ObjectSetDouble(0,p+"sl",   OBJPROP_PRICE,cs);
         ObjectSetDouble(0,p+"tp",   OBJPROP_PRICE,c1);
         if(scaleout) ObjectSetDouble(0,p+"tp2",OBJPROP_PRICE,c2);
         ChartRedraw(0);
         TD_SetOrderType(OrderNote(dir,ce));
         string rrd=rrs+(okr?"":StringFormat("  < MIN %s",DoubleToString(floor,1)));
         TD_SetDisplay(DoubleToString(ce,_Digits),DoubleToString(cs,_Digits),DoubleToString(c1,_Digits),
                       (scaleout?DoubleToString(c2,_Digits):""),
                       LotsLine(lots,ce,cs,atr),
                       rrd, okr?1:0);
        }
      else if(!shown_invalid)
        {
         shown_invalid=true;
         TD_SetDisplay(DoubleToString(ce,_Digits),DoubleToString(cs,_Digits),DoubleToString(c1,_Digits),
                       (scaleout?DoubleToString(c2,_Digits):""),"--","(bad levels)",0);
        }
      UiSpin(12);
     }
   TD_Close();

   if(r==3){ want_inv=true;   return false; } // INVERSE chosen: not a graded fade (handled by caller)
   if(r==4){ want_delay=true; return false; } // DELAY chosen: re-ask next bar (caller defers)
   if(r==5){ want_market=true; return false; }// MARKET-NOW chosen at reopen (handled by caller)

   if(r==1)   // approved: commit the (possibly edited) levels; NEVER collapse
     {
      cand.entry=ce; cand.sl=cs;
      if(scaleout)
        {
         cand.tp1=c1; cand.tp2=c2; cand.tp=c2;    // placed TP = runner; partial_fraction preserved
         RrText(scaleout,ce,cs,c1,c2,frac,blended,runnerR); cand.rr=blended;
        }
      else
        {
         cand.tp=c1;                               // single target; tp1/tp2 (SMC aux) left as-is
         double risk=MathAbs(ce-cs); cand.rr=(risk>0.0? MathAbs(c1-ce)/risk : cand.rr);
        }
      if(edited)
         Print("Signal #",id," levels edited: E=",DoubleToString(ce,_Digits)," SL=",DoubleToString(cs,_Digits),
               (scaleout? " TP1="+DoubleToString(c1,_Digits)+" TP2="+DoubleToString(c2,_Digits)
                        : " TP="+DoubleToString(c1,_Digits)),"  (R:R ",DoubleToString(cand.rr,2),")");
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Short busy-wait (ms) for the interactive poll loop. Sleep() is a  |
//| no-op in the tester, so spin on GetTickCount to cap CPU while the |
//| dialog stays responsive between TD_Poll pumps.                    |
//+------------------------------------------------------------------+
void UiSpin(int ms)
  {
   uint t0=GetTickCount();
   while((GetTickCount()-t0)<(uint)ms) { /* burn */ }
  }

//+------------------------------------------------------------------+
//| Modal / auto-approve. Returns true on approve.                    |
//+------------------------------------------------------------------+
bool AskApproval(int id,SignalCandidate &cand,double lots,string caption,long &decision_ms,
                 int &skip_reason,bool &entry_edited,bool offer_inv,bool &want_inv,bool &want_delay,
                 bool &want_market)
  {
   skip_reason=0; entry_edited=false; want_inv=false; want_delay=false; want_market=false;
   //--- headless automated verification: no DLL, no modal (never edits entry).
   //--- INVERSE is interactive-only: headless never chooses it.
   if(InpAutoApprove==AA_ALL)
     { decision_ms=0;
       // TEST hook: exercise the inverse lifecycle headlessly on EMArev (bypasses the
       // fwd-V gate, which legitimately blocks most news-driven stretches).
       // INVERSE fully disabled (coach 2026-09-08); the InpTestInverse auto-take hook is inert:
       // if(InpTestInverse && cand.strategy=="EMArev" && !g_inv_open){ want_inv=true; return false; }
       // TEST: delay each signal InpTestDelay bars (re-presented with the same id), then approve
       static int td_last=-1, td_cnt=0;
       if(InpTestDelay>0)
         { if(id!=td_last){ td_last=id; td_cnt=0; }
           // prove the decision-relative event context rolls forward each re-present:
           // log the SAME anchor the popup uses (current bar) and the nearest countdown.
           if(g_ev_loaded)
             { string ea,er; BuildEventBlocks(iTime(_Symbol,g_tf,0),ea,er);
               string first=er; int nl=StringFind(first,"\r\n"); if(nl>=0) first=StringSubstr(first,0,nl);
               PrintFormat("DELAYCTX #%d present=%d bar=%s trig=%s  next-event: %s",
                 id,td_cnt+1,TimeToString(iTime(_Symbol,g_tf,0),TIME_DATE|TIME_MINUTES),
                 TimeToString(cand.zone_to,TIME_DATE|TIME_MINUTES),first); }
           if(td_cnt<InpTestDelay){ td_cnt++; want_delay=true; return false; } }
       return true; }
   if(InpAutoApprove==AA_SKIP) { decision_ms=0; return false; }

   uint t0=GetTickCount();
   bool yes=false;
   string plan="";
   if(cand.partial_fraction>0.0 && cand.tp1>0.0)
      plan=StringFormat("  TP1 %s / TP2 %s bank %.0f%%",
            DoubleToString(cand.tp1,_Digits),DoubleToString(cand.tp2,_Digits),cand.partial_fraction*100.0);

   if(InpUseColoredDialog)
     {
      //--- editable, R:R-locked, live-updating dialog (may mutate cand levels);
      //--- returns the skip-reason code and whether the entry was edited (pending).
      yes=InteractiveDialog(id,cand,caption,plan,skip_reason,entry_edited,offer_inv,want_inv,want_delay,want_market);
     }
   else
     {
      string body=StringFormat(
         "Symbol:      %s\nStrategy:    %s%s\nDirection:   %s\nTime:        %s\n\n"
         "Entry:       %s\nStop Loss:   %s\nTake Profit: %s\nR:R:         1 : %s%s\n\n"
         "%s\nLot size:    %s   (%.1f%% risk)\n\nPlace this trade?",
         _Symbol,cand.strategy,(cand.d1_context?" [D1 aligned]":""),DirStr(cand.direction),
         TimeToString(cand.zone_to,TIME_DATE|TIME_MINUTES),
         DoubleToString(cand.entry,_Digits),DoubleToString(cand.sl,_Digits),
         DoubleToString(cand.tp,_Digits),DoubleToString(cand.rr,2),plan,
         (cand.comment!=""?cand.comment:""),DoubleToString(lots,2),InpRiskPct*100.0);
      int res=MessageBoxW(0,body,caption,MB_YESNO|MB_ICONQUESTION|MB_SYSTEMMODAL);
      yes=(res==IDYES);
      if(!yes) skip_reason=6;   // fallback path has no reason keys -> "other"
     }
   decision_ms=(long)(GetTickCount()-t0);
   return(yes);
  }

//+------------------------------------------------------------------+
//--- hard backstop: never let one position consume more than InpMaxMarginPct of
//--- free margin, regardless of the computed risk lots. A final net under the
//--- min-stop gate so no single upstream failure can place a monster position.
double MarginCapLots(double entry,double sl,double lots)
  {
   if(lots<=0.0 || InpMaxMarginPct<=0.0) return lots;
   double price=(entry>0.0? entry : SymbolInfoDouble(_Symbol,SYMBOL_ASK));
   ENUM_ORDER_TYPE ot=(entry>sl? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double mreq=0.0;
   if(!OrderCalcMargin(ot,_Symbol,lots,price,mreq) || mreq<=0.0) return lots;
   double cap=InpMaxMarginPct*AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(cap<=0.0 || mreq<=cap) return lots;
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0.0) step=0.01;
   double capped=MathFloor((lots*cap/mreq)/step)*step;
   Print("SAFETY: lots ",DoubleToString(lots,2)," -> ",DoubleToString(capped,2),
         " (margin cap ",DoubleToString(InpMaxMarginPct*100.0,0),"% of free) - check the stop distance.");
   return capped;
  }

double SizeByRisk(double entry,double sl)
  {
   double lots=LotsForRisk(_Symbol,entry,sl,InpRiskPct*g_risk_mult);   // shared math (floored, no clamp); g_risk_mult=1.0 unless LIVE (C2: sizing only, detectors untouched)
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   if(lots<=0.0)
     {
      double tsz=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
      double tvl=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
      if(tsz<=0.0||tvl<=0.0||MathAbs(entry-sl)<=0.0)
        { Print("WARNING: cannot size (specs/sl). tick_size=",tsz," tick_val=",tvl); return(0.0); }
      //--- risk-based lots rounded below vol_min: clamp up (harness policy)
      if(vmin>0.0)
        { Print("NOTE: risk lots < vol_min - clamping up; risks > ",DoubleToString(InpRiskPct*100.0,1),"%.");
          return(MarginCapLots(entry,sl,vmin)); }
      return(0.0);
     }
   return(MarginCapLots(entry,sl,lots));
  }

//+------------------------------------------------------------------+
//| Overlays: base zone + entry/sl/tp + aux levels + zone2 + leg line  |
//+------------------------------------------------------------------+
void DrawOverlays(int id,SignalCandidate &c)
  {
   string p=StringFormat("%s%d_",InpObjPrefix,id);

   string rz=p+"zone";
   if(ObjectCreate(0,rz,OBJ_RECTANGLE,0,c.zone_from,c.zone_hi,c.zone_to,c.zone_lo))
     {
      ObjectSetInteger(0,rz,OBJPROP_COLOR,clrSlateGray);
      ObjectSetInteger(0,rz,OBJPROP_BACK,true);
      ObjectSetInteger(0,rz,OBJPROP_FILL,true);
      ObjectSetInteger(0,rz,OBJPROP_STYLE,STYLE_DOT);
     }
   bool c_scaleout=(c.partial_fraction>0.0 && c.tp1>0.0 && c.tp2>0.0);
   DrawHLine(p+"entry",c.entry,C'0,160,0');
   DrawHLine(p+"sl",   c.sl,   C'204,0,0');
   //--- TP line = TP1 (bank) for scale-out, else the single target; a dashed
   //--- runner line marks TP2 so the two editable TP fields map to the chart.
   DrawHLine(p+"tp",   (c_scaleout? c.tp1 : c.tp), C'0,0,204');
   //--- mirror entry/SL/TP onto any OTHER open chart of this symbol (D1 tab etc.)
   MirrorLevels(c.entry,c.sl,(c_scaleout? c.tp1 : c.tp),(c_scaleout? c.tp2 : 0.0));
   if(c_scaleout)
     {
      string t2n=p+"tp2";
      if(ObjectCreate(0,t2n,OBJ_HLINE,0,0,c.tp2))
        {
         ObjectSetInteger(0,t2n,OBJPROP_COLOR,C'0,0,204');
         ObjectSetInteger(0,t2n,OBJPROP_WIDTH,InpLineWidth);
         ObjectSetInteger(0,t2n,OBJPROP_STYLE,STYLE_DASH);
         ObjectSetInteger(0,t2n,OBJPROP_BACK,false);
        }
     }

   //--- aux levels (dashed, brightened for contrast) + labels
   for(int k=0;InpShowAux && k<c.aux_count && k<8;k++)
     {
      string an=StringFormat("%saux%d",p,k);
      if(ObjectCreate(0,an,OBJ_HLINE,0,0,c.aux_price[k]))
        {
         ObjectSetInteger(0,an,OBJPROP_COLOR,clrSilver);
         ObjectSetInteger(0,an,OBJPROP_STYLE,STYLE_DOT);
         ObjectSetInteger(0,an,OBJPROP_BACK,true);
        }
      string al=StringFormat("%sauxL%d",p,k);
      if(ObjectCreate(0,al,OBJ_TEXT,0,c.zone_to,c.aux_price[k]))
        {
         ObjectSetString (0,al,OBJPROP_TEXT,c.aux_label[k]);
         ObjectSetInteger(0,al,OBJPROP_COLOR,clrSilver);
         ObjectSetInteger(0,al,OBJPROP_FONTSIZE,InpFontSize);
        }
     }
   //--- second zone (FVG overlap / band)
   if(c.zone2_hi>0.0 && c.zone2_lo>0.0)
     {
      string z2=p+"zone2";
      if(ObjectCreate(0,z2,OBJ_RECTANGLE,0,c.zone_from,c.zone2_hi,c.zone_to,c.zone2_lo))
        {
         ObjectSetInteger(0,z2,OBJPROP_COLOR,clrDarkSlateGray);
         ObjectSetInteger(0,z2,OBJPROP_BACK,true);
         ObjectSetInteger(0,z2,OBJPROP_FILL,true);
        }
     }
   //--- impulse / structure leg trendline
   if(c.leg_t0>0 && c.leg_t1>0)
     {
      string lg=p+"leg";
      if(ObjectCreate(0,lg,OBJ_TREND,0,c.leg_t0,c.leg_p0,c.leg_t1,c.leg_p1))
        {
         ObjectSetInteger(0,lg,OBJPROP_COLOR,clrGoldenrod);
         ObjectSetInteger(0,lg,OBJPROP_WIDTH,InpLineWidth);
         ObjectSetInteger(0,lg,OBJPROP_RAY_RIGHT,false);
         ObjectSetInteger(0,lg,OBJPROP_SELECTABLE,false);
        }
     }
   //--- native Fibonacci retracement grid on the Deep-Fib impulse leg. Only for
   //--- the Fib strategy (the only detector that sets a retracement leg).
   if(InpShowFib && c.strategy=="DeepFib" && c.leg_t0>0 && c.leg_t1>0)
      DrawFibo(p,c);
   //--- generic swing markers are now a PERSISTENT, signal-independent overlay
   //--- (DrawSwingMarkers, redrawn each bar) - not drawn per-signal here.
   //--- FVG / price-gap / tick-volume imbalance highlights (display-only context)
   if(InpShowImbal) DrawImbalances(p,c);
   //--- corner label
   string tx=p+"label";
   double anchor=(c.direction>0 ? c.zone_hi : c.zone_lo);
   if(ObjectCreate(0,tx,OBJ_TEXT,0,c.zone_to,anchor))
     {
      //--- F2 blindness: with InpBlindLabels the label carries NO calendar date
      //--- (time-of-day + UTC only), so a cropped screenshot handed to the blind
      //--- advisor can't leak timing. Off by default (coaching/debug want dates).
      string when=(InpBlindLabels? TimeToString(c.zone_to,TIME_MINUTES)+" UTC"
                                 : TimeToString(c.zone_to,TIME_DATE|TIME_MINUTES));
      string txt=StringFormat("#%d %s %s%s @ %s",id,c.strategy,DirStr(c.direction),
                              (c.d1_context?" [D1]":""),when);
      if(c.comment!="") txt+="  ("+c.comment+")";
      ObjectSetString (0,tx,OBJPROP_TEXT,txt);
      ObjectSetInteger(0,tx,OBJPROP_COLOR,clrWhite);
      ObjectSetInteger(0,tx,OBJPROP_FONTSIZE,InpFontSize+1);
      ObjectSetInteger(0,tx,OBJPROP_ANCHOR,(c.direction>0?ANCHOR_LEFT_LOWER:ANCHOR_LEFT_UPPER));
     }
  }

//+------------------------------------------------------------------+
//| INVERSE ("ride the stretch") preview lines, drawn alongside the   |
//| EMArev fade overlay when the INVERSE option is on the table. Same  |
//| per-signal prefix (HFT_<id>_) so PruneOverlays cleans them up, but  |
//| a DISTINCT colour (orange) + dashed + explicit "INVERSE" labels so  |
//| there is no mistaking them for the graded fade's entry/SL/TP. The   |
//| geometry MIRRORS PlaceInverse exactly (E1 entry at the signal price,|
//| stop just beyond EMA20 capped 1.0-2.5x ATR, X1 take 1x ATR beyond   |
//| the stretch extreme) so the picture matches the order that fires.   |
//+------------------------------------------------------------------+
void DrawInverseOverlays(int id,SignalCandidate &c)
  {
   string p=StringFormat("%s%d_",InpObjPrefix,id);
   int inv_dir=-c.direction;                       // trade WITH the stretch
   //--- ATR basis identical to PlaceInverse (zone height / stretch, iATR fallback)
   double atr=(InpEmaStretch>0.0? (c.zone_hi-c.zone_lo)/InpEmaStretch : 0.0);
   if(atr<=0.0){ double a[]; ArraySetAsSeries(a,true); int hA=iATR(_Symbol,g_tf,14);
                 if(hA!=INVALID_HANDLE && CopyBuffer(hA,0,1,1,a)>0) atr=a[0]; }
   if(atr<=0.0) return;                            // no scale -> can't preview
   //--- entry = MARKET now (what PlaceInverse actually fills), not the signal price,
   //--- so the preview matches the order. Re-drawn each re-present, so it stays live.
   double entry=(inv_dir>0? SymbolInfoDouble(_Symbol,SYMBOL_ASK)
                          : SymbolInfoDouble(_Symbol,SYMBOL_BID));
   if(entry<=0.0) entry=c.entry;
   double ema=c.tp1;                               // EMA20 (the fade's mean target)
   double ema_dist=MathAbs(entry-ema);
   double spread=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID); if(spread<0)spread=0;
   double buf=MathMax(0.10*atr,spread);            // mirror PlaceInverse's buffer exactly
   double risk=(ema_dist<1.0*atr ? 1.0*atr : MathMin(ema_dist+buf,2.5*atr)); // cap 2.5, floor 1.0
   double isl=(inv_dir>0? entry-risk : entry+risk);
   //--- X1 take = 1x ATR beyond the TRUE stretch extreme. For EMArev, zone_hi/zone_lo
   //--- are the EMA BAND (mean +/- 2xATR), NOT the extreme - the real extreme is the
   //--- detector's "stretch anchor" aux (m_extreme). Fall back to entry if absent.
   double xtreme=entry;
   for(int a=0;a<c.aux_count && a<8;a++)
      if(StringFind(c.aux_label[a],"stretch anchor")>=0){ xtreme=c.aux_price[a]; break; }
   double itake=xtreme+inv_dir*atr;                // 1x ATR beyond the extreme, ride direction

   color ORANGE=C'255,140,0';
   datetime lt=iTime(_Symbol,g_tf,0);              // labels on the right edge (clear of fade labels)
   double lv[3];   lv[0]=entry; lv[1]=isl;         lv[2]=itake;
   string nm[3];   nm[0]="ientry"; nm[1]="isl";    nm[2]="itake";
   string tx[3];
   tx[0]=StringFormat("INVERSE entry (ride %s)",(inv_dir>0?"UP":"DOWN"));
   tx[1]="INVERSE stop";
   tx[2]="INVERSE take ref (X1 1xATR - no TP order, 12-bar time exit)";
   for(int k=0;k<3;k++)
     {
      string ln=p+nm[k];
      if(ObjectCreate(0,ln,OBJ_HLINE,0,0,lv[k]))
        {
         ObjectSetInteger(0,ln,OBJPROP_COLOR,ORANGE);
         ObjectSetInteger(0,ln,OBJPROP_WIDTH,InpLineWidth);
         ObjectSetInteger(0,ln,OBJPROP_STYLE,STYLE_DASH);
         ObjectSetInteger(0,ln,OBJPROP_BACK,false);
         ObjectSetInteger(0,ln,OBJPROP_SELECTABLE,false);
        }
      else ObjectSetDouble(0,ln,OBJPROP_PRICE,lv[k]);   // re-present: keep it fresh
      string lb=p+nm[k]+"L";
      if(ObjectCreate(0,lb,OBJ_TEXT,0,lt,lv[k]))
        {
         ObjectSetString (0,lb,OBJPROP_TEXT,tx[k]);
         ObjectSetInteger(0,lb,OBJPROP_COLOR,ORANGE);
         ObjectSetInteger(0,lb,OBJPROP_FONTSIZE,InpFontSize);
         ObjectSetInteger(0,lb,OBJPROP_ANCHOR,ANCHOR_RIGHT);
         ObjectSetInteger(0,lb,OBJPROP_SELECTABLE,false);
        }
      else ObjectMove(0,lb,0,lt,lv[k]);                 // re-present: move label to right edge
     }
  }

//--- keep only the newest InpMaxVisibleSignals setups' overlays on the chart;
//--- delete older setups by their per-signal object prefix. Econ-event objects
//--- use a different prefix (HFT_EVT*/HFT_EVTL*) and are unaffected.
void PruneOverlays(int id)
  {
   //--- a re-presented (delayed) signal reuses its id: if it is already registered,
   //--- do NOT add it again. Re-adding lets the keep-list fill with duplicates and,
   //--- once it overflows, ObjectsDeleteAll("HFT_<id>_") would wipe THIS signal's own
   //--- entry/SL/TP (and inverse) lines - the "lines vanish after Delay" bug.
   for(int j=0;j<ArraySize(g_sig_ids);j++) if(g_sig_ids[j]==id) return;
   int n=ArraySize(g_sig_ids);
   ArrayResize(g_sig_ids,n+1); g_sig_ids[n]=id;
   int keep=(InpMaxVisibleSignals<1?1:InpMaxVisibleSignals);
   while(ArraySize(g_sig_ids)>keep)
     {
      ObjectsDeleteAll(0,StringFormat("%s%d_",InpObjPrefix,g_sig_ids[0]));
      int m=ArraySize(g_sig_ids);
      for(int i=1;i<m;i++) g_sig_ids[i-1]=g_sig_ids[i];
      ArrayResize(g_sig_ids,m-1);
     }
  }

void DrawHLine(string name,double price,color clr)
  {
   if(ObjectCreate(0,name,OBJ_HLINE,0,0,price))
     {
      ObjectSetInteger(0,name,OBJPROP_COLOR,clr);
      ObjectSetInteger(0,name,OBJPROP_WIDTH,InpLineWidth);
      ObjectSetInteger(0,name,OBJPROP_STYLE,STYLE_SOLID);
      ObjectSetInteger(0,name,OBJPROP_BACK,false);
     }
  }

//+------------------------------------------------------------------+
//| Mirror the entry/SL/TP lines onto EVERY other open chart of this  |
//| symbol (e.g. a D1 tab the operator opened) - the EA draws overlays |
//| only on chart 0, so a separate chart would otherwise be bare.      |
//| Idempotent (create-if-missing, else move) so it can be called live |
//| in the dialog loop: a D1 chart opened mid-decision still gets them,|
//| and edits track. Prefix HFT_MIRROR_ so cleanup is trivial.         |
//+------------------------------------------------------------------+
bool MirrorHLine(long cid,string name,double price,color clr,int style)
  {
   if(price<=0.0)                                   // no such level: remove if present
     { if(ObjectFind(cid,name)>=0){ ObjectDelete(cid,name); return true; } return false; }
   if(ObjectFind(cid,name)<0)                       // first time on this chart -> create
     {
      ObjectCreate(cid,name,OBJ_HLINE,0,0,price);
      ObjectSetInteger(cid,name,OBJPROP_COLOR,clr);
      ObjectSetInteger(cid,name,OBJPROP_WIDTH,InpLineWidth);
      ObjectSetInteger(cid,name,OBJPROP_STYLE,style);
      ObjectSetInteger(cid,name,OBJPROP_BACK,false);
      ObjectSetInteger(cid,name,OBJPROP_SELECTABLE,false);
      ObjectSetDouble(cid,name,OBJPROP_PRICE,price);
      return true;
     }
   if(MathAbs(ObjectGetDouble(cid,name,OBJPROP_PRICE)-price)>1e-10)   // moved (edit)
     { ObjectSetDouble(cid,name,OBJPROP_PRICE,price); return true; }
   return false;                                    // unchanged -> no redraw needed
  }

void MirrorLevels(double entry,double sl,double tp,double tp2)
  {
   long self=ChartID();
   int total=0,others=0;
   long cid=ChartFirst();
   while(cid>=0)
     {
      total++;
      if(cid!=self && ChartSymbol(cid)==_Symbol)
        {
         others++;
         bool ch=false;
         ch|=MirrorHLine(cid,"HFT_MIRROR_entry",entry,C'0,160,0',STYLE_SOLID);
         ch|=MirrorHLine(cid,"HFT_MIRROR_sl",   sl,   C'204,0,0',STYLE_SOLID);
         ch|=MirrorHLine(cid,"HFT_MIRROR_tp",   tp,   C'0,0,204',STYLE_SOLID);
         ch|=MirrorHLine(cid,"HFT_MIRROR_tp2",  tp2,  C'0,0,204',STYLE_DASH);
         if(ch) ChartRedraw(cid);                   // redraw only when something changed
        }
      cid=ChartNext(cid);
     }
   //--- DIAGNOSTIC: how many charts does the tester expose to this EA? Logged once
   //--- per (total,others) change so it doesn't spam. If total==1 the tester is
   //--- isolating the EA to its own chart (mirror to a D1 tab impossible there).
   static int lt=-1, lo=-1;
   if(total!=lt || others!=lo)
     { lt=total; lo=others; Print("MirrorLevels diag: ",total," chart(s) visible to EA, ",
                                   others," other ",_Symbol," chart(s) to mirror onto"); }
  }

void MirrorClear()
  {
   long cid=ChartFirst();
   while(cid>=0)
     {
      if(cid!=0 && ChartSymbol(cid)==_Symbol) ObjectsDeleteAll(cid,"HFT_MIRROR_");
      cid=ChartNext(cid);
     }
  }

//+------------------------------------------------------------------+
//| Display-only imbalance highlights: 3-candle FVGs, bar price-gaps, |
//| and tick-volume spikes - all ONE colour, each with a 50% midline. |
//| Only UNMITIGATED zones are drawn: a zone tapped-and-rejected is    |
//| dropped; a zone price CLOSES through inverts (flips polarity) and  |
//| stays until re-tapped, then drops. Tick-volume is a PROXY          |
//| (Dukascopy volume = tick count, not real exchange volume). This    |
//| never touches trade/entry logic - pure chart context.             |
//+------------------------------------------------------------------+
void AddImb(double &lo[],double &hi[],int &dir[],int &t[],int &n,
            double l,double h,int d,int idx)
  {
   if(h<=l) return;
   for(int q=0;q<n;q++)
      if(MathAbs(lo[q]-l)<_Point && MathAbs(hi[q]-h)<_Point) return;   // dedupe
   ArrayResize(lo,n+1); ArrayResize(hi,n+1); ArrayResize(dir,n+1); ArrayResize(t,n+1);
   lo[n]=l; hi[n]=h; dir[n]=d; t[n]=idx; n++;
  }
//--- 0=active, 1=inverted (still valid), 2=used up (do not draw)
int ImbState(const MqlRates &r[],int cnt,int iformed,double lo,double hi,int dir)
  {
   bool inverted=false;
   for(int j=iformed+1;j<cnt;j++)
     {
      bool enters=(r[j].high>=lo && r[j].low<=hi);
      if(!inverted)
        {
         bool through=(dir>0 ? r[j].close<lo : r[j].close>hi);
         if(through){ inverted=true; continue; }   // flip polarity; own bar isn't a re-tap
         if(enters) return 2;                       // tapped & rejected -> used up
        }
      else if(enters) return 2;                     // inverted zone re-tapped -> used up
     }
   return inverted ? 1 : 0;
  }
void DrawImbalances(string p,SignalCandidate &c)
  {
   MqlRates r[];
   ArraySetAsSeries(r,false);                       // r[0]=oldest .. r[cnt-1]=last closed (signal bar)
   int need=InpImbLookback+30;
   int cnt=CopyRates(_Symbol,g_tf,1,need,r);
   if(cnt<25) return;
   int cur=cnt-1;

   double zlo[],zhi[]; int zdir[],zt[]; int nz=0;
   int startscan=MathMax(2,cnt-InpImbLookback);
   for(int i=startscan;i<=cur;i++)
     {
      //--- 3-candle fair value gap
      if(r[i-2].high < r[i].low)  AddImb(zlo,zhi,zdir,zt,nz,r[i-2].high,r[i].low,+1,i);
      if(r[i-2].low  > r[i].high) AddImb(zlo,zhi,zdir,zt,nz,r[i].high,r[i-2].low,-1,i);
      //--- bar-to-bar price gap
      if(r[i].low  > r[i-1].high) AddImb(zlo,zhi,zdir,zt,nz,r[i-1].high,r[i].low,+1,i);
      if(r[i].high < r[i-1].low)  AddImb(zlo,zhi,zdir,zt,nz,r[i].high,r[i-1].low,-1,i);
      //--- tick-volume spike (proxy for activity)
      if(i>=21)
        {
         double sum=0; for(int k=i-20;k<i;k++) sum+=(double)r[k].tick_volume;
         double avg=sum/20.0;
         if(avg>0 && (double)r[i].tick_volume > InpImbVolMult*avg)
            AddImb(zlo,zhi,zdir,zt,nz,r[i].low,r[i].high,(r[i].close>=r[i].open?+1:-1),i);
        }
     }

   int drawn=0;
   for(int idx=nz-1; idx>=0 && drawn<InpImbMaxDraw; idx--)
     {
      int st=ImbState(r,cnt,zt[idx],zlo[idx],zhi[idx],zdir[idx]);
      if(st==2) continue;                            // used up -> skip
      datetime t0=r[zt[idx]].time, t1=r[cur].time;
      string nm=StringFormat("%simb%d",p,idx);
      if(ObjectCreate(0,nm,OBJ_RECTANGLE,0,t0,zhi[idx],t1,zlo[idx]))
        {
         ObjectSetInteger(0,nm,OBJPROP_COLOR,InpImbColor);
         ObjectSetInteger(0,nm,OBJPROP_BACK,true);
         ObjectSetInteger(0,nm,OBJPROP_FILL,true);
         ObjectSetInteger(0,nm,OBJPROP_SELECTABLE,false);
         ObjectSetInteger(0,nm,OBJPROP_STYLE,(st==1?STYLE_DASH:STYLE_SOLID)); // inverted = dashed edge
        }
      double mid=(zhi[idx]+zlo[idx])/2.0;            // 50% consequent-encroachment midline
      string mn=StringFormat("%simbM%d",p,idx);
      if(ObjectCreate(0,mn,OBJ_TREND,0,t0,mid,t1,mid))
        {
         ObjectSetInteger(0,mn,OBJPROP_COLOR,InpImbColor);
         ObjectSetInteger(0,mn,OBJPROP_STYLE,STYLE_DOT);
         ObjectSetInteger(0,mn,OBJPROP_WIDTH,1);
         ObjectSetInteger(0,mn,OBJPROP_RAY_RIGHT,false);
         ObjectSetInteger(0,mn,OBJPROP_BACK,true);
         ObjectSetInteger(0,mn,OBJPROP_SELECTABLE,false);
        }
      drawn++;
     }
  }

//--- split _Symbol into base/quote (6-letter fx pairs split 3/3 after a .dk
//--- suffix; indices/oil/etc are treated as USD-quoted so USD events invert).
void SymbolCcy(string &base,string &quote)
  {
   string s=_Symbol; int dot=StringFind(s,".");
   if(dot>=0) s=StringSubstr(s,0,dot);
   if(StringLen(s)==6){ base=StringSubstr(s,0,3); quote=StringSubstr(s,3,3); }
   else { base=s; quote="USD"; }
  }
//--- top-tier movers only: rate decisions, CPI, NFP, GDP, PMI, unemployment -
//--- but drop member-country sub-releases (German/French/Spanish/Italian CPI,
//--- PMI, GDP...) so only the headline US / Eurozone / UK prints remain.
bool IsTopTierEvent(string ev)
  {
   string e=ev; StringToLower(e);
   string skip[]={"german","french","spanish","italian","chinese","japanese",
                  "swiss","canadian","australian","new zealand"};
   for(int i=0;i<ArraySize(skip);i++) if(StringFind(e,skip[i])>=0) return false;
   string keys[]={"federal funds","fomc","rate decision","official bank rate",
                  "main refinancing","cash rate","cpi","non-farm","nonfarm",
                  "gdp","pce","pmi","unemployment rate","ecb press",
                  "monetary policy","press conference","interest rate",
                  "rate statement","bank rate","average hourly earnings",
                  "average earnings","retail sales","claimant count"};
   for(int i=0;i<ArraySize(keys);i++) if(StringFind(e,keys[i])>=0) return true;
   return false;
  }

//--- general significance tier for the popup events list: 2=HIGH, 1=MED, 0=LOW.
//--- econ_events.csv has no native impact field, so it is keyword-derived from
//--- the event name. HIGH = rate decisions + the biggest surprise-movers
//--- (NFP/CPI/GDP + central-bank statements/pressers); MED = other notable
//--- second-tier prints (PMI, unemployment, retail sales, PPI...); LOW = the
//--- rest. NOTE: the popup list is curated to top-tier events (IsTopTierEvent),
//--- so in practice ratings read HIGH or MED - a LOW-impact print isn't listed.
int EventSignificance(string ev)
  {
   string e=ev; StringToLower(e);
   string hi[]={"federal funds","fomc","rate decision","official bank rate",
                "main refinancing","cash rate","interest rate","rate statement",
                "bank rate","monetary policy","press conference","ecb press",
                "non-farm","nonfarm","cpi","gdp","pce",
                "unemployment rate","average hourly earnings","average earnings",
                "retail sales","claimant count","pmi"};
   for(int i=0;i<ArraySize(hi);i++) if(StringFind(e,hi[i])>=0) return 2;
   string md[]={"pmi","unemployment","retail sales","ppi","employment change",
                "jobless","confidence","durable goods","ism","trade balance",
                "core"};
   for(int i=0;i<ArraySize(md);i++) if(StringFind(e,md[i])>=0) return 1;
   return 0;
  }
string SigTag(int s){ return (s>=2 ? "[HIGH]" : (s==1 ? "[MED]" : "[LOW]")); }
//--- significance from the baked class column (V/W=HIGH, C=MED), falling back to the
//--- keyword rule for a legacy feed with no class. One source of truth = the feed.
int ClassSig(string cls,string ev)
  {
   if(cls=="V" || cls=="W") return 2;   // violation / weekend-hold -> HIGH
   if(cls=="C")             return 1;    // caution -> MED
   if(cls=="")              return EventSignificance(ev);  // legacy feed: keyword fallback
   return 0;                             // H (holiday) -> LOW
  }
//+------------------------------------------------------------------+
//| Display-only high-impact economic-event lines from              |
//| Common\Files\econ_events.csv (datetime_utc,ccy,event,actual,     |
//| forecast,ccy_bias). A dashed vertical line + rotated label at    |
//| each high-impact event in the loaded range involving this        |
//| symbol's base/quote ccy. Colour = bull/bear bias for THIS ticker |
//| (ccy_bias flipped when the event ccy is the quote). Bias is the  |
//| actual-vs-forecast surprise (a post-release review aid). Never    |
//| affects trade logic.                                             |
//+------------------------------------------------------------------+
//--- parse econ_events.csv ONCE into the cache (only this pair's ccy events).
void LoadEconEvents()
  {
   g_ev_loaded=true;                 // set even on failure so we don't retry each bar
   ArrayResize(g_ev_t,0); ArrayResize(g_ev_ccy,0);
   ArrayResize(g_ev_name,0); ArrayResize(g_ev_cb,0); ArrayResize(g_ev_top,0);
   ArrayResize(g_ev_cls,0);
   int h=FileOpen("econ_events.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE,',');   // six live instances open it at once
   if(h==INVALID_HANDLE)
     { Print("econ_events.csv not in Common\\Files - no event lines (err ",GetLastError(),")"); return; }
   string base,quote; SymbolCcy(base,quote);
   //--- detect the class column by reading the header row (7 cols with 'class', else 6)
   string hdr[]; int ncol=0;
   for(int k=0;k<8 && !FileIsEnding(h);k++)
     { string f=FileReadString(h); ArrayResize(hdr,k+1); hdr[k]=f; ncol++;
       if(FileIsLineEnding(h)) break; }
   bool has_class=(ncol>=7);   // datetime,ccy,event,actual,forecast,ccy_bias,class
   int n=0;
   while(!FileIsEnding(h))
     {
      string sdt=FileReadString(h);
      if(sdt==""){ if(FileIsEnding(h)) break; else continue; }
      string ccy=FileReadString(h);
      string ev =FileReadString(h);
      FileReadString(h);             // actual  (not cached - never shown before release)
      FileReadString(h);             // forecast
      int    cb =(int)StringToInteger(FileReadString(h));
      string cls=(has_class? FileReadString(h) : "");   // class column (V/W/C/H)
      StringToUpper(cls);
      // this pair's two currencies, plus all-ccy events (ccy=All: Jackson Hole, G20...)
      if(ccy!=base && ccy!=quote && ccy!="All") continue;
      datetime t=StringToTime(sdt);
      if(t<=0) continue;
      int m=ArraySize(g_ev_t);
      ArrayResize(g_ev_t,m+1); ArrayResize(g_ev_ccy,m+1); ArrayResize(g_ev_name,m+1);
      ArrayResize(g_ev_cb,m+1); ArrayResize(g_ev_top,m+1); ArrayResize(g_ev_cls,m+1);
      g_ev_t[m]=t; g_ev_ccy[m]=ccy; g_ev_name[m]=ev; g_ev_cb[m]=cb; g_ev_cls[m]=cls;
      // shown on the popup/lines = any classified event (V/W=HIGH, C=MED). Keeps the
      // caution prints (retail sales, PMI...) the trader asked to see, now tagged MED
      // rather than HIGH. Legacy feed (no class) falls back to the keyword rule.
      g_ev_top[m]=(cls=="V" || cls=="W" || cls=="C") || (cls=="" && IsTopTierEvent(ev));
      n++;
     }
   FileClose(h);
   PrintFormat("econ cache: %d %s/%s events loaded",n,base,quote);
  }

//--- rolling, LOOK-AHEAD-SAFE redraw. Upcoming events (t>now) render NEUTRAL with
//--- an [upcoming] tag - no bias/actual leaked; a released event (t<=now) reveals
//--- its bull/bear colour + surprise. Redrawn each new bar so the forward window
//--- (now .. now+InpEvtLookaheadHours) fills in as replay advances.
void DrawEconEvents()
  {
   if(!g_ev_loaded) return;
   ObjectsDeleteAll(0,InpObjPrefix+"EVT");   // clear EVT_* and EVTL_* from last redraw
   string base,quote; SymbolCcy(base,quote);
   datetime now=iTime(_Symbol,g_tf,0);
   datetime tmin=now-(datetime)((long)InpEvtPastDays*86400);
   datetime tmax=now+(datetime)((long)InpEvtLookaheadHours*3600);
   double px_now=iClose(_Symbol,g_tf,0);
   int drawn=0;
   for(int i=0;i<ArraySize(g_ev_t) && drawn<InpEvtMaxDraw;i++)
     {
      datetime t=g_ev_t[i];
      if(t<tmin || t>tmax) continue;
      if(InpEventTopTierOnly && !g_ev_top[i]) continue;
      int rel=(g_ev_ccy[i]==base?1:(g_ev_ccy[i]==quote?-1:(g_ev_ccy[i]=="All"?1:0)));
      if(rel==0) continue;
      bool released=(t<=now);
      int tb=(released? g_ev_cb[i]*rel : 0);   // no bias before the event fires
      color clr=(released? (tb>0?InpEvtBull:(tb<0?InpEvtBear:InpEvtNeutral)) : InpEvtNeutral);
      string tag=(released? (tb>0?" [BULL]":(tb<0?" [BEAR]":"")) : " [upcoming]");
      string vn=StringFormat("%sEVT_%d",InpObjPrefix,i);
      if(ObjectCreate(0,vn,OBJ_VLINE,0,t,0))
        {
         ObjectSetInteger(0,vn,OBJPROP_COLOR,clr);
         ObjectSetInteger(0,vn,OBJPROP_STYLE,STYLE_DASH);
         ObjectSetInteger(0,vn,OBJPROP_WIDTH,1);
         ObjectSetInteger(0,vn,OBJPROP_BACK,true);
         ObjectSetInteger(0,vn,OBJPROP_SELECTABLE,false);
        }
      int sh=iBarShift(_Symbol,g_tf,t,false);
      double py=(released && sh>=0? iClose(_Symbol,g_tf,sh) : px_now);  // future: anchor at current price
      if(py>0.0)
        {
         string tn=StringFormat("%sEVTL_%d",InpObjPrefix,i);
         //--- BLINDNESS: the event NAME leaks the economy/currency (e.g. "Non-Farm
         //--- Payrolls" -> USD leg), so under InpBlindLabels (the blind-capture runs)
         //--- print a generic "high-impact" marker instead of the name. The LINE
         //--- (timing) + bias tag (BULL/BEAR/upcoming) stay visible and add no leak
         //--- beyond what the advisor already gets from Tier-0. Non-blind runs (the
         //--- operator's own analysis) keep the full name.
         string txt=(InpBlindLabels? StringFormat(" high-impact%s",tag)
                                   : StringFormat(" %s%s",g_ev_name[i],tag));
         if(ObjectCreate(0,tn,OBJ_TEXT,0,t,py))
           {
            ObjectSetString (0,tn,OBJPROP_TEXT,txt);
            ObjectSetInteger(0,tn,OBJPROP_COLOR,clr);
            ObjectSetInteger(0,tn,OBJPROP_FONTSIZE,InpFontSize);
            ObjectSetDouble (0,tn,OBJPROP_ANGLE,90.0);
            ObjectSetInteger(0,tn,OBJPROP_ANCHOR,ANCHOR_LEFT);
            ObjectSetInteger(0,tn,OBJPROP_SELECTABLE,false);
           }
        }
      drawn++;
     }
  }

//--- "DD MMM HH:MM" (absolute, human) for the popup event list
string FmtAbsDate(datetime t)
  {
   MqlDateTime d; TimeToStruct(t,d);
   string mon[]={"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
   int mi=(d.mon>=1 && d.mon<=12)? d.mon-1 : 0;
   return StringFormat("%02d %s %02d:%02d",d.day,mon[mi],d.hour,d.min);
  }

//--- popup "upcoming events" block in BOTH forms: absolute ("22 Jul 11:45 EUR ..")
//--- and relative ("in 1d 4h  EUR .."). Relative dates + symbol redaction stop a
//--- coaching screenshot from being reverse-identified. Only SCHEDULED info
//--- (time/ccy/name) - never an outcome, since every listed event is AFTER the
//--- decision. Notable (top-tier) events over the next InpEvtListDays days.
void BuildEventBlocks(datetime sig,string &absb,string &relb)
  {
   absb=""; relb="";
   string base,quote; SymbolCcy(base,quote);
   datetime tmax=sig+(datetime)((long)InpEvtListDays*86400);
   int cnt=0;
   for(int i=0;i<ArraySize(g_ev_t);i++)
     {
      datetime t=g_ev_t[i];
      if(t<sig || t>tmax) continue;          // strictly the forward window
      if(!g_ev_top[i]) continue;             // notable only
      if(g_ev_ccy[i]!=base && g_ev_ccy[i]!=quote && g_ev_ccy[i]!="All") continue;
      // W-class rows carry a placeholder time (12:30 on the vote day, 00:00 for a
      // political row); measure imminence to the weekend-window START (that day's
      // midnight) so the popup's "in Xd Yh" matches what the blind advisor is told.
      datetime rt=t;
      if(g_ev_cls[i]=="W")
        { MqlDateTime mt; TimeToStruct(t,mt); mt.hour=0; mt.min=0; mt.sec=0; rt=StructToTime(mt); }
      long ds=(long)(rt-sig); if(ds<0) ds=0;
      int dd=(int)(ds/86400), hh=(int)((ds%86400)/3600);
      string nm=StringFormat("%s %s  %s",g_ev_ccy[i],g_ev_name[i],SigTag(ClassSig(g_ev_cls[i],g_ev_name[i])));
      absb+=StringFormat("%s  %s\r\n",FmtAbsDate(t),nm);
      relb+=StringFormat("in %dd %dh  %s\r\n",dd,hh,nm);
      if(++cnt>=InpEvtListMax){ absb+="(+ more)\r\n"; relb+="(+ more)\r\n"; break; }
     }
   if(cnt==0)
     { absb=StringFormat("(no high-impact events in the next %dd)",InpEvtListDays); relb=absb; }
  }

//+------------------------------------------------------------------+
//| Native OBJ_FIBO on the Deep-Fib impulse leg. Anchor 0 = leg       |
//| origin (leg_p0), anchor 1 = impulse extreme (leg_p1). The detector |
//| defines its retracement as Level(v) = leg_p1 + v*(leg_p0-leg_p1)   |
//| (0.0 at the extreme, 1.0 at the origin). We do NOT trust a          |
//| remembered MT5 level-value convention: we create the object, then  |
//| read where each level ACTUALLY lands via ObjectGetValueByTime and   |
//| compare to Level(v). If the 0.618 line does not coincide (MT5 maps  |
//| level values from the other anchor), we remap every level value to  |
//| 1-v so the labelled 0.618 line sits exactly on Level(0.618) for      |
//| both long and short setups. A FIBCHK line prints the numbers.       |
//+------------------------------------------------------------------+
void DrawFibo(string p,SignalCandidate &c)
  {
   double lv[7]={0.0,0.5,0.618,0.705,0.786,0.886,1.0};
   string lt[7]={"0.0","50.0","61.8","70.5 OTE","78.6","88.6 SL","100.0"};
   //--- detector price for each fib value: Level(v)=leg_p1+v*(leg_p0-leg_p1)
   double det[7];
   for(int i=0;i<7;i++) det[i]=c.leg_p1+lv[i]*(c.leg_p0-c.leg_p1);

   string fb=p+"fibo";
   ObjectDelete(0,fb);
   if(!ObjectCreate(0,fb,OBJ_FIBO,0,c.leg_t0,c.leg_p0,c.leg_t1,c.leg_p1))
     { Print("FIBCHK: OBJ_FIBO create failed err=",GetLastError()); return; }
   ObjectSetInteger(0,fb,OBJPROP_COLOR,clrGoldenrod);
   ObjectSetInteger(0,fb,OBJPROP_WIDTH,InpLineWidth);
   ObjectSetInteger(0,fb,OBJPROP_BACK,true);
   ObjectSetInteger(0,fb,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,fb,OBJPROP_SELECTED,false);
   ObjectSetInteger(0,fb,OBJPROP_RAY_RIGHT,InpFibRay);
   ObjectSetString (0,fb,OBJPROP_TEXT,"Deep-Fib impulse grid");
   ObjectSetInteger(0,fb,OBJPROP_LEVELS,7);

   //--- apply level VALUES straight (v), then probe the actual convention
   FiboApplyLevels(fb,lv,lt,false);
   datetime tprobe=(c.leg_t1>c.leg_t0? c.leg_t1 : c.leg_t0);
   double tol=10.0*_Point;                        // ~1 pip on a 5-digit fx symbol
   double a618=ObjectGetValueByTime(0,fb,tprobe,2); // index 2 == labelled 0.618
   bool   readable=(a618>0.0);
   bool   flip=false;
   if(readable && MathAbs(a618-det[2])>tol)
     { flip=true; FiboApplyLevels(fb,lv,lt,true); } // remap v -> 1-v

   //--- verify + print per-level: MT5 actual vs detector Level(v)
   double maxerr=0.0;
   string dump="";
   for(int i=0;i<7;i++)
     {
      double act=ObjectGetValueByTime(0,fb,tprobe,i);
      double d=(act>0.0? act-det[i] : 0.0);
      if(act>0.0 && MathAbs(d)>maxerr) maxerr=MathAbs(d);
      dump+=StringFormat(" %s[fib=%.5f det=%.5f d=%.6f]",lt[i],act,det[i],d);
     }
   double zh=MathMax(c.zone_hi,c.zone_lo), zl=MathMin(c.zone_hi,c.zone_lo);
   PrintFormat("FIBCHK %s %s leg(p0=%.5f p1=%.5f) flip=%s readable=%s maxerr=%.6f "
               "| goldenpocket zoneHi=%.5f=Level0.618 zoneLo=%.5f=Level0.786 |%s",
               p,DirStr(c.direction),c.leg_p0,c.leg_p1,(flip?"YES":"no"),
               (readable?"yes":"NO(fallback:no-flip)"),maxerr,zh,zl,dump);
  }

//--- set OBJ_FIBO level values (v, or 1-v when remap) + label texts + styling
void FiboApplyLevels(string fb,double &lv[],string &lt[],bool remap)
  {
   for(int i=0;i<7;i++)
     {
      double v=(remap? 1.0-lv[i] : lv[i]);
      ObjectSetDouble (0,fb,OBJPROP_LEVELVALUE,i,v);
      ObjectSetString (0,fb,OBJPROP_LEVELTEXT ,i,lt[i]);
      bool gp=(lv[i]>=0.618 && lv[i]<=0.786);       // golden-pocket emphasis
      ObjectSetInteger(0,fb,OBJPROP_LEVELCOLOR,i,(gp?clrOrange:clrGoldenrod));
      ObjectSetInteger(0,fb,OBJPROP_LEVELSTYLE,i,(gp?STYLE_SOLID:STYLE_DOT));
      ObjectSetInteger(0,fb,OBJPROP_LEVELWIDTH,i,1);
     }
  }

//+------------------------------------------------------------------+
//| PERSISTENT swing markers over a rolling InpSwingDays window,        |
//| independent of any signal (EMArev never fills the per-signal swing  |
//| arrays, so its setups showed no markers). Fractal 2-left/2-right    |
//| highs & lows; redrawn each new bar. Own prefix HFT_SW_ so neither   |
//| the per-signal prune (HFT_<id>_) nor the econ sweep (HFT_EVT) touch |
//| it. Replaces the per-signal generic swing markers.                 |
//+------------------------------------------------------------------+
void DrawSwingMarkers()
  {
   string swp=InpObjPrefix+"SW_";
   ObjectsDeleteAll(0,swp);
   int avail=Bars(_Symbol,g_tf);
   int nbars=(int)MathMin(InpSwingDays*6+8,avail-1);   // ~6 H4 bars/day + fractal margin
   if(nbars<12) return;
   MqlRates r[]; ArraySetAsSeries(r,true);
   if(CopyRates(_Symbol,g_tf,0,nbars,r)<12) return;
   int N=2, drawn=0;
   for(int i=N;i<nbars-N && drawn<120;i++)
     {
      bool sh=true,sl=true;
      for(int k=1;k<=N;k++)
        {
         if(!(r[i].high>r[i-k].high && r[i].high>r[i+k].high)) sh=false;
         if(!(r[i].low <r[i-k].low  && r[i].low <r[i+k].low )) sl=false;
        }
      if(sh)
        {
         string an=StringFormat("%sH_a%d",swp,i);
         if(ObjectCreate(0,an,OBJ_ARROW_DOWN,0,r[i].time,r[i].high))
           { ObjectSetInteger(0,an,OBJPROP_COLOR,clrOrangeRed);
             ObjectSetInteger(0,an,OBJPROP_WIDTH,InpLineWidth);
             ObjectSetInteger(0,an,OBJPROP_ANCHOR,ANCHOR_BOTTOM);
             ObjectSetInteger(0,an,OBJPROP_SELECTABLE,false); }
         string tn=StringFormat("%sH_t%d",swp,i);
         if(ObjectCreate(0,tn,OBJ_TEXT,0,r[i].time,r[i].high))
           { ObjectSetString(0,tn,OBJPROP_TEXT,"swing high");
             ObjectSetInteger(0,tn,OBJPROP_COLOR,clrOrangeRed);
             ObjectSetInteger(0,tn,OBJPROP_FONTSIZE,InpFontSize);
             ObjectSetInteger(0,tn,OBJPROP_ANCHOR,ANCHOR_LOWER);
             ObjectSetInteger(0,tn,OBJPROP_SELECTABLE,false); }
         drawn++;
        }
      if(sl)
        {
         string an=StringFormat("%sL_a%d",swp,i);
         if(ObjectCreate(0,an,OBJ_ARROW_UP,0,r[i].time,r[i].low))
           { ObjectSetInteger(0,an,OBJPROP_COLOR,clrDodgerBlue);
             ObjectSetInteger(0,an,OBJPROP_WIDTH,InpLineWidth);
             ObjectSetInteger(0,an,OBJPROP_ANCHOR,ANCHOR_TOP);
             ObjectSetInteger(0,an,OBJPROP_SELECTABLE,false); }
         string tn=StringFormat("%sL_t%d",swp,i);
         if(ObjectCreate(0,tn,OBJ_TEXT,0,r[i].time,r[i].low))
           { ObjectSetString(0,tn,OBJPROP_TEXT,"swing low");
             ObjectSetInteger(0,tn,OBJPROP_COLOR,clrDodgerBlue);
             ObjectSetInteger(0,tn,OBJPROP_FONTSIZE,InpFontSize);
             ObjectSetInteger(0,tn,OBJPROP_ANCHOR,ANCHOR_UPPER);
             ObjectSetInteger(0,tn,OBJPROP_SELECTABLE,false); }
         drawn++;
        }
     }
  }

//+------------------------------------------------------------------+
//| Mark confirmed fractal swings with an arrow + a literal            |
//| "swing high" / "swing low" text label. Non-selectable so the user  |
//| can't drag them; names are per-signal prefixed (p) for cleanup.    |
//+------------------------------------------------------------------+
void DrawSwings(string p,SignalCandidate &c)
  {
   for(int i=0;i<c.n_swing_hi;i++)
     {
      string an=StringFormat("%sswH_a%d",p,i);
      if(ObjectCreate(0,an,OBJ_ARROW_DOWN,0,c.swing_hi_t[i],c.swing_hi_p[i]))
        {
         ObjectSetInteger(0,an,OBJPROP_COLOR,clrOrangeRed);
         ObjectSetInteger(0,an,OBJPROP_WIDTH,InpLineWidth);
         ObjectSetInteger(0,an,OBJPROP_ANCHOR,ANCHOR_BOTTOM);   // arrow above the high
         ObjectSetInteger(0,an,OBJPROP_SELECTABLE,false);
         ObjectSetInteger(0,an,OBJPROP_SELECTED,false);
        }
      string tn=StringFormat("%sswH_t%d",p,i);
      if(ObjectCreate(0,tn,OBJ_TEXT,0,c.swing_hi_t[i],c.swing_hi_p[i]))
        {
         ObjectSetString (0,tn,OBJPROP_TEXT,"swing high");
         ObjectSetInteger(0,tn,OBJPROP_COLOR,clrOrangeRed);
         ObjectSetInteger(0,tn,OBJPROP_FONTSIZE,InpFontSize);
         ObjectSetInteger(0,tn,OBJPROP_ANCHOR,ANCHOR_LOWER);    // text above the high
         ObjectSetInteger(0,tn,OBJPROP_SELECTABLE,false);
         ObjectSetInteger(0,tn,OBJPROP_SELECTED,false);
        }
     }
   for(int i=0;i<c.n_swing_lo;i++)
     {
      string an=StringFormat("%sswL_a%d",p,i);
      if(ObjectCreate(0,an,OBJ_ARROW_UP,0,c.swing_lo_t[i],c.swing_lo_p[i]))
        {
         ObjectSetInteger(0,an,OBJPROP_COLOR,clrDodgerBlue);
         ObjectSetInteger(0,an,OBJPROP_WIDTH,InpLineWidth);
         ObjectSetInteger(0,an,OBJPROP_ANCHOR,ANCHOR_TOP);      // arrow below the low
         ObjectSetInteger(0,an,OBJPROP_SELECTABLE,false);
         ObjectSetInteger(0,an,OBJPROP_SELECTED,false);
        }
      string tn=StringFormat("%sswL_t%d",p,i);
      if(ObjectCreate(0,tn,OBJ_TEXT,0,c.swing_lo_t[i],c.swing_lo_p[i]))
        {
         ObjectSetString (0,tn,OBJPROP_TEXT,"swing low");
         ObjectSetInteger(0,tn,OBJPROP_COLOR,clrDodgerBlue);
         ObjectSetInteger(0,tn,OBJPROP_FONTSIZE,InpFontSize);
         ObjectSetInteger(0,tn,OBJPROP_ANCHOR,ANCHOR_UPPER);    // text below the low
         ObjectSetInteger(0,tn,OBJPROP_SELECTABLE,false);
         ObjectSetInteger(0,tn,OBJPROP_SELECTED,false);
        }
     }
  }

//+------------------------------------------------------------------+
//| Outcome capture: accumulate blended, volume-weighted R across the  |
//| (up to two) closing deals of a scaled-out position.                |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(!g_active) return;
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD) return;
   ulong deal=trans.deal;
   if(deal<=0 || !HistoryDealSelect(deal)) return;

   //--- PENDING FILL: a pending order we placed just became a position. Bind the
   //--- row to the new position and realise the entry/risk from the actual fill,
   //--- so the whole downstream lifecycle (scale-out, BE, exit R) works for it.
   if(HistoryDealGetInteger(deal,DEAL_ENTRY)==DEAL_ENTRY_IN)
     {
      long dorder=(long)HistoryDealGetInteger(deal,DEAL_ORDER);
      for(int i=0;i<ArraySize(g_rows);i++)
        {
         if(!g_rows[i].is_pending || g_rows[i].order_ticket!=dorder) continue;
         if(g_rows[i].posid>0 || g_rows[i].closed) continue;
         g_rows[i].posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
         double fill=HistoryDealGetDouble(deal,DEAL_PRICE);
         if(fill>0.0) { g_rows[i].entry=fill; g_rows[i].risk_px=MathAbs(fill-g_rows[i].sl); }
         g_rows[i].decision="approved_pending";      // keep provenance; guards accept it
         if(g_rows[i].partial_frac>0.0) MinLotSplitGuard(i);
         Print("Signal #",g_rows[i].id," PENDING FILLED -> posid=",g_rows[i].posid,
               " @ ",DoubleToString(fill,_Digits));
         WriteJournal(g_journal_part);
         return;
        }
      return;   // an entry-in deal that isn't one of our pendings
     }

   if(HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_OUT) return;

   long posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);

   //--- INVERSE cohort close: its posid is NEVER in g_rows[]; finalize the isolated
   //--- .inv row here (identical R math), clear g_inv_open only on FULL close.
   if(g_inv_open && posid==g_inv_posid && g_inv_open_idx>=0 && g_inv_open_idx<ArraySize(g_inv_rows))
     {
      int k=g_inv_open_idx;
      double iv=HistoryDealGetDouble(deal,DEAL_VOLUME), ip=HistoryDealGetDouble(deal,DEAL_PRICE);
      double ipr=HistoryDealGetDouble(deal,DEAL_PROFIT)+HistoryDealGetDouble(deal,DEAL_SWAP)+HistoryDealGetDouble(deal,DEAL_COMMISSION);
      datetime idt=(datetime)HistoryDealGetInteger(deal,DEAL_TIME);
      double imv=(g_inv_rows[k].direction>0? ip-g_inv_rows[k].entry : g_inv_rows[k].entry-ip);
      double iwf=(g_inv_rows[k].lots>0? iv/g_inv_rows[k].lots:1.0);
      g_inv_rows[k].pnl+=ipr; g_inv_rows[k].r_multiple+=(g_inv_rows[k].risk_px>0? iwf*imv/g_inv_rows[k].risk_px:0.0);
      g_inv_rows[k].closed_vol+=iv; g_inv_rows[k].exit_time=idt; g_inv_rows[k].exit_price=ip;
      double istep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(istep<=0)istep=0.01;
      if(g_inv_rows[k].closed_vol >= g_inv_rows[k].lots - istep*0.5)
        {
         g_inv_rows[k].closed=true; g_inv_open=false; g_inv_open_idx=-1; g_inv_posid=0;
         Print("INVERSE #",g_inv_rows[k].id," CLOSED  R=",DoubleToString(g_inv_rows[k].r_multiple,2),
               "  pnl=",DoubleToString(g_inv_rows[k].pnl,2));
        }
      WriteInvJournal(g_inv_journal_part);
      return;
     }

   int idx=-1;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].posid==posid && !g_rows[i].closed) { idx=i; break; }
   if(idx<0) return;

   ApplyExitDeal(idx,deal);
   WriteJournal(g_journal_part);
  }
//--- Graded-close accounting for ONE DEAL_ENTRY_OUT deal of row idx (extracted verbatim from
//--- OnTradeTransaction, Phase 3 M1 Slice 4) so the restart reconcile can replay history deals
//--- through exactly the same math.
void ApplyExitDeal(int idx,ulong deal)
  {
   double dvol  =HistoryDealGetDouble(deal,DEAL_VOLUME);
   double dprice=HistoryDealGetDouble(deal,DEAL_PRICE);
   double dprof =HistoryDealGetDouble(deal,DEAL_PROFIT)
                +HistoryDealGetDouble(deal,DEAL_SWAP)
                +HistoryDealGetDouble(deal,DEAL_COMMISSION);
   datetime dt  =(datetime)HistoryDealGetInteger(deal,DEAL_TIME);

   double moved =(g_rows[idx].direction>0)?(dprice-g_rows[idx].entry):(g_rows[idx].entry-dprice);
   double wfrac =(g_rows[idx].lots>0.0 ? dvol/g_rows[idx].lots : 1.0);
   double rcontrib=(g_rows[idx].risk_px>0.0 ? wfrac*(moved/g_rows[idx].risk_px) : 0.0);

   g_rows[idx].pnl        += dprof;
   g_rows[idx].r_multiple += rcontrib;
   g_rows[idx].closed_vol += dvol;
   g_rows[idx].exit_time   = dt;
   g_rows[idx].exit_price  = dprice;

   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
   if(g_rows[idx].closed_vol >= g_rows[idx].lots - step*0.5)
     {
      g_rows[idx].closed=true;
      //--- Item 2: terminal path event (TP / BE / SL / end) by exit vs levels
      double _xp=g_rows[idx].exit_price, _e=g_rows[idx].entry;
      double _tol=MathMax(3.0*(SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)>0?SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE):_Point),0.0005*_xp);
      string _term="end";
      if(g_rows[idx].tp>0.0 && MathAbs(_xp-g_rows[idx].tp)<=_tol) _term="TP";
      else if(g_rows[idx].tp1_done && MathAbs(_xp-_e)<=_tol)      _term="BE";
      else if(MathAbs(_xp-g_rows[idx].sl)<=_tol)                  _term="SL";
      MfeTerminal(idx,_term);
      Print("Signal #",g_rows[idx].id," CLOSED  blendedR=",DoubleToString(g_rows[idx].r_multiple,2),
            "  pnl=",DoubleToString(g_rows[idx].pnl,2),"  mfeR=",DoubleToString(g_rows[idx].mfe_r,2)," term=",g_rows[idx].terminal);
     }
   else
      Print("Signal #",g_rows[idx].id," partial exit ",DoubleToString(dvol,2),
            " @ ",DoubleToString(dprice,_Digits)," (Rsofar=",DoubleToString(g_rows[idx].r_multiple,2),")");
  }

//+------------------------------------------------------------------+
string StampMonth(datetime t)
  { MqlDateTime dt; TimeToStruct(t,dt); return StringFormat("%04d%02d",dt.year,dt.mon); }
//--- one journal row as CSV (shared by the tester file and the live monthly files)
string JournalRowLine(JournalRow &r)
  {
   return StringFormat(
         "%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%.2f,%s,%s,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%d,%d,%d,%s",
         r.id,TimeToString(r.time,TIME_DATE|TIME_SECONDS),r.symbol,r.strategy,DirStr(r.direction),
         DoubleToString(r.orig_entry,_Digits),DoubleToString(r.orig_sl,_Digits),DoubleToString(r.orig_tp,_Digits),
         (r.orig_tp1>0?DoubleToString(r.orig_tp1,_Digits):""),(r.orig_tp2>0?DoubleToString(r.orig_tp2,_Digits):""),
         DoubleToString(r.entry,_Digits),DoubleToString(r.sl,_Digits),DoubleToString(r.tp,_Digits),
         (r.tp1>0?DoubleToString(r.tp1,_Digits):""),(r.tp2>0?DoubleToString(r.tp2,_Digits):""),
         r.partial_frac,DoubleToString(r.lots,2),
         r.decision,r.skip_reason,(r.edited?1:0),(r.is_pending?1:0),r.decision_ms,r.posid,(r.tp1_done?1:0),
         (r.closed?TimeToString(r.exit_time,TIME_DATE|TIME_SECONDS):""),
         (r.closed?DoubleToString(r.exit_price,_Digits):""),
         (r.closed?DoubleToString(r.pnl,2):""),
         (r.closed?DoubleToString(r.r_multiple,2):""),
         r.regime,r.with_trend,
         DoubleToString(r.to_entry,_Digits),DoubleToString(r.to_sl,_Digits),
         (r.to_tp1>0?DoubleToString(r.to_tp1,_Digits):""),(r.to_tp2>0?DoubleToString(r.to_tp2,_Digits):""),
         (r.closed?DoubleToString(r.mfe_r,2):""),
         (r.closed?DoubleToString(r.pre_dip_r,2):""),(r.closed?DoubleToString(r.post_dip_r,2):""),
         r.dipped,r.terminal,r.decision_class,
         (r.closed?DoubleToString(r.rt_tp1R,3):""),(r.closed?DoubleToString(r.rt_tp2R,3):""),
         r.rt_touched1,r.rt_reached2,r.rt_redip1,(r.closed?DoubleToString(r.rt_bankr,3):""));
  }
#define JOURNAL_HEADER "signal_id,signal_time,symbol,strategy,direction," \
      "orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,entry,sl,tp,tp1,tp2,partial_frac,lots," \
      "decision,skip_reason,edited,is_pending,decision_ms,posid,tp1_done," \
      "exit_time,exit_price,pnl,r_multiple,regime,with_trend,to_entry,to_sl,to_tp1,to_tp2,mfe_r,pre_dip_r,post_dip_r,dipped,terminal,decision_class," \
      "rt_tp1r,rt_tp2r,rt_touched1,rt_reached2,rt_redip1,rt_bankr"
//--- LIVE (E3/G5/§11-5): monthly SYMBOL_YYYYMM.csv under live\journal, rows keyed by signal month, every
//--- present month rewritten atomically on every call (rows are few; simplicity over cleverness), with the
//--- five live columns appended. No .part, no finaliser. Tester bytes are produced by the branch below.
void WriteLiveJournals()
  {
   string months[]; int nm=0;
   for(int i=0;i<ArraySize(g_rows);i++)
     { string m=StampMonth(g_rows[i].time); bool have=false; for(int q=0;q<nm;q++) if(months[q]==m) have=true;
       if(!have){ ArrayResize(months,nm+1); months[nm++]=m; } }
   for(int q=0;q<nm;q++)
     {
      string body=JOURNAL_HEADER ",live,account_id,risk_pct_gate,risk_mult_applied,auto"+FloorHeader()+"\n";
      for(int i=0;i<ArraySize(g_rows);i++)
        {
         if(StampMonth(g_rows[i].time)!=months[q]) continue;
         body+=JournalRowLine(g_rows[i])+StringFormat(",%d,%I64d,%.4f,%.3f,%d",(g_rows[i].live?1:0),g_rows[i].account_id,g_rows[i].risk_pct_gate,g_rows[i].risk_mult_applied,g_rows[i].auto_skip)+FloorCols(g_rows[i])+"\n";
        }
      AtomicWriteText(LivePath(StringFormat("journal\\%s_%s.csv",_Symbol,months[q])),body);
     }
  }
string FloorHeader(){ return (InpStopFloorATR>0.0 ? ",stop_pre_floor,stop_post_floor,floor_applied" : ""); }
string FloorCols(JournalRow &r)
  { return (InpStopFloorATR>0.0 ? StringFormat(",%s,%s,%d",DoubleToString(r.stop_pre_floor,_Digits),DoubleToString(r.stop_post_floor,_Digits),r.floor_applied) : ""); }
void WriteJournal(string path)
  {
   if(InpLiveMode){ WriteLiveJournals(); LiveSaveAllState(); return; }   // live: monthly + atomic, no .part (also the init-time write)
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE) { Print("WARNING: cannot open journal '",path,"' err=",GetLastError()); return; }
   FileWriteString(h,
      "signal_id,signal_time,symbol,strategy,direction,"
      "orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,entry,sl,tp,tp1,tp2,partial_frac,lots,"
      "decision,skip_reason,edited,is_pending,decision_ms,posid,tp1_done,"
      "exit_time,exit_price,pnl,r_multiple,regime,with_trend,to_entry,to_sl,to_tp1,to_tp2,mfe_r,pre_dip_r,post_dip_r,dipped,terminal,decision_class,"
      "rt_tp1r,rt_tp2r,rt_touched1,rt_reached2,rt_redip1,rt_bankr"+FloorHeader()+"\n");
   for(int i=0;i<ArraySize(g_rows);i++) FileWriteString(h,JournalRowLine(g_rows[i])+FloorCols(g_rows[i])+"\n");
   FileFlush(h); FileClose(h);
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   double sumR=0.0;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].closed) sumR+=g_rows[i].r_multiple;
   return(sumR);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   //--- PHASE 3 LIVE: stop the timer and leave a final heartbeat so the watchdog can tell a
   //--- clean stop from a hang (status "stopped" vs a stale "running" beat).
   if(InpLiveMode && g_live_inited)
     {
      EventKillTimer();
      AuditLine("stop","","","","ok","",StringFormat("reason=%d",reason));
      WriteHeartbeat("stopped");
     }
   //--- tear the management panel down first (joins its thread) so nothing
   //--- outlives the test run.
   if(g_panel_open){ TDM_Close(); g_panel_open=false; }
   MirrorClear();   //--- remove mirrored entry/SL/TP lines from other symbol charts
   if(g_active && g_started)
     {
      // §3.8: AA-mode (headless) journals carry the "AA_" prefix so mt5_verify's
      // baseline can never overwrite the trader's own journal for the same window.
      string jpfx=(InpAutoApprove!=AA_NONE ? "AA_" : "");
      //--- PHASE 3 LIVE: journals stay under live\journal (§11-5, never mixed with tester files);
      //--- monthly rotation replaces this finaliser in Slice 5. Tester path/bytes unchanged.
      if(InpLiveMode)
        {   // live journals are monthly + atomic; nothing to rename. Flush once more and leave.
         WriteLiveJournals(); if(ArraySize(g_actions)>0) WriteLiveActions();
         Print("Live journals flushed: <Terminal>\\Common\\Files\\",InpLiveRoot,"\\journal\\ (",ArraySize(g_rows)," signals, monthly files)");
        }
      else
        {
      string finalp=StringFormat("journal\\%s%s_%s_%s.csv",jpfx,_Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
      WriteJournal(finalp);
      if(g_journal_part!="" && g_journal_part!=finalp) FileDelete(g_journal_part,FILE_COMMON);
      Print("Journal finalised: <Terminal>\\Common\\Files\\",finalp," (",ArraySize(g_rows)," signals)");
      //--- STUDY-ONLY: emit the shadow-v2 + impulse sidecar next to the journal.
      if(!InpV2Exit)
        {
         string v2p=StringFormat("journal\\v2study_%s_%s_%s.csv",
                                 _Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
         WriteV2Study(v2p);
         Print("v2 study sidecar: <Terminal>\\Common\\Files\\",v2p," (",ArraySize(g_rows)," rows)");
        }
      //--- finalise the manual-actions log alongside the journal (if any fired)
      if(ArraySize(g_actions)>0)
        {
         string af=StringFormat("journal\\%s%s_%s_%s.actions.csv",jpfx,_Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
         WriteActions(af);
         if(g_actions_part!="" && g_actions_part!=af) FileDelete(g_actions_part,FILE_COMMON);
         Print("Manual-actions log finalised: <Terminal>\\Common\\Files\\",af," (",ArraySize(g_actions)," actions)");
        }
      //--- finalise the ISOLATED inverse cohort (separate file; never the graded journal)
      if(ArraySize(g_inv_rows)>0)
        {
         string invf=StringFormat("journal\\%s%s_%s_%s.inv.csv",
                                  jpfx,_Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
         WriteInvJournal(invf);
         if(g_inv_journal_part!="" && g_inv_journal_part!=invf) FileDelete(g_inv_journal_part,FILE_COMMON);
         Print("Inverse journal finalised: <Terminal>\\Common\\Files\\",invf," (",ArraySize(g_inv_rows)," inverse trades)");
         if(ArraySize(g_inv_actions)>0)
           {
            string iaf=StringFormat("journal\\%s%s_%s_%s.inv.actions.csv",
                                    jpfx,_Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
            WriteInvActions(iaf);
            if(g_inv_actions_part!="" && g_inv_actions_part!=iaf) FileDelete(g_inv_actions_part,FILE_COMMON);
           }
        }
        }   // !InpLiveMode finaliser
      if(InpCleanupOnDeinit) ObjectsDeleteAll(0,InpObjPrefix);
     }
   for(int i=0;i<g_ndet;i++)
      if(CheckPointer(g_detectors[i])==POINTER_DYNAMIC) { delete g_detectors[i]; g_detectors[i]=NULL; }
   g_ndet=0;
  }
//+------------------------------------------------------------------+
