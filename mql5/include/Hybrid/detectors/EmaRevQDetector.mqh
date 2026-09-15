//+------------------------------------------------------------------+
//|                              Hybrid\detectors\EmaRevQDetector.mqh |
//|   Strategy 4 - EMArevQ: EMA20 mean-reversion, QUIET-TRIGGER ONLY. |
//|   (coach lineup ruling 2026-09-09)                                |
//|                                                                   |
//|   EMArevQ = the frozen base EMArev detector (CEma20MeanRev) PLUS  |
//|   a frozen QUALITY GATE applied at the trigger bar:               |
//|       gate = (B>=12 AND D>=0.56)  OR  strict-candle               |
//|   (coach stretch-scan ruling 2026-09-09: stretch 1.5xATR + this   |
//|   OR gate; validated 2022+ = +0.158R model-4, both halves +).     |
//|                                                                   |
//|   B/D (whole-climb slow drift), ports emarevq_calib_build.py:     |
//|     climb start = the most recent H4 bar (scanning back <=40)     |
//|                   STRICTLY before the trigger whose [low,high]    |
//|                   straddles EMA20; drop if none / trigger touches.|
//|     B = climb length in H4 bars (start..trigger inclusive).       |
//|     D = fraction of close-to-close steps over the climb moving in |
//|         the drift-AWAY direction (long: close falls).             |
//|                                                                   |
//|   strict-candle (re-cut #3 strict variant), ports emarevq_recut3: |
//|     range >= 1.25*ATR(14) AND close in the far QUARTER AND        |
//|     (Form A: sweeps prior-3 extreme, closes beyond open, toward   |
//|      EMA; OR Form B: body-engulf + sweeps the prior bar extreme). |
//|   ATR/EMA are the TRIGGER bar's (own m_hATRq/m_hEMAq handles).    |
//|                                                                   |
//|   PROTOCOL: DISCRETION (ClassifyProtocol takes only SweepMSS/     |
//|   DeepFib in TREND; EMArevQ falls through). Shared graded magic.  |
//+------------------------------------------------------------------+
#ifndef HYBRID_EMAREVQ_DETECTOR_MQH
#define HYBRID_EMAREVQ_DETECTOR_MQH

#include <Hybrid\detectors\EmaDetector.mqh>

