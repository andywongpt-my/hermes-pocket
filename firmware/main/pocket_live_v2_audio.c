#include "pocket_live_v2_audio.h"
#include "bsp_audio.h"
#include <string.h>
static void wipe(void *value,size_t size) {
    volatile uint8_t *p=value;
    for(size_t i=0;i<size;i++)p[i]=0;
}
static int failed(pocket_live_v2_audio_context*c) {
    c->ready=false;wipe(&c->frame,sizeof c->frame);return -1;
}
bool pocket_live_v2_audio_start(pocket_live_v2_audio_context*c){
    if(!c||!c->epoch||c->volume>100||c->attempted||!c->stopped||c->stopped(c->stop_context))return false;
    c->attempted=true;c->started=true;
    esp_err_t result=bsp_audio_live_begin(c->epoch,c->volume,&c->cleanup_required);
    if(!c->cleanup_required){c->unavailable=true;return false;}
    c->ready=result==ESP_OK&&!c->stopped(c->stop_context);return c->ready;
}
int pocket_live_v2_audio_capture(pocket_live_v2_audio_context*c,pocket_live_v2_capture*out){
    if(!c||!out||!c->ready||c->unavailable||!c->stopped)return -1;
    if(c->stopped(c->stop_context))return failed(c);
    /* v2 uses same-RX codec reference, not TX spans for AEC alignment. */
    for(unsigned i=0;i<32;i++){
        bsp_audio_render_span_t span;esp_err_t e=bsp_audio_render_poll(&span);
        if(e==ESP_ERR_NOT_FOUND)break;
        if(e!=ESP_OK)return failed(c);
    }
    esp_err_t e=bsp_audio_reference_poll(&c->frame);
    if(e==ESP_ERR_NOT_FOUND){wipe(&c->frame,sizeof c->frame);return 0;}
    if(e!=ESP_OK||c->stopped(c->stop_context))return failed(c);
    out->epoch=c->frame.epoch;out->sequence=c->frame.sequence;
    out->sample_offset=c->frame.sample_offset;out->dma_consumed=c->frame.dma_consumed;
    memcpy(out->pcm,c->frame.pcm,sizeof out->pcm);
    wipe(&c->frame,sizeof c->frame);return 1;
}
int pocket_live_v2_audio_submit(pocket_live_v2_audio_context*c,uint32_t offset,const uint8_t*pcm,size_t bytes){
    if(!c||!c->ready||c->unavailable||!c->stopped||!pcm||!bytes||bytes>960||bytes%2)return -1;
    if(c->stopped(c->stop_context))return failed(c);
    bsp_audio_render_status_t s;
    if(bsp_audio_render_status(&s)!=ESP_OK||!s.active||s.failed||s.epoch!=c->epoch||
        s.accepted!=offset||s.queued_samples>1920)return failed(c);
    size_t samples=bytes/2;
    if(samples>1920-s.queued_samples)return 0;
    if(c->stopped(c->stop_context))return failed(c);
    if(!c->output_enabled){
        if(bsp_audio_live_enable_output(c->epoch)!=ESP_OK)return failed(c);
        c->output_enabled=true;
    }
    /* A Stop arriving during serialized register I/O must not admit PCM. */
    if(c->stopped(c->stop_context))return failed(c);
    int16_t aligned[480];memcpy(aligned,pcm,bytes); /* ESP32-C3 is little endian. */
    esp_err_t result=bsp_audio_render_submit(c->epoch,offset,aligned,samples);
    wipe(aligned,sizeof aligned);
    return result==ESP_OK?1:failed(c);
}
bool pocket_live_v2_audio_stop(pocket_live_v2_audio_context*c){
    if(!c)return false;
    c->ready=false;wipe(&c->frame,sizeof c->frame);
    if(c->unavailable)return false; /* Never end foreign or unknown ownership. */
    if(!c->cleanup_required)return !c->started;
    if(bsp_audio_live_end(c->epoch)!=ESP_OK)return false;
    c->cleanup_required=false;c->started=false;c->output_enabled=false;return true;
}
