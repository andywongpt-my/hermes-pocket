// components/bsp/src/bsp_audio.c
// 移植自 trae_card/components/platform/platform_esp32/src/audio_es8311.c
#include "bsp_audio.h"
#include "sdkconfig.h"
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
#include "esp_timer.h"
#endif
#include "bsp_es8311_sleep_check.h"
#include "bsp_i2c.h"
#include "bsp_pins.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "es8311_codec.h"
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "bsp_audio";

static esp_codec_dev_handle_t s_dev;
static i2s_chan_handle_t      s_tx, s_rx;
static const audio_codec_ctrl_if_t *s_ctrl;
static const audio_codec_data_if_t *s_data;
static const audio_codec_if_t      *s_codec;
static const audio_codec_gpio_if_t *s_gpio;
// 记录当前已打开的格式,用于判断"要不要 close 重开"(见头文件里的坑说明)。
static uint32_t s_hz;
static uint8_t  s_bits, s_ch;
static bool     s_opened;
static bool     s_sleeping;
static bool     s_initialized;
static esp_err_t s_sleep_result;
static bool     s_codec_release_failed;
static uint8_t  s_volume;
static int      s_io_error;
/* Off by default: checked callers explicitly request a persistent mute guard.
 * This is codec control, not a PA hardware kill or an acoustic guarantee. */
static bool s_checked_mute_latched;
static bool s_checked_volume_active, s_checked_volume_written;
static uint8_t s_checked_volume_expected;

// Static internal RAM remains valid through codec/channel teardown. The ISR
// never dereferences a codec or channel handle, including during cleanup.
static DRAM_ATTR portMUX_TYPE s_tx_completion_lock = portMUX_INITIALIZER_UNLOCKED;
static DRAM_ATTR uint32_t s_tx_completed;
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
static DRAM_ATTR bsp_audio_render_t s_render;
static DRAM_ATTR bsp_audio_render_span_t s_render_spans[32];
static DRAM_ATTR unsigned s_render_span_head, s_render_span_count;
static bool s_render_owned, s_render_needs_reopen;
static DRAM_ATTR bsp_audio_capture_t s_capture;
static DRAM_ATTR bool s_reference_mode;
static bool s_reference_restore_pending;
/* Caller-owned serialization, not a task identity or mutex. Retain the lease
 * on cleanup failure so normal audio cannot mistake partial recovery for OK. */
static bool s_diagnostic_owned, s_diagnostic_cleanup_failed;
static bool s_diagnostic_saved_mute;
static uint8_t s_diagnostic_saved_volume, s_diagnostic_saved_bits, s_diagnostic_saved_ch;
static uint32_t s_diagnostic_saved_hz;
/* Synchronous delegation only: NOT a mutex or task identity. The existing
 * audio worker must exclusively own every lifecycle and PCM operation. */
static bool s_live_owned, s_live_delegating, s_live_ready, s_live_enabled;
static bool s_live_failed, s_live_cleanup_failed, s_live_saved_mute;
static uint32_t s_live_epoch, s_live_saved_hz;
static uint8_t s_live_requested_volume, s_live_saved_volume, s_live_saved_bits, s_live_saved_ch;
static uint32_t s_reference_restore_hz;
static uint8_t s_reference_restore_bits, s_reference_restore_ch;
#endif

static bool IRAM_ATTR audio_tx_sent(i2s_chan_handle_t channel,
                                   i2s_event_data_t *event, void *context) {
    (void)channel;
    (void)event;
    (void)context;
    portENTER_CRITICAL_ISR(&s_tx_completion_lock);
    s_tx_completed++;
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_render.active) {
        if (!s_reference_mode && s_render_span_count == 32) {
            s_render.failed = true;
            s_render.active = false;
        } else if (event && bsp_audio_render_complete(&s_render, s_render.epoch,
                                                      event->dma_buf, event->size)) {
            /* v2 reference comes from paired RX. Its unused diagnostic-span
             * queue must not impose a hidden consumer/deadline on streaming. */
            if (!s_reference_mode) {
                unsigned at = (s_render_span_head + s_render_span_count) % 32;
                s_render_spans[at] = s_render.completed;
                s_render_span_count++;
            }
        } else {
            s_render.failed = true;
            s_render.active = false;
        }
    }
#endif
    portEXIT_CRITICAL_ISR(&s_tx_completion_lock);
    return false;
}

#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
#if !CONFIG_I2S_ISR_IRAM_SAFE
#error "Callback render requires the IRAM-safe I2S interrupt path"
#endif
static bool IRAM_ATTR audio_rx_overflow(i2s_chan_handle_t channel,
                                        i2s_event_data_t *event, void *context) {
    (void)channel; (void)event; (void)context;
    portENTER_CRITICAL_ISR(&s_tx_completion_lock);
    /* Paired mode copies every RX descriptor itself. Its unused driver queue
     * naturally fills; only our owned queue/order/deadline establishes loss. */
    if (s_render.active && !s_reference_mode) {
        s_render.failed = true;
        s_render.active = false;
    }
    portEXIT_CRITICAL_ISR(&s_tx_completion_lock);
    return false;
}

static bool IRAM_ATTR audio_rx_received(i2s_chan_handle_t channel,
                                        i2s_event_data_t *event, void *context) {
    (void)channel; (void)context;
    portENTER_CRITICAL_ISR(&s_tx_completion_lock);
    if (s_reference_mode && s_render.active) {
        if (!event || !bsp_audio_capture_complete(&s_capture, s_render.epoch,
                event->dma_buf, event->size, esp_timer_get_time(), s_render.dma_completed)) {
            s_render.failed = true;
            s_render.active = false;
        }
    }
    portEXIT_CRITICAL_ISR(&s_tx_completion_lock);
    return false;
}
#endif

static uint32_t audio_tx_completions(void) {
    portENTER_CRITICAL(&s_tx_completion_lock);
    uint32_t completed = s_tx_completed;
    portEXIT_CRITICAL(&s_tx_completion_lock);
    return completed;
}

// esp_codec_dev 1.6.2 discards some control/data interface errors. Remember the
// first one at the public interface boundary; never inspect its private state.
static int audio_track_error(int result) {
    if (result != ESP_CODEC_DEV_OK && s_io_error == ESP_CODEC_DEV_OK) {
        s_io_error = result;
    }
    return result;
}

static bool audio_ctrl_is_open(const audio_codec_ctrl_if_t *ctrl) {
    (void)ctrl;
    return s_ctrl && s_ctrl->is_open(s_ctrl);
}

static int audio_ctrl_read(const audio_codec_ctrl_if_t *ctrl, int reg,
                           int reg_len, void *data, int data_len) {
    (void)ctrl;
    return audio_track_error(s_ctrl->read_reg(s_ctrl, reg, reg_len, data, data_len));
}

