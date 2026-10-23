/* MIT. Serial network-worker transport. TLS is verified, POSTs never retried. */
#include "pocket_http.h"
#include <string.h>
#include <stdio.h>
#include <time.h>
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "lwip/sockets.h"
static esp_http_client_handle_t client;
static bool client_nodelay;
static char previous_origin[193],previous_token[129],previous_ca[2049];
typedef struct {
 char *out;size_t capacity,length;bool overflow;
 int64_t started_us;
 unsigned connect_ms,headers_ms,response_ms,body_ms,finish_ms,event_us;
 bool connected,headers_sent,response_started,nodelay,body_started,finished;
} response_buffer;
static unsigned elapsed_us(int64_t start){return (unsigned)(esp_timer_get_time()-start);}
static bool clean(const char *s) {
 if(!s)return false;
 for(;*s;s++)if((unsigned char)*s<33 || (unsigned char)*s>126)return false;
 return true;
}
static bool urlsafe(char c) {
 return (c>='a' && c<='z') || (c>='A' && c<='Z') || (c>='0' && c<='9') || c=='_' || c=='-';
}
static bool cursor_value(const char *value) {
 size_t length=0;
 for(;*value;value++)if(length++==128 || !urlsafe(*value))return false;
 return length>0;
}
static bool generation_value(const char *value) {
 size_t length=0;
 for(;*value;value++)if(length++==32 || !((*value>='0' && *value<='9') || (*value>='a' && *value<='f')))return false;
 return length==32;
}
static bool consume(const char **text,const char *prefix) {
 size_t length=strlen(prefix);
 if(strncmp(*text,prefix,length))return false;
 *text+=length;return true;
}
static bool bounded_index(const char **text,unsigned maximum) {
 const char *at=*text;unsigned value=0;
 if(*at<'0' || *at>'9' || (*at=='0' && at[1]>='0' && at[1]<='9'))return false;
 do {value=value*10+(unsigned)(*at++-'0');if(value>maximum)return false;}while(*at>='0' && *at<='9');
 *text=at;return true;
}
static bool reply_query(const char *path) {
 const char *route=path;
 if(!consume(&route,"/targets/linux/tasks/") && !consume(&route,"/targets/windows/tasks/"))return false;
 size_t length=0;
 for(;*route && *route!='/';route++)if(length++==64 || !urlsafe(*route))return false;
 if(!length)return false;
 const char *value=route;
 if(consume(&value,"/result/pages?cursor="))return cursor_value(value);
 value=route;
 if(consume(&value,"/speech/v2?generation="))return generation_value(value);
 value=route;
 return consume(&value,"/speech/v2/segments/") && bounded_index(&value,11)
  && consume(&value,"/chunks/") && bounded_index(&value,156)
  && consume(&value,"?generation=") && generation_value(value);
}
static bool valid_path(const char *path,bool post) {
 if(path[0]!='/' || path[1]=='/' || strchr(path,'#'))return false;
 if(!strchr(path,'?'))return strlen(path)<=192;
 /* Only reply GETs carry queries. The longest is 64-byte task + 128-byte cursor. */
 return !post && strlen(path)<=236 && reply_query(path);
}
static esp_err_t event(esp_http_client_event_t *e) {
 response_buffer *b=e->user_data;int64_t began=esp_timer_get_time();esp_err_t error=ESP_OK;
 if(e->event_id==HTTP_EVENT_ON_CONNECTED){
  int fd=esp_http_client_get_socket(e->client),enabled=1;
  client_nodelay=fd>=0 && setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&enabled,sizeof enabled)==0;
  if(b){b->nodelay=client_nodelay;b->connected=true;b->connect_ms=(unsigned)((esp_timer_get_time()-b->started_us)/1000);}
 }
 if(e->event_id==HTTP_EVENT_DISCONNECTED)client_nodelay=false;
 if(e->event_id==HTTP_EVENT_HEADERS_SENT && b){pocket_http_timing_headers_sent();b->headers_sent=true;b->headers_ms=(unsigned)((esp_timer_get_time()-b->started_us)/1000);}
 if((e->event_id==HTTP_EVENT_ON_HEADER || e->event_id==HTTP_EVENT_ON_DATA) && b && !b->response_started){
  b->response_started=true;b->response_ms=(unsigned)((esp_timer_get_time()-b->started_us)/1000);
 }
 if(e->event_id==HTTP_EVENT_ON_FINISH && b){b->finished=true;b->finish_ms=elapsed_us(b->started_us)/1000;}
 if(e->event_id==HTTP_EVENT_ON_DATA && b && e->data_len>0) {
  if(!b->body_started){b->body_started=true;b->body_ms=elapsed_us(b->started_us)/1000;}
  if((size_t)e->data_len>b->capacity-1-b->length){b->overflow=true;error=ESP_ERR_INVALID_SIZE;}
  else {memcpy(b->out+b->length,e->data,(size_t)e->data_len);b->length+=(size_t)e->data_len;b->out[b->length]=0;}
 }
 if(b)b->event_us+=elapsed_us(began);
 return error;
}
void pocket_http_close(void) {
 if(client){esp_http_client_cleanup(client);client=NULL;}
 client_nodelay=false;
 memset(previous_token,0,sizeof(previous_token));previous_origin[0]=0;previous_ca[0]=0;
}
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,
 const char *ca,const char *body,char *out,size_t cap,pocket_http_result *result) {
 int64_t started_us=esp_timer_get_time();
 if(result)*result=(pocket_http_result){0};
 if(out && cap)out[0]=0;
 if(!result || !out || cap<2 || cap>32769 || !clean(origin) || !clean(path)
  || strncmp(origin,"https://",8) || strlen(origin)<9 || strlen(origin)>192
  || strchr(origin,'@') || strchr(origin,'?') || strchr(origin,'#')
  || !valid_path(path,body!=NULL)
  || !clean(token) || strlen(token)<32 || strlen(token)>128 || !ca || strlen(ca)>2048
  || (body && strlen(body)>32768))return ESP_ERR_INVALID_ARG;
 char url[429];size_t n=strlen(origin);
 int size=snprintf(url,sizeof(url),"%.*s%s",(int)(n-(origin[n-1]=='/')),origin,path);
 if(size<0 || size>=(int)sizeof(url))return ESP_ERR_INVALID_SIZE;
 unsigned target=!strncmp(path,"/targets/windows/",17)?0u:!strncmp(path,"/targets/linux/",15)?1u:2u;
 if(client && (strcmp(origin,previous_origin) || strcmp(token,previous_token) || strcmp(ca,previous_ca)))pocket_http_close();
 /* A reused capture socket needs no allocator walk on a successful chunk.
    New clients/boundaries retain pre/post diagnostics; reconnects retain post. */
 const char *tail=strrchr(path,'/');
 bool chunk=body && strstr(path,"/audio/") && tail && !strcmp(tail,"/chunk");
 bool measure_before=!chunk || !client;
 unsigned free_before=0,largest_before=0;
 if(measure_before){
  int64_t began=esp_timer_get_time();
  free_before=(unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
  largest_before=(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
  result->pre_heap_us=elapsed_us(began);result->heap_before_measured=true;
  result->free_heap=free_before;result->largest_block=largest_before;result->heap_measured=true;
 }
 response_buffer buffer={.out=out,.capacity=cap,.started_us=started_us,.nodelay=client_nodelay};
 if(!client) {
  snprintf(previous_origin,sizeof(previous_origin),"%s",origin);snprintf(previous_token,sizeof(previous_token),"%s",token);
  snprintf(previous_ca,sizeof(previous_ca),"%s",ca);
  esp_http_client_config_t config={.url=url,.timeout_ms=15000,.disable_auto_redirect=true,
   .buffer_size=2048,.buffer_size_tx=1024,.keep_alive_enable=true,.event_handler=event,.user_data=&buffer};
  if(previous_ca[0])config.cert_pem=previous_ca;else config.crt_bundle_attach=esp_crt_bundle_attach;
  client=esp_http_client_init(&config);if(!client){result->total_us=elapsed_us(started_us);return ESP_ERR_NO_MEM;}
 }
 esp_err_t error=esp_http_client_set_url(client,url);
 if(error==ESP_OK)error=esp_http_client_set_user_data(client,&buffer);
 if(error==ESP_OK)error=esp_http_client_set_method(client,body?HTTP_METHOD_POST:HTTP_METHOD_GET);
 char auth[136];snprintf(auth,sizeof(auth),"Bearer %s",token);
 if(error==ESP_OK)error=esp_http_client_set_header(client,"Authorization",auth);
 if(error==ESP_OK)error=esp_http_client_set_header(client,"Accept","application/json");
 if(error==ESP_OK)error=esp_http_client_set_header(client,"Content-Type","application/json");
 if(error==ESP_OK)error=esp_http_client_set_post_field(client,body?body:"",body?(int)strlen(body):0);
 if(error==ESP_OK){
  pocket_http_timing_begin(chunk);
  int64_t began=esp_timer_get_time();result->performed=true;
  error=esp_http_client_perform(client);
  result->perform_us=elapsed_us(began);result->perform_end_ms=elapsed_us(started_us)/1000;
  pocket_http_timing_end(&result->io);
 }
 result->status=esp_http_client_get_status_code(client);result->length=buffer.length;
 result->connect_ms=buffer.connect_ms;result->headers_ms=buffer.headers_ms;result->response_ms=buffer.response_ms;
 result->body_ms=buffer.body_ms;result->body_started=buffer.body_started;result->finish_ms=buffer.finish_ms;result->finished=buffer.finished;result->event_us=buffer.event_us;
 result->connected=buffer.connected;result->headers_sent=buffer.headers_sent;result->response_started=buffer.response_started;result->nodelay=buffer.nodelay;
 if(buffer.overflow)error=ESP_ERR_INVALID_SIZE;
 esp_http_client_set_user_data(client,NULL);memset(auth,0,sizeof(auth));
 unsigned free_after=0,largest_after=0;
 if(measure_before || result->connected || error!=ESP_OK || result->status<200 || result->status>=300){
  int64_t began=esp_timer_get_time();
  free_after=(unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
  largest_after=(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
  result->post_heap_us=elapsed_us(began);result->heap_measured=true;
  result->free_heap=free_after;result->largest_block=largest_after;
 }
 if(error!=ESP_OK){
  int64_t began=esp_timer_get_time();
  int tls_code=0,verify_flags=0;
  esp_err_t tls_error=esp_http_client_get_and_clear_last_tls_error(client,&tls_code,&verify_flags);
  result->tls_error=tls_error;result->tls_code=tls_code;result->verify_flags=verify_flags;
  /* Numeric-only diagnostics; never include endpoints, credentials or response data. */
  ESP_LOGW("pocket_tls","target=%u http=%d tls=0x%x mbedtls=0x%x verify=0x%x ca=%u utc_ready=%u free_before=%u largest_before=%u free_after=%u largest_after=%u heap_before=%u",
   target,error,(unsigned)tls_error,(unsigned)tls_code,(unsigned)verify_flags,
   (unsigned)(previous_ca[0]!=0),(unsigned)(time(NULL)>=1704067200),free_before,largest_before,free_after,largest_after,(unsigned)result->heap_before_measured);
  out[0]=0;result->length=0;pocket_http_close();result->diagnostic_us=elapsed_us(began);
 }else if(!strcmp(path,"/targets/linux/status") || !strcmp(path,"/targets/windows/status")){
  int64_t began=esp_timer_get_time();
  ESP_LOGI("pocket_tls_heap","target=%u http=%d free_before=%u largest_before=%u free_after=%u largest_after=%u",
   target,result->status,free_before,largest_before,free_after,largest_after);
#ifdef POCKET_LINUX_PUBLIC_ORIGIN
  if(target==1)ESP_LOGI("pocket_funnel_status","approved_origin=%u public_ca=%u http=%d",
                      (unsigned)!strcmp(origin,POCKET_LINUX_PUBLIC_ORIGIN),(unsigned)!ca[0],result->status);
#endif
  result->diagnostic_us=elapsed_us(began);
 }
 result->total_us=elapsed_us(started_us);
 return error;
}
