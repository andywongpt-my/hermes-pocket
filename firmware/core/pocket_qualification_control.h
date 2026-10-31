/* MIT. Pure Stage A/B admission/parser. Caller serializes every call with voice mux. */
#pragma once
#include "pocket_qualification_protocol.h"
#define PQ_HOST_CHUNK_MAX 256u
#define PQ_HOST_WIRE_MAX 65536u
#define PQ_HEARTBEAT_MS 500u
#define PQ_FRAGMENT_MS 500u
#define PQ_ARM_MS 10000u
#define PQ_OVERALL_MS 45000u
#define PQ_TLS_OBSERVATION_MS 75000u
#define PQ_TLS_CA_MAX 768u
typedef struct {
 char host[16],token[129],ca[PQ_TLS_CA_MAX+1];
 uint16_t port;uint32_t duration_ms;
} pocket_qualification_tls_config;
typedef enum { PQC_IDLE,PQC_HOST_ARMED,PQC_PHYSICAL_ARMED,PQC_ADMITTED,PQC_RUNNING,PQC_TERMINAL,PQC_QUARANTINED } pocket_qualification_control_phase;
typedef enum { PQC_OK,PQC_COMPLETE,PQC_STOPPED,PQC_EXPIRED,PQC_HEARTBEAT_LOST,PQC_INPUT_LOST,PQC_CLOCK,PQC_WIRE,PQC_SEQUENCE,PQC_NONCE,PQC_BUDGET,PQC_GENERATION,PQC_CONFIRMATION,PQC_CLEANUP } pocket_qualification_control_reason;
typedef struct {
 pocket_qualification_control_phase phase;
 pocket_qualification_control_reason reason;
 uint8_t profile;uint64_t nonce,deadline_ms,request_ms,dispatch_deadline_ms;
 uint32_t duration_ms;
 uint32_t generation,host_sequence,wire_bytes,code;
 bool stop,owned,safe_release,cleanup_verified,tls;
} pocket_qualification_control_snapshot;
typedef struct {
 pocket_qualification_control_snapshot state;
 uint64_t last_ms,host_ms,heartbeat_ms,pressed_ms,fragment_ms;
 uint32_t host_generation,pressed_generation,confirm_generation;
 size_t used;uint8_t buffer[PQ_MAX_RECORD];
 bool initialized,clock_seen,heartbeat_seen,ok_held,released,tls_config_ready,tls_config_taken;
 pocket_qualification_tls_config tls_config;
} pocket_qualification_control;
/* Zero storage required; reinit is refused, preserving one attempt per boot. */
bool pocket_qualification_control_init(pocket_qualification_control*);
bool pocket_qualification_control_feed(pocket_qualification_control*,const uint8_t*,size_t,uint64_t now_ms);
/* Callback token captured before queueing. Release is distinct from LONG/CLICK. */
uint32_t pocket_qualification_control_button(pocket_qualification_control*,bool is_ok,bool press,bool release,uint64_t now_ms);
bool pocket_qualification_control_arm(pocket_qualification_control*,uint64_t long_event_ms,uint32_t generation,uint64_t now_ms);
bool pocket_qualification_control_admit(pocket_qualification_control*,uint64_t press_ms,uint32_t generation,uint64_t now_ms);
bool pocket_qualification_control_dispatch(pocket_qualification_control*,uint64_t now_ms);
void pocket_qualification_control_input_lost(pocket_qualification_control*);
/* Poll even while IDLE: an incomplete first record may contain secret bytes.
 * Every incomplete record expires500ms from first byte, never refreshed by trickle. */
bool pocket_qualification_control_check(pocket_qualification_control*,uint64_t now_ms);
/* Only an admitted owner may finish. Unsafe release permanently quarantines.
 * Safe release without verified cleanup means no audio ownership was acquired;
 * it must never be presented as a checked hardware restore. Verified implies safe.
 * PQC_COMPLETE means the control attempt ended, NOT an audio/experiment pass;
 * caller-provided code and runner terminal evidence must be interpreted too. */
bool pocket_qualification_control_finish(pocket_qualification_control*,bool safe_release,bool cleanup_verified,uint32_t code);
void pocket_qualification_control_read(const pocket_qualification_control*,pocket_qualification_control_snapshot*);

/* Worker only, while caller holds serialization mutex; no I/O. One copy only,
 * admitted/running, not stopped, at a complete-record boundary (used==0).
 * Refusal with a partial incoming record retains it; no bytes are discarded.
 * Wipes internal config/parser after copy.
 * Output must not overlap controller; non-overlap output cleared on refusal.
 * CA limit768 is a wire compatibility limit, never truncation or TLS bypass. */
bool pocket_qualification_control_take_tls_config(pocket_qualification_control*,pocket_qualification_tls_config*);
