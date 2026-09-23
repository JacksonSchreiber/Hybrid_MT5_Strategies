//+------------------------------------------------------------------+
//|                          Hybrid\detectors\TrendContDetector.mqh   |
//|   Trend-continuation candidate (CTrendContinuation, "TrendCont")  |
//|   FROZEN 2026-09-09: docs/strategies/trendcont-frozen-spec.md     |
//|   D1 machine-tagged TREND -> H4 pullback to EMA20 (<=3 bars) ->    |
//|   a bullish/bearish close resuming the trend. Structural SL beyond |
//|   the 3-bar swing; R-based 2R/4R ladder (v2 banks at +1R); 1% risk.|
//|   LIVE detector (window-11+ era lineup: SMC,Fib,TrendCont,EMArevQ).|
//|   Per-pullback RE-ARM (coach live-defect fix 2026-09-09, tightened  |
//|   2026-09-10): one signal per pullback -- after any emit, disarm    |
//|   until a COMPLETED micro-cycle: a resume leg (close >=0.75xATR      |
//|   beyond EMA20) on one bar, THEN a touch of EMA20 on a LATER bar     |
//|   (one bar may not be both). A D1 trend FLIP also re-arms. Without   |
//|   it a declined popup (no position -> no one-setup lock) re-fires    |
//|   every bar.                                                        |
//+------------------------------------------------------------------+
#ifndef HYBRID_TRENDCONT_DETECTOR_MQH
#define HYBRID_TRENDCONT_DETECTOR_MQH

#include <Hybrid\detectors\DetectorCommon.mqh>

