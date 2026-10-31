# ES8311 paired digital-reference mode (experimental, inactive)

## Why this changes the design

Two independently observed TX/RX DMA timelines do not establish an exact sample
alignment. The pinned ES8311 driver supplies a better route on the existing codec:
**left ADC microphone + right DAC digital reference in the same stereo RX frame**.
This removes a guessed cross-timeline offset from the AEC input interface. It does
not remove codec/filter/acoustic delay, prove physical slot identity, or measure
actual audible playout. Protocol v1 must not be silently reinterpreted.

## Exact source contract

Audited `esp_codec_dev` 1.6.2 content hash:
`4779f31a3ba3f9b38aee1afe6ccb9ba8f6108dc3a6e77d39d7d714e9a215371b`.

- `device/include/es8311_codec.h:35–38` documents `no_dac_ref=false`: stereo
  recorded right channel carries DAC output; true leaves that channel empty.
- `device/es8311/es8311.c:568–572` programs ADCL+DACR using register 0x44=0x58,
  versus 0x08 for the legacy microphone-only route.
- `platform/audio_codec_data_i2s.c:set_drv_fs` derives active slot count from
  channel mask, not channel count alone. Hence **stereo/BOTH** is mandatory;
  merely setting channel=2 with the existing LEFT mask remains mono DMA.
- ESP-IDF5.5.3 `i2s_std.c` allocates based on active slots. At24kHz/16-bit/stereo,
  each configured240-frame descriptor is960 bytes, six descriptors total5760.
- `i2s_common.c:i2s_dma_rx_callback` exposes the completed DMA buffer to `on_recv`
  before queueing it. The mode copies it immediately into an owned bounded queue.

The manufacturer [product brief](https://www.everest-semi.com/pdf/ES8311%20PB.pdf)
and [Espressif-hosted datasheet](https://dl.espressif.com/dl/schematics/Audio_ES8311.pdf)
do not supply a measured acoustic delay for this board. The driver contract is
source evidence for implementing the route, not a real-device acceptance result.

## Byte and ownership contract

- Native24kHz, signed16-bit little-endian, stereo serial frames.
- Every RX frame consists of microphone LEFT then DAC-reference RIGHT: four bytes.
- One owned capture packet contains240 pairs=960 bytes=10ms.
- `bsp_audio_capture_frame_t` carries `epoch`, `sequence`, `sample_offset`,
  `dma_consumed`, and `pcm[960]`. Offset counts pairs, not16-bit words/bytes.
- `dma_consumed` is an independent observed source-credit watermark. It is neither
  a capture-clock timestamp nor actual-played. No TX epoch is guessed from it.
- TX queue accepts mono source samples. Each source sample is duplicated into
  both serial slots, but advances source counters once. A240-frame TX DMA buffer
  holds960 bytes while consuming240 source samples.
- RX owns eight copied frames (80ms), with no heap allocations in callbacks.
  Consumer polls more often than80ms; overflow ends the epoch, never drops a frame
  then pretends continuity. Source sequence/offset never wraps.
- Six RX descriptor identities must cycle in order. A >40ms callback gap also
  fails closed; it is a conservative discontinuity guard, not sample alignment
  from wall-clock time. ISR timing still requires physical stress testing.
- The driver's separate, unused RX queue naturally overflows in callback-copy
  mode. Only in this mode that **unused queue** notification is ignored. Owned
  queue overflow/descriptor mismatch/clock-gap failure remains fatal. Legacy
  bounded-read mode retains its original RX queue-overflow failure behavior.
- No `i2s_channel_read` or codec read is used alongside paired callback capture.

## Mode lifecycle and compatibility

The option remains default-off and unreachable through ordinary UI activation.
`bsp_audio_reference_start(epoch)` requires an awake/open legacy audio format and
exclusive owner. It saves that format, recreates the codec with reference enabled,
configures24k16stereo/BOTH, verifies register0x44 readback and public I2S geometry,
then starts callback-owned DMA/capture. No capture is exposed before those checks.

`bsp_audio_reference_poll` is nonblocking: OK supplies one copied frame, NOT_FOUND
means no frame ready, INVALID_STATE means failure/stopped/wrong mode. Paired mode
does not enqueue TX diagnostic spans: the owned RX reference makes that separate
timeline unnecessary. DMA ledger/credit still advances normally. Render `queued_samples` permits a sole producer to distinguish bounded
backpressure from invalid submission without dropping/replaying output.

`bsp_audio_reference_stop` invalidates capture, stops channels, recreates the codec
with `no_dac_ref=true`, and restores the previous sample format and volume. Failed
start attempts rollback; any rollback error remains observable through stop
retry/state, not success. The caller must check cleanup and leave failure inactive.

Exact codec1.6.2 close unconditionally disables its data interface. After render
has already stopped channels, its driver reports a redundant-disable error. The
checked BSP interface still invokes it (clearing its internal flags), normalizing
only that driver error when public `i2s_channel_get_info().is_enabled` first proves
the stopped-render channel is already disabled. Enabled-channel/codec failures
are not suppressed. This fixes the prior fake-boundary teardown blind spot.

## What cloud verification can and cannot establish

Native tests prove pair-byte preservation, copy ownership, bounded queues,
sequence/epoch guards, descriptor discontinuity, callback-gap failure, stereo
geometry, mono duplication, register-route readback rejection, failed-start
rollback, volume and16k microphone-only restoration. Capture core passes
ASan/UBSan. Exact firmware build establishes compilation/linkage, not acoustics.

Before physical duplex is claimed, a separately authorized offline bench check
on the existing Pocket must establish:

1. At low output level, deterministic non-speech PCM appears in the right lane;
   microphone input independently changes the left lane. Confirm polarity/scale,
   volume/mute effects and actual reference tap, with no clipping.
2. Feed the same RX-frame pairs to the supported host AEC and measure lag/ERLE,
   steady far-end residual, double-talk preservation and recovery. Keep the
   microphone live; muting it is not AEC. Choose explicit product thresholds
   before the run, not after viewing results.
3. Inject speaker gaps and bounded network stalls; reference must reflect actual
   codec input/silence and every queue/counter discontinuity must fail visibly.
4. Verify stop/restart, invalid format/readback, Wi-Fi/TLS heap pressure and
   legacy16k restoration. Report measured timing and all failures.

No bench operation, flash, service activation, key use or provider call was
performed. The minimum bench plan requires no extra hardware and can use
synthetic non-speech audio; actual-played remains unknown rather than fabricated.
