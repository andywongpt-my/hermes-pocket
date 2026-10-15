/* MIT. Bounded task snapshots; no POST, worker, HTTP client or response buffer. */
#include "pocket_tasks_client.h"
#include "pocket_http.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

typedef struct {
 pocket_task_entry entries[POCKET_TASKS_LIMIT];
 unsigned count,selected;
 uint32_t generation;
 pocket_tasks_status list_status,view_status;
 bool pending_list,pending_view;
} task_list;
static portMUX_TYPE tasks_lock=portMUX_INITIALIZER_UNLOCKED;
static task_list lists[2];
/* One result across both targets. Other target metadata stays cached. */
static struct {
 uint64_t seq;
 unsigned target;
 bool valid,has_result,truncated;
 char request_id[65],result[POCKET_TASKS_RESULT_BYTES+1];
} detail;
static char origins[2][193];

static void reset_locked(unsigned target){
 uint32_t generation=lists[target].generation+1;
 memset(&lists[target],0,sizeof lists[target]);lists[target].generation=generation;
 if(detail.valid && detail.target==target)memset(&detail,0,sizeof detail);
}
void pocket_tasks_reset(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);reset_locked(target);origins[target][0]=0;portEXIT_CRITICAL(&tasks_lock);
}
void pocket_tasks_refresh(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);
 task_list *list=&lists[target];list->generation++;list->pending_list=true;list->pending_view=false;list->list_status=POCKET_TASKS_LOADING;
 portEXIT_CRITICAL(&tasks_lock);
}
static void open_locked(task_list *list){
 if(!list->count)return;
 /* A selection invalidates an in-flight response, so preserve its list work
    for the next idle poll before fetching the newly selected view. */
 if(list->list_status==POCKET_TASKS_LOADING)list->pending_list=true;
 list->generation++;list->pending_view=true;list->view_status=POCKET_TASKS_LOADING;
}
void pocket_tasks_open(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);open_locked(&lists[target]);portEXIT_CRITICAL(&tasks_lock);
}
void pocket_tasks_next(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);
 task_list *list=&lists[target];if(list->count){list->selected=(list->selected+1)%list->count;open_locked(list);}
 portEXIT_CRITICAL(&tasks_lock);
}
void pocket_tasks_disconnected(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);
 task_list *list=&lists[target];list->generation++;list->pending_list=false;list->pending_view=false;
 list->list_status=POCKET_TASKS_OFFLINE;list->view_status=POCKET_TASKS_OFFLINE;
 portEXIT_CRITICAL(&tasks_lock);
}
void pocket_tasks_read(unsigned target,pocket_tasks_snapshot *out){
 if(!out)return;
 memset(out,0,sizeof *out);if(target>1)return;out->target=target;
 portENTER_CRITICAL(&tasks_lock);
 const task_list *list=&lists[target];out->count=list->count;out->selected=list->selected;
 out->list_status=list->list_status;out->view_status=list->view_status;
 memcpy(out->entries,list->entries,sizeof out->entries);
 if(list->count && detail.valid && detail.target==target && detail.seq==list->entries[list->selected].seq &&
    !strcmp(detail.request_id,list->entries[list->selected].request_id)){
  out->has_result=detail.has_result;out->truncated=detail.truncated;memcpy(out->result,detail.result,sizeof out->result);
 }else if(out->view_status==POCKET_TASKS_READY)out->view_status=POCKET_TASKS_UNFETCHED;
 portEXIT_CRITICAL(&tasks_lock);
}

