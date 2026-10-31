/* MIT. Bounded Pocket Live wire codec; no I/O, allocation or backend actions. */
#ifndef POCKET_LIVE_STREAM_H
#define POCKET_LIVE_STREAM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    POCKET_LIVE_HEADER_BYTES = 16,
    POCKET_LIVE_PCM_BYTES = 960,
    POCKET_LIVE_PACKET_BYTES = 976,
    POCKET_LIVE_MICROPHONE = 1,
    POCKET_LIVE_SPEAKER = 2
};

typedef enum {
    POCKET_LIVE_OK = 0,
    POCKET_LIVE_INVALID = 1,
    POCKET_LIVE_OUT_OF_ORDER = 2,
    POCKET_LIVE_BAD_PLAYOUT = 3
} pocket_live_result;

/* pcm is a borrowed mono PCM16 LE / 24 kHz view; its storage remains caller-owned. */
typedef struct {
    uint8_t kind;
    uint32_t sequence;
    uint32_t sample_offset;
    uint32_t played_samples;
    const uint8_t *pcm;
    size_t pcm_bytes;
} pocket_live_packet;

typedef struct {
    uint8_t kind;
    uint32_t next_sequence;
    uint32_t next_sample_offset;
    uint32_t played_samples;
} pocket_live_stream_state;

/* On failure out is unchanged and *written is zero (when non-NULL).
 * Payload overlap with out is supported. Input/output structs must not overlap out.
 * Counters that would wrap on acceptance are rejected, never wrapped or replayed. */
pocket_live_result pocket_live_pack(const pocket_live_packet *packet,
    uint8_t *out, size_t capacity, size_t *written);

/* Returns a view into wire on success; leaves *packet unchanged on failure.
 * The actual wire allocation must contain at least size bytes. */
pocket_live_result pocket_live_unpack(const uint8_t *wire, size_t size,
    pocket_live_packet *packet);

/* Initialize once for one direction of one session. Do not reset to accept a replay. */
void pocket_live_state_init(pocket_live_stream_state *state, uint8_t kind);

/* Validate and advance only on success. Host supplies cumulative speaker samples
 * sent to bound microphone playout claims. Speaker packets carry zero played_samples.
 * Rendered counts are device observations, not confirmation of backend cancellation.
 * Caller serializes access to state; this function does not allocate or perform I/O. */
pocket_live_result pocket_live_accept(pocket_live_stream_state *state,
    const pocket_live_packet *packet, uint32_t speaker_samples_sent);

#endif
