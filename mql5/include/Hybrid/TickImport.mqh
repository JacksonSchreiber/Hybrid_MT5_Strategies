//+------------------------------------------------------------------+
//|                                          Hybrid\TickImport.mqh    |
//|        FTMO Hybrid Trading System - reusable tick-import engine    |
//|                                                                  |
//|  The proven CustomTicksAdd + CustomRatesUpdate loader, extracted   |
//|  from ImportTicks.mq5 so BOTH the manual script (ImportTicks) and  |
//|  the unattended EA (AutoImport) drive the SAME code. Do not fork   |
//|  the tick loader - change it here.                               |
//|                                                                  |
//|  CSV format (no header, 3 cols, UTC): yyyy.MM.dd HH:mm:ss.mmm,bid,ask
//|                                                                  |
//|  Design (CustomTicksAdd vs Replace, M1 bar building) is unchanged |
//|  from ImportTicks - see docs/mt5-import.md.                       |
//+------------------------------------------------------------------+
#ifndef HYBRID_TICKIMPORT_MQH
#define HYBRID_TICKIMPORT_MQH

//--- result of one symbol import
struct TickImportResult
  {
   bool     ok;
   long     ticks;
   long     bad;
   string   first;     // "yyyy.MM.dd HH:mm:ss.mmm" (UTC)
   string   last;
   long     m1;
   long     h4;
   long     d1;
   double   seconds;
   string   err;       // populated on failure
  };

//--- file-scope working state (one import at a time; reset per call) --------
string   g_ti_sym;
int      g_ti_digits;
double   g_ti_point;
//--- config for the current call (set by RunTickImport)
string   g_ti_base;
string   g_ti_group;
string   g_ti_originSuffix;
string   g_ti_customSuffix;
int      g_ti_batchSize;
int      g_ti_progressN;
long     g_ti_maxBad;
bool     g_ti_deleteIfExists;
bool     g_ti_buildM1;

//+------------------------------------------------------------------+
int TI_DecimalsOf(const string s)
  {
   int dot=StringFind(s,".");
   if(dot<0) return 0;
   return StringLen(s)-dot-1;
  }

//+------------------------------------------------------------------+
bool TI_ParseDateTimeMsc(const string dt,datetime &sec_out,long &msc_out)
  {
   int sp=StringFind(dt," ");
   if(sp<0) return false;
   int dot=StringFind(dt,".",sp);       // ms dot is the first '.' after the space
   long ms=0; string base;
   if(dot<0) { base=dt; ms=0; }
   else      { base=StringSubstr(dt,0,dot); ms=StringToInteger(StringSubstr(dt,dot+1)); }
   datetime secs=StringToTime(base);    // "yyyy.MM.dd HH:mm:ss" (UTC, no conversion)
   if(secs<=0) return false;
   sec_out=secs;
   msc_out=(long)secs*1000+ms;
   return true;
  }

//+------------------------------------------------------------------+
string TI_MscToStr(long msc)
  {
   datetime secs=(datetime)(msc/1000);
   int ms=(int)(msc%1000);
   return TimeToString(secs,TIME_DATE|TIME_SECONDS)+"."+StringFormat("%03d",ms);
  }

//+------------------------------------------------------------------+
long TI_SyncedBars(const string sym,ENUM_TIMEFRAMES tf,int max_wait_ms=15000)
  {
   MqlRates tmp[];
   CopyRates(sym,tf,0,1,tmp);
   int waited=0;
   while(waited<max_wait_ms)
     {
      if((bool)SeriesInfoInteger(sym,tf,SERIES_SYNCHRONIZED)) break;
      Sleep(200); waited+=200;
      CopyRates(sym,tf,0,1,tmp);
     }
   long bars=SeriesInfoInteger(sym,tf,SERIES_BARS_COUNT);
   long ib=iBars(sym,tf);
   return (bars>ib ? bars : ib);
  }

