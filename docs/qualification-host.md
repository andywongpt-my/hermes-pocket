# Local qualification host: finite collection, conditional measurements

These new modules prepare future explicitly authorized qualification. They do not
activate production GPT-Live, establish acoustic acceptance, flash a device, find
credentials or contact a provider. Cloud verification used synthetic bytes, fake
serial drivers, actual C encoders/workers and a loopback-only TLS fixture. No real
port, microphone or loudspeaker was opened.

## Stage A APIs and bounds

`pocket.qualification.Collector` accepts a caller-owned transport with an exact
independently verified `.identity`, async `read(maximum)` returning bytes / `None`
for idle / empty bytes for EOF, and async `write(bytes)` returning the exact
admitted length. It never opens or closes that transport. Explicit
`capture_approved=True` is required; profile 2 AUDIO also requires separate
`audible_approved=True`. Profile 1 SILENT is the default. Host approval never
substitutes for the device's fresh physical confirmation.

One `run()` attempt sends HPQ1 ARM, keepalive and, on failure, at most one Stop.
A full 100ms read quantum is reserved before launching a read; when less remains,
collection stops early instead of cancelling an otherwise healthy driver poll.
The default/max host wall budget is 45 seconds, including a reserved Stop window.
Both injected observation-clock progression and a separate trusted event-loop
clock are checked after operations; Stop still uses the trusted clock if the
injected clock regresses. Heartbeat scheduling also uses trusted time. No retries,
resynchronization, reconnects or hidden device reset occur. Noncooperative OS/SDK
calls cannot be preempted by these software guards.

`Parser` enforces CRC, version, nonce, profile, payload and aggregate budgets.
`Capture` additionally enforces start/terminal schemas, strict sequence/epoch,
original sample offsets, counters, caps and cleanup. Source counts are whole
240-sample submissions; stimulus counts must match the exact scheduled prefix.
A last PCM frame is never a completion receipt. `complete` is protocol completion
only, and every report sets `physical_acceptance=0`.

| Resource | Bound |
|---|---:|
| SILENT / AUDIO frames | 1,000 / 2,400 |
| Raw paired PCM retained in RAM | 960,000 / 2,304,000 bytes |
| Payload / read chunk | 1,024 / 4,096 bytes |
| Parser scratch / boot prefix | 8 KiB / 16 KiB |
| Input wire bytes | 3 MiB |
| A host deadline | 45 seconds |

The live collector ends at the exact semantically validated terminal boundary.
Normal firmware may resume console output after checked cleanup/drain; any
same-read tail is discarded without parsing, logging or retaining it, and no
further read occurs. `Parser.discard_terminal_tail()` closes the parser and removes
that tail from counted wire bytes. Before terminal, arbitrary corruption remains
fatal; there is no repair/resync. Offline consumers can keep strict framing and
must not use that live boundary to skip a malformed record.

## Local privacy and reports

`Capture.report()` and `numeric_json()` expose only allowlisted bounded numeric
fields, never PCM, device text, addresses or credentials. `save_summary(report,
explicit_path)` writes only a new numeric JSON file; no overwrite, directory
search, automatic upload or raw temporary file. No file is created merely by
collecting or analyzing. Caller code must select the local summary destination.

Raw storage remains one bounded RAM buffer unless separately authorized through
`retain_raw(capture, new_absolute_directory, approved=True)`. That writer requires
POSIX ownership/modes, a fresh 0700 directory, 0600 files, no final-component
symlinks and no overwrite. Windows raw retention is deliberately unsupported until
an ACL-specific implementation is verified. RAM-only Windows collection is a
separate issue from raw-file privacy. `Capture.close()` overwrites its owned raw
buffer, but neither it nor dropping Python references promises erasure of copies,
OS buffers, swap, crash dumps or storage.

## Offline analysis and the unmet acoustic target

`pocket.qualification_analysis.analyze_capture(capture, explicit_library_path,
echo_only_confirmed=False)` performs no device, network, file-writing or playback
action. Verification used pinned native SpeexDSP 1.2.1, 24 kHz, 240-sample frames,
4,800-sample filter tail. Only actual paired microphone LEFT / reference RIGHT
samples enter Speex. Deterministic source reconstruction is used solely for
correlation/gain/lag diagnostics; replacing it cannot change AEC residual energy.

