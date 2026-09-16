//+------------------------------------------------------------------+
//| HybridLiveLauncher.mq5 - [StartUp] Script on the live box.        |
//| Opens one H4 chart per lineup symbol (idempotent: reuses an       |
//| existing chart of that symbol/period) and applies the live        |
//| template to any of them that has no expert attached. The template |
//| carries HybridForwardTest-live + its inputs, so the EA starts on   |
//| every symbol after every terminal launch, graceful exit or not.   |
//+------------------------------------------------------------------+
#property script_show_inputs
input string InpSymbols ="EURUSD.sim,GBPUSD.sim,US100.sim,US500.sim,XAUUSD.sim,USOIL.sim";
input string InpTemplate="hybrid_live";
input bool   InpCloseOthers=true;   // close charts whose symbol is not in the lineup (keeps the profile clean)

long FindChart(string sym)
  {
   long id=ChartFirst();
   while(id>=0)
     {
      if(ChartSymbol(id)==sym && ChartPeriod(id)==PERIOD_H4) return id;
      id=ChartNext(id);
     }
   return -1;
  }

void OnStart()
  {
   string syms[]; int n=StringSplit(InpSymbols,',',syms);
   int opened=0, applied=0, kept=0;
   //--- the chart this script runs on is handled LAST: applying a template that carries an EA to the
   //--- script's own chart unloads the script, so every other symbol must be done before that.
   string own=ChartSymbol(ChartID()); int order[]; ArrayResize(order,n); int no=0, self=-1;
   for(int i=0;i<n;i++){ string s=syms[i]; StringTrimLeft(s); StringTrimRight(s); if(s==own && ChartPeriod(ChartID())==PERIOD_H4) self=i; else order[no++]=i; }
   if(self>=0) order[no++]=self;
   if(InpCloseOthers)
     {
      long id=ChartFirst(); long victims[]; int nv=0;
      while(id>=0)
        {
         string cs=ChartSymbol(id); bool in=false;
         for(int i=0;i<n;i++){ string s=syms[i]; StringTrimLeft(s); StringTrimRight(s); if(s==cs) in=true; }
         if(!in && id!=ChartID()){ ArrayResize(victims,nv+1); victims[nv++]=id; }
         id=ChartNext(id);
        }
      for(int k=0;k<nv;k++) ChartClose(victims[k]);
      if(nv>0) Print("Launcher: closed ",nv," non-lineup chart(s)");
     }

   for(int oi=0;oi<no;oi++)
     {
      int i=order[oi];
      string s=syms[i]; StringTrimLeft(s); StringTrimRight(s); if(s=="") continue;
      if(!SymbolSelect(s,true)) { Print("Launcher: symbol not found: ",s); continue; }
      long id=FindChart(s);
      if(id<0) { id=ChartOpen(s,PERIOD_H4); if(id<=0){ Print("Launcher: ChartOpen failed for ",s," err=",GetLastError()); continue; } opened++; Sleep(500); }
      string ea=ChartGetString(id,CHART_EXPERT_NAME);
      Print("Launcher: ",s," chart ",id," expert='",ea,"'");
      if(StringFind(ea,"HybridForwardTest")<0)
        {
         ResetLastError();
         if(ChartApplyTemplate(id,InpTemplate)) applied++;
         else Print("Launcher: ChartApplyTemplate failed on ",s," err=",GetLastError());
        }
      else kept++;
     }
   Print("Launcher: lineup=",n," opened=",opened," template applied=",applied," already running=",kept);
  }
