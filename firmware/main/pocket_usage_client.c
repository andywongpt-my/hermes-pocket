/* MIT. Bounded read-only reports; uses the existing serial HTTPS transport. */
#include "pocket_usage_client.h"
#include <stdatomic.h>
#include "pocket_http.h"
#include "esp_log.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include <limits.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static portMUX_TYPE usage_lock=portMUX_INITIALIZER_UNLOCKED;
static pocket_usage_snapshot snapshots[2];
static int64_t next_poll[2];
static char origins[2][193];
typedef struct { bool seen,decoded;int http;unsigned availability[3],counts[3],local[3]; } usage_observation;
static usage_observation observed[2];
static void observe(unsigned target,int http,bool decoded){
 usage_observation current={.seen=true,.decoded=decoded,.http=http};
 portENTER_CRITICAL(&usage_lock);
 for(unsigned p=0;p<3;p++){current.availability[p]=(unsigned)snapshots[target].providers[p].availability;current.counts[p]=snapshots[target].providers[p].metric_count;current.local[p]=snapshots[target].providers[p].local.present?(unsigned)snapshots[target].providers[p].local.availability:0;}
 portEXIT_CRITICAL(&usage_lock);
 bool changed=!observed[target].seen || observed[target].http!=http || observed[target].decoded!=decoded;
 for(unsigned p=0;p<3;p++)if(current.availability[p]!=observed[target].availability[p] || current.counts[p]!=observed[target].counts[p] || current.local[p]!=observed[target].local[p])changed=true;
 if(!changed)return;
 observed[target]=current;
 ESP_LOGI("pocket_usage","target=%u http=%d decoded=%u p0=%u m0=%u p1=%u m1=%u p2=%u m2=%u l0=%u l1=%u l2=%u",target,http,(unsigned)decoded,
          current.availability[0],current.counts[0],current.availability[1],current.counts[1],current.availability[2],current.counts[2],current.local[0],current.local[1],current.local[2]);
}