static int audio_ctrl_write(const audio_codec_ctrl_if_t *ctrl, int reg,
                            int reg_len, void *data, int data_len) {
    (void)ctrl;
    uint8_t guarded;
    if (reg == 0x31 && s_checked_mute_latched && reg_len == 1 && data_len == 1 && data) {
        guarded = *(const uint8_t *)data | 0x60u;
        data = &guarded;
    }
    if (reg == 0x32 && s_checked_volume_active) {
        if (reg_len != 1 || data_len != 1 || !data ||
            *(const uint8_t *)data != s_checked_volume_expected) {
            return audio_track_error(ESP_CODEC_DEV_DRV_ERR);
        }
    }
    int result = s_ctrl->write_reg(s_ctrl, reg, reg_len, data, data_len);
    if (reg == 0x32 && s_checked_volume_active && result == ESP_CODEC_DEV_OK) {
        s_checked_volume_written = true;
    }
    return audio_track_error(result);
}

static int audio_ctrl_info(const audio_codec_ctrl_if_t *ctrl,
                           audio_codec_ctrl_info_t *info) {
    (void)ctrl;
    return audio_track_error(s_ctrl->get_info(s_ctrl, info));
}

static const audio_codec_ctrl_if_t s_checked_ctrl = {
    .is_open = audio_ctrl_is_open, .read_reg = audio_ctrl_read,
    .write_reg = audio_ctrl_write, .get_info = audio_ctrl_info,
};

static bool audio_data_is_open(const audio_codec_data_if_t *data) {
    (void)data;
    return s_data && s_data->is_open(s_data);
}

static int audio_data_enable_one(esp_codec_dev_type_t type, bool enable) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    bool stopped_render_channel = false;
    if (!enable && s_render_needs_reopen) {
        i2s_chan_info_t info;
        i2s_chan_handle_t channel = type == ESP_CODEC_DEV_TYPE_OUT ? s_tx : s_rx;
        stopped_render_channel = channel && i2s_channel_get_info(channel, &info) == ESP_OK && !info.is_enabled;
    }
#endif
    int result = s_data->enable(s_data, type, enable);
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    /* codec1.6.2 close disables even independently stopped channels. Still call
     * its interface to clear its enabled flags; normalize ONLY that verified
     * redundant driver's disable error, never an enabled-channel/codec error. */
    if (stopped_render_channel && result == ESP_CODEC_DEV_DRV_ERR) result = ESP_CODEC_DEV_OK;
#endif
    return audio_track_error(result);
}

static int audio_data_enable(const audio_codec_data_if_t *data,
                             esp_codec_dev_type_t type, bool enable) {
    (void)data;
    // IN_OUT overwrites a failed TX result with the RX result in 1.6.2.
    // Enable TX first (master clock), disable RX first (no deferred TX stop).
    esp_codec_dev_type_t first = enable ? ESP_CODEC_DEV_TYPE_OUT : ESP_CODEC_DEV_TYPE_IN;
    esp_codec_dev_type_t last = enable ? ESP_CODEC_DEV_TYPE_IN : ESP_CODEC_DEV_TYPE_OUT;
    int result = ESP_CODEC_DEV_OK;
    if (type & first) result = audio_data_enable_one(first, enable);
    if (type & last) {
        int next = audio_data_enable_one(last, enable);
        if (result == ESP_CODEC_DEV_OK) result = next;
    }
    return result;
}

static int audio_data_set_fmt(const audio_codec_data_if_t *data,
                              esp_codec_dev_type_t type,
                              esp_codec_dev_sample_info_t *fs) {
    (void)data;
    if (type != ESP_CODEC_DEV_TYPE_IN_OUT) return ESP_CODEC_DEV_NOT_SUPPORT;
    // Configure directions separately so a failed TX configuration cannot be
    // hidden by a successful RX configuration. Keep TX enabled while configuring
    // RX: the dependency otherwise reconfigures/restarts TX a second time.
    int result = audio_track_error(s_data->set_fmt(s_data, ESP_CODEC_DEV_TYPE_OUT, fs));
    if (result != ESP_CODEC_DEV_OK) return result;
    result = audio_track_error(s_data->enable(s_data, ESP_CODEC_DEV_TYPE_OUT, true));
    if (result == ESP_CODEC_DEV_OK) {
        result = audio_track_error(s_data->set_fmt(s_data, ESP_CODEC_DEV_TYPE_IN, fs));
    }
    int stopped = audio_track_error(s_data->enable(s_data, ESP_CODEC_DEV_TYPE_OUT, false));
    return result == ESP_CODEC_DEV_OK ? stopped : result;
}

static int audio_data_read(const audio_codec_data_if_t *data, uint8_t *pcm, int size) {
    (void)data;
    return s_data->read(s_data, pcm, size);
}

static int audio_data_write(const audio_codec_data_if_t *data, uint8_t *pcm, int size) {
    (void)data;
    return s_data->write(s_data, pcm, size);
}

static const audio_codec_data_if_t s_checked_data = {
    .is_open = audio_data_is_open, .enable = audio_data_enable,
    .set_fmt = audio_data_set_fmt, .read = audio_data_read, .write = audio_data_write,
};

#define AUDIO_DEFAULT_HZ   16000
#define AUDIO_DEFAULT_BITS 16
#define AUDIO_DEFAULT_CH   1
#define AUDIO_DMA_DESC_COUNT 6
#define ES8311_SLEEP_ATTEMPTS 2
#define ES8311_SLEEP_RETRY_MS 5

typedef struct {
    uint8_t reg;
    uint8_t value;
} es8311_reg_value_t;

// 寄存器序列不依赖 esp_codec_dev 的 opened 标志。REG45=0x01 额外关闭
// BCLK/LRCK 内部上拉，比当前 esp_codec_dev 1.6.2 的默认 suspend 更彻底。
static const es8311_reg_value_t s_es8311_sleep_sequence[] = {
    {0x32, 0x00}, {0x17, 0x00}, {0x0E, 0xFF}, {0x12, 0x02},
    {0x14, 0x00}, {0x0D, 0xFA}, {0x15, 0x00}, {0x02, 0x10},
    {0x00, 0x00}, {0x00, 0x1F}, {0x01, 0x30}, {0x01, 0x00},
    {0x45, 0x01}, {0x0D, 0xFC}, {0x02, 0x00},
};

static esp_err_t audio_disable_i2s_channels(void) {
    esp_err_t first_error = ESP_OK;
    const struct {
        i2s_chan_handle_t channel;
        const char *name;
    } channels[] = {
        {s_tx, "TX"},
        {s_rx, "RX"},
    };

    for (size_t i = 0; i < sizeof(channels) / sizeof(channels[0]); i++) {
        if (!channels[i].channel) continue;
        esp_err_t e = i2s_channel_disable(channels[i].channel);
        if (e == ESP_ERR_INVALID_STATE) e = ESP_OK; // READY 即已停止。
        if (e != ESP_OK) {
            ESP_LOGE(TAG, "I2S %s 停止失败: %s", channels[i].name,
                     esp_err_to_name(e));
            if (first_error == ESP_OK) first_error = e;
        }
    }
    return first_error;
}

