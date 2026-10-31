/* MIT. Inactive owner lifecycle. No normal-app call site or gate bypass. */
#ifndef POCKET_LIVE_SESSION_H
#define POCKET_LIVE_SESSION_H
#include "pocket_live_session_config.h"
#include "pocket_live_v2_worker.h"
#include <stdatomic.h>
typedef enum { PLS_UNINITIALIZED,PLS_IDLE,PLS_RESERVED,PLS_RUNNING,PLS_CLEANUP_PENDING,PLS_RELEASED } pocket_live_session_phase;
typedef enum { PLS_NONE,PLS_WORKER_RETURNED,PLS_STOPPED,PLS_EXPIRED,PLS_CLOCK_REGRESSION,PLS_RESERVE_FAILED,PLS_PREPARE_FAILED,PLS_EPOCH_REJECTED } pocket_live_session_reason;
typedef struct { uint32_t last; bool exhausted; } pocket_live_session_epoch;
typedef struct {
 void *owner,*adapter;
 bool (*reserve)(void *owner);
 bool (*release)(void *owner);
 /* Memory-only; acquired becomes true BEFORE touching owned adapter storage.
  * Rejection without acquisition must not appropriate an existing context. */
 bool (*prepare)(void *adapter,const pocket_live_session_config*,bool (*stop)(void*),void*,bool *acquired);
 pocket_live_v2_worker_result (*run)(void *adapter);
 bool (*cleanup)(void *adapter);
 bool (*scrub)(void *adapter);
 uint64_t (*now_ms)(void *owner);
 /* Shared across sessions for this owner; mutate only while reserved. */
 pocket_live_session_epoch *epochs;
} pocket_live_session_ops;
typedef struct {
 pocket_live_session_phase phase;
 pocket_live_session_reason reason;
 pocket_live_v2_worker_result worker;
 bool worker_ran,backend_status_known,owner_held,adapter_prepared,adapter_clean,ownership_unknown;
 uint64_t admitted_ms,dispatch_deadline_ms;
 atomic_bool stop;
 pocket_live_session_config config;
 pocket_live_session_ops ops;
} pocket_live_session;
/* First call requires zero-initialized persistent storage. Refuses every repeat,
 * including pending cleanup. Owner serializes all APIs except request_stop. */
bool pocket_live_session_init(pocket_live_session*,const pocket_live_session_ops*);
/* Valid immutable snapshot only; deadline must be in (now,now+10000]. No I/O
 * except generic reservation; prepare is memory-only. One admitted attempt. */
bool pocket_live_session_admit(pocket_live_session*,const pocket_live_session_config*,uint64_t dispatch_deadline_ms);
void pocket_live_session_request_stop(pocket_live_session*);
/* Synchronous owner call, never starts a task. Exactly once; revalidates Stop,
 * clock and dispatch expiry before calling injected run. */
bool pocket_live_session_execute(pocket_live_session*);
/* Explicit cleanup-only progress. Never reruns/reconnects/replays. false retains
 * owner/storage; verified-clean copies may already be scrubbed if release failed.
 * Callers must not free/reset storage or kill the owner while ownership remains. */
bool pocket_live_session_reap(pocket_live_session*);
#endif
