# Development protocol v1

This milestone is deliberately text-only. Voice messages described in the V1 design
are future work, not accepted by this server.

Authenticated local HTTP endpoints:

- `GET /health`: mock bridge status; explicitly not a live Hermes health check
- `POST /tasks`: exact JSON fields `version: 1`, `request_id`, `text`
- `GET /tasks/{request_id}`: refresh snapshot from the fake backend
- `GET /tasks/{request_id}/events?after=0`: up to 100 journal events after cursor
- `POST /tasks/{request_id}/stop`: body `{}`; request stop and re-read actual state
- `GET /usage`: three unavailable provider entries with null values

Request IDs and authenticated device IDs are 1–64 ASCII letters/digits/underscore/hyphen.
Request frames are limited to 8192 bytes; text to 4096 UTF-8 bytes. Duplicate JSON
keys, unknown fields, unsupported versions and non-integer versions are rejected.
The authenticated device comes from the server's token mapping, never request data.
Same device/request ID with identical content reuses a task. Different content is
409. A different device cannot read or stop another device's task.

States: queued, dispatching, queued_upstream, running, awaiting_approval, stopping,
completed, failed, cancelled, unknown. An upstream acceptance is not evidence of
running; stop acceptance is not evidence of cancellation. Unknown upstream states
fail closed without rewriting the stored state. Terminal states are immutable.

Journal sequence numbers increase per task. On reconnect, retrieve the snapshot,
then events after the last cursor; advance the cursor to the last returned event.
A page of 100 may require another read. This journal is not upstream token streaming.

Use one bridge process per database. At exclusive startup, recover() marks unfinished
local admission as unknown. Do not call recover() on device reconnect. A crash in the
remote-submit window is ambiguous and never triggers automatic replay. Real upstream
reconciliation is pending. SQLite stores prompts/results locally in plaintext;
protect the deployment volume and define retention before production use. This
prototype has no retention scheduler, production rate limiter, TLS endpoint, or
hardware pairing. Fake backend state is in-memory and does not survive server restart;
old run lookups then report backend unavailable, never a fake completion.

## Two-instance development mode

Start with `python3 -m pocket.server --demo --two-targets`. This creates two fake
backends and separate database files, suffixed `.windows` and `.linux`. It does
not connect to either real machine. The same local fixture token applies.

Authenticated `GET /targets` returns `targets: ["windows", "linux"]` and
`default_target: null`. Prefix every task, event, stop and usage path with
`/targets/windows` or `/targets/linux`, for example
`POST /targets/windows/tasks` and `GET /targets/linux/tasks/one/events?after=0`.
Unqualified operations fail with 400; unknown targets fail with 404. No fallback.
Clients must retain the target together with every task ID and event cursor,
including in-flight response handlers when the selected target changes.

Each configured target must use a separate database. Reusing a database is
rejected. Request IDs can be reused across targets without deduplication conflicts,
but clients must never replay a submitted command on another target merely because
they switched views or one host went offline. Configured targets are a trusted
server-side list, not arbitrary client-supplied URLs. This is routing isolation,
not per-target authorization: a device token currently authorizes all configured
demo targets. Production target ACLs and secure pairing remain future work.

## Chat and audio foundations

POST /targets/{target}/chats/{chat_id}/messages accepts the same versioned
request_id/text body as tasks. GET on that path returns the latest 100 turns.
Task IDs in returned turns are explicitly mapped from chat IDs and client request
IDs. One turn may be active per chat; an ambiguous/unfinished turn blocks new
ones until reconciled. Completed history (at most 20 prior turns and 16 KiB)
is supplied to Hermes using conversation_history. Earlier turns remain in the
journal but fall outside the bounded prompt window. Failed turns are not included
as successful dialogue. Separate target databases isolate histories.

pocket.audio provides in-memory mono PCM16LE/16 kHz capture, sequential chunks
up to 4096 bytes, maximum 30 seconds, discard on malformed/disconnected input,
and injected STT/TTS interfaces. This is a tested library, not yet an HTTP or
firmware audio transport. Provider choice and live audio configuration are pending.

pocket.usage parses explicitly supplied Codex app-server, Claude Code statusline,
and Ollama balance snapshots. Its target-scoped cache marks expired/old values
stale and never treats missing information as zero. These parsers do not access
accounts; the current HTTP usage route still returns not_configured.

## Linux speech relay source adapter

pocket/linux_voice.py adds a server-side adapter for the documented dashboard
POST /api/audio/transcribe and /api/audio/speak routes. It uploads PCM16LE mono
16 kHz recordings as WAV data URLs and preserves returned MIME types (including
Fish Audio MP3) rather than misinterpreting encoded audio as PCM. Speech recognition
is disabled by default and returns an explicit error without making a request.
It does not enable the Linux server's STT or change its provider configuration.

The owner supplies dashboard authentication to the bridge at deployment; no
credential discovery or voice-config call is used, and provider keys remain on
the server. Remote HTTP is rejected; HTTP loopback is permitted for a bridge on
the dashboard host. HTTPS certificate verification is retained. POSTs are not
retried automatically and redirects are refused.

This source adapter is syntax-checked only, not yet wired to the public-facing
bridge or device firmware. MP3 decoding/streaming to the device and authenticated
pairing remain unfinished. Schemas are from public upstream commit
0e427d788d253630d38a3a51cd76849e73cd6925; the user's installed fork commit was not
available from that public remote, so exact runtime compatibility is unverified.

The relay module also includes an optional server-side ffmpeg decoder to normalize
Fish Audio MP3 into bounded PCM16LE mono 16 kHz playback. It uses pipes, no shell,
network-disabled media protocols, a 20-second decoder timeout and a 30-second
playback cap. It does not install ffmpeg, persist audio, invoke a provider or run
at import time. This path has been syntax-checked only; real Fish Audio output
and device playback still require owner acceptance.

## Pending transport wiring (2026-10-14)

The firmware now contains a compiled, bounded HTTPS JSON transport in
`pocket_http.c`. It validates public roots, disables redirects, does not retry
POSTs, and returns HTTP status separately from transport errors. This module is
not yet connected to the UI or a provisioned device identity. A successful build
is not evidence of a live bridge connection. Device time synchronization and
owner-approved device authentication remain deployment requirements. No existing
Hermes account token is embedded in the firmware.
