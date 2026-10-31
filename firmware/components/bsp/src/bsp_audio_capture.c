#include "bsp_audio_capture.h"
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#define CAPTURE_IRAM IRAM_ATTR
#else
#define CAPTURE_IRAM
#endif
bool bsp_audio_capture_begin(bsp_audio_capture_t *s, uint32_t epoch) {
    if (!s || !epoch) return false;
    *s = (bsp_audio_capture_t){0};
    s->epoch = epoch;
    s->active = true;
    return true;
}
bool CAPTURE_IRAM bsp_audio_capture_complete(bsp_audio_capture_t *s, uint32_t epoch,
                                            const void *buffer, size_t bytes,
                                            int64_t now, uint32_t consumed) {
    if (!s || !s->active || s->failed || epoch != s->epoch) return false;
    if (!buffer || bytes != BSP_CAPTURE_FRAME_BYTES || now < 0 ||
        s->queued == BSP_CAPTURE_QUEUE_FRAMES || s->sequence == UINT32_MAX ||
        s->samples > UINT32_MAX - BSP_CAPTURE_FRAME_SAMPLES) goto fail;
    /* Six descriptors can mask whole-ring callback loss. At10ms/descriptor,
     * reject >40ms observed callback gaps, rather than trusting address modulo.
     * This guard does not claim timestamp-derived sample alignment.
     */
    if (s->sequence && (now <= s->last_callback_us || now - s->last_callback_us > 40000)) goto fail;
    unsigned at = s->next;
    uintptr_t address = (uintptr_t)buffer;
    if (s->discovered < BSP_CAPTURE_DESCRIPTORS) {
        for (unsigned i = 0; i < s->discovered; i++) if (s->descriptor[i] == address) goto fail;
        at = s->discovered++;
        s->descriptor[at] = address;
    } else if (s->descriptor[at] != address) goto fail;
    bsp_audio_capture_frame_t *frame = &s->queue[(s->head + s->queued) % BSP_CAPTURE_QUEUE_FRAMES];
    frame->epoch = epoch;
    frame->sequence = s->sequence++;
    frame->sample_offset = s->samples;
    frame->dma_consumed = consumed;
    const uint8_t *pcm = buffer;
    for (unsigned i = 0; i < BSP_CAPTURE_FRAME_BYTES; i++) frame->pcm[i] = pcm[i];
    s->samples += BSP_CAPTURE_FRAME_SAMPLES;
    s->queued++;
    s->next = (uint8_t)((at + 1) % BSP_CAPTURE_DESCRIPTORS);
    s->last_callback_us = now;
    return true;
fail:
    s->failed = true;
    s->active = false;
    return false;
}
bool bsp_audio_capture_poll(bsp_audio_capture_t *s, bsp_audio_capture_frame_t *out) {
    if (!s || !out || !s->active || s->failed || !s->queued) return false;
    *out = s->queue[s->head];
    s->head = (uint8_t)((s->head + 1) % BSP_CAPTURE_QUEUE_FRAMES);
    s->queued--;
    return true;
}
void bsp_audio_capture_stop(bsp_audio_capture_t *s) {
    if (!s) return;
    s->active = false;
    s->queued = 0;
}
