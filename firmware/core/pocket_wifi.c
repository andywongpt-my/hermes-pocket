/* MIT. One radio owner, fresh-event admission, no healthy roaming or eviction. */
#include "pocket_wifi.h"
#include <string.h>

enum { IDLE,DISCONNECTING,CONNECTING,STABLE,STOPPING };
static size_t bounded(const char *value,size_t capacity){
 const char *end=value?memchr(value,0,capacity):NULL;
 return end?(size_t)(end-value):capacity;
}
bool pocket_wifi_profile_valid(const pocket_wifi_profile *p){
 if(!p)return false;
 size_t ssid=bounded(p->ssid,sizeof p->ssid),password=bounded(p->password,sizeof p->password);
 if(!ssid || ssid>32 || password<8 || password>63)return false;
 for(size_t i=0;i<ssid;i++)if((unsigned char)p->ssid[i]<32 || (unsigned char)p->ssid[i]==127)return false;
 return true;
}
void pocket_wifi_book_init(pocket_wifi_book *b){memset(b,0,sizeof *b);b->version=1;}
bool pocket_wifi_book_valid(const pocket_wifi_book *b){
 if(!b || b->version!=1 || b->count>POCKET_WIFI_CAPACITY || b->reserved[0] || b->reserved[1]
  || (b->count?b->last>=b->count:b->last!=0))return false;
 for(unsigned i=0;i<b->count;i++){
  if(!pocket_wifi_profile_valid(&b->profiles[i]))return false;
  for(unsigned j=0;j<i;j++)if(!strcmp(b->profiles[i].ssid,b->profiles[j].ssid))return false;
 }
 return true;
}
int pocket_wifi_book_find(const pocket_wifi_book *b,const char *ssid){
 if(!b || !ssid || bounded(ssid,33)>32)return -1;
 for(unsigned i=0;i<b->count && i<POCKET_WIFI_CAPACITY;i++)if(!strcmp(b->profiles[i].ssid,ssid))return (int)i;
 return -1;
}
int pocket_wifi_book_put(pocket_wifi_book *b,const pocket_wifi_profile *p){
 if(!pocket_wifi_book_valid(b) || !pocket_wifi_profile_valid(p))return -1;
 int index=pocket_wifi_book_find(b,p->ssid);
 if(index<0){if(b->count==POCKET_WIFI_CAPACITY)return -1;index=b->count++;}
 b->profiles[index]=*p;b->last=(uint8_t)index;return index;
}
bool pocket_wifi_book_import(pocket_wifi_book *b,const pocket_wifi_profile *p){
 if(!b || b->count || b->sequence)return false;
 return pocket_wifi_book_put(b,p)==0;
}
int pocket_wifi_choose(const pocket_wifi_book *b,const pocket_wifi_visible *seen,size_t count,unsigned tried){
 if(!pocket_wifi_book_valid(b) || !b->count || (!seen && count))return -1;
 int choice=-1,strongest=-1000;
 for(unsigned i=0;i<b->count;i++){
  if(tried&(1u<<i))continue;
  for(size_t j=0;j<count && j<POCKET_WIFI_SCAN_MAX;j++){
   if(seen[j].open || !memchr(seen[j].ssid,0,sizeof seen[j].ssid) || strcmp(b->profiles[i].ssid,seen[j].ssid))continue;
   if(i==b->last)return (int)i;
   if(seen[j].rssi>strongest){choice=(int)i;strongest=seen[j].rssi;}
  }
 }
 if(choice>=0)return choice;
 /* Hidden networks do not appear in scans: give each saved SSID one bounded try. */
 if(!(tried&(1u<<b->last)))return b->last;
 for(unsigned i=0;i<b->count;i++)if(!(tried&(1u<<i)))return (int)i;
 return -1;
}
void pocket_wifi_init(pocket_wifi_engine *e,const pocket_wifi_book *b,const pocket_wifi_ops *ops){
 memset(e,0,sizeof *e);pocket_wifi_book_init(&e->book);
 if(pocket_wifi_book_valid(b))e->book=*b;
 if(ops)e->ops=*ops;
 e->selected=-1;e->backoff_ms=10000;
}
static bool persist(pocket_wifi_engine *e,pocket_wifi_book *next){
 next->sequence=e->book.sequence+1;if(!next->sequence)next->sequence=1;
 if(!e->ops.save || !e->ops.save(e->ops.context,next))return false;
 e->book=*next;return true;
}
static void restore(pocket_wifi_engine *e,int64_t now){
 e->testing=false;memset(&e->attempt,0,sizeof e->attempt);
 e->phase=IDLE;e->selected=-1;e->tried=0;e->retry_ms=now+1000;
}
static void stop_attempt(pocket_wifi_engine *e,int64_t now,const pocket_wifi_snapshot *s){
 e->disconnect_base=s->disconnected;e->association_base=s->associated;e->ip_base=s->got_ip;
 e->phase=STOPPING;e->deadline_ms=now+3000;
 int result=e->ops.disconnect?e->ops.disconnect(e->ops.context):-1;
 if(result==1)e->phase=IDLE;
}
static void failed(pocket_wifi_engine *e,int64_t now,const pocket_wifi_snapshot *s,pocket_wifi_connection reason){
 e->connection=reason;
 if(e->testing){e->join_result=POCKET_WIFI_JOIN_FAILED;restore(e,now);}
 else {memset(&e->attempt,0,sizeof e->attempt);e->phase=IDLE;e->selected=-1;e->retry_ms=now+1000;}
 stop_attempt(e,now,s);
}
static void connect_now(pocket_wifi_engine *e,int64_t now,const pocket_wifi_snapshot *s){
 e->disconnect_base=s->disconnected;e->association_base=s->associated;e->ip_base=s->got_ip;
 e->phase=CONNECTING;e->deadline_ms=now+POCKET_WIFI_CONNECT_MS;e->connection=POCKET_WIFI_CONNECTING;
 if(!e->ops.connect || !e->ops.connect(e->ops.context,&e->attempt))failed(e,now,s,POCKET_WIFI_CONNECTION_FAILED);
}
static void start(pocket_wifi_engine *e,int64_t now,const pocket_wifi_snapshot *s){
 e->disconnect_base=s->disconnected;e->phase=DISCONNECTING;e->connection=POCKET_WIFI_CONNECTING;e->deadline_ms=now+3000;
 int result=e->ops.disconnect?e->ops.disconnect(e->ops.context):-1;
 if(result==1)connect_now(e,now,s);
 else if(result<0)failed(e,now,s,POCKET_WIFI_CONNECTION_FAILED);
}
bool pocket_wifi_join(pocket_wifi_engine *e,const pocket_wifi_profile *p,int64_t now,const pocket_wifi_snapshot *s){
 if(!e || !s || e->testing || e->phase==STOPPING || !pocket_wifi_profile_valid(p))return false;
 int index=pocket_wifi_book_find(&e->book,p->ssid);
 if(index<0 && e->book.count==POCKET_WIFI_CAPACITY){e->join_result=POCKET_WIFI_JOIN_FULL;return false;}
 e->attempt=*p;e->testing=true;e->join_result=POCKET_WIFI_JOIN_TESTING;e->selected=index;start(e,now,s);return true;
}
bool pocket_wifi_select(pocket_wifi_engine *e,const char *ssid,int64_t now,const pocket_wifi_snapshot *s){
 int index=e?pocket_wifi_book_find(&e->book,ssid):-1;
 return index>=0 && pocket_wifi_join(e,&e->book.profiles[index],now,s);
}
void pocket_wifi_cancel(pocket_wifi_engine *e,int64_t now,const pocket_wifi_snapshot *s){
 if(!e || !s || !e->testing)return;
 e->join_result=POCKET_WIFI_JOIN_CANCELLED;e->connection=POCKET_WIFI_DISCONNECTED;restore(e,now);stop_attempt(e,now,s);
}
bool pocket_wifi_attempting(const pocket_wifi_engine *e){return e && (e->phase==DISCONNECTING || e->phase==CONNECTING || e->phase==STOPPING);}
bool pocket_wifi_forget(pocket_wifi_engine *e,const char *ssid,int64_t now,const pocket_wifi_snapshot *s){
 if(!e || !s || e->testing)return false;
 int index=pocket_wifi_book_find(&e->book,ssid);if(index<0)return false;
 pocket_wifi_book next=e->book;
 for(unsigned i=(unsigned)index;i+1<next.count;i++)next.profiles[i]=next.profiles[i+1];
 memset(&next.profiles[--next.count],0,sizeof next.profiles[0]);
 if(!next.count)next.last=0;
 else if(next.last==(unsigned)index)next.last=0;
 else if(next.last>(unsigned)index)next.last--;
 bool current=s->connected && !strcmp(s->ssid,ssid);
 if(!persist(e,&next)){memset(&next,0,sizeof next);e->join_result=POCKET_WIFI_JOIN_SAVE_FAILED;return false;}
 memset(&next,0,sizeof next);e->join_result=POCKET_WIFI_JOIN_FORGOTTEN;
 if(current || e->phase!=STABLE){e->connection=POCKET_WIFI_DISCONNECTED;restore(e,now);stop_attempt(e,now,s);}
 return true;
}
void pocket_wifi_tick(pocket_wifi_engine *e,int64_t now,const pocket_wifi_snapshot *s,bool busy){
 if(!e || !s || busy)return;
 if(e->phase==STABLE){
  if(s->connected)return;
  e->connection=POCKET_WIFI_DISCONNECTED;restore(e,now);return;
 }
 if(e->phase==STOPPING){
  /* Cancellation retires the driver attempt as well as its credentials. A
     late association must be disconnected before another radio owner runs. */
  if(s->associated!=e->association_base)stop_attempt(e,now,s);
  else if(s->disconnected!=e->disconnect_base && !s->connected){
   e->phase=IDLE;
   e->association_base=s->associated;e->retry_ms=now+1000;
  }else if(now>=e->deadline_ms)stop_attempt(e,now,s);
  return;
 }
 if(e->phase==DISCONNECTING){
  if(s->disconnected!=e->disconnect_base)connect_now(e,now,s);
  else if(now>=e->deadline_ms)failed(e,now,s,POCKET_WIFI_CONNECTION_FAILED);
  return;
 }
 if(e->phase==CONNECTING){
  /* A disconnect invalidates this attempt before any later IP callback can
     admit a save, including replacement credentials for the same SSID. */
  if(s->disconnected!=e->disconnect_base){
   failed(e,now,s,s->reason==POCKET_WIFI_REASON_AUTH?POCKET_WIFI_AUTH_FAILED:
    s->reason==POCKET_WIFI_REASON_NOT_FOUND?POCKET_WIFI_NOT_FOUND:POCKET_WIFI_CONNECTION_FAILED);return;
  }
  bool fresh=s->associated!=e->association_base && !strcmp(s->ssid,e->attempt.ssid);
  if(fresh)e->connection=POCKET_WIFI_GETTING_IP;
  if(fresh && s->got_ip!=e->ip_base && s->got_ip!=s->association_ip && s->connected){
   bool same=false;int index=pocket_wifi_book_find(&e->book,e->attempt.ssid);
   if(index>=0)same=!memcmp(&e->book.profiles[index],&e->attempt,sizeof e->attempt);
   if(e->testing && (!same || e->book.last!=(unsigned)index)){
    pocket_wifi_book next=e->book;int added=pocket_wifi_book_put(&next,&e->attempt);
    bool saved=added>=0 && persist(e,&next);memset(&next,0,sizeof next);
    if(!saved){e->join_result=POCKET_WIFI_JOIN_SAVE_FAILED;restore(e,now);stop_attempt(e,now,s);return;}
   }else if(index>=0)e->book.last=(uint8_t)index;
   if(e->testing)e->join_result=POCKET_WIFI_JOIN_SAVED;
   e->testing=false;e->phase=STABLE;e->connection=POCKET_WIFI_CONNECTED;e->tried=0;e->backoff_ms=10000;
   memset(&e->attempt,0,sizeof e->attempt);return;
  }
  if(now>=e->deadline_ms)failed(e,now,s,POCKET_WIFI_NO_IP);
  return;
 }
 if(!e->book.count && (s->connected || s->associated!=e->association_base)){
  stop_attempt(e,now,s);return;
 }
 if(!e->book.count || now<e->retry_ms)return;
 unsigned all=(1u<<e->book.count)-1;
 if((e->tried&all)==all){e->tried=0;e->retry_ms=now+e->backoff_ms;e->backoff_ms=e->backoff_ms<30000?e->backoff_ms*2:60000;return;}
 pocket_wifi_visible seen[POCKET_WIFI_SCAN_MAX]={0};size_t count=e->ops.scan?e->ops.scan(e->ops.context,seen,POCKET_WIFI_SCAN_MAX):0;
 int index=pocket_wifi_choose(&e->book,seen,count,e->tried);memset(seen,0,sizeof seen);
 if(index<0){e->retry_ms=now+10000;return;}
 e->selected=index;e->tried|=1u<<(unsigned)index;e->attempt=e->book.profiles[index];start(e,now,s);
}
