/* MIT. Private source-verified v2 binding. Public entry remains hard refused
 * pending actual device acceptance, not because paired reference is absent.
 * No UI task creates this context or calls these operations in this checkpoint. */
#define POCKET_LIVE_V2_IDF_INTERNAL 1
#include "pocket_live_v2_idf.h"
#include "pocket_live_v2_audio.h"
#include "pocket_live_v2_control.h"
#include "pocket_live_network_idf.h"
#include "esp_timer.h"
#include "esp_transport_ws.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
typedef pocket_live_v2_idf_context paired_idf_context;
#if CONFIG_POCKET_QUALIFICATION
/* Defined by the pure qualification core; source reconstruction is never AEC. */
#include "pocket_qualification.h"
static bool qualification_stopped(void*v){
 paired_idf_context*c=v;
 if(c->qualification_stopping)return true;
 bool source_failed=c->qualification_failed;
 int64_t micros=esp_timer_get_time();
 uint64_t now=micros<0?0:(uint64_t)(micros/1000);
 if(micros<0||now<c->qualification_last_ms){c->qualification_clock_failed=true;c->qualification_stopping=true;}
 c->qualification_last_ms=now;
 if(now>=c->qualification_deadline_ms){c->qualification_deadline_reached=true;c->qualification_stopping=true;}
 if(source_failed||c->config.stopped(c->config.stop_context))c->qualification_stopping=true;
 if(c->qualification_stopping)c->qualification_stop_observed_ms=now;
 return c->qualification_stopping;
}
static void monitor_(paired_idf_context*c,pocket_qualification_monitor_stage stage){
 if(c->qualification&&c->qualification_monitor)c->qualification_monitor(c->qualification_monitor_context,stage);
}
static uint64_t observed_ms(void){int64_t n=esp_timer_get_time();return n<0?0:(uint64_t)(n/1000);}
#endif
static bool connect_(void*v){
    paired_idf_context*c=v;
    if(!c||!c->prepared||c->config.target>1||!c->config.epoch)return false;
    c->audio.epoch=c->config.epoch;
    pocket_live_network_config config={.host=c->config.host,.token=c->config.token,
        .ca_pem=c->config.ca_pem,.path=c->config.target?"/targets/linux/live":"/targets/windows/live",
        .port=c->config.port,.stopped=c->config.stopped,.stop_context=c->config.stop_context};
#if CONFIG_POCKET_QUALIFICATION
    if(c->qualification){config.path="/qualification/v2/live";config.stopped=qualification_stopped;config.stop_context=c;}
#endif
    c->network_clean=false;
    if(!pocket_live_network_open_begin(&c->network,&config))return false;
    for(;;){
        int progress=pocket_live_network_open_step(&c->network);
        if(progress)return progress==1;
        vTaskDelay(1); /*TLS/DNS owner yields; callbacks never own its stack.*/
    }
}
static bool send_(void*v,bool binary,const uint8_t*data,size_t bytes){
    paired_idf_context*c=v;return pocket_live_network_send(&c->network,binary,data,bytes);
}
static bool receive_(void*v,pocket_live_worker_event*e,uint8_t*out,size_t capacity){
    paired_idf_context*c=v;e->kind=PLW_NONE;
    pocket_live_network_message meta={0};
    int n=pocket_live_network_read(&c->network,c->chunk,sizeof c->chunk,&meta);
    if(n<0)return false;
    if(!n)return true;
    if(!meta.fin||meta.payload_bytes==0||meta.payload_bytes>sizeof c->message||
        (meta.opcode!=WS_TRANSPORT_OPCODES_BINARY&&meta.opcode!=WS_TRANSPORT_OPCODES_TEXT))return false;
    if(c->used==0){c->expected=meta.payload_bytes;c->opcode=meta.opcode;}
    if(c->expected!=meta.payload_bytes||c->opcode!=meta.opcode||(size_t)n>c->expected-c->used)return false;
    memcpy(c->message+c->used,c->chunk,(size_t)n);c->used+=(size_t)n;
    if(c->used<c->expected)return true;
    bool ok;
    if(c->opcode==WS_TRANSPORT_OPCODES_BINARY){
        ok=c->used<=capacity;
        if(ok){memcpy(out,c->message,c->used);e->kind=PLW_AUDIO;e->bytes=c->used;}
    }else ok=pocket_live_v2_control((char*)c->message,c->used,e);
    c->used=c->expected=0;return ok;
}
static bool audio_start(void*v){paired_idf_context*c=v;c->audio_clean=false;
#if CONFIG_POCKET_QUALIFICATION
 monitor_(c,PQTM_AUDIO_START);
 if(c->qualification&&qualification_stopped(c))return false;
#endif
 return pocket_live_v2_audio_start(&c->audio);
}
static int capture(void*v,pocket_live_v2_capture*p){paired_idf_context*c=v;
#if CONFIG_POCKET_QUALIFICATION
 monitor_(c,PQTM_CAPTURE);
 if(c->qualification&&qualification_stopped(c))return -1;
#endif
 return pocket_live_v2_audio_capture(&c->audio,p);
}
static int submit(void*v,uint32_t offset,const uint8_t*p,size_t bytes){
 paired_idf_context*c=v;
#if CONFIG_POCKET_QUALIFICATION
 if(c->qualification){
  if(qualification_stopped(c))return -1;
  if(!p||!bytes||bytes>960||bytes%2||offset!=c->qualification_accepted||offset>1440000u||bytes/2>1440000u-offset){c->qualification_failed=true;return -1;}
  for(size_t i=0;i<bytes/2;i++){
   int16_t sample=(int16_t)((uint16_t)p[2*i]|((uint16_t)p[2*i+1]<<8));
   int16_t expected=c->qualification_profile==PQ_AUDIO&&offset+i<576000u?pocket_qualification_source_sample(offset+(uint32_t)i):0;
   uint32_t at=offset+(uint32_t)i;
   bool window=(at>=96000u&&at<144000u)||(at>=240000u&&at<288000u)||(at>=432000u&&at<480000u);
   if(sample<-512||sample>512||sample!=expected||(sample&&!window)){c->qualification_failed=true;return -1;}
  }
  monitor_(c,PQTM_SUBMIT);
  if(qualification_stopped(c))return -1;
  int result=pocket_live_v2_audio_submit(&c->audio,offset,p,bytes);
  if(result==1)c->qualification_accepted+=(uint32_t)(bytes/2);
  return result;
 }
#endif
 return pocket_live_v2_audio_submit(&c->audio,offset,p,bytes);
}
static bool audio_stop(void*v){paired_idf_context*c=v;
#if CONFIG_POCKET_QUALIFICATION
 if(c->qualification){(void)qualification_stopped(c);c->qualification_audio_stop_begin_ms=observed_ms();}
#endif
 c->audio_clean=pocket_live_v2_audio_stop(&c->audio);
#if CONFIG_POCKET_QUALIFICATION
 if(c->qualification){c->qualification_audio_stop_end_ms=observed_ms();monitor_(c,PQTM_AUDIO_STOP_RETURNED);}
#endif
 return c->audio_clean;
}
static bool close_(void*v){paired_idf_context*c=v;c->network_clean=pocket_live_network_close(&c->network);
#if CONFIG_POCKET_QUALIFICATION
 monitor_(c,PQTM_NETWORK_CLOSE_RETURNED);
#endif
 return c->network_clean;
}
static uint64_t now(void*v){(void)v;return (uint64_t)(esp_timer_get_time()/1000);}
static bool stopped(void*v){paired_idf_context*c=v;
#if CONFIG_POCKET_QUALIFICATION
 if(c->qualification)return qualification_stopped(c);
#endif
 return c->config.stopped(c->config.stop_context);
}
/*Not exposed through the physical-release gate. C compiler checks real headers,
 * but compile success does not prove scheduling, acoustics or heap headroom.*/
