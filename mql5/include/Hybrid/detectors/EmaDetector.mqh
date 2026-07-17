//+------------------------------------------------------------------+
//|                               Hybrid\detectors\EmaDetector.mqh    |
//|      Strategy 3 - 20 EMA Mean Reversion (CEma20MeanRev)           |
//|      Build sheet: docs/strategies/impl-ema20-mean-reversion.md    |
//+------------------------------------------------------------------+
#ifndef HYBRID_EMA_DETECTOR_MQH
#define HYBRID_EMA_DETECTOR_MQH

#include <Hybrid\detectors\DetectorCommon.mqh>

class CEma20MeanRev : public ISignalDetector
  {
private:
   //--- params (spec defaults; key ones overridable via ctor)
   int      EMA_PERIOD;
   int      ATR_PERIOD;
   double   STRETCH_MIN;
   int      ADX_PERIOD;
   double   ADX_CEILING;
   int      D1_BREAK_LOOKBACK;
   double   PIN_WICK_RATIO;
   int      ENTRY_VALID_BARS;
   double   MAX_EXTEND_ATR;
   double   SL_BUFFER_ATR;
   double   MIN_RR;
   double   RISK_PCT;

   //--- handles / state
   int      m_hEMA, m_hATR, m_hADX;
   bool     m_init;
   string   m_sym;
   datetime m_last_bar;

   //--- forming context
   int      m_state;          // 0 IDLE, 1 FORMING
   int      m_dir;
   double   m_ema_frozen, m_atr_frozen, m_stretch, m_extreme;
   datetime m_extreme_time;
   int      m_bars_forming;
   bool     m_pool;
   //--- diagnostic funnel counters
   int      m_dbg_bars, m_dbg_stretch, m_dbg_adxrej, m_dbg_d1rej, m_dbg_forming,
            m_dbg_trigger, m_dbg_skip, m_dbg_emit;
   int      m_dbg_calls, m_dbg_newbar, m_dbg_handles, m_dbg_barscalc, m_dbg_rates;

public:
   CEma20MeanRev(double stretch_min=2.0,double adx_ceiling=30.0,double min_rr=1.3,double risk_pct=0.01)
     {
      EMA_PERIOD=20; ATR_PERIOD=14; STRETCH_MIN=stretch_min; ADX_PERIOD=14;
      ADX_CEILING=adx_ceiling; D1_BREAK_LOOKBACK=20; PIN_WICK_RATIO=1.5;
      ENTRY_VALID_BARS=3; MAX_EXTEND_ATR=1.0; SL_BUFFER_ATR=0.10; MIN_RR=min_rr; RISK_PCT=risk_pct;
      m_hEMA=INVALID_HANDLE; m_hATR=INVALID_HANDLE; m_hADX=INVALID_HANDLE;
      m_init=false; m_sym=""; m_last_bar=0; ResetCtx();
      m_dbg_bars=0; m_dbg_stretch=0; m_dbg_adxrej=0; m_dbg_d1rej=0;
      m_dbg_forming=0; m_dbg_trigger=0; m_dbg_skip=0; m_dbg_emit=0;
      m_dbg_calls=0; m_dbg_newbar=0; m_dbg_handles=0; m_dbg_barscalc=0; m_dbg_rates=0;
     }
  ~CEma20MeanRev()
     {
      PrintFormat("EMA funnel: calls=%d newbar=%d handles=%d barscalc=%d rates=%d bars=%d stretch=%d adxrej=%d d1rej=%d forming=%d trigger=%d skip=%d EMIT=%d",
                  m_dbg_calls,m_dbg_newbar,m_dbg_handles,m_dbg_barscalc,m_dbg_rates,
                  m_dbg_bars,m_dbg_stretch,m_dbg_adxrej,m_dbg_d1rej,m_dbg_forming,m_dbg_trigger,m_dbg_skip,m_dbg_emit);
     }
   string Name(void) { return "EMArev"; }

private:
   void ResetCtx()
     {
      m_state=0; m_dir=0; m_ema_frozen=0; m_atr_frozen=0; m_stretch=0;
      m_extreme=0; m_extreme_time=0; m_bars_forming=0; m_pool=false;
     }
   bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
     {
      if(m_init && sym==m_sym) return true;
      m_sym=sym;
      m_hEMA=iMA(sym,tf,EMA_PERIOD,0,MODE_EMA,PRICE_CLOSE);
      m_hATR=iATR(sym,tf,ATR_PERIOD);
      m_hADX=iADX(sym,tf,ADX_PERIOD);
      if(m_hEMA==INVALID_HANDLE||m_hATR==INVALID_HANDLE||m_hADX==INVALID_HANDLE) return false;
      m_init=true;
      return true;
     }
   double D1Extreme(const string sym,bool low)
     {
      MqlRates d1[]; ArraySetAsSeries(d1,true);
      if(CopyRates(sym,PERIOD_D1,1,D1_BREAK_LOOKBACK,d1)<D1_BREAK_LOOKBACK)
         return (low? -DBL_MAX : DBL_MAX);   // fail-open: no fresh-break gate if no D1 data
      double ext=(low? DBL_MAX : -DBL_MAX);
      for(int i=0;i<D1_BREAK_LOOKBACK;i++)
         ext=(low? MathMin(ext,d1[i].low) : MathMax(ext,d1[i].high));
      return ext;
     }

public:
   bool Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out)
     {
      ResetCandidate(out);
      m_dbg_calls++;
      datetime b1=iTime(symbol,tf,1);
      if(b1<=0 || b1==m_last_bar) return false;
      m_last_bar=b1;
      m_dbg_newbar++;
      if(!EnsureHandles(symbol,tf)) return false;
      m_dbg_handles++;
      //--- NOTE: do NOT gate on BarsCalculated() before CopyBuffer. In the
      //--- Strategy Tester indicators calculate on-demand when CopyBuffer is
      //--- called, so a BarsCalculated-first guard deadlocks (stays 0 forever).
      //--- Rely on CopyBuffer's return count (< requested => not ready).
      m_dbg_barscalc++;

      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(symbol,tf,0,40,r)<40) return false;
      m_dbg_rates++;
      double ema[],atr[],adx[];
      ArraySetAsSeries(ema,true); ArraySetAsSeries(atr,true); ArraySetAsSeries(adx,true);
      if(CopyBuffer(m_hEMA,0,0,3,ema)<3) return false;
      if(CopyBuffer(m_hATR,0,0,3,atr)<3) return false;
      if(CopyBuffer(m_hADX,0,0,3,adx)<3) return false;
      double ATR=atr[1];
      if(ATR<=0.0) return false;
      m_dbg_bars++;

      //================= FORMING =================
      if(m_state==1)
        {
         m_bars_forming++;
         //--- track a deeper extreme (still forming)
         if(m_dir>0 && r[1].low<m_extreme)  { m_extreme=r[1].low;  m_extreme_time=r[1].time; m_ema_frozen=ema[1]; m_atr_frozen=ATR; }
         if(m_dir<0 && r[1].high>m_extreme) { m_extreme=r[1].high; m_extreme_time=r[1].time; m_ema_frozen=ema[1]; m_atr_frozen=ATR; }
         double buf=DC_Buffer(symbol,m_atr_frozen,SL_BUFFER_ATR);
         //--- invalidation
         bool dead=(m_dir>0 ? (r[1].close<m_extreme-buf) : (r[1].close>m_extreme+buf));
         bool runaway=(m_dir>0 ? (r[1].low <m_extreme-MAX_EXTEND_ATR*m_atr_frozen)
                                : (r[1].high>m_extreme+MAX_EXTEND_ATR*m_atr_frozen));
         bool regimeflip=(adx[1]>=ADX_CEILING);
         if(dead||regimeflip||m_bars_forming>ENTRY_VALID_BARS) { ResetCtx(); return false; }
         if(runaway) { ResetCtx(); return false; }
         //--- reversal trigger
         bool toward=(m_dir>0 ? (r[1].close>r[2].close) : (r[1].close<r[2].close));
         bool rev=(m_dir>0)
                  ? (DC_BullEngulf(r)||DC_BullPin(r,PIN_WICK_RATIO)||r[1].close>r[2].high)
                  : (DC_BearEngulf(r)||DC_BearPin(r,PIN_WICK_RATIO)||r[1].close<r[2].low);
         if(!(rev && toward)) return false;
         m_dbg_trigger++;
         bool e=Emit(symbol,tf,r,adx[1],out);
         if(e) m_dbg_emit++; else m_dbg_skip++;
         return e;
        }

      //================= IDLE -> FORMING =================
      double stretch_long =(ema[1]-r[1].close)/ATR;
      double stretch_short=(r[1].close-ema[1])/ATR;
      int dir=0; double stretch=0.0;
      if(stretch_long>=STRETCH_MIN)      { dir=+1; stretch=stretch_long; }
      else if(stretch_short>=STRETCH_MIN){ dir=-1; stretch=stretch_short; }
      if(dir==0) return false;
      m_dbg_stretch++;
      if(adx[1]>=ADX_CEILING) { m_dbg_adxrej++; return false; }
      //--- D1 fresh-breakout FILTER (approved: hard gate)
      if(dir>0 && r[1].low < D1Extreme(symbol,true))  { m_dbg_d1rej++; return false; }
      if(dir<0 && r[1].high> D1Extreme(symbol,false)) { m_dbg_d1rej++; return false; }
      m_dbg_forming++;

      m_state=1; m_dir=dir; m_stretch=stretch; m_ema_frozen=ema[1]; m_atr_frozen=ATR;
      m_extreme=(dir>0? r[1].low : r[1].high); m_extreme_time=r[1].time; m_bars_forming=0;
      //--- pool confluence flag
      double poolref=(dir>0? MathMin(iLow(symbol,PERIOD_D1,1),iLow(symbol,PERIOD_W1,1))
                            : MathMax(iHigh(symbol,PERIOD_D1,1),iHigh(symbol,PERIOD_W1,1)));
      m_pool=(MathAbs(m_extreme-poolref)<=0.10*m_atr_frozen);
      return false;   // FORMING now; emit on a later trigger bar
     }

