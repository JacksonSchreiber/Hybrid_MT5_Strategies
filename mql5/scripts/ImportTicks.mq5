//+------------------------------------------------------------------+
//|                                                  ImportTicks.mq5  |
//|            FTMO Hybrid Trading System - Dukascopy tick importer   |
//|                                                                  |
//|  Streams a Dukascopy tick CSV (exportToMT5 format) into an MT5    |
//|  custom symbol "<SymbolBase>.dk" for Strategy Tester use.         |
//|                                                                  |
//|  CSV format (no header, 3 columns, UTC timestamps):              |
//|      yyyy.MM.dd HH:mm:ss.mmm,bid,ask                             |
//|      e.g.  2020.01.01 22:01:12.821,1.12132,1.12133               |
//|                                                                  |
//|  Source of truth lives in the repo at mql5/scripts/; it is       |
//|  synced into <MT5 data folder>\MQL5\Scripts\ to compile & appear  |
//|  in the terminal Navigator.                                      |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "1.00"
#property script_show_inputs
#property description "Imports a Dukascopy tick CSV (MQL5\\Files\\import\\<Base>.csv)"
#property description "into custom symbol <Base>.dk for Strategy Tester use."

//====================================================================
//  DESIGN NOTES  (read before modifying)
//--------------------------------------------------------------------
//  CustomTicksAdd vs CustomTicksReplace
//  ------------------------------------
//  We load a FRESH custom symbol with a strictly time-ascending CSV,
//  processed sequentially in ~1M-tick batches. For that pattern
//  CustomTicksAdd is the correct API:
//    * Its documented behaviour is "append to the end of existing
//      ticks", and the official MQL5 example uses it to load 351M+
//      historical ticks (2011-2024) - i.e. it IS the documented bulk
//      historical loader, not just a realtime feed.
//    * It requires the symbol be selected in Market Watch (we do that)
//      and requires ascending time_msc within the array (our CSV is
//      already ascending) - both satisfied naturally here.
//    * Because each batch is strictly later than the previous one and
//      we only ever append, there is NO batch-boundary hazard.
//  CustomTicksReplace, by contrast, deletes+rewrites a [from,to]
//  interval. Used per-batch it risks clipping ticks that share an
//  identical millisecond across a batch boundary (Dukascopy has many
//  same-ms ticks). It is the right tool for correcting a range, not
//  for a clean sequential first load. Kept as a documented fallback
//  (see docs/mt5-import.md) if Add proves too slow on a given box.
//
//  Bar auto-build
//  --------------
//  The MQL5 docs are SILENT on whether adding ticks auto-generates M1
//  bars for a custom symbol. Rather than trust undocumented behaviour,
//  this script aggregates M1 OHLC bars from the ticks in-stream (bid-
//  based, exactly how MT5 builds bars) and writes them with
//  CustomRatesUpdate. H4/D1 (and every other timeframe) are always
//  derived by the terminal from M1, so M1 is sufficient. Before the
//  CustomRatesUpdate call we probe iBars (after forcing timeseries
//  synchronisation) to record empirically what auto-build produced -
//  that answer is Printed to the Journal.
//====================================================================

//--- inputs
input string SymbolBase        = "EURUSD";     // FTMO base name (custom symbol becomes <Base>.dk)
input string Group             = "Dukascopy";  // Navigator group / symbol path
input string OriginSuffix      = ".sim";       // broker origin symbol suffix to clone specs from
input string CustomSuffix      = ".dk";        // custom symbol suffix (<=4 chars, tester requirement)
input int    BatchSize         = 1000000;      // ticks per CustomTicksAdd batch
input int    ProgressEveryN    = 10;           // Print progress every N batches
input long   MaxBadLines       = 200;          // abort if malformed lines exceed this
input bool   DeleteIfExists    = true;         // delete+recreate the custom symbol if it already exists
input bool   BuildM1Bars       = true;         // build M1 bars in-stream and push via CustomRatesUpdate

//--- globals
string   g_sym;          // full custom symbol name, e.g. "EURUSD.dk"
int      g_digits;       // resolved digits
double   g_point;        // resolved point size

//+------------------------------------------------------------------+
//| Count decimal places in a numeric string field                   |
//+------------------------------------------------------------------+
int DecimalsOf(const string s)
  {
   int dot=StringFind(s,".");
   if(dot<0)
      return 0;
   return StringLen(s)-dot-1;
  }

