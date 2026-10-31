#include "bench_core.h"
#include <stdio.h>
#include <string.h>
#include <limits.h>
static void le32(uint8_t*p,uint32_t x){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(x>>(i*8));}
static uint32_t crc_update(uint32_t c,const uint8_t*p,size_t n){while(n--){c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^((0u-(c&1u))&0xedb88320u);}return c;}
size_t bench_encode(uint8_t*out,size_t cap,uint8_t kind,uint32_t seq,uint32_t offset,const uint8_t*p,size_t n){
 if(!out||!p||!n||n>1024||cap<28+n||(kind!=1&&kind!=2)||seq==UINT32_MAX)return 0;
 memcpy(out,"HPB1",4);out[4]=kind;out[5]=out[6]=out[7]=0;le32(out+8,BENCH_EPOCH);le32(out+12,seq);le32(out+16,offset);le32(out+20,(uint32_t)n);memcpy(out+28,p,n);
 uint32_t crc=crc_update(crc_update(UINT32_MAX,out,24),p,n)^UINT32_MAX;le32(out+24,crc);return n+28;
}
void bench_tone(uint32_t off,int16_t out[BENCH_PAIRS]){
 /* Exact24sample 1kHz table at24kHz; integer10ms ramps, no libm/state. */
 static const int16_t sine[24]={0,133,256,362,443,495,512,495,443,362,256,133,0,-133,-256,-362,-443,-495,-512,-495,-443,-362,-256,-133};
 static const uint32_t starts[3]={24000,52800,81600};
 for(uint32_t i=0;i<BENCH_PAIRS;i++){out[i]=0;uint32_t t=off+i;if(t<off)continue;for(unsigned b=0;b<3;b++)if(t>=starts[b]&&t-starts[b]<4800){uint32_t x=t-starts[b],gain=x<240?x:4799-x<240?4799-x:240;out[i]=(int16_t)((int32_t)sine[x%24]*(int32_t)gain/240);break;}}
}
static void finish(bench_core*c,const char*code){if(c->phase==BENCH_TERMINAL)return;c->phase=BENCH_TERMINAL;c->code=code;c->report_pending=true;}
static bool clock_ok(bench_core*c,uint64_t now){if(now<c->last_ms){finish(c,"clock_regression");return false;}c->last_ms=now;return true;}
void bench_init(bench_core*c,uint64_t now){memset(c,0,sizeof *c);c->phase=BENCH_IDLE;c->last_ms=now;c->code="boot";c->report_pending=false;}
static void command(bench_core*c,uint64_t now){
 if(c->phase==BENCH_TERMINAL)return;
 if(!strcmp(c->command,"STOP")){finish(c,"stopped");return;}
 if(c->phase!=BENCH_IDLE){finish(c,"unexpected_command");return;}
 if(!strcmp(c->command,"ARM SILENT"))c->profile=BENCH_SILENT;
 else if(!strcmp(c->command,"ARM TONE"))c->profile=BENCH_TONE;
 else {finish(c,"invalid_command");return;}
 c->profile_set=true;c->phase=BENCH_ARMED;c->armed_ms=now;c->code="armed";c->report_pending=true;
}
void bench_input(bench_core*c,const uint8_t*p,size_t n,uint64_t now){
 if(c->phase==BENCH_TERMINAL||!clock_ok(c,now))return;
 for(size_t i=0;i<n&&c->phase!=BENCH_TERMINAL;i++){
  if(p[i]=='\n'){c->command[c->command_used]=0;if(c->command_bad)finish(c,"invalid_command");else command(c,now);c->command_used=0;c->command_bad=false;}
  else if(p[i]<32||p[i]>126||c->command_used>=sizeof c->command-1)c->command_bad=true;
  else c->command[c->command_used++]=(char)p[i];
 }
}
void bench_button(bench_core*c,bool ok,bool press,bool click,uint64_t when){
 if(c->phase==BENCH_ACTIVE&&press){finish(c,"stopped");return;}
 if(c->phase!=BENCH_ARMED||when<=c->armed_ms)return;
 if(when-c->armed_ms>=10000){finish(c,"arm_expired");return;}
 if(!ok){if(press)c->press_after_arm=false;return;}
 if(press){c->press_after_arm=true;c->pressed_ms=when;}
 if(click&&c->press_after_arm&&when>c->pressed_ms){c->phase=BENCH_ACTIVE;c->code="running";c->report_pending=true;}
}
static bool send_record(bench_core*c,const bench_ops*o,void*v,uint8_t kind,uint32_t off,const uint8_t*p,size_t n){
 if(c->io_failed)return false;
 size_t bytes=bench_encode(c->record,sizeof c->record,kind,c->wire_sequence,off,p,n);
 if(!bytes||o->write(v,c->record,bytes)!=(int)bytes){c->io_failed=true;finish(c,"usb_write_failed");return false;}
 c->wire_sequence++;return true;
}
static void report(bench_core*c,const bench_ops*o,void*v){
 if(!c->report_pending||c->io_failed)return;
 const char*phase=c->phase==BENCH_IDLE?"idle":c->phase==BENCH_ARMED?"armed":c->phase==BENCH_ACTIVE?"running":c->restore_ok&&!strcmp(c->code,"complete")?"done":"error";
 const char*profile=!c->profile_set?"none":c->profile==BENCH_TONE?"tone":"silent";
 unsigned max=!c->profile_set?0:c->profile==BENCH_TONE?500:1000;bool tone=c->profile_set&&c->profile==BENCH_TONE;
 char json[640];int n=snprintf(json,sizeof json,"{\"phase\":\"%s\",\"profile\":\"%s\",\"code\":\"%s\",\"rate\":24000,\"channels\":2,\"reference\":\"es8311_dac_right\",\"captured\":%u,\"source_samples\":%u,\"restore_ok\":%s,\"actual_acoustic_validation\":false,\"max_capture_frames\":%u,\"peak\":%u,\"volume\":%u}",phase,profile,c->code,(unsigned)c->captured,(unsigned)c->source_samples,c->restore_ok?"true":"false",max,tone?512:0,tone?5:0);
 c->report_pending=false;if(n<=0||(size_t)n>=sizeof json){finish(c,"status_failed");return;}
 (void)send_record(c,o,v,1,0,(const uint8_t*)json,(size_t)n);
}
static void cleanup(bench_core*c,const bench_ops*o,void*v){
 if(!c->stop_done){c->stop_done=true;c->restore_ok=o->stop(v);if(!c->restore_ok)c->code="restore_failed";}
 report(c,o,v);
}
static bool active_guard(bench_core*c,const bench_ops*o,void*v){
 if(o->cancelled(v)||c->phase!=BENCH_ACTIVE){finish(c,"stopped");return false;}
 uint64_t now=o->now_ms(v);if(!clock_ok(c,now))return false;
 if(c->audio_attempted&&now-c->started_ms>=(c->audio_ready?(c->profile==BENCH_TONE?5250u:10250u):2000u)){finish(c,c->audio_ready?"runtime_cap":"startup_cap");return false;}
 return true;
}
void bench_step(bench_core*c,const bench_ops*o,void*v,uint64_t now){
 if(!c||!o||!o->start||!o->capture||!o->submit||!o->stop||!o->write||!o->cancelled||!o->now_ms)return;
 if(c->phase==BENCH_TERMINAL){cleanup(c,o,v);return;}
 if(!clock_ok(c,now))goto terminal;
 if((c->phase==BENCH_ARMED||(c->phase==BENCH_ACTIVE&&!c->audio_attempted))&&now-c->armed_ms>=10000){finish(c,"arm_expired");goto terminal;}
 if(c->phase!=BENCH_ACTIVE){report(c,o,v);if(c->phase==BENCH_TERMINAL)goto terminal;return;}
 if(!active_guard(c,o,v))goto terminal;
 if(!c->audio_attempted){c->audio_attempted=true;c->started_ms=now;if(!o->start(v,c->profile)){finish(c,"start_failed");goto terminal;}
  if(!active_guard(c,o,v))goto terminal;
  c->started_ms=c->last_ms;c->audio_ready=true;
 }
 if(!active_guard(c,o,v))goto terminal;
 unsigned max=c->profile==BENCH_TONE?500:1000;

 report(c,o,v);if(c->phase==BENCH_TERMINAL||!active_guard(c,o,v))goto terminal;
 bench_capture frame;int rc=o->capture(v,&frame);
 if(rc<0||rc>1){finish(c,"capture_failed");goto terminal;}
 if(!active_guard(c,o,v))goto terminal;
 if(rc>0){
  if(frame.epoch!=BENCH_EPOCH||frame.sequence!=c->captured||frame.sample_offset!=c->captured*BENCH_PAIRS){finish(c,"capture_discontinuity");goto terminal;}
  if(!send_record(c,o,v,2,frame.sample_offset,frame.pcm,sizeof frame.pcm))goto terminal;
  c->captured++;
  if(!active_guard(c,o,v))goto terminal;
 }
 if(c->profile==BENCH_TONE&&c->source_samples<120000){
  if(!active_guard(c,o,v))goto terminal;
  bench_tone(c->source_samples,c->source);
  int s=o->submit(v,c->source_samples,c->source,BENCH_PAIRS);
  if(s<0||s>1){finish(c,"submit_failed");goto terminal;}if(s>0)c->source_samples+=BENCH_PAIRS;
  if(!active_guard(c,o,v))goto terminal;
 }
 if(c->captured==max){finish(c,c->profile==BENCH_TONE&&c->source_samples!=120000?"source_incomplete":"complete");goto terminal;}
 return;
terminal:cleanup(c,o,v);
}
