#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define BENCH_PAIRS 240u
#define BENCH_CAPTURE_BYTES 960u
#define BENCH_RECORD_MAX 1052u
#define BENCH_EPOCH 1u
typedef enum { BENCH_IDLE, BENCH_ARMED, BENCH_ACTIVE, BENCH_TERMINAL } bench_phase;
typedef enum { BENCH_SILENT, BENCH_TONE } bench_profile;
typedef struct { uint32_t epoch, sequence, sample_offset; uint8_t pcm[BENCH_CAPTURE_BYTES]; } bench_capture;
/* All operations are bounded/nonblocking except serialized codec start/stop.
 * stop must mute, stop paired capture, restore legacy and sleep, checking all.
 * start must check cancelled before any nonzero volume; no operations may log.
 * submit: -1 fatal,0 busy/no copy,1 all240 samples copied; enforce <=240 queued.
 * capture: -1 fatal,0 empty,1 complete owned frame. write never retries a prefix.
 */
typedef struct {
 bool (*start)(void *,bench_profile);
 int (*capture)(void *,bench_capture *);
 int (*submit)(void *,uint32_t,const int16_t *,size_t);
 bool (*stop)(void *);
 int (*write)(void *,const uint8_t *,size_t);
 bool (*cancelled)(void *);
 uint64_t (*now_ms)(void *);
} bench_ops;
typedef struct {
 bench_phase phase; bench_profile profile; const char *code;
 uint64_t last_ms,armed_ms,pressed_ms,started_ms; uint32_t captured,source_samples,wire_sequence;
 bool press_after_arm,restore_ok,io_failed,audio_attempted,report_pending,stop_done,profile_set,audio_ready;
 char command[17]; size_t command_used; bool command_bad;
 int16_t source[BENCH_PAIRS]; uint8_t record[BENCH_RECORD_MAX];
} bench_core;
void bench_init(bench_core *,uint64_t now_ms);
/* Feed bytes in arrival order; no NUL,CR,spaces besides exact commands accepted.
 * ARM is accepted only once from IDLE; expiration/STOP/invalid input latches terminal.
 */
void bench_input(bench_core *,const uint8_t *,size_t,uint64_t now_ms);
/* Physical event timestamp must be sampled in callback, not when dequeued.
 * is_ok identifies button; press=true PRESS, click=true CLICK, other events ignored.
 */
void bench_button(bench_core *,bool is_ok,bool press,bool click,uint64_t event_ms);
/* One bounded pump iteration. ACTIVE may begin only after a fresh OK press+click.
 * Handles all shutdown before reporting; terminal never rearms until reboot.
 */
void bench_step(bench_core *,const bench_ops *,void *,uint64_t now_ms);
size_t bench_encode(uint8_t *,size_t,uint8_t kind,uint32_t sequence,uint32_t offset,const uint8_t *,size_t);
void bench_tone(uint32_t sample_offset,int16_t out[BENCH_PAIRS]);
