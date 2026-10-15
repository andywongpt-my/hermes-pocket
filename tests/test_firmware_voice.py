"""Exercise actual voice-worker allocation and cleanup without audio/network I/O."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HEADERS = {
    'esp_err.h': '#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n#define ESP_ERR_NO_MEM 0x101\n',
    'time.h': '#pragma once\n#include <stdint.h>\ntypedef int64_t time_t;\ntime_t test_time(time_t *);\n#define time test_time\n',
    'esp_log.h': '#pragma once\nvoid test_log(const char *,const char *,...);\n#define ESP_LOGI(tag,...) test_log(tag,__VA_ARGS__)\n',
    'esp_random.h': '#pragma once\n#include <stddef.h>\nvoid esp_fill_random(void *,size_t);\n',
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
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
static void *test_malloc(size_t);
static void test_free(void *);
#define malloc test_malloc
#define free test_free
#include "pocket_voice.c"
#undef malloc
#undef free
static jmp_buf stopped;
static int mode, command_reads, audio_attempts, status_calls, posts, queue_live, recorder_tasks, record_active, packets, task_deletes;
static int command_handle, audio_handle;
static audio_frame packet;
static void *allocations[16];
static char status_log[128];
static void *test_malloc(size_t size) {
 bool audio=size!=sizeof(pocket_link_config) && size!=8192;
 if(audio){audio_attempts++;if(mode<3 || (mode==3 && size==sizeof(audio_frame)) || (mode==6 && record_active))return NULL;}
 void *p=malloc(size);for(unsigned i=0;i<16;i++)if(!allocations[i]){allocations[i]=p;return p;}
 abort();
}
static void test_free(void *p) { if(!p)return;for(unsigned i=0;i<16;i++)if(allocations[i]==p){allocations[i]=NULL;free(p);return;}abort(); }
static unsigned live_allocations(void) { unsigned n=0;for(unsigned i=0;i<16;i++)if(allocations[i])n++;return n; }
time_t test_time(time_t *out) { if(out)*out=1735689600;return 1735689600; }
void test_log(const char *tag,const char *fmt,...) { if(strcmp(tag,"pocket_status"))return;va_list args;va_start(args,fmt);vsnprintf(status_log,sizeof(status_log),fmt,args);va_end(args); }
void esp_fill_random(void *p,size_t size) { memset(p,0,size); }
int64_t esp_timer_get_time(void) { return 0; }
void esp_sntp_setoperatingmode(int unused) { (void)unused; }
void esp_sntp_setservername(int unused,const char *name) { (void)unused;(void)name; }
void esp_sntp_init(void) { }
void pocket_setup_read(pocket_setup_state *out) { memset(out,0,sizeof(*out));out->wifi_connected=true; }
bool pocket_setup_link(unsigned target,pocket_link_config *out) { (void)target;memset(out,0,sizeof(*out));memcpy(out->origin,"https://fixture.invalid",sizeof("https://fixture.invalid"));return true; }
QueueHandle_t xQueueCreate(unsigned count,unsigned size) { if(size==sizeof(audio_frame)){assert(count==6);queue_live++;return &audio_handle;}return &command_handle; }
void vQueueDelete(QueueHandle_t queue) { assert(queue==&audio_handle && !state.recording);queue_live--; }
void xQueueReset(QueueHandle_t queue) { assert(queue==&audio_handle);packets=0; }
unsigned uxQueueMessagesWaiting(QueueHandle_t queue) { assert(queue==&audio_handle);return (unsigned)packets; }
BaseType_t xQueueSend(QueueHandle_t queue,const void *value,TickType_t ticks) { (void)ticks;if(queue==&audio_handle){packet=*(const audio_frame *)value;packets++;}return pdTRUE; }
BaseType_t xQueueReceive(QueueHandle_t queue,void *out,TickType_t ticks) {
 (void)ticks;if(queue==commands){if(command_reads++)longjmp(stopped,1);*(unsigned *)out=1;held=mode!=2;state.busy=mode!=0;return mode?pdTRUE:pdFALSE;}
 assert(queue==&audio_handle);if(mode==7)cancelled=true;if(packets){*(audio_frame *)out=packet;packets--;return pdTRUE;}return pdFALSE;
}
BaseType_t xTaskCreate(void (*fn)(void *),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *handle) {
 (void)stack;(void)priority;(void)handle;if(!strcmp(name,"pocket_record")){recorder_tasks++;if(mode==5)return pdFALSE;if(mode==7)return pdPASS;record_active=1;fn(arg);record_active=0;}return pdPASS;
}
void vTaskDelete(TaskHandle_t unused) { (void)unused;if(record_active){task_deletes++;return;}longjmp(stopped,2); }
void vTaskDelay(TickType_t ticks) { (void)ticks; }
unsigned ulTaskNotifyTake(BaseType_t clear,TickType_t ticks) { (void)clear;(void)ticks;return 1; }
void xTaskNotifyGive(TaskHandle_t task) { (void)task; }
esp_err_t bsp_audio_init(void) { return ESP_OK; }
esp_err_t bsp_audio_set_format(unsigned a,unsigned b,unsigned c) { (void)a;(void)b;(void)c;return ESP_OK; }
void bsp_audio_set_volume(unsigned volume) { (void)volume; }
esp_err_t bsp_audio_read(void *out,size_t size) { memset(out,0,size);held=false;return ESP_OK; }
esp_err_t bsp_audio_write(const void *data,size_t size) { (void)data;(void)size;return ESP_OK; }
int mbedtls_base64_encode(unsigned char *out,size_t cap,size_t *size,const unsigned char *in,size_t bytes) { (void)cap;(void)in;(void)bytes;memcpy(out,"QQ==",4);*size=4;return 0; }
int mbedtls_base64_decode(unsigned char *out,size_t cap,size_t *size,const unsigned char *in,size_t bytes) { (void)out;(void)cap;(void)in;(void)bytes;*size=2;return 0; }
static cJSON root, available={"available",0,0,2}, voice, stt, failed={"failed",0,0,2};
cJSON *cJSON_Parse(const char *text) { (void)text;return &root; }
void cJSON_Delete(cJSON *value) { (void)value; }
cJSON *cJSON_GetObjectItemCaseSensitive(const cJSON *value,const char *key) { (void)value;if(!strcmp(key,"backend"))return &available;if(!strcmp(key,"voice"))return &voice;if(!strcmp(key,"stt")){stt.type=mode>=2?1:0;return &stt;}if(!strcmp(key,"state"))return &failed;return NULL; }
int cJSON_IsString(const cJSON *value) { return value && value->type==2; }
int cJSON_IsTrue(const cJSON *value) { return value && value->type==1; }
int cJSON_IsNumber(const cJSON *value) { return value && value->type==3; }
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t cap,pocket_http_result *result) {
 (void)origin;(void)token;(void)ca;(void)cap;if(strstr(path,"/status"))status_calls++;if(body)posts++;memcpy(out,"{}",3);result->status=200;result->length=2;return ESP_OK;
}
void pocket_http_close(void) { }
static void reset(int value) {
 for(unsigned i=0;i<16;i++)if(allocations[i]){free(allocations[i]);allocations[i]=NULL;}
 mode=value;command_reads=audio_attempts=status_calls=posts=queue_live=recorder_tasks=record_active=packets=task_deletes=0;
 memset(&state,0,sizeof(state));held=cancelled=capture_done=capture_failed=false;audio_unavailable=false;commands=&command_handle;audio_queue=NULL;status_log[0]=0;
}
#define CHECK(c,m) do { if(!(c)){puts("FAIL: " m);return 1;} } while(0)
int main(void) {
 reset(0);int exit_kind=setjmp(stopped);if(!exit_kind)network(NULL);
 CHECK(exit_kind==1 && status_calls==1,"idle status polling must survive unavailable audio memory");
 CHECK(audio_attempts==0 && queue_live==0 && recorder_tasks==0,"idle status polling allocated inactive audio resources");
 CHECK(state.online[0] && !state.speech_ready[0],"STT-off bridge status did not remain available");
 CHECK(!strcmp(status_log,"target=0 http=200 backend_available=1 stt=0"),"status readiness evidence must be numeric-only");
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
 reset(0);puts("PASS: idle status, eligibility, allocation failure and capture cleanup");return 0;
}
'''


class FirmwareVoiceTests(unittest.TestCase):
    def test_idle_status_and_audio_resource_lifetime(self):
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
            compiled = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I'+str(temp), '-I'+str(source), str(harness), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS:', run.stdout)
