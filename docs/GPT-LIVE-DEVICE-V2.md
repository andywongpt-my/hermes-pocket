# Explicit paired-reference device protocol v2

**Source/offline candidate, not physical-device acceptance.** This profile uses
existing ES8311 hardware functionality; it is not ordinary Realtime and it does
not change the GPT-Live model or Hermes client-delegation architecture.

## Why v2 exists

Protocol v1 calls its acknowledgment `played_samples`. ESP32-C3 DMA EOF cannot
prove audible playout, and reconstructing a render reference from that count
loses silence placement and capture alignment. Those meanings are not changed.

Exact esp_codec_dev1.6.2 instead documents a two-channel capture mode with left
microphone and right DAC output (`no_dac_ref=false`, ADCL+DACR, register0x44=0x58).
The isolated BSP mode captures both values in each RX stereo frame. This is a
co-captured digital reference, not guessed alignment between separately enabled
TX and RX streams. The adaptive filter still models the codec/room echo path;
physical lane order, sample clock, gain, polarity and useful cancellation require
bench acceptance. The DAC digital reference is not proof of audible playback.

## Admission and exact controls

Use the same optional authenticated existing-server endpoint:
`/targets/<target>/live`. Bearer identity, TLS/hostname requirements, target/device
admission, Origin/query/duplicate-header rejection and1024-byte message limit are
unchanged. There is no new listener or automatic activation.

The endpoint owner must explicitly supply a fresh `PairedSpeexEcho` per session
from its factory. This processor supports v2 only; a mismatched v1 processor or
metadata fails before the paid Live connection. No protocol fallback is attempted.

Start is exactly:

```json
{"type":"start","version":2,"rate":24000,"channels":2,"format":"pcm_s16le","reference":"es8311_dac_right","chat_id":"chat_one"}
```

After provider readiness the host emits exactly:

```json
{"type":"ready","version":2,"rate":24000,"reference":"es8311_dac_right"}
```

Stop remains `{"type":"stop"}`. It never means cancelling Hermes work.
A known-v2 closed report adds an explicit unknown acoustic position:

```json
{"type":"closed","finalized":true,"reason":"stopped","backend_pending":false,"playback_pending":true,"actual_playout":"unknown"}
```

After any speaker packet was sent, `playback_pending` remains conservative even
if all digital DMA credit arrived. No output at all can leave it false; actual
playout is still unknown. Finalized describes provider closure, not hardware
cleanup or audible drain. Generic errors/connection loss remain unconfirmed;
pre-handshake closure cannot know the requested protocol profile.

## Binary frames — still at most976 bytes

The16-byte header is ASCII PL, version byte2, kind byte, then unsigned BE32
sequence, sample_offset and dma_consumed. Counters start at zero per connection,
are contiguous/monotonic as appropriate and never wrap. v1 packets are rejected.

- Kind1 capture payload: exactly960bytes =240interleaved pairs of signed PCM16LE,
  order microphone-left, DAC-reference-right. This is10ms at24kHz. Capture sample
  offset advances240, **not480**. Both channels always share the same RX frame.
- Kind2 speaker payload:2..960even bytes =1..480mono source samples, PCM16LE.
  Offset advances source sample count. Its dma_consumed field must be zero.
- Capture dma_consumed is the source-mono sample count observed at completed TX
  descriptors. It is only buffer credit, bounded by speaker samples issued by
  the host. It must not be presented as actual pin/DAC/acoustic position.

The BSP duplicates each mono speaker sample into both stereo TX slots. Its RX
callback copies the complete frame into owned storage before returning; no task
keeps a pointer into reusable DMA. Overflow/descriptor discontinuity/epoch loss
fails the session rather than silently dropping or replaying microphone audio.
Standard `i2s_channel_read` is not used in paired mode because the pinned driver
can abandon a partly consumed buffer near queue saturation.

## Host AEC and bounds

`PairedSpeexEcho` deinterleaves one pair frame and invokes the real SpeexDSP1.2.1
public synchronous AEC API at240samples/24kHz, with a4800-sample echo tail. Only
240processed mono samples (480bytes) enter GPT-Live. The reference channel is
never forwarded as microphone input. There is no mute gate or no-AEC fallback.

The host capture queue holds8paired frames (80ms,7680PCM bytes). Speaker credit
window is1920source samples (80ms). A larger provider chunk is split and waits
for capture-carried DMA credit; the per-chunk wait is cancellation-aware and
bounded to1second. Missing progress fails explicitly, not unbounded buffering.
Transport sends/counter updates share a lock with credit validation. Credit wait
happens outside that lock so capture can progress. Existing Live-session lifetime,
provider frame/byte limits and task ownership remain in force.

See `GPT-LIVE-PAIRED-REFERENCE.md` for BSP ownership/restoration,
`GPT-LIVE-PAIRED-BENCH-PLAN.md` for separately authorized physical validation,
and the latest checkpoint results for actual build/test scope. No key, provider
call, service setting or device firmware is changed by importing these modules.
