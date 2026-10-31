/* Normal-app composition; production readiness remains unconditionally false. */
#pragma once
#include "pocket_live_session.h"
typedef struct pocket_live_app pocket_live_app;
typedef struct {
 void *context;
 bool (*held)(void*);
 bool (*release)(void*);
 bool (*stopped)(void*);
 uint64_t (*now_ms)(void*);
 pocket_live_session_epoch *epochs;
} pocket_live_app_owner;
typedef struct {
 const char *origin,*token,*ca_pem,*chat_id;
 unsigned target,volume;uint32_t epoch;uint64_t deadline_ms;
} pocket_live_app_request;
typedef struct {
 pocket_live_session_phase phase;pocket_live_session_reason reason;
 pocket_live_v2_worker_result worker;
 bool worker_ran,backend_status_known,ownership_unknown,network_cleanup_pending;
} pocket_live_app_snapshot;
/* Worker only. Gate checked before allocation/config access; NULL leaves caller's
 * existing lease untouched. Non-NULL may already be terminal or quarantined. */
pocket_live_app *pocket_live_app_create(const pocket_live_app_request*,const pocket_live_app_owner*);
bool pocket_live_app_execute(pocket_live_app*);
/* Cleanup-only retry permitted only when audio already clean and network pending. */
bool pocket_live_app_reap(pocket_live_app*);
void pocket_live_app_read(const pocket_live_app*,pocket_live_app_snapshot*);
/* Refuses every state except RELEASED; never frees pending cleanup. */
bool pocket_live_app_destroy(pocket_live_app*);