static esp_err_t es8311_force_sleep_once(unsigned attempt) {
    bool valid = true;

    for (size_t i = 0; i < sizeof(s_es8311_sleep_sequence) /
                           sizeof(s_es8311_sleep_sequence[0]); i++) {
        const es8311_reg_value_t *item = &s_es8311_sleep_sequence[i];
        uint8_t value = item->value;
        int write_result = s_ctrl->write_reg(s_ctrl, item->reg, 1, &value, 1);
        if (write_result == ESP_CODEC_DEV_OK) continue;

        uint8_t actual = 0;
        int read_result = s_ctrl->read_reg(s_ctrl, item->reg, 1, &actual, 1);
        if (read_result == ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "ES8311 休眠写入失败 attempt=%u REG%02X "
                          "expected=0x%02X actual=0x%02X error=%d",
                     attempt, item->reg, item->value, actual, write_result);
        } else {
            ESP_LOGE(TAG, "ES8311 休眠写入失败 attempt=%u REG%02X "
                          "expected=0x%02X actual=unavailable error=%d read_error=%d",
                     attempt, item->reg, item->value, write_result, read_result);
        }
        valid = false;
    }

    for (size_t i = 0; i < bsp_es8311_sleep_check_count; i++) {
        const bsp_es8311_reg_check_t *item = &bsp_es8311_sleep_checks[i];
        uint8_t actual = 0;
        int read_result = s_ctrl->read_reg(s_ctrl, item->reg, 1, &actual, 1);
        if (read_result == ESP_CODEC_DEV_OK &&
            bsp_es8311_sleep_check_matches(item, actual)) continue;

        ESP_LOGE(TAG, "ES8311 休眠校验失败 attempt=%u REG%02X "
                      "expected=0x%02X mask=0x%02X actual=0x%02X error=%d",
                 attempt, item->reg, item->value, item->mask, actual, read_result);
        valid = false;
    }
    return valid ? ESP_OK : ESP_FAIL;
}

static esp_err_t es8311_force_sleep(void) {
    if (!s_ctrl || !s_ctrl->read_reg || !s_ctrl->write_reg) {
        return ESP_ERR_INVALID_STATE;
    }

    for (unsigned attempt = 1; attempt <= ES8311_SLEEP_ATTEMPTS; attempt++) {
        esp_err_t e = es8311_force_sleep_once(attempt);
        if (e == ESP_OK) {
            ESP_LOGI(TAG, "ES8311 已进入低功耗状态并通过寄存器校验");
            return ESP_OK;
        }
        if (attempt < ES8311_SLEEP_ATTEMPTS) {
            vTaskDelay(pdMS_TO_TICKS(ES8311_SLEEP_RETRY_MS));
        }
    }

    ESP_LOGE(TAG, "ES8311 强制休眠失败");
    return ESP_FAIL;
}

// esp_codec_dev_open() 会先 disable 再重配 I2S；close 后通道处于 READY，
// 先 enable 一次可让下一次 open 的内部 disable 合法。
static esp_err_t audio_prepare_i2s_reopen(void) {
    esp_err_t e = s_tx ? i2s_channel_enable(s_tx) : ESP_ERR_INVALID_STATE;
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "I2S TX 恢复失败: %s", esp_err_to_name(e));
        return e;
    }
    e = s_rx ? i2s_channel_enable(s_rx) : ESP_ERR_INVALID_STATE;
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX 恢复失败: %s", esp_err_to_name(e));
        i2s_channel_disable(s_tx);
    }
    return e;
}

// esp_codec_dev 不拥有传入的接口和 I2S channel；失败回滚必须按依赖逆序逐一释放。
static void audio_delete_codec(void) {
    if (s_dev) {
        esp_codec_dev_delete(s_dev);
        s_dev = NULL;
    }
    if (s_codec) {
        // The dependency frees the interface even when close() fails (for
        // example, a reference-manager lock timeout). Its shared reference may
        // then survive: rebuilding could silently skip hardware initialization.
        // Such an unrecoverable release error needs a reboot, never a false OK.
        int result = audio_codec_delete_codec_if(s_codec);
        if (result != ESP_CODEC_DEV_OK) {
            s_codec_release_failed = true;
            audio_track_error(result);
            ESP_LOGE(TAG, "ES8311 codec 释放失败，需重启恢复: %d", result);
        }
        s_codec = NULL;
    }
    s_opened = false;
}

static void audio_cleanup(void) {
    audio_delete_codec();
    if (s_gpio) {
        audio_codec_delete_gpio_if(s_gpio);
        s_gpio = NULL;
    }
    if (s_data) {
        audio_codec_delete_data_if(s_data);
        s_data = NULL;
    }
    if (s_rx) {
        i2s_channel_disable(s_rx);
        esp_err_t e = i2s_del_channel(s_rx);
        if (e == ESP_OK) s_rx = NULL;
        else ESP_LOGE(TAG, "I2S RX 回滚失败: %s", esp_err_to_name(e));
    }
    if (s_tx) {
        i2s_channel_disable(s_tx);
        esp_err_t e = i2s_del_channel(s_tx);
        if (e == ESP_OK) s_tx = NULL;
        else ESP_LOGE(TAG, "I2S TX 回滚失败: %s", esp_err_to_name(e));
    }
    if (s_ctrl) {
        audio_codec_delete_ctrl_if(s_ctrl);
        s_ctrl = NULL;
    }
    s_opened = false;
    s_sleeping = false;
    s_initialized = false;
    s_sleep_result = ESP_OK;
    s_volume = 0;
    s_hz = 0;
    s_bits = 0;
    s_ch = 0;
}

static esp_err_t i2s_full_duplex_init(void) {
    i2s_chan_config_t chan = {
        .id = BSP_I2S_PORT,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = AUDIO_DMA_DESC_COUNT,
        .dma_frame_num = 240,
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
        .auto_clear_after_cb = false,
        .auto_clear_before_cb = true,
#else
        .auto_clear_after_cb = true,
        .auto_clear_before_cb = false,
#endif
        .intr_priority = 0,
    };
    esp_err_t e = i2s_new_channel(&chan, &s_tx, &s_rx);
    if (e != ESP_OK) { ESP_LOGE(TAG, "i2s_new_channel 失败: %s", esp_err_to_name(e)); return e; }

    // 这里的采样率只用于建通道;实际速率由 esp_codec_dev_open() 按需重配。
    i2s_std_config_t std = {
        .clk_cfg = {
            .sample_rate_hz = 16000,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = I2S_STD_SLOT_BOTH,
            .ws_width = I2S_DATA_BIT_WIDTH_16BIT,
            .ws_pol = false,
            .bit_shift = true,
            .left_align = true,
            .big_endian = false,
            .bit_order_lsb = false,
        },
        .gpio_cfg = {
            .mclk = BSP_I2S_MCLK, .bclk = BSP_I2S_BCLK, .ws = BSP_I2S_WS,
            .dout = BSP_I2S_DOUT, .din = BSP_I2S_DIN,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    if ((e = i2s_channel_init_std_mode(s_tx, &std)) != ESP_OK) {
        ESP_LOGE(TAG, "i2s tx 初始化失败: %s", esp_err_to_name(e)); return e;
    }
    if ((e = i2s_channel_init_std_mode(s_rx, &std)) != ESP_OK) {
        ESP_LOGE(TAG, "i2s rx 初始化失败: %s", esp_err_to_name(e)); return e;
    }
    // This is a new TX channel with no other callbacks. Register once while
    // READY; codec format changes subsequently stop/restart the same channel.
    const i2s_event_callbacks_t callbacks = { .on_sent = audio_tx_sent };
    e = i2s_channel_register_event_callback(s_tx, &callbacks, NULL);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "I2S TX completion callback: %s", esp_err_to_name(e));
        return e;
    }
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    const i2s_event_callbacks_t rx_callbacks = { .on_recv = audio_rx_received, .on_recv_q_ovf = audio_rx_overflow };
    e = i2s_channel_register_event_callback(s_rx, &rx_callbacks, NULL);
    if (e != ESP_OK) return e;
#endif
    // esp_codec_dev_open 内部重配前会先 i2s_channel_disable,而 disable 要求通道处于
    // RUNNING;刚 init 的通道是 READY,会打一条 "channel has not been enabled yet" 错误日志。
    // 这里先 enable 一次让那次 disable 合法(此时 codec 未配,不出声)。
    e = i2s_channel_enable(s_tx);
    if (e == ESP_OK) e = i2s_channel_enable(s_rx);
    if (e != ESP_OK) ESP_LOGE(TAG, "i2s channel enable 失败: %s", esp_err_to_name(e));
    return e;
}