private:
   bool Emit(const string sym,ENUM_TIMEFRAMES tf,const MqlRates &r[],double adx1,SignalCandidate &out)
     {
      double buf=DC_Buffer(sym,m_atr_frozen,SL_BUFFER_ATR);
      double entry=DC_Fill(sym,m_dir,r[1].close);
      double sl,risk,tp1,tp2,tp,rr;
      if(m_dir>0)
        {
         sl=m_extreme-buf; risk=entry-sl; if(risk<=0.0){ ResetCtx(); return false; }
         tp1=m_ema_frozen;
         double band=m_ema_frozen+STRETCH_MIN*m_atr_frozen;
         tp2=MathMax(entry+MIN_RR*risk,band); tp=tp2; rr=(tp-entry)/risk;
        }
      else
        {
         sl=m_extreme+buf; risk=sl-entry; if(risk<=0.0){ ResetCtx(); return false; }
         tp1=m_ema_frozen;
         double band=m_ema_frozen-STRETCH_MIN*m_atr_frozen;
         tp2=MathMin(entry-MIN_RR*risk,band); tp=tp2; rr=(entry-tp)/risk;
        }
      if(rr<MIN_RR) { ResetCtx(); return false; }
      if(LotsForRisk(sym,entry,sl,RISK_PCT)<DC_VolMin(sym)) { ResetCtx(); return false; }

      out.valid=true; out.strategy=Name(); out.direction=m_dir;
      out.entry=entry; out.sl=sl; out.tp=tp; out.rr=rr;
      out.tp1=tp1; out.tp2=tp2; out.partial_fraction=0.5;
      out.d1_context=true;   // passed the D1 fresh-break gate => regime clean
      out.comment=StringFormat("stretch %.1f ATR, ADX %.0f%s",m_stretch,adx1,(m_pool?" +pool":""));
      out.zone_from=m_extreme_time; out.zone_to=iTime(sym,tf,1);
      if(m_dir>0){ out.zone_hi=m_ema_frozen; out.zone_lo=m_ema_frozen-STRETCH_MIN*m_atr_frozen; }
      else       { out.zone_hi=m_ema_frozen+STRETCH_MIN*m_atr_frozen; out.zone_lo=m_ema_frozen; }
      DC_AddAux(out,m_ema_frozen,"EMA20 (mean)");
      DC_AddAux(out,m_ema_frozen-STRETCH_MIN*m_atr_frozen,"lower band");
      DC_AddAux(out,m_ema_frozen+STRETCH_MIN*m_atr_frozen,"upper band");
      DC_AddAux(out,m_extreme,"stretch anchor");
      ResetCtx();            // one-shot: back to IDLE for the next scan
      return true;
     }
  };

#endif // HYBRID_EMA_DETECTOR_MQH
//+------------------------------------------------------------------+
