/* Hermes Pocket MIT. Pure, bounded Usage reminders; caller owns all state. */
#ifndef POCKET_REMINDERS_H
#define POCKET_REMINDERS_H
#include "pocket_usage.h"
#include <stddef.h>

#define POCKET_REMINDER_TARGET_COUNT 2
#define POCKET_REMINDER_EVENT_LIMIT (POCKET_USAGE_PROVIDER_COUNT*POCKET_USAGE_METRIC_LIMIT)

typedef enum { POCKET_REMINDER_THRESHOLD, POCKET_REMINDER_RESET_REFRESH } pocket_reminder_kind;
typedef struct {
 unsigned percent_used_threshold; /* 80 => >=80% used or <=20% remaining. Zero disables percentages. */
 bool usd_floor_enabled; /* Independent from percentages; a zero USD floor is valid. */
 double usd_floor;
} pocket_reminder_config;
typedef struct {
 pocket_reminder_kind kind;
 unsigned target,provider,metric,window_minutes;
 pocket_usage_category category;
 pocket_usage_unit unit;
 char window[49];
 int64_t resets_at;
 bool has_resets_at,has_value;
 double value,threshold; /* Original metric units; never reinterpret USD as percent. */
} pocket_reminder_event;

/* Internal slots are exposed only for static allocation. Initialize, then use
 * the APIs below. No eviction: a fifth distinct category/window is ignored
 * until clear_target/init, so replayed old data cannot repeat a notification. */
typedef struct {
 int64_t resets_at,last_fetched_at;
 unsigned window_minutes;
 char window[49];
 unsigned char flags;
} pocket_reminder_slot;
typedef struct {
 pocket_reminder_slot slots[POCKET_REMINDER_TARGET_COUNT][POCKET_USAGE_PROVIDER_COUNT][POCKET_USAGE_METRIC_LIMIT];
 int64_t last_now;
 unsigned char enabled[POCKET_REMINDER_TARGET_COUNT];
} pocket_reminders_state;

void pocket_reminders_init(pocket_reminders_state *state);
/* Use only for an intentional account/configuration change, not disconnection. */
void pocket_reminders_clear_target(pocket_reminders_state *state,unsigned target);
/* Serialize calls; all timestamps are Unix seconds. NULL config disables reminders. Thresholds require an
 * available sample with known fetched/expiry times, future expiry, and a valid
 * clock. Partial providers may contain an available metric. Unknown reset is
 * allowed and stays explicitly unknown. Used/remaining percentages share a
 * dedupe key; included/purchased USD credit have separate keys and USD floor.
 * Reset notices require a previously observed future reset and matching fresh,
 * available data. Advancing to a later reset after the old deadline can emit
 * the old reset notice when fetched_at is at/after that deadline. New-period
 * thresholds and the next reset remain independent. Stale/failed/unknown
 * observations never trigger any event; expiry is never extended or changed.
 * Clock loss/rollback or disabling disarms pending reset notices; sent flags
 * remain deduplicated. Entries reject regressing reset/sample timestamps.
 * Only emitted events consume dedupe flags. Capacity zero can observe/arm but
 * emits nothing. A full output defers the old notice without losing it; a later
 * call still requires fresh data. At most POCKET_REMINDER_EVENT_LIMIT events
 * are returned. NULL snapshot/output are safe; output needs capacity>0. */
size_t pocket_reminders_evaluate(pocket_reminders_state *state,const pocket_usage_snapshot *snapshot,
 int64_t now,unsigned target,const pocket_reminder_config *config,pocket_reminder_event *events,size_t capacity);
const char *pocket_reminder_message(pocket_reminder_kind kind);
#endif
