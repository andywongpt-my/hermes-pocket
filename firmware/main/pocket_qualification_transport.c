#include "pocket_qualification_transport.h"
#if CONFIG_POCKET_QUALIFICATION
#include <string.h>
#include <limits.h>
static bool stopped(void*v){
 pocket_qualification_transport*c=v;
 if(c->stop_latched)return true;
 uint64_t now=c->options.clock_ms(c->options.clock_context);
 if(now<c->last_ms){c->clock_regressed=true;c->stop_latched=true;}
 c->last_ms=now;
 if(now>=c->stop_deadline_ms){c->deadline_reached=true;c->stop_latched=true;}
 if(c->options.stop(c->options.stop_context))c->stop_latched=true;
 if(c->stop_latched)c->stop_observed_ms=now;
 return c->stop_latched;
}
static void wipe_retired_packets(pocket_qualification_transport*c){
 /* Worker is terminal; cleanup retains config/audio/network/DNS ownership, not
  * packet payloads. This does not erase BSP/SDK-owned DMA buffers. */
 void*blocks[]={&c->worker.capture,c->worker.receive,c->worker.transmit,&c->worker.speaker,c->idf.message,c->idf.chunk,c->idf.network.mutable_wire};
 const size_t sizes[]={sizeof c->worker.capture,sizeof c->worker.receive,sizeof c->worker.transmit,sizeof c->worker.speaker,sizeof c->idf.message,sizeof c->idf.chunk,sizeof c->idf.network.mutable_wire};
 for(size_t j=0;j<sizeof sizes/sizeof sizes[0];j++){volatile unsigned char*p=blocks[j];for(size_t i=0;i<sizes[j];i++)p[i]=0;}
}
static bool clean(const pocket_qualification_transport*c){return c->idf.audio_clean&&c->idf.network_clean;}
static bool network_pending(const pocket_qualification_transport*c){return c->phase==PQT_PENDING&&c->idf.prepared&&c->idf.audio_clean&&!c->idf.network_clean;}
bool pocket_qualification_transport_prepare(pocket_qualification_transport*c,
 const pocket_live_session_config*k,const pocket_qualification_transport_options*o,bool*acquired){
 if(!acquired)return false;
 *acquired=false;
 if(!c||!o||c->phase!=PQT_EMPTY||c->idf.prepared||c->idf.retired||!o->stop||!o->clock_ms||
    !o->duration_ms||o->duration_ms>60000||!o->request_ms||o->request_ms>UINT64_MAX-60000||
    o->dispatch_deadline_ms<=o->request_ms||o->dispatch_deadline_ms-o->request_ms>10000||
    !pocket_live_v2_idf_qualification_config_valid(k,o->profile))return false;
 uint64_t now=o->clock_ms(o->clock_context);
 if(now<o->request_ms||now>=o->dispatch_deadline_ms||now>=o->request_ms+o->duration_ms||o->stop(o->stop_context))return false;
 c->options=*o;c->stop_deadline_ms=o->request_ms+o->duration_ms;c->last_ms=now;
 if(!pocket_live_v2_idf_prepare_qualification(&c->idf,k,o->profile,stopped,c,o->monitor,o->monitor_context,acquired))return false;
 c->phase=PQT_PREPARED;return true;
}
bool pocket_qualification_transport_run(pocket_qualification_transport*c){
 if(!c||c->phase!=PQT_PREPARED)return false;
 c->phase=PQT_RUNNING;
 if(stopped(c)||c->last_ms>=c->options.dispatch_deadline_ms){
  c->stop_latched=true;c->result=(pocket_live_v2_worker_result){.reason=PLW_STOPPED_BEFORE_CONNECT,.cleanup_complete=clean(c)};
 }else {c->worker_ran=true;c->result=pocket_live_v2_idf_execute(&c->idf,&c->worker);}
 wipe_retired_packets(c);
 c->result.actual_playout_known=false;
 c->result.cleanup_complete=clean(c);
 c->phase=clean(c)?PQT_DONE:PQT_PENDING;return true;
}
bool pocket_qualification_transport_cleanup(pocket_qualification_transport*c){
 if(!c)return false;
 if(c->phase==PQT_DONE)return true;
 if(!network_pending(c))return false;
 if(!pocket_live_v2_idf_cleanup(&c->idf))return false;
 c->phase=PQT_DONE;return true;
}
void pocket_qualification_transport_read(const pocket_qualification_transport*c,pocket_qualification_transport_snapshot*s){
 if(!c||!s)return;
 *s=(pocket_qualification_transport_snapshot){.phase=c->phase,.result=c->result,
  .accepted_source_samples=c->idf.qualification_accepted,
  .capture_frames=c->worker.microphone.next_sequence,.capture_samples=c->worker.microphone.next_sample_offset,.dma_consumed=c->worker.microphone.dma_consumed,
  .stop_observed_ms=c->stop_observed_ms?c->stop_observed_ms:c->idf.qualification_stop_observed_ms,
  .audio_stop_begin_ms=c->idf.qualification_audio_stop_begin_ms,.audio_stop_end_ms=c->idf.qualification_audio_stop_end_ms,
  .audio_cleanup_complete=c->idf.audio_clean,.network_cleanup_complete=c->idf.network_clean,.audio_attempted=c->idf.audio.attempted,.source_rejected=c->idf.qualification_failed,
  .cleanup_complete=c->phase==PQT_DONE&&clean(c),.network_cleanup_pending=network_pending(c),
  .clock_regressed=c->clock_regressed||c->idf.qualification_clock_failed,.deadline_reached=c->deadline_reached||c->idf.qualification_deadline_reached,.worker_ran=c->worker_ran};
}
bool pocket_qualification_transport_scrub(pocket_qualification_transport*c){
 if(!c||c->phase!=PQT_DONE||!clean(c)||!pocket_live_v2_idf_scrub(&c->idf))return false;
 volatile unsigned char*p=(volatile unsigned char*)c;for(size_t i=0;i<sizeof *c;i++)p[i]=0;
 c->phase=PQT_RETIRED;return true;
}
#endif