The source is the exact waveform in `QUALIFICATION-WIRE.md`, with windows
[4,6), [10,12), [18,20) seconds. Default analysis excludes the first second of
each window for adaptation and the final 10ms ramp. One-second prior baselines,
a 1-PCM RMS numerical floor, >=10dB microphone/reference SNR, no clipping and a
supported RIGHT-lane correlation hypothesis are required for a usable interval.
These are conditional measurements, not physical lane, loudness or intelligibility
proof. Global echo-only confirmation applies only to the first and third windows.
The middle window is reserved for future near-end/double-talk assessment and is
**never counted as an echo-only acceptance interval**.

Task-observed millisecond timestamps produce an observed rate with a quantization
component only; scheduling uncertainty remains unknown. Intervals <=2ms cannot
yield a bounded quantization component and are marked unavailable. No sample-clock
acceptance is inferred from callback counts or task timing.

The proposed ERLE target remains >=10dB in every usable echo-only interval, **not
an automatic physical pass**. Current quiet synthetic FIR-path measurements after
one-second adaptation were **1.006 / 1.699 / 2.729dB**, below target. Independent
native-adapter energy calculation agrees. A bounded, never-played diagnostic found
windowed synthetic amplitude x16 produced 19.591 / 27.185 / 30.696dB; continuous
quiet excitation produced 4.296 / 7.114 / 8.462dB; continuous x16 produced
30.750 / 37.958 / 42.662dB. This supports inadequate excitation/adaptation as a
limitation of this synthetic test, not an analysis/lag arithmetic error. **No
physical waveform, amplitude, volume or duration cap was increased.** The middle
number in this diagnostic has no real-trial acceptance role. Weak/inaudible data
stays inconclusive; no escalation is automatic.

## Explicit-port adapter: source prepared, platform unverified

`pocket.qualification_serial.ExplicitSerialTransport` is optional and is not
imported by the default collector/analysis path. Its constructor performs no I/O.
`open()` requires all separate future operator acknowledgements: `open_approved`,
`reset_risk_acknowledged`, `recovery_confirmed`, `identity_verified`, and
`platform_behavior_verified`, plus an
exact port and operator-attested identity. These flags document authority, not
machine discovery or proof of physical identity. The pyserial import is lazy,
after authority checks. It configures an unopened driver with DTR/RTS false before
open; no enumeration, port guessing, explicit reset/flush, toggling, reconnect or
retry. The pinned pyserial Windows backend itself configures/purges buffers during
open; absence of a wrapper flush is not absence of that backend behavior.

**Opening or closing can still pulse control lines on an actual OS/driver. This
is not reset-free and is not Windows-tested.** Exact candidate/recovery, port
binding and open/reset-risk approval remain prerequisites. No real open was
performed in cloud. Source existence and fake tests do not grant those approvals.

One daemon-owned driver operation may be in flight. Cancellation/timeout retains
and quarantines the owner; no overlapping read/write/close is admitted. `close()`
returns false while an operation remains pending. A later explicit call may
observe the outcome or close after a finished non-close operation; an uncertain
close is never executed twice. A hung OS call may retain a handle indefinitely.
The adapter offers bounded async observation, not OS preemption. No automatic
cleanup result is mislabeled device-safe. No turnkey Windows install/driver or
physical validation is supplied by this source module.

Stage B APIs and distinct observation bounds are in
[`qualification-tls-control.md`](qualification-tls-control.md),
[`qualification-tls.md`](qualification-tls.md) and
[`qualification-transport.md`](qualification-transport.md).


## New Windows-host handoff

The separately reviewed preflight, open/close-only probe and SILENT launcher are
specified in [qualification-windows.md](qualification-windows.md). The probe has
a distinct no-read/no-write purpose; it does not claim platform behavior was
already verified or grant capture authority. Failed-open uncertainty remains
quarantined, and close completion denotes driver API return, not measured native
handle/electrical/device cleanup. The newer full recovery-window hash is recorded
there as parent-supplied AW evidence; old candidate bytes remain unchanged.
