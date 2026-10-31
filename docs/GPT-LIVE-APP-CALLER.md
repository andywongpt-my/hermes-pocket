# Default-off normal-app Live caller

`CONFIG_POCKET_LIVE_APP_CALLER` selects an optional caller inside the normal voice
worker. Disabled builds retain the legacy voice path. Enabled builds do **not**
fall back to legacy voice: their Live readiness query currently returns false
unconditionally from `pocket_live_admission.c`. No runtime setting or build option
can make that query true. The original v2 device-run refusal gate is unchanged.

Consequently, the current production caller reports `Live requires device
validation` before additional Live config access, owner reservation, allocation,
network connection or codec activity. It does not stop normal background behavior
elsewhere in the application. Tests replace the readiness function at a fake
boundary to exercise future lifecycle code; production has no such replacement.

## Future admitted path, presently unreachable

A fresh physical event must be no more than one second old, strictly newer than
the last admitted event, and have a non-overflowing ten-second dispatch deadline.
Admission freezes target, current user volume and original event timestamp. It
also requires the exact current callback generation. Every physical PRESS and
input loss advances that saturating generation even while idle; a later queued
press/loss invalidates older admission before the lease lock can be acquired.
Generation exhaustion disables Live admission rather than wrapping. Admission
reserves the existing setup/voice lease, closes reader admission and sends private
sentinel 3 to the existing voice worker. No new audio or network task is created.
Initial gesture release does not end a full-duplex session. Any later physical
PRESS or input queue loss latches Stop before the main event queue.

The worker rechecks Stop, readiness and the original deadline before reading the
existing link configuration. It uses chat ID `pocket`, matching the existing
normal application namespace, and strictly snapshots origin/token/CA/target/
volume/epoch into one persistent heap bundle. Large storage is never allocated on
the UI stack. That bundle contains the session, private IDF context and worker;
credential copies and PCM are scrubbed before owner release and destruction.
No URLs, tokens, CA material or PCM are placed in status text.

The callback Stop latch is static atomic storage. The callback never dereferences
a heap session pointer, avoiding free-versus-button races. The IDF predicate
combines this latch with the session controller's own atomic cancellation state.

## Terminal state and cleanup

The worker executes once. There is no automatic new session, reconnect, frame
replay or legacy fallback. Backend status remains unknown unless an explicit
validated terminal event was received; backend work pending and unconfirmed
finalization are reported separately. No status asserts actual audible playout.

Incomplete cleanup retains the bundle, owner reservation and busy state and pauses
this worker's ordinary polling. Cleanup-only reaping is allowed at most four times
per second **only** when audio is already verified clean and the known owned
network context remains pending. Audio failure, unknown ownership, scrub failure
or owner-release failure stays locked: it must not trigger repeated I2C cleanup or
appropriation of a foreign context. No timeout frees pending storage.

Normal manual Screen Off remains standby/wake, not physical power removal. UI
navigation and Stop remain responsive subject to the existing event system; a
stalled SDK call cannot be preempted by these software guards. No hardware failsafe,
measured shutdown latency or acoustic validation is claimed.

## Verification boundary

`test_firmware_live_caller.py` compiles the actual gated voice-admission block and
tests the actual always-false production admission query with the option forced
on. `test_firmware_live_app.py` compiles the real heap bundle, session controller
and config helper against fake IDF operations. These exercise allocation failure,
immutable intent, event replay/expiry, Stop, known-network cleanup and locked
ownership without opening a socket or touching hardware. Existing legacy tests,
real IDF compile/link and UI render tests remain separate checks.