static esp_err_t audio_create_codec(void) {
    if (s_codec_release_failed) return ESP_ERR_INVALID_STATE;
    s_io_error = ESP_CODEC_DEV_OK;
    s_codec = es8311_codec_new(&(es8311_codec_cfg_t){
        .ctrl_if = &s_checked_ctrl,
        .gpio_if = s_gpio,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .pa_pin = BSP_I2S_PA_CTRL,
        .pa_reverted = false,
        .master_mode = false,
        .use_mclk = true,
        .hw_gain = { .pa_voltage = 5.0f, .codec_dac_voltage = 3.3f },
        #if CONFIG_BSP_AUDIO_CALLBACK_RENDER
        .no_dac_ref = !s_reference_mode,
#else
        .no_dac_ref = true, // Mono capture must use ADC, not the DAC reference.
#endif
    });
    if (!s_codec || s_io_error != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "es8311_codec_new 失败: %d", s_io_error);
        audio_delete_codec();
        return ESP_FAIL;
    }
    s_dev = esp_codec_dev_new(&(esp_codec_dev_cfg_t){
        .dev_type = ESP_CODEC_DEV_TYPE_IN_OUT,
        .codec_if = s_codec,
        .data_if = &s_checked_data,
    });
    if (!s_dev) {
        audio_delete_codec();
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t bsp_audio_init(void) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
    if (s_codec_release_failed) return ESP_ERR_INVALID_STATE;
    if (s_initialized) return ESP_OK;
    if (s_tx || s_rx || s_ctrl || s_data || s_codec || s_gpio) {
        ESP_LOGE(TAG, "上次音频初始化回滚不完整，拒绝覆盖仍存活的资源句柄");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t e = bsp_i2c_init();
    if (e != ESP_OK) return e;

    s_ctrl = audio_codec_new_i2c_ctrl(&(audio_codec_i2c_cfg_t){
        .port = BSP_I2C_PORT,
        .addr = BSP_I2C_ES8311_ADDR << 1,   // 该接口要 8 位地址形式
        .bus_handle = bsp_i2c_bus(),
    });
    if (!s_ctrl) {
        ESP_LOGE(TAG, "ES8311 控制口创建失败 —— 用 bsp_i2c_scan() 确认 0x%02X 是否应答;"
                      "检查 SDA=GPIO%d / SCL=GPIO%d 接线与 codec 供电",
                 BSP_I2C_ES8311_ADDR, BSP_I2C_SDA, BSP_I2C_SCL);
        return ESP_FAIL;
    }

    if ((e = i2s_full_duplex_init()) != ESP_OK) goto fail;

    s_data = audio_codec_new_i2s_data(&(audio_codec_i2s_cfg_t){
        .port = BSP_I2S_PORT, .tx_handle = s_tx, .rx_handle = s_rx,
    });
    if (!s_data) { ESP_LOGE(TAG, "I2S 数据口创建失败"); e = ESP_ERR_NO_MEM; goto fail; }

    s_gpio = audio_codec_new_gpio();
    if (!s_gpio) { ESP_LOGE(TAG, "codec GPIO 接口创建失败"); e = ESP_ERR_NO_MEM; goto fail; }

    if ((e = audio_create_codec()) != ESP_OK) goto fail;

    s_initialized = true;
    ESP_LOGI(TAG, "ES8311 就绪");
    return ESP_OK;

fail:
    audio_delete_codec();
    if (s_ctrl) (void)es8311_force_sleep();
    audio_cleanup();
    return e;
}

esp_err_t bsp_audio_set_format(uint32_t hz, uint8_t bits, uint8_t ch) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_render_owned) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_initialized) return ESP_ERR_INVALID_STATE;
    if (s_sleeping) return ESP_ERR_INVALID_STATE;
    if (s_opened && s_hz == hz && s_bits == bits && s_ch == ch
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
        && !s_render_needs_reopen
#endif
    ) return ESP_OK;   // 同格式复用

    esp_err_t e = ESP_FAIL;
    s_io_error = ESP_CODEC_DEV_OK;
    if (s_opened) audio_delete_codec();
    if (s_io_error != ESP_CODEC_DEV_OK) goto fail;
    // Always normalize the channels, including after a failed partial open.
    e = audio_disable_i2s_channels();
    if (e != ESP_OK) goto fail;
    e = audio_prepare_i2s_reopen();
    if (e != ESP_OK) goto fail;
    if (!s_dev && (e = audio_create_codec()) != ESP_OK) goto fail;
    s_io_error = ESP_CODEC_DEV_OK;

    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = bits,
        .channel = ch,
        #if CONFIG_BSP_AUDIO_CALLBACK_RENDER
        .channel_mask = s_reference_mode ? (ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0) | ESP_CODEC_DEV_MAKE_CHANNEL_MASK(1)) : ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0),
#else
        .channel_mask = ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0),
#endif
        .sample_rate = hz,
        .mclk_multiple = 0,          // 0 → 驱动按默认 256xfs 取 MCLK
    };
    int r = esp_codec_dev_open(s_dev, &fs);
    if (r != ESP_CODEC_DEV_OK || s_io_error != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "esp_codec_dev_open 失败: %d interface=%d", r, s_io_error);
        e = ESP_FAIL;
        goto fail;
    }

    // ⚠ open 之后【不要】手动覆写 ES8311 的时钟分频寄存器(REG01~06):
    //   驱动已按采样率与 MCLK 精确算好,覆写会导致 ADC/DAC 时序错乱、录音回放全是杂音。
    // Pinned esp_codec_dev 1.6.2 set_in_gain(30) writes ADC register 0x16=0x05,
    // not the analog PGA: codec start separately sets 0x14=0x1A and 0x17=0xBF.
    // The full-byte gain write also clears ADC_SYNC bit 5 from init 0x24.
    // This documents existing behavior only; no gain/clock register change here.
    r = esp_codec_dev_set_in_gain(s_dev, 30.0f);
    if (r == ESP_CODEC_DEV_OK) r = esp_codec_dev_set_out_vol(s_dev, s_volume);
    if (r != ESP_CODEC_DEV_OK || s_io_error != ESP_CODEC_DEV_OK) {
        e = ESP_FAIL;
        goto fail;
    }

    s_opened = true; s_hz = hz; s_bits = bits; s_ch = ch;
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    s_render_needs_reopen = false;
#endif
    ESP_LOGI(TAG, "codec 打开 %luHz/%ubit/%uch", (unsigned long)hz, bits, ch);
    return ESP_OK;

