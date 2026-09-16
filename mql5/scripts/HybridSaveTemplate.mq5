//+------------------------------------------------------------------+
//| HybridSaveTemplate.mq5 - run ONCE on the chart that has the live |
//| EA attached: saves the chart (EA + inputs + overlays) as the      |
//| template the launcher applies to every lineup symbol.            |
//+------------------------------------------------------------------+
#property script_show_inputs
input string InpTemplate="hybrid_live";
void OnStart()
  {
   string ea=ChartGetString(0,CHART_EXPERT_NAME);
   if(ea=="") { Print("HybridSaveTemplate: NO expert on this chart - nothing saved"); return; }
   bool ok=ChartSaveTemplate(0,InpTemplate);
   Print("HybridSaveTemplate: expert='",ea,"' -> template '",InpTemplate,".tpl' saved=",ok," err=",GetLastError());
  }
