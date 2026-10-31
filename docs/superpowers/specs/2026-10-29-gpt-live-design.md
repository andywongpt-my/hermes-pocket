# GPT-Live voice bridge design

This local implementation is authorized by the October 29 delegation to implement and mock-test GPT-Live where feasible. GPT-Live means `gpt-live-1`, not the separate Realtime API. No deployment, credential configuration, paid request, publication, or flash is authorized by this work.

## Architecture and approaches

Pocket currently captures mono PCM16 at 16 kHz, finishes a push-to-talk recording, transcribes it, submits a durable Hermes chat turn, and later downloads synthesized playback. GPT-Live uses continuous concurrent input/output. Replacing a transcription or synthesis adapter would preserve a turn-based pipeline and would not implement GPT-Live conversation semantics.

Use a host-side GPT-Live primary connection with client delegation. Keep the existing `ChatService`/`Service`/Hermes adapter and its journal, history, configured default model, tools, and approval states. Device per-request model profiles are not added to the host interface in this iteration. Alternative hosted Responses delegation would move backend orchestration away from Hermes; ordinary Realtime would change the requested provider architecture. Neither is selected.

The new host bridge consumes a caller-supplied, authenticated OpenAI SDK client, a paced 24 kHz PCM16 microphone source, a playback sink, and an explicit stop signal. It starts a `gpt-live-1` session, waits for `session.started`, and handles input, output and delegated work independently. It imports no SDK and discovers no credentials at import or construction. The transport helper uses the documented Live SDK methods; its presence is checked before opening a connection. Missing Live support fails explicitly, without fallback.

Client delegation events contain IDs/timing, not task text. Build bounded request context from the session's timestamped user and assistant transcript fragments. Only a delegation to the client can dispatch Hermes work. Deduplicate delegation IDs, allow one outstanding task per session, and preserve durable task IDs. Completed public results return as commentary; pending approval, failure and uncertain admission remain distinct. Speech interruption or session closure does not claim to cancel backend tools. No automatic reconnect, resubmission, or approval is added.

Bound transcript storage, audio frame size, playback backlog, delegation count, startup/close waits and session duration. Reject malformed PCM/base64/events. Keep audio/transcripts, raw exceptions, SDK headers and keys out of logs and result metadata. Session storage is disabled. Playback completion belongs to the playback sink; GPT-Live has no Realtime-style response-done turn marker.

## Feasible deliverable and device barrier

Implement the reusable host session bridge and durable Hermes delegation adapter, with synthetic transport/audio and the real local task journal in tests. Do not wire it into the existing HTTP push-to-talk endpoint or advertise full-duplex support to current firmware. A later device streaming transport, 16/24 kHz format decision, concurrent codec path, echo/feedback handling and physical acceptance are still required. The October 25 release block remains in force. The current device has not been opened or reset in this task.

## Key and cost proposal

After separate approval, the owner can provide a dedicated project credential through a host-only secret environment variable such as `POCKET_OPENAI_API_KEY`, outside Git and device storage. Pass an explicitly constructed client to this bridge, preferably with automatic request retries disabled. Do not reuse or change Hermes reasoning credentials. Verify project/model access separately: possession of an API key does not establish GPT-Live availability for that project.

At documentation review time, GPT-Live voice costs $0.05 per session minute, billed per second; backend model/tool costs are separate. An open hour is approximately $3 in voice charges. Close idle sessions; the default local session lifetime is five minutes, with bounded finalization. This is an operating limit, not a provider-enforced spending cap.

## Validation and references

Test exact Live startup, readiness before audio, concurrent microphone/playback/delegation, transcript-derived context, duplicate/busy/invalid delegations, malformed and oversized audio, backpressure, disconnect/finalization, time limits, durable duplicate admission, history, uncertainty and approval handling. Recheck the earlier failing cases with bounded execution before interpreting them as resolved. Run the project suite once after the new targeted checks; preserve and name any remaining failure. No real API, audio hardware or server call is required for these checks.

Official sources reviewed October 29, 2026:

- https://developers.openai.com/api/docs/models/gpt-live-1
- https://developers.openai.com/api/docs/guides/live
- https://developers.openai.com/api/docs/guides/live-delegation
- https://developers.openai.com/api/docs/guides/live-prompting
- https://developers.openai.com/api/docs/guides/voice-websockets?api=live
- https://developers.openai.com/api/docs/pricing
