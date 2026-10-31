"""Inactive Live lifecycle against the real BSP and fake peripheral boundaries."""
import unittest
from unittest.mock import patch
if __package__:
 from . import test_firmware_reference_bsp as ref
 from . import test_firmware_diagnostic_bsp as diag
else:
 import test_firmware_reference_bsp as ref
 import test_firmware_diagnostic_bsp as diag

MAIN=r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=true;
 assert(bsp_audio_live_begin(0,20,&owned)==ESP_ERR_INVALID_ARG && !owned);
 assert(bsp_audio_live_begin(1,101,&owned)==ESP_ERR_INVALID_ARG && !owned);
 assert(bsp_audio_live_begin(1,256,&owned)==ESP_ERR_INVALID_ARG && !owned);
 assert(bsp_audio_live_end(1)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_live_begin(1,20,&owned)==ESP_OK && owned);
 assert(s_live_owned && s_checked_mute_latched && s_volume==0 && registers[0x32]==6);
 assert(unmute_writes==0);
 unsigned before=unmute_writes;
 int16_t pcm[240]={1};
 assert(bsp_audio_render_submit(1,0,pcm,240)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_live_enable_output(2)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_live_end(2)==ESP_ERR_INVALID_STATE && s_live_owned);
 assert(bsp_audio_diagnostic_end()==ESP_ERR_INVALID_STATE);
 bool other=true;
 assert(bsp_audio_diagnostic_begin(2,&other)==ESP_ERR_INVALID_STATE && !other);
 assert(bsp_audio_live_begin(2,20,&other)==ESP_ERR_INVALID_STATE && !other);
 assert(bsp_audio_reference_stop()==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_render_stop_output()==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_set_format(8000,16,1)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_sleep()==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_wake()==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_set_volume_checked(70)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_set_mute_checked(false)==ESP_ERR_INVALID_STATE);
 bsp_audio_set_volume(90);assert(s_volume==0);
 assert(bsp_audio_read(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_write(pcm,sizeof pcm)==ESP_ERR_INVALID_STATE);
 assert(unmute_writes==before);
 assert(bsp_audio_live_enable_output(1)==ESP_OK && s_volume==20 && !s_checked_mute_latched);
 assert(registers[0x32]==118);
 assert(bsp_audio_render_submit(1,0,pcm,240)==ESP_OK);
 assert(bsp_audio_live_enable_output(1)==ESP_ERR_INVALID_STATE);
 before=unmute_writes;
 assert(bsp_audio_live_end(1)==ESP_OK && !s_live_owned && s_sleeping && s_volume==0);
 assert(!s_checked_mute_latched && unmute_writes==before);
 assert(bsp_audio_live_end(1)==ESP_ERR_INVALID_STATE);
 /* Prior sleeping 8k format and normal volume survive without end-time unmute. */
 assert(bsp_audio_wake()==ESP_OK);
 assert(bsp_audio_set_format(8000,16,1)==ESP_OK);bsp_audio_set_volume(37);
 assert(bsp_audio_sleep()==ESP_OK);
 assert(bsp_audio_live_begin(3,0,&owned)==ESP_OK && owned);
 assert(bsp_audio_live_enable_output(3)==ESP_OK && s_checked_mute_latched && s_volume==0);
 assert(bsp_audio_live_end(3)==ESP_OK && s_hz==8000 && s_volume==37);
 assert(bsp_audio_wake()==ESP_OK && tx_rate==8000 && last_volume==37);
 /* Awake start and an existing checked mute intent. */
 assert(bsp_audio_set_mute_checked(true)==ESP_OK);
 assert(bsp_audio_live_begin(4,5,&owned)==ESP_OK);
 assert(bsp_audio_live_end(4)==ESP_OK && s_checked_mute_latched && s_volume==37);
 assert(bsp_audio_diagnostic_begin(5,&owned)==ESP_OK);
 assert(bsp_audio_live_begin(6,5,&other)==ESP_ERR_INVALID_STATE && !other);
 assert(bsp_audio_live_end(5)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_live_enable_output(5)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_render_submit(5,0,pcm,240)==ESP_ERR_INVALID_STATE);
 assert(bsp_audio_diagnostic_end()==ESP_OK);
 /* Failed begin still owns cleanup; successful cleanup may restore normal state. */
 fail_volume_read=true;
 assert(bsp_audio_live_begin(6,5,&owned)!=ESP_OK && owned);
 assert(bsp_audio_live_enable_output(6)==ESP_ERR_INVALID_STATE);
 fail_volume_read=false;
 assert(bsp_audio_live_end(6)==ESP_OK);
 /* End failure is sticky even if subsequent best-effort stop succeeds. */
 assert(bsp_audio_live_begin(7,5,&owned)==ESP_OK);
 fail_rx_format=true;
 assert(bsp_audio_live_end(7)!=ESP_OK && s_live_owned && s_volume==0);
 fail_rx_format=false;
 assert(bsp_audio_live_end(7)!=ESP_OK && s_live_owned && s_checked_mute_latched);
 assert(bsp_audio_live_enable_output(7)==ESP_ERR_INVALID_STATE);
 return 0;
}
'''

class LiveLeaseBspTests(unittest.TestCase):
 def run_main(self, main, fail_init=False):
  original=ref.harness
  def base():
   code=original()
   if fail_init:code=code.replace('esp_err_t bsp_i2c_init(void) { return ESP_OK; }','esp_err_t bsp_i2c_init(void) { return ESP_FAIL; }')
   return code
  with patch.object(ref,'harness_original',base,create=True),patch.object(ref,'harness',diag.diagnostic_harness),patch.object(ref,'MAIN',diag.PREFIX+main):
   ref.ReferenceBspTests('test_stereo_reference_is_paired_isolated_and_restores_legacy').test_stereo_reference_is_paired_isolated_and_restores_legacy()
 def test_separate_enable_exclusive_lease_and_restoration(self):self.run_main(MAIN)
 def test_failed_init_cannot_claim_cleanup(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;
 assert(bsp_audio_live_begin(1,5,&owned)!=ESP_OK && owned);
 assert(bsp_audio_live_end(1)!=ESP_OK && s_live_owned);
 assert(bsp_audio_live_end(1)!=ESP_OK && s_live_owned);
 return 0;
}
''',True)
 def test_failed_enable_stays_muted_and_cannot_submit(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;int16_t pcm[240]={1};
 assert(bsp_audio_live_begin(1,5,&owned)==ESP_OK && owned);
 fail_volume_read=true;
 assert(bsp_audio_live_enable_output(1)!=ESP_OK && s_checked_mute_latched && s_volume==0);
 fail_volume_read=false;
 assert(bsp_audio_live_enable_output(1)!=ESP_OK);
 assert(bsp_audio_render_submit(1,0,pcm,240)!=ESP_OK);
 assert(bsp_audio_live_end(1)==ESP_OK);
 return 0;
}
''')

 def test_reference_failure_before_enable_never_unmutes(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;
 assert(bsp_audio_live_begin(1,5,&owned)==ESP_OK);
 s_capture.failed=true;
 unsigned before=unmute_writes;
 assert(bsp_audio_live_enable_output(1)!=ESP_OK && unmute_writes==before);
 assert(s_checked_mute_latched && s_volume==0);
 assert(bsp_audio_live_end(1)==ESP_OK);
 return 0;
}
''')
 def test_reference_failure_after_enable_blocks_more_pcm(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;int16_t pcm[240]={1};
 assert(bsp_audio_live_begin(1,5,&owned)==ESP_OK);
 assert(bsp_audio_live_enable_output(1)==ESP_OK);
 s_capture.failed=true;
 assert(bsp_audio_render_submit(1,0,pcm,240)!=ESP_OK);
 assert(bsp_audio_live_end(1)==ESP_OK);
 return 0;
}
''')
 def test_awake_mute_failure_precedes_any_reconfiguration(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 assert(bsp_audio_init()==ESP_OK);
 assert(bsp_audio_set_format(8000,16,1)==ESP_OK);
 bsp_audio_set_volume(37);
 unsigned opened=codec_opens;bool owned=false;
 fail_volume_read=true;
 assert(bsp_audio_live_begin(1,5,&owned)!=ESP_OK && owned);
 assert(codec_opens==opened && tx_rate==8000 && rx_rate==8000);
 fail_volume_read=false;
 assert(bsp_audio_live_end(1)==ESP_OK && s_hz==8000 && s_volume==37);
 return 0;
}
''')
 def test_end_mute_failure_keeps_quarantine_after_sleep(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 bool owned=false;
 assert(bsp_audio_live_begin(1,5,&owned)==ESP_OK);
 assert(bsp_audio_live_enable_output(1)==ESP_OK);
 fail_volume_read=true;
 assert(bsp_audio_live_end(1)!=ESP_OK && s_live_owned && s_sleeping);
 fail_volume_read=false;
 assert(bsp_audio_live_end(1)!=ESP_OK && s_live_owned && s_checked_mute_latched);
 assert(bsp_audio_wake()==ESP_ERR_INVALID_STATE);
 return 0;
}
''')

 def test_saved_mute_is_not_overridden_by_positive_requested_volume(self):self.run_main(r'''
int main(void){
 (void)drain_cancelled;(void)expected_rate;
 assert(bsp_audio_init()==ESP_OK);
 assert(bsp_audio_set_format(16000,16,1)==ESP_OK);
 assert(bsp_audio_set_volume_checked(60)==ESP_OK);
 assert(bsp_audio_set_mute_checked(true)==ESP_OK && s_volume==60);
 unsigned before=unmute_writes;bool owned=false;
 assert(bsp_audio_live_begin(1,60,&owned)==ESP_OK && owned);
 assert(bsp_audio_live_enable_output(1)==ESP_OK);
 assert(s_volume==0 && s_checked_mute_latched && registers[0x32]==6);
 assert((registers[0x31]&0x60)==0x60 && unmute_writes==before);
 assert(bsp_audio_live_end(1)==ESP_OK && s_sleeping);
 assert(s_volume==60 && s_checked_mute_latched && unmute_writes==before);
 return 0;
}
''')
