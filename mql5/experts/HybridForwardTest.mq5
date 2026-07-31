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

//--- coloured dialog + plain fallback (both early-bound; see docs)
#import "TradeDialog.dll"
int ShowTradeDialog(string title,string symbol,string strategy,string direction,
                    string entry,string sl,string tp,string lots,string rr);
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
//--- readability / decluttering
input int    InpMaxVisibleSignals = 1;        // recent setups whose overlays stay on chart (older auto-clear)
input bool   InpEventTopTierOnly  = true;     // events: only top-tier movers (rate/CPI/NFP/GDP/PMI)
input bool   InpShowSwings  = true;           // draw swing high/low markers
input bool   InpShowAux     = true;           // draw aux level lines + labels
input bool   InpShowFib     = true;           // draw the native Fib grid
input bool   InpFibRay      = true;           // extend fib levels right across the chart
input int    InpFontSize    = 10;             // overlay label font size (bigger = more readable)
input int    InpLineWidth   = 2;              // overlay line width (thicker = more readable)

//--- globals
CTrade         g_trade;
ISignalDetector *g_detectors[3];           // priority order: [0]=SMC,[1]=Fib,[2]=EMA
int            g_ndet       = 0;
ENUM_TIMEFRAMES g_tf        = PERIOD_H4;
bool            g_events_drawn = false;      // econ-event lines drawn once (first tick)
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
   string   decision;       // approved / denied
   long     decision_ms;
   long     posid;
   bool     closed;
   datetime exit_time;
   double   exit_price;
   double   pnl;
   double   r_multiple;     // blended, volume-weighted
  };
