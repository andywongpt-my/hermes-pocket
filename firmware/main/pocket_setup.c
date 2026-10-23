/* MIT. Owner-initiated secure setup; one worker owns scans and radio changes. */
#include "pocket_setup.h"
#include "pocket_pairing.h"
#include "pocket_linux_link.h"
#include "pocket_wifi_store.h"
#include "pocket_setup_address.h"
#include "pocket_setup_page.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static pocket_setup_state state;
static pocket_wifi_snapshot radio;
static bool station_associated,station_request_pending;
static pocket_wifi_engine engine;
static pocket_wifi_store profile_store;
static TaskHandle_t worker;
static httpd_handle_t http;
static QueueHandle_t commands;
static char nonce[33];
static int64_t expires_us,last_scan_us;
enum { RADIO_IDLE,RADIO_VOICE,RADIO_SETUP,RADIO_AUTO,RADIO_POLL };
static atomic_uint radio_owner;
static atomic_bool setup_pending,cancel_requested;
typedef enum { CMD_SCAN,CMD_JOIN,CMD_SELECT,CMD_FORGET,CMD_CANCEL } command_kind;
typedef struct { command_kind kind;pocket_wifi_profile profile; } setup_command;
/* RTOS queue slots retain received bytes. Keep credentials out of them. */
static setup_command pending_command;
static uint32_t pending_operation;
typedef struct {
 char profiles[POCKET_WIFI_CAPACITY][33];
 pocket_wifi_visible networks[POCKET_WIFI_SCAN_MAX];
 unsigned count,network_count,scan_state,connection,join_result,operation;
 bool pending,paired,connection_busy;
} setup_public_state;
static setup_public_state public_state;

