/* MIT. No network task, response buffer, HTTP client or mutating endpoint. */
#include "pocket_actions.h"
#include "pocket_tasks_client.h"
#include "pocket_usage_client.h"
#include <stdatomic.h>
#include <limits.h>

static atomic_uint visible=ATOMIC_VAR_INIT(UINT_MAX),requested_target;
void pocket_actions_init(void){
 atomic_store_explicit(&visible,UINT_MAX,memory_order_relaxed);
 atomic_store_explicit(&requested_target,0,memory_order_relaxed);
}
void pocket_actions_view(pocket_page page,unsigned target){
 if(target>1)return;
 const unsigned view=(unsigned)page<POCKET_PAGE_COUNT?(unsigned)page*2+target:UINT_MAX;
 const unsigned previous=atomic_exchange_explicit(&visible,view,memory_order_relaxed);
 if(page==POCKET_TASKS && previous!=view){pocket_tasks_refresh(target);atomic_store_explicit(&requested_target,target+1,memory_order_relaxed);}
}
bool pocket_actions_request(unsigned target,pocket_tool tool){
 if(target>1 || (unsigned)tool>=POCKET_TOOL_TODO_UNAVAILABLE)return false;
 if(tool==POCKET_TOOL_USAGE)pocket_usage_force(target);
 else if(tool==POCKET_TOOL_TASKS)pocket_tasks_refresh(target);
 atomic_store_explicit(&requested_target,target+1,memory_order_relaxed);return true;
}
unsigned pocket_actions_poll_target(unsigned fallback){
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
void pocket_actions_idle(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 if(target>1 || !link || !response || capacity<2 || capacity>8192)return;
 if(atomic_load_explicit(&visible,memory_order_relaxed)==(unsigned)POCKET_TASKS*2+target)prepare_visible_task(target);
 (void)pocket_tasks_poll(target,link,response,capacity); /* At most one pending GET. */
}
void pocket_actions_disconnected(unsigned target){if(target<2)pocket_tasks_disconnected(target);}