//+------------------------------------------------------------------+
//| Parse one CSV datetime "yyyy.MM.dd HH:mm:ss.mmm" -> time_msc      |
//| Returns false on parse failure. sec_out = whole seconds.         |
//+------------------------------------------------------------------+
bool ParseDateTimeMsc(const string dt,datetime &sec_out,long &msc_out)
  {
   int sp=StringFind(dt," ");
   if(sp<0)
      return false;
   //--- the millisecond dot is the first '.' AFTER the space
   int dot=StringFind(dt,".",sp);
   long ms=0;
   string base;
   if(dot<0)
     {
      base=dt;              // tolerate a missing .mmm
      ms=0;
     }
   else
     {
      base=StringSubstr(dt,0,dot);
      ms=StringToInteger(StringSubstr(dt,dot+1));
     }
   datetime secs=StringToTime(base);   // parses "yyyy.MM.dd HH:mm:ss" (UTC, no conversion)
   if(secs<=0)
      return false;
   sec_out=secs;
   msc_out=(long)secs*1000+ms;
   return true;
  }

//+------------------------------------------------------------------+
//| Resolve / create the custom symbol. Returns false on fatal error |
//+------------------------------------------------------------------+
bool SetupSymbol(const string csvpath)
  {
   string origin=SymbolBase+OriginSuffix;

   //--- handle pre-existing custom symbol
   bool   is_custom=false;
   if(SymbolExist(g_sym,is_custom))
     {
      if(!is_custom)
        {
         Print("FATAL: '",g_sym,"' already exists as a NON-custom symbol. Aborting.");
         return false;
        }
      if(!DeleteIfExists)
        {
         Print("FATAL: custom symbol '",g_sym,"' already exists and DeleteIfExists=false. Aborting.");
         return false;
        }
      Print("Custom symbol '",g_sym,"' already exists - deleting to reload fresh.");
      SymbolSelect(g_sym,false);
      if(!CustomSymbolDelete(g_sym))
        {
         Print("FATAL: CustomSymbolDelete('",g_sym,"') failed, err=",GetLastError(),
               ". Close any chart/Market-Watch entry for it and retry.");
         return false;
        }
     }

   //--- does the broker origin (<Base>.sim) exist to clone specs from?
   bool origin_custom=false;
   bool have_origin=SymbolExist(origin,origin_custom);
   if(have_origin)
      SymbolSelect(origin,true);   // ensure specs are populated

   bool created=false;
   if(have_origin)
     {
      created=CustomSymbolCreate(g_sym,Group,origin);
      if(created)
         Print("Created '",g_sym,"' in group '",Group,"' by CLONING contract specs from origin '",origin,"'.");
      else
         Print("WARNING: clone-create from '",origin,"' failed (err=",GetLastError(),
               "); falling back to a bare create.");
     }
   if(!created)
     {
      created=CustomSymbolCreate(g_sym,Group);
      if(!created)
        {
         Print("FATAL: CustomSymbolCreate('",g_sym,"') failed, err=",GetLastError());
         return false;
        }
      Print("WARNING: origin '",origin,"' NOT found (this is EXPECTED on a non-FTMO terminal, e.g. OANDA).");
      Print("         Created '",g_sym,"' with specs set by inference - VERIFY P/L-affecting specs");
      Print("         (contract size, tick value, calc mode) before trusting tester money figures.");
      if(!SetInferredSpecs(csvpath))
         return false;
     }

   //--- resolve digits/point for downstream spread computation
   g_digits=(int)SymbolInfoInteger(g_sym,SYMBOL_DIGITS);
   if(g_digits<=0)
      g_digits=5;
   g_point=SymbolInfoDouble(g_sym,SYMBOL_POINT);
   if(g_point<=0.0)
     {
      g_point=MathPow(10.0,-g_digits);
      CustomSymbolSetDouble(g_sym,SYMBOL_POINT,g_point);
     }

   //--- select into Market Watch (required by CustomTicksAdd)
   if(!SymbolSelect(g_sym,true))
     {
      Print("FATAL: SymbolSelect('",g_sym,"',true) failed, err=",GetLastError());
      return false;
     }
   Print("Symbol ready: ",g_sym," digits=",g_digits," point=",DoubleToString(g_point,10));
   return true;
  }

