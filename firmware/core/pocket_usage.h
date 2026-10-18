/* Hermes Pocket MIT. Bounded account usage shared by the client and UI. */
#ifndef POCKET_USAGE_H
#define POCKET_USAGE_H
#include <stdbool.h>
#include <stdint.h>

#define POCKET_USAGE_PROVIDER_COUNT 3
#define POCKET_USAGE_METRIC_LIMIT 4
#define POCKET_USAGE_COUNTER_MAX UINT64_C(9007199254740991)
enum { POCKET_USAGE_LOCAL_PAGES=15,POCKET_USAGE_DASHBOARD_PAGES=19,POCKET_USAGE_SELECTOR_CYCLE=228 };

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

typedef enum {
 POCKET_USAGE_RUNS,POCKET_USAGE_API_CALLS,POCKET_USAGE_INPUT_TOKENS,POCKET_USAGE_OUTPUT_TOKENS,
 POCKET_USAGE_CACHE_READ_TOKENS,POCKET_USAGE_CACHE_WRITE_TOKENS,POCKET_USAGE_REASONING_TOKENS,POCKET_USAGE_COUNTER_COUNT
} pocket_usage_counter;
typedef enum {
 POCKET_USAGE_OWNED_RUNS,POCKET_USAGE_OBSERVED_RUNS,POCKET_USAGE_MISSING_RUNS,
 POCKET_USAGE_UNATTRIBUTED_RUNS,POCKET_USAGE_UNMAPPED_OBSERVATIONS,POCKET_USAGE_PROVIDER_OBSERVED_RUNS,POCKET_USAGE_COVERAGE_COUNT
} pocket_usage_coverage;
/* Optional additive runtime history. Known masks distinguish observed zero from
 * null; exact safe JSON integers never pass through a 32-bit counter. No totals
 * are derived from overlapping input/cache counters. Source/scope/period are
 * fixed by the validated runtime contract, never printed from untrusted text. */
typedef struct {
 uint64_t main[POCKET_USAGE_COUNTER_COUNT],auxiliary[POCKET_USAGE_COUNTER_COUNT];
 uint64_t coverage[POCKET_USAGE_COVERAGE_COUNT];
 int64_t fetched_at,last_seen;
 pocket_usage_availability availability;
 uint8_t main_known,auxiliary_known,coverage_known;
 bool present,has_fetched_at,has_last_seen;
} pocket_usage_local;
typedef struct {
 pocket_usage_availability availability;
 unsigned metric_count;
 pocket_usage_metric metrics[POCKET_USAGE_METRIC_LIMIT];
 pocket_usage_local local;
} pocket_usage_provider;
typedef struct { pocket_usage_provider providers[POCKET_USAGE_PROVIDER_COUNT]; } pocket_usage_snapshot;

/* Three short display lines, excluding the fixed provider name. */
typedef struct {
 char value[32],detail[64],freshness[48];
 pocket_usage_availability availability;
} pocket_usage_display;
/* Select a metric/window independently per provider; malformed data stays unknown. */
void pocket_usage_format(const pocket_usage_provider *provider,unsigned window,int64_t now,pocket_usage_display *out);
/* Runtime pages0..6: calls,input,output,cache read,cache write,reasoning,runs;
 * 7..12: coverage enum order;13: actual read time;14: last source observation.
 * Missing local data stays unknown even when another provider has a report. */
const char *pocket_usage_local_title(unsigned page);
void pocket_usage_local_format(const pocket_usage_local *local,unsigned page,int64_t now,pocket_usage_display *out);
#endif
