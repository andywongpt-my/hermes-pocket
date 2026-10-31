# Inactive Live configuration and audio lease — 30 October 2026

This continuation prepares source-level ownership and cleanup for the existing
paired v2 worker. **It does not activate GPT-Live in normal firmware.** The public
v2 entry remains hard-refused, the normal UI has no caller, and no new key,
provider connection, device operation or persistent setting is introduced.

## Why a separate lease

The previous paired wrapper expected an already-open/awake legacy codec and only
restored its reference format. Normal voice may have left the codec asleep. The
silent diagnostic bracket is not an acceptable workaround: its contract forbids
all playback and unmute, and that contract remains unchanged.

The new BSP APIs are internal, serialized-owner operations:

- `bsp_audio_live_begin(epoch, requested_user_volume, &cleanup_required)` reserves
  a separate lease before any codec operation. Cold/asleep/awake entry snapshots
  prior normal format, volume and checked-mute intent. Initialization and format
  changes occur with checked mute retained. Begin never enables speaker output.
- `bsp_audio_live_enable_output(epoch)` is an explicit matching-epoch operation
  on a healthy reference-ready lease. Requested volume is a frozen user upper
  bound, not a calibrated SPL or permission to override pre-existing mute intent.
  It is not an automatic volume escalation.
- `bsp_audio_live_end(epoch)` refuses foreign/stale epochs, mutes first, stops
  paired capture/output, restores the saved format, verifies sleep, then restores
  only cached normal volume/mute intent. No end-time unmute occurs. Failed cleanup
  retains ownership/quarantine; no timeout or reset of a software flag proves it
  safe to release.

Direct legacy input/output, volume, format, sleep and reference-stop operations
cannot bypass the lease. Live and silent diagnostic ownership are mutually
exclusive. This is a single-owner programming contract, not a task-identity
security boundary or a hardware kill switch.

## Paired adapter

`pocket_live_v2_audio_context` is persistent and permits one start attempt. It
records whether BSP begin actually acquired a bracket. A partial acquired begin
must be ended; unknown/pre-existing ownership must not be ended on its behalf.
The latter leaves the wrapper unavailable instead of claiming cleanup success.

The wrapper remains muted during startup/capture and queue backpressure. Only
its first valid speaker submission enables output. It rechecks the persistent
owner Stop callback after serialized output-enable I/O and immediately before
submitting PCM, so Stop observed during enable cannot enqueue that frame.
Capture/submit errors disable further ordinary work until owner cleanup. These
checks do not preempt an operation already executing inside the SDK/codec.

Copied microphone/reference frames and temporary speaker buffers are overwritten
where owned by this adapter. The persistent worker/context is retained through
incomplete cleanup and can be scrubbed only after verified completion. This is
not a secure-erasure claim about driver DMA or every device memory region.

## Frozen configuration

`pocket_live_session_config_snapshot` copies existing owner-supplied values into
fixed storage; it performs no credential discovery, DNS, TLS, NVS or network I/O.
It accepts strict HTTPS DNS/IPv4 origins with an optional port and root slash.
It rejects path prefixes, userinfo, query/fragment, escapes, insecure schemes,
ambiguous numeric hosts/ports, IPv6 (unsupported by the current binding), and
unterminated or oversized fields rather than silently changing their meaning.

Bounds are host 192, token 32..128 visible ASCII, CA 0..2048 bytes, chat identifier
1..64 ASCII letters/digits/underscore/hyphen, target 0/1, nonzero 32-bit epoch and
user volume 0..100. Empty CA retains the existing SDK trust-bundle policy. The
snapshot does not validate certificate cryptography or assert device acceptance.
Failure clears output; copies are independent of later caller mutation. Explicit
clear uses volatile writes. Credentials are not logged or written to settings.

## Evidence and unresolved gates

Native tests compile the actual config parser, wrapper and BSP with fake SDK/
peripheral boundaries. They exercise malformed inputs, immutable lifetime,
unknown-owner rejection, partial begin, separate enable, Stop during enable,
queue pressure, failed restore and legacy/diagnostic exclusion. Sanitizer
receipts are retained in the continuation's verification directories. Target
builds verify compilation, not physical audio behavior.

Andy recalled the earlier silent diagnostic reaching 1000. The original app was
subsequently restored and boot-verified according to the parent history; the
terminal diagnostic screen is unavailable. That observation remains **unconfirmed**:
frame 1000 is published before final budget checks and cleanup, so it does not
establish a finished/checked/code-0 terminal, Stop behavior or acoustic acceptance.
No diagnostic result was persistently saved by that design.

A future activated normal-app caller still needs reviewed UI/admission and
owner configuration, and separate device acceptance for lane identity/rate,
reference correlation, echo/double-talk, runtime heap/stack, Wi-Fi/TLS pressure,
Stop/cleanup and ordinary recovery. V2 DMA-consumed credit remains distinct from
actual audible playout. No new hardware is inherently introduced by this work.
