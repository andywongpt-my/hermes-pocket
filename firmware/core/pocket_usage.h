/* Hermes Pocket MIT. Bounded account usage shared by the client and UI. */
#ifndef POCKET_USAGE_H
#define POCKET_USAGE_H
#include <stdbool.h>
#include <stdint.h>

#define POCKET_USAGE_PROVIDER_COUNT 3
#define POCKET_USAGE_METRIC_LIMIT 4

typedef enum {
 POCKET_USAGE_UNFETCHED=0, POCKET_USAGE_UNAVAILABLE, POCKET_USAGE_NOT_CONFIGURED,
 POCKET_USAGE_AVAILABLE, POCKET_USAGE_PARTIAL, POCKET_USAGE_STALE, POCKET_USAGE_FAILED
} pocket_usage_availability;
typedef enum { POCKET_USAGE_PERCENT_USED, POCKET_USAGE_PERCENT_REMAINING, POCKET_USAGE_USD_CREDIT } pocket_usage_unit;
typedef enum { POCKET_USAGE_SUBSCRIPTION, POCKET_USAGE_INCLUDED_CREDIT, POCKET_USAGE_PURCHASED_CREDIT } pocket_usage_category;
typedef struct {
 bool has_value,has_fetched_at,has_resets_at,has_expires_at;
 double value;
 int64_t fetched_at,resets_at,expires_at;
 unsigned window_minutes; /* Zero means unknown. */
 char window[49];
 pocket_usage_unit unit;
 pocket_usage_category category;
 pocket_usage_availability availability;
} pocket_usage_metric;
typedef struct {
 pocket_usage_availability availability;
 unsigned metric_count;
 pocket_usage_metric metrics[POCKET_USAGE_METRIC_LIMIT];
} pocket_usage_provider;
typedef struct { pocket_usage_provider providers[POCKET_USAGE_PROVIDER_COUNT]; } pocket_usage_snapshot;

/* Three short display lines, excluding the fixed provider name. */
typedef struct {
 char value[32],detail[64],freshness[48];
 pocket_usage_availability availability;
} pocket_usage_display;
/* Select a metric/window independently per provider; malformed data stays unknown. */
void pocket_usage_format(const pocket_usage_provider *provider,unsigned window,int64_t now,pocket_usage_display *out);
#endif
