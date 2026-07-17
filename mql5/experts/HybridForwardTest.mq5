//+------------------------------------------------------------------+
//|                                          HybridForwardTest.mq5    |
//|        FTMO Hybrid Trading System - Phase 2 interactive harness   |
//|                                                                  |
//|  A VISUAL-TESTER-ONLY harness that lets the account owner live    |
//|  through the approve/deny decision workflow on EURUSD.dk before   |
//|  the real strategies are wired in.                               |
//|                                                                  |
//|  On each signal (dummy: first H4 bar of every Monday, alternating |
//|  buy/sell) it: draws entry/SL/TP lines + a setup-zone rectangle + |
//|  a label, then shows a SYSTEM-MODAL Yes/No box via user32.dll     |
//|  MessageBoxW (native MQL5 MessageBox does NOT work in the tester).|
//|  Yes -> size at 1% risk and place the order; No -> journal a skip.|
//|  Every signal + outcome is appended to a crash-safe CSV journal.  |
//|                                                                  |
//|  HARD RULE: the DLL is called ONLY when running in the visual     |
//|  tester (MQL_TESTER && MQL_VISUAL_MODE). Anywhere else the EA      |
//|  prints an error and stays inert - it never touches user32.dll.   |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "1.00"
#property description "Phase 2 visual-tester approve/deny harness (dummy signal) for EURUSD.dk."

#include <Trade\Trade.mqh>
#include <Hybrid\Signal.mqh>

//--- user32.dll: the ONLY way to get a blocking modal inside the tester
#import "user32.dll"
int MessageBoxW(long hWnd,string lpText,string lpCaption,uint uType);
#import

//--- MessageBox flags / return codes
#define MB_YESNO        0x00000004
#define MB_ICONQUESTION 0x00000020
#define MB_SYSTEMMODAL  0x00001000
#define IDYES           6
#define IDNO            7

//--- inputs
input double InpRiskPct     = 0.01;     // risk per trade (fraction of equity)
input int    InpLookback    = 5;        // setup-zone lookback (bars)
input double InpRR          = 2.0;      // reward:risk of the dummy signal
input long   InpMagic       = 990217;   // magic number
input int    InpDeviation   = 50;       // max slippage (points)
input bool   InpCleanupOnDeinit = false;// delete overlay objects when the EA is removed
input string InpObjPrefix   = "HFT_";   // chart-object name prefix

//--- globals
CTrade         g_trade;
ISignalDetector *g_detector = NULL;
ENUM_TIMEFRAMES g_tf        = PERIOD_H4;   // dummy signal timeframe
bool           g_active     = false;       // true only in a DLL-enabled visual tester
bool           g_started    = false;       // journal/start time set on the first tick
datetime       g_last_bar   = 0;           // last seen H4 bar time (new-bar detection)
datetime       g_start_time = 0;           // sim start (for journal filename)
datetime       g_last_time  = 0;           // latest sim time seen (for journal filename)
int            g_sig_seq    = 0;           // running signal id
string         g_journal_part= "";         // in-progress journal path (renamed at deinit)

//--- The journal is written with FILE_COMMON so it lands in a STABLE, findable
//--- place during testing: <Terminal>\Common\Files\journal\ (usually
//--- C:\Users\<user>\AppData\Roaming\MetaQuotes\Terminal\Common\Files\journal\).
//--- Without FILE_COMMON the tester sandboxes it under the hidden per-agent
//--- Tester\Agent-*\MQL5\Files\ dir where QA would never find it.

//--- one row per signal; outcome fields filled on close
struct JournalRow
  {
   int      id;
   datetime time;
   string   symbol;
   string   strategy;
   int      direction;
   double   entry;
   double   sl;
   double   tp;
   double   lots;
   string   decision;      // approved / denied
   long     decision_ms;   // wall-clock ms the human took to decide
   long     posid;         // position id if approved (0 otherwise)
   bool     closed;
   datetime exit_time;
   double   exit_price;
   double   pnl;
   double   r_multiple;
  };
