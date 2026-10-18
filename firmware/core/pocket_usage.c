/* Hermes Pocket MIT. Pure bounded usage formatting; no network or LVGL calls. */
#include "pocket_usage.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static bool valid_state(pocket_usage_availability state) {
 return state>=POCKET_USAGE_UNFETCHED && state<=POCKET_USAGE_FAILED;
}
static void duration(uint64_t seconds,char *out,size_t size) {
 if(seconds<60)snprintf(out,size,"%llus",(unsigned long long)seconds);
 else if(seconds<3600)snprintf(out,size,"%llum",(unsigned long long)(seconds/60));
 else if(seconds<86400)snprintf(out,size,"%lluh",(unsigned long long)(seconds/3600));
 else snprintf(out,size,"%llud",(unsigned long long)(seconds/86400));
}
static void window_name(const pocket_usage_metric *metric,char *out,size_t size) {
 if(metric->window_minutes){
  const unsigned minutes=metric->window_minutes;
  if(minutes%1440==0)snprintf(out,size,"%ud",minutes/1440);
  else if(minutes%60==0)snprintf(out,size,"%uh",minutes/60);
  else snprintf(out,size,"%um",minutes);
  return;
 }
 /* A source label is optional and untrusted. Never print URLs or control text. */
 size_t length=0;
 while(length<sizeof metric->window && metric->window[length]){
  const unsigned char c=(unsigned char)metric->window[length++];
  if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c==' '||c=='_'||c=='-')){
   snprintf(out,size,"Window");return;
  }
 }
 if(!length || length==sizeof metric->window){snprintf(out,size,"Window");return;}
 snprintf(out,size,"%.*s",(int)(length>12?12:length),metric->window);
}
static bool format_value(const pocket_usage_metric *metric,char *out,size_t size) {
 if(!metric->has_value || !isfinite(metric->value) || metric->value<0)return false;
 const double value=metric->value==0?0:metric->value;
 int length;
 if(metric->unit==POCKET_USAGE_USD_CREDIT){
  if(metric->category!=POCKET_USAGE_INCLUDED_CREDIT && metric->category!=POCKET_USAGE_PURCHASED_CREDIT)return false;
  length=value>0&&value<0.01?snprintf(out,size,"<$0.01 USD"):snprintf(out,size,"$%.2f USD",value);
 }
 else if(metric->unit==POCKET_USAGE_PERCENT_USED || metric->unit==POCKET_USAGE_PERCENT_REMAINING){
  if(value>100 || metric->category!=POCKET_USAGE_SUBSCRIPTION)return false;
  const char *unit=metric->unit==POCKET_USAGE_PERCENT_USED?"used":"left";
  char number[16];snprintf(number,sizeof number,"%.2f",value);
  size_t end=strlen(number);while(end&&number[end-1]=='0')number[--end]='\0';
  if(end&&number[end-1]=='.')number[--end]='\0';
  if(value>0&&value<0.01)length=snprintf(out,size,"<0.01%% %s",unit);
  else length=snprintf(out,size,"%s%% %s",number,unit);
 }
 else return false;
 return length>=0 && (size_t)length<size;
}
static void no_sample(pocket_usage_availability state,pocket_usage_display *out) {
 out->availability=state;
 const char *value="Unknown",*detail="Partial source data",*freshness="No sample";
 switch(state){
 case POCKET_USAGE_UNFETCHED:value="Not fetched";detail="Waiting for bridge";break;
 case POCKET_USAGE_UNAVAILABLE:value="Unavailable";detail="Source unavailable";break;
 case POCKET_USAGE_NOT_CONFIGURED:value="Not set up";detail="Usage not configured";break;
 case POCKET_USAGE_FAILED:detail="Usage fetch failed";freshness="Failed / no sample";break;
 case POCKET_USAGE_STALE:detail="Cached data expired";freshness="Stale / no sample";break;
 default:break;
 }
 snprintf(out->value,sizeof out->value,"%s",value);
 snprintf(out->detail,sizeof out->detail,"%s",detail);
 snprintf(out->freshness,sizeof out->freshness,"%s",freshness);
}
void pocket_usage_format(const pocket_usage_provider *provider,unsigned window,int64_t now,pocket_usage_display *out) {
 if(!out)return;
 memset(out,0,sizeof *out);
 if(!provider){no_sample(POCKET_USAGE_UNFETCHED,out);return;}
 pocket_usage_availability state=provider->availability;
 if(!valid_state(state) || provider->metric_count>POCKET_USAGE_METRIC_LIMIT){no_sample(POCKET_USAGE_PARTIAL,out);return;}
 if(state<=POCKET_USAGE_NOT_CONFIGURED || !provider->metric_count){
  no_sample(state==POCKET_USAGE_AVAILABLE?POCKET_USAGE_PARTIAL:state,out);return;
 }
 const pocket_usage_metric *metric=&provider->metrics[window%provider->metric_count];
 if(!valid_state(metric->availability)){no_sample(POCKET_USAGE_PARTIAL,out);return;}
 if(metric->availability<=POCKET_USAGE_NOT_CONFIGURED){
  no_sample(state==POCKET_USAGE_FAILED||state==POCKET_USAGE_STALE?state:metric->availability,out);return;
 }
 if(metric->availability==POCKET_USAGE_FAILED || state==POCKET_USAGE_FAILED)state=POCKET_USAGE_FAILED;
 else if(metric->availability==POCKET_USAGE_STALE || state==POCKET_USAGE_STALE)state=POCKET_USAGE_STALE;
 else if(metric->availability==POCKET_USAGE_PARTIAL || state==POCKET_USAGE_PARTIAL)state=POCKET_USAGE_PARTIAL;
 const bool known=format_value(metric,out->value,sizeof out->value);
 if(!known){snprintf(out->value,sizeof out->value,"Unknown");if(state==POCKET_USAGE_AVAILABLE)state=POCKET_USAGE_PARTIAL;}
 const bool clock_known=now>0;
 const bool fetched_known=metric->has_fetched_at && metric->fetched_at>=0;
 const bool future=clock_known && fetched_known && metric->fetched_at>now;
 const bool expired=clock_known && metric->has_expires_at && now>=metric->expires_at;
 if((future||expired) && state!=POCKET_USAGE_FAILED)state=POCKET_USAGE_STALE;
 if((!clock_known||!fetched_known) && state==POCKET_USAGE_AVAILABLE)state=POCKET_USAGE_PARTIAL;
 out->availability=state;
 const char *category=metric->category==POCKET_USAGE_INCLUDED_CREDIT?"Included":metric->category==POCKET_USAGE_PURCHASED_CREDIT?"Purchased":"";
 char name[16],reset[32];window_name(metric,name,sizeof name);
 if(state==POCKET_USAGE_FAILED)snprintf(out->detail,sizeof out->detail,"%s%sfetch failed",category,category[0]?" / ":"");
 else {
  if(!metric->has_resets_at || metric->resets_at<0 || !clock_known)snprintf(reset,sizeof reset,"reset unknown");
  else if(metric->resets_at<=now)snprintf(reset,sizeof reset,"reset due");
  else {char remaining[24];duration((uint64_t)metric->resets_at-(uint64_t)now,remaining,sizeof remaining);snprintf(reset,sizeof reset,"reset %s",remaining);}
  if(category[0])snprintf(out->detail,sizeof out->detail,"%s / %s",category,reset);
  else snprintf(out->detail,sizeof out->detail,"%s / %s",name,reset);
 }
 if(!clock_known){
  snprintf(out->freshness,sizeof out->freshness,"%sClock unknown",state==POCKET_USAGE_FAILED?"Failed / ":state==POCKET_USAGE_STALE?"Stale / ":"");return;
 }
 if(future){snprintf(out->freshness,sizeof out->freshness,"%s / Clock mismatch",state==POCKET_USAGE_FAILED?"Failed":"Stale");return;}
 if(!fetched_known){snprintf(out->freshness,sizeof out->freshness,"%s / time unknown",state==POCKET_USAGE_FAILED?"Failed":state==POCKET_USAGE_STALE?"Stale":"Partial");return;}
 char age[24];duration((uint64_t)now-(uint64_t)metric->fetched_at,age,sizeof age);
 if(state==POCKET_USAGE_FAILED)snprintf(out->freshness,sizeof out->freshness,known?"Failed / cached %s":"Failed / %s ago",age);
 else snprintf(out->freshness,sizeof out->freshness,"%s %s ago",state==POCKET_USAGE_STALE?"Stale /":state==POCKET_USAGE_PARTIAL?"Partial /":"Fetched",age);
}

