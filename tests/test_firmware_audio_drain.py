"""Run the real BSP against a local DMA/codec boundary, without hardware or SDK builds."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HEADERS = {
    'esp_err.h': r'''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL (-1)
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_NOT_SUPPORTED 0x106
#define ESP_ERR_TIMEOUT 0x107
const char *esp_err_to_name(esp_err_t error);
''',
    'esp_types.h': '#pragma once\n#include <stdint.h>\n#include <stdbool.h>\n#include <stddef.h>\n',
    'esp_attr.h': '#pragma once\n#define IRAM_ATTR __attribute__((section(".iram1")))\n#define DRAM_ATTR __attribute__((section(".dram1")))\n',
    'esp_log.h': r'''#pragma once
void fixture_log(const char *tag, const char *format, ...);
#define ESP_LOGE(...) fixture_log(__VA_ARGS__)
#define ESP_LOGI(...) fixture_log(__VA_ARGS__)
''',
    'sdkconfig.h': '#pragma once\n#define CONFIG_IDF_TARGET_ESP32S3 1\n#define CONFIG_I2S_ISR_IRAM_SAFE 1\n',
    'soc/soc_caps.h': '#pragma once\n#define SOC_I2S_NUM 2\n#define SOC_I2S_SUPPORTED 1\n#define SOC_I2S_HW_VERSION_2 1\n',
    'soc/clk_tree_defs.h': '#pragma once\ntypedef int soc_periph_i2s_clk_src_t;\n#define I2S_CLK_SRC_DEFAULT 0\n',
    'hal/gpio_types.h': '#pragma once\ntypedef int gpio_num_t;\n',
    'driver/spi_master.h': '#pragma once\n',
    'driver/i2c_types.h': '#pragma once\n#define I2C_NUM_0 0\n',
    'driver/i2c_master.h': '#pragma once\ntypedef void *i2c_master_bus_handle_t;\n',
    'hal/adc_types.h': '#pragma once\n',
    'driver/gpio.h': r'''#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef struct { uint64_t pin_bit_mask; int mode, pull_up_en, pull_down_en, intr_type; } gpio_config_t;
enum { GPIO_MODE_INPUT, GPIO_PULLUP_DISABLE, GPIO_PULLDOWN_DISABLE, GPIO_INTR_DISABLE };
esp_err_t gpio_config(const gpio_config_t *config);
''',
    'freertos/FreeRTOS.h': r'''#pragma once
#include <assert.h>
#include <stdint.h>
typedef uint32_t TickType_t;
typedef struct { unsigned held; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
#define pdMS_TO_TICKS(ms) ((TickType_t)((ms) / FIXTURE_TICK_MS))
#define portENTER_CRITICAL(mux) do { assert(!(mux)->held); (mux)->held = 1; } while (0)
#define portEXIT_CRITICAL(mux) do { assert((mux)->held); (mux)->held = 0; } while (0)
#define portENTER_CRITICAL_ISR(mux) portENTER_CRITICAL(mux)
#define portEXIT_CRITICAL_ISR(mux) portEXIT_CRITICAL(mux)
''',
    'freertos/task.h': r'''#pragma once
#include "freertos/FreeRTOS.h"
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
''',
}


HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bsp_audio.c"
#undef assert
#define assert(condition) do { if (!(condition)) { fprintf(stderr, "drain assertion failed at line %d: %s\n", __LINE__, #condition); exit(77); } } while (0)

struct i2s_channel_obj_t {
    bool allocated, ready, enabled;
    i2s_event_callbacks_t callbacks;
    void *context;
};
static struct i2s_channel_obj_t tx, rx;
static unsigned registrations, enables, disables, deletes, writes, volume_sets, codec_opens;
static esp_err_t registration_result;
static TickType_t now_ticks;
static unsigned elapsed_ms, next_completion_ms = 1, completion_count;
static unsigned completion_limit = 1000, stop_at_completion;
static unsigned pending_mask, next_descriptor;
static unsigned cancel_at_ms, cancel_checks;
static bool in_isr;
static unsigned last_volume;
static uint8_t registers[256];
static const audio_codec_data_if_t *device_data;
static int device_handle;

void fixture_log(const char *tag, const char *format, ...) { (void)tag; (void)format; }
const char *esp_err_to_name(esp_err_t error) { (void)error; return "fixture"; }
esp_err_t bsp_i2c_init(void) { return ESP_OK; }
i2c_master_bus_handle_t bsp_i2c_bus(void) { return &device_handle; }
esp_err_t gpio_config(const gpio_config_t *config) { assert(config); return ESP_OK; }

esp_err_t i2s_new_channel(const i2s_chan_config_t *config, i2s_chan_handle_t *out_tx, i2s_chan_handle_t *out_rx) {
    assert(config->dma_desc_num == 6 && config->dma_frame_num == 240);
    assert(config->auto_clear_after_cb && !config->auto_clear_before_cb);
    memset(&tx, 0, sizeof tx); memset(&rx, 0, sizeof rx);
    tx.allocated = rx.allocated = true; *out_tx = &tx; *out_rx = &rx; return ESP_OK;
}
esp_err_t i2s_channel_init_std_mode(i2s_chan_handle_t channel, const i2s_std_config_t *config) {
    assert(channel->allocated && !channel->enabled);
    assert(config->clk_cfg.sample_rate_hz == 16000);
    channel->ready = true; return ESP_OK;
}
esp_err_t i2s_channel_register_event_callback(i2s_chan_handle_t channel, const i2s_event_callbacks_t *callbacks, void *context) {
    assert(channel == &tx && channel->allocated && !channel->enabled);
    assert(callbacks->on_sent && !callbacks->on_recv && !callbacks->on_recv_q_ovf && !callbacks->on_send_q_ovf);
    registrations++;
    if (registration_result != ESP_OK) return registration_result;
    channel->callbacks = *callbacks; channel->context = context; return ESP_OK;
}
esp_err_t i2s_channel_enable(i2s_chan_handle_t channel) {
    assert(channel->allocated);
    if (!channel->ready || channel->enabled) return ESP_ERR_INVALID_STATE;
    /* The hook must exist before the first start, including codec format restarts. */
    if (channel == &tx) assert(channel->callbacks.on_sent);
    channel->enabled = true; enables++; return ESP_OK;
}
esp_err_t i2s_channel_disable(i2s_chan_handle_t channel) {
    assert(channel->allocated);
    if (!channel->enabled) return ESP_ERR_INVALID_STATE;
    channel->enabled = false; disables++; return ESP_OK;
}
esp_err_t i2s_del_channel(i2s_chan_handle_t channel) {
    assert(channel->allocated && !channel->enabled);
    memset(channel, 0, sizeof *channel); deletes++; return ESP_OK;
}
esp_err_t i2s_channel_write(i2s_chan_handle_t channel, const void *data, size_t size, size_t *written, uint32_t timeout_ms) {
    (void)timeout_ms; assert(channel == &tx && channel->enabled && data && size);
    /* All six buffers contain final-write data. Descriptor zero is in flight:
       its already-latched EOF can arrive first, before this write is played.
       The final outstanding buffer therefore completes only on event seven. */
    pending_mask = 0x3f; next_descriptor = 0; completion_count = 0;
    next_completion_ms = elapsed_ms + 1; writes++;
    if (written) *written = size;
    return ESP_OK;
}
static void complete_descriptor(void) {
    unsigned descriptor = next_descriptor;
    if (completion_count != 0) pending_mask &= ~(1u << descriptor);
    completion_count++; next_descriptor = (descriptor + 1) % 6;
    i2s_event_data_t event = { .dma_buf = &registers[descriptor], .size = 960 };
    in_isr = true;
    assert(tx.callbacks.on_sent && !tx.callbacks.on_sent(&tx, &event, tx.context));
    in_isr = false;
    if (stop_at_completion && completion_count == stop_at_completion) tx.enabled = false;
}
TickType_t xTaskGetTickCount(void) { return now_ticks; }
void vTaskDelay(TickType_t ticks) {
    assert(ticks > 0 && ticks <= pdMS_TO_TICKS(250));
    for (TickType_t i = 0; i < ticks; i++) {
        now_ticks++; elapsed_ms += FIXTURE_TICK_MS;
        while (tx.enabled && completion_count < completion_limit && elapsed_ms >= next_completion_ms) {
            complete_descriptor(); next_completion_ms += 15;
        }
    }
    assert(elapsed_ms <= 1000); /* A missing timeout fails deterministically. */
}

static bool ctrl_open(const audio_codec_ctrl_if_t *ctrl) { (void)ctrl; return true; }
static int ctrl_read(const audio_codec_ctrl_if_t *ctrl, int reg, int reg_len, void *data, int size) {
    (void)ctrl; assert(reg >= 0 && reg < 256 && reg_len == 1 && size == 1);
    *(uint8_t *)data = registers[reg]; return ESP_CODEC_DEV_OK;
}
static int ctrl_write(const audio_codec_ctrl_if_t *ctrl, int reg, int reg_len, void *data, int size) {
    (void)ctrl; assert(reg >= 0 && reg < 256 && reg_len == 1 && size == 1);
    registers[reg] = *(uint8_t *)data; return ESP_CODEC_DEV_OK;
}
static int ctrl_info(const audio_codec_ctrl_if_t *ctrl, audio_codec_ctrl_info_t *info) {
    (void)ctrl; (void)info; return ESP_CODEC_DEV_OK;
}
static const audio_codec_ctrl_if_t ctrl = { .is_open = ctrl_open, .read_reg = ctrl_read, .write_reg = ctrl_write, .get_info = ctrl_info };
static bool data_open(const audio_codec_data_if_t *data) { (void)data; return true; }
static int data_enable(const audio_codec_data_if_t *data, esp_codec_dev_type_t type, bool enabled) {
    (void)data;
    i2s_chan_handle_t channel = type == ESP_CODEC_DEV_TYPE_OUT ? &tx : &rx;
    return enabled ? i2s_channel_enable(channel) : i2s_channel_disable(channel);
}
static int data_format(const audio_codec_data_if_t *data, esp_codec_dev_type_t type, esp_codec_dev_sample_info_t *format) {
    (void)data; assert(format->sample_rate == 16000 && format->bits_per_sample == 16 && format->channel == 1);
    i2s_chan_handle_t channel = type == ESP_CODEC_DEV_TYPE_OUT ? &tx : &rx;
    if (channel->enabled) return i2s_channel_disable(channel);
    return ESP_CODEC_DEV_OK;
}
static int data_read(const audio_codec_data_if_t *data, uint8_t *pcm, int size) {
    (void)data; memset(pcm, 0, (size_t)size); return ESP_CODEC_DEV_OK;
}
static int data_write(const audio_codec_data_if_t *data, uint8_t *pcm, int size) {
    (void)data; return i2s_channel_write(&tx, pcm, (size_t)size, NULL, 1000);
}
static const audio_codec_data_if_t data = { .is_open = data_open, .enable = data_enable, .set_fmt = data_format, .read = data_read, .write = data_write };
static const audio_codec_gpio_if_t codec_gpio = { 0 };
static const audio_codec_if_t codec = { 0 };
const audio_codec_ctrl_if_t *audio_codec_new_i2c_ctrl(audio_codec_i2c_cfg_t *config) { assert(config); return &ctrl; }
const audio_codec_data_if_t *audio_codec_new_i2s_data(audio_codec_i2s_cfg_t *config) { assert(config->tx_handle == &tx && config->rx_handle == &rx); return &data; }
const audio_codec_gpio_if_t *audio_codec_new_gpio(void) { return &codec_gpio; }
const audio_codec_if_t *es8311_codec_new(es8311_codec_cfg_t *config) { assert(config->ctrl_if && config->no_dac_ref); return &codec; }
esp_codec_dev_handle_t esp_codec_dev_new(esp_codec_dev_cfg_t *config) { device_data = config->data_if; return &device_handle; }
int esp_codec_dev_open(esp_codec_dev_handle_t device, esp_codec_dev_sample_info_t *format) {
    assert(device == &device_handle); codec_opens++;
    int result = device_data->set_fmt(device_data, ESP_CODEC_DEV_TYPE_IN_OUT, format);
    return result ? result : device_data->enable(device_data, ESP_CODEC_DEV_TYPE_IN_OUT, true);
}
void esp_codec_dev_delete(esp_codec_dev_handle_t device) {
    assert(device == &device_handle);
    if (tx.enabled || rx.enabled) assert(device_data->enable(device_data, ESP_CODEC_DEV_TYPE_IN_OUT, false) == ESP_CODEC_DEV_OK);
}
int esp_codec_dev_set_in_gain(esp_codec_dev_handle_t device, float gain) { assert(device && gain == 30.0f); return ESP_CODEC_DEV_OK; }
int esp_codec_dev_set_out_vol(esp_codec_dev_handle_t device, int volume) { assert(device); last_volume = (unsigned)volume; volume_sets++; return ESP_CODEC_DEV_OK; }
int esp_codec_dev_write(esp_codec_dev_handle_t device, void *pcm, int size) { assert(device); return device_data->write(device_data, pcm, size); }
int esp_codec_dev_read(esp_codec_dev_handle_t device, void *pcm, int size) { assert(device); return device_data->read(device_data, pcm, size); }
int audio_codec_delete_codec_if(const audio_codec_if_t *value) { assert(value == &codec); return ESP_CODEC_DEV_OK; }
int audio_codec_delete_ctrl_if(const audio_codec_ctrl_if_t *value) { assert(value == &ctrl); return ESP_CODEC_DEV_OK; }
int audio_codec_delete_data_if(const audio_codec_data_if_t *value) { assert(value == &data); return ESP_CODEC_DEV_OK; }
int audio_codec_delete_gpio_if(const audio_codec_gpio_if_t *value) { assert(value == &codec_gpio); return ESP_CODEC_DEV_OK; }

static bool drain_cancelled(void *context) {
    assert(!in_isr && context == &cancel_at_ms);
    cancel_checks++;
    return elapsed_ms >= cancel_at_ms;
}

int main(int argc, char **argv) {
    assert(argc == 2); const char *scenario = argv[1];
    assert(bsp_audio_drain(250) == ESP_ERR_INVALID_STATE && !elapsed_ms);
    if (!strcmp(scenario, "registration_failure")) {
        registration_result = ESP_ERR_INVALID_ARG;
        assert(bsp_audio_init() == ESP_ERR_INVALID_ARG);
        assert(registrations == 1 && !enables && deletes == 2 && !tx.allocated && !rx.allocated);
        assert(bsp_audio_drain(250) == ESP_ERR_INVALID_STATE && !elapsed_ms);
        registration_result = ESP_OK;
    }
    assert(bsp_audio_init() == ESP_OK);
    assert(bsp_audio_drain(250) == ESP_ERR_INVALID_STATE && !elapsed_ms);
    assert(bsp_audio_set_format(16000, 16, 1) == ESP_OK);
    bsp_audio_set_volume(37);
    unsigned before_volume = volume_sets, before_opens = codec_opens, before_disables = disables;
    uint8_t pcm[16] = { 1, 2, 3 };
    /* Old silence completions must never count toward a new drain. */
    for (unsigned i = 0; i < 20; i++) complete_descriptor();
    if (!strcmp(scenario, "wrap")) { s_tx_completed = UINT32_MAX - 3; now_ticks = UINT32_MAX - 40; }
    assert(bsp_audio_write(pcm, sizeof pcm) == ESP_OK && pending_mask == 0x3f && !elapsed_ms);
    esp_err_t expected = ESP_OK;
    unsigned timeout = 250;
    if (!strcmp(scenario, "timeout")) { timeout = 20; expected = ESP_ERR_TIMEOUT; }
    if (!strcmp(scenario, "zero")) { timeout = 0; expected = ESP_ERR_TIMEOUT; }
    if (!strcmp(scenario, "subtick")) { timeout = FIXTURE_TICK_MS - 1; expected = ESP_ERR_TIMEOUT; }
    if (!strcmp(scenario, "stopped")) { tx.enabled = false; expected = ESP_ERR_TIMEOUT; }
    if (!strcmp(scenario, "nonadvancing")) { completion_limit = 0; expected = ESP_ERR_TIMEOUT; }
    if (!strcmp(scenario, "six_only")) { completion_limit = 6; expected = ESP_ERR_TIMEOUT; }
    if (!strcmp(scenario, "stop_midway")) { stop_at_completion = 3; expected = ESP_ERR_TIMEOUT; }
    bool cancellable = !strncmp(scenario, "cancel_", 7) || !strncmp(scenario, "never_cancel", 12);
    cancel_at_ms = UINT32_MAX;
    if (!strcmp(scenario, "cancel_entry")) cancel_at_ms = 0;
    if (!strcmp(scenario, "cancel_tick_one")) cancel_at_ms = FIXTURE_TICK_MS;
    if (!strcmp(scenario, "cancel_tick_two")) cancel_at_ms = 2 * FIXTURE_TICK_MS;
    if (!strcmp(scenario, "cancel_success")) cancel_at_ms = ((91 + FIXTURE_TICK_MS - 1) / FIXTURE_TICK_MS) * FIXTURE_TICK_MS;
    if (!strcmp(scenario, "cancel_deadline")) { timeout = 20; cancel_at_ms = 20; }
    if (!strcmp(scenario, "cancel_zero")) { timeout = 0; cancel_at_ms = 0; }
    if (!strcmp(scenario, "never_cancel_timeout")) { timeout = 20; expected = ESP_ERR_TIMEOUT; }
    if (cancel_at_ms != UINT32_MAX) expected = ESP_ERR_INVALID_STATE;
    esp_err_t drained = cancellable ? bsp_audio_drain_cancel(timeout, drain_cancelled, &cancel_at_ms) : bsp_audio_drain(timeout);
    assert(drained == expected);
    assert(elapsed_ms <= timeout);
    assert(cancellable ? cancel_checks >= elapsed_ms / FIXTURE_TICK_MS + 1 : !cancel_checks);
    assert(volume_sets == before_volume && codec_opens == before_opens && disables == before_disables && last_volume == 37);
    if (expected == ESP_OK) {
        assert(completion_count >= 7 && !pending_mask && elapsed_ms >= 91 && elapsed_ms <= 100);
        /* A second final write needs its own new completed ring. */
        unsigned before = elapsed_ms;
        assert(bsp_audio_write(pcm, sizeof pcm) == ESP_OK && pending_mask == 0x3f);
        assert(bsp_audio_drain(250) == ESP_OK && !pending_mask && elapsed_ms >= before + 91);
    } else if (expected == ESP_ERR_INVALID_STATE) {
        assert(elapsed_ms == cancel_at_ms);
        if (!strcmp(scenario, "cancel_success")) assert(completion_count == 7 && !pending_mask);
        else assert(completion_count < 7 && pending_mask);
    } else {
        assert(pending_mask && elapsed_ms == timeout - timeout % FIXTURE_TICK_MS);
    }
    /* Restore a fixture-stopped transport before exercising normal sleep. */
    if (!tx.enabled) assert(i2s_channel_enable(&tx) == ESP_OK);
    assert(bsp_audio_sleep() == ESP_OK);
    unsigned asleep_ms = elapsed_ms;
    assert(bsp_audio_drain(250) == ESP_ERR_INVALID_STATE && elapsed_ms == asleep_ms);
    assert(!tx.enabled && !rx.enabled);
    assert(bsp_audio_wake() == ESP_OK && last_volume == 37 && tx.enabled && rx.enabled);
    assert(bsp_audio_sleep() == ESP_OK);
    puts("PASS: bounded drain, DMA completion boundary and codec ownership");
    return 0;
}
'''


class FirmwareAudioDrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        sdk = Path(os.environ.get('IDF_PATH', str(root.parent / 'HPsdk/idf-5.5.3')))
        codec_candidates = list((root.parent / 'HPsdk/component-cache').glob('*/espressif__esp_codec_dev_1.6.2_*'))
        compiler = shutil.which('clang') or shutil.which('gcc')
        cached = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler or not (sdk / 'components/esp_driver_i2s/include/driver/i2s_common.h').is_file() or not codec_candidates:
            raise unittest.SkipTest('cached host compiler / SDK 5.5.3 / codec-dev 1.6.2 unavailable')
        codec = codec_candidates[0]
        # Keep all generated objects/linker files in the writable workspace.
        cls.directory = tempfile.TemporaryDirectory(prefix='audio-drain-', dir=root.parent)
        cls.addClassCleanup(cls.directory.cleanup)
        temp = Path(cls.directory.name)
        for name, content in HEADERS.items():
            path = temp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        (temp / 'drain.c').write_text(HARNESS, encoding='utf-8')
        cls.binaries = []
        for tick_ms in (1, 10):
            binary = temp / ('drain%d%s' % (tick_ms, '.exe' if os.name == 'nt' else ''))
            command = [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                       '-DFIXTURE_TICK_MS=%d' % tick_ms]
            for directory in (temp, root / 'firmware/components/bsp/include', root / 'firmware/components/bsp/src',
                              sdk / 'components/esp_driver_i2s/include', sdk / 'components/hal/include',
                              sdk / 'components/esp_common/include', codec / 'include', codec / 'interface', codec / 'device/include'):
                command.append('-I' + str(directory))
            command += [str(temp / 'drain.c'), str(root / 'firmware/components/bsp/src/bsp_es8311_sleep_check.c'), '-o', str(binary)]
            environment = os.environ.copy()
            environment.update(TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
            result = subprocess.run(command, capture_output=True, text=True, env=environment)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries.append(binary)

    def exercise(self, *scenarios):
        for binary in self.binaries:
            for scenario in scenarios:
                with self.subTest(binary=binary.name, scenario=scenario):
                    result = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('PASS:', result.stdout)

    def test_success_waits_for_final_descriptor_and_a_fresh_ring_each_time(self):
        self.exercise('success', 'wrap')

    def test_timeout_zero_and_subtick_budgets_are_bounded(self):
        self.exercise('timeout', 'zero', 'subtick')

    def test_stopped_nonadvancing_and_six_completions_cannot_claim_played(self):
        self.exercise('stopped', 'nonadvancing', 'six_only', 'stop_midway')

    def test_callback_registration_failure_cleans_up_and_allows_retry(self):
        self.exercise('registration_failure')

    def test_cancel_is_polled_at_entry_and_each_task_tick_without_codec_changes(self):
        self.exercise('cancel_entry', 'cancel_tick_one', 'cancel_tick_two', 'cancel_zero')

    def test_cancel_precedes_success_and_timeout_with_the_original_total_deadline(self):
        self.exercise('cancel_success', 'cancel_deadline', 'never_cancel', 'never_cancel_timeout')


if __name__ == '__main__':
    unittest.main()