fail:
    // A failed open can already have set codec-dev's opened flags and the
    // ES8311 enabled/reference state. Recreate through public APIs next time.
    audio_delete_codec();
    (void)es8311_force_sleep();
    (void)audio_disable_i2s_channels();
    return e == ESP_OK ? ESP_FAIL : e;
}

esp_err_t bsp_audio_sleep(void) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_render_owned) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_initialized) return ESP_OK;
    if (s_sleeping) return s_sleep_result;

    esp_err_t first_error = ESP_OK;
    // Drop stale enabled/opened state, even if suspend fails. Deletion may write
    // the dependency's weaker suspend sequence, so force-sleep MUST run last.
    // Only codec objects are released: I2C bus and I2S channels remain owned.
    s_io_error = ESP_CODEC_DEV_OK;
    audio_delete_codec();
    if (s_io_error != ESP_CODEC_DEV_OK) first_error = ESP_FAIL;

    esp_err_t e = es8311_force_sleep();
    if (e != ESP_OK && first_error == ESP_OK) first_error = e;

    e = audio_disable_i2s_channels();
    if (e != ESP_OK && first_error == ESP_OK) first_error = e;
    s_sleeping = true;
    s_sleep_result = first_error;
    return first_error;
}

esp_err_t bsp_audio_prepare_deep_sleep(void) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_render_owned) return ESP_ERR_INVALID_STATE;
#endif
    esp_err_t first_error = audio_disable_i2s_channels();
    const int pins[] = {
        BSP_I2S_MCLK, BSP_I2S_BCLK, BSP_I2S_WS, BSP_I2S_DOUT, BSP_I2S_DIN,
    };
    uint64_t mask = 0;
    for (size_t i = 0; i < sizeof(pins) / sizeof(pins[0]); i++) {
        if (pins[i] >= 0) mask |= 1ULL << (unsigned)pins[i];
    }

    gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t e = gpio_config(&cfg);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "I2S 引脚高阻配置失败: %s", esp_err_to_name(e));
        if (first_error == ESP_OK) first_error = e;
    } else {
        ESP_LOGI(TAG, "I2S MCLK/BCLK/WS/DOUT/DIN 已切换为高阻");
    }
    return first_error;
}

esp_err_t bsp_audio_wake(void) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_initialized || !s_sleeping) return ESP_OK;

    // 允许内部格式设置重新 open codec；失败时再次 close，避免留下半唤醒状态。
    s_sleeping = false;
    esp_err_t e = bsp_audio_set_format(s_hz ? s_hz : AUDIO_DEFAULT_HZ,
                             s_bits ? s_bits : AUDIO_DEFAULT_BITS,
                             s_ch ? s_ch : AUDIO_DEFAULT_CH);
    if (e != ESP_OK) {
        s_opened = false;
        s_sleeping = true;
        s_sleep_result = e;
        ESP_LOGE(TAG, "ES8311 唤醒失败: %s", esp_err_to_name(e));
        return e;
    }

    s_sleep_result = ESP_OK;
    ESP_LOGI(TAG, "ES8311 已从低功耗状态恢复");
    return ESP_OK;
}

esp_err_t bsp_audio_write(const void *pcm, size_t bytes) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned || s_diagnostic_owned || s_render_owned || s_render_needs_reopen) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_dev || !s_opened || s_sleeping) return ESP_ERR_INVALID_STATE;
    return esp_codec_dev_write(s_dev, (void *)pcm, bytes) == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t bsp_audio_drain(uint32_t timeout_ms) {
    return bsp_audio_drain_cancel(timeout_ms, NULL, NULL);
}

esp_err_t bsp_audio_drain_cancel(uint32_t timeout_ms,
                                 bool (*cancelled)(void *), void *context) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned || s_diagnostic_owned) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_dev || !s_opened || s_sleeping || !s_tx) {
        return ESP_ERR_INVALID_STATE;
    }

    // i2s_channel_write() only copies to the circular DMA buffers. Starting
    // after the caller's final write, one entire ring plus one completion
    // covers every queued buffer and an EOF already in flight at the snapshot.
    // Require fresh hardware progress: a stopped/stalled TX must time out.
    const uint32_t initial = audio_tx_completions();
    const TickType_t started = xTaskGetTickCount();
    const TickType_t budget = pdMS_TO_TICKS(timeout_ms);
    for (;;) {
        if (cancelled && cancelled(context)) return ESP_ERR_INVALID_STATE;
        if ((TickType_t)(xTaskGetTickCount() - started) >= budget) {
            return ESP_ERR_TIMEOUT;
        }
        if ((uint32_t)(audio_tx_completions() - initial) >=
            AUDIO_DMA_DESC_COUNT + 1u) {
            return ESP_OK;
        }
        vTaskDelay(1);
    }
}

esp_err_t bsp_audio_read(void *pcm, size_t bytes) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned || s_diagnostic_owned || s_render_owned || s_render_needs_reopen) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_dev || !s_opened || s_sleeping) return ESP_ERR_INVALID_STATE;
    return esp_codec_dev_read(s_dev, pcm, bytes) == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t bsp_audio_prepare_silent_start(void) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
    if (s_initialized || s_dev || s_ctrl || s_tx || s_rx || s_data || s_codec || s_gpio) {
        return ESP_ERR_INVALID_STATE;
    }
    s_checked_mute_latched = true;
    s_volume = 0;
    return ESP_OK; /* software intent only; checked hardware readback follows init */
}

esp_err_t bsp_audio_set_mute_checked(bool muted) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_diagnostic_owned && !muted) return ESP_ERR_INVALID_STATE;
#endif
    if (!s_initialized || !s_dev || !s_ctrl || s_sleeping) return ESP_ERR_INVALID_STATE;
    /* Latch true until an independently read unmute has succeeded. Direct
     * public ctrl I/O avoids codec1.6.2's swallowed mute-write errors and its
     * use of an uninitialized register value when its initial read fails. */
    s_checked_mute_latched = true;
    s_io_error = ESP_CODEC_DEV_OK;
    uint8_t before = 0, after = 0;
    if (audio_ctrl_read(&s_checked_ctrl, 0x31, 1, &before, 1) != ESP_CODEC_DEV_OK) {
        s_volume = 0;
        return ESP_FAIL;
    }
    uint8_t desired = (uint8_t)((before & (uint8_t)~0x60u) | (muted ? 0x60u : 0));
    int result = audio_track_error(s_ctrl->write_reg(s_ctrl, 0x31, 1, &desired, 1));
    if (result != ESP_CODEC_DEV_OK ||
        audio_ctrl_read(&s_checked_ctrl, 0x31, 1, &after, 1) != ESP_CODEC_DEV_OK ||
        after != desired || s_io_error != ESP_CODEC_DEV_OK) {
        /* Best effort only: an I2C fault can prevent muting; return failure. */
        s_volume = 0;
        uint8_t safe = before | 0x60u;
        (void)audio_ctrl_write(&s_checked_ctrl, 0x31, 1, &safe, 1);
        return ESP_FAIL;
    }
    s_checked_mute_latched = muted;
    return ESP_OK;
}

