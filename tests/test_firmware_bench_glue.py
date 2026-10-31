"""Offline glue control-boundary regression; no peripherals or sockets."""
from pathlib import Path
import subprocess,tempfile,unittest
STUB=r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NOT_FOUND 1
#define pdTRUE 1
#define pdFALSE 0
#define pdMS_TO_TICKS(x) (x)
typedef void *QueueHandle_t;
typedef unsigned TickType_t;
typedef int bsp_btn_t;
typedef int bsp_btn_ev_t;
#define BSP_BTN_OK 2
#define BSP_BTN_PRESS 0
#define BSP_BTN_CLICK 1
#define BSP_CAPTURE_FRAME_BYTES 960
#define BSP_CAPTURE_FRAME_SAMPLES 240
typedef struct {uint32_t epoch,sequence,sample_offset,dma_consumed;uint8_t pcm[960];} bsp_audio_capture_frame_t;
typedef struct {uint32_t epoch,accepted,dma_completed,dma_samples,queued_samples;bool active,failed;} bsp_audio_render_status_t;
typedef struct {uint32_t tx_buffer_size,rx_buffer_size;} usb_serial_jtag_driver_config_t;
QueueHandle_t xQueueCreate(unsigned,unsigned);int xQueueSend(QueueHandle_t,const void*,unsigned);int xQueueReceive(QueueHandle_t,void*,unsigned);void vTaskDelay(unsigned);
int64_t esp_timer_get_time(void);
int usb_serial_jtag_read_bytes(void*,uint32_t,unsigned);int usb_serial_jtag_write_bytes(const void*,size_t,unsigned);int usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t*);
int bsp_button_init(void(*)(bsp_btn_t,bsp_btn_ev_t,void*),void*);
int bsp_audio_prepare_silent_start(void);int bsp_audio_init(void);int bsp_audio_set_format(uint32_t,uint8_t,uint8_t);int bsp_audio_set_volume_checked(uint8_t);int bsp_audio_reference_start(uint32_t);int bsp_audio_reference_stop(void);int bsp_audio_sleep(void);int bsp_audio_reference_poll(bsp_audio_capture_frame_t*);int bsp_audio_render_status(bsp_audio_render_status_t*);int bsp_audio_render_submit(uint32_t,uint32_t,const int16_t*,size_t);
'''
HARNESS=r'''
#include "bench_main.c"
#include <assert.h>
static int prepared;static int muted,nonzero,started,stopped,slept;static bool inject_stop,inject_after_reference;static unsigned queued;static uint64_t fake_ms=1;static bool advance_reference,stop_on_status;
QueueHandle_t xQueueCreate(unsigned a,unsigned b){(void)a;(void)b;return(void*)1;}int xQueueSend(QueueHandle_t a,const void*b,unsigned c){(void)a;(void)b;(void)c;return 1;}int xQueueReceive(QueueHandle_t a,void*b,unsigned c){(void)a;(void)b;(void)c;return 0;}void vTaskDelay(unsigned n){(void)n;}
int64_t esp_timer_get_time(void){return (int64_t)(fake_ms*1000);}
int usb_serial_jtag_read_bytes(void*p,uint32_t n,unsigned t){(void)t;if(inject_stop){assert(n>=5);memcpy(p,"STOP\n",5);inject_stop=false;return 5;}return 0;}
int usb_serial_jtag_write_bytes(const void*p,size_t n,unsigned t){(void)p;assert(t==0);return(int)n;}
int usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t*c){(void)c;return 0;}
int bsp_button_init(void(*f)(bsp_btn_t,bsp_btn_ev_t,void*),void*p){(void)f;(void)p;return 0;}
int bsp_audio_prepare_silent_start(void){prepared++;return 0;}int bsp_audio_init(void){assert(prepared);prepared--;return 0;}int bsp_audio_set_format(uint32_t h,uint8_t b,uint8_t c){assert(muted);assert(h==16000&&b==16&&c==1);return 0;}
int bsp_audio_set_volume_checked(uint8_t v){if(v){assert(v<=5);nonzero++;}else muted++;return 0;}
int bsp_audio_reference_start(uint32_t e){assert(e==1&&muted);started++;if(advance_reference)fake_ms+=6000;if(inject_after_reference)inject_stop=true;return 0;}int bsp_audio_reference_stop(void){assert(muted);stopped++;return 0;}int bsp_audio_sleep(void){slept++;return 0;}
int bsp_audio_reference_poll(bsp_audio_capture_frame_t*f){(void)f;return ESP_ERR_NOT_FOUND;}int bsp_audio_render_status(bsp_audio_render_status_t*s){if(stop_on_status)inject_stop=true;*s=(bsp_audio_render_status_t){.epoch=1,.active=true,.queued_samples=queued};return 0;}int bsp_audio_render_submit(uint32_t e,uint32_t o,const int16_t*p,size_t n){(void)e;(void)o;(void)p;assert(n==240);return 0;}
void bench_init(bench_core*c,uint64_t n){memset(c,0,sizeof*c);c->last_ms=n;}
void bench_input(bench_core*c,const uint8_t*p,size_t n,uint64_t t){(void)p;(void)n;(void)t;c->phase=BENCH_TERMINAL;}
void bench_button(bench_core*c,bool a,bool b,bool d,uint64_t t){(void)c;(void)a;(void)b;(void)d;(void)t;}
void bench_step(bench_core*c,const bench_ops*o,void*p,uint64_t t){(void)c;(void)o;(void)p;(void)t;}
int main(void){
 bench_context pre={0};pre.core.phase=BENCH_ACTIVE;pre.core.profile=BENCH_TONE;pre.core.armed_ms=59000;pre.core.last_ms=60000;fake_ms=60000;
 assert(!cancelled(&pre));
 pre.core.audio_attempted=true;pre.core.audio_ready=true;pre.core.started_ms=60000;fake_ms=65249;assert(!cancelled(&pre));fake_ms=65250;assert(cancelled(&pre));fake_ms=1;
 bench_context c={0};c.core.phase=BENCH_ACTIVE;c.core.audio_attempted=true;
 assert(audio_start(&c,BENCH_SILENT));assert(started==1&&nonzero==0);assert(audio_stop(&c));assert(stopped==1&&slept==1);
 bench_context d={0};d.core.phase=BENCH_ACTIVE;d.core.audio_attempted=true;inject_stop=true;
 assert(!audio_start(&d,BENCH_TONE));assert(nonzero==0);assert(audio_stop(&d));
 bench_context late={0};late.core.phase=BENCH_ACTIVE;late.core.audio_attempted=true;inject_after_reference=true;
 assert(!audio_start(&late,BENCH_TONE));assert(nonzero==0);assert(audio_stop(&late));inject_after_reference=false;
 bench_context expired={0};expired.core.phase=BENCH_ACTIVE;expired.core.audio_attempted=true;expired.core.profile=BENCH_TONE;advance_reference=true;
 assert(!audio_start(&expired,BENCH_TONE));assert(nonzero==0);assert(audio_stop(&expired));advance_reference=false;fake_ms=1;
 bench_context e={0};e.core.phase=BENCH_ACTIVE;e.core.audio_attempted=true;assert(audio_start(&e,BENCH_TONE));assert(nonzero==1);
 int16_t pcm[240]={0};queued=1;assert(audio_submit(&e,0,pcm,240)==0);queued=0;assert(audio_submit(&e,0,pcm,240)==1);stop_on_status=true;assert(audio_submit(&e,0,pcm,240)==-1);assert(audio_stop(&e));
 return 0;
}
'''
class BenchGlueTests(unittest.TestCase):
 def test_offline_glue_mute_stop_and_queue_boundaries(self):
  root=Path(__file__).resolve().parents[1]
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'stub.h').write_text(STUB)
   for name in ['bsp_audio.h','bsp_button.h','esp_timer.h','driver/usb_serial_jtag.h','freertos/FreeRTOS.h','freertos/task.h','freertos/queue.h']:
    f=p/name;f.parent.mkdir(exist_ok=True);f.write_text('#include "stub.h"\n')
   (p/'test.c').write_text(HARNESS)
   cmd=['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(p),'-I'+str(root/'firmware/bench/main'),str(p/'test.c'),'-o',str(p/'test')]
   r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5);self.assertEqual(r.returncode,0,r.stderr)