static bool bounded_json(const char *text,size_t length){
 if(!length || length>=8192 || memchr(text,0,length))return false;
 unsigned depth=0,items=0;bool quoted=false,escape=false,complete=false;
 for(size_t i=0;i<length;i++){
  unsigned char c=(unsigned char)text[i];
  if(complete){if(c!=' ' && c!='\t' && c!='\r' && c!='\n')return false;continue;}
  if(quoted){
   if(escape){if(c=='u' && i+4<length && !memcmp(text+i+1,"0000",4))return false;escape=false;}
   else if(c=='\\')escape=true;else if(c=='"')quoted=false;
   continue;
  }
  if(c=='"'){quoted=true;continue;}
  if(c=='{' || c=='['){if(++depth>6 || ++items>256)return false;}
  else if(c=='}' || c==']'){if(!depth)return false;if(!--depth)complete=true;}
  else if(c==':' || c==','){if(++items>256)return false;}
 }
 return !quoted && !depth && complete;
}
static bool unique_object(const cJSON *object){
 if(!cJSON_IsObject(object))return false;
 for(const cJSON *a=object->child;a;a=a->next){
  if(!a->string)return false;
  for(const cJSON *b=a->next;b;b=b->next)if(!b->string || !strcmp(a->string,b->string))return false;
 }
 return true;
}
static bool identifier(const cJSON *value,char out[65]){
 if(!cJSON_IsString(value) || !value->valuestring)return false;
 size_t length=strlen(value->valuestring);if(!length || length>64)return false;
 for(size_t i=0;i<length;i++){
  unsigned char c=(unsigned char)value->valuestring[i];
  if(!((c>='a'&&c<='z') || (c>='A'&&c<='Z') || (c>='0'&&c<='9') || c=='_' || c=='-'))return false;
 }
 memcpy(out,value->valuestring,length+1);return true;
}
static bool entry(const cJSON *object,pocket_task_entry *out){
 static const char *const states[]={"unknown","queued","dispatching","queued_upstream","running","awaiting_approval","stopping","completed","failed","cancelled"};
 if(!unique_object(object) || !identifier(cJSON_GetObjectItemCaseSensitive(object,"request_id"),out->request_id))return false;
 const cJSON *seq=cJSON_GetObjectItemCaseSensitive(object,"seq"),*state=cJSON_GetObjectItemCaseSensitive(object,"state");
 if(!cJSON_IsNumber(seq) || !isfinite(seq->valuedouble) || seq->valuedouble<1 || seq->valuedouble>9007199254740991.0 || floor(seq->valuedouble)!=seq->valuedouble || !cJSON_IsString(state) || !state->valuestring)return false;
 out->seq=(uint64_t)seq->valuedouble;
 for(unsigned i=0;i<sizeof states/sizeof states[0];i++)if(!strcmp(states[i],state->valuestring)){out->state=(pocket_task_state)i;return true;}
 return false;
}
static bool consistent(const pocket_task_entry *old,const pocket_task_entry *fresh){
 if(fresh->seq<old->seq || (fresh->seq==old->seq && fresh->state!=old->state))return false;
 return old->state<POCKET_TASK_STATE_COMPLETED || old->state==fresh->state;
}
static bool result_prefix(const char *text,size_t *prefix){
 const unsigned char *bytes=(const unsigned char *)text;size_t length=strlen(text);*prefix=0;
 for(size_t i=0;i<length;){
  unsigned char c=bytes[i];size_t width;
  if(c<0x80){if(c<32 && c!='\n' && c!='\r' && c!='\t')return false;width=1;}
  else if(c>=0xc2 && c<=0xdf)width=2;
  else if(c>=0xe0 && c<=0xef)width=3;
  else if(c>=0xf0 && c<=0xf4)width=4;
  else return false;
  if(width>length-i)return false;
  for(size_t j=1;j<width;j++)if((bytes[i+j]&0xc0)!=0x80)return false;
  if((c==0xe0 && bytes[i+1]<0xa0) || (c==0xed && bytes[i+1]>=0xa0) ||
     (c==0xf0 && bytes[i+1]<0x90) || (c==0xf4 && bytes[i+1]>=0x90))return false;
  i+=width;if(i<=POCKET_TASKS_RESULT_BYTES)*prefix=i;
 }
 return true;
}
static void failed(unsigned target,uint32_t generation,bool listing,pocket_tasks_status status){
 portENTER_CRITICAL(&tasks_lock);
 if(lists[target].generation==generation){if(listing)lists[target].list_status=status;else lists[target].view_status=status;}
 portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_tasks_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1 || !link || !response || capacity<2 || capacity>8192 || !memchr(link->origin,0,sizeof link->origin) || !link->origin[0])return false;
 char id[65]={0};uint32_t generation;bool listing;
 portENTER_CRITICAL(&tasks_lock);
 task_list *list=&lists[target];
 if(origins[target][0] && strcmp(origins[target],link->origin)){
  bool pending=list->pending_list || list->pending_view;reset_locked(target);
  list->pending_list=pending;if(pending)list->list_status=POCKET_TASKS_LOADING;
 }
 memcpy(origins[target],link->origin,strlen(link->origin)+1);
 listing=list->pending_list;
 if(!listing && (!list->pending_view || !list->count)){portEXIT_CRITICAL(&tasks_lock);return false;}
 generation=list->generation;
 if(listing)list->pending_list=false;
 else {list->pending_view=false;memcpy(id,list->entries[list->selected].request_id,sizeof id);}
 portEXIT_CRITICAL(&tasks_lock);
 char path[128];const char *name=target?"linux":"windows";
 if(listing)snprintf(path,sizeof path,"/targets/%s/tasks",name);
 else snprintf(path,sizeof path,"/targets/%s/tasks/%s/view",name,id);
 pocket_http_result http={0};esp_err_t error=pocket_http_json(link->origin,path,link->token,link->ca,NULL,response,capacity,&http);
 if(error!=ESP_OK || http.status!=200 || http.length>=capacity || response[http.length]!=0 || !bounded_json(response,http.length)){
  failed(target,generation,listing,http.status==401 || http.status==403?POCKET_TASKS_AUTH_REQUIRED:POCKET_TASKS_FAILED);return true;
 }
 cJSON *root=cJSON_Parse(response);
 if(listing){
  pocket_task_entry rows[POCKET_TASKS_LIMIT];memset(rows,0,sizeof rows);
  int count=cJSON_IsArray(root)?cJSON_GetArraySize(root):-1;bool valid=count>=0 && count<=POCKET_TASKS_LIMIT;
  for(int i=0;valid && i<count;i++){
   valid=entry(cJSON_GetArrayItem(root,i),&rows[i]);
   for(int j=0;valid && j<i;j++)if(!strcmp(rows[j].request_id,rows[i].request_id))valid=false;
  }
  portENTER_CRITICAL(&tasks_lock);
  if(list->generation==generation){
   unsigned selected=0;
   for(int i=0;valid && i<count;i++)for(unsigned j=0;j<list->count;j++)if(!strcmp(rows[i].request_id,list->entries[j].request_id)){
    if(!consistent(&list->entries[j],&rows[i]))valid=false;
    if(j==list->selected)selected=(unsigned)i;
   }
   if(valid){memcpy(list->entries,rows,sizeof rows);list->count=(unsigned)count;list->selected=selected;list->list_status=POCKET_TASKS_READY;
    list->pending_view=count>0;list->view_status=count>0?POCKET_TASKS_LOADING:POCKET_TASKS_UNFETCHED;
    if(!count && detail.valid && detail.target==target)memset(&detail,0,sizeof detail);
   }else list->list_status=POCKET_TASKS_FAILED;
  }
  portEXIT_CRITICAL(&tasks_lock);
 }else{
  pocket_task_entry fresh={0};const cJSON *result=cJSON_GetObjectItemCaseSensitive(root,"result"),*truncated=cJSON_GetObjectItemCaseSensitive(root,"truncated");
  size_t prefix=0;bool has_result=cJSON_IsString(result) && result->valuestring;
  bool valid=entry(root,&fresh) && !strcmp(fresh.request_id,id) && (has_result || cJSON_IsNull(result)) && cJSON_IsBool(truncated);
  if(valid && has_result)valid=result_prefix(result->valuestring,&prefix);
  bool was_truncated=cJSON_IsTrue(truncated) || (valid && has_result && strlen(result->valuestring)>prefix);
  portENTER_CRITICAL(&tasks_lock);
  if(list->generation==generation && list->count && !strcmp(list->entries[list->selected].request_id,id)){
   if(valid && consistent(&list->entries[list->selected],&fresh)){
    list->entries[list->selected]=fresh;list->view_status=POCKET_TASKS_READY;
    memset(&detail,0,sizeof detail);detail.valid=true;detail.target=target;detail.seq=fresh.seq;detail.has_result=has_result;
    memcpy(detail.request_id,id,sizeof id);detail.truncated=was_truncated;
    if(has_result){memcpy(detail.result,result->valuestring,prefix);detail.result[prefix]=0;}
   }else list->view_status=POCKET_TASKS_FAILED;
  }
  portEXIT_CRITICAL(&tasks_lock);
 }
 cJSON_Delete(root);return true;
}
