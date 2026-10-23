/* MIT. Bounded half-duplex PCM capture/transport/playback, never on UI thread. */
#include "pocket_voice.h"
#include "pocket_setup.h"
#include "pocket_http.h"
#include "pocket_usage_client.h"
#include "pocket_models_client.h"
#include "pocket_utf8.h"
#include "pocket_power.h"
#include "pocket_actions.h"
#include "pocket_reader.h"
#include "pocket_speech_client.h"
#include "bsp_audio.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_sntp.h"
#include "pocket_clock.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "cJSON.h"
#include "mbedtls/base64.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <stdatomic.h>
typedef struct { size_t size; unsigned char pcm[4096]; } audio_frame;
enum { CAPTURE_PCM_BYTES=2048, CAPTURE_QUEUE_FRAMES=4, CAPTURE_BODY_BYTES=2816, CAPTURE_ACK_BYTES=8192-CAPTURE_BODY_BYTES };
typedef struct { size_t size; unsigned char pcm[CAPTURE_PCM_BYTES]; } capture_frame;
_Static_assert(sizeof(capture_frame)<=CAPTURE_ACK_BYTES,"consumer frame must fit below POST storage");
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static pocket_voice_state state={.volume=60};
static bool held,cancelled,capture_done,capture_failed;
static unsigned pending_model_choice; /* Frozen with the one queued voice command. */
static char pending_begin_body[96];
static bool delivery_seen[2];
static unsigned delivery_caps[2];
static enum { VOICE_PRE_FINISH,VOICE_SUBMISSION_ATTEMPT,VOICE_LOCAL_PLAYBACK } phase;
enum { CAPTURE_READ_ERROR=1, CAPTURE_QUEUE_FULL=2, CAPTURE_NO_MEMORY=3, CAPTURE_NO_TASK=4 };
enum { VOICE_STATUS=1, VOICE_BEGIN=2, VOICE_CHUNK=3, VOICE_FINISH=4, VOICE_STT=5, VOICE_REPLY=6, VOICE_SPEECH=7, VOICE_PLAYBACK=8, VOICE_ABORT=9 };
typedef struct { unsigned reason,bytes,queue_peak,capture_ms,max_read_ms;int error; } capture_diagnostics;
static capture_diagnostics capture_diag;
static unsigned max_upload_ms;
typedef struct {
 unsigned count,request_us,max_log_us,max_json_us,max_post_us,report_us;
 esp_err_t error;pocket_http_result http;
} upload_diagnostics;
static upload_diagnostics upload_diag;
/* Emit once after capture/provider handling, never on the PCM upload path.
   Phase offsets describe the slowest request; max_post/log/json span all chunks. */
