//+------------------------------------------------------------------+
//|                                                  AutoImport.mq5   |
//|        FTMO Hybrid Trading System - unattended tick importer      |
//|                                                                  |
//|  Headless batch importer for the rolling disk pipeline. Launched  |
//|  unattended by pipeline/mt5_import.sh (terminal64 /config startup |
//|  attaches this EA to a chart). On start it:                       |
//|    1. reads a job list  MQL5\Files\import\jobs.txt (one BASE/line)|
//|    2. imports each staged CSV into <BASE>.dk via the SHARED       |
//|       Hybrid\TickImport.mqh engine (same code as ImportTicks)     |
//|    3. writes per-symbol results to MQL5\Files\import\import_status.txt
//|    4. requests terminal shutdown (TerminalClose) so the launcher  |
//|       knows the run finished.                                     |
//|                                                                  |
//|  RE-TRIGGER SAFETY: the FIRST thing it does is delete jobs.txt, so |
//|  a later NORMAL launch of the terminal (no jobs.txt) finds nothing |
//|  to do and NEVER closes a terminal the user is using.             |
//+------------------------------------------------------------------+
#property copyright "FTMO Hybrid Trading System"
#property version   "1.00"
#property description "Unattended batch tick importer (reads jobs.txt, writes import_status.txt, self-closes)."

#include <Hybrid\TickImport.mqh>

input bool   AutoShutdown = true;   // request terminal close when all jobs are done

//+------------------------------------------------------------------+
//| OnInit stays trivial: the heavy batch runs in the first OnTimer   |
//| tick, NOT in OnInit. A multi-minute blocking OnInit holds the EA  |
//| thread while the terminal is still attaching, and the M1-bar      |
//| series-sync (SyncedBars) needs the terminal's background workers  |
//| to progress - which is reliable once the EA is fully initialised  |
//| (i.e. from OnTimer), not mid-OnInit. This is the standard startup |
//| batch idiom.                                                     |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(MQLInfoInteger(MQL_TESTER))
     {
      Print("AutoImport is a live-terminal batch tool, not for the Strategy Tester.");
      return(INIT_FAILED);
     }
   EventSetTimer(1);      // fire ~1s after init; the batch runs there
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   static bool ran=false;
   if(ran) return;        // one-shot
   ran=true;
   EventKillTimer();      // no repeats

   string jobsPath="import\\jobs.txt";
   if(!FileIsExist(jobsPath))
     {
      //--- no work armed -> stay completely inert (this is what makes a
      //--- normal user launch safe: no jobs.txt => nothing happens, and we
      //--- never call TerminalClose).
      Print("AutoImport: no jobs.txt present - nothing to do (inert; terminal NOT closed).");
      return;
     }

   //--- read the job list
   string bases[]; int nb=0;
   int jh=FileOpen(jobsPath,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(jh==INVALID_HANDLE)
     {
      Print("AutoImport: cannot open jobs.txt err=",GetLastError()," - staying inert.");
      return;
     }
   while(!FileIsEnding(jh))
     {
      string ln=FileReadString(jh);
      StringTrimLeft(ln); StringTrimRight(ln);
      if(StringLen(ln)==0) continue;
      if(StringGetCharacter(ln,0)=='#') continue;
      ArrayResize(bases,nb+1); bases[nb]=ln; nb++;
     }
   FileClose(jh);

   //--- DISARM re-trigger immediately: once consumed, a re-launch is inert
   FileDelete(jobsPath);

   if(nb==0)
     {
      Print("AutoImport: jobs.txt had no symbols.");
      if(AutoShutdown) TerminalClose(0);
      return;
     }

   Print("=== AutoImport: ",nb," job(s) queued ===");
   uint t0=GetTickCount();

   //--- open the status file (truncate); flush per line so a crash keeps rows
   int sh=FileOpen("import\\import_status.txt",FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(sh!=INVALID_HANDLE)
      FileWriteString(sh,"# SYMBOL,STATUS,ticks,first,last,seconds\n");

   int okc=0, failc=0;
   for(int i=0;i<nb;i++)
     {
      string job=bases[i];
      //--- "SESSIONS <base>" -> re-patch trading sessions on an existing .dk
      //--- symbol WITHOUT re-importing ticks (sessions are symbol properties).
      if(StringSubstr(job,0,9)=="SESSIONS ")
        {
         string base=job; StringTrimLeft(base); // "SESSIONS <base>"
         base=StringSubstr(job,9); StringTrimLeft(base); StringTrimRight(base);
         Print("--- job ",i+1,"/",nb,": SESSIONS ",base," ---");
         bool ok=TI_SessionsOnly(base);
         string line=StringFormat("%s,%s,0,,,sessions\n",base,(ok?"OK":"FAIL"));
         if(ok) okc++; else failc++;
         if(sh!=INVALID_HANDLE) { FileWriteString(sh,line); FileFlush(sh); }
         continue;
        }

      Print("--- job ",i+1,"/",nb,": ",job," ---");
      TickImportResult r;
      bool ok=RunTickImport(job,r);        // shared engine, all defaults (.dk, Dukascopy)

      string line;
      if(ok)
        {
         okc++;
         line=StringFormat("%s,OK,%I64d,%s,%s,%.1f\n",job,r.ticks,r.first,r.last,r.seconds);
        }
      else
        {
         failc++;
         line=StringFormat("%s,FAIL,0,,,%s\n",job,r.err);
        }
      if(sh!=INVALID_HANDLE) { FileWriteString(sh,line); FileFlush(sh); }
     }

   double secs=(GetTickCount()-t0)/1000.0;
   if(sh!=INVALID_HANDLE)
     {
      FileWriteString(sh,StringFormat("# DONE %d ok, %d fail, %.1f s\n",okc,failc,secs));
      FileFlush(sh);
      FileClose(sh);
     }
   Print("=== AutoImport COMPLETE: ",okc," ok, ",failc," fail in ",DoubleToString(secs,1)," s ===");

   if(AutoShutdown)
     {
      Print("AutoImport: requesting terminal shutdown (TerminalClose).");
      TerminalClose(0);
     }
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTick(void) { }
//+------------------------------------------------------------------+
