#include "pocket_qualification.h"
#include <string.h>
static void wipe(void*p,size_t n){volatile uint8_t*q=p;while(n--)*q++=0;}
static int32_t white(uint32_t i){uint32_t x=i+0x9e3779b9u;x^=x>>16;x*=0x7feb352du;x^=x>>15;x*=0x846ca68bu;x^=x>>16;return (int32_t)(x&65535u)-32768;}
static uint32_t local_sample(uint32_t offset){static const uint32_t begin[3]={96000,240000,432000};for(unsigned i=0;i<3;i++)if(offset>=begin[i]&&offset-begin[i]<48000)return offset-begin[i];return UINT32_MAX;}
bool pocket_qualification_source_window(uint32_t offset){return local_sample(offset)!=UINT32_MAX;}
int16_t pocket_qualification_source_sample(uint32_t offset){
 uint32_t t=local_sample(offset);if(t==UINT32_MAX)return 0;
 int32_t acc=0;for(unsigned i=0;i<8;i++)acc+=white(offset-i);for(unsigned i=8;i<16;i++)acc-=white(offset-i);
 int32_t base=acc/1024;uint32_t gain=t<240?t:47999-t<240?47999-t:240;return (int16_t)(base*(int32_t)gain/240);
}
static bool clock_check(pocket_qualification*q,const pocket_qualification_ops*o){uint64_t now=o->now_ms(o->context);if(now<q->last_ms){q->result.code=PQ_CLOCK_REGRESSION;return false;}q->last_ms=now;return true;}
static bool guard(pocket_qualification*q,const pocket_qualification_ops*o,uint64_t deadline,pocket_qualification_result_code timeout){
 if(o->stopped(o->context)){q->result.code=PQ_STOPPED;return false;}
 if(!clock_check(q,o))return false;
 if(q->last_ms-q->start_ms>=deadline){q->result.code=timeout;return false;}return true;
}
static void words(uint8_t*out,const uint32_t*p,size_t count){for(size_t i=0;i<count;i++)pocket_qualification_put_u32(out+i*4,p[i]);}
/* Returns -2 on fatal/partial transport contamination, -1 on bounded refusal. */
static int record(pocket_qualification*q,const pocket_qualification_config*c,const pocket_qualification_ops*o,uint8_t kind,uint32_t offset,size_t bytes,bool final){
 pocket_qualification_packet p={.nonce=c->nonce,.sequence=q->wire_sequence,.sample_offset=offset,.kind=kind,.profile=c->profile,.payload=q->payload,.bytes=bytes};
 size_t n=pocket_qualification_encode(&p,q->wire,sizeof q->wire);
 if(!n||n>PQ_MAX_WIRE_BYTES-q->result.wire_bytes){q->result.code=PQ_EMIT_ERROR;return -2;}
 uint64_t began=o->now_ms(o->context);unsigned attempts=0;
 for(;;){
  if(attempts++>=20){q->result.code=PQ_EMIT_ERROR;return -1;}
  if(!clock_check(q,o))return -1;
  if(q->last_ms<began||q->last_ms-began>=20){q->result.code=PQ_EMIT_ERROR;return -1;}
  if(!final&&o->stopped(o->context)&&kind!=PQ_START){q->result.code=PQ_STOPPED;return -1;}
  int r=o->emit(o->context,q->wire,n);
  if(r<0||r>1){q->result.code=PQ_EMIT_ERROR;return -2;}
  if(r==1){q->wire_sequence++;q->result.wire_bytes+=(uint32_t)n;return 1;}
  o->idle(o->context);
 }
}
pocket_qualification_result pocket_qualification_run(pocket_qualification*q,const pocket_qualification_config*c,const pocket_qualification_ops*o){
 pocket_qualification_result invalid={.code=PQ_INVALID_ARGUMENT,.cleanup=PQ_NOT_ACQUIRED};
 if(!q||q->attempted||!c||!c->nonce||!c->epoch||!pocket_qualification_frames(c->profile)||c->volume>5||(c->profile==PQ_SILENT&&c->volume)||!o||!o->begin||!o->capture||!o->submit||!o->end||!o->emit||!o->stopped||!o->now_ms||!o->idle)return invalid;
 q->attempted=true;q->result=(pocket_qualification_result){.code=PQ_COMPLETE};q->last_ms=q->start_ms=o->now_ms(o->context);
 uint32_t frames=pocket_qualification_frames(c->profile),source_cap=c->profile==PQ_AUDIO?576000u:0u,stimulus_cap=c->profile==PQ_AUDIO?144000u:0u,acq_budget=c->profile==PQ_AUDIO?24250u:10250u;
 bool acquired=false,wire_broken=false;
 pocket_qualification_begin_ownership ownership=PQ_BEGIN_UNTOUCHED;
 uint32_t start[12]={c->epoch,PQ_RATE,2,PQ_PAIRS,frames,source_cap,stimulus_cap,c->profile==PQ_AUDIO?512u:0u,c->volume,2000,acq_budget,c->profile==PQ_AUDIO?1u:0u};words(q->payload,start,12);
 int exported=record(q,c,o,PQ_START,0,PQ_STATUS_PAYLOAD_BYTES,false);
 if(exported!=1){wire_broken=exported==-2;goto done;}
 q->start_ms=q->last_ms;
 if(!guard(q,o,2000,PQ_STARTUP_TIMEOUT))goto done;
 ownership=PQ_BEGIN_UNKNOWN;
 bool started=o->begin(o->context,c->epoch,c->volume,&ownership);
 acquired=ownership==PQ_BEGIN_ACQUIRED;
 if(!started||!acquired){q->result.code=o->stopped(o->context)?PQ_STOPPED:PQ_START_FAILED;goto done;}
 if(!guard(q,o,2000,PQ_STARTUP_TIMEOUT))goto done;
 q->start_ms=q->last_ms;
 uint32_t polls=0;
 while(q->result.capture_frames<frames){
  if(polls++>=acq_budget*2){q->result.code=PQ_ACQUISITION_TIMEOUT;goto done;}
  if(!guard(q,o,acq_budget,PQ_ACQUISITION_TIMEOUT))goto done;
  int got=o->capture(o->context,&q->capture);
  if(!guard(q,o,acq_budget,PQ_ACQUISITION_TIMEOUT))goto done;
  if(got<0||got>1){q->result.code=PQ_CAPTURE_ERROR;goto done;}
  if(got){
   pocket_qualification_capture*f=&q->capture;
   if(f->epoch!=c->epoch||f->sequence!=q->result.capture_frames||f->sample_offset!=q->result.capture_frames*PQ_PAIRS||f->dma_consumed<q->result.dma_consumed||f->dma_consumed>q->result.source_samples||(f->queue_frames!=PQ_QUEUE_UNKNOWN&&f->queue_frames>8)){q->result.code=PQ_DISCONTINUITY;goto done;}
   uint32_t metadata[8]={f->epoch,f->sequence,(uint32_t)(q->last_ms-q->start_ms),q->result.source_samples,f->dma_consumed,f->queue_frames,0,0};words(q->payload,metadata,8);memcpy(q->payload+32,f->pcm,PQ_PCM_BYTES);
   exported=record(q,c,o,PQ_PCM,f->sample_offset,PQ_CAPTURE_PAYLOAD_BYTES,false);
   if(exported!=1){wire_broken=exported==-2;goto done;}
   q->result.capture_frames++;q->result.dma_consumed=f->dma_consumed;wipe(f,sizeof *f);
   if(!guard(q,o,acq_budget,PQ_ACQUISITION_TIMEOUT))goto done;
  }
  if(c->profile==PQ_AUDIO&&q->result.source_samples<source_cap&&q->result.source_samples<(q->result.capture_frames+1)*PQ_PAIRS){
   uint32_t stimulus=0,peak=0;for(unsigned i=0;i<PQ_PAIRS;i++){uint32_t t=q->result.source_samples+i;q->source[i]=pocket_qualification_source_sample(t);if(pocket_qualification_source_window(t))stimulus++;uint32_t a=(uint32_t)(q->source[i]<0?-q->source[i]:q->source[i]);if(a>peak)peak=a;}
   uint64_t submit_start=q->last_ms;unsigned attempts=0;
   for(;;){
    if(attempts++>=20){q->result.code=PQ_SUBMIT_ERROR;goto done;}
    if(!guard(q,o,acq_budget,PQ_ACQUISITION_TIMEOUT))goto done;
    if(q->last_ms-submit_start>=20){q->result.code=PQ_SUBMIT_ERROR;goto done;}
    int sent=o->submit(o->context,q->result.source_samples,q->source,PQ_PAIRS);
    if(sent<0||sent>1){q->result.code=PQ_SUBMIT_ERROR;goto done;}
    if(sent==1){q->result.source_samples+=PQ_PAIRS;q->result.stimulus_samples+=stimulus;if(peak>q->result.peak)q->result.peak=peak;break;}
    o->idle(o->context);
   }
   if(!guard(q,o,acq_budget,PQ_ACQUISITION_TIMEOUT))goto done;
  }
  if(!got)o->idle(o->context);
 }
 if(q->result.source_samples!=source_cap)q->result.code=PQ_SOURCE_INCOMPLETE;
done:
 q->result.source_complete=q->result.source_samples==source_cap;
 q->result.cleanup=acquired?(o->end(o->context,c->epoch)?PQ_CLEANUP_VERIFIED:PQ_CLEANUP_UNKNOWN):ownership==PQ_BEGIN_UNTOUCHED?PQ_NOT_ACQUIRED:PQ_CLEANUP_UNKNOWN;
 if(clock_check(q,o)&&q->last_ms>=q->start_ms){uint64_t elapsed=q->last_ms-q->start_ms;q->result.elapsed_ms=elapsed>UINT32_MAX?UINT32_MAX:(uint32_t)elapsed;}
 if(q->result.cleanup==PQ_CLEANUP_UNKNOWN&&acquired)q->result.code=PQ_CLEANUP_ERROR;
 wipe(&q->capture,sizeof q->capture);wipe(q->source,sizeof q->source);wipe(q->payload,sizeof q->payload);wipe(q->wire,sizeof q->wire);
 if(q->wire_sequence&&!wire_broken){
  uint32_t terminal[12]={c->epoch,q->result.code,q->result.cleanup,q->result.capture_frames,q->result.source_samples,q->result.stimulus_samples,q->result.dma_consumed,q->result.elapsed_ms,q->result.peak,q->result.wire_bytes,q->result.source_complete?1u:0u,0};words(q->payload,terminal,12);
  q->result.terminal_emitted=record(q,c,o,PQ_TERMINAL,0,PQ_STATUS_PAYLOAD_BYTES,true)==1;
 }
 wipe(q->payload,sizeof q->payload);wipe(q->wire,sizeof q->wire);return q->result;
}
