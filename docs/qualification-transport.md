# Inactive device-side synthetic TLS qualification

This is a separate **default-off test composition**, not a way to enable normal
GPT-Live. Original production gates and the always-false admission query remain
unchanged. `CONFIG_POCKET_QUALIFICATION` includes this source only in an explicitly
selected qualification build; compiling it is not physical authorization.

The caller owns zero-initialized persistent `pocket_qualification_transport`
storage and the normal audio-owner lease. It must separately establish the fresh
physical test confirmation and allowed data/output profile. `prepare` performs
no network/audio action and copies its bounded configuration and options. The
external Stop/clock/monitor callback contexts must remain alive until scrub.

## Explicit immutable inputs

- Canonical numeric RFC1918 IPv4 only (10/8, 172.16/12, 192.168/16), not DNS,
  loopback, public/link-local addresses, octal-looking or shorthand IPv4.
- Explicit custom PEM CA; SDK TLS hostname/chain checking remains intact. Local
  syntax checks do not establish that a certificate is cryptographically valid.
- Explicit 32–128 character URL-safe test token, never saved production settings.
- Fixed chat ID `qualification` and route `/qualification/v2/live`, never a
  caller-controlled path or the production `/targets/.../live` route.
- Explicit `PQ_SILENT` requires requested volume 0 and all-zero received PCM.
  Explicit `PQ_AUDIO` allows requested volume 0..5 and the exact deterministic
  qualification waveform. Volume never selects the profile or grants permission.
  A saved hardware mute is respected by the underlying Live audio lease.
- Original request timestamp, dispatch deadline within its original ten-second
  budget, and duration 1..60,000 ms measured from that original request. Queueing
  cannot refresh the budget. The qualified IDF constructor also has its own
  sixty-second stop cap as defense in depth; neither cap extends the earlier one.

Every speaker block is checked before the BSP submission/enable boundary. Offset
must equal previously accepted source samples; busy retries do not increment it.
PCM must match the selected profile, have absolute amplitude at most 512, and be
zero outside the three source windows [4,6), [10,12), [18,20) seconds. At most six
seconds of source positions can contain nonzero samples. Source beyond 24 seconds
must be zero, with a total 60-second source-sample bound. These are digital bounds,
not measured sound level, actual audible duration or physical playout accounting.

## Stop, ownership and observation

The mandatory Stop predicate combines physical/host policy supplied by the caller
with original deadline and clock-regression latching. The real v2 worker runs at
most once. Source violations terminate rather than fall back to another profile.
Its existing three-second network-finalization allowance is separate from audio
Stop; software deadline checks cannot preempt a stuck SDK call.

The optional monitor callback has numeric checkpoints only: audio start, capture,
validated submission, audio-stop return and network-close return. It must not
allocate, block, log or perform I/O; the normal owner can sample heap/stack metrics.
A return checkpoint is **not** proof of cleanup. Snapshots separately report
verified audio cleanliness, retained network cleanup, capture/source counters and
Stop-observed/audio-stop begin/end timestamps. Stop-observed is the first sampled
predicate time, not a hardware interrupt timestamp or calibrated acoustic stop.

An incomplete audio cleanup stays quarantined without repeated codec I/O. Only
known network-pending teardown after clean audio can be reaped. No retry, new
session, reconnect or timeout frees retained callback/transport storage. Scrub
refuses until both audio and network are clean, wipes configuration/worker and
retires the object; the caller releases its audio-owner lease only afterward.

## Evidence and remaining limits

Native fixtures exercise the actual qualified IDF composition with fake SDK
boundaries; additional tests use the actual C v2 worker and real deterministic
source with fake network/audio. They cover private endpoint validation, explicit
profiles, source rejection before BSP, busy/replay, original/deep constructor caps,
clock regression, callback budget crossing and cleanup quarantine. ASan/UBSan
results are cloud-only; leak sanitizer is disabled under ptrace.

This module does not provision a device-reachable TLS endpoint, certificate or
active device configuration. The normal app now supplies reviewed fresh-physical
admission/UI and a bounded one-use USB RAM-config handoff; these are cloud-tested
code, not physically validated controls. A separate app-only candidate has been
prepared without installing it. The host synthetic server remains loopback-only
and SILENT-only, and does not yet serve a physical device. Future approved testing
still needs explicit endpoint/certificate and supported USB platform arrangements,
real TLS/audio memory and Stop measurements, and independently available verified
recovery covering the exact affected interval. No device, audio playback, real
microphone capture, provider or service operation was performed here.
