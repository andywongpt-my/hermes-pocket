/* Synthetic-only host fixture. Writes actual C core records to stdout, never
 * opens USB/audio/network. Its simulated delay is NOT a device measurement. */
#include "pocket_qualification.h"
#include <stdio.h>
#include <string.h>
typedef struct {uint64_t now;uint32_t captures,accepted;int mode;bool audio;} fake;
static bool begin(void*v,uint32_t epoch,unsigned volume,pocket_qualification_begin_ownership*acquired){(void)v;if(epoch!=7||volume>5)return false;*acquired=PQ_BEGIN_ACQUIRED;return true;}
static int capture(void*v,pocket_qualification_capture*f){
 fake*s=v;s->now+=10;*f=(pocket_qualification_capture){.epoch=7,.sequence=s->captures,.sample_offset=s->captures*240,.dma_consumed=s->accepted,.queue_frames=PQ_QUEUE_UNKNOWN};
 for(uint32_t i=0;i<240;i++){
  uint32_t t=f->sample_offset+i;int16_t ref=s->audio&&t>=1440?pocket_qualification_source_sample(t-1440):0;
  int16_t mic=(int16_t)(ref/2+(int32_t)(t%31)-15);
  uint16_t a=(uint16_t)mic,b=(uint16_t)ref;f->pcm[i*4]=(uint8_t)a;f->pcm[i*4+1]=(uint8_t)(a>>8);f->pcm[i*4+2]=(uint8_t)b;f->pcm[i*4+3]=(uint8_t)(b>>8);
 }
 s->captures++;return 1;
}
static int submit(void*v,uint32_t offset,const int16_t*p,size_t n){fake*s=v;if(offset!=s->accepted||n!=240)return -1;for(size_t i=0;i<n;i++)if(p[i]!=pocket_qualification_source_sample(offset+(uint32_t)i))return -1;s->accepted+=(uint32_t)n;return 1;}
static bool end(void*v,uint32_t epoch){fake*s=v;s->now+=5;return epoch==7&&s->mode!=3;}
static int emit(void*v,const uint8_t*p,size_t n){(void)v;return fwrite(p,1,n,stdout)==n?1:-1;}
static bool stop(void*v){fake*s=v;return s->mode==2&&s->now>=2100;}
static uint64_t now(void*v){return ((fake*)v)->now;}
static void idle(void*v){((fake*)v)->now++;}
int main(int argc,char**argv){
 if(argc!=2)return 2;
 fake f={.now=100};
 if(!strcmp(argv[1],"silent"))f.mode=0;
 else if(!strcmp(argv[1],"audio")){f.mode=1;f.audio=true;}
 else if(!strcmp(argv[1],"stop")){f.mode=2;f.audio=true;}
 else if(!strcmp(argv[1],"cleanup_error"))f.mode=3;
 else return 2;
 pocket_qualification core={0};pocket_qualification_config config={.nonce=UINT64_C(0x1020304050607080),.epoch=7,.profile=f.audio?PQ_AUDIO:PQ_SILENT,.volume=f.audio?5:0};
 pocket_qualification_ops ops={&f,begin,capture,submit,end,emit,stop,now,idle};
 pocket_qualification_result result=pocket_qualification_run(&core,&config,&ops);
 return result.terminal_emitted&&fflush(stdout)==0?0:1;
}
