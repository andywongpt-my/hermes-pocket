#include "pocket_live_network.h"
#include <string.h>
static bool bounded(const char *s,size_t cap,bool host) {
 if(!s||!s[0])return false;
 size_t i=0;
 for(;i<cap&&s[i];i++) {
  unsigned char c=(unsigned char)s[i];
  bool alpha=(c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9');
  if(!alpha&&c!='.'&&c!='-'&&(host||c!=':'))return false;
 }
 return i<cap;
}
void pocket_live_network_dns_init(pocket_live_network_dns *s){if(s)memset(s,0,sizeof *s);}
bool pocket_live_network_dns_begin(pocket_live_network_dns*s,const char*host,uint32_t*token){
 if(!s||!token||s->busy||s->generation==UINT32_MAX||!bounded(host,sizeof s->host,true))return false;
 uint32_t next=s->generation+1;memset(s,0,sizeof *s);s->generation=next;s->busy=true;
 memcpy(s->host,host,strlen(host)+1);*token=next;return true;
}
bool pocket_live_network_dns_start(pocket_live_network_dns*s,uint32_t token){
 if(!s||!s->busy||s->generation!=token||s->started||s->completed||s->cancelled)return false;
 s->started=true;return true;
}
void pocket_live_network_dns_cancel(pocket_live_network_dns*s,uint32_t token){
 if(!s||!s->busy||s->generation!=token)return;
 s->cancelled=true;
 if(s->completed)s->busy=false;
}
bool pocket_live_network_dns_complete(pocket_live_network_dns*s,uint32_t token,const char*ip){
 if(!s||!s->busy||s->generation!=token||s->completed)return false;
 s->completed=true;
 if(s->cancelled){s->busy=false;return true;}
 s->success=bounded(ip,sizeof s->address,false);
 if(s->success)memcpy(s->address,ip,strlen(ip)+1);
 return true;
}
bool pocket_live_network_dns_take(pocket_live_network_dns*s,uint32_t token,char*out,size_t cap){
 if(!s||!out||!s->busy||s->generation!=token||!s->completed||s->cancelled)return false;
 if(!s->success){s->busy=false;return false;}
 size_t n=strlen(s->address)+1;if(cap<n)return false;
 memcpy(out,s->address,n);s->busy=false;return true;
}
void pocket_live_network_deadline_init(pocket_live_network_deadline*d){if(d)memset(d,0,sizeof *d);}
bool pocket_live_network_deadline_begin(pocket_live_network_deadline*d,uint64_t now,uint32_t budget){
 if(!d||d->active||d->failed||!budget||budget>10000||now>UINT64_MAX-budget)return false;
 d->active=true;d->last=now;d->end=now+budget;return true;
}
uint32_t pocket_live_network_deadline_slice(pocket_live_network_deadline*d,uint64_t now,uint32_t wanted,bool stop){
 if(!d||!d->active||d->failed)return 0;
 if(stop||now<d->last||now>=d->end||!wanted){d->failed=true;return 0;}
 d->last=now;
 uint64_t left=d->end-now;uint32_t n=wanted>50?50:wanted;
 return left<n?(uint32_t)left:n;
}
bool pocket_live_network_deadline_finish(pocket_live_network_deadline*d,uint64_t now,bool stop){
 if(!pocket_live_network_deadline_slice(d,now,1,stop))return false;
 d->active=false;return true;
}
