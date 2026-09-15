//+------------------------------------------------------------------+
//|                                                 HybridTriEMA.mq5   |
//|  Display-only three-EMA overlay for the Hybrid system (coach       |
//|  pair-12 item 8, 2026-09-10). EMA20 / EMA50 / EMA200 on the chart  |
//|  window, added to the EA's chart via ChartIndicatorAdd so it shows |
//|  identically on the live chart and in the tester-window capture.   |
//|  NO signal/logic role — pure visual context. Colours are MT5 named |
//|  colours (clrGold / clrDeepSkyBlue / clrViolet) so the Python D1   |
//|  render (inbox_bridge.render_d1) can match them exactly.           |
//+------------------------------------------------------------------+
#property copyright "Hybrid"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 3
#property indicator_plots   3

#property indicator_label1  "EMA20"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrGold
#property indicator_style1  STYLE_SOLID
#property indicator_width1  1

#property indicator_label2  "EMA50"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrDeepSkyBlue
#property indicator_style2  STYLE_SOLID
#property indicator_width2  1

#property indicator_label3  "EMA200"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrViolet
#property indicator_style3  STYLE_SOLID
#property indicator_width3  2

double B20[], B50[], B200[];
int    h20=INVALID_HANDLE, h50=INVALID_HANDLE, h200=INVALID_HANDLE;

int OnInit()
  {
   SetIndexBuffer(0,B20, INDICATOR_DATA);
   SetIndexBuffer(1,B50, INDICATOR_DATA);
   SetIndexBuffer(2,B200,INDICATOR_DATA);
   PlotIndexSetInteger(0,PLOT_DRAW_BEGIN,19);
   PlotIndexSetInteger(1,PLOT_DRAW_BEGIN,49);
   PlotIndexSetInteger(2,PLOT_DRAW_BEGIN,199);
   IndicatorSetString(INDICATOR_SHORTNAME,"HybridTriEMA(20,50,200)");
   //--- source the EMAs from the platform's own iMA so values match a hand-added MA exactly
   h20 =iMA(_Symbol,PERIOD_CURRENT, 20,0,MODE_EMA,PRICE_CLOSE);
   h50 =iMA(_Symbol,PERIOD_CURRENT, 50,0,MODE_EMA,PRICE_CLOSE);
   h200=iMA(_Symbol,PERIOD_CURRENT,200,0,MODE_EMA,PRICE_CLOSE);
   if(h20==INVALID_HANDLE || h50==INVALID_HANDLE || h200==INVALID_HANDLE)
     { Print("HybridTriEMA: iMA handle failed err=",GetLastError()); return(INIT_FAILED); }
   return(INIT_SUCCEEDED);
  }

int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   //--- Mirror the platform's own iMA buffers into our plot buffers. Warm-sweep pattern: copy the
   //--- freshly-changed tail (all bars on the first pass) into the NON-series plot buffers, where
   //--- CopyBuffer places values at the correct bar positions. On a not-ready sub-iMA, return
   //--- prev_calculated (NOT 0) so partial progress is kept and we retry next tick without forcing
   //--- a full recalc every call — returning 0 here left the buffers at EMPTY_VALUE (invisible).
   int to_copy=(prev_calculated==0 ? rates_total : rates_total-prev_calculated+1);
   if(to_copy>rates_total) to_copy=rates_total;
   if(CopyBuffer(h20, 0,0,to_copy,B20 )<=0) return(prev_calculated);
   if(CopyBuffer(h50, 0,0,to_copy,B50 )<=0) return(prev_calculated);
   if(CopyBuffer(h200,0,0,to_copy,B200)<=0) return(prev_calculated);
   return(rates_total);
  }
//+------------------------------------------------------------------+
