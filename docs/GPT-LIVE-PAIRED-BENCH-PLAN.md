> **Superseded experiment:** The current candidate is the normal-firmware SILENT
> diagnostic, not the standalone bench image or raw USB/tone path below. Retained
> for provenance only; do not run these older device instructions for this trial.
> See `NORMAL-DIAGNOSTIC-DEVICE-PROCEDURE.md` and `GPT-LIVE-OFFLINE-IMAGE.md`.

# Paired-reference bench plan — approval required, not executed

> Continuation: a separate finite bench launcher and strict local collector are
> now prepared in `firmware/bench` and `pocket/bench_capture.py`. See
> [implemented subset and procedure](GPT-LIVE-BENCH-COLLECTOR.md) and the latest
> checkpoint results. This broader acceptance plan remains unexecuted; its
> 60-second, AEC/double-talk, repeated-cycle and network stages are not supplied
> by the initial 10-second SILENT / 5-second TONE launcher.

## Decision and scope

This is a proposed **separately authorized physical experiment**, not permission
to flash or activate anything. Use only the existing Pocket, its speaker/mic,
existing USB connection and host. No new hardware, provider/account key, model
request, deployment or service change is required for the minimum experiment.
Do not alter the current installation until the owner approves the exact device,
candidate image/hash, app-only update and recovery procedure.

The pinned esp_codec_dev 1.6.2 ES8311 driver writes register 0x44 = 0x58 when
`no_dac_ref=false`, with the source comment `ADCL + DACR`; the legacy path writes
0x08. This supports a proposed simultaneous mic/reference stream, **not proof of
the board's physical lane order, sample clock, signal scaling or acoustics**.

Success here means bounded simultaneous capture and reference transport, useful
offline echo cancellation on this device at the tested settings, and restoration
of legacy operation. It does not authorize a paid Live session or establish a
production full-duplex release.

## Prerequisites before asking to flash

- Finish native tests, independent review, real default/opt-in builds and source
  hash comparison. Keep the real-device entry disabled in ordinary builds.
- Prepare a separate, finite, locally started bench entry. It must not call an
  API, auto-connect to a host, reconnect, write NVS, change pairing/Wi-Fi/TLS, or
  enable an existing Live route. The separate bench entry now implements only the finite initial qualification subset; it does not activate Live.
- Identify and hash the known-good recovery app. Verify installed chip/flash,
  partition layout and application slot against the intended image; do not infer
  installed layout from a newly generated partition file. Obtain explicit
  authorization for any readback. Do not read or export secret-bearing settings.
- The approved operation is app-only to the verified slot, never full-flash
  erase, partition-table/bootloader/NVS writes or a blind combined image.
- Record existing target selection, user-visible volume and legacy behavior
  without recording secrets. Prepare the normal recovery/rollback method before
  the experiment; if rollback cannot be established, do not flash.
- Ensure the owner knows the device's actual power-off method. Unplugging USB
  is not necessarily power-off on a battery-powered Pocket.

## Conservative sound limits

Keep the device on a table away from ears. Start codec volume at **zero**; use
digital silence first. A proposed first audible stimulus is a 200 ms ramped
1 kHz tone, PCM16 peak at most 512 (about -36 dBFS), at no more than 5% of the
device volume setting. Use 10 ms onset/offset ramps and at least one second of
silence between bursts. Limit the initial stage to three bursts. These are
conservative initial test settings, **not a calibrated sound-pressure guarantee**.

Do not raise volume automatically if a result is weak. Owner discomfort, harsh
sound, unexpected loudness, clipping, repeated sound or failed Stop means abort,
mute and use the known power-off/recovery method if software Stop fails. Later
AEC stages remain at the same cap unless the owner separately approves a modest
increase. Cap each far-end stimulus window at three seconds; insert at least
three seconds of silence. Never run an unattended or continuous tone/noise loop.

The bench must have a finite stimulus schedule, explicit output-zero tail,
Stop admission checks, a terminal failure latch and bounded capture buffers.
Validate those controls before audible tests. They do not constitute a hardware
fail-safe against all MCU/driver failures.

## Minimum sequence and acceptance record

### 1. Silent configuration and lane sanity

1. Join legacy audio work; save in-RAM settings; enter the isolated native
   24,000 Hz, 16-bit, two-slot RX mode. Read back the intended non-secret codec
   mode registers including 0x44 and log format, slot layout and epoch only.
2. Capture ten seconds of silence in 240-frame paired blocks, with monotonic
   RX frame positions, queue high-water and drop/overflow counters. Expect
   960 bytes per stereo block (480 mic + 480 reference bytes), not duplicated
   mono, relabeled 16 kHz, swapped halves or independently timed lane reads.
3. With TX silent, speak a short nonprivate count near the mic: one lane should
   respond as microphone while the reference remains near its measured floor.
   If both lanes respond similarly, are identical, or slot order is ambiguous,
   stop: do not guess the mapping.
4. Measure total frames against device monotonic time over a silent 60-second
   interval and compare repeated host USB receipt intervals. Report the measured
   rate and timestamp/scheduling uncertainty; flag obvious 16 kHz/48 kHz mismatch.
   This is an approximate no-new-hardware rate check, not independent ppm clock
   certification (device timers and audio may share an oscillator).

**Pass:** exact stereo framing, no discontinuities/overflows, expected register
mode, unambiguous mic lane, and a rate estimate consistent with 24 kHz within
the measured timing uncertainty. Otherwise no audible/AEC continuation.

