# Cloud Live continuation implementation plan

> **For agentic workers:** Use superpowers:executing-plans. The delegated user request authorizes local implementation and offline tests, not deployment, credentials, paid calls, publication or flashing.

**Goal:** Correct cloud/Linux host defects and add a bounded opt-in device streaming protocol/host adapter without altering legacy voice.

**Architecture:** Keep gpt-live-1 and Hermes client delegation. Introduce a pure binary wire codec shared by Python and C and a caller-authenticated async stream adapter; the adapter requires an explicit echo processor and fails before connecting when none is configured. Native 24 kHz is a prospective device mode, not a claim about current firmware.

**Tech Stack:** Python 3.12, websockets 16 Sans-I/O, host C/GCC, existing caller-provided Live SDK.

**Spec:** Existing `docs/superpowers/specs/2026-10-29-gpt-live-design.md`, extended only for isolated opt-in stream components in this plan; legacy HTTP voice/UI firmware remains unchanged; the later authorized extension adds an opt-in route on the existing listener.

## Global constraints

- Preserve exact gpt-live-1 and Hermes task/journal/approval semantics.
- No credential discovery, API calls, listener/startup/configuration changes or hardware operations.
- Existing 16 kHz push-to-talk is unchanged; raw 16 kHz may not enter the Live interface.
- No microphone muting passed off as echo cancellation; require a supplied supported AEC implementation.
- No automatic reconnect or replay. Explicit native 24 kHz, mono PCM16, 20 ms microphone frames.

## Review focus

- Malformed, replayed, out-of-order and overflowing frames fail closed.
- Playback acknowledgments never acknowledge audio that was not sent.
- Stop/disconnect and asynchronous exceptions cannot leave unbounded application tasks.
- Missing AEC, wrong format or unauthenticated caller identity must fail before paid connection.
- Slow playback/capture cannot create unbounded buffers; exact PCM crosses independent direction boundaries.

## Task 1: Existing host/Linux defects

- [x] Verify source archive and all 345 manifest entries.
- [x] Reproduce SDK policy ambiguity and Python 3.12 microphone starvation with bounded regressions.
- [x] Explicitly disable reconnect and yield between microphone frames; verify targeted suite.
- [x] Correct GCC-only embedded test harness indentation without suppressing warnings.

## Task 2: Shared stream codec

Files: `pocket/live_device.py`, `firmware/core/pocket_live_stream.h/.c`, `tests/test_live_device.py`, `tests/test_firmware_live_stream.py`.

Wire format: 16-byte header: ASCII PL; version byte 1; kind byte 1 microphone / 2 speaker; uint32 BE sequence, sample_offset, played_samples; followed by even PCM16 LE bytes, 2..960. Microphone payload exactly 960 bytes. Sequence and sample offset begin at zero and are contiguous. Speaker played_samples field must be zero. A microphone acknowledgment is monotonic and never greater than the number of speaker samples issued. Counters never wrap.

- [x] Write/run failing literal encode/decode, malformed-size, continuity, overflow and acknowledgment tests.
- [x] Implement bounded Python and no-allocation C codec/state validation.
- [x] Run both suites and cross-language literal vectors.

## Task 3: Opt-in host device adapter

File: `pocket/live_device.py`; tests: `tests/test_live_device.py`.

Interface: `run_device_gpt_live(client, service, device, stream, stop, *, echo, settings=None)` receives a trusted authenticated device identity and an asynchronous transport (`recv`, `send`, `close`). The first message is bounded JSON start metadata with version=1, rate=24000, channels=1, format=pcm_s16le and a validated chat_id. The echo processor exposes `capture(pcm, capture_sample, played_samples)` and `playback(pcm, sample_offset)`; actual playback reference/timing remains its responsibility. Neither wire support nor a synthetic processor establishes production AEC.

- [x] Test wrong format/missing processor/no-start fail before connection, concurrent audio/delegation/stop, chunk splitting, bad counters and backpressure.
- [x] Implement adapter using `run_gpt_live` and `HermesLiveBackend`, retaining explicit bounded shutdown.
- [x] Run complete project suite and simulator under no-external-network guard; document all skips.
- [x] Obtain independent review and produce source-only checkpoint with exact limitations.

## Authorized continuation: route and firmware prerequisites

Ruling: parent explicitly requested continuing local transport/firmware integration,
without deployment or flashing. The optional route reuses the existing authenticated
TLS server; it does not create another listener or auto-load a credential.

- [x] Add optional target-to-LiveEndpoint mapping and same-server WebSocket upgrade.
- [x] Test authentication before upgrade/provider, browser Origin, duplicate headers,
  body/query rejection, prefetched bytes, fragmentation, ping, limits, concurrent
  admission, shutdown and cancellation-safe writes.
- [x] Report bounded terminal outcome before close; normal close requires confirmed
  provider finalization. Unconfirmed/error outcomes are never represented as success.
- [x] Add inactive C handshake/lifecycle/four-frame FIFO core, with separate driver
  submission vs independently measured render counters.
- [x] Characterize actual BSP native24k configuration through fake I2S/codec boundaries;
  prove raw idle DMA completions cannot count as rendered speech.
- [x] Independent host/route/client review and red→green regression fixes.

## Deferred, not claimed complete

Firmware streaming worker/UI activation, supported AEC integration, exact pinned
codec/firmware build, physical duplex acoustics/memory/timing and paid account/model
acceptance remain open. The current public BSP cannot map actual rendered speech
samples to DMA completion; its completion counter includes idle/zero data. Fabricating
acknowledgments from write acceptance or elapsed time would be incorrect. Therefore
no worker was wired into existing firmware or CMake. A future audited public render
API must retain descriptor/source-offset association and format/underrun/cancel epochs.

Rulings: native24k avoids a guessed resampler path but remains hardware-unverified;
echo processing is mandatory and not replaced by microphone muting; missing procfs
containment causes tests to fail closed, not a relaxed process-security path. These
limits are called out in the final status, not hidden by focused passing tests.
