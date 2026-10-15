# Development protocol v1

This milestone is deliberately text-only. Voice messages described in the V1 design
are future work, not accepted by this server.

Authenticated local HTTP endpoints:

- `GET /health`: mock bridge status; explicitly not a live Hermes health check
- `POST /tasks`: exact JSON fields `version: 1`, `request_id`, `text`
- `GET /tasks/{request_id}`: refresh snapshot from the fake backend
- `GET /tasks/{request_id}/events?after=0`: up to 100 journal events after cursor
- `POST /tasks/{request_id}/stop`: body `{}`; request stop and re-read actual state
- `POST /tasks/{request_id}/abandon`: body `{}`; explicitly close a task whose state
  is still `unknown` after a refresh, recording it as `failed`. It neither stops nor
  resubmits upstream work, which may still have run. Other states return 409.
- `GET /usage`: three provider reports; defaults to `not_configured` with null values

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
ones until it resolves or the device explicitly abandons its task ID. Completed history (at most 20 prior turns and 16 KiB)
is supplied to Hermes using conversation_history. Earlier turns remain in the
journal but fall outside the bounded prompt window. Failed turns are not included
as successful dialogue. Separate target databases isolate histories.

pocket.audio provides in-memory mono PCM16LE/16 kHz capture, sequential chunks
up to 4096 bytes, maximum 30 seconds, discard on malformed/disconnected input,
and injected STT/TTS interfaces. This is a tested library, not yet an HTTP or
firmware audio transport. Provider choice and live audio configuration are pending.

## Usage snapshots

`pocket.usage` parses explicitly supplied Codex app-server, Claude Code statusline,
and Ollama balance snapshots. Inject a `UsageCache` with
`make_server(..., usage_cache=cache)` to expose those observations. There are no
account collectors, ingest routes, background refreshes, or provider requests.
An uninjected cache remains empty. Authentication and target routing apply as
for other operations; use `GET /targets/windows/usage` or
`GET /targets/linux/usage`. The unqualified `/usage` path remains supported only
in single-target legacy mode.

The JSON root is an array of exactly three reports, ordered `codex`, `claude`,
`ollama_cloud`. Each report contains `provider`, `availability`, `value: null`,
`fetched_at` (Unix seconds or null), `source` (a display label or null), and a
`metrics` array. Provider-level `value` is always null because different units
and windows have no meaningful aggregate. An empty cache returns
`not_configured`, null timestamps/sources, and empty metrics for every provider.

Report availability is explicit:

- `not_configured`: no supplied source or cache entry for this target/provider.
- `unavailable`: configured without observations, an explicitly empty snapshot,
  or only unknown measurements.
- `available`: every supplied metric is known and fresh, with no omitted metrics.
- `partial`: a mixture of known, missing, or stale metrics, or fresh metrics
  omitted by the four-metric wire limit.
- `stale`: all supplied metrics have expired or lack a usable observation time.
- `failed`: the latest refresh was explicitly marked failed. Previous successful
  metrics remain visible with `stale` availability; no exception text is exposed.

Each metric preserves `category` (`subscription`, `included_credit`, or
`purchased_credit`), `window` (1–48 printable ASCII characters), nullable `value`,
`unit` (`percent_used`, `percent_remaining`, or `USD_credit`), nullable
`fetched_at` and `resets_at`, `availability` (`available`, `unavailable`, or
`stale`), nullable `source`, and nullable `expires_at`. The parser's `provider`
field is retained. `window_minutes` is optional and nullable. Percentages are
bounded to 0–100; unknown measurements stay null and a real zero stays zero.
Included and purchased credit balances remain separate. All numeric fields are
finite and nonnegative; sources are sanitized to at most 64 ASCII characters.

`expires_at` is the earlier of observation time plus cache TTL (300 seconds by
default) and any provider reset time. At that boundary a known measurement is
stale, so a client can age cached data without another request. Missing or future
observation times cannot establish freshness. Report `fetched_at` is the oldest
metric observation time, or null if any metric lacks one; failures never advance
it. An explicitly empty snapshot may carry its own observation time.

`UsageCache.put(target, provider, rows, *, fetched_at=None, source=None)` validates
the complete supplied snapshot before replacing it and clears a prior failure.
`configure(target, provider, *, source=None)` starts a configured, empty entry;
`unavailable(target, provider, *, fetched_at=None, source=None)` clears old
observations; `failed(target, provider, *, source=None)` retains the last
successful observations. `report(target, provider, now)` returns an isolated copy
of the bounded wire report; `get(target, provider, now)` returns its metrics.
All operations validate the target/provider scope and share a lock. At most 64
metrics are accepted per snapshot; at most four are returned, with
`omitted_metrics` indicating the remainder. All-expired observations still report
`stale`, and an explicit failed refresh takes precedence over other states.

For example, a supplied Codex observation may produce this single report inside
the three-provider array:

```json
{
  "provider": "codex",
  "availability": "available",
  "value": null,
  "fetched_at": 1000,
  "source": "account/rateLimits",
  "metrics": [{
    "provider": "codex",
    "category": "subscription",
    "window": "codex:primary",
    "value": 0,
    "unit": "percent_used",
    "fetched_at": 1000,
    "resets_at": 2000,
    "window_minutes": 300,
    "availability": "available",
    "source": "account/rateLimits",
    "expires_at": 1300
  }]
}
```

The device integration polls these read-only reports through the existing voice
network worker after a successful idle status response. It reuses that worker's
verified HTTPS client and 8 KiB response buffer, releases status JSON before
parsing Usage, and polls each target at most once per 30 seconds. A queued voice
request takes priority; capture, transcription/task waiting and playback do not
poll Usage. No additional network task or concurrent TLS client is created.
The idle connection closes after status/Usage processing, before the next poll
wait, so it cannot outlive the bridge's keep-alive budget. Disconnected targets
show unavailable data; failed Usage refreshes retain cached observations with an
explicit failed/stale label. OK cycles the supplied windows on the existing
Codex, Claude and Ollama rows. No account collector is enabled by this firmware
or by the default bridge configuration.

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
