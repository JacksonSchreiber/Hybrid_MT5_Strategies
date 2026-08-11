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

#include <Trade\Trade.mqh>
#include <Hybrid\Signal.mqh>
#include <Hybrid\detectors\DetectorCommon.mqh>
#include <Hybrid\detectors\SmcDetector.mqh>
#include <Hybrid\detectors\FibDetector.mqh>
#include <Hybrid\detectors\EmaDetector.mqh>

//--- coloured EDITABLE dialog (poll-driven) + plain fallback (early-bound)
//--- TD_Open shows the window (Entry/SL/TP as edit boxes) and returns at once;
//--- the EA then loops TD_Poll (pumps window msgs, reads the boxes) and pushes
//--- R:R-locked recomputed values back via TD_SetDisplay, moving the real chart
//--- lines live, until the user clicks Accept(1)/Skip(2). See TradeDialog.c.
#import "TradeDialog.dll"
int  TD_Open(string title,string symbol,string strategy,string direction,
             string sigtime,string entry,string sl,string tp,string tp2,string lots,string rr);
int  TD_Poll(double &entry,double &sl,double &tp,double &tp2,int &dirty);
void TD_SetDisplay(string entry,string sl,string tp,string tp2,string lots,string rr,int ok);
void TD_Close(void);
int  TD_SkipReason(void);   // 1-6 skip-reason code from the last skip (0 none)
void TD_SetOrderType(string s); // set the "Order" row (MARKET / BUY LIMIT @ x)
void TD_SetEvents(string abs_dates,string rel_dates); // upcoming-events list (both forms)
int  TD_Coach(void);        // 1 = coach mode on (scrub chart label to match)
//--- mid-trade MANAGEMENT PANEL (separate, non-modal window on its OWN thread,
//--- so it stays live + clickable while the visual tester is PAUSED - which is
//--- exactly when the operator decides at bar close). The EA only pushes state
//--- and drains a latched button action; all trading stays on the MQL thread.
int  TDM_Open(string title);                       // spawn panel; 1=up
void TDM_Update(string state_text,double lots,int be_enabled); // push live state
int  TDM_Poll(void);        // 0 none, 1 close, 2 close50, 3 SL->BE (one-shot)
void TDM_Close(void);       // tear down + join the panel thread
#import
#import "user32.dll"
int MessageBoxW(long hWnd,string lpText,string lpCaption,uint uType);
#import
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
input long   InpMagic       = 990217;   // magic number
input int    InpDeviation   = 50;       // max slippage (points)
input bool   InpCleanupOnDeinit = false;// delete overlay objects on EA removal
input string InpObjPrefix   = "HFT_";   // chart-object name prefix
input bool   InpUseColoredDialog = true;// true: coloured TradeDialog.dll; false: MessageBoxW
input ENUM_AUTO_APPROVE InpAutoApprove = AA_NONE; // NONE=interactive; ALL/SKIP=headless (tester only)
//--- strategy selection
input bool   InpUseSMC      = true;     // Strategy 1: liquidity sweep + MSS (priority 1)
input bool   InpUseFib      = true;     // Strategy 2: deep fib retracement (priority 2)
input bool   InpUseEMA      = true;     // Strategy 3: EMA20 mean reversion (priority 3)
//--- SMC params
input double InpSmcMinRR    = 2.0;
input double InpSmcTpR      = 3.0;
//--- Fib params
input double InpFibImpulseATR = 2.0;
input double InpFibMinRR    = 2.0;
//--- EMA params
input double InpEmaStretch  = 2.0;
input double InpEmaAdxCeil  = 30.0;
input double InpEmaMinRR    = 1.3;
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
ISignalDetector *g_detectors[3];           // priority order: [0]=SMC,[1]=Fib,[2]=EMA
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

//+------------------------------------------------------------------+
string DirStr(int d) { return (d>0 ? "BUY" : "SELL"); }
string StampCompact(datetime t)
  { MqlDateTime dt; TimeToStruct(t,dt); return StringFormat("%04d%02d%02d",dt.year,dt.mon,dt.day); }

