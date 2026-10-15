/* MIT. Read-only Pocket tasks; all HTTP runs in the caller's idle worker. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "pocket_setup.h"

#define POCKET_TASKS_LIMIT 12
#define POCKET_TASKS_RESULT_BYTES 768

typedef enum {
 POCKET_TASKS_UNFETCHED, POCKET_TASKS_LOADING, POCKET_TASKS_READY,
 POCKET_TASKS_OFFLINE, POCKET_TASKS_FAILED, POCKET_TASKS_AUTH_REQUIRED
} pocket_tasks_status;
typedef enum {
 POCKET_TASK_STATE_UNKNOWN, POCKET_TASK_STATE_QUEUED, POCKET_TASK_STATE_DISPATCHING,
 POCKET_TASK_STATE_QUEUED_UPSTREAM, POCKET_TASK_STATE_RUNNING,
 POCKET_TASK_STATE_AWAITING_APPROVAL, POCKET_TASK_STATE_STOPPING,
 POCKET_TASK_STATE_COMPLETED, POCKET_TASK_STATE_FAILED, POCKET_TASK_STATE_CANCELLED
} pocket_task_state;
typedef struct {
 uint64_t seq;
 pocket_task_state state;
 char request_id[65];
} pocket_task_entry;
typedef struct {
 unsigned target,count,selected;
 pocket_tasks_status list_status,view_status;
 pocket_task_entry entries[POCKET_TASKS_LIMIT];
 bool has_result,truncated;
 char result[POCKET_TASKS_RESULT_BYTES+1];
} pocket_tasks_snapshot;

/* These only queue work. No task, HTTP client, recording or submission is created.
 * A successful list queues its selected view. Re-open a nonterminal selected task
 * at the caller's bounded idle cadence to refresh its actual upstream phase. */
void pocket_tasks_refresh(unsigned target);
void pocket_tasks_open(unsigned target);
void pocket_tasks_next(unsigned target);
void pocket_tasks_read(unsigned target,pocket_tasks_snapshot *out);
void pocket_tasks_disconnected(unsigned target);
/* Call on pairing/identity changes; no credential contents are retained here. */
void pocket_tasks_reset(unsigned target);
/* At most one pending GET, using the caller's existing <=8192-byte buffer.
 * Returns true if an HTTP request was attempted, including a failed request. */
bool pocket_tasks_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity);
