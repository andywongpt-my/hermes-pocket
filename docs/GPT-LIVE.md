# GPT-Live host integration

This is a locally implemented host component for **GPT-Live (`gpt-live-1`)** with Hermes client delegation. It is not an ordinary Realtime adapter. It has not been installed in a running service, connected to OpenAI, or flashed to Pocket. No real key has been requested, stored, configured or used.

## Architecture

`pocket/gpt_live.py` handles session startup/readiness, continuous bidirectional PCM audio, transcript context, client delegations, bounded queues, stop/finalization and the documented Live SDK helpers. The prompt uses GPT-Live's documented `<|SpawnThinking|>` handoff policy. `pocket/live_hermes.py` forwards delegated context through the existing Pocket `ChatService` and `Service` to Hermes. Hermes retains its configured default model, task journal, chat history, memory/tools available in that backend, and approval behavior.

Delegation metadata contains no utterance. The bridge derives bounded context from timestamped user/assistant transcript deltas, excludes future fragments, retains the original delegation ID and prevents duplicate admission. Escaped JSON context compaction makes guaranteed progress; truncated context is labeled. A completed public result becomes Live commentary, with at most 500 UTF-8 bytes per append. Unknown, failed, cancelled and approval states remain distinct.

One task may be active per session/chat. New requests are blocked while earlier work is pending or uncertain. This initial interface does not amend a running Hermes task, approve it, select a device per-request model profile, or cancel backend work when speech is interrupted. Existing task controls remain the place to manage such work. Do not claim a spoken correction was applied to an already running task.

## Embedding interface

The application must supply an explicitly configured SDK client, an existing owner-scoped Pocket service, and asynchronous audio I/O. This example defines the interface; it does not configure credentials or start a service:

```python
from pocket.chat import ChatService
from pocket.gpt_live import run_gpt_live
from pocket.live_hermes import HermesLiveBackend

async def attach_voice(owner_client, service, device_id, chat_id,
                       microphone_24khz, playback_24khz, stop_event):
    backend = HermesLiveBackend(ChatService(service), device_id, chat_id)
    return await run_gpt_live(owner_client, backend, microphone_24khz,
                              playback_24khz, stop_event)
```

Calling `run_gpt_live` can incur charges. Importing these modules and constructing the backend does not open an OpenAI connection. There is no credential discovery, SDK installation, auto-start, new listener, automatic reconnect, automatic task resubmission, or provider fallback. A client without `live.connect` fails explicitly. An already-set stop signal prevents connection acquisition.

Microphone frames must be paced raw mono signed PCM16 little endian at **24,000 Hz**, no WAV header, at most 100 ms/4,800 bytes per frame. Output has the same format. The caller owns actual device-format conversion and asynchronous playback. The bridge bounds output to 32 queued frames and 96,000 buffered PCM bytes, transcript storage to 128 fragments/16,384 UTF-8 bytes, serialized backend context to 4,096 bytes, and delegation IDs to 32 per session.

Default session lifetime is five minutes. Startup, SDK context entry/exit, finalization and cleanup waits are bounded for cancellation-aware SDK and application methods. The paid transport is closed before waiting on cancelled application tasks. Blocking the event loop or suppressing cancellation is unsupported: asyncio deadlines cannot forcibly stop such code. Delayed callbacks are reported as incomplete cleanup, not as finished. Timed-out Hermes threads may still finish their journaled work; retain the service/store until those calls settle and inspect Tasks before retrying. A `session.closed` acknowledgment confirms session finalization, not that all speech played or a backend task was cancelled.

## Cloud continuation and current limits

**30 October source update:** paired v2 now has an inactive owner/controller,
strict frozen configuration, persistent internal IDF execution composition and a
separate muted-start Live audio lease. See [session lifecycle](GPT-LIVE-SESSION-LIFECYCLE.md)
and [audio lease](GPT-LIVE-INACTIVE-AUDIO-LEASE.md). These are not normal-UI
activation. Co-captured digital reference is implemented; physical usefulness is
unverified. V1 still requires its actual-played semantics; v2 deliberately reports
actual playout unknown and uses DMA counts only as buffer credit. The older
paragraphs below describe prior checkpoints and do not turn synthetic/compile
evidence or the remembered silent frame count into device acceptance.

