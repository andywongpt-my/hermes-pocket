"""Silent diagnostic lease: actual BSP, fake I/O; not physical silence evidence."""
import unittest
from unittest.mock import patch
if __package__:
 from . import test_firmware_reference_bsp as ref
else:
 import test_firmware_reference_bsp as ref

PREFIX=ref.MAIN.split('int main(void)')[0]
MAIN=r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=true;
 assert(bsp_audio_diagnostic_begin(0,&owned)==ESP_ERR_INVALID_ARG && !owned);
 assert(bsp_audio_diagnostic_begin(1,NULL)==ESP_ERR_INVALID_ARG);
 assert(bsp_audio_diagnostic_end()==ESP_ERR_INVALID_STATE);
 /* Fresh boot: lease precedes initialization and blocks internal unmute. */
 assert(bsp_audio_diagnostic_begin(1,&owned)==ESP_OK && owned);
 assert(s_diagnostic_owned && s_checked_mute_latched && s_volume==0);
 assert(registers[0x31]==0x60 && registers[0x32]==6 && !last_no_dac_ref);
 assert(bsp_audio_set_volume_checked(5)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_set_mute_checked(false)==ESP_ERR_INVALID_STATE);
 bsp_audio_set_volume(95);assert(s_volume==0);
 int16_t pcm[240]={0};
 assert(bsp_audio_render_submit(1,0,pcm,240)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_read(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_write(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 bool nested=true;assert(bsp_audio_diagnostic_begin(2,&nested)==ESP_ERR_INVALID_STATE && !nested);
 memset(s_capture.queue,0x55,sizeof(s_capture.queue));
 assert(bsp_audio_diagnostic_end()==ESP_OK);
 for(size_t i=0;i<sizeof(s_capture.queue);i++)assert(((uint8_t*)s_capture.queue)[i]==0);
 assert(!s_diagnostic_owned && !s_checked_mute_latched && s_sleeping && !tx.enabled && !rx.enabled);
 assert(s_hz==16000 && s_bits==16 && s_ch==1 && !s_dev);
 /* Existing sleeping normal owner: preserve cached volume; no end-time unmute. */
 bsp_audio_set_volume(37);assert(s_volume==37);
 unsigned old_unmutes=unmute_writes;
 assert(bsp_audio_diagnostic_begin(3,&owned)==ESP_OK && owned);
 assert(unmute_writes==old_unmutes);
 assert(bsp_audio_diagnostic_end()==ESP_OK && s_volume==37 && !s_checked_mute_latched);
 assert(unmute_writes==old_unmutes && s_sleeping);
 assert(bsp_audio_wake()==ESP_OK && last_volume==37 && !s_checked_mute_latched);
 assert(bsp_audio_write(pcm,sizeof pcm)==ESP_OK); /* normal sound path not latched forever */
 /* Preserve an idle legacy TTS format, not only the default voice format. */
 assert(bsp_audio_set_format(8000,16,1)==ESP_OK);
 assert(bsp_audio_sleep()==ESP_OK);
 assert(bsp_audio_diagnostic_begin(30,&owned)==ESP_OK && owned);
 assert(bsp_audio_diagnostic_end()==ESP_OK && s_sleeping && s_hz==8000);
 assert(bsp_audio_wake()==ESP_OK && tx_rate==8000 && rx_rate==8000 && last_volume==37);
 /* Preserve a pre-existing checked mute intent too. */
 assert(bsp_audio_set_mute_checked(true)==ESP_OK);
 assert(bsp_audio_diagnostic_begin(4,&owned)==ESP_OK && owned);
 assert(bsp_audio_diagnostic_end()==ESP_OK && s_checked_mute_latched && s_volume==37);
 /* Begin failure after acquisition always requires cleanup. */
 assert(bsp_audio_wake()==ESP_OK);
 fail_volume_read=true;
 assert(bsp_audio_diagnostic_begin(5,&owned)!=ESP_OK && owned && s_diagnostic_owned);
 fail_volume_read=false;
 assert(bsp_audio_diagnostic_end()==ESP_OK && !s_diagnostic_owned);
 /* Failed restore must NOT release ownership, even if sleep later succeeds. */
 assert(bsp_audio_diagnostic_begin(6,&owned)==ESP_OK);
 fail_rx_format=true;
 assert(bsp_audio_diagnostic_end()!=ESP_OK && s_diagnostic_owned && s_checked_mute_latched && s_volume==0);
 fail_rx_format=false;
 assert(bsp_audio_diagnostic_end()!=ESP_OK && s_diagnostic_owned);
 assert(bsp_audio_diagnostic_begin(7,&owned)==ESP_ERR_INVALID_STATE && !owned);
 bsp_audio_set_volume(90);assert(s_volume==0);
 assert(bsp_audio_set_mute_checked(false)==ESP_ERR_INVALID_STATE);
 return 0;
}
'''

def diagnostic_harness():
 code=ref.harness_original()
 code=code.replace('static bool fail_rx_format;', 'static bool fail_rx_format;\nstatic bool fail_volume_read;static unsigned unmute_writes;')
 code=code.replace('*(uint8_t *)data = registers[reg]; return ESP_CODEC_DEV_OK;', 'if(reg==0x32 && fail_volume_read){return ESP_FAIL;} *(uint8_t *)data = registers[reg]; return ESP_CODEC_DEV_OK;')
 code=code.replace('registers[reg] = *(uint8_t *)data; return ESP_CODEC_DEV_OK;', 'if(reg==0x31 && !(*(uint8_t *)data & 0x60)){unmute_writes++;} registers[reg] = *(uint8_t *)data; return ESP_CODEC_DEV_OK;')
 code=code.replace('int esp_codec_dev_set_out_vol(esp_codec_dev_handle_t device, int volume) { assert(device); last_volume = (unsigned)volume; volume_sets++; return ESP_CODEC_DEV_OK; }', '''int esp_codec_dev_set_out_vol(esp_codec_dev_handle_t device,int volume){
 assert(device);last_volume=(unsigned)volume;volume_sets++;uint8_t reg=volume?98+volume:6;
 (void)s_checked_ctrl.write_reg(&s_checked_ctrl,0x32,1,&reg,1);return ESP_CODEC_DEV_OK;
 }''')
 code=code.replace('codec_opens++;', 'codec_opens++; uint8_t unmute=0; (void)s_checked_ctrl.write_reg(&s_checked_ctrl,0x31,1,&unmute,1);')
 return code

class DiagnosticBspTests(unittest.TestCase):
 def test_silent_lease_and_verified_normal_restore(self):
  with patch.object(ref,'harness_original',ref.harness,create=True), patch.object(ref,'harness',diagnostic_harness),patch.object(ref,'MAIN',PREFIX+MAIN):
   ref.ReferenceBspTests('test_stereo_reference_is_paired_isolated_and_restores_legacy').test_stereo_reference_is_paired_isolated_and_restores_legacy()

 def test_failed_init_retains_ownership_without_false_sleep_proof(self):
  main=r"""
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;
 assert(bsp_audio_diagnostic_begin(1,&owned)!=ESP_OK && owned);
 assert(!s_initialized && s_diagnostic_owned && s_checked_mute_latched);
 assert(bsp_audio_diagnostic_end()!=ESP_OK && s_diagnostic_owned);
 assert(bsp_audio_diagnostic_end()!=ESP_OK && s_diagnostic_owned);
 return 0;
}
"""
  original=ref.harness
  def fail_init_harness():
   return original().replace('esp_err_t bsp_i2c_init(void) { return ESP_OK; }','esp_err_t bsp_i2c_init(void) { return ESP_FAIL; }')
  with patch.object(ref,'harness_original',fail_init_harness,create=True),patch.object(ref,'harness',diagnostic_harness),patch.object(ref,'MAIN',PREFIX+main):
   ref.ReferenceBspTests('test_stereo_reference_is_paired_isolated_and_restores_legacy').test_stereo_reference_is_paired_isolated_and_restores_legacy()

 def test_failed_end_mute_is_sticky_even_after_verified_sleep(self):
  main=r"""
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;
 assert(bsp_audio_diagnostic_begin(1,&owned)==ESP_OK && owned);
 fail_volume_read=true;
 assert(bsp_audio_diagnostic_end()!=ESP_OK && s_diagnostic_owned);
 assert(s_sleeping && !tx.enabled && !rx.enabled);
 fail_volume_read=false;
 assert(bsp_audio_diagnostic_end()!=ESP_OK && s_diagnostic_owned);
 assert(s_volume==0 && s_checked_mute_latched);
 return 0;
}
"""
  with patch.object(ref,'harness_original',ref.harness,create=True),patch.object(ref,'harness',diagnostic_harness),patch.object(ref,'MAIN',PREFIX+main):
   ref.ReferenceBspTests('test_stereo_reference_is_paired_isolated_and_restores_legacy').test_stereo_reference_is_paired_isolated_and_restores_legacy()
