/* MIT. Included only by the existing voice owner, after its state declarations.
 * Main is the only USB RX reader. No saved link/config/provider access here. */
static pocket_qualification_control qualification_control;
static bool qualification_owned,qualification_quarantined,qualification_terminal_confirmed;
static unsigned qualification_volume;
static bool qualification_network_wait;
static uint32_t qualification_frames;
typedef struct {
 pocket_qualification core;
 pocket_qualification_config config;
 pocket_live_v2_audio_context audio;
 pocket_live_v2_capture capture;
} qualification_bundle;
static qualification_bundle *qualification_storage; /* Retained on unknown cleanup. */
static uint64_t qualification_now(void*unused){(void)unused;return (uint64_t)(esp_timer_get_time()/1000);}
static void qualification_wipe(void*p,size_t n){volatile uint8_t*b=p;while(n--)*b++=0;}
static void qualification_init(void){(void)pocket_qualification_control_init(&qualification_control);}
void pocket_voice_qualification_poll(void){
 uint8_t b[PQ_HOST_CHUNK_MAX];int n=pocket_qualification_usb_read(b,sizeof b);
 portENTER_CRITICAL(&mux);
 if(n<0)pocket_qualification_control_input_lost(&qualification_control);
 else if(n>0)(void)pocket_qualification_control_feed(&qualification_control,b,(size_t)n,qualification_now(NULL));
 (void)pocket_qualification_control_check(&qualification_control,qualification_now(NULL));
 portEXIT_CRITICAL(&mux);qualification_wipe(b,sizeof b);
}
uint32_t pocket_voice_qualification_button(bool ok,bool press,bool release,uint64_t*event_ms){
 if(!event_ms){pocket_voice_qualification_input_lost();return 0;}
 portENTER_CRITICAL(&mux);*event_ms=qualification_now(NULL);uint32_t g=pocket_qualification_control_button(&qualification_control,ok,press,release,*event_ms);portEXIT_CRITICAL(&mux);return g;
}
void pocket_voice_qualification_input_lost(void){portENTER_CRITICAL(&mux);pocket_qualification_control_input_lost(&qualification_control);portEXIT_CRITICAL(&mux);}
bool pocket_voice_qualification_active(void){portENTER_CRITICAL(&mux);bool v=qualification_owned;portEXIT_CRITICAL(&mux);return v;}
bool pocket_voice_qualification_locked(void){portENTER_CRITICAL(&mux);bool v=qualification_quarantined;portEXIT_CRITICAL(&mux);return v;}
bool pocket_voice_qualification_arm(uint64_t ms,uint32_t generation){
 portENTER_CRITICAL(&mux);bool ok=!state.busy&&!audio_unavailable&&commands&&pocket_qualification_usb_available()&&pocket_qualification_control_arm(&qualification_control,ms,generation,qualification_now(NULL));portEXIT_CRITICAL(&mux);return ok;
}
static void qualification_finish(bool safe,bool checked,uint32_t code){
 /* Scrubbing and USB restoration are completed by the owner before release. */
 if(safe)pocket_setup_voice_release();
 portENTER_CRITICAL(&mux);
 (void)pocket_qualification_control_finish(&qualification_control,safe,checked,code);
 qualification_owned=!safe;qualification_quarantined=!safe;state.busy=!safe;state.recording=false;state.playing=false;
 if(!safe)audio_unavailable=true;
 portEXIT_CRITICAL(&mux);
}
bool pocket_voice_qualification_begin(uint64_t ms,uint32_t generation){
 portENTER_CRITICAL(&mux);
 bool ok=!state.busy&&!audio_unavailable&&commands&&pocket_qualification_usb_available()&&pocket_qualification_control_admit(&qualification_control,ms,generation,qualification_now(NULL));
 if(!ok){portEXIT_CRITICAL(&mux);return false;}
 if(!pocket_setup_voice_acquire()){
  (void)pocket_qualification_control_finish(&qualification_control,true,false,PQ_START_FAILED);portEXIT_CRITICAL(&mux);return false;
 }
 qualification_owned=true;state.busy=true;state.recording=false;state.playing=false;held=false;cancelled=false;
 qualification_volume=qualification_control.state.profile==PQ_AUDIO?(state.volume<5?state.volume:5):0;
 portEXIT_CRITICAL(&mux);pocket_reader_close();unsigned command=4;
 if(xQueueSend(commands,&command,0)!=pdTRUE){qualification_finish(true,false,PQ_START_FAILED);return false;}
 return true;
}
static bool qualification_stopped(void*unused){
 (void)unused;portENTER_CRITICAL(&mux);bool ok=pocket_qualification_control_check(&qualification_control,qualification_now(NULL));bool stop=!ok||qualification_control.state.stop||cancelled;portEXIT_CRITICAL(&mux);return stop;
}
static void qualification_idle(void*unused){(void)unused;vTaskDelay(pdMS_TO_TICKS(1));}
static bool qualification_audio_begin(void*v,uint32_t epoch,unsigned volume,pocket_qualification_begin_ownership*ownership){
 qualification_bundle*b=v;b->audio.epoch=epoch;b->audio.volume=volume;b->audio.stopped=qualification_stopped;b->audio.stop_context=NULL;
 bool ok=pocket_live_v2_audio_start(&b->audio);
 *ownership=b->audio.cleanup_required?PQ_BEGIN_ACQUIRED:!b->audio.attempted?PQ_BEGIN_UNTOUCHED:PQ_BEGIN_UNKNOWN;
 return ok;
}
static int qualification_capture(void*v,pocket_qualification_capture*out){
 qualification_bundle*b=v;int r=pocket_live_v2_audio_capture(&b->audio,&b->capture);
 if(r==1){out->epoch=b->capture.epoch;out->sequence=b->capture.sequence;out->sample_offset=b->capture.sample_offset;out->dma_consumed=b->capture.dma_consumed;out->queue_frames=PQ_QUEUE_UNKNOWN;memcpy(out->pcm,b->capture.pcm,sizeof out->pcm);}
 qualification_wipe(&b->capture,sizeof b->capture);return r;
}
static int qualification_submit(void*v,uint32_t off,const int16_t*p,size_t n){qualification_bundle*b=v;return pocket_live_v2_audio_submit(&b->audio,off,(const uint8_t*)p,n*sizeof *p);}
static bool qualification_end(void*v,uint32_t epoch){qualification_bundle*b=v;if(b->audio.epoch!=epoch)return false;return pocket_live_v2_audio_stop(&b->audio);}
static int qualification_emit(void*v,const uint8_t*p,size_t n){
 (void)v;int r=pocket_qualification_usb_write(p,n);
 if(r==1&&n>=PQ_HEADER_BYTES&&p[5]==PQ_PCM){portENTER_CRITICAL(&mux);qualification_frames++;portEXIT_CRITICAL(&mux);}
 return r;
}
static bool qualification_drain(unsigned budget,unsigned iterations,bool stop_guard){
 uint64_t start=qualification_now(NULL),last=start;
 for(unsigned i=0;i<iterations;i++){
  uint64_t now=qualification_now(NULL);if(now<last||now-start>=budget||(stop_guard&&qualification_stopped(NULL)))return false;last=now;
  int r=pocket_qualification_usb_export_ready();if(r!=0)return r==1;qualification_idle(NULL);
 }
 return false;
}
#include "pocket_qualification_tls_voice.inc"
static void qualification_run(void){
 portENTER_CRITICAL(&mux);bool dispatch=pocket_qualification_control_dispatch(&qualification_control,qualification_now(NULL));
 pocket_qualification_control_snapshot s; pocket_qualification_control_read(&qualification_control,&s);portEXIT_CRITICAL(&mux);
 if(!dispatch){qualification_finish(true,false,PQ_STOPPED);return;}
 if(s.tls){qualification_tls_run(&s);return;}
 qualification_bundle*b=calloc(1,sizeof *b);
 if(!b){qualification_finish(true,false,PQ_START_FAILED);return;}
 qualification_storage=b;b->config=(pocket_qualification_config){.nonce=s.nonce,.epoch=1,.profile=s.profile,.volume=(uint8_t)qualification_volume};
 bool exporting=pocket_qualification_usb_export_begin();
 if(!exporting||!qualification_drain(2000,2000,true)){
  if(exporting)pocket_qualification_usb_export_end();
  qualification_wipe(b,sizeof *b);free(b);qualification_storage=NULL;qualification_finish(true,false,PQ_EMIT_ERROR);return;
 }
 pocket_qualification_ops ops={b,qualification_audio_begin,qualification_capture,qualification_submit,qualification_end,qualification_emit,qualification_stopped,qualification_now,qualification_idle};
 pocket_qualification_result r=pocket_qualification_run(&b->core,&b->config,&ops);
 bool safe=r.cleanup!=PQ_CLEANUP_UNKNOWN,checked=r.cleanup==PQ_CLEANUP_VERIFIED;
 /* Finite queue drain is not proof the host received or validated a terminal. */
 bool drained=r.terminal_emitted&&qualification_drain(100,100,false);
 portENTER_CRITICAL(&mux);qualification_terminal_confirmed=drained;portEXIT_CRITICAL(&mux);
 if(safe){pocket_qualification_usb_export_end();qualification_wipe(b,sizeof *b);free(b);qualification_storage=NULL;}
 else{qualification_wipe(&b->capture,sizeof b->capture);/* Retain owner/context; no automatic I2C retry. */}
 qualification_finish(safe,checked,!drained?PQ_EMIT_ERROR:r.code);
}
void pocket_voice_qualification_format(char*title,size_t tn,char*detail,size_t dn,char*hint,size_t hn){
 pocket_qualification_control_snapshot s;uint32_t count;bool terminal,pending;
 portENTER_CRITICAL(&mux);pocket_qualification_control_read(&qualification_control,&s);count=qualification_frames;terminal=qualification_terminal_confirmed;pending=qualification_network_wait;portEXIT_CRITICAL(&mux);
 const char*profile=s.profile==PQ_AUDIO?"AUDIO <=5%":"SILENT";
 snprintf(title,tn,"%s",s.tls?"TLS LOCAL TEST":"USB LOCAL TEST");
 const char*phase=pending?"Network cleanup pending":s.phase==PQC_IDLE?"Connect local collector":s.phase==PQC_HOST_ARMED?"Host armed":s.phase==PQC_PHYSICAL_ARMED?"Release then press OK":s.phase==PQC_ADMITTED?"Queued":s.phase==PQC_RUNNING?(s.tls?"TLS fixture active":"Local capture"):s.phase==PQC_QUARANTINED?"Cleanup locked":"Attempt ended";
 snprintf(detail,dn,"%s\n%s | %lu frames\nMic/ref exported via %s\nCleanup: %s\nTerminal: %s\nNo acoustic pass",phase,profile,(unsigned long)count,s.tls?"TLS":"USB",s.cleanup_verified?"checked":s.phase==PQC_QUARANTINED?"unknown":"not checked",terminal?"queued, not receipt":"not confirmed");
 snprintf(hint,hn,"%s",s.phase==PQC_HOST_ARMED?"Hold OK to arm":s.phase==PQC_PHYSICAL_ARMED?"Fresh OK starts\nOther key cancels":s.phase==PQC_RUNNING?"Any press stops":"Screen Off remains standby");
}