//+------------------------------------------------------------------+
int OnInit()
  {
   bool in_tester = (bool)MQLInfoInteger(MQL_TESTER);
   bool in_visual = (bool)MQLInfoInteger(MQL_VISUAL_MODE);
   bool dll_ok    = (bool)MQLInfoInteger(MQL_DLLS_ALLOWED);
   bool auto_mode = (InpAutoApprove!=AA_NONE);

   //--- HARD RULE: auto-approve (no modal) is TESTER-ONLY; the interactive
   //--- modal path additionally needs visual mode + DLLs. Never auto-trade live.
   if(!in_tester)
     {
      Print("HybridForwardTest runs only in the Strategy Tester - staying INERT.");
      g_active=false; return(INIT_SUCCEEDED);
     }
   if(!auto_mode && (!in_visual || !dll_ok))
     {
      Print("Interactive mode needs visual tester + DLLs (or set InpAutoApprove=ALL/SKIP for ",
            "headless). MQL_VISUAL_MODE=",in_visual," MQL_DLLS_ALLOWED=",dll_ok," - staying INERT.");
      g_active=false; return(INIT_SUCCEEDED);
     }
   g_active=true;

   //--- build detector list in PRIORITY order (SMC > Fib > EMA)
   g_ndet=0;
   if(InpUseSMC) g_detectors[g_ndet++]=new CLiquiditySweepMSS(InpSmcMinRR,InpSmcTpR,InpRiskPct);
   if(InpUseFib) g_detectors[g_ndet++]=new CDeepFibRetrace(InpFibImpulseATR,InpFibMinRR,InpRiskPct);
   if(InpUseEMA) g_detectors[g_ndet++]=new CEma20MeanRev(InpEmaStretch,InpEmaAdxCeil,InpEmaMinRR,InpRiskPct);
   if(g_ndet==0) Print("WARNING: no detectors enabled.");

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviation);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   g_last_bar=iTime(_Symbol,g_tf,0);
   g_started =false;

   string mode=(auto_mode? (InpAutoApprove==AA_ALL?"AUTO-APPROVE(headless)":"AUTO-SKIP(headless)")
                         : "INTERACTIVE");
   Print("HybridForwardTest ACTIVE [",mode,"] on ",_Symbol," detectors=",g_ndet,
         " risk=",DoubleToString(InpRiskPct*100.0,1),"%");
   //--- build tag: if the popup misbehaves, confirm THIS line appears (fresh EA)
   //--- and that MT5 was restarted so the matching TradeDialog.dll is loaded.
   //--- the mid-trade panel needs the same footing as the interactive dialog
   //--- (real window + human): interactive mode, visual tester, DLLs allowed.
   //--- Headless AA_ALL/AA_SKIP runs never create a window.
   g_can_panel = (g_active && !auto_mode && in_visual && dll_ok && InpManagePanel);

   Print("HFT build 2026-08-07a: mid-trade management panel (Close / Close50 / SL->BE) +"
         " day-of-week signal time (needs matching TradeDialog.dll - restart MT5 after a rebuild).");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!g_active) return;

   //--- econ-event lines: parse the calendar once, then redraw on each new bar
   //--- (below) so the forward window rolls with the replay. ALWAYS load (the
   //--- approval popup's upcoming-events list needs g_ev_loaded, and the popup
   //--- goes to the operator, not the blind advisor) — InpShowEvents gates only
   //--- the on-CHART lines, which would leak event names into the advisor shot.
   if(!g_ev_loaded) { LoadEconEvents(); if(InpShowEvents) DrawEconEvents(); }

   if(!g_started)
     {
      g_started=true;
      g_start_time=TimeCurrent(); g_last_time=g_start_time;
      g_journal_part=StringFormat("journal\\%s_%s.part.csv",_Symbol,StampCompact(g_start_time));
      g_actions_part=StringFormat("journal\\%s_%s.actions.part.csv",_Symbol,StampCompact(g_start_time));
      WriteJournal(g_journal_part);
      if(InpShowSwings) DrawSwingMarkers();   // first draw (new-bar gate skips tick 1)
     }
   g_last_time=TimeCurrent();

   //--- two-target management runs EVERY tick (a bar can blow through TP1)
   ManageOpenPositions();
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

   //--- roll the econ-event overlay forward with the replay (reveals upcoming
   //--- events within the lookahead; bias stays hidden until each one fires)
   if(InpShowEvents) DrawEconEvents();
   //--- persistent swing markers over the rolling window (signal-independent)
   if(InpShowSwings) DrawSwingMarkers();

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
   g_rows[n].entry=cand.entry; g_rows[n].sl=cand.sl; g_rows[n].tp=cand.tp;
   g_rows[n].tp1=cand.tp1; g_rows[n].tp2=cand.tp2; g_rows[n].partial_frac=0.0;
   g_rows[n].lots=0.0; g_rows[n].risk_px=MathAbs(cand.entry-cand.sl);
   g_rows[n].decision="rejected"; g_rows[n].skip_reason=0; g_rows[n].edited=false;
   g_rows[n].is_pending=false; g_rows[n].order_ticket=0; g_rows[n].placed_time=0;
   g_rows[n].tp1_done=true; g_rows[n].decision_ms=0; g_rows[n].posid=0;
   g_rows[n].closed=true; g_rows[n].exit_time=0; g_rows[n].exit_price=0.0;
   g_rows[n].pnl=0.0; g_rows[n].r_multiple=0.0;
   Print("Signal #",id," ",cand.strategy," ",DirStr(cand.direction)," REJECTED: ",why);
   WriteJournal(g_journal_part);
  }