JournalRow g_rows[];

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
string DirStr(int d) { return (d>0 ? "BUY" : "SELL"); }

string StampCompact(datetime t)
  {
   MqlDateTime dt; TimeToStruct(t,dt);
   return StringFormat("%04d%02d%02d",dt.year,dt.mon,dt.day);
  }

//+------------------------------------------------------------------+
//| Initialisation - decide whether the harness may run at all        |
//+------------------------------------------------------------------+
int OnInit()
  {
   bool in_tester = (bool)MQLInfoInteger(MQL_TESTER);
   bool in_visual = (bool)MQLInfoInteger(MQL_VISUAL_MODE);
   bool dll_ok    = (bool)MQLInfoInteger(MQL_DLLS_ALLOWED);

   if(!in_tester || !in_visual)
     {
      Print("HybridForwardTest is a VISUAL-TESTER-ONLY harness. It will not run a ",
            "modal outside Strategy Tester visual mode - staying INERT (no DLL, no trades).");
      Print("  MQL_TESTER=",in_tester,"  MQL_VISUAL_MODE=",in_visual);
      g_active=false;
      return(INIT_SUCCEEDED);   // load but do nothing
     }
   if(!dll_ok)
     {
      Print("ERROR: DLL imports are DISABLED. Enable 'Allow DLL imports' in the EA's ",
            "tester settings (and Tools->Options->Expert Advisors). Staying INERT.");
      g_active=false;
      return(INIT_SUCCEEDED);
     }

   g_active=true;
   g_detector=new CDummyDetector(InpLookback,InpRR);

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviation);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   g_last_bar  =iTime(_Symbol,g_tf,0);
   g_started   =false;   // journal + start time initialised on the first tick

   Print("HybridForwardTest ACTIVE (visual tester). Symbol=",_Symbol,
         " detector=",g_detector.Name()," risk=",DoubleToString(InpRiskPct*100.0,1),"%");
   Print("Tip: press the PAUSE key while the modal is up to keep the tester paused ",
         "after you answer (lets you inspect the chart before it resumes).");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Per-tick - new-bar detection then dummy signal check              |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!g_active)
      return;

   //--- first tick: sim time is now reliable -> set start + open the journal
   if(!g_started)
     {
      g_started=true;
      g_start_time=TimeCurrent();
      g_last_time =g_start_time;
      g_journal_part=StringFormat("journal\\%s_%s.part.csv",_Symbol,StampCompact(g_start_time));
      WriteJournal(g_journal_part);   // header, so even a 0-signal run leaves a file
     }

   g_last_time=TimeCurrent();

   datetime bar0=iTime(_Symbol,g_tf,0);
   if(bar0==g_last_bar)
      return;                 // still inside the current H4 bar
   g_last_bar=bar0;           // a new H4 bar has opened

   SignalCandidate cand;
   if(!g_detector.Detect(_Symbol,g_tf,cand) || !cand.valid)
      return;

   HandleSignal(cand);
  }

