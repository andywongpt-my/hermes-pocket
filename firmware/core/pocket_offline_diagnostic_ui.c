#include "pocket_offline_diagnostic_ui.h"
#include <stdio.h>
void pocket_offline_diagnostic_format(const pocket_offline_diagnostic_snapshot *s,
 char *title,size_t title_n,char *body,size_t body_n,char *footer,size_t footer_n){
 if(!s)return;
 const char *name="SILENT DIAGNOSTIC",*hint="Hold OK: arm once\nUP/DOWN: pages";
 switch(s->phase){
 case PDIAG_IDLE:break;
 case PDIAG_ARMED:name="SILENT: ARMED";hint="Release, fresh OK: start\nUP/DOWN: pages";break;
 case PDIAG_QUEUED:name="SILENT: QUEUED";hint="Any press: stop\nUP/DOWN: pages";break;
 case PDIAG_STARTING:name="SILENT: STARTING";hint="Any press: stop\nUP/DOWN: pages";break;
 case PDIAG_RUNNING:name="SILENT: CAPTURING";hint="Any press: stop\nUP/DOWN: pages";break;
 case PDIAG_DONE:name="SILENT: FINISHED";hint="One attempt used\nUP/DOWN: pages";break;
 case PDIAG_ERROR:name="SILENT: STOPPED / ERROR";hint="One attempt used\nUP/DOWN: pages";break;
 case PDIAG_CLEANUP_FAILED:name="CLEANUP FAILED";hint="Screen off still available\nUP/DOWN: pages";break;
 default:name="DIAGNOSTIC UNKNOWN";hint="UP/DOWN: pages";break;
 }
 snprintf(title,title_n,"%s",name);snprintf(footer,footer_n,"%s",hint);
 if(s->phase==PDIAG_IDLE)snprintf(body,body_n,"Offline, silent, no export.\nHold OK, then fresh press.\nOne attempt per boot.\nNo tone or provider call.\nNot acoustic validation.");
 else if(s->phase==PDIAG_ARMED)snprintf(body,body_n,"Release the held key.\nFresh OK within 10 seconds.\nNo delayed click starts it.\nSilent capture only.\nNot acoustic validation.");
 else snprintf(body,body_n,"Frames %lu / 1000\nMIC RMS %lu Peak %lu\nREF RMS %lu Peak %lu\nClips MIC %lu REF %lu\nCleanup: %s (code %u)\nNot acoustic validation.",
  (unsigned long)s->frames,(unsigned long)s->rms[0],(unsigned long)s->peak[0],
  (unsigned long)s->rms[1],(unsigned long)s->peak[1],(unsigned long)s->clips[0],(unsigned long)s->clips[1],
  s->cleanup_attempted?(s->restore_ok?"checked":"FAILED"):
   s->phase==PDIAG_CLEANUP_FAILED?"unavailable":
   s->phase==PDIAG_QUEUED||s->phase==PDIAG_STARTING||s->phase==PDIAG_RUNNING?"pending":"not started",(unsigned)s->code);
}
void pocket_offline_diagnostic_format_page(const pocket_offline_diagnostic_snapshot *s,unsigned page,
 char *title,size_t title_n,char *body,size_t body_n,char *footer,size_t footer_n){
 if(!s)return;
 pocket_offline_diagnostic_format(s,title,title_n,body,body_n,footer,footer_n);
 if(s->phase==PDIAG_IDLE||s->phase==PDIAG_ARMED)return;
 page%=POCKET_DIAGNOSTIC_PAGES;
 const char *phase=s->phase==PDIAG_QUEUED?"QUEUED":s->phase==PDIAG_STARTING?"START":
  s->phase==PDIAG_RUNNING?"CAPTURE":s->phase==PDIAG_DONE?"DONE":
  s->phase==PDIAG_CLEANUP_FAILED?"LOCKED":"STOP/ERR";
 snprintf(title,title_n,"SILENT %s %u/5",phase,page+1);
 if(!page)return;
 const char *cleanup=s->cleanup_attempted?(s->restore_ok?"checked":"FAILED"):
  s->phase==PDIAG_CLEANUP_FAILED?"unavailable":
  s->phase==PDIAG_QUEUED||s->phase==PDIAG_STARTING||s->phase==PDIAG_RUNNING?"pending":"not started";
 const unsigned lane=(page==2||page==4)?1:0;
 const char *name=lane?"REF":"MIC";
 if(page<=2){
  snprintf(body,body_n,"%s DC %ld (all)\nEarly [0,2400) n%lu\nR%lu P%lu DC%ld\nRails %lu\nLate [2400,N) n%lu\nR%lu P%lu DC%ld\nRails %lu\nCleanup %s code %u",
   name,(long)s->mean[lane],(unsigned long)s->early.samples,
   (unsigned long)s->early.rms[lane],(unsigned long)s->early.peak[lane],(long)s->early.mean[lane],(unsigned long)s->early.clips[lane],
   (unsigned long)s->late.samples,(unsigned long)s->late.rms[lane],(unsigned long)s->late.peak[lane],(long)s->late.mean[lane],(unsigned long)s->late.clips[lane],cleanup,(unsigned)s->code);
 }else{
  char first[12],last[12];
  if(s->clips[lane]){
   snprintf(first,sizeof first,"%lu",(unsigned long)s->first_clip_sample[lane]);
   snprintf(last,sizeof last,"%lu",(unsigned long)s->last_clip_sample[lane]);
  }else{snprintf(first,sizeof first,"none");snprintf(last,sizeof last,"none");}
  snprintf(body,body_n,"%s rails (+32767/-32768)\n+ %lu  - %lu\nFirst sample %s\nLast sample %s\nLongest run %lu\n0-based samples, not ms\nCleanup %s code %u",
   name,(unsigned long)s->positive_clips[lane],(unsigned long)s->negative_clips[lane],first,last,(unsigned long)s->longest_clip_run[lane],cleanup,(unsigned)s->code);
 }
}
