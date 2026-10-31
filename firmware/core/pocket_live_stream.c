/* MIT. Independent codec for the opt-in Live protocol; legacy voice is unchanged. */
#include "pocket_live_stream.h"
#include <string.h>

static bool valid(const pocket_live_packet *packet) {
    if (!packet || !packet->pcm || packet->pcm_bytes < 2 ||
        packet->pcm_bytes > POCKET_LIVE_PCM_BYTES || packet->pcm_bytes % 2 != 0) {
        return false;
    }
    if (packet->kind == POCKET_LIVE_MICROPHONE) {
        if (packet->pcm_bytes != POCKET_LIVE_PCM_BYTES) return false;
    } else if (packet->kind == POCKET_LIVE_SPEAKER) {
        if (packet->played_samples != 0) return false;
    } else {
        return false;
    }
    return packet->sequence != UINT32_MAX &&
        packet->sample_offset <= UINT32_MAX - (uint32_t)(packet->pcm_bytes / 2);
}

static void put_u32(uint8_t *out, uint32_t value) {
    out[0] = (uint8_t)(value >> 24);
    out[1] = (uint8_t)(value >> 16);
    out[2] = (uint8_t)(value >> 8);
    out[3] = (uint8_t)value;
}

static uint32_t get_u32(const uint8_t *in) {
    return ((uint32_t)in[0] << 24) | ((uint32_t)in[1] << 16) |
        ((uint32_t)in[2] << 8) | (uint32_t)in[3];
}

pocket_live_result pocket_live_pack(const pocket_live_packet *packet,
    uint8_t *out, size_t capacity, size_t *written) {
    if (written) *written = 0;
    if (!out || !written || !valid(packet) ||
        capacity < POCKET_LIVE_HEADER_BYTES + packet->pcm_bytes) {
        return POCKET_LIVE_INVALID;
    }
    /* Copy first: the caller may pack a view already inside the output buffer. */
    memmove(out + POCKET_LIVE_HEADER_BYTES, packet->pcm, packet->pcm_bytes);
    out[0] = 'P'; out[1] = 'L'; out[2] = 1; out[3] = packet->kind;
    put_u32(out + 4, packet->sequence);
    put_u32(out + 8, packet->sample_offset);
    put_u32(out + 12, packet->played_samples);
    *written = POCKET_LIVE_HEADER_BYTES + packet->pcm_bytes;
    return POCKET_LIVE_OK;
}

pocket_live_result pocket_live_unpack(const uint8_t *wire, size_t size,
    pocket_live_packet *packet) {
    if (!wire || !packet || size < POCKET_LIVE_HEADER_BYTES + 2 ||
        size > POCKET_LIVE_PACKET_BYTES) return POCKET_LIVE_INVALID;
    if (wire[0] != 'P' || wire[1] != 'L' || wire[2] != 1) return POCKET_LIVE_INVALID;
    const pocket_live_packet candidate = {
        .kind = wire[3], .sequence = get_u32(wire + 4),
        .sample_offset = get_u32(wire + 8), .played_samples = get_u32(wire + 12),
        .pcm = wire + POCKET_LIVE_HEADER_BYTES, .pcm_bytes = size - POCKET_LIVE_HEADER_BYTES
    };
    if (!valid(&candidate)) return POCKET_LIVE_INVALID;
    *packet = candidate;
    return POCKET_LIVE_OK;
}

void pocket_live_state_init(pocket_live_stream_state *state, uint8_t kind) {
    if (!state) return;
    memset(state, 0, sizeof *state);
    state->kind = kind;
}

pocket_live_result pocket_live_accept(pocket_live_stream_state *state,
    const pocket_live_packet *packet, uint32_t speaker_samples_sent) {
    if (!state || !valid(packet) || state->kind != packet->kind) {
        return POCKET_LIVE_INVALID;
    }
    if (packet->sequence != state->next_sequence ||
        packet->sample_offset != state->next_sample_offset) {
        return POCKET_LIVE_OUT_OF_ORDER;
    }
    if (packet->kind == POCKET_LIVE_MICROPHONE &&
        (packet->played_samples < state->played_samples ||
         packet->played_samples > speaker_samples_sent)) {
        return POCKET_LIVE_BAD_PLAYOUT;
    }
    state->next_sequence = packet->sequence + 1;
    state->next_sample_offset = packet->sample_offset + (uint32_t)(packet->pcm_bytes / 2);
    state->played_samples = packet->played_samples;
    return POCKET_LIVE_OK;
}