static bool bounded_json(const char *value,size_t length){
 if(!length || length>=8192 || memchr(value,0,length))return false;
 unsigned depth=0,items=0;bool quoted=false,escape=false,complete=false;
 for(size_t i=0;i<length;i++){
  unsigned char c=(unsigned char)value[i];
  if(complete){if(c!=' ' && c!='\t' && c!='\r' && c!='\n')return false;continue;}
  if(quoted){if(escape)escape=false;else if(c=='\\')escape=true;else if(c=='"')quoted=false;continue;}
  if(c=='"'){quoted=true;continue;}
  if(c=='{' || c=='['){if(++depth>8 || ++items>512)return false;}
  else if(c=='}' || c==']'){if(!depth)return false;depth--;if(!depth)complete=true;}
  else if(c==':' || c==','){if(++items>512)return false;}
 }
 return !quoted && !depth && complete;
}
static bool unique_object(const cJSON *value){
 if(!cJSON_IsObject(value))return false;
 for(const cJSON *a=value->child;a;a=a->next){
  if(!a->string)return false;
  for(const cJSON *b=a->next;b;b=b->next)if(!b->string || !strcmp(a->string,b->string))return false;
 }
 return true;
}
static pocket_usage_availability availability(const cJSON *value){
 static const char *names[]={"unavailable","not_configured","available","partial","stale","failed"};
 if(!cJSON_IsString(value) || !value->valuestring)return POCKET_USAGE_UNFETCHED;
 for(unsigned i=0;i<6;i++)if(!strcmp(value->valuestring,names[i]))return (pocket_usage_availability)(i+1);
 return POCKET_USAGE_UNFETCHED;
}
static bool timestamp(const cJSON *value,int64_t *out,bool *present){
 *present=false;*out=0;if(!value || cJSON_IsNull(value))return true;
 if(!cJSON_IsNumber(value) || !isfinite(value->valuedouble) || value->valuedouble<0 || value->valuedouble>9007199254740991.0)return false;
 *out=(int64_t)value->valuedouble;*present=true;return true;
}
static bool text(const cJSON *value,char *out,size_t size){
 if(!cJSON_IsString(value) || !value->valuestring)return false;
 size_t length=strlen(value->valuestring);if(!length || length>=size)return false;
 for(size_t i=0;i<length;i++)if((unsigned char)value->valuestring[i]<32 || (unsigned char)value->valuestring[i]>126)return false;
 memcpy(out,value->valuestring,length+1);return true;
}
static bool metric(const cJSON *json,pocket_usage_metric *out){
 if(!unique_object(json))return false;
 out->availability=availability(cJSON_GetObjectItemCaseSensitive(json,"availability"));
 if(out->availability!=POCKET_USAGE_AVAILABLE && out->availability!=POCKET_USAGE_UNAVAILABLE && out->availability!=POCKET_USAGE_STALE)return false;
 char name[32];if(!text(cJSON_GetObjectItemCaseSensitive(json,"unit"),name,sizeof(name)))return false;
 if(!strcmp(name,"percent_used"))out->unit=POCKET_USAGE_PERCENT_USED;
 else if(!strcmp(name,"percent_remaining"))out->unit=POCKET_USAGE_PERCENT_REMAINING;
 else if(!strcmp(name,"USD_credit"))out->unit=POCKET_USAGE_USD_CREDIT;else return false;
 if(!text(cJSON_GetObjectItemCaseSensitive(json,"category"),name,sizeof(name)))return false;
 if(!strcmp(name,"subscription"))out->category=POCKET_USAGE_SUBSCRIPTION;
 else if(!strcmp(name,"included_credit"))out->category=POCKET_USAGE_INCLUDED_CREDIT;
 else if(!strcmp(name,"purchased_credit"))out->category=POCKET_USAGE_PURCHASED_CREDIT;else return false;
 if((out->category==POCKET_USAGE_SUBSCRIPTION)==(out->unit==POCKET_USAGE_USD_CREDIT))return false;
 if(!text(cJSON_GetObjectItemCaseSensitive(json,"window"),out->window,sizeof(out->window)))return false;
 const cJSON *value=cJSON_GetObjectItemCaseSensitive(json,"value");
 if(value && !cJSON_IsNull(value)){
  if(!cJSON_IsNumber(value) || !isfinite(value->valuedouble) || value->valuedouble<0 || (out->unit!=POCKET_USAGE_USD_CREDIT && value->valuedouble>100.0))return false;
  out->value=value->valuedouble;out->has_value=true;
 }
 if(!timestamp(cJSON_GetObjectItemCaseSensitive(json,"fetched_at"),&out->fetched_at,&out->has_fetched_at) ||
  !timestamp(cJSON_GetObjectItemCaseSensitive(json,"resets_at"),&out->resets_at,&out->has_resets_at) ||
  !timestamp(cJSON_GetObjectItemCaseSensitive(json,"expires_at"),&out->expires_at,&out->has_expires_at))return false;
 value=cJSON_GetObjectItemCaseSensitive(json,"window_minutes");
 if(value && !cJSON_IsNull(value)){
  if(!cJSON_IsNumber(value) || !isfinite(value->valuedouble) || value->valuedouble<0 || value->valuedouble>9007199254740991.0/60.0)return false;
  out->window_minutes=value->valuedouble<=UINT_MAX?(unsigned)value->valuedouble:0;
 }
 if(!out->has_value)out->availability=POCKET_USAGE_UNAVAILABLE;
 else if(!out->has_fetched_at)out->availability=POCKET_USAGE_STALE;
 return true;
}
static const char *const local_counters[]={"runs","api_calls","input_tokens","output_tokens","cache_read_tokens","cache_write_tokens","reasoning_tokens"};
static const char *const local_coverage[]={"owned_runs","observed_runs","missing_runs","unattributed_runs","unmapped_observations","provider_observed_runs"};
typedef enum { WIRE_IGNORE,WIRE_REPORT,WIRE_LOCAL,WIRE_COUNTERS,WIRE_COVERAGE,WIRE_INTEGER } usage_wire_context;
typedef struct { const char *position,*end; } usage_wire_cursor;
static void wire_space(usage_wire_cursor *wire){
 while(wire->position<wire->end && (*wire->position==' ' || *wire->position=='\t' || *wire->position=='\r' || *wire->position=='\n'))wire->position++;
}
static bool wire_take(usage_wire_cursor *wire,char expected){
 wire_space(wire);
 if(wire->position==wire->end || *wire->position!=expected)return false;
 wire->position++;return true;
}
static bool wire_string(usage_wire_cursor *wire){
 if(!wire_take(wire,'"'))return false;
 while(wire->position<wire->end){
  const char value=*wire->position++;
  if(value=='"')return true;
  if(value=='\\'){
   if(wire->position==wire->end)return false;
   wire->position++;
  }
 }
 return false;
}
static bool local_name(const char *name,const char *const *names,unsigned count){
 for(unsigned i=0;i<count;i++)if(!strcmp(name,names[i]))return true;
 return false;
}
static usage_wire_context wire_child(usage_wire_context context,const char *name){
 if(!name)return WIRE_IGNORE;
 if(context==WIRE_REPORT && !strcmp(name,"local_usage"))return WIRE_LOCAL;
 if(context==WIRE_LOCAL){
  if(!strcmp(name,"main") || !strcmp(name,"auxiliary"))return WIRE_COUNTERS;
  if(!strcmp(name,"coverage"))return WIRE_COVERAGE;
  if(!strcmp(name,"fetched_at") || !strcmp(name,"last_seen"))return WIRE_INTEGER;
 }
 if((context==WIRE_COUNTERS && local_name(name,local_counters,POCKET_USAGE_COUNTER_COUNT)) ||
    (context==WIRE_COVERAGE && local_name(name,local_coverage,POCKET_USAGE_COVERAGE_COUNT)))return WIRE_INTEGER;
 return WIRE_IGNORE;
}
static bool wire_value(const cJSON *json,usage_wire_cursor *wire,unsigned depth,usage_wire_context context,bool *local_valid){
 if(depth>8)return false;
 const bool object=cJSON_IsObject(json),array=cJSON_IsArray(json);
 if(object || array){
  if(!wire_take(wire,object?'{':'['))return false;
  for(const cJSON *child=json->child;child;child=child->next){
   if(child!=json->child && !wire_take(wire,','))return false;
   if(object && (!wire_string(wire) || !wire_take(wire,':')))return false;
   if(!wire_value(child,wire,depth+1,object?wire_child(context,child->string):WIRE_IGNORE,local_valid))return false;
  }
  return wire_take(wire,object?'}':']');
 }
 wire_space(wire);
 if(wire->position==wire->end)return false;
 if(*wire->position=='"')return wire_string(wire);
 const char *start=wire->position;
 while(wire->position<wire->end && *wire->position!=',' && *wire->position!='}' && *wire->position!=']' &&
       *wire->position!=' ' && *wire->position!='\t' && *wire->position!='\r' && *wire->position!='\n')wire->position++;
 if(wire->position==start)return false;
 if(context==WIRE_INTEGER && cJSON_IsNumber(json)){
  /* cJSON has already rounded its double. Only canonical integer bytes can
   * establish an exact counter; keep quota floats and future fields separate. */
  if(*start=='0' && wire->position-start!=1)*local_valid=false;
  for(const char *p=start;p<wire->position;p++)if(*p<'0' || *p>'9')*local_valid=false;
 }
 return true;
}
static bool exact_counter(const cJSON *json,uint64_t maximum,uint64_t *out,bool *known){
 *known=false;*out=0;if(!json || cJSON_IsNull(json))return true;
 if(!cJSON_IsNumber(json) || !isfinite(json->valuedouble) || json->valuedouble<0 || json->valuedouble>(double)maximum)return false;
 const uint64_t integer=(uint64_t)json->valuedouble;
 if((double)integer!=json->valuedouble)return false;
 *out=integer;*known=true;return true;
}
static bool local_timestamp(const cJSON *json,int64_t *out,bool *known){
 uint64_t integer=0;
 if(!exact_counter(json,UINT64_C(253402300799),&integer,known))return false;
 *out=(int64_t)integer;return true;
}
static bool local_group(const cJSON *json,const char *const *names,unsigned count,uint64_t *out,uint8_t *mask){
 if(!unique_object(json))return false;
 for(unsigned i=0;i<count;i++){
  bool known=false;
  if(!exact_counter(cJSON_GetObjectItemCaseSensitive(json,names[i]),POCKET_USAGE_COUNTER_MAX,&out[i],&known))return false;
  if(known)*mask|=(uint8_t)(1u<<i);
 }
 return true;
}
static bool literal(const cJSON *json,const char *expected){return cJSON_IsString(json) && json->valuestring && !strcmp(json->valuestring,expected);}
static bool local_decode(const cJSON *json,pocket_usage_local *out){
 if(!unique_object(json) || !literal(cJSON_GetObjectItemCaseSensitive(json,"scope"),"pocket_runs") ||
    !literal(cJSON_GetObjectItemCaseSensitive(json,"period"),"all_recorded") ||
    !literal(cJSON_GetObjectItemCaseSensitive(json,"source"),"Hermes runtime"))return false;
 out->availability=availability(cJSON_GetObjectItemCaseSensitive(json,"availability"));
 if(out->availability!=POCKET_USAGE_UNAVAILABLE && out->availability!=POCKET_USAGE_AVAILABLE && out->availability!=POCKET_USAGE_PARTIAL &&
    out->availability!=POCKET_USAGE_STALE && out->availability!=POCKET_USAGE_FAILED)return false;
 if(!local_timestamp(cJSON_GetObjectItemCaseSensitive(json,"fetched_at"),&out->fetched_at,&out->has_fetched_at) ||
    !local_timestamp(cJSON_GetObjectItemCaseSensitive(json,"last_seen"),&out->last_seen,&out->has_last_seen))return false;
 return local_group(cJSON_GetObjectItemCaseSensitive(json,"main"),local_counters,POCKET_USAGE_COUNTER_COUNT,out->main,&out->main_known) &&
        local_group(cJSON_GetObjectItemCaseSensitive(json,"auxiliary"),local_counters,POCKET_USAGE_COUNTER_COUNT,out->auxiliary,&out->auxiliary_known) &&
        local_group(cJSON_GetObjectItemCaseSensitive(json,"coverage"),local_coverage,POCKET_USAGE_COVERAGE_COUNT,out->coverage,&out->coverage_known);
}
static bool decode(const char *response,size_t length,pocket_usage_snapshot *out){
 static const char *names[]={"codex","claude","ollama_cloud"};
 if(!bounded_json(response,length))return false;
 cJSON *root=cJSON_Parse(response);bool valid=cJSON_IsArray(root) && cJSON_GetArraySize(root)==3;unsigned seen=0;
 usage_wire_cursor wire={response,response+length};
 if(valid)valid=wire_take(&wire,'[');
 for(int i=0;valid && i<3;i++){
  const cJSON *report=cJSON_GetArrayItem(root,i);char name[32];unsigned provider=3;
  if(!unique_object(report) || !text(cJSON_GetObjectItemCaseSensitive(report,"provider"),name,sizeof(name))){valid=false;break;}
  for(unsigned p=0;p<3;p++)if(!strcmp(name,names[p]))provider=p;
  if(provider==3 || (seen&(1u<<provider))){valid=false;break;}seen|=1u<<provider;
  pocket_usage_provider *value=&out->providers[provider];value->availability=availability(cJSON_GetObjectItemCaseSensitive(report,"availability"));
  if(value->availability==POCKET_USAGE_UNFETCHED){valid=false;break;}
  /* Follow the parsed children in wire order, using decoded names even for
   * escaped keys. This checks local number spellings without parsing JSON anew. */
  bool local_valid=true;
  if((i && !wire_take(&wire,',')) || !wire_value(report,&wire,1,WIRE_REPORT,&local_valid)){valid=false;break;}
  const cJSON *local=cJSON_GetObjectItemCaseSensitive(report,"local_usage");
  if(local){
   value->local.present=true;
   /* An additive runtime source can fail independently of valid account quota
      and the other provider rows. Never retain partially decoded counters. */
   if(!local_valid || !local_decode(local,&value->local)){
    memset(&value->local,0,sizeof(value->local));value->local.present=true;value->local.availability=POCKET_USAGE_FAILED;
   }
  }
  const cJSON *metrics=cJSON_GetObjectItemCaseSensitive(report,"metrics");
  if(!metrics && value->availability==POCKET_USAGE_NOT_CONFIGURED)continue; /* Previous bridge's honest empty report. */
  if(!cJSON_IsArray(metrics) || cJSON_GetArraySize(metrics)>POCKET_USAGE_METRIC_LIMIT){valid=false;break;}
  value->metric_count=(unsigned)cJSON_GetArraySize(metrics);
  if(value->availability==POCKET_USAGE_NOT_CONFIGURED && value->metric_count){valid=false;break;}
  for(unsigned m=0;m<value->metric_count;m++)if(!metric(cJSON_GetArrayItem(metrics,(int)m),&value->metrics[m])){valid=false;break;}
  if(!value->metric_count && value->availability==POCKET_USAGE_AVAILABLE)value->availability=POCKET_USAGE_UNAVAILABLE;
 }
 if(valid){valid=wire_take(&wire,']');wire_space(&wire);valid=valid && wire.position==wire.end;}
 cJSON_Delete(root);return valid && seen==7;
}
static void mark(unsigned target,pocket_usage_availability state){
 portENTER_CRITICAL(&usage_lock);
 for(unsigned p=0;p<3;p++){
  snapshots[target].providers[p].availability=state;
  if(snapshots[target].providers[p].local.present)snapshots[target].providers[p].local.availability=state==POCKET_USAGE_UNAVAILABLE?POCKET_USAGE_STALE:state;
  for(unsigned m=0;m<snapshots[target].providers[p].metric_count;m++)snapshots[target].providers[p].metrics[m].availability=POCKET_USAGE_STALE;
 }
 portEXIT_CRITICAL(&usage_lock);
}
void pocket_usage_read(unsigned target,pocket_usage_snapshot *out){
 if(!out)return;
 memset(out,0,sizeof(*out));if(target>1)return;
 portENTER_CRITICAL(&usage_lock);*out=snapshots[target];portEXIT_CRITICAL(&usage_lock);
 int64_t now=(int64_t)time(NULL);
 for(unsigned p=0;p<3;p++){
  pocket_usage_provider *provider=&out->providers[p];unsigned available=0,stale=0;
  /* Runtime counters cover all recorded history. Cache TTL/last_seen age do
     not expire them; only the source state or a future read time marks stale. */
  if(provider->local.present && provider->local.has_fetched_at && now>0 && now<provider->local.fetched_at && provider->local.availability!=POCKET_USAGE_FAILED)provider->local.availability=POCKET_USAGE_STALE;
  for(unsigned m=0;m<provider->metric_count;m++){
   pocket_usage_metric *value=&provider->metrics[m];
   if((value->has_expires_at && now>=value->expires_at) || (value->has_resets_at && now>=value->resets_at) || (value->has_fetched_at && now<value->fetched_at))value->availability=POCKET_USAGE_STALE;
   if(value->has_value && value->availability==POCKET_USAGE_AVAILABLE)available++;
   if(value->has_value && value->availability==POCKET_USAGE_STALE)stale++;
  }
  if(provider->availability==POCKET_USAGE_AVAILABLE || provider->availability==POCKET_USAGE_PARTIAL || provider->availability==POCKET_USAGE_STALE){
   if(stale==provider->metric_count && stale)provider->availability=POCKET_USAGE_STALE;
   else if(available!=provider->metric_count)provider->availability=POCKET_USAGE_PARTIAL;
  }
 }
}
void pocket_usage_disconnected(unsigned target){
 if(target>1)return;
 next_poll[target]=0;mark(target,POCKET_USAGE_UNAVAILABLE);
 observe(target,0,false);
}
static atomic_uint force_targets;
void pocket_usage_force(unsigned target){if(target<2)atomic_fetch_or_explicit(&force_targets,1u<<target,memory_order_relaxed);}
void pocket_usage_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1 || !link || !response || capacity<2 || capacity>8192)return;
 int64_t now=(int64_t)time(NULL);if(now<1704067200)return;
 if(strcmp(origins[target],link->origin)){
  observed[target].seen=false;
  portENTER_CRITICAL(&usage_lock);memset(&snapshots[target],0,sizeof(snapshots[target]));portEXIT_CRITICAL(&usage_lock);
  memcpy(origins[target],link->origin,sizeof(origins[target]));origins[target][sizeof(origins[target])-1]=0;next_poll[target]=0;
 }
 const bool forced=(atomic_fetch_and_explicit(&force_targets,~(1u<<target),memory_order_relaxed)&(1u<<target))!=0;
 if(!forced && now<next_poll[target] && now+30>=next_poll[target])return;
 next_poll[target]=now+30;
 pocket_http_result result={0};
 const char *route=target?"/targets/linux/usage":"/targets/windows/usage";
 if(pocket_http_json(link->origin,route,link->token,link->ca,NULL,response,capacity,&result)!=ESP_OK || result.status!=200){mark(target,POCKET_USAGE_FAILED);observe(target,result.status,false);return;}
 if(result.length>=capacity || response[result.length]!=0){mark(target,POCKET_USAGE_FAILED);observe(target,result.status,false);return;}
 pocket_usage_snapshot *parsed=calloc(1,sizeof(*parsed));
 if(!parsed){mark(target,POCKET_USAGE_FAILED);observe(target,result.status,false);return;}
 const bool decoded=decode(response,result.length,parsed);
 if(decoded){
  portENTER_CRITICAL(&usage_lock);snapshots[target]=*parsed;portEXIT_CRITICAL(&usage_lock);
 }else mark(target,POCKET_USAGE_FAILED);
 memset(parsed,0,sizeof(*parsed));free(parsed);
 observe(target,result.status,decoded);
}