//+------------------------------------------------------------------+
//| Size -> overlays -> dialog -> execute (market or pending) -> log   |
//+------------------------------------------------------------------+
void HandleSignal(SignalCandidate &cand)
  {
   g_sig_seq++;
   int id=g_sig_seq;

   //--- ACCOUNT-SAFETY GATE (before sizing/overlays/dialog): a stop tighter than
   //--- the min distance is a degenerate signal - reject it, log it, journal it.
   //--- This is what catches the "1.9-pip stop -> 13-lot monster" class of bug.
   double atr_now=SignalATR();
   double stopdist=MathAbs(cand.entry-cand.sl);
   double minstop=MinStopDist(atr_now);
   if(stopdist<minstop || stopdist<=0.0)
     {
      double atrx=(atr_now>0.0? stopdist/atr_now : 0.0);
      JournalReject(id,cand,StringFormat("SL distance %s (%.2f ATR) < min %s - degenerate stop",
                    DoubleToString(stopdist,_Digits),atrx,DoubleToString(minstop,_Digits)));
      return;
     }

   double lots=SizeByRisk(cand.entry,cand.sl);

   DrawOverlays(id,cand);
   PruneOverlays(id);
   //--- scroll the chart HARD RIGHT to the latest bar before the dialog opens, so
   //--- the advisor's decision-time screenshot always shows the most recent bars
   //--- (incl. the trigger). Autoscroll can lag in the visual tester and leave the
   //--- last few bars off the right edge; CHART_END forces the current bar into view.
   ChartSetInteger(0,CHART_AUTOSCROLL,true);
   ChartNavigate(0,CHART_END,0);
   ChartRedraw(0);

   //--- snapshot the detector's PROPOSED levels BEFORE the dialog can edit them
   double orig_entry=cand.entry, orig_sl=cand.sl, orig_tp=cand.tp;
   double orig_tp1=cand.tp1, orig_tp2=cand.tp2;

   //--- real-time advisor delivery: write the blind numbers NOW (before the
   //--- dialog) so the OS-capture daemon can deliver the setup while the popup is
   //--- up, not only after the decision writes the main journal row.
   WritePendingSetup(id,cand,orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2);

   string caption=StringFormat("Signal #%d  -  %s  %s",id,cand.strategy,DirStr(cand.direction));
   long decision_ms=0; int skip_reason=0; bool entry_edited=false;
   bool approved=AskApproval(id,cand,lots,caption,decision_ms,skip_reason,entry_edited);
   //--- the dialog may have retuned Entry/SL/TP (R:R held) - re-size on the
   //--- final risk distance so the placed order + journal use edited levels.
   lots=SizeByRisk(cand.entry,cand.sl);
   //--- authoritative "operator edited a level" flag, taken from the COMMITTED
   //--- cand vs the detector's proposal BEFORE any fill overwrites cand.entry.
   //--- (Can't infer this in review from orig_entry vs entry: entry is later
   //--- overwritten by the realised fill, which ~never equals the proposal.)
   double etol=0.5*SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(etol<=0.0) etol=0.5*_Point;
   bool any_edited=(MathAbs(cand.entry-orig_entry)>etol
                    || MathAbs(cand.sl-orig_sl)>etol
                    || MathAbs(cand.tp-orig_tp)>etol
                    || MathAbs(cand.tp1-orig_tp1)>etol
                    || MathAbs(cand.tp2-orig_tp2)>etol);

   //--- for two-target strategies the order TP is the RUNNER (tp2); we bank
   //--- partial_fraction at tp1 en route and move SL to BE.
   bool two_target=(cand.partial_fraction>0.0 && cand.tp1>0.0 && cand.tp2>0.0);
   double order_tp=(two_target? cand.tp2 : cand.tp);

   int n=ArraySize(g_rows); ArrayResize(g_rows,n+1);
   g_rows[n].id=id; g_rows[n].time=cand.zone_to; g_rows[n].symbol=_Symbol;
   g_rows[n].strategy=cand.strategy; g_rows[n].direction=cand.direction;
   g_rows[n].orig_entry=orig_entry; g_rows[n].orig_sl=orig_sl; g_rows[n].orig_tp=orig_tp;
   g_rows[n].orig_tp1=orig_tp1; g_rows[n].orig_tp2=orig_tp2;
   g_rows[n].entry=cand.entry; g_rows[n].sl=cand.sl; g_rows[n].tp=order_tp;
   g_rows[n].tp1=cand.tp1; g_rows[n].tp2=cand.tp2; g_rows[n].partial_frac=(two_target?cand.partial_fraction:0.0);
   g_rows[n].lots=lots; g_rows[n].risk_px=MathAbs(cand.entry-cand.sl);
   g_rows[n].tp1_done=(!two_target); g_rows[n].closed_vol=0.0;
   g_rows[n].decision_ms=decision_ms; g_rows[n].skip_reason=0; g_rows[n].edited=any_edited;
   g_rows[n].is_pending=false; g_rows[n].order_ticket=0; g_rows[n].placed_time=0;
   g_rows[n].posid=0; g_rows[n].closed=false;
   g_rows[n].exit_time=0; g_rows[n].exit_price=0.0; g_rows[n].pnl=0.0; g_rows[n].r_multiple=0.0;

   if(approved)
     {
      g_rows[n].decision="approved";
      if(lots<=0.0)
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
         bool want_pending=(entry_edited && MathAbs(cand.entry-mkt)>gate);
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
               if(deal>0 && HistoryDealSelect(deal))
                 {
                  g_rows[n].posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
                  double fill=HistoryDealGetDouble(deal,DEAL_PRICE);
                  if(fill>0.0) { g_rows[n].entry=fill; g_rows[n].risk_px=MathAbs(fill-cand.sl); }
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
//| Per-tick two-target management: at TP1 bank a partial + move to BE |
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
      if(g_rows[i].closed || g_rows[i].tp1_done) continue;
      if(g_rows[i].partial_frac<=0.0 || g_rows[i].tp1<=0.0) continue;
      if(!PositionSelectByTicket((ulong)g_rows[i].posid)) continue;   // already gone

      int dir=g_rows[i].direction;
      bool reached=(dir>0 ? bid>=g_rows[i].tp1 : ask<=g_rows[i].tp1);
      if(!reached) continue;

      double lots=g_rows[i].lots;
      double pv=MathFloor((g_rows[i].partial_frac*lots)/step)*step;
      double be=PositionGetDouble(POSITION_PRICE_OPEN);
      if(pv>=vmin && (lots-pv)>=vmin)
        {
         if(g_trade.PositionClosePartial((ulong)g_rows[i].posid,pv))
            Print("Signal #",g_rows[i].id," TP1 hit -> banked ",DoubleToString(pv,2),
                  " lots, SL -> breakeven, runner to TP2");
         else
            Print("Signal #",g_rows[i].id," partial close FAILED: ",g_trade.ResultRetcode());
         g_trade.PositionModify((ulong)g_rows[i].posid,be,g_rows[i].tp2);
        }
      g_rows[i].tp1_done=true;
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

//--- is SL->BE currently placeable? PositionModify rejects an SL inside the
//--- broker's stops-level band, so the button is gated on it (otherwise the
//--- click silently does nothing).
bool BEPlaceable(int idx)
  {
   if(!PositionSelectByTicket((ulong)g_rows[idx].posid)) return false;
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
void WriteActions(string path)
  {
   if(path=="") return;
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE){ Print("WARNING: cannot open actions log '",path,"' err=",GetLastError()); return; }
   FileWriteString(h,"signal_id,posid,bar_time,action,price,lots_before,lots_after,sl_after,banked_r,open_r\n");
   for(int i=0;i<ArraySize(g_actions);i++)
     {
      ManualAction a=g_actions[i];
      FileWriteString(h,StringFormat("%d,%d,%s,%s,%s,%s,%s,%s,%.2f,%.2f\n",
         a.id,a.posid,TimeToString(a.bar_time,TIME_DATE|TIME_SECONDS),a.action,
         DoubleToString(a.price,_Digits),DoubleToString(a.lots_before,2),
         DoubleToString(a.lots_after,2),DoubleToString(a.sl_after,_Digits),
         a.banked_r,a.open_r));
     }
   FileFlush(h); FileClose(h);
  }

//--- append one manual-intervention row + rewrite the actions CSV.
void LogManualAction(int idx,string what,double price,double lots_before,
                     double lots_after,double sl_after)
  {
   int n=ArraySize(g_actions); ArrayResize(g_actions,n+1);
   g_actions[n].id=g_rows[idx].id; g_actions[n].posid=g_rows[idx].posid;
   g_actions[n].bar_time=iTime(_Symbol,g_tf,0); g_actions[n].action=what;
   g_actions[n].price=price; g_actions[n].lots_before=lots_before;
   g_actions[n].lots_after=lots_after; g_actions[n].sl_after=sl_after;
   g_actions[n].banked_r=g_rows[idx].r_multiple; g_actions[n].open_r=OpenR(idx);
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
   g_rows[idx].tp1_done=true;   // manual scale-out replaces the auto one (no double-close)
   if(g_trade.PositionClosePartial((ulong)g_rows[idx].posid,pv))
      Print("Signal #",g_rows[idx].id," MANUAL CLOSE 50% -> banked ",DoubleToString(pv,2),
            " lots (auto scale-out disabled)");
   else
      Print("Signal #",g_rows[idx].id," manual 50% FAILED: ",g_trade.ResultRetcode());
  }

//--- manual SL->break-even (+ pad). NEVER writes g_rows[].sl - that is the risk
//--- basis for every R number; only the position's live stop moves. TP is read
//--- back + passed unchanged (no mid-trade TP moves). tp1_done is left alone so
//--- the auto scale-out (which also moves SL->BE at TP1) still runs.
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
   s+=StringFormat("%s  %s   #%d\r\n",g_rows[idx].strategy,DirStr(g_rows[idx].direction),g_rows[idx].id);
   s+=StringFormat("Lots: %s  (init %s)\r\n",DoubleToString(vol,2),DoubleToString(g_rows[idx].lots,2));
   s+=StringFormat("Entry: %s\r\n",DoubleToString(g_rows[idx].entry,_Digits));
   s+=StringFormat("SL: %s    TP: %s\r\n",DoubleToString(psl,_Digits),DoubleToString(ptp,_Digits));
   s+=StringFormat("Open R: %+.2f    Banked R: %+.2f\r\n",oR,bR);
   s+=StringFormat("Close-now total: %+.2f R\r\n",oR+bR);
   s+=(be_ok? "Ready." : "SL->BE unavailable (too close to market)");
   return s;
  }

//+------------------------------------------------------------------+
//| Per-tick panel driver: open when a position appears, drain+execute |
//| a confirmed button action, refresh state, tear down when flat.     |
//+------------------------------------------------------------------+
void ManagePanelTick()
  {
   int idx=ActiveRowIdx();
   if(idx<0)
     {
      if(g_panel_open){ TDM_Close(); g_panel_open=false; }
      return;
     }
   static bool warned=false;
   if(!g_panel_open)
     {
      if(TDM_Open("Manage position")==1) { g_panel_open=true; warned=false; }
      else                               // window not up yet; retry next tick
        {
         if(!warned){ Print("Management panel not up yet (retrying) - if this persists the DLL window failed to create."); warned=true; }
         return;
        }
     }

   //--- drain a CONFIRMED button press and execute it on THIS (MQL) thread
   int act=TDM_Poll();
   if(act==1)      ManualClose(idx);
   else if(act==2) ManualClose50(idx);
   else if(act==3) ManualBE(idx);

   //--- a full close / final exit may have flattened us this tick
   idx=ActiveRowIdx();
   if(idx<0){ TDM_Close(); g_panel_open=false; return; }

   //--- refresh at most once per tester-clock SECOND (or immediately after an
   //--- action): pushing every tick under model-1 fast-forward floods the panel
   //--- thread with repaints and makes it sluggish exactly when a click is due.
   static datetime last_push=0;
   datetime now=TimeCurrent();
   if(now!=last_push || act!=0)
     {
      bool be_ok=BEPlaceable(idx);
      double vol=(PositionSelectByTicket((ulong)g_rows[idx].posid)? PositionGetDouble(POSITION_VOLUME):0.0);
      TDM_Update(PanelStateText(idx,be_ok),vol,be_ok?1:0);
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
                     "orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2\r\n");
   FileWriteString(h,StringFormat("%d,%s,%s,%s,%s,%s,%s,%s,%s,%s\r\n",
      id,TimeToString(cand.zone_to,TIME_DATE|TIME_MINUTES),_Symbol,cand.strategy,
      (cand.direction>0?"BUY":"SELL"),
      DoubleToString(oe,_Digits),DoubleToString(osl,_Digits),DoubleToString(ot,_Digits),
      DoubleToString(ot1,_Digits),DoubleToString(ot2,_Digits)));
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
bool InteractiveDialog(int id,SignalCandidate &cand,string caption,string plan,
                       int &skip_reason,bool &entry_edited)
  {
   skip_reason=0; entry_edited=false;
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
   //--- signal fire time: day-of-week + HH:MM only (no date -> coach-safe),
   //--- labelled UTC (.dk symbols are Dukascopy UTC data; econ overlay confirms).
   MqlDateTime sdt; TimeToStruct(cand.zone_to,sdt);
   string dows[]={"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
   string sigtime=StringFormat("%s  %s UTC",dows[sdt.day_of_week],TimeToString(cand.zone_to,TIME_MINUTES));
   if(TD_Open(caption,_Symbol,stratArg,DirStr(dir),sigtime,
              DoubleToString(ce,_Digits),DoubleToString(cs,_Digits),DoubleToString(c1,_Digits),tp2str,
              LotsLine(lots0,ce,cs,atr),rrs)!=1)
     {
      Print("Signal #",id," dialog failed to open - fail-closed to SKIP.");
      return false;
     }
   //--- decision-time chart snapshot (overlays already drawn; dialog not in shot)
   DecisionScreenshot(id);
   //--- popup upcoming-events list (both date forms for the coach-mode toggle)
   string ev_abs="",ev_rel="";
   if(g_ev_loaded) BuildEventBlocks(cand.zone_to,ev_abs,ev_rel);
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
                 int &skip_reason,bool &entry_edited)
  {
   skip_reason=0; entry_edited=false;
   //--- headless automated verification: no DLL, no modal (never edits entry)
   if(InpAutoApprove==AA_ALL)  { decision_ms=0; return true;  }
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
      yes=InteractiveDialog(id,cand,caption,plan,skip_reason,entry_edited);
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
   double lots=LotsForRisk(_Symbol,entry,sl,InpRiskPct);   // shared math (floored, no clamp)
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

//--- keep only the newest InpMaxVisibleSignals setups' overlays on the chart;
//--- delete older setups by their per-signal object prefix. Econ-event objects
//--- use a different prefix (HFT_EVT*/HFT_EVTL*) and are unaffected.
void PruneOverlays(int id)
  {
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
                  "gdp","pmi","unemployment rate","ecb press",
                  "monetary policy","press conference","interest rate",
                  "rate statement","bank rate"};
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
                "non-farm","nonfarm","cpi","gdp"};
   for(int i=0;i<ArraySize(hi);i++) if(StringFind(e,hi[i])>=0) return 2;
   string md[]={"pmi","unemployment","retail sales","ppi","employment change",
                "jobless","confidence","durable goods","ism","trade balance",
                "core"};
   for(int i=0;i<ArraySize(md);i++) if(StringFind(e,md[i])>=0) return 1;
   return 0;
  }
string SigTag(int s){ return (s>=2 ? "[HIGH]" : (s==1 ? "[MED]" : "[LOW]")); }
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
   int h=FileOpen("econ_events.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
   if(h==INVALID_HANDLE)
     { Print("econ_events.csv not in Common\\Files - no event lines (err ",GetLastError(),")"); return; }
   string base,quote; SymbolCcy(base,quote);
   for(int k=0;k<6 && !FileIsEnding(h);k++) FileReadString(h);   // skip header
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
      if(ccy!=base && ccy!=quote) continue;      // only THIS pair's currencies
      datetime t=StringToTime(sdt);
      if(t<=0) continue;
      int m=ArraySize(g_ev_t);
      ArrayResize(g_ev_t,m+1); ArrayResize(g_ev_ccy,m+1); ArrayResize(g_ev_name,m+1);
      ArrayResize(g_ev_cb,m+1); ArrayResize(g_ev_top,m+1);
      g_ev_t[m]=t; g_ev_ccy[m]=ccy; g_ev_name[m]=ev; g_ev_cb[m]=cb;
      g_ev_top[m]=IsTopTierEvent(ev);
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
      int rel=(g_ev_ccy[i]==base?1:(g_ev_ccy[i]==quote?-1:0));
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
         string txt=StringFormat(" %s %s%s",g_ev_ccy[i],g_ev_name[i],tag);
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
      if(g_ev_ccy[i]!=base && g_ev_ccy[i]!=quote) continue;
      long ds=(long)(t-sig);
      int dd=(int)(ds/86400), hh=(int)((ds%86400)/3600);
      string nm=StringFormat("%s %s  %s",g_ev_ccy[i],g_ev_name[i],SigTag(EventSignificance(g_ev_name[i])));
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
   int idx=-1;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].posid==posid && !g_rows[i].closed) { idx=i; break; }
   if(idx<0) return;

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
      Print("Signal #",g_rows[idx].id," CLOSED  blendedR=",DoubleToString(g_rows[idx].r_multiple,2),
            "  pnl=",DoubleToString(g_rows[idx].pnl,2));
     }
   else
      Print("Signal #",g_rows[idx].id," partial exit ",DoubleToString(dvol,2),
            " @ ",DoubleToString(dprice,_Digits)," (Rsofar=",DoubleToString(g_rows[idx].r_multiple,2),")");
   WriteJournal(g_journal_part);
  }

