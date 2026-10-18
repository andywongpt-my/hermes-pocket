/* MIT. Bounded task snapshots; no POST, worker, HTTP client or response buffer. */
#include "pocket_tasks_client.h"
#include "pocket_reader.h"
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
/* Fixed metadata only. Page bytes live exclusively in detail.result. */
static struct {
 pocket_reader_snapshot snapshot;
 char prev[POCKET_READER_CURSOR_BYTES+1],next[POCKET_READER_CURSOR_BYTES+1];
 uint64_t task_seq;
 unsigned utf8_before;
 int pending_direction;
 bool pending,available[2];
} reader;

static void reader_clear_page_locked(void){
 reader.snapshot.has_page=false;reader.snapshot.has_prev=false;reader.snapshot.has_next=false;
 reader.snapshot.eof=false;reader.snapshot.utf8_bytes=0;
 reader.utf8_before=0;
 reader.prev[0]=0;reader.next[0]=0;memset(&detail,0,sizeof detail);
}
static void reader_close_locked(void){
 uint32_t generation=reader.snapshot.generation+1;
 if(reader.snapshot.active)reader_clear_page_locked();
 memset(&reader.snapshot,0,sizeof reader.snapshot);reader.snapshot.generation=generation;
 reader.prev[0]=0;reader.next[0]=0;reader.task_seq=0;reader.utf8_before=0;reader.pending=false;reader.pending_direction=0;
}
static void reader_disconnected_locked(unsigned target){
 reader.available[target]=false;
 if(reader.snapshot.active && reader.snapshot.target==target){
  reader.snapshot.generation++;reader.snapshot.status=POCKET_READER_OFFLINE;
  reader.pending=false;reader_clear_page_locked();
 }
}

