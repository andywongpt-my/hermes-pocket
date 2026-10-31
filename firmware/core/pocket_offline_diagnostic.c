#include "pocket_offline_diagnostic.h"
#include <string.h>
static void wipe(void*p,size_t n){volatile uint8_t*q=p;while(n--)*q++=0;}
static uint32_t root(uint64_t n){uint64_t x=0,b=1ull<<62;while(b>n)b>>=2;while(b){if(n>=x+b){n-=x+b;x=(x>>1)+b;}else x>>=1;b>>=2;}return (uint32_t)x;}
/* Compute divisions/roots only when publishing (at most every 10 frames plus
 * terminal), not in the sample loop. Accumulators still include every sample. */
static void summarize(pocket_offline_diagnostic_snapshot *s,const int64_t sum[2],
 const uint64_t square[2],const int64_t early_sum[2],const uint64_t early_square[2]){
 if(!s->samples)return;
 for(unsigned lane=0;lane<2;lane++){
   s->mean[lane]=(int32_t)(sum[lane]/s->samples);s->rms[lane]=root(square[lane]/s->samples);
   s->early.mean[lane]=(int32_t)(early_sum[lane]/s->early.samples);
   s->early.rms[lane]=root(early_square[lane]/s->early.samples);
   if(s->late.samples){
    s->late.mean[lane]=(int32_t)((sum[lane]-early_sum[lane])/s->late.samples);
    s->late.rms[lane]=root((square[lane]-early_square[lane])/s->late.samples);
   }
  }
}
static bool guard(const pocket_offline_diagnostic_ops*o,void*v,uint64_t*last,uint64_t start,uint64_t limit,pocket_offline_diagnostic_snapshot*s){
 if(o->cancelled(v)){s->code=PDIAG_STOPPED;return false;}
 uint64_t now=o->now_ms(v);if(now<*last){s->code=PDIAG_CLOCK;return false;}*last=now;
 if(now-start>=limit){s->code=limit==2000?PDIAG_STARTUP_CAP:PDIAG_RUNTIME_CAP;return false;}return true;
}
bool pocket_offline_diagnostic_run(const pocket_offline_diagnostic_ops*o,void*v){
 if(!o||!o->begin||!o->capture||!o->end||!o->cancelled||!o->now_ms||!o->idle||!o->publish)return false;
 pocket_offline_diagnostic_snapshot s={.phase=PDIAG_STARTING,
  .first_clip_sample={UINT32_MAX,UINT32_MAX},.last_clip_sample={UINT32_MAX,UINT32_MAX}};
 int64_t sum[2]={0},early_sum[2]={0};uint64_t square[2]={0},early_square[2]={0};
 uint32_t clip_run[2]={0};
 pocket_offline_diagnostic_frame frame={0};bool cleanup=false,success=false,began=false;
 uint64_t last=o->now_ms(v),start=last;o->publish(v,&s);
 if(!guard(o,v,&last,start,2000,&s))goto done;
 began=true;
 if(!o->begin(v,1,&cleanup)){s.code=PDIAG_START_FAILED;goto done;}
 if(!cleanup){s.code=PDIAG_START_FAILED;goto done;}
 if(!guard(o,v,&last,start,2000,&s))goto done;
 start=last;s.phase=PDIAG_RUNNING;o->publish(v,&s);
 while(s.frames<1000){
  if(!guard(o,v,&last,start,10250,&s))goto done;
  int rc=o->capture(v,&frame);
  if(!guard(o,v,&last,start,10250,&s))goto done;
  if(rc<0||rc>1){s.code=PDIAG_CAPTURE_FAILED;goto done;}
  if(!rc){o->idle(v);continue;}
  if(frame.epoch!=1||frame.sequence!=s.frames||frame.sample_offset!=s.frames*240){s.code=PDIAG_DISCONTINUITY;goto done;}
  for(unsigned i=0;i<240;i++)for(unsigned lane=0;lane<2;lane++){
   const uint8_t*p=frame.pcm+i*4+lane*2;int32_t x=(int16_t)((uint16_t)p[0]|((uint16_t)p[1]<<8));uint32_t magnitude=(uint32_t)(x<0?-x:x);
   const uint32_t index=s.samples+i;
   const bool early=index<POCKET_DIAGNOSTIC_EARLY_SAMPLES;
   pocket_offline_diagnostic_window *window=early?&s.early:&s.late;
   const uint64_t squared=(uint64_t)((int64_t)x*x);
   sum[lane]+=x;square[lane]+=squared;
   if(early){early_sum[lane]+=x;early_square[lane]+=squared;}
   if(magnitude>s.peak[lane])s.peak[lane]=magnitude;
   if(magnitude>window->peak[lane])window->peak[lane]=magnitude;
   if(x==32767||x==-32768){
    s.clips[lane]++;window->clips[lane]++;
    if(x>0)s.positive_clips[lane]++;else s.negative_clips[lane]++;
    if(s.first_clip_sample[lane]==UINT32_MAX)s.first_clip_sample[lane]=index;
    s.last_clip_sample[lane]=index;
    if(++clip_run[lane]>s.longest_clip_run[lane])s.longest_clip_run[lane]=clip_run[lane];
   }else clip_run[lane]=0;
  }
  wipe(&frame,sizeof frame);s.frames++;s.samples=s.frames*240;
  s.early.samples=s.samples<POCKET_DIAGNOSTIC_EARLY_SAMPLES?s.samples:POCKET_DIAGNOSTIC_EARLY_SAMPLES;
  s.late.samples=s.samples-s.early.samples;
  if(s.frames%10==0){summarize(&s,sum,square,early_sum,early_square);o->publish(v,&s);}
 }
 if(!guard(o,v,&last,start,10250,&s))goto done;
 success=true;
done:
 summarize(&s,sum,square,early_sum,early_square);
 wipe(&frame,sizeof frame);wipe(sum,sizeof sum);wipe(square,sizeof square);
 wipe(early_sum,sizeof early_sum);wipe(early_square,sizeof early_square);wipe(clip_run,sizeof clip_run);
 s.cleanup_attempted=cleanup;s.restore_ok=cleanup&&o->end(v);
 bool safe=cleanup?s.restore_ok:!began;
 if(!safe){if(cleanup)s.code=PDIAG_RESTORE_FAILED;s.phase=PDIAG_CLEANUP_FAILED;}
 else s.phase=success?PDIAG_DONE:PDIAG_ERROR;
 o->publish(v,&s);return safe;
}
void pocket_offline_diagnostic_expire(pocket_offline_diagnostic_control*c,uint64_t now){
 if((c->phase==PDIAG_ARMED||c->phase==PDIAG_QUEUED)&&(now<c->armed_ms||now-c->armed_ms>=10000)){c->phase=PDIAG_ERROR;c->code=now<c->armed_ms?PDIAG_CLOCK:PDIAG_EXPIRED;c->stop=true;}
}
bool pocket_offline_diagnostic_arm(pocket_offline_diagnostic_control*c,uint64_t now,uint32_t nonce){
 if(c->used||nonce==UINT32_MAX)return false;
 *c=(pocket_offline_diagnostic_control){.phase=PDIAG_ARMED,.armed_ms=now,.armed_nonce=nonce,.used=true};return true;
}
void pocket_offline_diagnostic_button(pocket_offline_diagnostic_control*c,bool ok,bool press,uint64_t ms,uint32_t nonce){
 if(!press)return;
 if(c->phase==PDIAG_QUEUED||c->phase==PDIAG_STARTING||c->phase==PDIAG_RUNNING){c->stop=true;return;}
 if(c->phase!=PDIAG_ARMED)return;
 if(nonce==UINT32_MAX){pocket_offline_diagnostic_input_lost(c);return;}
 if(!ok){pocket_offline_diagnostic_input_lost(c);return;}
 if(ms<=c->armed_ms||nonce<=c->armed_nonce)return;
 if(ms-c->armed_ms>=10000){pocket_offline_diagnostic_expire(c,ms);return;}
 c->pressed_ms=ms;c->pressed_nonce=nonce;
}
bool pocket_offline_diagnostic_admit(pocket_offline_diagnostic_control*c,uint64_t ms,uint32_t nonce,uint64_t now){
 pocket_offline_diagnostic_expire(c,now);
 if(c->phase!=PDIAG_ARMED||c->stop||!c->pressed_nonce||ms!=c->pressed_ms||nonce!=c->pressed_nonce||ms>now)return false;
 c->phase=PDIAG_QUEUED;return true;
}
bool pocket_offline_diagnostic_dispatch(pocket_offline_diagnostic_control*c,uint64_t now){
 pocket_offline_diagnostic_expire(c,now);
 if(c->phase!=PDIAG_QUEUED||c->stop){if(c->phase==PDIAG_QUEUED){c->phase=PDIAG_ERROR;c->code=PDIAG_STOPPED;}return false;}
 c->phase=PDIAG_STARTING;return true;
}
void pocket_offline_diagnostic_input_lost(pocket_offline_diagnostic_control*c){
 if(c->phase==PDIAG_IDLE||c->phase==PDIAG_DONE||c->phase==PDIAG_ERROR||c->phase==PDIAG_CLEANUP_FAILED)return;
 c->stop=true;if(c->phase==PDIAG_ARMED){c->phase=PDIAG_ERROR;c->code=PDIAG_STOPPED;}
}
