/* MIT. Serial network-worker transport. TLS is verified, POSTs never retried. */
#include "pocket_http.h"
#include <string.h>
#include <stdio.h>
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
static esp_http_client_handle_t client;
static char previous_origin[193],previous_token[129],previous_ca[2049];
typedef struct { char *out;size_t capacity,length;bool overflow; } response_buffer;
static bool clean(const char *s) {
 if(!s)return false;
 for(;*s;s++)if((unsigned char)*s<33 || (unsigned char)*s>126)return false;
 return true;
}
static esp_err_t event(esp_http_client_event_t *e) {
 response_buffer *b=e->user_data;
 if(e->event_id==HTTP_EVENT_ON_DATA && b && e->data_len>0) {
  if((size_t)e->data_len>b->capacity-1-b->length){b->overflow=true;return ESP_ERR_INVALID_SIZE;}
  memcpy(b->out+b->length,e->data,(size_t)e->data_len);b->length+=(size_t)e->data_len;b->out[b->length]=0;
 }
 return ESP_OK;
}
void pocket_http_close(void) {
 if(client){esp_http_client_cleanup(client);client=NULL;}
 memset(previous_token,0,sizeof(previous_token));previous_origin[0]=0;previous_ca[0]=0;
}
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,
 const char *ca,const char *body,char *out,size_t cap,pocket_http_result *result) {
 if(result)*result=(pocket_http_result){0};
 if(out && cap)out[0]=0;
 if(!result || !out || cap<2 || cap>32769 || !clean(origin) || !clean(path)
  || strncmp(origin,"https://",8) || strlen(origin)<9 || strlen(origin)>192
  || strchr(origin,'@') || strchr(origin,'?') || strchr(origin,'#')
  || path[0]!='/' || path[1]=='/' || strchr(path,'?') || strchr(path,'#') || strlen(path)>192
  || !clean(token) || strlen(token)<32 || strlen(token)>128 || !ca || strlen(ca)>2048
  || (body && strlen(body)>32768))return ESP_ERR_INVALID_ARG;
 char url[386];size_t n=strlen(origin);
 int size=snprintf(url,sizeof(url),"%.*s%s",(int)(n-(origin[n-1]=='/')),origin,path);
 if(size<0 || size>=(int)sizeof(url))return ESP_ERR_INVALID_SIZE;
 if(client && (strcmp(origin,previous_origin) || strcmp(token,previous_token) || strcmp(ca,previous_ca)))pocket_http_close();
 response_buffer buffer={.out=out,.capacity=cap};
 if(!client) {
  snprintf(previous_origin,sizeof(previous_origin),"%s",origin);snprintf(previous_token,sizeof(previous_token),"%s",token);
  snprintf(previous_ca,sizeof(previous_ca),"%s",ca);
  esp_http_client_config_t config={.url=url,.timeout_ms=15000,.disable_auto_redirect=true,
   .buffer_size=2048,.buffer_size_tx=1024,.keep_alive_enable=true,.event_handler=event,.user_data=&buffer};
  if(previous_ca[0])config.cert_pem=previous_ca;else config.crt_bundle_attach=esp_crt_bundle_attach;
  client=esp_http_client_init(&config);if(!client)return ESP_ERR_NO_MEM;
 }
 esp_err_t error=esp_http_client_set_url(client,url);
 if(error==ESP_OK)error=esp_http_client_set_user_data(client,&buffer);
 if(error==ESP_OK)error=esp_http_client_set_method(client,body?HTTP_METHOD_POST:HTTP_METHOD_GET);
 char auth[136];snprintf(auth,sizeof(auth),"Bearer %s",token);
 if(error==ESP_OK)error=esp_http_client_set_header(client,"Authorization",auth);
 if(error==ESP_OK)error=esp_http_client_set_header(client,"Accept","application/json");
 if(error==ESP_OK)error=esp_http_client_set_header(client,"Content-Type","application/json");
 if(error==ESP_OK)error=esp_http_client_set_post_field(client,body?body:"",body?(int)strlen(body):0);
 if(error==ESP_OK)error=esp_http_client_perform(client);
 result->status=esp_http_client_get_status_code(client);result->length=buffer.length;
 if(buffer.overflow)error=ESP_ERR_INVALID_SIZE;
 esp_http_client_set_user_data(client,NULL);memset(auth,0,sizeof(auth));
 if(error!=ESP_OK){out[0]=0;result->length=0;pocket_http_close();}
 return error;
}
