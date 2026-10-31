/* Included by qualification_voice.inc: explicit RAM-only Stage B composition. */
typedef struct {
 pocket_qualification_transport transport;
 pocket_qualification_tls_config input;
 pocket_live_session_config config;
 pocket_qualification_transport_snapshot terminal;
 uint64_t nonce,request_ms,next_reap;
 uint32_t min_heap,min_block,min_stack,monitor_count,terminal_code;
 uint8_t profile,wire[PQ_MAX_RECORD],payload[96];
 bool acquired,exporting,terminal_queued,network_pending;
} qualification_tls_bundle;
static qualification_tls_bundle *qualification_tls_storage;
static void qualification_monitor(void*v,pocket_qualification_monitor_stage stage){
 (void)stage;qualification_tls_bundle*b=v;
 unsigned caps=MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT;
 size_t heap=heap_caps_get_free_size(caps),minimum=heap_caps_get_minimum_free_size(caps),block=heap_caps_get_largest_free_block(caps);
 uint32_t stack=(uint32_t)uxTaskGetStackHighWaterMark(NULL);
 if(minimum<heap)heap=minimum;
 if(heap<b->min_heap)b->min_heap=(uint32_t)heap;
 if(block<b->min_block)b->min_block=(uint32_t)block;
 if(stack<b->min_stack)b->min_stack=stack;
 if(b->monitor_count<UINT32_MAX)b->monitor_count++;
}
static uint32_t qualification_relative(uint64_t now,uint64_t request){return !now||now<request||now-request>=UINT32_MAX?UINT32_MAX:(uint32_t)(now-request);}
static bool qualification_tls_record(qualification_tls_bundle*b,uint8_t kind,const uint32_t*p,size_t count){
 for(size_t i=0;i<count;i++)pocket_qualification_put_u32(b->payload+i*4,p[i]);
 pocket_qualification_packet packet={.nonce=b->nonce,.sequence=kind==PQ_TLS_START?0:1,.kind=kind,.profile=b->profile,.payload=b->payload,.bytes=count*4};
 size_t bytes=pocket_qualification_encode(&packet,b->wire,sizeof b->wire);if(!bytes)return false;
 uint64_t began=qualification_now(NULL),last=began;
 for(unsigned i=0;i<20;i++){
  uint64_t now=qualification_now(NULL);if(now<last||now-began>=20||(kind==PQ_TLS_START&&qualification_stopped(NULL)))return false;last=now;
  int r=pocket_qualification_usb_write(b->wire,bytes);if(r!=0)return r==1;qualification_idle(NULL);
 }
 return false;
}
static void qualification_tls_release(qualification_tls_bundle*b,bool checked){
 uint32_t code=b->terminal_code;
 if(b->exporting)pocket_qualification_usb_export_end();
 qualification_wipe(b,sizeof *b);free(b);qualification_tls_storage=NULL;
 portENTER_CRITICAL(&mux);qualification_network_wait=false;portEXIT_CRITICAL(&mux);
 qualification_finish(true,checked,code);
}
static void qualification_progress(void){
 qualification_tls_bundle*b=qualification_tls_storage;
 if(!b||!b->network_pending)return;
 uint64_t now=qualification_now(NULL);if(now<b->next_reap)return;
 b->next_reap=now>UINT64_MAX-250?UINT64_MAX:now+250;
 if(!pocket_qualification_transport_cleanup(&b->transport))return;
 if(!pocket_qualification_transport_scrub(&b->transport)){b->network_pending=false;portENTER_CRITICAL(&mux);qualification_network_wait=false;portEXIT_CRITICAL(&mux);qualification_finish(false,false,PQ_CLEANUP_ERROR);return;}
 /* Historical terminal remains unchanged; no second session or terminal. */
 qualification_tls_release(b,b->terminal.audio_attempted&&b->terminal.audio_cleanup_complete);
}
static void qualification_tls_run(const pocket_qualification_control_snapshot*s){
 qualification_tls_bundle*b=calloc(1,sizeof *b);
 if(!b){qualification_finish(true,false,PQ_START_FAILED);return;}
 qualification_tls_storage=b;b->nonce=s->nonce;b->profile=s->profile;b->request_ms=s->request_ms;
 b->min_heap=b->min_block=b->min_stack=UINT32_MAX;
 portENTER_CRITICAL(&mux);bool taken=pocket_qualification_control_check(&qualification_control,qualification_now(NULL))&&pocket_qualification_control_take_tls_config(&qualification_control,&b->input);portEXIT_CRITICAL(&mux);
 if(!taken){b->terminal_code=PQ_START_FAILED;qualification_tls_release(b,false);return;}
 char origin[40];int n=snprintf(origin,sizeof origin,"https://%s:%u",b->input.host,(unsigned)b->input.port);
 bool configured=n>0&&(size_t)n<sizeof origin&&pocket_live_session_config_snapshot(&b->config,origin,b->input.token,b->input.ca,"qualification",0,1,qualification_volume);
 qualification_wipe(origin,sizeof origin);
 uint32_t duration=b->input.duration_ms;qualification_wipe(&b->input,sizeof b->input);
 if(!configured){b->terminal_code=PQ_START_FAILED;qualification_tls_release(b,false);return;}
 b->exporting=pocket_qualification_usb_export_begin();
 if(!b->exporting||!qualification_drain(2000,2000,true)){b->terminal_code=PQ_EMIT_ERROR;qualification_tls_release(b,false);return;}
 uint32_t start[12]={1,24000,2,240,duration,6000,1440000,s->profile==PQ_AUDIO?144000:0,qualification_volume,qualification_relative(qualification_now(NULL),s->request_ms),10000,0};
 if(!qualification_tls_record(b,PQ_TLS_START,start,12)){b->terminal_code=PQ_EMIT_ERROR;qualification_tls_release(b,false);return;}
 pocket_qualification_transport_options o={.profile=s->profile,.request_ms=s->request_ms,.dispatch_deadline_ms=s->dispatch_deadline_ms,.duration_ms=duration,.stop=qualification_stopped,.clock_ms=qualification_now,.monitor=qualification_monitor,.monitor_context=b};
 bool prepared=pocket_qualification_transport_prepare(&b->transport,&b->config,&o,&b->acquired);
 /* Only the owned IDF snapshot must retain credentials for pending cleanup. */
 pocket_live_session_config_clear(&b->config);
 if(prepared)(void)pocket_qualification_transport_run(&b->transport);
 pocket_qualification_transport_read(&b->transport,&b->terminal);
 pocket_qualification_transport_snapshot*t=&b->terminal;
 if(!prepared)t->result.reason=PLW_INVALID_ARGUMENT;
 bool clean=!b->acquired||t->cleanup_complete;
 if(clean&&b->acquired&&!pocket_qualification_transport_scrub(&b->transport)){clean=false;t->result.reason=PLW_CLEANUP_FAILED;}
 uint32_t terminal[24]={1,(uint32_t)t->result.reason,!b->acquired?0:clean?(t->audio_attempted?1:0):2,t->capture_frames,t->capture_samples,t->dma_consumed,t->accepted_source_samples,t->source_rejected,t->deadline_reached,t->clock_regressed,qualification_relative(t->stop_observed_ms,s->request_ms),qualification_relative(t->audio_stop_begin_ms,s->request_ms),qualification_relative(t->audio_stop_end_ms,s->request_ms),t->audio_cleanup_complete,t->network_cleanup_complete,t->result.terminal_received,t->result.finalized,t->result.backend_pending,b->min_heap,b->min_block,b->min_stack,b->monitor_count,qualification_relative(qualification_now(NULL),s->request_ms),0};

 b->terminal_queued=qualification_tls_record(b,PQ_TLS_TERMINAL,terminal,24)&&qualification_drain(100,100,false);
 portENTER_CRITICAL(&mux);qualification_terminal_confirmed=b->terminal_queued;qualification_frames=t->capture_frames;portEXIT_CRITICAL(&mux);
 b->terminal_code=b->terminal_queued?(uint32_t)t->result.reason:PQ_EMIT_ERROR;
 if(clean){
  qualification_tls_release(b,t->audio_attempted&&t->audio_cleanup_complete);return;
 }
 if(t->audio_cleanup_complete&&t->network_cleanup_pending){
  b->network_pending=true;portENTER_CRITICAL(&mux);qualification_network_wait=true;portEXIT_CRITICAL(&mux);uint64_t now=qualification_now(NULL);b->next_reap=now>UINT64_MAX-250?UINT64_MAX:now+250;return;
 }
 qualification_finish(false,false,PQ_CLEANUP_ERROR);
}
