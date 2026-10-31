# Finite, local ES8311 reference qualification image

This is a **separate offline bench application**, not a normal-main feature flag,
GPT-Live client, or enabled device-support path. No provider credentials, network,
NVS writes, automatic capture or automatic restart. Source/build tests are not
physical validation. Installing or running this image requires separate device
permission; no installation/run was performed during its development.

## Two-part consent and one-shot ownership

Raw USB Serial/JTAG input accepts exactly `ARM SILENT\n` or `ARM TONE\n` once.
No structured records are emitted while idle. Accepted ARM produces the first
HPB1 record (sequence0, epoch1). Within10seconds the board must observe a **new OK
PRESS then later OK CLICK**, with event timestamps after ARM. A button already
held when ARM arrived cannot start. Any active button PRESS, `STOP\n`, invalid
command, expired arm, queue overflow, clock regression or I/O error permanently
terminates this attempt for the boot. Buffered ARM cannot restart. USB commands
never start audio by themselves. The firmware never increases volume automatically.

The owner checks cancellation and fresh monotonic deadlines around operations.
Startup has a2s observed software budget. After successful setup, acquisition
has a5.25s TONE /10.25s SILENT deadline: the extra250ms is scheduling/export
allowance only, not extra source samples, longer bursts or extra capture frames.
A single serialized start/stop codec operation may itself take time; this is not
an independent hardware watchdog or proven hard-real-time shutdown. Stop is
checked/muted before restoration or terminal reporting. Failed mute/readback,
restore or sleep is `restore_ok:false`, never a clean success.

## Fixed profiles

* **SILENT:**1000 frames of240 pairs (10s at native24k), checked hardware mute,
  zero source samples submitted (the callback ring emits zeros).
* **TONE:**500 capture frames (5s sample window;5.25s export deadline), checked volume5 maximum and digital
  peak512/32768 maximum. Exactly three200ms/4800-sample1kHz source bursts begin
  at source offsets24000,52800,81600. Each has240-sample10ms integer ramps;
  interburst silence is1s, initial silence1s and final source silence1.4s.
  All120000 source samples, including zeros, must be accepted for `done`.

The source schedule refers to submitted digital source offsets, **not audible
playout or capture alignment**. Only one240-sample frame waits for TX DMA;
additional six-descriptor hardware latency remains. Busy retains the exact
source frame/offset, never skips or replays accepted samples. If sample/runtime
caps expire before full submission, this is a failed qualification. Quiet numeric
limits are not a calibrated SPL guarantee; speaker/acoustic behavior is untested.

## HPB1 export

28-byte little-endian header `<4sBBHIIIII>`:
`magic=HPB1, kind, flags=0, reserved=0, epoch=1, global_sequence,
capture_sample_offset, payload_len, crc32`.
Kind1 is status JSON (offset0); kind2 is960-byte interleaved microphone-left /
ES8311-DAC-reference-right signed16LE PCM (240 stereo pairs). Payload<=1024.
CRC32 IEEE covers first24header bytes followed by payload. Global sequence starts0
and advances over **all** records; no wrap/reset. Capture sequence and offset are
validated independently against the owned BSP queue. There is no resynchronization
or retry after partial USB admission. USB ring admission does not prove host receipt.

Every status has exactly: `phase`, `profile`, `code`, `rate:24000`, `channels:2`,
`reference:"es8311_dac_right"`, `captured` (exported frame count), `source_samples`
(accepted mono source count), `restore_ok`, `actual_acoustic_validation:false`,
`max_capture_frames`, `peak`, `volume`. Phase is armed/running/done/error (idle is
reserved but never periodically emitted); profile is none/silent/tone. Done means
finite export and checked restoration, **not acoustic quality or audible drain**.
No logs may interleave the structured stream. A host must reject corruption,
missing/duplicate records, missing terminal status and incomplete captures.

## Verification scope

`tests/test_firmware_bench_core.py` exercises the actual pure C core, wire CRC,
finite profiles, stale clicks, one-shot latch, deadline changes inside callbacks,
stop/failure cleanup ordering and fatal partial writes. The glue harness tests
SDK-boundary fakes only. BSP paired-reference tests and an actual ESP-IDF build
are additional source checks. Physical microphone/reference lane identity,
reference delay/amplitude, clipping, near-end/double-talk AEC quality and actual
USB/stop/heap timing still require a separately authorized no-provider bench run.
