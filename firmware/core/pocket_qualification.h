/* MIT. Finite injected Stage A runner; no IDF, allocation or network. */
#pragma once
#include "pocket_qualification_protocol.h"
typedef enum {
 PQ_COMPLETE=0,PQ_STOPPED=1,PQ_START_FAILED=2,PQ_STARTUP_TIMEOUT=3,
 PQ_ACQUISITION_TIMEOUT=4,PQ_CLOCK_REGRESSION=5,PQ_CAPTURE_ERROR=6,
 PQ_DISCONTINUITY=7,PQ_SUBMIT_ERROR=8,PQ_EMIT_ERROR=9,
 PQ_CLEANUP_ERROR=10,PQ_SOURCE_INCOMPLETE=11,PQ_INVALID_ARGUMENT=12
} pocket_qualification_result_code;
typedef enum { PQ_NOT_ACQUIRED=0,PQ_CLEANUP_VERIFIED=1,PQ_CLEANUP_UNKNOWN=2 } pocket_qualification_cleanup;
typedef enum { PQ_BEGIN_UNKNOWN=0,PQ_BEGIN_UNTOUCHED=1,PQ_BEGIN_ACQUIRED=2 } pocket_qualification_begin_ownership;
typedef struct {
 uint64_t nonce;uint32_t epoch;uint8_t profile,volume;
} pocket_qualification_config;
typedef struct {
 uint32_t epoch,sequence,sample_offset,dma_consumed,queue_frames;
 uint8_t pcm[PQ_PCM_BYTES];
} pocket_qualification_capture;
typedef struct {
 pocket_qualification_result_code code;
 pocket_qualification_cleanup cleanup;
 uint32_t capture_frames,source_samples,stimulus_samples,dma_consumed,elapsed_ms,peak,wire_bytes;
 bool source_complete,terminal_emitted;
} pocket_qualification_result;
typedef struct {
 void *context;
 /* ACQUIRED must be reported before any owned hardware mutation; UNTOUCHED
  * only when no BSP ownership or mutation was attempted. Default UNKNOWN. Begin stays
  * muted; output wrapper respects saved mute and caps requested volume <=5. */
 bool (*begin)(void*,uint32_t epoch,unsigned volume,pocket_qualification_begin_ownership*ownership);
 int (*capture)(void*,pocket_qualification_capture*); /* -1/0empty/1owned frame */
 int (*submit)(void*,uint32_t offset,const int16_t*,size_t samples); /* -1/0busy/1 */
 bool (*end)(void*,uint32_t epoch); /* stop/mute/restore BEFORE terminal export */
 /* Nonblocking atomic record admission: 1=whole copied,0=busy/no bytes,-1=fatal
  * including ANY partial write. Never retry a partially admitted record. */
 int (*emit)(void*,const uint8_t*,size_t);
 bool (*stopped)(void*); /* Includes physical Stop, heartbeat<=500ms, queue loss. */
 uint64_t (*now_ms)(void*);
 void (*idle)(void*);
} pocket_qualification_ops;
typedef struct {
 bool attempted;
 uint32_t wire_sequence;
 uint64_t last_ms,start_ms;
 uint8_t payload[PQ_MAX_PAYLOAD],wire[PQ_MAX_RECORD];
 int16_t source[PQ_PAIRS];
 pocket_qualification_capture capture;
 pocket_qualification_result result;
} pocket_qualification;
/* Requires zero-initialized persistent per-boot storage. One attempt only;
 * caller already verified host ARM + fresh physical event and owns audio.
 * Startup2s, acquisition10.25/24.25s, busy emit/submit20ms software bounds.
 * No software deadline can preempt a stalled injected operation. */
pocket_qualification_result pocket_qualification_run(pocket_qualification*,const pocket_qualification_config*,const pocket_qualification_ops*);
/* Waveform1: deterministic 16-tap band-shaped hash noise, 10ms ramps, bounded
 * windows [4,6),[10,12),[18,20)s. Source reconstruction is NOT an AEC reference. */
int16_t pocket_qualification_source_sample(uint32_t offset);
bool pocket_qualification_source_window(uint32_t offset);
