# Hermes Pocket: V1 design for review

Date: 2026-10-14. Status: proposed specification, not implemented or device-tested.

## Purpose and confirmed scope

An open-source FoloToy AI Passport companion to the owner's existing server-hosted Hermes Agent. The owner has the hardware. V1 displays connection and task status, accepts voice requests, plays spoken replies, invokes Hermes tasks, displays their results, and queries usage for Codex, Claude and Ollama Cloud. API consumption and subscription allowance are separate values. The owner approved the proposed Wi-Fi, push-to-talk and server-held API-key direction on October 14.

## System design

1. Device firmware handles three buttons, display, bounded microphone capture, playback, Wi-Fi connection and a small local connection state machine. A press starts recording; release submits it. Cancel stops recording or playback. Task cancellation is an explicit separate command with server confirmation.
2. A Pocket bridge runs beside Hermes. It authenticates the device, converts audio to text through a configurable STT adapter, submits the resulting text to Hermes, forwards task events and synthesizes the reply with a configurable TTS adapter.
3. Hermes handles conversations, tools, runs and approvals. The bridge uses supported run/session/status/event interfaces where the installed version provides them. Startup capability detection must fail clearly for unsupported versions rather than pretending to be connected.
4. Usage adapters collect provider-specific reports on the server. The device receives normalized read-only summaries, never provider credentials.

Direct Wi-Fi is preferred over a permanently required phone/BLE relay. BLE provisioning may be added only after its authentication and ownership flow is designed and tested. The upstream baseline's scan/advertising demos are not a production network client.

## Device experience

Home shows device connectivity, server connectivity, Hermes availability and current task status separately. A connected Wi-Fi network alone must not imply Hermes is online. Three primary pages are Chat, Tasks and Usage. Physical controls must be labeled in the UI and avoid accidental approval through a held talk button.

Recording and playback are half-duplex in V1. No always-listening wake word or full-duplex interruption is promised. Recordings use bounded streaming buffers, duration limits and server limits. Failed or interrupted uploads are discarded. Reconnection must not submit a task twice; requests carry stable identifiers and the bridge performs deduplication.

Task states distinguish queued, running, awaiting approval, completed, failed, cancelled and disconnected/unknown. A disconnected device must not label a still-running server task as failed or cancelled. It can retrieve the final result after reconnecting. Long results are paginated; spoken replies are bounded and can be stopped independently of the task.

## Usage contract

Every entry includes provider, account scope, metric, unit, reporting window, reset time when supplied, source, fetched time and availability. Missing data is unknown, never zero. Stale data remains visibly stale. A provider failure must not block chat.

- Codex: investigate the documented app-server account/rateLimits/read and account/rateLimits/updated surfaces on the installed server. Report provider-supplied usage percentage and reset windows. Do not infer an exact number of remaining requests. OpenAI API accounting is separate and requires its own authorized source.
- Claude: documented Claude Code status-line data may expose five-hour/seven-day rate-limit usage for supported subscriptions after a response. Verify installed-version support and do not generate a paid request merely to refresh the meter. Anthropic API tokens and billed costs use a separate adapter/source.
- Ollama Cloud: current official OpenAPI defines GET /api/usage for usage history and GET /api/balance for remaining included/purchased credits, authenticated with bearer credentials. Prefer those supported surfaces; do not assume older session/weekly quota semantics. Account-specific support remains untested.

Voice queries read these same structured summaries. The model must not invent balances, convert tokens into subscription quotas or silently substitute estimated spending for provider billing. Partial reports name what is missing.

## Security and privacy

Keep full Hermes and provider keys on the server. Give the device a revocable, restricted bridge identity with TLS certificate validation. Pairing and persistent credentials require owner approval at setup time. Do not expose an unrestricted Hermes tool endpoint to the internet. Preserve Hermes approval gates; consequential operations should show their details and route to an appropriate approval interface if the tiny display cannot show enough context.

Raw audio is not retained by default. Logs redact secrets and sensitive message bodies. STT/TTS provider choice, costs and audio-processing destinations must be disclosed before configuration. No automatic account scraping, login bypass or credential extraction.

## Licensing and repository

Requested public repository: hermes-pocket under the verified owner andywongpt-my. Creation was still pending login at specification time. Both upstream repositories advertise MIT licensing. Preserve their actual copyright and license notices for reused code and audit dependencies/assets separately. MIT is the proposed license for original project code; confirm before publishing that license. Public scaffolding must contain no private server addresses, keys, recordings or unrelated project files. The repository is not an official NousResearch or FoloToy product.

## Delivery milestones and acceptance

M0: pin upstream revisions, verify the owner's board and installed Hermes version, finalize interface schema and security setup; provide simulator fixtures and explicit unsupported states.

M1: implement device navigation and bridge protocol against a fake Hermes backend; test offline/reconnect, deduplication, malformed input, bounded buffers, cancellation and TLS failure.

M2: implement text requests, real Hermes task/event integration and approval preservation; verify restart recovery and a task completing while the device disconnects.

M3: add push-to-talk STT and spoken TTS responses; verify duration limits, silence, upload interruption, playback stop, memory bounds and privacy defaults.

M4: add provider usage adapters, voice queries, missing/stale/reset states and fixture-based tests; then authorized account integration and hardware acceptance.

A firmware build is not hardware validation. Report host tests, firmware build, server integration and physical-device checks separately. Installation, security configuration and flashing are not yet authorized. A signed-off test plan must cover real Wi-Fi, microphone/speaker, all buttons, power cycles and interrupted tasks before a release is called ready.

## Remaining decisions and prerequisites

The server's OS, Hermes version and reachable network topology are unknown. STT/TTS providers and budget are not selected. Device board revision and safe firmware recovery process must be checked. Account integrations require explicit setup and suitable permissions. Written design review precedes implementation planning and code.

## Primary references

- https://github.com/FoloToy/ai-passport
- https://github.com/FoloToy/ai-passport/blob/main/docs/development/engineering/wifi-provisioning.md
- https://github.com/FoloToy/ai-passport/blob/main/components/bsp/include/bsp_pins.h
- https://github.com/FoloToy/ai-passport/blob/main/docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.md
- https://github.com/nousresearch/hermes-agent
- https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
- https://hermes-agent.nousresearch.com/docs/user-guide/features/voice-mode
- https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py
- https://learn.chatgpt.com/docs/app-server
- https://code.claude.com/docs/en/statusline
- https://github.com/ollama/ollama/blob/main/docs/openapi.yaml

References describe upstream capabilities, not the owner's installed server. The FoloToy overview and detailed hardware sources have some differing claims; pin a revision and resolve against its BSP and actual board before choosing hardware parameters.

## Approved addendum: Windows/Linux switching (2026-10-14)

Andy confirmed switching between his Windows and Linux Hermes instances. The
handheld selector will show the selected target clearly before sending text,
audio or task actions. Each target retains separate chats, task IDs, journal
cursors and usage observations. Switching is a view/routing change, never task
migration, cancellation, restart or automatic failover. Offline targets remain
selectable for viewing cached information, but do not silently route commands to
the other host. Actual server URLs, authentication and firmware UI remain pending.

The foundation now implements explicit target-qualified task routes and separate
SQLite journals. Chat session management and the physical selector UI are still
pending; this addition does not claim those are implemented.
