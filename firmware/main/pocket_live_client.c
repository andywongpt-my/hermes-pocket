/* MIT. Transport-independent, opt-in Live client core. No active worker is installed. */
#include "pocket_live_client.h"
#include <stdio.h>
#include <string.h>

_Static_assert(sizeof(pocket_live_client) <= 4200, "Live client queue budget exceeded");

static bool chat_valid(const char *chat) {
    if (!chat) return false;
    size_t length = 0;
    for (; length < 65 && chat[length]; length++) {
        unsigned char c = (unsigned char)chat[length];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
    }
    return length > 0 && length <= 64;
}

void pocket_live_client_init(pocket_live_client *client) {
    if (!client) return;
    memset(client, 0, sizeof *client);
    pocket_live_state_init(&client->microphone, POCKET_LIVE_MICROPHONE);
    pocket_live_state_init(&client->speaker, POCKET_LIVE_SPEAKER);
}

bool pocket_live_client_begin(pocket_live_client *client, const char *chat_id,
    char *json, size_t capacity) {
    if (!client || client->phase != POCKET_LIVE_IDLE || !json || !capacity ||
        !chat_valid(chat_id)) return false;
    int count = snprintf(json, capacity,
        "{\"type\":\"start\",\"version\":1,\"rate\":24000,\"channels\":1,"
        "\"format\":\"pcm_s16le\",\"chat_id\":\"%s\"}", chat_id);
    if (count < 0 || (size_t)count >= capacity) return false;
    client->phase = POCKET_LIVE_STARTING;
    return true;
}

bool pocket_live_client_ready(pocket_live_client *client) {
    if (!client || client->phase != POCKET_LIVE_STARTING) return false;
    client->phase = POCKET_LIVE_READY;
    return true;
}

bool pocket_live_client_microphone(pocket_live_client *client, const uint8_t *pcm,
    size_t bytes, uint8_t *wire, size_t capacity, size_t *written) {
    if (written) *written = 0;
    if (!client || client->phase != POCKET_LIVE_READY) return false;
    const pocket_live_packet packet = {
        .kind = POCKET_LIVE_MICROPHONE, .sequence = client->microphone.next_sequence,
        .sample_offset = client->microphone.next_sample_offset,
        .played_samples = client->rendered_samples, .pcm = pcm, .pcm_bytes = bytes
    };
    pocket_live_stream_state next = client->microphone;
    if (pocket_live_accept(&next, &packet, client->submitted_samples) != POCKET_LIVE_OK ||
        pocket_live_pack(&packet, wire, capacity, written) != POCKET_LIVE_OK) return false;
    client->microphone = next;
    return true;
}

static bool fail(pocket_live_client *client) {
    client->phase = POCKET_LIVE_FAILED;
    client->queued_frames = 0;
    return false;
}

bool pocket_live_client_speaker(pocket_live_client *client, const uint8_t *wire, size_t bytes) {
    if (!client || client->phase != POCKET_LIVE_READY) return false;
    pocket_live_packet packet;
    pocket_live_stream_state next = client->speaker;
    if (client->queued_frames >= POCKET_LIVE_PLAYBACK_FRAMES ||
        pocket_live_unpack(wire, bytes, &packet) != POCKET_LIVE_OK ||
        pocket_live_accept(&next, &packet, 0) != POCKET_LIVE_OK) return fail(client);
    unsigned index = (client->head + client->queued_frames) % POCKET_LIVE_PLAYBACK_FRAMES;
    memcpy(client->playback[index], packet.pcm, packet.pcm_bytes);
    client->lengths[index] = packet.pcm_bytes;
    client->queued_frames++;
    client->speaker = next;
    return true;
}

bool pocket_live_client_playback_peek(const pocket_live_client *client,
    const uint8_t **pcm, size_t *bytes) {
    if (!client || !pcm || !bytes || client->phase != POCKET_LIVE_READY ||
        !client->queued_frames) return false;
    *pcm = client->playback[client->head];
    *bytes = client->lengths[client->head];
    return true;
}

bool pocket_live_client_playback_submitted(pocket_live_client *client) {
    if (!client || client->phase != POCKET_LIVE_READY || !client->queued_frames) return false;
    uint32_t samples = (uint32_t)(client->lengths[client->head] / 2);
    if (samples > UINT32_MAX - client->submitted_samples) return fail(client);
    client->submitted_samples += samples;
    client->head = (client->head + 1) % POCKET_LIVE_PLAYBACK_FRAMES;
    client->queued_frames--;
    return true;
}

bool pocket_live_client_rendered(pocket_live_client *client, uint32_t samples) {
    if (!client || client->phase != POCKET_LIVE_READY || samples < client->rendered_samples ||
        samples > client->submitted_samples) return false;
    client->rendered_samples = samples;
    return true;
}

void pocket_live_client_stop(pocket_live_client *client) {
    if (!client || client->phase == POCKET_LIVE_CLOSED || client->phase == POCKET_LIVE_FAILED) return;
    client->phase = POCKET_LIVE_STOPPING;
    client->queued_frames = 0;
}

void pocket_live_client_closed(pocket_live_client *client) {
    if (!client) return;
    if (client->phase != POCKET_LIVE_FAILED) client->phase = POCKET_LIVE_CLOSED;
    client->queued_frames = 0;
}
