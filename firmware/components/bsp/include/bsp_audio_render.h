#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define BSP_RENDER_DESCRIPTORS 6u
#define BSP_RENDER_FRAMES 240u
#define BSP_RENDER_QUEUE_SAMPLES 1920u

/* Callback-owned DMA ledger. Not a replacement for the codec write API.
 * Caller serializes every access with one ISR-safe lock. All storage must be
 * internal RAM; the IRQ and the entire call graph must be IRAM safe on ESP.
 * Before begin/reuse/format change, stop channels and quiesce old callbacks.
 * No i2s_channel_write, codec writes or DMA preload may run in this mode.
 * Use auto_clear_before_cb=true, auto_clear_after_cb=false. Slot count must be
 * independently verified against the configured I2S layout (not inferred PCM).
 * Completion is digital DMA delivery, not I2S pin serialization or analog
 * acoustic/codec output timing. Never use dma_completed as v1 played_samples.
 */
typedef struct {
    uintptr_t address;
    uint32_t source_start;
    uint16_t samples;
} bsp_audio_render_descriptor_t;

typedef struct {
    uint32_t epoch, dma_start, source_start;
    uint16_t source_samples; /* zero denotes an all-silence interval */
} bsp_audio_render_span_t;

typedef struct {
    int16_t queue[BSP_RENDER_QUEUE_SAMPLES];
    bsp_audio_render_descriptor_t descriptor[BSP_RENDER_DESCRIPTORS];
    bsp_audio_render_span_t completed;
    uint32_t epoch, accepted, dma_completed, dma_samples;
    uint16_t head, queued;
    uint8_t slots, discovered, next;
    bool active, failed;
} bsp_audio_render_t;

bool bsp_audio_render_begin(bsp_audio_render_t *, uint32_t epoch, unsigned slots);
bool bsp_audio_render_enqueue(bsp_audio_render_t *, uint32_t epoch,
                              uint32_t offset, const int16_t *, size_t samples);
/* Called only for a genuine DMA finished event. It accounts the buffer's prior
 * assigned source, then fills the SAME descriptor for the next ring traversal.
 * Copy completed after each success to retain source/silence timeline spans.
 * Timeline starts at the first callback, not a synchronized microphone epoch.
 */
bool bsp_audio_render_complete(bsp_audio_render_t *, uint32_t epoch,
                               void *dma_buffer, size_t dma_bytes);
void bsp_audio_render_stop(bsp_audio_render_t *);
