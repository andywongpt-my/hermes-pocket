#pragma once
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"
#include "pocket_http_timing.h"
/* Synchronous, bounded transport. Call only from a network worker, never LVGL
 * or button callbacks. Credentials are supplied by future owner pairing. */
typedef struct {
 int status;size_t length;
 int tls_error,tls_code,verify_flags;
 unsigned free_heap,largest_block;
 bool heap_measured;
 /* All event offsets use the same HTTP-entry monotonic origin. Phase costs
  * are microseconds; perform includes transport waits and SDK event posting.
  * event_us is our callback wall time, not SDK event-queue wait time. */
 unsigned connect_ms,headers_ms,response_ms,body_ms,finish_ms,perform_end_ms;
 unsigned pre_heap_us,perform_us,post_heap_us,diagnostic_us,event_us,total_us;
 bool connected,headers_sent,response_started,nodelay,body_started,finished;
 bool heap_before_measured,performed;
 pocket_http_io_timing io;
} pocket_http_result;
esp_err_t pocket_http_json(const char *origin, const char *path,
 const char *device_token, const char *ca_pem, const char *json_body, char *output,
 size_t capacity, pocket_http_result *result);

void pocket_http_close(void);