//+------------------------------------------------------------------+
//| Full signal handling: size -> overlays -> modal -> execute -> log |
//+------------------------------------------------------------------+
void HandleSignal(SignalCandidate &cand)
  {
   g_sig_seq++;
   int id=g_sig_seq;
   double lots=SizeByRisk(cand.entry,cand.sl);

   //--- overlays first, so the frozen chart shows full context
   DrawOverlays(id,cand);
   ChartRedraw(0);

   //--- build the decision context string
   string caption=StringFormat("Signal #%d  -  %s  %s",id,cand.strategy,DirStr(cand.direction));
   string body=StringFormat(
      "Symbol:      %s\n"
      "Strategy:    %s\n"
      "Direction:   %s\n"
      "Time:        %s\n\n"
      "Entry:       %s\n"
      "Stop Loss:   %s\n"
      "Take Profit: %s\n"
      "R:R:         1 : %s\n\n"
      "Lot size:    %s   (%.1f%% equity risk)\n\n"
      "Place this trade?",
      _Symbol,cand.strategy,DirStr(cand.direction),
      TimeToString(cand.zone_to,TIME_DATE|TIME_MINUTES),
      DoubleToString(cand.entry,_Digits),
      DoubleToString(cand.sl,_Digits),
      DoubleToString(cand.tp,_Digits),
      DoubleToString(cand.rr,2),
      DoubleToString(lots,2),InpRiskPct*100.0);

   //--- SYSTEM-MODAL blocking box (tester thread is frozen until answered)
   uint t0=GetTickCount();
   int res=MessageBoxW(0,body,caption,MB_YESNO|MB_ICONQUESTION|MB_SYSTEMMODAL);
   long decision_ms=(long)(GetTickCount()-t0);

   //--- record the row
   int n=ArraySize(g_rows);
   ArrayResize(g_rows,n+1);
   g_rows[n].id         =id;
   g_rows[n].time       =cand.zone_to;
   g_rows[n].symbol     =_Symbol;
   g_rows[n].strategy   =cand.strategy;
   g_rows[n].direction  =cand.direction;
   g_rows[n].entry      =cand.entry;
   g_rows[n].sl         =cand.sl;
   g_rows[n].tp         =cand.tp;
   g_rows[n].lots       =lots;
   g_rows[n].decision_ms=decision_ms;
   g_rows[n].posid      =0;
   g_rows[n].closed     =false;
   g_rows[n].exit_time  =0;
   g_rows[n].exit_price =0.0;
   g_rows[n].pnl        =0.0;
   g_rows[n].r_multiple =0.0;

   if(res==IDYES)
     {
      g_rows[n].decision="approved";
      if(lots<=0.0)
        {
         Print("Signal #",id," approved but computed lot size <= 0 - NOT placing. ",
               "Check symbol specs (tick value/step) on ",_Symbol,".");
        }
      else
        {
         bool ok=(cand.direction>0)
                 ? g_trade.Buy(lots,_Symbol,0.0,cand.sl,cand.tp,caption)
                 : g_trade.Sell(lots,_Symbol,0.0,cand.sl,cand.tp,caption);
         if(ok)
           {
            ulong deal=g_trade.ResultDeal();
            if(deal>0 && HistoryDealSelect(deal))
               g_rows[n].posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
            Print("Signal #",id," APPROVED -> ",DirStr(cand.direction)," ",
                  DoubleToString(lots,2)," lots, posid=",g_rows[n].posid,
                  " (decided in ",decision_ms," ms)");
           }
         else
            Print("Signal #",id," approved but order FAILED: ret=",
                  g_trade.ResultRetcode()," ",g_trade.ResultRetcodeDescription());
        }
     }
   else
     {
      g_rows[n].decision="denied";
      Print("Signal #",id," DENIED - no order placed (decided in ",decision_ms," ms)");
     }

   WriteJournal(g_journal_part);   // flushed inside
  }

//+------------------------------------------------------------------+
//| 1%-risk position sizing from SL distance, read from symbol specs  |
//+------------------------------------------------------------------+
double SizeByRisk(double entry,double sl)
  {
   double equity  =AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_cash=equity*InpRiskPct;

   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tick_val =SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double sl_dist  =MathAbs(entry-sl);
   if(tick_size<=0.0 || tick_val<=0.0 || sl_dist<=0.0)
     {
      Print("WARNING: cannot size - tick_size=",tick_size," tick_value=",tick_val,
            " sl_dist=",sl_dist," (verify ",_Symbol," specs).");
      return(0.0);
     }

   double loss_per_lot=(sl_dist/tick_size)*tick_val;
   if(loss_per_lot<=0.0)
      return(0.0);
   double lots=risk_cash/loss_per_lot;

   //--- normalise to the symbol's volume constraints
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   if(step<=0.0) step=0.01;
   lots=MathFloor(lots/step)*step;
   if(vmin>0.0 && lots<vmin)
     {
      Print("NOTE: risk-based lots below volume min (",DoubleToString(vmin,2),
            ") - clamping up; this trade risks > ",DoubleToString(InpRiskPct*100.0,1),"%.");
      lots=vmin;
     }
   if(vmax>0.0 && lots>vmax)
      lots=vmax;

   int vol_digits=(step<1.0 ? (int)MathCeil(-MathLog10(step)) : 0);
   return(NormalizeDouble(lots,vol_digits));
  }

