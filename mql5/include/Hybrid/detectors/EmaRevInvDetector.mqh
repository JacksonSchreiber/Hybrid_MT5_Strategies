//+------------------------------------------------------------------+
//|                          Hybrid\detectors\EmaRevInvDetector.mqh   |
//|   EMArev-Inverse ("ride the stretch") - SIGNAL LOGGER, backtest   |
//|   Spec: docs/strategies/emarev-inverse-spec.md                    |
//|                                                                  |
//|   Reuses the FROZEN CEma20MeanRev signal stream unmodified (wraps |
//|   it, never edits it) so the population is exactly what the trader |
//|   sees. On each EMArev ARMED signal it does NOT trade - it LOGS    |
//|   the inverted-continuation setup geometry + flags to a sidecar,   |
//|   and the 6 (entry x exit) cells are simulated in Python. The      |
//|   stretch extreme is tracked here directly (NOT derived from the   |
//|   fade SL, whose buffer carries un-reconstructable live spread).   |
//|                                                                  |
//|   Isolation: registered only behind InpUseEmaRevInv (default OFF). |
//+------------------------------------------------------------------+
#ifndef HYBRID_EMAREVINV_DETECTOR_MQH
#define HYBRID_EMAREVINV_DETECTOR_MQH

#include <Hybrid\detectors\DetectorCommon.mqh>
#include <Hybrid\detectors\EmaDetector.mqh>