class CTrendContinuation : public ISignalDetector
  {
private:
   //--- FROZEN constants (spec). No ctor knobs beyond risk.
   int    EMA_H4;        // 20
   int    ATR_PERIOD;    // 14
   int    EMA_D1;        // 200
   int    ADX_D1;        // 14
   int    D1_SLOPE_BACK; // 10  (EMA200(1) vs EMA200(11))
   double ADX_MIN;       // 20
   int    PB_LOOKBACK;   // 3   pullback window / SL swing
   double SL_BUFFER_ATR; // 0.10
   double TP1_R, TP2_R;  // 2R, 4R
   double RISK_PCT;

   double REARM_ATR;     // 0.75  resume-leg distance (x ATR) for the per-pullback re-arm

   int    m_hEMA, m_hATR, m_hDEMA, m_hDADX;
   bool   m_init;
   string m_sym;
   datetime m_last_bar;
   //--- per-pullback RE-ARM state (coach live-defect fix 2026-09-09). Without it TrendCont
   //--- re-fires every bar while pullback+resume persist and no position opens (declined popup:
   //--- no position -> no one-setup lock -> nothing suppresses the repeat). One-signal-per-
   //--- pullback: after any emit, DISARM (m_block_dir = the trend dir at emit); re-ARM only once
   //--- price has (a) pushed a resume leg >= REARM_ATR*ATR beyond EMA20 in-trend AND then
   //--- (b) returned to touch EMA20 -> a genuinely new pullback. A D1 trend FLIP (not a mere
   //--- loss) also re-arms immediately. Applies identically offline/AA/live (one detector).
   int    m_block_dir;   // 0 = armed; +1/-1 = disarmed, trend dir at the emit that disarmed it
   bool   m_resumed;     // has the resume leg been seen since the disarm (within the same trend)
   int    m_dbg_calls, m_dbg_trend, m_dbg_pull, m_dbg_resume, m_dbg_emit, m_dbg_rej;

public:
   CTrendContinuation(double risk_pct=0.01)
     {
      EMA_H4=20; ATR_PERIOD=14; EMA_D1=200; ADX_D1=14; D1_SLOPE_BACK=10;
      ADX_MIN=20.0; PB_LOOKBACK=3; SL_BUFFER_ATR=0.10; TP1_R=2.0; TP2_R=4.0;
      RISK_PCT=risk_pct; REARM_ATR=0.75;
      m_hEMA=INVALID_HANDLE; m_hATR=INVALID_HANDLE; m_hDEMA=INVALID_HANDLE; m_hDADX=INVALID_HANDLE;
      m_init=false; m_sym=""; m_last_bar=0; m_block_dir=0; m_resumed=false;
      m_dbg_calls=0; m_dbg_trend=0; m_dbg_pull=0; m_dbg_resume=0; m_dbg_emit=0; m_dbg_rej=0;
     }
  ~CTrendContinuation()
     {
      PrintFormat("TrendCont funnel: calls=%d trend=%d pullback=%d resume=%d rej=%d EMIT=%d",
                  m_dbg_calls,m_dbg_trend,m_dbg_pull,m_dbg_resume,m_dbg_rej,m_dbg_emit);
     }
   string Name(void) { return "TrendCont"; }
   string Funnel(void) { return StringFormat("TC[calls=%d trend=%d pull=%d resume=%d rej=%d emit=%d]",m_dbg_calls,m_dbg_trend,m_dbg_pull,m_dbg_resume,m_dbg_rej,m_dbg_emit); }

private:
   bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
     {
      if(m_init && sym==m_sym) return true;
      m_sym=sym;
      m_hEMA =iMA(sym,tf,EMA_H4,0,MODE_EMA,PRICE_CLOSE);
      m_hATR =iATR(sym,tf,ATR_PERIOD);
      m_hDEMA=iMA(sym,PERIOD_D1,EMA_D1,0,MODE_EMA,PRICE_CLOSE);
      m_hDADX=iADX(sym,PERIOD_D1,ADX_D1);
      if(m_hEMA==INVALID_HANDLE||m_hATR==INVALID_HANDLE||m_hDEMA==INVALID_HANDLE||m_hDADX==INVALID_HANDLE)
         return false;
      m_init=true;
      return true;
     }

   //--- D1 regime (the frozen tag formula, D1 shift 1). Returns +1/-1/0.
   int TrendDir(const string sym)
     {
      double dema[],dadx[];
      ArraySetAsSeries(dema,true); ArraySetAsSeries(dadx,true);
      if(CopyBuffer(m_hDEMA,0,1,D1_SLOPE_BACK+1,dema)<D1_SLOPE_BACK+1) return 0; // shifts 1..11
      if(CopyBuffer(m_hDADX,0,1,1,dadx)<1) return 0;
      if(dadx[0]<ADX_MIN) return 0;
      double dc=iClose(sym,PERIOD_D1,1);
      double e1=dema[0], e11=dema[D1_SLOPE_BACK];       // EMA200 now vs 10 D1 bars ago
      if(dc>e1 && e1>e11) return +1;
      if(dc<e1 && e1<e11) return -1;
      return 0;
     }

public:
   bool Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out)
     {
      ResetCandidate(out);
      m_dbg_calls++;
      datetime b1=iTime(symbol,tf,1);
      if(b1<=0 || b1==m_last_bar) return false;
      m_last_bar=b1;
      if(!EnsureHandles(symbol,tf)) return false;

      int dir=TrendDir(symbol);
      //--- RE-ARM (regime FLIP only): a D1 trend flip to the opposite side definitively kills the
      //--- old pullback -> re-arm (and drop any stale resume flag, else a leftover m_resumed would
      //--- rearm on the first touch of the next disarm cycle without a fresh resume leg). A trend
      //--- LOSS (dir==0, e.g. ADX dips below 20 for a day) is NOT a new pullback -- the same H4
      //--- pullback+resume persists -- so it must NOT re-arm; during dir==0 we return below and the
      //--- block is preserved, so re-acquisition on the SAME side still requires a fresh resume
      //--- leg + touch. (coach: "a new signal requires a new pullback".)
      if(m_block_dir!=0 && dir==-m_block_dir) { m_block_dir=0; m_resumed=false; }
      if(dir==0) return false;
      m_dbg_trend++;

      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(symbol,tf,0,6,r)<6) return false;
      double ema[],atr[];
      ArraySetAsSeries(ema,true); ArraySetAsSeries(atr,true);
      if(CopyBuffer(m_hEMA,0,0,5,ema)<5) return false;   // shifts 0..4 -> ema[1..3] usable
      if(CopyBuffer(m_hATR,0,0,3,atr)<3) return false;
      double ATR=atr[1];
      if(ATR<=0.0) return false;

      //--- RE-ARM (same trend still disarmed): a COMPLETED micro-cycle -- leave, THEN return --
      //--- across two SEPARATE bars (coach tighten 2026-09-10). One bar may not serve as both the
      //--- resume leg and the touch: the touch re-arms only if the resume leg was already seen on
      //--- an EARLIER bar (resumed_prev). (a) resume leg: the CLOSE (not a wick) is
      //--- >= REARM_ATR*ATR beyond EMA20 in-trend. (b) touch: price has since returned to EMA20.
      if(m_block_dir==dir)
        {
         bool resumed_prev=m_resumed;                       // resume leg seen on a PRIOR bar?
         double beyond=(dir>0 ? r[1].close-ema[1] : ema[1]-r[1].close);
         if(!m_resumed && beyond>=REARM_ATR*ATR) m_resumed=true;
         bool touch=(dir>0 ? (r[1].low<=ema[1]) : (r[1].high>=ema[1]));
         if(resumed_prev && touch) { m_block_dir=0; m_resumed=false; }   // touch on a LATER bar
        }

      //--- (1) pullback: any of bars 1..3 touched/breached the EMA20 from the trend side.
      bool pullback=false;
      for(int i=1;i<=PB_LOOKBACK;i++)
         if(dir>0 ? (r[i].low<=ema[i]) : (r[i].high>=ema[i])) { pullback=true; break; }
      if(!pullback) return false;
      m_dbg_pull++;

      //--- (2) resume close on the signal bar (shift 1): back on the trend side, with-trend body.
      bool resume=(dir>0 ? (r[1].close>ema[1] && r[1].close>r[1].open)
                         : (r[1].close<ema[1] && r[1].close<r[1].open));
      if(!resume) return false;
      m_dbg_resume++;

      //--- RE-ARM gate: a valid pullback+resume is present, but suppress the emit while disarmed
      //--- (a prior signal on this pullback has not yet been superseded by a new one). This is the
      //--- fix for the declined-popup re-fire: the condition can persist for many bars.
      if(m_block_dir!=0) return false;

      //--- structural SL beyond the 3-bar pullback swing.
      double buf=DC_Buffer(symbol,ATR,SL_BUFFER_ATR);
      double swing=(dir>0? MathMin(r[1].low,MathMin(r[2].low,r[3].low))
                         : MathMax(r[1].high,MathMax(r[2].high,r[3].high)));
      double entry=DC_Fill(symbol,dir,r[1].close);
      double sl=(dir>0? swing-buf : swing+buf);
      double risk=(dir>0? entry-sl : sl-entry);
      if(risk<=0.0) { m_dbg_rej++; return false; }

      //--- R-based ladder (v2 banks at +1R; tp1=2R inert, tp2=4R runner).
      double tp1=(dir>0? entry+TP1_R*risk : entry-TP1_R*risk);
      double tp2=(dir>0? entry+TP2_R*risk : entry-TP2_R*risk);
      if(LotsForRisk(symbol,entry,sl,RISK_PCT)<DC_VolMin(symbol)) { m_dbg_rej++; return false; }

      out.valid=true; out.strategy=Name(); out.direction=dir;
      out.entry=entry; out.sl=sl; out.tp=tp2; out.rr=TP2_R;
      out.tp1=tp1; out.tp2=tp2; out.partial_fraction=0.5;
      out.d1_context=true;
      out.comment=StringFormat("D1 %s trend, pullback->EMA20 resume",(dir>0?"up":"down"));
      out.zone_from=r[PB_LOOKBACK].time; out.zone_to=iTime(symbol,tf,1);
      if(dir>0){ out.zone_hi=ema[1]; out.zone_lo=swing; }
      else     { out.zone_hi=swing; out.zone_lo=ema[1]; }
      DC_AddAux(out,ema[1],"EMA20 (H4)");
      DC_AddAux(out,swing,"pullback swing");
      //--- DISARM: one signal per pullback. Suppress further TrendCont emits until price pushes a
      //--- fresh resume leg beyond EMA20 and then returns to touch it (a new pullback), or the D1
      //--- regime changes. Identical in offline/AA/live so cohorts stay comparable.
      m_block_dir=dir; m_resumed=false;
      m_dbg_emit++;
      return true;
     }
  };

#endif // HYBRID_TRENDCONT_DETECTOR_MQH
//+------------------------------------------------------------------+
