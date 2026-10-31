"""Execute the real C transport against deterministic SDK boundary fixtures."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


HEADERS = {
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
    'lwip/sockets.h': '#pragma once\n#include <stddef.h>\n#define IPPROTO_TCP 6\n#define TCP_NODELAY 1\nint pocket_test_setsockopt(int,int,int,const void *,unsigned);\n#define setsockopt pocket_test_setsockopt\n',
    'esp_heap_caps.h': '#pragma once\n#include <stddef.h>\n#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_8BIT 2\nsize_t heap_caps_get_free_size(unsigned);\nsize_t heap_caps_get_largest_free_block(unsigned);\n',
    'time.h': '#pragma once\n#include <stdint.h>\ntypedef int64_t time_t;\ntime_t pocket_test_time(time_t *);\n#define time pocket_test_time\n',
    'esp_err.h': '''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_SIZE 0x104
''',
    'esp_http_client.h': '''#pragma once
#include <stdbool.h>
#include "esp_err.h"
typedef struct mock_client *esp_http_client_handle_t;
typedef struct { int event_id; void *user_data; void *data; int data_len; esp_http_client_handle_t client; } esp_http_client_event_t;
enum { HTTP_EVENT_ON_DATA, HTTP_EVENT_ON_CONNECTED, HTTP_EVENT_HEADERS_SENT, HTTP_EVENT_ON_HEADER, HTTP_EVENT_DISCONNECTED, HTTP_EVENT_ON_FINISH, HTTP_METHOD_GET, HTTP_METHOD_POST };
typedef struct {
 const char *url, *cert_pem;
 int timeout_ms, buffer_size, buffer_size_tx;
 bool disable_auto_redirect, keep_alive_enable, skip_cert_common_name_check;
 esp_err_t (*event_handler)(esp_http_client_event_t *);
 esp_err_t (*crt_bundle_attach)(void *);
 void *user_data;
} esp_http_client_config_t;
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *);
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t);
esp_err_t esp_http_client_set_url(esp_http_client_handle_t,const char *);
esp_err_t esp_http_client_set_user_data(esp_http_client_handle_t,void *);
esp_err_t esp_http_client_set_method(esp_http_client_handle_t,int);
esp_err_t esp_http_client_set_header(esp_http_client_handle_t,const char *,const char *);
esp_err_t esp_http_client_set_post_field(esp_http_client_handle_t,const char *,int);
esp_err_t esp_http_client_perform(esp_http_client_handle_t);
int esp_http_client_get_status_code(esp_http_client_handle_t);
int esp_http_client_get_socket(esp_http_client_handle_t);
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t,int *,int *);
''',
    'esp_crt_bundle.h': '#pragma once\n#include "esp_err.h"\nesp_err_t esp_crt_bundle_attach(void *);\n',
    'esp_log.h': '''#pragma once
void test_log(const char *,const char *,...);
#define ESP_LOGW(tag,...) test_log(tag,__VA_ARGS__)
#define ESP_LOGI(tag,...) test_log(tag,__VA_ARGS__)
''',
}

HARNESS = r'''
#include "pocket_http.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
struct mock_client { bool active,connected,nodelay; void *user_data; esp_err_t (*event_handler)(esp_http_client_event_t *); };
static struct mock_client mock;
static int failure, verify_flags, reads, performs, cleanups, logs;
static bool custom_ca;
static bool timing_active,timing_marked;
void pocket_http_timing_begin(bool enabled){timing_active=enabled;timing_marked=false;}
void pocket_http_timing_headers_sent(void){if(timing_active)timing_marked=true;}
void pocket_http_timing_end(pocket_http_io_timing *out){
 if(out){*out=timing_active?(pocket_http_io_timing){.write_us=1100,.body_write_us=900,.read_us=70000,.event_post_us=4000,.write_calls=2,.read_calls=3,.event_posts=8,.body_bytes=timing_marked?2756:0}:(pocket_http_io_timing){0};}
 timing_active=false;
}

static bool disconnect_on_success;
static time_t epoch;
static int64_t clock_us;
static unsigned free_reads,largest_reads,heap_delay_ms,body_wait_ms,sdk_tail_ms,callback_delay_us,log_delay_us;
static int response_status=200;
static char requested_url[600];
static int requested_method;
int64_t esp_timer_get_time(void) { return clock_us; }
static char diagnostic[256], log_tag[32];
static const char token[]="SYNTHETIC_PRIVATE_TOKEN_0123456789abcdef";
time_t pocket_test_time(time_t *out) { if(out)*out=epoch;return epoch; }
size_t heap_caps_get_free_size(unsigned caps) { (void)caps;free_reads++;return 64000; }
size_t heap_caps_get_largest_free_block(unsigned caps) { (void)caps;largest_reads++;clock_us+=(int64_t)heap_delay_ms*1000;return 48000; }
void test_log(const char *tag,const char *fmt,...) {
 va_list args;va_start(args,fmt);vsnprintf(diagnostic,sizeof(diagnostic),fmt,args);va_end(args);
 snprintf(log_tag,sizeof(log_tag),"%s",tag);logs++;clock_us+=log_delay_us;
}
esp_err_t esp_crt_bundle_attach(void *unused) { (void)unused;return ESP_OK; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *cfg) {
 assert(cfg->disable_auto_redirect && !cfg->skip_cert_common_name_check);
 assert(cfg->timeout_ms==15000 && cfg->buffer_size==2048 && cfg->buffer_size_tx==1024 && cfg->keep_alive_enable);
 assert(custom_ca ? cfg->cert_pem && !cfg->crt_bundle_attach : !cfg->cert_pem && cfg->crt_bundle_attach);
 mock.active=true;mock.user_data=cfg->user_data;mock.event_handler=cfg->event_handler;return &mock;
}
int esp_http_client_get_socket(esp_http_client_handle_t c) { assert(c->active && c->connected);return 7; }
int pocket_test_setsockopt(int fd,int level,int option,const void *value,unsigned length) {
 assert(fd==7 && level==6 && option==1 && length==sizeof(int) && *(const int*)value==1);
 clock_us+=callback_delay_us;mock.nodelay=true;return 0;
}
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t c) { assert(c->active);c->active=false;cleanups++;return ESP_OK; }
esp_err_t esp_http_client_set_url(esp_http_client_handle_t c,const char *value) { assert(c->active && strlen(value)<sizeof(requested_url));snprintf(requested_url,sizeof(requested_url),"%s",value);return ESP_OK; }
esp_err_t esp_http_client_set_user_data(esp_http_client_handle_t c,void *value) { assert(c->active);c->user_data=value;return ESP_OK; }
esp_err_t esp_http_client_set_method(esp_http_client_handle_t c,int value) { assert(c->active);requested_method=value;return ESP_OK; }
esp_err_t esp_http_client_set_header(esp_http_client_handle_t c,const char *name,const char *value) {
 assert(c->active);if(!strcmp(name,"Authorization"))assert(!strncmp(value,"Bearer ",7) && !strcmp(value+7,token));return ESP_OK;
}
esp_err_t esp_http_client_set_post_field(esp_http_client_handle_t c,const char *value,int size) { (void)value;(void)size;assert(c->active);return ESP_OK; }
esp_err_t esp_http_client_perform(esp_http_client_handle_t c) {
 assert(c->active);performs++;
 if(failure==2)return ESP_FAIL;
 if(!c->connected){clock_us+=20000;c->connected=true;esp_http_client_event_t connected={.event_id=HTTP_EVENT_ON_CONNECTED,.user_data=c->user_data,.client=c};c->event_handler(&connected);}
 clock_us+=1000;esp_http_client_event_t headers={.event_id=HTTP_EVENT_HEADERS_SENT,.user_data=c->user_data,.client=c};c->event_handler(&headers);
 clock_us+=21000;headers.event_id=HTTP_EVENT_ON_HEADER;c->event_handler(&headers);
 clock_us+=(int64_t)body_wait_ms*1000;headers.event_id=HTTP_EVENT_ON_DATA;headers.data="{}";headers.data_len=2;c->event_handler(&headers);
 headers.event_id=HTTP_EVENT_ON_FINISH;c->event_handler(&headers);clock_us+=(int64_t)sdk_tail_ms*1000;
 if(disconnect_on_success){headers.event_id=HTTP_EVENT_DISCONNECTED;c->connected=false;c->event_handler(&headers);}
 return failure?ESP_FAIL:ESP_OK;
}
int esp_http_client_get_status_code(esp_http_client_handle_t c) { assert(c->active);return failure?0:response_status; }
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t c,int *code,int *flags) {
 assert(c->active);reads++;*code=0x2700;*flags=verify_flags;return 0x801a;
}
static void reset(bool ca,time_t now,int error,int flags) {
 pocket_http_close();memset(&mock,0,sizeof(mock));failure=error;verify_flags=flags;custom_ca=ca;epoch=now;
 reads=performs=cleanups=logs=0;diagnostic[0]=log_tag[0]=0;
 requested_url[0]=0;requested_method=-1;
 disconnect_on_success=false;free_reads=largest_reads=heap_delay_ms=body_wait_ms=sdk_tail_ms=callback_delay_us=log_delay_us=0;response_status=200;
}
#define CHECK(condition,message) do { if(!(condition)){puts("FAIL: " message);return 1;} } while(0)
int main(void) {
 char out[64]="old response";pocket_http_result result;
 reset(true,1735689600,0,0);heap_delay_ms=7;body_wait_ms=139;sdk_tail_ms=11;callback_delay_us=1000;log_delay_us=30000;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_OK,"timed status");
 CHECK(result.pre_heap_us==7000 && result.perform_us==193000 && result.post_heap_us==7000 && result.diagnostic_us==30000 && result.total_us==237000,"HTTP timing stages were conflated");
 CHECK(result.headers_ms==29 && result.response_ms==50 && result.body_ms==189 && result.finish_ms==189 && result.perform_end_ms==200,"event timestamps use different origins or lose body/finish");
 CHECK(result.event_us==1000 && result.performed && result.body_started && result.finished && result.heap_before_measured,"callback wall-time or stage-presence evidence lost");
 reset(true,1735689600,0,0);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_OK,"warm status");
 free_reads=largest_reads=logs=0;heap_delay_ms=35;int64_t began=clock_us;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture/chunk",token,"SYNTHETIC_PUBLIC_CA","{}",out,sizeof(out),&result)==ESP_OK,"reused chunk");
 CHECK(!free_reads && !largest_reads,"successful reused audio chunk scanned the heap");
 CHECK(!timing_active && result.io.write_us==1100 && result.io.read_us==70000 && result.io.event_post_us==4000 && result.io.body_bytes==2756,"chunk trace was not started/marked/copied/ended");
 CHECK(!logs && !result.heap_measured && clock_us-began<64000,"chunk diagnostics consumed a PCM frame budget");
 CHECK(!result.heap_before_measured && !result.pre_heap_us && !result.post_heap_us && result.perform_us==22000 && result.total_us==22000,"skipped heap timing or reused chunk perform evidence changed");
 response_status=403;free_reads=largest_reads=0;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture/chunk",token,"SYNTHETIC_PUBLIC_CA","{}",out,sizeof(out),&result)==ESP_OK && result.status==403 && result.heap_measured && !result.heap_before_measured && largest_reads==1,"HTTP rejection lost post-failure heap evidence");
 response_status=200;
 failure=1;free_reads=largest_reads=0;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture/chunk",token,"SYNTHETIC_PUBLIC_CA","{}",out,sizeof(out),&result)==ESP_FAIL,"chunk failure");
 CHECK(free_reads==1 && largest_reads==1 && result.heap_measured && !result.heap_before_measured && reads==1,"failed reused chunk lost its post-failure heap/TLS diagnostic");
 reset(true,1735689600,0,0);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture",token,"SYNTHETIC_PUBLIC_CA","{}",out,sizeof(out),&result)==ESP_OK && largest_reads==2,"begin heap evidence missing");
 free_reads=largest_reads=0;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture/finish",token,"SYNTHETIC_PUBLIC_CA","{}",out,sizeof(out),&result)==ESP_OK && largest_reads==2,"finish heap evidence missing");
 reset(true,1735689600,0,0);disconnect_on_success=true;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_OK,"closed status");
 disconnect_on_success=false;free_reads=largest_reads=0;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture/chunk",token,"SYNTHETIC_PUBLIC_CA","{}",out,sizeof(out),&result)==ESP_OK && result.connected && !result.heap_before_measured && result.heap_measured && largest_reads==1,"automatic reconnect lost post-TLS heap diagnostic");
 reset(true,1735689600,1,8);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA","SYNTHETIC_PRIVATE_BODY",out,sizeof(out),&result)==ESP_FAIL,"original transport error changed");
 CHECK(reads==1,"TLS verification flags were not retrieved before cleanup");
 CHECK(result.status==0 && result.tls_error==0x801a && result.tls_code==0x2700 && result.verify_flags==8,"diagnostic snapshot lost separate HTTP/TLS evidence before cleanup");
 CHECK(mock.nodelay,"connected upload socket retained Nagle batching before headers/body");
 CHECK(result.nodelay && result.connected && result.headers_sent && result.response_started && result.connect_ms==20 && result.headers_ms==21 && result.response_ms==42,"request phase timing or reuse attribution was lost");
 CHECK(result.free_heap==64000 && result.largest_block==48000,"diagnostic snapshot lost the measured internal heap");
 CHECK(performs==1 && cleanups==1 && out[0]==0 && result.length==0,"failure cleanup or no-retry contract changed");
 CHECK(logs==1 && !strcmp(log_tag,"pocket_tls"),"missing single diagnostic record");
 CHECK(!strcmp(diagnostic,"target=1 http=-1 tls=0x801a mbedtls=0x2700 verify=0x8 ca=1 utc_ready=1 free_before=64000 largest_before=48000 free_after=64000 largest_after=48000 heap_before=1"),"diagnostic must contain only the approved numeric fields and booleans");
 reset(false,0,1,0x200);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/windows/status",token,"",NULL,out,sizeof(out),&result)==ESP_FAIL,"bundle failure changed");
 CHECK(!strcmp(diagnostic,"target=0 http=-1 tls=0x801a mbedtls=0x2700 verify=0x200 ca=0 utc_ready=0 free_before=64000 largest_before=48000 free_after=64000 largest_after=48000 heap_before=1"),"target, bundle, or UTC attribution lost");
 reset(true,1735689600,0,0);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_OK,"success changed");
 CHECK(logs==1 && reads==0 && cleanups==0 && result.status==200,"status success must preserve the client and TLS error state");
 CHECK(result.tls_error==0 && result.tls_code==0 && result.verify_flags==0 && result.free_heap==64000,"successful snapshot retained an older TLS failure");
 CHECK(!strcmp(log_tag,"pocket_tls_heap") && !strcmp(diagnostic,"target=1 http=200 free_before=64000 largest_before=48000 free_after=64000 largest_after=48000"),"successful status heap evidence must be numeric-only");
 reset(true,1735689600,0,0);disconnect_on_success=true;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_OK && result.nodelay,"successful closed response lost its own socket option snapshot");
 failure=2;disconnect_on_success=false;
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_FAIL && !result.nodelay && !result.connected,"failed reconnect reported a previous socket's nodelay state");
 pocket_http_close();puts("PASS: numeric-only TLS diagnostics survive cleanup with target/CA/UTC attribution");return 0;
}
'''


QUERY_HARNESS = HARNESS.partition('int main(void) {')[0] + r'''
static const char origin[]="https://synthetic-private-host.invalid";
static const char generation[]="0123456789abcdef0123456789abcdef";
static int accept_route(const char *host,const char *path,const char *body) {
 char out[8192]="old response",expected[600];pocket_http_result result;
 reset(true,1735689600,0,0);
 int error=pocket_http_json(host,path,token,"SYNTHETIC_PUBLIC_CA",body,out,sizeof(out),&result);
 if(error!=ESP_OK){printf("FAIL: accepted route rejected: %s (%d)\n",path,error);return 1;}
 snprintf(expected,sizeof(expected),"%s%s",host,path);
 CHECK(performs==1 && result.performed && result.status==200 && !strcmp(out,"{}"),"accepted route did not perform once");
 CHECK(!strcmp(requested_url,expected),"accepted route URL was altered or truncated");
 CHECK(requested_method==(body?HTTP_METHOD_POST:HTTP_METHOD_GET),"route method changed");
 return 0;
}
static int reject_request(const char *host,const char *path,const char *body,const char *credential) {
 char out[8192]="old response";pocket_http_result result;
 reset(true,1735689600,0,0);memset(&result,0xa5,sizeof(result));
 int error=pocket_http_json(host,path,credential,"SYNTHETIC_PUBLIC_CA",body,out,sizeof(out),&result);
 if(error!=ESP_ERR_INVALID_ARG){printf("FAIL: rejected route accepted: %s (%d)\n",path,error);return 1;}
 CHECK(!performs && !mock.active && !result.performed && !result.status && !result.length && !out[0],"invalid route reached SDK or retained response state");
 return 0;
}
int main(void) {
 const char *valid[]={
  "/targets/linux/tasks/a/result/pages",
  "/targets/windows/tasks/Az09_-/result/pages?cursor=a",
  "/targets/linux/tasks/a/result/pages?cursor=AZaz09_-",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdef",
  "/targets/windows/tasks/Az09_-/speech/v2?generation=00000000000000000000000000000000",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/windows/tasks/Az09_-/speech/v2/segments/11/chunks/156?generation=ffffffffffffffffffffffffffffffff"
 };
 for(size_t i=0;i<sizeof(valid)/sizeof(valid[0]);i++)if(accept_route(origin,valid[i],NULL))return 1;
 CHECK(!accept_route(origin,"/targets/linux/tasks/a/speech/v2","{}"),"bare V2 creation POST changed");
 CHECK(!accept_route(origin,"/targets/windows/tasks/a/speech/v2/cancel","{}"),"bare V2 cancellation POST changed");
 CHECK(!accept_route(origin,"/targets/linux/audio/fixture/chunk","{}"),"legacy POST changed");
 CHECK(!accept_route(origin,"/legacy/unknown/path",NULL),"legacy route validation changed");
 char id[66],cursor[130],path[400],max_origin[194];
 memset(id,'Z',64);id[64]=0;memset(cursor,'_',128);cursor[128]=0;
 snprintf(path,sizeof(path),"/targets/windows/tasks/%s/result/pages?cursor=%s",id,cursor);
 CHECK(strlen(path)==236,"maximum fulltext path fixture length changed");
 memcpy(max_origin,"https://",8);memset(max_origin+8,'h',184);max_origin[192]=0;
 CHECK(!accept_route(max_origin,path,NULL),"64-byte task, 128-byte cursor and 192-byte origin failed");
 snprintf(path,sizeof(path),"/targets/windows/tasks/%s/speech/v2/segments/11/chunks/156?generation=%s",id,generation);
 CHECK(!accept_route(max_origin,path,NULL),"maximum speech route failed");
 const char *invalid[]={
  "/targets/linux/status?cursor=a",
  "/targets/linux/tasks/a/result/pages?",
  "/targets/linux/tasks/a/result/pages?cursor=",
  "/targets/linux/tasks/a/result/pages?page=0",
  "/targets/linux/tasks/a/result/pages?Cursor=a",
  "/targets/linux/tasks/a/result/pages?cursor=a&cursor=b",
  "/targets/linux/tasks/a/result/pages?cursor=a&other=b",
  "/targets/linux/tasks/a/result/pages?cursor=a?cursor=b",
  "/targets/linux/tasks/a/result/pages?cursor=a#fragment",
  "/targets/linux/tasks/a/result/pages?cursor=a=b",
  "/targets/linux/tasks/a/result/pages?cursor=a+b",
  "/targets/linux/tasks/a/result/pages?cursor=a/b",
  "/targets/linux/tasks/a/result/pages?cursor=a.b",
  "/targets/linux/tasks/a/result/pages?cursor=a~b",
  "/targets/linux/tasks/a/result/pages?cursor=a@b",
  "/targets/linux/tasks/a/result/pages?cursor=a:b",
  "/targets/linux/tasks/a/result/pages?cursor=a\\b",
  "/targets/linux/tasks/a/result/pages?cursor=a%2Fb",
  "/targets/linux/tasks/a/result/pages?cursor=a b",
  "/targets/linux/tasks/a/result/pages?cursor=a\tb",
  "/targets/linux/tasks/a/result/pages?cursor=a\x80",
  "/targets/linux/tasks/a/result/pages/?cursor=a",
  "/targets/linux/tasks/a/result/pages/extra?cursor=a",
  "/targets/mac/tasks/a/result/pages?cursor=a",
  "/targets/Linux/tasks/a/result/pages?cursor=a",
  "/targets/linux/tasks//result/pages?cursor=a",
  "/targets/linux/tasks/./result/pages?cursor=a",
  "/targets/linux/tasks/../result/pages?cursor=a",
  "/targets/linux/tasks/a.b/result/pages?cursor=a",
  "/targets/linux/tasks/a@b/result/pages?cursor=a",
  "/targets/linux/tasks/a%2Fb/result/pages?cursor=a",
  "//targets/linux/tasks/a/result/pages?cursor=a",
  "https://other.invalid/targets/linux/tasks/a/result/pages?cursor=a",
  "/targets/linux/tasks/a/speech?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/cancel?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2?generation=",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcde",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdef0",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdeF",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdeg",
  "/targets/linux/tasks/a/speech/v2?Generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdef&generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdef&other=a",
  "/targets/linux/tasks/a/speech/v2?generation=0123456789abcdef0123456789abcdef#fragment",
  "/targets/linux/tasks/a/speech/v2/?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/12/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/157?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/00/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/01/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/01?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/-1/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/+1/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments//chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/%30/chunks/0?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/999999999999999999999999999999999999?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/0/?generation=0123456789abcdef0123456789abcdef",
  "/targets/linux/tasks/a/speech/v2/segments/0/chunks/0?cursor=a"
 };
 for(size_t i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++)if(reject_request(origin,invalid[i],NULL,token))return 1;
 cursor[128]='_';cursor[129]=0;
 snprintf(path,sizeof(path),"/targets/linux/tasks/a/result/pages?cursor=%s",cursor);
 CHECK(!reject_request(origin,path,NULL,token),"129-byte cursor passed");
 cursor[128]=0;id[64]='Z';id[65]=0;
 snprintf(path,sizeof(path),"/targets/linux/tasks/%s/result/pages?cursor=a",id);
 CHECK(!reject_request(origin,path,NULL,token),"65-byte fulltext task ID passed");
 snprintf(path,sizeof(path),"/targets/linux/tasks/%s/speech/v2?generation=%s",id,generation);
 CHECK(!reject_request(origin,path,NULL,token),"65-byte speech task ID passed");
 const char *query_routes[]={valid[1],valid[3],valid[5]};
 for(size_t i=0;i<sizeof(query_routes)/sizeof(query_routes[0]);i++){
  if(reject_request(origin,query_routes[i],"{}",token) || reject_request(origin,query_routes[i],"",token))return 1;
 }
 max_origin[192]='h';max_origin[193]=0;
 CHECK(!reject_request(max_origin,valid[1],NULL,token),"193-byte origin passed");
 CHECK(!reject_request("https://host.invalid@other.invalid",valid[1],NULL,token),"origin authority credentials passed");
 CHECK(!reject_request("https://host.invalid?other=a",valid[1],NULL,token),"origin query passed");
 CHECK(!reject_request("https://host.invalid#fragment",valid[1],NULL,token),"origin fragment passed");
 CHECK(!reject_request("http://host.invalid",valid[1],NULL,token),"non-TLS origin passed");
 CHECK(!reject_request(origin,valid[1],NULL,"short"),"short credential passed");
 CHECK(!reject_request(origin,valid[1],NULL,"SYNTHETIC_PRIVATE_TOKEN_0123456789abcdef\r\n"),"credential header injection passed");
 memset(path,'a',193);path[0]='/';path[192]=0;
 CHECK(!accept_route(origin,path,NULL),"192-byte legacy path changed");
 path[192]='a';path[193]=0;
 CHECK(!reject_request(origin,path,NULL,token),"legacy path limit was widened");
 pocket_http_close();puts("PASS: contract GET queries are exact, bounded, read-only and reach SDK unchanged");return 0;
}
'''


class FirmwareHTTPTests(unittest.TestCase):
    def compile_and_run(self, harness_text):
        compiler = shutil.which('gcc') or shutil.which('clang')
        cached_clang = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'LLVM/bin/clang.exe'
        if not compiler and cached_clang.is_file():
            compiler = str(cached_clang)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1] / 'firmware/main'
        with tempfile.TemporaryDirectory(prefix='http-contract-', dir=source.parents[1]) as temporary:
            temp = pathlib.Path(temporary)
            for name, content in HEADERS.items():
                path=temp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content, encoding='utf-8')
            harness = temp / 'test_http.c'
            harness.write_text(harness_text, encoding='utf-8')
            binary = temp / ('test-http.exe' if os.name == 'nt' else 'test-http')
            environment = os.environ.copy()
            environment.update(TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
            compile_result = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I'+str(temp), '-I'+str(source), str(source/'pocket_http.c'), str(harness), '-o', str(binary)], capture_output=True, text=True, env=environment, timeout=60)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10, env=environment)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS:', run.stdout)

    def test_failure_diagnostics_are_safe_and_survive_cleanup(self):
        self.compile_and_run(HARNESS)

    def test_contract_get_queries_are_exact_bounded_and_read_only(self):
        self.compile_and_run(QUERY_HARNESS)