class CEma20MeanRevQ : public CEma20MeanRev
  {
private:
   int      QB_MIN;           // climb-length floor (bars)
   double   QD_MIN;           // directional-steadiness floor (fraction)
   int      QCLIMB_CAP;       // max H4 bars scanned back for the EMA20 touch
   double   QSC_ATR;          // strict-candle range floor (x ATR)
   double   m_lastB, m_lastD; // last computed metrics (for the popup comment)
   //--- own EMA20 + ATR14 handles (base's m_hEMA/m_hATR are private). Both indicators are
   //--- deterministic, so independent handles with identical params yield identical values
   //--- -> no coupling to the base, no base-class edit, same parity.
   int      m_hEMAq, m_hATRq;
   string   m_symq;
   //--- one-signal-per-stretch RE-ARM (coach live-defect sanity-check 2026-09-09). The base
   //--- CEma20MeanRev emits on a reversal trigger then resets to IDLE and can re-form and
   //--- re-emit while the stretch persists (same class of defect as TrendCont: a declined popup
   //--- opens no position, so the one-setup lock never suppresses the repeat). Fix in the
   //--- SUBCLASS only (base + the stretch-scan cohort stay frozen): after any emit, DISARM until
   //--- price returns to TOUCH EMA20 (the mean the trade targets) -> a genuinely new stretch.
   //--- The touch is tracked on EVERY closed bar, before the base's trigger-only return.
   bool     m_armed;          // true = a fresh emit is allowed; false = waiting for a mean touch
   datetime m_lastbar_q;      // new-bar guard for the re-arm touch check

public:
   CEma20MeanRevQ(double stretch_min=2.0,double adx_ceiling=30.0,double min_rr=1.3,double risk_pct=0.01)
     : CEma20MeanRev(stretch_min,adx_ceiling,min_rr,risk_pct)
     {
      QB_MIN=12; QD_MIN=0.56; QCLIMB_CAP=40; QSC_ATR=1.25; m_lastB=0; m_lastD=0;
      m_hEMAq=INVALID_HANDLE; m_hATRq=INVALID_HANDLE; m_symq="";
      m_armed=true; m_lastbar_q=0;
     }

   //--- Name() is virtual (ISignalDetector): the base Emit sets out.strategy=Name(),
   //--- so a base-EMArev emit reaching here is already labelled "EMArevQ".
   string Name(void) { return "EMArevQ"; }

   bool Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out)
     {
      //--- RE-ARM (one signal per stretch): on every new closed bar, if we are disarmed and price
      //--- has returned to touch EMA20, re-arm. Runs BEFORE the base return because the base emits
      //--- only on trigger bars, whereas the mean touch typically occurs on a non-trigger bar.
      TrackRearm(symbol,tf);
      //--- run the frozen base EMArev state machine; it emits only on a trigger bar.
      if(!CEma20MeanRev::Detect(symbol,tf,out)) return false;
      //--- base fired: out is filled (strategy already "EMArevQ" via virtual Name()).
      //--- frozen OR gate: (B>=12 AND D>=0.56) OR strict-candle. Both computed on the
      //--- trigger bar; either qualifies.
      double B=0,D=0;
      bool haveBD=ClimbBD(symbol,tf,out.direction,B,D);
      bool bAndD=(haveBD && B>=QB_MIN && D>=QD_MIN);
      m_lastB=B; m_lastD=D;
      bool strict=StrictCandle(symbol,tf,out.direction);
      if(!(bAndD || strict)) { ResetCandidate(out); return false; }   // neither leg -> suppress
      //--- RE-ARM gate: suppress a gated emit while disarmed (a prior stretch signal has not yet
      //--- been superseded by a mean touch). Closes the re-fire-across-bars leak.
      if(!m_armed) { ResetCandidate(out); return false; }
      out.comment=out.comment+StringFormat(" | gate:%s%s%s (B=%d D=%.2f)",
                    (bAndD?"B&D":""),(bAndD&&strict?"+":""),(strict?"strict":""),(int)B,D);
      //--- DISARM until the next mean touch: one signal per stretch.
      m_armed=false;
      return true;
     }

private:
   //--- Track the one-per-stretch re-arm: once per newly-closed bar, if disarmed and the just-
   //--- closed bar's [low,high] straddles EMA20 (price returned to the mean), re-arm. Straddle is
   //--- direction-independent and matches the ClimbBD climb-start touch definition.
   void TrackRearm(const string sym,ENUM_TIMEFRAMES tf)
     {
      datetime b1=iTime(sym,tf,1);
      if(b1<=0 || b1==m_lastbar_q) return;    // once per closed bar
      m_lastbar_q=b1;
      if(m_armed) return;                      // already armed -> nothing to do
      if(m_hEMAq==INVALID_HANDLE || sym!=m_symq)
        { m_hEMAq=iMA(sym,tf,20,0,MODE_EMA,PRICE_CLOSE); m_symq=sym; }
      if(m_hEMAq==INVALID_HANDLE) return;
      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(sym,tf,0,2,r)<2) return;
      double ema[]; ArraySetAsSeries(ema,true);
      if(CopyBuffer(m_hEMAq,0,0,2,ema)<2) return;
      if(r[1].low<=ema[1] && ema[1]<=r[1].high) m_armed=true;   // touched the mean -> re-arm
     }

