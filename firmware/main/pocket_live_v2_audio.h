/* MIT. Internal single-owner paired BSP adapter. No legacy/global activation. */
#ifndef POCKET_LIVE_V2_AUDIO_H
#define POCKET_LIVE_V2_AUDIO_H
#include "pocket_live_v2_worker.h"
#include "bsp_audio_capture.h"
typedef struct {
    uint32_t epoch;
    unsigned volume; /* Frozen user intent, 0..100; no automatic escalation. */
    bool (*stopped)(void *);
    void *stop_context; /* Persistent owner callback, not a borrowed stack. */
    bool attempted, started, ready, cleanup_required, unavailable, output_enabled;
    bsp_audio_capture_frame_t frame;
} pocket_live_v2_audio_context;
/* Zero-initialized persistent context; exactly one start attempt. Begin obtains
 * a separate Live lease and remains checked-muted. Output is explicitly enabled
 * only for the first validated speaker submission, never for construction/start.
 * Partial begin still requires end; a rejected foreign/unknown lease must not
 * be ended and leaves this context unavailable. No diagnostic-lease bypass.
 */
bool pocket_live_v2_audio_start(pocket_live_v2_audio_context *);
int pocket_live_v2_audio_capture(pocket_live_v2_audio_context *,pocket_live_v2_capture *);
int pocket_live_v2_audio_submit(pocket_live_v2_audio_context *,uint32_t,const uint8_t *,size_t);
bool pocket_live_v2_audio_stop(pocket_live_v2_audio_context *);
#endif
