#pragma once
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"
/* Synchronous, bounded transport. Call only from a network worker, never LVGL
 * or button callbacks. Credentials are supplied by future owner pairing. */
typedef struct {
 int status;size_t length;
 int tls_error,tls_code,verify_flags;
 unsigned free_heap,largest_block;
 bool heap_measured;
} pocket_http_result;
esp_err_t pocket_http_json(const char *origin, const char *path,
 const char *device_token, const char *ca_pem, const char *json_body, char *output,
 size_t capacity, pocket_http_result *result);

void pocket_http_close(void);
