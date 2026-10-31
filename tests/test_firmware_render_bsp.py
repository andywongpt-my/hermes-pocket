"""Compile opt-in real BSP with fake peripheral boundaries; no hardware claim."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
if __package__:
    from .test_firmware_live_audio import CODEC, harness
    from .test_firmware_audio_drain import HEADERS
else:
    from test_firmware_live_audio import CODEC, harness
    from test_firmware_audio_drain import HEADERS

MAIN=r'''
int64_t esp_timer_get_time(void){return 10000;}
esp_err_t i2s_channel_get_info(i2s_chan_handle_t c,i2s_chan_info_t *out){memset(out,0,sizeof *out);out->is_enabled=c->enabled;return ESP_OK;}

static bool preload_failure;
esp_err_t i2s_channel_preload_data(i2s_chan_handle_t c,const void *p,size_t n,size_t *loaded){
 assert(c==&tx && !tx.enabled && p && n==480); *loaded=preload_failure?0:n; return ESP_OK;
}
esp_err_t i2s_channel_read(i2s_chan_handle_t c,void *p,size_t n,size_t *read,uint32_t timeout){
 assert(c==&rx && rx.enabled && timeout==20); memset(p,0,n);*read=n;return ESP_OK;
}
int main(void){
 (void)drain_cancelled;
 assert(bsp_audio_init()==ESP_OK);expected_rate=24000;
 assert(bsp_audio_set_format(24000,16,1)==ESP_OK);
 assert(bsp_audio_render_start(1)==ESP_OK);
 int16_t pcm[480];for(unsigned i=0;i<480;i++)pcm[i]=1;
 assert(bsp_audio_render_submit(1,0,pcm,480)==ESP_OK);
 assert(bsp_audio_write(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_set_format(24000,16,1)==ESP_ERR_INVALID_STATE);
 uint8_t buffers[6][480];
 bsp_audio_render_status_t status;
 assert(bsp_audio_render_status(&status)==ESP_OK && status.dma_completed==0 && status.queued_samples==480);
 for(unsigned i=0;i<6;i++){
  memset(buffers[i],0,480);i2s_event_data_t event={.dma_buf=buffers[i],.size=480};
  tx.callbacks.on_sent(&tx,&event,tx.context);
 }
 assert(bsp_audio_render_status(&status)==ESP_OK && status.dma_completed==0);
 i2s_event_data_t event={.dma_buf=buffers[0],.size=480};
 memset(buffers[0],0,480);tx.callbacks.on_sent(&tx,&event,tx.context);
 assert(bsp_audio_render_status(&status)==ESP_OK && status.dma_completed==240);
 bsp_audio_render_span_t span;unsigned spans=0;
 while(bsp_audio_render_poll(&span)==ESP_OK)spans++;
 assert(spans==7 && span.source_samples==240 && span.dma_start==1440);
 size_t got=0;assert(bsp_audio_read_bounded(pcm,sizeof pcm,&got,20)==ESP_OK && got==960);
 assert(bsp_audio_render_stop_output()==ESP_OK);
 assert(bsp_audio_render_status(&status)==ESP_OK && status.dma_completed==240 && !status.active);
 assert(bsp_audio_render_submit(1,480,pcm,480)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_write(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_set_format(24000,16,1)==ESP_OK);
 assert(tx.enabled && rx.enabled);
 assert(bsp_audio_render_start(2)==ESP_OK);
 assert(bsp_audio_render_submit(1,0,pcm,480)==ESP_ERR_INVALID_STATE);
 /* A consumer that fails to drain timeline events fails closed. */
 for(unsigned i=0;i<33;i++){
  event.dma_buf=buffers[i%6];memset(event.dma_buf,0,480);
  tx.callbacks.on_sent(&tx,&event,tx.context);
 }
 assert(bsp_audio_render_status(&status)==ESP_OK && status.failed && !status.active);
 assert(bsp_audio_render_stop_output()==ESP_OK);
 assert(bsp_audio_sleep()==ESP_OK);
 assert(bsp_audio_wake()==ESP_OK);preload_failure=true;
 assert(bsp_audio_render_start(3)!=ESP_OK);
 assert(bsp_audio_write(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_render_status(&status)==ESP_OK && status.failed && !status.active);
 assert(bsp_audio_render_stop_output()==ESP_OK);
 assert(bsp_audio_sleep()==ESP_OK);
 preload_failure=false;assert(bsp_audio_wake()==ESP_OK);
 assert(bsp_audio_render_start(4)==ESP_OK);
 assert(rx.callbacks.on_recv_q_ovf);
 rx.callbacks.on_recv_q_ovf(&rx,&event,rx.context);
 assert(bsp_audio_render_status(&status)==ESP_OK && status.failed && !status.active);
 assert(bsp_audio_read_bounded(pcm,sizeof pcm,&got,20)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_render_stop_output()==ESP_OK);
 return 0;
}
'''
class RenderBspTests(unittest.TestCase):
 def test_callback_mode_is_opt_in_and_accounts_only_real_descriptors(self):
  root=Path(__file__).resolve().parents[1]
  sdk=Path(os.environ.get('IDF_PATH',root.parent.parent/'esp-idf-5.5.3'))
  cc=shutil.which('gcc')
  if not cc or not sdk.exists():self.skipTest('GCC/IDF unavailable')
  with tempfile.TemporaryDirectory() as d:
   d=Path(d)
   for name,content in dict(HEADERS,**{'fixture_codec.h':CODEC,'esp_codec_dev.h':'#include "fixture_codec.h"','esp_codec_dev_defaults.h':'#include "fixture_codec.h"','es8311_codec.h':'#include "fixture_codec.h"','esp_timer.h':'#include <stdint.h>\nint64_t esp_timer_get_time(void);'}).items():
    p=d/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
   code=harness().split('int main(int argc,char **argv)')[0]
   code=code.replace('if (tx.enabled || rx.enabled) (void)device_data->enable', '(void)device_data->enable')
   code=code.replace('return enabled ? i2s_channel_enable(channel) : i2s_channel_disable(channel);', 'return (enabled ? i2s_channel_enable(channel) : i2s_channel_disable(channel)) == ESP_OK ? ESP_CODEC_DEV_OK : ESP_CODEC_DEV_DRV_ERR;')
   code=code.replace('assert(channel == &tx && channel->allocated && !channel->enabled);', 'assert(channel->allocated && !channel->enabled);')
   code=code.replace('assert(callbacks->on_sent && !callbacks->on_recv && !callbacks->on_recv_q_ovf && !callbacks->on_send_q_ovf);', 'assert(channel==&tx ? callbacks->on_sent!=NULL : callbacks->on_recv_q_ovf!=NULL);')
   code=code.replace('config->auto_clear_after_cb && !config->auto_clear_before_cb','!config->auto_clear_after_cb && config->auto_clear_before_cb')
   (d/'bsp_audio.c').write_text((root/'firmware/components/bsp/src/bsp_audio.c').read_text())
   (d/'main.c').write_text(code+MAIN)
   includes=[d,root/'firmware/components/bsp/include',root/'firmware/components/bsp/src',sdk/'components/esp_driver_i2s/include',sdk/'components/hal/include',sdk/'components/esp_common/include']
   cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-DFIXTURE_TICK_MS=1','-DCONFIG_BSP_AUDIO_CALLBACK_RENDER=1']+['-I'+str(p) for p in includes]+[str(d/'main.c'),str(root/'firmware/components/bsp/src/bsp_audio_render.c'),str(root/'firmware/components/bsp/src/bsp_audio_capture.c'),str(root/'firmware/components/bsp/src/bsp_es8311_sleep_check.c'),'-o',str(d/'test')]
   r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(d/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
