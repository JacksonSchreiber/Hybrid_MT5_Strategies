//+------------------------------------------------------------------+
//|                            Hybrid\detectors\DetectorCommon.mqh    |
//|      FTMO Hybrid Trading System - shared detector primitives       |
//|                                                                  |
//|  Free helpers used by all three real detectors (and the harness   |
//|  for lot sizing, so the 1% rule can't drift). All detection is on  |
//|  CLOSED bars; as-series convention: index 0 = forming, 1 = last    |
//|  closed. See docs/strategies/impl-README.md.                      |
//+------------------------------------------------------------------+
#ifndef HYBRID_DETECTOR_COMMON_MQH
#define HYBRID_DETECTOR_COMMON_MQH

#include <Hybrid\Signal.mqh>

//--- risk-based lot size (floored to step, NO clamp-up). Returns 0 on bad
//--- specs. The harness clamps up with a warning; detectors call this to
//--- PRE-EMPT sub-min-lot setups (skip, never round up -> keeps 1% exact).
double LotsForRisk(const string sym,double entry,double sl,double riskpct)
  {
   double equity   =AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_cash=equity*riskpct;
   double tsize=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   double tval =SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   double dist =MathAbs(entry-sl);
   if(tsize<=0.0 || tval<=0.0 || dist<=0.0) return 0.0;
   double loss_per_lot=(dist/tsize)*tval;
   if(loss_per_lot<=0.0) return 0.0;
   double lots=risk_cash/loss_per_lot;
   double step=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   if(step<=0.0) step=0.01;
   lots=MathFloor(lots/step)*step;
   double vmax=SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX);
   if(vmax>0.0 && lots>vmax) lots=vmax;
   return lots;
  }

double DC_VolMin(const string sym)
  {
   double v=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   return (v>0.0 ? v : 0.01);
  }

//--- SL buffer = max(atr term, live spread, broker stops level)
double DC_Buffer(const string sym,double atr,double sl_buffer_atr)
  {
   double point=SymbolInfoDouble(sym,SYMBOL_POINT);
   if(point<=0.0) point=_Point;
   double spread=SymbolInfoDouble(sym,SYMBOL_ASK)-SymbolInfoDouble(sym,SYMBOL_BID);
   if(spread<0.0) spread=0.0;
   double stops=(double)SymbolInfoInteger(sym,SYMBOL_TRADE_STOPS_LEVEL)*point;
   double atrterm=sl_buffer_atr*atr;
   double b=atrterm;
   if(spread>b) b=spread;
   if(stops>b)  b=stops;
   return b;
  }

//--- expected market fill at emit: Ask for long, Bid for short (fallback close)
double DC_Fill(const string sym,int dir,double fallback)
  {
   double px=(dir>0 ? SymbolInfoDouble(sym,SYMBOL_ASK) : SymbolInfoDouble(sym,SYMBOL_BID));
   if(px<=0.0) px=fallback;
   return px;
  }

//--- STRICT 5-bar fractals (2 left, 2 right by default), strict inequality
bool DC_IsSwingHigh(const MqlRates &r[],int c,int n)
  {
   int sz=ArraySize(r);
   if(c-n<0 || c+n>=sz) return false;
   double h=r[c].high;
   for(int k=1;k<=n;k++)
      if(!(h>r[c-k].high) || !(h>r[c+k].high)) return false;
   return true;
  }
bool DC_IsSwingLow(const MqlRates &r[],int c,int n)
  {
   int sz=ArraySize(r);
   if(c-n<0 || c+n>=sz) return false;
   double l=r[c].low;
   for(int k=1;k<=n;k++)
      if(!(l<r[c-k].low) || !(l<r[c+k].low)) return false;
   return true;
  }

//--- Collect confirmed swings, NEWEST FIRST, over centres c=1+n .. maxc.
//--- earliest confirmable centre = 1+FRACTAL_N (right neighbours all closed).
void DC_CollectSwings(const MqlRates &r[],int n,int maxc,
                      double &hi_px[],int &hi_idx[],int &n_hi,
                      double &lo_px[],int &lo_idx[],int &n_lo,int maxK)
  {
   n_hi=0; n_lo=0;
   int sz=ArraySize(r);
   int lastc=MathMin(maxc,sz-1-n);
   for(int c=1+n;c<=lastc;c++)
     {
      if(n_hi<maxK && DC_IsSwingHigh(r,c,n)) { hi_px[n_hi]=r[c].high; hi_idx[n_hi]=c; n_hi++; }
      if(n_lo<maxK && DC_IsSwingLow(r,c,n))  { lo_px[n_lo]=r[c].low;  lo_idx[n_lo]=c; n_lo++; }
      if(n_hi>=maxK && n_lo>=maxK) break;
     }
  }

//--- explicit, string-safe reset of a candidate. MQL5 does NOT reliably
//--- zero-initialise a locally-declared struct (aux_count etc. can hold stack
//--- garbage -> DC_AddAux would index out of range). Every detector MUST call
//--- this at the top of Detect before filling 'out'. (ZeroMemory is unsafe
//--- here because the struct owns string members.)
void ResetCandidate(SignalCandidate &c)
  {
   c.valid=false; c.strategy=""; c.direction=0;
   c.entry=0; c.sl=0; c.tp=0; c.rr=0;
   c.zone_from=0; c.zone_to=0; c.zone_hi=0; c.zone_lo=0;
   c.tp1=0; c.tp2=0; c.partial_fraction=0; c.d1_context=false; c.comment="";
   c.aux_count=0;
   for(int i=0;i<8;i++) { c.aux_price[i]=0.0; c.aux_label[i]=""; }
   c.zone2_hi=0; c.zone2_lo=0; c.leg_t0=0; c.leg_t1=0; c.leg_p0=0; c.leg_p1=0;
  }

//--- append an aux overlay level (bounded to 8)
void DC_AddAux(SignalCandidate &c,double price,string label)
  {
   if(c.aux_count<0 || c.aux_count>=8) return;
   c.aux_price[c.aux_count]=price;
   c.aux_label[c.aux_count]=label;
   c.aux_count++;
  }

//--- bullish reversal candle helpers (as-series r[], trigger at index 1)
bool DC_BullEngulf(const MqlRates &r[])
  {
   return (r[1].close>r[1].open && r[1].close>r[2].open && r[1].open<r[2].close);
  }
bool DC_BearEngulf(const MqlRates &r[])
  {
   return (r[1].close<r[1].open && r[1].close<r[2].open && r[1].open>r[2].close);
  }
bool DC_BullPin(const MqlRates &r[],double wick_ratio)
  {
   if(r[1].close<=r[1].open) return false;
   double rng=r[1].high-r[1].low;
   if(rng<=0.0) return false;
   double body=MathAbs(r[1].close-r[1].open);
   double lowwick=MathMin(r[1].open,r[1].close)-r[1].low;
   return (r[1].close>=r[1].high-rng/3.0) && (lowwick>=wick_ratio*body);
  }
bool DC_BearPin(const MqlRates &r[],double wick_ratio)
  {
   if(r[1].close>=r[1].open) return false;
   double rng=r[1].high-r[1].low;
   if(rng<=0.0) return false;
   double body=MathAbs(r[1].close-r[1].open);
   double hiwick=r[1].high-MathMax(r[1].open,r[1].close);
   return (r[1].close<=r[1].low+rng/3.0) && (hiwick>=wick_ratio*body);
  }

#endif // HYBRID_DETECTOR_COMMON_MQH
//+------------------------------------------------------------------+
