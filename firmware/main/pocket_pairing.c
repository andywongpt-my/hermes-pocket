/* MIT. Read-only legacy migration, explicit pairing updates, no Wi-Fi writes. */
#include "pocket_pairing.h"
#include "nvs.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef struct { uint32_t version;char ssid[32],pass[64],windows[193],linux_url[193]; } legacy_config;
_Static_assert(sizeof(legacy_config)==488,"legacy v1 layout changed");
_Static_assert(sizeof(pocket_pairing_config)==2664,"legacy v2 layout changed");
static bool terminated(const char *s,size_t capacity){return memchr(s,0,capacity)!=NULL;}
esp_err_t pocket_pairing_load(pocket_pairing_config *out){
 if(!out)return ESP_ERR_INVALID_ARG;
 memset(out,0,sizeof *out);nvs_handle_t handle;esp_err_t error=nvs_open("pocket",NVS_READONLY,&handle);
 if(error!=ESP_OK)return error;
 size_t size=0;error=nvs_get_blob(handle,"config",NULL,&size);
 if(error==ESP_OK && size==sizeof(legacy_config)){
  legacy_config old={0};error=nvs_get_blob(handle,"config",&old,&size);
  if(error==ESP_OK && old.version==1){
   memcpy(out->ssid,old.ssid,sizeof old.ssid);memcpy(out->pass,old.pass,sizeof old.pass);
   memcpy(out->windows,old.windows,sizeof old.windows);memcpy(out->linux_url,old.linux_url,sizeof old.linux_url);out->version=2;
  }else error=ESP_ERR_INVALID_STATE;
  memset(&old,0,sizeof old);
 }else if(error==ESP_OK && size==sizeof *out){
  error=nvs_get_blob(handle,"config",out,&size);
  if(error==ESP_OK && out->version!=2)error=ESP_ERR_INVALID_STATE;
 }else if(error==ESP_OK)error=ESP_ERR_INVALID_STATE;
 nvs_close(handle);
 if(error==ESP_OK && (!terminated(out->ssid,sizeof out->ssid) || !terminated(out->pass,sizeof out->pass)
  || !terminated(out->windows,sizeof out->windows) || !terminated(out->linux_url,sizeof out->linux_url)
  || !terminated(out->token,sizeof out->token) || !terminated(out->ca,sizeof out->ca)))error=ESP_ERR_INVALID_STATE;
 if(error!=ESP_OK)memset(out,0,sizeof *out);
 return error;
}
bool pocket_pairing_legacy_wifi(pocket_wifi_profile *out){
 if(!out)return false;
 memset(out,0,sizeof *out);pocket_pairing_config *saved=calloc(1,sizeof *saved);if(!saved)return false;
 bool valid=pocket_pairing_load(saved)==ESP_OK;
 if(valid){memcpy(out->ssid,saved->ssid,sizeof saved->ssid);memcpy(out->password,saved->pass,sizeof saved->pass);valid=pocket_wifi_profile_valid(out);}
 memset(saved,0,sizeof *saved);free(saved);if(!valid)memset(out,0,sizeof *out);return valid;
}
static bool endpoint(const char *url){
 if(!url || strlen(url)>192)return false;
 if(!*url)return true;
 if(strncmp(url,"https://",8) || strlen(url)<9)return false;
 for(;*url;url++)if((unsigned char)*url<33 || (unsigned char)*url>126 || *url=='@' || *url=='?' || *url=='#')return false;
 return true;
}
esp_err_t pocket_pairing_update(const char *win,const char *linux_url,const char *token,const char *ca){
 if(!endpoint(win) || !endpoint(linux_url) || !token || !ca || strlen(token)>128
  || (*token && strlen(token)<32) || strlen(ca)>2048
  || (*ca && (strncmp(ca,"-----BEGIN CERTIFICATE-----",27) || !strstr(ca,"-----END CERTIFICATE-----"))))return ESP_ERR_INVALID_ARG;
 for(const char *p=token;*p;p++)if((unsigned char)*p<33 || (unsigned char)*p>126)return ESP_ERR_INVALID_ARG;
 if(!*win && !*linux_url && !*token && !*ca)return ESP_OK;
 pocket_pairing_config *saved=calloc(1,sizeof *saved);if(!saved)return ESP_ERR_NO_MEM;
 esp_err_t error=pocket_pairing_load(saved);
 if(error==ESP_ERR_NVS_NOT_FOUND){memset(saved,0,sizeof *saved);saved->version=2;error=ESP_OK;}
 if(error==ESP_OK){
  if(*win)snprintf(saved->windows,sizeof saved->windows,"%s",win);
  if(*linux_url)snprintf(saved->linux_url,sizeof saved->linux_url,"%s",linux_url);
  if(*token)snprintf(saved->token,sizeof saved->token,"%s",token);
  if(*ca)snprintf(saved->ca,sizeof saved->ca,"%s",ca);
  nvs_handle_t handle;error=nvs_open("pocket",NVS_READWRITE,&handle);
  if(error==ESP_OK){error=nvs_set_blob(handle,"config",saved,sizeof *saved);if(error==ESP_OK)error=nvs_commit(handle);nvs_close(handle);}
 }
 memset(saved,0,sizeof *saved);free(saved);return error;
}