//+------------------------------------------------------------------+
void WriteJournal(string path)
  {
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE) { Print("WARNING: cannot open journal '",path,"' err=",GetLastError()); return; }
   FileWriteString(h,
      "signal_id,signal_time,symbol,strategy,direction,"
      "orig_entry,orig_sl,orig_tp,orig_tp1,orig_tp2,entry,sl,tp,tp1,tp2,partial_frac,lots,"
      "decision,skip_reason,edited,is_pending,decision_ms,posid,tp1_done,"
      "exit_time,exit_price,pnl,r_multiple\n");
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      JournalRow r=g_rows[i];
      string line=StringFormat(
         "%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%.2f,%s,%s,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s\n",
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
         (r.closed?DoubleToString(r.r_multiple,2):""));
      FileWriteString(h,line);
     }
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
   //--- tear the management panel down first (joins its thread) so nothing
   //--- outlives the test run.
   if(g_panel_open){ TDM_Close(); g_panel_open=false; }
   if(g_active && g_started)
     {
      string finalp=StringFormat("journal\\%s_%s_%s.csv",
                                 _Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
      WriteJournal(finalp);
      if(g_journal_part!="" && g_journal_part!=finalp) FileDelete(g_journal_part,FILE_COMMON);
      Print("Journal finalised: <Terminal>\\Common\\Files\\",finalp," (",ArraySize(g_rows)," signals)");
      //--- finalise the manual-actions log alongside the journal (if any fired)
      if(ArraySize(g_actions)>0)
        {
         string af=StringFormat("journal\\%s_%s_%s.actions.csv",
                                _Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
         WriteActions(af);
         if(g_actions_part!="" && g_actions_part!=af) FileDelete(g_actions_part,FILE_COMMON);
         Print("Manual-actions log finalised: <Terminal>\\Common\\Files\\",af," (",ArraySize(g_actions)," actions)");
        }
      if(InpCleanupOnDeinit) ObjectsDeleteAll(0,InpObjPrefix);
     }
   for(int i=0;i<g_ndet;i++)
      if(CheckPointer(g_detectors[i])==POINTER_DYNAMIC) { delete g_detectors[i]; g_detectors[i]=NULL; }
   g_ndet=0;
  }
//+------------------------------------------------------------------+
