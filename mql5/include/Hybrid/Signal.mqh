//+------------------------------------------------------------------+
//|                                              Hybrid\Signal.mqh    |
//|          FTMO Hybrid Trading System - signal plug-in contract     |
//|                                                                  |
//|  Phase-2 growth seam: the harness (HybridForwardTest.mq5) talks   |
//|  ONLY to the ISignalDetector interface and the SignalCandidate    |
//|  struct. The three real strategies plug in later by implementing  |
//|  ISignalDetector - no change to the harness (overlays, modal,     |
//|  sizing, execution, journal) is required.                        |
//+------------------------------------------------------------------+
#ifndef HYBRID_SIGNAL_MQH
#define HYBRID_SIGNAL_MQH

//+------------------------------------------------------------------+
//| A proposed trade produced by a detector. Prices only - lot size, |
//| overlays, the modal and execution are the harness's job.         |
//+------------------------------------------------------------------+
struct SignalCandidate
  {
   bool     valid;        // false -> no signal on this bar
   string   strategy;     // detector name, shown in modal + journal
   int      direction;    // +1 = buy, -1 = sell
   double   entry;        // proposed entry price
   double   sl;           // stop loss price
   double   tp;           // take profit price
   double   rr;           // reward:risk ratio (tp distance / sl distance)
   datetime zone_from;    // setup-zone left edge (bar time)
   datetime zone_to;      // setup-zone right edge (bar time)
   double   zone_hi;      // setup-zone top price
   double   zone_lo;      // setup-zone bottom price

   //--- Phase-2 extension (all optional/back-compat; default 0/false/"" ⇒
   //--- harness behaves exactly as before). A freshly declared struct is
   //--- auto-zeroed by MQL5, so detectors need only SET what they use.
   //--- scale-out plan: bank partial_fraction at tp1, run remainder to tp2.
   double   tp1;              // first / partial target price (0 = unused)
   double   tp2;              // runner target price          (0 = unused)
   double   partial_fraction;// fraction to bank at tp1 (0 = single target)
   //--- context flag (PM decision 1: FLAG, never a hard filter)
   bool     d1_context;      // true = D1 aligned / confluent (shown, not gated)
   //--- human-readable one-liner for modal + label
   string   comment;
   //--- extra overlay geometry (rendered by the extended DrawOverlays)
   int      aux_count;       // number of aux levels used (<= 8)
   double   aux_price[8];    // horizontal levels
   string   aux_label[8];    // label per aux level
   double   zone2_hi, zone2_lo;  // optional 2nd rectangle (FVG/band); 0 = unused
   datetime leg_t0, leg_t1;      // impulse-leg / structure trendline endpoints; 0 = unused
   double   leg_p0, leg_p1;
  };

//+------------------------------------------------------------------+
//| Detector contract. Detect() is called once per new signal bar;   |
//| it returns true and fills 'out' when a setup exists.             |
//+------------------------------------------------------------------+
interface ISignalDetector
  {
   bool   Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out);
   string Name(void);
  };

//+------------------------------------------------------------------+
//| CDummyDetector - Phase-2 placeholder so the account owner can     |
//| exercise the approve/deny workflow before real strategies exist.  |
//|                                                                  |
//| Fires on the FIRST H4 bar of each Monday, alternating buy/sell.   |
//| Entry/SL/TP are derived from the last 5 completed bars' range so  |
//| the levels look realistic on real EURUSD.dk ticks; RR fixed at 2. |
//+------------------------------------------------------------------+
class CDummyDetector : public ISignalDetector
  {
private:
   datetime          m_last_monday;   // midnight of the last Monday we signalled
   int               m_count;         // signal counter, drives buy/sell alternation
   int               m_lookback;      // bars in the setup zone
   double            m_rr;            // fixed reward:risk

public:
                     CDummyDetector(int lookback=5,double rr=2.0)
     {
      m_last_monday=0;
      m_count=0;
      m_lookback=lookback;
      m_rr=rr;
     }

   string            Name(void) { return "DummyMondayH4"; }

   bool              Detect(const string symbol,ENUM_TIMEFRAMES tf,SignalCandidate &out)
     {
      out.valid=false;

      //--- evaluate the just-opened bar (index 0)
      datetime bar0=iTime(symbol,tf,0);
      if(bar0<=0)
         return false;

      MqlDateTime dt;
      TimeToStruct(bar0,dt);
      if(dt.day_of_week!=1)                 // 1 = Monday
         return false;

      datetime midnight=bar0-(bar0%86400);
      if(midnight==m_last_monday)           // already signalled this Monday
         return false;
      m_last_monday=midnight;

      //--- setup zone = range of the last m_lookback completed bars
      MqlRates r[];
      if(CopyRates(symbol,tf,1,m_lookback,r)<m_lookback)
         return false;                       // not enough history yet
      //--- robust to the array's AsSeries ordering: scan min/max explicitly
      double   hi=r[0].high, lo=r[0].low;
      datetime tmin=r[0].time;
      for(int i=1;i<m_lookback;i++)
        {
         if(r[i].high>hi)  hi=r[i].high;
         if(r[i].low <lo)  lo=r[i].low;
         if(r[i].time<tmin) tmin=r[i].time;
        }

      double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
      if(point<=0.0) point=_Point;
      double buffer=10.0*point;

      m_count++;
      int dir=((m_count%2)==1)?+1:-1;        // 1st Monday buy, 2nd sell, ...

      double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
      double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
      if(ask<=0.0) ask=r[0].close;
      if(bid<=0.0) bid=r[0].close;

      double entry,sl,tp,risk;
      if(dir>0)
        {
         entry=ask;
         sl=lo-buffer;
         risk=entry-sl;
         if(risk<=0.0) { sl=entry-buffer*3.0; risk=entry-sl; }
         tp=entry+m_rr*risk;
        }
      else
        {
         entry=bid;
         sl=hi+buffer;
         risk=sl-entry;
         if(risk<=0.0) { sl=entry+buffer*3.0; risk=sl-entry; }
         tp=entry-m_rr*risk;
        }

      out.valid    =true;
      out.strategy =Name();
      out.direction=dir;
      out.entry    =entry;
      out.sl       =sl;
      out.tp       =tp;
      out.rr       =m_rr;
      out.zone_from=tmin;                    // left edge = oldest of the lookback bars
      out.zone_to  =bar0;                    // right edge = current bar
      out.zone_hi  =hi;
      out.zone_lo  =lo;
      return true;
     }
  };

#endif // HYBRID_SIGNAL_MQH
//+------------------------------------------------------------------+
