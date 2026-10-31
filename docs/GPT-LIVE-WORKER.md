# Inactive Live worker: source verification only

> This document describes the v1 worker. The separately implemented v2 paired-reference worker is described in [paired worker notes](GPT-LIVE-PAIRED-WORKER.md); it has its own physical-validation gate and does not reinterpret v1 played acknowledgments.

**The physical device entry always returns `ESP_ERR_NOT_SUPPORTED`.** Turning on
`CONFIG_POCKET_LIVE_WORKER_COMPILE` does not activate it. No UI/button path calls
it, and no configuration value bypasses the refusal. Current Pocket voice remains
16 kHz half duplex. Neither a compiled object nor fake-I/O tests establish usable
full duplex, real TLS traffic, microphone clocks, acoustics or paid-provider access.

## Implemented components

- `pocket_live_worker.{h,c}`: allocation-free, single-owner streaming pump using
  supplied I/O operations, native 24 kHz PCM16, existing bounded client/FIFO and
  independent DMA playback. Captures partial reads into exactly 480-sample frames;
  requires separately measured render position, rejects impossible positions,
  and never derives rendering from submission or elapsed time.
- `pocket_live_control.{h,c}`: bounded flat cJSON validation of exact ready, closed
  and error controls. Rejects duplicate/extra keys, non-integer rate/version syntax,
  nesting, NUL, escaped-string aliases, unknown reasons and nonboolean status.
  The canonical server-generated controls contain no escaped keys/values.
- `pocket_live_gate.c`: tested public entry refusing real-device execution.
- `pocket_live_idf.c`: **unexposed, compile-checked prototype operations**, not a
  usable runtime adapter. Reuses pinned IDF 5.5.3 TLS/WebSocket and BSP APIs, with
  certificate/hostname validation, dedicated device bearer token, exact target
  path, current-boot clock check, and no redirect/reconnect/retry/replay.

The pump starts audio only after validated readiness. Stop prevents subsequent
capture admission (including stop observed during receive/capture), stops audio,
then requests host finalization. In-flight speech after stop remains pending,
including when a later host report says its own playback queue is empty. Unknown
backend state is explicit: **`terminal_received=false` means no authoritative
backend-pending report was received**, not that Hermes has no task. Stopping or
provider finalization never cancels a Hermes task.

Startup/lifetime/finalization checks are 10 seconds / 300 seconds / 3 seconds in
the generic pump. These are conditional software deadlines: an operation that
blocks outside its contract cannot be preempted by this single-owner pump.
Failed partial audio setup is cleaned; unsuccessful cleanup is reported and must
not be treated as released hardware ownership. The worker's caller-owned memory
has a compile-time 7,600-byte upper bound, excluding codec/TLS/driver allocations.

## Why the IDF prototype cannot be exposed

1. **GDMA completion is not physical playout.** BSP `dma_completed` tracks source
   samples delivered through completed GDMA descriptors; downstream I2S FIFO,
   serializer and codec delay are not audited into a truthful played position.
   The prototype render callback explicitly fails instead of mapping that count
   to protocol v1 `played_samples`.
2. **No synchronized microphone/reference transport.** Render spans carry a DMA
   timeline, but its origin is not synchronized to microphone sample zero. The
   prototype retains only 32 spans, then fails closed; it does not discard them
   and invent alignment. The cumulative v1 ACK is not an AEC reference timeline.
3. **End-to-end cancellation timing is not proven.** Post-handshake TLS sockets
   are explicitly made nonblocking through the public socket API; failed flag
   configuration fails closed. Individual poll arguments are not proof of a
   whole-operation bound. DNS lookup, repeated handshake reads, SDK scheduling,
   I2S mutexes and teardown still need bounded ownership/cancellation design and
   hardware acceptance. No bounded Stop/join claim applies to this IDF prototype.
4. **RX overflow must be terminal.** The new BSP's overflow latch is checked
   before/after bounded reads. Wi-Fi stalls can exceed audio buffering; dropped
   PCM cannot silently become contiguous samples. Memory/headroom under actual
   simultaneous TLS, audio and UI work remains unverified.

The pinned SDK can log an authenticated Upgrade request on a failed header write.
The opt-in CMake path compiles `tcp_transport` with `LOG_LOCAL_LEVEL=ESP_LOG_NONE`;
application-only log hygiene or mutable runtime tag settings would not be enough.
No running service/logging configuration was changed.

The adapter first polls before reading: idle poll returns no event, whereas a
readable header whose first payload read times out is terminal, because IDF can
reset its internal frame state. It rejects fragmented application messages rather
than attempting unsafe reconstruction; partial reads within one final frame are
bounded to 1,024 bytes. Real-server fragmentation/handshake/TLS acceptance is not
established by the fake-public-boundary regression.

## Verification

Run from the source root, with exact pinned cJSON source available:

```sh
POCKET_CJSON_DIR=/path/to/esp-idf/components/json/cJSON \
python -m unittest tests.test_firmware_live_worker \
  tests.test_firmware_live_control tests.test_firmware_live_gate \
  tests.test_firmware_live_idf_adapter -v
```

15 focused tests passed in the October 29 source run. They cover actual C pump,
controls and refusal gate, plus actual IDF-adapter source against fake public
boundaries. They are not network/hardware tests. Red/green receipts are retained
under the checkpoint's `verification/worker/` directory. Aggregate repository and
real ESP-IDF build results are recorded separately by the checkpoint owner.

For compile-only verification select both `CONFIG_BSP_AUDIO_CALLBACK_RENDER=y`
and `CONFIG_POCKET_LIVE_WORKER_COMPILE=y` in a separate build configuration. Both
are off by default. Never interpret these selections as release authorization.
