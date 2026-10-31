/* Actual diagnostic runner, generated sample fixtures only; never device I/O. */
#include "pocket_offline_diagnostic.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef POCKET_DIAGNOSTIC_OBSERVATIONS_VERSION
int main(void) { fputs("numeric observations unavailable\n", stderr); assert(0); }
#else
typedef struct {
 unsigned mode, limit, frames, ends, publishes;
 uint64_t now;
 bool stop;
 pocket_offline_diagnostic_snapshot result;
} fake;
static bool begin(void *v,uint32_t epoch,bool *cleanup){(void)v;assert(epoch==1);*cleanup=true;return true;}
static int16_t sample(unsigned mode,unsigned index,unsigned lane){
 if(mode<=2){
  if(lane)return 0;
  bool rail=mode==0?index<1030:mode==1?index>=211680-1030:((uint64_t)(index+1)*1030/211680!=((uint64_t)index*1030/211680));
  return rail?-32768:(index&1?-1409:1409);
 }
 if(mode==3)return 0;
 if(mode==4){if(index>=2398&&index<=2402)return lane?(index&1?-32768:32767):(index&1?32767:-32768);return lane?-2:16384;}
 if(mode==5)return lane?-32768:32767;
 if(mode==6)return lane?-1:1;
 return 0;
}
static int capture(void *v,pocket_offline_diagnostic_frame *p){
 fake *f=v;f->now+=10;
 if(f->frames==f->limit){f->stop=true;return 0;}
 *p=(pocket_offline_diagnostic_frame){.epoch=1,.sequence=f->frames,.sample_offset=f->frames*240};
 for(unsigned i=0;i<240;i++)for(unsigned lane=0;lane<2;lane++){
  uint16_t x=(uint16_t)sample(f->mode,f->frames*240+i,lane);p->pcm[i*4+lane*2]=(uint8_t)x;p->pcm[i*4+lane*2+1]=(uint8_t)(x>>8);
 }
 f->frames++;return 1;
}
static bool end(void *v){((fake*)v)->ends++;return true;}
static bool cancelled(void *v){return ((fake*)v)->stop;}
static uint64_t now(void *v){return ((fake*)v)->now;}
static void idle(void *v){((fake*)v)->now++;}
static void publish(void *v,const pocket_offline_diagnostic_snapshot *s){
 fake *f=v;f->publishes++;f->result=*s;
 for(unsigned lane=0;lane<2;lane++){
  if(s->samples&&(f->mode==5||f->mode==6)){
   int32_t expected=f->mode==5?(lane?-32768:32767):(lane?-1:1);
   assert(s->mean[lane]==expected&&s->early.mean[lane]==expected);
   if(s->late.samples)assert(s->late.mean[lane]==expected);
  }
  assert(s->clips[lane]==s->positive_clips[lane]+s->negative_clips[lane]);
  assert(s->early.clips[lane]+s->late.clips[lane]==s->clips[lane]);
  assert(s->early.samples+s->late.samples==s->samples);
  assert(s->early.samples<=2400);
  if(!s->clips[lane])assert(s->first_clip_sample[lane]==UINT32_MAX&&s->last_clip_sample[lane]==UINT32_MAX&&s->longest_clip_run[lane]==0);
 }
}
static pocket_offline_diagnostic_snapshot run(unsigned mode,unsigned limit){
 fake f={.mode=mode,.limit=limit,.now=100};const pocket_offline_diagnostic_ops ops={begin,capture,end,cancelled,now,idle,publish};
 assert(pocket_offline_diagnostic_run(&ops,&f));assert(f.ends==1&&f.result.restore_ok&&f.result.cleanup_attempted);
 assert(f.result.frames==limit&&f.result.samples==limit*240&&f.publishes<=105);
 assert(f.result.code==(limit==1000?PDIAG_OK:PDIAG_STOPPED));return f.result;
}
static void ambiguity(void){
 for(unsigned mode=0;mode<3;mode++){
  pocket_offline_diagnostic_snapshot s=run(mode,882);
  assert(s.rms[0]==2683&&s.peak[0]==32768&&s.clips[0]==1030&&s.mean[0]==-159);
  assert(s.positive_clips[0]==0&&s.negative_clips[0]==1030&&s.rms[1]==0&&s.clips[1]==0);
  assert(s.early.samples==2400&&s.late.samples==209280);
  if(mode==0)assert(s.early.clips[0]==1030&&s.late.clips[0]==0&&s.first_clip_sample[0]==0&&s.last_clip_sample[0]==1029&&s.longest_clip_run[0]==1030);
  if(mode==1)assert(s.early.clips[0]==0&&s.late.clips[0]==1030&&s.first_clip_sample[0]==210650&&s.last_clip_sample[0]==211679&&s.longest_clip_run[0]==1030);
  if(mode==2)assert(s.early.clips[0]==11&&s.late.clips[0]==1019&&s.first_clip_sample[0]==205&&s.last_clip_sample[0]==211679&&s.longest_clip_run[0]==1);
 }
}
static void boundaries(void){
 for(unsigned limit=0;limit<=11;limit++){
  pocket_offline_diagnostic_snapshot s=run(6,limit);
  assert(s.early.samples==(limit<10?limit*240:2400));assert(s.late.samples==(limit>10?(limit-10)*240:0));
  for(unsigned lane=0;lane<2;lane++){
   assert(s.rms[lane]==(limit?1:0));assert(s.mean[lane]==(limit?(lane?-1:1):0));
   assert(s.early.mean[lane]==(limit?(lane?-1:1):0));assert(s.late.mean[lane]==(limit>10?(lane?-1:1):0));
  }
 }
 pocket_offline_diagnostic_snapshot s=run(4,11);
 assert(s.positive_clips[0]==2&&s.negative_clips[0]==3&&s.positive_clips[1]==3&&s.negative_clips[1]==2);
 for(unsigned lane=0;lane<2;lane++)assert(s.first_clip_sample[lane]==2398&&s.last_clip_sample[lane]==2402&&s.longest_clip_run[lane]==5&&s.early.clips[lane]==2&&s.late.clips[lane]==3);
 assert(s.early.mean[0]==16370&&s.late.mean[0]==16042);assert(s.early.mean[1]==-1&&s.late.mean[1]==134);
 /* Asymmetric signed/little-endian nonrail values retain channel identity. */
 assert(s.mean[0]==16340&&s.mean[1]==10);assert(s.early.peak[1]==32768&&s.late.peak[0]==32768);
}
static void full_scale(void){
 pocket_offline_diagnostic_snapshot s=run(5,1000);
 for(unsigned lane=0;lane<2;lane++){
  assert(s.clips[lane]==240000&&s.first_clip_sample[lane]==0&&s.last_clip_sample[lane]==239999&&s.longest_clip_run[lane]==240000);
  assert(s.early.clips[lane]==2400&&s.late.clips[lane]==237600);
  assert(s.rms[lane]==(lane?32768:32767)&&s.early.rms[lane]==s.rms[lane]&&s.late.rms[lane]==s.rms[lane]);
 }
 assert(s.mean[0]==32767&&s.mean[1]==-32768&&s.positive_clips[0]==240000&&s.negative_clips[1]==240000);
 s=run(3,1000);assert(s.rms[0]==0&&s.early.samples==2400&&s.late.samples==237600);
}
int main(int argc,char **argv){assert(argc==2);switch(atoi(argv[1])){case 0:ambiguity();break;case 1:boundaries();break;case 2:full_scale();break;default:assert(0);}puts("numeric observations passed");}
#endif