static void upload_report(unsigned target){
 if(!upload_diag.count)return;
 int64_t began=esp_timer_get_time();const pocket_http_result *r=&upload_diag.http;
 ESP_LOGI("pocket_upload","target=%u count=%u error=%d http=%d request_us=%u http_us=%u pre_heap_us=%u perform_us=%u performed=%u post_heap_us=%u diag_us=%u event_us=%u connected=%u connect_ms=%u headers=%u headers_ms=%u response=%u response_ms=%u body=%u body_ms=%u finish=%u finish_ms=%u perform_end_ms=%u nodelay=%u heap=%u heap_before=%u free=%u largest=%u max_log_us=%u max_json_us=%u max_post_us=%u write_us=%u body_write_us=%u read_us=%u event_post_us=%u write_calls=%u read_calls=%u event_posts=%u body_bytes=%u",
  target,upload_diag.count,upload_diag.error,r->status,upload_diag.request_us,r->total_us,r->pre_heap_us,r->perform_us,(unsigned)r->performed,r->post_heap_us,r->diagnostic_us,r->event_us,
  (unsigned)r->connected,r->connect_ms,(unsigned)r->headers_sent,r->headers_ms,(unsigned)r->response_started,r->response_ms,(unsigned)r->body_started,r->body_ms,
  (unsigned)r->finished,r->finish_ms,r->perform_end_ms,(unsigned)r->nodelay,(unsigned)r->heap_measured,(unsigned)r->heap_before_measured,r->free_heap,r->largest_block,upload_diag.max_log_us,upload_diag.max_json_us,upload_diag.max_post_us,
  r->io.write_us,r->io.body_write_us,r->io.read_us,r->io.event_post_us,r->io.write_calls,r->io.read_calls,r->io.event_posts,r->io.body_bytes);
 upload_diag.report_us=(unsigned)(esp_timer_get_time()-began);
}
static QueueHandle_t audio_queue,commands;
static atomic_bool audio_unavailable;
/* Worker-owned recovery: a failed restore must not redefine the idle mode. */
static bool radio_restore_pending;
static wifi_ps_type_t radio_saved_ps;
static unsigned radio_saved_target;
static void message(const char *value){portENTER_CRITICAL(&mux);snprintf(state.message,sizeof(state.message),"%s",value);portEXIT_CRITICAL(&mux);}
void pocket_voice_read(pocket_voice_state *out){portENTER_CRITICAL(&mux);*out=state;portEXIT_CRITICAL(&mux);}
void pocket_voice_release(void){portENTER_CRITICAL(&mux);held=false;portEXIT_CRITICAL(&mux);}
void pocket_voice_cancel(void){portENTER_CRITICAL(&mux);held=false;cancelled=true;portEXIT_CRITICAL(&mux);}
void pocket_voice_set_volume(unsigned volume){portENTER_CRITICAL(&mux);state.volume=volume>100?100:volume;portEXIT_CRITICAL(&mux);}
static void apply_volume(void){portENTER_CRITICAL(&mux);unsigned volume=state.volume;portEXIT_CRITICAL(&mux);bsp_audio_set_volume((uint8_t)volume);}
static bool flags(bool *pressed){portENTER_CRITICAL(&mux);*pressed=held;bool stop=cancelled;portEXIT_CRITICAL(&mux);return stop;}
static bool begin_finish(void){
 /* This is the submission handoff. Cancellation winning this lock discards;
    once handed off, an in-flight finish can have admitted upstream work. */
 portENTER_CRITICAL(&mux);bool send=!cancelled;if(send)phase=VOICE_SUBMISSION_ATTEMPT;portEXIT_CRITICAL(&mux);return send;
}
static void cancellation_message(void){
 portENTER_CRITICAL(&mux);unsigned current=phase;portEXIT_CRITICAL(&mux);
 message(current==VOICE_PRE_FINISH?"Recording discarded":current==VOICE_LOCAL_PLAYBACK?"Playback stopped":"Stopped waiting; task may continue");
}
static void capture_error(unsigned reason,int error){
 portENTER_CRITICAL(&mux);capture_failed=true;capture_diag.reason=reason;capture_diag.error=error;held=false;portEXIT_CRITICAL(&mux);
}
static void capture_message(void){
 portENTER_CRITICAL(&mux);unsigned reason=capture_diag.reason;portEXIT_CRITICAL(&mux);
 message(reason==CAPTURE_READ_ERROR?"Audio input failed; recording discarded":
         reason==CAPTURE_NO_MEMORY || reason==CAPTURE_NO_TASK?"Not enough memory for recording":
         "Connection too slow; recording discarded");
}
static bool capture_radio_retry(void){
 if(!radio_restore_pending)return true;
 esp_err_t error=esp_wifi_set_ps(radio_saved_ps);
 ESP_LOGI("pocket_voice_radio","target=%u stage=2 mode=%u error=%d",radio_saved_target,(unsigned)radio_saved_ps,error);
 if(error==ESP_OK)radio_restore_pending=false;
 return error==ESP_OK;
}
static bool capture_radio_start(unsigned target,wifi_ps_type_t *saved,bool *changed){
 if(!capture_radio_retry())return false;
 esp_err_t error=esp_wifi_get_ps(saved);
 if(error!=ESP_OK){ESP_LOGI("pocket_voice_radio","target=%u stage=0 mode=-1 error=%d",target,error);return false;}
 if(*saved!=WIFI_PS_NONE){error=esp_wifi_set_ps(WIFI_PS_NONE);*changed=error==ESP_OK;}
 ESP_LOGI("pocket_voice_radio","target=%u stage=1 mode=%u error=%d",target,(unsigned)WIFI_PS_NONE,error);
 return error==ESP_OK;
}
static bool capture_radio_restore(unsigned target,wifi_ps_type_t saved,bool *changed){
 if(*changed){radio_restore_pending=true;radio_saved_ps=saved;radio_saved_target=target;}
 bool restored=capture_radio_retry();if(restored)*changed=false;return restored;
}
static void audio_sleep(unsigned target){
 /* Caller has observed recorder completion. BSP retains channel ownership
    while stopping I2S and putting the codec in its verified sleep sequence. */
 esp_err_t error=bsp_audio_sleep();
 ESP_LOGI("pocket_audio_power","target=%u stage=0 error=%d",target,error);
}
void pocket_voice_begin_model(unsigned target,unsigned choice){
 if(target>1 || choice>=POCKET_MODEL_CHOICE_COUNT || !commands || audio_unavailable)return;
 portENTER_CRITICAL(&mux);
 if(state.busy){cancelled=true;held=false;portEXIT_CRITICAL(&mux);return;}
 portEXIT_CRITICAL(&mux);
 /* Admission freezes the exact request. A later menu refresh must not change
    or invalidate a queued turn, and serialization never allocates audio. */
 char begin_body[sizeof(pending_begin_body)]={0};
 bool available=pocket_models_voice_body(target,choice,begin_body,sizeof(begin_body));
 portENTER_CRITICAL(&mux);
 if(state.busy){cancelled=true;held=false;portEXIT_CRITICAL(&mux);return;}
 if(!available){
  held=false;cancelled=false;state.target=target;state.text[0]=0;state.reply[0]=0;
  snprintf(state.message,sizeof(state.message),"Selected model unavailable; open Models");
  portEXIT_CRITICAL(&mux);return;
 }
 if(!pocket_setup_voice_acquire()){
  held=false;snprintf(state.message,sizeof state.message,"Finish Wi-Fi setup before speaking");portEXIT_CRITICAL(&mux);return;
 }
 memcpy(pending_begin_body,begin_body,sizeof(pending_begin_body));
 held=true;cancelled=false;phase=VOICE_PRE_FINISH;pending_model_choice=choice;state.busy=true;state.target=target;state.text[0]=0;state.reply[0]=0;
 portEXIT_CRITICAL(&mux);
 pocket_reader_close(); /* Invalidates an idle GET before the voice worker can use its buffers. */
 if(xQueueSend(commands,&target,0)!=pdTRUE){portENTER_CRITICAL(&mux);state.busy=false;held=false;portEXIT_CRITICAL(&mux);pocket_setup_voice_release();}
}
void pocket_voice_begin(unsigned target){pocket_voice_begin_model(target,POCKET_MODEL_DEFAULT);}
static void record(void *unused){
 (void)unused;capture_frame *frame=malloc(sizeof(*frame));
 if(!frame){
  capture_error(CAPTURE_NO_MEMORY,ESP_ERR_NO_MEM);
  portENTER_CRITICAL(&mux);capture_done=true;state.recording=false;portEXIT_CRITICAL(&mux);
  message("Not enough memory for recording");vTaskDelete(NULL);return;
 }
 size_t total=0;bool pressed=false;int64_t began=esp_timer_get_time();
 for(;;){
  if(flags(&pressed) || !pressed || total>=960000)break;
  frame->size=sizeof(frame->pcm);if(frame->size>960000-total)frame->size=960000-total;
  int64_t before=esp_timer_get_time();esp_err_t error=bsp_audio_read(frame->pcm,frame->size);
  unsigned read_ms=(unsigned)((esp_timer_get_time()-before)/1000);
  portENTER_CRITICAL(&mux);if(read_ms>capture_diag.max_read_ms)capture_diag.max_read_ms=read_ms;portEXIT_CRITICAL(&mux);
  if(error!=ESP_OK){capture_error(CAPTURE_READ_ERROR,error);break;}
  total+=frame->size;
  portENTER_CRITICAL(&mux);capture_diag.bytes=(unsigned)total;portEXIT_CRITICAL(&mux);
  if(xQueueSend(audio_queue,frame,0)!=pdTRUE){
   portENTER_CRITICAL(&mux);capture_diag.queue_peak=CAPTURE_QUEUE_FRAMES;portEXIT_CRITICAL(&mux);
   capture_error(CAPTURE_QUEUE_FULL,ESP_OK);break;
  }
  unsigned queued=(unsigned)uxQueueMessagesWaiting(audio_queue);
  portENTER_CRITICAL(&mux);if(queued>capture_diag.queue_peak)capture_diag.queue_peak=queued;portEXIT_CRITICAL(&mux);
 }
 free(frame);
 portENTER_CRITICAL(&mux);capture_diag.capture_ms=(unsigned)((esp_timer_get_time()-began)/1000);capture_done=true;state.recording=false;held=false;portEXIT_CRITICAL(&mux);
 vTaskDelete(NULL);
}
static bool release_capture(capture_frame **frame,char **body){
 portENTER_CRITICAL(&mux);bool recording=state.recording;portEXIT_CRITICAL(&mux);
 /* A stalled codec still owns its queue; the network scratch is independent. */
 if(!recording && audio_queue){vQueueDelete(audio_queue);audio_queue=NULL;}
 /* Borrowed views of the network scratch; all synchronous HTTP/JSON use ended. */
 if(*frame){memset(*frame,0,sizeof(**frame));*frame=NULL;}
 if(*body){memset(*body,0,CAPTURE_BODY_BYTES);*body=NULL;}
 return !recording;
}
static cJSON *request(pocket_link_config *link,const char *path,const char *body,char *response,size_t size,unsigned stage){
 pocket_http_result result={0};int64_t began=esp_timer_get_time();
 esp_err_t error=pocket_http_json(link->origin,path,link->token,link->ca,body,response,size,&result);
 int64_t after_http=esp_timer_get_time();unsigned duration_us=(unsigned)(after_http-began),duration_ms=duration_us/1000;
 if(stage==VOICE_CHUNK && duration_ms>max_upload_ms)max_upload_ms=duration_ms;
 int64_t log_began=esp_timer_get_time();
 if(error!=ESP_OK || result.status<200 || result.status>=300 || stage==VOICE_BEGIN || stage==VOICE_FINISH){
  portENTER_CRITICAL(&mux);unsigned target=state.target;portEXIT_CRITICAL(&mux);
  ESP_LOGI("pocket_voice_request","target=%u stage=%u error=%d http=%d duration_ms=%u heap=%u free=%u largest=%u",
   target,stage,error,result.status,duration_ms,(unsigned)result.heap_measured,result.free_heap,result.largest_block);
 }
 unsigned log_us=(unsigned)(esp_timer_get_time()-log_began),json_us=0;cJSON *json=NULL;
 if(error!=ESP_OK)message("Connection lost; not resent");
 else if(result.status<200 || result.status>=300){
  if(result.status==401 || result.status==403)message("Pairing / login required");
  else if(result.status==409)message("Previous task needs attention");
  else message("Bridge could not accept request");
 }else {
  int64_t json_began=esp_timer_get_time();json=cJSON_Parse(response);json_us=(unsigned)(esp_timer_get_time()-json_began);
  if(!json)message("Invalid bridge response");
 }
 if(stage==VOICE_CHUNK){
  unsigned post_us=(unsigned)(esp_timer_get_time()-after_http);
  if(!upload_diag.count || duration_us>upload_diag.request_us){upload_diag.request_us=duration_us;upload_diag.http=result;upload_diag.error=error;}
  upload_diag.count++;
  if(log_us>upload_diag.max_log_us)upload_diag.max_log_us=log_us;
  if(json_us>upload_diag.max_json_us)upload_diag.max_json_us=json_us;
  if(post_us>upload_diag.max_post_us)upload_diag.max_post_us=post_us;
 }
 return json;
}
static const char *string(cJSON *json,const char *key){cJSON *value=cJSON_GetObjectItemCaseSensitive(json,key);return cJSON_IsString(value)?value->valuestring:"";}
static bool post_empty(pocket_link_config *link,const char *path,char *response,unsigned stage){cJSON *j=request(link,path,"{}",response,8192,stage);bool ok=j!=NULL;cJSON_Delete(j);return ok;}
static bool wait_task(pocket_link_config *link,const char *target,const char *id,char *response,char *task){
 char path[193];int64_t deadline=esp_timer_get_time()+120000000;
 snprintf(path,sizeof(path),"/targets/%s/audio/%s",target,id);
 while(esp_timer_get_time()<deadline){
  bool pressed;if(flags(&pressed)){message("Stopped waiting; task may continue");return false;}
  cJSON *j=request(link,path,NULL,response,8192,VOICE_STT);if(!j)return false;
  const char *status=string(j,"state"),*rid=string(j,"task_id");
  if(!strcmp(status,"submitted") && strlen(rid)>0 && strlen(rid)<65){
   snprintf(task,65,"%s",rid);portENTER_CRITICAL(&mux);pocket_utf8_copy(state.text,sizeof(state.text),string(j,"text"),sizeof(state.text)-1);portEXIT_CRITICAL(&mux);cJSON_Delete(j);return true;
  }
  bool failure=!strcmp(status,"unknown") || !strcmp(status,"failed");cJSON_Delete(j);
  if(failure){message("Voice request failed; check Hermes");return false;}
  vTaskDelay(pdMS_TO_TICKS(600));
 }
 message("Still processing; check Hermes");return false;
}
static bool wait_reply(pocket_link_config *link,const char *target,const char *task,char *response){
 char path[193];snprintf(path,sizeof(path),"/targets/%s/tasks/%s/view",target,task);
 int64_t deadline=esp_timer_get_time()+180000000;
 while(esp_timer_get_time()<deadline){
  bool pressed;if(flags(&pressed)){message("Stopped waiting; task may continue");return false;}
  cJSON *j=request(link,path,NULL,response,8192,VOICE_REPLY);if(!j)return false;
  const char *status=string(j,"state");
  if(!strcmp(status,"completed")){
   portENTER_CRITICAL(&mux);pocket_utf8_copy(state.reply,sizeof(state.reply),string(j,"result"),sizeof(state.reply)-1);portEXIT_CRITICAL(&mux);cJSON_Delete(j);return true;
  }
  bool approval=!strcmp(status,"awaiting_approval"),unknown=!strcmp(status,"unknown"),ended=!strcmp(status,"failed") || !strcmp(status,"cancelled");
  cJSON_Delete(j);
  if(approval || unknown || ended){message(approval?"Open Hermes to approve":unknown?"Outcome uncertain; check Hermes":"Task ended without a reply");return false;}
  message("Hermes is working...");vTaskDelay(pdMS_TO_TICKS(800));
 }
 message("Task continues in Hermes");return false;
}
static bool speech_cancelled(void *unused){(void)unused;bool pressed;return flags(&pressed);}
static void speech_message(void *unused,const char *text){(void)unused;message(text);}
static bool speech_start(void *context){
 const unsigned target=*(unsigned *)context;if(speech_cancelled(NULL))return false;
 esp_err_t error=bsp_audio_wake();ESP_LOGI("pocket_audio_power","target=%u stage=1 error=%d",target,error);
 if(error!=ESP_OK)return false;
 if(speech_cancelled(NULL)){bsp_audio_set_volume(0);audio_sleep(target);return false;}
 portENTER_CRITICAL(&mux);state.playing=true;portEXIT_CRITICAL(&mux);message("Speaking (OK to stop)");return true;
}
static bool speech_write(void *unused,const void *pcm,size_t bytes){
 (void)unused;if(speech_cancelled(NULL))return false;apply_volume();
 return !speech_cancelled(NULL) && bsp_audio_write(pcm,bytes)==ESP_OK;
}
static bool speech_drain(void *unused){
 (void)unused;return bsp_audio_drain_cancel(250,speech_cancelled,NULL)==ESP_OK && !speech_cancelled(NULL);
}
static void speech_stop(void *context){
 const unsigned target=*(unsigned *)context;
 /* Stop hardware before a best-effort remote cancellation can block. The
    requested UI volume stays in state and is applied before every next write. */
 bsp_audio_set_volume(0);audio_sleep(target);
 portENTER_CRITICAL(&mux);state.playing=false;portEXIT_CRITICAL(&mux);
}
static void play(pocket_link_config *link,const char *target,const char *task,const char *request_id,bool segmented,char *response,audio_frame *frame){
 portENTER_CRITICAL(&mux);phase=VOICE_LOCAL_PLAYBACK;portEXIT_CRITICAL(&mux);
 bool pressed;if(flags(&pressed)){cancellation_message();return;}
 if(segmented){
  portENTER_CRITICAL(&mux);unsigned audio_target=state.target;portEXIT_CRITICAL(&mux);
  const pocket_speech_io io={.context=&audio_target,.cancelled=speech_cancelled,.start=speech_start,.write=speech_write,.drain=speech_drain,.stop=speech_stop,.message=speech_message};
  (void)pocket_speech_v2_play(link,audio_target,task,request_id,response,8192,frame->pcm,sizeof frame->pcm,&io);return;
 }
 char path[193];snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech",target,task);
 if(!post_empty(link,path,response,VOICE_SPEECH))return;
 int chunks=0;int64_t deadline=esp_timer_get_time()+120000000;
 while(esp_timer_get_time()<deadline){
  bool pressed;if(flags(&pressed))return;
  cJSON *j=request(link,path,NULL,response,8192,VOICE_SPEECH);if(!j)return;
  const char *status=string(j,"state");cJSON *count=cJSON_GetObjectItemCaseSensitive(j,"chunks");
  if(!strcmp(status,"ready") && cJSON_IsNumber(count) && count->valuedouble==count->valueint && count->valueint>0 && count->valueint<=235)chunks=count->valueint;
  bool failed=!strcmp(status,"unavailable");cJSON_Delete(j);
  if(chunks)break;
  if(failed){message("Text ready; speech unavailable");return;}vTaskDelay(pdMS_TO_TICKS(600));
 }
 if(!chunks){message("Text ready; speech timed out");return;}
 esp_err_t audio_error=bsp_audio_wake();
 portENTER_CRITICAL(&mux);unsigned audio_target=state.target;portEXIT_CRITICAL(&mux);
 ESP_LOGI("pocket_audio_power","target=%u stage=1 error=%d",audio_target,audio_error);
 if(audio_error!=ESP_OK){message("Text ready; audio unavailable");return;}
 portENTER_CRITICAL(&mux);state.playing=true;portEXIT_CRITICAL(&mux);message("Speaking (OK to stop)");
 bool complete=true;
 for(int i=0;i<chunks;i++){
  bool pressed;if(flags(&pressed)){complete=false;message("Playback stopped");break;}
  snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech/chunks/%d",target,task,i);
  cJSON *j=request(link,path,NULL,response,8192,VOICE_PLAYBACK);if(!j){complete=false;break;}
  const char *data=string(j,"data");size_t bytes=0;
  int error=mbedtls_base64_decode(frame->pcm,sizeof(frame->pcm),&bytes,(const unsigned char*)data,strlen(data));cJSON_Delete(j);
  if(error || !bytes || bytes%2 || flags(&pressed)){complete=false;message("Text ready; playback stopped");break;}
  apply_volume();
  if(flags(&pressed) || bsp_audio_write(frame->pcm,bytes)!=ESP_OK){complete=false;message("Text ready; playback stopped");break;}
 }
 /* Download completion only enqueued the tail. Keep playing/awake through
    actual TX progress, with cancellation checked every RTOS wait tick. */
 if(complete && !speech_drain(NULL)){complete=false;message("Text ready; playback stopped");}
 if(flags(&pressed))complete=false;
 speech_stop(&audio_target);
 if(complete)message("Reply ready");
}
static void network(void *unused){
 (void)unused;
 pocket_link_config *link=malloc(sizeof(*link));char *response=malloc(8192);
 capture_frame *frame=NULL;char *body=NULL;audio_frame *playback=NULL;
 if(!link || !response){audio_unavailable=true;message("Not enough memory for connection");free(link);free(response);pocket_setup_voice_release();vTaskDelete(NULL);return;}
 unsigned target,poll_target=0,last_poll_ms=0;
 for(;;){
  portENTER_CRITICAL(&mux);bool active_voice=state.busy;portEXIT_CRITICAL(&mux);
  const unsigned poll_ms=active_voice?POCKET_IDLE_POLL_MS:pocket_actions_poll_ms();
  if(poll_ms!=last_poll_ms){ESP_LOGI("pocket_poll","delay_ms=%u",poll_ms);last_poll_ms=poll_ms;}
  if(xQueueReceive(commands,&target,pdMS_TO_TICKS(poll_ms))!=pdTRUE){
   if(!pocket_setup_poll_acquire())continue;
   /* Retry only restoration of our own prior mode, once per idle cycle. */
   capture_radio_retry();
   poll_target=pocket_actions_poll_target(poll_target);
   if(!pocket_actions_poll_due(poll_target)){
    pocket_setup_poll_release();poll_target=1-poll_target;continue;
   }
   pocket_setup_state current;pocket_setup_read(&current);bool online=false,ready=false,reachable=false;int http_status=0;
   pocket_diagnostics observed={.checked=true,.wifi=current.wifi_connected,.utc_ready=time(NULL)>=1704067200};
   if(observed.wifi && observed.utc_ready && !current.active)observed.configured=pocket_setup_link(poll_target,link);
   if(observed.configured){
    observed.attempted=true;
    observed.custom_ca=link->ca[0]!=0;
    char route[64];snprintf(route,sizeof(route),"/targets/%s/status",poll_target?"linux":"windows");pocket_http_result result={0};
    esp_err_t error=pocket_http_json(link->origin,route,link->token,link->ca,NULL,response,8192,&result);http_status=result.status;
    observed.transport_error=error;observed.http_status=result.status;observed.tls_error=result.tls_error;
    observed.tls_code=result.tls_code;observed.verify_flags=result.verify_flags;observed.free_heap=result.free_heap;observed.largest_block=result.largest_block;observed.heap_measured=result.heap_measured;
    if(error==ESP_OK && http_status==200){
     cJSON *j=cJSON_Parse(response);const char *backend=string(j,"backend");
     reachable=!strcmp(backend,"available") || !strcmp(backend,"unavailable");online=reachable && !strcmp(backend,"available");
     cJSON *v=cJSON_GetObjectItemCaseSensitive(j,"voice"),*stt=cJSON_GetObjectItemCaseSensitive(v,"stt"),*tts=cJSON_GetObjectItemCaseSensitive(v,"tts");
     observed.stt_known=reachable && cJSON_IsBool(stt);observed.tts_known=reachable && cJSON_IsBool(tts);
     ready=observed.stt_known && cJSON_IsTrue(stt);observed.tts=observed.tts_known && cJSON_IsTrue(tts);
     const bool text_pages=reachable && pocket_delivery_text_pages(j),speech_segments=reachable && pocket_delivery_speech_segments(j);
     const unsigned caps=(unsigned)text_pages|((unsigned)speech_segments<<1);
     if(!delivery_seen[poll_target] || delivery_caps[poll_target]!=caps){
      ESP_LOGI("pocket_delivery","target=%u text_pages=%u speech_segments=%u",poll_target,(unsigned)text_pages,(unsigned)speech_segments);
      delivery_seen[poll_target]=true;delivery_caps[poll_target]=caps;
     }
     pocket_reader_available(poll_target,text_pages);cJSON_Delete(j);
    }
   }
   observed.response_valid=reachable;observed.backend_available=online;observed.stt=ready;observed.checked_ms=esp_timer_get_time()/1000;
   portENTER_CRITICAL(&mux);bool changed=state.online[poll_target]!=online || state.speech_ready[poll_target]!=ready;bool voice_pending=state.busy;state.online[poll_target]=online;state.speech_ready[poll_target]=ready;state.diagnostics[poll_target]=observed;portEXIT_CRITICAL(&mux);
   if(changed)ESP_LOGI("pocket_status","target=%u http=%d backend_available=%u stt=%u",poll_target,http_status,(unsigned)online,(unsigned)ready);
   /* Reuse this worker/client/buffer after deleting status JSON; queued voice
      takes priority. No Usage requests run during capture, waiting or playback. */
   if(reachable && !voice_pending && pocket_setup_poll_continue()){
    if(pocket_actions_usage_due(poll_target))pocket_usage_poll(poll_target,link,response,8192);
    /* A held talk gesture may arrive during the Usage GET. Give its queued
       command priority before starting another read-only request. */
    portENTER_CRITICAL(&mux);voice_pending=state.busy;portEXIT_CRITICAL(&mux);
    if(!voice_pending && pocket_setup_poll_continue())pocket_actions_idle(poll_target,link,response,8192);
   }else if(!reachable){pocket_usage_disconnected(poll_target);pocket_actions_disconnected(poll_target);}
   /* Idle polling outlives the bridge's keep-alive budget. Release this
      connection before waiting; active audio/task requests keep their reuse. */
   pocket_http_close();
   pocket_setup_poll_release();
   memset(link,0,sizeof(*link));poll_target=1-poll_target;continue;
  }bool started=false,pressed=false,restore_radio=false;wifi_ps_type_t saved_ps=WIFI_PS_NONE;unsigned sequence=0;char id[33],path[193],task[65]={0},begin_body[96];
  portENTER_CRITICAL(&mux);unsigned choice=pending_model_choice;memcpy(begin_body,pending_begin_body,sizeof(begin_body));portEXIT_CRITICAL(&mux);
  portENTER_CRITICAL(&mux);capture_diag=(capture_diagnostics){0};capture_failed=false;capture_done=false;portEXIT_CRITICAL(&mux);max_upload_ms=0;upload_diag=(upload_diagnostics){0};
  const char *name=target?"linux":"windows";uint32_t random[4];esp_fill_random(random,sizeof(random));
  snprintf(id,sizeof(id),"%08lx%08lx%08lx%08lx",(unsigned long)random[0],(unsigned long)random[1],(unsigned long)random[2],(unsigned long)random[3]);
  message("Connecting...");
  pocket_setup_state setup;pocket_setup_read(&setup);
  if(!setup.wifi_connected){message("Wi-Fi offline; hold OK on Status");goto finished;}
  if(time(NULL)<1704067200){message("Waiting for secure network time");goto finished;}
  if(!pocket_setup_link(target,link)){message("Configure bridge and device token");goto finished;}
  snprintf(path,sizeof(path),"/targets/%s/status",name);
  cJSON *status=request(link,path,NULL,response,8192,VOICE_STATUS);if(!status)goto finished;
  cJSON *voice=cJSON_GetObjectItemCaseSensitive(status,"voice");bool stt=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(voice,"stt"));
  const bool segmented=pocket_delivery_speech_segments(status);
  portENTER_CRITICAL(&mux);state.online[target]=!strcmp(string(status,"backend"),"available");state.speech_ready[target]=stt;portEXIT_CRITICAL(&mux);cJSON_Delete(status);
  if(!stt){message("Speech recognition is not enabled");goto finished;}
  if(flags(&pressed) || !pressed){message("Hold OK to speak");goto finished;}
  if(choice>=POCKET_MODEL_CHOICE_COUNT || !begin_body[0]){message("Selected model unavailable; open Models");goto finished;}
  /* Only capture/upload needs an awake radio. Keep the user's idle power mode. */
  if(!capture_radio_start(target,&saved_ps,&restore_radio)){message("Wi-Fi not ready for recording");goto finished;}
  /* Keep inactive audio out of the heap needed by certificate/signature checks. */
  audio_queue=xQueueCreate(CAPTURE_QUEUE_FRAMES,sizeof(capture_frame));
  /* malloc-aligned frame is consumed by encoding before ACK output overwrites it.
     POST lives above that output: the HTTP helper clears output before sending. */
  frame=(capture_frame*)response;body=response+CAPTURE_ACK_BYTES;
  if(!audio_queue){message("Not enough memory for voice");goto finished;}
  if(bsp_audio_init()!=ESP_OK || bsp_audio_wake()!=ESP_OK || bsp_audio_set_format(16000,16,1)!=ESP_OK){message("Audio hardware unavailable");goto finished;}
  apply_volume();
  snprintf(path,sizeof(path),"/targets/%s/audio/%s",name,id);
  cJSON *begin=request(link,path,begin_body,response,8192,VOICE_BEGIN);if(!begin)goto finished;cJSON_Delete(begin);started=true;
  if(flags(&pressed) || !pressed){message("Hold OK to speak");goto abort_capture;}
  xQueueReset(audio_queue);
  portENTER_CRITICAL(&mux);capture_done=false;capture_failed=false;state.recording=true;portEXIT_CRITICAL(&mux);
  message("Listening... release OK to send");
  if(xTaskCreate(record,"pocket_record",6144,NULL,5,NULL)!=pdPASS){
   capture_error(CAPTURE_NO_TASK,ESP_ERR_NO_MEM);
   portENTER_CRITICAL(&mux);capture_done=true;state.recording=false;portEXIT_CRITICAL(&mux);
   message("Not enough memory for recording");goto abort_capture;
  }
  bool failed=false;
  for(;;){
   if(flags(&pressed)){failed=true;break;}
   if(xQueueReceive(audio_queue,frame,pdMS_TO_TICKS(100))==pdTRUE){
    size_t length=0;int prefix=snprintf(body,CAPTURE_BODY_BYTES,"{\"sequence\":%u,\"data\":\"",sequence);
    if(prefix<0 || (size_t)prefix>CAPTURE_BODY_BYTES-3 ||
     mbedtls_base64_encode((unsigned char*)body+prefix,CAPTURE_BODY_BYTES-(size_t)prefix-2,&length,frame->pcm,frame->size)!=0){failed=true;break;}
    memcpy(body+(size_t)prefix+length,"\"}",3);
    snprintf(path,sizeof(path),"/targets/%s/audio/%s/chunk",name,id);
    cJSON *ack=request(link,path,body,response,CAPTURE_ACK_BYTES,VOICE_CHUNK);if(!ack){failed=true;break;}cJSON_Delete(ack);sequence++;
   }
   portENTER_CRITICAL(&mux);bool done=capture_done,bad=capture_failed;portEXIT_CRITICAL(&mux);
   if(bad){capture_message();failed=true;break;}
   if(done && uxQueueMessagesWaiting(audio_queue)==0)break;
  }
  pocket_voice_release();
  if(failed || !sequence)goto abort_capture;
  /* The recorder is done and every queued frame was uploaded. Free its queue
     and clear borrowed views before finish/STT/task/speech reuse the full scratch. */
  if(!release_capture(&frame,&body)){message("Audio stalled; restart device");goto abort_capture;}
  audio_sleep(target); /* No microphone/I2S clocks while waiting for providers. */
  if(!capture_radio_restore(target,saved_ps,&restore_radio)){message("Wi-Fi restore failed; recording discarded");goto abort_capture;}
  if(!begin_finish())goto abort_capture;
  snprintf(path,sizeof(path),"/targets/%s/audio/%s/finish",name,id);
  if(!post_empty(link,path,response,VOICE_FINISH))goto finished;
  message("Transcribing...");
  if(wait_task(link,name,id,response,task) && wait_reply(link,name,task,response)){
   playback=malloc(sizeof(*playback));
   if(playback)play(link,name,task,id,segmented,response,playback);else message("Text ready; not enough memory for playback");
  }
  goto finished;
 abort_capture:
  pocket_voice_release();
  if(started){snprintf(path,sizeof(path),"/targets/%s/audio/%s/abort",name,id);post_empty(link,path,response,VOICE_ABORT);}
  portENTER_CRITICAL(&mux);bool bad=capture_failed;portEXIT_CRITICAL(&mux);
  if(bad)capture_message();
 finished:
  pocket_voice_release();
  capture_radio_restore(target,saved_ps,&restore_radio);
  /* Let the bounded codec read finish before any subsequent capture/playback. */
  for(int i=0;i<20;i++){portENTER_CRITICAL(&mux);bool recording=state.recording;portEXIT_CRITICAL(&mux);if(!recording)break;vTaskDelay(pdMS_TO_TICKS(100));}
  if(started){
   upload_report(target);
   portENTER_CRITICAL(&mux);capture_diagnostics diag=capture_diag;portEXIT_CRITICAL(&mux);
   /* Aggregate numeric metadata only. No PCM, endpoints, task IDs or text. */
   ESP_LOGI("pocket_capture","target=%u reason=%u error=%d bytes=%u capture_ms=%u max_read_ms=%u queue_peak=%u chunks=%u max_upload_ms=%u free=%u largest=%u upload_log_us=%u",
    target,diag.reason,diag.error,diag.bytes,diag.capture_ms,diag.max_read_ms,diag.queue_peak,sequence,max_upload_ms,
    (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),upload_diag.report_us);
  }
  bool fault=!release_capture(&frame,&body);
  if(!fault)audio_sleep(target); /* Includes cancellation, failures and playback. */
  if(playback){memset(playback,0,sizeof(*playback));free(playback);playback=NULL;}
  memset(link,0,sizeof(*link));
  if(fault)message("Audio stalled; restart device");
  else {portENTER_CRITICAL(&mux);bool stopped=cancelled && !capture_failed;portEXIT_CRITICAL(&mux);if(stopped)cancellation_message();}
  pocket_http_close();
  if(!fault)pocket_setup_voice_release();
  portENTER_CRITICAL(&mux);state.busy=fault;state.playing=false;portEXIT_CRITICAL(&mux);
 }
}
static void clock_synchronized(struct timeval *synchronized){
 if(!synchronized)return;
 const bool was_ready=pocket_clock_ready();pocket_clock_note_sync((int64_t)synchronized->tv_sec);
 if(!was_ready && pocket_clock_ready())ESP_LOGI("pocket_clock","ready=1 offset_minutes=480");
}
esp_err_t pocket_voice_init(void){
 commands=xQueueCreate(1,sizeof(unsigned));
 if(!commands)return ESP_ERR_NO_MEM;
 esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);esp_sntp_setservername(0,"pool.ntp.org");esp_sntp_set_time_sync_notification_cb(clock_synchronized);esp_sntp_init();
 if(xTaskCreate(network,"pocket_voice",12288,NULL,4,NULL)!=pdPASS){vQueueDelete(commands);commands=NULL;return ESP_ERR_NO_MEM;}
 message("Hold OK to talk");return ESP_OK;
}
