#include "bsp_audio_render.h"
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#define RENDER_IRAM IRAM_ATTR
#else
#define RENDER_IRAM
#endif

bool bsp_audio_render_begin(bsp_audio_render_t *s, uint32_t epoch, unsigned slots) {
    if (!s || !epoch || (slots != 1 && slots != 2)) return false;
    /* Called only with DMA stopped; avoid hidden ISR memset dependencies. */
    *s = (bsp_audio_render_t){0};
    s->epoch = epoch;
    s->slots = (uint8_t)slots;
    s->active = true;
    return true;
}

bool bsp_audio_render_enqueue(bsp_audio_render_t *s, uint32_t epoch,
                              uint32_t offset, const int16_t *pcm, size_t count) {
    if (!s || !pcm || !s->active || s->failed || epoch != s->epoch ||
        !count || count > 480 || offset != s->accepted ||
        count > UINT32_MAX - s->accepted ||
        count > BSP_RENDER_QUEUE_SAMPLES - s->queued) return false;
    for (size_t i = 0; i < count; i++) {
        unsigned index = (s->head + s->queued + i) % BSP_RENDER_QUEUE_SAMPLES;
        s->queue[index] = pcm[i];
    }
    s->queued += (uint16_t)count;
    s->accepted += (uint32_t)count;
    return true;
}

bool RENDER_IRAM bsp_audio_render_complete(bsp_audio_render_t *s, uint32_t epoch,
                                          void *buffer, size_t bytes) {
    if (!s || !s->active || s->failed || epoch != s->epoch) return false;
    if (!buffer || bytes != BSP_RENDER_FRAMES * s->slots * 2u ||
        s->dma_samples > UINT32_MAX - BSP_RENDER_FRAMES) goto fail;
    unsigned index = s->next;
    uintptr_t address = (uintptr_t)buffer;
    if (s->discovered < BSP_RENDER_DESCRIPTORS) {
        for (unsigned i = 0; i < s->discovered; i++) {
            if (s->descriptor[i].address == address) goto fail;
        }
        index = s->discovered++;
        s->descriptor[index].address = address;
    } else if (s->descriptor[index].address != address) goto fail;
    bsp_audio_render_descriptor_t *d = &s->descriptor[index];
    if (d->samples && (d->source_start != s->dma_completed ||
                      d->samples > s->accepted - s->dma_completed)) goto fail;
    s->completed.epoch = epoch;
    s->completed.dma_start = s->dma_samples;
    s->completed.source_start = d->source_start;
    s->completed.source_samples = d->samples;
    s->dma_completed += d->samples;
    s->dma_samples += BSP_RENDER_FRAMES;
    d->source_start = s->accepted - s->queued;
    d->samples = s->queued < BSP_RENDER_FRAMES ? s->queued : BSP_RENDER_FRAMES;
    uint8_t *out = buffer;
    for (unsigned frame = 0; frame < BSP_RENDER_FRAMES; frame++) {
        uint16_t sample = frame < d->samples ?
            (uint16_t)s->queue[(s->head + frame) % BSP_RENDER_QUEUE_SAMPLES] : 0;
        for (unsigned slot = 0; slot < s->slots; slot++) {
            *out++ = (uint8_t)sample;
            *out++ = (uint8_t)(sample >> 8);
        }
    }
    s->head = (s->head + d->samples) % BSP_RENDER_QUEUE_SAMPLES;
    s->queued -= d->samples;
    s->next = (uint8_t)((index + 1) % BSP_RENDER_DESCRIPTORS);
    return true;
fail:
    s->failed = true;
    s->active = false;
    return false;
}

void bsp_audio_render_stop(bsp_audio_render_t *s) {
    if (!s) return;
    s->active = false;
    s->queued = 0;
    /* Keep last proven completed position, never acknowledge pending audio. */
}
