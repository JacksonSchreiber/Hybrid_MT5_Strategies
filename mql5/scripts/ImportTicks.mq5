//+------------------------------------------------------------------+
//|                                                  ImportTicks.mq5  |
//|            FTMO Hybrid Trading System - Dukascopy tick importer   |
//|                                                                  |
//|  Manual (drag-onto-chart) importer for one symbol. Streams a      |
//|  Dukascopy tick CSV (exportToMT5 format) into custom symbol       |
//|  "<SymbolBase>.dk" for Strategy Tester use.                       |
//|                                                                  |
//|  The tick loader itself lives in Hybrid\TickImport.mqh and is     |
//|  SHARED with the unattended AutoImport.mq5 EA - do not fork it.   |
//|  Design notes (CustomTicksAdd vs Replace, M1 bar building) are in |
//|  that include and in docs/mt5-import.md.                          |
//|                                                                  |
//|  CSV format (no header, 3 columns, UTC timestamps):              |
//|      yyyy.MM.dd HH:mm:ss.mmm,bid,ask                             |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "1.10"
#property script_show_inputs
#property description "Imports a Dukascopy tick CSV (MQL5\\Files\\import\\<Base>.csv)"
#property description "into custom symbol <Base>.dk for Strategy Tester use."

#include <Hybrid\TickImport.mqh>

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

//+------------------------------------------------------------------+
void OnStart()
  {
   Print("=== ImportTicks: ",SymbolBase," -> ",SymbolBase,CustomSuffix," ===");

   TickImportResult res;
   bool ok=RunTickImport(SymbolBase,res,Group,OriginSuffix,CustomSuffix,
                         BatchSize,ProgressEveryN,MaxBadLines,DeleteIfExists,BuildM1Bars);

   if(!ok)
     {
      Print("=== IMPORT FAILED: ",SymbolBase," -> ",res.err," ===");
      return;
     }

   Print("=== IMPORT COMPLETE: ",SymbolBase,CustomSuffix," ===");
   Print("  total ticks : ",res.ticks);
   Print("  malformed   : ",res.bad," (tolerance ",MaxBadLines,")");
   Print("  first tick  : ",res.first);
   Print("  last  tick  : ",res.last);
   Print("  M1 bars     : ",res.m1,"  H4: ",res.h4,"  D1: ",res.d1);
   Print("  elapsed     : ",DoubleToString(res.seconds,1)," s");
   Print("  NEXT: run VerifyImport (TargetSymbol=",SymbolBase,CustomSuffix,") for QA.");
  }
//+------------------------------------------------------------------+