//+------------------------------------------------------------------+
//| Draw entry/SL/TP lines, setup-zone rectangle and a label          |
//| Objects are prefixed with the signal id and PERSIST after the     |
//| decision (cleanup optional, at deinit).                          |
//+------------------------------------------------------------------+
void DrawOverlays(int id,SignalCandidate &c)
  {
   string p=StringFormat("%s%d_",InpObjPrefix,id);

   //--- setup zone rectangle
   string rz=p+"zone";
   if(ObjectCreate(0,rz,OBJ_RECTANGLE,0,c.zone_from,c.zone_hi,c.zone_to,c.zone_lo))
     {
      ObjectSetInteger(0,rz,OBJPROP_COLOR,clrSlateGray);
      ObjectSetInteger(0,rz,OBJPROP_BACK,true);
      ObjectSetInteger(0,rz,OBJPROP_FILL,true);
      ObjectSetInteger(0,rz,OBJPROP_STYLE,STYLE_DOT);
     }

   //--- entry / SL / TP horizontal lines
   DrawHLine(p+"entry",c.entry,clrLime);
   DrawHLine(p+"sl",   c.sl,   clrRed);
   DrawHLine(p+"tp",   c.tp,   clrDodgerBlue);

   //--- text label at the zone's right edge
   string tx=p+"label";
   double anchor=(c.direction>0 ? c.zone_hi : c.zone_lo);
   if(ObjectCreate(0,tx,OBJ_TEXT,0,c.zone_to,anchor))
     {
      string txt=StringFormat("#%d %s %s @ %s",
                              id,c.strategy,DirStr(c.direction),
                              TimeToString(c.zone_to,TIME_DATE|TIME_MINUTES));
      ObjectSetString (0,tx,OBJPROP_TEXT,txt);
      ObjectSetInteger(0,tx,OBJPROP_COLOR,clrWhite);
      ObjectSetInteger(0,tx,OBJPROP_FONTSIZE,9);
      ObjectSetInteger(0,tx,OBJPROP_ANCHOR,(c.direction>0?ANCHOR_LEFT_LOWER:ANCHOR_LEFT_UPPER));
     }
  }

void DrawHLine(string name,double price,color clr)
  {
   if(ObjectCreate(0,name,OBJ_HLINE,0,0,price))
     {
      ObjectSetInteger(0,name,OBJPROP_COLOR,clr);
      ObjectSetInteger(0,name,OBJPROP_WIDTH,1);
      ObjectSetInteger(0,name,OBJPROP_STYLE,STYLE_SOLID);
      ObjectSetInteger(0,name,OBJPROP_BACK,false);
     }
  }

