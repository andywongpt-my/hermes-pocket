"""Characterize real BSP 24 kHz configuration against fake codec/I2S APIs.

This does not validate the absent codec-dev 1.6.2 implementation or real clocks.
No production BSP change is required: the existing runtime format API supports it.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
if __package__:
    from .test_firmware_audio_drain import HEADERS, HARNESS
else:
    from test_firmware_audio_drain import HEADERS, HARNESS


CODEC = r'''
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
typedef void *esp_codec_dev_handle_t;
typedef enum { ESP_CODEC_DEV_TYPE_IN=1, ESP_CODEC_DEV_TYPE_OUT=2, ESP_CODEC_DEV_TYPE_IN_OUT=3 } esp_codec_dev_type_t;
enum { ESP_CODEC_DEV_OK=0, ESP_CODEC_DEV_DRV_ERR=-10, ESP_CODEC_DEV_NOT_SUPPORT=-5, ESP_CODEC_DEV_WORK_MODE_BOTH=3 };
#define ESP_CODEC_DEV_MAKE_CHANNEL_MASK(x) (1u<<(x))
typedef struct {uint32_t sample_rate; unsigned bits_per_sample,channel,channel_mask,mclk_multiple;} esp_codec_dev_sample_info_t;
typedef struct {int unused;} audio_codec_ctrl_info_t;
typedef struct audio_codec_ctrl_if_t audio_codec_ctrl_if_t;
struct audio_codec_ctrl_if_t {
 bool (*is_open)(const audio_codec_ctrl_if_t *);
 int (*read_reg)(const audio_codec_ctrl_if_t *,int,int,void *,int);
 int (*write_reg)(const audio_codec_ctrl_if_t *,int,int,void *,int);
 int (*get_info)(const audio_codec_ctrl_if_t *,audio_codec_ctrl_info_t *);
};
typedef struct audio_codec_data_if_t audio_codec_data_if_t;
struct audio_codec_data_if_t {
 bool (*is_open)(const audio_codec_data_if_t *);
 int (*enable)(const audio_codec_data_if_t *,esp_codec_dev_type_t,bool);
 int (*set_fmt)(const audio_codec_data_if_t *,esp_codec_dev_type_t,esp_codec_dev_sample_info_t *);
 int (*read)(const audio_codec_data_if_t *,uint8_t *,int);
 int (*write)(const audio_codec_data_if_t *,uint8_t *,int);
};
typedef struct {int unused;} audio_codec_if_t;
typedef struct {int unused;} audio_codec_gpio_if_t;
typedef struct {int port,addr;i2c_master_bus_handle_t bus_handle;} audio_codec_i2c_cfg_t;
typedef struct {int port;i2s_chan_handle_t tx_handle,rx_handle;} audio_codec_i2s_cfg_t;
typedef struct {const audio_codec_ctrl_if_t *ctrl_if;const audio_codec_gpio_if_t *gpio_if;int codec_mode,pa_pin;bool pa_reverted,master_mode,use_mclk,no_dac_ref;struct {float pa_voltage,codec_dac_voltage;} hw_gain;} es8311_codec_cfg_t;
typedef struct {esp_codec_dev_type_t dev_type;const audio_codec_if_t *codec_if;const audio_codec_data_if_t *data_if;} esp_codec_dev_cfg_t;
const audio_codec_ctrl_if_t *audio_codec_new_i2c_ctrl(audio_codec_i2c_cfg_t *);
const audio_codec_data_if_t *audio_codec_new_i2s_data(audio_codec_i2s_cfg_t *);
const audio_codec_gpio_if_t *audio_codec_new_gpio(void);
const audio_codec_if_t *es8311_codec_new(es8311_codec_cfg_t *);
esp_codec_dev_handle_t esp_codec_dev_new(esp_codec_dev_cfg_t *);
int esp_codec_dev_open(esp_codec_dev_handle_t,esp_codec_dev_sample_info_t *);
void esp_codec_dev_delete(esp_codec_dev_handle_t);
int esp_codec_dev_set_in_gain(esp_codec_dev_handle_t,float);
int esp_codec_dev_set_out_vol(esp_codec_dev_handle_t,int);
int esp_codec_dev_write(esp_codec_dev_handle_t,void *,int);
int esp_codec_dev_read(esp_codec_dev_handle_t,void *,int);
int audio_codec_delete_codec_if(const audio_codec_if_t *);
int audio_codec_delete_ctrl_if(const audio_codec_ctrl_if_t *);
int audio_codec_delete_data_if(const audio_codec_data_if_t *);
int audio_codec_delete_gpio_if(const audio_codec_gpio_if_t *);
'''

MAIN = r'''
int main(int argc,char **argv) {
 assert(argc==2);int test=atoi(argv[1]);(void)drain_cancelled;
 assert(bsp_audio_init()==ESP_OK);
 expected_rate=24000;
 assert(bsp_audio_set_format(24000,16,1)==ESP_OK);
 assert(tx_rate==24000 && rx_rate==24000 && tx.enabled && rx.enabled);
 unsigned opened=codec_opens,disabled=disables;
 if(test==1){
  assert(bsp_audio_set_format(24000,16,1)==ESP_OK);
  assert(codec_opens==opened && disables==disabled);
  uint8_t pcm[960]={0};assert(bsp_audio_read(pcm,sizeof pcm)==ESP_OK);
  assert(bsp_audio_write(pcm,sizeof pcm)==ESP_OK);
  assert(tx.enabled && rx.enabled && codec_opens==opened);
 }else if(test==2){
  bsp_audio_set_volume(37);assert(bsp_audio_sleep()==ESP_OK);
  assert(!tx.enabled && !rx.enabled);
  assert(bsp_audio_wake()==ESP_OK);
  assert(tx_rate==24000 && rx_rate==24000 && tx.enabled && rx.enabled && last_volume==37);
 }else if(test==3){
  expected_rate=16000;assert(bsp_audio_set_format(16000,16,1)==ESP_OK);
  assert(tx_rate==16000 && rx_rate==16000 && codec_opens==opened+1);
 }else if(test==4){
  assert(bsp_audio_sleep()==ESP_OK);fail_rx_format=true;
  assert(bsp_audio_wake()!=ESP_OK);
  assert(!tx.enabled && !rx.enabled && !s_opened);
 }else if(test==5){
  /* An idle completion is not proof any user PCM was rendered. */
  assert(writes==0);uint32_t before=audio_tx_completions();complete_descriptor();
  assert(audio_tx_completions()==before+1 && writes==0);
 }else assert(0);
 puts("PASS: actual BSP runtime-format boundary only; no physical codec validation");
 return 0;
}
'''


def harness():
    code = HARNESS.split('int main(int argc, char **argv) {', 1)[0]
    code = code.replace('static unsigned last_volume;',
        'static unsigned last_volume, expected_rate, tx_rate, rx_rate;\nstatic bool fail_rx_format;')
    code = code.replace('assert(format->sample_rate == 16000 && format->bits_per_sample == 16 && format->channel == 1);',
        'assert(format->sample_rate == expected_rate && format->bits_per_sample == 16 && format->channel == 1);\n'
        '    if(type==ESP_CODEC_DEV_TYPE_OUT)tx_rate=format->sample_rate;else rx_rate=format->sample_rate;\n'
        '    if(type==ESP_CODEC_DEV_TYPE_IN && fail_rx_format)return ESP_FAIL;')
    # Partial-open failure can leave one direction stopped. The real delete API
    # is void; do not let a fixture assert mask the BSP's subsequent forced stop.
    code = code.replace('assert(device_data->enable(device_data, ESP_CODEC_DEV_TYPE_IN_OUT, false) == ESP_CODEC_DEV_OK);',
        '(void)device_data->enable(device_data, ESP_CODEC_DEV_TYPE_IN_OUT, false);')
    return code + MAIN


class FirmwareLiveAudioTests(unittest.TestCase):
    def run_case(self, scenario, mutation=False):
        compiler = shutil.which('gcc')
        root = Path(__file__).resolve().parents[1]
        sdk = Path(os.environ.get('IDF_PATH', root.parent.parent / 'esp-idf-5.5.3'))
        if not compiler or not (sdk / 'components/esp_driver_i2s/include/driver/i2s_std.h').is_file():
            self.skipTest('GCC / existing ESP-IDF headers unavailable')
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            for name, contents in dict(HEADERS, **{
                'fixture_codec.h': CODEC,
                'esp_codec_dev.h': '#include "fixture_codec.h"\n',
                'esp_codec_dev_defaults.h': '#include "fixture_codec.h"\n',
                'es8311_codec.h': '#include "fixture_codec.h"\n',
            }).items():
                path = temp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            source = (root / 'firmware/components/bsp/src/bsp_audio.c').read_text()
            if mutation:
                source = source.replace('.sample_rate = hz,', '.sample_rate = 16000,')
            (temp / 'bsp_audio.c').write_text(source)
            (temp / 'audio.c').write_text(harness())
            binary = temp / 'audio'
            includes = [temp, root / 'firmware/components/bsp/include',
                root / 'firmware/components/bsp/src', sdk / 'components/esp_driver_i2s/include',
                sdk / 'components/hal/include', sdk / 'components/esp_common/include']
            command = [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-DFIXTURE_TICK_MS=1']
            command += ['-I' + str(path) for path in includes]
            command += [str(temp / 'audio.c'), str(root / 'firmware/components/bsp/src/bsp_es8311_sleep_check.c'), '-o', str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary), str(scenario)], capture_output=True, text=True, timeout=5)
            if mutation:
                self.assertNotEqual(result.returncode, 0, '24k fixture missed forced 16k regression')
                self.assertIn('format->sample_rate == expected_rate', result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_24k_configures_both_directions_and_same_format_reuses(self): self.run_case(1)
    def test_24k_survives_sleep_wake_without_volume_loss(self): self.run_case(2)
    def test_legacy_16k_restores_after_live_format(self): self.run_case(3)
    def test_rx_format_failure_stops_both_directions(self): self.run_case(4)
    def test_idle_dma_completion_is_not_rendered_pcm_evidence(self): self.run_case(5)
    def test_mutation_forcing_16k_is_caught(self): self.run_case(1, mutation=True)