The later audio continuation adds a tested SpeexDSP 1.2.1 native-24-kHz engine,
default-off callback-owned DMA source/silence accounting, and an inactive
firmware streaming pump/IDF adapter. See [AEC implementation](GPT-LIVE-AEC.md)
and [DMA accounting audit](GPT-LIVE-DMA-ACCOUNTING.md). Actual pinned ESP-IDF
build results are recorded separately in the checkpoint. Compilation and
synthetic AEC do not enable a physical duplex session: the device entry is
hard-refused while true playout and capture/reference synchronization are
missing. DMA EOF is **not** speaker rendering.

The cloud continuation uses Python 3.12.14. It fixes a Python 3.12 scheduling
starvation bug, makes Stop responsive during SDK acquisition/startup, and explicitly
passes `max_retries=0, on_reconnecting=None`. Official SDK 3.41.0 source shows that
the default five-retry budget was gated by a non-None callback: the old default
was not demonstrated to reconnect. Explicit settings now pin the intended policy.
No OpenAI package is installed in this cloud executor and no provider/client/key
acceptance is implied by mock tests. Optional dependencies are recorded in
`requirements-gpt-live.txt`, not installed or configured by importing this code.

`pocket/live_device.py` adds a bounded native-24-kHz device protocol adapter, and
`pocket/live_websocket.py` adds an optional authenticated upgrade on the existing
server at `/targets/<target>/live`. This requires explicit embedding configuration
with an owner-supplied client and supported echo processor; it is off by default.
`run_bridge` does not discover a Live key or enable this route. Existing TLS,
bearer authentication and device/target isolation remain in force. See
[device stream protocol](GPT-LIVE-DEVICE-PROTOCOL.md).

Matching allocation-free C framing and an inactive firmware handshake/lifecycle/
FIFO core are source prerequisites only. The current physical firmware and
legacy HTTP endpoints remain **16 kHz half duplex**. No firmware worker, UI mode,
service, network/security settings or hardware operation was activated. Host
tests never establish firmware linkage or codec-clock/heap/acoustic acceptance;
the later actual firmware builds are recorded separately.

The existing ES8311/I2S BSP supports prospective concurrent TX/RX and a native
24-kHz format; no additional hardware is inherently required. Its current public
write/drain API does not expose exact continuous rendered speech position. Idle
DMA completions cannot be counted as played speech. The new descriptor ledger
tracks only DMA-completed source samples and source/silence spans. Exact codec
1.6.2 source and the SpeexDSP C ABI have now been audited, but a true rendered
position plus capture-aligned reference transport and physical duplex
timing/echo/double-talk/heap acceptance are still required.
Microphone muting during playback is not used as a substitute for full duplex.
The generic echo callbacks in transport tests remain synthetic fixtures. The
separate native-AEC tests run real SpeexDSP against synthetic paired PCM, not
physical Pocket recordings.

The earlier cloud suite is separately recorded in
`CLOUD-LIVE-STATUS-20261029.md`; updated results are in the new checkpoint's
`CLOUD-RESULTS.md`. SDK/LVGL sources are available for the later verification,
while procfs process-containment blockers remain. Historical October 25 failures/release
blocks remain intact. No commit, push, merge, paid call, key setup, deployment or
flash was performed. The October 25 app image is not a GPT-Live image.

## Key and cost proposal

After explicit confirmation at configuration time, place a dedicated project key only in the Pocket bridge host's existing secret mechanism, under a separate name such as `POCKET_OPENAI_API_KEY`. Construct an official OpenAI SDK client for the OpenAI HTTPS endpoint, with automatic request retries disabled, and pass it into this module. Do not put the key in chat, Git, firmware, pairing data, diagnostic output or examples; do not change Hermes reasoning credentials. No persistent key setup is performed by this code or task. Confirm the actual deployment host and current service state before preparing that setup.

GPT-Live is documented at **$0.05 per session minute**, billed per second: approximately **$3 for an open hour**, plus the backend's normal model/tool costs. Open-session silence is still session time. Five minutes is approximately $0.25 of voice time, plus finalization and backend usage; the local lifetime limit is not a provider spending cap. Andy reported $100 credit; neither balance nor project/model eligibility was queried. No paid validation is implied by the mock results. [Model](https://developers.openai.com/api/docs/models/gpt-live-1), [pricing](https://developers.openai.com/api/docs/pricing).

Protocol references: [client delegation](https://developers.openai.com/api/docs/guides/live-delegation), [WebSockets](https://developers.openai.com/api/docs/guides/voice-websockets?api=live), [Live prompting](https://developers.openai.com/api/docs/guides/live-prompting).
