#include "pocket_qualification_control.h"
#include <string.h>
#include <limits.h>
static void wipe(void*v,size_t n){volatile unsigned char*p=v;while(n--)*p++=0;}
static void secrets_clear(pocket_qualification_control*c){wipe(&c->tls_config,sizeof c->tls_config);wipe(c->buffer,sizeof c->buffer);c->used=0;c->tls_config_ready=false;}
static bool private_ip(const uint8_t*p,size_t n){
 unsigned octet[4]={0};size_t pos=0;
 for(unsigned i=0;i<4;i++){
  size_t first=pos;while(pos<n&&p[pos]>='0'&&p[pos]<='9'){octet[i]=octet[i]*10+(p[pos]-'0');if(octet[i]>255||pos-first>=3)return false;pos++;}
  if(pos==first||(pos-first>1&&p[first]=='0'))return false;
  if(i<3){if(pos>=n||p[pos++]!='.')return false;}else if(pos!=n)return false;
 }
 return octet[0]==10||(octet[0]==172&&octet[1]>=16&&octet[1]<=31)||(octet[0]==192&&octet[1]==168);
}
static bool url_token(const uint8_t*p,size_t n){
 for(size_t i=0;i<n;i++){uint8_t b=p[i];if(!((b>='A'&&b<='Z')||(b>='a'&&b<='z')||(b>='0'&&b<='9')||b=='-'||b=='_'))return false;}return true;
}
static bool pem_outline(const uint8_t*p,size_t n){
 static const char begin[]="-----BEGIN CERTIFICATE-----",end[]="-----END CERTIFICATE-----";
 size_t a=sizeof begin-1,b=sizeof end-1;
 if(n<=a+b+2||memcmp(p,begin,a))return false;
 if(n&&p[n-1]=='\n'){n--;if(n&&p[n-1]=='\r')n--;}
 if(n<a+b+2||memcmp(p+n-b,end,b))return false;
 size_t tail=n-b;if(p[tail-1]!='\n')return false;
 if(p[a]=='\r'){a++;if(a>=tail||p[a]!='\n')return false;}else if(p[a]!='\n')return false;
 a++;bool data=false;
 for(;a<tail;a++){uint8_t v=p[a];if((v>='A'&&v<='Z')||(v>='a'&&v<='z')||(v>='0'&&v<='9')||v=='+'||v=='/'||v=='=')data=true;else if(v!='\r'&&v!='\n')return false;}
 return data;
}
static bool snapshot_tls(pocket_qualification_control*c,const pocket_qualification_packet*p){
 if(p->bytes<36)return false;
 uint32_t sizes=pocket_qualification_get_u32(p->payload+28),ca=pocket_qualification_get_u32(p->payload+32);
 size_t hn=sizes&65535u,tn=sizes>>16;
 uint32_t port=pocket_qualification_get_u32(p->payload+20),duration=pocket_qualification_get_u32(p->payload+24);
 if(hn<7||hn>15||tn<32||tn>128||!ca||ca>PQ_TLS_CA_MAX||!port||port>65535||!duration||duration>60000||p->bytes!=36+hn+tn+ca)return false;
 const uint8_t*host=p->payload+36,*token=host+hn,*cert=token+tn;
 if(!private_ip(host,hn)||!url_token(token,tn)||!pem_outline(cert,ca))return false;
 memcpy(c->tls_config.host,host,hn);memcpy(c->tls_config.token,token,tn);memcpy(c->tls_config.ca,cert,ca);
 c->tls_config.port=(uint16_t)port;c->tls_config.duration_ms=duration;c->tls_config_ready=true;
 c->state.duration_ms=duration;return true;
}
static bool terminal(const pocket_qualification_control*c){return c->state.phase==PQC_TERMINAL||c->state.phase==PQC_QUARANTINED;}
static bool fail(pocket_qualification_control*c,pocket_qualification_control_reason reason){
 if(!c||!c->initialized)return false;
 if(!c->state.stop)c->state.reason=reason;
 c->state.stop=true;secrets_clear(c);
 if(!c->state.owned)c->state.phase=PQC_TERMINAL;
 return false;
}
bool pocket_qualification_control_init(pocket_qualification_control*c){
 if(!c||c->initialized)return false;
 /* Caller provides zero storage; initialization cannot reset a used object. */
 memset(c,0,sizeof *c);c->initialized=true;return true;
}
bool pocket_qualification_control_check(pocket_qualification_control*c,uint64_t now){
 if(!c||!c->initialized||terminal(c)||c->state.stop)return false;
 if(c->clock_seen&&now<c->last_ms)return fail(c,PQC_CLOCK);
 c->clock_seen=true;c->last_ms=now;
 if(c->used&&now-c->fragment_ms>=PQ_FRAGMENT_MS)return fail(c,PQC_EXPIRED);
 if(c->state.phase!=PQC_IDLE){
  if(now>=c->state.deadline_ms)return fail(c,PQC_EXPIRED);
  uint64_t last=c->heartbeat_seen?c->heartbeat_ms:c->host_ms;
  if(now-last>=PQ_HEARTBEAT_MS)return fail(c,PQC_HEARTBEAT_LOST);
 }
 return true;
}
static bool accept(pocket_qualification_control*c,const pocket_qualification_packet*p,uint64_t now){
 if(c->state.phase==PQC_IDLE){
  if(p->kind!=PQ_ARM&&p->kind!=PQ_ARM_TLS)return fail(c,PQC_WIRE);
  bool tls=p->kind==PQ_ARM_TLS;
  if(now>UINT64_MAX-(tls?PQ_TLS_OBSERVATION_MS:PQ_OVERALL_MS))return fail(c,PQC_CLOCK);
  if(tls&&!snapshot_tls(c,p))return fail(c,PQC_WIRE);
  c->state.tls=tls;
  c->state.phase=PQC_HOST_ARMED;c->state.profile=p->profile;c->state.nonce=p->nonce;
  c->state.deadline_ms=now+PQ_ARM_MS;c->state.dispatch_deadline_ms=now+PQ_ARM_MS;c->host_ms=now;c->host_generation=c->state.generation;
  return true;
 }
 if(p->nonce!=c->state.nonce||p->profile!=c->state.profile)return fail(c,PQC_NONCE);
 if(p->kind!=PQ_KEEPALIVE&&p->kind!=PQ_STOP)return fail(c,PQC_WIRE);
 if(c->state.host_sequence>=UINT32_MAX-1||p->sequence!=c->state.host_sequence+1)return fail(c,PQC_SEQUENCE);
 c->state.host_sequence=p->sequence;
 if(p->kind==PQ_STOP)return fail(c,PQC_STOPPED);
 c->heartbeat_seen=true;c->heartbeat_ms=now;return true;
}
bool pocket_qualification_control_feed(pocket_qualification_control*c,const uint8_t*data,size_t bytes,uint64_t now){
 if(!pocket_qualification_control_check(c,now))return false;
 if((bytes&&!data)||bytes>PQ_HOST_CHUNK_MAX||bytes>PQ_HOST_WIRE_MAX-c->state.wire_bytes)return fail(c,PQC_BUDGET);
 c->state.wire_bytes+=(uint32_t)bytes;
 for(size_t i=0;i<bytes;i++){
  if(c->used>=sizeof c->buffer)return fail(c,PQC_BUDGET);
  if(!c->used)c->fragment_ms=now;
  c->buffer[c->used++]=data[i];
  pocket_qualification_packet packet;
  pocket_qualification_wire_result result=pocket_qualification_decode(c->buffer,c->used,&packet);
  if(result==PQ_WIRE_INCOMPLETE)continue;
  if(result!=PQ_WIRE_OK)return fail(c,PQC_WIRE);
  if(!accept(c,&packet,now))return false;
  wipe(c->buffer,sizeof c->buffer);c->used=0;
 }
 return true;
}
uint32_t pocket_qualification_control_button(pocket_qualification_control*c,bool ok,bool press,bool release,uint64_t now){
 if(!c||!c->initialized)return 0;
 bool healthy=pocket_qualification_control_check(c,now);
 if(press){
  if(c->state.generation<UINT32_MAX)c->state.generation++;
  if(c->state.generation==UINT32_MAX)fail(c,PQC_GENERATION);
 }
 uint32_t generation=c->state.generation;
 if(!healthy||terminal(c)||c->state.stop)return generation;
 if(press&&release){fail(c,PQC_CONFIRMATION);return generation;}
 if(c->state.owned){if(press)fail(c,PQC_STOPPED);return generation;}
 if(press){
  if(c->state.phase!=PQC_IDLE&&!ok){fail(c,PQC_STOPPED);return generation;}
  if(ok){
   if(c->ok_held&&c->state.phase!=PQC_IDLE){fail(c,PQC_CONFIRMATION);return generation;}
   c->ok_held=true;c->pressed_ms=now;c->pressed_generation=generation;
   if(c->state.phase==PQC_PHYSICAL_ARMED){
    if(!c->released){fail(c,PQC_CONFIRMATION);return generation;}
    c->confirm_generation=generation;
   }
  }
 }
 if(release&&ok){
  if(c->ok_held&&c->state.phase==PQC_PHYSICAL_ARMED)c->released=true;
  c->ok_held=false;
 }
 return generation;
}
bool pocket_qualification_control_arm(pocket_qualification_control*c,uint64_t event,uint32_t generation,uint64_t now){
 if(!pocket_qualification_control_check(c,now))return false;
 if(c->state.phase!=PQC_HOST_ARMED||!c->heartbeat_seen||!c->ok_held||!generation||generation==UINT32_MAX||generation!=c->state.generation||generation!=c->pressed_generation||generation<=c->host_generation||event<c->pressed_ms||event>now||now-event>1000)return fail(c,PQC_CONFIRMATION);
 c->state.phase=PQC_PHYSICAL_ARMED;c->released=false;c->confirm_generation=0;return true;
}
bool pocket_qualification_control_admit(pocket_qualification_control*c,uint64_t event,uint32_t generation,uint64_t now){
 if(!pocket_qualification_control_check(c,now))return false;
 if(c->state.phase!=PQC_PHYSICAL_ARMED||!c->heartbeat_seen||!c->released||!generation||generation==UINT32_MAX||generation!=c->state.generation||generation!=c->confirm_generation||event!=c->pressed_ms||event>now||now-event>1000)return fail(c,PQC_CONFIRMATION);
 if(c->state.tls&&(event>UINT64_MAX-c->state.duration_ms||now>=event+c->state.duration_ms))return fail(c,PQC_EXPIRED);
 c->state.request_ms=event;c->state.phase=PQC_ADMITTED;c->state.owned=true;return true;
}
bool pocket_qualification_control_dispatch(pocket_qualification_control*c,uint64_t now){
 if(!pocket_qualification_control_check(c,now)||c->state.phase!=PQC_ADMITTED)return false;
 uint64_t end=c->state.tls?c->state.request_ms+c->state.duration_ms:c->host_ms+PQ_OVERALL_MS;
 if(now>=end)return fail(c,PQC_EXPIRED);
 c->state.phase=PQC_RUNNING;c->state.deadline_ms=end;return true;
}
void pocket_qualification_control_input_lost(pocket_qualification_control*c){
 if(!c||!c->initialized)return;
 if(c->state.generation<UINT32_MAX)c->state.generation++;
 (void)fail(c,PQC_INPUT_LOST);
}
bool pocket_qualification_control_finish(pocket_qualification_control*c,bool safe,bool cleanup,uint32_t code){
 if(!c||!c->initialized||!c->state.owned||terminal(c))return false;
 bool valid=!cleanup||safe;secrets_clear(c);
 c->state.code=code;c->state.safe_release=safe;c->state.cleanup_verified=cleanup&&safe;
 c->state.phase=safe?PQC_TERMINAL:PQC_QUARANTINED;
 if(safe){c->state.owned=false;if(!c->state.stop)c->state.reason=PQC_COMPLETE;}
 else {c->state.stop=true;c->state.reason=PQC_CLEANUP;}
 return valid;
}

void pocket_qualification_control_read(const pocket_qualification_control*c,pocket_qualification_control_snapshot*out){
 if(c&&out)*out=c->state;
}

bool pocket_qualification_control_take_tls_config(pocket_qualification_control*c,pocket_qualification_tls_config*out){
 if(!out)return false;
 if(c){uintptr_t a=(uintptr_t)c,b=(uintptr_t)out;if(b-a<sizeof *c||a-b<sizeof *out)return false;}
 wipe(out,sizeof *out);
 if(!c||!c->initialized||!c->state.tls||!c->state.owned||c->state.stop||c->used||!c->tls_config_ready||c->tls_config_taken||(c->state.phase!=PQC_ADMITTED&&c->state.phase!=PQC_RUNNING))return false;
 *out=c->tls_config;c->tls_config_taken=true;secrets_clear(c);return true;
}
