"""Actual v2 audio adapter using fake public BSP, not hardware validation."""
from pathlib import Path
import subprocess,shutil,tempfile,unittest
STUB=r'''
#pragma once
#include "bsp_audio_capture.h"
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NOT_FOUND 5
#define ESP_ERR_INVALID_STATE 6
typedef struct {uint32_t epoch,accepted,dma_completed,dma_samples,queued_samples;bool active,failed;} bsp_audio_render_status_t;
typedef struct {uint32_t epoch,dma_start,source_start;uint16_t source_samples;} bsp_audio_render_span_t;
esp_err_t bsp_audio_reference_start(uint32_t);
esp_err_t bsp_audio_live_begin(uint32_t,unsigned,bool*);
esp_err_t bsp_audio_live_enable_output(uint32_t);
esp_err_t bsp_audio_live_end(uint32_t);
esp_err_t bsp_audio_reference_poll(bsp_audio_capture_frame_t*);
esp_err_t bsp_audio_reference_stop(void);
esp_err_t bsp_audio_render_status(bsp_audio_render_status_t*);
esp_err_t bsp_audio_render_poll(bsp_audio_render_span_t*);
esp_err_t bsp_audio_render_submit(uint32_t,uint32_t,const int16_t*,size_t);
'''
HARNESS=r'''
#include "pocket_live_v2_audio.h"
#include "bsp_audio.h"
#include <assert.h>
#include <string.h>
static int start_error,stop_error,poll_error,enable_error,submit_calls,stop_calls,begin_calls,enable_calls,spans_pending;
static bool acquire=true, stop_flag, stop_on_enable;
static bool stopped(void*v){(void)v;return stop_flag;}
static bsp_audio_render_status_t status={.epoch=7,.active=true};
/* These older primitives must no longer be called directly by the adapter. */
esp_err_t bsp_audio_reference_start(uint32_t e){(void)e;assert(!"old start bypassed session lease");return 6;}
esp_err_t bsp_audio_reference_stop(void){assert(!"old stop bypassed session lease");return 6;}
esp_err_t bsp_audio_live_begin(uint32_t e,unsigned volume,bool*cleanup){assert(e==7&&volume==60);begin_calls++;*cleanup=acquire;return start_error;}
esp_err_t bsp_audio_live_enable_output(uint32_t e){assert(e==7);enable_calls++;if(stop_on_enable)stop_flag=true;return enable_error;}
esp_err_t bsp_audio_live_end(uint32_t e){assert(e==7);stop_calls++;return stop_error;}
esp_err_t bsp_audio_render_status(bsp_audio_render_status_t*out){*out=status;return 0;}
esp_err_t bsp_audio_render_poll(bsp_audio_render_span_t*out){(void)out;if(spans_pending){spans_pending--;return ESP_OK;}return ESP_ERR_NOT_FOUND;}
esp_err_t bsp_audio_render_submit(uint32_t e,uint32_t at,const int16_t*p,size_t n){assert(e==7&&at==0&&p[0]==0x1234&&n==480);assert(enable_calls);submit_calls++;return 0;}
esp_err_t bsp_audio_reference_poll(bsp_audio_capture_frame_t*out){if(poll_error)return poll_error;*out=(bsp_audio_capture_frame_t){.epoch=7,.sequence=4,.sample_offset=960,.dma_consumed=123};out->pcm[0]=0x34;out->pcm[1]=0x12;out->pcm[2]=0xcc;out->pcm[3]=0xed;return 0;}
static void wiped(const pocket_live_v2_audio_context*c){const uint8_t*p=(const uint8_t*)&c->frame;for(unsigned i=0;i<sizeof c->frame;i++)assert(!p[i]);}
int main(void){pocket_live_v2_audio_context c={.epoch=7,.volume=60,.stopped=stopped};pocket_live_v2_capture frame;
 assert(pocket_live_v2_audio_start(&c)&&c.started&&begin_calls==1&&enable_calls==0);
 assert(!pocket_live_v2_audio_start(&c)&&begin_calls==1);
 assert(pocket_live_v2_audio_capture(&c,&frame)==1);wiped(&c);
 assert(frame.epoch==7&&frame.sequence==4&&frame.sample_offset==960&&frame.dma_consumed==123);
 assert(frame.pcm[0]==0x34&&frame.pcm[2]==0xcc);
 for(unsigned i=0;i<64;i++){spans_pending++;assert(pocket_live_v2_audio_capture(&c,&frame)==1);assert(spans_pending==0);}
 uint8_t pcm[960]={0x34,0x12};
 status.queued_samples=1500;assert(pocket_live_v2_audio_submit(&c,0,pcm,sizeof pcm)==0&&submit_calls==0&&enable_calls==0);
 status.queued_samples=0;assert(pocket_live_v2_audio_submit(&c,0,pcm,sizeof pcm)==1&&submit_calls==1&&enable_calls==1);
 assert(pocket_live_v2_audio_submit(&c,0,pcm,sizeof pcm)==1&&enable_calls==1);
 poll_error=ESP_ERR_NOT_FOUND;assert(pocket_live_v2_audio_capture(&c,&frame)==0);
 poll_error=ESP_ERR_INVALID_STATE;assert(pocket_live_v2_audio_capture(&c,&frame)==-1);wiped(&c);
 assert(pocket_live_v2_audio_submit(&c,0,pcm,sizeof pcm)==-1&&submit_calls==2);
 stop_error=ESP_ERR_INVALID_STATE;assert(!pocket_live_v2_audio_stop(&c)&&c.started);
 stop_error=0;assert(pocket_live_v2_audio_stop(&c)&&!c.started&&stop_calls==2);wiped(&c);
 assert(!pocket_live_v2_audio_start(&c)&&begin_calls==1);
 assert(pocket_live_v2_audio_stop(&c)&&stop_calls==2);
 /* Partially acquired failed start still ends its own lease. */
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=60,.stopped=stopped};start_error=ESP_ERR_INVALID_STATE;
 assert(!pocket_live_v2_audio_start(&c)&&c.started);
 assert(pocket_live_v2_audio_stop(&c)&&stop_calls==3);
 /* A rejection with no acquired bracket must never end foreign ownership. */
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=60,.stopped=stopped};acquire=false;
 assert(!pocket_live_v2_audio_start(&c));assert(!pocket_live_v2_audio_stop(&c)&&stop_calls==3);
 assert(!pocket_live_v2_audio_start(&c));
 /* Failed explicit output enable never submits PCM, but ends our acquired lease. */
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=60,.stopped=stopped};acquire=true;start_error=0;poll_error=0;enable_error=6;
 assert(pocket_live_v2_audio_start(&c));assert(pocket_live_v2_audio_submit(&c,0,pcm,sizeof pcm)==-1&&submit_calls==2);
 assert(pocket_live_v2_audio_stop(&c)&&stop_calls==4);
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=101,.stopped=stopped};assert(!pocket_live_v2_audio_start(&c));
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=60,.stopped=stopped};assert(pocket_live_v2_audio_stop(&c)&&stop_calls==4);
 /* Stop during register enable must not admit the subsequent PCM write. */
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=60,.stopped=stopped};enable_error=0;stop_on_enable=true;
 assert(pocket_live_v2_audio_start(&c));assert(pocket_live_v2_audio_submit(&c,0,pcm,sizeof pcm)==-1&&submit_calls==2);
 assert(pocket_live_v2_audio_stop(&c)&&stop_calls==5);
 c=(pocket_live_v2_audio_context){.epoch=7,.volume=60,.stopped=stopped};int begins=begin_calls;
 assert(!pocket_live_v2_audio_start(&c)&&begin_calls==begins);assert(pocket_live_v2_audio_stop(&c));
 return 0;}
'''
class FirmwareLiveV2AudioTests(unittest.TestCase):
    def test_paired_queue_flow_credit_and_restore_ownership(self):
        root=Path(__file__).resolve().parents[1];src=root/'firmware/main/pocket_live_v2_audio.c'
        self.assertTrue(src.exists(),'paired adapter missing')
        cc=shutil.which('gcc')
        if not cc:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'bsp_audio.h').write_text(STUB);(p/'test.c').write_text(HARNESS)
            cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2','-I'+str(p),
                '-I'+str(root/'firmware/main'),'-I'+str(root/'firmware/core'),'-I'+str(root/'firmware/components/bsp/include'),
                str(p/'test.c'),str(src),'-o',str(p/'test')]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=20);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