esp_err_t bsp_audio_set_volume_checked(uint8_t percent) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
#endif
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_diagnostic_owned && percent) return ESP_ERR_INVALID_STATE;
#endif
    if (percent > 100) return ESP_ERR_INVALID_ARG;
    if (!s_initialized || !s_dev || !s_ctrl || s_sleeping) return ESP_ERR_INVALID_STATE;
    if (bsp_audio_set_mute_checked(true) != ESP_OK) {
        s_volume = 0;
        return ESP_FAIL;
    }
    /* Exact pinned ES8311/default curve + this BSP's5V/3.3V,pa_gain0 profile:
     * dB(p>0)=-50+p/2; dB(0)=-96; hw_gain=20log10(3.3/5).
     * REG32=floor(2*(dB-hw_gain+95.5)): p>0 =>98+p, p0=>6.
     * Reject a wrong curve/software-volume/no-write path BEFORE a louder write.
     * These are digital attenuation settings, not calibrated speaker SPL.
     */
    s_checked_volume_expected = percent ? (uint8_t)(98u + percent) : 6u;
    s_checked_volume_written = false;
    s_checked_volume_active = true;
    s_io_error = ESP_CODEC_DEV_OK;
    int result = esp_codec_dev_set_out_vol(s_dev, percent);
    s_checked_volume_active = false;
    uint8_t actual = 0;
    if (result != ESP_CODEC_DEV_OK || s_io_error != ESP_CODEC_DEV_OK || !s_checked_volume_written ||
        audio_ctrl_read(&s_checked_ctrl, 0x32, 1, &actual, 1) != ESP_CODEC_DEV_OK ||
        actual != s_checked_volume_expected || s_io_error != ESP_CODEC_DEV_OK) goto failed;
    s_volume = percent;
    if (percent && bsp_audio_set_mute_checked(false) != ESP_OK) goto failed;
    return ESP_OK;
failed:
    s_volume = 0;
    (void)bsp_audio_set_mute_checked(true);
    return ESP_FAIL;
}

void bsp_audio_set_volume(uint8_t percent) {
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
    if (s_live_owned || s_diagnostic_owned) return;
#endif
    s_volume = percent > 100 ? 100 : percent;
    if (s_dev && s_opened && !s_sleeping) esp_codec_dev_set_out_vol(s_dev, s_volume);
}

#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
esp_err_t bsp_audio_render_start(uint32_t epoch) {
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
    if (!epoch || s_render_owned || !s_dev || !s_opened || s_sleeping ||
        s_hz != 24000 || s_bits != 16 || s_ch != (s_reference_mode ? 2 : 1)) return ESP_ERR_INVALID_STATE;
    /* Own even a failed start until explicit stop: stopped/partially started
     * channels must never fall through to legacy writes or same-format reuse. */
    s_render_owned = true;
    s_render_needs_reopen = true;
    portENTER_CRITICAL(&s_tx_completion_lock);
    s_render.active = false;
    s_render.failed = true;
    portEXIT_CRITICAL(&s_tx_completion_lock);
    /* Owner has joined legacy readers/writers. Stop RX before its TX clock. */
    esp_err_t e = i2s_channel_disable(s_rx);
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) return e;
    e = i2s_channel_disable(s_tx);
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) return e;
    static const int16_t silence[BSP_RENDER_FRAMES * 2] = {0};
    size_t silence_bytes = BSP_RENDER_FRAMES * s_ch * sizeof(int16_t);
    for (unsigned i = 0; i < BSP_RENDER_DESCRIPTORS; i++) {
        size_t loaded = 0;
        e = i2s_channel_preload_data(s_tx, silence, silence_bytes, &loaded);
        if (e != ESP_OK || loaded != silence_bytes) return ESP_FAIL;
    }
    portENTER_CRITICAL(&s_tx_completion_lock);
    bool ready = bsp_audio_render_begin(&s_render, epoch, s_ch);
    if (ready && s_reference_mode) ready = bsp_audio_capture_begin(&s_capture, epoch);
    s_render_span_head = s_render_span_count = 0;
    portEXIT_CRITICAL(&s_tx_completion_lock);
    if (!ready) return ESP_FAIL;
    s_render_owned = true;
    e = i2s_channel_enable(s_tx);
    if (e == ESP_OK) e = i2s_channel_enable(s_rx);
    if (e != ESP_OK) {
        (void)bsp_audio_render_stop_output();
        s_render_owned = true;
        portENTER_CRITICAL(&s_tx_completion_lock);
        s_render.failed = true;
        portEXIT_CRITICAL(&s_tx_completion_lock);
        return e;
    }
    return ESP_OK;
}

esp_err_t bsp_audio_render_submit(uint32_t epoch, uint32_t offset,
                                  const int16_t *pcm, size_t samples) {
    if (s_diagnostic_owned || !s_render_owned ||
        (s_live_owned && (!s_live_enabled || s_live_failed))) return ESP_ERR_INVALID_STATE;
    portENTER_CRITICAL(&s_tx_completion_lock);
    bool healthy = !s_live_owned || (s_capture.active && !s_capture.failed &&
                                    s_capture.epoch == s_live_epoch);
    bool ok = healthy && bsp_audio_render_enqueue(&s_render, epoch, offset, pcm, samples);
    portEXIT_CRITICAL(&s_tx_completion_lock);
    if (s_live_owned && !ok) s_live_failed = true;
    return ok ? ESP_OK : ESP_ERR_INVALID_STATE;
}

esp_err_t bsp_audio_render_status(bsp_audio_render_status_t *out) {
    if (!out) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&s_tx_completion_lock);
    *out = (bsp_audio_render_status_t){
        .epoch=s_render.epoch, .accepted=s_render.accepted, .dma_completed=s_render.dma_completed,
        .dma_samples=s_render.dma_samples, .queued_samples=s_render.queued,
        .active=s_render.active, .failed=s_render.failed};
    portEXIT_CRITICAL(&s_tx_completion_lock);
    return ESP_OK;
}

esp_err_t bsp_audio_render_poll(bsp_audio_render_span_t *out) {
    if (!out) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&s_tx_completion_lock);
    bool available = s_render_span_count != 0;
    if (available) {
        *out = s_render_spans[s_render_span_head];
        s_render_span_head = (s_render_span_head + 1) % 32;
        s_render_span_count--;
    }
    portEXIT_CRITICAL(&s_tx_completion_lock);
    return available ? ESP_OK : ESP_ERR_NOT_FOUND;
}

