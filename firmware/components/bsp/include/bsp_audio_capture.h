#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define BSP_CAPTURE_FRAME_SAMPLES 240u
#define BSP_CAPTURE_FRAME_BYTES 960u
#define BSP_CAPTURE_QUEUE_FRAMES 8u
#define BSP_CAPTURE_DESCRIPTORS 6u
/* Native24k interleaved stereo: micL int16LE, codec DAC referenceR int16LE.
 * Pairing is a shared RX serial frame, NOT zero acoustic/filter delay.
 * dma_consumed is an independent observed source-credit watermark, not a time
 * alignment or evidence of audible playout. Callers serialize with an ISR lock.
 */
typedef struct {
    uint32_t epoch, sequence, sample_offset, dma_consumed;
    uint8_t pcm[BSP_CAPTURE_FRAME_BYTES];
} bsp_audio_capture_frame_t;
typedef struct {
    bsp_audio_capture_frame_t queue[BSP_CAPTURE_QUEUE_FRAMES];
    uintptr_t descriptor[BSP_CAPTURE_DESCRIPTORS];
    int64_t last_callback_us;
    uint32_t epoch, sequence, samples;
    uint8_t head, queued, discovered, next;
    bool active, failed;
} bsp_audio_capture_t;
bool bsp_audio_capture_begin(bsp_audio_capture_t *, uint32_t epoch);
bool bsp_audio_capture_complete(bsp_audio_capture_t *, uint32_t epoch,
                                const void *buffer, size_t bytes,
                                int64_t callback_us, uint32_t dma_consumed);
bool bsp_audio_capture_poll(bsp_audio_capture_t *, bsp_audio_capture_frame_t *);
void bsp_audio_capture_stop(bsp_audio_capture_t *);
