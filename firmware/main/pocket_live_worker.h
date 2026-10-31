/* MIT. Inactive single-owner Live streaming pump. No transport/audio globals.
 * Every supplied operation must be bounded and run on this one owning task.
 * Stop does not cancel Hermes. This v1 pump cannot establish AEC synchronization. */
#ifndef POCKET_LIVE_WORKER_H
#define POCKET_LIVE_WORKER_H
#include "pocket_live_client.h"

typedef enum { PLW_NONE, PLW_READY, PLW_AUDIO, PLW_CLOSED, PLW_LOST, PLW_ERROR } pocket_live_worker_event_kind;
typedef struct {
    pocket_live_worker_event_kind kind;
    size_t bytes;
    bool finalized, backend_pending, playback_pending;
} pocket_live_worker_event;
typedef enum {
    PLW_STOPPED, PLW_STOPPED_BEFORE_CONNECT, PLW_INVALID_ARGUMENT, PLW_CONNECT_FAILED,
    PLW_SEND_FAILED, PLW_START_TIMEOUT, PLW_PROTOCOL_ERROR, PLW_CONNECTION_LOST,
    PLW_UNCONFIRMED, PLW_AUDIO_ERROR, PLW_CLEANUP_FAILED, PLW_FINALIZE_TIMEOUT,
    PLW_REMOTE_ERROR, PLW_REFERENCE_UNAVAILABLE, PLW_DEVICE_VALIDATION_REQUIRED
} pocket_live_worker_reason;
typedef struct {
    pocket_live_worker_reason reason;
    /* Pending flags are authoritative host reports only when terminal_received.
     * Without it, backend state is unknown; false must not mean no work. */
    bool finalized, backend_pending, playback_pending, cleanup_complete, terminal_received;
} pocket_live_worker_result;
typedef struct {
    void *context;
    bool (*connect)(void *);
    bool (*send)(void *, bool binary, const uint8_t *, size_t);
    /* Receive at most one event; absent data returns NONE. Read timeout <=10 ms.
     * READY and CLOSED must already have strict control/schema validation.
     * Audio writes only to supplied storage, bytes <= capacity. No retained view. */
    bool (*receive)(void *, pocket_live_worker_event *, uint8_t *, size_t);
    bool (*audio_start)(void *);
    /* Partial capture is allowed. Return <=capacity bytes, even-byte PCM16 LE.
     * Must never wait more than 10 ms or silently discard input samples. */
    bool (*capture)(void *, uint8_t *, size_t, size_t *);
    bool (*submit)(void *, uint32_t offset, const uint8_t *, size_t);
    bool (*rendered)(void *, uint32_t *);
    /* Called even if start partially fails; must silence/quiesce DMA ownership. */
    bool (*audio_stop)(void *);
    bool (*close)(void *);
    uint64_t (*now_ms)(void *);
    bool (*stopped)(void *);
} pocket_live_worker_ops;
typedef struct {
    pocket_live_client client;
    uint8_t receive[1024], capture[960], transmit[976];
    size_t captured;
} pocket_live_worker;
/* Caller supplies exclusive audio ownership and stable storage for entire run.
 * No allocation, automatic reconnect/retry/replay, UI binding or credentials.
 * Startup=10s, lifetime=300s, finalization=3s, measured by monotonic now_ms.
 * Deadlines cannot preempt an operation that violates the bounded-op contract. */
pocket_live_worker_result pocket_live_worker_run(pocket_live_worker *,
    const pocket_live_worker_ops *, const char *chat_id);
#endif