esp_err_t bsp_audio_read_bounded(void *pcm, size_t bytes, size_t *received,
                                  uint32_t timeout_ms) {
    if (!received) return ESP_ERR_INVALID_ARG;
    *received = 0;
    if (s_live_owned || s_diagnostic_owned || !s_render_owned || s_reference_mode || !pcm || !bytes || bytes > 960 || bytes % 2 ||
        !timeout_ms || timeout_ms > 50) return ESP_ERR_INVALID_STATE;
    portENTER_CRITICAL(&s_tx_completion_lock);
    bool valid = s_render.active && !s_render.failed;
    portEXIT_CRITICAL(&s_tx_completion_lock);
    if (!valid) return ESP_ERR_INVALID_STATE;
    esp_err_t result = i2s_channel_read(s_rx, pcm, bytes, received, timeout_ms);
    portENTER_CRITICAL(&s_tx_completion_lock);
    valid = s_render.active && !s_render.failed;
    portEXIT_CRITICAL(&s_tx_completion_lock);
    if (!valid) {
        *received = 0;
        return ESP_ERR_INVALID_STATE;
    }
    return result;
}

esp_err_t bsp_audio_render_stop_output(void) {
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
    portENTER_CRITICAL(&s_tx_completion_lock);
    bsp_audio_render_stop(&s_render);
    bsp_audio_capture_stop(&s_capture);
    portEXIT_CRITICAL(&s_tx_completion_lock);
    /* Owner must join the bounded capture operation before this call. */
    esp_err_t first = i2s_channel_disable(s_rx);
    if (first == ESP_ERR_INVALID_STATE) first = ESP_OK;
    esp_err_t next = i2s_channel_disable(s_tx);
    if (next == ESP_ERR_INVALID_STATE) next = ESP_OK;
    if (first == ESP_OK) first = next;
    if (first == ESP_OK) s_render_owned = false;
    return first;
}

static bool audio_reference_layout_valid(i2s_chan_handle_t channel) {
    i2s_chan_info_t info;
    if (i2s_channel_get_info(channel, &info) != ESP_OK ||
        info.mode != I2S_COMM_MODE_STD || !info.mode_cfg ||
        info.total_dma_buf_size != BSP_RENDER_DESCRIPTORS * BSP_CAPTURE_FRAME_BYTES) return false;
    const i2s_std_config_t *cfg = info.mode_cfg;
    return cfg->clk_cfg.sample_rate_hz == 24000 &&
           cfg->slot_cfg.data_bit_width == I2S_DATA_BIT_WIDTH_16BIT &&
           cfg->slot_cfg.slot_mode == I2S_SLOT_MODE_STEREO &&
           cfg->slot_cfg.slot_mask == I2S_STD_SLOT_BOTH;
}

esp_err_t bsp_audio_reference_start(uint32_t epoch) {
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
    if (!epoch || !s_initialized || !s_opened || s_sleeping || s_render_owned ||
        s_reference_restore_pending) return ESP_ERR_INVALID_STATE;
    s_reference_restore_hz = s_hz;
    s_reference_restore_bits = s_bits;
    s_reference_restore_ch = s_ch;
    s_reference_restore_pending = true;
    s_reference_mode = true;
    s_render_needs_reopen = true;
    esp_err_t e = bsp_audio_set_format(24000, 16, 2);
    uint8_t route = 0;
    if (e == ESP_OK && (audio_ctrl_read(&s_checked_ctrl, 0x44, 1, &route, 1) != ESP_CODEC_DEV_OK ||
            route != 0x58 || !audio_reference_layout_valid(s_tx) || !audio_reference_layout_valid(s_rx))) e = ESP_FAIL;
    if (e == ESP_OK) e = bsp_audio_render_start(epoch);
    if (e != ESP_OK) (void)bsp_audio_reference_stop();
    return e;
}

esp_err_t bsp_audio_reference_poll(bsp_audio_capture_frame_t *out) {
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_reference_mode || !s_render_owned) return ESP_ERR_INVALID_STATE;
    portENTER_CRITICAL(&s_tx_completion_lock);
    bool failed = s_render.failed || !s_render.active || s_capture.failed;
    bool ready = !failed && bsp_audio_capture_poll(&s_capture, out);
    portEXIT_CRITICAL(&s_tx_completion_lock);
    return failed ? ESP_ERR_INVALID_STATE : (ready ? ESP_OK : ESP_ERR_NOT_FOUND);
}

esp_err_t bsp_audio_reference_stop(void) {
    if (s_live_owned && !s_live_delegating) return ESP_ERR_INVALID_STATE;
    if (!s_reference_restore_pending) return ESP_OK;
    esp_err_t e = bsp_audio_render_stop_output();
    if (e != ESP_OK) return e;
    s_reference_mode = false;
    s_render_needs_reopen = true;
    e = bsp_audio_set_format(s_reference_restore_hz, s_reference_restore_bits, s_reference_restore_ch);
    if (e == ESP_OK) s_reference_restore_pending = false;
    return e;
}

/* No diagnostic output is submitted: the preloaded/idle TX ring stays zero.
 * This requests/reads digital mute; it cannot prove acoustic silence. */
esp_err_t bsp_audio_diagnostic_begin(uint32_t epoch, bool *cleanup_required) {
    if (!cleanup_required) return ESP_ERR_INVALID_ARG;
    *cleanup_required = false;
    if (!epoch) return ESP_ERR_INVALID_ARG;
    if (s_live_owned || s_diagnostic_owned || s_render_owned || s_reference_restore_pending ||
        s_codec_release_failed) return ESP_ERR_INVALID_STATE;
    s_diagnostic_saved_volume = s_volume;
    s_diagnostic_saved_hz = s_hz ? s_hz : 16000;
    s_diagnostic_saved_bits = s_bits ? s_bits : 16;
    s_diagnostic_saved_ch = s_ch ? s_ch : 1;
    s_diagnostic_saved_mute = s_checked_mute_latched;
    s_diagnostic_owned = true;
    s_diagnostic_cleanup_failed = false;
    *cleanup_required = true;
    s_checked_mute_latched = true;
    s_volume = 0;
    esp_err_t e = bsp_audio_init();
    if (e == ESP_OK) e = bsp_audio_wake();
    if (e == ESP_OK) e = bsp_audio_set_volume_checked(0);
    if (e == ESP_OK) e = bsp_audio_set_format(16000, 16, 1);
    if (e == ESP_OK) e = bsp_audio_set_volume_checked(0);
    if (e == ESP_OK) e = bsp_audio_reference_start(epoch);
    if (e == ESP_OK) e = bsp_audio_set_volume_checked(0);
    return e; /* The caller MUST end even when this returns an error. */
}

