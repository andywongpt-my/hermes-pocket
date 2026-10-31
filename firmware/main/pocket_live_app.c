#include "pocket_live_app.h"
#include "pocket_live_admission.h"
#define POCKET_LIVE_V2_IDF_INTERNAL 1
#include "pocket_live_v2_idf.h"
#include <stdlib.h>
struct pocket_live_app {
 pocket_live_session session;
 pocket_live_v2_idf_context idf;
 pocket_live_v2_worker worker;
 pocket_live_app_owner owner;
 bool (*session_stop)(void*);void *session_stop_context;
};
static void wipe(void*p,size_t n){volatile unsigned char*q=p;while(n--)*q++=0;}
static bool stopped(void*v){pocket_live_app*a=v;return a->owner.stopped(a->owner.context)||(a->session_stop&&a->session_stop(a->session_stop_context));}
static bool reserve(void*v){pocket_live_app*a=v;return a->owner.held(a->owner.context);}
static bool release(void*v){pocket_live_app*a=v;return a->owner.release(a->owner.context);}
static uint64_t now(void*v){pocket_live_app*a=v;return a->owner.now_ms(a->owner.context);}
static bool prepare(void*v,const pocket_live_session_config*c,bool(*stop)(void*),void*ctx,bool*acquired){
 pocket_live_app*a=v;a->session_stop=stop;a->session_stop_context=ctx;
 return pocket_live_v2_idf_prepare(&a->idf,c,stopped,a,acquired);
}
static pocket_live_v2_worker_result run(void*v){pocket_live_app*a=v;return pocket_live_v2_idf_execute(&a->idf,&a->worker);}
static bool cleanup(void*v){return pocket_live_v2_idf_cleanup(&((pocket_live_app*)v)->idf);}
static bool scrub(void*v){return pocket_live_v2_idf_scrub(&((pocket_live_app*)v)->idf);}
pocket_live_app*pocket_live_app_create(const pocket_live_app_request*r,const pocket_live_app_owner*o){
 if(!pocket_live_v2_device_admission_allowed())return NULL;
 if(!r||!o||!o->held||!o->release||!o->stopped||!o->now_ms||!o->epochs||o->stopped(o->context))return NULL;
 uint64_t current=o->now_ms(o->context);if(r->deadline_ms<=current||r->deadline_ms-current>10000)return NULL;
 pocket_live_app*a=calloc(1,sizeof *a);if(!a)return NULL;a->owner=*o;
 pocket_live_session_ops ops={a,a,reserve,release,prepare,run,cleanup,scrub,now,o->epochs};
 bool ok=pocket_live_session_init(&a->session,&ops)&&pocket_live_session_config_snapshot(&a->session.config,r->origin,r->token,r->ca_pem,r->chat_id,r->target,r->epoch,r->volume);
 if(ok)(void)pocket_live_session_admit(&a->session,&a->session.config,r->deadline_ms);
 if(!ok||a->session.phase==PLS_IDLE){wipe(a,sizeof *a);free(a);return NULL;}
 return a;
}
bool pocket_live_app_execute(pocket_live_app*a){return a&&pocket_live_session_execute(&a->session);}
static bool network_pending(const pocket_live_app*a){return a->session.phase==PLS_CLEANUP_PENDING&&a->session.owner_held&&!a->session.ownership_unknown&&a->session.adapter_prepared&&a->idf.audio_clean&&!a->idf.network_clean;}
bool pocket_live_app_reap(pocket_live_app*a){return a&&(a->session.phase==PLS_RELEASED||(network_pending(a)&&pocket_live_session_reap(&a->session)));}
void pocket_live_app_read(const pocket_live_app*a,pocket_live_app_snapshot*out){
 if(!a||!out)return;
 *out=(pocket_live_app_snapshot){.phase=a->session.phase,.reason=a->session.reason,.worker=a->session.worker,.worker_ran=a->session.worker_ran,.backend_status_known=a->session.backend_status_known,.ownership_unknown=a->session.ownership_unknown,.network_cleanup_pending=network_pending(a)};
}
bool pocket_live_app_destroy(pocket_live_app*a){
 if(!a||a->session.phase!=PLS_RELEASED)return false;
 wipe(a,sizeof *a);free(a);return true;
}
