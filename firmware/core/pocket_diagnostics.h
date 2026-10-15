/* MIT. Numeric observations only; no endpoints, response text or credentials. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
 bool checked,wifi,utc_ready,configured,attempted,response_valid,backend_available,stt,tts,custom_ca,heap_measured;
 int transport_error,http_status,tls_error,tls_code,verify_flags;
 unsigned free_heap,largest_block;
 int64_t checked_ms;
} pocket_diagnostics;

void pocket_diagnostics_format(const pocket_diagnostics *value,int64_t now_ms,bool paused,
 char *title,size_t title_capacity,char *body,size_t body_capacity);