esp_err_t bsp_audio_diagnostic_end(void) {
    if (!s_diagnostic_owned) return ESP_ERR_INVALID_STATE;
    s_checked_mute_latched = true;
    s_volume = 0;
    esp_err_t first = s_diagnostic_cleanup_failed ? ESP_FAIL : ESP_OK;
    if (!s_initialized || s_codec_release_failed) first = ESP_FAIL;
    if (s_dev && !s_sleeping && bsp_audio_set_volume_checked(0) != ESP_OK) first = ESP_FAIL;
    if (bsp_audio_reference_stop() != ESP_OK) first = ESP_FAIL;
    if (s_initialized && !s_sleeping && !s_render_owned && !s_reference_restore_pending) {
        if (bsp_audio_set_format(s_diagnostic_saved_hz, s_diagnostic_saved_bits,
                                 s_diagnostic_saved_ch) != ESP_OK ||
            bsp_audio_set_volume_checked(0) != ESP_OK) first = ESP_FAIL;
    }
    if (bsp_audio_sleep() != ESP_OK) first = ESP_FAIL;
    /* stop holds the ISR lock before clearing active; subsequent callbacks
     * cannot copy PCM. Only our owned queue is scrubbed, NOT driver RX DMA. */
    if (!s_render_owned && !s_capture.active) {
        volatile uint8_t *raw = (volatile uint8_t *)s_capture.queue;
        for (size_t i = 0; i < sizeof(s_capture.queue); ++i) raw[i] = 0;
    }
    if (!s_initialized || !s_sleeping || s_sleep_result != ESP_OK || s_dev ||
        s_render_owned || s_reference_restore_pending || s_codec_release_failed ||
        s_hz != s_diagnostic_saved_hz || s_bits != s_diagnostic_saved_bits ||
        s_ch != s_diagnostic_saved_ch) first = ESP_FAIL;
    if (first != ESP_OK) {
        s_diagnostic_cleanup_failed = true;
        return first; /* Reboot required: no silent release after a failed end. */
    }
    /* Hardware stays asleep. Restore only normal software behavior; NEVER
     * invoke an unmute or volume write here. UI state and NVS are untouched. */
    s_volume = s_diagnostic_saved_volume;
    s_checked_mute_latched = s_diagnostic_saved_mute;
    s_diagnostic_owned = false;
    return ESP_OK;
}

/* Separate inactive Live lease; construction never enables speaker output. */
esp_err_t bsp_audio_live_begin(uint32_t epoch, unsigned requested_user_volume,
                               bool *cleanup_required) {
    if (!cleanup_required) return ESP_ERR_INVALID_ARG;
    *cleanup_required = false;
    if (!epoch || requested_user_volume > 100) return ESP_ERR_INVALID_ARG;
    if (s_live_owned || s_diagnostic_owned || s_render_owned ||
        s_reference_restore_pending || s_codec_release_failed) return ESP_ERR_INVALID_STATE;
    s_live_saved_volume = s_volume;
    s_live_saved_hz = s_hz ? s_hz : 16000;
    s_live_saved_bits = s_bits ? s_bits : 16;
    s_live_saved_ch = s_ch ? s_ch : 1;
    s_live_saved_mute = s_checked_mute_latched;
    s_live_requested_volume = (uint8_t)requested_user_volume;
    s_live_epoch = epoch;
    s_live_owned = true;
    s_live_ready = s_live_enabled = s_live_failed = s_live_cleanup_failed = false;
    *cleanup_required = true;
    s_checked_mute_latched = true;
    s_volume = 0;
    s_live_delegating = true;
    esp_err_t e = bsp_audio_init();
    if (e == ESP_OK) e = bsp_audio_wake();
    if (e == ESP_OK) e = bsp_audio_set_volume_checked(0);
    if (e == ESP_OK) e = bsp_audio_set_format(16000, 16, 1);
    if (e == ESP_OK) e = bsp_audio_set_volume_checked(0);
    if (e == ESP_OK) e = bsp_audio_reference_start(epoch);
    if (e == ESP_OK) e = bsp_audio_set_volume_checked(0);
    s_live_delegating = false;
    s_live_ready = e == ESP_OK;
    s_live_failed = e != ESP_OK;
    return e;
}

static bool audio_live_reference_healthy(void) {
    portENTER_CRITICAL(&s_tx_completion_lock);
    bool healthy = s_render_owned && s_reference_mode && s_render.active &&
                   !s_render.failed && s_capture.active && !s_capture.failed &&
                   s_render.epoch == s_live_epoch && s_capture.epoch == s_live_epoch;
    portEXIT_CRITICAL(&s_tx_completion_lock);
    return healthy;
}

esp_err_t bsp_audio_live_enable_output(uint32_t epoch) {
    if (!s_live_owned || epoch != s_live_epoch || !s_live_ready ||
        s_live_enabled || s_live_failed || s_live_cleanup_failed) return ESP_ERR_INVALID_STATE;
    if (!audio_live_reference_healthy()) {
        s_live_failed = true;
        return ESP_ERR_INVALID_STATE;
    }
    s_live_delegating = true;
    /* Requested volume is an upper bound, not permission to override a
     * pre-existing checked mute/safety intent. Ordinary sleep preserves it. */
    esp_err_t e = bsp_audio_set_volume_checked(s_live_saved_mute ? 0 : s_live_requested_volume);
    /* I2C is not atomic with DMA callbacks: recheck health after register I/O.
     * This is observed digital state, never an acoustic/timing guarantee. */
    if (e == ESP_OK && !audio_live_reference_healthy()) e = ESP_FAIL;
    if (e != ESP_OK) {
        s_live_failed = true;
        (void)bsp_audio_set_volume_checked(0);
    } else s_live_enabled = true;
    s_live_delegating = false;
    return e;
}

esp_err_t bsp_audio_live_end(uint32_t epoch) {
    if (!s_live_owned || epoch != s_live_epoch) return ESP_ERR_INVALID_STATE;
    s_live_ready = s_live_enabled = false;
    s_checked_mute_latched = true;
    s_volume = 0;
    s_live_delegating = true;
    esp_err_t first = s_live_cleanup_failed ? ESP_FAIL : ESP_OK;
    if (!s_initialized || s_codec_release_failed) first = ESP_FAIL;
    if (s_dev && !s_sleeping && bsp_audio_set_volume_checked(0) != ESP_OK) first = ESP_FAIL;
    if (bsp_audio_reference_stop() != ESP_OK) first = ESP_FAIL;
    if (s_initialized && !s_sleeping && !s_render_owned && !s_reference_restore_pending) {
        if (bsp_audio_set_format(s_live_saved_hz, s_live_saved_bits,
                                 s_live_saved_ch) != ESP_OK ||
            bsp_audio_set_volume_checked(0) != ESP_OK) first = ESP_FAIL;
    }
    if (bsp_audio_sleep() != ESP_OK) first = ESP_FAIL;
    if (!s_render_owned && !s_capture.active) {
        volatile uint8_t *raw = (volatile uint8_t *)s_capture.queue;
        for (size_t i = 0; i < sizeof(s_capture.queue); ++i) raw[i] = 0;
    }
    if (!s_initialized || !s_sleeping || s_sleep_result != ESP_OK || s_dev ||
        s_render_owned || s_reference_restore_pending || s_codec_release_failed ||
        s_hz != s_live_saved_hz || s_bits != s_live_saved_bits ||
        s_ch != s_live_saved_ch) first = ESP_FAIL;
    s_live_delegating = false;
    if (first != ESP_OK) {
        s_live_cleanup_failed = s_live_failed = true;
        return first; /* Retained ownership/quarantine until reboot. */
    }
    s_volume = s_live_saved_volume;
    s_checked_mute_latched = s_live_saved_mute;
    s_live_owned = false;
    return ESP_OK;
}

#endif
