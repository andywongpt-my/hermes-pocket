"""Exercise actual voice-worker allocation and cleanup without audio/network I/O."""
import base64
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HEADERS = {
    'esp_wifi.h': '#pragma once\n#include "esp_err.h"\ntypedef enum {WIFI_PS_NONE,WIFI_PS_MIN_MODEM,WIFI_PS_MAX_MODEM} wifi_ps_type_t;\nesp_err_t esp_wifi_get_ps(wifi_ps_type_t *);\nesp_err_t esp_wifi_set_ps(wifi_ps_type_t);\n',
    'esp_err.h': '#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n#define ESP_ERR_NO_MEM 0x101\n',
    'time.h': '#pragma once\n#include <stdint.h>\ntypedef int64_t time_t;\ntime_t test_time(time_t *);\n#define time test_time\n',
    'esp_log.h': '#pragma once\nvoid test_log(const char *,const char *,...);\n#define ESP_LOGI(tag,...) test_log(tag,__VA_ARGS__)\n',
    'esp_random.h': '#pragma once\n#include <stddef.h>\nvoid esp_fill_random(void *,size_t);\n',
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
    'esp_heap_caps.h': '#pragma once\n#include <stddef.h>\n#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_8BIT 2\nsize_t heap_caps_get_free_size(unsigned);\nsize_t heap_caps_get_largest_free_block(unsigned);\n',
    'esp_sntp.h': '#pragma once\n#define SNTP_OPMODE_POLL 0\nvoid esp_sntp_setoperatingmode(int);\nvoid esp_sntp_setservername(int,const char *);\nvoid esp_sntp_init(void);\n',
    'freertos/FreeRTOS.h': '''#pragma once
#include <stdint.h>
typedef int portMUX_TYPE;
typedef int BaseType_t;
typedef unsigned UBaseType_t;
typedef unsigned TickType_t;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(mux) ((void)(mux))
#define portEXIT_CRITICAL(mux) ((void)(mux))
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(ms) (ms)
''',
    'freertos/task.h': '''#pragma once
#include "FreeRTOS.h"
typedef void *TaskHandle_t;
BaseType_t xTaskCreate(void (*)(void *),const char *,unsigned,void *,unsigned,TaskHandle_t *);
void vTaskDelete(TaskHandle_t);
void vTaskDelay(TickType_t);
unsigned ulTaskNotifyTake(BaseType_t,TickType_t);
void xTaskNotifyGive(TaskHandle_t);
''',
    'freertos/queue.h': '''#pragma once
#include "FreeRTOS.h"
typedef void *QueueHandle_t;
QueueHandle_t xQueueCreate(unsigned,unsigned);
BaseType_t xQueueSend(QueueHandle_t,const void *,TickType_t);
BaseType_t xQueueReceive(QueueHandle_t,void *,TickType_t);
void xQueueReset(QueueHandle_t);
unsigned uxQueueMessagesWaiting(QueueHandle_t);
void vQueueDelete(QueueHandle_t);
''',
    'bsp_audio.h': '''#pragma once
#include <stddef.h>
#include "esp_err.h"
esp_err_t bsp_audio_init(void);
esp_err_t bsp_audio_set_format(unsigned,unsigned,unsigned);
void bsp_audio_set_volume(unsigned);
esp_err_t bsp_audio_read(void *,size_t);
esp_err_t bsp_audio_write(const void *,size_t);
''',
    'mbedtls/base64.h': '#pragma once\n#include <stddef.h>\nint mbedtls_base64_encode(unsigned char *,size_t,size_t *,const unsigned char *,size_t);\nint mbedtls_base64_decode(unsigned char *,size_t,size_t *,const unsigned char *,size_t);\n',
    'cJSON.h': '''#pragma once
typedef struct cJSON { char *valuestring;int valueint;double valuedouble;int type; } cJSON;
cJSON *cJSON_Parse(const char *);
void cJSON_Delete(cJSON *);
cJSON *cJSON_GetObjectItemCaseSensitive(const cJSON *,const char *);
int cJSON_IsString(const cJSON *);
int cJSON_IsTrue(const cJSON *);
int cJSON_IsBool(const cJSON *);
int cJSON_IsNumber(const cJSON *);
''',
}

