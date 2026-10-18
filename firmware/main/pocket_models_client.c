/* MIT. Catalog metadata only: no provider configuration or submission endpoint. */
#include "pocket_models_client.h"
#include "pocket_http.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "esp_log.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static portMUX_TYPE models_lock=portMUX_INITIALIZER_UNLOCKED;
static struct {pocket_models_snapshot snapshot;uint32_t generation;bool pending;} states[2]={{.pending=true},{.pending=true}};
void pocket_models_refresh(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&models_lock);states[target].generation++;states[target].pending=true;
 states[target].snapshot=(pocket_models_snapshot){.status=POCKET_MODELS_LOADING};portEXIT_CRITICAL(&models_lock);
}
void pocket_models_read(unsigned target,pocket_models_snapshot *out){
 if(!out)return;
 memset(out,0,sizeof *out);if(target>1)return;
 portENTER_CRITICAL(&models_lock);*out=states[target].snapshot;portEXIT_CRITICAL(&models_lock);
}
void pocket_models_disconnected(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&models_lock);states[target].generation++;states[target].pending=true;
 states[target].snapshot=(pocket_models_snapshot){.status=POCKET_MODELS_OFFLINE};portEXIT_CRITICAL(&models_lock);
}
static bool bounded(const char *text,size_t length){
 if(!length || length>=1024 || memchr(text,0,length))return false;
 unsigned depth=0,items=0;bool quoted=false,escape=false,complete=false;
 for(size_t i=0;i<length;i++){
  unsigned char c=(unsigned char)text[i];
  if(complete){if(c!=' '&&c!='\t'&&c!='\n'&&c!='\r')return false;continue;}
  if(quoted){
   if(escape){if(c=='u'&&i+4<length&&!memcmp(text+i+1,"0000",4))return false;escape=false;}
   else if(c=='\\')escape=true;else if(c=='"')quoted=false;
  }else if(c=='"')quoted=true;
  else if(c=='{'||c=='['){if(++depth>4 || ++items>64)return false;}
  else if(c=='}'||c==']'){if(!depth)return false;if(!--depth)complete=true;}
  else if(c==':'||c==','){if(++items>64)return false;}
 }
 return complete&&!quoted&&!depth;
}
static bool unique(const cJSON *object){
 if(!cJSON_IsObject(object))return false;
 for(const cJSON *a=object->child;a;a=a->next){
  if(!a->string)return false;
  for(const cJSON *b=a->next;b;b=b->next)if(!b->string||!strcmp(a->string,b->string))return false;
 }
 return true;
}
static bool decode(const char *text,size_t length,pocket_models_snapshot *out){
 if(!bounded(text,length))return false;
 cJSON *root=cJSON_ParseWithLengthOpts(text,length+1,NULL,1);bool valid=false;
 if(!unique(root))goto done;
 const cJSON *version=cJSON_GetObjectItemCaseSensitive(root,"version"),*default_id=cJSON_GetObjectItemCaseSensitive(root,"default_id"),*models=cJSON_GetObjectItemCaseSensitive(root,"models");
 if(!cJSON_IsNumber(version)||version->valuedouble!=2 || !cJSON_IsString(default_id)||strcmp(default_id->valuestring,"default")||
    !cJSON_IsArray(models)||cJSON_GetArraySize(models)<1||cJSON_GetArraySize(models)>3)goto done;
 for(const cJSON *item=models->child;item;item=item->next){
  if(!unique(item))goto done;
  const cJSON *id=cJSON_GetObjectItemCaseSensitive(item,"id"),*name=cJSON_GetObjectItemCaseSensitive(item,"name"),*verified=cJSON_GetObjectItemCaseSensitive(item,"verified");
  if(!cJSON_IsString(id)||!cJSON_IsString(name)||!name->valuestring[0]||strlen(name->valuestring)>48||!cJSON_IsBool(verified))goto done;
  unsigned choice=0;for(;choice<POCKET_MODEL_CHOICE_COUNT;choice++)if(!strcmp(id->valuestring,pocket_model_choice_id(choice)))break;
  if(choice==POCKET_MODEL_CHOICE_COUNT || (out->available_mask&(1u<<choice)))goto done;
  out->available_mask|=1u<<choice;if(cJSON_IsTrue(verified))out->verified_mask|=1u<<choice;
 }
 valid=(out->available_mask&1u)!=0;
done:
 cJSON_Delete(root);return valid;
}
bool pocket_models_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1||!link||!response||capacity<2)return false;
 portENTER_CRITICAL(&models_lock);bool pending=states[target].pending;uint32_t generation=states[target].generation;
 if(pending){states[target].pending=false;states[target].snapshot=(pocket_models_snapshot){.status=POCKET_MODELS_LOADING};}
 portEXIT_CRITICAL(&models_lock);if(!pending)return false;
 char path[32];snprintf(path,sizeof path,"/targets/%s/models",target?"linux":"windows");
 pocket_http_result result={0};esp_err_t error=pocket_http_json(link->origin,path,link->token,link->ca,NULL,response,capacity<1024?capacity:1024,&result);
 pocket_models_snapshot observed={.status=POCKET_MODELS_FAILED};
 if(error!=ESP_OK)observed.status=error==ESP_ERR_INVALID_SIZE?POCKET_MODELS_FAILED:POCKET_MODELS_OFFLINE;
 else if(result.status==404||result.status==405)observed.status=POCKET_MODELS_UNSUPPORTED;
 else if(result.status==401||result.status==403)observed.status=POCKET_MODELS_AUTH_REQUIRED;
 else if(result.status==200&&decode(response,result.length,&observed))observed.status=POCKET_MODELS_READY;
 if(observed.status!=POCKET_MODELS_READY){observed.available_mask=0;observed.verified_mask=0;}
 portENTER_CRITICAL(&models_lock);bool current=generation==states[target].generation;if(current)states[target].snapshot=observed;portEXIT_CRITICAL(&models_lock);
 if(current)ESP_LOGI("pocket_models","target=%u http=%d state=%u available=%u verified=%u",target,result.status,(unsigned)observed.status,observed.available_mask,observed.verified_mask);
 return true;
}
bool pocket_models_voice_body(unsigned target,unsigned choice,char *out,size_t capacity){
 if(!out||!capacity)return false;
 out[0]=0;if(target>1||choice>=POCKET_MODEL_CHOICE_COUNT)return false;
 pocket_models_snapshot current;pocket_models_read(target,&current);
 if(choice!=POCKET_MODEL_DEFAULT&&(current.status!=POCKET_MODELS_READY||!(current.available_mask&(1u<<choice))))return false;
 int length=current.status==POCKET_MODELS_READY?
  snprintf(out,capacity,"{\"version\":2,\"chat_id\":\"pocket\",\"model_choice\":\"%s\"}",pocket_model_choice_id(choice)):
  snprintf(out,capacity,"{\"chat_id\":\"pocket\"}");
 if(length<0||(size_t)length>=capacity){out[0]=0;return false;}return true;
}