//+------------------------------------------------------------------+
bool TI_SetInferredSpecs(const string csvpath)
  {
   int fh=FileOpen(csvpath,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   int digits=5;
   if(fh!=INVALID_HANDLE)
     {
      string line=FileReadString(fh);
      string parts[];
      if(StringSplit(line,StringGetCharacter(",",0),parts)==3)
        {
         int db=TI_DecimalsOf(parts[1]), da=TI_DecimalsOf(parts[2]);
         digits=MathMax(db,da);
        }
      FileClose(fh);
     }
   if(digits<=0) digits=5;

   double contract=100000.0;
   string b=g_ti_base;
   if(StringFind(b,"XAU")>=0)      { contract=100.0;  if(digits<2) digits=2; }
   else if(StringFind(b,"XAG")>=0) { contract=5000.0; if(digits<3) digits=3; }
   else if(b=="US100" || b=="US500" || b=="US30") { contract=1.0; if(digits>2) digits=2; }
   else if(b=="USOIL")             { contract=1000.0; }
   else if(StringFind(b,"BTC")>=0) { contract=1.0; }

   double point=MathPow(10.0,-digits);
   CustomSymbolSetInteger(g_ti_sym,SYMBOL_DIGITS,digits);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_POINT,point);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_TRADE_TICK_SIZE,point);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_TRADE_TICK_VALUE,1.0);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_TRADE_CONTRACT_SIZE,contract);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_VOLUME_MIN,0.01);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_VOLUME_MAX,1000.0);
   CustomSymbolSetDouble (g_ti_sym,SYMBOL_VOLUME_STEP,0.01);
   CustomSymbolSetInteger(g_ti_sym,SYMBOL_TRADE_CALC_MODE,SYMBOL_CALC_MODE_FOREX);
   CustomSymbolSetInteger(g_ti_sym,SYMBOL_TRADE_MODE,SYMBOL_TRADE_MODE_FULL);
   CustomSymbolSetInteger(g_ti_sym,SYMBOL_TRADE_EXEMODE,SYMBOL_TRADE_EXECUTION_MARKET);
   CustomSymbolSetString (g_ti_sym,SYMBOL_DESCRIPTION,g_ti_base+" (Dukascopy ticks, inferred specs)");
   CustomSymbolSetString (g_ti_sym,SYMBOL_CURRENCY_BASE,StringSubstr(g_ti_base,0,3));
   CustomSymbolSetString (g_ti_sym,SYMBOL_CURRENCY_PROFIT,StringSubstr(g_ti_base,3,3));
   CustomSymbolSetString (g_ti_sym,SYMBOL_CURRENCY_MARGIN,StringSubstr(g_ti_base,0,3));

   Print("  [",g_ti_base,"] INFERRED SPECS -> digits=",digits," point=",DoubleToString(point,10),
         " contract=",DoubleToString(contract,2));
   return true;
  }

//+------------------------------------------------------------------+
bool TI_SetupSymbol(const string csvpath)
  {
   string origin=g_ti_base+g_ti_originSuffix;
   bool is_custom=false;
   if(SymbolExist(g_ti_sym,is_custom))
     {
      if(!is_custom) { Print("  [",g_ti_base,"] FATAL: '",g_ti_sym,"' exists as a non-custom symbol."); return false; }
      if(!g_ti_deleteIfExists) { Print("  [",g_ti_base,"] FATAL: '",g_ti_sym,"' exists and deleteIfExists=false."); return false; }
      Print("  [",g_ti_base,"] existing custom symbol - deleting to reload fresh.");
      SymbolSelect(g_ti_sym,false);
      if(!CustomSymbolDelete(g_ti_sym))
        { Print("  [",g_ti_base,"] FATAL: CustomSymbolDelete failed err=",GetLastError()); return false; }
     }

   bool origin_custom=false;
   bool have_origin=SymbolExist(origin,origin_custom);
   if(have_origin) SymbolSelect(origin,true);

   bool created=false;
   if(have_origin)
     {
      created=CustomSymbolCreate(g_ti_sym,g_ti_group,origin);
      if(created) Print("  [",g_ti_base,"] created by CLONING specs from '",origin,"'.");
     }
   if(!created)
     {
      created=CustomSymbolCreate(g_ti_sym,g_ti_group);
      if(!created) { Print("  [",g_ti_base,"] FATAL: CustomSymbolCreate failed err=",GetLastError()); return false; }
      Print("  [",g_ti_base,"] origin '",origin,"' not found - inferring specs (expected on OANDA terminal).");
      if(!TI_SetInferredSpecs(csvpath)) return false;
     }

   g_ti_digits=(int)SymbolInfoInteger(g_ti_sym,SYMBOL_DIGITS);
   if(g_ti_digits<=0) g_ti_digits=5;
   g_ti_point=SymbolInfoDouble(g_ti_sym,SYMBOL_POINT);
   if(g_ti_point<=0.0)
     { g_ti_point=MathPow(10.0,-g_ti_digits); CustomSymbolSetDouble(g_ti_sym,SYMBOL_POINT,g_ti_point); }

   if(!SymbolSelect(g_ti_sym,true))
     { Print("  [",g_ti_base,"] FATAL: SymbolSelect failed err=",GetLastError()); return false; }
   return true;
  }

