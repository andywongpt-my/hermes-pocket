/* MIT. No network task, response buffer, HTTP client or mutating endpoint. */
#include "pocket_actions.h"
#include "pocket_tasks_client.h"
#include "pocket_usage_client.h"
#include "pocket_models_client.h"
#include "pocket_power.h"
#include "pocket_reader.h"
#include "esp_timer.h"
#include <stdatomic.h>
#include <limits.h>
#include <string.h>

static atomic_uint visible=ATOMIC_VAR_INIT(UINT_MAX),requested_target;
static atomic_bool screen_asleep;
static atomic_uint selected_target;
typedef struct {int64_t checked_ms;bool seen;} idle_stamp;
static idle_stamp status_checks[2],usage_checks[2],background_checks[2];
enum {SCREEN_STATUS_MS=30000,INACTIVE_STATUS_MS=120000,SCREEN_USAGE_MS=300000,SCREEN_TASKS_MS=120000};
static bool quiet(void){return atomic_load_explicit(&screen_asleep,memory_order_relaxed)&&!pocket_tasks_active();}
static bool due(idle_stamp *stamp,unsigned interval_ms,bool force){
 const int64_t now=esp_timer_get_time()/1000;
 if(!force&&stamp->seen&&now>=stamp->checked_ms&&now-stamp->checked_ms<interval_ms)return false;
 stamp->seen=true;stamp->checked_ms=now;return true;
}
void pocket_actions_init(void){
 atomic_store_explicit(&visible,UINT_MAX,memory_order_relaxed);
 atomic_store_explicit(&requested_target,0,memory_order_relaxed);
 atomic_store_explicit(&screen_asleep,false,memory_order_relaxed);
 atomic_store_explicit(&selected_target,0,memory_order_relaxed);
 memset(status_checks,0,sizeof status_checks);memset(usage_checks,0,sizeof usage_checks);memset(background_checks,0,sizeof background_checks);
}
void pocket_actions_screen_off(bool off){
 const bool was_off=atomic_exchange_explicit(&screen_asleep,off,memory_order_relaxed);
 if(was_off&&!off)atomic_store_explicit(&requested_target,atomic_load_explicit(&selected_target,memory_order_relaxed)+1,memory_order_relaxed);
}
bool pocket_actions_poll_due(unsigned target){
 if(target>1)return false;
 const unsigned selected=atomic_load_explicit(&selected_target,memory_order_relaxed);
 return due(&status_checks[target],target==selected?SCREEN_STATUS_MS:INACTIVE_STATUS_MS,!quiet());
}
bool pocket_actions_usage_due(unsigned target){
 return target<2&&due(&usage_checks[target],SCREEN_USAGE_MS,!quiet());
}
unsigned pocket_actions_poll_ms(void){
 const bool off=atomic_load_explicit(&screen_asleep,memory_order_relaxed);
 return pocket_power_poll_ms(off,off&&pocket_tasks_active());
}
void pocket_actions_view(pocket_page page,unsigned target){
 if(target>1)return;
 atomic_store_explicit(&selected_target,target,memory_order_relaxed);
 const unsigned view=(unsigned)page<POCKET_PAGE_COUNT?(unsigned)page*2+target:UINT_MAX;
 const unsigned previous=atomic_exchange_explicit(&visible,view,memory_order_relaxed);
 if(page==POCKET_TASKS && previous!=view){pocket_tasks_refresh(target);atomic_store_explicit(&requested_target,target+1,memory_order_relaxed);}
 if(page==POCKET_MODELS && previous!=view){pocket_models_refresh(target);atomic_store_explicit(&requested_target,target+1,memory_order_relaxed);}
}
bool pocket_actions_request(unsigned target,pocket_tool tool){
 if(target>1 || (unsigned)tool>=POCKET_TOOL_READER)return false; /* Reader has its own bounded queue. */
 if(tool==POCKET_TOOL_USAGE)pocket_usage_force(target);
 else if(tool==POCKET_TOOL_TASKS)pocket_tasks_refresh(target);
 atomic_store_explicit(&requested_target,target+1,memory_order_relaxed);return true;
}
unsigned pocket_actions_poll_target(unsigned fallback){
 pocket_reader_snapshot reader;pocket_reader_read(&reader,NULL,0);
 if(reader.active)return reader.target;
 const unsigned requested=atomic_exchange_explicit(&requested_target,0,memory_order_relaxed);
 return requested?requested-1:fallback<2?fallback:0;
}
#if defined(__GNUC__) || defined(__clang__)
__attribute__((noinline))
#endif
static void prepare_visible_task(unsigned target){
 /* A short-lived copy uses the existing worker stack, not another task/result
  * cache. It is gone before the task client allocates its bounded JSON parse. */
 pocket_tasks_snapshot tasks;pocket_tasks_read(target,&tasks);
 if(tasks.list_status==POCKET_TASKS_OFFLINE || tasks.list_status==POCKET_TASKS_UNFETCHED){pocket_tasks_refresh(target);return;}
 if(tasks.list_status!=POCKET_TASKS_READY || !tasks.count || tasks.selected>=tasks.count || tasks.view_status==POCKET_TASKS_LOADING || tasks.view_status==POCKET_TASKS_AUTH_REQUIRED)return;
 const pocket_task_state state=tasks.entries[tasks.selected].state;
 if(state>=POCKET_TASK_STATE_QUEUED && state<=POCKET_TASK_STATE_STOPPING)pocket_tasks_open(target);
}
#if defined(__GNUC__) || defined(__clang__)
__attribute__((noinline))
#endif
static void prepare_background_tasks(unsigned target){
 /* A list GET queues a selected view GET. Consume that work before refreshing
  * the list again, since /view is what advances the backend's stored state.
  * End this stack copy before the bounded JSON parser runs in tasks_poll. */
 pocket_tasks_snapshot tasks;pocket_tasks_read(target,&tasks);
 if(tasks.list_status==POCKET_TASKS_READY && tasks.view_status==POCKET_TASKS_LOADING)return;
 if(due(&background_checks[target],SCREEN_TASKS_MS,!quiet()))pocket_tasks_refresh(target);
}
void pocket_actions_idle(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1 || !link || !response || capacity<2 || capacity>8192)return;
 if(pocket_reader_active()){(void)pocket_reader_poll(target,link,response,capacity);return;}
 if(pocket_models_poll(target,link,response,capacity))return;
 if(atomic_load_explicit(&screen_asleep,memory_order_relaxed))prepare_background_tasks(target);
 else if(atomic_load_explicit(&visible,memory_order_relaxed)==(unsigned)POCKET_TASKS*2+target)prepare_visible_task(target);
 (void)pocket_tasks_poll(target,link,response,capacity); /* At most one pending GET. */
}
void pocket_actions_disconnected(unsigned target){if(target<2){pocket_tasks_disconnected(target);pocket_models_disconnected(target);}}