private:
   //--- Compute whole-climb B and D at the trigger bar (series index 1). Returns false
   //--- if there is no clean climb (no EMA20 touch strictly before the trigger within the
   //--- cap, or the trigger bar itself still straddles EMA20 -> "no_climb" in the Python).
   bool ClimbBD(const string sym,ENUM_TIMEFRAMES tf,int dir,double &B,double &D)
     {
      if(m_hEMAq==INVALID_HANDLE || sym!=m_symq)
        { m_hEMAq=iMA(sym,tf,20,0,MODE_EMA,PRICE_CLOSE); m_symq=sym; }
      if(m_hEMAq==INVALID_HANDLE) return false;
      int need=QCLIMB_CAP+2;               // series indices 0..QCLIMB_CAP+1 => trigger(1)+cap back
      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(sym,tf,0,need,r)<need) return false;
      double ema[]; ArraySetAsSeries(ema,true);
      if(CopyBuffer(m_hEMAq,0,0,need,ema)<need) return false;
      //--- climb start = smallest series index k>=2 (i.e. STRICTLY before the trigger at k=1)
      //--- within the cap whose [low,high] straddles EMA20 at that bar. Mirrors the Python
      //--- scan-back-from-si that drops when the trigger bar itself straddles.
      int start_k=-1;
      for(int k=2;k<=QCLIMB_CAP+1;k++)
        {
         if(r[k].low<=ema[k] && ema[k]<=r[k].high) { start_k=k; break; }
        }
      if(start_k<2) return false;          // no touch found / degenerate -> no clean climb
      //--- B = bars from start(oldest, index start_k) to trigger(index 1) inclusive.
      B=(double)start_k;                   // (start_k - 1) + 1
      //--- D = fraction of chronological close-to-close steps moving drift-away. Chronology
      //--- runs from the OLDER bar (higher index) to the NEWER bar (lower index): the step
      //--- landing on series index k-1 compares close[k-1] (newer) vs close[k] (older).
      //--- "moving with the drift" == close fell for a long (dir>0), rose for a short.
      int steps=start_k-1, dircnt=0;
      for(int k=start_k;k>=2;k--)
        {
         bool fell=(r[k-1].close < r[k].close);   // newer close below older close
         if(fell==(dir>0)) dircnt++;
        }
      D=(steps>0 ? (double)dircnt/steps : 0.0);
      return true;
     }

   //--- strict-candle qualifier at the trigger bar (series idx 1; prior bars 2,3,4). Ports
   //--- emarevq_recut3_candle.qualifies(...,"strict"): range>=1.25*ATR AND far-quarter close
   //--- AND (Form A sweep-of-prior-3 + beyond-open + toward-EMA  OR  Form B engulf + sweep-1).
   //--- ATR and EMA are the TRIGGER bar's own (atr[1], ema[1]) - NOT the base's frozen values.
   bool StrictCandle(const string sym,ENUM_TIMEFRAMES tf,int dir)
     {
      if(m_hEMAq==INVALID_HANDLE || sym!=m_symq)
        { m_hEMAq=iMA(sym,tf,20,0,MODE_EMA,PRICE_CLOSE); m_symq=sym; }
      if(m_hATRq==INVALID_HANDLE || sym!=m_symq)
        { m_hATRq=iATR(sym,tf,14); }
      if(m_hEMAq==INVALID_HANDLE || m_hATRq==INVALID_HANDLE) return false;
      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(sym,tf,0,5,r)<5) return false;          // r[1]=trigger, r[2..4]=prior 3
      double ema[],atr[]; ArraySetAsSeries(ema,true); ArraySetAsSeries(atr,true);
      if(CopyBuffer(m_hEMAq,0,0,2,ema)<2) return false;
      if(CopyBuffer(m_hATRq,0,0,2,atr)<2) return false;
      double O=r[1].open,H=r[1].high,L=r[1].low,C=r[1].close,E=ema[1],A=atr[1];
      if(A<=0.0) return false;
      double rng=H-L;
      if(rng<=0.0) return false;
      if(rng < QSC_ATR*A) return false;                    // range >= 1.25*ATR
      //--- top-level far-QUARTER close (both forms)
      if(dir>0 && !(C >= H-rng/4.0)) return false;
      if(dir<0 && !(C <= L+rng/4.0)) return false;
      if(dir>0)
        {
         double pmin=MathMin(r[2].low,MathMin(r[3].low,r[4].low));
         bool formA=(L<pmin) && (C>O) && (C<E);                         // sweep3 + beyond-open + toward-EMA
         bool engulf=(C>O) && (C>r[2].open) && (O<r[2].close);
         bool formB=engulf && (L<r[2].low);                             // engulf + sweep prior-1 low
         return (formA || formB);
        }
      else
        {
         double pmax=MathMax(r[2].high,MathMax(r[3].high,r[4].high));
         bool formA=(H>pmax) && (C<O) && (C>E);
         bool engulf=(C<O) && (C<r[2].open) && (O>r[2].close);
         bool formB=engulf && (H>r[2].high);
         return (formA || formB);
        }
     }
  };

#endif // HYBRID_EMAREVQ_DETECTOR_MQH
//+------------------------------------------------------------------+
