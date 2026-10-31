# Experimental callback-owned DMA accounting

> These are v1 DMA diagnostic contracts. The newer [paired-reference mode](GPT-LIVE-PAIRED-REFERENCE.md) obtains its reference directly from both RX lanes, and uses DMA completion only as explicit v2 buffer credit. Neither mode proves acoustic playout.

This change accounts the source-code gap between accepted PCM and **completed
DMA source samples**; it does not close the actual-rendered-ACK release blocker. It does not prove analog speaker position, echo cancellation,
shared capture/render epoch, hardware safety, or a working physical Live session.
`CONFIG_BSP_AUDIO_CALLBACK_RENDER` defaults **off**; no UI starts it.

## Audited dependencies

- ESP-IDF 5.5.3 `components/esp_driver_i2s/i2s_common.c`,
  `i2s_dma_tx_callback`: the public `on_sent` event exposes the buffer address and
  byte count of the descriptor that just completed. The callback may refill that
  buffer; `auto_clear_before_cb=true`, `auto_clear_after_cb=false` preserves the
  refill for the next traversal. No private descriptor fields are accessed.
- `i2s_std.c` sets `active_slot=1` for mono; `i2s_get_buf_size` uses that active slot
  count. At 16-bit mono, the configured 240 frames consume 480 bytes.
- Exact registry `esp_codec_dev` 1.6.2, component content hash
  `4779f31a3ba3f9b38aee1afe6ccb9ba8f6108dc3a6e77d39d7d714e9a215371b`,
  `platform/audio_codec_data_i2s.c`: `set_drv_fs` maps the BSP mono/channel-mask-bit-0
  selection to one active mono slot; data writes call `i2s_channel_write` directly,
  with no mono-to-stereo conversion. The BSP actually supplies channel-mask bit 0
  (value 1), so the selected slot is LEFT, not an unspecified zero mask.
- ES8311 exposes hardware `set_vol`. BSP does not install a software volume
  processor; direct DMA submission does not silently bypass one here. Future
  installation of a codec software-volume handler requires reconsidering this API.

## Ownership and truthful counters

`bsp_audio_render_start(epoch)` requires native 24000 Hz/16-bit/mono already open.
The single audio owner must have joined all legacy readers and writers. Start
stops RX then TX, preloads the entire ring with zeros, creates a fresh ledger,
and re-enables TX then RX. Epoch must be nonzero and unique for the session.

Every completed buffer is identified by its public address. The first traversal
learns exactly six unique addresses; later traversals must repeat their order.
Each descriptor remembers the exact source offset and sample count placed in it.
Only its **next completion** advances `dma_completed`. Initial/underrun silence advances
only `dma_samples`, not `dma_completed`. Partial descriptor speech is followed by explicit
zeros. Replayed/skipped/unexpected descriptors, counter overflow and bad DMA sizes
fail closed. Queue capacity is fixed at 1,920 mono source samples (80 ms at 24 kHz).
Submit never blocks or allocates, and `accepted` is never reported as `dma_completed`.

The ISR uses an internal-RAM state and a single ISR-safe critical section; its
render helper is IRAM annotated on ESP builds. No logging, allocation, codec I/O,
or waits occur in the callback. Only this callback may write TX DMA in render mode.
The opt-in Kconfig selects `I2S_ISR_IRAM_SAFE` and compilation rejects a
non-IRAM-safe driver configuration. Actual ISR execution time/cache behavior
remains a build/hardware review gate.

`bsp_audio_render_poll` drains a fixed 32-entry completion-span ring. Each span
covers 240 DMA frames and carries epoch, DMA start, source start, and source prefix
length; remaining frames are silence. If the consumer fails to drain it, overflow
stops new refills and sets failure instead of losing alignment silently. Consumer
must poll more often than 320 ms. Call `status` to see failure/active flags.

`bsp_audio_read_bounded` uses public `i2s_channel_read`, accepts at most 960 bytes
and a 1–50 ms wait. The owner must handle short reads and finish any read before
stop. RX queue overflow marks the session failed; bounded reads check failure
both before and after the driver call, never passing silently dropped capture
as contiguous PCM. Stop invalidates the ledger and disables RX then TX. Queued/uncompleted
samples are **not** acknowledged. Legacy read/write remain rejected until an
explicit format reopen or sleep/wake; same-format reopen is forced after render.
Failed starts retain exclusive ownership until explicit stop, preventing silent
fallthrough to stopped/partly restarted legacy channels.

## Remaining acceptance gates

1. Completion is at the I2S DMA boundary, not proof of I2S pins or analog DAC/speaker output. Codec
   C3 `gdma_struct.h` describes TX EOF mode as data popped from the DMA FIFO;
   `i2s_common.c` uses GDMA `on_trans_eof`, not a codec or I2S serializer-drained
   event. Downstream I2S FIFO and serializer delay is not yet bounded here.
   [ESP32-C3 TRM v1.4](https://documentation.espressif.com/esp32-c3_technical_reference_manual_en.pdf)
   section 29.4 (printed page 732) specifies a 64×32-bit I2S TX FIFO; the GDMA
   EOF register description (printed page 81) confirms that GDMA completion does
   not itself certify downstream serialization. Section 29.9.1 separates DMA-to-FIFO transfer, data conversion and serial
   clock-out. A complete extra ring is a plausible conservative delay, but the
   audited material does not establish total staging/serializer capacity or a
   direct exact pin position. Protocol v1 also describes an actual position, not
   a lower-bound watermark. No guessed lag subtraction is used.
   **Never pass `dma_completed` as protocol v1 `played_samples`.** Codec
   latency and acoustic propagation need measurement, not a fabricated offset.
2. Span time zero is the first TX completion, **not a shared RX start epoch**.
   RX enable is sequential and capture may have buffered/dropped DMA. A consumer
   must not infer echo-reference timing from cumulative played samples alone.
3. Source queue/spans have no wire encoding in the existing version-1 protocol.
   Transport and supported AEC must preserve silence/underrun placement and align
   to capture before any honest full-duplex activation.
4. Fixed six-buffer order is audited against the pinned driver but needs physical
   stress validation. Entire-ring IRQ loss could evade order checks; no hard
   real-time guarantees, ISR latency limits or undetected-loss bounds are claimed.
5. Stop calls public driver disable after the owner joins bounded reads. Driver
   mutex/interrupt teardown timing has not been proven on hardware.
6. Native clocks, concurrent TLS/audio heap, codec register behavior, speaker
   volume, echo/double-talk, jitter, Wi-Fi stalls, buttons and teardown remain
   physical acceptance gates. Do not flash or enable services on test results.

## Offline evidence

`tests/test_firmware_render_ledger.py`: real C descriptor core, literal PCM,
acceptance-vs-completion, silence, queue bounds, stop, epochs, unexpected layout,
descriptor loss/replay, source counter overflow.

`tests/test_firmware_render_bsp.py`: actual BSP against fake peripheral boundary,
opt-in flags, public DMA callbacks, mono layout, finite capture wait, failure
ownership, forced same-format reopen, source/silence spans and event overflow.

Both pass GCC with `-Wall -Wextra -Werror -pedantic`. Ledger passes ASan+UBSan
(LSan disabled under sandbox ptrace). Twelve existing audio characterization and
legacy drain tests pass with exact codec headers. These are **not hardware tests**.
Build and aggregate-suite results belong in the enclosing checkpoint report.
