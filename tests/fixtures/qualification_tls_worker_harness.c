/* TEST ONLY: reuse original synthetic harness unchanged; select private chat ID.
 * Worker/control/codec implementations and all harness audio/IPC remain real. */
#define _POSIX_C_SOURCE 200809L
#include "pocket_live_v2_worker.h"
static pocket_live_v2_worker_result qualification_run(pocket_live_v2_worker *worker,
    const pocket_live_v2_worker_ops *ops, const char *chat_id, uint32_t epoch) {
    (void)chat_id;
    return pocket_live_v2_worker_run(worker, ops, "qualification", epoch);
}
#define pocket_live_v2_worker_run qualification_run
#include "live_v2_worker_harness.c"
#undef pocket_live_v2_worker_run
