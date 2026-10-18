"""Real setup worker/core/cJSON with synthetic RTOS, HTTP and radio boundaries.

The socket boundary is an in-memory fake: this test never opens a socket, touches
NVS, connects Wi-Fi, or accesses a device. All credentials are synthetic fixtures.
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


HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pocket_wifi_store.h"
#include "pocket_pairing.h"
#include "freertos/queue.h"
#include "esp_http_server.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs.h"
#include "lwip/sockets.h"
#include "cJSON.h"
#ifndef SETUP_SOURCE
#define SETUP_SOURCE "pocket_setup.c"
#endif
#include SETUP_SOURCE

const char test_wifi_base=1,test_ip_base=2;
static unsigned notifications,ap_starts,http_starts,http_stops,radio_calls;
static unsigned connects,disconnects,scans,profile_saves,pair_updates,legacy_reads,body_reads;
static bool initialized,in_worker,association_live,save_ok=true,queue_full,disconnect_not_connect_while_idle;
static esp_err_t ap_query_error=ESP_OK;
static int64_t clock_us=1000000;
static int64_t expire_on_receive;
static wifi_config_t applied_ap,applied_sta;
static wifi_ap_record_t scan_records[20];static uint16_t scan_count;
static pocket_wifi_book loaded_book,persisted_book;
static pocket_wifi_store_result load_result=POCKET_WIFI_STORE_ABSENT;
static pocket_pairing_config pair_config;
static bool legacy_available;
static char logs[4096];static unsigned log_used;
static char response[65536],response_type[64];static int response_status=200;
static unsigned response_used,route_count;
static httpd_uri_t routes[12];
static struct fake_queue { unsigned capacity,size,head,count;unsigned char data[65536]; } queue_data;

void test_log(const char *tag,const char *fmt,...) {
 (void)tag;if(log_used>=sizeof logs-1)return;
 va_list args;va_start(args,fmt);int n=vsnprintf(logs+log_used,sizeof logs-log_used,fmt,args);va_end(args);
 if(n>0)log_used+=(unsigned)n<sizeof logs-log_used?(unsigned)n:(unsigned)(sizeof logs-log_used-1);
}
void esp_fill_random(void *out,size_t size) { unsigned char *bytes=out;for(size_t i=0;i<size;i++)bytes[i]=(unsigned char)(i+17); }
int64_t esp_timer_get_time(void) { return clock_us; }
void esp_restart(void) { abort(); }
esp_err_t nvs_flash_init(void) { return ESP_OK; }
esp_err_t nvs_open(const char *name,int mode,nvs_handle_t *out) { (void)name;(void)mode;(void)out;return ESP_ERR_NVS_NOT_FOUND; }
esp_err_t nvs_get_blob(nvs_handle_t h,const char *key,void *out,size_t *size) { (void)h;(void)key;(void)out;(void)size;return ESP_ERR_NVS_NOT_FOUND; }
esp_err_t nvs_set_blob(nvs_handle_t h,const char *key,const void *data,size_t size) { (void)h;(void)key;(void)data;(void)size;abort(); }
esp_err_t nvs_commit(nvs_handle_t h) { (void)h;abort(); }
void nvs_close(nvs_handle_t h) { (void)h; }
pocket_wifi_store_result pocket_wifi_store_load(pocket_wifi_store *s,pocket_wifi_book *b) {
 s->slot=-1;s->writable=load_result==POCKET_WIFI_STORE_OK||load_result==POCKET_WIFI_STORE_ABSENT;
 *b=loaded_book;return load_result;
}
esp_err_t pocket_wifi_store_save(pocket_wifi_store *s,const pocket_wifi_book *b) {
 assert(in_worker);profile_saves++;if(!save_ok||!s->writable)return ESP_FAIL;persisted_book=*b;return ESP_OK;
}
esp_err_t pocket_pairing_load(pocket_pairing_config *out) { *out=pair_config;return ESP_OK; }
bool pocket_pairing_legacy_wifi(pocket_wifi_profile *out) {
 legacy_reads++;memset(out,0,sizeof *out);if(!legacy_available)return false;
 snprintf(out->ssid,sizeof out->ssid,"Synthetic Legacy");snprintf(out->password,sizeof out->password,"legacy-secret-fixture");return true;
}
esp_err_t pocket_pairing_update(const char *win,const char *linux_url,const char *token,const char *ca) {
 pair_updates++;
 if(*win)snprintf(pair_config.windows,sizeof pair_config.windows,"%s",win);
 if(*linux_url)snprintf(pair_config.linux_url,sizeof pair_config.linux_url,"%s",linux_url);
 if(*token)snprintf(pair_config.token,sizeof pair_config.token,"%s",token);
 if(*ca)snprintf(pair_config.ca,sizeof pair_config.ca,"%s",ca);
 return ESP_OK;
}
esp_err_t esp_netif_init(void) { return ESP_OK; }
esp_netif_t *esp_netif_create_default_wifi_sta(void) { static esp_netif_t s;return &s; }
esp_netif_t *esp_netif_create_default_wifi_ap(void) { static esp_netif_t a;return &a; }
esp_err_t esp_event_loop_create_default(void) { return ESP_OK; }
esp_err_t esp_event_handler_register(esp_event_base_t b,int32_t id,void (*fn)(void *,esp_event_base_t,int32_t,void *),void *arg) { (void)b;(void)id;(void)fn;(void)arg;return ESP_OK; }
static void radio_call(void) { assert(!initialized||in_worker);radio_calls++; }
esp_err_t esp_wifi_init(const wifi_init_config_t *c) { (void)c;radio_call();return ESP_OK; }
esp_err_t esp_wifi_set_storage(int storage) { assert(storage==WIFI_STORAGE_RAM);radio_call();return ESP_OK; }
esp_err_t esp_wifi_set_mode(wifi_mode_t mode) { radio_call();if(mode==WIFI_MODE_APSTA)ap_starts++;return ESP_OK; }
esp_err_t esp_wifi_set_config(wifi_interface_t interface,const wifi_config_t *c) { radio_call();if(interface==WIFI_IF_AP)applied_ap=*c;else applied_sta=*c;return ESP_OK; }
esp_err_t esp_wifi_start(void) { radio_call();return ESP_OK; }
esp_err_t esp_wifi_connect(void) { radio_call();connects++;return ESP_OK; }
/* IDF may return ESP_OK while already idle, without a disconnect callback. */
esp_err_t esp_wifi_disconnect(void) { radio_call();disconnects++;return !association_live&&disconnect_not_connect_while_idle?ESP_ERR_WIFI_NOT_CONNECT:ESP_OK; }
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *out) { radio_call();if(ap_query_error!=ESP_OK)return ap_query_error;if(!association_live)return ESP_ERR_WIFI_NOT_CONNECT;memset(out,0,sizeof *out);memcpy(out->ssid,applied_sta.sta.ssid,32);return ESP_OK; }
esp_err_t esp_wifi_scan_start(const wifi_scan_config_t *c,bool block) { (void)c;(void)block;radio_call();scans++;return ESP_OK; }
esp_err_t esp_wifi_scan_get_ap_num(uint16_t *out) { radio_call();*out=scan_count;return ESP_OK; }
esp_err_t esp_wifi_scan_get_ap_records(uint16_t *count,wifi_ap_record_t *out) { radio_call();if(*count>scan_count)*count=scan_count;memcpy(out,scan_records,*count*sizeof *out);return ESP_OK; }
esp_err_t esp_wifi_clear_ap_list(void) { radio_call();return ESP_OK; }
BaseType_t xTaskCreate(void (*fn)(void *),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *out) {
 (void)fn;(void)name;(void)stack;(void)arg;(void)priority;if(out)*out=(void *)&queue_data;return pdPASS;
}
void vTaskDelete(TaskHandle_t task) { (void)task; }
void vTaskDelay(TickType_t ticks) { (void)ticks; }
unsigned ulTaskNotifyTake(BaseType_t clear,TickType_t ticks) { (void)clear;(void)ticks;return 0; }
void xTaskNotifyGive(TaskHandle_t task) { assert(task);notifications++; }
QueueHandle_t xQueueCreate(unsigned capacity,unsigned size) {
 assert(capacity&&size&&capacity*size<=sizeof queue_data.data);memset(&queue_data,0,sizeof queue_data);queue_data.capacity=capacity;queue_data.size=size;return &queue_data;
}
BaseType_t xQueueSend(QueueHandle_t handle,const void *value,TickType_t ticks) {
 (void)ticks;assert(handle==&queue_data);if(queue_full||queue_data.count==queue_data.capacity)return pdFALSE;
 unsigned tail=(queue_data.head+queue_data.count)%queue_data.capacity;memcpy(queue_data.data+tail*queue_data.size,value,queue_data.size);queue_data.count++;return pdTRUE;
}
BaseType_t xQueueReceive(QueueHandle_t handle,void *out,TickType_t ticks) {
 (void)ticks;assert(handle==&queue_data);if(!queue_data.count)return pdFALSE;
 memcpy(out,queue_data.data+queue_data.head*queue_data.size,queue_data.size);
 queue_data.head=(queue_data.head+1)%queue_data.capacity;queue_data.count--;return pdTRUE;
}
void xQueueReset(QueueHandle_t handle) { assert(handle==&queue_data);queue_data.head=queue_data.count=0; }
unsigned uxQueueMessagesWaiting(QueueHandle_t handle) { assert(handle==&queue_data);return queue_data.count; }
void vQueueDelete(QueueHandle_t handle) { xQueueReset(handle); }
int getsockname(int kind,struct sockaddr *out,socklen_t *length) {
 if(kind==4)return -1;
 if(kind==2){struct sockaddr_in6 a={0};a.sin6_family=AF_INET6;unsigned char bytes[16]={0,0,0,0,0,0,0,0,0,0,255,255,192,168,4,1};memcpy(&a.sin6_addr,bytes,16);assert(*length>=sizeof a);memcpy(out,&a,sizeof a);*length=sizeof a;return 0;}
 struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_addr.s_addr=htonl(kind==1?0xc0a80401:kind==5?0xc0a80402:kind==6?0x7f000001:0xc0a80108);assert(*length>=sizeof a);memcpy(out,&a,sizeof a);*length=kind==3?2:(socklen_t)sizeof a;return 0;
}
esp_err_t httpd_start(httpd_handle_t *out,const httpd_config_t *config) { assert(in_worker);assert(config->max_open_sockets<=3);http_starts++;route_count=0;memset(routes,0,sizeof routes);*out=&routes;return ESP_OK; }
esp_err_t httpd_stop(httpd_handle_t handle) { assert(in_worker&&handle);http_stops++;return ESP_OK; }
esp_err_t httpd_register_uri_handler(httpd_handle_t h,const httpd_uri_t *route) { assert(h&&route_count<12);routes[route_count++]=*route;return ESP_OK; }
int httpd_req_to_sockfd(httpd_req_t *req) { return req->socket_kind; }
static const char *header_value(httpd_req_t *req,const char *key) { return !strcmp(key,"X-Pocket-Setup")?req->request_nonce:!strcmp(key,"Content-Type")?req->content_type:NULL; }
size_t httpd_req_get_hdr_value_len(httpd_req_t *req,const char *key) { const char *value=header_value(req,key);return value?strlen(value):0; }
esp_err_t httpd_req_get_hdr_value_str(httpd_req_t *req,const char *key,char *out,size_t capacity) { const char *value=header_value(req,key);if(!value||strlen(value)>=capacity)return ESP_FAIL;memcpy(out,value,strlen(value)+1);return ESP_OK; }
int httpd_req_recv(httpd_req_t *req,char *out,size_t size) { body_reads++;size_t left=(size_t)req->content_len-req->received;if(size>left)size=left;if(size>13)size=13;memcpy(out,req->body+req->received,size);req->received+=size;if(expire_on_receive&&req->received==(size_t)req->content_len){clock_us=expire_on_receive;expire_on_receive=0;}return (int)size; }
esp_err_t httpd_resp_set_status(httpd_req_t *req,const char *value) { (void)req;response_status=atoi(value);return ESP_OK; }
esp_err_t httpd_resp_set_type(httpd_req_t *req,const char *value) { (void)req;snprintf(response_type,sizeof response_type,"%s",value);return ESP_OK; }
esp_err_t httpd_resp_set_hdr(httpd_req_t *req,const char *key,const char *value) { (void)req;(void)key;(void)value;return ESP_OK; }
esp_err_t httpd_resp_send(httpd_req_t *req,const char *value,int length) { (void)req;if(length<0)length=(int)strlen(value);assert(length>=0&&response_used+(unsigned)length<sizeof response);if(length)memcpy(response+response_used,value,(size_t)length);response_used+=(unsigned)length;response[response_used]=0;return ESP_OK; }
esp_err_t httpd_resp_sendstr(httpd_req_t *req,const char *value) { return httpd_resp_send(req,value,-1); }
esp_err_t httpd_resp_send_chunk(httpd_req_t *req,const char *value,int length) { return httpd_resp_send(req,value,length); }
esp_err_t httpd_resp_send_err(httpd_req_t *req,int status,const char *value) { response_status=status;return httpd_resp_sendstr(req,value); }

#define CHECK(c,m) do { if(!(c)) { puts("FAIL: " m);return 1; } } while(0)
static int initialize(void) {
 CHECK(pocket_setup_init()==ESP_OK,"setup initialization failed");initialized=true;
 CHECK(!notifications&&!ap_starts&&!http_starts,"unconfigured boot must not request or open setup AP");
 return 0;
}
static int boot(void) { pocket_wifi_book_init(&loaded_book);return initialize(); }
#ifndef POCKET_SETUP_BASELINE
static void step(bool notified,int64_t now) { clock_us=now;in_worker=true;work_step(notified,now);in_worker=false; }
static void open_window(void) { pocket_setup_request();step(true,clock_us); }
static int request_bytes(httpd_uri_func handler,const char *uri,const char *body,size_t length,int socket_kind,const char *authorization,const char *content_type) {
 response_used=0;response[0]=response_type[0]=0;response_status=200;
 httpd_req_t req={.uri=uri,.content_len=(int)length,.body=body,.request_nonce=authorization,.content_type=content_type,.socket_kind=socket_kind};
 unsigned before=radio_calls;handler(&req);assert(radio_calls==before);return response_status;
}
static int request(httpd_uri_func handler,const char *uri,const char *body,int socket_kind,const char *authorization) { return request_bytes(handler,uri,body,body?strlen(body):0,socket_kind,authorization,"application/json"); }
static int authorized(httpd_uri_func handler,const char *uri,const char *body) { return request(handler,uri,body,1,nonce); }
static void associate_auth(const char *ssid,wifi_auth_mode_t authmode) {
 wifi_event_sta_connected_t event={0};event.ssid_len=(uint8_t)strlen(ssid);event.authmode=authmode;memcpy(event.ssid,ssid,event.ssid_len);association_live=true;wifi_event(NULL,WIFI_EVENT,WIFI_EVENT_STA_CONNECTED,&event);
}
static void associate(const char *ssid) { associate_auth(ssid,WIFI_AUTH_WPA2_PSK); }
static void got_ip(void) { wifi_event(NULL,IP_EVENT,IP_EVENT_STA_GOT_IP,NULL); }
static void disconnected(unsigned reason) { wifi_event_sta_disconnected_t event={0};event.reason=(uint8_t)reason;association_live=false;wifi_event(NULL,WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,&event); }
static bool no_secrets(void) { return !strstr(response,"password")&&!strstr(response,"private-token-fixture")&&!strstr(response,"secret-fixture")&&!strstr(response,"PRIVATE_CA_FIXTURE")&&!strstr(response,"windows")&&!strstr(response,"linux"); }
static cJSON *status_json(void) { if(authorized(wifi_status,"/wifi/status",NULL)!=200)return NULL;return cJSON_Parse(response); }
static int status_contract(void) {
 cJSON *json=status_json();CHECK(json&&cJSON_IsObject(json),"status was not a JSON object");
 const char *keys[]={"connected","ssid","connection","join_result","operation","capacity","profiles","scan_state","networks","pairing_saved","store_writable","expires_seconds"};
 CHECK(cJSON_GetArraySize(json)==12,"status has missing or private fields");
 for(unsigned i=0;i<sizeof keys/sizeof *keys;i++)CHECK(cJSON_GetObjectItemCaseSensitive(json,keys[i]),"status field missing");
 cJSON *capacity=cJSON_GetObjectItemCaseSensitive(json,"capacity"),*connection=cJSON_GetObjectItemCaseSensitive(json,"connection"),*join=cJSON_GetObjectItemCaseSensitive(json,"join_result");
 CHECK(cJSON_IsNumber(capacity)&&capacity->valueint==5,"status capacity not five");
 CHECK(cJSON_IsNumber(connection)&&connection->valueint>=0&&connection->valueint<=7,"status connection not numeric public enum");
 CHECK(cJSON_IsNumber(join)&&join->valueint>=0&&join->valueint<=7,"status join result not numeric public enum");
 CHECK(cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(json,"connected"))&&cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(json,"pairing_saved"))&&cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(json,"store_writable")),"status public flags not booleans");
 CHECK(cJSON_IsString(cJSON_GetObjectItemCaseSensitive(json,"ssid")),"status SSID not a string");
 cJSON *operation=cJSON_GetObjectItemCaseSensitive(json,"operation"),*scan=cJSON_GetObjectItemCaseSensitive(json,"scan_state"),*seconds=cJSON_GetObjectItemCaseSensitive(json,"expires_seconds");
 CHECK(cJSON_IsNumber(operation)&&operation->valueint>=0&&cJSON_IsNumber(scan)&&scan->valueint>=0&&scan->valueint<=3,"status operation or scan state invalid");
 CHECK(cJSON_IsNumber(seconds)&&seconds->valueint>=0&&seconds->valueint<=300,"status expiry not bounded to five minutes");
 cJSON *profiles=cJSON_GetObjectItemCaseSensitive(json,"profiles"),*networks_list=cJSON_GetObjectItemCaseSensitive(json,"networks");
 CHECK(cJSON_IsArray(profiles)&&cJSON_GetArraySize(profiles)<=5&&cJSON_IsArray(networks_list)&&cJSON_GetArraySize(networks_list)<=20,"status list shape or bounds invalid");
 for(cJSON *item=profiles->child;item;item=item->next)CHECK(cJSON_IsObject(item)&&cJSON_GetArraySize(item)==2&&cJSON_IsString(cJSON_GetObjectItemCaseSensitive(item,"ssid"))&&cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(item,"connected")),"profile record contains private fields or invalid types");
 CHECK(no_secrets(),"status exposed a stored credential or pairing field");cJSON_Delete(json);return 0;
}
static int guards(void) {
 CHECK(!boot(),"boot prerequisite failed");
 CHECK(request(page,"/",NULL,1,NULL)==403,"inactive page accepted");
 CHECK(request(wifi_status,"/wifi/status",NULL,1,nonce)==403,"inactive status accepted");
 open_window();CHECK(http_starts==1&&ap_starts==1,"explicit gesture did not open AP");
 CHECK(applied_ap.ap.authmode==WIFI_AUTH_WPA2_PSK&&applied_ap.ap.pmf_cfg.required&&strlen((char *)applied_ap.ap.password)>=8,"AP lacks WPA2 password or required PMF");
 CHECK(strlen(nonce)==32,"setup nonce not 128-bit hex");
 for(int socket=0;socket<=6;socket++)if(socket!=1&&socket!=2){
  CHECK(request(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic New\",\"password\":\"new-secret-fixture\"}",socket,nonce)==403,"non-AP join accepted");
  CHECK(request(wifi_status,"/wifi/status",NULL,socket,nonce)==403,"non-AP status accepted");
  CHECK(request(page,"/",NULL,socket,NULL)==403,"non-AP setup page accepted");
 }
 CHECK(request(wifi_join,"/wifi/join","{}",1,"wrong-nonce")==403,"wrong nonce accepted");
 CHECK(request(wifi_status,"/wifi/status",NULL,1,NULL)==403,"missing nonce accepted");
 CHECK(request(networks,"/networks",NULL,1,"wrong-nonce")==403,"scan accepted wrong nonce");
 CHECK(request(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic\"}",0,nonce)==403,"forget accepted non-AP request");
 CHECK(request(wifi_control,"/wifi/cancel","{}",1,"wrong-nonce")==403,"cancel accepted wrong nonce");
 CHECK(request(pairing,"/pairing","{}",0,nonce)==403&&request(pairing,"/pairing","{}",1,"wrong-nonce")==403,"pairing guard failed");
 CHECK(!queue_data.count&&!profile_saves&&!pair_updates&&!body_reads,"rejected authorization read a body or mutated state");
 CHECK(request(page,"/",NULL,1,NULL)==200,"active AP page rejected");
 CHECK(request(wifi_status,"/wifi/status",NULL,2,nonce)==200,"IPv4-mapped AP address rejected");
 CHECK(!status_contract(),"status contract failed");
 bool saw_status=false,saw_join=false,saw_pairing=false;
 for(unsigned i=0;i<route_count;i++){CHECK(strcmp(routes[i].uri,"/save"),"legacy all-in-one save route remains registered");saw_status|=!strcmp(routes[i].uri,"/wifi/status");saw_join|=!strcmp(routes[i].uri,"/wifi/join");saw_pairing|=!strcmp(routes[i].uri,"/pairing");}
 CHECK(saw_status&&saw_join&&saw_pairing,"new routes not registered");return 0;
}
static int ownership(void) {
 CHECK(!boot(),"boot prerequisite failed");CHECK(pocket_setup_voice_acquire(),"idle voice ownership unavailable");
 pocket_setup_request();step(true,clock_us);CHECK(!http_starts&&!ap_starts,"setup opened while voice owned radio");
 pocket_setup_voice_release();pocket_setup_request();CHECK(!pocket_setup_voice_acquire(),"pending setup did not reserve ownership");
 step(true,clock_us);CHECK(http_starts==1&&!pocket_setup_voice_acquire(),"active setup lost ownership");
 step(false,clock_us+300000000);pocket_setup_state visible;pocket_setup_read(&visible);
 CHECK(!visible.active&&http_stops==1&&!visible.password[0],"five-minute setup window retained AP or password");
 CHECK(pocket_setup_voice_acquire(),"expired setup did not release ownership");pocket_setup_voice_release();return 0;
}
static int join_events(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 got_ip();CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic New\",\"password\":\"new-secret-fixture\"}")==202,"valid join was not queued");
 CHECK(queue_data.count==1&&!connects&&!profile_saves,"HTTP join started radio or saved untested credentials");
 step(false,clock_us+1000);CHECK(connects==1&&!profile_saves&&!engine.book.count,"queued join not started exclusively by worker");
 got_ip();step(false,clock_us+1000);CHECK(!profile_saves,"IP without fresh association saved credentials");
 associate("Other Synthetic");got_ip();step(false,clock_us+1000);CHECK(!profile_saves,"wrong SSID association saved credentials");
 associate("Synthetic New");pocket_setup_state current;pocket_setup_read(&current);CHECK(!current.wifi_connected,"new association reused connected state before DHCP");
 step(false,clock_us+1000);CHECK(!profile_saves,"association without fresh IP saved credentials");
 got_ip();step(false,clock_us+1000);
 CHECK(profile_saves==1&&persisted_book.count==1&&!strcmp(persisted_book.profiles[0].ssid,"Synthetic New")&&!strcmp(persisted_book.profiles[0].password,"new-secret-fixture"),"fresh candidate success was not saved");
 CHECK(!pair_updates&&engine.join_result==POCKET_WIFI_JOIN_SAVED,"Wi-Fi join mutated pairing or missed saved result");
 unsigned before=scans;for(int i=1;i<=10;i++)step(false,clock_us+1000000);CHECK(scans==before&&connects==1,"healthy connection roamed or scanned");
 CHECK(!status_contract(),"public status failed after save");CHECK(!strstr(logs,"Synthetic")&&!strstr(logs,"secret")&&!strstr(logs,"private-token"),"diagnostics exposed identifiers or secrets");return 0;
}
static int expiry(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Expired\",\"password\":\"expired-secret-fixture\"}")==202,"join queue prerequisite failed");
 CHECK(queue_data.count==1,"candidate command was not queued");int64_t end=clock_us+300000000;
 step(false,end);CHECK(!connects&&!profile_saves&&!queue_data.count,"expired queued command started or committed");
 CHECK(request(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Late\",\"password\":\"late-secret-fixture\"}",1,nonce)==403,"expired window accepted mutation");
 CHECK(!queue_data.count&&!pair_updates,"expired window retained work");return 0;
}
static int body_expiry(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();expire_on_receive=clock_us+300000000;
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Late\",\"password\":\"late-secret-fixture\"}")==403,"join did not recheck expiry after body receive");
 CHECK(!queue_data.count&&!connects&&!profile_saves,"expired received credentials were queued or used");
 step(false,clock_us);CHECK(!queue_data.count&&!connects&&!profile_saves,"expired received credentials reached worker");return 0;
}
static int cancellation(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Cancel\",\"password\":\"cancel-secret-fixture\"}")==202,"join queue prerequisite failed");
 CHECK(authorized(wifi_control,"/wifi/cancel","{}")==202,"cancel was not accepted");
 step(false,clock_us+1000);CHECK(!profile_saves&&!engine.testing,"cancelled queued candidate remained active");
 CHECK(!connects,"cancelled queued candidate reached radio");
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Cancel\",\"password\":\"cancel-secret-fixture\"}")==202,"second join rejected");
 step(false,clock_us+1000);CHECK(connects==1&&engine.testing,"second join did not start");
 CHECK(authorized(wifi_control,"/wifi/cancel","{}")==202,"active cancel rejected");
 associate("Synthetic Cancel");got_ip();step(false,clock_us+1000);CHECK(!profile_saves&&!engine.book.count,"cancel race saved cancelled credentials");return 0;
}
static bool queue_contains(const char *text) {
 size_t length=strlen(text);for(size_t i=0;i+length<=sizeof queue_data.data;i++)if(!memcmp(queue_data.data+i,text,length))return true;return false;
}
static int queued_secret(bool expire) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Queue\",\"password\":\"queue-only-secret-fixture\"}")==202,"queue-secret prerequisite failed");
 CHECK(queue_data.count==1,"candidate operation was not queued");
 CHECK(!queue_contains("queue-only-secret-fixture"),"queued candidate password remains in RTOS backing bytes");
 if(expire)step(false,clock_us+300000000);
 else {CHECK(authorized(wifi_control,"/wifi/cancel","{}")==202,"queue-secret cancel rejected");step(false,clock_us+1000);}
 CHECK(!queue_data.count&&!queue_contains("queue-only-secret-fixture"),"drained queue retains cancelled or expired candidate password");
 CHECK(!connects&&!profile_saves,"cancelled or expired secret reached radio or store");return 0;
}
static int validation(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 const char *invalid[]={"{}","[]","{\"ssid\":\"Synthetic\",\"password\":\"short\"}","{\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\",\"windows\":\"https://fixture.invalid\"}","{\"ssid\":\"Synthetic\",\"ssid\":\"Other\",\"password\":\"synthetic-only\"}","{\"ssid\":\"Synthetic\\u0000hidden\",\"password\":\"synthetic-only\"}","{\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\"} trailing","{\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\"} {}"};
 for(unsigned i=0;i<sizeof invalid/sizeof *invalid;i++)CHECK(authorized(wifi_join,"/wifi/join",invalid[i])==400,"malformed, duplicate, NUL or excess join fields accepted");
 CHECK(authorized(wifi_control,"/wifi/connect","{\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\"}")==400,"connect accepted secret or excess field");
 CHECK(authorized(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic\",\"ssid\":\"Other\"}")==400,"forget accepted duplicate field");
 CHECK(authorized(wifi_control,"/wifi/cancel","{\"ssid\":\"Synthetic\"}")==400,"cancel accepted excess field");
 CHECK(request_bytes(wifi_join,"/wifi/join","{}",2,1,nonce,"text/plain")==400,"non-JSON body accepted");
 CHECK(authorized(wifi_join,"/wifi/join","")==400,"empty join body accepted");
 char oversized[4098];memset(oversized,'x',sizeof oversized-1);oversized[sizeof oversized-1]=0;unsigned reads=body_reads;
 CHECK(authorized(wifi_join,"/wifi/join",oversized)==400&&body_reads==reads,"oversized body was read or accepted");
 const char raw_nul[]="{\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\"}\0hidden";
 CHECK(request_bytes(wifi_join,"/wifi/join",raw_nul,sizeof raw_nul-1,1,nonce,"application/json")==400,"raw NUL concealed trailing request data");
 const char raw_nul_field[]="{\"ssid\":\"Synthetic\0hidden\",\"password\":\"synthetic-only\"}";
 CHECK(request_bytes(wifi_join,"/wifi/join",raw_nul_field,sizeof raw_nul_field-1,1,nonce,"application/json")==400,"raw NUL aliased a network name");
 CHECK(!queue_data.count&&!profile_saves&&!pair_updates&&!connects,"invalid request changed state");
 queue_full=true;CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\"}")>=400,"full command queue reported success");
 CHECK(!queue_data.count&&!connects&&!profile_saves,"queue overflow performed work");return 0;
}
static int pairing_isolation(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 const char *mixed="{\"windows\":\"https://new.invalid\",\"linux\":\"\",\"token\":\"\",\"ca\":\"\",\"ssid\":\"Synthetic\",\"password\":\"synthetic-only\"}";
 CHECK(authorized(pairing,"/pairing",mixed)==400,"pairing accepted mixed Wi-Fi payload");
 CHECK(!pair_updates&&!profile_saves&&!queue_data.count,"mixed pairing payload mutated state");
 pocket_pairing_config before=pair_config;
 CHECK(authorized(pairing,"/pairing","{\"windows\":\"\",\"linux\":\"\",\"token\":\"\",\"ca\":\"\"}")==200,"blank-preserving pairing rejected");
 CHECK(!profile_saves,"pairing wrote Wi-Fi profiles");
 CHECK(!memcmp(&before,&pair_config,sizeof before)&&!profile_saves,"blank pairing changed stored values or Wi-Fi");
 CHECK(authorized(pairing,"/pairing","{\"windows\":\"https://new.invalid\",\"linux\":\"\",\"token\":\"\",\"ca\":\"\"}")==200,"pairing update rejected");
 CHECK(!strcmp(pair_config.windows,"https://new.invalid")&&!strcmp(pair_config.token,before.token)&&!strcmp(pair_config.ca,before.ca)&&!profile_saves,"pairing update lost private values or changed profiles");
 CHECK(!status_contract(),"pairing leaked in status");
 unsigned updates=pair_updates;expire_on_receive=clock_us+300000000;
 CHECK(authorized(pairing,"/pairing","{\"windows\":\"https://late.invalid\",\"linux\":\"\",\"token\":\"\",\"ca\":\"\"}")>=400,"pairing did not recheck expiry after body receive");
 CHECK(pair_updates==updates&&!profile_saves&&!strcmp(pair_config.windows,"https://new.invalid"),"expired body committed pairing");return 0;
}
static void add_saved(const char *ssid) {
 pocket_wifi_profile profile={0};snprintf(profile.ssid,sizeof profile.ssid,"%s",ssid);snprintf(profile.password,sizeof profile.password,"saved-secret-fixture");assert(pocket_wifi_book_put(&loaded_book,&profile)>=0);
}
static int migration(const char *kind) {
 pocket_wifi_book_init(&loaded_book);legacy_available=true;
 load_result=!strcmp(kind,"legacy")?POCKET_WIFI_STORE_ABSENT:!strcmp(kind,"empty")?POCKET_WIFI_STORE_OK:!strcmp(kind,"corrupt")?POCKET_WIFI_STORE_CORRUPT:POCKET_WIFI_STORE_ERROR;
 CHECK(!initialize(),"migration initialization failed");
 if(load_result==POCKET_WIFI_STORE_ABSENT)CHECK(legacy_reads==1&&engine.book.count==1&&!strcmp(engine.book.profiles[0].ssid,"Synthetic Legacy"),"absent profile store did not read-only import legacy Wi-Fi");
 else CHECK(!legacy_reads&&!engine.book.count,"authoritative empty, corrupt or failed store revived legacy Wi-Fi");
 CHECK(!profile_saves&&!pair_updates,"boot migration wrote storage");
 if(load_result==POCKET_WIFI_STORE_CORRUPT||load_result==POCKET_WIFI_STORE_ERROR){
  open_window();CHECK(!profile_store.writable,"bad profile store remained writable");
  CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic New\",\"password\":\"new-secret-fixture\"}")>=400,"bad store accepted a candidate");
  CHECK(!profile_saves&&!connects,"bad store was overwritten or connected");
 }
 return 0;
}
static int capacity_and_forget(void) {
 pocket_wifi_book_init(&loaded_book);load_result=POCKET_WIFI_STORE_OK;
 for(unsigned i=0;i<5;i++){char ssid[24];snprintf(ssid,sizeof ssid,"Synthetic Saved %u",i);add_saved(ssid);}
 CHECK(!initialize(),"saved boot failed");open_window();associate("Synthetic Saved 4");got_ip();step(false,clock_us+1000);
 CHECK(engine.connection==POCKET_WIFI_CONNECTED&&!profile_saves,"saved boot connection rewrote profiles");
 pocket_wifi_book original=engine.book;unsigned connections=connects;
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Sixth\",\"password\":\"sixth-secret-fixture\"}")==202,"capacity attempt was not queued");
 step(false,clock_us+1000);CHECK(engine.join_result==POCKET_WIFI_JOIN_FULL&&engine.book.count==5&&!profile_saves&&connects==connections&&!memcmp(&engine.book,&original,sizeof original),"sixth profile evicted, wrote or connected");
 CHECK(!status_contract(),"full-store status leaked secrets");
 CHECK(authorized(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic Saved 2\"}")==202,"explicit forget was not queued");
 CHECK(!profile_saves,"forget persisted in HTTP handler");step(false,clock_us+1000);
 CHECK(profile_saves==1&&engine.book.count==4&&pocket_wifi_book_find(&engine.book,"Synthetic Saved 2")<0&&!pair_updates,"explicit forget did not preserve remaining profiles and pairing");
 CHECK(authorized(wifi_control,"/wifi/connect","{\"ssid\":\"Synthetic Saved 0\"}")==202,"saved-profile connect not queued");step(false,clock_us+1000);
 disconnected(0);step(false,clock_us+1000);CHECK(connects==connections+1&&!memcmp(applied_sta.sta.ssid,"Synthetic Saved 0",17)&&!strcmp((char *)applied_sta.sta.password,"saved-secret-fixture"),"saved-profile connect did not use stored credentials");return 0;
}
static int failure_and_fallback(void) {
 pocket_wifi_book_init(&loaded_book);load_result=POCKET_WIFI_STORE_OK;add_saved("Synthetic Home");add_saved("Synthetic Office");
 CHECK(!initialize(),"saved boot failed");open_window();associate("Synthetic Office");got_ip();step(false,clock_us+1000);
 pocket_wifi_book original=engine.book;
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Wrong\",\"password\":\"wrong-secret-fixture\"}")==202,"failure candidate not queued");step(false,clock_us+1000);
 disconnected(0);step(false,clock_us+1000);CHECK(!strcmp((char *)applied_sta.sta.ssid,"Synthetic Wrong"),"candidate not tested after disconnect barrier");
 disconnected(WIFI_REASON_AUTH_FAIL);step(false,clock_us+1000);
 CHECK(engine.join_result==POCKET_WIFI_JOIN_FAILED&&engine.connection==POCKET_WIFI_AUTH_FAILED&&!profile_saves&&!memcmp(&engine.book,&original,sizeof original),"auth failure lost saved profiles or reason");
 step(false,clock_us+1001000);CHECK(!strcmp((char *)applied_sta.sta.ssid,"Synthetic Office"),"failed candidate did not reconnect last saved profile");
 associate("Synthetic Office");got_ip();step(false,clock_us+1000);
 CHECK(engine.connection==POCKET_WIFI_CONNECTED&&!profile_saves&&!memcmp(&engine.book,&original,sizeof original),"fallback changed saved credentials");return 0;
}
static int async_scan(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 snprintf((char *)scan_records[0].ssid,sizeof scan_records[0].ssid,"Synthetic Scan");scan_records[0].rssi=-45;scan_records[0].authmode=WIFI_AUTH_WPA2_PSK;
 scan_records[1]=scan_records[0];snprintf((char *)scan_records[2].ssid,sizeof scan_records[2].ssid,"Synthetic Open");scan_records[2].rssi=-60;scan_records[2].authmode=WIFI_AUTH_OPEN;scan_count=4;
 CHECK(authorized(networks,"/networks",NULL)==202&&!scans&&queue_data.count==1,"scan blocked HTTP or was not queued");
 step(false,clock_us+1000);CHECK(scans==1&&!profile_saves&&!connects,"worker scan changed saved profiles or connected");
 cJSON *json=status_json();CHECK(json,"scan status failed");cJSON *state_value=cJSON_GetObjectItemCaseSensitive(json,"scan_state"),*list=cJSON_GetObjectItemCaseSensitive(json,"networks");
 CHECK(cJSON_IsNumber(state_value)&&state_value->valueint==2&&cJSON_IsArray(list)&&cJSON_GetArraySize(list)==2,"async scan did not deduplicate or finish");
 cJSON *first=cJSON_GetArrayItem(list,0),*second=cJSON_GetArrayItem(list,1);
 CHECK(cJSON_GetArraySize(first)==3&&cJSON_GetArraySize(second)==3&&!strcmp(cJSON_GetObjectItemCaseSensitive(first,"ssid")->valuestring,"Synthetic Scan")&&cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(second,"open")),"scan public record contract incorrect");
 CHECK(no_secrets(),"scan exposed credentials");cJSON_Delete(json);
 CHECK(authorized(networks,"/networks",NULL)==400,"scan throttle missing");return 0;
}
static int automatic_ownership(void) {
 pocket_wifi_book_init(&loaded_book);load_result=POCKET_WIFI_STORE_OK;add_saved("Synthetic Home");CHECK(!initialize(),"saved boot failed");
 CHECK(pocket_setup_voice_acquire(),"idle initialized radio unavailable to voice");
 step(false,clock_us);CHECK(!connects&&!scans,"automatic reconnect started while voice owned radio");pocket_setup_voice_release();
 step(false,clock_us+1000);CHECK(connects==1,"automatic reconnect did not start");
 CHECK(!pocket_setup_voice_acquire(),"voice acquired radio during automatic connection attempt");
 associate("Synthetic Home");got_ip();step(false,clock_us+1000);
 CHECK(pocket_setup_voice_acquire(),"stable automatic connection did not release radio ownership");pocket_setup_voice_release();return 0;
}
static int legacy_idle_disconnect_ok(bool compatibility) {
 pocket_wifi_book_init(&loaded_book);legacy_available=true;disconnect_not_connect_while_idle=compatibility;
 CHECK(!initialize(),"legacy idle-disconnect boot failed");CHECK(engine.book.count==1,"legacy fixture was not imported");
 step(false,clock_us+1000);
 CHECK(connects==1,"legacy boot waited for a nonexistent disconnect event while station was already idle");
 CHECK(!strcmp((char *)applied_sta.sta.ssid,"Synthetic Legacy"),"legacy boot connected the wrong profile");
 associate("Synthetic Legacy");got_ip();step(false,clock_us+1000);pocket_setup_state visible;pocket_setup_read(&visible);
 CHECK(engine.connection==POCKET_WIFI_CONNECTED&&visible.wifi_connected,"fresh legacy WPA2/IP did not validate after idle disconnect success");
 CHECK(!profile_saves&&!pair_updates&&!ap_starts&&!http_starts,"legacy boot wrote settings or opened setup");
 CHECK(!queue_contains("legacy-secret-fixture")&&!strstr(logs,"secret"),"legacy boot exposed stored credentials");return 0;
}
static int legacy_query_error_recovery(void) {
 pocket_wifi_book_init(&loaded_book);legacy_available=true;ap_query_error=ESP_FAIL;
 CHECK(!initialize(),"legacy query-error boot failed");step(false,clock_us+1000);
 CHECK(!connects&&!disconnects,"failed station pre-query still changed radio configuration");
 CHECK(engine.connection==POCKET_WIFI_CONNECTION_FAILED&&!profile_saves&&!pair_updates,"station pre-query error was hidden or wrote settings");
 ap_query_error=ESP_OK;
 for(unsigned i=0;i<40&&!connects;i++)step(false,clock_us+1000000);
 CHECK(connects==1,"legacy connection did not retry station pre-query after transient error");
 associate("Synthetic Legacy");got_ip();step(false,clock_us+1000);
 CHECK(engine.connection==POCKET_WIFI_CONNECTED&&!profile_saves&&!pair_updates&&!ap_starts,"station query recovery failed or changed stored settings");
 CHECK(!queue_contains("legacy-secret-fixture")&&!strstr(logs,"secret"),"station query recovery exposed credentials");return 0;
}
static int first_join_late_events(bool timeout,bool delayed_ack) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic First Race\",\"password\":\"first-race-secret-fixture\"}")==202,"first candidate was not queued");
 step(false,clock_us+1000);CHECK(connects==1&&engine.testing,"first candidate did not start");
 if(timeout)step(false,clock_us+(int64_t)(POCKET_WIFI_CONNECT_MS+1)*1000);
 else {CHECK(authorized(wifi_control,"/wifi/cancel","{}")==202,"first-candidate cancel rejected");step(false,clock_us+1000);}
 CHECK(!engine.testing&&!engine.attempt.password[0],"terminal first candidate retained testing or secret");
 unsigned before=disconnects;associate("Synthetic First Race");got_ip();step(false,clock_us+1000);
 CHECK(disconnects>before,"late terminal first candidate did not reissue radio disconnect");
 CHECK(!status_contract()&&!queue_contains("first-race-secret-fixture"),"late first-candidate events exposed credentials");
 CHECK(!profile_saves&&!engine.book.count,"late cancelled or timed-out first candidate was saved");
 if(!delayed_ack){disconnected(0);step(false,clock_us+1000);}
 step(false,expires_us);pocket_setup_state visible;pocket_setup_read(&visible);
 CHECK(!visible.active&&!visible.password[0]&&!queue_data.count,"terminal first join prevented clean setup expiry");
 if(delayed_ack){
  CHECK(!pocket_setup_voice_acquire(),"voice acquired radio before cleanup disconnect acknowledgement");
  CHECK(pocket_wifi_attempting(&engine),"cleanup lost radio attempt exclusion while driver remained connected");
  disconnected(0);step(false,clock_us+1000);
 }
 pocket_setup_read(&visible);CHECK(!visible.wifi_connected&&!pocket_wifi_attempting(&engine),"disconnect acknowledgement did not complete candidate cleanup");
 CHECK(pocket_setup_voice_acquire(),"late first-candidate events stranded automatic ownership after expiry");pocket_setup_voice_release();
 pocket_setup_request();step(true,clock_us+1000);pocket_setup_read(&visible);
 CHECK(visible.active&&http_starts==2,"setup could not reopen after terminal first join");
 CHECK(!status_contract()&&!profile_saves&&!engine.book.count,"reopened setup leaked or saved first-candidate credentials");return 0;
}
static int disconnected_candidate_late_ip(bool replacement) {
 if(replacement){pocket_wifi_book_init(&loaded_book);load_result=POCKET_WIFI_STORE_OK;add_saved("Synthetic Disconnect Race");CHECK(!initialize(),"replacement boot failed");}
 else CHECK(!boot(),"boot prerequisite failed");
 open_window();
 if(replacement){associate("Synthetic Disconnect Race");got_ip();step(false,clock_us+1000);CHECK(engine.connection==POCKET_WIFI_CONNECTED,"saved replacement prerequisite did not connect");}
 pocket_wifi_book original=engine.book;
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Disconnect Race\",\"password\":\"disconnect-race-secret-fixture\"}")==202,"disconnect-race candidate was not queued");
 step(false,clock_us+1000);
 if(replacement){disconnected(0);step(false,clock_us+1000);}
 CHECK(engine.testing,"disconnect-race candidate was not testing");
 associate("Synthetic Disconnect Race");disconnected(WIFI_REASON_AUTH_FAIL);got_ip();step(false,clock_us+1000);
 CHECK(!status_contract()&&!queue_contains("disconnect-race-secret-fixture"),"disconnect/IP race exposed candidate credentials");
 CHECK(!profile_saves,"disconnect followed by late IP persisted invalid candidate");
 CHECK(!memcmp(&engine.book,&original,sizeof original),"disconnect/IP race replaced saved credentials");
 CHECK(engine.join_result==POCKET_WIFI_JOIN_FAILED&&!engine.testing,"disconnect/IP race did not fail candidate");return 0;
}
static int repeated_forget_pending(void) {
 pocket_wifi_book_init(&loaded_book);load_result=POCKET_WIFI_STORE_OK;add_saved("Synthetic Forget A");add_saved("Synthetic Forget B");add_saved("Synthetic Forget Current");
 CHECK(!initialize(),"forget boot failed");open_window();associate("Synthetic Forget Current");got_ip();step(false,clock_us+1000);
 CHECK(authorized(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic Forget A\"}")==202,"first forget was not queued");step(false,clock_us+1000);
 cJSON *json=status_json();CHECK(json&&no_secrets(),"first-forget status leaked credentials");
 cJSON *result=cJSON_GetObjectItemCaseSensitive(json,"join_result");CHECK(cJSON_IsNumber(result)&&result->valueint==POCKET_WIFI_JOIN_FORGOTTEN&&profile_saves==1,"first forget did not complete");cJSON_Delete(json);
 CHECK(authorized(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic Forget B\"}")==202,"second forget was not queued");
 cJSON *accepted=cJSON_Parse(response);CHECK(accepted,"second forget did not return JSON");cJSON *operation=cJSON_GetObjectItemCaseSensitive(accepted,"operation");CHECK(cJSON_IsNumber(operation),"second forget did not return operation");unsigned ticket=(unsigned)operation->valueint;cJSON_Delete(accepted);
 json=status_json();CHECK(json&&no_secrets(),"pending-forget status leaked credentials");
 operation=cJSON_GetObjectItemCaseSensitive(json,"operation");result=cJSON_GetObjectItemCaseSensitive(json,"join_result");
 CHECK(cJSON_IsNumber(operation)&&(unsigned)operation->valueint==ticket,"status did not identify newly accepted forget");
 CHECK(cJSON_IsNumber(result)&&result->valueint==POCKET_WIFI_JOIN_TESTING,"second queued forget reused previous terminal result before worker");cJSON_Delete(json);
 CHECK(profile_saves==1&&engine.book.count==2&&queue_data.count==1,"second forget mutated profiles before worker");
 step(false,clock_us+1000);CHECK(profile_saves==2&&engine.book.count==1&&pocket_wifi_book_find(&engine.book,"Synthetic Forget B")<0,"second forget did not complete independently");
 CHECK(!status_contract()&&!queue_contains("saved-secret-fixture"),"completed repeated forget exposed credentials");return 0;
}
static int poll_setup_lease(void) {
 CHECK(!boot(),"boot prerequisite failed");CHECK(pocket_setup_poll_acquire(),"idle radio denied polling lease");
 CHECK(pocket_setup_poll_continue(),"poll lease could not continue before setup gesture");
 pocket_setup_request();step(true,clock_us);
 CHECK(!ap_starts&&!http_starts,"setup changed radio while HTTP poll held lease");
 CHECK(!pocket_setup_poll_continue(),"setup gesture did not cancel further poll requests");
 CHECK(!pocket_setup_voice_acquire(),"poll upgraded to voice after setup reservation");
 unsigned before=notifications;pocket_setup_poll_release();CHECK(notifications>before,"poll release did not wake reserved setup");
 CHECK(!pocket_setup_poll_acquire()&&!pocket_setup_voice_acquire(),"reserved setup lost ownership after poll release");
 step(true,clock_us+1000);pocket_setup_state visible;pocket_setup_read(&visible);
 CHECK(visible.active&&http_starts==1&&!pocket_setup_poll_acquire(),"poll release failed to open setup or active setup admitted polling");
 CHECK(!status_contract()&&!profile_saves&&!pair_updates,"poll/setup handoff exposed credentials or mutated storage");return 0;
}
static int poll_voice_lease(void) {
 CHECK(!boot(),"boot prerequisite failed");CHECK(pocket_setup_poll_acquire()&&pocket_setup_poll_continue(),"poll lease prerequisite failed");
 CHECK(pocket_setup_voice_acquire(),"poll lease could not upgrade to voice without pending setup");
 pocket_setup_poll_release();CHECK(!pocket_setup_poll_acquire()&&!pocket_setup_voice_acquire(),"poll release cleared upgraded voice ownership");
 pocket_setup_request();step(true,clock_us);CHECK(!ap_starts&&!http_starts,"setup opened while upgraded voice held ownership");
 pocket_setup_voice_release();CHECK(pocket_setup_poll_acquire(),"voice release retained polling exclusion");pocket_setup_poll_release();
 CHECK(pocket_setup_voice_acquire(),"poll release retained voice exclusion");pocket_setup_voice_release();
 CHECK(!profile_saves&&!pair_updates&&!connects&&!strstr(logs,"secret"),"poll/voice handoff mutated settings or exposed credentials");return 0;
}
static int open_association(void) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Secure\",\"password\":\"secure-race-secret-fixture\"}")==202,"secure candidate was not queued");step(false,clock_us+1000);
 CHECK(applied_sta.sta.threshold.authmode==WIFI_AUTH_WPA2_PSK,"station auth threshold does not require WPA2");
 associate_auth("Synthetic Secure",WIFI_AUTH_OPEN);got_ip();step(false,clock_us+1000);
 CHECK(!profile_saves&&!engine.book.count,"open association validated password-protected candidate");
 pocket_setup_state visible;pocket_setup_read(&visible);CHECK(!visible.wifi_connected,"open association exposed a connected station");
 CHECK(!status_contract()&&!queue_contains("secure-race-secret-fixture"),"open association exposed candidate credentials");
 CHECK(!engine.testing&&!engine.attempt.password[0],"unsupported association retained candidate testing or secret");
 disconnected(0);step(false,clock_us+1000);
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic Secure\",\"password\":\"secure-race-secret-fixture\"}")==202,"fresh owner join rejected after open-event cleanup");step(false,clock_us+1000);
 associate("Synthetic Secure");got_ip();step(false,clock_us+1000);
 CHECK(profile_saves==1&&engine.book.count==1&&engine.join_result==POCKET_WIFI_JOIN_SAVED,"fresh valid WPA2 join could not complete after open-event cleanup");return 0;
}
static int cancelled_queued_forget(void) {
 pocket_wifi_book_init(&loaded_book);load_result=POCKET_WIFI_STORE_OK;add_saved("Synthetic Keep Forget");CHECK(!initialize(),"cancel-forget boot failed");
 open_window();associate("Synthetic Keep Forget");got_ip();step(false,clock_us+1000);pocket_wifi_book original=engine.book;
 CHECK(authorized(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic Keep Forget\"}")==202,"cancel-forget prerequisite was not queued");
 CHECK(authorized(wifi_control,"/wifi/cancel","{}")==202,"queued-forget cancel rejected");
 cJSON *accepted=cJSON_Parse(response);CHECK(accepted,"forget cancel did not return JSON");cJSON *operation=cJSON_GetObjectItemCaseSensitive(accepted,"operation");CHECK(cJSON_IsNumber(operation),"forget cancel operation missing");unsigned ticket=(unsigned)operation->valueint;cJSON_Delete(accepted);
 step(false,clock_us+1000);
 CHECK(!profile_saves&&!queue_data.count&&!memcmp(&engine.book,&original,sizeof original),"cancelled queued forget changed saved profiles");
 cJSON *json=status_json();CHECK(json&&no_secrets()&&!queue_contains("saved-secret-fixture"),"queued-forget cancellation exposed credentials");
 operation=cJSON_GetObjectItemCaseSensitive(json,"operation");cJSON *result=cJSON_GetObjectItemCaseSensitive(json,"join_result");
 CHECK(cJSON_IsNumber(operation)&&(unsigned)operation->valueint==ticket,"queued-forget cancellation lost new operation");
 CHECK(cJSON_IsNumber(result)&&result->valueint==POCKET_WIFI_JOIN_CANCELLED,"queued-forget cancel operation did not publish terminal cancelled result");cJSON_Delete(json);
 CHECK(authorized(wifi_control,"/wifi/forget","{\"ssid\":\"Synthetic Keep Forget\"}")==202,"cancelled forget blocked a new owner operation");step(false,clock_us+1000);
 CHECK(profile_saves==1&&!engine.book.count,"fresh forget after cancel did not complete");return 0;
}
static int cleanup_late_association_no_ip(bool after_disconnect) {
 CHECK(!boot(),"boot prerequisite failed");open_window();
 CHECK(authorized(wifi_join,"/wifi/join","{\"ssid\":\"Synthetic No IP Cleanup\",\"password\":\"no-ip-cleanup-secret-fixture\"}")==202,"no-IP cleanup candidate was not queued");
 step(false,clock_us+1000);associate("Synthetic No IP Cleanup");step(false,clock_us+1000);
 CHECK(authorized(wifi_control,"/wifi/cancel","{}")==202,"no-IP cleanup cancel rejected");step(false,clock_us+1000);
 CHECK(pocket_wifi_attempting(&engine)&&!engine.testing&&!engine.attempt.password[0],"cancel did not start secret-free asynchronous cleanup");
 unsigned before=disconnects;
 if(after_disconnect){disconnected(0);associate("Synthetic No IP Cleanup");}
 else {associate("Synthetic No IP Cleanup");disconnected(0);}
 step(false,clock_us+1000);
 CHECK(!status_contract()&&!queue_contains("no-ip-cleanup-secret-fixture")&&!profile_saves&&!engine.book.count,"no-IP cleanup event race exposed or saved credentials");
 CHECK(disconnects>before,"cleanup absorbed changed association without reissuing disconnect");
 if(after_disconnect)CHECK(pocket_wifi_attempting(&engine),"live late association lost cleanup exclusion before acknowledgement");
 step(false,expires_us);pocket_setup_state visible;pocket_setup_read(&visible);CHECK(!visible.active&&!visible.password[0],"no-IP cleanup prevented setup expiry");
 if(after_disconnect){CHECK(!pocket_setup_voice_acquire(),"voice acquired during late no-IP cleanup before fresh disconnect");disconnected(0);step(false,clock_us+1000);}
 CHECK(!pocket_wifi_attempting(&engine)&&pocket_setup_voice_acquire(),"acknowledged no-IP cleanup did not release radio ownership");pocket_setup_voice_release();return 0;
}
#endif
int main(int argc,char **argv) {
 (void)argc;const char *name=argv[1];
 snprintf(pair_config.windows,sizeof pair_config.windows,"https://windows.invalid");snprintf(pair_config.linux_url,sizeof pair_config.linux_url,"https://linux.invalid");
 snprintf(pair_config.token,sizeof pair_config.token,"private-token-fixture-00000000000000000000");snprintf(pair_config.ca,sizeof pair_config.ca,"PRIVATE_CA_FIXTURE");
 int result=1;
 if(!strcmp(name,"boot"))result=boot();
#ifndef POCKET_SETUP_BASELINE
 else if(!strcmp(name,"guards"))result=guards();
 else if(!strcmp(name,"ownership"))result=ownership();
 else if(!strcmp(name,"join"))result=join_events();
 else if(!strcmp(name,"expiry"))result=expiry();
 else if(!strcmp(name,"body-expiry"))result=body_expiry();
 else if(!strcmp(name,"cancel"))result=cancellation();
 else if(!strcmp(name,"queue-secret-cancel")||!strcmp(name,"queue-secret-expiry"))result=queued_secret(!strcmp(name,"queue-secret-expiry"));
 else if(!strcmp(name,"validation"))result=validation();
 else if(!strcmp(name,"pairing"))result=pairing_isolation();
 else if(!strcmp(name,"legacy")||!strcmp(name,"empty")||!strcmp(name,"corrupt")||!strcmp(name,"error"))result=migration(name);
 else if(!strcmp(name,"capacity"))result=capacity_and_forget();
 else if(!strcmp(name,"fallback"))result=failure_and_fallback();
 else if(!strcmp(name,"scan"))result=async_scan();
 else if(!strcmp(name,"automatic"))result=automatic_ownership();
 else if(!strcmp(name,"legacy-idle-disconnect-ok")||!strcmp(name,"legacy-idle-disconnect-not-connect"))result=legacy_idle_disconnect_ok(!strcmp(name,"legacy-idle-disconnect-not-connect"));
 else if(!strcmp(name,"legacy-query-error-recovery"))result=legacy_query_error_recovery();
 else if(!strcmp(name,"first-join-cancel-race")||!strcmp(name,"first-join-timeout-race")||!strcmp(name,"first-join-delayed-cleanup-race"))result=first_join_late_events(!strcmp(name,"first-join-timeout-race"),!strcmp(name,"first-join-delayed-cleanup-race"));
 else if(!strcmp(name,"disconnect-new-race")||!strcmp(name,"disconnect-replace-race"))result=disconnected_candidate_late_ip(!strcmp(name,"disconnect-replace-race"));
 else if(!strcmp(name,"forget-pending-race"))result=repeated_forget_pending();
 else if(!strcmp(name,"poll-setup-lease"))result=poll_setup_lease();
 else if(!strcmp(name,"poll-voice-lease"))result=poll_voice_lease();
 else if(!strcmp(name,"open-association"))result=open_association();
 else if(!strcmp(name,"cancel-forget-p2"))result=cancelled_queued_forget();
 else if(!strcmp(name,"cleanup-disconnect-associate-p2")||!strcmp(name,"cleanup-associate-disconnect-p2"))result=cleanup_late_association_no_ip(!strcmp(name,"cleanup-disconnect-associate-p2"));
#endif
 if(!result)printf("PASS setup %s\n",name);return result;
}
'''


class FirmwareSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('POCKET_HOST_CC') or shutil.which('clang') or shutil.which('gcc')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            raise unittest.SkipTest('host C compiler unavailable')
        root = pathlib.Path(__file__).resolve().parents[1]
        sdk = pathlib.Path(os.environ.get('IDF_PATH', str(root.parent/'HPsdk/idf-5.5.3')))
        cjson = sdk/'components/json/cJSON'
        if not (cjson/'cJSON.c').is_file():
            raise unittest.SkipTest('cached SDK cJSON unavailable; set IDF_PATH')
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        temp = pathlib.Path(cls.directory.name)
        for name, contents in HEADERS.items():
            path = temp/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding='utf-8')
        fixture = temp/'fixture.c'
        fixture.write_text(HARNESS, encoding='utf-8')
        cls.binary = temp/('setup.exe' if os.name == 'nt' else 'setup')
        baseline = os.environ.get('POCKET_SETUP_TEST_BASELINE')
        source_override = baseline or os.environ.get('POCKET_SETUP_TEST_SOURCE')
        options = ['-DPOCKET_SETUP_BASELINE=1'] if baseline else []
        if source_override:
            options.append('-DSETUP_SOURCE="'+pathlib.Path(source_override).as_posix()+'"')
        built = subprocess.run([
            compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-Wno-overlength-strings', '-DESP_PLATFORM=1', *options,
            '-I'+str(temp), '-I'+str(root/'firmware/main'), '-I'+str(root/'firmware/core'), '-I'+str(cjson),
            str(fixture), str(root/'firmware/core/pocket_wifi.c'), str(cjson/'cJSON.c'), '-o', str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise AssertionError('native setup harness did not compile:\n'+built.stdout+built.stderr)

    def exercise(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('PASS setup '+name, result.stdout)

    def test_unconfigured_boot_does_not_open_setup(self):
        self.exercise('boot')

    def test_ap_nonce_guards_and_public_status(self):
        self.exercise('guards')

    def test_voice_ownership_and_five_minute_expiry(self):
        self.exercise('ownership')

    def test_worker_requires_fresh_association_and_ip_before_save(self):
        self.exercise('join')

    def test_expired_queued_credentials_never_reach_radio_or_store(self):
        self.exercise('expiry')
        self.exercise('body-expiry')

    def test_cancelled_commands_never_commit(self):
        self.exercise('cancel')

    def test_rtos_queue_never_retains_candidate_password_after_cancel_or_expiry(self):
        for name in ('queue-secret-cancel', 'queue-secret-expiry'):
            with self.subTest(scenario=name):
                self.exercise(name)

    def test_bounded_exact_json_and_queue_overflow(self):
        self.exercise('validation')

    def test_pairing_payload_cannot_change_wifi_and_blanks_preserve_values(self):
        self.exercise('pairing')

    def test_only_absent_profiles_import_legacy_without_writing(self):
        for state in ('legacy', 'empty', 'corrupt', 'error'):
            with self.subTest(state=state):
                self.exercise(state)

    def test_full_store_requires_explicit_forget_and_saved_connect_uses_private_password(self):
        self.exercise('capacity')

    def test_failed_join_retains_saved_book_and_reconnects_last_network(self):
        self.exercise('fallback')

    def test_scan_runs_asynchronously_on_radio_worker(self):
        self.exercise('scan')

    def test_automatic_reconnect_and_voice_are_mutually_exclusive(self):
        self.exercise('automatic')

    def test_legacy_boot_connects_when_disconnect_succeeds_without_an_event(self):
        for name in ('legacy-idle-disconnect-ok', 'legacy-idle-disconnect-not-connect'):
            with self.subTest(scenario=name):
                self.exercise(name)

    def test_legacy_boot_station_query_error_fails_closed_and_recovers(self):
        self.exercise('legacy-query-error-recovery')

    def test_first_join_terminal_race_releases_ownership_and_allows_reopening(self):
        for name in ('first-join-cancel-race', 'first-join-timeout-race', 'first-join-delayed-cleanup-race'):
            with self.subTest(scenario=name):
                self.exercise(name)

    def test_disconnect_then_late_ip_race_cannot_save_new_or_replacement_credentials(self):
        for name in ('disconnect-new-race', 'disconnect-replace-race'):
            with self.subTest(scenario=name):
                self.exercise(name)

    def test_second_forget_race_reports_pending_before_worker(self):
        self.exercise('forget-pending-race')

    def test_poll_lease_defers_setup_and_blocks_voice_upgrade_after_gesture(self):
        self.exercise('poll-setup-lease')

    def test_poll_lease_upgrades_to_voice_and_release_preserves_voice_owner(self):
        self.exercise('poll-voice-lease')

    def test_open_association_cannot_validate_password_protected_profile(self):
        self.exercise('open-association')

    def test_p2_cancelled_queued_forget_reports_cancelled_and_allows_new_operation(self):
        self.exercise('cancel-forget-p2')

    def test_p2_cleanup_reissues_disconnect_for_changed_association_without_ip(self):
        for name in ('cleanup-disconnect-associate-p2', 'cleanup-associate-disconnect-p2'):
            with self.subTest(scenario=name):
                self.exercise(name)


if __name__ == '__main__':
    unittest.main()