### 2. Identify the DAC-reference path

1. While volume is zero, send one known finite ramped test sequence and record
   both lanes. Determine whether the reference persists at zero volume: it may
   be pre-volume/pre-mute. A zero-volume reference is not evidence of audible
   playout, and a silent reference does not by itself identify a failed lane.
2. If approved, run the three low-level audible bursts. Cross-correlate source
   with each lane; record reference gain, sign/polarity, lag, clipping count and
   correlation. The microphone should contain the acoustically delayed response;
   the reference should track the electronic source reproducibly.
3. Compare at volume zero and the approved test volume, then return to zero.
   Record whether volume/mute changes alter reference amplitude, polarity or
   delay. Do not assume the reference is post-DAC-volume or acoustic output.
4. Repeat isolated stop/reopen twice and compare lane mapping and lag. Reject
   variable slot swaps, missing samples, unexplained gain changes or unbounded
   lag. Use measured reference-to-microphone delay for AEC analysis; never
   substitute DMA completion, queued samples or elapsed playback time.

**Pass:** deterministic lane identity, stable finite electronic reference,
documented gain/polarity/volume behavior, no clipping, and no data loss. Report
observed lag distribution rather than calling one guessed offset universal.

### 3. Echo and double-talk, offline and no provider

Transfer only the bounded paired sample windows to the existing local host and
run the exact pinned SpeexDSP path offline. Preserve pairing, sample rate and
silence placement; do not use the speaker-source buffer in place of measured
reference. Do not upload raw mic audio to Library or a service by default.

- **Far end only:** three different capped three-second speech-like/test-signal
  windows, separated by silence. Compare raw mic and residual RMS over active
  echo windows after convergence; report ERLE, reference/mic/residual levels,
  clipping and initial convergence time. Exclude silence/noise-floor windows
  from ERLE. A proposed minimum trial criterion is at least 10 dB ERLE in each
  usable echo-only window; this is a project acceptance target, not a vendor
  guarantee or proof for other volumes/rooms.
- **Near end only:** owner says a brief nonprivate phrase with far end silent.
  Check that processed audio remains intelligible, not muted or discontinuous;
  retain no transcript. Any near-end suppression/mic gate is a failure.
- **Double-talk:** repeat a different short phrase during a capped far-end
  window. Listen locally to raw and processed samples at comfortable host
  volume; record whether near speech survives, residual echo remains controlled,
  and the filter recovers on the following echo-only window. Report qualitative
  limitations; without a separate clean near-end recording this is not an
  objective near-end distortion measurement.

An offline pass proves simultaneous acquisition and the tested signal path,
**not real-time network latency or a live conversation**.

### 4. Bounded faults, Stop and ownership

Start with silence; use the quiet test cap only if output behavior is needed.

- Deliberately stall the paired-frame consumer beyond its documented queue
  capacity and separately overflow TX admission. Require a visible terminal
  error, no silent frame deletion/relabeling, no counter wrap and no replay.
- Stop during startup, silent capture, playback, simultaneous capture/playback,
  and a synthetic stalled transport. Measure button/stop observation to audio
  admission stop and owner join separately; report actual worst observed times.
  Proposed bench limit: no new stimulus after Stop is observed, output drains
  or is muted within 100 ms, owner returns within one second. If the design
  promises less, use the stricter bound. A driver hang is a failure, not a
  timeout that may be ignored while ownership is uncertain.
- Repeat ten silent start/stop cycles, then three brief audible cycles. Confirm
  unique epochs, no stale queued PCM on restart, paired-lane continuity, and
  unchanged settings. Test a simulated lost host without any provider call.
- Exercise format restore and sleep/wake. Require explicit reopen of legacy
  16 kHz mono with `no_dac_ref=true`/0x44=0x08; confirm legacy microphone and
  speaker paths remain usable before declaring rollback successful.

### 5. Memory and optional no-provider transport check

Record free internal heap, minimum-ever free heap, largest free block and task
stack high-water at idle, after audio allocation, during paired capture/playback
with the existing UI/Wi-Fi state, and after every stop. Use synthetic in-memory
transport for the minimum test. Require no allocation failures, queue growth,
watchdog resets or downward post-stop heap trend; retain actual values.

These measurements **exclude concurrent TLS cost**. A later no-provider TLS test
may use an already authorized local synthetic endpoint with certificate and
hostname validation intact, bounded authenticated messages and no upstream SDK.
Creating/changing that endpoint, provisioning credentials or changing services
requires separate authorization. Do not disable TLS checks to obtain a pass.
Repeat memory, jitter/overflow and Stop tests under that transport before any
claim about concurrent TLS/audio headroom. No fixed heap figure alone is a pass.

## Completion and rollback

Always stop output, join audio ownership, restore saved volume/format and confirm
legacy operation. Restore the approved known-good application if any acceptance
gate fails or the owner requests it; do not improvise an erase/repartition fix.
If teardown/ownership is uncertain, do not continue tests or start legacy audio.

Keep a small report: artifact/source hashes, build flags, device/codec identity,
register mode, frame counts, estimated rate/uncertainty, lane/gain/polarity/lag,
ERLE and listening outcome, overflow/Stop measurements, heap/stack summaries,
and restored-settings/rollback result. Do not include secrets, raw speech or
transcripts. Mark each check passed, failed or not run. A failed/inconclusive
physical gate keeps activation blocked; success still does not authorize paid
calls, deployment or a changed default mode.
