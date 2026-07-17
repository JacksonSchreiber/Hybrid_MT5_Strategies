//+------------------------------------------------------------------+
//|                                                VerifyImport.mq5   |
//|          FTMO Hybrid Trading System - custom-symbol QA probe      |
//|                                                                  |
//|  Independent read-only check of a loaded custom symbol. Prints:  |
//|    - tick count (sum of M1 tick_volume)                          |
//|    - earliest actual tick time (from CopyTicks)                  |
//|    - first / last M1 bar time                                    |
//|    - M1 / H4 / D1 bar counts                                     |
//|                                                                  |
//|  QA uses this after running ImportTicks.                         |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "1.00"
#property script_show_inputs
#property description "Read-only QA probe of a loaded custom symbol (tick count, first/last, M1/H4/D1 bars)."

input string TargetSymbol = "EURUSD.dk"; // full custom symbol name to verify
input int    SyncWaitMs   = 15000;       // max ms to wait for each timeseries to synchronise

//+------------------------------------------------------------------+
//| Wait for a timeseries to synchronise, return its bar count       |
//+------------------------------------------------------------------+
long SyncedBars(const string sym,ENUM_TIMEFRAMES tf,int max_wait_ms)
  {
   MqlRates tmp[];
   CopyRates(sym,tf,0,1,tmp);
   int waited=0;
   while(waited<max_wait_ms)
     {
      if((bool)SeriesInfoInteger(sym,tf,SERIES_SYNCHRONIZED))
         break;
      Sleep(200);
      waited+=200;
      CopyRates(sym,tf,0,1,tmp);
     }
   long bars=SeriesInfoInteger(sym,tf,SERIES_BARS_COUNT);
   long ib=iBars(sym,tf);
   return (bars>ib ? bars : ib);
  }

//+------------------------------------------------------------------+
//| Format a time_msc value as "yyyy.MM.dd HH:mm:ss.mmm" (UTC)       |
//+------------------------------------------------------------------+
string MscToStr(long msc)
  {
   datetime secs=(datetime)(msc/1000);
   int ms=(int)(msc%1000);
   return TimeToString(secs,TIME_DATE|TIME_SECONDS)+"."+StringFormat("%03d",ms);
  }

//+------------------------------------------------------------------+
void OnStart()
  {
   string sym=TargetSymbol;
   Print("=== VerifyImport: ",sym," ===");

   bool is_custom=false;
   if(!SymbolExist(sym,is_custom))
     {
      Print("FATAL: symbol '",sym,"' does not exist.");
      return;
     }
   if(!is_custom)
      Print("NOTE: '",sym,"' is not a custom symbol (verifying anyway).");
   if(!SymbolSelect(sym,true))
     {
      Print("FATAL: SymbolSelect('",sym,"',true) failed, err=",GetLastError());
      return;
     }

   int    digits=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   double point =SymbolInfoDouble(sym,SYMBOL_POINT);
   Print("  digits=",digits," point=",DoubleToString(point,10));

   //--- bar counts (synchronised)
   long m1=SyncedBars(sym,PERIOD_M1,SyncWaitMs);
   long h4=SyncedBars(sym,PERIOD_H4,SyncWaitMs);
   long d1=SyncedBars(sym,PERIOD_D1,SyncWaitMs);
   Print("  bars: M1=",m1,"  H4=",h4,"  D1=",d1);

   if(m1<=0)
     {
      Print("WARNING: no M1 bars - the symbol has no bar history (import may have failed).");
      return;
     }

   //--- first / last M1 bar time
   datetime first_bar=iTime(sym,PERIOD_M1,(int)m1-1);
   datetime last_bar =iTime(sym,PERIOD_M1,0);
   Print("  first M1 bar: ",TimeToString(first_bar,TIME_DATE|TIME_SECONDS)," (UTC)");
   Print("  last  M1 bar: ",TimeToString(last_bar,TIME_DATE|TIME_SECONDS)," (UTC)");

   //--- tick count via sum of M1 tick_volume (cheap vs iterating 100M+ ticks)
   long vols[];
   long copied=CopyTickVolume(sym,PERIOD_M1,0,(int)m1,vols);
   long tickcount=0;
   for(long i=0;i<copied;i++)
      tickcount+=vols[i];
   if(copied<m1)
      Print("  (note: only ",copied," of ",m1," M1 volumes copied in one call - tick count is a lower bound)");
   Print("  tick count (sum of M1 tick_volume): ",tickcount);

   //--- earliest actual tick from the tick store
   MqlTick t[];
   int nt=CopyTicks(sym,t,COPY_TICKS_ALL,0,1);
   if(nt>0)
      Print("  earliest stored tick: ",MscToStr(t[0].time_msc),
            "  bid=",DoubleToString(t[0].bid,digits),
            "  ask=",DoubleToString(t[0].ask,digits));
   else
      Print("  WARNING: CopyTicks returned no ticks (err=",GetLastError(),") - tick store may be empty.");

   Print("=== VerifyImport done ===");
  }
//+------------------------------------------------------------------+
