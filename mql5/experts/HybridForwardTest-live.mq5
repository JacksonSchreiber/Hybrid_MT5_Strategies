//+------------------------------------------------------------------+
//|                                     HybridForwardTest-live.mq5    |
//|  Phase-3 LIVE build target (docs/phase3-live-system-requirements  |
//|  §10 C1). SINGLE SOURCE: this wrapper only defines LIVE and       |
//|  includes the one EA source, so the tester build and the live     |
//|  build can never drift. LIVE removes the popup-DLL #import block  |
//|  (E2) — everything else is byte-identical to HybridForwardTest.   |
//|  #property lines must be repeated here: #property inside an       |
//|  included file is ignored by the compiler.                        |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "2.00"
#property description "Phase 3 LIVE build: same EA source, popup DLL not loaded (file-queue path)."
#property tester_indicator "HybridTriEMA.ex5"

#define LIVE
#include "HybridForwardTest.mq5"
