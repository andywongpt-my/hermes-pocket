# Inactive Live session ownership prototype

This adds independently testable lifecycle code, **not normal-app activation**.
The public `pocket_live_v2_device_run` gate still refuses device execution. The optional default-off normal-app caller is documented in
`GPT-LIVE-APP-CALLER.md`; it refuses admission before Live setup because its
production readiness query is hard false. There is no configuration switch that
certifies physical acceptance. The remembered silent
1000-frame observation does not establish cleanup, output or AEC quality.

## Generic controller

`pocket_live_session` is persistent, zero-initialized storage with injected owner
and adapter operations. Initialization changes memory only. Admission validates
and copies a frozen configuration, reserves the existing abstract owner and runs
memory-only adapter preparation. Preparation explicitly reports whether it
acquired the supplied context before changing it. A rejected pre-existing or
otherwise unknown context is quarantined: the controller does not appropriate,
clean up or scrub foreign state, and `reap` cannot release that unknown owner.
Ordinary config validation and pre-admission Stop occur before context acquisition.
It never starts the worker. The owner serializes
all lifecycle calls; only `request_stop` is an atomic cross-task operation.

Dispatch must occur before an explicit deadline no more than 10 seconds after
admission. Stop, expiry and clock regression are rechecked before the injected
run callback. A session executes at most once and cannot be reinitialized or
rearmed, including after pending cleanup. The shared owner epoch guard persists
across successive session objects; accepted epochs strictly increase, and reaching
UINT32_MAX permanently exhausts further admission. Failed preparation consumes
its admitted epoch rather than making it replayable.

A returned worker result is preserved as the original result. Backend status is
known only if a validated terminal event was received; absence of such an event
never means no Hermes work exists. Actual playout remains unknown. Later cleanup
progress is represented by lifecycle state, not by rewriting backend history.

## Cleanup ordering and retained ownership

A worker returning `cleanup_complete=false` enters CLEANUP_PENDING. No implicit
retry, reconnect or second execution occurs. An explicit owner `reap` may only
advance cleanup. Until actual adapter cleanup succeeds, configuration, credentials,
PCM buffers, DNS tombstone dependencies and owner reservation remain retained.
No timeout permits freeing storage or killing a task inside SDK cleanup.

Once cleanup is verified, adapter memory is scrubbed and the copied configuration
is wiped **while the owner is still held**. Only then is owner release attempted.
If scrub fails, ownership remains retained. If abstract owner release fails after
scrub, only release is retried; cleared SDK state is neither reconstructed nor
reused. There is no interval after owner release in which this session touches
adapter storage. Callers must provide independent persistent storage, truthful
cleanup/release callbacks and a shared epoch guard for the same actual owner.

## Internal IDF context

The existing v2 IDF source now exposes INTERNAL construction declarations under
`POCKET_LIVE_V2_IDF_INTERNAL`; this is a source visibility marker, not a device
validation or activation option. `prepare` copies configuration into persistent
storage and binds callback pointers without audio/network I/O. `take_ops` provides
one operations handoff. The internal one-shot `execute` helper composes those
operations with `pocket_live_v2_worker_run`, using frozen chat/epoch and a
caller-owned persistent worker. It allocates nothing and starts no task. Both
context and worker must remain alive through incomplete cleanup; successful
scrub overwrites the attached worker before retiring the context. The optional normal caller can compose this helper only beyond its currently
hard-false admission boundary; it cannot execute in production today. Audio and network cleanup are tracked
independently so pending DNS settlement cannot cause audio restart or replay.
Scrub refuses incomplete cleanup and retires the context after a volatile wipe.
The same storage cannot be prepared again after retirement.

A default-off normal-app admission/owner adapter now composes this context, but
its unconditional production refusal remains in force. Fake-boundary tests and target compilation do
not establish real DNS/TLS latency, RTOS scheduling, codec behavior, heap headroom,
actual speaker/reference alignment, acoustic cancellation or clean physical stop.
The separate v1 actual-playout gate and v2 physical-acceptance gate remain intact.

## Offline checks

`test_firmware_live_session.py` compiles the actual controller/config helper and
injects only memory-local fake owner/adapter operations. It checks immutable
configuration, one-shot dispatch, pending cleanup, scrub-before-release ordering,
Stop/expiry/clock guards, epoch exhaustion and terminal unknown status.
`test_firmware_live_v2_context.py` compiles the actual internal IDF context with
fake network/audio APIs and a worker-call stub to check constructor inactivity,
exact composed worker arguments, single execution, retained PCM until cleanup,
configuration lifetime,
one-shot ops handoff, separate cleanup and scrubbing. No real endpoints, serial
devices, credentials or provider sessions are used.