//+------------------------------------------------------------------+
bool TI_FlushTicks(const MqlTick &ticks[],int count)
  {
   ResetLastError();
   int added=CustomTicksAdd(g_ti_sym,ticks,count);
   if(added<0)
     { Print("  [",g_ti_base,"] FATAL: CustomTicksAdd err=",GetLastError()); return false; }
   if(added!=count) Print("  [",g_ti_base,"] WARNING: added ",added," of ",count," ticks.");
   return true;
  }

//+------------------------------------------------------------------+
void TI_PushBar(MqlRates &arr[],int &cap,int &cnt,datetime t,
                double o,double h,double l,double c,long v,int s)
  {
   if(cnt>=cap) { cap+=100000; ArrayResize(arr,cap,100000); }
   arr[cnt].time=t; arr[cnt].open=o; arr[cnt].high=h; arr[cnt].low=l; arr[cnt].close=c;
   arr[cnt].tick_volume=v; arr[cnt].spread=s; arr[cnt].real_volume=0;
   cnt++;
  }

//+------------------------------------------------------------------+
//| Import one symbol's staged CSV into <base><customSuffix>.         |
//| CSV is read from MQL5\Files\import\<base>.csv. Returns res.ok.    |
//+------------------------------------------------------------------+
bool RunTickImport(const string base,TickImportResult &res,
                   const string group="Dukascopy",const string originSuffix=".sim",
                   const string customSuffix=".dk",const int batchSize=1000000,
                   const int progressEveryN=20,const long maxBadLines=200,
                   const bool deleteIfExists=true,const bool buildM1=true)
  {
   uint t0=GetTickCount();
   //--- reset result + config
   res.ok=false; res.ticks=0; res.bad=0; res.first=""; res.last="";
   res.m1=0; res.h4=0; res.d1=0; res.seconds=0; res.err="";
   g_ti_base=base; g_ti_group=group; g_ti_originSuffix=originSuffix;
   g_ti_customSuffix=customSuffix; g_ti_batchSize=batchSize; g_ti_progressN=progressEveryN;
   g_ti_maxBad=maxBadLines; g_ti_deleteIfExists=deleteIfExists; g_ti_buildM1=buildM1;
   g_ti_sym=base+customSuffix;
   g_ti_digits=5; g_ti_point=0.00001;

   if(StringLen(customSuffix)>4)
     { res.err="custom suffix >4 chars"; return false; }

   string csvpath="import\\"+base+".csv";
   if(!FileIsExist(csvpath))
     { res.err="CSV not staged: MQL5\\Files\\"+csvpath; Print("  [",base,"] ",res.err); return false; }

   if(!TI_SetupSymbol(csvpath))
     { res.err="symbol setup failed"; return false; }

   int fh=FileOpen(csvpath,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(fh==INVALID_HANDLE)
     { res.err="FileOpen failed err="+(string)GetLastError(); Print("  [",base,"] ",res.err); return false; }
   ulong fsize=FileSize(fh);
   Print("  [",base,"] importing ",DoubleToString(fsize/1073741824.0,2)," GB CSV -> ",g_ti_sym);

   MqlTick ticks[]; ArrayResize(ticks,batchSize);
   int n=0; long total=0, bad=0; int batchno=0;
   MqlRates m1[]; int m1cap=0, m1cnt=0;
   datetime curmin=0; double o=0,h=0,l=0,c=0; long vol=0; int spr=0;
   long first_msc=0, last_msc=0; bool have_first=false;
   string fld[]; ushort comma=StringGetCharacter(",",0);

   while(!FileIsEnding(fh))
     {
      string line=FileReadString(fh);
      int got=StringSplit(line,comma,fld);
      if(got<=0) continue;
      if(got!=3)
        { if(++bad>maxBadLines) { FileClose(fh); res.err="malformed lines exceeded tolerance"; res.bad=bad; return false; } continue; }

      datetime secs; long msc;
      if(!TI_ParseDateTimeMsc(fld[0],secs,msc))
        { if(++bad>maxBadLines) { FileClose(fh); res.err="malformed lines exceeded tolerance"; res.bad=bad; return false; } continue; }
      double bid=StringToDouble(fld[1]);
      double ask=StringToDouble(fld[2]);
      if(bid<=0.0 || ask<=0.0)
        { if(++bad>maxBadLines) { FileClose(fh); res.err="malformed lines exceeded tolerance"; res.bad=bad; return false; } continue; }

      ticks[n].time=secs; ticks[n].time_msc=msc; ticks[n].bid=bid; ticks[n].ask=ask;
      ticks[n].last=0.0; ticks[n].volume=0; ticks[n].volume_real=0.0;
      ticks[n].flags=TICK_FLAG_BID|TICK_FLAG_ASK;
      n++;
      if(!have_first) { first_msc=msc; have_first=true; }
      last_msc=msc;

      if(buildM1)
        {
         datetime m=secs-(secs%60);
         if(m!=curmin)
           {
            if(curmin>0) TI_PushBar(m1,m1cap,m1cnt,curmin,o,h,l,c,vol,spr);
            curmin=m; o=bid; h=bid; l=bid; c=bid; vol=0;
           }
         if(bid>h) h=bid;
         if(bid<l) l=bid;
         c=bid; vol++;
         spr=(int)MathRound((ask-bid)/g_ti_point);
        }

      if(n>=batchSize)
        {
         if(!TI_FlushTicks(ticks,n)) { FileClose(fh); res.err="CustomTicksAdd failed"; return false; }
         total+=n; n=0;
         if((++batchno)%progressEveryN==0)
            Print("  [",base,"] ",total," ticks, last=",TI_MscToStr(last_msc));
        }
     }
   if(n>0)
     {
      if(!TI_FlushTicks(ticks,n)) { FileClose(fh); res.err="CustomTicksAdd failed"; return false; }
      total+=n; n=0;
     }
   if(buildM1 && curmin>0) TI_PushBar(m1,m1cap,m1cnt,curmin,o,h,l,c,vol,spr);
   FileClose(fh);

   if(total==0) { res.err="no valid ticks parsed"; return false; }

   if(buildM1 && m1cnt>0)
     {
      int rc=CustomRatesUpdate(g_ti_sym,m1,m1cnt);
      if(rc<0) Print("  [",base,"] WARNING: CustomRatesUpdate err=",GetLastError());
     }

   res.m1=TI_SyncedBars(g_ti_sym,PERIOD_M1);
   res.h4=TI_SyncedBars(g_ti_sym,PERIOD_H4);
   res.d1=TI_SyncedBars(g_ti_sym,PERIOD_D1);
   res.ticks=total;
   res.bad=bad;
   res.first=TI_MscToStr(first_msc);
   res.last=TI_MscToStr(last_msc);
   res.seconds=(GetTickCount()-t0)/1000.0;
   res.ok=true;
   Print("  [",base,"] DONE ticks=",total," bad=",bad," M1=",res.m1," H4=",res.h4,
         " D1=",res.d1," in ",DoubleToString(res.seconds,1),"s");
   return true;
  }

#endif // HYBRID_TICKIMPORT_MQH
//+------------------------------------------------------------------+
