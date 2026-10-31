#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef enum { PDIAG_IDLE, PDIAG_ARMED, PDIAG_QUEUED, PDIAG_STARTING, PDIAG_RUNNING, PDIAG_DONE, PDIAG_ERROR, PDIAG_CLEANUP_FAILED } pocket_offline_diagnostic_phase;
typedef enum { PDIAG_OK, PDIAG_STOPPED, PDIAG_EXPIRED, PDIAG_START_FAILED, PDIAG_CAPTURE_FAILED, PDIAG_DISCONTINUITY, PDIAG_CLOCK, PDIAG_STARTUP_CAP, PDIAG_RUNTIME_CAP, PDIAG_RESTORE_FAILED, PDIAG_ADMISSION_FAILED } pocket_offline_diagnostic_code;
#define POCKET_DIAGNOSTIC_OBSERVATIONS_VERSION 1
#define POCKET_DIAGNOSTIC_EARLY_SAMPLES 2400u
/* Fixed sample-domain windows: early = [0,2400), late = [2400,samples).
 * No samples are excluded. Means truncate toward zero; RMS includes DC. */
typedef struct {
 uint32_t samples, rms[2], peak[2], clips[2];
 int32_t mean[2];
} pocket_offline_diagnostic_window;
typedef struct {
 pocket_offline_diagnostic_phase phase; pocket_offline_diagnostic_code code;
 uint32_t frames, samples, rms[2], peak[2], clips[2]; int32_t mean[2];
 /* Endpoint counts, not a detector for all analog distortion. Positions are
  * zero-based accepted sample indices, not wall-clock timestamps. UINT32_MAX
  * means no endpoint seen; longest run includes either rail sign. */
 uint32_t positive_clips[2],negative_clips[2];
 uint32_t first_clip_sample[2],last_clip_sample[2],longest_clip_run[2];
 pocket_offline_diagnostic_window early,late;
 bool restore_ok,cleanup_attempted;
} pocket_offline_diagnostic_snapshot;
typedef struct { uint32_t epoch,sequence,sample_offset;uint8_t pcm[960]; } pocket_offline_diagnostic_frame;
typedef struct {
 bool (*begin)(void*,uint32_t,bool *cleanup_required);
 int (*capture)(void*,pocket_offline_diagnostic_frame*);
 bool (*end)(void*);
 bool (*cancelled)(void*);
 uint64_t (*now_ms)(void*);
 void (*idle)(void*);
 void (*publish)(void*,const pocket_offline_diagnostic_snapshot*);
} pocket_offline_diagnostic_ops;
/* Single existing audio owner only; never submits output or calls network.
 * Startup2s/acquisition10.25s are observed software deadlines, not preemption.
 * Returns false when cleanup failed or begin rejected unknown BSP ownership;
 * true means safe to release this worker lease, not necessarily verified cleanup.
 */
bool pocket_offline_diagnostic_run(const pocket_offline_diagnostic_ops*,void*);
/* Serialized by caller. Callback records physical token before UI queueing. */
typedef struct {
 pocket_offline_diagnostic_phase phase; pocket_offline_diagnostic_code code;
 uint64_t armed_ms,pressed_ms; uint32_t armed_nonce,pressed_nonce;
 bool used,stop;
} pocket_offline_diagnostic_control;
bool pocket_offline_diagnostic_arm(pocket_offline_diagnostic_control*,uint64_t,uint32_t);
void pocket_offline_diagnostic_button(pocket_offline_diagnostic_control*,bool,bool,uint64_t,uint32_t);
bool pocket_offline_diagnostic_admit(pocket_offline_diagnostic_control*,uint64_t,uint32_t,uint64_t);
bool pocket_offline_diagnostic_dispatch(pocket_offline_diagnostic_control*,uint64_t);
void pocket_offline_diagnostic_input_lost(pocket_offline_diagnostic_control*);
void pocket_offline_diagnostic_expire(pocket_offline_diagnostic_control*,uint64_t);
