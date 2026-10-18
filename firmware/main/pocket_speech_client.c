/* MIT. One bounded V2 job on the existing serial network worker; no audio allocation. */
#include "pocket_speech_client.h"
#include "pocket_http.h"
#include "cJSON.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { RESPONSE_BYTES=8192,JSON_BYTES=8000,PCM_BYTES=4096,SEGMENT_BYTES=640000,
 MAX_SEGMENTS=12,MAX_PCM_BYTES=7680000,MAX_CHUNKS=1884,JSON_DEPTH=8,JSON_ITEMS=256 };
typedef struct {unsigned bytes,chunks;} speech_segment;
typedef struct {speech_segment segments[MAX_SEGMENTS];unsigned count,bytes,chunks;} speech_manifest;
typedef struct {
 const pocket_link_config *link;const pocket_speech_io *io;char *response;
 char task[65],request[33],generation[33],revision[65];unsigned target;
 int64_t began;bool cancelled;
} speech_session;

static bool unique_object(const cJSON *value){
 if(!cJSON_IsObject(value))return false;
 for(const cJSON *a=value->child;a;a=a->next){
  if(!a->string)return false;
  for(const cJSON *b=a->next;b;b=b->next)if(!b->string || !strcmp(a->string,b->string))return false;
 }
 return true;
}
static bool bounded_tree(const cJSON *value,unsigned depth,unsigned *items){
 if(!value || depth>JSON_DEPTH || ++*items>JSON_ITEMS)return false;
 if(cJSON_IsObject(value) || cJSON_IsArray(value)){
  if(cJSON_IsObject(value) && !unique_object(value))return false;
  for(const cJSON *child=value->child;child;child=child->next)if(!bounded_tree(child,depth+1,items))return false;
  return true;
 }
 if(cJSON_IsString(value))return value->valuestring!=NULL;
 if(cJSON_IsNumber(value))return isfinite(value->valuedouble);
 return cJSON_IsBool(value) || cJSON_IsNull(value);
}
static bool valid_tree(const cJSON *value){unsigned items=0;return cJSON_IsObject(value) && bounded_tree(value,0,&items);}
static const cJSON *field(const cJSON *object,const char *key){return cJSON_GetObjectItemCaseSensitive(object,key);}
static const char *string(const cJSON *value){return cJSON_IsString(value) && value->valuestring?value->valuestring:"";}
static bool literal(const cJSON *object,const char *key,const char *expected){return !strcmp(string(field(object,key)),expected);}
static bool integer(const cJSON *object,const char *key,unsigned minimum,unsigned maximum,unsigned *out){
 const cJSON *value=field(object,key);
 if(!cJSON_IsNumber(value) || !isfinite(value->valuedouble) || value->valuedouble<minimum || value->valuedouble>maximum)return false;
 unsigned number=(unsigned)value->valuedouble;if((double)number!=value->valuedouble)return false;
 if(out)*out=number;
 return true;
}
static bool exact(const cJSON *object,const char *key,unsigned expected){return integer(object,key,expected,expected,NULL);}
static const cJSON *delivery(const cJSON *status){
 if(!valid_tree(status))return NULL;
 const cJSON *value=field(status,"reply_delivery");return cJSON_IsObject(value) && exact(value,"version",1)?value:NULL;
}
bool pocket_delivery_text_pages(const cJSON *status){
 const cJSON *pages=field(delivery(status),"text_pages");
 return cJSON_IsObject(pages) && exact(pages,"version",1) && exact(pages,"page_bytes",768) && exact(pages,"max_result_utf8_bytes",65536);
}
bool pocket_delivery_speech_segments(const cJSON *status){
 const cJSON *speech=field(delivery(status),"speech_segments");
 return cJSON_IsObject(speech) && exact(speech,"version",2) && cJSON_IsTrue(field(speech,"available"))
  && exact(speech,"segment_seconds",20) && exact(speech,"max_reply_seconds",240) && exact(speech,"max_segments",12)
  && exact(speech,"chunk_bytes",4096) && exact(speech,"sample_rate",16000) && exact(speech,"channels",1) && exact(speech,"sample_bits",16)
  && exact(speech,"max_tts_utf8_bytes",4096) && exact(speech,"max_encoded_bytes",3145728)
  && exact(speech,"generation_timeout_seconds",120) && exact(speech,"decode_timeout_seconds",20)
  && exact(speech,"idle_ttl_seconds",180) && exact(speech,"absolute_ttl_seconds",900);
}
static bool hex(const char *value,size_t length){
 if(strlen(value)!=length)return false;
 for(size_t i=0;i<length;i++)if(!((value[i]>='0' && value[i]<='9') || (value[i]>='a' && value[i]<='f')))return false;
 return true;
}
static bool safe_task(const char *value){
 if(!value)return false;
 size_t length=strlen(value);if(!length || length>64)return false;
 for(size_t i=0;i<length;i++)if(!((value[i]>='a' && value[i]<='z') || (value[i]>='A' && value[i]<='Z') ||
  (value[i]>='0' && value[i]<='9') || value[i]=='_' || value[i]=='-'))return false;
 return true;
}
static bool bounded_json(const char *value,size_t length){
 if(!length || length>JSON_BYTES || length>=RESPONSE_BYTES || value[length] || memchr(value,0,length))return false;
 unsigned depth=0,items=0;bool quoted=false,escape=false,complete=false;
 for(size_t i=0;i<length;i++){
  unsigned char c=(unsigned char)value[i];
  if(complete){if(c!=' ' && c!='\t' && c!='\r' && c!='\n')return false;continue;}
  if(quoted){
   if(c<32)return false;
   if(escape){if(c=='u' && i+4<length && !memcmp(value+i+1,"0000",4))return false;escape=false;}
   else if(c=='\\')escape=true;else if(c=='"')quoted=false;
  }else if(c=='"')quoted=true;
  else if(c=='{' || c=='['){if(++depth>JSON_DEPTH || ++items>JSON_ITEMS)return false;}
  else if(c=='}' || c==']'){if(!depth)return false;if(!--depth)complete=true;}
  else if(c==':' || c==','){if(++items>JSON_ITEMS)return false;}
 }
 return complete && !quoted && !depth;
}
static bool active(speech_session *session,bool generating){
 if(session->cancelled || session->io->cancelled(session->io->context)){session->cancelled=true;return false;}
 int64_t elapsed=esp_timer_get_time()-session->began;
 return elapsed>=0 && elapsed<INT64_C(900000000) && (!generating || elapsed<INT64_C(120000000));
}
static cJSON *request(speech_session *session,const char *path,const char *body,int status,bool generating){
 if(!active(session,generating))return NULL;
 pocket_http_result result={0};
 esp_err_t error=pocket_http_json(session->link->origin,path,session->link->token,session->link->ca,body,session->response,RESPONSE_BYTES,&result);
 /* Late GETs are ignored. A successful create may expose only its identity
    for retirement; the caller checks active again immediately after pinning. */
 bool current=active(session,generating),creating=body && status==202;
 if((!current && !creating) || error!=ESP_OK || result.status!=status || !bounded_json(session->response,result.length))return NULL;
 cJSON *json=cJSON_ParseWithLengthOpts(session->response,result.length+1,NULL,1);
 if(!valid_tree(json)){cJSON_Delete(json);return NULL;}
 return json;
}
static bool pin_identity(speech_session *session,const cJSON *json){
 const char *generation=string(field(json,"generation")),*revision=string(field(json,"result_revision"));
 if(!exact(json,"version",2) || !literal(json,"task_id",session->task) || !literal(json,"request_id",session->request)
  || !hex(generation,32) || !hex(revision,64))return false;
 memcpy(session->generation,generation,33);memcpy(session->revision,revision,65);return true;
}
/* Returns 1 for processing, 2 for a complete manifest, 0 for terminal/invalid. */
static unsigned metadata(const speech_session *session,const cJSON *json,speech_manifest *manifest){
 if(!exact(json,"version",2) || !literal(json,"task_id",session->task) || !literal(json,"request_id",session->request)
  || !literal(json,"generation",session->generation) || !literal(json,"result_revision",session->revision)
  || !literal(json,"format","pcm_s16le") || !exact(json,"sample_rate",16000) || !exact(json,"channels",1)
  || !exact(json,"sample_bits",16) || !exact(json,"chunk_bytes",PCM_BYTES))return 0;
 bool ready=literal(json,"state","ready");
 if(!ready && !literal(json,"state","processing"))return 0;
 unsigned bytes=0,chunks=0,count=0;const cJSON *segments=field(json,"segments");
 if(!integer(json,"bytes",0,MAX_PCM_BYTES,&bytes) || !integer(json,"chunks",0,MAX_CHUNKS,&chunks)
  || !integer(json,"segment_count",0,MAX_SEGMENTS,&count) || !cJSON_IsArray(segments) || cJSON_GetArraySize(segments)!=(int)count)return 0;
 if(!ready)return !bytes && !chunks && !count?1u:0u;
 if(!bytes || bytes%2 || !count)return 0;
 unsigned total=0,total_chunks=0,index=0;
 for(const cJSON *segment=segments->child;segment;segment=segment->next,index++){
  unsigned size=0,local_chunks=0;
  if(!cJSON_IsObject(segment) || !exact(segment,"index",index) || !integer(segment,"bytes",2,SEGMENT_BYTES,&size) || size%2
   || (index+1<count && size!=SEGMENT_BYTES) || !integer(segment,"chunks",1,157,&local_chunks)
   || local_chunks!=(size+PCM_BYTES-1)/PCM_BYTES || !exact(segment,"first_frame",total/2) || !exact(segment,"frames",size/2)
   || size>MAX_PCM_BYTES-total)return 0;
  manifest->segments[index]=(speech_segment){.bytes=size,.chunks=local_chunks};total+=size;total_chunks+=local_chunks;
 }
 if(total!=bytes || total_chunks!=chunks)return 0;
 manifest->count=count;manifest->bytes=bytes;manifest->chunks=chunks;return 2;
}
static bool chunk(const speech_session *session,const cJSON *json,unsigned segment,unsigned sequence,
 const speech_manifest *manifest,unsigned char *pcm,size_t *length){
 const speech_segment *part=&manifest->segments[segment];unsigned bytes=0;
 unsigned remaining=part->bytes-sequence*PCM_BYTES,expected=remaining>PCM_BYTES?PCM_BYTES:remaining;
 bool segment_eof=sequence+1==part->chunks,reply_eof=segment_eof && segment+1==manifest->count;
 const cJSON *seof=field(json,"segment_eof"),*reof=field(json,"reply_eof");
 if(!exact(json,"version",2) || !literal(json,"generation",session->generation) || !exact(json,"segment",segment)
  || !exact(json,"sequence",sequence) || !integer(json,"bytes",2,PCM_BYTES,&bytes) || bytes%2 || bytes!=expected
  || !cJSON_IsBool(seof) || !cJSON_IsBool(reof) || (cJSON_IsTrue(seof)!=0)!=segment_eof || (cJSON_IsTrue(reof)!=0)!=reply_eof)return false;
 const char *data=string(field(json,"data"));size_t size=strlen(data),encoded=((size_t)bytes+2)/3*4;
 if(size!=encoded || !size)return false;
 size_t padding=bytes%3?(3-bytes%3):0;
 for(size_t i=0;i<size;i++){
  char c=data[i];bool digit=(c>='A' && c<='Z') || (c>='a' && c<='z') || (c>='0' && c<='9') || c=='+' || c=='/';
  if(i<size-padding?!digit:c!='=')return false;
 }
 size_t decoded=0;
 if(mbedtls_base64_decode(pcm,PCM_BYTES,&decoded,(const unsigned char*)data,size) || decoded!=bytes)return false;
 *length=decoded;return true;
}
static void cancel_generation(speech_session *session){
 if(!session->generation[0])return;
 char path[193],body[64];pocket_http_result ignored={0};
 snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech/v2/cancel",session->target?"linux":"windows",session->task);
 snprintf(body,sizeof(body),"{\"generation\":\"%s\"}",session->generation);
 /* Cleanup is one best-effort POST after local stop; its response cannot restore a job. */
 pocket_http_json(session->link->origin,path,session->link->token,session->link->ca,body,session->response,RESPONSE_BYTES,&ignored);
}
bool pocket_speech_v2_play(const pocket_link_config *link,unsigned target,
 const char *task_id,const char *request_id,char *response,size_t capacity,
 unsigned char *pcm,size_t pcm_capacity,const pocket_speech_io *io){
 if(!link || target>1 || !safe_task(task_id) || !request_id || !hex(request_id,32) || !response || capacity<RESPONSE_BYTES
  || !pcm || pcm_capacity<PCM_BYTES || !io || !io->cancelled || !io->start || !io->write || !io->drain || !io->stop)return false;
 speech_session session={.link=link,.io=io,.response=response,.target=target,.began=esp_timer_get_time()};
 memcpy(session.task,task_id,strlen(task_id)+1);memcpy(session.request,request_id,33);
 speech_manifest manifest={0};char path[193],body[64];cJSON *json=NULL;bool started=false;
 if(!active(&session,true))goto failed;
 if(io->message)io->message(io->context,"Preparing speech...");
 snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech/v2",target?"linux":"windows",session.task);
 snprintf(body,sizeof(body),"{\"request_id\":\"%s\"}",session.request);
 json=request(&session,path,body,202,true);
 if(!json || !pin_identity(&session,json))goto failed;
 if(!active(&session,true) || !metadata(&session,json,&manifest))goto failed;
 cJSON_Delete(json);json=NULL;
 snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech/v2?generation=%s",target?"linux":"windows",session.task,session.generation);
 for(;;){
  json=request(&session,path,NULL,200,true);if(!json)goto failed;
  unsigned state=metadata(&session,json,&manifest);cJSON_Delete(json);json=NULL;
  if(state==2)break;
  if(state!=1 || !active(&session,true))goto failed;
  vTaskDelay(pdMS_TO_TICKS(600));if(!active(&session,true))goto failed;
 }
 if(!active(&session,false))goto failed;
 started=true;bool started_ok=io->start(io->context),still_active=active(&session,false);
 if(!started_ok || !still_active)goto failed;
 if(io->message)io->message(io->context,"Speaking (OK to stop)");
 for(unsigned segment=0;segment<manifest.count;segment++)for(unsigned sequence=0;sequence<manifest.segments[segment].chunks;sequence++){
  snprintf(path,sizeof(path),"/targets/%s/tasks/%s/speech/v2/segments/%u/chunks/%u?generation=%s",target?"linux":"windows",session.task,segment,sequence,session.generation);
  json=request(&session,path,NULL,200,false);if(!json)goto failed;size_t length=0;
  bool valid=chunk(&session,json,segment,sequence,&manifest,pcm,&length);cJSON_Delete(json);json=NULL;
  if(!valid || !active(&session,false))goto failed;
  bool wrote=io->write(io->context,pcm,length);still_active=active(&session,false);
  if(!wrote || !still_active)goto failed;
 }
 if(!active(&session,false))goto failed;
 bool drained=io->drain(io->context);still_active=active(&session,false);
 if(!drained || !still_active)goto failed;
 io->stop(io->context);started=false;if(!active(&session,false))goto failed;
 if(io->message)io->message(io->context,"Reply ready");
 return true;
failed:
 /* Stop/mute before JSON cleanup and the potentially blocking cancellation request. */
 if(started)io->stop(io->context);
 cJSON_Delete(json);
 if(io->message)io->message(io->context,session.cancelled?"Playback stopped":"Text ready; speech unavailable");
 cancel_generation(&session);return false;
}