const char *pocket_usage_local_title(unsigned page) {
 static const char *titles[POCKET_USAGE_LOCAL_PAGES]={
  "CALLS / MAIN + AUX","INPUT / MAIN + AUX","OUTPUT / MAIN + AUX","CACHE READ / M + A","CACHE WRITE / M + A","REASONING / M + A","RUNS / MAIN + AUX",
  "COVERAGE / OWNED","COVERAGE / OBSERVED","COVERAGE / MISSING","COVERAGE / UNATTR.","COVERAGE / UNMAPPED","COVERAGE / PROVIDER","FETCHED / UTC","LAST SEEN / UTC"};
 return page<POCKET_USAGE_LOCAL_PAGES?titles[page]:"POCKET HISTORY";
}
static bool local_time_known(bool present,int64_t seconds) {
 /* Four-digit UTC dates, including the Unix epoch, are portable even where
  * time_t is 32 bits. Reject extremes before any calendar arithmetic. */
 return present && seconds>=0 && seconds<=INT64_C(253402300799);
}
static bool local_utc(bool present,int64_t seconds,char *out,size_t size) {
 if(!local_time_known(present,seconds)){snprintf(out,size,"Time unknown");return false;}
 /* Gregorian civil date from days since 1970; no libc timezone or time_t. */
 const int64_t day=seconds/86400+719468,era=day/146097;
 const unsigned doe=(unsigned)(day-era*146097),yoe=(doe-doe/1460+doe/36524-doe/146096)/365;
 const unsigned doy=doe-(365*yoe+yoe/4-yoe/100),mp=(5*doy+2)/153;
 const unsigned date=doy-(153*mp+2)/5+1,month=mp<10?mp+3:mp-9;
 const unsigned year=(unsigned)(yoe+era*400)+(month<=2);
 const unsigned within=(unsigned)(seconds%86400);
 snprintf(out,size,"%04u-%02u-%02u %02u:%02u:%02u UTC",year,month,date,within/3600,(within/60)%60,within%60);
 return true;
}
static bool local_counter(uint64_t value,bool known,const char *prefix,char *out,size_t size) {
 if(!known || value>POCKET_USAGE_COUNTER_MAX){snprintf(out,size,"%sUnknown",prefix);return false;}
 snprintf(out,size,"%s%llu",prefix,(unsigned long long)value);return true;
}
void pocket_usage_local_format(const pocket_usage_local *local,unsigned page,int64_t now,pocket_usage_display *out) {
 if(!out)return;
 memset(out,0,sizeof *out);
 const bool present=local && local->present;
 pocket_usage_availability state=present?local->availability:POCKET_USAGE_UNFETCHED;
 if(!valid_state(state) || (present && (state==POCKET_USAGE_UNFETCHED || state==POCKET_USAGE_NOT_CONFIGURED)))state=POCKET_USAGE_PARTIAL;
 bool known=false;
 if(page<7){
  static const pocket_usage_counter order[]={POCKET_USAGE_API_CALLS,POCKET_USAGE_INPUT_TOKENS,POCKET_USAGE_OUTPUT_TOKENS,
   POCKET_USAGE_CACHE_READ_TOKENS,POCKET_USAGE_CACHE_WRITE_TOKENS,POCKET_USAGE_REASONING_TOKENS,POCKET_USAGE_RUNS};
  const unsigned counter=(unsigned)order[page];
  const bool main_reported=present && (local->main_known&(1u<<counter));
  const bool auxiliary_reported=present && (local->auxiliary_known&(1u<<counter));
  const bool main=local_counter(present?local->main[counter]:0,main_reported,"Main ",out->value,sizeof out->value);
  const bool auxiliary=local_counter(present?local->auxiliary[counter]:0,auxiliary_reported,"Aux ",out->detail,sizeof out->detail);
  /* The runtime may observe only one group. An absent group stays unknown
   * without downgrading an available report; malformed reported values do. */
  known=(main||auxiliary) && (!main_reported||main) && (!auxiliary_reported||auxiliary);
 }
 else if(page<13){
  const unsigned counter=page-7;
  known=local_counter(present?local->coverage[counter]:0,present && (local->coverage_known&(1u<<counter)),"",out->value,sizeof out->value);
  snprintf(out->detail,sizeof out->detail,"%s",counter==POCKET_USAGE_PROVIDER_OBSERVED_RUNS?"This provider":"Device/target");
 }
 else if(page<POCKET_USAGE_LOCAL_PAGES){
  const bool fetched=page==13;
  known=local_utc(present && (fetched?local->has_fetched_at:local->has_last_seen),present?(fetched?local->fetched_at:local->last_seen):0,out->value,sizeof out->value);
  snprintf(out->detail,sizeof out->detail,"Hermes runtime");
 }
 else {
  snprintf(out->value,sizeof out->value,"Unknown");snprintf(out->detail,sizeof out->detail,"Page unknown");state=POCKET_USAGE_PARTIAL;
 }
 const bool clock_known=now>0;
 const bool fetched_known=present && local_time_known(local->has_fetched_at,local->fetched_at);
 const bool future=clock_known && fetched_known && local->fetched_at>now;
 if((!known || !clock_known || !fetched_known) && state==POCKET_USAGE_AVAILABLE)state=POCKET_USAGE_PARTIAL;
 if(future && state!=POCKET_USAGE_FAILED)state=POCKET_USAGE_STALE;
 out->availability=state;
 /* last_seen describes historical activity. Only source state and a future
  * fetched_at can make a report stale; the bridge cache TTL is not an expiry. */
 const char *prefix=state==POCKET_USAGE_FAILED?"Failed / ":state==POCKET_USAGE_STALE?"Stale / ":state==POCKET_USAGE_PARTIAL?"Partial / ":"";
 if(!present){snprintf(out->freshness,sizeof out->freshness,"Unknown / no local sample");return;}
 if(state==POCKET_USAGE_UNAVAILABLE){snprintf(out->freshness,sizeof out->freshness,"Unavailable / no local sample");return;}
 if(!clock_known){snprintf(out->freshness,sizeof out->freshness,"%sClock unknown",prefix);return;}
 if(future){snprintf(out->freshness,sizeof out->freshness,"%sClock mismatch",prefix);return;}
 if(!fetched_known){snprintf(out->freshness,sizeof out->freshness,"%sTime unknown",prefix);return;}
 char age[24];duration((uint64_t)now-(uint64_t)local->fetched_at,age,sizeof age);
 if(state==POCKET_USAGE_FAILED || state==POCKET_USAGE_STALE)snprintf(out->freshness,sizeof out->freshness,"%scached %s ago",prefix,age);
 else snprintf(out->freshness,sizeof out->freshness,"%s%s%s ago",prefix,state==POCKET_USAGE_AVAILABLE?"Fetched ":"",age);
}
