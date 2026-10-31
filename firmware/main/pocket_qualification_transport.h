/* MIT. Explicit qualification-only composition; not production admission.
 * Caller supplies fresh physical authorization and holds the normal audio owner.
 * Persistent zeroed storage must survive every pending cleanup. No allocation,
 * settings lookup, endpoint discovery, provider operation or background task. */
#ifndef POCKET_QUALIFICATION_TRANSPORT_H
#define POCKET_QUALIFICATION_TRANSPORT_H
#define POCKET_LIVE_V2_IDF_INTERNAL 1
#include "pocket_live_v2_idf.h"
#if CONFIG_POCKET_QUALIFICATION
typedef enum { PQT_EMPTY, PQT_PREPARED, PQT_RUNNING, PQT_PENDING, PQT_DONE, PQT_RETIRED } pocket_qualification_transport_phase;
typedef struct {
 uint8_t profile; /* Explicit PQ_SILENT or PQ_AUDIO; NEVER inferred from volume. */
 uint64_t request_ms,dispatch_deadline_ms;
 uint32_t duration_ms;
 bool (*stop)(void*);void *stop_context;
 uint64_t (*clock_ms)(void*);void *clock_context;
 pocket_qualification_monitor_fn monitor;void *monitor_context;
} pocket_qualification_transport_options;
typedef struct {
 pocket_live_v2_idf_context idf;
 pocket_live_v2_worker worker;
 pocket_qualification_transport_options options;
 uint64_t stop_deadline_ms,last_ms,stop_observed_ms;
 pocket_live_v2_worker_result result;
 pocket_qualification_transport_phase phase;
 bool stop_latched,clock_regressed,deadline_reached,worker_ran;
} pocket_qualification_transport;
typedef struct {
 pocket_qualification_transport_phase phase;
 pocket_live_v2_worker_result result;
 uint32_t accepted_source_samples,capture_frames,capture_samples,dma_consumed;
 uint64_t stop_observed_ms,audio_stop_begin_ms,audio_stop_end_ms;
 bool cleanup_complete,audio_cleanup_complete,network_cleanup_complete,audio_attempted,network_cleanup_pending,source_rejected;
 bool clock_regressed,deadline_reached,worker_ran;
} pocket_qualification_transport_snapshot;
/* No I/O. Original request timestamp is never refreshed on dispatch. Dispatch
 * deadline stays within its original 10-second budget; duration is 1..60000 ms
 * from request, including queue/startup time. Requires canonical RFC1918 IPv4,
 * explicit custom CA and chat_id "qualification". PQ_SILENT requires volume 0
 * and all-zero source. PQ_AUDIO permits volume 0..5 and the exact capped waveform
 * (zero volume or saved mute never implies permission to unmute). Options/config
 * are copied, including retained callback contexts, which the caller must keep
 * valid until scrub. Numeric monitor is optional and must not block/do I/O. */
bool pocket_qualification_transport_prepare(pocket_qualification_transport*,
 const pocket_live_session_config*,const pocket_qualification_transport_options*,bool *acquired);
bool pocket_qualification_transport_run(pocket_qualification_transport*);
/* Retry only known network teardown after audio clean; never repeat failed or
 * unknown audio cleanup. At most one run, no reconnect/replay. */
bool pocket_qualification_transport_cleanup(pocket_qualification_transport*);
void pocket_qualification_transport_read(const pocket_qualification_transport*,pocket_qualification_transport_snapshot*);
/* Refuses until both clean, scrubs IDF config/worker/callbacks and retires object.
 * Caller releases normal ownership only after successful scrub. */
bool pocket_qualification_transport_scrub(pocket_qualification_transport*);
#endif
#endif
