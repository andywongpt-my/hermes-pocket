# Normal Pocket qualification — cloud-only design, 2026-10-30

## Purpose and authority

Implement test instrumentation needed to obtain physical evidence later, without
changing production readiness. Parent selected staged USB-local audio first and
a separately explicit synthetic TLS exercise second. Cloud implementation/tests
are authorized; flashing, serial access/reset, audible playback, real microphone
capture/export, network changes and credential provisioning are not authorized by
this design. No provider/paid request, deployment or new hardware.

Input is the verified post-caller source archive:
`private-source-checkpoint (identifier omitted for public publication)`,
`hermes-pocket-gpt-live-app-caller-source-20261030.zip`, SHA256
`54094ffc498781106b7138f3b712314be398abc1e7f4957c7cc67b3ac5d76bd7`.
Do not edit its frozen checkout. Original device recovery remains intact.

## Approaches selected

A single LAN-first trial would exercise TLS sooner but requires a reachable test
endpoint and certificate/configuration before audio is understood. A standalone
bench image is ruled out by Andy's normal-firmware direction. Chosen approach:
separate bounded qualification inside the existing normal app, USB-local audio
first, then a separately explicit local synthetic TLS harness. Neither stage alone
is a full GPT-Live acceptance or permission to lift the source-hard production gate.

## Stage A: finite local audio evidence

- New default-OFF compile option and separate test controller, normal UI and
  standby/wake intact; original silent diagnostic and production gates unchanged.
- Reuse existing voice owner/worker and separate Live BSP lease; no second audio
  owner or polling alongside the experiment. No provider/network calls or settings
  writes by this qualification controller. Normal app background behavior remains
  outside that statement.
- Explicit host request plus fresh physical confirmation, clearly labeled SILENT
  versus AUDIO TEST. One attempt per boot, no auto start/retry/reconnect. Expiration,
  queue loss, non-current event generation, missing host, overflow, discontinuity,
  CRC/write failure, time regression or any later physical press stops the attempt.
- SILENT profile: at most 1,000 paired frames / 10 seconds nominal capture, no
  speaker submission. Existing 2-second startup / 10.25-second acquisition guards.
- Optional AUDIO profile only with an explicit future approval: bounded 24-second
  schedule, at most three 2-second band-limited deterministic non-tone windows
  (6 seconds total), separated by silence. PCM absolute peak <=512, 10-ms onset/
  offset ramps, requested codec volume <=5%, never automatically raised and never
  overrides a saved mute. Output starts muted. These are digital bounds, not an SPL
  guarantee. Weak/inaudible reference is inconclusive; no automatic escalation.
- Native 24 kHz, 16-bit paired RX; carry original epoch/sequence/sample offsets,
  measured elapsed time and bounded queue statistics. Preserve paired mic/reference;
  reconstruct source only for correlation, never substitute it as AEC reference.
- Stream through a bounded, checked USB adapter; no raw PCM stored on device flash.
  Native USB throughput and driver coexistence remain physical acceptance items.
  No UART0 reassignment (GPIO21 conflicts with backlight). Transport/log ownership
  must be established from pinned IDF sources before implementation; abort rather
  than drop/relabel capture if the exporter cannot sustain the stream.
- Mute/end/restore checked before owner release; unknown or failed cleanup stays
  quarantined and blocks new audio. Software guards cannot preempt an SDK hang.
  Normal Screen Off is standby, never claimed as a physical kill switch.

## Local retention and analysis

Collector disabled unless explicitly invoked with exact approved device identity;
no enumeration/port guessing, automatic reset, flash, reconnection or ARM retry.
Default: bounded paired data in host RAM only, numeric JSON summary saved locally,
no raw speech file, transcript, upload or playback. Raw retention, if separately
approved, is explicit to a newly created private directory; no overwrite, no cloud
upload. Do not claim POSIX modes guarantee Windows ACLs or RAM/SSD erasure.

Offline analysis uses pinned native SpeexDSP 1.2.1 and actual paired reference:
exact record integrity/rate estimate with uncertainty, lane identity/correlation,
gain/polarity/lag, clipping, and far-end residual/ERLE over usable non-silent windows.
A proposed trial target is >=10 dB ERLE in each usable echo-only window; inadequate
SNR is inconclusive. Near-end/double-talk requires separately approved nonprivate
speech and local human assessment; no automatic pass can prove intelligibility or
SPL. Analysis never opens a device, network connection or audio output.

## Stage B: explicit local synthetic TLS

Prepare a no-provider host fixture and an inactive test-only caller integration
that exercises the real v2 worker/audio/network path without replacing the
production always-false gate. No automatic endpoint or certificate discovery;
TLS hostname/chain verification must stay enabled. Default listener is loopback
and can be tested entirely in cloud. A future device-reachable endpoint, temporary
in-memory test config, host bind/firewall choice and certificate arrangement need
separate explicit approval; do not change services/settings to make it reachable.