bool pocket_setup_voice_acquire(void){
 if(atomic_load(&setup_pending))return false;
 unsigned expected=RADIO_IDLE;
 if(atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_VOICE))return true;
 if(expected!=RADIO_POLL || atomic_load(&setup_pending))return false;
 return atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_VOICE);
}
void pocket_setup_voice_release(void){
 unsigned expected=RADIO_VOICE;(void)atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_IDLE);
}
bool pocket_setup_poll_acquire(void){
 if(atomic_load(&setup_pending))return false;
 unsigned expected=RADIO_IDLE;
 return atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_POLL);
}
bool pocket_setup_poll_continue(void){return atomic_load(&radio_owner)==RADIO_POLL && !atomic_load(&setup_pending);}
void pocket_setup_poll_release(void){
 unsigned expected=RADIO_POLL;
 if(atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_IDLE) && atomic_load(&setup_pending) && worker)xTaskNotifyGive(worker);
}
static void message(const char *text){portENTER_CRITICAL(&lock);snprintf(state.message,sizeof state.message,"%s",text);portEXIT_CRITICAL(&lock);}
void pocket_setup_read(pocket_setup_state *out){portENTER_CRITICAL(&lock);*out=state;portEXIT_CRITICAL(&lock);}
static pocket_wifi_snapshot snapshot(void){portENTER_CRITICAL(&lock);pocket_wifi_snapshot copy=radio;portEXIT_CRITICAL(&lock);return copy;}
#ifndef POCKET_LINUX_PUBLIC_ORIGIN
#define POCKET_LINUX_PUBLIC_ORIGIN ""
#endif
static bool link_reported[2];
static bool load_pairing_config(pocket_pairing_config *out){return pocket_pairing_load(out)==ESP_OK;}
bool pocket_setup_link(unsigned target,pocket_link_config *out) {
 if(!out || target>1)return false;
 memset(out,0,sizeof(*out));pocket_pairing_config *saved=calloc(1,sizeof(*saved));
 if(!saved)return false;
 bool valid=load_pairing_config(saved),public_link=false;
 if(valid){snprintf(out->origin,sizeof(out->origin),"%s",target?saved->linux_url:saved->windows);
  memcpy(out->token,saved->token,sizeof(out->token));memcpy(out->ca,saved->ca,sizeof(out->ca));
  if(target==1 && saved->token[0]){
   public_link=pocket_linux_link_select(POCKET_LINUX_PUBLIC_ORIGIN,saved->linux_url,out->origin,sizeof out->origin);
   if(public_link)memset(out->ca,0,sizeof out->ca); /* Existing verified public bundle path, Linux only. */
  }
 }
 bool report;portENTER_CRITICAL(&lock);report=!link_reported[target];link_reported[target]=true;portEXIT_CRITICAL(&lock);
 if(report)ESP_LOGI("pocket_link_mode","target=%u approved_origin=%u public_ca=%u legacy_preserved=%u",target,
                   (unsigned)public_link,(unsigned)(valid&&!out->ca[0]),(unsigned)valid);
 memset(saved,0,sizeof(*saved));free(saved);
 return valid && out->origin[0] && out->token[0];
}
static void migrate_linux_public(void){
 if(!POCKET_LINUX_PUBLIC_ORIGIN[0])return;
 pocket_pairing_config *before=calloc(1,sizeof *before),*after=calloc(1,sizeof *after);
 esp_err_t result=ESP_ERR_NO_MEM;bool preserved=false,wifi=false,windows=false,pairing=false,ca=false;
 if(before&&after){
  result=ESP_ERR_NOT_FOUND;
  if(load_pairing_config(before)&&before->token[0]){
   result=pocket_linux_link_apply_once(POCKET_LINUX_PUBLIC_ORIGIN,before->linux_url);
   if(load_pairing_config(after)){
    preserved=!memcmp(before,after,sizeof *before);
    wifi=!memcmp(before->ssid,after->ssid,sizeof before->ssid)&&!memcmp(before->pass,after->pass,sizeof before->pass);
    windows=!memcmp(before->windows,after->windows,sizeof before->windows);
    pairing=!memcmp(before->token,after->token,sizeof before->token);
    ca=!memcmp(before->ca,after->ca,sizeof before->ca);
   }
  }
 }
 ESP_LOGI("pocket_link_migration","result=%d preserved=%u wifi=%u windows=%u pairing=%u ca=%u",result,
          (unsigned)preserved,(unsigned)wifi,(unsigned)windows,(unsigned)pairing,(unsigned)ca);
 if(before){memset(before,0,sizeof *before);free(before);}if(after){memset(after,0,sizeof *after);free(after);}
}
static bool pairing_saved(void){
 pocket_pairing_config *saved=calloc(1,sizeof *saved);if(!saved)return false;
 bool valid=pocket_pairing_load(saved)==ESP_OK && saved->token[0] && (saved->windows[0] || saved->linux_url[0]);
 memset(saved,0,sizeof *saved);free(saved);return valid;
}
static bool on_setup_interface(httpd_req_t *req){
 struct sockaddr_storage local={0};socklen_t length=sizeof local;
 return getsockname(httpd_req_to_sockfd(req),(struct sockaddr*)&local,&length)==0 && pocket_setup_address_matches(&local,length);
}
static bool window_open(void){
 portENTER_CRITICAL(&lock);bool active=state.active;int64_t expires=expires_us;portEXIT_CRITICAL(&lock);
 return active && esp_timer_get_time()<expires;
}
static bool authorize(httpd_req_t *req,bool check_nonce){
 if(!on_setup_interface(req) || !window_open()){httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Setup expired. Hold OK on Status and reconnect.");return false;}
 char check[40];
 if(check_nonce && (httpd_req_get_hdr_value_str(req,"X-Pocket-Setup",check,sizeof check)!=ESP_OK || strcmp(check,nonce))){httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Setup authorization required");return false;}
 return true;
}
static esp_err_t send_json(httpd_req_t *req,cJSON *root){
 char *body=root?cJSON_PrintUnformatted(root):NULL;cJSON_Delete(root);
 if(!body)return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Not enough memory. Retry.");
 httpd_resp_set_type(req,"application/json");httpd_resp_set_hdr(req,"Cache-Control","no-store");
 esp_err_t error=httpd_resp_sendstr(req,body);cJSON_free(body);return error;
}
static esp_err_t page(httpd_req_t *req){
 if(!authorize(req,false))return ESP_OK;
 httpd_resp_set_type(req,"text/html; charset=utf-8");httpd_resp_set_hdr(req,"Cache-Control","no-store");httpd_resp_set_hdr(req,"X-Content-Type-Options","nosniff");
 if(httpd_resp_send_chunk(req,setup_html_before,sizeof setup_html_before-1)!=ESP_OK || httpd_resp_send_chunk(req,nonce,strlen(nonce))!=ESP_OK
  || httpd_resp_send_chunk(req,setup_html_after,sizeof setup_html_after-1)!=ESP_OK)return ESP_FAIL;
 return httpd_resp_send_chunk(req,NULL,0);
}
static esp_err_t wifi_status(httpd_req_t *req){
 if(!authorize(req,true))return ESP_OK;
 portENTER_CRITICAL(&lock);setup_public_state copy=public_state;pocket_wifi_snapshot current=radio;int64_t expires=expires_us;portEXIT_CRITICAL(&lock);
 cJSON *root=cJSON_CreateObject(),*profiles=root?cJSON_AddArrayToObject(root,"profiles"):NULL,*networks=root?cJSON_AddArrayToObject(root,"networks"):NULL;
 if(!profiles || !networks){cJSON_Delete(root);return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Not enough memory. Retry.");}
 bool ok=cJSON_AddBoolToObject(root,"connected",current.connected) && cJSON_AddStringToObject(root,"ssid",current.connected?current.ssid:"")
  && cJSON_AddNumberToObject(root,"connection",copy.connection) && cJSON_AddNumberToObject(root,"join_result",copy.join_result)
  && cJSON_AddNumberToObject(root,"operation",copy.operation) && cJSON_AddNumberToObject(root,"capacity",POCKET_WIFI_CAPACITY)
  && cJSON_AddBoolToObject(root,"connection_busy",copy.connection_busy)
  && cJSON_AddNumberToObject(root,"scan_state",copy.scan_state) && cJSON_AddBoolToObject(root,"pairing_saved",copy.paired)
  && cJSON_AddBoolToObject(root,"store_writable",profile_store.writable)
  && cJSON_AddNumberToObject(root,"expires_seconds",expires>esp_timer_get_time()?(double)((expires-esp_timer_get_time())/1000000):0);
 for(unsigned i=0;ok && i<copy.count;i++){
  cJSON *item=cJSON_CreateObject();ok=item && cJSON_AddStringToObject(item,"ssid",copy.profiles[i])
   && cJSON_AddBoolToObject(item,"connected",current.connected && !strcmp(copy.profiles[i],current.ssid));
  if(ok)ok=cJSON_AddItemToArray(profiles,item);else cJSON_Delete(item);
 }
 for(unsigned i=0;ok && i<copy.network_count;i++){
  cJSON *item=cJSON_CreateObject();ok=item && cJSON_AddStringToObject(item,"ssid",copy.networks[i].ssid)
   && cJSON_AddNumberToObject(item,"rssi",copy.networks[i].rssi) && cJSON_AddBoolToObject(item,"open",copy.networks[i].open);
  if(ok)ok=cJSON_AddItemToArray(networks,item);else cJSON_Delete(item);
 }
 if(!ok){cJSON_Delete(root);root=NULL;}
 return send_json(req,root);
}
static void delete_secret_json(cJSON *root){
 if(root)for(cJSON *item=root->child;item;item=item->next)if(cJSON_IsString(item) && item->valuestring)memset(item->valuestring,0,strlen(item->valuestring));
 cJSON_Delete(root);
}
static cJSON *read_json(httpd_req_t *req,size_t maximum){
 if(!authorize(req,true))return NULL;
 char content[40];
 if(httpd_req_get_hdr_value_str(req,"Content-Type",content,sizeof content)!=ESP_OK || strcmp(content,"application/json")){httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Use JSON settings");return NULL;}
 if(req->content_len<=0 || (size_t)req->content_len>maximum){httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Invalid settings size");return NULL;}
 char *body=calloc((size_t)req->content_len+1,1);if(!body){httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Not enough memory. Retry.");return NULL;}
 int received=0;
 while(received<req->content_len){int got=httpd_req_recv(req,body+received,req->content_len-received);if(got<=0)break;received+=got;}
 bool zero=memchr(body,0,(size_t)received)!=NULL;
 for(int i=0;i<received;i++)if(body[i]=='\\' && i+1<received){i++;if(body[i]=='u' && i+4<received && !memcmp(body+i+1,"0000",4))zero=true;}
 cJSON *root=received==req->content_len && !zero?cJSON_ParseWithLengthOpts(body,(size_t)received+1,NULL,true):NULL;
 memset(body,0,(size_t)req->content_len+1);free(body);
 if(!cJSON_IsObject(root)){delete_secret_json(root);httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Invalid JSON settings");return NULL;}
 return root;
}
static const char *field(cJSON *root,const char *key){cJSON *value=cJSON_GetObjectItemCaseSensitive(root,key);return cJSON_IsString(value)?value->valuestring:NULL;}
static bool fields(cJSON *root,const char *const *names,unsigned count){
 if((unsigned)cJSON_GetArraySize(root)!=count)return false;
 for(unsigned i=0;i<count;i++)if(!field(root,names[i]))return false;
 return true;
}
static esp_err_t enqueue(httpd_req_t *req,setup_command *command){
 if(!window_open()){memset(command,0,sizeof *command);return httpd_resp_send_err(req,HTTPD_403_FORBIDDEN,"Setup expired. Reopen setup.");}
 if(command->kind==CMD_CANCEL){
  atomic_store(&cancel_requested,true);memset(command,0,sizeof *command);
  portENTER_CRITICAL(&lock);unsigned operation=++public_state.operation;portEXIT_CRITICAL(&lock);
  cJSON *root=cJSON_CreateObject();if(root){cJSON_AddBoolToObject(root,"accepted",true);cJSON_AddNumberToObject(root,"operation",operation);}
  httpd_resp_set_status(req,"202 Accepted");return send_json(req,root);
 }
 portENTER_CRITICAL(&lock);bool busy=public_state.pending || public_state.connection_busy || public_state.join_result==POCKET_WIFI_JOIN_TESTING;
 unsigned previous_join=public_state.join_result;
 busy=busy || atomic_load(&cancel_requested);
 if(!busy){public_state.pending=true;public_state.operation++;if(!public_state.operation)public_state.operation=1;pending_command=*command;pending_operation=public_state.operation;if(command->kind==CMD_SCAN)public_state.scan_state=1;else public_state.join_result=POCKET_WIFI_JOIN_TESTING;}
 unsigned operation=public_state.operation;portEXIT_CRITICAL(&lock);
 if(busy){memset(command,0,sizeof *command);return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"A Wi-Fi operation is already running. Wait or cancel.");}
 BaseType_t sent=xQueueSend(commands,&operation,0);memset(command,0,sizeof *command);
 if(sent!=pdTRUE){portENTER_CRITICAL(&lock);if(pending_operation==operation){memset(&pending_command,0,sizeof pending_command);pending_operation=0;public_state.pending=false;public_state.join_result=previous_join;}portEXIT_CRITICAL(&lock);return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"Wi-Fi queue busy. Retry.");}
 cJSON *root=cJSON_CreateObject();if(root){cJSON_AddBoolToObject(root,"accepted",true);cJSON_AddNumberToObject(root,"operation",operation);}
 httpd_resp_set_status(req,"202 Accepted");return send_json(req,root);
}
static esp_err_t networks(httpd_req_t *req){
 if(!authorize(req,true))return ESP_OK;
 int64_t now=esp_timer_get_time();
 if(last_scan_us && now-last_scan_us<5000000)return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Wait five seconds before scanning again.");
 last_scan_us=now;setup_command command={.kind=CMD_SCAN};return enqueue(req,&command);
}
static esp_err_t wifi_join(httpd_req_t *req){
 cJSON *root=read_json(req,1024);if(!root)return ESP_OK;
 static const char *const names[]={"ssid","password"};setup_command command={.kind=CMD_JOIN};
 const char *ssid=field(root,"ssid"),*password=field(root,"password");
 bool valid=fields(root,names,2) && ssid && password && strlen(ssid)<=32 && strlen(password)<=63;
 if(valid){snprintf(command.profile.ssid,sizeof command.profile.ssid,"%s",ssid);snprintf(command.profile.password,sizeof command.profile.password,"%s",password);valid=pocket_wifi_profile_valid(&command.profile);}
 delete_secret_json(root);
 if(!valid || !profile_store.writable){memset(&command,0,sizeof command);return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,valid?"Saved networks need recovery. Existing settings retained.":"Check network name and password.");}
 return enqueue(req,&command);
}
static esp_err_t wifi_control(httpd_req_t *req){
 cJSON *root=read_json(req,192);if(!root)return ESP_OK;
 bool cancel=!strcmp(req->uri,"/wifi/cancel");setup_command command={.kind=cancel?CMD_CANCEL:!strcmp(req->uri,"/wifi/forget")?CMD_FORGET:CMD_SELECT};
 static const char *const names[]={"ssid"};const char *ssid=field(root,"ssid");
 bool valid=cancel?cJSON_GetArraySize(root)==0:fields(root,names,1) && ssid && strlen(ssid)>0 && strlen(ssid)<=32;
 if(valid && !cancel)snprintf(command.profile.ssid,sizeof command.profile.ssid,"%s",ssid);
 delete_secret_json(root);
 if(!valid || (command.kind==CMD_FORGET && !profile_store.writable))return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Cannot apply this Wi-Fi action. Settings retained.");
 return enqueue(req,&command);
}
static esp_err_t pairing(httpd_req_t *req){
 cJSON *root=read_json(req,4096);if(!root)return ESP_OK;
 static const char *const names[]={"windows","linux","token","ca"};esp_err_t error=ESP_ERR_INVALID_ARG;
 if(fields(root,names,4) && window_open())error=pocket_pairing_update(field(root,"windows"),field(root,"linux"),field(root,"token"),field(root,"ca"));
 delete_secret_json(root);
 if(error!=ESP_OK)return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Could not update pairing. Check HTTPS settings; existing Wi-Fi retained.");
 bool paired=pairing_saved();portENTER_CRITICAL(&lock);public_state.paired=paired;portEXIT_CRITICAL(&lock);
 httpd_resp_set_hdr(req,"Cache-Control","no-store");return httpd_resp_sendstr(req,"Pairing saved. Blank fields preserved. Wi-Fi unchanged.");
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data){
 (void)arg;portENTER_CRITICAL(&lock);
 if(base==WIFI_EVENT && id==WIFI_EVENT_STA_CONNECTED){
  const wifi_event_sta_connected_t *connected=data;
  const bool secure=connected && (connected->authmode==WIFI_AUTH_WPA2_PSK || connected->authmode==WIFI_AUTH_WPA_WPA2_PSK
   || connected->authmode==WIFI_AUTH_WPA3_PSK || connected->authmode==WIFI_AUTH_WPA2_WPA3_PSK
   || connected->authmode==WIFI_AUTH_WPA3_EXT_PSK || connected->authmode==WIFI_AUTH_WPA3_EXT_PSK_MIXED_MODE);
  station_associated=secure && connected->ssid_len>0 && connected->ssid_len<=32;
  radio.connected=false;state.wifi_connected=false;
  if(station_associated){memset(radio.ssid,0,sizeof radio.ssid);memcpy(radio.ssid,connected->ssid,connected->ssid_len);radio.association_ip=radio.got_ip;radio.associated++;}
  else {radio.disconnected++;radio.reason=POCKET_WIFI_REASON_AUTH;}
 }
 if(base==IP_EVENT && id==IP_EVENT_STA_GOT_IP && station_associated){radio.connected=true;radio.got_ip++;state.wifi_connected=true;}
 if(base==WIFI_EVENT && id==WIFI_EVENT_STA_DISCONNECTED){
  const wifi_event_sta_disconnected_t *disconnected=data;unsigned reason=disconnected?disconnected->reason:0;
  station_associated=false;station_request_pending=false;radio.connected=false;radio.disconnected++;state.wifi_connected=false;
  radio.reason=reason==WIFI_REASON_AUTH_FAIL || reason==WIFI_REASON_HANDSHAKE_TIMEOUT || reason==WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT?POCKET_WIFI_REASON_AUTH:
   reason==WIFI_REASON_NO_AP_FOUND || reason==WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY || reason==WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD || reason==WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD?POCKET_WIFI_REASON_NOT_FOUND:POCKET_WIFI_REASON_OTHER;
 }
 portEXIT_CRITICAL(&lock);
}
static bool scan_ok;
static size_t radio_scan(void *unused,pocket_wifi_visible *out,size_t capacity){
 (void)unused;scan_ok=false;wifi_scan_config_t config={0};config.scan_type=WIFI_SCAN_TYPE_ACTIVE;config.scan_time.active.min=40;config.scan_time.active.max=100;
 if(esp_wifi_scan_start(&config,true)!=ESP_OK)return 0;
 uint16_t count=POCKET_WIFI_SCAN_MAX;wifi_ap_record_t *records=calloc(count,sizeof *records);
 if(!records){esp_wifi_clear_ap_list();return 0;}
 esp_err_t error=esp_wifi_scan_get_ap_records(&count,records);
 if(error!=ESP_OK){esp_wifi_clear_ap_list();free(records);return 0;}
 size_t used=0;
 for(unsigned i=0;i<count && used<capacity;i++){
  records[i].ssid[32]=0;if(!records[i].ssid[0])continue;
  bool duplicate=false;for(size_t j=0;j<used;j++)if(!strcmp(out[j].ssid,(char*)records[i].ssid))duplicate=true;
  if(duplicate)continue;
  snprintf(out[used].ssid,sizeof out[used].ssid,"%s",(char*)records[i].ssid);out[used].rssi=records[i].rssi;out[used].open=records[i].authmode==WIFI_AUTH_OPEN;used++;
 }
 free(records);scan_ok=true;return used;
}
static int radio_disconnect(void *unused){
 (void)unused;wifi_ap_record_t previous={0};esp_err_t query=esp_wifi_sta_get_ap_info(&previous);memset(&previous,0,sizeof previous);
 if(query!=ESP_OK && query!=ESP_ERR_WIFI_NOT_CONNECT)return -1;
 /* IDF succeeds without a disconnect event when no association exists.
    An existing association still requires its fresh disconnect callback. */
 portENTER_CRITICAL(&lock);bool pending=station_request_pending;portEXIT_CRITICAL(&lock);
 const bool associated=query==ESP_OK;esp_err_t error=esp_wifi_disconnect();
 ESP_LOGI("pocket_wifi_driver","associated=%u pending=%u query=%d disconnect=%d",(unsigned)associated,(unsigned)pending,query,error);
 if(error==ESP_OK || error==ESP_ERR_WIFI_NOT_CONNECT){portENTER_CRITICAL(&lock);station_associated=false;radio.connected=false;state.wifi_connected=false;portEXIT_CRITICAL(&lock);}
 /* Only initial idle can retire synchronously. A previously issued connect
    remains fenced until its callback, even if association has not completed. */
 return error==ESP_OK || error==ESP_ERR_WIFI_NOT_CONNECT?((associated||pending)?0:1):-1;
}
static bool radio_connect(void *unused,const pocket_wifi_profile *profile){
 (void)unused;wifi_config_t config={0};memcpy(config.sta.ssid,profile->ssid,strlen(profile->ssid));memcpy(config.sta.password,profile->password,strlen(profile->password));
 config.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
 esp_err_t error=esp_wifi_set_config(WIFI_IF_STA,&config);memset(&config,0,sizeof config);
 if(error!=ESP_OK)return false;
 /* Set before the driver call so an immediate callback cannot race admission. */
 portENTER_CRITICAL(&lock);station_request_pending=true;portEXIT_CRITICAL(&lock);
 error=esp_wifi_connect();
 if(error!=ESP_OK){portENTER_CRITICAL(&lock);station_request_pending=false;portEXIT_CRITICAL(&lock);}
 return error==ESP_OK;
}
static bool save_profiles(void *unused,const pocket_wifi_book *book){
 (void)unused;if(!window_open() || atomic_load(&cancel_requested) || atomic_load(&radio_owner)!=RADIO_SETUP)return false;
 return pocket_wifi_store_save(&profile_store,book)==ESP_OK;
}
static void publish(void){
 pocket_wifi_snapshot current=snapshot();
 portENTER_CRITICAL(&lock);public_state.count=engine.book.count;public_state.connection=engine.connection;if(!public_state.pending)public_state.join_result=engine.join_result;state.configured=engine.book.count>0;
 public_state.connection_busy=pocket_wifi_attempting(&engine)&&!engine.testing;
 memset(public_state.profiles,0,sizeof public_state.profiles);for(unsigned i=0;i<engine.book.count;i++)memcpy(public_state.profiles[i],engine.book.profiles[i].ssid,33);
 bool active=state.active;portEXIT_CRITICAL(&lock);
 static unsigned previous=~0u;
 unsigned key=(unsigned)current.connected|((unsigned)engine.connection<<1)|((unsigned)engine.join_result<<5)|((unsigned)engine.book.count<<9)|((unsigned)active<<12);
 if(key!=previous){previous=key;ESP_LOGI("pocket_wifi","connection=%u join=%u connected=%u profiles=%u setup=%u",(unsigned)engine.connection,(unsigned)engine.join_result,(unsigned)current.connected,(unsigned)engine.book.count,(unsigned)active);}
 if(active){
  const char *note=engine.testing?"Testing Wi-Fi; old networks retained":engine.join_result==POCKET_WIFI_JOIN_SAVED?"Wi-Fi saved. Check Hermes on Status":
   engine.join_result==POCKET_WIFI_JOIN_FULL?"Five saved. Forget one on your phone":engine.join_result==POCKET_WIFI_JOIN_SAVE_FAILED?"Save uncertain. Check saved networks":
   engine.join_result==POCKET_WIFI_JOIN_FAILED?"Join failed; restoring saved Wi-Fi":engine.join_result==POCKET_WIFI_JOIN_CANCELLED?"Cancelled; restoring saved Wi-Fi":"Finish setup on your phone";
  message(note);
 }
}
static void start_setup(int64_t now){
 if(atomic_load(&radio_owner)!=RADIO_SETUP)return;
 atomic_store(&setup_pending,false);
 uint8_t random[16];esp_fill_random(random,sizeof random);wifi_config_t config={0};config.ap.max_connection=1;config.ap.authmode=WIFI_AUTH_WPA2_PSK;config.ap.pmf_cfg.required=true;
 snprintf((char*)config.ap.ssid,sizeof config.ap.ssid,"Pocket-%02X%02X",random[0],random[1]);static const char alphabet[]="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
 for(unsigned i=0;i<8;i++)config.ap.password[i]=alphabet[random[i]&31];
 esp_fill_random(random,sizeof random);for(unsigned i=0;i<16;i++)snprintf(nonce+i*2,3,"%02x",random[i]);
 bool started=esp_wifi_set_mode(WIFI_MODE_APSTA)==ESP_OK && esp_wifi_set_config(WIFI_IF_AP,&config)==ESP_OK;
 if(started){
  httpd_config_t settings=HTTPD_DEFAULT_CONFIG();settings.max_open_sockets=2;settings.recv_wait_timeout=5;settings.send_wait_timeout=5;settings.stack_size=12288;settings.max_uri_handlers=9;
  started=httpd_start(&http,&settings)==ESP_OK;
  if(started){
   const httpd_uri_t routes[]={
    {.uri="/",.method=HTTP_GET,.handler=page},{.uri="/networks",.method=HTTP_GET,.handler=networks},
    {.uri="/wifi/status",.method=HTTP_GET,.handler=wifi_status},{.uri="/wifi/join",.method=HTTP_POST,.handler=wifi_join},
    {.uri="/wifi/connect",.method=HTTP_POST,.handler=wifi_control},{.uri="/wifi/forget",.method=HTTP_POST,.handler=wifi_control},
    {.uri="/wifi/cancel",.method=HTTP_POST,.handler=wifi_control},{.uri="/pairing",.method=HTTP_POST,.handler=pairing}};
   for(unsigned i=0;i<sizeof routes/sizeof routes[0];i++)if(httpd_register_uri_handler(http,&routes[i])!=ESP_OK)started=false;
  }
 }
 if(started){
  portENTER_CRITICAL(&lock);expires_us=now+300000000;state.active=true;snprintf(state.ssid,sizeof state.ssid,"%s",config.ap.ssid);snprintf(state.password,sizeof state.password,"%.24s",config.ap.password);portEXIT_CRITICAL(&lock);
  last_scan_us=0;message("Finish setup on your phone");
 }else {
  if(http){httpd_stop(http);http=NULL;}
  esp_wifi_set_mode(WIFI_MODE_STA);atomic_store(&radio_owner,RADIO_IDLE);memset(nonce,0,sizeof nonce);message("Setup unavailable. Hold OK to retry");
 }
 memset(&config,0,sizeof config);memset(random,0,sizeof random);
}
static bool discard_pending(void){
 uint32_t ticket;while(xQueueReceive(commands,&ticket,0)==pdTRUE){}
 portENTER_CRITICAL(&lock);bool join=pending_operation && pending_command.kind!=CMD_SCAN;
 memset(&pending_command,0,sizeof pending_command);pending_operation=0;public_state.pending=false;portEXIT_CRITICAL(&lock);return join;
}
static void work_step(bool notified,int64_t now){
 if(notified && !http)start_setup(now);
 if(http && now>=expires_us){
  pocket_wifi_snapshot current=snapshot();bool queued=discard_pending();pocket_wifi_cancel(&engine,now/1000,&current);
  if(queued)engine.join_result=POCKET_WIFI_JOIN_CANCELLED;
  portENTER_CRITICAL(&lock);state.active=false;public_state.pending=false;memset(state.password,0,sizeof state.password);portEXIT_CRITICAL(&lock);
  httpd_stop(http);http=NULL;esp_wifi_set_mode(WIFI_MODE_STA);memset(nonce,0,sizeof nonce);atomic_store(&radio_owner,RADIO_IDLE);message("Setup expired. Hold OK to reopen");
 }
 if(atomic_exchange(&cancel_requested,false)){
  bool queued=discard_pending();pocket_wifi_snapshot current=snapshot();pocket_wifi_cancel(&engine,now/1000,&current);
  if(queued)engine.join_result=POCKET_WIFI_JOIN_CANCELLED;
 }
 setup_command command={0};uint32_t ticket=0;bool received=false;
 if(xQueueReceive(commands,&ticket,0)==pdTRUE){
  portENTER_CRITICAL(&lock);received=pending_operation==ticket && pending_operation!=0;
  if(received){command=pending_command;memset(&pending_command,0,sizeof pending_command);pending_operation=0;}
  portEXIT_CRITICAL(&lock);
 }
 if(received){
  pocket_wifi_snapshot current=snapshot();
  if(window_open() && atomic_load(&radio_owner)==RADIO_SETUP){
   if(command.kind==CMD_JOIN){
    /* This accepted operation needs its own terminal result if cleanup rejects
       admission. The core replaces this default with TESTING or FULL. */
    engine.join_result=POCKET_WIFI_JOIN_FAILED;
    (void)pocket_wifi_join(&engine,&command.profile,now/1000,&current);
   }
   else if(command.kind==CMD_SELECT){if(!pocket_wifi_select(&engine,command.profile.ssid,now/1000,&current))engine.join_result=POCKET_WIFI_JOIN_FAILED;}
   else if(command.kind==CMD_FORGET){if(!pocket_wifi_forget(&engine,command.profile.ssid,now/1000,&current) && engine.join_result!=POCKET_WIFI_JOIN_SAVE_FAILED)engine.join_result=POCKET_WIFI_JOIN_FAILED;}
   else if(command.kind==CMD_CANCEL)pocket_wifi_cancel(&engine,now/1000,&current);
   else {
    pocket_wifi_visible records[POCKET_WIFI_SCAN_MAX]={0};size_t count=0;scan_ok=false;
    if(!engine.testing && engine.connection!=POCKET_WIFI_CONNECTING && engine.connection!=POCKET_WIFI_GETTING_IP)count=radio_scan(NULL,records,POCKET_WIFI_SCAN_MAX);
    portENTER_CRITICAL(&lock);memcpy(public_state.networks,records,sizeof records);public_state.network_count=(unsigned)count;public_state.scan_state=scan_ok?2:3;portEXIT_CRITICAL(&lock);
   }
  }
  memset(&command,0,sizeof command);portENTER_CRITICAL(&lock);public_state.pending=false;portEXIT_CRITICAL(&lock);
 }
 unsigned expected=RADIO_IDLE;(void)atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_AUTO);
 pocket_wifi_snapshot current=snapshot();unsigned owner=atomic_load(&radio_owner);pocket_wifi_tick(&engine,now/1000,&current,owner==RADIO_VOICE || owner==RADIO_POLL);
 current=snapshot();
 if(atomic_load(&radio_owner)==RADIO_AUTO && !pocket_wifi_attempting(&engine) && (!current.connected || engine.connection==POCKET_WIFI_CONNECTED)){
  if(atomic_load(&setup_pending)){atomic_store(&radio_owner,RADIO_SETUP);start_setup(esp_timer_get_time());}
  else atomic_store(&radio_owner,RADIO_IDLE);
 }
 publish();
}
static void work(void *unused){(void)unused;for(;;){bool notified=ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(250))!=0;work_step(notified,esp_timer_get_time());}}
esp_err_t pocket_setup_init(void){
 esp_err_t error=nvs_flash_init();if(error!=ESP_OK)return error;
 migrate_linux_public();
 pocket_wifi_book book;pocket_wifi_store_result loaded=pocket_wifi_store_load(&profile_store,&book);bool legacy=false;
 if(loaded==POCKET_WIFI_STORE_ABSENT){
  pocket_wifi_book_init(&book);pocket_wifi_profile original;
  if(pocket_pairing_legacy_wifi(&original))legacy=pocket_wifi_book_import(&book,&original);
  memset(&original,0,sizeof original);
 }
 pocket_wifi_ops ops={NULL,radio_disconnect,radio_connect,radio_scan,save_profiles};pocket_wifi_init(&engine,&book,&ops);memset(&book,0,sizeof book);
 state.configured=engine.book.count>0;public_state.paired=pairing_saved();publish();
 ESP_LOGI("pocket_wifi_boot","profiles=%u legacy=%u store=%u writable=%u",(unsigned)engine.book.count,(unsigned)legacy,(unsigned)loaded,(unsigned)profile_store.writable);
 ESP_ERROR_CHECK(esp_netif_init());ESP_ERROR_CHECK(esp_event_loop_create_default());esp_netif_create_default_wifi_sta();esp_netif_create_default_wifi_ap();
 wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();ESP_ERROR_CHECK(esp_wifi_init(&init));
 ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
 ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
 commands=xQueueCreate(1,sizeof(uint32_t));if(!commands)return ESP_ERR_NO_MEM;
 ESP_ERROR_CHECK(esp_wifi_start());
 if(xTaskCreate(work,"pocket_setup",6144,NULL,4,&worker)!=pdPASS){vQueueDelete(commands);commands=NULL;return ESP_ERR_NO_MEM;}
 message(state.configured?"Reconnecting to saved Wi-Fi":"Hold OK on Status to add Wi-Fi");return ESP_OK;
}
void pocket_setup_request(void){
 if(!worker)return;
 unsigned expected=RADIO_IDLE;
 if(atomic_compare_exchange_strong(&radio_owner,&expected,RADIO_SETUP))xTaskNotifyGive(worker);
 else if(expected==RADIO_AUTO || expected==RADIO_POLL){atomic_store(&setup_pending,true);message("Opening setup after current request");xTaskNotifyGive(worker);}
 else if(expected==RADIO_VOICE)message("Finish voice before Wi-Fi setup");
}
