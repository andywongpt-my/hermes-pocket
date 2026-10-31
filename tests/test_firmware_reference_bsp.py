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
static bool corrupt_reference_readback;
static int64_t callback_us;
int64_t esp_timer_get_time(void){ callback_us+=10000; return callback_us; }
esp_err_t i2s_channel_get_info(i2s_chan_handle_t c,i2s_chan_info_t *out){
 static i2s_std_config_t cfg;
 memset(out,0,sizeof *out);memset(&cfg,0,sizeof cfg);
 out->is_enabled=c->enabled;out->mode=I2S_COMM_MODE_STD;
 unsigned channels=c==&tx?tx_channels:rx_channels;
 out->total_dma_buf_size=6*240*channels*2;
 cfg.clk_cfg.sample_rate_hz=c==&tx?tx_rate:rx_rate;
 cfg.slot_cfg.data_bit_width=I2S_DATA_BIT_WIDTH_16BIT;
 cfg.slot_cfg.slot_mode=channels==2?I2S_SLOT_MODE_STEREO:I2S_SLOT_MODE_MONO;
 cfg.slot_cfg.slot_mask=c==&tx?tx_mask:rx_mask;
 out->mode_cfg=&cfg;return ESP_OK;
}
esp_err_t i2s_channel_preload_data(i2s_chan_handle_t c,const void *p,size_t n,size_t *loaded){
 assert(c==&tx && !tx.enabled && p && n==960);*loaded=n;return ESP_OK;
}
esp_err_t i2s_channel_read(i2s_chan_handle_t c,void *p,size_t n,size_t *read,uint32_t timeout){
 (void)c;(void)p;(void)n;(void)read;(void)timeout;assert(0);return ESP_FAIL;
}
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 assert(bsp_audio_init()==ESP_OK);
 assert(bsp_audio_set_format(16000,16,1)==ESP_OK);
 bsp_audio_set_volume(37);assert(last_no_dac_ref);
 assert(bsp_audio_reference_start(1)==ESP_OK);
 assert(!last_no_dac_ref && registers[0x44]==0x58);
 assert(tx_rate==24000 && rx_rate==24000 && tx_channels==2 && rx_channels==2);
 assert(tx_mask==I2S_STD_SLOT_BOTH && rx_mask==I2S_STD_SLOT_BOTH);
 int16_t mono[480];for(unsigned i=0;i<480;i++)mono[i]=(int16_t)(i+1);
 assert(bsp_audio_render_submit(1,0,mono,480)==ESP_OK);
 uint8_t txbuf[6][960],rxbuf[6][960];
 bsp_audio_capture_frame_t frame;
 for(unsigned d=0;d<6;d++){
  memset(txbuf[d],0,960);i2s_event_data_t txevent={.dma_buf=txbuf[d],.size=960};
  tx.callbacks.on_sent(&tx,&txevent,tx.context);
  for(unsigned i=0;i<240;i++){rxbuf[d][i*4]=0x34;rxbuf[d][i*4+1]=0x12;rxbuf[d][i*4+2]=(uint8_t)i;rxbuf[d][i*4+3]=0x56;}
  i2s_event_data_t rxevent={.dma_buf=rxbuf[d],.size=960};
  rx.callbacks.on_recv(&rx,&rxevent,rx.context);
  /* Driver queue is unused in this mode: its overflow is not capture loss. */
  rx.callbacks.on_recv_q_ovf(&rx,&rxevent,rx.context);
  assert(bsp_audio_reference_poll(&frame)==ESP_OK);
  assert(frame.epoch==1 && frame.sequence==d && frame.sample_offset==240*d);
  assert(frame.pcm[0]==0x34 && frame.pcm[1]==0x12 && frame.pcm[2]==0 && frame.pcm[3]==0x56);
 }
 assert(txbuf[0][0]==1 && txbuf[0][1]==0 && txbuf[0][2]==1 && txbuf[0][3]==0);
 assert(bsp_audio_reference_poll(&frame)==ESP_ERR_NOT_FOUND);
 /* Paired AEC uses RX reference, so unused TX diagnostic spans cannot fill. */
 for(unsigned d=6;d<46;d++){
  i2s_event_data_t te={.dma_buf=txbuf[d%6],.size=960};
  memset(te.dma_buf,0,960);tx.callbacks.on_sent(&tx,&te,tx.context);
  i2s_event_data_t re={.dma_buf=rxbuf[d%6],.size=960};
  rx.callbacks.on_recv(&rx,&re,rx.context);
  assert(bsp_audio_reference_poll(&frame)==ESP_OK && frame.sequence==d);
 }
 bsp_audio_render_span_t unused_span;
 assert(bsp_audio_render_poll(&unused_span)==ESP_ERR_NOT_FOUND);
 assert(bsp_audio_read_bounded(mono,sizeof mono,(size_t[]){0},1)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_reference_stop()==ESP_OK);
 assert(last_no_dac_ref && registers[0x44]==0x08 && last_volume==37);
 assert(tx_rate==16000 && rx_rate==16000 && tx_channels==1 && rx_channels==1);
 assert(bsp_audio_write(mono,sizeof mono)==ESP_OK);
 /* Mode failure rolls back rather than leaving reference enabled for legacy. */
 corrupt_reference_readback=true;
 assert(bsp_audio_reference_start(2)!=ESP_OK);
 assert(last_no_dac_ref && tx_rate==16000 && rx_rate==16000);
 assert(bsp_audio_write(mono,sizeof mono)==ESP_OK);
 corrupt_reference_readback=false;
 assert(bsp_audio_reference_start(3)==ESP_OK);
 fail_rx_format=true;
 assert(bsp_audio_reference_stop()!=ESP_OK);
 assert(!s_opened && s_reference_restore_pending);
 fail_rx_format=false;
 assert(bsp_audio_reference_stop()==ESP_OK);
 assert(last_no_dac_ref && tx_rate==16000 && rx_rate==16000 && !s_reference_restore_pending);
 return 0;
}
'''
class ReferenceBspTests(unittest.TestCase):
 def test_stereo_reference_is_paired_isolated_and_restores_legacy(self):
  root=Path(__file__).resolve().parents[1]
  sdk=Path(os.environ.get('IDF_PATH',root.parent.parent/'esp-idf-5.5.3'))
  cc=shutil.which('gcc')
  if not cc or not sdk.exists():self.skipTest('GCC/IDF unavailable')
  with tempfile.TemporaryDirectory() as d:
   d=Path(d)
   for name,content in dict(HEADERS,**{'fixture_codec.h':CODEC,'esp_codec_dev.h':'#include "fixture_codec.h"','esp_codec_dev_defaults.h':'#include "fixture_codec.h"','es8311_codec.h':'#include "fixture_codec.h"','esp_timer.h':'#include <stdint.h>\nint64_t esp_timer_get_time(void);'}).items():
    p=d/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
   code=harness().split('int main(int argc,char **argv)')[0]
   code=code.replace('static bool fail_rx_format;', 'static bool fail_rx_format;\nstatic bool last_no_dac_ref;\nstatic unsigned tx_channels,rx_channels,tx_mask,rx_mask;\nstatic bool corrupt_reference_readback;')
   code=code.replace('format->sample_rate == expected_rate && format->bits_per_sample == 16 && format->channel == 1', 'format->bits_per_sample == 16 && (format->channel==1 || format->channel==2)')
   code=code.replace('if(type==ESP_CODEC_DEV_TYPE_OUT)tx_rate=format->sample_rate;else rx_rate=format->sample_rate;', 'if(type==ESP_CODEC_DEV_TYPE_OUT){tx_rate=format->sample_rate;tx_channels=format->channel;tx_mask=format->channel_mask;}else{rx_rate=format->sample_rate;rx_channels=format->channel;rx_mask=format->channel_mask;}')
   code=code.replace('assert(config->ctrl_if && config->no_dac_ref);', 'assert(config->ctrl_if);last_no_dac_ref=config->no_dac_ref;registers[0x44]=config->no_dac_ref?0x08:(corrupt_reference_readback?0:0x58);')
   code=code.replace('return enabled ? i2s_channel_enable(channel) : i2s_channel_disable(channel);', 'return (enabled ? i2s_channel_enable(channel) : i2s_channel_disable(channel)) == ESP_OK ? ESP_CODEC_DEV_OK : ESP_CODEC_DEV_DRV_ERR;')
   code=code.replace('if (tx.enabled || rx.enabled) (void)device_data->enable', '(void)device_data->enable')

   code=code.replace('assert(channel == &tx && channel->allocated && !channel->enabled);', 'assert(channel->allocated && !channel->enabled);')
   code=code.replace('assert(callbacks->on_sent && !callbacks->on_recv && !callbacks->on_recv_q_ovf && !callbacks->on_send_q_ovf);', 'assert(channel==&tx ? callbacks->on_sent!=NULL : callbacks->on_recv_q_ovf!=NULL && callbacks->on_recv!=NULL);')
   code=code.replace('config->auto_clear_after_cb && !config->auto_clear_before_cb','!config->auto_clear_after_cb && config->auto_clear_before_cb')
   (d/'bsp_audio.c').write_text((root/'firmware/components/bsp/src/bsp_audio.c').read_text())
   (d/'main.c').write_text(code+MAIN)
   includes=[d,root/'firmware/components/bsp/include',root/'firmware/components/bsp/src',sdk/'components/esp_driver_i2s/include',sdk/'components/hal/include',sdk/'components/esp_common/include']
   cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-DFIXTURE_TICK_MS=1','-DCONFIG_BSP_AUDIO_CALLBACK_RENDER=1']+['-I'+str(p) for p in includes]+[str(d/'main.c'),str(root/'firmware/components/bsp/src/bsp_audio_render.c'),str(root/'firmware/components/bsp/src/bsp_audio_capture.c'),str(root/'firmware/components/bsp/src/bsp_es8311_sleep_check.c'),'-o',str(d/'test')]
   r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(d/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
