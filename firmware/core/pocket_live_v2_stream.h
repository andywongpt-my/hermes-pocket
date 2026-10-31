/* MIT. v2 wire: paired microphone/DAC-reference input, mono speaker output.
 * dma_consumed is ONLY flow credit, never physical playout or an AEC clock. */
#ifndef POCKET_LIVE_V2_STREAM_H
#define POCKET_LIVE_V2_STREAM_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef enum {POCKET_LIVE_V2_OK,POCKET_LIVE_V2_INVALID,POCKET_LIVE_V2_OUT_OF_ORDER,
    POCKET_LIVE_V2_BAD_CREDIT} pocket_live_v2_result;
typedef struct {
    uint8_t kind;
    uint32_t sequence,sample_offset,dma_consumed;
    const uint8_t *pcm;
    size_t pcm_bytes;
} pocket_live_v2_packet;
typedef struct {
    uint8_t kind;
    uint32_t next_sequence,next_sample_offset,dma_consumed;
} pocket_live_v2_state;
/* Kind1: exactly240 interleaved int16LE micL/DACrefR pairs (960B), offsets in
 * mic frames. Kind2:1..480 mono samples(2..960B), field12 zero. Max packet976B.
 * No aliasing of packet struct with output; PCM payload overlap is allowed. */
pocket_live_v2_result pocket_live_v2_pack(const pocket_live_v2_packet *,uint8_t *,size_t,size_t *);
pocket_live_v2_result pocket_live_v2_unpack(const uint8_t *,size_t,pocket_live_v2_packet *);
void pocket_live_v2_init(pocket_live_v2_state *,uint8_t kind);
pocket_live_v2_result pocket_live_v2_accept(pocket_live_v2_state *,const pocket_live_v2_packet *,uint32_t speaker_sent);
#endif
