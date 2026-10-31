// components/bsp/include/bsp_audio.h
// ES8311 音频 codec:I2C 走控制口(复用 bsp_i2c 的共享总线),I2S 走全双工数据口。
#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// 初始化 codec 与 I2S。内部会调 bsp_i2c_init()(幂等),无需外部先调。成功调用可重复；
// 失败会释放本次已创建的 codec 接口和 I2S channel，修正故障后可重试。
esp_err_t bsp_audio_init(void);

// 设置采样格式。同格式重复调用是廉价的(直接复用已打开的 codec)。
//
// ⚠ 这里有个必须绕开的坑:esp_codec_dev_open() 在 codec【已打开】时会直接返回 OK 且
//   【不重新配置采样率】。若不先 close,16kHz 播完再播 8kHz 会以 16k 时钟送出 ——
//   音调和速度都快一倍。故本函数在格式变化时先 close 再 open。
// 控制口和数据口错误也会检查（不依赖 codec-dev 是否传播错误）；失败后停止
// I2S、强制休眠并释放 codec 对象，下次调用重建，不会复用半打开状态。
// 调用方须串行化格式/休眠/唤醒操作，且切换前停止所有 PCM 读写。
esp_err_t bsp_audio_set_format(uint32_t hz, uint8_t bits, uint8_t ch);

// 进入低功耗前强制执行 ES8311 完整 suspend 寄存器序列，回读关键寄存器
// （REG0E 只比较 bit6:0，其余寄存器比较全部位），
// 失败时重试一次，并显式停止 I2S TX/RX。该路径不依赖 codec-dev 是否打开过，
// 因此开机后从未播放也能正确暂停。调用前必须停止所有 PCM 读写；函数幂等，
// 音频未初始化时视为无需处理并返回成功。失败后的重复调用保留失败结果。
// 休眠时释放 codec 对象但保留共享 I2C 总线和 I2S channel；不依赖 codec
// 内部 enabled 标志恢复，唤醒会重新创建 codec 并恢复音量/格式。
esp_err_t bsp_audio_sleep(void);

// deep sleep 专用：确保 I2S TX/RX 已停止，再将 MCLK/BCLK/WS/DOUT/DIN
// 设为无上下拉的高阻输入。调用后 I2S 不能在本次运行中恢复，必须立即
// 进入 deep sleep 或重启；不得用于 light sleep。
esp_err_t bsp_audio_prepare_deep_sleep(void);

// 从 light sleep 返回后恢复 ES8311 和休眠前的采样格式。函数幂等；deep sleep
// 唤醒会重启应用，应由 bsp_audio_init() 按正常启动流程重新初始化。
esp_err_t bsp_audio_wake(void);

// 播放 / 录音。bytes 为字节数(16bit 单声道时 = 采样数 x 2)。
esp_err_t bsp_audio_write(const void *pcm, size_t bytes);
// After the final successful write, wait for a complete TX DMA ring plus one
// guard completion. Serialize with writes/format/wake/sleep using the same
// audio owner. Does not mute, stop, close, or change volume. Timeout is rounded
// down to whole RTOS ticks; closed/asleep returns ESP_ERR_INVALID_STATE and a
// stopped/nonadvancing TX returns ESP_ERR_TIMEOUT. Use only at successful EOF.
esp_err_t bsp_audio_drain(uint32_t timeout_ms);
// Same deadline/completion contract, with an optional fast cancellation check
// in task context at entry and every wait tick, before success or timeout.
// Cancellation returns ESP_ERR_INVALID_STATE; the audio owner handles mute and
// sleep. The predicate is never called by the I2S ISR.
esp_err_t bsp_audio_drain_cancel(uint32_t timeout_ms,
                                 bool (*cancelled)(void *), void *context);
esp_err_t bsp_audio_read(void *pcm, size_t bytes);

// 输出音量 0..100(%)。
void bsp_audio_set_volume(uint8_t percent);
/* Checked bench/control variants, single audio owner only. Available after init
 * before format-open while awake. Percent is0..100 (rejects rather than clamps).
 * Volume0 asserts and reads real ES8311 mute; positive volume is programmed and
 * hardware-readback verified while muted, then explicitly unmutes. Real register
 * checks do NOT prove acoustic silence/SPL; PA pin=-1 provides no hardware kill.
 * Failure leaves a requested mute latch and zero future volume, but I2C failure
 * can prevent actual mute: caller must abort playback and check cleanup errors.
 * Mute latch survives codec recreation; legacy void setter remains unchecked.
 */
/* Before first init only: latch zero/mute intent with NO hardware-I/O or muted
 * assurance. Call checked0 after init and after each format/reference start. */
esp_err_t bsp_audio_prepare_silent_start(void);
esp_err_t bsp_audio_set_volume_checked(uint8_t percent);
esp_err_t bsp_audio_set_mute_checked(bool muted);

