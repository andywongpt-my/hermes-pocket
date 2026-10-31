/* Offline, separately built image. No networking, NVS, credentials or Live API.
 * Hardware operations occur only after the core's two-part, one-boot consent.
 * USB writes are admission acknowledgements, not proof of host receipt. */
#include "bench_core.h"
#include "bsp_audio.h"
#include "bsp_button.h"
#include "driver/usb_serial_jtag.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <stdatomic.h>
#include <string.h>

typedef struct { bsp_btn_t button; bsp_btn_ev_t event; uint64_t ms; } button_event;
typedef struct {
    bench_core core;
    QueueHandle_t buttons;
    atomic_bool active, cancelled;
    bool initialized;
} bench_context;
static uint64_t now_ms(void) { return (uint64_t)(esp_timer_get_time()/1000); }
static void read_control(bench_context *c) {
    uint8_t input[64];
    int n=usb_serial_jtag_read_bytes(input,sizeof input,0);
    if(n<0 || n>(int)sizeof input) atomic_store(&c->cancelled,true);
    else if(n) bench_input(&c->core,input,(size_t)n,now_ms());
}
static void read_buttons(bench_context *c) {
    button_event event;
    for(unsigned i=0;c->buttons && i<16 && xQueueReceive(c->buttons,&event,0)==pdTRUE;i++)
        bench_button(&c->core,event.button==BSP_BTN_OK,event.event==BSP_BTN_PRESS,event.event==BSP_BTN_CLICK,event.ms);
}
static bool cancelled(void *arg) {
    bench_context *c=arg;
    uint64_t now=now_ms();
    bool expired=now<c->core.last_ms;
    if(c->core.audio_attempted) {
        uint64_t limit=!c->core.audio_ready?2000u:c->core.profile==BENCH_TONE?5250u:10250u;
        expired=expired || now<c->core.started_ms || now-c->core.started_ms>=limit;
    } else {
        expired=expired || now<c->core.armed_ms || now-c->core.armed_ms>=10000u;
    }
    if(expired) atomic_store(&c->cancelled,true);
    read_control(c);
    read_buttons(c);
    return atomic_load(&c->cancelled) || c->core.phase!=BENCH_ACTIVE;
}
static void button_callback(bsp_btn_t button,bsp_btn_ev_t event,void *arg) {
    bench_context *c=arg;
    if(event!=BSP_BTN_PRESS && event!=BSP_BTN_CLICK) return;
    if(event==BSP_BTN_PRESS && atomic_load(&c->active)) atomic_store(&c->cancelled,true);
    button_event item={button,event,now_ms()};
    if(xQueueSend(c->buttons,&item,0)!=pdTRUE) atomic_store(&c->cancelled,true);
}
static bool audio_start(void *arg,bench_profile profile) {
    bench_context *c=arg;
    atomic_store(&c->active,true);
    if(cancelled(c) || bsp_audio_prepare_silent_start()!=ESP_OK || cancelled(c) ||
       bsp_audio_init()!=ESP_OK) return false;
    c->initialized=true;
    if(bsp_audio_set_volume_checked(0)!=ESP_OK || cancelled(c) ||
       bsp_audio_set_format(16000,16,1)!=ESP_OK ||
       bsp_audio_set_volume_checked(0)!=ESP_OK || cancelled(c) ||
       bsp_audio_reference_start(BENCH_EPOCH)!=ESP_OK ||
       bsp_audio_set_volume_checked(0)!=ESP_OK || cancelled(c)) return false;
    if(profile==BENCH_TONE) {
        if(cancelled(c) || bsp_audio_set_volume_checked(5)!=ESP_OK) return false;
    }
    return !cancelled(c);
}
static int audio_capture(void *arg,bench_capture *out) {
    if(cancelled(arg)) return -1;
    bsp_audio_capture_frame_t frame;
    esp_err_t e=bsp_audio_reference_poll(&frame);
    if(e==ESP_ERR_NOT_FOUND) return 0;
    if(e!=ESP_OK) return -1;
    out->epoch=frame.epoch;out->sequence=frame.sequence;out->sample_offset=frame.sample_offset;
    memcpy(out->pcm,frame.pcm,sizeof out->pcm);
    return 1;
}
static int audio_submit(void *arg,uint32_t offset,const int16_t *pcm,size_t samples) {
    if(samples!=BENCH_PAIRS || cancelled(arg)) return -1;
    bsp_audio_render_status_t s;
    if(bsp_audio_render_status(&s)!=ESP_OK || !s.active || s.failed || s.epoch!=BENCH_EPOCH) return -1;
    /* At most one source frame waiting for DMA ownership. Not a playout claim. */
    if(cancelled(arg)) return -1;
    if(s.queued_samples) return 0;
    return bsp_audio_render_submit(BENCH_EPOCH,offset,pcm,samples)==ESP_OK?1:-1;
}
static bool audio_stop(void *arg) {
    bench_context *c=arg;
    atomic_store(&c->active,false);
    bool ok=true;
    /* Do not short-circuit cleanup: muted legacy restore and sleep are checked. */
    if(c->initialized && bsp_audio_set_volume_checked(0)!=ESP_OK) ok=false;
    if(bsp_audio_reference_stop()!=ESP_OK) ok=false;
    if(c->initialized && bsp_audio_set_volume_checked(0)!=ESP_OK) ok=false;
    if(bsp_audio_sleep()!=ESP_OK) ok=false;
    return ok;
}
static int write_record(void *arg,const uint8_t *data,size_t bytes) {
    (void)arg;
    return usb_serial_jtag_write_bytes(data,bytes,0);
}
static uint64_t operation_now(void *arg) { (void)arg; return now_ms(); }
static const bench_ops operations={audio_start,audio_capture,audio_submit,audio_stop,write_record,cancelled,operation_now};
void app_main(void) {
    static bench_context c;
    atomic_init(&c.active,false);atomic_init(&c.cancelled,false);
    bench_init(&c.core,now_ms());
    usb_serial_jtag_driver_config_t usb={.tx_buffer_size=8192,.rx_buffer_size=256};
    if(usb_serial_jtag_driver_install(&usb)!=ESP_OK) return;
    c.buttons=xQueueCreate(16,sizeof(button_event));
    if(!c.buttons || bsp_button_init(button_callback,&c)!=ESP_OK) {
        const uint8_t invalid[]="INVALID\n";
        bench_input(&c.core,invalid,sizeof invalid-1,now_ms());
    }
    for(;;) {
        read_control(&c);
        /* Publish accepted ARM before any queued physical confirmation. */
        if(c.core.phase==BENCH_ARMED) bench_step(&c.core,&operations,&c,now_ms());
        read_buttons(&c);
        if(atomic_load(&c.cancelled) && c.core.phase!=BENCH_TERMINAL) {
            const uint8_t stop[]="STOP\n";
            bench_input(&c.core,stop,sizeof stop-1,now_ms());
        }
        bench_step(&c.core,&operations,&c,now_ms());
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
