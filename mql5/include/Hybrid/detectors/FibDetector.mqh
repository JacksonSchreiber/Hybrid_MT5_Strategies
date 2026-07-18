//+------------------------------------------------------------------+
//|                               Hybrid\detectors\FibDetector.mqh    |
//|      Strategy 2 - Deep Fibonacci Retracement (CDeepFibRetrace)    |
//|      Build sheet: docs/strategies/impl-deep-fib-retracement.md    |
//+------------------------------------------------------------------+
#ifndef HYBRID_FIB_DETECTOR_MQH
#define HYBRID_FIB_DETECTOR_MQH

#include <Hybrid\detectors\DetectorCommon.mqh>

class CDeepFibRetrace : public ISignalDetector
  {
private:
   int      FRACTAL_N, ATR_PERIOD, EMA_PERIOD, EMA_SLOPE_BARS, ENTRY_VALID_BARS;
   double   IMPULSE_MIN_ATR, FIB_LO, FIB_HI, SL_FIB, SL_BUFFER_ATR, TP_RUNNER_EXT, MIN_RR, PIN_WICK_RATIO, RISK_PCT;

   int      m_hATR, m_hEMA, m_hEMA_D1;
   bool     m_init; string m_sym;
   datetime m_last_bar;

   int      m_state;      // 0 IDLE, 1 FORMING
   int      m_dir;
   double   m_L0, m_L100, m_atr_frozen;
   datetime m_t0, m_t100;
   int      m_bars_forming;
   //--- diag
   int      m_dbg_bars, m_dbg_trend, m_dbg_leg, m_dbg_forming, m_dbg_tag, m_dbg_trig, m_dbg_skip, m_dbg_emit;

public:
   CDeepFibRetrace(double impulse_min_atr=2.0,double min_rr=2.0,double risk_pct=0.01)
     {
      FRACTAL_N=2; ATR_PERIOD=14; EMA_PERIOD=50; EMA_SLOPE_BARS=5; ENTRY_VALID_BARS=4;
      IMPULSE_MIN_ATR=impulse_min_atr; FIB_LO=0.618; FIB_HI=0.786; SL_FIB=0.886;
      SL_BUFFER_ATR=0.10; TP_RUNNER_EXT=1.618; MIN_RR=min_rr; PIN_WICK_RATIO=1.5; RISK_PCT=risk_pct;
      m_hATR=INVALID_HANDLE; m_hEMA=INVALID_HANDLE; m_hEMA_D1=INVALID_HANDLE;
      m_init=false; m_sym=""; m_last_bar=0; ResetCtx();
      m_dbg_bars=0;m_dbg_trend=0;m_dbg_leg=0;m_dbg_forming=0;m_dbg_tag=0;m_dbg_trig=0;m_dbg_skip=0;m_dbg_emit=0;
     }
  ~CDeepFibRetrace()
     { PrintFormat("FIB funnel: bars=%d trend=%d leg=%d forming=%d tag=%d trigger=%d skip=%d EMIT=%d",
                   m_dbg_bars,m_dbg_trend,m_dbg_leg,m_dbg_forming,m_dbg_tag,m_dbg_trig,m_dbg_skip,m_dbg_emit); }
   string Name(void) { return "DeepFib"; }

private:
   void ResetCtx(){ m_state=0; m_dir=0; m_L0=0; m_L100=0; m_atr_frozen=0; m_t0=0; m_t100=0; m_bars_forming=0; }
   double Level(double f){ return m_L100 + f*(m_L0-m_L100); }
   bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
     {
      if(m_init && sym==m_sym) return true;
      m_sym=sym;
      m_hATR=iATR(sym,tf,ATR_PERIOD);
      m_hEMA=iMA(sym,tf,EMA_PERIOD,0,MODE_EMA,PRICE_CLOSE);
      m_hEMA_D1=iMA(sym,PERIOD_D1,EMA_PERIOD,0,MODE_EMA,PRICE_CLOSE);
      if(m_hATR==INVALID_HANDLE||m_hEMA==INVALID_HANDLE) return false;
      m_init=true; return true;
     }
   bool D1Bias(const string sym,int dir)
     {
      double e[]; ArraySetAsSeries(e,true);
      if(m_hEMA_D1==INVALID_HANDLE || CopyBuffer(m_hEMA_D1,0,0,3,e)<3) return false;
      double c1=iClose(sym,PERIOD_D1,1);
      if(c1<=0.0) return false;
      return (dir>0 ? c1>e[1] : c1<e[1]);
     }

public:
   bool Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out)
     {
      ResetCandidate(out);
      datetime b1=iTime(symbol,tf,1);
      if(b1<=0 || b1==m_last_bar) return false;
      m_last_bar=b1;
      if(!EnsureHandles(symbol,tf)) return false;

      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(symbol,tf,0,90,r)<90) return false;
      double ema[],atr[]; ArraySetAsSeries(ema,true); ArraySetAsSeries(atr,true);
      if(CopyBuffer(m_hEMA,0,0,EMA_SLOPE_BARS+3,ema)<EMA_SLOPE_BARS+3) return false;
      if(CopyBuffer(m_hATR,0,0,3,atr)<3) return false;
      double ATR=atr[1]; if(ATR<=0.0) return false;
      m_dbg_bars++;

      //--- confirmed swings (newest first)
      double hip[16],lop[16]; int hii[16],loi[16],nh,nl;
      DC_CollectSwings(r,FRACTAL_N,75,hip,hii,nh,lop,loi,nl,16);

      //--- trend gate (structure + EMA regime/slope)
      bool struct_up  =(nh>=2 && nl>=2 && hip[0]>hip[1] && lop[0]>lop[1]);
      bool struct_dn  =(nh>=2 && nl>=2 && hip[0]<hip[1] && lop[0]<lop[1]);
      bool ema_up=(r[1].close>ema[1] && ema[1]-ema[1+EMA_SLOPE_BARS]>=0.0);
      bool ema_dn=(r[1].close<ema[1] && ema[1]-ema[1+EMA_SLOPE_BARS]<=0.0);
      int dir=0;
      if(struct_up && ema_up) dir=+1; else if(struct_dn && ema_dn) dir=-1;
      if(dir==0) { ResetCtx(); return false; }
      m_dbg_trend++;

      //--- impulse leg: latest completed low->high (up) / high->low (down)
      double L0=0,L100=0; datetime t0=0,t100=0;
      if(dir>0)
        {
         if(nh<1 || nl<1) { ResetCtx(); return false; }
         int hi=hii[0]; L100=r[hi].high; t100=r[hi].time;
         int origin=-1;
         for(int k=0;k<nl;k++) if(loi[k]>hi){ if(origin<0||loi[k]<origin) origin=loi[k]; }
         if(origin<0) { ResetCtx(); return false; }
         L0=r[origin].low; t0=r[origin].time;
         if(L100-L0 < IMPULSE_MIN_ATR*ATR) { ResetCtx(); return false; }
        }
      else
        {
         if(nh<1 || nl<1) { ResetCtx(); return false; }
         int lo=loi[0]; L100=r[lo].low; t100=r[lo].time;
         int origin=-1;
         for(int k=0;k<nh;k++) if(hii[k]>lo){ if(origin<0||hii[k]<origin) origin=hii[k]; }
         if(origin<0) { ResetCtx(); return false; }
         L0=r[origin].high; t0=r[origin].time;
         if(L0-L100 < IMPULSE_MIN_ATR*ATR) { ResetCtx(); return false; }
        }
      m_dbg_leg++;

      //--- (re)anchor FORMING on a new leg
      if(m_state!=1 || m_dir!=dir || t100!=m_t100)
        { m_state=1; m_dir=dir; m_L0=L0; m_L100=L100; m_t0=t0; m_t100=t100; m_bars_forming=0; }
      else m_bars_forming++;
      m_dbg_forming++;

      double buf=DC_Buffer(symbol,ATR,SL_BUFFER_ATR);
      //--- invalidation: closed beyond 0.886 (too deep) or timed out
      if(dir>0 && r[1].close < Level(SL_FIB)-buf) { ResetCtx(); return false; }
      if(dir<0 && r[1].close > Level(SL_FIB)+buf) { ResetCtx(); return false; }
      if(m_bars_forming>ENTRY_VALID_BARS) { ResetCtx(); return false; }

      //--- discount/premium band tag on the trigger bar
      bool tagged=(dir>0 ? r[1].low<=Level(FIB_LO) : r[1].high>=Level(FIB_LO));
      if(!tagged) return false;
      m_dbg_tag++;
      //--- reversal confirmation
      bool rev=(dir>0 ? (DC_BullEngulf(r)||DC_BullPin(r,PIN_WICK_RATIO))
                       : (DC_BearEngulf(r)||DC_BearPin(r,PIN_WICK_RATIO)));
      if(!rev) return false;
      m_dbg_trig++;

      //--- entry/SL/TP
      m_atr_frozen=ATR;
      double entry=DC_Fill(symbol,dir,r[1].close);
      double sl,risk,tp,rr,tp2;
      if(dir>0)
        {
         sl=MathMin(Level(SL_FIB)-buf, r[1].low-buf);
         risk=entry-sl; if(risk<=0.0){ ResetCtx(); return false; }
         tp=m_L100; rr=(tp-entry)/risk;
         tp2=m_L100+(TP_RUNNER_EXT-1.0)*(m_L100-m_L0);
        }
      else
        {
         sl=MathMax(Level(SL_FIB)+buf, r[1].high+buf);
         risk=sl-entry; if(risk<=0.0){ ResetCtx(); return false; }
         tp=m_L100; rr=(entry-tp)/risk;
         tp2=m_L100-(TP_RUNNER_EXT-1.0)*(m_L0-m_L100);
        }
      if(rr<MIN_RR) { m_dbg_skip++; ResetCtx(); return false; }
      if(LotsForRisk(symbol,entry,sl,RISK_PCT)<DC_VolMin(symbol)) { m_dbg_skip++; ResetCtx(); return false; }

      out.valid=true; out.strategy=Name(); out.direction=dir;
      out.entry=entry; out.sl=sl; out.tp=tp; out.rr=rr;
      out.tp1=m_L100; out.tp2=tp2; out.partial_fraction=0.5;
      out.d1_context=D1Bias(symbol,dir);
      out.comment=StringFormat("%s @ deep discount%s",(dir>0?"bull reversal":"bear reversal"),
                               (out.d1_context?", D1 bias aligned":", counter-D1"));
      out.zone_from=m_t100; out.zone_to=iTime(symbol,tf,1);
      double b618=Level(FIB_LO), b786=Level(FIB_HI);
      out.zone_hi=MathMax(b618,b786); out.zone_lo=MathMin(b618,b786);
      out.leg_t0=m_t0; out.leg_p0=m_L0; out.leg_t1=m_t100; out.leg_p1=m_L100;
      //--- 0.5/0.618/0.786/0.886 are now drawn by the native OBJ_FIBO grid
      //--- (anchored on this leg, labelled); keep the non-fib aux levels.
      DC_AddAux(out,ema[1],"EMA50");
      DC_AddAux(out,tp2,"TP2 ext 1.618");
      //--- confirmed swings for the "swing high"/"swing low" overlay markers
      DC_FillSwings(out,r,hip,hii,nh,lop,loi,nl,6);
      m_dbg_emit++;
      ResetCtx();
      return true;
     }
  };

#endif // HYBRID_FIB_DETECTOR_MQH
//+------------------------------------------------------------------+