/* Experimental, default-off callback-owned digital DMA render mode.
 * Serialize start/stop/format/volume against all I/O; join prior readers/writers.
 * Start requires open native 24k/16/mono. Stop keeps channels stopped: call sleep
 * then wake (or set format, even same format) before ordinary codec I/O. No pin
 * serialization, analog playout or microphone alignment is established here.
 * dma_completed must NOT be passed as protocol-v1 actual played_samples.
 */
#include "sdkconfig.h"
#if CONFIG_BSP_AUDIO_CALLBACK_RENDER
#include "bsp_audio_render.h"
#include "bsp_audio_capture.h"
typedef struct {
    uint32_t epoch, accepted, dma_completed, dma_samples, queued_samples;
    bool active, failed;
} bsp_audio_render_status_t;
esp_err_t bsp_audio_render_start(uint32_t epoch);
esp_err_t bsp_audio_render_submit(uint32_t epoch, uint32_t offset,
                                  const int16_t *pcm, size_t samples);
esp_err_t bsp_audio_render_status(bsp_audio_render_status_t *out);
/* Drain at least every 320ms; overflow stops submission/refill, marks failed.
 * Each successful callback reports a 240-frame DMA interval; source_samples
 * describes its initial speech prefix, remainder silence. Retain every span.
 */
esp_err_t bsp_audio_render_poll(bsp_audio_render_span_t *out);
/* Native mono PCM; <=960 bytes, 1..50ms driver wait, caller handles short reads.
 * Join this read before stop. Driver mutex/teardown timing still needs hardware
 * acceptance; this is not a claim of hard real-time shutdown guarantees.
 */
esp_err_t bsp_audio_read_bounded(void *pcm, size_t bytes, size_t *received,
                                  uint32_t timeout_ms);
esp_err_t bsp_audio_render_stop_output(void);
/* Isolated v2 reference mode: recreates codec for native24k16 stereo/BOTH,
 * micL + digital DACrefR, validated REG44 and public I2S layout. Requires an
 * open/awake legacy format; stop restores that format and no_dac_ref=true.
 * This is paired digital reference, not a claim of zero acoustic/filter delay.
 * Single owner, no legacy reads; drain owned capture queue every <80ms.
 * Paired mode does not enqueue unused TX spans. Failure must stop; no restart.
 */
esp_err_t bsp_audio_reference_start(uint32_t epoch);
esp_err_t bsp_audio_reference_poll(bsp_audio_capture_frame_t *out);
esp_err_t bsp_audio_reference_stop(void);

/* SILENT offline diagnostic bracket, exclusive existing audio-owner task only.
 * begin acquires cleanup_required BEFORE I/O; call end whenever it is true,
 * including failed begin. No writes/submissions or positive volume/unmute are
 * permitted during the lease. Poll paired capture only. No task lock invented.
 * end restores prior cached format (cold fallback16k/16/mono), verifies sleep,
 * then restores prior cached volume
 * and mute intent WITHOUT hardware unmute; UI/NVS remain unchanged. Only end
 * ESP_OK permits normal audio again. Failed end retains ownership until reboot.
 * Mute readback is not acoustic silence/SPL proof. Owned capture queue is
 * scrubbed on stop; driver DMA storage can retain temporary microphone PCM.
 */
esp_err_t bsp_audio_diagnostic_begin(uint32_t epoch, bool *cleanup_required);
esp_err_t bsp_audio_diagnostic_end(void);

/* Inactive Live lifecycle, distinct from the strict silent diagnostic lease.
 * Exclusive audio-worker ownership is REQUIRED: delegation is not a mutex or
 * caller identity. Freeze requested user volume0..100, no UI/NVS changes.
 * begin always remains checked-muted; cleanup_required is acquired BEFORE I/O
 * and requires matching-epoch end even when begin fails. A separate explicit
 * enable_output is required before the first speaker submission; no automatic
 * unmute on construction/admission. Requested volume is only an upper bound: zero volume or a pre-existing
 * checked mute intent remains checked-muted; enable never overrides that intent.
 * Direct legacy/control/reference-stop APIs cannot bypass the Live lease.
 * end restores prior format (cold fallback16k16mono), verifies sleep, then
 * restores software-only volume/mute intent without hardware unmute. Any end
 * failure retains quarantine until reboot; wrong epochs cannot end a lease.
 * Epochs must be unique across caller sessions to reject stale same-value work.
 * Register checks are not acoustic/SPL or physical full-duplex acceptance.
 * Owned capture queue is scrubbed after stop, not driver DMA storage.
 */
esp_err_t bsp_audio_live_begin(uint32_t epoch, unsigned requested_user_volume,
                               bool *cleanup_required);
esp_err_t bsp_audio_live_enable_output(uint32_t epoch);
esp_err_t bsp_audio_live_end(uint32_t epoch);

#endif