JournalRow g_rows[];

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
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!g_active) return;

   //--- draw the high-impact economic-event lines once, on the first tick
   if(InpShowEvents && !g_events_drawn) { DrawEconEvents(); g_events_drawn=true; }

   if(!g_started)
     {
      g_started=true;
      g_start_time=TimeCurrent(); g_last_time=g_start_time;
      g_journal_part=StringFormat("journal\\%s_%s.part.csv",_Symbol,StampCompact(g_start_time));
      WriteJournal(g_journal_part);
     }
   g_last_time=TimeCurrent();

   //--- two-target management runs EVERY tick (a bar can blow through TP1)
   ManageOpenPositions();

   //--- new-bar gate: detection only when a fresh H4 bar has closed
   datetime bar0=iTime(_Symbol,g_tf,0);
   if(bar0==g_last_bar) return;
   g_last_bar=bar0;

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
   if(HasOpenPosition())
     {
      Print("Signal from ",best.strategy," suppressed - a position is already active (one setup/symbol).");
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

//+------------------------------------------------------------------+
//| Size -> overlays -> modal -> execute (two-target aware) -> log     |
//+------------------------------------------------------------------+
void HandleSignal(SignalCandidate &cand)
  {
   g_sig_seq++;
   int id=g_sig_seq;
   double lots=SizeByRisk(cand.entry,cand.sl);

   DrawOverlays(id,cand);
   PruneOverlays(id);
   ChartRedraw(0);

   string caption=StringFormat("Signal #%d  -  %s  %s",id,cand.strategy,DirStr(cand.direction));
   long decision_ms=0;
   bool approved=AskApproval(id,cand,lots,caption,decision_ms);

   //--- for two-target strategies the order TP is the RUNNER (tp2); we bank
   //--- partial_fraction at tp1 en route and move SL to BE.
   bool two_target=(cand.partial_fraction>0.0 && cand.tp1>0.0 && cand.tp2>0.0);
   double order_tp=(two_target? cand.tp2 : cand.tp);

   int n=ArraySize(g_rows); ArrayResize(g_rows,n+1);
   g_rows[n].id=id; g_rows[n].time=cand.zone_to; g_rows[n].symbol=_Symbol;
   g_rows[n].strategy=cand.strategy; g_rows[n].direction=cand.direction;
   g_rows[n].entry=cand.entry; g_rows[n].sl=cand.sl; g_rows[n].tp=order_tp;
   g_rows[n].tp1=cand.tp1; g_rows[n].tp2=cand.tp2; g_rows[n].partial_frac=(two_target?cand.partial_fraction:0.0);
   g_rows[n].lots=lots; g_rows[n].risk_px=MathAbs(cand.entry-cand.sl);
   g_rows[n].tp1_done=(!two_target); g_rows[n].closed_vol=0.0;
   g_rows[n].decision_ms=decision_ms; g_rows[n].posid=0; g_rows[n].closed=false;
   g_rows[n].exit_time=0; g_rows[n].exit_price=0.0; g_rows[n].pnl=0.0; g_rows[n].r_multiple=0.0;

   if(approved)
     {
      g_rows[n].decision="approved";
      if(lots<=0.0)
         Print("Signal #",id," approved but lots<=0 - NOT placing (check ",_Symbol," specs).");
      else
        {
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
            //--- can't-split guard (exactly-min-lot): fall back to single target
            if(two_target)
              {
               double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP); if(step<=0)step=0.01;
               double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN); if(vmin<=0)vmin=0.01;
               double pv=MathFloor((cand.partial_fraction*lots)/step)*step;
               if(pv<vmin || (lots-pv)<vmin)
                 { g_rows[n].tp1_done=true; g_rows[n].partial_frac=0.0;
                   Print("Signal #",id," min-lot: cannot scale out - single target to TP2."); }
              }
            Print("Signal #",id," APPROVED -> ",cand.strategy," ",DirStr(cand.direction)," ",
                  DoubleToString(lots,2)," lots posid=",g_rows[n].posid,
                  (two_target?"  [scale-out TP1/TP2]":"  [single TP]"));
           }
         else
            Print("Signal #",id," order FAILED: ",g_trade.ResultRetcode()," ",
                  g_trade.ResultRetcodeDescription());
        }
     }
   else
     {
      g_rows[n].decision="denied";
      Print("Signal #",id," ",cand.strategy," DENIED (decided in ",decision_ms," ms)");
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
      if(g_rows[i].decision!="approved" || g_rows[i].posid<=0) continue;
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
//| Modal / auto-approve. Returns true on approve.                    |
//+------------------------------------------------------------------+
bool AskApproval(int id,SignalCandidate &cand,double lots,string caption,long &decision_ms)
  {
   //--- headless automated verification: no DLL, no modal
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
      string stratArg=cand.strategy+(cand.d1_context?"  [D1 aligned]":"");
      if(cand.comment!="") stratArg+=" - "+cand.comment;
      int r=ShowTradeDialog(caption,_Symbol,stratArg,DirStr(cand.direction),
               DoubleToString(cand.entry,_Digits),DoubleToString(cand.sl,_Digits),
               DoubleToString(cand.tp,_Digits),
               StringFormat("%s  (%.1f%% risk)",DoubleToString(lots,2),InpRiskPct*100.0),
               StringFormat("1 : %s%s",DoubleToString(cand.rr,2),plan));
      yes=(r==1);
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
     }
   decision_ms=(long)(GetTickCount()-t0);
   return(yes);
  }

