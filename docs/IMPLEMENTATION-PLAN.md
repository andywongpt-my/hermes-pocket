# Hermes Pocket implementation plan

2026-10-14. Based on the V1 design approved in chat. This is a plan for review, not a completed build.

## Execution and boundaries

Use the assistant's cloud workspace directly for code and host tests, respecting the user's restriction on starting Codex development tasks. Keep the new project isolated from Neko Coin. Use a local development branch and reviewable incremental changes. The public repository currently starts with a README; the MIT addition is separately approved. Do not install on the user's server, access accounts, generate credentials, expose ports or flash hardware without the appropriate setup authorization.

Implement in the order below. For each task, write a failing behavioral test first, verify it fails for the intended reason, implement, then run focused and aggregate checks. Use fake services and synthetic fixtures until real endpoints and setup are authorized. Do not make paid model calls for tests.

## 1. Pin source and define contracts

Create docs/upstream.md with exact FoloToy and Hermes commit references, supported protocol capabilities and license notices. Inspect upstream AGENTS instructions before code reuse. Read the actual run request/event schema and pin representative fixtures; do not invent upstream request payloads from endpoint names.

Create bridge/pocket/schema.py and docs/protocol.md for versioned device messages: hello, status, text request, audio begin/chunk/end, task snapshot, task events, playback and structured errors. Define IDs, size limits and monotonic event cursors. Reject unsupported versions, oversized frames and invalid enum values.

Tests: schema round trips, booleans rejected where integers are required, UTF-8 byte limits, unknown versions, malformed fields and event ordering. Fixture coverage must distinguish service connectivity from task state.

## 2. Implement a testable bridge core

Use Python 3.12 for the bridge, standard-library SQLite for durable request/run mappings, and an isolated transport adapter so core tests need no network. Choose maintained HTTP/WebSocket dependencies only after checking their official licenses and versions. Separate modules: config, protocol, task_store, task_service, hermes_adapter, audio_pipeline and usage adapters.

The core admits client request IDs transactionally and tracks dispatching/accepted/running/awaiting-approval/terminal/unknown states. Same ID and same request return the existing result; same ID with changed content is rejected. A crash after sending to Hermes but before recording its run ID is ambiguous: reconcile using supported upstream identifiers if possible, otherwise surface unknown and require user decision. Never silently replay a possibly executed task.

Tests: duplicate requests, changed-payload conflict, crash boundaries, restart recovery, concurrent admission, out-of-order events, terminal state immutability and independent device disconnect.

## 3. Add the Hermes adapter and server transport

Adapter methods: capabilities, create run, read run, stream events, stop run, resolve supported approval. Implement current HTTP/SSE interfaces only after verifying the pinned schema. Probe required capabilities at startup and display unsupported versions clearly. Limit retries to safe reads; mutation retries depend on documented idempotency.

The device connects to a restricted bridge endpoint over verified TLS. Credentials are supplied at deployment, not checked into source. Enforce per-device request limits and body/audio limits. Provide a loopback-only development configuration using fake services. Document production reverse-proxy setup without applying it to a live server.

Tests: missing authorization, wrong-device run access, expired/revoked identity, malformed SSE, partial frames, upstream timeouts, stop failure, stale approval IDs, reconnect resume and oversized requests. Approval UI must not auto-accept or drop required details. Unsupported approval types route to the existing trusted interface.

## 4. Build a host-side device simulator

Provide a small command-line simulator for connect, send text, list tasks, reconnect, stop playback and query usage. This is a test client, not the final device UI. It exercises the same protocol and fake backend as firmware will use.

Acceptance: one synthetic conversation and long-running task survive disconnect/reconnect without duplication; errors are distinguishable and final results remain retrievable. No real user accounts are needed.

## 5. Implement firmware foundation

Start from the pinned FoloToy baseline in firmware/ and preserve BSP boundaries and upstream notices. Create a new three-page application UI, not a reskinned hardware demo. Implement connection manager, protocol client, bounded event queue, task/status models and button state machine. Keep network and audio work off the LVGL/UI thread. Provisioning is a separate reviewed flow, not an insecure copied demo.

Host-test pure state/model code. Build with the pinned ESP-IDF version and report build warnings and flash/RAM size. Board pinout, available memory, display timings and recovery procedure must match the real board before flashing.

Tests: held/repeated buttons, cancel versus task-stop distinction, rapid navigation, stale updates, offline boot, credential absence, reconnect backoff, text truncation/pagination and bounded queues.

## 6. Add push-to-talk audio

STT and TTS are configurable interfaces with deterministic fake implementations for tests. Production provider selection, costs, languages and audio destinations require owner choice before deployment. Firmware streams bounded PCM chunks; server rejects unsupported audio parameters and overlong recordings. Stop recording on release and show clear capture/submission states.

Playback uses bounded buffering; stop playback does not cancel a task. Disconnect during upload discards incomplete input. No raw-audio persistence by default. Do not promise simultaneous listening/speaking or on-device inference.

Tests: silence, empty/too-long input, lost final chunk, interrupted upload, STT failure, TTS failure, reply length cap, playback interruption and memory bounds. Actual microphones, speakers, network jitter and acoustic quality remain hardware acceptance.

## 7. Implement usage adapters independently

Create a common model with provider, source scope, unit, window, reset, fetched_at and availability. Adapters may return partial/stale/unavailable data. Cache and refresh with rate limits; voice reads the same cache used by the screen.

- Codex: implement parsing of authorized app-server rate-limit events/snapshots when available. Keep OpenAI API token/cost accounting separate. Do not read private session storage or extract auth tokens.
- Claude: parse explicitly supplied supported Claude Code status-line snapshots for subscription usage; add a separate API usage adapter only for an authorized official reporting source. No model call solely to refresh the display.
- Ollama Cloud: implement official /api/usage and /api/balance adapters against pinned schema. Keep included and purchased balances separate and preserve provider units. Do not assume legacy weekly windows.

Tests: omitted windows, stale timestamps, malformed response, 401/403/429/503, retry-after, wrong account scope, credits versus currency, exact reset boundaries and available partial reports. Never display missing data as zero or fabricate a request count.

## 8. Verification and release preparation

Run all bridge tests, firmware host tests, protocol contract tests, static checks, firmware build and dependency/license audit on the final source. Test secrets redaction and ensure examples contain only placeholders. Publish an explicit verification matrix distinguishing passed, failed and not run checks.

After separately authorized server/account setup, test real Hermes requests and provider reads. After separately authorized flashing, test physical audio, buttons, Wi-Fi, power cycles, reconnection and a running task surviving device loss. Obtain acceptance before labeling V1 usable. Do not issue a completion claim based only on mock tests.

## First delivery target

Steps 1–4: a verified protocol and bridge skeleton with fake Hermes integration, durable task state and a host simulator. This delivers useful testable progress without waiting for server credentials, provider spending or device flashing. Firmware and real voice/usage integrations follow the same contracts.

## Decisions still needed

- Review this plan and choose direct cloud implementation (recommended under the existing no-Codex-task restriction) or a different explicitly authorized environment.
- Provide server OS and installed Hermes version before live compatibility checks.
- Select STT/TTS providers and budget before live audio integration.
- Confirm hardware revision and flashing/recovery approach before device installation.

The prior approval of the V1 design is recorded; this plan is the next review checkpoint. No feature code has been written yet.
