"""Actual BSP checked codec writes/readback; fake peripherals, not SPL proof."""
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
int main(void){
 (void)drain_cancelled;expected_rate=16000;
 assert(bsp_audio_set_volume_checked(0)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_prepare_silent_start()==ESP_OK && volume_writes==0);
 assert(bsp_audio_init()==ESP_OK);
 assert((registers[0x31]&0x60)==0x60 && s_volume==0);
 assert(bsp_audio_prepare_silent_start()==ESP_ERR_INVALID_STATE);
 /* Accepted before I2S/codec format open: control registers already exist. */
 assert(bsp_audio_set_volume_checked(0)==ESP_OK);
 assert((registers[0x31]&0x60)==0x60 && registers[0x32]==6);
 assert(bsp_audio_set_format(16000,16,1)==ESP_OK);
 assert((registers[0x31]&0x60)==0x60); /* startup cannot undo mute latch */
 assert(bsp_audio_set_volume_checked(5)==ESP_OK);
 assert(registers[0x32]==103 && (registers[0x31]&0x60)==0 && s_volume==5);
 assert(bsp_audio_set_volume_checked(101)==ESP_ERR_INVALID_ARG && s_volume==5);
 fail_volume_write=true;
 assert(bsp_audio_set_volume_checked(4)!=ESP_OK);
 assert((registers[0x31]&0x60)==0x60 && s_volume==0);
 fail_volume_write=false;
 assert(bsp_audio_set_volume_checked(0)==ESP_OK);
 corrupt_volume_read=true;
 assert(bsp_audio_set_volume_checked(5)!=ESP_OK);
 assert((registers[0x31]&0x60)==0x60);
 corrupt_volume_read=false;fail_volume_read=true;
 assert(bsp_audio_set_volume_checked(5)!=ESP_OK);
 fail_volume_read=false;skip_volume_write=true;
 assert(bsp_audio_set_volume_checked(5)!=ESP_OK); /* cached percent isn't proof */
 skip_volume_write=false;wrong_curve=true;volume_writes=0;
 assert(bsp_audio_set_volume_checked(5)!=ESP_OK && volume_writes==0);
 wrong_curve=false;fail_unmute_write=true;
 assert(bsp_audio_set_volume_checked(5)!=ESP_OK && (registers[0x31]&0x60)==0x60);
 fail_unmute_write=false;
 assert(bsp_audio_set_volume_checked(0)==ESP_OK);
 assert(bsp_audio_sleep()==ESP_OK);
 assert(bsp_audio_set_volume_checked(5)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_wake()==ESP_OK && (registers[0x31]&0x60)==0x60);
 assert(bsp_audio_set_volume_checked(5)==ESP_OK);
 fail_mute_read=true;
 assert(bsp_audio_set_mute_checked(true)!=ESP_OK && s_volume==0);
 fail_mute_read=false;assert(bsp_audio_set_volume_checked(5)==ESP_OK);
 assert(bsp_audio_set_mute_checked(true)==ESP_OK && (registers[0x31]&0x60)==0x60);
 assert(bsp_audio_set_mute_checked(false)==ESP_OK && (registers[0x31]&0x60)==0);
 /* Legacy setter retains clamp/void behavior; no checked success is implied. */
 bsp_audio_set_volume(255);assert(s_volume==100 && last_volume==100);
 return 0;
}
'''

class CheckedVolumeTests(unittest.TestCase):
 def test_real_register_readback_and_fail_closed_mute(self):
  root=Path(__file__).resolve().parents[1]
  sdk=Path(os.environ.get('IDF_PATH',root.parent.parent/'esp-idf-5.5.3'))
  cc=shutil.which('gcc')
  if not cc or not sdk.exists():self.skipTest('GCC/IDF unavailable')
  with tempfile.TemporaryDirectory() as d:
   d=Path(d)
   for name,content in dict(HEADERS,**{'fixture_codec.h':CODEC,'esp_codec_dev.h':'#include "fixture_codec.h"','esp_codec_dev_defaults.h':'#include "fixture_codec.h"','es8311_codec.h':'#include "fixture_codec.h"'}).items():
    p=d/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
   code=harness().split('int main(int argc,char **argv)')[0]
   code=code.replace('static bool fail_rx_format;', 'static bool fail_rx_format;\nstatic bool fail_volume_write,corrupt_volume_read,fail_volume_read,skip_volume_write,wrong_curve,fail_unmute_write,fail_mute_read;\nstatic unsigned volume_writes;')
   code=code.replace('*(uint8_t *)data = registers[reg]; return ESP_CODEC_DEV_OK;', 'if((reg==0x32 && fail_volume_read)||(reg==0x31 && fail_mute_read)){return ESP_FAIL;} *(uint8_t *)data = registers[reg] ^ ((reg==0x32 && corrupt_volume_read)?1:0); return ESP_CODEC_DEV_OK;')
   code=code.replace('registers[reg] = *(uint8_t *)data; return ESP_CODEC_DEV_OK;', 'if(reg==0x32){volume_writes++;if(fail_volume_write)return ESP_FAIL;} if(reg==0x31 && fail_unmute_write && !(*(uint8_t *)data & 0x60))return ESP_FAIL; registers[reg] = *(uint8_t *)data; return ESP_CODEC_DEV_OK;')
   code=code.replace('int esp_codec_dev_set_out_vol(esp_codec_dev_handle_t device, int volume) { assert(device); last_volume = (unsigned)volume; volume_sets++; return ESP_CODEC_DEV_OK; }', '''int esp_codec_dev_set_out_vol(esp_codec_dev_handle_t device,int volume){
    assert(device);last_volume=(unsigned)volume;volume_sets++;
    uint8_t reg=wrong_curve?198:(volume?98+volume:6);
    if(!skip_volume_write)(void)s_checked_ctrl.write_reg(&s_checked_ctrl,0x32,1,&reg,1);
    return ESP_CODEC_DEV_OK; /* exact facade also discards codec result */
   }''')
   code=code.replace('assert(config->ctrl_if && config->no_dac_ref); return &codec;', 'assert(config->ctrl_if && config->no_dac_ref); uint8_t unmute=0; (void)s_checked_ctrl.write_reg(&s_checked_ctrl,0x31,1,&unmute,1); return &codec;')
   code=code.replace('codec_opens++;', 'codec_opens++; uint8_t unmute=0; (void)s_checked_ctrl.write_reg(&s_checked_ctrl,0x31,1,&unmute,1);')
   (d/'bsp_audio.c').write_text((root/'firmware/components/bsp/src/bsp_audio.c').read_text())
   (d/'main.c').write_text(code+MAIN)
   includes=[d,root/'firmware/components/bsp/include',root/'firmware/components/bsp/src',sdk/'components/esp_driver_i2s/include',sdk/'components/hal/include',sdk/'components/esp_common/include']
   cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-DFIXTURE_TICK_MS=1']+['-I'+str(p) for p in includes]+[str(d/'main.c'),str(root/'firmware/components/bsp/src/bsp_es8311_sleep_check.c'),'-o',str(d/'test')]
   r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(d/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)

 def test_mapping_matches_exact_pinned_public_volume_math(self):
  root=Path(__file__).resolve().parents[1]
  codec=root.parent.parent/'pocket-build-components/esp_codec_dev-1.6.2/esp_codec_dev'
  cc=shutil.which('gcc')
  if not cc or not (codec/'esp_codec_dev_vol.c').is_file():self.skipTest('exact codec1.6.2 source unavailable')
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);(d/'esp_err.h').write_text(HEADERS['esp_err.h'])
   (d/'t.c').write_text(r'''
#include "esp_codec_dev_vol.h"
#include <assert.h>
int main(void){
 esp_codec_dev_hw_gain_t hw={.pa_voltage=5.0f,.codec_dac_voltage=3.3f,.pa_gain=0.0f};
 esp_codec_dev_vol_range_t range={.min_vol={0,-95.5f},.max_vol={255,32.0f}};
 float gain=esp_codec_dev_col_calc_hw_gain(&hw);
 for(int p=0;p<=100;p++){
  float db=p?(-50.0f+(float)p*0.5f):-96.0f;
  assert(esp_codec_dev_vol_calc_reg(&range,db-gain)==(p?98+p:6));
 }
 return 0;
}
''')
   cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+str(d),'-I'+str(codec/'include'),str(d/'t.c'),str(codec/'esp_codec_dev_vol.c'),'-lm','-o',str(d/'t')]
   r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(d/'t')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
