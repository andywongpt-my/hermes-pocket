/* MIT. Bounded half-duplex PCM capture/transport/playback, never on UI thread. */
#include "pocket_voice.h"
#include "pocket_setup.h"
#include "pocket_http.h"
#include "pocket_usage_client.h"
#include "pocket_actions.h"
#include "bsp_audio.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_sntp.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
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
enum { CAPTURE_PCM_BYTES=2048, CAPTURE_QUEUE_FRAMES=4, CAPTURE_BODY_BYTES=2816 };
typedef struct { size_t size; unsigned char pcm[CAPTURE_PCM_BYTES]; } capture_frame;
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static pocket_voice_state state={.volume=60};
static bool held,cancelled,capture_done,capture_failed;
static enum { VOICE_PRE_FINISH,VOICE_SUBMISSION_ATTEMPT,VOICE_LOCAL_PLAYBACK } phase;
enum { CAPTURE_READ_ERROR=1, CAPTURE_QUEUE_FULL=2, CAPTURE_NO_MEMORY=3, CAPTURE_NO_TASK=4 };
enum { VOICE_STATUS=1, VOICE_BEGIN=2, VOICE_CHUNK=3, VOICE_FINISH=4, VOICE_STT=5, VOICE_REPLY=6, VOICE_SPEECH=7, VOICE_PLAYBACK=8, VOICE_ABORT=9 };
typedef struct { unsigned reason,bytes,queue_peak,capture_ms,max_read_ms;int error; } capture_diagnostics;
static capture_diagnostics capture_diag;
static unsigned max_upload_ms;
static QueueHandle_t audio_queue,commands;
static atomic_bool audio_unavailable;
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
void pocket_voice_begin(unsigned target){
 if(target>1 || !commands || audio_unavailable)return;
 portENTER_CRITICAL(&mux);
 if(state.busy){cancelled=true;held=false;portEXIT_CRITICAL(&mux);return;}
 held=true;cancelled=false;phase=VOICE_PRE_FINISH;state.busy=true;state.target=target;state.text[0]=0;state.reply[0]=0;
 portEXIT_CRITICAL(&mux);
 if(xQueueSend(commands,&target,0)!=pdTRUE){portENTER_CRITICAL(&mux);state.busy=false;held=false;portEXIT_CRITICAL(&mux);}
}
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
 if(*frame){memset(*frame,0,sizeof(**frame));free(*frame);*frame=NULL;}
 if(*body){memset(*body,0,CAPTURE_BODY_BYTES);free(*body);*body=NULL;}
 return !recording;
}
static cJSON *request(pocket_link_config *link,const char *path,const char *body,char *response,size_t size,unsigned stage){
 pocket_http_result result={0};int64_t began=esp_timer_get_time();
 esp_err_t error=pocket_http_json(link->origin,path,link->token,link->ca,body,response,size,&result);
 unsigned duration_ms=(unsigned)((esp_timer_get_time()-began)/1000);
 if(stage==VOICE_CHUNK && duration_ms>max_upload_ms)max_upload_ms=duration_ms;
 if(error!=ESP_OK || result.status<200 || result.status>=300 || stage==VOICE_BEGIN || stage==VOICE_FINISH){
  portENTER_CRITICAL(&mux);unsigned target=state.target;portEXIT_CRITICAL(&mux);
  ESP_LOGI("pocket_voice_request","target=%u stage=%u error=%d http=%d duration_ms=%u free=%u largest=%u",
   target,stage,error,result.status,duration_ms,(unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
 }
 if(error!=ESP_OK){message("Connection lost; not resent");return NULL;}
 if(result.status<200 || result.status>=300){
  if(result.status==401 || result.status==403)message("Pairing / login required");
  else if(result.status==409)message("Previous task needs attention");
  else message("Bridge could not accept request");
  return NULL;
 }
 cJSON *json=cJSON_Parse(response);if(!json)message("Invalid bridge response");return json;
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
   snprintf(task,65,"%s",rid);portENTER_CRITICAL(&mux);snprintf(state.text,sizeof(state.text),"%s",string(j,"text"));portEXIT_CRITICAL(&mux);cJSON_Delete(j);return true;
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
   portENTER_CRITICAL(&mux);snprintf(state.reply,sizeof(state.reply),"%s",string(j,"result"));portEXIT_CRITICAL(&mux);cJSON_Delete(j);return true;
  }
  bool approval=!strcmp(status,"awaiting_approval"),unknown=!strcmp(status,"unknown"),ended=!strcmp(status,"failed") || !strcmp(status,"cancelled");
  cJSON_Delete(j);
  if(approval || unknown || ended){message(approval?"Open Hermes to approve":unknown?"Outcome uncertain; check Hermes":"Task ended without a reply");return false;}
  message("Hermes is working...");vTaskDelay(pdMS_TO_TICKS(800));
 }
 message("Task continues in Hermes");return false;
}
static void play(pocket_link_config *link,const char *target,const char *task,char *response,audio_frame *frame){
 portENTER_CRITICAL(&mux);phase=VOICE_LOCAL_PLAYBACK;portEXIT_CRITICAL(&mux);
 bool pressed;if(flags(&pressed)){cancellation_message();return;}
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
 portENTER_CRITICAL(&mux);state.playing=false;portEXIT_CRITICAL(&mux);
 if(complete)message("Reply ready");
}
static void network(void *unused){
 (void)unused;
 pocket_link_config *link=malloc(sizeof(*link));char *response=malloc(8192);
 capture_frame *frame=NULL;char *body=NULL;audio_frame *playback=NULL;
 if(!link || !response){audio_unavailable=true;message("Not enough memory for connection");free(link);free(response);vTaskDelete(NULL);return;}
 unsigned target,poll_target=0;
 for(;;){
  if(xQueueReceive(commands,&target,pdMS_TO_TICKS(5000))!=pdTRUE){
   poll_target=pocket_actions_poll_target(poll_target);
   pocket_setup_state current;pocket_setup_read(&current);bool online=false,ready=false,reachable=false;int http_status=0;
   pocket_diagnostics observed={.checked=true,.wifi=current.wifi_connected,.utc_ready=time(NULL)>=1704067200};
   if(observed.wifi && observed.utc_ready)observed.configured=pocket_setup_link(poll_target,link);
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
     ready=observed.stt_known && cJSON_IsTrue(stt);observed.tts=observed.tts_known && cJSON_IsTrue(tts);cJSON_Delete(j);
    }
   }
   observed.response_valid=reachable;observed.backend_available=online;observed.stt=ready;observed.checked_ms=esp_timer_get_time()/1000;
   portENTER_CRITICAL(&mux);bool changed=state.online[poll_target]!=online || state.speech_ready[poll_target]!=ready;bool voice_pending=state.busy;state.online[poll_target]=online;state.speech_ready[poll_target]=ready;state.diagnostics[poll_target]=observed;portEXIT_CRITICAL(&mux);
   if(changed)ESP_LOGI("pocket_status","target=%u http=%d backend_available=%u stt=%u",poll_target,http_status,(unsigned)online,(unsigned)ready);
   /* Reuse this worker/client/buffer after deleting status JSON; queued voice
      takes priority. No Usage requests run during capture, waiting or playback. */
   if(reachable && !voice_pending){
    pocket_usage_poll(poll_target,link,response,8192);
    /* A held talk gesture may arrive during the Usage GET. Give its queued
       command priority before starting another read-only request. */
    portENTER_CRITICAL(&mux);voice_pending=state.busy;portEXIT_CRITICAL(&mux);
    if(!voice_pending)pocket_actions_idle(poll_target,link,response,8192);
   }else if(!reachable){pocket_usage_disconnected(poll_target);pocket_actions_disconnected(poll_target);}
   /* Idle polling outlives the bridge's keep-alive budget. Release this
      connection before waiting; active audio/task requests keep their reuse. */
   pocket_http_close();
   memset(link,0,sizeof(*link));poll_target=1-poll_target;continue;
  }bool started=false,pressed=false;unsigned sequence=0;char id[33],path[193],task[65]={0};
  portENTER_CRITICAL(&mux);capture_diag=(capture_diagnostics){0};capture_failed=false;capture_done=false;portEXIT_CRITICAL(&mux);max_upload_ms=0;
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
  portENTER_CRITICAL(&mux);state.online[target]=!strcmp(string(status,"backend"),"available");state.speech_ready[target]=stt;portEXIT_CRITICAL(&mux);cJSON_Delete(status);
  if(!stt){message("Speech recognition is not enabled");goto finished;}
  if(flags(&pressed) || !pressed){message("Hold OK to speak");goto finished;}
  /* Keep inactive audio out of the heap needed by certificate/signature checks. */
  audio_queue=xQueueCreate(CAPTURE_QUEUE_FRAMES,sizeof(capture_frame));frame=malloc(sizeof(*frame));body=malloc(CAPTURE_BODY_BYTES);
  if(!audio_queue || !frame || !body){message("Not enough memory for voice");goto finished;}
  if(bsp_audio_init()!=ESP_OK || bsp_audio_set_format(16000,16,1)!=ESP_OK){message("Audio hardware unavailable");goto finished;}
  apply_volume();
  snprintf(path,sizeof(path),"/targets/%s/audio/%s",name,id);
  cJSON *begin=request(link,path,"{\"chat_id\":\"pocket\"}",response,8192,VOICE_BEGIN);if(!begin)goto finished;cJSON_Delete(begin);started=true;
  if(flags(&pressed) || !pressed)goto abort_capture;
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
    cJSON *ack=request(link,path,body,response,8192,VOICE_CHUNK);if(!ack){failed=true;break;}cJSON_Delete(ack);sequence++;
   }
   portENTER_CRITICAL(&mux);bool done=capture_done,bad=capture_failed;portEXIT_CRITICAL(&mux);
   if(bad){capture_message();failed=true;break;}
   if(done && uxQueueMessagesWaiting(audio_queue)==0)break;
  }
  pocket_voice_release();
  if(failed || !sequence)goto abort_capture;
  /* The recorder is done and every queued frame was uploaded. Reclaim capture
     memory before finish/STT/task/speech requests can need another TLS handshake. */
  if(!release_capture(&frame,&body)){message("Audio stalled; restart device");goto abort_capture;}
  if(!begin_finish())goto abort_capture;
  snprintf(path,sizeof(path),"/targets/%s/audio/%s/finish",name,id);
  if(!post_empty(link,path,response,VOICE_FINISH))goto finished;
  message("Transcribing...");
  if(wait_task(link,name,id,response,task) && wait_reply(link,name,task,response)){
   playback=malloc(sizeof(*playback));
   if(playback)play(link,name,task,response,playback);else message("Text ready; not enough memory for playback");
  }
  goto finished;
 abort_capture:
  pocket_voice_release();
  if(started){snprintf(path,sizeof(path),"/targets/%s/audio/%s/abort",name,id);post_empty(link,path,response,VOICE_ABORT);}
  portENTER_CRITICAL(&mux);bool bad=capture_failed;portEXIT_CRITICAL(&mux);
  if(bad)capture_message();
 finished:
  pocket_voice_release();
  /* Let the bounded codec read finish before any subsequent capture/playback. */
  for(int i=0;i<20;i++){portENTER_CRITICAL(&mux);bool recording=state.recording;portEXIT_CRITICAL(&mux);if(!recording)break;vTaskDelay(pdMS_TO_TICKS(100));}
  if(started){
   portENTER_CRITICAL(&mux);capture_diagnostics diag=capture_diag;portEXIT_CRITICAL(&mux);
   /* Aggregate numeric metadata only. No PCM, endpoints, task IDs or text. */
   ESP_LOGI("pocket_capture","target=%u reason=%u error=%d bytes=%u capture_ms=%u max_read_ms=%u queue_peak=%u chunks=%u max_upload_ms=%u free=%u largest=%u",
    target,diag.reason,diag.error,diag.bytes,diag.capture_ms,diag.max_read_ms,diag.queue_peak,sequence,max_upload_ms,
    (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
  }
  bool fault=!release_capture(&frame,&body);
  portENTER_CRITICAL(&mux);state.busy=fault;state.playing=false;portEXIT_CRITICAL(&mux);
  if(playback){memset(playback,0,sizeof(*playback));free(playback);playback=NULL;}
  memset(link,0,sizeof(*link));
  if(fault)message("Audio stalled; restart device");
  else {portENTER_CRITICAL(&mux);bool stopped=cancelled && !capture_failed;portEXIT_CRITICAL(&mux);if(stopped)cancellation_message();}
  pocket_http_close();
 }
}
esp_err_t pocket_voice_init(void){
 commands=xQueueCreate(1,sizeof(unsigned));
 if(!commands)return ESP_ERR_NO_MEM;
 esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);esp_sntp_setservername(0,"pool.ntp.org");esp_sntp_init();
 if(xTaskCreate(network,"pocket_voice",12288,NULL,4,NULL)!=pdPASS){vQueueDelete(commands);commands=NULL;return ESP_ERR_NO_MEM;}
 message("Hold OK to talk");return ESP_OK;
}
