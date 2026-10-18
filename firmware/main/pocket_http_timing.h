/* MIT. Fixed numeric counters for one synchronous upload request. */
#pragma once
#include <stdbool.h>
typedef struct {
 unsigned write_us,body_write_us,read_us,event_post_us;
 unsigned write_calls,read_calls,event_posts,body_bytes;
} pocket_http_io_timing;
/* Begin/end belong to the single network worker. Wrappers filter other tasks. */
void pocket_http_timing_begin(bool enabled);
void pocket_http_timing_headers_sent(void);
void pocket_http_timing_end(pocket_http_io_timing *out);
