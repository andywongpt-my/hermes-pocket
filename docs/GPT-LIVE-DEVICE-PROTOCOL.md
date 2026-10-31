# Opt-in Pocket Live device stream v1

Status: offline-tested host/protocol components; **not a physical Pocket release**.
The current legacy voice implementation remains 16 kHz push-to-talk. The new
wire format is native 24 kHz mono signed PCM16 little-endian. There is no silent
16→24 kHz relabeling or fallback to ordinary Realtime.

## Authentication and startup

The optional endpoint is `GET /targets/<target>/live`, upgraded on the existing
HTTP/TLS server. The existing dedicated bearer token determines device identity;
the device ID is never accepted from message JSON. Target mapping must explicitly
supply an already-configured Live client and an echo-processor factory. No route
is enabled by default, no new listener is created, and `run_bridge` does not
discover Live credentials or enable it. TLS/certificate/hostname validation and
existing specific-interface requirements remain unchanged. Browser Origin,
query strings, request bodies and duplicate Authorization are rejected.

Each device/target has at most one admitted stream. Disconnect is terminal;
there is no reconnect, frame replay or backend task resubmission. Starting a new
conversation is an explicit owner/device action, not a reconnect strategy.

The first WebSocket message is UTF-8 JSON, at most 1,024 bytes, exactly:

```json
{"type":"start","version":1,"rate":24000,"channels":1,"format":"pcm_s16le","chat_id":"chat_one"}
```

`chat_id` uses the existing Pocket identifier validation. Duplicate JSON keys,
additional fields, non-integer version/rate/channels, and unsupported formats fail
closed. The host emits `{"type":"ready","version":1,"rate":24000}` only after
GPT-Live's `session.started`. It does not send pre-ready microphone audio to GPT-Live.

`{"type":"stop"}` ends local voice waiting. It does **not** cancel or approve a
Hermes task. Finalization confirms session closure, not device audio drain or
backend cancellation. Unconfirmed device playout remains `playback_pending`.

Before closing a usable stream, the host reports the actual outcome:

```json
{"type":"closed","finalized":true,"reason":"stopped","backend_pending":false,"playback_pending":false}
```

`reason` is restricted to the bridge's fixed lifecycle reasons, never provider
exception text. A confirmed provider finalization permits WebSocket close 1000;
an unfinalized outcome (including `stopped_before_connect`) remains close 1011.
Failures instead send `{"type":"error","code":"session_unconfirmed"}` when the
socket is still usable, then close 1011 with a fixed reason. Transport loss may
prevent either terminal message; missing status is never proof of finalization.
These flags report provider-session state and outstanding work, not successful
completion of later callback cleanup, actual speaker drain, or Hermes actions.
The optional `stream.report_outcome(outcome)` capability is deadline-bounded;
generic embedding streams still require only `recv`, `send` and `close`.

## Binary audio

Every WebSocket binary message has one packet, at most 976 bytes:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 2 | ASCII `PL` |
| 2 | 1 | Version 1 |
| 3 | 1 | Kind: 1 microphone / 2 speaker |
| 4 | 4 | Unsigned big-endian sequence |
| 8 | 4 | Unsigned big-endian sample offset |
| 12 | 4 | Unsigned big-endian actual rendered speaker samples |
| 16 | 2–960 | PCM16 little-endian payload |

Microphone payload is exactly 960 bytes (480 samples, 20 ms). Speaker payload
may be 1–480 samples. Each direction starts sequence/sample offset at zero;
packets are contiguous. Sequence and sample counters never wrap. Speaker packets
carry zero in the rendered-count field. A microphone rendered count must be
monotonic and may not exceed speaker samples successfully issued by the host.
Failed validation never advances the codec state. There is no audio history log.

The Python input queue holds eight microphone frames (160 ms). The host permits
at most 7,680 sent-but-unconfirmed speaker samples (320 ms). Excess backlog ends
the stream explicitly instead of discarding speech or growing memory indefinitely.
The underlying Live bridge retains its additional frame/byte/session bounds.

## Echo and actual playout are required, not simulated away

`DeviceAudioBridge` requires an explicit asynchronous processor:

- `capture(pcm, capture_sample, played_samples)` returns exactly one 960-byte PCM frame.
- `playback(pcm, sample_offset)` registers a speaker reference before sending.
- Optional `aclose()` transfers per-session cleanup ownership to the adapter;
  it is called under the close deadline even if the handshake fails.

These callbacks are an integration boundary, **not an AEC implementation**.
An identity function is only a synthetic test fixture. A production processor
must account for real device render timing, silence, underruns, acoustic delay,
clock drift and double-talk. Counter bounds verify consistency, not the truth of
device observations. Never claim AEC by muting microphone capture during speech.

The legacy BSP provides write acceptance and end-of-stream drain. Its old DMA
completion counter includes idle buffers. The newer default-off descriptor
ledger maps exact source/silence spans to **DMA completion**, but C3 DMA EOF is
not I2S-pin/codec playback. Its `dma_completed` counter must never be copied to
v1 `played_samples`. A real streaming worker therefore still cannot truthfully
populate this field. Neither queued bytes nor a guessed FIFO-delay subtraction
is an acceptable substitute. A true rendered-reference/timing API and physical
validation remain required before connecting a real microphone/speaker stream.

`pocket/live_echo.py` now supplies a real SpeexDSP engine and explicit-reference
adapter. It does not fabricate a reference from this protocol's cumulative
counter; there is no built-in capture-aligned physical reference owner yet.
See [AEC details](GPT-LIVE-AEC.md) and [DMA audit](GPT-LIVE-DMA-ACCOUNTING.md).

## Implementation boundaries

`pocket/live_device.py` joins the protocol to the existing `run_gpt_live` and
`HermesLiveBackend`; `pocket/live_websocket.py` provides the opt-in existing-server
upgrade. `firmware/core/pocket_live_stream.*` is a no-allocation C codec and state
validator. Native host tests do not prove ESP-IDF firmware linkage, real codec
clocks, Wi-Fi latency, heap headroom, acoustic quality or hardware acceptance.

No API key, paid model access, service/network setting, device firmware or public
repository was configured/changed by the cloud verification. Optional host
requirements are recorded separately in `requirements-gpt-live.txt`; source-only
SDK review is not an installed-SDK or paid integration test.
