/* MIT. Explicit one-time Linux public-TLS link; no credential or Wi-Fi storage. */
#include "pocket_linux_link.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "nvs.h"

#define LINK_VERSION 0x504c0001u
typedef struct { uint32_t version;char previous[193],origin[193]; } public_link;
_Static_assert(sizeof(public_link)==392,"Linux link record changed");

static bool alnum_ascii(char c){return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9');}
static bool public_https(const char *url){
 if(!url || strlen(url)>192 || strncmp(url,"https://",8))return false;
 const char *p=url+8,*label=p;bool dotted=false;
 for(;*p&&*p!=':';p++){
  if(*p=='.'){if(p==label || !alnum_ascii(p[-1]))return false;label=p+1;dotted=true;}
  else if(!alnum_ascii(*p)&&(*p!='-'||p==label))return false;
 }
 if(!dotted || p==label || !alnum_ascii(p[-1]))return false;
 if(*p==':'){
  unsigned port=0;unsigned digits=0;
  for(p++;*p;p++){if(*p<'0'||*p>'9'||++digits>5)return false;port=port*10+(unsigned)(*p-'0');}
  if(!digits || !port || port>65535)return false;
 }
 return true;
}
static esp_err_t read_link(public_link *out){
 memset(out,0,sizeof *out);nvs_handle_t nvs;
 esp_err_t result=nvs_open("pocket_link",NVS_READONLY,&nvs);if(result!=ESP_OK)return result;
 size_t size=0;result=nvs_get_blob(nvs,"linux_public",NULL,&size);
 if(result==ESP_OK && size!=sizeof *out)result=ESP_ERR_INVALID_STATE;
 if(result==ESP_OK)result=nvs_get_blob(nvs,"linux_public",out,&size);
 nvs_close(nvs);
 if(result==ESP_OK && (size!=sizeof *out || out->version!=LINK_VERSION || out->previous[192]
    || out->origin[192] || !public_https(out->origin)))result=ESP_ERR_INVALID_STATE;
 if(result!=ESP_OK)memset(out,0,sizeof *out);
 return result;
}
esp_err_t pocket_linux_link_apply_once(const char *approved,const char *legacy){
 if(!public_https(approved) || !legacy || strlen(legacy)>192)return ESP_ERR_INVALID_ARG;
 public_link saved;esp_err_t result=read_link(&saved);
 if(result==ESP_OK){
  bool same=!strcmp(saved.origin,approved)&&!strcmp(saved.previous,legacy);
  memset(&saved,0,sizeof saved);return same?ESP_OK:ESP_ERR_INVALID_STATE;
 }
 if(result!=ESP_ERR_NVS_NOT_FOUND)return result;
 public_link next={.version=LINK_VERSION};
 snprintf(next.previous,sizeof next.previous,"%s",legacy);snprintf(next.origin,sizeof next.origin,"%s",approved);
 nvs_handle_t nvs;result=nvs_open("pocket_link",NVS_READWRITE,&nvs);
 if(result==ESP_OK){
  result=nvs_set_blob(nvs,"linux_public",&next,sizeof next);
  if(result==ESP_OK)result=nvs_commit(nvs);
  nvs_close(nvs);
 }
 if(result==ESP_OK){
  result=read_link(&saved);
  if(result==ESP_OK && memcmp(&next,&saved,sizeof next))result=ESP_ERR_INVALID_STATE;
 }
 memset(&saved,0,sizeof saved);memset(&next,0,sizeof next);return result;
}
bool pocket_linux_link_select(const char *approved,const char *legacy,char *origin,size_t capacity){
 if(!public_https(approved)||!legacy||!origin)return false;
 public_link saved;esp_err_t result=read_link(&saved);
 bool active=result==ESP_OK&&!strcmp(saved.origin,approved)&&!strcmp(saved.previous,legacy)
             &&strlen(saved.origin)<capacity;
 if(active)memcpy(origin,saved.origin,strlen(saved.origin)+1);
 memset(&saved,0,sizeof saved);return active;
}