//+------------------------------------------------------------------+
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
          return(vmin); }
      return(0.0);
     }
   return(lots);
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
   DrawHLine(p+"entry",c.entry,C'0,160,0');
   DrawHLine(p+"sl",   c.sl,   C'204,0,0');
   DrawHLine(p+"tp",   c.tp,   C'0,0,204');

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
   //--- generic confirmed-swing markers, labelled "swing high" / "swing low"
   if(InpShowSwings) DrawSwings(p,c);
   //--- FVG / price-gap / tick-volume imbalance highlights (display-only context)
   if(InpShowImbal) DrawImbalances(p,c);
   //--- corner label
   string tx=p+"label";
   double anchor=(c.direction>0 ? c.zone_hi : c.zone_lo);
   if(ObjectCreate(0,tx,OBJ_TEXT,0,c.zone_to,anchor))
     {
      string txt=StringFormat("#%d %s %s%s @ %s",id,c.strategy,DirStr(c.direction),
                              (c.d1_context?" [D1]":""),TimeToString(c.zone_to,TIME_DATE|TIME_MINUTES));
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
                  "gdp","pmi","unemployment rate","ecb press"};
   for(int i=0;i<ArraySize(keys);i++) if(StringFind(e,keys[i])>=0) return true;
   return false;
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
void DrawEconEvents()
  {
   int h=FileOpen("econ_events.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
   if(h==INVALID_HANDLE)
     { Print("econ_events.csv not in Common\\Files - no event lines (err ",GetLastError(),")"); return; }

   string base,quote; SymbolCcy(base,quote);
   int bars=Bars(_Symbol,g_tf);
   if(bars<2){ FileClose(h); return; }
   datetime tmin=iTime(_Symbol,g_tf,bars-1);
   datetime tmax=iTime(_Symbol,g_tf,0)+PeriodSeconds(g_tf);

   for(int k=0;k<6 && !FileIsEnding(h);k++) FileReadString(h);   // skip header

   int n=0,drawn=0;
   while(!FileIsEnding(h) && drawn<InpEvtMaxDraw)
     {
      string sdt=FileReadString(h);
      if(sdt==""){ if(FileIsEnding(h)) break; else continue; }
      string ccy=FileReadString(h);
      string ev =FileReadString(h);
      string act=FileReadString(h);
      string fc =FileReadString(h);
      int    cb =(int)StringToInteger(FileReadString(h));
      n++;

      datetime t=StringToTime(sdt);
      if(t<=0 || t<tmin || t>tmax) continue;
      int rel=0;
      if(ccy==base) rel=1; else if(ccy==quote) rel=-1; else continue;
      if(InpEventTopTierOnly && !IsTopTierEvent(ev)) continue;
      int tb=cb*rel;
      color clr=(tb>0?InpEvtBull:(tb<0?InpEvtBear:InpEvtNeutral));
      string tag=(tb>0?" [BULL]":(tb<0?" [BEAR]":""));

      string vn=StringFormat("%sEVT_%d",InpObjPrefix,n);
      if(ObjectCreate(0,vn,OBJ_VLINE,0,t,0))
        {
         ObjectSetInteger(0,vn,OBJPROP_COLOR,clr);
         ObjectSetInteger(0,vn,OBJPROP_STYLE,STYLE_DASH);
         ObjectSetInteger(0,vn,OBJPROP_WIDTH,1);
         ObjectSetInteger(0,vn,OBJPROP_BACK,true);
         ObjectSetInteger(0,vn,OBJPROP_SELECTABLE,false);
        }
      int sh=iBarShift(_Symbol,g_tf,t,false);
      double py=(sh>=0? iClose(_Symbol,g_tf,sh) : 0.0);
      if(py>0.0)
        {
         string tn=StringFormat("%sEVTL_%d",InpObjPrefix,n);
         string txt=StringFormat(" %s %s%s",ccy,ev,tag);
         if(act!="") txt+=StringFormat("  (a:%s f:%s)",act,fc);
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
   FileClose(h);
   PrintFormat("econ events: drew %d high-impact lines for %s (base=%s quote=%s), scanned %d rows",
               drawn,_Symbol,base,quote,n);
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
      "signal_id,signal_time,symbol,strategy,direction,entry,sl,tp,tp1,tp2,partial_frac,"
      "lots,decision,decision_ms,posid,tp1_done,exit_time,exit_price,pnl,r_multiple\n");
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      JournalRow r=g_rows[i];
      string line=StringFormat("%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%.2f,%s,%s,%d,%d,%s,%s,%s,%s,%s\n",
         r.id,TimeToString(r.time,TIME_DATE|TIME_SECONDS),r.symbol,r.strategy,DirStr(r.direction),
         DoubleToString(r.entry,_Digits),DoubleToString(r.sl,_Digits),DoubleToString(r.tp,_Digits),
         (r.tp1>0?DoubleToString(r.tp1,_Digits):""),(r.tp2>0?DoubleToString(r.tp2,_Digits):""),
         r.partial_frac,DoubleToString(r.lots,2),r.decision,r.decision_ms,r.posid,
         (r.tp1_done?1:0),
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
   if(g_active && g_started)
     {
      string finalp=StringFormat("journal\\%s_%s_%s.csv",
                                 _Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
      WriteJournal(finalp);
      if(g_journal_part!="" && g_journal_part!=finalp) FileDelete(g_journal_part,FILE_COMMON);
      Print("Journal finalised: <Terminal>\\Common\\Files\\",finalp," (",ArraySize(g_rows)," signals)");
      if(InpCleanupOnDeinit) ObjectsDeleteAll(0,InpObjPrefix);
     }
   for(int i=0;i<g_ndet;i++)
      if(CheckPointer(g_detectors[i])==POINTER_DYNAMIC) { delete g_detectors[i]; g_detectors[i]=NULL; }
   g_ndet=0;
  }
//+------------------------------------------------------------------+
