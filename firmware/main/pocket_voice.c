/* MIT. Bounded half-duplex PCM capture/transport/playback, never on UI thread. */
#include "pocket_voice.h"
#include "pocket_setup.h"
#include "pocket_http.h"
#include "bsp_audio.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_sntp.h"
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
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static pocket_voice_state state;
static bool held,cancelled,capture_done,capture_failed;
static QueueHandle_t audio_queue,commands;
static TaskHandle_t recorder;
static atomic_bool audio_unavailable;
static void message(const char *value){portENTER_CRITICAL(&mux);snprintf(state.message,sizeof(state.message),"%s",value);portEXIT_CRITICAL(&mux);}
void pocket_voice_read(pocket_voice_state *out){portENTER_CRITICAL(&mux);*out=state;portEXIT_CRITICAL(&mux);}
void pocket_voice_release(void){portENTER_CRITICAL(&mux);held=false;portEXIT_CRITICAL(&mux);}
void pocket_voice_cancel(void){portENTER_CRITICAL(&mux);held=false;cancelled=true;portEXIT_CRITICAL(&mux);}
static bool flags(bool *pressed){portENTER_CRITICAL(&mux);*pressed=held;bool stop=cancelled;portEXIT_CRITICAL(&mux);return stop;}
void pocket_voice_begin(unsigned target){
 if(target>1 || !commands || audio_unavailable)return;
 portENTER_CRITICAL(&mux);
 if(state.busy){cancelled=true;held=false;portEXIT_CRITICAL(&mux);return;}
 held=true;cancelled=false;state.busy=true;state.target=target;state.text[0]=0;state.reply[0]=0;
 portEXIT_CRITICAL(&mux);
 if(xQueueSend(commands,&target,0)!=pdTRUE){portENTER_CRITICAL(&mux);state.busy=false;held=false;portEXIT_CRITICAL(&mux);}
}
static void record(void *unused){
 (void)unused;audio_frame *frame=malloc(sizeof(*frame));
 if(!frame){audio_unavailable=true;message("Audio allocation failed; restart");vTaskDelete(NULL);return;}
 for(;;){
  ulTaskNotifyTake(pdTRUE,portMAX_DELAY);size_t total=0;bool pressed=false;
  for(;;){
   if(flags(&pressed) || !pressed || total>=960000)break;
   frame->size=sizeof(frame->pcm);if(frame->size>960000-total)frame->size=960000-total;
   if(bsp_audio_read(frame->pcm,frame->size)!=ESP_OK || xQueueSend(audio_queue,frame,0)!=pdTRUE){portENTER_CRITICAL(&mux);capture_failed=true;held=false;portEXIT_CRITICAL(&mux);break;}
   total+=frame->size;
  }
  portENTER_CRITICAL(&mux);capture_done=true;state.recording=false;held=false;portEXIT_CRITICAL(&mux);
 }
}
static cJSON *request(pocket_link_config *link,const char *path,const char *body,char *response,size_t size){
 pocket_http_result result;
 if(pocket_http_json(link->origin,path,link->token,link->ca,body,response,size,&result)!=ESP_OK){message("Connection lost; not resent");return NULL;}
 if(result.status<200 || result.status>=300){
  if(result.status==401 || result.status==403)message("Pairing / login required");
  else if(result.status==409)message("Previous task needs attention");
  else message("Bridge could not accept request");
  return NULL;
 }
 cJSON *json=cJSON_Parse(response);if(!json)message("Invalid bridge response");return json;
}
static const char *string(cJSON *json,const char *key){cJSON *value=cJSON_GetObjectItemCaseSensitive(json,key);return cJSON_IsString(value)?value->valuestring:"";}
static bool post_empty(pocket_link_config *link,const char *path,char *response){cJSON *j=request(link,path,"{}",response,8192);bool ok=j!=NULL;cJSON_Delete(j);return ok;}
static bool wait_task(pocket_link_config *link,const char *target,const char *id,char *response,char *task){
 char path[193];int64_t deadline=esp_timer_get_time()+120000000;
 snprintf(path,sizeof(path),"/targets/%s/audio/%s",target,id);
 while(esp_timer_get_time()<deadline){
  bool pressed;if(flags(&pressed)){message("Stopped waiting; task may continue");return false;}
  cJSON *j=request(link,path,NULL,response,8192);if(!j)return false;
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
  bool pressed;if(flags(&pressed)){message("Stopped waiting; task continues");return false;}
  cJSON *j=request(link,path,NULL,response,8192);if(!j)return false;
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
 char path[193];snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech",target,task);
 if(!post_empty(link,path,response))return;
 int chunks=0;int64_t deadline=esp_timer_get_time()+120000000;
 while(esp_timer_get_time()<deadline){
  bool pressed;if(flags(&pressed))return;
  cJSON *j=request(link,path,NULL,response,8192);if(!j)return;
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
  cJSON *j=request(link,path,NULL,response,8192);if(!j){complete=false;break;}
  const char *data=string(j,"data");size_t bytes=0;
  int error=mbedtls_base64_decode(frame->pcm,sizeof(frame->pcm),&bytes,(const unsigned char*)data,strlen(data));cJSON_Delete(j);
  if(error || !bytes || bytes%2 || flags(&pressed) || bsp_audio_write(frame->pcm,bytes)!=ESP_OK){complete=false;message("Text ready; playback stopped");break;}
 }
 portENTER_CRITICAL(&mux);state.playing=false;portEXIT_CRITICAL(&mux);
 if(complete)message("Reply ready");
}
static void network(void *unused){
 (void)unused;
 pocket_link_config *link=malloc(sizeof(*link));audio_frame *frame=malloc(sizeof(*frame));
 char *response=malloc(8192),*body=malloc(6000),*encoded=malloc(5465);
 if(!link || !frame || !response || !body || !encoded){audio_unavailable=true;message("Not enough memory for voice");free(link);free(frame);free(response);free(body);free(encoded);vTaskDelete(NULL);return;}
 unsigned target,poll_target=0;
 for(;;){
  if(xQueueReceive(commands,&target,pdMS_TO_TICKS(5000))!=pdTRUE){
   pocket_setup_state current;pocket_setup_read(&current);bool online=false,ready=false;
   if(current.wifi_connected && time(NULL)>=1704067200 && pocket_setup_link(poll_target,link)){
    char route[64];snprintf(route,sizeof(route),"/targets/%s/status",poll_target?"linux":"windows");pocket_http_result result;
    if(pocket_http_json(link->origin,route,link->token,link->ca,NULL,response,8192,&result)==ESP_OK && result.status==200){
     cJSON *j=cJSON_Parse(response);online=!strcmp(string(j,"backend"),"available");
     cJSON *v=cJSON_GetObjectItemCaseSensitive(j,"voice");ready=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(v,"stt"));cJSON_Delete(j);
    }
   }
   portENTER_CRITICAL(&mux);state.online[poll_target]=online;state.speech_ready[poll_target]=ready;portEXIT_CRITICAL(&mux);
   memset(link,0,sizeof(*link));poll_target=1-poll_target;continue;
  }bool started=false,pressed=false;char id[33],path[193],task[65]={0};
  const char *name=target?"linux":"windows";uint32_t random[4];esp_fill_random(random,sizeof(random));
  snprintf(id,sizeof(id),"%08lx%08lx%08lx%08lx",(unsigned long)random[0],(unsigned long)random[1],(unsigned long)random[2],(unsigned long)random[3]);
  message("Connecting...");
  pocket_setup_state setup;pocket_setup_read(&setup);
  if(!setup.wifi_connected){message("Wi-Fi offline; hold OK on Status");goto finished;}
  if(time(NULL)<1704067200){message("Waiting for secure network time");goto finished;}
  if(!pocket_setup_link(target,link)){message("Configure bridge and device token");goto finished;}
  snprintf(path,sizeof(path),"/targets/%s/status",name);
  cJSON *status=request(link,path,NULL,response,8192);if(!status)goto finished;
  cJSON *voice=cJSON_GetObjectItemCaseSensitive(status,"voice");bool stt=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(voice,"stt"));
  portENTER_CRITICAL(&mux);state.online[target]=!strcmp(string(status,"backend"),"available");state.speech_ready[target]=stt;portEXIT_CRITICAL(&mux);cJSON_Delete(status);
  if(!stt){message("Speech recognition is not enabled");goto finished;}
  if(flags(&pressed) || !pressed){message("Hold OK to speak");goto finished;}
  if(bsp_audio_init()!=ESP_OK || bsp_audio_set_format(16000,16,1)!=ESP_OK){message("Audio hardware unavailable");goto finished;}
  bsp_audio_set_volume(60);
  snprintf(path,sizeof(path),"/targets/%s/audio/%s",name,id);
  cJSON *begin=request(link,path,"{\"chat_id\":\"pocket\"}",response,8192);if(!begin)goto finished;cJSON_Delete(begin);started=true;
  if(flags(&pressed) || !pressed)goto abort_capture;
  xQueueReset(audio_queue);
  portENTER_CRITICAL(&mux);capture_done=false;capture_failed=false;state.recording=true;portEXIT_CRITICAL(&mux);
  message("Listening... release OK to send");xTaskNotifyGive(recorder);
  unsigned sequence=0;bool failed=false;
  for(;;){
   if(flags(&pressed)){failed=true;break;}
   if(xQueueReceive(audio_queue,frame,pdMS_TO_TICKS(100))==pdTRUE){
    size_t length=0;
    if(mbedtls_base64_encode((unsigned char*)encoded,5465,&length,frame->pcm,frame->size)!=0){failed=true;break;}
    encoded[length]=0;snprintf(body,6000,"{\"sequence\":%u,\"data\":\"%s\"}",sequence,encoded);
    snprintf(path,sizeof(path),"/targets/%s/audio/%s/chunk",name,id);
    cJSON *ack=request(link,path,body,response,8192);if(!ack){failed=true;break;}cJSON_Delete(ack);sequence++;
   }
   portENTER_CRITICAL(&mux);bool done=capture_done,bad=capture_failed;portEXIT_CRITICAL(&mux);
   if(bad){message("Connection too slow; recording discarded");failed=true;break;}
   if(done && uxQueueMessagesWaiting(audio_queue)==0)break;
  }
  pocket_voice_release();
  if(failed || !sequence)goto abort_capture;
  snprintf(path,sizeof(path),"/targets/%s/audio/%s/finish",name,id);
  if(!post_empty(link,path,response))goto finished;
  message("Transcribing...");
  if(wait_task(link,name,id,response,task) && wait_reply(link,name,task,response))play(link,name,task,response,frame);
  goto finished;
 abort_capture:
  pocket_voice_release();
  if(started){snprintf(path,sizeof(path),"/targets/%s/audio/%s/abort",name,id);post_empty(link,path,response);}
 finished:
  pocket_voice_release();
  /* Let the bounded codec read finish before any subsequent capture/playback. */
  for(int i=0;i<20;i++){portENTER_CRITICAL(&mux);bool recording=state.recording;portEXIT_CRITICAL(&mux);if(!recording)break;vTaskDelay(pdMS_TO_TICKS(100));}
  memset(frame,0,sizeof(*frame));memset(body,0,6000);memset(encoded,0,5465);memset(link,0,sizeof(*link));
  portENTER_CRITICAL(&mux);state.busy=state.recording;state.playing=false;bool fault=state.recording;portEXIT_CRITICAL(&mux);
  if(fault)message("Audio stalled; restart device");
  pocket_http_close();
 }
}
esp_err_t pocket_voice_init(void){
 audio_queue=xQueueCreate(6,sizeof(audio_frame));commands=xQueueCreate(1,sizeof(unsigned));
 if(!audio_queue || !commands)return ESP_ERR_NO_MEM;
 esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);esp_sntp_setservername(0,"pool.ntp.org");esp_sntp_init();
 if(xTaskCreate(record,"pocket_record",6144,NULL,5,&recorder)!=pdPASS)return ESP_ERR_NO_MEM;
 if(xTaskCreate(network,"pocket_voice",12288,NULL,4,NULL)!=pdPASS)return ESP_ERR_NO_MEM;
 message("Hold OK to talk");return ESP_OK;
}
