/* Hermes Pocket MIT. No allocation, IO, callbacks, scheduler or account access. */
#include "pocket_reminders.h"
#include <math.h>
#include <string.h>

enum { OCCUPIED=1,THRESHOLD_SENT=2,RESET_SENT=4,RESET_ARMED=8,HAS_RESET=16,USD=32,PURCHASED=64 };
enum { PERCENT_ENABLED=1,USD_ENABLED=2 };
static bool timestamp(int64_t value){return value>=1704067200 && value<=253402300799LL;}
void pocket_reminders_init(pocket_reminders_state *state){if(state)memset(state,0,sizeof *state);}
void pocket_reminders_clear_target(pocket_reminders_state *state,unsigned target){
 if(state && target<POCKET_REMINDER_TARGET_COUNT){memset(state->slots[target],0,sizeof state->slots[target]);state->enabled[target]=0;}
}
static void disarm(pocket_reminders_state *state,unsigned target,unsigned domains){
 for(unsigned p=0;p<POCKET_USAGE_PROVIDER_COUNT;p++)for(unsigned i=0;i<POCKET_USAGE_METRIC_LIMIT;i++){
  pocket_reminder_slot *slot=&state->slots[target][p][i];
  if(domains&(slot->flags&USD?USD_ENABLED:PERCENT_ENABLED))slot->flags&=(unsigned char)~RESET_ARMED;
 }
}
static bool name_length(const pocket_usage_metric *m,size_t *length){
 *length=0;while(*length<sizeof m->window && m->window[*length])(*length)++;
 return *length<sizeof m->window && (*length || m->window_minutes);
}
static unsigned identity(const pocket_usage_metric *m){
 return m->unit==POCKET_USAGE_USD_CREDIT?USD|(m->category==POCKET_USAGE_PURCHASED_CREDIT?PURCHASED:0):0;
}
static bool same_window(const char *a,unsigned minutes_a,const char *b,unsigned minutes_b){
 return a[0]&&b[0]?strcmp(a,b)==0:!a[0]&&!b[0]&&minutes_a==minutes_b;
}
static bool fresh(const pocket_usage_metric *m,int64_t now){
 size_t length;
 if(m->availability!=POCKET_USAGE_AVAILABLE || !m->has_value || !isfinite(m->value) || m->value<0 ||
    !m->has_fetched_at || !timestamp(m->fetched_at) || m->fetched_at>now ||
    !m->has_expires_at || !timestamp(m->expires_at) || m->expires_at<=now || m->expires_at<=m->fetched_at ||
    (m->has_resets_at && !timestamp(m->resets_at)) || !name_length(m,&length))return false;
 if(m->unit==POCKET_USAGE_PERCENT_USED || m->unit==POCKET_USAGE_PERCENT_REMAINING)
  return m->category==POCKET_USAGE_SUBSCRIPTION && m->value<=100;
 return m->unit==POCKET_USAGE_USD_CREDIT && (m->category==POCKET_USAGE_INCLUDED_CREDIT || m->category==POCKET_USAGE_PURCHASED_CREDIT);
}
static bool conflict(const pocket_usage_provider *p,unsigned index,int64_t now){
 const pocket_usage_metric *m=&p->metrics[index];
 for(unsigned i=0;i<p->metric_count;i++){
  if(i==index)continue;
  const pocket_usage_metric *other=&p->metrics[i];
  if(!fresh(other,now) || identity(m)!=identity(other) || !same_window(m->window,m->window_minutes,other->window,other->window_minutes))continue;
  if(m->has_resets_at!=other->has_resets_at || (m->has_resets_at && m->resets_at!=other->resets_at))return true;
  const double a=m->unit==POCKET_USAGE_PERCENT_REMAINING?100-m->value:m->value;
  const double b=other->unit==POCKET_USAGE_PERCENT_REMAINING?100-other->value:other->value;
  const double difference=a>b?a-b:b-a;if(difference>0.000001)return true;
 }
 return false;
}
static pocket_reminder_slot *find_slot(pocket_reminder_slot *slots,const pocket_usage_metric *m){
 pocket_reminder_slot *empty=NULL;const unsigned key=identity(m);
 for(unsigned i=0;i<POCKET_USAGE_METRIC_LIMIT;i++){
  if(!(slots[i].flags&OCCUPIED)){if(!empty)empty=&slots[i];continue;}
  if((slots[i].flags&(USD|PURCHASED))==key && same_window(slots[i].window,slots[i].window_minutes,m->window,m->window_minutes))return &slots[i];
 }
 return empty;
}
static void emit(pocket_reminder_event *event,pocket_reminder_kind kind,const pocket_usage_metric *m,
 unsigned target,unsigned provider,unsigned metric,double boundary){
 memset(event,0,sizeof *event);event->kind=kind;event->target=target;event->provider=provider;event->metric=metric;
 event->window_minutes=m->window_minutes;event->category=m->category;event->unit=m->unit;
 size_t length=0;(void)name_length(m,&length);memcpy(event->window,m->window,length+1);
 event->has_resets_at=m->has_resets_at;if(m->has_resets_at)event->resets_at=m->resets_at;
 event->has_value=kind==POCKET_REMINDER_THRESHOLD;
 if(event->has_value){event->value=m->value;event->threshold=boundary;}
}
size_t pocket_reminders_evaluate(pocket_reminders_state *state,const pocket_usage_snapshot *snapshot,
 int64_t now,unsigned target,const pocket_reminder_config *config,pocket_reminder_event *events,size_t capacity){
 if(!state || target>=POCKET_REMINDER_TARGET_COUNT)return 0;
 unsigned enabled=0;
 if(config){
  if(config->percent_used_threshold>0 && config->percent_used_threshold<=100)enabled|=PERCENT_ENABLED;
  if(config->usd_floor_enabled && isfinite(config->usd_floor) && config->usd_floor>=0)enabled|=USD_ENABLED;
 }
 if(!timestamp(now) || (state->last_now && now<state->last_now)){
  for(unsigned t=0;t<POCKET_REMINDER_TARGET_COUNT;t++)disarm(state,t,PERCENT_ENABLED|USD_ENABLED);
  state->last_now=timestamp(now)?now:0;state->enabled[target]=(unsigned char)enabled;return 0;
 }
 state->last_now=now;
 /* Disable/enable transitions never replay an old scheduled reset. Threshold
  * sent flags survive preference changes, disconnects and clock corrections. */
 disarm(state,target,(unsigned)(state->enabled[target]^enabled));state->enabled[target]=(unsigned char)enabled;
 if(!enabled || !snapshot)return 0;
 if(!events)capacity=0;
 if(capacity>POCKET_REMINDER_EVENT_LIMIT)capacity=POCKET_REMINDER_EVENT_LIMIT;
 size_t count=0;
 for(unsigned p=0;p<POCKET_USAGE_PROVIDER_COUNT;p++){
  const pocket_usage_provider *provider=&snapshot->providers[p];
  if((provider->availability!=POCKET_USAGE_AVAILABLE && provider->availability!=POCKET_USAGE_PARTIAL) || provider->metric_count>POCKET_USAGE_METRIC_LIMIT)continue;
  for(unsigned i=0;i<provider->metric_count;i++){
   const pocket_usage_metric *m=&provider->metrics[i];
   if(!fresh(m,now) || conflict(provider,i,now))continue;
   const unsigned domain=m->unit==POCKET_USAGE_USD_CREDIT?USD_ENABLED:PERCENT_ENABLED;
   if(!(enabled&domain))continue;
   pocket_reminder_slot *slot=find_slot(state->slots[target][p],m);if(!slot)continue;
   if(!(slot->flags&OCCUPIED)){
    memset(slot,0,sizeof *slot);slot->flags=(unsigned char)(OCCUPIED|identity(m));
    size_t length=0;(void)name_length(m,&length);memcpy(slot->window,m->window,length+1);slot->window_minutes=m->window_minutes;
   }
   if(m->fetched_at<slot->last_fetched_at)continue;
   if(m->has_resets_at && (slot->flags&HAS_RESET) && m->resets_at<slot->resets_at)continue;
   if(m->has_resets_at){
    if(!(slot->flags&HAS_RESET)){
     /* Learning the initial reset does not repeat a threshold already emitted
      * while its reset was unknown. A later observed cycle can rearm it. */
     slot->resets_at=m->resets_at;slot->flags|=HAS_RESET;
    }else if(m->resets_at>slot->resets_at){
     /* Production expires the old sample at its reset. Only fresh next-period
      * data observed after that known deadline can confirm the time was
      * reached. Preserve the old schedule if output is full so it can retry. */
     if((slot->flags&RESET_ARMED) && !(slot->flags&RESET_SENT) && now>=slot->resets_at && m->fetched_at>=slot->resets_at){
      if(count>=capacity)continue;
      emit(&events[count],POCKET_REMINDER_RESET_REFRESH,m,target,p,i,0);
      events[count++].resets_at=slot->resets_at;
     }
     slot->resets_at=m->resets_at;slot->flags&=(unsigned char)~(THRESHOLD_SENT|RESET_SENT|RESET_ARMED);
    }
   }else slot->flags&=(unsigned char)~RESET_ARMED;
   slot->last_fetched_at=m->fetched_at;
   if(m->has_resets_at && m->resets_at>now && !(slot->flags&RESET_SENT))slot->flags|=RESET_ARMED;
   const double boundary=m->unit==POCKET_USAGE_USD_CREDIT?config->usd_floor:
                         m->unit==POCKET_USAGE_PERCENT_REMAINING?100.0-config->percent_used_threshold:config->percent_used_threshold;
   const bool reached=m->unit==POCKET_USAGE_PERCENT_USED?m->value>=boundary:m->value<=boundary;
   const bool reset_due=m->has_resets_at && (slot->flags&RESET_ARMED) && !(slot->flags&RESET_SENT) && now>=slot->resets_at;
   if(count>=capacity)continue;
   if(reset_due){emit(&events[count++],POCKET_REMINDER_RESET_REFRESH,m,target,p,i,0);slot->flags|=RESET_SENT;}
   else if(reached && !(slot->flags&THRESHOLD_SENT)){
    emit(&events[count++],POCKET_REMINDER_THRESHOLD,m,target,p,i,boundary);slot->flags|=THRESHOLD_SENT;
   }
  }
 }
 return count;
}
const char *pocket_reminder_message(pocket_reminder_kind kind){
 return kind==POCKET_REMINDER_RESET_REFRESH?"Reset time reached; refresh needed":kind==POCKET_REMINDER_THRESHOLD?"Usage threshold reached":"";
}
