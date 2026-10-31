"""Real delivered setup/NVS adapter with synthetic SDK boundaries; no hardware.

Regression: Linux must select the explicitly compiled public endpoint while the
original Wi-Fi, pairing, Windows endpoint and private CA blob remain byte-identical.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HEADERS = {
    'esp_err.h': r'''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_WIFI_NOT_CONNECT 0x300f
#define ESP_ERR_WIFI_NOT_STARTED 0x3002
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define ESP_ERROR_CHECK(call) do { if ((call)!=ESP_OK) abort(); } while (0)
''',
    'esp_log.h': r'''#pragma once
void test_log(const char *,const char *,...);
#define ESP_LOGI(tag,...) test_log(tag,__VA_ARGS__)
#define ESP_LOGW(tag,...) test_log(tag,__VA_ARGS__)
#define ESP_LOGE(tag,...) test_log(tag,__VA_ARGS__)
''',
    'esp_random.h': '#pragma once\n#include <stddef.h>\nvoid esp_fill_random(void *,size_t);\n',
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
    'esp_system.h': '#pragma once\nvoid esp_restart(void);\n',
    'nvs_flash.h': '#pragma once\n#include "esp_err.h"\nesp_err_t nvs_flash_init(void);\n',
    'nvs.h': r'''#pragma once
#include <stddef.h>
#include "esp_err.h"
typedef unsigned nvs_handle_t;
#define NVS_READONLY 0
#define NVS_READWRITE 1
esp_err_t nvs_open(const char *,int,nvs_handle_t *);
esp_err_t nvs_get_blob(nvs_handle_t,const char *,void *,size_t *);
esp_err_t nvs_set_blob(nvs_handle_t,const char *,const void *,size_t);
esp_err_t nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
''',
    'freertos/FreeRTOS.h': r'''#pragma once
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
    'freertos/task.h': r'''#pragma once
#include "FreeRTOS.h"
typedef void *TaskHandle_t;
BaseType_t xTaskCreate(void (*)(void *),const char *,unsigned,void *,unsigned,TaskHandle_t *);
void vTaskDelete(TaskHandle_t);
void vTaskDelay(TickType_t);
unsigned ulTaskNotifyTake(BaseType_t,TickType_t);
void xTaskNotifyGive(TaskHandle_t);
''',
    'freertos/queue.h': r'''#pragma once
#include "FreeRTOS.h"
typedef void *QueueHandle_t;
QueueHandle_t xQueueCreate(unsigned,unsigned);
BaseType_t xQueueSend(QueueHandle_t,const void *,TickType_t);
BaseType_t xQueueReceive(QueueHandle_t,void *,TickType_t);
void xQueueReset(QueueHandle_t);
unsigned uxQueueMessagesWaiting(QueueHandle_t);
void vQueueDelete(QueueHandle_t);
''',
    'esp_event.h': r'''#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef const char *esp_event_base_t;
extern const char test_wifi_base,test_ip_base;
#define WIFI_EVENT (&test_wifi_base)
#define IP_EVENT (&test_ip_base)
#define ESP_EVENT_ANY_ID (-1)
#define WIFI_EVENT_STA_START 1
#define WIFI_EVENT_STA_CONNECTED 2
#define WIFI_EVENT_STA_DISCONNECTED 3
#define WIFI_EVENT_SCAN_DONE 4
#define IP_EVENT_STA_GOT_IP 5
#define IP_EVENT_STA_LOST_IP 6
esp_err_t esp_event_loop_create_default(void);
esp_err_t esp_event_handler_register(esp_event_base_t,int32_t,void (*)(void *,esp_event_base_t,int32_t,void *),void *);
''',
    'esp_netif.h': r'''#pragma once
#include "esp_err.h"
typedef struct { int unused; } esp_netif_t;
typedef struct { struct { unsigned addr; } ip,netmask,gw; } esp_netif_ip_info_t;
esp_err_t esp_netif_init(void);
esp_netif_t *esp_netif_create_default_wifi_sta(void);
esp_netif_t *esp_netif_create_default_wifi_ap(void);
''',
    'esp_wifi.h': r'''#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
typedef enum {
 WIFI_AUTH_OPEN,WIFI_AUTH_WEP,WIFI_AUTH_WPA_PSK,WIFI_AUTH_WPA2_PSK,WIFI_AUTH_WPA_WPA2_PSK,
 WIFI_AUTH_ENTERPRISE,WIFI_AUTH_WPA2_ENTERPRISE=WIFI_AUTH_ENTERPRISE,WIFI_AUTH_WPA3_PSK,
 WIFI_AUTH_WPA2_WPA3_PSK,WIFI_AUTH_WAPI_PSK,WIFI_AUTH_OWE,WIFI_AUTH_WPA3_ENT_192,
 WIFI_AUTH_WPA3_EXT_PSK,WIFI_AUTH_WPA3_EXT_PSK_MIXED_MODE,WIFI_AUTH_DPP,
 WIFI_AUTH_WPA3_ENTERPRISE,WIFI_AUTH_WPA2_WPA3_ENTERPRISE,WIFI_AUTH_WPA_ENTERPRISE,WIFI_AUTH_MAX
} wifi_auth_mode_t;
typedef enum { WIFI_MODE_NULL,WIFI_MODE_STA,WIFI_MODE_AP,WIFI_MODE_APSTA } wifi_mode_t;
typedef enum { WIFI_IF_STA,WIFI_IF_AP } wifi_interface_t;
typedef struct { bool capable,required; } wifi_pmf_config_t;
typedef union {
 struct { uint8_t ssid[32],password[64];struct { wifi_auth_mode_t authmode; } threshold;wifi_pmf_config_t pmf_cfg; } sta;
 struct { uint8_t ssid[32],password[64];uint8_t ssid_len,channel,max_connection;wifi_auth_mode_t authmode;wifi_pmf_config_t pmf_cfg; } ap;
} wifi_config_t;
typedef struct { int unused; } wifi_init_config_t;
typedef struct { int scan_type;struct { struct { unsigned min,max; } active;unsigned passive; } scan_time; } wifi_scan_config_t;
typedef struct { uint8_t ssid[33];int8_t rssi;wifi_auth_mode_t authmode; } wifi_ap_record_t;
typedef struct { uint8_t ssid[32],ssid_len,bssid[6];uint8_t channel;wifi_auth_mode_t authmode; } wifi_event_sta_connected_t;
typedef struct { uint8_t ssid[32],ssid_len,bssid[6],reason;int8_t rssi; } wifi_event_sta_disconnected_t;
#define WIFI_INIT_CONFIG_DEFAULT() ((wifi_init_config_t){0})
#define WIFI_STORAGE_RAM 1
#define WIFI_SCAN_TYPE_ACTIVE 1
#define WIFI_REASON_NO_AP_FOUND 201
#define WIFI_REASON_AUTH_FAIL 202
#define WIFI_REASON_HANDSHAKE_TIMEOUT 204
#define WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT 15
#define WIFI_REASON_AUTH_EXPIRE 2
#define WIFI_REASON_ASSOC_FAIL 203
#define WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY 210
#define WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD 211
#define WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD 212
esp_err_t esp_wifi_init(const wifi_init_config_t *);
esp_err_t esp_wifi_set_storage(int);
esp_err_t esp_wifi_set_mode(wifi_mode_t);
esp_err_t esp_wifi_set_config(wifi_interface_t,const wifi_config_t *);
esp_err_t esp_wifi_start(void);
esp_err_t esp_wifi_connect(void);
esp_err_t esp_wifi_disconnect(void);
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *);
esp_err_t esp_wifi_scan_start(const wifi_scan_config_t *,bool);
esp_err_t esp_wifi_scan_get_ap_num(uint16_t *);
esp_err_t esp_wifi_scan_get_ap_records(uint16_t *,wifi_ap_record_t *);
esp_err_t esp_wifi_clear_ap_list(void);
''',
    'lwip/sockets.h': r'''#pragma once
#include <stddef.h>
#include <stdint.h>
typedef unsigned socklen_t;
#define AF_INET 2
#define AF_INET6 10
struct sockaddr { uint16_t sa_family;char sa_data[14]; };
struct in_addr { uint32_t s_addr; };
struct in6_addr { unsigned char s6_addr[16]; };
struct sockaddr_in { uint16_t sin_family,sin_port;struct in_addr sin_addr;unsigned char sin_zero[8]; };
struct sockaddr_in6 { uint16_t sin6_family,sin6_port;uint32_t sin6_flowinfo;struct in6_addr sin6_addr;uint32_t sin6_scope_id; };
struct sockaddr_storage { uint16_t ss_family;unsigned char padding[126]; };
static inline uint32_t htonl(uint32_t n) { const uint16_t x=1;return *(const unsigned char *)&x?(n<<24)|((n&0xff00)<<8)|((n>>8)&0xff00)|(n>>24):n; }
int getsockname(int,struct sockaddr *,socklen_t *);
''',
    'esp_http_server.h': r'''#pragma once
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"
typedef void *httpd_handle_t;
typedef struct httpd_req {
 const char *uri;int content_len;const char *body,*request_nonce,*content_type;
 size_t received;int socket_kind;
} httpd_req_t;
typedef esp_err_t (*httpd_uri_func)(httpd_req_t *);
typedef struct { const char *uri;int method;httpd_uri_func handler;void *user_ctx; } httpd_uri_t;
typedef struct { unsigned max_open_sockets,recv_wait_timeout,send_wait_timeout,stack_size,max_uri_handlers;bool lru_purge_enable; } httpd_config_t;
#define HTTPD_DEFAULT_CONFIG() ((httpd_config_t){0})
#define HTTP_GET 0
#define HTTP_POST 1
#define HTTPD_400_BAD_REQUEST 400
#define HTTPD_403_FORBIDDEN 403
#define HTTPD_404_NOT_FOUND 404
#define HTTPD_408_REQ_TIMEOUT 408
#define HTTPD_409_CONFLICT 409
#define HTTPD_500_INTERNAL_SERVER_ERROR 500
#define HTTPD_503_SERVICE_UNAVAILABLE 503
#define HTTPD_RESP_USE_STRLEN (-1)
#define HTTPD_SOCK_ERR_TIMEOUT (-3)
esp_err_t httpd_start(httpd_handle_t *,const httpd_config_t *);
esp_err_t httpd_stop(httpd_handle_t);
esp_err_t httpd_register_uri_handler(httpd_handle_t,const httpd_uri_t *);
int httpd_req_to_sockfd(httpd_req_t *);
size_t httpd_req_get_hdr_value_len(httpd_req_t *,const char *);
esp_err_t httpd_req_get_hdr_value_str(httpd_req_t *,const char *,char *,size_t);
int httpd_req_recv(httpd_req_t *,char *,size_t);
esp_err_t httpd_resp_set_status(httpd_req_t *,const char *);
esp_err_t httpd_resp_set_type(httpd_req_t *,const char *);
esp_err_t httpd_resp_set_hdr(httpd_req_t *,const char *,const char *);
esp_err_t httpd_resp_send(httpd_req_t *,const char *,int);
esp_err_t httpd_resp_sendstr(httpd_req_t *,const char *);
esp_err_t httpd_resp_send_chunk(httpd_req_t *,const char *,int);
esp_err_t httpd_resp_send_err(httpd_req_t *,int,const char *);
''',
}

from test_firmware_setup import HEADERS as SETUP_HEADERS
HEADERS.update(SETUP_HEADERS)

HARNESS = r'''
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pocket_setup.c"
#include "nvs.h"
typedef pocket_pairing_config stored_config;
const char test_wifi_base=1,test_ip_base=2;
static unsigned char legacy_blob[4096],original_blob[4096],override_blob[2048];
static size_t legacy_size,original_size,override_size;
static unsigned legacy_writes,override_writes,overrides_opened,ap_starts,notifications;
static bool fail_open,fail_set,fail_commit,retain_failed_commit,corrupt_readback;
static wifi_config_t station_config;
static char logs[4096];
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)

void test_log(const char *tag,const char *fmt,...){
 size_t n=strlen(logs);if(n+2>=sizeof logs)return;
 int used=snprintf(logs+n,sizeof logs-n,"%s ",tag);if(used<0)return;
 n=strlen(logs);va_list args;va_start(args,fmt);vsnprintf(logs+n,sizeof logs-n,fmt,args);va_end(args);
}
esp_err_t nvs_flash_init(void){return ESP_OK;}
esp_err_t nvs_open(const char *name,int mode,nvs_handle_t *out){
 if(!strcmp(name,"pocket")){if(mode!=NVS_READONLY)legacy_writes++;*out=1;return legacy_size?ESP_OK:ESP_ERR_NVS_NOT_FOUND;}
 if(!strcmp(name,"pocket_wifi"))return ESP_ERR_NVS_NOT_FOUND;
 overrides_opened++;if(fail_open)return ESP_FAIL;
 if(strcmp(name,"pocket_link"))return ESP_FAIL;
 if(!override_size&&mode==NVS_READONLY)return ESP_ERR_NVS_NOT_FOUND;
 *out=2;return ESP_OK;
}
esp_err_t nvs_get_blob(nvs_handle_t n,const char *key,void *out,size_t *size){
 const unsigned char *blob=n==1?legacy_blob:override_blob;size_t count=n==1?legacy_size:override_size;
 if(strcmp(key,n==1?"config":"linux_public"))return ESP_ERR_NVS_NOT_FOUND;
 if(!count)return ESP_ERR_NVS_NOT_FOUND;
 if(!out){*size=count;return ESP_OK;}
 if(*size<count)return ESP_FAIL;
 memcpy(out,blob,count);*size=count;return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t n,const char *key,const void *data,size_t size){
 if(n==1){legacy_writes++;return ESP_FAIL;}
 if(n!=2||strcmp(key,"linux_public")||size>sizeof override_blob)return ESP_FAIL;
 override_writes++;if(fail_set)return ESP_FAIL;
 memcpy(override_blob,data,size);override_size=size;
 if(corrupt_readback)override_blob[0]^=0xff;
 return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t n){(void)n;if(fail_commit){if(!retain_failed_commit)override_size=0;return ESP_FAIL;}return ESP_OK;}
void nvs_close(nvs_handle_t n){(void)n;}
esp_err_t esp_netif_init(void){return ESP_OK;}
static esp_netif_t netif;
esp_netif_t *esp_netif_create_default_wifi_sta(void){return &netif;}
esp_netif_t *esp_netif_create_default_wifi_ap(void){return &netif;}
esp_err_t esp_event_loop_create_default(void){return ESP_OK;}
esp_err_t esp_event_handler_register(esp_event_base_t b,int32_t i,void(*fn)(void*,esp_event_base_t,int32_t,void*),void *a){(void)b;(void)i;(void)fn;(void)a;return ESP_OK;}
esp_err_t esp_wifi_init(const wifi_init_config_t *c){(void)c;return ESP_OK;}
esp_err_t esp_wifi_set_storage(int n){(void)n;return ESP_OK;}
esp_err_t esp_wifi_set_mode(wifi_mode_t m){if(m==WIFI_MODE_APSTA)ap_starts++;return ESP_OK;}
esp_err_t esp_wifi_set_config(wifi_interface_t i,const wifi_config_t *c){if(i==WIFI_IF_STA)station_config=*c;return ESP_OK;}
esp_err_t esp_wifi_start(void){return ESP_OK;}
esp_err_t esp_wifi_connect(void){return ESP_OK;}
esp_err_t esp_wifi_scan_start(const wifi_scan_config_t *c,bool b){(void)c;(void)b;return ESP_OK;}
esp_err_t esp_wifi_scan_get_ap_num(uint16_t *n){*n=0;return ESP_OK;}
esp_err_t esp_wifi_scan_get_ap_records(uint16_t *n,wifi_ap_record_t *r){(void)r;*n=0;return ESP_OK;}
esp_err_t esp_wifi_clear_ap_list(void){return ESP_OK;}
BaseType_t xTaskCreate(void(*fn)(void*),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *out){(void)fn;(void)name;(void)stack;(void)arg;(void)priority;*out=(void*)1;return pdPASS;}
void vTaskDelay(TickType_t n){(void)n;}
unsigned ulTaskNotifyTake(BaseType_t b,TickType_t t){(void)b;(void)t;return 0;}
void xTaskNotifyGive(TaskHandle_t t){(void)t;notifications++;}
int64_t esp_timer_get_time(void){return 0;}
void esp_restart(void){abort();}
void esp_fill_random(void *out,size_t n){memset(out,3,n);}
int getsockname(int fd,struct sockaddr *a,socklen_t *n){(void)fd;(void)a;(void)n;return -1;}
esp_err_t httpd_start(httpd_handle_t *h,const httpd_config_t *c){(void)h;(void)c;return ESP_FAIL;}
esp_err_t httpd_stop(httpd_handle_t h){(void)h;return ESP_OK;}
esp_err_t httpd_register_uri_handler(httpd_handle_t h,const httpd_uri_t *u){(void)h;(void)u;return ESP_OK;}
int httpd_req_to_sockfd(httpd_req_t *r){(void)r;return 0;}
esp_err_t httpd_req_get_hdr_value_str(httpd_req_t *r,const char *k,char *out,size_t n){(void)r;(void)k;(void)out;(void)n;return ESP_FAIL;}
int httpd_req_recv(httpd_req_t *r,char *out,size_t n){(void)r;(void)out;(void)n;return -1;}
esp_err_t httpd_resp_set_type(httpd_req_t *r,const char *v){(void)r;(void)v;return ESP_OK;}
esp_err_t httpd_resp_set_hdr(httpd_req_t *r,const char *k,const char *v){(void)r;(void)k;(void)v;return ESP_OK;}
esp_err_t httpd_resp_send(httpd_req_t *r,const char *v,int n){(void)r;(void)v;(void)n;return ESP_OK;}
esp_err_t httpd_resp_sendstr(httpd_req_t *r,const char *v){(void)r;(void)v;return ESP_OK;}
esp_err_t httpd_resp_send_chunk(httpd_req_t *r,const char *v,int n){(void)r;(void)v;(void)n;return ESP_OK;}
esp_err_t httpd_resp_send_err(httpd_req_t *r,int e,const char *v){(void)r;(void)e;(void)v;return ESP_FAIL;}

esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *out){memset(out,0,sizeof *out);return ESP_ERR_WIFI_NOT_CONNECT;}
esp_err_t esp_wifi_disconnect(void){return ESP_OK;}
void vTaskDelete(TaskHandle_t t){(void)t;}
QueueHandle_t xQueueCreate(unsigned n,unsigned size){(void)n;(void)size;return (void*)1;}
BaseType_t xQueueSend(QueueHandle_t q,const void *v,TickType_t t){(void)q;(void)v;(void)t;return pdTRUE;}
BaseType_t xQueueReceive(QueueHandle_t q,void *v,TickType_t t){(void)q;(void)v;(void)t;return pdFALSE;}
void xQueueReset(QueueHandle_t q){(void)q;}
unsigned uxQueueMessagesWaiting(QueueHandle_t q){(void)q;return 0;}
void vQueueDelete(QueueHandle_t q){(void)q;}
size_t httpd_req_get_hdr_value_len(httpd_req_t *r,const char *k){(void)r;(void)k;return 0;}
esp_err_t httpd_resp_set_status(httpd_req_t *r,const char *s){(void)r;(void)s;return ESP_OK;}

static stored_config fixture(void){
 stored_config c={.version=2};snprintf(c.ssid,sizeof c.ssid,"Synthetic WiFi");snprintf(c.pass,sizeof c.pass,"synthetic-wifi-password");
 snprintf(c.windows,sizeof c.windows,"https://windows.example.invalid:8443");snprintf(c.linux_url,sizeof c.linux_url,"https://old-linux.example.invalid:8443");
 memset(c.token,'s',64);snprintf(c.ca,sizeof c.ca,"synthetic-private-CA-fixture");return c;
}
static void seed(stored_config c){legacy_size=sizeof c;memcpy(legacy_blob,&c,sizeof c);original_size=legacy_size;memcpy(original_blob,legacy_blob,legacy_size);}
static bool preserved(void){return !legacy_writes&&legacy_size==original_size&&!memcmp(legacy_blob,original_blob,legacy_size);}
static bool safe_logs(const stored_config *c){return (!c->pass[0]||!strstr(logs,c->pass))&&(!c->token[0]||!strstr(logs,c->token))&&(!c->ca[0]||!strstr(logs,c->ca))&&(!c->windows[0]||!strstr(logs,c->windows))&&(!c->linux_url[0]||!strstr(logs,c->linux_url));}
int main(int argc,char **argv){
 CHECK(argc==2,"missing scenario");const char *which=argv[1];stored_config c=fixture();seed(c);
 if(!strcmp(which,"no-pairing")){c.token[0]=0;seed(c);}
 if(!strcmp(which,"invalid-config")){legacy_size=17;original_size=17;memcpy(original_blob,legacy_blob,17);}
 fail_open=!strcmp(which,"open-failure");fail_set=!strcmp(which,"set-failure");
 retain_failed_commit=!strcmp(which,"commit-failure-retained");
 fail_commit=!strcmp(which,"commit-failure")||retain_failed_commit;corrupt_readback=!strcmp(which,"readback-failure");
 CHECK(pocket_setup_init()==ESP_OK,"baseline initialization changed");
 work_step(false,1000);
 pocket_link_config win,linux_link;bool win_ok=pocket_setup_link(0,&win),linux_ok=pocket_setup_link(1,&linux_link);
 CHECK(preserved(),"legacy settings record was rewritten");
 CHECK(safe_logs(&c),"saved secret/endpoint reached diagnostics");
 CHECK(!ap_starts,"migration opened provisioning");
 if(!strcmp(which,"no-pairing")||!strcmp(which,"invalid-config")){CHECK(!override_writes&&!win_ok&&!linux_ok,"unpaired/invalid config migrated");puts("PASS");return 0;}
 CHECK(win_ok&&!strcmp(win.origin,c.windows)&&!strcmp(win.token,c.token)&&!strcmp(win.ca,c.ca),"Windows configuration or trust changed");
 CHECK(!strcmp((char*)station_config.sta.ssid,c.ssid)&&!strcmp((char*)station_config.sta.password,c.pass),"WiFi credentials changed");
 if(fail_open||fail_set||(fail_commit&&!retain_failed_commit)||corrupt_readback){
  CHECK(linux_ok&&!strcmp(linux_link.origin,c.linux_url)&&!strcmp(linux_link.ca,c.ca),"failed override selected partial/public trust");puts("PASS");return 0;
 }
 CHECK(linux_ok&&!strcmp(linux_link.origin,POCKET_LINUX_PUBLIC_ORIGIN),"approved Linux public endpoint was not selected");
 CHECK(!strcmp(linux_link.token,c.token)&&!linux_link.ca[0],"Linux did not preserve pairing and select public CA");
 CHECK(override_writes==1,"override was not one bounded write");
 if(retain_failed_commit)CHECK(strstr(logs,"result=-1"),"commit failure was not reported");
 if(!strcmp(which,"blank-pairing")){CHECK(pocket_pairing_update("","","","")==ESP_OK&&preserved(),"blank pairing fields rewrote original settings");}
 if(!strcmp(which,"idempotent")){CHECK(pocket_setup_init()==ESP_OK,"second boot failed");CHECK(override_writes==1&&preserved(),"boot rewrote the one-time override or legacy record");}
 if(!strcmp(which,"windows-independent")){unsigned n=overrides_opened;CHECK(pocket_setup_link(0,&win)&&overrides_opened==n,"Windows read the Linux-only trust store");}
 if(!strcmp(which,"manual-retarget")){
  snprintf(c.linux_url,sizeof c.linux_url,"https://new-linux.example.invalid:8443");seed(c);
  CHECK(pocket_setup_init()==ESP_OK&&override_writes==1,"boot overrode an explicit legacy retarget");
  CHECK(pocket_setup_link(1,&linux_link)&&!strcmp(linux_link.origin,c.linux_url)&&!strcmp(linux_link.ca,c.ca)&&preserved(),"old override ignored explicit retarget");
 }
 if(!strcmp(which,"corrupt-stored")){
  override_blob[0]^=0xff;
  CHECK(pocket_setup_link(1,&linux_link)&&!strcmp(linux_link.origin,c.linux_url)&&!strcmp(linux_link.ca,c.ca),"corrupt override changed trust/endpoint");
  CHECK(pocket_setup_init()==ESP_OK&&override_writes==1&&preserved(),"corrupt record was silently replaced");
 }
 CHECK(safe_logs(&c)&&preserved(),"preservation/logging failed");puts("PASS");return 0;
}
'''

class LinuxPublicLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=pathlib.Path(__file__).resolve().parents[1]
        compiler=os.environ.get('POCKET_HOST_CC') or shutil.which('clang') or shutil.which('gcc')
        if not compiler:raise unittest.SkipTest('host C compiler unavailable')
        sdk=pathlib.Path(os.environ.get('IDF_PATH',str(cls.root.parent/'HPsdk/idf-5.5.3')))
        cjson=sdk/'components/json/cJSON'
        if not (cjson/'cJSON.c').is_file():raise unittest.SkipTest('cached SDK cJSON required')
        cls.temp=tempfile.TemporaryDirectory(prefix='pocket-linux-link-')
        cls.addClassCleanup(cls.temp.cleanup)
        temp=pathlib.Path(cls.temp.name)
        for name,value in HEADERS.items():
            path=temp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8')
        fixture=temp/'harness.c';fixture.write_text(HARNESS,encoding='utf-8')
        cls.binary=temp/('link.exe' if os.name=='nt' else 'link')
        module=cls.root/'firmware/main/pocket_linux_link.c'
        files=[str(fixture),str(cjson/'cJSON.c'),str(cls.root/'firmware/core/pocket_wifi.c'),str(cls.root/'firmware/main/pocket_pairing.c'),str(cls.root/'firmware/main/pocket_wifi_store.c')]+([str(module)] if module.is_file() else [])
        command=[compiler,'-std=c11','-Wall','-Wextra','-Werror','-Wno-overlength-strings','-DESP_PLATFORM=1',
                 '-DPOCKET_LINUX_PUBLIC_ORIGIN="https://bridge.example.invalid:10000"',
                 '-I'+str(temp),'-I'+str(cls.root/'firmware/main'),'-I'+str(cls.root/'firmware/core'),'-I'+str(cjson),
                 *files,'-o',str(cls.binary)]
        built=subprocess.run(command,capture_output=True,text=True,timeout=60)
        if built.returncode:raise AssertionError(built.stdout+built.stderr)
    def exercise(self,scenario):
        result=subprocess.run([str(self.binary),scenario],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_linux_override_preserves_original_settings(self):self.exercise('preserve')
    def test_blank_pairing_preserves_funnel_and_original_secrets(self):self.exercise('blank-pairing')
    def test_boot_is_idempotent(self):self.exercise('idempotent')
    def test_windows_ignores_linux_trust_store(self):self.exercise('windows-independent')
    def test_unpaired_configuration_is_not_migrated(self):self.exercise('no-pairing')
    def test_invalid_legacy_configuration_is_not_migrated(self):self.exercise('invalid-config')
    def test_open_failure_preserves_original_behavior(self):self.exercise('open-failure')
    def test_write_failure_preserves_original_behavior(self):self.exercise('set-failure')
    def test_commit_failure_without_record_preserves_original_behavior(self):self.exercise('commit-failure')
    def test_commit_failure_with_complete_record_keeps_endpoint_and_trust_coherent(self):self.exercise('commit-failure-retained')
    def test_corrupt_readback_preserves_original_trust(self):self.exercise('readback-failure')
    def test_explicit_legacy_retarget_disables_old_override(self):self.exercise('manual-retarget')
    def test_corrupt_stored_override_is_not_replaced(self):self.exercise('corrupt-stored')
