# Paired-reference v2 implementation plan

> Delegated continuation: source, public dependency audit, offline tests/build only. No flash, hardware, key, provider call, deployment or service changes.

**Goal:** Replace the unresolved guessed TX/RX reference alignment with ES8311's documented co-captured microphone/DAC-reference mode, while preserving honest unknown actual playout and legacy behavior.

**Architecture:** Exact codec-dev1.6.2 supports `no_dac_ref=false`, ADCL+DACR, register0x44=0x58. Isolated native24k16 stereo/both-slot mode returns micLEFT and internalDACrefRIGHT in one RX frame. ISR copies each240-pair frame into an owned bounded FIFO; no borrowed DMA pointer or lossy standard read path. Explicit wirev2 transports those pairs; host deinterleaves and processes real SpeexDSP240-sample frames. DMA-consumed source counts are buffer credit only. Actual acoustic playout remains unknown.

**Alternatives rejected:** Guessing cross-TX/RX epochs or interpreting DMAEOF as audible samples is unsupported. Mic muting violates duplex. New hardware is unnecessary for the source-documented internal reference route. This direction follows the parent's request to implement viable cloud-verifiable work and isolate physical measurements.

## Exact protocol and constraints

- Header remains16bytes: PL,version2,kind1mic-pairs/2speaker,BE32sequence,BE32sample_offset,BE32dma_consumed.
- Capture payload exactly960bytes=240interleaved mic/ref PCM16LE pairs (10ms). Capture offset advances240, not480.
- Speaker payload2..960even bytes=1..480mono samples. Its credit field is zero. TX duplicates each mono source sample to both hardware slots.
- Start keys exactly type/start,version2,rate24000,channels2,format/pcm_s16le,reference/es8311_dac_right,chat_id.
- Ready keys exactly type/ready,version2,rate24000,reference/es8311_dac_right.
- Terminal closed adds `actual_playout:"unknown"`; successful digital credit never clears pending after any speaker output.
- Existing v1 codec/start/control and legacy16k remain unchanged; cross-version packets fail.
- Explicit native dependency and per-session owned cleanup. No generic reference callback is needed for paired v2; no right-channel data goes to GPT-Live as microphone audio.
- Mode teardown must restore no_dac_ref=true and legacy format, including actual codec unconditional data-interface disables.
- No drop/replay/reconnect, silent FIFO overflow or guessed lane fallback. Physical slot mapping/clock/acoustic behavior still requires separate authorization and acceptance.

## Tasks

- [ ] Host v2 codec/bridge and terminal schema: red tests for pair units, credit bounds, cross-version rejection, exact metadata, processed-mic-only output and unknown playout; implement and run v1 regression suite.
- [ ] Native240sample Speex engine and paired adapter: red tests for actual deinterleaving, synthetic echo/double-talk, malformed input, continuity and lifetime; verify real pinned library.
- [ ] BSP isolated paired mode/callback-copy FIFO and actual-codec restore regression: test source layout, owned-copy/reuse, epochs/overflow, no standard RXread, legacy restoration; implement no-allocation APIs.
- [ ] Separate C v2 codec/worker/control plus supported-API bounded network ownership prototype, with deadline/latecallback/stop tests and no key logging. Keep public activation gate while acceptance is incomplete.
- [ ] Independent review, final complete suite and exact default/experimental ESP-IDF builds. Report all failures/skips; isolate minimum no-key existing-hardware experiment without executing it.
- [ ] Preserve prior audio checkpoint, reconstruct/verify new delta and save separate source-only continuation checkpoint.
