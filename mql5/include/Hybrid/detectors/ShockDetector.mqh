//+------------------------------------------------------------------+
//|                             Hybrid\detectors\ShockDetector.mqh    |
//|   Strategy 4 (CANDIDATE, backtest-only) - Shock Continuation      |
//|   Spec: docs/strategies/shock-continuation-spec.md                |
//|                                                                  |
//|   A large D1 "shock" candle is the announcement; the days after   |
//|   are repositioning. We DON'T touch the release. We wait for the   |
//|   first H4 pullback that fails to reclaim pre-shock territory, then |
//|   JOIN the shock's direction on resumption via a breakout STOP.    |
//|                                                                  |
//|   Isolation: this detector is only ever registered behind the EA's |
//|   InpUseShock input, which defaults FALSE. The three live detectors |
//|   are untouched; baselines hold.                                   |
//+------------------------------------------------------------------+
#ifndef HYBRID_SHOCK_DETECTOR_MQH
#define HYBRID_SHOCK_DETECTOR_MQH

#include <Hybrid\detectors\DetectorCommon.mqh>

class CShockContinuation : public ISignalDetector
  {
private:
   //--- params (spec defaults; the three grid params come via ctor)
   double   SHOCK_ATR;        // range >= SHOCK_ATR * ATR_D1  (grid: 1.5/1.8/2.2)
   double   SHOCK_BODY_ATR;   // |close-open| >= this * ATR_D1 (alt trigger)
   double   SHOCK_CLOSE_PCT;  // close in top(long)/bottom(short) this fraction of range
   int      SHOCK_LOOKBACK_D1;// consider a shock bar within the last N closed D1 bars
   int      VALID_BARS_H4;    // setup live for N H4 bars after shock close
   int      COOLDOWN_H4;      // no entry inside the first N H4 bars after shock close
   double   PULL_MIN_ATR;     // pullback depth >= this * ATR_H4 (grid: 0.5/0.8/1.2)
   double   TP_MULT;          // TP = entry + this * (shock_high-shock_low) (grid .5/.75/1)
   double   MIN_RR;
   double   SL_BUFFER_ATR;
   int      TRIG_MODE;        // 0 resume_close, 1 reversal_candle
   int      REGIME_LOOKBACK;  // D1 bars for the count-the-closes regime tag
   double   CAL_BLOCK_H;      // forward V within this many h -> do not arm
   double   CAL_CAUTION_H;    // forward V within this many h -> caution (still arm)
   bool     CAL_GATE;         // apply the block (else log only)
   double   RISK_PCT;
   int      ATR_PERIOD;

   //--- handles / state
   int      m_hATR_h4, m_hATR_d1;
   bool     m_init;
   string   m_sym, m_base, m_quote;
   datetime m_last_bar;

   //--- shock + forming context (long case; short mirror)
   int      m_state;          // 0 IDLE, 1 WAIT (pullback+resume)
   int      m_dir;
   datetime m_shock_time;     // close time of the shock D1 bar (identity + dedup)
   datetime m_last_shock;     // last shock we armed off / rejected (dedup)
   double   m_shock_hi, m_shock_lo, m_shock_mid, m_pre_level, m_atr_h4_frozen;
   double   m_shock_range_atr, m_shock_body_atr;
   double   m_post_ext;       // running post-shock extreme (high for long)
   double   m_pull_ext;       // pullback extreme (lowest low since last post_ext)
   int      m_bars_since;     // H4 bars since shock close
   bool     m_sched;          // shock coincided with a scheduled V/C event
   int      m_regime;         // +1 trend-aligned, -1 counter, 0 range

   //--- econ feed (self-contained; reads the baked `class` column)
   datetime m_ev_t[];         // event times (symbol ccys or All), sorted asc
   uchar    m_ev_v[];         // 1 = class V (violation), 2 = class C (caution), else 0
   bool     m_ev_loaded;

   //--- diagnostic funnel
   int      m_dbg_calls,m_dbg_newbar,m_dbg_shock,m_dbg_arm,m_dbg_pull,m_dbg_trig,
            m_dbg_emit,m_dbg_calblock,m_dbg_rrrej,m_dbg_sizerej,m_dbg_holdfail,m_dbg_expire;

public:
   CShockContinuation(double shock_atr=1.8,double pull_min_atr=0.8,double tp_mult=0.75,
                      double min_rr=2.0,double risk_pct=0.01,int trig_mode=0,bool cal_gate=true)
     {
      SHOCK_ATR=shock_atr; SHOCK_BODY_ATR=1.5; SHOCK_CLOSE_PCT=0.35; SHOCK_LOOKBACK_D1=3;
      VALID_BARS_H4=18; COOLDOWN_H4=1; PULL_MIN_ATR=pull_min_atr; TP_MULT=tp_mult;
      MIN_RR=min_rr; SL_BUFFER_ATR=0.10; TRIG_MODE=trig_mode; REGIME_LOOKBACK=5;
      CAL_BLOCK_H=6.0; CAL_CAUTION_H=12.0; CAL_GATE=cal_gate; RISK_PCT=risk_pct; ATR_PERIOD=14;
      m_hATR_h4=INVALID_HANDLE; m_hATR_d1=INVALID_HANDLE;
      m_init=false; m_sym=""; m_base=""; m_quote=""; m_last_bar=0; m_last_shock=0;
      m_ev_loaded=false;
      ResetCtx();
      m_dbg_calls=0;m_dbg_newbar=0;m_dbg_shock=0;m_dbg_arm=0;m_dbg_pull=0;m_dbg_trig=0;
      m_dbg_emit=0;m_dbg_calblock=0;m_dbg_rrrej=0;m_dbg_sizerej=0;m_dbg_holdfail=0;m_dbg_expire=0;
     }
  ~CShockContinuation()
     {
      PrintFormat("SHOCK funnel: calls=%d newbar=%d shockseen=%d armed=%d pullOK=%d trig=%d EMIT=%d | calblock=%d rrrej=%d sizerej=%d holdfail=%d expire=%d",
                  m_dbg_calls,m_dbg_newbar,m_dbg_shock,m_dbg_arm,m_dbg_pull,m_dbg_trig,m_dbg_emit,
                  m_dbg_calblock,m_dbg_rrrej,m_dbg_sizerej,m_dbg_holdfail,m_dbg_expire);
     }
   string Name(void) { return "ShockCont"; }

private:
   void ResetCtx()
     {
      m_state=0; m_dir=0; m_shock_time=0; m_shock_hi=0; m_shock_lo=0; m_shock_mid=0;
      m_pre_level=0; m_atr_h4_frozen=0; m_shock_range_atr=0; m_shock_body_atr=0;
      m_post_ext=0; m_pull_ext=0; m_bars_since=0; m_sched=false; m_regime=0;
     }
   bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
     {
      if(m_init && sym==m_sym) return true;
      m_sym=sym;
      string core=sym; int dot=StringFind(core,"."); if(dot>0) core=StringSubstr(core,0,dot);
      StringToUpper(core);
      m_base =(StringLen(core)>=6? StringSubstr(core,0,3):"");
      m_quote=(StringLen(core)>=6? StringSubstr(core,3,3):"");
      m_hATR_h4=iATR(sym,tf,ATR_PERIOD);
      m_hATR_d1=iATR(sym,PERIOD_D1,ATR_PERIOD);
      if(m_hATR_h4==INVALID_HANDLE||m_hATR_d1==INVALID_HANDLE) return false;
      m_init=true;
      return true;
     }

   //================= self-contained econ reader (baked `class` column) =========
   void LoadEcon()
     {
      m_ev_loaded=true;
      int h=FileOpen("econ_events.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
      if(h==INVALID_HANDLE){ Print("ShockCont: no econ_events.csv (calendar gate inert), err ",GetLastError()); return; }
      //--- header: detect the class column (7 cols) vs legacy (6)
      string hdr[]; int ncol=0;
      for(int k=0;k<8 && !FileIsEnding(h);k++){ string f=FileReadString(h); ArrayResize(hdr,k+1); hdr[k]=f; ncol++; if(FileIsLineEnding(h)) break; }
      bool has_class=(ncol>=7);
      while(!FileIsEnding(h))
        {
         string sdt=FileReadString(h);
         if(sdt==""){ if(FileIsEnding(h)) break; else continue; }
         string ccy=FileReadString(h);
         FileReadString(h);          // event
         FileReadString(h);          // actual
         FileReadString(h);          // forecast
         FileReadString(h);          // ccy_bias
         string cls=(has_class? FileReadString(h):""); StringToUpper(cls);
         if(ccy!=m_base && ccy!=m_quote && ccy!="All") continue;    // this pair or all-ccy
         uchar v=(cls=="V"?1:(cls=="C"?2:0));
         if(v==0) continue;          // only V/C matter for the gate + scheduled tag
         datetime t=StringToTime(sdt);
         if(t<=0) continue;
         int m=ArraySize(m_ev_t);
         ArrayResize(m_ev_t,m+1); ArrayResize(m_ev_v,m+1);
         m_ev_t[m]=t; m_ev_v[m]=v;
        }
      FileClose(h);
      PrintFormat("ShockCont econ: %d V/C events for %s/%s loaded",ArraySize(m_ev_t),m_base,m_quote);
     }
   //--- nearest forward V (class 1) in hours from t; DBL_MAX if none within look
   double ForwardVHours(datetime t,double look_h)
     {
      double best=DBL_MAX;
      datetime end=t+(datetime)((long)(look_h*3600.0));
      for(int i=0;i<ArraySize(m_ev_t);i++)
        {
         if(m_ev_v[i]!=1) continue;
         if(m_ev_t[i]>t && m_ev_t[i]<=end)
           { double hh=(double)(m_ev_t[i]-t)/3600.0; if(hh<best) best=hh; }
        }
      return best;
     }
   //--- did a V/C event fall on the shock D1 bar's day [open,close)?
   bool ScheduledShock(datetime shock_open,datetime shock_close)
     {
      for(int i=0;i<ArraySize(m_ev_t);i++)
         if(m_ev_t[i]>=shock_open && m_ev_t[i]<shock_close) return true;
      return false;
     }

   //================= shock detection on D1 ====================================
   //--- find the most recent qualifying D1 shock within lookback; returns its
   //--- as-series index in d1[] (0 = most recent closed D1), or -1.
   int FindShock(const MqlRates &d1[],const double &atrd1[],int &dir_out)
     {
      int lb=MathMin(SHOCK_LOOKBACK_D1,ArraySize(d1));
      for(int i=0;i<lb;i++)
        {
         double atr=atrd1[i]; if(atr<=0.0) continue;
         double rng=d1[i].high-d1[i].low; if(rng<=0.0) continue;
         double body=MathAbs(d1[i].close-d1[i].open);
         bool big=(rng>=SHOCK_ATR*atr) || (body>=SHOCK_BODY_ATR*atr);
         if(!big) continue;
         int dir=(d1[i].close>d1[i].open?1:(d1[i].close<d1[i].open?-1:0));
         if(dir==0) continue;
         bool close_ok=(dir>0)
                       ? (d1[i].close >= d1[i].low + (1.0-SHOCK_CLOSE_PCT)*rng)   // top 35%
                       : (d1[i].close <= d1[i].low + SHOCK_CLOSE_PCT*rng);         // bottom 35%
         if(!close_ok) continue;
         dir_out=dir;
         return i;
        }
      return -1;
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
      if(!m_ev_loaded) LoadEcon();

      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(symbol,tf,0,10,r)<10) return false;
      double atrh4[]; ArraySetAsSeries(atrh4,true);
      if(CopyBuffer(m_hATR_h4,0,0,3,atrh4)<3) return false;
      double ATR_H4=atrh4[1];
      if(ATR_H4<=0.0) return false;

      MqlRates d1[]; ArraySetAsSeries(d1,true);
      int need=SHOCK_LOOKBACK_D1+REGIME_LOOKBACK+2;   // shock bar + its regime window
      if(CopyRates(symbol,PERIOD_D1,1,need,d1)<need) return false;
      double atrd1[]; ArraySetAsSeries(atrd1,true);
      if(CopyBuffer(m_hATR_d1,0,1,SHOCK_LOOKBACK_D1+1,atrd1)<SHOCK_LOOKBACK_D1) return false;

      //================= WAIT (pullback + resume) =================
      if(m_state==1)
        {
         m_bars_since++;
         //--- (b) validity window expired
         if(m_bars_since>VALID_BARS_H4){ m_dbg_expire++; m_last_shock=m_shock_time; ResetCtx(); return false; }
         //--- (c) opposite shock appears
         int od; int oi=FindShock(d1,atrd1,od);
         if(oi>=0 && od==-m_dir && d1[oi].time!=m_shock_time){ m_last_shock=m_shock_time; ResetCtx(); return false; }
         //--- (a) hold check: an H4 CLOSE beyond hold_level (shock_mid) kills it
         bool hold_break=(m_dir>0 ? (r[1].close<m_shock_mid) : (r[1].close>m_shock_mid));
         if(hold_break){ m_dbg_holdfail++; m_last_shock=m_shock_time; ResetCtx(); return false; }

         //--- track post-shock extreme + pullback trough
         if(m_dir>0){ if(r[1].high>m_post_ext){ m_post_ext=r[1].high; m_pull_ext=r[1].low; } else m_pull_ext=MathMin(m_pull_ext,r[1].low); }
         else       { if(r[1].low <m_post_ext){ m_post_ext=r[1].low;  m_pull_ext=r[1].high;} else m_pull_ext=MathMax(m_pull_ext,r[1].high);}

         //--- cooldown: no arm inside the first COOLDOWN_H4 bars after shock close
         if(m_bars_since<=COOLDOWN_H4) return false;

         //--- pullback of required depth formed?
         double depth=(m_dir>0? m_post_ext-m_pull_ext : m_pull_ext-m_post_ext);
         if(depth < PULL_MIN_ATR*ATR_H4) return false;
         m_dbg_pull++;

         //--- resumption trigger on this closed H4 bar
         double pull_mid=(m_dir>0? (m_post_ext+m_pull_ext)/2.0 : (m_pull_ext+m_post_ext)/2.0);
         bool trig;
         if(TRIG_MODE==0)   // resume_close: close beyond the prior H4 extreme
            trig=(m_dir>0 ? r[1].close>r[2].high : r[1].close<r[2].low);
         else               // reversal_candle beyond the pullback midpoint
            trig=(m_dir>0 ? ((DC_BullEngulf(r)||DC_BullPin(r,1.5)) && r[1].close>pull_mid)
                          : ((DC_BearEngulf(r)||DC_BearPin(r,1.5)) && r[1].close<pull_mid));
         if(!trig) return false;
         m_dbg_trig++;

         bool e=Emit(symbol,tf,r,ATR_H4,out);
         return e;
        }

      //================= IDLE -> arm on a fresh shock =================
      int dir=0; int si=FindShock(d1,atrd1,dir);
      if(si<0) return false;
      m_dbg_shock++;
      if(d1[si].time==m_last_shock) return false;         // already handled this shock
      //--- only arm once the shock bar has actually closed (si is a closed D1 bar,
      //--- CopyRates start=1 guarantees that) and we're inside the validity window.
      m_state=1; m_dir=dir; m_shock_time=d1[si].time;
      m_shock_hi=d1[si].high; m_shock_lo=d1[si].low; m_shock_mid=(d1[si].high+d1[si].low)/2.0;
      m_pre_level=d1[si].open; m_atr_h4_frozen=ATR_H4;
      double srng=d1[si].high-d1[si].low; double atr=atrd1[si];
      m_shock_range_atr=(atr>0? srng/atr:0.0);
      m_shock_body_atr =(atr>0? MathAbs(d1[si].close-d1[si].open)/atr:0.0);
      m_post_ext=(dir>0? m_shock_hi : m_shock_lo);
      m_pull_ext=(dir>0? m_shock_lo : m_shock_hi);
      //--- H4 bars elapsed since shock close (validity clock)
      m_bars_since=(int)((r[1].time-m_shock_time)/(PeriodSeconds(tf)));
      if(m_bars_since<0) m_bars_since=0;
      //--- scheduled tag: any V/C event on the shock D1 day
      datetime sopen=m_shock_time-(datetime)PeriodSeconds(PERIOD_D1);
      m_sched=ScheduledShock(sopen,m_shock_time);
      //--- regime: count-the-closes over REGIME_LOOKBACK D1 bars ending at the shock
      int ri=si+REGIME_LOOKBACK;
      if(ri<ArraySize(d1)){ double delta=d1[si].close-d1[ri].close;
                            m_regime=(delta>0?1:(delta<0?-1:0));
                            m_regime=(m_regime==dir?1:(m_regime==0?0:-1)); }
      m_dbg_arm++;
      return false;   // WAIT now; emit on a later resumption trigger
     }

private:
   bool Emit(const string sym,ENUM_TIMEFRAMES tf,const MqlRates &r[],double atr_h4,SignalCandidate &out)
     {
      double buf=DC_Buffer(sym,m_atr_h4_frozen,SL_BUFFER_ATR);
      double entry,sl,risk,tp,rr;
      if(m_dir>0)
        {
         entry=r[1].high+buf;                 // buy-stop above the trigger bar
         sl=m_pull_ext-buf; risk=entry-sl; if(risk<=0.0){ ResetCtx(); return false; }
         tp=entry+TP_MULT*(m_shock_hi-m_shock_lo); rr=(tp-entry)/risk;
        }
      else
        {
         entry=r[1].low-buf;                  // sell-stop below the trigger bar
         sl=m_pull_ext+buf; risk=sl-entry; if(risk<=0.0){ ResetCtx(); return false; }
         tp=entry-TP_MULT*(m_shock_hi-m_shock_lo); rr=(entry-tp)/risk;
        }
      if(rr<MIN_RR){ m_dbg_rrrej++; m_last_shock=m_shock_time; ResetCtx(); return false; }

      //--- calendar gate: forward V within CAL_BLOCK_H -> do not arm
      double vh=ForwardVHours(r[1].time,CAL_CAUTION_H);
      string cal="clear";
      if(vh<=CAL_BLOCK_H){ cal="blocked";
         if(CAL_GATE){ m_dbg_calblock++; m_last_shock=m_shock_time; ResetCtx(); return false; } }
      else if(vh<=CAL_CAUTION_H) cal="caution";

      if(LotsForRisk(sym,entry,sl,RISK_PCT)<DC_VolMin(sym)){ m_dbg_sizerej++; m_last_shock=m_shock_time; ResetCtx(); return false; }

      out.valid=true; out.strategy=Name(); out.direction=m_dir;
      out.entry=entry; out.sl=sl; out.tp=tp; out.rr=rr;
      out.tp1=0; out.tp2=0; out.partial_fraction=0.0;   // single-target (clean stats)
      out.stop_entry=true;                              // route through the pending buy/sell-STOP path
      out.d1_context=(m_regime>0);
      double depth=(m_dir>0? m_post_ext-m_pull_ext : m_pull_ext-m_post_ext);
      string sregime=(m_regime>0?"trend":(m_regime<0?"counter":"range"));
      out.comment=StringFormat("shock %.1fATR %s%s | pull %.1fATR | %s | cal:%s",
                               m_shock_range_atr,(m_dir>0?"up":"dn"),(m_sched?" sched":" unsched"),
                               (atr_h4>0? depth/atr_h4:0.0),sregime,cal);
      out.zone_from=m_shock_time; out.zone_to=iTime(sym,tf,1);   // zone_to == journal signal_time
      out.zone_hi=m_shock_hi; out.zone_lo=m_shock_lo;
      DC_AddAux(out,m_shock_hi,"shock high");
      DC_AddAux(out,m_shock_lo,"shock low");
      DC_AddAux(out,m_shock_mid,"hold (mid)");
      DC_AddAux(out,m_pull_ext,"pullback ext");

      WriteMeta(out.zone_to,sym,depth,atr_h4,sregime,cal,entry,sl,tp,rr);
      m_dbg_emit++;
      m_last_shock=m_shock_time;
      ResetCtx();
      return true;
     }

   //--- ShockCont-only sidecar keyed by (signal_time, symbol); Python joins it to
   //--- the journal for the scheduled/unscheduled and regime splits. Touches no
   //--- shared writer. Header written once (truncate) on first emit.
   void WriteMeta(datetime sigtime,const string sym,double depth,double atr_h4,
                  const string regime,const string cal,double entry,double sl,double tp,double rr)
     {
      string path="journal\\shock_meta.csv";
      int h;
      static bool s_hdr=false;
      FolderCreate("journal",FILE_COMMON);   // the EA's own journal write creates this;
                                             // WriteMeta must too, else FileOpen fails
                                             // silently when the folder was cleaned away.
      if(!s_hdr)
        {
         h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
         if(h!=INVALID_HANDLE){ FileWriteString(h,"signal_time,symbol,direction,shock_time,shock_range_atr,shock_body_atr,scheduled,regime,pullback_atr,cal_flag,entry,sl,tp,rr\r\n"); FileClose(h); s_hdr=true; }
        }
      h=FileOpen(path,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(h==INVALID_HANDLE) return;
      FileSeek(h,0,SEEK_END);
      FileWriteString(h,StringFormat("%s,%s,%d,%s,%.2f,%.2f,%s,%s,%.2f,%s,%.5f,%.5f,%.5f,%.2f\r\n",
                      TimeToString(sigtime,TIME_DATE|TIME_MINUTES),sym,m_dir,
                      TimeToString(m_shock_time,TIME_DATE|TIME_MINUTES),
                      m_shock_range_atr,m_shock_body_atr,(m_sched?"sched":"unsched"),
                      regime,(atr_h4>0? depth/atr_h4:0.0),cal,entry,sl,tp,rr));
      FileClose(h);
     }
  };

#endif // HYBRID_SHOCK_DETECTOR_MQH
//+------------------------------------------------------------------+