HARNESS = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "pocket_usage_client.h"
#include "esp_wifi.h"
static void *test_malloc(size_t);
static void test_free(void *);
#define malloc test_malloc
#define free test_free
#include "pocket_voice.c"
#undef malloc
#undef free
static jmp_buf stopped;
static int mode, command_reads, audio_attempts, status_calls, posts, queue_live, recorder_tasks, record_active, packets, task_deletes;
static int retained_capture, playback_calls, finish_calls, abort_calls, capture_reads;
static int transport_failures,status_successes,http_closes;
static int chunk_attempts;
static wifi_ps_type_t wifi_ps;
static unsigned radio_awake_calls,radio_restore_calls;
static bool capture_low_latency,radio_restored_before_finish;
static int status_shape;
static unsigned volume_calls,last_volume,capture_volume,playback_volume,volume_changes;
static int64_t elapsed_ms,last_exchange_ms;
static bool transport_open;
static int usage_polls,usage_disconnects,json_live;
static int readonly_polls,readonly_disconnects;
static bool queue_voice_during_usage;
static unsigned usage_targets;
static int command_handle, audio_handle;
static unsigned char packet_data[65536];
static unsigned queue_count, queue_item_size, queue_head, queue_tail;
static size_t audio_bytes, peak_audio_bytes, queue_bytes, record_stack, uploaded_bytes, playback_bytes;
static void *allocations[16];
static size_t allocation_sizes[16];
static bool allocation_audio[16];
static char status_log[128];
static char capture_log[256],request_log[256];
static char endpoint[193];
/* Virtual 16kHz codec/RTOS producer while the real network worker is blocked.
   Delays are injected at HTTP's external boundary, independent of radio flags. */
