/* MIT. Inactive Live client state/queue prerequisite, NOT a socket/audio worker.
 * No key discovery, TLS connection, I2S ownership or backend cancellation occurs.
 * A future owner must serialize all calls, validate ready/closed control events,
 * and stop/join capture+playback before touching codec format/sleep/teardown. */
#ifndef POCKET_LIVE_CLIENT_H
#define POCKET_LIVE_CLIENT_H
#include "pocket_live_stream.h"

typedef enum {
    POCKET_LIVE_IDLE, POCKET_LIVE_STARTING, POCKET_LIVE_READY,
    POCKET_LIVE_STOPPING, POCKET_LIVE_CLOSED, POCKET_LIVE_FAILED
} pocket_live_phase;

enum { POCKET_LIVE_PLAYBACK_FRAMES = 4 };
typedef struct {
    pocket_live_phase phase;
    pocket_live_stream_state microphone, speaker;
    uint32_t submitted_samples, rendered_samples;
    unsigned head, queued_frames;
    size_t lengths[POCKET_LIVE_PLAYBACK_FRAMES];
    uint8_t playback[POCKET_LIVE_PLAYBACK_FRAMES][POCKET_LIVE_PCM_BYTES];
} pocket_live_client;

void pocket_live_client_init(pocket_live_client *client);
/* Produces the start JSON, but sends nothing. Chat IDs are 1..64 ASCII URL-safe bytes. */
bool pocket_live_client_begin(pocket_live_client *client, const char *chat_id,
    char *json, size_t capacity);
/* Call only after validated server ready, never immediately after WebSocket upgrade. */
bool pocket_live_client_ready(pocket_live_client *client);
bool pocket_live_client_microphone(pocket_live_client *client, const uint8_t *pcm,
    size_t bytes, uint8_t *wire, size_t capacity, size_t *written);
bool pocket_live_client_speaker(pocket_live_client *client, const uint8_t *wire, size_t bytes);
/* The borrowed view lasts until submitted/stop/closed; do not retain it in DMA. */
bool pocket_live_client_playback_peek(const pocket_live_client *client,
    const uint8_t **pcm, size_t *bytes);
/* Call only after the driver copied the entire peeked frame. This is NOT rendered. */
bool pocket_live_client_playback_submitted(pocket_live_client *client);
/* Only independently measured actual rendered PCM sample counts may enter here.
 * Existing BSP does NOT provide that measurement. Never use write-return bytes,
 * elapsed wall time, server send counts, or raw idle DMA completion counts.
 * This core intentionally supplies no fabricated measurement implementation. */
bool pocket_live_client_rendered(pocket_live_client *client, uint32_t samples);
/* Stops admission only. Does NOT stop hardware, close transport or cancel Hermes. */
void pocket_live_client_stop(pocket_live_client *client);
/* Server finalization is not hardware cleanup or confirmation of backend cancel. */
void pocket_live_client_closed(pocket_live_client *client);
#endif