static void reset_locked(unsigned target){
 reader.available[target]=false;
 if(reader.snapshot.active && reader.snapshot.target==target)reader_close_locked();
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
 portENTER_CRITICAL(&tasks_lock);
 if(reader.snapshot.active && (reader.snapshot.target!=target || !lists[target].count ||
    strcmp(reader.snapshot.task_id,lists[target].entries[lists[target].selected].request_id)))reader_close_locked();
 open_locked(&lists[target]);portEXIT_CRITICAL(&tasks_lock);
}
void pocket_tasks_next(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);
 if(reader.snapshot.active)reader_close_locked();
 task_list *list=&lists[target];if(list->count){list->selected=(list->selected+1)%list->count;open_locked(list);}
 portEXIT_CRITICAL(&tasks_lock);
}
void pocket_tasks_disconnected(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);
 reader_disconnected_locked(target);
 task_list *list=&lists[target];list->generation++;list->pending_list=false;list->pending_view=false;
 list->list_status=POCKET_TASKS_OFFLINE;list->view_status=POCKET_TASKS_OFFLINE;
 portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_tasks_active(void){
 bool active=false;portENTER_CRITICAL(&tasks_lock);
 for(unsigned t=0;t<2&&!active;t++)for(unsigned i=0;i<lists[t].count;i++)
  if(lists[t].entries[i].state<=POCKET_TASK_STATE_STOPPING){active=true;break;}
 portEXIT_CRITICAL(&tasks_lock);return active;
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
static void failed(unsigned target,uint32_t generation,uint32_t ownership,bool listing,pocket_tasks_status status){
 portENTER_CRITICAL(&tasks_lock);
 if(lists[target].generation==generation && !reader.snapshot.active && reader.snapshot.generation==ownership){if(listing)lists[target].list_status=status;else lists[target].view_status=status;}
 portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_tasks_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1 || !link || !response || capacity<2 || capacity>8192 || !memchr(link->origin,0,sizeof link->origin) || !link->origin[0])return false;
 char id[65]={0};uint32_t generation,ownership;bool listing;
 portENTER_CRITICAL(&tasks_lock);
 if(reader.snapshot.active){portEXIT_CRITICAL(&tasks_lock);return false;}
 ownership=reader.snapshot.generation;
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
  failed(target,generation,ownership,listing,http.status==401 || http.status==403?POCKET_TASKS_AUTH_REQUIRED:POCKET_TASKS_FAILED);return true;
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
  if(list->generation==generation && !reader.snapshot.active && reader.snapshot.generation==ownership){
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
  if(list->generation==generation && !reader.snapshot.active && reader.snapshot.generation==ownership && list->count && !strcmp(list->entries[list->selected].request_id,id)){
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

void pocket_reader_available(unsigned target,bool available){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);reader.available[target]=available;
 if(!available && reader.snapshot.active && reader.snapshot.target==target)reader_close_locked();
 portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_reader_open(unsigned target){
 if(target>1)return false;
 portENTER_CRITICAL(&tasks_lock);
 task_list *list=&lists[target];
 if(!reader.available[target] || !list->count || list->selected>=list->count ||
    list->entries[list->selected].state!=POCKET_TASK_STATE_COMPLETED){portEXIT_CRITICAL(&tasks_lock);return false;}
 reader_close_locked();reader.snapshot.active=true;reader.snapshot.target=target;
 reader.snapshot.status=POCKET_READER_LOADING;reader.task_seq=list->entries[list->selected].seq;
 memcpy(reader.snapshot.task_id,list->entries[list->selected].request_id,sizeof reader.snapshot.task_id);
 reader.pending=true;reader.pending_direction=0;memset(&detail,0,sizeof detail);
 /* A GET begun before open is invalidated by reader generation. Preserve its
  * consumed pending work so the caller can resume ordinary Tasks after close. */
 for(unsigned t=0;t<2;t++){
  if(lists[t].list_status==POCKET_TASKS_LOADING)lists[t].pending_list=true;
  if(lists[t].view_status==POCKET_TASKS_LOADING)lists[t].pending_view=true;
 }
 portEXIT_CRITICAL(&tasks_lock);return true;
}
void pocket_reader_close(void){
 portENTER_CRITICAL(&tasks_lock);reader_close_locked();portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_reader_active(void){
 bool active;portENTER_CRITICAL(&tasks_lock);active=reader.snapshot.active;portEXIT_CRITICAL(&tasks_lock);return active;
}
void pocket_reader_disconnected(unsigned target){
 if(target>1)return;
 portENTER_CRITICAL(&tasks_lock);reader_disconnected_locked(target);portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_reader_turn(int direction){
 if(direction!=-1 && direction!=1)return false;
 portENTER_CRITICAL(&tasks_lock);
 if(!reader.snapshot.active || reader.snapshot.status!=POCKET_READER_READY || !reader.snapshot.has_page ||
    (direction<0?!reader.snapshot.has_prev:!reader.snapshot.has_next)){portEXIT_CRITICAL(&tasks_lock);return false;}
 reader.snapshot.generation++;reader.snapshot.status=POCKET_READER_LOADING;
 reader.pending=true;reader.pending_direction=direction;portEXIT_CRITICAL(&tasks_lock);return true;
}
void pocket_reader_read(pocket_reader_snapshot *out,char *text,size_t capacity){
 if(text && capacity)text[0]=0;
 if(!out)return;
 portENTER_CRITICAL(&tasks_lock);*out=reader.snapshot;
 if(out->has_page){
  if(text && capacity>=POCKET_READER_PAGE_BYTES+1){memcpy(text,detail.result,out->utf8_bytes);text[out->utf8_bytes]=0;}
  else {out->has_page=false;out->utf8_bytes=0;}
 }
 portEXIT_CRITICAL(&tasks_lock);
}

typedef struct {const unsigned char *at,*end;} reader_json;
typedef struct {
 pocket_reader_snapshot snapshot;
 char prev[POCKET_READER_CURSOR_BYTES+1],next[POCKET_READER_CURSOR_BYTES+1];
 char *text_token;
} reader_page;

static void reader_space(reader_json *json){
 while(json->at<json->end && (*json->at==' ' || *json->at=='\t' || *json->at=='\r' || *json->at=='\n'))json->at++;
}
static bool reader_take(reader_json *json,unsigned char value){
 reader_space(json);if(json->at==json->end || *json->at!=value)return false;json->at++;return true;
}
static bool reader_literal(reader_json *json,const char *literal){
 reader_space(json);size_t length=strlen(literal);
 if((size_t)(json->end-json->at)<length || memcmp(json->at,literal,length))return false;
 json->at+=length;return true;
}
static bool reader_hex4(reader_json *json,unsigned *out){
 if(json->end-json->at<4)return false;
 unsigned value=0;
 for(unsigned i=0;i<4;i++){
  unsigned char c=*json->at++;unsigned digit;
  if(c>='0' && c<='9')digit=c-'0';
  else if(c>='a' && c<='f')digit=c-'a'+10;
  else if(c>='A' && c<='F')digit=c-'A'+10;
  else return false;
  value=(value<<4)|digit;
 }
 *out=value;return true;
}
/* Decode a JSON string by length rather than cJSON.valuestring/strlen. The
 * first pass validates without writing; a second pass compacts only the text
 * token in the existing HTTP response. No second page buffer is allocated. */
static bool reader_string(reader_json *json,char *out,size_t max_bytes,size_t *length){
 reader_space(json);if(json->at==json->end || *json->at++!='"')return false;
 size_t used=0;
 while(json->at<json->end){
  unsigned char c=*json->at++;unsigned point=c;unsigned char bytes[4];size_t width=1;
  if(c=='"'){if(out)out[used]=0;*length=used;return true;}
  if(c<32)return false;
  if(c=='\\'){
   if(json->at==json->end)return false;
   c=*json->at++;
   switch(c){
    case '"':point='"';break;case '\\':point='\\';break;case '/':point='/';break;
    case 'b':point=8;break;case 'f':point=12;break;case 'n':point=10;break;
    case 'r':point=13;break;case 't':point=9;break;
    case 'u':
     if(!reader_hex4(json,&point))return false;
     if(point>=0xd800 && point<=0xdbff){
      unsigned low;
      if(json->end-json->at<6 || json->at[0]!='\\' || json->at[1]!='u')return false;
      json->at+=2;if(!reader_hex4(json,&low) || low<0xdc00 || low>0xdfff)return false;
      point=0x10000+((point-0xd800)<<10)+(low-0xdc00);
     }else if(point>=0xdc00 && point<=0xdfff)return false;
     break;
    default:return false;
   }
  }else if(c>=0x80){
   if(c>=0xc2 && c<=0xdf){width=2;point=c&0x1f;}
   else if(c>=0xe0 && c<=0xef){width=3;point=c&0x0f;}
   else if(c>=0xf0 && c<=0xf4){width=4;point=c&7;}
   else return false;
   if((size_t)(json->end-json->at)<width-1)return false;
   for(size_t i=1;i<width;i++){
    unsigned char tail=*json->at++;if((tail&0xc0)!=0x80)return false;point=(point<<6)|(tail&0x3f);
   }
   if((width==2 && point<0x80) || (width==3 && point<0x800) || (width==4 && point<0x10000) ||
      (point>=0xd800 && point<=0xdfff) || point>0x10ffff)return false;
  }
  if(point<0x80){width=1;bytes[0]=(unsigned char)point;}
  else if(point<0x800){width=2;bytes[0]=(unsigned char)(0xc0|(point>>6));bytes[1]=(unsigned char)(0x80|(point&0x3f));}
  else if(point<0x10000){width=3;bytes[0]=(unsigned char)(0xe0|(point>>12));bytes[1]=(unsigned char)(0x80|((point>>6)&0x3f));bytes[2]=(unsigned char)(0x80|(point&0x3f));}
  else {width=4;bytes[0]=(unsigned char)(0xf0|(point>>18));bytes[1]=(unsigned char)(0x80|((point>>12)&0x3f));bytes[2]=(unsigned char)(0x80|((point>>6)&0x3f));bytes[3]=(unsigned char)(0x80|(point&0x3f));}
  if(width>max_bytes-used)return false;
  if(out)memcpy(out+used,bytes,width);
  used+=width;
 }
 return false;
}
static bool reader_number(reader_json *json,unsigned max,unsigned *out){
 reader_space(json);if(json->at==json->end || *json->at<'0' || *json->at>'9')return false;
 bool zero=*json->at=='0';unsigned number=0,digits=0;
 while(json->at<json->end && *json->at>='0' && *json->at<='9'){
  unsigned digit=*json->at++-'0';if((zero && digits) || number>max/10 || (number==max/10 && digit>max%10))return false;
  number=number*10+digit;digits++;
 }
 *out=number;return true;
}
static bool reader_ascii(const char *text,size_t length,bool revision){
 if(!length || (revision && length!=64))return false;
 for(size_t i=0;i<length;i++){
  unsigned char c=(unsigned char)text[i];
  if(revision){if(!((c>='0'&&c<='9') || (c>='a'&&c<='f')))return false;}
  else if(!((c>='a'&&c<='z') || (c>='A'&&c<='Z') || (c>='0'&&c<='9') || c=='_' || c=='-'))return false;
 }
 return true;
}
static bool reader_cursor(reader_json *json,char out[POCKET_READER_CURSOR_BYTES+1],bool *present){
 reader_space(json);
 if(json->at<json->end && *json->at=='n'){*present=false;return reader_literal(json,"null");}
 size_t length=0;
 if(!reader_string(json,out,POCKET_READER_CURSOR_BYTES,&length) || !reader_ascii(out,length,false))return false;
 *present=true;return true;
}
static bool reader_parse(char *response,size_t length,reader_page *page){
 static const char *const keys[]={"version","task_id","result_revision","page","page_count","utf8_bytes","total_utf8_bytes","text","prev","next","eof"};
 reader_json json={(const unsigned char *)response,(const unsigned char *)response+length};
 unsigned seen=0,version=0;size_t text_bytes=0;bool closed=false;
 memset(page,0,sizeof *page);if(!reader_take(&json,'{'))return false;
 for(unsigned members=0;members<11;members++){
  char key[32];size_t key_bytes=0;
  if(!reader_string(&json,key,sizeof key-1,&key_bytes) || !reader_take(&json,':'))return false;
  unsigned field=0;
  while(field<11 && (strlen(keys[field])!=key_bytes || memcmp(key,keys[field],key_bytes)))field++;
  if(field==11 || (seen&(1u<<field)))return false;
  seen|=1u<<field;size_t bytes=0;
  switch(field){
   case 0:if(!reader_number(&json,1,&version) || version!=1)return false;break;
   case 1:if(!reader_string(&json,page->snapshot.task_id,64,&bytes) || !reader_ascii(page->snapshot.task_id,bytes,false))return false;break;
   case 2:if(!reader_string(&json,page->snapshot.result_revision,64,&bytes) || !reader_ascii(page->snapshot.result_revision,bytes,true))return false;break;
   case 3:if(!reader_number(&json,65535,&page->snapshot.page))return false;break;
   case 4:if(!reader_number(&json,65536,&page->snapshot.page_count))return false;break;
   case 5:if(!reader_number(&json,POCKET_READER_PAGE_BYTES,&page->snapshot.utf8_bytes))return false;break;
   case 6:if(!reader_number(&json,POCKET_READER_MAX_RESULT_BYTES,&page->snapshot.total_utf8_bytes))return false;break;
   case 7:reader_space(&json);page->text_token=(char *)json.at;if(!reader_string(&json,NULL,POCKET_READER_PAGE_BYTES,&text_bytes))return false;break;
   case 8:if(!reader_cursor(&json,page->prev,&page->snapshot.has_prev))return false;break;
   case 9:if(!reader_cursor(&json,page->next,&page->snapshot.has_next))return false;break;
   case 10:
    reader_space(&json);
    if(json.at<json.end && *json.at=='t'){if(!reader_literal(&json,"true"))return false;page->snapshot.eof=true;}
    else if(!reader_literal(&json,"false"))return false;
    break;
  }
  reader_space(&json);
  if(json.at<json.end && *json.at=='}'){json.at++;closed=true;break;}
  if(!reader_take(&json,','))return false;
 }
 reader_space(&json);const pocket_reader_snapshot *s=&page->snapshot;
 if(!closed || seen!=((1u<<11)-1) || json.at!=json.end || !s->page_count || s->page>=s->page_count || text_bytes!=s->utf8_bytes ||
    s->has_prev!=(s->page>0) || s->has_next!=(s->page+1<s->page_count) || s->eof==s->has_next ||
    (s->has_prev && s->has_next && !strcmp(page->prev,page->next)))return false;
 if(!s->total_utf8_bytes){
  if(s->page || s->page_count!=1 || s->utf8_bytes)return false;
 }else if(!s->utf8_bytes || s->page_count>s->total_utf8_bytes ||
          s->total_utf8_bytes<s->utf8_bytes+s->page_count-1 ||
          s->total_utf8_bytes>s->utf8_bytes+(s->page_count-1)*POCKET_READER_PAGE_BYTES)return false;
 /* Parsing is complete before compaction can overwrite JSON delimiters. */
 json.at=(const unsigned char *)page->text_token;
 return reader_string(&json,page->text_token,POCKET_READER_PAGE_BYTES,&text_bytes);
}
static bool reader_owns_locked(unsigned target,uint32_t generation){
 const task_list *list=&lists[target];
 return reader.snapshot.active && reader.snapshot.target==target && reader.snapshot.generation==generation &&
  list->count && list->selected<list->count && list->entries[list->selected].state==POCKET_TASK_STATE_COMPLETED &&
  list->entries[list->selected].seq==reader.task_seq && !strcmp(list->entries[list->selected].request_id,reader.snapshot.task_id);
}
static void reader_failed(unsigned target,uint32_t generation,pocket_reader_status status){
 portENTER_CRITICAL(&tasks_lock);
 if(reader_owns_locked(target,generation)){reader_clear_page_locked();reader.snapshot.status=status;reader.pending=false;}
 portEXIT_CRITICAL(&tasks_lock);
}
bool pocket_reader_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1 || !link || !response || capacity<2 || capacity>8192 || !memchr(link->origin,0,sizeof link->origin) || !link->origin[0])return false;
 char cursor[POCKET_READER_CURSOR_BYTES+1]={0},origin[193],id[65];uint32_t generation;int direction;
 portENTER_CRITICAL(&tasks_lock);
 if(!reader.snapshot.active || reader.snapshot.target!=target){portEXIT_CRITICAL(&tasks_lock);return false;}
 if(origins[target][0] && strcmp(origins[target],link->origin)){reset_locked(target);origins[target][0]=0;portEXIT_CRITICAL(&tasks_lock);return false;}
 generation=reader.snapshot.generation;
 if(!reader.pending || !reader_owns_locked(target,generation)){portEXIT_CRITICAL(&tasks_lock);return false;}
 memcpy(origin,link->origin,strlen(link->origin)+1);memcpy(id,reader.snapshot.task_id,sizeof id);
 memcpy(origins[target],origin,strlen(origin)+1);direction=reader.pending_direction;
 if(direction)memcpy(cursor,direction<0?reader.prev:reader.next,sizeof cursor);
 reader.pending=false;portEXIT_CRITICAL(&tasks_lock);
 char path[256];
 int written=snprintf(path,sizeof path,"/targets/%s/tasks/%s/result/pages%s%s",target?"linux":"windows",id,direction?"?cursor=":"",cursor);
 if(written<0 || (size_t)written>=sizeof path){reader_failed(target,generation,POCKET_READER_FAILED);return false;}
 pocket_http_result http={0};esp_err_t error=pocket_http_json(origin,path,link->token,link->ca,NULL,response,capacity,&http);
 portENTER_CRITICAL(&tasks_lock);
 bool current=reader_owns_locked(target,generation);
 if(current && (!memchr(link->origin,0,sizeof link->origin) || strcmp(origin,link->origin) || strcmp(origin,origins[target]))){reset_locked(target);origins[target][0]=0;current=false;}
 portEXIT_CRITICAL(&tasks_lock);
 if(!current)return true;
 if(error!=ESP_OK || http.status!=200 || !http.length || http.length>8000 || http.length>=capacity || response[http.length]!=0 || memchr(response,0,http.length)){
  pocket_reader_status status=http.status==401 || http.status==403?POCKET_READER_AUTH_REQUIRED:http.status==409?POCKET_READER_REVISION_CHANGED:POCKET_READER_FAILED;
  reader_failed(target,generation,status);return true;
 }
 reader_page page;bool valid=reader_parse(response,http.length,&page);
 portENTER_CRITICAL(&tasks_lock);
 if(reader_owns_locked(target,generation)){
  const pocket_reader_snapshot *fresh=&page.snapshot;const pocket_reader_snapshot *old=&reader.snapshot;
  unsigned before=reader.utf8_before;
  bool changed=valid && old->result_revision[0] && strcmp(old->result_revision,fresh->result_revision);
  if(valid)valid=!strcmp(id,fresh->task_id) && (direction?old->has_page &&
    fresh->page==(direction<0?old->page-1:old->page+1) && fresh->page_count==old->page_count &&
    fresh->total_utf8_bytes==old->total_utf8_bytes:fresh->page==0);
  if(valid){
   if(direction>0)before+=old->utf8_bytes;
   else if(direction<0){if(before<fresh->utf8_bytes)valid=false;else before-=fresh->utf8_bytes;}
   else before=0;
   unsigned after=before+fresh->utf8_bytes,remaining=fresh->page_count-fresh->page-1;
   /* One scalar byte position verifies totals across forward/backward turns;
    * it is never used to construct a request or interpret an opaque cursor. */
   if(before<fresh->page || before>fresh->page*POCKET_READER_PAGE_BYTES || after>fresh->total_utf8_bytes ||
      fresh->total_utf8_bytes-after<remaining || fresh->total_utf8_bytes-after>remaining*POCKET_READER_PAGE_BYTES)valid=false;
  }
  if(!valid || changed){reader_clear_page_locked();reader.snapshot.status=changed?POCKET_READER_REVISION_CHANGED:POCKET_READER_FAILED;}
  else {
   pocket_reader_snapshot published=page.snapshot;published.active=true;published.has_page=true;published.target=target;
   published.generation=generation;published.status=POCKET_READER_READY;reader.snapshot=published;
   reader.utf8_before=before;
   memcpy(reader.prev,page.prev,sizeof reader.prev);memcpy(reader.next,page.next,sizeof reader.next);
   memcpy(detail.result,page.text_token,published.utf8_bytes);detail.result[published.utf8_bytes]=0;
  }
 }
 portEXIT_CRITICAL(&tasks_lock);return true;
}