static capture_frame *scheduled_frame;
static bool scheduled_recording;
static unsigned produced_frames,acknowledged_frames;
static int64_t next_frame_ms,record_started_ms;
static void produce_until(int64_t until);
static void track_peak(void) { size_t bytes=audio_bytes+queue_bytes+record_stack;if(bytes>peak_audio_bytes)peak_audio_bytes=bytes; }
static void *test_malloc(size_t size) {
 bool audio=size!=sizeof(pocket_link_config) && size!=8192;
 if(audio){audio_attempts++;if(mode<3 || (mode==3 && audio_attempts==1) || (mode==6 && record_active))return NULL;}
 void *p=malloc(size);assert(p);for(unsigned i=0;i<16;i++)if(!allocations[i]){allocations[i]=p;allocation_sizes[i]=size;allocation_audio[i]=audio;if(audio)audio_bytes+=size;track_peak();return p;}
 abort();
}
static void test_free(void *p) { if(!p)return;for(unsigned i=0;i<16;i++)if(allocations[i]==p){bool cancel_after=mode==17 && allocation_sizes[i]==CAPTURE_BODY_BYTES && !queue_live && !state.recording;if(allocation_audio[i])audio_bytes-=allocation_sizes[i];allocations[i]=NULL;free(p);if(cancel_after){assert(!audio_bytes);pocket_voice_cancel();}return;}abort(); }
static unsigned live_allocations(void) { unsigned n=0;for(unsigned i=0;i<16;i++)if(allocations[i])n++;return n; }
time_t test_time(time_t *out) { if(out)*out=1735689600;return 1735689600; }
void test_log(const char *tag,const char *fmt,...) { char *out=NULL;size_t cap=0;if(!strcmp(tag,"pocket_status")){out=status_log;cap=sizeof(status_log);}if(!strcmp(tag,"pocket_capture")){out=capture_log;cap=sizeof(capture_log);}if(!strcmp(tag,"pocket_voice_request")){out=request_log;cap=sizeof(request_log);}if(!out)return;va_list args;va_start(args,fmt);vsnprintf(out,cap,fmt,args);va_end(args); }
void esp_fill_random(void *p,size_t size) { memset(p,0,size); }
int64_t esp_timer_get_time(void) { return elapsed_ms*1000; }
size_t heap_caps_get_free_size(unsigned caps) { (void)caps;return 32000; }
size_t heap_caps_get_largest_free_block(unsigned caps) { (void)caps;return 24000; }
void esp_sntp_setoperatingmode(int unused) { (void)unused; }
void esp_sntp_setservername(int unused,const char *name) { (void)unused;(void)name; }
void esp_sntp_init(void) { }
esp_err_t esp_wifi_get_ps(wifi_ps_type_t *out) { if(mode==23)return ESP_FAIL;*out=wifi_ps;return ESP_OK; }
esp_err_t esp_wifi_set_ps(wifi_ps_type_t value) {
 assert(!record_active);if(value==WIFI_PS_NONE){radio_awake_calls++;if(mode==22)return ESP_FAIL;}else {radio_restore_calls++;if(mode==25 || (mode==24 && radio_restore_calls==1))return ESP_FAIL;}
 wifi_ps=value;return ESP_OK;
}
void pocket_setup_read(pocket_setup_state *out) { memset(out,0,sizeof(*out));out->wifi_connected=mode!=13; }
bool pocket_setup_link(unsigned target,pocket_link_config *out) { (void)target;memset(out,0,sizeof(*out));memcpy(out->origin,"https://fixture.invalid",sizeof("https://fixture.invalid"));return true; }
QueueHandle_t xQueueCreate(unsigned count,unsigned size) { if(size!=sizeof(unsigned)){assert(count*size<=sizeof(packet_data));queue_count=count;queue_item_size=size;queue_bytes=count*size;queue_live++;track_peak();return &audio_handle;}return &command_handle; }
void vQueueDelete(QueueHandle_t queue) { assert(queue==&audio_handle && !state.recording);queue_live--;queue_bytes=0; }
void xQueueReset(QueueHandle_t queue) { assert(queue==&audio_handle);packets=0;queue_head=queue_tail=0; }
unsigned uxQueueMessagesWaiting(QueueHandle_t queue) { assert(queue==&audio_handle);return (unsigned)packets; }
BaseType_t xQueueSend(QueueHandle_t queue,const void *value,TickType_t ticks) { (void)ticks;if(queue==&audio_handle){if((unsigned)packets==queue_count)return pdFALSE;memcpy(packet_data+queue_tail*queue_item_size,value,queue_item_size);queue_tail=(queue_tail+1)%queue_count;packets++;}return pdTRUE; }
static void produce_until(int64_t until){
 while(scheduled_recording && next_frame_ms<=until){
  elapsed_ms=next_frame_ms;scheduled_frame->size=2048;memset(scheduled_frame->pcm,0,2048);
  produced_frames++;capture_diag.bytes+=2048;capture_diag.max_read_ms=64;
  if(xQueueSend(audio_queue,scheduled_frame,0)!=pdTRUE){capture_diag.queue_peak=queue_count;capture_error(CAPTURE_QUEUE_FULL,ESP_OK);scheduled_recording=false;}
  else {if((unsigned)packets>capture_diag.queue_peak)capture_diag.queue_peak=(unsigned)packets;if(produced_frames==32)scheduled_recording=false;}
  next_frame_ms+=64;
  if(!scheduled_recording){capture_diag.capture_ms=(unsigned)(elapsed_ms-record_started_ms);capture_done=true;state.recording=false;held=false;test_free(scheduled_frame);scheduled_frame=NULL;}
 }
 elapsed_ms=until;
}
BaseType_t xQueueReceive(QueueHandle_t queue,void *out,TickType_t ticks) {
 if(queue==commands){if(command_reads++>=(mode==10?6:mode==11?4:1)){if((mode>0 && mode<10) || mode==12 || mode==15 || mode==16)assert(!usage_polls && !usage_disconnects);longjmp(stopped,1);}elapsed_ms+=ticks;*(unsigned *)out=1;state.target=1;held=mode!=2;bool idle=mode==0 || mode==10 || mode==11 || mode==13 || mode==14;state.busy=!idle;return idle?pdFALSE:pdTRUE;}
 assert(queue==&audio_handle);if(mode==7)cancelled=true;
 if(mode>=26 && mode<=28 && scheduled_recording && !packets)produce_until(next_frame_ms);
 if(packets){memcpy(out,packet_data+queue_head*queue_item_size,queue_item_size);queue_head=(queue_head+1)%queue_count;packets--;return pdTRUE;}return pdFALSE;
}
BaseType_t xTaskCreate(void (*fn)(void *),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *handle) {
 /* Conservatively retain a self-deleted recorder's stack until the test ends;
    FreeRTOS idle cleanup need not have run before subsequent TLS requests. */
 (void)priority;(void)handle;if(!strcmp(name,"pocket_record")){recorder_tasks++;if(mode==5)return pdFALSE;if(mode==7)return pdPASS;record_stack=stack;track_peak();
  if(mode>=26 && mode<=28){scheduled_frame=test_malloc(sizeof(*scheduled_frame));scheduled_recording=true;record_started_ms=next_frame_ms=elapsed_ms;return pdPASS;}
  record_active=1;fn(arg);record_active=0;}return pdPASS;
}
void vTaskDelete(TaskHandle_t unused) { (void)unused;if(record_active){task_deletes++;return;}longjmp(stopped,2); }
void vTaskDelay(TickType_t ticks) { (void)ticks; }
unsigned ulTaskNotifyTake(BaseType_t clear,TickType_t ticks) { (void)clear;(void)ticks;return 1; }
void xTaskNotifyGive(TaskHandle_t task) { (void)task; }
esp_err_t bsp_audio_init(void) { return ESP_OK; }
esp_err_t bsp_audio_set_format(unsigned a,unsigned b,unsigned c) { assert(a==16000 && b==16 && c==1);return ESP_OK; }
void bsp_audio_set_volume(unsigned volume) { assert(!record_active && volume<=100);volume_calls++;last_volume=volume; }
esp_err_t bsp_audio_read(void *out,size_t size) { memset(out,0,size);capture_volume=last_volume;capture_low_latency=wifi_ps==WIFI_PS_NONE;capture_reads++;elapsed_ms+=64;if(mode!=9)held=false;return mode==15?ESP_FAIL:ESP_OK; }
esp_err_t bsp_audio_write(const void *data,size_t size) { (void)data;playback_volume=last_volume;playback_calls++;playback_bytes=size;if(mode==19)pocket_voice_cancel();if(mode==21 && playback_calls==1){unsigned before=volume_calls;pocket_voice_set_volume(15);assert(volume_calls==before);volume_changes++;}return ESP_OK; }
int mbedtls_base64_encode(unsigned char *out,size_t cap,size_t *size,const unsigned char *in,size_t bytes) { (void)in;*size=4*((bytes+2)/3);if(cap<=*size)return -1;memset(out,'A',*size);if(bytes%3)out[*size-1]='=';if(bytes%3==1)out[*size-2]='=';out[*size]=0;uploaded_bytes=bytes;return 0; }
int mbedtls_base64_decode(unsigned char *out,size_t cap,size_t *size,const unsigned char *in,size_t bytes) { (void)in;(void)bytes;*size=mode==8?4096:2;if(cap<*size)return -1;memset(out,0,*size);return 0; }
static cJSON root, available={"available",0,0,2}, voice, stt, failed={"failed",0,0,2};
static cJSON submitted={"submitted",0,0,2}, completed={"completed",0,0,2}, ready={"ready",0,0,2};
static cJSON task_id={"fixture-task",0,0,2}, text={"synthetic",0,0,2}, chunk_data={"synthetic-pcm",0,0,2}, chunks={NULL,1,1,3};
cJSON *cJSON_Parse(const char *text) { (void)text;json_live++;return &root; }
void cJSON_Delete(cJSON *value) { if(value){assert(json_live>0);json_live--;} }
static cJSON *fixture_item(const cJSON *value,const char *key) { (void)value;if(!strcmp(key,"backend"))return &available;if(!strcmp(key,"voice"))return &voice;if(!strcmp(key,"stt")){stt.type=mode>=2?1:0;return &stt;}if(!strcmp(key,"state")){if((mode!=8 && mode<18) || mode>=26)return &failed;return strstr(endpoint,"/speech")?&ready:strstr(endpoint,"/tasks/")?&completed:&submitted;}if(!strcmp(key,"task_id"))return &task_id;if(!strcmp(key,"text") || !strcmp(key,"result"))return &text;if(!strcmp(key,"chunks")){chunks.valueint=mode==19 || mode==21?2:1;chunks.valuedouble=chunks.valueint;return &chunks;}if(!strcmp(key,"data"))return &chunk_data;return NULL; }
cJSON *cJSON_GetObjectItemCaseSensitive(const cJSON *value,const char *key) {
 if(status_shape && (!strcmp(key,"stt") || !strcmp(key,"tts")))return status_shape==1?NULL:&available;
 return fixture_item(value,key);
}
int cJSON_IsString(const cJSON *value) { return value && value->type==2; }
int cJSON_IsTrue(const cJSON *value) { return value && value->type==1; }
int cJSON_IsBool(const cJSON *value) { return value && (value->type==0 || value->type==1); }
int cJSON_IsNumber(const cJSON *value) { return value && value->type==3; }
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t cap,pocket_http_result *result) {
 (void)origin;(void)token;(void)ca;assert(cap>=3);snprintf(endpoint,sizeof(endpoint),"%s",path);if(strstr(path,"/status"))status_calls++;if(body)posts++;
 if(strstr(path,"/finish")){radio_restored_before_finish=wifi_ps==WIFI_PS_MIN_MODEM;finish_calls++;if(mode==18)pocket_voice_cancel();if(mode==20 || mode==21){unsigned before=volume_calls;pocket_voice_set_volume(89);assert(volume_calls==before);}}
 if(strstr(path,"/abort"))abort_calls++;
 if(body && strstr(path,"/chunk"))chunk_attempts++;
 if(mode>=26 && mode<=28 && body && strstr(path,"/chunk")){
  assert(!strncmp(body,"{\"sequence\":",12));char *end=NULL;unsigned long sequence=strtoul(body+12,&end,10);assert(end!=body+12 && *end==',' && sequence==acknowledged_frames);
  assert(uploaded_bytes==2048);unsigned delay=mode==27?1175:mode==28 && acknowledged_frames==0?200:20;
  produce_until(elapsed_ms+delay);acknowledged_frames++;
 }
 /* Model the deployed bridge's three-second idle expiry. Old firmware reuses
    this dead connection; fresh status connections must never hit that path. */
 if(mode==11 || (mode==10 && transport_open && elapsed_ms-last_exchange_ms>=3000) || (mode==12 && body) || (mode==16 && body && strstr(path,"/chunk"))){
  transport_failures++;transport_open=false;out[0]=0;result->status=-1;result->length=0;return ESP_FAIL;
 }
 transport_open=true;last_exchange_ms=elapsed_ms;if(strstr(path,"/status"))status_successes++;
 if(mode==8 && body && strstr(path,"/chunk")){elapsed_ms+=80;printf("SYNTHETIC_UPLOAD %s\n",body);}
 if(mode==8 && (strstr(path,"/finish") || (!body && !strstr(path,"/status") && !strstr(path,"/speech"))) && (queue_live || audio_bytes))retained_capture++;
 memcpy(out,"{}",3);result->status=200;result->length=2;if(mode==14)state.busy=true;return ESP_OK;
}
void pocket_http_close(void) { http_closes++;transport_open=false; }
void pocket_usage_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity) {
 assert(target<2 && capacity==8192 && !state.busy && !state.recording && !state.playing && !json_live);
 assert(transport_open && strstr(endpoint,"/status") && link && response && !queue_live && !audio_bytes);
 bool buffer_found=false;for(unsigned i=0;i<16;i++)if(allocations[i]==response && allocation_sizes[i]==8192)buffer_found=true;
 assert(buffer_found);usage_polls++;usage_targets|=1u<<target;if(queue_voice_during_usage)state.busy=true;
}
void pocket_usage_disconnected(unsigned target) { assert(target<2);usage_disconnects++; }
unsigned pocket_actions_poll_target(unsigned fallback){return fallback;}
void pocket_actions_idle(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 assert(target<2 && link && response && capacity==8192 && !state.busy && !state.recording && !state.playing && !json_live);
 assert(transport_open && !queue_live && !audio_bytes);readonly_polls++;
}
void pocket_actions_disconnected(unsigned target){assert(target<2);readonly_disconnects++;}
static void reset(int value) {
 for(unsigned i=0;i<16;i++)if(allocations[i]){free(allocations[i]);allocations[i]=NULL;}
 mode=value;status_shape=0;command_reads=audio_attempts=status_calls=posts=queue_live=recorder_tasks=record_active=packets=task_deletes=0;
 retained_capture=playback_calls=finish_calls=abort_calls=capture_reads=0;audio_bytes=peak_audio_bytes=queue_bytes=record_stack=uploaded_bytes=playback_bytes=0;queue_head=queue_tail=0;endpoint[0]=0;
 transport_failures=status_successes=http_closes=chunk_attempts=0;elapsed_ms=last_exchange_ms=0;transport_open=false;
 usage_polls=usage_disconnects=json_live=readonly_polls=readonly_disconnects=0;usage_targets=0;queue_voice_during_usage=false;
 volume_calls=last_volume=capture_volume=playback_volume=volume_changes=0;
 wifi_ps=WIFI_PS_MIN_MODEM;radio_awake_calls=radio_restore_calls=0;capture_low_latency=radio_restored_before_finish=false;
 scheduled_frame=NULL;scheduled_recording=false;produced_frames=acknowledged_frames=0;next_frame_ms=record_started_ms=0;
 memset(&state,0,sizeof(state));held=cancelled=capture_done=capture_failed=false;audio_unavailable=false;commands=&command_handle;audio_queue=NULL;status_log[0]=capture_log[0]=request_log[0]=0;
 pocket_voice_set_volume(60);pocket_voice_begin(1);
}
#define CHECK(c,m) do { if(!(c)){puts("FAIL: " m);return 1;} } while(0)
int main(int argc,char **argv) {
 if(argc>1 && !strcmp(argv[1],"cadence")){
  for(int value=26;value<=28;value++){
   reset(value);int cadence_exit=setjmp(stopped);if(!cadence_exit)network(NULL);
   if(value==27){CHECK(capture_diag.reason==CAPTURE_QUEUE_FULL && abort_calls==1 && !finish_calls && acknowledged_frames==1,"1175ms stall silently dropped audio or submitted an incomplete capture");CHECK(strstr(capture_log,"max_upload_ms=1175")!=NULL,"measured upload stall lost numeric evidence");}
   else {CHECK(!capture_diag.reason && produced_frames==32 && acknowledged_frames==32 && finish_calls==1 && !abort_calls,"sustainable cadence or bounded jitter lost/duplicated PCM frames");CHECK(capture_diag.bytes==65536,"complete PCM byte count changed");}
   CHECK(wifi_ps==WIFI_PS_MIN_MODEM && !queue_live && !scheduled_frame && !state.busy && peak_audio_bytes<=22000,"cadence scenario leaked resources or exceeded memory budget");
  }
  reset(0);puts("PASS: real worker uploads 65536 PCM bytes in order under20ms cadence/200ms jitter and explicitly discards1175ms stalls");return 0;
 }
 if(argc>1 && !strcmp(argv[1],"radio")){
  reset(8);int radio_exit=setjmp(stopped);if(!radio_exit)network(NULL);
  CHECK(capture_low_latency && radio_awake_calls==1,"capture kept DTIM power saving enabled");
  CHECK(wifi_ps==WIFI_PS_MIN_MODEM && radio_restore_calls==1 && radio_restored_before_finish,"power saving was not restored before finish/provider waiting");
  CHECK(peak_audio_bytes<=22000 && !queue_live && !state.busy,"low-latency profile expanded or retained capture memory");
  for(int value=3;value<=9;value++){reset(value);radio_exit=setjmp(stopped);if(!radio_exit)network(NULL);CHECK(wifi_ps==WIFI_PS_MIN_MODEM && radio_awake_calls==radio_restore_calls,"capture failure retained the awake radio profile");}
  reset(2);radio_exit=setjmp(stopped);if(!radio_exit)network(NULL);CHECK(!radio_awake_calls && !posts,"released gesture changed radio or started audio");
  for(int value=22;value<=23;value++){reset(value);radio_exit=setjmp(stopped);if(!radio_exit)network(NULL);CHECK(!posts && !recorder_tasks && wifi_ps==WIFI_PS_MIN_MODEM,"unavailable radio profile admitted a recording");}
  reset(24);radio_exit=setjmp(stopped);if(!radio_exit)network(NULL);CHECK(wifi_ps==WIFI_PS_MIN_MODEM && radio_restore_calls==2 && !finish_calls && abort_calls==1,"failed power restoration was forgotten or submitted audio to providers");
  reset(25);radio_exit=setjmp(stopped);if(!radio_exit)network(NULL);CHECK(wifi_ps==WIFI_PS_NONE && !finish_calls,"persistent restoration failure submitted audio");
  mode=8;wifi_ps_type_t saved=WIFI_PS_NONE;bool changed=false;CHECK(capture_radio_start(1,&saved,&changed) && saved==WIFI_PS_MIN_MODEM,"next capture overwrote the pending original idle power mode");capture_radio_restore(1,saved,&changed);CHECK(wifi_ps==WIFI_PS_MIN_MODEM,"pending original radio mode was not recovered");
  reset(0);puts("PASS: capture radio latency scope, failure cleanup and unchanged audio memory budget");return 0;
 }
 if(argc>1 && !strcmp(argv[1],"controls")){
  pocket_voice_state observed;pocket_voice_read(&observed);CHECK(observed.volume==60,"default volume is not 60");
  pocket_voice_set_volume(0);pocket_voice_read(&observed);CHECK(observed.volume==0,"mute was not retained");
  pocket_voice_set_volume(73);pocket_voice_read(&observed);CHECK(observed.volume==73,"requested volume lost");
  pocket_voice_set_volume(101);pocket_voice_read(&observed);CHECK(observed.volume==100,"volume upper clamp missing");
  pocket_voice_set_volume((unsigned)-1);pocket_voice_read(&observed);CHECK(observed.volume==100 && !volume_calls && !http_closes,"UI setter touched codec or transport");
  reset(20);pocket_voice_set_volume(37);int control_exit=setjmp(stopped);if(!control_exit)network(NULL);
  CHECK(capture_volume==37 && playback_volume==89 && volume_calls==2,"worker did not apply requested capture/playback volume");
  CHECK(!queue_live && live_allocations()==2 && !state.busy,"volume scenario leaked capture resources");
  reset(21);pocket_voice_set_volume(37);control_exit=setjmp(stopped);if(!control_exit)network(NULL);
  CHECK(playback_calls==2 && playback_volume==15 && volume_calls==3 && volume_changes==1,"volume edit was not applied on next playback chunk");
  reset(17);control_exit=setjmp(stopped);if(!control_exit)network(NULL);
  CHECK(finish_calls==0 && abort_calls==1 && !playback_calls,"cancel after capture release admitted finish/model work");
  CHECK(strstr(state.message,"discarded") && !strstr(state.message,"may continue"),"pre-finish discard was mislabeled");
  CHECK(!queue_live && live_allocations()==2 && !state.busy && !state.recording,"pre-finish discard leaked capture resources");
  reset(18);control_exit=setjmp(stopped);if(!control_exit)network(NULL);
  CHECK(finish_calls==1 && abort_calls==0 && strstr(state.message,"may continue"),"post-finish cancel claimed upstream discard");
  reset(19);control_exit=setjmp(stopped);if(!control_exit)network(NULL);
  CHECK(playback_calls==1 && finish_calls==1 && !abort_calls && strstr(state.message,"Playback stopped"),"local playback cancel did not stop cooperatively");
  CHECK(!queue_live && live_allocations()==2 && !state.busy && !state.playing,"playback cancel leaked resources");
  reset(0);puts("PASS: volume requests/application and pre/post-finish cooperative cancellation");return 0;
 }
 if(argc>1){
  reset(8);int exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
  CHECK(exit_kind==1 && finish_calls==1 && abort_calls==0,"synthetic voice did not finish capture");
  printf("METRICS peak_audio_bytes=%zu retained_capture=%d capture_pcm_bytes=%zu playback_pcm_bytes=%zu queued_pcm_bytes=%u deferred_recorder_stack_bytes=%zu\n",peak_audio_bytes,retained_capture,uploaded_bytes,playback_bytes,queue_count*(queue_item_size-(unsigned)sizeof(size_t)),record_stack);
  if(!strcmp(argv[1],"budget"))CHECK(peak_audio_bytes<=22000,"active audio exceeds the 22000-byte queue/workspace/recorder-stack budget");
  if(!strcmp(argv[1],"phase"))CHECK(retained_capture==0,"capture-only resources survived into finish/STT/task waiting");
  CHECK(record_stack==6144,"phase budget did not account for deferred recorder-stack reclamation");
  CHECK(playback_calls==1 && playback_bytes==4096,"full-size PCM playback changed");
  CHECK(uploaded_bytes>0 && uploaded_bytes<=2048,"capture chunks exceeded the reduced workspace");
  CHECK(strstr(capture_log,"target=1 reason=0 error=0 bytes=2048 capture_ms=64 max_read_ms=64 queue_peak=1 chunks=1 max_upload_ms=80")!=NULL,"capture production and upload timing were not recorded accurately");
  CHECK(queue_live==0 && live_allocations()==2 && !state.busy,"voice candidate leaked audio resources");
  printf("PASS: %s peak_audio_bytes=%zu capture_pcm_bytes=%zu playback_pcm_bytes=%zu queued_pcm_bytes=%u\n",argv[1],peak_audio_bytes,uploaded_bytes,playback_bytes,queue_count*(queue_item_size-(unsigned)sizeof(size_t)));return 0;
 }
 reset(0);int exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(exit_kind==1 && status_calls==1,"idle status polling must survive unavailable audio memory");
 CHECK(audio_attempts==0 && queue_live==0 && recorder_tasks==0,"idle status polling allocated inactive audio resources");
 CHECK(state.online[0] && !state.speech_ready[0],"STT-off bridge status did not remain available");
 CHECK(!strcmp(status_log,"target=0 http=200 backend_available=1 stt=0"),"status readiness evidence must be numeric-only");
 CHECK(usage_polls==1 && usage_targets==1 && !usage_disconnects,"idle Usage did not use the existing worker/client after status cleanup");
 reset(0);status_shape=1;exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(!state.diagnostics[0].stt_known && !state.diagnostics[0].tts_known,"missing voice capabilities became known disabled flags");
 reset(0);status_shape=2;exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(!state.diagnostics[0].stt_known && !state.diagnostics[0].tts_known,"non-boolean voice capabilities became known disabled flags");
 reset(10);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(status_calls==6 && status_successes==6 && !transport_failures && http_closes==6 && !transport_open,"status reused a connection after the bridge idle expiry");
 CHECK(state.online[0] && state.online[1] && state.speech_ready[0] && state.speech_ready[1],"fresh successful polls did not keep both target states ready");
 CHECK(usage_polls==6 && usage_targets==3 && !usage_disconnects,"Usage did not remain serialized and target scoped across fresh idle polls");
 CHECK(readonly_polls==6 && !readonly_disconnects,"idle read-only tasks did not share the existing worker");
 CHECK(state.diagnostics[0].checked && state.diagnostics[1].checked && state.diagnostics[0].http_status==200 && state.diagnostics[1].http_status==200,"successful status observations were not target scoped");
 CHECK(state.diagnostics[0].backend_available && state.diagnostics[1].stt && !state.diagnostics[1].tts,"status diagnostic capabilities were invented or lost");
 reset(10);queue_voice_during_usage=true;exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(usage_polls==6 && readonly_polls==0,"queued voice lost priority after an in-flight Usage GET");
 reset(11);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(status_calls==4 && !status_successes && transport_failures==4 && !state.online[0] && !state.online[1] && !state.speech_ready[0] && !state.speech_ready[1],"persistent failure was hidden instead of staying offline");
 CHECK(!posts && !audio_attempts && !queue_live,"failed status triggered side effects or audio allocations");
 CHECK(!usage_polls && usage_disconnects==4,"failed status started Usage TLS requests");
 CHECK(!readonly_polls && readonly_disconnects==4,"failed status started task GETs or lost honest offline state");
 CHECK(state.diagnostics[0].transport_error==ESP_FAIL && state.diagnostics[1].http_status==-1 && !state.diagnostics[1].backend_available,"failed status retained stale success or mixed HTTP and transport errors");
 reset(12);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(posts==1 && transport_failures==1 && !finish_calls && !abort_calls,"failed audio submission was retried or duplicated");
 CHECK(!state.busy && !queue_live && live_allocations()==2,"failed POST did not release capture allocations");
 reset(13);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(!usage_polls && usage_disconnects==1 && !status_calls,"offline worker attempted Usage I/O");
 CHECK(state.diagnostics[0].checked && !state.diagnostics[0].wifi && !state.diagnostics[0].http_status,"offline poll invented a network observation");
 reset(14);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(!usage_polls && !usage_disconnects && status_calls==1,"queued voice lost priority to Usage polling");
 for(int value=1;value<=2;value++){
  reset(value);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
  CHECK(exit_kind==1 && status_calls==1 && posts==0,"STT-off or released button executed audio work");
  CHECK(audio_attempts==0 && queue_live==0 && recorder_tasks==0 && live_allocations()==2,"ineligible audio reserved resources");
 }
 reset(3);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(exit_kind==1 && posts==0 && !state.busy,"partial audio allocation failure did not recover");
 CHECK(queue_live==0 && audio_queue==NULL && live_allocations()==2,"partial allocation leaked audio resources");
 reset(4);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(exit_kind==1 && recorder_tasks==1 && task_deletes==1 && posts>0,"eligible synthetic capture did not execute");
 CHECK(queue_live==0 && audio_queue==NULL && live_allocations()==2 && !state.busy,"completed capture retained audio resources");
 for(int value=5;value<=6;value++){
  reset(value);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
  CHECK(exit_kind==1 && posts==2 && !state.busy && !state.recording,"recorder failure did not abort and recover");
  CHECK(queue_live==0 && audio_queue==NULL && live_allocations()==2,"recorder failure leaked audio resources");
 }
 reset(7);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(exit_kind==1 && state.busy && state.recording,"stalled recorder must remain blocked from reuse");
 CHECK(queue_live==1 && audio_queue!=NULL && live_allocations()==2,"cleanup freed a queue still owned by the recorder");
 reset(9);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(exit_kind==1 && capture_reads==(int)queue_count+1 && abort_calls==1 && finish_calls==0,"queue overflow was not bounded and discarded");
 CHECK(queue_live==0 && live_allocations()==2 && !state.busy,"overflow retained capture resources");
 CHECK(strstr(capture_log,"target=1 reason=2 error=0 bytes=10240 capture_ms=320 max_read_ms=64 queue_peak=4")!=NULL,"bounded queue overflow lacked distinct numeric diagnostics");
 reset(15);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(capture_reads==1 && abort_calls==1 && finish_calls==0,"audio read failure did not discard capture before transcription");
 CHECK(strstr(state.message,"Audio input failed")!=NULL,"audio read failure was mislabeled as a slow connection");
 CHECK(strstr(capture_log,"target=1 reason=1 error=-1 bytes=0 capture_ms=64 max_read_ms=64 queue_peak=0 chunks=0")!=NULL,"audio read failure lacked distinct numeric diagnostics");
 CHECK(queue_live==0 && live_allocations()==2 && !state.busy,"audio read failure retained capture resources");
 reset(16);exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(chunk_attempts==1 && abort_calls==1 && finish_calls==0,"failed audio chunk was retried or submitted for transcription");
 CHECK(strstr(request_log,"target=1 stage=3 error=-1 http=-1")!=NULL,"upload failure lacked request-stage diagnostics");
 CHECK(queue_live==0 && live_allocations()==2 && !state.busy,"failed upload retained capture resources");
 reset(0);puts("PASS: idle status, eligibility, allocation failure and capture cleanup");return 0;
}
'''


class FirmwareVoiceTests(unittest.TestCase):
    def _exercise(self, cases):
        compiler = shutil.which('gcc') or shutil.which('clang')
        cached = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1] / 'firmware/main'
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            for name, contents in HEADERS.items():
                path = temp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding='utf-8')
            harness = temp / 'test_voice.c'
            harness.write_text(HARNESS, encoding='utf-8')
            binary = temp / ('test-voice.exe' if os.name == 'nt' else 'test-voice')
            compiled = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I'+str(temp), '-I'+str(source), '-I'+str(source.parent/'core'), str(harness), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            for case in cases:
                with self.subTest(case=case or 'lifetime'):
                    run = subprocess.run([str(binary)] + ([case] if case else []), capture_output=True, text=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    self.assertIn('PASS:', run.stdout)
                    if case in ('budget', 'phase'):
                        uploads = [json.loads(line.removeprefix('SYNTHETIC_UPLOAD '))
                                   for line in run.stdout.splitlines()
                                   if line.startswith('SYNTHETIC_UPLOAD ')]
                        self.assertEqual(len(uploads), 1)
                        self.assertEqual(uploads[0]['sequence'], 0)
                        self.assertEqual(base64.b64decode(uploads[0]['data'], validate=True), b'\0' * 2048)

    def test_idle_status_and_audio_resource_lifetime(self):
        self._exercise([''])

    def test_active_voice_memory_and_phase_release(self):
        self._exercise(['budget', 'phase'])

    def test_worker_volume_and_cancel_boundaries(self):
        self._exercise(['controls'])

    def test_capture_radio_latency_scope_and_failure_cleanup(self):
        self._exercise(['radio'])

    def test_pcm_cadence_jitter_and_measured_upload_stall(self):
        self._exercise(['cadence'])