static const pocket_live_v2_worker_ops operations __attribute__((used))={
    NULL,connect_,send_,receive_,audio_start,capture,submit,audio_stop,close_,now,stopped
};

bool pocket_live_v2_idf_prepare(pocket_live_v2_idf_context*c,const pocket_live_session_config*config,bool(*stop)(void*),void*stop_context,bool*acquired){
 if(!acquired)return false;
 *acquired=false;
 if(!c||c->prepared||c->retired||!stop||!pocket_live_session_config_valid(config))return false;
 *acquired=true;
 c->snapshot=*config;
 c->config=(pocket_live_idf_config){.host=c->snapshot.host,.token=c->snapshot.token,.ca_pem=c->snapshot.ca_pem,.chat_id=c->snapshot.chat_id,
  .port=c->snapshot.port,.target=c->snapshot.target,.epoch=c->snapshot.epoch,.stopped=stop,.stop_context=stop_context};
 c->audio.epoch=config->epoch;c->audio.volume=config->volume;
 c->audio.stopped=stop;c->audio.stop_context=stop_context;
 c->audio_clean=c->network_clean=true;c->prepared=true;return true;
}
#if CONFIG_POCKET_QUALIFICATION
bool pocket_live_v2_idf_qualification_config_valid(const pocket_live_session_config*k,uint8_t profile){
 if((profile!=PQ_SILENT&&profile!=PQ_AUDIO)||!pocket_live_session_config_valid(k)||(profile==PQ_SILENT&&k->volume!=0)||k->volume>5||strcmp(k->chat_id,"qualification")||
    !strstr(k->ca_pem,"-----BEGIN CERTIFICATE-----")||!strstr(k->ca_pem,"-----END CERTIFICATE-----"))return false;
 for(const char*p=k->token;*p;p++){
  if(!((*p>='A'&&*p<='Z')||(*p>='a'&&*p<='z')||(*p>='0'&&*p<='9')||*p=='_'||*p=='-'))return false;
 }
 unsigned octets[4]={0},part=0;
 for(const char*p=k->host;*p;p++){
  if(*p=='.'){if(++part>3)return false;}
  else {if(*p<'0'||*p>'9')return false;octets[part]=octets[part]*10u+(unsigned)(*p-'0');}
 }
 if(part!=3)return false;
 return octets[0]==10||(octets[0]==172&&octets[1]>=16&&octets[1]<=31)||(octets[0]==192&&octets[1]==168);
}
bool pocket_live_v2_idf_prepare_qualification(pocket_live_v2_idf_context*c,const pocket_live_session_config*k,uint8_t profile,bool(*stop)(void*),void*v,pocket_qualification_monitor_fn monitor,void*monitor_context,bool*acquired){
 if(!acquired)return false;
 *acquired=false;
 int64_t micros=esp_timer_get_time();
 if(micros<0)return false;
 uint64_t now=(uint64_t)(micros/1000);
 if(now>UINT64_MAX-60000u||!pocket_live_v2_idf_qualification_config_valid(k,profile)||!pocket_live_v2_idf_prepare(c,k,stop,v,acquired))return false;
 c->qualification_last_ms=now;c->qualification_deadline_ms=now+60000u;
 c->qualification_profile=profile;c->qualification_monitor=monitor;c->qualification_monitor_context=monitor_context;
 c->qualification=true;c->audio.stopped=qualification_stopped;c->audio.stop_context=c;return true;
}
#endif
bool pocket_live_v2_idf_take_ops(pocket_live_v2_idf_context*c,pocket_live_v2_worker_ops*out){
 if(!c||!out||!c->prepared||c->issued||c->retired)return false;
 *out=operations;out->context=c;c->issued=true;return true;
}
pocket_live_v2_worker_result pocket_live_v2_idf_execute(pocket_live_v2_idf_context*c,pocket_live_v2_worker*w){
 pocket_live_v2_worker_result result={.reason=PLW_INVALID_ARGUMENT,
  .cleanup_complete=!c||!c->prepared||(c->audio_clean&&c->network_clean)};
 if(!c||!w||!c->prepared||c->issued||c->retired)return result;
 uintptr_t cp=(uintptr_t)c,wp=(uintptr_t)w;
 if(wp-cp<sizeof *c||cp-wp<sizeof *w)return result;
 pocket_live_v2_worker_ops ops;
 if(!pocket_live_v2_idf_take_ops(c,&ops))return result;
 c->worker=w;memset(w,0,sizeof *w);
 result=pocket_live_v2_worker_run(w,&ops,c->snapshot.chat_id,c->snapshot.epoch);
 if(!c->audio_clean||!c->network_clean){result.cleanup_complete=false;}
 return result;
}
bool pocket_live_v2_idf_cleanup(pocket_live_v2_idf_context*c){
 if(!c)return false;
 if(!c->prepared)return true;
 /* Audio stops before potentially stalled network destruction. Both flags are
  * retained; a late DNS settlement never causes audio to restart or resubmit. */
 if(!c->audio_clean)(void)audio_stop(c);
 if(!c->network_clean)(void)close_(c);
 return c->audio_clean&&c->network_clean;
}
bool pocket_live_v2_idf_scrub(pocket_live_v2_idf_context*c){
 if(!c)return false;
 if(c->prepared&&(!c->audio_clean||!c->network_clean))return false;
 if(c->worker){volatile uint8_t*w=(volatile uint8_t*)c->worker;for(size_t i=0;i<sizeof *c->worker;i++)w[i]=0;}
 volatile uint8_t*p=(volatile uint8_t*)c;for(size_t i=0;i<sizeof *c;i++)p[i]=0;
 c->retired=true;return true;
}