//+------------------------------------------------------------------+
//| Outcome capture: on a closing deal, fill the matching journal row |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(!g_active)
      return;
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD)
      return;
   ulong deal=trans.deal;
   if(deal<=0 || !HistoryDealSelect(deal))
      return;
   if(HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_OUT)
      return;                       // only interested in closes

   long posid=(long)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
   int idx=-1;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].posid==posid && !g_rows[i].closed)
        { idx=i; break; }
   if(idx<0)
      return;

   double exit_price=HistoryDealGetDouble(deal,DEAL_PRICE);
   double profit    =HistoryDealGetDouble(deal,DEAL_PROFIT)
                    +HistoryDealGetDouble(deal,DEAL_SWAP)
                    +HistoryDealGetDouble(deal,DEAL_COMMISSION);
   datetime exit_t  =(datetime)HistoryDealGetInteger(deal,DEAL_TIME);

   double risk_px=MathAbs(g_rows[idx].entry-g_rows[idx].sl);
   double moved  =(g_rows[idx].direction>0)
                  ? (exit_price-g_rows[idx].entry)
                  : (g_rows[idx].entry-exit_price);
   double r_mult =(risk_px>0.0 ? moved/risk_px : 0.0);

   g_rows[idx].closed    =true;
   g_rows[idx].exit_time =exit_t;
   g_rows[idx].exit_price=exit_price;
   g_rows[idx].pnl       =profit;
   g_rows[idx].r_multiple=r_mult;

   Print("Signal #",g_rows[idx].id," CLOSED @ ",DoubleToString(exit_price,_Digits),
         "  pnl=",DoubleToString(profit,2),"  R=",DoubleToString(r_mult,2));
   WriteJournal(g_journal_part);
  }

//+------------------------------------------------------------------+
//| Rewrite the whole journal (crash-safe: each write is complete)    |
//+------------------------------------------------------------------+
void WriteJournal(string path)
  {
   int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);   // truncates; COMMON = findable
   if(h==INVALID_HANDLE)
     {
      Print("WARNING: cannot open journal '",path,"' err=",GetLastError());
      return;
     }
   FileWriteString(h,
      "signal_id,signal_time,symbol,strategy,direction,entry,sl,tp,lots,"
      "decision,decision_ms,posid,exit_time,exit_price,pnl,r_multiple\n");
   for(int i=0;i<ArraySize(g_rows);i++)
     {
      JournalRow r=g_rows[i];
      string line=StringFormat("%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%d,%s,%s,%s,%s\n",
         r.id,
         TimeToString(r.time,TIME_DATE|TIME_SECONDS),
         r.symbol,r.strategy,DirStr(r.direction),
         DoubleToString(r.entry,_Digits),
         DoubleToString(r.sl,_Digits),
         DoubleToString(r.tp,_Digits),
         DoubleToString(r.lots,2),
         r.decision,r.decision_ms,r.posid,
         (r.closed ? TimeToString(r.exit_time,TIME_DATE|TIME_SECONDS) : ""),
         (r.closed ? DoubleToString(r.exit_price,_Digits) : ""),
         (r.closed ? DoubleToString(r.pnl,2) : ""),
         (r.closed ? DoubleToString(r.r_multiple,2) : ""));
      FileWriteString(h,line);
     }
   FileFlush(h);           // survive a tester crash
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| OnTester - end-of-run scalar (sum of R multiples) for optimiser   |
//+------------------------------------------------------------------+
double OnTester()
  {
   double sumR=0.0;
   for(int i=0;i<ArraySize(g_rows);i++)
      if(g_rows[i].closed)
         sumR+=g_rows[i].r_multiple;
   return(sumR);
  }

//+------------------------------------------------------------------+
//| Deinit - finalise the journal filename (start_end) and cleanup    |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(g_active && g_started)
     {
      string finalp=StringFormat("journal\\%s_%s_%s.csv",
                                 _Symbol,StampCompact(g_start_time),StampCompact(g_last_time));
      WriteJournal(finalp);
      if(g_journal_part!="" && g_journal_part!=finalp)
         FileDelete(g_journal_part,FILE_COMMON);
      Print("Journal finalised: <Terminal>\\Common\\Files\\",finalp," (",ArraySize(g_rows)," signals)");

      if(InpCleanupOnDeinit)
         ObjectsDeleteAll(0,InpObjPrefix);
     }
   if(CheckPointer(g_detector)==POINTER_DYNAMIC)
     {
      delete g_detector;
      g_detector=NULL;
     }
  }
//+------------------------------------------------------------------+
