/* MIT. Paired source is implemented; physical acceptance is still required. */
#ifndef POCKET_LIVE_V2_IDF_H
#define POCKET_LIVE_V2_IDF_H
#include "pocket_live_idf.h"
#include "pocket_live_v2_worker.h"
/*Always refuses in this checkpoint with DEVICE_VALIDATION_REQUIRED, not
 * REFERENCE_UNAVAILABLE. No config boolean can certify acoustics, clocks, heap,
 * or stop/restore. V1 separately remains blocked on actual-playout semantics.*/
esp_err_t pocket_live_v2_device_run(const pocket_live_idf_config *,
    pocket_live_v2_worker *,pocket_live_v2_worker_result *);
/* INTERNAL construction only. Optional normal-app caller is hard-refused; no change
 * to pocket_live_v2_device_run's hard refusal. Lifetime spans pending cleanup. */
#ifdef POCKET_LIVE_V2_IDF_INTERNAL
#include "pocket_live_session_config.h"
#include "pocket_live_v2_audio.h"
#include "pocket_live_network_idf.h"
#if CONFIG_POCKET_QUALIFICATION
#include "pocket_qualification_protocol.h"
typedef enum { PQTM_AUDIO_START=1, PQTM_CAPTURE=2, PQTM_SUBMIT=3, PQTM_AUDIO_STOP_RETURNED=4, PQTM_NETWORK_CLOSE_RETURNED=5 } pocket_qualification_monitor_stage;
/* Must be bounded, owner-only, numeric bookkeeping: no logs, I/O or allocation. */
typedef void (*pocket_qualification_monitor_fn)(void*,pocket_qualification_monitor_stage);
#endif
typedef struct {
 pocket_live_session_config snapshot;
 pocket_live_idf_config config;
 pocket_live_network_context network;
 pocket_live_v2_audio_context audio;
 pocket_live_v2_worker *worker;
 uint8_t message[1024],chunk[1024];
 size_t used,expected;
 int opcode;
 bool prepared,issued,retired,audio_clean,network_clean;
#if CONFIG_POCKET_QUALIFICATION
 bool qualification,qualification_failed,qualification_stopping;
 bool qualification_clock_failed,qualification_deadline_reached;
 uint8_t qualification_profile;
 pocket_qualification_monitor_fn qualification_monitor;void *qualification_monitor_context;
 uint32_t qualification_accepted;
 uint64_t qualification_last_ms,qualification_deadline_ms,qualification_stop_observed_ms;
 uint64_t qualification_audio_stop_begin_ms,qualification_audio_stop_end_ms;
#endif
} pocket_live_v2_idf_context;
/* First call requires zero-initialized storage; copies config and does no I/O. */
bool pocket_live_v2_idf_prepare(pocket_live_v2_idf_context*,const pocket_live_session_config*,bool(*stop)(void*),void*,bool *acquired);
#if CONFIG_POCKET_QUALIFICATION
/* Qualification-only fixed route and constrained source, not production entry.
 * No user-supplied path, saved settings, CA fallback, or public endpoint. */
bool pocket_live_v2_idf_qualification_config_valid(const pocket_live_session_config*,uint8_t profile);
bool pocket_live_v2_idf_prepare_qualification(pocket_live_v2_idf_context*,const pocket_live_session_config*,uint8_t profile,bool(*stop)(void*),void*,pocket_qualification_monitor_fn,void*,bool *acquired);
#endif
/* One-shot internal operations handoff, not device authorization. */
bool pocket_live_v2_idf_take_ops(pocket_live_v2_idf_context*,pocket_live_v2_worker_ops*);
/* One-shot internal composition; caller owns worker exclusively and retains
 * BOTH storages until cleanup and scrub succeed. No allocation or new task. */
pocket_live_v2_worker_result pocket_live_v2_idf_execute(pocket_live_v2_idf_context*,pocket_live_v2_worker*);
bool pocket_live_v2_idf_cleanup(pocket_live_v2_idf_context*);
/* Refuses while cleanup is incomplete. Retires context after volatile wipe. */
bool pocket_live_v2_idf_scrub(pocket_live_v2_idf_context*);
#endif
#endif