//+------------------------------------------------------------------+
//| Fallback specs when no <Base>.sim origin exists. Digits inferred |
//| from the first CSV price; other specs set to sane per-class      |
//| defaults. Logs a warning listing what was assumed.               |
//+------------------------------------------------------------------+
bool SetInferredSpecs(const string csvpath)
  {
   //--- peek the first data line to infer digits from the bid/ask decimals
   int fh=FileOpen(csvpath,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   int digits=5;
   if(fh!=INVALID_HANDLE)
     {
      string line=FileReadString(fh);
      string parts[];
      if(StringSplit(line,StringGetCharacter(",",0),parts)==3)
        {
         int db=DecimalsOf(parts[1]), da=DecimalsOf(parts[2]);
         digits=MathMax(db,da);
        }
      FileClose(fh);
     }
   if(digits<=0)
      digits=5;

   //--- rough per-asset-class sanity (name-based); refined by CSV digits above
   double contract=100000.0;   // FX default lot notional
   string b=SymbolBase;
   if(StringFind(b,"XAU")>=0)      { contract=100.0;  if(digits<2) digits=2; }
   else if(StringFind(b,"XAG")>=0) { contract=5000.0; if(digits<3) digits=3; }
   else if(b=="US100" || b=="US500" || b=="US30") { contract=1.0; if(digits>2) digits=2; }
   else if(b=="USOIL")             { contract=1000.0; }
   else if(StringFind(b,"BTC")>=0) { contract=1.0; }

   double point=MathPow(10.0,-digits);

   CustomSymbolSetInteger(g_sym,SYMBOL_DIGITS,digits);
   CustomSymbolSetDouble (g_sym,SYMBOL_POINT,point);
   CustomSymbolSetDouble (g_sym,SYMBOL_TRADE_TICK_SIZE,point);
   CustomSymbolSetDouble (g_sym,SYMBOL_TRADE_TICK_VALUE,1.0);
   CustomSymbolSetDouble (g_sym,SYMBOL_TRADE_CONTRACT_SIZE,contract);
   CustomSymbolSetDouble (g_sym,SYMBOL_VOLUME_MIN,0.01);
   CustomSymbolSetDouble (g_sym,SYMBOL_VOLUME_MAX,1000.0);
   CustomSymbolSetDouble (g_sym,SYMBOL_VOLUME_STEP,0.01);
   CustomSymbolSetInteger(g_sym,SYMBOL_TRADE_CALC_MODE,SYMBOL_CALC_MODE_FOREX);
   CustomSymbolSetInteger(g_sym,SYMBOL_TRADE_MODE,SYMBOL_TRADE_MODE_FULL);
   CustomSymbolSetInteger(g_sym,SYMBOL_TRADE_EXEMODE,SYMBOL_TRADE_EXECUTION_MARKET);
   CustomSymbolSetString (g_sym,SYMBOL_DESCRIPTION,SymbolBase+" (Dukascopy ticks, inferred specs)");
   CustomSymbolSetString (g_sym,SYMBOL_CURRENCY_BASE,StringSubstr(SymbolBase,0,3));
   CustomSymbolSetString (g_sym,SYMBOL_CURRENCY_PROFIT,StringSubstr(SymbolBase,3,3));
   CustomSymbolSetString (g_sym,SYMBOL_CURRENCY_MARGIN,StringSubstr(SymbolBase,0,3));

   Print("INFERRED SPECS -> digits=",digits," point=",DoubleToString(point,10),
         " contract=",DoubleToString(contract,2),
         " (VERIFY tick value / calc mode before using tester P/L).");
   return true;
  }

//+------------------------------------------------------------------+
//| Wait until a timeseries is synchronised, then return its bars    |
//+------------------------------------------------------------------+
long SyncedBars(const string sym,ENUM_TIMEFRAMES tf,int max_wait_ms=15000)
  {
   //--- kick the terminal into building/loading the series
   MqlRates tmp[];
   CopyRates(sym,tf,0,1,tmp);
   int waited=0;
   while(waited<max_wait_ms)
     {
      bool synced=(bool)SeriesInfoInteger(sym,tf,SERIES_SYNCHRONIZED);
      if(synced)
         break;
      Sleep(200);
      waited+=200;
      CopyRates(sym,tf,0,1,tmp); // nudge again
     }
   long bars=SeriesInfoInteger(sym,tf,SERIES_BARS_COUNT);
   long ib=iBars(sym,tf);
   if(bars<ib)
      bars=ib;
   return bars;
  }

//+------------------------------------------------------------------+
//| Script entry point                                               |
//+------------------------------------------------------------------+
void OnStart()
  {
   uint t0=GetTickCount();
   g_sym=SymbolBase+CustomSuffix;
   if(StringLen(CustomSuffix)>4)
     {
      Print("FATAL: CustomSuffix '",CustomSuffix,"' exceeds 4 chars - Strategy Tester rejects it.");
      return;
     }

   string csvpath="import\\"+SymbolBase+".csv";   // relative to MQL5\Files
   Print("=== ImportTicks: ",SymbolBase," -> ",g_sym," ===");
   Print("CSV (sandbox): MQL5\\Files\\",csvpath);

   //--- confirm the file exists in the sandbox
   if(!FileIsExist(csvpath))
     {
      Print("FATAL: '",csvpath,"' not found under MQL5\\Files. Stage it first with");
      Print("       pipeline/stage_csv_for_import.sh ",SymbolBase);
      return;
     }

   //--- create / resolve the custom symbol
   if(!SetupSymbol(csvpath))
      return;

   //--- open the CSV for streaming, one full line per FileReadString.
   //    FILE_TXT (not FILE_CSV) + our own StringSplit(',') is deliberate: it
   //    makes field-splitting unambiguous, so the space inside the datetime
   //    field ("2020.01.01 22:01:12.821") can never be mistaken for a column
   //    break. 64-bit file offsets handle the >2 GB size; we only read
   //    sequentially (no FileSeek/FileTell), so no offset wrap is possible.
   int fh=FileOpen(csvpath,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(fh==INVALID_HANDLE)
     {
      Print("FATAL: FileOpen('",csvpath,"') failed, err=",GetLastError());
      return;
     }
   ulong fsize=FileSize(fh);
   Print("File size: ",fsize," bytes (",DoubleToString(fsize/1073741824.0,2)," GB)");

   //--- batch buffers
   MqlTick  ticks[];
   ArrayResize(ticks,BatchSize);
   int      n=0;                 // ticks in current batch
   long     total=0;            // total ticks added
   long     bad=0;             // malformed lines
   int      batchno=0;

   //--- M1 aggregation state (bid-based OHLC, like MT5's own bars)
   MqlRates m1[];
   int      m1cap=0, m1cnt=0;
   datetime curmin=0;
   double   o=0,h=0,l=0,c=0;
   long     vol=0;
   int      spr=0;

   //--- first/last tick tracking
   long     first_msc=0, last_msc=0;
   bool     have_first=false;

   //--- per-line split buffer
   string   fld[];
   ushort   comma=StringGetCharacter(",",0);

   while(!FileIsEnding(fh))
     {
      string line=FileReadString(fh);           // one full line (terminator stripped)
      int got=StringSplit(line,comma,fld);
      if(got<=0)
         continue;                              // blank line / trailing newline - not an error
      if(got!=3)
        {
         if(++bad>MaxBadLines) { AbortBad(fh,bad); return; }
         continue;
        }

      datetime secs; long msc;
      if(!ParseDateTimeMsc(fld[0],secs,msc))
        {
         if(++bad>MaxBadLines) { AbortBad(fh,bad); return; }
         continue;
        }
      double bid=StringToDouble(fld[1]);
      double ask=StringToDouble(fld[2]);
      if(bid<=0.0 || ask<=0.0)
        {
         if(++bad>MaxBadLines) { AbortBad(fh,bad); return; }
         continue;
        }

      //--- build the tick
      ticks[n].time       =secs;
      ticks[n].time_msc   =msc;
      ticks[n].bid        =bid;
      ticks[n].ask        =ask;
      ticks[n].last       =0.0;
      ticks[n].volume     =0;
      ticks[n].volume_real=0.0;
      ticks[n].flags      =TICK_FLAG_BID|TICK_FLAG_ASK;
      n++;

      if(!have_first) { first_msc=msc; have_first=true; }
      last_msc=msc;

      //--- fold into the current M1 bar (bid-based)
      if(BuildM1Bars)
        {
         datetime m=secs-(secs%60);
         if(m!=curmin)
           {
            if(curmin>0)
               PushBar(m1,m1cap,m1cnt,curmin,o,h,l,c,vol,spr);
            curmin=m; o=bid; h=bid; l=bid; c=bid; vol=0;
           }
         if(bid>h) h=bid;
         if(bid<l) l=bid;
         c=bid;
         vol++;
         spr=(int)MathRound((ask-bid)/g_point);
        }

      //--- flush a full tick batch
      if(n>=BatchSize)
        {
         if(!FlushTicks(ticks,n)) { FileClose(fh); return; }
         total+=n;
         n=0;
         if((++batchno)%ProgressEveryN==0)
            Print("  ... ",total," ticks added (batch ",batchno,"), last=",
                  MscToStr(last_msc));
        }
     }

   //--- flush the final partial tick batch
   if(n>0)
     {
      if(!FlushTicks(ticks,n)) { FileClose(fh); return; }
      total+=n;
      n=0;
     }
   //--- close the final open M1 bar
   if(BuildM1Bars && curmin>0)
      PushBar(m1,m1cap,m1cnt,curmin,o,h,l,c,vol,spr);

   FileClose(fh);

   if(total==0)
     {
      Print("FATAL: no valid ticks parsed - check CSV format.");
      return;
     }

   //=================================================================
   //  Deliverable 5: does MT5 auto-build bars from the imported ticks?
   //=================================================================
   Print("--- Bar auto-build probe (BEFORE CustomRatesUpdate) ---");
   long m1_before=SyncedBars(g_sym,PERIOD_M1);
   long h4_before=SyncedBars(g_sym,PERIOD_H4);
   long d1_before=SyncedBars(g_sym,PERIOD_D1);
   Print("  auto-built bars: M1=",m1_before," H4=",h4_before," D1=",d1_before,
         (m1_before>0 ? "  -> terminal DID auto-build from ticks"
                      : "  -> terminal did NOT auto-build (M1 push required)"));

   //--- push our in-stream M1 bars (idempotent if auto-build already made them)
   if(BuildM1Bars && m1cnt>0)
     {
      int rc=CustomRatesUpdate(g_sym,m1,m1cnt);
      if(rc<0)
         Print("WARNING: CustomRatesUpdate failed, err=",GetLastError());
      else
         Print("CustomRatesUpdate wrote/updated ",rc," M1 bars (of ",m1cnt," aggregated).");
     }

   Print("--- Bar counts (AFTER CustomRatesUpdate) ---");
   long m1_after=SyncedBars(g_sym,PERIOD_M1);
   long h4_after=SyncedBars(g_sym,PERIOD_H4);
   long d1_after=SyncedBars(g_sym,PERIOD_D1);
   Print("  M1=",m1_after," H4=",h4_after," D1=",d1_after,
         "  (H4/D1 are derived by the terminal from M1)");

   //=================================================================
   //  Final summary
   //=================================================================
   double secs_elapsed=(GetTickCount()-t0)/1000.0;
   Print("=== IMPORT COMPLETE: ",g_sym," ===");
   Print("  total ticks : ",total);
   Print("  malformed   : ",bad," (tolerance ",MaxBadLines,")");
   Print("  first tick  : ",MscToStr(first_msc));
   Print("  last  tick  : ",MscToStr(last_msc));
   Print("  M1 bars     : ",m1_after,"  H4: ",h4_after,"  D1: ",d1_after);
   Print("  elapsed     : ",DoubleToString(secs_elapsed,1)," s");
   Print("  NEXT: run VerifyImport (SymbolName=",g_sym,") for an independent QA check.");
  }

//+------------------------------------------------------------------+
//| CustomTicksAdd one batch; returns false on fatal error           |
//+------------------------------------------------------------------+
bool FlushTicks(const MqlTick &ticks[],int count)
  {
   ResetLastError();
   int added=CustomTicksAdd(g_sym,ticks,count);
   if(added<0)
     {
      Print("FATAL: CustomTicksAdd failed, err=",GetLastError(),
            " (symbol must be in Market Watch; ticks must be time-ascending).");
      return false;
     }
   if(added!=count)
      Print("WARNING: CustomTicksAdd added ",added," of ",count," ticks in this batch.");
   return true;
  }

//+------------------------------------------------------------------+
//| Append a completed M1 bar to the rates buffer (chunked reserve)  |
//+------------------------------------------------------------------+
void PushBar(MqlRates &arr[],int &cap,int &cnt,datetime t,
             double o,double h,double l,double c,long v,int s)
  {
   if(cnt>=cap)
     {
      cap+=100000;
      ArrayResize(arr,cap,100000);
     }
   arr[cnt].time        =t;
   arr[cnt].open        =o;
   arr[cnt].high        =h;
   arr[cnt].low         =l;
   arr[cnt].close       =c;
   arr[cnt].tick_volume =v;
   arr[cnt].spread      =s;
   arr[cnt].real_volume =0;
   cnt++;
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
//| Abort helper for the malformed-line tolerance                    |
//+------------------------------------------------------------------+
void AbortBad(int fh,long bad)
  {
   FileClose(fh);
   Print("FATAL: malformed lines (",bad,") exceeded MaxBadLines (",MaxBadLines,
         "). Aborting - inspect the CSV. Custom symbol left partially loaded;");
   Print("       rerun with DeleteIfExists=true after fixing the data.");
  }
//+------------------------------------------------------------------+