Bound individual future runs to <=60 seconds and no automatic repeat. Test startup,
continuous paired flow, bounded queue pressure, Stop while waiting for credit,
transport close/failure, and retained cleanup. Record actual free/minimum/largest
heap and task stack high-water, observed Stop/cleanup times, frame continuity and
restart isolation. Synthetic endpoint never imports/calls a paid provider or
stops Hermes work. Proposed healthy-run audio-stop target <=100 ms after callback
observation; network finalization/cleanup measured separately (worker permits a
3-second finalization grace). Quarantine must not be mislabeled completion.

## Acceptance boundaries and artifact handoff

Cloud: red/green native tests, fake transport/codec cases, privacy/size/time caps,
independent review, actual LVGL renders and exact C3 builds against 3 MiB app bound.
Then publish a separately identified source checkpoint and, only if requested,
an exact normal-app qualification candidate with app hash and erase-rounded range.
No flash command follows automatically. Never include the source partition table,
bootloader, NVS, old standalone bench binary or provider credentials.

Before any future device action: exact candidate hash, target C3 v1.1 / 8 MiB /
factory [0x10000,0x310000), independently usable recovery/reset method, device-proven
original recovery app hash, finite run/profile and local data retention approval.
The original recovery hash is not available in this subagent's verified cloud
inputs and must not be invented. Original restoration was reported boot-verified.

Production gate stays false after all cloud work. Only recorded physical lane/
clock/acoustic, runtime TLS/audio, Stop/cleanup and recovery evidence can justify
a later reviewed gate change. A remembered silent count of 1,000 cannot do so.

## Builder-review clarifications before implementation

- Frame protocol will be separately versioned from production audio: fixed magic,
  bounded fixed-width header, kind/profile, nonzero one-use 64-bit host run nonce,
  sequence, original sample offset, explicit payload length and whole-header/payload
  CRC. Maximum payload 1,024 bytes; no variable unbounded strings. A checked terminal
  record carries cleanup disposition and counters; the last audio frame is never
  a success receipt. Control ARM/keepalive/STOP uses the same nonce and strict CRC.
  Physical confirmation uses the non-wrapping input generation. No resynchronization
  after framed-stream corruption. Prefix boot noise is separately bounded.
- At most 1,000 / 2,400 paired frames (silent/audio), respectively 960,000 /
  2,304,000 raw PCM bytes. Raw host PCM storage is a bounded contiguous buffer;
  maximum framed input 3 MiB, parser scratch <=8 KiB. A run cannot grow these caps
  through malformed lengths, repeated statuses or late data. No raw data in cloud
  evidence; synthetic fixtures only. Host overall deadline <=45 seconds for A;
  heartbeat loss <=500 ms requests Stop. Deadlines are software-observed, not a
  hardware preemption guarantee.
- Pinned IDF research shows the normal USB console initially uses direct FIFO,
  not its buffered driver. Export must not merely install a second writer. Proposed
  opt-in-build arrangement: one controlled driver installation and VFS-to-driver
  switch before qualification admission; binary export bypasses VFS newline
  conversion but uses that single driver's queue. Suppress ordinary log formatting
  during framed export, preserve normal console outside the run, and treat any
  ROM/panic/other injected bytes as corruption/terminal failure. Exact install/
  drain/lifetime tests and review are required; never redirect to UART0. Native
  USB throughput is unproven and any backpressure beyond bounded storage aborts.
- Stage B must add an independent <=60-second Stop predicate; the existing worker's
  300-second session cap is not the qualification cap. Use a unique qualification
  route, never the ordinary /targets/.../live route or a fallback to saved bridge
  config. Any future test configuration is explicit, bounded, in-memory and wiped
  after verified cleanup. No provider key import, saved settings fallback or
  certificate-verification bypass. Future device-reachable addresses must be
  explicit numeric private IPv4 with verified TLS identity; cloud listeners stay
  loopback. Test endpoint provisioning/listening beyond loopback remains separately
  approved and is not performed by this work.

## Implementation handoff note, 2026-10-30

The above design is preserved as the approved pre-implementation record. Parent
subsequently supplied historical recovery hashes and the Windows receipt path;
those bytes are not present or freshly verified in this cloud workspace. Final
candidate erase interval [0x10000,0x271000) exceeds the historically preserved
[0x10000,0x267000) interval by 40 KiB, so installation remains blocked. Exact hashes,
build/reproducibility evidence, supported platform limitations, raw-data retention
and the separately required future approvals are in the final checkpoint evidence
and candidate read-first document. No original recovery artifact was modified.
