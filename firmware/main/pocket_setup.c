/* MIT. Device-initiated, time-limited WPA2 phone setup. No provider keys. */
#include "pocket_setup.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "esp_wifi.h"
#include "lwip/sockets.h"
#include "pocket_setup_address.h"
#include "pocket_setup_page.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static pocket_setup_state state;
static TaskHandle_t worker;
static httpd_handle_t http;
static char nonce[33];
typedef struct { uint32_t version; char ssid[32],pass[64],windows[193],linux_url[193]; } legacy_config;
typedef struct { uint32_t version; char ssid[32],pass[64],windows[193],linux_url[193],token[129],ca[2049]; } stored_config;
static bool load_config(stored_config *out) {
 memset(out,0,sizeof(*out));nvs_handle_t nvs;size_t size=0;
 if(nvs_open("pocket",NVS_READONLY,&nvs)!=ESP_OK)return false;
 esp_err_t e=nvs_get_blob(nvs,"config",NULL,&size);
 if(e==ESP_OK && size==sizeof(legacy_config)) {
  legacy_config old={0};e=nvs_get_blob(nvs,"config",&old,&size);
  if(e==ESP_OK && old.version==1){memcpy(out->ssid,old.ssid,sizeof(old.ssid));memcpy(out->pass,old.pass,sizeof(old.pass));memcpy(out->windows,old.windows,sizeof(old.windows));memcpy(out->linux_url,old.linux_url,sizeof(old.linux_url));out->version=2;}else e=ESP_FAIL;
  memset(&old,0,sizeof(old));
 }else if(e==ESP_OK && size==sizeof(*out)){e=nvs_get_blob(nvs,"config",out,&size);if(out->version!=2)e=ESP_FAIL;}
 else e=ESP_FAIL;
 nvs_close(nvs);
 return e==ESP_OK && out->ssid[0] && out->ssid[31]==0 && out->pass[63]==0
  && out->windows[192]==0 && out->linux_url[192]==0 && out->token[128]==0 && out->ca[2048]==0;
}
bool pocket_setup_link(unsigned target,pocket_link_config *out) {
 if(!out || target>1)return false;
 memset(out,0,sizeof(*out));stored_config *saved=calloc(1,sizeof(*saved));
 if(!saved)return false;
 bool valid=load_config(saved);
 if(valid){snprintf(out->origin,sizeof(out->origin),"%s",target?saved->linux_url:saved->windows);
  memcpy(out->token,saved->token,sizeof(out->token));memcpy(out->ca,saved->ca,sizeof(out->ca));}
 memset(saved,0,sizeof(*saved));free(saved);
 return valid && out->origin[0] && out->token[0];
}
static volatile bool reboot_pending;
static int64_t reboot_at;
static void message(const char *text) {
 portENTER_CRITICAL(&lock);snprintf(state.message,sizeof(state.message),"%s",text);portEXIT_CRITICAL(&lock);
}
void pocket_setup_read(pocket_setup_state *out) {portENTER_CRITICAL(&lock);*out=state;portEXIT_CRITICAL(&lock);}
static bool read_wifi(wifi_config_t *config) {
 stored_config *saved=calloc(1,sizeof(*saved));memset(config,0,sizeof(*config));if(!saved)return false;
 bool valid=load_config(saved);
 if(valid){memcpy(config->sta.ssid,saved->ssid,sizeof(saved->ssid));memcpy(config->sta.password,saved->pass,sizeof(saved->pass));}
 memset(saved,0,sizeof(*saved));free(saved);return valid;
}
static bool on_setup_interface(httpd_req_t *req) {
 struct sockaddr_storage local={0}; socklen_t length=sizeof(local);
 return getsockname(httpd_req_to_sockfd(req),(struct sockaddr*)&local,&length)==0
     && pocket_setup_address_matches(&local,length);
}
static esp_err_t page(httpd_req_t *req) {
 if(!on_setup_interface(req))return httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Connect to device setup Wi-Fi");
 httpd_resp_set_type(req,"text/html; charset=utf-8");httpd_resp_set_hdr(req,"Cache-Control","no-store");
 httpd_resp_set_hdr(req,"X-Content-Type-Options","nosniff");
 if(httpd_resp_send_chunk(req,setup_html_before,sizeof(setup_html_before)-1)!=ESP_OK ||
    httpd_resp_send_chunk(req,nonce,strlen(nonce))!=ESP_OK ||
    httpd_resp_send_chunk(req,setup_html_after,sizeof(setup_html_after)-1)!=ESP_OK)return ESP_FAIL;
 return httpd_resp_send_chunk(req,NULL,0);
}
static esp_err_t networks(httpd_req_t *req) {
 if(!on_setup_interface(req))return httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Connect to Pocket setup Wi-Fi");
 char check[40];
 if(httpd_req_get_hdr_value_str(req,"X-Pocket-Setup",check,sizeof(check))!=ESP_OK || strcmp(check,nonce))return httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Setup expired. Reopen setup on Pocket.");
 static int64_t last_scan;
 int64_t now=esp_timer_get_time();
 if(last_scan && now-last_scan<5000000)return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Please wait five seconds before scanning again.");
 last_scan=now;
 wifi_scan_config_t config={0};config.scan_type=WIFI_SCAN_TYPE_ACTIVE;config.scan_time.active.min=40;config.scan_time.active.max=100;
 esp_err_t result=esp_wifi_scan_start(&config,true);
 if(result!=ESP_OK)return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Wi-Fi busy. Try again or enter the network name.");
 uint16_t count=20;wifi_ap_record_t *records=calloc(count,sizeof(*records));
 if(!records){esp_wifi_clear_ap_list();return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Not enough memory. Try manual entry.");}
 result=esp_wifi_scan_get_ap_records(&count,records);
 if(result!=ESP_OK){esp_wifi_clear_ap_list();free(records);return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Scan failed. Try again.");}
 cJSON *root=cJSON_CreateObject();cJSON *list=root?cJSON_AddArrayToObject(root,"networks"):NULL;
 bool ok=list!=NULL;
 for(unsigned i=0;ok && i<count;i++){
  records[i].ssid[32]=0;
  if(!records[i].ssid[0])continue;
  bool duplicate=false;for(unsigned j=0;j<i;j++)if(!strcmp((char*)records[j].ssid,(char*)records[i].ssid))duplicate=true;
  if(duplicate)continue;
  cJSON *item=cJSON_CreateObject();if(!item){ok=false;break;}
  if(!cJSON_AddStringToObject(item,"ssid",(char*)records[i].ssid) || !cJSON_AddNumberToObject(item,"rssi",records[i].rssi) || !cJSON_AddBoolToObject(item,"open",records[i].authmode==WIFI_AUTH_OPEN)){cJSON_Delete(item);ok=false;break;}
  if(!cJSON_AddItemToArray(list,item)){cJSON_Delete(item);ok=false;break;}
 }
 free(records);char *json=ok?cJSON_PrintUnformatted(root):NULL;cJSON_Delete(root);
 if(!json)return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Could not prepare network list.");
 httpd_resp_set_type(req,"application/json");httpd_resp_set_hdr(req,"Cache-Control","no-store");
 result=httpd_resp_sendstr(req,json);cJSON_free(json);return result;
}
static const char *field(cJSON *root,const char *key) {
 cJSON *value=cJSON_GetObjectItemCaseSensitive(root,key);
 return cJSON_IsString(value)?value->valuestring:NULL;
}
static bool endpoint(const char *url) {
 if(!url || strlen(url)>192)return false;
 if(!*url)return true;
 if(strncmp(url,"https://",8)!=0 || strlen(url)<9)return false;
 for(const char *p=url;*p;p++)if((unsigned char)*p<33 || (unsigned char)*p>126 || *p=='@' || *p=='?' || *p=='#')return false;
 return true;
}
static esp_err_t save(httpd_req_t *req) {
 if(!on_setup_interface(req))return httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Connect to device setup Wi-Fi");
 char check[40],content[40];
 if(httpd_req_get_hdr_value_str(req,"X-Pocket-Setup",check,sizeof(check))!=ESP_OK || strcmp(check,nonce) || httpd_req_get_hdr_value_str(req,"Content-Type",content,sizeof(content))!=ESP_OK || strcmp(content,"application/json"))return httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Setup authorization required");
 if(req->content_len<=0 || req->content_len>4096)return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Invalid settings size");
 char *body=calloc(4097,1);if(!body)return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Not enough memory. Please retry.");int got=0;
 while(got<req->content_len){int n=httpd_req_recv(req,body+got,req->content_len-got);if(n<=0){memset(body,0,4097);free(body);return ESP_FAIL;}got+=n;}
 body[got]=0;cJSON *root=cJSON_Parse(body);
 const char *ssid=field(root,"ssid"),*pass=field(root,"password"),*win=field(root,"windows"),*linux_url=field(root,"linux"),*token=field(root,"token"),*ca=field(root,"ca");
 if(!cJSON_IsObject(root) || cJSON_GetArraySize(root)!=6 || !ssid || strlen(ssid)<1 || strlen(ssid)>31 || !pass || strlen(pass)<8 || strlen(pass)>63 || !endpoint(win) || !endpoint(linux_url) || !token || strlen(token)>128 || (*token && strlen(token)<32) || !ca || strlen(ca)>2048 || (*ca && (strncmp(ca,"-----BEGIN CERTIFICATE-----",27) || !strstr(ca,"-----END CERTIFICATE-----")))) {
  cJSON_Delete(root);memset(body,0,4097);free(body);return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Check Wi-Fi name/password and HTTPS bridge addresses");
 }
 stored_config saved={.version=2};
 for(const char *t=token;*t;t++)if((unsigned char)*t<33 || (unsigned char)*t>126){cJSON_Delete(root);memset(body,0,4097);free(body);return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Invalid device token");}
 snprintf(saved.ssid,sizeof(saved.ssid),"%s",ssid);snprintf(saved.pass,sizeof(saved.pass),"%s",pass);
 snprintf(saved.windows,sizeof(saved.windows),"%s",win);snprintf(saved.linux_url,sizeof(saved.linux_url),"%s",linux_url);
 snprintf(saved.token,sizeof(saved.token),"%s",token);snprintf(saved.ca,sizeof(saved.ca),"%s",ca);
 nvs_handle_t nvs;esp_err_t result=nvs_open("pocket",NVS_READWRITE,&nvs);
 if(result==ESP_OK){result=nvs_set_blob(nvs,"config",&saved,sizeof(saved));if(result==ESP_OK)result=nvs_commit(nvs);nvs_close(nvs);}
 memset(&saved,0,sizeof(saved));
 cJSON_Delete(root);memset(body,0,4097);free(body);
 if(result!=ESP_OK)return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Could not save settings");
 message("Saved. Restarting...");reboot_at=esp_timer_get_time()+2000000;reboot_pending=true;
 httpd_resp_set_hdr(req,"Cache-Control","no-store");return httpd_resp_sendstr(req,"Saved. Device restarting. Rejoin your normal Wi-Fi.");
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data) {
 (void)arg;(void)data;
 if(base==IP_EVENT && id==IP_EVENT_STA_GOT_IP){portENTER_CRITICAL(&lock);state.wifi_connected=true;portEXIT_CRITICAL(&lock);}
 if(base==WIFI_EVENT && id==WIFI_EVENT_STA_DISCONNECTED){portENTER_CRITICAL(&lock);state.wifi_connected=false;portEXIT_CRITICAL(&lock);}
}
static void work(void *arg) {
 (void)arg;int64_t expires=0,last_attempt=0;
 for(;;){
  if(ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(250)) && !http){
   uint8_t random[16];esp_fill_random(random,sizeof(random));
   wifi_config_t config={0};config.ap.max_connection=1;config.ap.authmode=WIFI_AUTH_WPA2_PSK;config.ap.pmf_cfg.required=true;
   snprintf((char*)config.ap.ssid,sizeof(config.ap.ssid),"Pocket-%02X%02X",random[0],random[1]);
   static const char alphabet[]="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
   for(int i=0;i<8;i++)config.ap.password[i]=alphabet[random[i]&31];
   config.ap.password[8]=0;
   esp_fill_random(random,sizeof(random));
   for(int i=0;i<16;i++)snprintf(nonce+2*i,3,"%02x",random[i]);
   if(esp_wifi_set_mode(WIFI_MODE_APSTA)!=ESP_OK || esp_wifi_set_config(WIFI_IF_AP,&config)!=ESP_OK){esp_wifi_set_mode(WIFI_MODE_STA);message("Could not start setup Wi-Fi");continue;}
   httpd_config_t h=HTTPD_DEFAULT_CONFIG();h.max_open_sockets=2;h.recv_wait_timeout=5;h.send_wait_timeout=5;h.stack_size=12288;
   if(httpd_start(&http,&h)==ESP_OK){
    httpd_uri_t root={.uri="/",.method=HTTP_GET,.handler=page};httpd_register_uri_handler(http,&root);
    httpd_uri_t post={.uri="/save",.method=HTTP_POST,.handler=save};httpd_register_uri_handler(http,&post);
    httpd_uri_t scan={.uri="/networks",.method=HTTP_GET,.handler=networks};httpd_register_uri_handler(http,&scan);
    expires=esp_timer_get_time()+300000000;
    portENTER_CRITICAL(&lock);state.active=true;snprintf(state.ssid,sizeof(state.ssid),"%s",config.ap.ssid);snprintf(state.password,sizeof(state.password),"%.24s",config.ap.password);portEXIT_CRITICAL(&lock);
    message("Phone: http://192.168.4.1");
   }else {http=NULL;esp_wifi_set_mode(WIFI_MODE_STA);message("Setup unavailable. Retry.");}
  }
  if(http && esp_timer_get_time()>expires){httpd_stop(http);http=NULL;esp_wifi_set_mode(WIFI_MODE_STA);portENTER_CRITICAL(&lock);state.active=false;memset(state.password,0,sizeof(state.password));portEXIT_CRITICAL(&lock);message("Setup expired. Hold OK.");}
  if(reboot_pending && esp_timer_get_time()>reboot_at)esp_restart();
  pocket_setup_state copy;pocket_setup_read(&copy);
  if(!http && copy.configured && !copy.wifi_connected && esp_timer_get_time()-last_attempt>10000000){esp_wifi_connect();last_attempt=esp_timer_get_time();}
 }
}
esp_err_t pocket_setup_init(void) {
 /* Never erase existing NVS automatically on an initialization error. */
 esp_err_t result=nvs_flash_init();if(result!=ESP_OK)return result;
 ESP_ERROR_CHECK(esp_netif_init());ESP_ERROR_CHECK(esp_event_loop_create_default());
 esp_netif_create_default_wifi_sta();esp_netif_create_default_wifi_ap();
 wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();ESP_ERROR_CHECK(esp_wifi_init(&init));
 ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
 ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));
 ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
 wifi_config_t config;state.configured=read_wifi(&config);
 if(state.configured)ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&config));
 memset(&config,0,sizeof(config));ESP_ERROR_CHECK(esp_wifi_start());
 if(xTaskCreate(work,"pocket_setup",6144,NULL,4,&worker)!=pdPASS)return ESP_ERR_NO_MEM;
 if(!state.configured)pocket_setup_request();
 return ESP_OK;
}
void pocket_setup_request(void){if(worker)xTaskNotifyGive(worker);}
