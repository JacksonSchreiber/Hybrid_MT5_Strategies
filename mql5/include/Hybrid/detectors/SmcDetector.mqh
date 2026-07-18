//+------------------------------------------------------------------+
//|                               Hybrid\detectors\SmcDetector.mqh    |
//|      Strategy 1 - Liquidity Sweep & MSS (CLiquiditySweepMSS)      |
//|      Build sheet: docs/strategies/impl-liquidity-sweep-mss.md     |
//|      PM decision 2: SMC = SINGLE TP (no scale-out).               |
//+------------------------------------------------------------------+
#ifndef HYBRID_SMC_DETECTOR_MQH
#define HYBRID_SMC_DETECTOR_MQH

#include <Hybrid\detectors\DetectorCommon.mqh>

class CLiquiditySweepMSS : public ISignalDetector
  {
private:
   int      FRACTAL_N, ATR_PERIOD, LIQ_LOOKBACK, SWEEP_MAX_BARS, MSS_MAX_BARS, ENTRY_VALID_BARS;
   double   EQ_TOL_ATR, SWEEP_MIN_ATR, SWEEP_MAX_ATR, SL_BUFFER_ATR, MIN_RR, TP_R, TP1_R, RISK_PCT;

   int      m_hATR; bool m_init; string m_sym; datetime m_last_bar;

   int      m_state;          // 0 IDLE, 1 FORMING, 2 ARMED
   int      m_dir;
   double   m_pool_level, m_sweep_extreme, m_atr_frozen, m_ref_swing;
   int      m_pool_type;      // 0 swing,1 EQL,2 PDL/PDH,3 PWL/PWH
   datetime m_sweep_time;
   int      m_bars_since_sweep;
   double   m_ob_hi, m_ob_lo; datetime m_ob_t0;
   datetime m_mss_time;
   int      m_bars_since_arm;
   bool     m_d1;
   //--- diag
   int      m_dbg_bars,m_dbg_sweep,m_dbg_mss,m_dbg_arm,m_dbg_emit;

public:
   CLiquiditySweepMSS(double min_rr=2.0,double tp_r=3.0,double risk_pct=0.01)
     {
      FRACTAL_N=2; ATR_PERIOD=14; LIQ_LOOKBACK=20; SWEEP_MAX_BARS=1; MSS_MAX_BARS=6; ENTRY_VALID_BARS=8;
      EQ_TOL_ATR=0.10; SWEEP_MIN_ATR=0.05; SWEEP_MAX_ATR=1.2; SL_BUFFER_ATR=0.10;
      MIN_RR=min_rr; TP_R=tp_r; TP1_R=2.0; RISK_PCT=risk_pct;
      m_hATR=INVALID_HANDLE; m_init=false; m_sym=""; m_last_bar=0; ResetCtx();
      m_dbg_bars=0;m_dbg_sweep=0;m_dbg_mss=0;m_dbg_arm=0;m_dbg_emit=0;
     }
  ~CLiquiditySweepMSS()
     { PrintFormat("SMC funnel: bars=%d sweep=%d mss=%d arm=%d EMIT=%d",
                   m_dbg_bars,m_dbg_sweep,m_dbg_mss,m_dbg_arm,m_dbg_emit); }
   string Name(void) { return "SweepMSS"; }

private:
   void ResetCtx()
     {
      m_state=0; m_dir=0; m_pool_level=0; m_sweep_extreme=0; m_atr_frozen=0; m_ref_swing=0;
      m_pool_type=0; m_sweep_time=0; m_bars_since_sweep=0; m_ob_hi=0; m_ob_lo=0; m_ob_t0=0;
      m_mss_time=0; m_bars_since_arm=0; m_d1=false;
     }
   bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
     {
      if(m_init && sym==m_sym) return true;
      m_sym=sym; m_hATR=iATR(sym,tf,ATR_PERIOD);
      if(m_hATR==INVALID_HANDLE) return false;
      m_init=true; return true;
     }
   //--- nearest opposing liquidity above (long) / below (short) entry, for TP
   double OpposingLiquidity(const string sym,int dir,double entry,const double &hip[],int nh,const double &lop[],int nl)
     {
      double best=0.0;
      if(dir>0)
        {
         for(int k=0;k<nh;k++) if(hip[k]>entry && (best==0.0||hip[k]<best)) best=hip[k];
         double pdh=iHigh(sym,PERIOD_D1,1), pwh=iHigh(sym,PERIOD_W1,1);
         if(pdh>entry && (best==0.0||pdh<best)) best=pdh;
         if(pwh>entry && (best==0.0||pwh<best)) best=pwh;
        }
      else
        {
         for(int k=0;k<nl;k++) if(lop[k]<entry && (best==0.0||lop[k]>best)) best=lop[k];
         double pdl=iLow(sym,PERIOD_D1,1), pwl=iLow(sym,PERIOD_W1,1);
         if(pdl<entry && (best==0.0||pdl>best)) best=pdl;
         if(pwl<entry && (best==0.0||pwl>best)) best=pwl;
        }
      return best;
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
      int need=LIQ_LOOKBACK+MSS_MAX_BARS+2*FRACTAL_N+10;
      if(CopyRates(symbol,tf,0,need,r)<need) return false;
      double atr[]; ArraySetAsSeries(atr,true);
      if(CopyBuffer(m_hATR,0,0,3,atr)<3) return false;
      double ATR=atr[1]; if(ATR<=0.0) return false;
      m_dbg_bars++;

      double hip[24],lop[24]; int hii[24],loi[24],nh,nl;
      DC_CollectSwings(r,FRACTAL_N,LIQ_LOOKBACK+MSS_MAX_BARS+2,hip,hii,nh,lop,loi,nl,24);
      double buf=DC_Buffer(symbol,(m_atr_frozen>0?m_atr_frozen:ATR),SL_BUFFER_ATR);

      //================= ARMED: wait for pullback into OB =================
      if(m_state==2)
        {
         m_bars_since_arm++;
         bool dead=(m_dir>0 ? r[1].close<m_sweep_extreme-buf : r[1].close>m_sweep_extreme+buf);
         bool mitigated=(m_dir>0 ? r[1].close<m_ob_lo : r[1].close>m_ob_hi);
         if(dead||mitigated||m_bars_since_arm>ENTRY_VALID_BARS) { ResetCtx(); return false; }
         double prox=(m_dir>0? m_ob_hi : m_ob_lo);
         bool pulled=(m_dir>0 ? r[1].low<=prox : r[1].high>=prox);
         if(!pulled) return false;
         bool ok=EmitSmc(symbol,tf,hip,nh,lop,nl,out);
         //--- confirmed swings for the "swing high"/"swing low" overlay markers
         if(ok) DC_FillSwings(out,r,hip,hii,nh,lop,loi,nl,6);
         return ok;
        }

      //================= FORMING: wait for MSS =================
      if(m_state==1)
        {
         m_bars_since_sweep++;
         if(m_dir>0 && r[1].close<m_sweep_extreme-buf) { ResetCtx(); return false; }
         if(m_dir<0 && r[1].close>m_sweep_extreme+buf) { ResetCtx(); return false; }
         if(m_bars_since_sweep>MSS_MAX_BARS) { ResetCtx(); return false; }
         bool mss=(m_dir>0 ? r[1].close>m_ref_swing : r[1].close<m_ref_swing);
         if(!mss) return false;
         //--- displacement quality
         if(MathAbs(r[1].close-r[1].open) < 0.5*m_atr_frozen) { ResetCtx(); return false; }
         //--- order block = last opposing-close candle before the displacement bar
         int ob=-1;
         for(int k=1;k<=MSS_MAX_BARS+2 && k<ArraySize(r);k++)
           { if(m_dir>0 && r[k].close<r[k].open){ ob=k; break; }
             if(m_dir<0 && r[k].close>r[k].open){ ob=k; break; } }
         if(ob<0) { ResetCtx(); return false; }
         m_ob_hi=r[ob].high; m_ob_lo=r[ob].low; m_ob_t0=r[ob].time;
         m_mss_time=r[1].time;
         //--- prospective RR check at OB proximal edge
         double prox=(m_dir>0? m_ob_hi : m_ob_lo);
         double sl=(m_dir>0? m_sweep_extreme-buf : m_sweep_extreme+buf);
         double risk=(m_dir>0? prox-sl : sl-prox);
         if(risk<=0.0) { ResetCtx(); return false; }
         double liq=OpposingLiquidity(symbol,m_dir,prox,hip,nh,lop,nl);
         double tp=(liq>0.0 && ((m_dir>0?(liq-prox):(prox-liq))>=MIN_RR*risk)) ? liq
                   : (m_dir>0? prox+TP_R*risk : prox-TP_R*risk);
         double rr=(m_dir>0?(tp-prox):(prox-tp))/risk;
         if(rr<MIN_RR || LotsForRisk(symbol,prox,sl,RISK_PCT)<DC_VolMin(symbol)) { ResetCtx(); return false; }
         double pdl=iLow(symbol,PERIOD_D1,1), pwl=iLow(symbol,PERIOD_W1,1);
         double pdh=iHigh(symbol,PERIOD_D1,1), pwh=iHigh(symbol,PERIOD_W1,1);
         m_d1=(m_dir>0 ? (MathAbs(m_pool_level-pdl)<=EQ_TOL_ATR*m_atr_frozen||MathAbs(m_pool_level-pwl)<=EQ_TOL_ATR*m_atr_frozen)
                        : (MathAbs(m_pool_level-pdh)<=EQ_TOL_ATR*m_atr_frozen||MathAbs(m_pool_level-pwh)<=EQ_TOL_ATR*m_atr_frozen));
         m_state=2; m_bars_since_arm=0; m_dbg_mss++; m_dbg_arm++;
         return false;   // ARMED; emit on pullback
        }

      //================= IDLE -> FORMING: detect a sweep on bar 1 =================
      //--- candidate pools BELOW (for long) / ABOVE (for short)
      double pdl=iLow(symbol,PERIOD_D1,1), pwl=iLow(symbol,PERIOD_W1,1);
      double pdh=iHigh(symbol,PERIOD_D1,1), pwh=iHigh(symbol,PERIOD_W1,1);
      //--- try LONG sweep (swept a low pool)
      if(TrySweep(symbol,+1,r,ATR,lop,loi,nl,pdl,pwl)) { m_dbg_sweep++; return false; }
      if(TrySweep(symbol,-1,r,ATR,hip,hii,nh,pdh,pwh)) { m_dbg_sweep++; return false; }
      return false;
     }

private:
   //--- test bar 1 for a valid sweep of a pool; on pass set FORMING
   bool TrySweep(const string sym,int dir,const MqlRates &r[],double ATR,
                 const double &sw[],const int &swi[],int nsw,double lvlA,double lvlB)
     {
      //--- assemble candidate pool levels (recent swings + PDL/PWL or PDH/PWH)
      double cand[26]; int types[26]; int nc=0;
      for(int k=0;k<nsw && nc<24;k++) if(swi[k]<=LIQ_LOOKBACK+FRACTAL_N) { cand[nc]=sw[k]; types[nc]=0; nc++; }
      if(lvlA>0){ cand[nc]=lvlA; types[nc]=2; nc++; }
      if(lvlB>0){ cand[nc]=lvlB; types[nc]=3; nc++; }

      for(int i=0;i<nc;i++)
        {
         double pool=cand[i];
         bool penetrate=(dir>0 ? r[1].low<pool  : r[1].high>pool);
         bool reclaim  =(dir>0 ? r[1].close>pool: r[1].close<pool);
         double depth  =(dir>0 ? pool-r[1].low  : r[1].high-pool);
         if(!penetrate||!reclaim) continue;
         if(depth<SWEEP_MIN_ATR*ATR || depth>SWEEP_MAX_ATR*ATR) continue;
         //--- valid sweep -> FORMING; reference swing = most recent opposing fractal
         m_state=1; m_dir=dir; m_atr_frozen=ATR;
         m_sweep_extreme=(dir>0? r[1].low : r[1].high); m_pool_level=pool; m_pool_type=types[i];
         m_sweep_time=r[1].time; m_bars_since_sweep=0;
         //--- ref swing to break for MSS = nearest opposing fractal beyond bar 2
         double ref=0;
         for(int c=1+FRACTAL_N;c<ArraySize(r)-FRACTAL_N;c++)
           { if(dir>0 && DC_IsSwingHigh(r,c,FRACTAL_N)){ ref=r[c].high; break; }
             if(dir<0 && DC_IsSwingLow (r,c,FRACTAL_N)){ ref=r[c].low;  break; } }
         if(ref==0){ ResetCtx(); return false; }
         m_ref_swing=ref;
         return true;
        }
      return false;
     }

   bool EmitSmc(const string sym,ENUM_TIMEFRAMES tf,const double &hip[],int nh,const double &lop[],int nl,SignalCandidate &out)
     {
      double buf=DC_Buffer(sym,m_atr_frozen,SL_BUFFER_ATR);
      double entry=DC_Fill(sym,m_dir,iClose(sym,tf,1));
      double sl=(m_dir>0? m_sweep_extreme-buf : m_sweep_extreme+buf);
      double risk=(m_dir>0? entry-sl : sl-entry);
      if(risk<=0.0){ ResetCtx(); return false; }
      double liq=OpposingLiquidity(sym,m_dir,entry,hip,nh,lop,nl);
      double tp=(liq>0.0 && ((m_dir>0?(liq-entry):(entry-liq))>=MIN_RR*risk)) ? liq
                : (m_dir>0? entry+TP_R*risk : entry-TP_R*risk);
      double rr=(m_dir>0?(tp-entry):(entry-tp))/risk;
      if(rr<MIN_RR || LotsForRisk(sym,entry,sl,RISK_PCT)<DC_VolMin(sym)){ ResetCtx(); return false; }

      out.valid=true; out.strategy=Name(); out.direction=m_dir;
      out.entry=entry; out.sl=sl; out.tp=tp; out.rr=rr;
      out.tp1=(m_dir>0? entry+TP1_R*risk : entry-TP1_R*risk); out.tp2=tp;
      out.partial_fraction=0.0;                 // PM decision 2: SMC single TP (no scale-out)
      out.d1_context=m_d1;
      out.comment=StringFormat("swept %s%s",PoolTypeStr(m_pool_type),(m_d1?" + D1 confluence":""));
      out.zone_from=m_ob_t0; out.zone_to=iTime(sym,tf,1);
      out.zone_hi=m_ob_hi; out.zone_lo=m_ob_lo;
      DC_AddAux(out,m_pool_level,"swept "+PoolTypeStr(m_pool_type));
      DC_AddAux(out,m_ref_swing,"MSS level");
      DC_AddAux(out,m_sweep_extreme,(m_dir>0?"sweep low":"sweep high"));
      DC_AddAux(out,(m_ob_hi+m_ob_lo)/2.0,"OB equilibrium");
      DC_AddAux(out,out.tp1,"TP1 2R");
      m_dbg_emit++;
      ResetCtx();
      return true;
     }
   string PoolTypeStr(int t){ return (t==0?"swing":(t==1?"EQL":(t==2?"PD-level":"PW-level"))); }
  };

#endif // HYBRID_SMC_DETECTOR_MQH
//+------------------------------------------------------------------+