class CEmaRevInverse : public ISignalDetector
  {
private:
   CEma20MeanRev *m_ema;      // the FROZEN detector, wrapped (decides WHEN a signal is)
   double   STRETCH_MIN;
   int      EMA_PERIOD, ATR_PERIOD;
   int      SHOCK_LOOKBACK_D1;
   double   SHOCK_ATR, SHOCK_BODY_ATR;

   int      m_hEMA, m_hATR, m_hATR_d1;
   bool     m_init;
   string   m_sym, m_base, m_quote;
   ENUM_TIMEFRAMES m_tf;
   datetime m_last_bar;

   //--- own stretch-extreme tracker (mirrors EMArev's forming low/high)
   int      m_epi_dir;        // +1 stretched below (ext=low), -1 above (ext=high), 0 none
   double   m_ext;            // running extreme of the current stretch episode

   //--- econ feed (class column), self-contained
   datetime m_ev_t[]; uchar m_ev_v[]; string m_ev_ccy[]; bool m_ev_loaded;
   int      m_nsig;

public:
   CEmaRevInverse(double stretch_min=2.0,double adx_ceiling=30.0,double min_rr=1.3,double risk_pct=0.01)
     {
      m_ema=new CEma20MeanRev(stretch_min,adx_ceiling,min_rr,risk_pct);
      STRETCH_MIN=stretch_min; EMA_PERIOD=20; ATR_PERIOD=14;
      SHOCK_LOOKBACK_D1=3; SHOCK_ATR=1.8; SHOCK_BODY_ATR=1.5;
      m_hEMA=INVALID_HANDLE; m_hATR=INVALID_HANDLE; m_hATR_d1=INVALID_HANDLE;
      m_init=false; m_sym=""; m_base=""; m_quote=""; m_tf=PERIOD_H4; m_last_bar=0;
      m_epi_dir=0; m_ext=0; m_ev_loaded=false; m_nsig=0;
     }
  ~CEmaRevInverse()
     {
      DumpH4();                       // one-shot bar dump for the Python exit sim
      PrintFormat("EMArevINV: %d signals logged",m_nsig);
      if(m_ema!=NULL){ delete m_ema; m_ema=NULL; }
     }
   string Name(void) { return "EmaRevInv"; }

private:
   bool EnsureHandles(const string sym,ENUM_TIMEFRAMES tf)
     {
      if(m_init && sym==m_sym) return true;
      m_sym=sym; m_tf=tf;
      string core=sym; int dot=StringFind(core,"."); if(dot>0) core=StringSubstr(core,0,dot);
      StringToUpper(core);
      m_base =(StringLen(core)>=6? StringSubstr(core,0,3):"");
      m_quote=(StringLen(core)>=6? StringSubstr(core,3,3):"");
      m_hEMA=iMA(sym,tf,EMA_PERIOD,0,MODE_EMA,PRICE_CLOSE);
      m_hATR=iATR(sym,tf,ATR_PERIOD);
      m_hATR_d1=iATR(sym,PERIOD_D1,ATR_PERIOD);
      if(m_hEMA==INVALID_HANDLE||m_hATR==INVALID_HANDLE||m_hATR_d1==INVALID_HANDLE) return false;
      m_init=true; return true;
     }

   //--- econ reader (baked class column: V=1, C=2) filtered to this pair + All
   void LoadEcon()
     {
      m_ev_loaded=true;
      int h=FileOpen("econ_events.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
      if(h==INVALID_HANDLE) return;
      string hdr[]; int ncol=0;
      for(int k=0;k<8 && !FileIsEnding(h);k++){ string f=FileReadString(h); ArrayResize(hdr,k+1); hdr[k]=f; ncol++; if(FileIsLineEnding(h)) break; }
      bool has_class=(ncol>=7);
      while(!FileIsEnding(h))
        {
         string sdt=FileReadString(h);
         if(sdt==""){ if(FileIsEnding(h)) break; else continue; }
         string ccy=FileReadString(h);
         FileReadString(h); FileReadString(h); FileReadString(h); FileReadString(h); // event,actual,forecast,ccy_bias
         string cls=(has_class? FileReadString(h):""); StringToUpper(cls);
         uchar v=(cls=="V"?1:(cls=="C"?2:(cls=="W"?3:0)));
         if(v==0) continue;
         if(ccy!=m_base && ccy!=m_quote && ccy!="All") continue;
         datetime t=StringToTime(sdt); if(t<=0) continue;
         int m=ArraySize(m_ev_t); ArrayResize(m_ev_t,m+1); ArrayResize(m_ev_v,m+1); ArrayResize(m_ev_ccy,m+1);
         m_ev_t[m]=t; m_ev_v[m]=v; m_ev_ccy[m]=ccy;
        }
      FileClose(h);
     }
   double ForwardVHours(datetime t)      // hours to nearest forward V within 12h, else 999
     {
      double best=999.0; datetime end=t+12*3600;
      for(int i=0;i<ArraySize(m_ev_t);i++)
         if(m_ev_v[i]==1 && m_ev_t[i]>t && m_ev_t[i]<=end){ double hh=(double)(m_ev_t[i]-t)/3600.0; if(hh<best) best=hh; }
      return best;
     }
   bool WInHold(datetime t)              // a W-class event in the next 3 days
     {
      datetime end=t+3*86400;
      for(int i=0;i<ArraySize(m_ev_t);i++) if(m_ev_v[i]==3 && m_ev_t[i]>t && m_ev_t[i]<=end) return true;
      return false;
     }
   bool SchedStretch(datetime t)         // V/C event in the 3 days up to the signal (stretch-maker)
     {
      datetime start=t-3*86400;
      for(int i=0;i<ArraySize(m_ev_t);i++) if((m_ev_v[i]==1||m_ev_v[i]==2) && m_ev_t[i]>=start && m_ev_t[i]<=t) return true;
      return false;
     }
   bool ShockFlag(const string sym)      // D1 bar >=1.8xATR range OR >=1.5x body in last 3 closed D1
     {
      MqlRates d1[]; ArraySetAsSeries(d1,true);
      if(CopyRates(sym,PERIOD_D1,1,SHOCK_LOOKBACK_D1,d1)<SHOCK_LOOKBACK_D1) return false;
      double a[]; ArraySetAsSeries(a,true);
      if(CopyBuffer(m_hATR_d1,0,1,SHOCK_LOOKBACK_D1,a)<SHOCK_LOOKBACK_D1) return false;
      for(int i=0;i<SHOCK_LOOKBACK_D1;i++)
        {
         if(a[i]<=0) continue;
         double rng=d1[i].high-d1[i].low, body=MathAbs(d1[i].close-d1[i].open);
         if(rng>=SHOCK_ATR*a[i] || body>=SHOCK_BODY_ATR*a[i]) return true;
        }
      return false;
     }

public:
   bool Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out)
     {
      ResetCandidate(out);
      datetime b1=iTime(symbol,tf,1);
      if(b1<=0 || b1==m_last_bar){ return false; }
      m_last_bar=b1;
      if(!EnsureHandles(symbol,tf)) return false;
      if(!m_ev_loaded) LoadEcon();

      double ema[],atr[]; ArraySetAsSeries(ema,true); ArraySetAsSeries(atr,true);
      if(CopyBuffer(m_hEMA,0,0,3,ema)<3) return false;
      if(CopyBuffer(m_hATR,0,0,3,atr)<3) return false;
      MqlRates r[]; ArraySetAsSeries(r,true);
      if(CopyRates(symbol,tf,0,3,r)<3) return false;
      double ATR=atr[1]; if(ATR<=0) return false;

      //--- update own stretch-episode extreme (mirrors EMArev forming low/high)
      double sl_long=(ema[1]-r[1].close)/ATR;   // stretched BELOW mean (fade dir +1)
      double sl_short=(r[1].close-ema[1])/ATR;   // stretched ABOVE mean (fade dir -1)
      if(sl_long>=STRETCH_MIN)
        { if(m_epi_dir!=1){ m_epi_dir=1; m_ext=r[1].low; } else m_ext=MathMin(m_ext,r[1].low); }
      else if(sl_short>=STRETCH_MIN)
        { if(m_epi_dir!=-1){ m_epi_dir=-1; m_ext=r[1].high; } else m_ext=MathMax(m_ext,r[1].high); }
      else m_epi_dir=0;

      //--- run the FROZEN detector; a valid fade candidate == an EMArev ARMED signal
      SignalCandidate fade; ResetCandidate(fade);
      if(!m_ema.Detect(symbol,tf,fade)) return false;
      if(!fade.valid) return false;

      int emarev_dir=fade.direction;            // fade direction
      int inv_dir=-emarev_dir;                   // continuation direction (we ride the stretch)
      double extreme=(m_epi_dir!=0? m_ext : (emarev_dir>0? r[1].low : r[1].high));
      datetime sigt=iTime(symbol,tf,1);
      WriteSignal(sigt,symbol,emarev_dir,inv_dir,ema[1],ATR,extreme,r[1].close,
                  ForwardVHours(sigt),(WInHold(sigt)?1:0),(ShockFlag(symbol)?1:0),(SchedStretch(sigt)?1:0));
      return false;                              // logger only - never places a trade
     }

private:
   void WriteSignal(datetime sigt,const string sym,int edir,int idir,double ema20,double atr,
                    double extreme,double trig_close,double cal_v_h,int w_hold,int shock,int sched)
     {
      string path="journal\\emarev_inv_signals.csv";
      FolderCreate("journal",FILE_COMMON);
      static bool s_hdr=false;
      int h;
      if(!s_hdr)
        {
         h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
         if(h!=INVALID_HANDLE){ FileWriteString(h,"signal_time,symbol,emarev_dir,inv_dir,ema20,atr_h4,extreme,trigger_close,cal_v_hours,w_in_hold,shock_flag,sched\r\n"); FileClose(h); s_hdr=true; }
        }
      h=FileOpen(path,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(h==INVALID_HANDLE) return;
      FileSeek(h,0,SEEK_END);
      FileWriteString(h,StringFormat("%s,%s,%d,%d,%.5f,%.5f,%.5f,%.5f,%.2f,%d,%d,%d\r\n",
                      TimeToString(sigt,TIME_DATE|TIME_MINUTES),sym,edir,idir,ema20,atr,extreme,trig_close,
                      cal_v_h,w_hold,shock,sched));
      FileClose(h);
      m_nsig++;
     }
   //--- one-shot: dump the full H4 OHLC series so Python can simulate the exits
   void DumpH4()
     {
      if(m_sym=="") return;
      MqlRates r[]; ArraySetAsSeries(r,false);
      int got=CopyRates(m_sym,m_tf,0,200000,r);
      if(got<=0) return;
      string base=m_sym; int dot=StringFind(base,"."); if(dot>0) base=StringSubstr(base,0,dot);
      string path="journal\\h4bars_"+base+".csv";
      FolderCreate("journal",FILE_COMMON);
      int h=FileOpen(path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(h==INVALID_HANDLE) return;
      FileWriteString(h,"time,open,high,low,close\r\n");
      for(int i=0;i<got;i++)
         FileWriteString(h,StringFormat("%s,%.5f,%.5f,%.5f,%.5f\r\n",
                         TimeToString(r[i].time,TIME_DATE|TIME_MINUTES),r[i].open,r[i].high,r[i].low,r[i].close));
      FileClose(h);
      PrintFormat("EMArevINV: dumped %d H4 bars -> %s",got,path);
     }
  };

#endif // HYBRID_EMAREVINV_DETECTOR_MQH
//+------------------------------------------------------------------+
