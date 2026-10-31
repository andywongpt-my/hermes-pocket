#include "pocket_live_session.h"
#include <string.h>
#include <limits.h>
static bool stopped(void*v){return atomic_load_explicit(&((pocket_live_session*)v)->stop,memory_order_acquire);}
static bool release_clean(pocket_live_session*s){
 if(s->ownership_unknown){s->phase=PLS_CLEANUP_PENDING;return false;}
 if(s->adapter_prepared&&!s->adapter_clean){s->adapter_clean=s->ops.cleanup(s->ops.adapter);if(!s->adapter_clean){s->phase=PLS_CLEANUP_PENDING;return false;}}
 if(s->adapter_prepared){if(!s->ops.scrub(s->ops.adapter)){s->phase=PLS_CLEANUP_PENDING;return false;}s->adapter_prepared=false;}
 pocket_live_session_config_clear(&s->config);
 if(s->owner_held){if(!s->ops.release(s->ops.owner)){s->phase=PLS_CLEANUP_PENDING;return false;}s->owner_held=false;}
 s->phase=PLS_RELEASED;return true;
}
bool pocket_live_session_init(pocket_live_session*s,const pocket_live_session_ops*o){
 if(!s||s->phase!=PLS_UNINITIALIZED||!o||!o->reserve||!o->release||!o->prepare||!o->run||!o->cleanup||!o->scrub||!o->now_ms||!o->epochs)return false;
 memset(s,0,sizeof *s);s->ops=*o;s->phase=PLS_IDLE;atomic_init(&s->stop,false);return true;
}
void pocket_live_session_request_stop(pocket_live_session*s){if(s)atomic_store_explicit(&s->stop,true,memory_order_release);}
bool pocket_live_session_admit(pocket_live_session*s,const pocket_live_session_config*c,uint64_t deadline){
 if(!s||s->phase!=PLS_IDLE||!c)return false;
 s->config=*c;
 if(!pocket_live_session_config_valid(&s->config)){pocket_live_session_config_clear(&s->config);return false;}
 uint64_t now=s->ops.now_ms(s->ops.owner);
 if(deadline<=now||deadline-now>10000){pocket_live_session_config_clear(&s->config);return false;}
 if(stopped(s)){s->reason=PLS_STOPPED;(void)release_clean(s);return false;}
 if(!s->ops.reserve(s->ops.owner)){s->reason=PLS_RESERVE_FAILED;(void)release_clean(s);return false;}
 s->owner_held=true;
 pocket_live_session_epoch*epochs=s->ops.epochs;
 if(epochs->exhausted||s->config.epoch<=epochs->last){s->reason=PLS_EPOCH_REJECTED;(void)release_clean(s);return false;}
 epochs->last=s->config.epoch;epochs->exhausted=epochs->last==UINT32_MAX;
 s->admitted_ms=now;s->dispatch_deadline_ms=deadline;
 bool prepared=s->ops.prepare(s->ops.adapter,&s->config,stopped,s,&s->adapter_prepared);
 if(!s->adapter_prepared)s->ownership_unknown=true;
 if(!prepared||!s->adapter_prepared){
  s->reason=PLS_PREPARE_FAILED;(void)release_clean(s);return false;
 }
 s->phase=PLS_RESERVED;return true;
}
bool pocket_live_session_execute(pocket_live_session*s){
 if(!s||s->phase!=PLS_RESERVED)return false;
 uint64_t now=s->ops.now_ms(s->ops.owner);
 if(stopped(s))s->reason=PLS_STOPPED;
 else if(now<s->admitted_ms)s->reason=PLS_CLOCK_REGRESSION;
 else if(now>=s->dispatch_deadline_ms)s->reason=PLS_EXPIRED;
 else {
  s->phase=PLS_RUNNING;s->worker_ran=true;s->worker=s->ops.run(s->ops.adapter);
  s->worker.actual_playout_known=false;
  s->backend_status_known=s->worker.terminal_received;s->reason=PLS_WORKER_RETURNED;
  s->adapter_clean=s->worker.cleanup_complete;
  if(!s->adapter_clean){s->phase=PLS_CLEANUP_PENDING;return true;}
 }
 (void)release_clean(s);return true;
}
bool pocket_live_session_reap(pocket_live_session*s){
 if(!s)return false;
 if(s->phase==PLS_RELEASED)return true;
 if(s->phase!=PLS_CLEANUP_PENDING)return false;
 return release_clean(s);
}
